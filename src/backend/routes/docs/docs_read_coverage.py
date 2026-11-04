# -*- coding: utf-8 -*-
"""
文档中心 — 覆盖率路由
====================
"""

from flask import jsonify, request
from src.core.guarded import guard
import logging
from collections import Counter

_log = logging.getLogger(__name__)

from src.backend.routes.docs import docs_bp
from src.backend.state import _current_project
from src.models.permission import check_admin_or_permission


@docs_bp.route("/api/docs/coverage")
def api_docs_coverage():
    ok, err = check_admin_or_permission("docs.view", project=_current_project())
    if not ok:
        return jsonify({"ok": False, "error": err}), (
            401 if err.startswith("请先登录") else 403
        )
    from src.backend.helpers import _resolve_request_project, _temporary_project

    req_project = request.args.get("project", "").strip()
    project_name, err = _resolve_request_project(req_project)
    if err:
        msg, status = err
        return jsonify({"ok": False, "error": msg}), status
    assess = request.args.get("assess", "") == "1"
    try:
        from src.core.excel_handler import excel_handler

        # ★ excel_handler.read_requirements/read_test_cases 依赖全局项目，需临时切换
        with _temporary_project(project_name):
            reqs = excel_handler.read_requirements()
            intents = excel_handler.read_test_cases()
        if not reqs:
            return jsonify({"ok": False, "error": "暂无需求拆解数据"}), 404

        intent_count_by_req = Counter()
        for c in intents:
            cid = c.get("case_id", "")
            rid = cid.rsplit("-", 1)[0] if "-" in cid else ""
            if rid:
                intent_count_by_req[rid] += 1

        coverage = []
        gap_items = []
        total_reqs = len(reqs)
        covered = 0
        low = 0
        uncovered = 0
        for r in reqs:
            rid = r.get("req_id", "")
            module = r.get("module", "")
            desc = r.get("description", "")[:80]
            count = intent_count_by_req.get(rid, 0)
            if count >= 3:
                status = "ok"
                covered += 1
            elif count >= 1:
                status = "low"
                covered += 1
                low += 1
            else:
                status = "gap"
                uncovered += 1
            item = {
                "req_id": rid,
                "module": module,
                "desc": desc,
                "intent_count": count,
                "status": status,
            }
            coverage.append(item)
            if status in ("gap", "low"):
                gap_items.append(item)

        result = {
            "total_reqs": total_reqs,
            "covered": covered,
            "uncovered": uncovered,
            "low_count": low,
            "coverage": coverage,
        }

        if assess and gap_items:
            with guard("api_docs_coverage: from src.ai.ai_client import ai_brai", log=_log, level=logging.WARNING):
                from src.ai.ai_client import ai_brain

                gap_text = "\n".join(
                    "- [%s] %s: %s (测试用例数=%d, 状态=%s)"
                    % (
                        g["req_id"],
                        g["module"],
                        g["desc"][:60],
                        g["intent_count"],
                        g["status"],
                    )
                    for g in gap_items[:15]
                )
                prompt = (
                    "你是测试覆盖度评估专家。以下需求存在测试用例覆盖不足的问题。\n"
                    "对每条需求，判断风险等级（high/medium/low），并给出简短建议（≤30字）。\n\n"
                    "## 输出格式（严格 JSON）\n"
                    '{"risks": [{"req_id": "REQ-xxx", "risk": "high", "advice": "建议补充异常场景测试用例"}, ...]}\n'
                )
                ai_result = ai_brain._call_api(
                    system_prompt=prompt,
                    user_message=gap_text,
                    temperature=0.2,
                    max_tokens=512,
                    caller_name="覆盖度评估",
                    task_type="test_design",
                    force_cloud=True,
                )
                if ai_result.get("success"):
                    parsed = ai_brain._parse_json_response(ai_result["data"])
                    if parsed.get("success"):
                        risks = {
                            r["req_id"]: r for r in parsed["data"].get("risks", [])
                        }
                        for item in result["coverage"]:
                            if item["req_id"] in risks:
                                item["risk"] = risks[item["req_id"]]["risk"]
                                item["advice"] = risks[item["req_id"]]["advice"]
        return jsonify(result)
    except Exception as e:
        import traceback

        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)}), 500
