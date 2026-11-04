# -*- coding: utf-8 -*-
"""流水线追溯链路 API（从 pipeline_progress.py 拆分，§4.3 单文件≤800行）。

职责：
  - GET /api/pipeline-trace/<step>/<id> 返回指定步骤条目的上下游关联

通用性：纯只读 DB 查询，按 (project, version_id/version_label) 隔离，
适用于所有项目/行业的追溯链路展示。
"""
import json
import logging

from flask import jsonify, request

from src.backend.routes.execution import exec_bp
from src.models.permission import check_admin_or_permission
from src.backend.state import _current_project

_log = logging.getLogger(__name__)


def _infer_rail(item, fallback="api"):
    """从校验条目内容推断所属轨道(ui/api/db)。

    通用性：不依赖业务，仅按字段/请求特征识别：
      - 有 HTTP 语义字段(target/method/url/status_code/observed_status) → api
      - 有 SQL 语义字段(sql/query/rows/db_assert) → db
      - 有浏览器语义字段(selector/coords/dom/html/url+click) → ui
    """
    itxt = json.dumps(item, ensure_ascii=False) if not isinstance(item, str) else item
    itxt = (itxt or "").lower()
    _m = item if isinstance(item, dict) else {}
    # ★ 显式声明的轨道优先（部分执行器在条目的 track/rail 字段里写死 ui/api/db）
    _explicit = (_m.get("track") or _m.get("rail") or "").strip().lower()
    if _explicit in ("ui", "api", "db"):
        return _explicit
    if any(k in _m for k in ("target", "method", "status_code", "observed_status")):
        if "observed_status" in _m or "target" in _m or "status_code" in _m:
            if any(k in itxt for k in ("sql", "db_assert", "query", "rows")):
                return "db"
            return "api"
    if any(k in _m for k in ("sql", "db_assert", "query")):
        return "db"
    if any(k in _m for k in ("selector", "coords", "dom_path", "element")):
        return "ui"
    return fallback


def _parse_execution_details(conn, project, run_id, case_id, fallback_rails=None):
    """从 executions.details(JSON 数组)解析逐项校验明细。

    返回 [ {rail, expected, actual, passed, source} ... ]；无数据返回 []。
    details 兼容：
      - JSON 数组（每项含断言/实际/是否通过）
      - 其他文本 → 作为单条通用明细并入
    fallback_rails：用例声明的真实轨道列表(required_tracks)。当某校验条目既无
      显式声明的 track/rail，又不含 API/DB/UI 特征关键字时，据此推断所属轨道，
      避免"用例标记 UI 轨道却冒出 API 行"的误导（默认曾固定回退 api）。
    通用性：纯只读解析，适用于任意项目。
    """
    # 回退轨道：若用例只声明单一轨道则用之；多轨优先取 UI（UI 执行常伴随操作证据）；
    # 仍不确定时保持 'ui' 中性，避免误判为 API。
    _fb = "ui"
    if fallback_rails:
        _rails = [t for t in fallback_rails if t in ("ui", "api", "db")]
        if len(_rails) == 1:
            _fb = _rails[0]
        elif "ui" in _rails:
            _fb = "ui"
        elif _rails:
            _fb = _rails[0]
    items = []
    try:
        _rid = str(run_id or "").strip()
        if not _rid:
            return items
        if case_id:
            rows = conn.execute(
                "SELECT expected, actual, details FROM executions "
                "WHERE project=%s AND run_id=%s AND case_id=%s "
                "ORDER BY id DESC LIMIT 1",
                (project, _rid, str(case_id)),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT expected, actual, details FROM executions "
                "WHERE project=%s AND run_id=%s ORDER BY id DESC LIMIT 1",
                (project, _rid),
            ).fetchall()
        if not rows:
            return items
        row = rows[0]
        _raw_details = row.get("details") if row else None
        _expected = (row or {}).get("expected") or ""
        _actual = (row or {}).get("actual") or ""
        parsed = None
        if _raw_details:
            try:
                parsed = json.loads(_raw_details)
            except Exception:
                parsed = None
        if isinstance(parsed, list):
            for it in parsed:
                if not isinstance(it, dict):
                    continue
                exp = (
                    it.get("assertion")
                    or it.get("expected")
                    or it.get("expect")
                    or it.get("expected_value")
                    or ""
                )
                # ★ 项级未存预期时回退整条执行(executions.expected)的预期模板，
                #   保证「预期(Expected)」列不为空。
                if not exp:
                    exp = _expected
                act = (
                    it.get("observed_evidence")
                    or it.get("evidence")
                    or it.get("actual")
                    or it.get("observed_status")
                    or it.get("observed_summary")
                    or ""
                )
                passed = it.get("observed_passed")
                if passed is None:
                    passed = it.get("passed")
                # ★ 合并同轨多条证据：同一轨出现多次时拼成一行，避免表格出现
                #   多个空 expected 的重复行（§预期VS实际 展示更清晰）。
                rail = _infer_rail(it, fallback=_fb)
                merged = next((x for x in items if x["rail"] == rail), None)
                if merged is not None:
                    merged["expected"] = merged["expected"] or exp
                    merged["actual"] = (
                        (merged["actual"] + "; " + act) if act and merged["actual"] else (
                            merged["actual"] or act
                        )
                    )
                    merged["passed"] = merged["passed"] and bool(passed)
                    merged["source"] = merged["source"] or it.get(
                        "evidence_source"
                    ) or it.get("evidence") or ""
                else:
                    items.append(
                        {
                            "rail": rail,
                            "expected": exp,
                            "actual": act,
                            "passed": bool(passed),
                            "source": it.get("evidence_source")
                            or it.get("evidence")
                            or "",
                        }
                    )
        elif _expected or _actual:
            items.append(
                {
                    "rail": _infer_rail(
                        parsed if isinstance(parsed, dict) else {}, fallback=_fb
                    ),
                    "expected": _expected,
                    "actual": _actual,
                    "passed": True,
                    "source": "execution",
                }
            )
    except Exception as e:
        _log.error("[execution-tracks] 解析校验明细异常: %s", e, exc_info=True)
        return []
    return items


@exec_bp.route("/api/execution-tracks", methods=["GET"])
def api_execution_tracks():
    """获取某条执行记录的三轨(ui/api/db)通过率（执行详情展示用）。

    数据源：ai_health_metrics(metric_type='verification', metric_name∈{ui_pass,api_pass,
    db_pass})。优先按 run_id + case_id（details.case_id 精确匹配，根因修复后新数据带
    case_id）聚合；若该 run 内无此 case 的记录（历史数据未存储 case_id），回退为
    run 级聚合兜底，保证有数据即可展示、无数据不漏报。
    返回: {ok, tracks: {ui:{pass,total,rate}, api:{...}, db:{...}}, scoped: 'case'|'run'}
    """
    ok, err = check_admin_or_permission("execution.run", project=_current_project())
    if not ok:
        return jsonify({"ok": False, "error": err}), (
            401 if err.startswith("请先登录") else 403
        )
    try:
        from src.core.db_manager import get_connection, release_connection

        _proj = _current_project()
        _run_id = str(request.args.get("run_id", "") or "").strip()
        _case_id = str(request.args.get("case_id", "") or "").strip()
        if not _run_id:
            return jsonify({"ok": False, "error": "缺少 run_id"}), 400
        conn = None
        try:
            conn = get_connection()
            rows = conn.execute(
                "SELECT metric_name, value, total, details FROM ai_health_metrics "
                "WHERE project=%s AND run_id=%s AND step=4 AND metric_type='verification' "
                "AND metric_name IN ('ui_pass','api_pass','db_pass') "
                "ORDER BY id ASC",
                (_proj, _run_id),
            ).fetchall()

            # 初始化三轨累加器（case 级与 run 级各一套）
            def _acc():
                return {r: {"pass": 0, "total": 0} for r in ("ui", "api", "db")}

            acc_case, acc_run = _acc(), _acc()
            case_hit = False
            for row in rows:
                rail = str(row["metric_name"] or "").replace("_pass", "")
                if rail not in acc_run:
                    continue
                acc_run[rail]["total"] += 1
                acc_run[rail]["pass"] += 1 if float(row["value"] or 0) > 0 else 0
                # case 级匹配：details JSON 内含 case_id 且与目标用例一致
                if _case_id:
                    try:
                        _det = json.loads(row["details"] or "{}") if row["details"] else {}
                    except Exception:
                        _det = {}
                    if _det.get("case_id") == _case_id:
                        acc_case[rail]["total"] += 1
                        acc_case[rail]["pass"] += 1 if float(row["value"] or 0) > 0 else 0
                        case_hit = True

            # ★ 根因修复(2026-09-02): 透传用例声明的真实执行轨道(required_tracks)，
            #   前端据此只渲染有用例数据支撑的轨道，消除"写死三轨→结果全空"的误解。
            req_tracks = None
            if _case_id:
                try:
                    _crow = conn.execute(
                        "SELECT payload_json FROM test_cases "
                        "WHERE project=%s AND case_id=%s LIMIT 1",
                        (_proj, _case_id),
                    ).fetchone()
                    if _crow:
                        _pj = json.loads(_crow["payload_json"] or "{}")
                        _rt = _pj.get("required_tracks") or []
                        if isinstance(_rt, (list, tuple)):
                            req_tracks = [t for t in _rt if t in ("ui", "api", "db")] or None
                except Exception:
                    req_tracks = None

            # ★ 逐项校验明细（预期 VS 实际）：从 executions.details 解析，
            #   每条含断言预期/观测实际/是否通过，并按特征推断所属轨道。
            #   传入用例声明的 required_tracks 作为回退轨道，避免 UI 用例误判为 API。
            details = _parse_execution_details(
                conn, _proj, _run_id, _case_id, fallback_rails=req_tracks
            )
            # ★ 按用例声明轨道过滤：只展示/统计该用例真实执行的轨道，
            #   消除"UI 轨道用例却冒出 API 行"的误导。
            if req_tracks:
                details = [d for d in details if d.get("rail") in req_tracks]

            # ★ 优先以本执行自身的逐项校验明细计算轨道通过率（按用例、精确、
            #   不依赖 ai_health_metrics 的 run 级噪声累计）；明细缺失时才回退
            #   ai_health_metrics 聚合。
            acc_detail = {r: {"pass": 0, "total": 0} for r in ("ui", "api", "db")}
            for _d in details:
                _r = _d.get("rail")
                if _r in acc_detail:
                    acc_detail[_r]["total"] += 1
                    if _d.get("passed"):
                        acc_detail[_r]["pass"] += 1
            detail_total = sum(a["total"] for a in acc_detail.values())

            if detail_total > 0:
                tracks = {}
                for rail, a in acc_detail.items():
                    rate = round(100.0 * a["pass"] / a["total"], 1) if a["total"] > 0 else None
                    tracks[rail] = {"pass": a["pass"], "total": a["total"], "rate": rate}
                scoped = "case" if _case_id else "run"
            else:
                # ── 回退：ai_health_metrics 聚合（旧数据/纯指标用例）──
                scoped = "case" if (case_hit and _case_id) else "run"
                track_src = acc_case if scoped == "case" else acc_run
                tracks = {}
                for rail, a in track_src.items():
                    total = a["total"]
                    rate = round(100.0 * a["pass"] / total, 1) if total > 0 else None
                    tracks[rail] = {"pass": a["pass"], "total": total, "rate": rate}

            return jsonify(
                {"ok": True, "tracks": tracks, "scoped": scoped,
                 "required_tracks": req_tracks, "details": details}
            )
        finally:
            if conn:
                release_connection(conn)
    except Exception as e:
        _log.error("[api/execution-tracks] 异常: %s", e, exc_info=True)
        return jsonify({"ok": False, "error": "服务器内部错误"}), 500


@exec_bp.route("/api/pipeline-trace/<int:step>/<id>", methods=["GET"])
def api_pipeline_trace(step, id):
    """获取指定步骤条目的上下游关联（追溯链路）。

    ★ 按 (project, version_id / version_label) 隔离。
    返回: {upstream: [{step, id, label}], downstream: [{step, id, label}]}
    """
    ok, err = check_admin_or_permission("execution.run", project=_current_project())
    if not ok:
        return jsonify({"ok": False, "error": err}), (
            401 if err.startswith("请先登录") else 403
        )
    try:
        from src.core.db_manager import get_connection, release_connection
        from src.core.versioning import current_version_id, current_version_label

        _proj = _current_project()
        _vid = current_version_id(_proj)
        _vlabel = current_version_label(_proj)
        conn = None
        upstream, downstream = [], []
        try:
            conn = get_connection()

            if step == 1:
                # id = doc_type；下游 = requirements（按 source_anchor 模糊匹配 doc_type）
                _dt = str(id)
                rows = conn.execute(
                    "SELECT req_id, module, description FROM requirements "
                    "WHERE project=%s AND version_id=%s "
                    "AND (source_anchor LIKE %s OR slice_id LIKE %s) "
                    "ORDER BY req_id LIMIT 200",
                    (_proj, _vid, "%" + _dt + "%", "%" + _dt + "%"),
                ).fetchall()
                for r in rows:
                    downstream.append(
                        {
                            "step": 2,
                            "id": str(r["req_id"]),
                            "label": str(r["req_id"])
                            + " · "
                            + (str(r.get("description", ""))[:40] or "—"),
                        }
                    )

            elif step == 2:
                # id = req_id；上游 = source_documents（从 source_anchor / slice_id 反查 doc_type）
                row = conn.execute(
                    "SELECT source_anchor, slice_id FROM requirements "
                    "WHERE req_id=%s AND project=%s AND version_id=%s",
                    (str(id), _proj, _vid),
                ).fetchone()
                if row:
                    _anchor = str(row.get("source_anchor", "") or "")
                    _slice = str(row.get("slice_id", "") or "")
                    # 从 slice_id 提取 doc_type（格式如 req_raw:1:2 → req_raw）
                    _guess_dt = _slice.split(":")[0] if ":" in _slice else ""
                    _sd_rows = conn.execute(
                        "SELECT doc_type, filename FROM source_documents "
                        "WHERE project=%s AND version_label=%s "
                        "AND (doc_type=%s OR %s LIKE CONCAT('%%', doc_type, '%%')) "
                        "ORDER BY created_at DESC LIMIT 20",
                        (_proj, _vlabel, _guess_dt, _anchor),
                    ).fetchall()
                    for s in _sd_rows:
                        upstream.append(
                            {
                                "step": 1,
                                "id": str(s["doc_type"]),
                                "label": str(s.get("filename", ""))
                                or str(s["doc_type"]),
                            }
                        )
                # 下游 = test_cases WHERE req_id
                rows = conn.execute(
                    "SELECT case_id, title FROM test_cases "
                    "WHERE req_id=%s AND project=%s AND version_id=%s "
                    "ORDER BY case_id LIMIT 200",
                    (str(id), _proj, _vid),
                ).fetchall()
                for r in rows:
                    downstream.append(
                        {
                            "step": 3,
                            "id": str(r["case_id"]),
                            "label": str(r["case_id"])
                            + " · "
                            + (str(r.get("title", ""))[:40] or "—"),
                        }
                    )

            elif step == 3:
                # id = case_id；上游 = requirements（通过 req_id）
                _req_row = conn.execute(
                    "SELECT req_id FROM test_cases WHERE case_id=%s AND project=%s AND version_id=%s",
                    (str(id), _proj, _vid),
                ).fetchone()
                if _req_row and _req_row.get("req_id"):
                    _rid = str(_req_row["req_id"])
                    _r = conn.execute(
                        "SELECT description FROM requirements WHERE req_id=%s AND project=%s AND version_id=%s",
                        (_rid, _proj, _vid),
                    ).fetchone()
                    upstream.append(
                        {
                            "step": 2,
                            "id": _rid,
                            "label": _rid
                            + " · "
                            + (str(_r.get("description", ""))[:40] if _r else "—"),
                        }
                    )
                # 下游 = executions WHERE case_id（★ 必须 SELECT case_id，trace id 以 case_id 标识）
                rows = conn.execute(
                    "SELECT id, case_id, run_id, status FROM executions "
                    "WHERE case_id=%s AND project=%s AND version_id=%s "
                    "ORDER BY executed_at DESC LIMIT 200",
                    (str(id), _proj, _vid),
                ).fetchall()
                for r in rows:
                    downstream.append(
                        {
                            "step": 4,
                            "id": str(r["case_id"]),
                            "label": "执行 #"
                            + str(r["id"])
                            + " · "
                            + str(r.get("status", ""))
                            + " · "
                            + str(r.get("run_id", ""))[:12],
                        }
                    )

            elif step == 4:
                # id = case_id（前端用 case_id 标识 execution）；上游 = test_cases
                _tc = conn.execute(
                    "SELECT case_id, title, req_id FROM test_cases "
                    "WHERE case_id=%s AND project=%s AND version_id=%s",
                    (str(id), _proj, _vid),
                ).fetchone()
                if _tc:
                    upstream.append(
                        {
                            "step": 3,
                            "id": str(_tc["case_id"]),
                            "label": str(_tc["case_id"])
                            + " · "
                            + (str(_tc.get("title", ""))[:40] or "—"),
                        }
                    )
                # 下游 = generated_reports（通过 run_id）
                _exec = conn.execute(
                    "SELECT run_id FROM executions WHERE case_id=%s AND project=%s AND version_id=%s "
                    "ORDER BY executed_at DESC LIMIT 1",
                    (str(id), _proj, _vid),
                ).fetchone()
                if _exec and _exec.get("run_id"):
                    _rid = str(_exec["run_id"])
                    rows = conn.execute(
                        "SELECT id, report_type FROM generated_reports "
                        "WHERE run_id=%s AND project=%s AND version_id=%s "
                        "ORDER BY id DESC LIMIT 50",
                        (_rid, _proj, _vid),
                    ).fetchall()
                    for r in rows:
                        downstream.append(
                            {
                                "step": 5,
                                "id": str(r["id"]),
                                "label": "报告 #"
                                + str(r["id"])
                                + " · "
                                + str(r.get("report_type", "")),
                            }
                        )

            elif step == 5:
                # id = report_id；上游 = executions（通过 run_id）
                _rep = conn.execute(
                    "SELECT run_id FROM generated_reports WHERE id=%s AND project=%s AND version_id=%s",
                    (int(id), _proj, _vid),
                ).fetchone()
                if _rep and _rep.get("run_id"):
                    _rid = str(_rep["run_id"])
                    rows = conn.execute(
                        "SELECT id, case_id, status FROM executions "
                        "WHERE run_id=%s AND project=%s AND version_id=%s "
                        "ORDER BY executed_at DESC LIMIT 200",
                        (_rid, _proj, _vid),
                    ).fetchall()
                    for r in rows:
                        upstream.append(
                            {
                                "step": 4,
                                "id": str(r["case_id"]),
                                "label": "执行 #"
                                + str(r["id"])
                                + " · "
                                + str(r.get("status", "")),
                            }
                        )

            return jsonify({"ok": True, "upstream": upstream, "downstream": downstream})
        finally:
            if conn:
                release_connection(conn)
    except Exception as e:
        _log.error("[api/pipeline-trace] 异常: %s", e, exc_info=True)
        return jsonify({"ok": False, "error": "服务器内部错误"}), 500
