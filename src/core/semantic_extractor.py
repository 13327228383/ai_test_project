"""semantic_extractor.py — AI 语义提取：需求条目 → 控件组合意图

★ 架构铁律（2026-08-28 用户拍板）：理解交给 AI，规则只兜底。
  需求条目（如"REQ-PRE-01：可为患者开处方（诊断/药品/用量） 处方列表展示"）
  → 一次 LLM 调用提取结构化意图 {entity, scene, fields[], button, expect}，
  正则（_rule_fallback）只在 LLM 失败/超时/不可解析时降级兜底。

  通用性: 跨系统/行业 — 控件组合意图与具体系统无关；
  智能性: 真实 LLM 调用（本地 qwen2.5:7b / 云端强模型双路由）。
"""

from __future__ import annotations
from src.core.guarded import guard

import hashlib
import json
import logging
import re
from typing import Any

_log = logging.getLogger(__name__)

# 内存缓存：operation hash → intent dict（同条目不重复调 LLM，省 ~4s/次）
_INTENT_CACHE: dict[str, dict[str, Any]] = {}
# 重入保护：AI 提取过程中，规则兜底又调回 extract_control_intent 时直接返回空
#   （避免 extractor → fallback → extract_module_from_operation → extractor 递归）
_AI_IN_PROGRESS: set[str] = set()

# ★ 磁盘持久化缓存：LLM 提取 ~10s/次且内存缓存不跨进程（Step3 重建进程 vs
#   Step4 执行进程）→ 持久化到 data/semantic_intent_cache.json，
#   Step3 生成时预热、Step4 执行时命中（0ms）。通用性: 与项目无关。
import os as _os


# 词法噪声 token 唯一源（下游 import 引用，勿重抄）
_NOISE_TOKENS = ("需", "请", "填", "输", "选", "提交", "保存")

_CACHE_FILE = _os.path.join(
    _os.path.dirname(__file__), "..", "..", "data", "semantic_intent_cache.json"
)

# 已验证的 few-shot prompt（qwen2.5:7b 实测 schema 遵循正确）
_PROMPT_TMPL = """你是Web自动化测试引擎的需求理解模块。把需求条目解析为 JSON：
{"entity":"实体名(2字)","scene":"view/create/edit/delete/login/other","fields":["字段名"],"button":"按钮文本","expect":"期望"}
只输出 JSON。示例：
"REQ-PAT-02：可新增患者（填姓名/年龄/电话）" -> {"entity":"患者","scene":"create","fields":["姓名","年龄","电话"],"button":"新增患者","expect":"创建成功"}
"REQ-PAT-01：患者列表页展示全部患者" -> {"entity":"患者","scene":"view","fields":[],"button":"","expect":"显示患者信息"}
需求条目: {op}"""

_SCENES = {"view", "create", "edit", "delete", "search", "login", "other"}


def _norm_operation(operation: str) -> str:
    """归一化：去掉测试变体标注（form:boundary / view:error_path / SQL注入等），
    让同一需求的多个变体共享同一份 AI 意图（变体标注是测试类型，非业务意图）。"""
    return re.sub(
        r"[（(]\s*(?:form|view|api|db)\s*[:：][^）)]*[）)]", "", operation
    ).replace("（SQL注入安全测试）", "").replace("（XSS安全测试）", "").replace(
        "（CSRF防护测试）", ""
    ).strip()


def _cache_key(operation: str, project: str = "") -> str:
    return hashlib.md5(f"{project}|{_norm_operation(operation)}".encode("utf-8")).hexdigest()


def _load_disk_cache() -> dict[str, dict[str, Any]]:
    try:
        with open(_CACHE_FILE, encoding="utf-8") as f:
            obj = json.load(f)
            return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def _save_disk_cache() -> None:
    with guard("_save_disk_cache: _os.makedirs(_os.path.dirname(_CACHE", log=_log):
        _os.makedirs(_os.path.dirname(_CACHE_FILE), exist_ok=True)
        with open(_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(_INTENT_CACHE, f, ensure_ascii=False)


def extract_control_intent(
    operation: str, project: str = "", use_ai: bool = True
) -> dict[str, Any]:
    """需求条目 → 控件组合意图（主入口）。

    AI 优先（带缓存）；LLM 失败/超时/不可解析 → 正则兜底（_rule_fallback）。
    返回 dict，至少含 entity/scene，缺省字段用默认值。
    """
    op = (operation or "").strip()
    if not op:
        return {}
    key = _cache_key(op, project)
    if key in _INTENT_CACHE:
        return _INTENT_CACHE[key]
    # 磁盘缓存（跨进程：Step3 预热 → Step4 执行命中，0ms）
    disk = _load_disk_cache()
    if key in disk:
        _INTENT_CACHE[key] = disk[key]
        return _INTENT_CACHE[key]
    if key in _AI_IN_PROGRESS:
        # 重入：规则兜底又调回（fallback → extract_module_from_operation → 本函数）
        return {}

    intent: dict[str, Any] = {}
    _AI_IN_PROGRESS.add(key)
    try:
        if use_ai:
            intent = _ai_extract(op)
        if not intent:
            intent = _rule_fallback(op)
    finally:
        _AI_IN_PROGRESS.discard(key)

    # 归一化 + 默认值
    _scene = str(intent.get("scene") or "other").strip().lower()
    if _scene not in _SCENES:
        _scene = "other"
    intent = {
        "entity": str(intent.get("entity") or "").strip(),
        "scene": _scene,
        "fields": [str(f).strip() for f in (intent.get("fields") or []) if str(f).strip()],
        "button": str(intent.get("button") or "").strip(),
        "expect": str(intent.get("expect") or "").strip(),
    }
    _INTENT_CACHE[key] = intent
    _save_disk_cache()
    return intent


def clear_intent_cache() -> None:
    """清空缓存（测试/靶子切换时用）。"""
    _INTENT_CACHE.clear()


# ─────────────────────────────────────────────────────────────
# AI 提取（主理解路径）
# ─────────────────────────────────────────────────────────────


def _ai_extract(operation: str) -> dict[str, Any] | None:
    """LLM 提取控件意图。成功且含 entity+scene → dict；否则 None（降级）。"""
    try:
        from src.ai.router_module import router
        from src.core.pipeline_config import _read_config_value as _cfg

        r = router.route(
            # ★ 用 replace 而非 .format：模板内 JSON 示例含花括号（{"entity":...}），
            #   .format 会把它们当占位符 → KeyError: 'entity'
            _PROMPT_TMPL.replace("{op}", operation[:200]),
            system_prompt="你是Web测试助手，只输出JSON。",
            expect_json=True,
            task_type="struct",
            # ★ F2 全局修复 [未复核-需 GLM/DeepSeek 把关]（2026-08-29）：
            #   主理解路径确定性化（审计核心缺口：原默认 temperature=0.3/seed=None）。
            #   seed 读 pipeline_config.ai_stability_seed，不硬编码 42。
            temperature=0.0,
            seed=(None if (_seed_v := _cfg("ai_stability_seed", 42)) is None else int(_seed_v)),
        )
        data = r.get("data", "") if isinstance(r, dict) else r
        if isinstance(data, str):
            txt = data.strip().strip("`").strip()
            if txt.startswith("json"):
                txt = txt[4:].strip()
            data = json.loads(txt)
        if not isinstance(data, dict):
            _log.warning("[semantic] AI 返回非对象，降级: %r", str(data)[:80])
            return None
        if not data.get("entity") or not data.get("scene"):
            _log.warning(
                "[semantic] AI 结果缺 entity/scene，降级: %r", str(data)[:80]
            )
            return None
        _log.info("[semantic] AI 提取成功: %r → %r", operation[:40], data)
        return data
    except Exception as e:
        _log.warning("[semantic] AI 提取异常(降级正则): %s", str(e)[:100])
        return None


# ─────────────────────────────────────────────────────────────
# 规则兜底（仅 LLM 不可用时；禁止新增"模拟理解"的正则）
# ─────────────────────────────────────────────────────────────


def _rule_fallback(operation: str) -> dict[str, Any]:
    """正则兜底：仅 LLM 不可用时。只提取 entity + scene + 括号字段
    （复用现有生成器提取逻辑，禁止新增"模拟理解"的正则）。
    注意: 不调用 _build_default_test_method（避免 builder↔extractor 递归）。
    """
    try:
        from src.pipeline.steps.step3.case_generator._explore_task_builder import (  # noqa: E501
            extract_module_from_operation,
        )

        entity = extract_module_from_operation(operation) or ""
        if len(entity) > 4:
            entity = entity[:2]
        # 括号字段（"填姓名/年龄/电话" → 姓名/年龄/电话）
        fields: list[str] = []
        _bm = re.search(r"[（(](?:填|填写|录入|输入)?([^）)]+)[）)]", operation)
        if _bm:
            for _it in _bm.group(1).split("/"):
                _it = re.sub(r"等.*$", "", _it).strip()
                _it = re.sub(r"[，,、\s].*$", "", _it)
                if 1 < len(_it) <= 6 and not _it.startswith(_NOISE_TOKENS):
                    fields.append(_it)
        # 场景判定（简单规则，兜底用）
        scene = "other"
        if any(k in operation for k in ("登录", "login")):
            scene = "login"
        elif any(k in operation for k in ("删除", "移除")):
            scene = "delete"
        elif any(k in operation for k in ("编辑", "修改", "更新")):
            scene = "edit"
        elif any(k in operation for k in ("新增", "创建", "新建", "添加")):
            scene = "create"
        elif any(k in operation for k in ("列表", "展示", "详情", "查看", "浏览")):
            scene = "view"
        return {
            "entity": entity,
            "scene": scene,
            "fields": fields,
            "button": "",
            "expect": "",
        }
    except Exception as e:  # 极端兜底
        _log.debug("[semantic] 规则兜底异常: %s", str(e)[:80])
        return {"entity": "", "scene": "other", "fields": [], "button": "", "expect": ""}
