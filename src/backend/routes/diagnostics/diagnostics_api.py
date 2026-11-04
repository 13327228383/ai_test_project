# -*- coding: utf-8 -*-
"""诊断中心 — 三大功能 API：
1. POST /api/diagnostics/self-test — 流水线自检
2. GET  /api/diagnostics/health  — Step 健康快照
3. GET  /api/diagnostics/failures — 失败归因汇总
"""

import logging
from flask import Blueprint, request, jsonify

from src.backend.state import _resolve_project

_log = logging.getLogger(__name__)

diagnostics_bp = Blueprint("diagnostics", __name__, url_prefix="/api/diagnostics")


@diagnostics_bp.route("/self-test", methods=["POST"])
def run_self_test():
    """执行流水线自检，返回Step级结果。"""
    try:
        data = request.get_json(silent=True) or {}
        project = _resolve_project(data.get("project"))

        if not project:
            return jsonify({"success": False, "error": "缺少 project 参数"}), 400

        from src.pipeline.diagnostics.self_test import run_self_test as _run

        result = _run(
            project=project,
            version_label=data.get("version_label", ""),
            target_url=data.get("target_url", ""),
            user_id=data.get("user_id", ""),
        )

        return jsonify({"success": True, "data": result})

    except Exception as e:
        _log.error("[Diagnostics] 自检异常: %s", str(e)[:200])
        return jsonify({"success": False, "error": str(e)[:200]}), 500


@diagnostics_bp.route("/self-test/latest", methods=["GET"])
def get_latest_self_test():
    """获取最近一次自检结果。"""
    project = _resolve_project(request.args.get("project"))

    if not project:
        return jsonify({"success": False, "error": "缺少 project 参数"}), 400

    from src.pipeline.diagnostics.self_test import get_latest_self_test as _get

    result = _get(project)
    if result is None:
        return jsonify({"success": True, "data": None, "message": "暂无自检记录"})

    return jsonify({"success": True, "data": result})


@diagnostics_bp.route("/self-test/history", methods=["GET"])
def get_self_test_history():
    """获取自检历史记录。"""
    project = _resolve_project(request.args.get("project"))
    limit = min(int(request.args.get("limit", 10)), 50)

    if not project:
        return jsonify({"success": False, "error": "缺少 project 参数"}), 400

    from src.pipeline.diagnostics.self_test import get_self_test_history as _get_history

    result = _get_history(project, limit)
    return jsonify({"success": True, "data": result})


# ══════════════════════════════════════════════════
# Step 健康快照
# ══════════════════════════════════════════════════


@diagnostics_bp.route("/health", methods=["GET"])
def get_step_health():
    """获取各Step健康快照（从 ai_health_metrics 聚合）。"""
    project = _resolve_project(request.args.get("project"))
    run_id = request.args.get("run_id", "")

    if not project:
        return jsonify({"success": False, "error": "缺少 project 参数"}), 400

    try:
        from src.core.db_manager import get_connection

        conn = get_connection()
        try:
            cur = conn.cursor()

            if run_id:
                # 指定 run_id 的详细快照
                cur.execute(
                    "SELECT metric_type, value, run_id, created_at "
                    "FROM ai_health_metrics "
                    "WHERE project=%s AND run_id=%s AND metric_type LIKE 'step_%%' "
                    "ORDER BY metric_type",
                    (project, run_id),
                )
            else:
                # 最新一次运行的快照
                cur.execute(
                    "SELECT metric_type, value, run_id, created_at "
                    "FROM ai_health_metrics "
                    "WHERE project=%s AND metric_type LIKE 'step_%%' "
                    "AND run_id = (SELECT run_id FROM ai_health_metrics WHERE project=%s "
                    "              AND metric_type LIKE 'step_%%' ORDER BY created_at DESC LIMIT 1) "
                    "ORDER BY metric_type",
                    (project, project),
                )

            rows = cur.fetchall()
            cur.close()

            # 按 step 分组
            steps = {}
            for row in rows:
                is_dict = isinstance(row, dict)
                mtype = row["metric_type"] if is_dict else row[0]
                mvalue = row["value"] if is_dict else row[1]
                mrun = row["run_id"] if is_dict else row[2]
                mtime = str(row["created_at"] if is_dict else row[3])

                # step2_coverage_rate → step2
                step = mtype.split("_")[0] if "_" in mtype else mtype
                key = mtype[len(step) + 1 :] if mtype.startswith(step + "_") else mtype

                if step not in steps:
                    steps[step] = {"run_id": mrun, "updated_at": mtime, "metrics": {}}
                steps[step]["metrics"][key] = mvalue

            return jsonify(
                {
                    "success": True,
                    "data": {
                        "steps": steps,
                        "run_id": run_id
                        or (
                            rows[0]["run_id"]
                            if isinstance(rows[0], dict)
                            else rows[0][2] if rows else ""
                        ),
                    },
                }
            )

        finally:
            conn.close()
    except Exception as e:
        _log.error("[Diagnostics] 健康快照查询失败: %s", str(e)[:200])
        return jsonify({"success": False, "error": str(e)[:200]}), 500


# ══════════════════════════════════════════════════
# 失败归因汇总 / 明细
# ══════════════════════════════════════════════════

# 失败分类中文名。原为 get_failure_attribution() 内的局部变量，
# 因 D3 明细分支也要用同一份映射，提升为模块级常量避免两处各写一份而漂移。
# （提升为常量不改变任何 SQL 与返回结构，汇总分支返回体逐字节不变。）
_CATEGORY_LABELS = {
    "PRODUCT_BUG": "目标系统缺陷",
    "HARNESS_ERROR": "流水线自身问题",
    "ENVIRONMENT_ERROR": "环境问题",
    "DATA_ERROR": "数据问题",
    "MISSING_PARAM": "缺少必要参数",
    "PAGE_MISMATCH": "页面不匹配",
    "ELEMENT_NOT_FOUND": "元素未找到",
}

# 明细分支分页上限。归因明细是给人看的排查列表，不是导出接口；
# 放开上限会让一次请求拉走整个 run 的失败行，拖垮页面也拖垮 DB。
_DETAIL_LIMIT_DEFAULT = 50
_DETAIL_LIMIT_MAX = 200

# ══════════════════════════════════════════════════════════════════════════
# 明细分支主查询 SQL
# ══════════════════════════════════════════════════════════════════════════
# 🔴 BUG#1 修复（QA_T01_T02_VERIFY_2026-08-06.md §三 BUG#1，2026-08-07）
#
# 原实现用 `LEFT JOIN test_cases tc ON tc.project=e.project AND
# tc.version_id=e.version_id AND tc.case_id=e.case_id` 取 case_name。
# 真库实证：`test_cases` 的 (project, version_id, case_id) **并不唯一**
# （complex_crm/v316 下 CRM-EXP-001 有 3 行、CRM-ORD-001 / CRM-SET-001 各 2 行），
# 一对多 JOIN 把 executions 行复制了：
#     HARNESS_ERROR  total=5 → len(items)=7   exec_id=[357,357,353,353,309,299,285]
#     DATA_ERROR     total=2 → len(items)=4   exec_id=[355,355,355,333]
# 后果有三层：① 汇总说 5 条、下钻列 7 条，归因面板自相矛盾；
#            ② total != len(items)，前端分页器算错页数；
#            ③ LIMIT/OFFSET 作用在膨胀集上，翻页会丢数据。
#
# 修复方案 A（QA 推荐）：改用**相关子查询**取 case_name，彻底去掉 JOIN。
# 这样主查询的行集严格等于 COUNT(*) 所数的行集，total 与 items 天然一致，
# LIMIT/OFFSET 也回到正确的分页语义。
#
# ⚠️ 两个必须保留的口径：
#   1) 子查询带 `LIMIT 1` —— 脏数据下同键多行时取任意一行的 title，
#      绝不能让子查询返回多行（MySQL 会直接报 1242 Subquery returns more than 1 row）。
#   2) 子查询取不到时返回 NULL（相当于原 LEFT JOIN 的语义），
#      由 `_build_failure_item()` 回退成 case_id —— 保住 §Q8 要求的
#      legacy（version_id=0）数据兼容，不会整片丢失。
#
# ★ 顺带补 `ORDER BY ... , e.id DESC` 稳定排序键：
#   同一个 run 内多条 executions 的 executed_at 常常同秒（批量执行），
#   只按 executed_at 排序时 MySQL 对并列行的返回顺序不保证稳定，
#   翻页时同一行可能在第 1 页和第 2 页各出现一次、另一行两页都不出现。
#   这与 BUG#1 的「翻页丢数据」是同一类症状的第二个来源，一并封堵。
_SQL_DETAIL_ROWS = (
    "SELECT e.id, e.case_id, e.run_id, e.failure_chain, e.failure_reason, "
    "       e.screenshot, e.executed_at, "
    "       (SELECT tc.title FROM test_cases tc "
    "         WHERE tc.project = e.project AND tc.version_id = e.version_id "
    "           AND tc.case_id = e.case_id LIMIT 1) AS case_name "
    "FROM executions e "
    "WHERE e.project=%s AND e.status IN ('FAIL','ERROR') "
    "  AND e.failure_category=%s AND e.run_id=%s "
    "ORDER BY e.executed_at DESC, e.id DESC "
    "LIMIT %s OFFSET %s"
)


@diagnostics_bp.route("/failures", methods=["GET"])
def get_failure_attribution():
    """失败归因汇总 / 明细。

    Query:
        project  : str  可选
        run_id   : str  可选
        category : str  ★新增。为空 → 原汇总分支（返回体逐字节不变）；
                        非空 → 明细分支，返回该归因下的用例清单
        limit    : int  ★新增，默认 50，上限 200（仅明细分支）
        offset   : int  ★新增，默认 0（仅明细分支）
    """
    project = _resolve_project(request.args.get("project"))
    run_id = request.args.get("run_id", "")

    if not project:
        return jsonify({"success": False, "error": "缺少 project 参数"}), 400

    # ★ D3：带 category 时走明细分支。分流点放在这里（而不是在下方 SQL 里加 if），
    #   是为了让汇总分支的代码路径与改造前完全一致 —— diagnostics.html:302 的
    #   fetch('/api/diagnostics/failures') 零改动继续工作。
    category = (request.args.get("category") or "").strip()
    if category:
        return _failure_detail_branch(project, run_id, category)

    try:
        from src.core.db_manager import get_connection

        conn = get_connection()
        try:
            cur = conn.cursor()

            if run_id:
                # 指定 run_id 的失败归因
                # ★ F5 修复（2026-08-05 W3）：status 实际值为大写 FAIL/ERROR
                #   （record_execution 写入，见 schema.sql status 注释），原查询用
                #   小写 'failed' 永远匹配不到 → 归因分布恒空。改为 IN ('FAIL','ERROR')。
                cur.execute(
                    "SELECT failure_category, COUNT(*) as cnt "
                    "FROM executions "
                    "WHERE project=%s AND run_id=%s AND status IN ('FAIL','ERROR') "
                    "AND failure_category IS NOT NULL AND failure_category != '' "
                    "GROUP BY failure_category "
                    "ORDER BY cnt DESC",
                    (project, run_id),
                )
            else:
                # 最新一次运行的失败归因
                cur.execute(
                    "SELECT failure_category, COUNT(*) as cnt "
                    "FROM executions "
                    "WHERE project=%s AND status IN ('FAIL','ERROR') "
                    "AND failure_category IS NOT NULL AND failure_category != '' "
                    "AND run_id = (SELECT run_id FROM executions WHERE project=%s "
                    "              AND status IN ('FAIL','ERROR') ORDER BY executed_at DESC LIMIT 1) "
                    "GROUP BY failure_category "
                    "ORDER BY cnt DESC",
                    (project, project),
                )

            rows = cur.fetchall()
            cur.close()

            categories = {}
            for row in rows:
                is_dict = isinstance(row, dict)
                cat = row["failure_category"] if is_dict else row[0]
                cnt = row["cnt"] if is_dict else row[1]
                categories[cat] = cnt

            # 补充失败分类说明（映射表已提升为模块级 _CATEGORY_LABELS，内容不变）
            category_labels = _CATEGORY_LABELS

            enriched = []
            for cat, cnt in categories.items():
                enriched.append(
                    {
                        "category": cat,
                        "label": category_labels.get(cat, cat),
                        "count": cnt,
                    }
                )

            return jsonify(
                {
                    "success": True,
                    "data": {
                        "categories": enriched,
                        "total_failed": sum(c["count"] for c in enriched),
                        "run_id": run_id,
                    },
                }
            )

        finally:
            conn.close()
    except Exception as e:
        _log.error("[Diagnostics] 失败归因查询失败: %s", str(e)[:200])
        return jsonify({"success": False, "error": str(e)[:200]}), 500


def _clamp_int(raw, default: int, low: int, high: int) -> int:
    """把查询参数安全地收敛成区间内的整数（非法输入回落默认值）。"""
    try:
        val = int(raw)
    except (TypeError, ValueError):
        return default
    return max(low, min(val, high))


def _resolve_latest_failed_run(cur, project: str) -> str:
    """未指定 run_id 时取「最近一次有失败的 run」。

    与汇总分支 :191-192 的子查询口径保持一致，否则同一页面上
    汇总数字和下钻明细会来自不同的 run，用户会以为数据错了。
    """
    cur.execute(
        "SELECT run_id FROM executions WHERE project=%s "
        "AND status IN ('FAIL','ERROR') ORDER BY executed_at DESC LIMIT 1",
        (project,),
    )
    row = cur.fetchone()
    if not row:
        return ""
    return str((row["run_id"] if isinstance(row, dict) else row[0]) or "")


def _build_failure_item(row: dict) -> dict:
    """executions 行 → 明细项。"""
    from src.pipeline.steps.step5.engine_health import _chain_pattern

    case_id = str(row.get("case_id", "") or "")
    run_id = str(row.get("run_id", "") or "")
    # ★ §8.8：失败链路解析口径唯一 —— 必须复用 engine_health._chain_pattern()。
    #   自己再解析一遍 failure_chain 会与引擎健康报告的 pattern 口径漂移，
    #   同一条失败在两个页面显示成两种描述。
    failed_step = _chain_pattern(row.get("failure_chain"), row.get("failure_reason"))
    # ⚠️ executions 表没有 case_name 列，靠**相关子查询**从 test_cases 取 title
    #    （见 _SQL_DETAIL_ROWS 的说明：绝不能用 JOIN，会行膨胀）。
    #    legacy 数据（version_id=0）可能取不到 → 回退显示 case_id，不显示空白。
    case_name = str(row.get("case_name") or "").strip() or case_id
    executed_at = row.get("executed_at")

    return {
        "exec_id": row.get("id"),
        "case_id": case_id,
        "case_name": case_name,
        "run_id": run_id,
        "failed_step": failed_step,
        "failure_reason": str(row.get("failure_reason", "") or "")[:200],
        "screenshot": str(row.get("screenshot", "") or ""),
        "log_ref": "/log-panel?run_id=%s&case_id=%s" % (run_id, case_id),
        "executed_at": str(executed_at) if executed_at else "",
    }


def _failure_detail_branch(project: str, run_id: str, category: str):
    """失败归因明细分支（D3）。

    返回该 run 下指定归因类别的失败用例清单，供 diagnostics 页面下钻。
    沿用 diagnostics_bp 的 `{"success": ..., "data": ...}` 返回格式（§8.5）。
    """
    limit = _clamp_int(
        request.args.get("limit"), _DETAIL_LIMIT_DEFAULT, 1, _DETAIL_LIMIT_MAX
    )
    offset = _clamp_int(request.args.get("offset"), 0, 0, 10**9)

    try:
        from src.core.db_manager import get_connection

        conn = get_connection()
        try:
            cur = conn.cursor()

            effective_run = run_id or _resolve_latest_failed_run(cur, project)
            if not effective_run:
                # 没有任何失败记录：返回空清单而不是 404 —— 前端下钻到空类别属正常态
                cur.close()
                return jsonify(
                    {
                        "success": True,
                        "data": {
                            "category": category,
                            "label": _CATEGORY_LABELS.get(category, category),
                            "total": 0,
                            "limit": limit,
                            "offset": offset,
                            "run_id": "",
                            "items": [],
                        },
                    }
                )

            # ★ status 用大写 IN ('FAIL','ERROR')（§8.8 F5 修复结论），
            #   小写 'failed' 永远匹配不到，禁止改回。
            cur.execute(
                "SELECT COUNT(*) AS cnt FROM executions "
                "WHERE project=%s AND status IN ('FAIL','ERROR') "
                "AND failure_category=%s AND run_id=%s",
                (project, category, effective_run),
            )
            total_row = cur.fetchone()
            total = int(
                (total_row.get("cnt") if isinstance(total_row, dict) else total_row[0])
                or 0
            )

            # ★ BUG#1 修复：主查询改用 _SQL_DETAIL_ROWS（相关子查询取 case_name，
            #   无 JOIN → 无行膨胀 → len(items) 恒等于同条件 COUNT(*) 的分页切片）。
            cur.execute(
                _SQL_DETAIL_ROWS,
                (project, category, effective_run, limit, offset),
            )
            rows = cur.fetchall() or []
            cur.close()

            items = [
                _build_failure_item(r if isinstance(r, dict) else dict(r)) for r in rows
            ]

            return jsonify(
                {
                    "success": True,
                    "data": {
                        "category": category,
                        "label": _CATEGORY_LABELS.get(category, category),
                        "total": total,
                        "limit": limit,
                        "offset": offset,
                        "run_id": effective_run,
                        "items": items,
                    },
                }
            )
        finally:
            conn.close()
    except Exception as e:
        _log.error("[Diagnostics] 失败归因明细查询失败: %s", str(e)[:200])
        return jsonify({"success": False, "error": str(e)[:200]}), 500


# ══════════════════════════════════════════════════
# Step 健康快照（按 run 列表）
# ══════════════════════════════════════════════════


@diagnostics_bp.route("/health/runs", methods=["GET"])
def get_recent_runs():
    """获取最近的运行 ID 列表，用于选择查看健康快照。"""
    project = _resolve_project(request.args.get("project"))
    limit = min(int(request.args.get("limit", 10)), 50)

    if not project:
        return jsonify({"success": False, "error": "缺少 project 参数"}), 400

    try:
        from src.core.db_manager import get_connection

        conn = get_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT DISTINCT run_id, MAX(created_at) as latest "
                "FROM ai_health_metrics "
                "WHERE project=%s AND metric_type LIKE 'step_%%' "
                "GROUP BY run_id ORDER BY latest DESC LIMIT %s",
                (project, limit),
            )
            rows = cur.fetchall()
            cur.close()

            runs = []
            for row in rows:
                is_dict = isinstance(row, dict)
                runs.append(
                    {
                        "run_id": row["run_id"] if is_dict else row[0],
                        "latest": str(row["latest"] if is_dict else row[1]),
                    }
                )

            return jsonify({"success": True, "data": runs})
        finally:
            conn.close()
    except Exception as e:
        _log.error("[Diagnostics] 运行列表查询失败: %s", str(e)[:200])
        return jsonify({"success": False, "error": str(e)[:200]}), 500
