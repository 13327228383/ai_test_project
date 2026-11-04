# -*- coding: utf-8 -*-
"""
执行时 verification_plan 自动构建器（v3.0）
============================================
Step 4 执行时，优先消费 Step3 产出的 checks[] + actions[] 构建 verification_plan。
checks[] 缺失或某轨空白时，RAG + Schema 兜底补充。
零 AI 调用，纯规则 + RAG + Schema 驱动。

通用性保障：
- 不依赖任何特定应用的业务逻辑
- 证据类型来自引擎能力注册表，不硬编码
- RAG 检索参数从 pipeline_config 读取
"""

import json
from src.core.guarded import guard
from src.core.constants import is_illegal_assertion_text
from src.pipeline.steps.step4.verification._api import API_CALL_COUNT_CHECK_TYPES
import logging
import re
from typing import Any

_log = logging.getLogger(__name__)

# ── 引擎能力注册表：轨道 → 默认证据类型 ──
_TRACK_DEFAULTS: dict[str, dict[str, Any]] = {
    "ui": {"evidence": "dom_text", "source": "requirement"},
    "api": {"evidence": "api_status", "source": "inferred"},
    "db": {"evidence": "record_exists", "source": "schema"},
}


def _get_rag_top_k() -> int:
    try:
        from src.core.pipeline_config import pipeline_config

        return min(int(getattr(pipeline_config, "rag_max_top_k", 5)), 10)
    except Exception as _e:
        _log.debug(
            "[verification_plan_builder] _get_rag_top_k 异常: %s", _e, exc_info=True
        )
        return 5


def _resolve_strictness(risk_level: str) -> str:
    return "high" if str(risk_level).upper() == "P0" else "medium"


def _parse_checks(case: dict[str, Any]) -> list[dict[str, Any]]:
    """从 case dict 提取 checks[]（兼容 JSON 字符串、payload 间接层）。"""
    checks = case.get("checks") or []
    if isinstance(checks, str):
        try:
            checks = json.loads(checks)
        except (json.JSONDecodeError, ValueError):
            checks = []
    if not checks:
        payload = case.get("payload")
        if isinstance(payload, dict):
            checks = payload.get("checks") or []
            if isinstance(checks, str):
                try:
                    checks = json.loads(checks)
                except (json.JSONDecodeError, ValueError):
                    checks = []
    return checks if isinstance(checks, list) else []


def _track_from_checks(
    checks: list[dict[str, Any]], track: str
) -> list[dict[str, Any]]:
    """筛选某轨的 check 条目。"""
    return [
        c
        for c in checks
        if isinstance(c, dict) and str(c.get("track", "")).lower() == track
    ]


def _check_to_assertion(check_data: Any) -> str:
    """check 值 → assertion 文本。"""
    if isinstance(check_data, dict):
        return json.dumps(check_data, ensure_ascii=False)
    return str(check_data or "").strip()


def _parse_db_check_sql(sql_text: str) -> tuple:
    """从 SQL 文本提取 table 名和 WHERE 条件。"""
    table = ""
    where = ""
    try:
        m = re.search(r'\bFROM\s+[`"\[]?(\w+)[`"\]]?', sql_text, re.IGNORECASE)
        if m:
            table = m.group(1)
        m = re.search(
            r"\bWHERE\s+(.+?)(?:\s*$|\s*;|\s*GROUP\s+BY|\s*ORDER\s+BY|\s*LIMIT)",
            sql_text,
            re.IGNORECASE | re.DOTALL,
        )
        if m:
            where = m.group(1).strip().rstrip(";")
    except Exception as _e:
        _log.debug(
            "[verification_plan_builder] _parse_db_check_sql 异常: %s",
            _e,
            exc_info=True,
        )
    return table, where


def _preserve_registered_check(
    check_data: Any, track: str, what: str, strictness: str
) -> dict[str, Any] | None:
    """★ 无损透传：Step3 check 携带的 ``type`` 若已注册在
    Step4 执行注册表，则**原样保留该指令**，不做任何重编码。

    根因：原实现把 check 降维成 ``{evidence, table, where}`` 并丢弃 ``type`` →
      Step4 只能走 ``verify_db`` 兜底 → 语义合法的 ``db_table_accessed_check``
      （本就无 table，只需探库可达）被当成 "unverified" → ``all_ok=False`` →
      假 FAIL 并归因 PRODUCT_BUG（实测 TC-REQ-005-E01，risk=P2）。
    通用性：只要生成端发射的类型在执行注册表内，plan 就必须是指令的**恒等映射**
      （中间层不得篡改），执行端才能真正"按用例指令执行"——这是五步各司其职的前提。
    """
    if not isinstance(check_data, dict):
        return None
    _t = str(check_data.get("type", "") or "").strip()
    if not _t:
        return None
    try:
        from src.pipeline.steps.step4.verification._track_protocol import (
            registered_check_types,
        )

        _known = registered_check_types().get(track) or frozenset()
    except Exception as _e:  # noqa: BLE001
        _log.debug("[verification_plan] 执行注册表不可读，跳过无损透传: %s", _e)
        return None
    if _t not in _known:
        return None
    return {
        "type": _t,
        "check": check_data,
        "track": track,
        "assertion": what,
        "source": "step3_ai",
        "confidence": None,
        "strictness": strictness,
    }


# ─────────────────────────────────────────────
# W1-1: Step3 已产出计划的保留 / 兜底决策
# ─────────────────────────────────────────────

_PLAN_TRACKS: tuple[str, ...] = ("ui", "api", "db")


def _normalize_track(raw: Any) -> list[dict[str, Any]]:
    """把单轨数据归一化为 assertion dict 列表。

    Step3 落库格式为 ``{"ui": [assertion, ...]}``；历史/外部格式可能是
    ``{"ui": {"checks": [...]}}``。两者都要能识别，无法识别一律返回空列表
    （由调用方判定为"该轨缺失"）。
    """
    if isinstance(raw, list):
        return [a for a in raw if isinstance(a, dict) and a]
    if isinstance(raw, dict):
        inner = raw.get("checks")
        if isinstance(inner, list):
            return [a for a in inner if isinstance(a, dict) and a]
        # 裸 dict 形态：本身就是一条 assertion
        if raw.get("assertion") or raw.get("evidence"):
            return [raw]
    return []


def summarize_plan(plan: Any) -> dict[str, int]:
    """统计计划各轨的有效断言数，无效输入返回全 0。"""
    if not isinstance(plan, dict):
        return {t: 0 for t in _PLAN_TRACKS}
    return {t: len(_normalize_track(plan.get(t))) for t in _PLAN_TRACKS}


def is_meaningful_plan(plan: Any) -> bool:
    """判断计划是否"有实际断言"——任意一轨含 ≥1 条有效 assertion 即成立。"""
    return any(v > 0 for v in summarize_plan(plan).values())


def _extract_case_plan(case: dict[str, Any]) -> Any:
    """从 case 顶层或 payload 中取 Step3 已生成的 verification_plan。

    payload 可能是 dict（read.py 已解析）或 JSON 字符串（未解析路径），
    两者都要覆盖，否则会漏判为"缺失"而误触发兜底覆盖。
    """
    plan = case.get("verification_plan")
    if is_meaningful_plan(plan):
        return plan

    payload = case.get("payload")
    if isinstance(payload, str) and payload.strip():
        # fail-closed: 解析失败必须记录详情，不静默当作"缺失"
        try:
            payload = json.loads(payload)
        except (ValueError, TypeError) as exc:
            _log.error(
                "[verification_plan] case=%s payload JSON 解析失败，无法读取 Step3 计划: %s",
                case.get("case_id", "?"),
                exc,
            )
            return None
    if isinstance(payload, dict):
        return payload.get("verification_plan")
    return plan


def resolve_verification_plan(case: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """决定该用例最终使用的 verification_plan。

    W1-1 根因修复：原实现在 Step4 无条件用 ``auto_build_verification_plan``
    覆盖 ``c["verification_plan"]``，导致 Step3 已生成的三轨深度计划
    （db/api 轨）被整体丢弃、永不执行。

    策略：
      1. Step3 已携带有效计划（任意轨含实际断言）→ **保留**，不覆盖。
      2. 缺失 / 结构无法解析 / 全轨为空 → 用 auto_build 兜底。
      3. 任何异常都记录详情（fail-closed），不静默吞。

    返回:
        (plan, origin)，origin ∈ {"step3", "auto_build", "empty"}
    """
    case_id = case.get("case_id", "?")

    existing = _extract_case_plan(case)
    if is_meaningful_plan(existing):
        return existing, "step3"

    try:
        built = auto_build_verification_plan(case)
    except Exception as exc:
        _log.error(
            "[verification_plan] case=%s auto_build 兜底失败(%s): %s",
            case_id,
            type(exc).__name__,
            exc,
            exc_info=True,
        )
        return {t: [] for t in _PLAN_TRACKS}, "empty"

    if not is_meaningful_plan(built):
        _log.warning(
            "[verification_plan] case=%s Step3 无计划且 auto_build 兜底为空，"
            "该用例将缺少可证伪断言",
            case_id,
        )
        return (
            built if isinstance(built, dict) else {t: [] for t in _PLAN_TRACKS}
        ), "empty"
    return built, "auto_build"


def apply_verification_plans(candidates: list[dict[str, Any]]) -> dict[str, int]:
    """为候选用例批量落定 verification_plan，返回各来源计数（供日志/断言）。"""
    stats = {"step3": 0, "auto_build": 0, "empty": 0}
    for case in candidates:
        if not isinstance(case, dict):
            continue
        plan, origin = resolve_verification_plan(case)
        stats[origin] = stats.get(origin, 0) + 1
        case["verification_plan"] = plan
        case["_verification_plan_origin"] = origin
        payload = case.get("payload")
        if isinstance(payload, dict):
            payload["verification_plan"] = plan
        elif isinstance(payload, str) and payload.strip():
            # ★ D6 修复(2026-08-05): 读写契约对称 —— 读侧 _extract_case_plan 明确支持
            #   解析字符串 payload，写侧不得整段覆盖。解析后更新 verification_plan 再写回，
            #   避免丢失 actions/title/checks 等字段（否则与原 plan 来源的断言一并丢弃）。
            #   当前不可达（case_store._helpers._row_to_intent 已保证 payload 为 dict），
            #   属埋雷防御 —— 与读侧契约保持一致，杜绝数据静默丢失。
            try:
                _p = json.loads(payload)
            except (ValueError, TypeError) as _je:
                _log.error(
                    "[verification_plan] case=%s payload 字符串解析失败，仅写回 verification_plan: %s",
                    case.get("case_id", "?"),
                    _je,
                )
                _p = None
            if isinstance(_p, dict):
                _p["verification_plan"] = plan
                case["payload"] = _p
            else:
                case["payload"] = {"verification_plan": plan}
        else:
            case["payload"] = {"verification_plan": plan}
        # ★ W3 T04 (2026-08-05): 契约断言补全 —— CONTRACT_ENABLED 时从 YApi schema
        #   为 api 轨生成 api_contract/api_response_body 断言（零 DDL，仅补 body 级）。
        #   默认 OFF → 零行为变更；异常不阻断主流程（记 warning）。
        # ★ 2026-08-12 增强：支持 OpenAPI 3.0 / Swagger 2.0 schema 来源
        #   （CONTRACT_SCHEMA_SOURCE=openapi 时从 CONTRACT_OPENAPI_SPEC 路径加载）。
        with guard("apply_verification_plans: from src.core.pipeline_config import", log=_log, level=logging.WARNING):
            from src.core.pipeline_config import _read_config_value as _cfg

            if bool(_cfg("CONTRACT_ENABLED", False)):
                from src.pipeline.steps.step4.verification.contract_builder import (
                    build_contract_checks,
                    load_openapi_schemas,
                    load_yapi_schemas,
                )

                _schema_source = str(
                    _cfg("CONTRACT_SCHEMA_SOURCE", "yapi") or "yapi"
                ).strip().lower()
                _schemas: dict = {}
                if _schema_source == "openapi":
                    _spec_path = str(
                        _cfg("CONTRACT_OPENAPI_SPEC", "") or ""
                    ).strip()
                    if _spec_path:
                        _schemas = load_openapi_schemas(_spec_path)
                else:
                    _schemas = load_yapi_schemas()
                if _schemas:
                    _contract_checks = build_contract_checks(case, _schemas)
                    if _contract_checks and isinstance(plan, dict):
                        _api_plan = plan.get("api")
                        if not isinstance(_api_plan, list):
                            _api_plan = []
                        _api_plan.extend(_contract_checks)
                        plan["api"] = _api_plan
    return stats


# ─────────────────────────────────────────────
# 主入口
# ─────────────────────────────────────────────


def auto_build_verification_plan(case: dict[str, Any]) -> dict[str, Any]:
    """v3.0: 优先从 checks[] 构建 verification_plan，缺失轨用 RAG 兜底。

    流程：
    1. 从 case 提取 checks[] → 按 track 分组 → 转为 verification_plan 格式
    2. 对 checks[] 未覆盖的轨道，RAG + Schema 兜底（仅 required_tracks 声明的轨）
    3. 完全无 checks[] → 全量 RAG 构建（原逻辑）
    """
    what = case.get("title", "")
    tracks = case.get("required_tracks", ["ui"])
    risk = case.get("risk_level", "P1")
    strictness = _resolve_strictness(risk)

    plan: dict[str, list[dict[str, Any]]] = {"ui": [], "api": [], "db": []}
    checks = _parse_checks(case)

    # ── 1) checks[] → verification_plan ──
    covered_tracks: set = set()
    if checks:
        for chk in checks:
            if not isinstance(chk, dict):
                continue
            track = str(chk.get("track", "")).lower()
            if track not in ("ui", "api", "db"):
                continue
            covered_tracks.add(track)

            precision = str(chk.get("precision", "exact")).lower()
            check_data = chk.get("check", "")
            expect = str(chk.get("expect", "")).strip()

            if track == "ui":
                plan["ui"].append(
                    {
                        "evidence": "dom_text" if precision == "exact" else "visual",
                        "assertion": _check_to_assertion(check_data) or what,
                        "source": "step3_ai",
                        "confidence": None,
                        "strictness": strictness,
                    }
                )

            elif track == "api":
                target = ""
                method = ""
                check_type = ""
                if isinstance(check_data, dict):
                    check_type = str(check_data.get("type", "") or "").strip().lower()
                    method = str(check_data.get("method", "") or "").upper()
                    path = str(
                        check_data.get("path", "")
                        or check_data.get("url_pattern", "")
                        or ""
                    )
                    target = f"{method} {path}" if method and path else path
                elif isinstance(check_data, str) and check_data.strip():
                    target = check_data.strip()

                # 调用次数探测 check（非 API 响应断言）→ 转 call_count 证据
                #   api_call_count_check 语义="页面加载期间触发了几次 XHR"，
                #   属于 UI 轨的"后端联动证据"而非 API 轨的"接口响应断言"。
                #   Step3 AI 偶发把它用于"接口清单验证"需求 → expect="1" →
                #   被盲目赋为 assertion → body_contains("1") 假 PASS 链。
                is_count_probe = check_type in API_CALL_COUNT_CHECK_TYPES

                # 非法断言文本过滤（纯数字/空串/单标点 → 不直接赋 assertion）
                _assertion_text = expect if not is_illegal_assertion_text(expect) else what

                plan["api"].append(
                    {
                        "evidence": "call_count" if is_count_probe else "api_status",
                        "target": target or "unknown",
                        "method": method or "GET",
                        "assertion": _assertion_text,
                        "source": "step3_ai",
                        "confidence": None,
                        "strictness": strictness,
                    }
                )

            elif track == "db":
                # ★ 契约修复(2026-09-27): 已注册类型的 DB check 必须**无损透传**——
                #   db check 的语义完全承载于 ``type``（如 db_table_accessed_check 本就无
                #   table/where），重编码成 evidence/table/where 即丢失指令 → 假 FAIL。
                #   ui/api 轨的语义承载于 assertion 文本且消费端已按文本工作，
                #   故仅 db 轨需要（也是唯一）保留原始类型。
                _typed = _preserve_registered_check(check_data, "db", what, strictness)
                if _typed is not None:
                    plan["db"].append(_typed)
                    continue
                table = ""
                where = ""
                if isinstance(check_data, dict):
                    table = str(check_data.get("table", ""))
                    where = str(check_data.get("where", ""))
                elif isinstance(check_data, str) and check_data.strip():
                    table, where = _parse_db_check_sql(check_data)

                plan["db"].append(
                    {
                        "evidence": "record_exists" if table else "unverified",
                        "table": table,
                        "where": where,
                        "expect": expect or ">=1",
                        "assertion": what,
                        "source": "step3_ai",
                        "confidence": None,
                        "strictness": strictness,
                    }
                )

    # ── 2) RAG 兜底：补充 checks[] 未覆盖的轨道 ──
    _rag_supplement(plan, case, covered_tracks, what, tracks, strictness)

    return plan


# ─────────────────────────────────────────────
# RAG 兜底（原逻辑保留，仅用于 checks[] 缺失时）
# ─────────────────────────────────────────────


def _rag_supplement(
    plan: dict[str, list[dict[str, Any]]],
    case: dict[str, Any],
    covered_tracks: set,
    what: str,
    tracks: list,
    strictness: str,
) -> None:
    """对 checks[] 未覆盖的轨道用 RAG + Schema 兜底。"""

    # UI 轨恒在：checks[] 未覆盖时从 RAG 兜底
    if "ui" not in covered_tracks and "ui" in tracks:
        ui_defaults = _TRACK_DEFAULTS["ui"]
        if not plan["ui"]:
            plan["ui"].append(
                {
                    "evidence": ui_defaults["evidence"],
                    "assertion": what,
                    "source": ui_defaults["source"],
                    "confidence": None,
                    "strictness": strictness,
                }
            )

    # API 轨
    if "api" not in covered_tracks and "api" in tracks:
        api_checks = _build_api_checks(case)
        if api_checks:
            plan["api"].extend(api_checks)
        else:
            api_defaults = _TRACK_DEFAULTS["api"]
            plan["api"].append(
                {
                    "evidence": api_defaults["evidence"],
                    "target": "unknown",
                    "assertion": what,
                    "source": api_defaults["source"],
                    "confidence": None,
                    "strictness": "optional",
                }
            )

    # DB 轨
    if "db" not in covered_tracks and "db" in tracks:
        db_checks = _build_db_checks(case)
        if db_checks:
            plan["db"].extend(db_checks)
        else:
            risk_upper = str(case.get("risk_level", "")).upper()
            if risk_upper == "P0":
                plan["db"].append(
                    {
                        "evidence": "unverified",
                        "table": "",
                        "where": "",
                        "expect": "manual",
                        "assertion": what,
                        "source": "schema",
                        "confidence": None,
                        "strictness": "required",
                    }
                )
            else:
                plan["db"].append(
                    {
                        "evidence": "skipped",
                        "table": "",
                        "where": "",
                        "expect": "skipped",
                        "assertion": what,
                        "source": "schema",
                        "confidence": None,
                        "strictness": "optional",
                    }
                )


def _build_api_checks(case: dict[str, Any]) -> list[dict[str, Any]]:
    """从 RAG 检索相关 API 端点，构建 api 轨断言。零硬编码业务逻辑。"""
    what = case.get("title", "")
    req_id = case.get("req_id", "")
    case_id = case.get("case_id", "")
    check_id = case.get("check_id", "")
    top_k = _get_rag_top_k()

    endpoints: list[dict] = []
    with guard("_build_api_checks: from src.extensions.rag_engine impor", log=_log):
        from src.extensions.rag_engine import rag_engine

        # ★ 2026-07-12: 只检索 api_endpoints 类型，避免全量 dump 8 种类型
        retrieved = rag_engine.retrieve(
            {"req_id": req_id, "description": what},
            top_k=top_k,
            retrieve_types=["api_endpoints"],
        )
        if retrieved:
            endpoints = retrieved.get("api_endpoints", []) or []
            # ★ S3-012: 记录 RAG 使用日志
            with guard("_build_api_checks: from src.core.versioning import curr", log=_log):
                from src.core.versioning import current_version_id
                from src.core.config_loader import config
                from src.extensions.rag_engine.rag_usage_logger import log_rag_retrieval

                project = getattr(config, "project_name", "") or ""
                vid = current_version_id(project)
                log_rag_retrieval(
                    retrieved,
                    project=project,
                    version_id=vid,
                    case_id=case_id,
                    check_id=check_id,
                    req_id=req_id,
                )
            # ★ 2026-07-12: 10 层过滤管道 L6-L10（MMR + LLM-as-Judge）
            if endpoints:
                with guard("_build_api_checks: from src.extensions.rag_engine.rag_f", log=_log):
                    from src.extensions.rag_engine.rag_filter import filter_rag_chunks

                    endpoints = filter_rag_chunks(
                        endpoints,
                        requirement=case,
                        operation=case.get("action", ""),
                        description=what,
                    )

    checks = []
    for ep in endpoints[:top_k]:
        method = ep.get("method", "") if isinstance(ep, dict) else ""
        path = ep.get("path", "") if isinstance(ep, dict) else str(ep)
        if path:
            target = f"{method} {path}".strip() if method else path
            checks.append(
                {
                    "evidence": "api_status",
                    "target": target,
                    "assertion": what,
                    "source": "rag",
                    "confidence": None,
                    "strictness": _resolve_strictness(case.get("risk_level", "P1")),
                }
            )

    return checks


def _build_db_checks(case: dict[str, Any]) -> list[dict[str, Any]]:
    """从 RAG/Schema 检索相关 DB 表，构建 db 轨断言。

    1. RAG 无 db_tables 时，降级从 diff_snapshot_store 加载 DB schema 解析表名
    2. 生成 where 条件（基于 case 的 entity/action/test_data）
    3. 生成 expect 期望值（create→">=1", delete→"==0", update→">=1"）
    4. 生成 field_check 字段级校验
    5. 无匹配表时返回空列表（不创建假检查）
    """
    what = case.get("title", "")
    req_id = case.get("req_id", "")
    case_id = case.get("case_id", "")
    check_id = case.get("check_id", "")
    top_k = _get_rag_top_k()

    tables: dict[str, Any] = {}
    with guard("_build_db_checks: from src.extensions.rag_engine impor", log=_log):
        from src.extensions.rag_engine import rag_engine

        # ★ 2026-07-12: 只检索 db_tables 类型，避免全量 dump 8 种类型
        retrieved = rag_engine.retrieve(
            {"req_id": req_id, "description": what},
            top_k=top_k,
            retrieve_types=["db_tables"],
        )
        if retrieved:
            tables = retrieved.get("db_tables", {}) or {}
            # ★ S3-012: 记录 RAG 使用日志
            with guard("_build_db_checks: from src.core.versioning import curr", log=_log):
                from src.core.versioning import current_version_id
                from src.core.config_loader import config
                from src.extensions.rag_engine.rag_usage_logger import log_rag_retrieval

                project = getattr(config, "project_name", "") or ""
                vid = current_version_id(project)
                log_rag_retrieval(
                    retrieved,
                    project=project,
                    version_id=vid,
                    case_id=case_id,
                    check_id=check_id,
                    req_id=req_id,
                )
            # ★ 2026-07-12: 10 层过滤管道 L6-L10（MMR + LLM-as-Judge）
            if isinstance(tables, list) and tables:
                with guard("_build_db_checks: from src.extensions.rag_engine.rag_f", log=_log):
                    from src.extensions.rag_engine.rag_filter import filter_rag_chunks

                    tables = filter_rag_chunks(
                        tables,
                        requirement=case,
                        operation=case.get("action", ""),
                        description=what,
                    )

    if not tables:
        tables = _load_db_schema_tables(what, top_k)

    if isinstance(tables, dict):
        table_names = list(tables.keys())[:top_k]
    elif isinstance(tables, list):
        table_names = [t if isinstance(t, str) else str(t) for t in tables[:top_k]]
    else:
        table_names = []

    action = str(case.get("action", "")).lower()
    entity = case.get("entity", "")
    test_data = case.get("test_data", {})
    strictness = _resolve_strictness(case.get("risk_level", "P1"))

    if action in ("delete", "remove", "destroy"):
        expect = "==0"
    elif action in ("create", "add", "insert", "update", "modify", "edit"):
        expect = ">=1"
    else:
        expect = ">=1"

    where_clause = _build_where_clause(test_data, entity)
    field_check = _build_field_check(test_data)

    checks = []
    for tn in table_names:
        if tn:
            check = {
                "evidence": "record_exists",
                "target": tn,
                "table": tn,
                "where": where_clause,
                "expect": expect,
                "assertion": what,
                "source": "schema",
                "confidence": None,
                "strictness": strictness,
            }
            if field_check:
                check["field_check"] = field_check
            checks.append(check)

    return checks


def _load_db_schema_tables(what: str, top_k: int) -> dict[str, Any]:
    """从 diff_snapshot_store 加载 DB schema，解析表名。"""
    try:
        from src.pipeline.utils.diff_snapshot_store import diff_snapshot_store

        db_snap = diff_snapshot_store.load_snapshot("db_schema")
        if not db_snap or not isinstance(db_snap, dict):
            return {}

        schema_text = db_snap.get("schema_text", "")
        if not schema_text:
            return {}

        table_names = re.findall(
            r'CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[`"\[]?(\w+)[`"\]]?',
            schema_text,
            re.IGNORECASE,
        )

        return {tn: True for tn in table_names[:top_k] if tn}
    except Exception as e:
        _log.debug("diff_snapshot DB schema 降级跳过: %s", str(e)[:60])
        return {}


def _build_where_clause(test_data: Any, entity: str = "") -> str:
    """从 test_data 生成 WHERE 条件。"""
    if not test_data:
        return ""

    if isinstance(test_data, str):
        return test_data.strip()

    if isinstance(test_data, dict):
        clauses = []
        for field, value in test_data.items():
            if isinstance(value, str):
                clauses.append("%s = '%s'" % (field, value.replace("'", "''")))
            elif isinstance(value, (int, float)):
                clauses.append("%s = %s" % (field, value))
            elif isinstance(value, bool):
                clauses.append("%s = %s" % (field, 1 if value else 0))
            elif value is None:
                clauses.append("%s IS NULL" % field)
        return " AND ".join(clauses) if clauses else ""

    return ""


def _build_field_check(test_data: Any) -> dict[str, Any]:
    """从 test_data 提取字段级期望值。"""
    if not isinstance(test_data, dict):
        return {}

    field_check = {}
    for field, value in test_data.items():
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if isinstance(value, float):
                field_check[field] = {"expect": value, "precision": 0.01}
            else:
                field_check[field] = {"expect": value}
        elif isinstance(value, str):
            field_check[field] = {"expect": value}

    return field_check if field_check else {}
