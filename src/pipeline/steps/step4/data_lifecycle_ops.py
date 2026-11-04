# -*- coding: utf-8 -*-
"""case 级 data_setup / data_teardown 数据生命周期 op 执行器（C 数据治理，L0 短板闭环）。

★ [已复核-DeepSeek-V3 2026-08-16] 2026 差距闭环 C（寇豆码）：
  在既有 W2 TestDataLifecycle（setup_steps 通道制，TDL_ENABLED）之外，新增 case schema
  可选字段 data_setup / data_teardown（op 列表制，独立开关 TDL_OPS_ENABLED）：
    - data_setup:    [{"op": "sql", "sql": "INSERT INTO t (k,v) VALUES ('${ns}','x')"},
                      {"op": "api", "method": "POST", "url": "/api/x", "body": {...}}]
    - data_teardown: [{"op": "sql", "sql": "DELETE FROM t WHERE k='${ns}'"}, ...]
  仅支持 sql / api 两类 op；fail-open（单条失败记 warning + evidence，不阻断主流程）。

隔离铁律：
  - 命名空间 ns = sanitize(project)_sanitize(case_id)；SQL/URL/BODY 内 ${ns} 占位符替换。
  - teardown 的 sql op 必须含 ${ns}（否则跳过 + warning），保证只清本 case 数据、不碰其他。
  - DB 写仅当 TDL_OPS_SQL_ENABLED=True 才执行（默认 False = 跳过，零行为变更）；
    API 调用经 _make_http_request，失败只记 evidence（fail-open）。
"""
from __future__ import annotations

import logging
import re
from typing import Any

from src.core.pipeline_config import _read_config_value as _cfg

_log = logging.getLogger(__name__)


def build_namespace(project: str, case_id: str) -> str:
    """命名空间 {project}_{case_id}（标识符合法化，防 SQL 注入/路径注入）。"""
    def _clean(v: Any) -> str:
        s = re.sub(r"[^A-Za-z0-9_]", "_", str(v or ""))
        s = re.sub(r"_+", "_", s).strip("_")
        return (s or "x")[:64]

    return f"{_clean(project)}_{_clean(case_id)}"


def substitute_ns(value: Any, ns: str) -> Any:
    """递归替换 ${ns} 占位符（str/dict/list）。"""
    if isinstance(value, str):
        return value.replace("${ns}", ns)
    if isinstance(value, dict):
        return {k: substitute_ns(v, ns) for k, v in value.items()}
    if isinstance(value, list):
        return [substitute_ns(v, ns) for v in value]
    return value


def _run_sql_op(op: dict[str, Any], ns: str, phase: str) -> dict[str, Any]:
    """执行单条 sql op（fail-open）。teardown 无 ${ns} 一律跳过（隔离铁律）。"""
    sql_raw = str(op.get("sql") or "").strip()
    if not sql_raw:
        return {"op": "sql", "ok": False, "skipped": False, "error": "sql 为空"}
    if phase == "teardown" and "${ns}" not in sql_raw:
        return {
            "op": "sql",
            "ok": False,
            "skipped": True,
            "error": "teardown sql 必须含 ${ns}（隔离铁律），跳过",
        }
    if not _cfg("TDL_OPS_SQL_ENABLED", False):
        return {
            "op": "sql",
            "ok": False,
            "skipped": True,
            "error": "TDL_OPS_SQL_ENABLED 未开启，跳过 sql op",
        }
    sql = substitute_ns(sql_raw, ns)
    try:
        from src.extensions.db.db_agent import db_agent

        res = db_agent.execute(sql)
        return {
            "op": "sql",
            "ok": bool(res.get("success")),
            "skipped": False,
            "rows_affected": res.get("rows_affected", 0),
            "error": str(res.get("error") or "")[:200],
        }
    except Exception as e:  # noqa: BLE001
        return {
            "op": "sql",
            "ok": False,
            "skipped": False,
            "error": f"{type(e).__name__}: {e}"[:200],
        }


def _run_api_op(op: dict[str, Any], ns: str, phase: str) -> dict[str, Any]:
    """执行单条 api op（fail-open）。GET/DELETE 不携带 body。"""
    url_raw = str(op.get("url") or "").strip()
    method = str(op.get("method") or "POST").strip().upper() or "POST"
    if not url_raw:
        return {"op": "api", "ok": False, "skipped": False, "error": "url 为空"}
    url = substitute_ns(url_raw, ns)
    body = substitute_ns(op.get("body") or {}, ns)
    headers = op.get("headers") or {}
    try:
        from src.pipeline.api_login_helpers import _make_http_request

        resp = _make_http_request(
            url,
            method=method,
            headers=headers,
            body=body if method in ("POST", "PUT", "PATCH") else None,
        )
        ok = bool(resp.get("ok")) and (resp.get("status") or 0) < 400
        return {
            "op": "api",
            "ok": ok,
            "skipped": False,
            "status": resp.get("status"),
            "error": str(resp.get("error") or "")[:200],
        }
    except Exception as e:  # noqa: BLE001
        return {
            "op": "api",
            "ok": False,
            "skipped": False,
            "error": f"{type(e).__name__}: {e}"[:200],
        }


def run_data_lifecycle(
    cases: list[dict[str, Any]], project: str, phase: str
) -> dict[str, dict[str, Any]]:
    """对每个 case 执行 data_setup/data_teardown op 列表。

    返回 {case_id: {"phase","ns","ok","ops":[...]}}；fail-open：永不 raise，
    单条 op 失败记 evidence.error + warning（不阻断主流程）。
    """
    if not _cfg("TDL_OPS_ENABLED", False):
        return {}
    if phase not in ("setup", "teardown"):
        return {}
    key = "data_setup" if phase == "setup" else "data_teardown"
    out: dict[str, dict[str, Any]] = {}
    for case in cases or []:
        if not isinstance(case, dict):
            continue
        case_id = str(case.get("case_id") or "").strip()
        ops = case.get(key) or []
        if not isinstance(ops, list) or not ops:
            continue
        ns = build_namespace(project, case_id)
        op_results: list[dict[str, Any]] = []
        for op in ops:
            if not isinstance(op, dict):
                op_results.append(
                    {"op": "?", "ok": False, "skipped": True, "error": "op 非 dict"}
                )
                continue
            kind = str(op.get("op") or "").strip().lower()
            if kind == "sql":
                r = _run_sql_op(op, ns, phase)
            elif kind == "api":
                r = _run_api_op(op, ns, phase)
            else:
                r = {
                    "op": kind,
                    "ok": False,
                    "skipped": True,
                    "error": f"未知 op 类型: {kind}",
                }
            r["op"] = kind
            op_results.append(r)
        all_ok = all(r.get("ok") for r in op_results)
        ev: dict[str, Any] = {"phase": phase, "ns": ns, "ok": all_ok, "ops": op_results}
        out[case_id] = ev
        if not all_ok:
            _log.warning(
                "[TDL-OPS] case=%s %s 部分失败: %s",
                case_id,
                phase,
                "; ".join(
                    f"{r.get('op')}:{r.get('error', '')}"
                    for r in op_results
                    if not r.get("ok")
                ),
            )
    return out
