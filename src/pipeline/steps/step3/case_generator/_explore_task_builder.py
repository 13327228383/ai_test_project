# -*- coding: utf-8 -*-
"""★ P0-refactor(2026-07-29): explore_task 统一构建器（从 _model.py 拆分）

职责: 接收 requirement 字典，输出 _explore_task 字典。
      包含 module 提取、target_page 推断、legacy actions 包装。

单一职责: explore_task 构建逻辑独立于 AI 调用和校验，可单独测试和复用。
通用性: 跨系统/行业/页面 — 不写死业务术语。
"""

from __future__ import annotations
from src.core.guarded import guard

import re
import logging
from typing import Any
from src.core.semantic_extractor import _NOISE_TOKENS as _noise_prefix  # noqa: F401  词表唯一源

_log = logging.getLogger(__name__)


def extract_module_from_operation(operation: str) -> str:
    """★ 通用修复(2026-07-28) + P0-3b(2026-07-29): 从 operation 文本中提取模块名。

    根因: Step2 的 module 字段来自文档 section 标题（如"表格块：第1行"），
          对导航无用。真正的模块名（如"烟叶画像数据"）在 operation 文本中。

    通用性: 跨系统/行业/文档格式 — 正则匹配多种模式。
    ★ 2026-08-28 R1: 理解交给 AI — 优先用 semantic_extractor 的 AI 实体提取，
      正则（下方 patterns）降级兜底。
    """
    if not operation:
        return ""
    # ★ R1(2026-08-28): AI 实体提取优先（带缓存；失败静默降级正则）
    with guard("extract_module_from_operation: from src.core.semantic_extractor imp", log=_log):
        from src.core.semantic_extractor import extract_control_intent

        _intent = extract_control_intent(operation)
        _entity = str(_intent.get("entity") or "").strip()
        if _entity:
            return _entity[:4]
    # ★ P0-3b fix(2026-07-29): "所属模块/所属功能"优先于"在...模块中"
    #   根因: "在...模块中"匹配文档章节标题（如"表格块：第1行"），而非业务模块名。
    #         "所属模块"是明确的业务标注，必须优先匹配。
    #   通用性: 跨系统/行业/文档格式 — 业务标注优先于文档结构描述。
    patterns = [
        r"所属模块[：:]\s*([^\s，,。.]{2,20})",  # ★ 明确的业务模块标注，最高优先级
        r"所属功能[：:]\s*([^\s，,。.]{2,20})",  # 所属功能标注
        r"模块[：:]\s*([^\s，,。.]{2,20})",  # 通用模块标注
        r"在(.+?)模块中[,，]?\s*用户执行",  # 文档结构描述，兜底（仅当无明确标注时）
    ]
    for pat in patterns:
        m = re.search(pat, operation)
        if m:
            _mod = m.group(1).strip()
            _log.info(
                "[ExploreTaskBuilder] 从operation提取模块名: %r → %r",
                operation[:60],
                _mod,
            )
            return _mod

    # ★ 2026-08-28 实体词提取 fallback: 无"所属模块"标注时，从操作宾语提取业务实体
    #   根因(验收实证): hospital_sut 等系统的 operation 是需求条目原文
    #     ("REQ-PAT-02：可新增患者（填姓名/年龄/电话等）提交后列表出现新患者")，
    #     module 是需求文档标题("医院诊所管理系统需求规格") → 导航匹配永远 score=0。
    #   修复: 提取"新增/创建/编辑/删除/查看 + 宾语名词"（如"患者""医生""预约"），
    #     该实体词与真实菜单("患者管理")有高相似度。
    #   通用性: 跨系统/行业 — 操作宾语实体是业务模块的稳定标识。
    _op_verbs = (
        "新增", "创建", "新建", "添加", "录入", "登记", "注册",
        "编辑", "修改", "更新", "删除", "移除", "查看", "查询",
        "浏览", "打开", "列表", "展示", "填报", "上传", "导入", "导出",
    )
    _verb_pos = -1
    for _v in _op_verbs:
        _idx = operation.find(_v)
        if _idx >= 0 and (_verb_pos == -1 or _idx < _verb_pos):
            _verb_pos = _idx
    if _verb_pos >= 0:
        _tail = operation[_verb_pos + 2:]  # 动词后文本
        # 提取 2-6 字中文名词（去括号、需求编号、标点）
        _tail = re.sub(r"REQ-?\w*\s*[:：]?\s*", "", _tail)
        _tail = re.sub(r"[（(【\[].*?[）)】\]]", "", _tail)
        _tail = re.sub(r"[,，。;；、\s]+", "", _tail)
        _m2 = re.search(r"[\u4e00-\u9fff]{2,6}", _tail)
        if _m2:
            _entity = _m2.group(0)
            # 先截短再过滤（"展示医生信息"→截短"展示"→动词碎片过滤才生效）
            if len(_entity) > 4:
                _entity = _entity[:2]
            # 过滤通用词（操作/数据/记录/信息等），保留业务实体
            _generic = {"操作", "数据", "记录", "信息", "内容", "列表", "功能", "页面", "字段"}
            # 动词碎片过滤（"处方列表显示"→"显示"、"患者列表页展示"→"页展"）：
            # 动词定位偏差导致的碎片不作为实体，落 fallback2（REQ 前缀实体）
            _action_frag = ("显示", "提交", "出现", "展示", "保存", "处理", "操作", "页展", "并出现")
            if _entity.endswith(_action_frag) or _entity in _action_frag:
                _entity = ""
            if _entity and _entity not in _generic:
                _log.info(
                    "[ExploreTaskBuilder] 从operation提取实体模块名(动词宾语): %r → %r",
                    operation[:60],
                    _entity,
                )
                return _entity

    # ★ 2026-08-28 fallback2: REQ 前缀后跳过虚词+操作动词取实体（覆盖查看/展示类：
    #   "REQ-PAT-01：患者列表页展示全部患者" → "患者"；"可为患者开处方" → "患者"）
    _txt = re.sub(r"^REQ[-\w]*\s*[:：]?\s*", "", operation)
    _txt = re.sub(r"[（(【\[].*?[）)】\]]", "", _txt)
    # ★ 2026-08-28 介词结构优先（必须在 lstrip 之前匹配——lstrip("可为...")
    #   会吃掉介词"为"，使"为患者开处方"失配）。
    #   模式: 介词(为/给/对/向/替) + 受益者 + 动词 + 宾语。
    #   通用性: 跨系统/行业 — 需求条目常以"为X做Y"描述，Y 才是业务实体。
    _generic3 = {"操作", "数据", "记录", "信息", "内容", "列表", "功能", "页面", "字段", "相关", "管理", "系统"}
    _prep_m = re.search(
        r"(?:为|给|对|向|替)[\u4e00-\u9fff]{1,4}?"
        r"(?:开|写|做|发|生成|创建|新增|录入|建立|添加)[\u4e00-\u9fff]{2,4}",
        _txt,
    )
    if _prep_m:
        _prep_tail = re.search(
            r"(?:开|写|做|发|生成|创建|新增|录入|建立|添加)([\u4e00-\u9fff]{2,4})",
            _prep_m.group(0),
        )
        if _prep_tail:
            _entity_prep = _prep_tail.group(1)[:2]
            if _entity_prep not in _generic3:
                _log.info(
                    "[ExploreTaskBuilder] 从operation提取实体模块名(介词宾语): %r → %r",
                    operation[:60],
                    _entity_prep,
                )
                return _entity_prep
    _txt = _txt.lstrip("可可以为将对给在把从向以于")
    for _v in _op_verbs:
        if _txt.startswith(_v):
            _txt = _txt[len(_v):]
            break
    _m3 = re.search(r"[\u4e00-\u9fff]{2,4}", _txt)
    if _m3:
        _entity3 = _m3.group(0)[:2]
        if _entity3 not in _generic3:
            _log.info(
                "[ExploreTaskBuilder] 从operation提取实体模块名(REQ前缀): %r → %r",
                operation[:60],
                _entity3,
            )
            return _entity3
    return ""


def _clean_module_name(raw_module: str) -> str:
    """★ P0-3b: 清理模块名——去除文档章节标题噪音。

    根因: Step2 的 module 字段可能是"P3需求 辅材、掺配物"或"表格块：第1行"，
          这些是文档章节标题，不是业务模块名。需要提取真正的业务模块名。

    策略:
      1. 去除"P0/P1/P2/P3需求"前缀
      2. 去除"表格块"、"列"等文档结构词
      3. 保留剩余部分作为业务模块名
    """
    if not raw_module:
        return ""
    _mod = raw_module.strip()
    # 去除"P数字需求"前缀
    _mod = re.sub(r"^P\d+\s*需求\s*", "", _mod).strip()
    # 去除文档章节编号前缀（"10. 接口清单" / "5、新增资产" / "第三章节"）。
    # 章节号属文档结构、非业务模块名，留着会让侧边栏菜单匹配恒不命中。
    _mod = re.sub(r"^\s*(?:第\s*[0-9一二三四五六七八九十]+\s*[章节]|"
                  r"[0-9]+(?:\.[0-9]+)*\s*[.、)）])\s*", "", _mod).strip()
    # 去除需求类型括注（"新增资产（创建需求）"→"新增资产"；括注是需求分类标签，
    # 不是菜单名的一部分）。
    _mod = re.sub(r"[（(][^）)]{0,12}?需求[）)]\s*$", "", _mod).strip()
    # 去除文档结构词
    _doc_noise = {
        "表格块",
        "表格",
        "列",
        "行",
        "单元格",
        "table chunk",
        "table",
        "column",
        "row",
        "cell",
        "chunk",
        "第",
    }
    _parts = re.split(r"[：:，,、\s]+", _mod)
    _cleaned = [p for p in _parts if p.strip() and p.strip() not in _doc_noise]
    _result = "".join(_cleaned) if _cleaned else _mod
    _result = _result.strip()
    # ★ P0-3b fix(2026-07-29): 验证清理后结果是否为有效业务模块名
    #   根因: 全文档噪音（如"表格块：第1行"）清理后得到"第1"、"详见chunk"等无效结果。
    #   修复: ① 至少包含2个中文字符或2个字母数字字符
    #         ② 不含文档结构词（详见/参见/chunk/table等）
    #   通用性: 跨系统/行业/语言 — 中英文模块名都支持。
    _cn_chars = re.findall(r"[\u4e00-\u9fff]", _result)
    _alnum_chars = re.findall(r"[a-zA-Z0-9]", _result)
    if len(_cn_chars) < 2 and len(_alnum_chars) < 2:
        _log.info(
            '[ExploreTaskBuilder] _clean_module_name: 清理后无效 %r → ""',
            raw_module[:40],
        )
        return ""
    # 检测残留文档噪音（中文结构词 + 英文文档术语）
    _structural_cn = {
        "详见",
        "参见",
        "参考",
        "详情",
        "如下",
        "下方",
        "上方",
        "前述",
        "上述",
        "下述",
    }
    _structural_en = {"chunk", "table", "column", "row", "cell"}
    _lower = _result.lower()
    for _sw in _structural_cn:
        if _sw in _result:
            _log.info(
                '[ExploreTaskBuilder] _clean_module_name: 含结构词%r %r → ""',
                _sw,
                raw_module[:40],
            )
            return ""
    for _en in _structural_en:
        if _en in _lower:
            _log.info(
                '[ExploreTaskBuilder] _clean_module_name: 含英文噪音%r %r → ""',
                _en,
                raw_module[:40],
            )
            return ""
    return _result


def build_explore_task(requirement: dict[str, Any]) -> dict[str, Any]:
    """构建 _explore_task 字典（Step3 统一入口）。

    Args:
        requirement: Step2 输出的需求条目（含 operation/expected_feedback/module/target_page/test_method）

    Returns:
        _explore_task 字典: {operation, target_page, description, module, test_method}
    """
    op = (requirement.get("operation") or "").strip()
    eb = (
        requirement.get("expected_feedback")
        or requirement.get("expected_behavior")
        or ""
    ).strip()
    title = ("%s - %s" % (op or "操作", eb or "验证"))[:50]

    # ★ v5.4: 传递 test_method（AI已在Step3生成）
    _tm = requirement.get("test_method") or {}

    _task = {
        "operation": op,
        "target_page": requirement.get("target_page", "") or "",
        "description": title,
        "module": _clean_module_name(
            requirement.get("module", "") or extract_module_from_operation(op) or ""
        )
        or extract_module_from_operation(op)
        or "",  # ★ 回退：清理后无效→从operation提取
    }
    # ★ 2026-08-30 需求权威性标记: 供 Step4 在"导航失败"时判决 SKIP vs FAIL。
    #   需求比实际系统更权威 —— 若用例有可追溯需求编号(req_id)且非隐含(is_implicit=0)，
    #   则系统未实现该需求 = 需求得到满足缺口 → 判 FAIL；无权威需求(虚构/AI臆造)才 SKIP。
    #   通用性: req_id/is_implicit 是跨系统/行业的通用需求元数据字段。
    _task["req_id"] = str(requirement.get("req_id") or "").strip()
    _task["_req_implicit"] = bool(requirement.get("is_implicit"))
    # ★ 通用修复(2026-09-24): 透传 expected_feedback 供测试方法生成器判断拒绝/校验语义。
    #   根因: description 仅拼 op-eb 且截断 50 字符，长 expected_feedback 会被截掉，
    #     导致"提交登录请求 → 用户名或密码错误"类需求落空（见登录拒绝检测）。
    _task["expected_feedback"] = eb
    if _tm:
        _task["test_method"] = _tm
    return _task


def enhance_requirement_module(requirement: dict[str, Any]) -> None:
    """增强 requirement 的 module 字段（从 operation 提取真实模块名 + 清理章节标题噪音）。

    修改 requirement 引用（原地修改），不返回新对象。
    """
    _op_text = requirement.get("operation", "")
    _extracted_mod = extract_module_from_operation(_op_text)
    if _extracted_mod:
        _extracted_mod = _clean_module_name(_extracted_mod)
    _old_module = requirement.get("module", "")
    _cleaned_old = _clean_module_name(_old_module)
    if _extracted_mod:
        if (
            not _old_module
            or "表格块" in _old_module
            or "table chunk" in _old_module.lower()
        ):
            requirement["module"] = _extracted_mod
            _log.info(
                "[ExploreTaskBuilder] module增强: %r → %r",
                _old_module[:40],
                _extracted_mod,
            )
    elif _cleaned_old and _cleaned_old != _old_module:
        # 清理后的模块名不同于原始（去除了"P3需求"等前缀）
        requirement["module"] = _cleaned_old
        _log.info(
            "[ExploreTaskBuilder] module清理: %r → %r", _old_module[:40], _cleaned_old
        )


def wrap_legacy_actions_to_explore_task(
    actions: list[dict], requirement: dict[str, Any]
) -> dict[str, Any]:
    """将旧 actions 数组包装为 explore_task 格式的用例。

    用于处理 AI 仍输出旧格式 actions 的场景（向后兼容）。
    """
    op = (requirement.get("operation") or "").strip()
    eb = (
        requirement.get("expected_feedback")
        or requirement.get("expected_behavior")
        or ""
    ).strip()
    title = ("%s - %s" % (op or "操作", eb or "验证"))[:50]
    req_id = requirement.get("req_id", "REQ-???")

    return {
        "case_id": "TC-%s-P01" % req_id,
        "req_id": req_id,
        "title": title,
        "scenario_type": "happy_path",
        "test_method": "FD",
        "control_type": "composite",
        "risk_level": "P1",
        "type": "功能",
        "explore_task": build_explore_task(requirement),
        "checks": [
            {
                "track": "ui",
                "precision": "fuzzy",
                "check": "验证%s结果" % (op or "操作"),
                "expect": eb or "操作成功",
            }
        ],
    }


def _build_default_test_method(explore_task: dict[str, Any]) -> dict[str, Any]:
    """★ 方案B(2026-07-29): AI未生成test_method时，从module+operation自动构建默认测试方法。

    根因(5层why):
      1. Agent自由探索不可靠 → 需要test_method指导
      2. AI经常不生成test_method → prompt太长或模型能力不足
      3. 缺少兜底机制 → inject_explore_task_to_cases只复制不构建
      4. 构建逻辑应该在post-processing而非AI层 → 确定性 > 概率性
      5. 方案B务实：module导航已就绪，只需补test_method兜底

    通用性: 跨系统/行业/页面 — 不写死业务术语，从operation文本提取动作关键词。

    ★  精细化修复: verifications 的 expect 必须精确（如"创建成功"），
      禁止使用"场景主流程操作成功"等模糊描述。从 operation 提取具体字段名和值，
      生成更具体的 verification 步骤。
    """
    _module = (explore_task.get("module") or "").strip()
    _op = (explore_task.get("operation") or "").strip()
    _desc = (explore_task.get("description") or "").strip()
    # ★ 通用修复(2026-09-24): 读透传的 expected_feedback 用于拒绝/校验语义判定
    _expect_fb = str(explore_task.get("expected_feedback") or "").strip().lower()

    # ★ R1(2026-08-28): AI 控件意图优先 — scene/fields/button/expect
    #   由 LLM 一次提取（带缓存），正则只在 AI 不可用时兜底。
    #   铁律: 理解交给 AI，规则只兜底。
    _intent: dict[str, Any] = {}
    try:
        from src.core.semantic_extractor import extract_control_intent

        _intent = extract_control_intent(_op)
    except Exception:
        _intent = {}
    _ai_scene = str(_intent.get("scene") or "").strip()
    _ai_fields = [str(f).strip() for f in (_intent.get("fields") or []) if str(f).strip()]
    _ai_button = str(_intent.get("button") or "").strip()
    _ai_expect = str(_intent.get("expect") or "").strip()

    # navigation: 从module构建侧边栏路径
    _nav = f"侧边栏→{_module}" if _module else "侧边栏菜单"

    # inputs: 从operation提取关键动作词，使用更具体的测试值
    _inputs = {}
    # ★  根因修复: 原硬编码"新增→名称"，但真实表单字段常是
    #   "姓名/年龄/电话"（需求文本括号内标注）→ 填充"名称"找不到元素。
    #   优先从 operation 括号提取真实字段（"填姓名/年龄/电话等"→姓名/年龄/电话）。
    #   通用性: 跨系统/行业 — 需求条目括号内的字段清单是表单字段的可靠来源。
    # ★ R1(2026-08-28): AI 提取的 fields 优先（LLM 理解括号语义 + 过滤噪音）。
    _field_vals = {}
    if _ai_fields:
        for _it in _ai_fields:
            if 1 < len(_it) <= 6 and _it not in ("填", "表单", "信息"):
                _field_vals[_it] = "TEST_%s" % _it
    if not _field_vals:
        _bracket_m = re.search(r"[（(](?:填|填写|录入|输入)?([^）)]+)[）)]", _op)
        if _bracket_m:
            for _it in _bracket_m.group(1).split("/"):
                _it = re.sub(r"等.*$", "", _it).strip()
                _it = re.sub(r"[，,、\s].*$", "", _it)
                # ★ 2026-08-28 场景驱动: 过滤动作/状态噪音（"需提交"、"选患者"、
                #   "请选择"等非字段名）——括号内容可能是操作提示而非表单字段。
                if (
                    1 < len(_it) <= 6
                    and _it not in ("填", "表单", "信息")
                    and not _it.startswith(_noise_prefix)
                ):
                    _field_vals[_it] = "TEST_%s" % _it
    _action_keywords = {
        "搜索": ("搜索关键词", "TEST001"),
        "查询": ("查询条件", "TEST001"),
        "新增": ("名称", "TEST_新增"),
        "创建": ("名称", "TEST_创建"),
        "编辑": ("名称", "TEST_编辑"),
        "修改": ("名称", "TEST_修改"),
        "删除": ("名称", "TEST_删除"),
        "导入": ("文件", "TEST_导入数据.xlsx"),
        "导出": ("导出条件", "TEST_导出"),
        "录入": ("数据值", "TEST_录入_001"),
        "配置": ("配置项", "TEST_配置"),
        "设置": ("设置项", "TEST_设置"),
        "上传": ("文件", "TEST_上传文件.pdf"),
        "填报": ("数据值", "TEST_填报_001"),
        "登记": ("数据值", "TEST_登记_001"),
        "注册": ("用户名", "TEST_USER_001"),
    }
    if _field_vals:
        _inputs = {
            k: {"value": v, "source": "semantic_infer"} for k, v in _field_vals.items()
        }
    else:
        for _kw, (_field, _val) in _action_keywords.items():
            if _kw in _op:
                _inputs[_field] = {"value": _val, "source": "semantic_infer"}
                break

    # ★ 2026-08-09 精细化: 从 operation 提取精确的 expect 值
    # ★ R1(2026-08-28): AI 提取的 expect 优先（LLM 理解"提交后列表出现新患者"→期望）
    if _ai_expect and len(_ai_expect) <= 12:
        _expect_value = _ai_expect
    else:
        _expect_value = "操作成功"
        if any(kw in _op for kw in ["新增", "创建", "新建", "添加"]):
            _expect_value = "创建成功"
        elif any(kw in _op for kw in ["编辑", "修改", "更新"]):
            _expect_value = "编辑成功"
        elif any(kw in _op for kw in ["删除", "移除"]):
            _expect_value = "删除成功"
        elif any(kw in _op for kw in ["搜索", "查询"]):
            _expect_value = "显示搜索结果"
        elif any(kw in _op for kw in ["保存", "提交"]):
            _expect_value = "保存成功"
        elif any(kw in _op for kw in ["导入"]):
            _expect_value = "导入成功"
        elif any(kw in _op for kw in ["导出"]):
            _expect_value = "导出成功"
        elif any(kw in _op for kw in ["注册"]):
            _expect_value = "注册成功"

    # verifications: ★ T01(2026-08-06): 不再生成「页面显示XX相关功能」模糊语义断言
    #   （该断言会被 _navigation_resolver DROP_EXPECT_KW 清洗，纯摆设）。
    #   改为调用确定性方法论规则模板（'playwright'/'' 归一为 FD 场景法），
    #   模板 expect 是短文本确定性断言，天然兼容 _fix_explore_task 的保留规则。
    from ._test_method_rules import apply_method_rules, merge_verifications

    # ★ 2026-08-28 场景驱动: 删除/移除类操作无表单（点击→confirm弹窗→列表更新），
    #   不能套用 FD 表单模板（fill"需提交"等字段噪音 → 填充失败 ERROR）。
    #   删除类: inputs 清空 + verifications 只含"点击删除按钮 + 验证删除成功"。
    # ★ R1(2026-08-28): AI scene 优先（LLM 理解"删除/查看"语义，正则关键词兜底）
    _is_delete_op = (_ai_scene == "delete") or any(k in _op for k in ("删除", "移除"))
    # ★ 2026-08-28 查看类分支（用户洞察: 通用性=场景=控件组合）:
    #   "医生列表展示医生信息"/"可查看预约详情"等查看类用例无表单，若套用 FD 表单
    #   模板 → 生成 fill"输入框"（字段提取失败占位符）→ 必然 ERROR。
    #   修复: 查看类关键词命中且无括号字段提示（纯查看，非"新增患者（填...）"）
    #   → 只生成 verify（验证页面文本），不生成 fill。
    #   通用性: 跨系统/行业 — 列表/展示/详情/浏览/查看是查看场景的稳定信号。
    _VIEW_KW = ("列表", "展示", "详情", "浏览", "查看", "访问", "进入")
    _OP_KW = ("新增", "创建", "编辑", "删除", "移除", "导入", "上传",
              "提交", "保存", "搜索", "查询", "填报", "登记", "注册")
    # ★ 2026-08-28 查看类判定: 查看关键词命中且无操作动词 → 查看类。
    #   注意: 括号字段在查看类里是"列表展示的列名"（如"患者列表页展示全部
    #   患者（姓名/年龄/电话）"）而非表单字段 → 不能用 not _field_vals 排除
    #   （那会把查看类误判为表单类，生成 fill 步骤 → 必失败）。
    #   操作动词保护: "可新增患者...提交后列表出现新患者" 含"列表"但含"新增"
    #   → 仍判为表单类。
    # ★ R1: AI scene == view 直接命中（LLM 理解"列表展示"是查看而非表单）。
    _is_view_op = (_ai_scene == "view") or (
        any(k in _op for k in _VIEW_KW)
        and not any(k in _op for k in _OP_KW)
    )
    # ★ 通用修复(2026-09-24): 登录失败/错误凭证/鉴权拒绝场景识别。
    #   根因: "提交错误凭证登录"类 operation 落入 FD 表单模板但 _inputs 为空 →
    #     primary_input="输入框"/valid_value="TEST_数据" → fill 无元素 → 全家 ERROR。
    #   通用性: 登录/凭证/鉴权 + 错误/失败/无效/拒绝 语义 → 错误凭证输入 + 拒绝断言。
    #   与 _guided_verify 的拒绝语义 DOM 判定（错误条/401/403/重定向）配合，拒绝被
    #   正确反馈即真实 PASS。
    #   ★ 判定入口唯一化(2026-09-24): 关键词表收敛到 _rule_extractor_keywords.is_login_reject，
    #     与 _fallback_cases（规则用例动作）共用同一事实源，杜绝两套口径漂移。
    from src.pipeline.step3._rule_extractor_keywords import is_login_reject

    _is_login_reject = is_login_reject(_op, _expect_fb)
    if _is_login_reject:
        _inputs = {
            k: {"value": v, "source": "scenario_login_fail"}
            for k, v in (("用户名", "wrong_user_9347"), ("密码", "wrong_pass_9347"))
        }
        _verifications = [
            {"step": 1, "action": "fill", "target": "用户名", "value": "wrong_user_9347",
             "expect": "输入成功"},
            {"step": 2, "action": "fill", "target": "密码", "value": "wrong_pass_9347",
             "expect": "输入成功"},
            {"step": 3, "action": "click", "target": "登录,提交,确定", "value": "",
             "expect": "提交错误凭证"},
            {"step": 4, "action": "verify", "target": "操作结果", "value": "",
             "expect": "错误凭证被拒绝，提示错误信息/登录失败"},
        ]
    elif _is_delete_op:
        _inputs = {}
        # ★ 删除类: ②块会自动从 operation 提取"删除患者"并点击（confirm 弹窗
        #   自动 accept），verifications 只需验证删除结果；不要再加 click"操作按钮"
        #   （字面文本找不到元素 → 冗余失败）。
        _verifications = [
            {
                "step": 1,
                "action": "verify",
                "target": "操作结果",
                "value": "",
                "expect": _expect_value or "删除成功",
            },
        ]
    elif _is_view_op:
        # 查看类: 只验证页面展示内容（实体词，如"医生信息"）
        _inputs = {}
        _view_entity = extract_module_from_operation(_op) or ""
        if len(_view_entity) > 4:
            _view_entity = _view_entity[:2]
        _verifications = [
            {
                "step": 1,
                "action": "verify",
                "target": "页面内容",
                "value": "",
                "expect": "显示%s信息" % (_view_entity or ""),
            },
        ]
    else:
        _rule_steps = apply_method_rules(
            {"method": "FD", "inputs": _inputs}, "composite", explore_task
        )
        _verifications = merge_verifications([], _rule_steps)
    if not _verifications:
        _verifications = [
            {
                "step": 1,
                "action": "verify",
                "target": "操作结果",
                "value": "",
                "expect": _expect_value,
            },
        ]

    # ── ★ 根因修复(2026-08-09): 从 explore_task 读取字段名，生成精确的字段展示验证 ──
    _fn_list = explore_task.get("_field_verbose_names") or []
    if isinstance(_fn_list, list) and _fn_list:
        _fn_str = ",".join(str(f).strip() for f in _fn_list[:6] if str(f).strip())
        if _fn_str:
            _verifications.append({
                "step": len(_verifications) + 1,
                "action": "verify",
                "target": "页面表格",
                "value": "",
                "expect": "页面表格包含字段: %s" % _fn_str,
            })

    _log.info(
        "[ExploreTaskBuilder] 构建默认test_method: module=%s op=%s expect=%s",
        _module[:40],
        _op[:40],
        _expect_value,
    )
    return {
        "navigation": _nav,
        "inputs": _inputs,
        "verifications": _verifications,
    }


# 业务动作类型：test_method.verifications 中含这些动作才算"真正执行了操作"
_BIZ_ACTION_TYPES = {
    "fill", "click", "submit", "select_option", "press_key", "clear",
    "double_click", "set_date", "check", "uncheck", "upload_file",
}
# 需求文档特征词：navigation 含这些词说明拿需求章节当菜单（导航必失败）
_REQ_DOC_KEYWORDS = ("需求规格", "需求文档", "需求描述", "需求明细", "PRD", "需求-")


def _has_biz_action(verifications: list) -> bool:
    """verifications 是否含真正的业务动作（fill/click/submit 等）。"""
    if not isinstance(verifications, list):
        return False
    for _v in verifications:
        if not isinstance(_v, dict):
            continue
        _act = str(_v.get("action") or "").strip().lower()
        if _act in _BIZ_ACTION_TYPES:
            return True
    return False


def _validate_and_repair_test_method(tm: dict, et: dict) -> dict:
    """校验并修复 AI 生成的 test_method（确定性优先，防假PASS）。

    根因(2026-08-27 验收实证): AI 生成的 test_method 常见三类质量问题，
      导致 Step4 Agent 执行假 PASS：
      1. navigation 用需求文档标题/章节名（如"侧边栏→医院诊所管理系统需求规格"）
         而非真实业务菜单 → 导航必失败，Agent 停留在无关页面；
      2. verifications 全是页面级 verify（"页面显示X相关功能"/"验证 REQ-XXX 需求"），
         无 fill/click/submit 动作链 → 业务操作从未执行，宽泛文本匹配即 PASS；
      3. expect 直接引用需求编号/需求原文，而非可证伪的业务结果断言。

    修复策略（确定性 > 概率性）:
      - operation 含操作动词但 verifications 无任何业务动作 → 用确定性规则模板
        _build_default_test_method 重建（含 fill 链与操作结果验证）；
      - navigation 含需求文档特征词 → 从 operation 提取业务模块重建导航。
    通用性: 跨系统/行业 — 纯规则校验，零业务硬编码；所有 AI 生成用例统一收口。

    返回: 修复后的 test_method（原样返回则未发现问题）。
    """
    if not isinstance(tm, dict):
        return tm
    _verifs = tm.get("verifications") or []
    _nav = str(tm.get("navigation") or "").strip()
    _op = str(et.get("operation") or "").strip() if isinstance(et, dict) else ""

    _need_repair = False
    _op_verbs = ("新增", "创建", "新建", "添加", "编辑", "修改", "更新", "删除",
                 "移除", "提交", "保存", "导入", "上传", "登记", "注册", "填报", "录入")
    _has_verb = any(v in _op for v in _op_verbs)
    if _has_verb and not _has_biz_action(_verifs):
        _need_repair = True
        _log.warning(
            "[ExploreTaskBuilder] test_method 修复: operation=%r 含操作动词但无业务动作链，"
            "重建为确定性规则模板",
            _op[:50],
        )
    if _nav and any(kw in _nav for kw in _REQ_DOC_KEYWORDS):
        _need_repair = True
        _log.warning(
            "[ExploreTaskBuilder] test_method 修复: navigation 含需求文档特征词 %r → 重建",
            _nav[:50],
        )

    if not _need_repair:
        return tm

    _rebuilt = _build_default_test_method(et)
    # 保留 AI 的 inputs（若与默认不冲突），否则用默认
    _ai_inputs = tm.get("inputs") or {}
    _reb_inputs = _rebuilt.get("inputs") or {}
    if isinstance(_ai_inputs, dict) and _ai_inputs:
        _merged = dict(_reb_inputs)
        _merged.update(_ai_inputs)
        _rebuilt["inputs"] = _merged
    _rebuilt["_repaired_by"] = "validate_and_repair"
    _log.info(
        "[ExploreTaskBuilder] test_method 修复完成: nav=%r verifications=%d inputs=%d",
        str(_rebuilt.get("navigation", ""))[:40],
        len(_rebuilt.get("verifications") or []),
        len(_rebuilt.get("inputs") or {}),
    )
    return _rebuilt


def _attach_declared_actions(case: dict) -> None:
    """把用例声明的权威动作序列带入 `_explore_task.declared_actions`。

    根因（SSOT 违背）: 用例的动作序列有两个副本 ——
      ① `case_actions_json` / `test_steps`（Step3 落库的结构化真实动作）；
      ② `_explore_task.test_method`（由 module+operation 文本**重新猜**的一套）。
    Step4 的 AgentExplorer 路径只消费 ②，① 从未被读取（browser_core 内零引用），
    于是「导航去哪、点什么」这一条语义被两处各自推导 —— 用例声明的动作被丢弃，
    执行端拿文档章节名当菜单名去猜，猜不到即 ERROR。
    修法: 语义只在上游构造一次 —— 把声明动作归一为结构化 `declared_actions`
    随 `_explore_task` 下发，执行端只读不推。

    通用性: 跨系统/行业 — 归一规则只依赖动作类型枚举与 target_hint 文本，
    零业务/模块硬编码。幂等：重复调用结果一致。
    """
    if not isinstance(case, dict):
        return
    _et = case.get("_explore_task")
    if not isinstance(_et, dict):
        return
    if _et.get("declared_actions"):
        return
    _raw = case.get("case_actions_json") or case.get("test_steps") or []
    if isinstance(_raw, str):
        try:
            import json as _json

            _raw = _json.loads(_raw)
        except Exception:
            _raw = []
    if not isinstance(_raw, list) or not _raw:
        return
    _norm: list[dict] = []
    for _a in _raw:
        if isinstance(_a, str):
            _a = {"type": _a}
        if not isinstance(_a, dict):
            continue
        _t = str(_a.get("type") or _a.get("action") or "").strip().lower()
        if not _t:
            continue
        _item = {"type": _t}
        _hint = str(
            _a.get("target_hint") or _a.get("target") or _a.get("selector") or ""
        ).strip()
        if _hint:
            _item["target_hint"] = _hint
        _url = str(_a.get("url") or "").strip()
        if _url and _t == "navigate":
            _item["url"] = _url
        # 施加值任意取值位：value（fill/check）或 option_text（select_option）。
        # 只取 value 会让声明的下拉选项从未被选中（以空值执行 = 空操作）。
        _val = _a.get("value")
        if _val is None or str(_val) == "":
            _val = _a.get("option_text")
        if _val is not None and str(_val) != "":
            _item["value"] = _val
        _norm.append(_item)
    if _norm:
        _et["declared_actions"] = _norm


def inject_explore_task_to_cases(cases: list[dict]) -> None:
    """为每个用例注入 _explore_task（复制 explore_task），使 Step4 能识别。

    根因: Step3 AI 生成 explore_task，但 Step4 检查 _explore_task（下划线前缀），
          字段名不匹配 → AgentExplorer 路径从未被触发。
    ★ v5.4: 同时复制 test_method 字段，确保 Agent 收到结构化测试方法。
    ★ 方案B(2026-07-29): AI未生成test_method时自动构建默认值，确保Agent至少有基本引导。
    ★ 方案B修复(2026-07-29): 旧代码路径（case_rules/base.py等）直接创建_explore_task
          但不含test_method，导致本函数跳过。新增elif分支处理已有_explore_task但缺test_method的情况。
    通用性: 跨系统/行业/页面 — 所有 AI 生成的用例都受益。

    ★ T6 流水线强化专项(2026-08-24) 字段匹配契约固化（design §1.2 #5 / PRD Q3）：
      - Step3 产出字段名：``explore_task``（无下划线前缀）
      - Step4 消费字段名：``_explore_task``（下划线前缀）—— 双路径路由
        ExecutionRouter.route_case 以 ``_explore_task`` 是否存在判定
        「AgentExplorer 实时探索路径」vs「经验/探针 step loop 路径」。
      - 本函数是两字段之间的**唯一桥接点**：复制 explore_task → _explore_task
        并补全 test_method；任何新建用例生成路径都必须经此函数收口，
        否则字段名不匹配会再次导致 AgentExplorer 路径静默失效（历史根因）。
    """
    for _c in cases:
        if not isinstance(_c, dict):
            continue
        if "explore_task" in _c and "_explore_task" not in _c:
            _c["_explore_task"] = _c["explore_task"]
            _c["_exploratory"] = True
            _et = _c["explore_task"]
            # ★ 通用兜底(2026-08-25): AI 可能把 explore_task 生成为字符串（畸形输出），
            #   导致下方 _build_default_test_method(_et) 崩溃（str 无 .get）。规范化为 dict。
            if not isinstance(_et, dict):
                _et = {"operation": str(_et).strip()} if _et else {}
                _c["explore_task"] = _et
                _c["_explore_task"] = _et

            # ★ 方案B(2026-07-29): 清理AI生成的module噪音（文档章节标题→业务模块名）
            _raw_mod = (
                (_et.get("module") or "").strip() if isinstance(_et, dict) else ""
            )
            _cleaned_mod = _clean_module_name(_raw_mod)
            _op_text = (
                (_et.get("operation") or "").strip() if isinstance(_et, dict) else ""
            )
            if not _cleaned_mod and _op_text:
                _cleaned_mod = _clean_module_name(
                    extract_module_from_operation(_op_text)
                )
            if _cleaned_mod and _cleaned_mod != _raw_mod:
                _c["_explore_task"]["module"] = _cleaned_mod
                _log.info(
                    "[ExploreTaskBuilder] inject: module清理 %r → %r",
                    _raw_mod[:40],
                    _cleaned_mod,
                )
            elif not _cleaned_mod and _raw_mod:
                # ★ 兜底：清理后为空但原始module非空 → 尝试从operation提取
                _from_op = _clean_module_name(extract_module_from_operation(_op_text))
                if _from_op:
                    _c["_explore_task"]["module"] = _from_op
                    _log.info(
                        "[ExploreTaskBuilder] inject: module从op提取 %r → %r",
                        _raw_mod[:40],
                        _from_op,
                    )
                else:
                    # ★ 兜底：module仍是噪音，直接置空
                    _c["_explore_task"]["module"] = ""
                    _log.info(
                        "[ExploreTaskBuilder] inject: module噪音无法清理 %r → 置空",
                        _raw_mod[:40],
                    )

            # ★ v5.4: 确保 test_method 也被复制到 _explore_task
            if isinstance(_et, dict) and "test_method" in _et and _et["test_method"]:
                # ★ : 校验并修复 AI 生成的 test_method（防假PASS——
                #   无业务动作链/需求章节导航 → 确定性重建）
                _c["_explore_task"]["test_method"] = _validate_and_repair_test_method(
                    _et["test_method"], _et
                )
            else:
                # ★ 方案B: AI未生成test_method → 自动构建默认值
                _c["_explore_task"]["test_method"] = _build_default_test_method(_et)
            # 用例声明的权威动作序列随 _explore_task 下发（执行端只读不推）。
            _attach_declared_actions(_c)
        elif isinstance(_c.get("_explore_task"), dict):
            # ★ 方案B修复(2026-07-29): 旧代码路径已创建_explore_task但缺test_method
            _et = _c["_explore_task"]
            _has_tm = "test_method" in _et and _et["test_method"]
            if not _has_tm:
                _c["_explore_task"]["test_method"] = _build_default_test_method(_et)
                _log.info(
                    "[ExploreTaskBuilder] inject: 补test_method到已有_explore_task module=%r",
                    str(_et.get("module", ""))[:40],
                )
            # ★ 方案B修复(2026-07-29): elif分支也清理module噪音
            _raw_mod = (_et.get("module") or "").strip()
            _cleaned_mod = _clean_module_name(_raw_mod)
            _op_text = (_et.get("operation") or "").strip()
            if not _cleaned_mod and _op_text:
                _cleaned_mod = _clean_module_name(
                    extract_module_from_operation(_op_text)
                )
            if _cleaned_mod and _cleaned_mod != _raw_mod:
                _c["_explore_task"]["module"] = _cleaned_mod
                _log.info(
                    "[ExploreTaskBuilder] inject(elif): module清理 %r → %r",
                    _raw_mod[:40],
                    _cleaned_mod,
                )
            elif not _cleaned_mod and _raw_mod:
                # ★ 清理后为空但原始module非空 → 尝试从operation提取
                _from_op = _clean_module_name(extract_module_from_operation(_op_text))
                if _from_op:
                    _c["_explore_task"]["module"] = _from_op
                    _log.info(
                        "[ExploreTaskBuilder] inject(elif): module从op提取 %r → %r",
                        _raw_mod[:40],
                        _from_op,
                    )
                else:
                    # ★ 兜底：module仍是噪音，直接置空（让Agent走自由探索）
                    _c["_explore_task"]["module"] = ""
                    _log.info(
                        "[ExploreTaskBuilder] inject(elif): module噪音无法清理 %r → 置空",
                        _raw_mod[:40],
                    )
            # 用例声明的权威动作序列随 _explore_task 下发（执行端只读不推）。
            _attach_declared_actions(_c)
