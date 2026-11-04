# -*- coding: utf-8 -*-
"""需求文本 → 表单字段规格 → 录入动作（单一实现 SSOT）。

根因（2026-09-27，accept_sut_0923 实测 TC-REQ-002-P01 / TC-REQ-003-P01 假 FAIL）：
  变更型用例的动作编排只产出**单个** fill（target_hint="主输入框"），而真实 SUT
  的新增表单有多个必填字段（名称/分类/价格/库存/状态/备注/勾选确认）。缺字段 →
  客户端 JS 校验拦截提交 → 结构化执行必败 → 降级 AI 自由探索 8 轮不收敛
  （单条 6.4 分钟，占全轮 69%）→ 既失真又低效。

本模块从需求原文提取字段规格，组装**完整**录入动作序列，供
`_rule_extractor_actions._build_actions`（主规则路径）与
`_fallback_cases._build_base_actions`（兜底路径）共用——两条生成路径口径唯一。

通用性(§1)：
  · 只依赖需求文本的**通用书写范式**「标签（英文键）：约束」——这是需求规格
    说明书的通用写法，跨系统/行业成立，零业务硬编码。
  · 字段与本需求的关联用「操作对象词重叠」（复用 `_extract_operation_objects`），
    不依赖 module 是否等于文档标题（实测二者常不等）。
  · 关联不到字段规格时返回空列表 → 调用方维持原有单 fill 行为（诚实降级，
    不臆造字段）。
"""

import logging
import re
from typing import Any

_log = logging.getLogger(__name__)

# 「标签（key）：约束」——通用书写范式。标签 1-12 字（排除括号/标点），
# key 为英文/下划线标识符，约束为到句末/分号前的自由文本。
_FIELD_SPEC_RE = re.compile(
    r"([^\s（）()【】\[\]，,。；;：:]{1,12})\s*[（(]\s*([A-Za-z_][\w\-]{0,30})\s*[)）]"
    r"\s*[：:]\s*([^\n；;。]{1,120})"
)

# 约束文本的关键词表（语言学分类，非业务词，跨系统/行业通用）
_REQUIRED_KEYS = ("必填", "必须", "非空", "不能为空", "不可为空", "required")
_CHECKBOX_KEYS = ("复选框", "勾选", "checkbox")
_SELECT_KEYS = ("下拉", "单选", "select", "枚举", "选项")
_TEXTAREA_KEYS = ("文本域", "多行", "textarea")
_INT_KEYS = ("整数", "整型", "int")
_NUM_KEYS = ("数字", "数值", "金额", "价格", "float", "double", "number")

# 名称类字段（唯一值注入目标）——用于列表可见性断言与 SUT 唯一约束对齐
_NAME_LABEL_KEYS = ("名称", "姓名", "名字", "标题", "title", "name", "编号", "code")

# project → 解析出的全部字段规格（进程内缓存；Step3 单次运行内需求不变）
_SPEC_CACHE: dict[str, list[dict]] = {}


def _iter_specs_from_text(text: str) -> list[tuple[str, str, str]]:
    """从一段需求文本解析出全部 ``(label, key, constraint)`` 三元组。"""
    out: list[tuple[str, str, str]] = []
    for m in _FIELD_SPEC_RE.finditer(str(text or "")):
        _label = m.group(1).strip()
        _key = m.group(2).strip()
        _cons = m.group(3).strip()
        if _label and _key and _cons:
            out.append((_label, _key, _cons))
    return out


def _load_all_specs(project: str) -> list[dict]:
    """一次性从 requirements 读取全部需求并解析字段规格（按 project 缓存）。"""
    if project in _SPEC_CACHE:
        return _SPEC_CACHE[project]
    specs: list[dict] = []
    try:
        from src.pipeline.utils.requirement_store import requirement_store

        reqs = requirement_store.list_requirements()
    except Exception as e:  # noqa: BLE001
        _log.warning("[fields] 读取需求失败，字段规格降级为空: %s", str(e)[:120])
        reqs = []
    for r in reqs or []:
        _text = " ".join(
            str(r.get(k, "") or "")
            for k in ("source_quote", "description", "expected_feedback")
        )
        for _label, _key, _cons in _iter_specs_from_text(_text):
            specs.append(
                {
                    "label": _label,
                    "key": _key,
                    "constraint": _cons,
                    "module": str(r.get("module", "") or ""),
                    "operation": str(r.get("operation", "") or ""),
                    "req_id": str(r.get("req_id", "") or ""),
                }
            )
    _SPEC_CACHE[project] = specs
    _log.info("[fields] 解析字段规格 %d 条 (project=%s)", len(specs), project)
    return specs


def extract_field_specs(
    operation: str, module: str = "", project: str = "", hints=()
) -> list[dict[str, Any]]:
    """提取与本操作同域的字段规格（按 key 去重合并）。

    关联规则（保守，宁缺毋滥）：
      · module 与本需求相等 → 采纳；
      · 否则要求「本操作的对象词」出现在字段规格来源文本（module/operation/label）中；
      · 或 `hints`（用例**既有录入控件**的 target_hint）含该字段标签 → 采纳。
        why: 用例自己声明了"往哪个控件录值"，这是比需求文本更硬的表单归属证据 ——
          跨模块引用（如"接口清单"章节的需求实际走资产表单）时，仅靠 module/对象词
          关联不到字段规格 → 提交前置无法补齐（实测 UX02 族：录入段缺必勾选 → 提交
          被客户端校验拦截 → 期望的成功反馈结构性不出现）。
    三者皆不满足 → 不采纳（避免跨实体张冠李戴）。
    """
    from src.core.config_loader import config

    project = project or str(getattr(config, "project_name", "") or "")
    _all = _load_all_specs(project)
    if not _all:
        return []

    from src.pipeline.step3._rule_extractor_buttons import _extract_operation_objects

    _objs = _extract_operation_objects(operation)
    _hints = [str(h or "") for h in (hints or []) if str(h or "")]

    merged: dict[str, dict] = {}
    _mods: set[str] = set()
    for s in _all:
        _src = f"{s['module']} {s['operation']} {s['label']}"
        _by_module = bool(module) and s["module"] == module
        _by_object = bool(_objs) and any(o in _src for o in _objs)
        _by_hint = bool(s["label"]) and any(s["label"] in _h for _h in _hints)
        if not (_by_module or _by_object or _by_hint):
            continue
        if s["module"]:
            _mods.add(s["module"])
        _k = s["key"]
        if _k not in merged:
            merged[_k] = dict(s)
        else:
            merged[_k]["constraint"] = (
                str(merged[_k]["constraint"]).rstrip("；;") + "；" + s["constraint"]
            )
            if not merged[_k].get("label"):
                merged[_k]["label"] = s["label"]
    # ★ 表单归属一经确定 → 取该表单**全量**字段规格。
    #   why: 只取"标签恰好出现在 hint 里"的字段会漏掉未出现在录入段中的必填项
    #     （如必勾选确认），使提交前置仍不完整（实测 TC-REQ-035-UX02：
    #      录入段有名称/价格/库存 → 只关联到这三项 → 漏掉"信息确认"必勾选 →
    #      客户端校验拦截提交）。归属判据已由上一轮确定，字段集应按**表单**取全，
    #      而不是按"是否已被录入"取。
    if _mods:
        for s in _all:
            if s["module"] not in _mods or s["key"] in merged:
                continue
            merged[s["key"]] = dict(s)
    return list(merged.values())


def _classify(spec: dict) -> dict[str, Any]:
    """字段规格 → (是否必填, 控件族, 值类型)。"""
    c = str(spec.get("constraint", "") or "").lower()
    _required = any(k in c for k in _REQUIRED_KEYS)
    if any(k in c for k in _CHECKBOX_KEYS):
        _widget = "checkbox"
    elif any(k in c for k in _SELECT_KEYS):
        _widget = "select"
    elif any(k in c for k in _TEXTAREA_KEYS):
        _widget = "textarea"
    else:
        _widget = "input"
    if any(k in c for k in _INT_KEYS):
        _vtype = "integer"
    elif any(k in c for k in _NUM_KEYS):
        _vtype = "number"
    else:
        _vtype = "string"
    return {"required": _required, "widget": _widget, "vtype": _vtype}


def _is_name_like(spec: dict) -> bool:
    _t = f"{spec.get('label', '')} {spec.get('key', '')}".lower()
    return any(k.lower() in _t for k in _NAME_LABEL_KEYS)


def build_form_data_actions(
    operation: str,
    *,
    module: str = "",
    unique_value: str = "",
    project: str = "",
    include_optional: bool = False,
    invalid: bool = False,
    hints=(),
) -> list[dict]:
    """按字段规格组装录入动作序列（target_hint 统一 ``name=<key>``）。

    :param operation: 本用例的操作意图文本（对象词来源）
    :param unique_value: 名称类字段的确定性唯一值（列表可见性断言对齐）
    :param include_optional: 是否含选填字段（默认只录必填，避免过度构造）
    :param invalid: 负向用例 → 取非法值（供客户端/服务端校验拦截）
    :param hints: 用例**既有录入控件**的 target_hint（表单归属的硬证据，见 extract_field_specs）

    target_hint 用 ``name=<key>`` 而非 label 文本：实测表单 input 常被 ``<label>``
    包裹，用文本定位会命中 label 本身致 ``fill()`` 抛错；``name=`` 是 DOM 契约级
    精确定位，且模糊回退可覆盖 key≠HTML name 的情形。
    """
    specs = extract_field_specs(operation, module, project, hints=hints)
    if not specs:
        return []
    from src.pipeline.step3.value_generator import (
        generate_invalid_value_from_type,
        generate_value_from_type,
    )

    actions: list[dict] = []
    for s in specs:
        info = _classify(s)
        if not info["required"] and not include_optional:
            continue
        hint = "name=%s" % s["key"]
        # 字段身份随动作携带（标签 + key）——供「提交前置完整性」判据识别"该字段
        # 是否已被**既有**录入动作覆盖"（既有动作用 label 形态 hint，如
        # "名称/姓名输入框"，只认 key 会漏判 → 重复录入同一字段）。
        _ident = {"_field_key": s["key"], "_field_label": s.get("label", "")}
        if info["widget"] == "checkbox":
            # 必勾选类（如"信息确认"）：仅正向需要勾选；负向留空以触发客户端拦截
            if not invalid:
                actions.append({"type": "check", "target_hint": hint, **_ident})
        elif info["widget"] == "select":
            # 下拉：正向取首项兜底（Step4 对哨兵值回落 index 选择）；
            # 负向交由默认首项（不臆造"无效选项"以免定位失败）
            if not invalid:
                actions.append(
                    {
                        "type": "select_option",
                        "target_hint": hint,
                        "option_text": "第一项",
                        **_ident,
                    }
                )
        else:
            if invalid:
                val = generate_invalid_value_from_type(info["vtype"], s["key"])
            elif _is_name_like(s) and unique_value:
                val = unique_value
            else:
                val = generate_value_from_type(info["vtype"], s["key"])
            actions.append({"type": "fill", "target_hint": hint, "value": val, **_ident})
    return actions