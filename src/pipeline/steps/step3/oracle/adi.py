# -*- coding: utf-8 -*-
"""ADI 计算内核（T01，W2 P0-3）—— 深度式 Assertion Depth Index。

口径（已拍板，深度式 max 而非计数式）：
  adi = max(s(a) for a in 全部可编译断言)，0~3；无断言 → 0。
  count 信息保留在 adi_detail.assert_count / track_count（可随时切换口径）。

数据来源（并集，去重）：
  1. case["verification_plan"]{ui,api,db} —— W1 复活的 Step3 三轨深度计划
     （复用 verification_plan_builder._normalize_track，兼容 list / {"checks":[]} 两形态）
  2. case["checks"] / case["payload"]["checks"]
     （复用 verification_plan_builder._parse_checks，兼容 JSON 字符串与 payload 间接层）

归一取值字段（★ 两个字段名不同，写错一个会有一半恒 0 分）：
  plan 侧 = evidence；checks 侧 = type
  归一函数 = browser_tools._taxonomy.resolve_check_type（46 族权威枚举 + 别名表）

去重键：(track, 归一后族名, assertion/expect 前 64 字符)
  —— verification_plan 与 checks 常是同一批断言的两种形态，会重复。
  —— 禁止用 id() 或整 dict 哈希（dict 含 confidence: None 等噪声字段）。

fail-closed：
  - 任何内部异常 → (0, {"error": "..."})，绝不返回高分。
  - 归一失败（词表漂移/幻觉）→ 0 分 + 记入 unresolved（仍计入 assert_count 分母）。
  - 新增族（不在评分表）→ 0 分 + 告警（防静默误判）。

★ §14 关键改动 [已复核-DeepSeek-V4-Flash 2026-08-05]：
  W2 P0-3 计算内核（≥2 文件），评分表族名必须与 CHECK_TAXONOMY 逐一对齐；
  未经过强模型（GLM/DeepSeek）复核，请勿视为已审核。
  ★ F2 修复（2026-08-05 W3）：去重键 text 为空时用 hint 兜底进 key，
    避免同轨同族断言因空 text 被误并（text[:64] → (text or hint)[:64]）。
"""
from __future__ import annotations

import json
import logging
from typing import Any

from src.pipeline.steps.step4.browser_tools._taxonomy import (
    CHECK_TAXONOMY,
    resolve_check_type,
)
from src.pipeline.steps.step4.verification_plan_builder import (
    _normalize_track,
    _parse_checks,
)

_log = logging.getLogger(__name__)

# ── ADI 深度评分表（与 CHECK_TAXONOMY 逐族对齐；覆盖率由 check_taxonomy_coverage() 断言）──
#   ★ F5 (2026-09-27)：键统一为「CHECK_TAXONOMY 权威族名（裸名）」，不再用 xxx_check 形态，
#     避免 resolve_check_type 归一后与评分表键不匹配（历史 4 族曾按 _check 键硬编码 →
#     裸名形态仍 0 分）。漂移门禁见 tests/test_oracle/test_taxonomy_drift.py。
#   分值语义：
#     3 值级/跨轨     —— 校验到具体数据值 / 跨源一致性（最高深度）
#     2 属性/协议/文本 —— 校验属性、协议、文本内容级
#     1 存在性/状态/性能 —— 校验元素存在、状态、性能指标（最浅）
#     0 归一失败       —— 无法归一到 46 族（词表漂移/幻觉），记 unresolved
ADI_SCORE_TABLE: dict[str, int] = {
    # ── 3 分 值级/跨轨 (13) ──
    "db_record_exists": 3,
    "db_record_count": 3,
    "db_record_not_exists": 3,
    "db_field_value": 3,
    "db_query": 3,
    "db_ui_consistency": 3,
    "api_response_body": 3,
    "api_contract": 3,  # ★ W3 T04 (2026-08-05): 契约校验族（必填字段/类型/业务码，值级）
    "request_body": 3,
    "text_equals": 3,
    "element_attribute": 3,
    "element_count": 3,
    "storage": 3,
    "time_range": 3,
    "list_row_count": 3,  # ★ 2026-09-26 补齐 / F5 改名(2026-09-27): 精确行数计数=值级
    # ── 2 分 属性/协议/文本级 (18) ──
    "text_contains": 2,
    "text_regex": 2,
    "url_matches": 2,
    "select_value": 2,
    "element_checked": 2,
    "toast": 2,
    "api_status": 2,
    "api_header": 2,
    "api_call_count": 2,
    "element_attribute_all": 2,
    "list_order": 2,
    "pagination": 2,
    "text_not_exists": 2,
    "element_not_exists": 2,
    "download": 2,
    "dialog": 2,
    "network": 2,
    "websocket": 2,
    "semantic": 2,  # ★ 2026-09-26 补齐 / F5 改名(2026-09-27)：AI 语义文本判定=文本级
    "field_error_visible": 2,  # ★ 2026-09-26 补齐 / F5 改名：字段错误文本可见=属性/文本级
    # ★ F5 (2026-09-27)：补齐「执行端已注册、词表未登记」族的深度分（原先静默 0 分）
    "form_autocomplete": 2,  # WCAG 1.3.5 输入目的自动填充=属性级
    "form_label_association": 2,  # label/控件关联=属性级
    "field_constraint": 2,  # 控件 DOM 约束属性核对=属性级
    "access_control": 2,  # 访问控制行为断言（403/登录墙）=协议/行为级
    # ★ 2026-11-04：补齐 any_of / security_probe 的深度分（此前静默 0 分）
    "any_of": 2,  # 析取式组合断言：分支行为级（等价于其中最强分支的门槛）
    "security_probe": 3,  # 安全探针：载荷注入后的行为/内容级核验
    # ── 1 分 存在性/状态/性能级 (15) ──
    "element_exists": 1,
    "element_visible": 1,
    "element_enabled": 1,
    "no_error": 1,
    "loading_complete": 1,
    "image_loaded": 1,
    "console_error": 1,
    "style": 1,
    "position": 1,
    "geometry_check": 1,
    "visual_check": 1,
    "refresh": 1,
    "animation_complete": 1,
    "page_load_time": 1,
    "response_time": 1,
    "db_table_accessed": 1,  # ★ 2026-09-26 补齐 / F5 改名：表访问存在性探针=存在性级
    "click_target_size": 1,  # ★ F5：可点击目标尺寸（WCAG 2.5.8）=几何/存在性级
    "touch_target_mobile": 1,  # ★ F5：移动端触控目标尺寸=几何级
    "viewport_overflow": 1,  # ★ F5：视口水平溢出检查=几何/布局级
    "viewport_rendering": 1,  # ★ F5：多视口渲染无重叠=几何/布局级
}

# ── 解析层：evidence 标记（plan 侧）→ (族名, 分值) 别名表 ──
#   族名可能不在 46 族（如 db_state/db_field_changed，属自造族），分值由别名表显式给出。
#   归一顺序：taxonomy 优先（46 族 + resolve_check_type 已有别名），别名兜底。
#   ★ §14 复核重点：db_state(2)/db_field_changed(3) 为自造族分值，需强模型确认。
EVIDENCE_ALIAS: dict[str, tuple[str, int]] = {
    "dom_text": ("text_contains", 2),  # UI 文本断言（精确匹配精度）
    "visual": ("visual_check", 1),  # UI 模糊匹配精度 → 存在/视觉级
    "record_exists": ("db_record_exists", 3),  # DB 记录存在（值级）
    "record_not_exists": ("db_record_not_exists", 3),  # 与 ADI_SCORE_TABLE 同族同分
    "db_state": ("db_state", 2),  # DB 状态断言（状态级，非值级）——族名自造，分值显式
    "field_changed": ("db_field_changed", 3),  # 字段变更校验（值级）
    # ★ F4（2026-08-05 W3）：response_schema → api_response_body(3)。
    #   QA 实测真实库 TC-004-1 存在该 evidence，属真实深度断言被低估；
    #   api_response_body 已在 46 族评分表 = 3 分，别名仅补归一兜底。
    "response_schema": ("api_response_body", 3),  # API 响应体结构/字段校验（值级）
}

# 已知退化标记：不是真实断言，0 分但不告警、不记 unresolved
KNOWN_DEGENERATE: set[str] = {"unverified", "skipped"}

# 已告警过的缺失族（避免每个用例刷屏）
_WARNED_MISSING: set[str] = set()


def check_taxonomy_coverage() -> list[str]:
    """返回 CHECK_TAXONOMY 中未被评分表覆盖的族名（空 = 46/46 全覆盖）。

    T01 覆盖率断言单测直接消费此函数，防 taxonomy 新增族被静默按 0 分处理。
    """
    return [k for k in CHECK_TAXONOMY if k not in ADI_SCORE_TABLE]


def _warn_missing_family(family: str) -> None:
    """taxonomy 新增族未同步评分表时告警（防静默误判）。"""
    if family and family not in _WARNED_MISSING:
        _WARNED_MISSING.add(family)
        _log.warning(
            "[ADI] 检查族 '%s' 未在 ADI_SCORE_TABLE 中（taxonomy 新增族？）—— "
            "默认按 0 分处理（fail-closed），请同步评分表，否则该族深度断言被静默误判为幻觉。",
            family,
        )


def _extract_plan(case: dict[str, Any]) -> dict[str, Any]:
    """从 case 顶层或 payload 取 verification_plan（兼容 JSON 字符串）。"""
    plan = case.get("verification_plan")
    if isinstance(plan, dict):
        return plan
    payload = case.get("payload")
    if isinstance(payload, str) and payload.strip():
        try:
            payload = json.loads(payload)
        except (ValueError, TypeError):
            payload = None
    if isinstance(payload, dict):
        inner = payload.get("verification_plan")
        if isinstance(inner, dict):
            return inner
    return {}


def _resolve_hint(hint: str) -> tuple[str, int, bool]:
    """把 evidence/type hint 归一为 (最终族名, 分值, 是否已知退化标记)。

    1) taxonomy 优先：resolve_check_type（46 族 + 其自带别名表）
    2) EVIDENCE_ALIAS 兜底：plan 侧 evidence 标记 → 显式族名 + 显式分值
    3) 仍 0 分：KNOWN_DEGENERATE → degenerate=True（0 分但不告警、不记 unresolved）；
       否则 fail-closed 0 分（由调用方告警 + unresolved，语义不变）
    """
    family = resolve_check_type(hint)
    score = ADI_SCORE_TABLE.get(family, 0)
    if score == 0 and hint in EVIDENCE_ALIAS:
        family, score = EVIDENCE_ALIAS[hint]
    degenerate = bool(hint in KNOWN_DEGENERATE)
    return family, score, degenerate


def _collect_assertions(case: dict[str, Any]) -> list[dict[str, Any]]:
    """收集全部断言（verification_plan ∪ checks，含重复，由调用方去重）。

    返回元素：{"track","hint","text","family","score","degenerate"}
      - family 为**最终值**（含别名解析），score 为最终分值；
      - 解析只在此处完成一次，_compute_adi_impl 不再二次解析。
    """
    out: list[dict[str, Any]] = []

    # 1) verification_plan 各轨（plan 侧取值字段 = evidence）
    plan = _extract_plan(case)
    for track in ("ui", "api", "db"):
        for a in _normalize_track(plan.get(track)):
            if not isinstance(a, dict):
                continue
            hint = str(a.get("evidence") or a.get("type") or "").strip()
            text = str(a.get("assertion") or a.get("expect") or "").strip()
            family, score, degenerate = _resolve_hint(hint)
            out.append(
                {
                    "track": track,
                    "hint": hint,
                    "text": text,
                    "family": family,
                    "score": score,
                    "degenerate": degenerate,
                }
            )

    # 2) checks[]（checks 侧取值字段 = type；兼容嵌套 check dict）
    for c in _parse_checks(case):
        if not isinstance(c, dict):
            continue
        track = str(c.get("track") or "").strip().lower()
        hint = str(c.get("evidence") or c.get("type") or "").strip()
        if not hint:
            inner = c.get("check")
            if isinstance(inner, dict):
                hint = str(inner.get("type") or "").strip()
        text = str(c.get("assertion") or c.get("expect") or "").strip()
        family, score, degenerate = _resolve_hint(hint)
        if not track:
            info = CHECK_TAXONOMY.get(family)
            if info:
                track = str(info.get("track") or "").strip().lower()
            if not track:
                track = "ui"  # 兜底（UI 恒在）
        out.append(
            {
                "track": track,
                "hint": hint,
                "text": text,
                "family": family,
                "score": score,
                "degenerate": degenerate,
            }
        )
    return out


def _compute_adi_impl(case: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    """compute_adi 内部实现（异常由 compute_adi 兜底 fail-closed）。"""
    detail: dict[str, Any] = {
        "ui": 0,
        "api": 0,
        "db": 0,
        "track_count": 0,
        "assert_count": 0,
        "unresolved": [],
        "error": "",
    }
    seen: set[tuple[str, str, str]] = set()
    track_scores: dict[str, int] = {"ui": 0, "api": 0, "db": 0}
    unresolved_counter: dict[str, int] = {}
    assert_count = 0

    for item in _collect_assertions(case):
        track = item["track"] if item["track"] in track_scores else "ui"
        family = str(item["family"] or "").strip()
        text = str(item["text"] or "").strip()
        hint = str(item["hint"] or "").strip()
        score = int(item.get("score", 0) or 0)
        degenerate = bool(item.get("degenerate", False))
        # ★ F2（2026-08-05）：去重键 text 为空时用 hint 兜底（空 text 的两条同轨同族
        #   断言若 hint 不同则不应误并；hint 也空时回退空串，保持原行为）。
        key = (track, family, (text or hint)[:64])
        if key in seen:
            continue
        seen.add(key)
        assert_count += 1
        if score == 0 and not degenerate:
            # 归一失败 / 新增族 / 未知 evidence → 0 分 + 记 unresolved（幻觉线索），仍计入分母
            raw = family or item["hint"] or text[:64]
            if family:
                _warn_missing_family(family)
            if raw:
                unresolved_counter[raw] = unresolved_counter.get(raw, 0) + 1
        if score > track_scores[track]:
            track_scores[track] = score

    adi = max(track_scores.values()) if assert_count else 0
    detail["ui"] = track_scores["ui"]
    detail["api"] = track_scores["api"]
    detail["db"] = track_scores["db"]
    detail["track_count"] = sum(1 for v in track_scores.values() if v > 0)
    detail["assert_count"] = assert_count
    detail["unresolved"] = sorted(
        unresolved_counter, key=lambda k: -unresolved_counter[k]
    )[:20]
    return adi, detail


def compute_adi(case: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    """统计 case 跨三轨的可编译断言深度。

    返回：(adi, adi_detail)；任何内部异常 → (0, {"error": "..."})，绝不返回高分（fail-closed）。
    """
    if not isinstance(case, dict):
        return 0, {"error": "case 非 dict"}
    try:
        return _compute_adi_impl(case)
    except Exception as e:  # noqa: BLE001 —— fail-closed 方向，任何异常都不给高分
        _log.error("[ADI] compute_adi 异常（fail-closed 返回 0）: %s", e, exc_info=True)
        return 0, {"error": f"{type(e).__name__}: {e}"}
