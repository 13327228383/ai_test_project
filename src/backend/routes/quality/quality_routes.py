# -*- coding: utf-8 -*-
"""质量治理只读聚合 API（W3 九类能力）。

设计依据：docs/frontend-adaptation-design-2026-08-06.md §3.2 / §8.5 / §8.9

端点：
    GET /api/quality/w3-summary — 九类质量治理能力的只读摘要聚合

★ 蓝图归属决策（§1.1 D1）：新建独立 `quality_bp`，不扩展 `metrics_page_bp`。
  理由是职责边界 —— metrics_page 是「执行结果指标」，quality 是「治理能力状态」，
  两者的开关语义、权限要求、缓存策略都不同，混在一起会让 metrics_page 被迫
  引入九个 W3 开关判断，后续任何一方改动都要回归另一方。

★ 返回格式（§8.5）：本蓝图为新增，统一用 `{"ok": true, ...}`。
  注意 diagnostics_bp 用的是 `{"success": ...}` —— 三套格式并存是历史现实，
  改现有端点时沿用原格式，新端点一律用 ok。
"""
from __future__ import annotations
from src.core.guarded import guard

import logging
from datetime import datetime, timezone

from flask import Blueprint, jsonify, request

from src.backend.state import _resolve_project

# ★ 直接具名导入子模块成员，而不是 `from src.backend.routes.quality import w3_collectors`。
#   原因：本包的 __init__.py 会 re-export 本模块，若本模块再回过头去取父包属性，
#   加载顺序上父包正处于「部分初始化」状态，属性尚未绑定，只能依赖解释器的
#   sys.modules 兜底。直接 import 子模块路径不依赖父包属性，从结构上杜绝该风险。
from src.backend.routes.quality.w3_collectors import (
    CAP_FLAGS,
    _safe as safe_collect,
    collect_adi,
    collect_engine_health,
    collect_fake_pass,
    collect_flag_only,
    collect_identity_pool,
    collect_perf_baseline,
    collect_privilege_matrix,
    disabled_for,
    flag_enabled,
)

_log = logging.getLogger(__name__)

quality_bp = Blueprint("quality", __name__, url_prefix="/api/quality")

# 九类能力的固定顺序（前端按此顺序渲染卡片，勿随意调整）
CAP_ORDER: tuple[str, ...] = (
    "fake_pass",
    "engine_health",
    "perf_baseline",
    "adi",
    "privilege_matrix",
    "identity_pool",
    "contract",
    "network_fault",
    "idempotency",
)

# 仅需开关三态、无明细数据源的能力（§3.2 表注：oracle_results 尚无这三类记录）
_FLAG_ONLY_CAPS: frozenset[str] = frozenset(
    {"contract", "network_fault", "idempotency"}
)


@quality_bp.before_request
def _require_login():
    """质量治理数据属于内部运营信息，未登录一律拒绝。

    只校验「是否登录」，不校验具体权限 —— 细粒度的明细下钻权限
    在 `api_w3_summary` 内单独判断（无权限时降级为只给计数，而非整体 403）。
    """
    try:
        from src.core.config_loader import get_current_user

        if not get_current_user():
            return jsonify({"ok": False, "error": "请先登录"}), 401
    except Exception as e:  # noqa: BLE001 —— 鉴权组件异常时 fail-closed
        _log.warning("[Quality] 登录校验异常(拒绝访问): %s", str(e)[:160])
        return jsonify({"ok": False, "error": "鉴权失败"}), 401
    return None


@quality_bp.route("/w3-summary", methods=["GET"])
def api_w3_summary():
    """W3 九类质量治理能力只读摘要聚合。

    Query:
        project   : str  可选，缺省取当前项目（_resolve_project）
        run_id   : str  可选，缺省 = 最近一次 run（由各采集器内部处理）
        caps     : str  可选，逗号分隔的能力名白名单，用于前端按需拉取

    纯只读、纯聚合：不写任何表、不触发任何执行、不改变任何配置。
    单个采集器异常 → 该能力块降级为 {"enabled": <flag>, "error": "..."}，
    整体仍返回 HTTP 200，绝不因单点失败导致整页报错。
    """
    project = _resolve_project(request.args.get("project"))
    run_id = (request.args.get("run_id") or "").strip()
    caps = _parse_caps(request.args.get("caps", ""))
    with_details = _may_view_details()

    summary: dict[str, dict] = {}
    for cap in CAP_ORDER:
        if cap not in caps:
            continue
        summary[cap] = _collect_one(cap, project, run_id, with_details)

    return jsonify(
        {
            "ok": True,
            "project": project,
            "run_id": run_id,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "summary": summary,
        }
    )


@quality_bp.route("/methodology-validation", methods=["GET"])
def api_methodology_validation():
    """★ T2 流水线强化专项：Step3 方法论标签校验结果只读查询。

    读取 case_meta 中 T2 增量列（method_valid=0 或 method_fix_note 非空）的用例，
    供 quality-panel.html 展示「标签非法/已修正」标记（PRD P1-1 前端可选交付）。

    Query:
        project   : str  可选，缺省取当前项目（_resolve_project）
        version_id: str  可选，按项目版本过滤
        limit     : int  可选，默认 100

    Returns:
        {"ok": True, "project": ..., "items": [...], "stats": {...}}
    """
    project = _resolve_project(request.args.get("project"))
    version_id = (request.args.get("version_id") or "").strip()
    try:
        limit = min(int(request.args.get("limit", 100)), 500)
    except (TypeError, ValueError):
        limit = 100

    items: list[dict] = []
    stats = {"total": 0, "invalid": 0, "auto_fixed": 0, "blocked": 0}
    if not project:
        return jsonify({"ok": True, "project": "", "items": [], "stats": stats})

    try:
        from src.core.db_manager import get_connection

        conn = None
        try:
            conn = get_connection()
            params: list = [project]
            where = "WHERE project=%s"
            if version_id:
                where += " AND version_id=%s"
                params.append(version_id)
            where += " AND (method_valid=0 OR method_fix_note <> '')"
            params.append(limit)
            rows = conn.execute(
                "SELECT case_id, project, version_id, method_valid, method_fix_note "
                "FROM case_meta %s ORDER BY updated_at DESC LIMIT %%s" % where,
                params,
            ).fetchall() or []
            for r in rows:
                note = str(r.get("method_fix_note") or "")
                # ★ 修复 falsy-0 陷阱：method_valid 列值为 0 时 `0 or 1` 会求值为 1，
                #   导致所有「高危阻断」用例被判成「已自动修正」（auto_fixed 虚高、blocked 恒 0）。
                #   必须显式区分「缺省(视为通过)」与「显式 0(非法/阻断)」。
                _raw_mv = r.get("method_valid")
                is_invalid = _raw_mv is not None and int(_raw_mv or 0) == 0
                items.append(
                    {
                        "case_id": r.get("case_id", ""),
                        "project": r.get("project", ""),
                        "version_id": r.get("version_id", 0),
                        "method_valid": 0 if is_invalid else 1,
                        "method_fix_note": note,
                        "disposition": "blocked" if is_invalid else "auto_fix",
                    }
                )
            stats["total"] = len(items)
            stats["invalid"] = sum(1 for it in items if it["method_valid"] == 0)
            stats["auto_fixed"] = sum(1 for it in items if it["method_valid"] == 1)
            stats["blocked"] = stats["invalid"]
        finally:
            if conn:
                with guard("api_methodology_validation: conn.close()", log=_log):
                    conn.close()
    except Exception as e:  # noqa: BLE001 —— 单点失败不整页报错
        _log.warning("[Quality] methodology-validation 查询异常: %s", str(e)[:160])
        return jsonify(
            {"ok": False, "error": f"查询失败: {e}", "items": [], "stats": stats}
        )

    return jsonify({"ok": True, "project": project, "items": items, "stats": stats})


@quality_bp.route("/semantic-merge/latest", methods=["GET"])
def api_semantic_merge_latest():
    """★ T4 流水线强化专项：最近一次 Step2 功能点语义聚合统计只读查询。

    返回 feature_driven 语义聚合去重（_fd_semantic_dedup）的最近一次统计
    （合并前 N 条 → 合并后 M 条 + 可解释合并依据 + timestamp）。
    统计已持久化到 src/verification/meta/semantic_merge_latest.json，
    服务重启后仍可读；从未执行过语义聚合时返回全 0 空统计（has_data=false）。
    """
    try:
        from src.pipeline.steps.step2.analyzers._fd_semantic_dedup import (
            latest_merge_stats,
        )

        stats = latest_merge_stats()
    except Exception as e:  # noqa: BLE001
        _log.warning("[Quality] semantic-merge/latest 读取异常: %s", str(e)[:160])
        stats = {"before": 0, "after": 0, "merged_count": 0, "logs": []}
    stats["has_data"] = bool(stats.get("timestamp") or (stats.get("before") or 0) > 0)
    return jsonify({"ok": True, "stats": stats})


# ══════════════════════════════════════════════════════════════
# 内部辅助
# ══════════════════════════════════════════════════════════════


def _parse_caps(raw: str) -> frozenset[str]:
    """解析 `caps` 参数 → 待采集能力集合。

    缺省或全部非法 → 返回全部九类（保证老前端不传 caps 时行为不变）。
    非法能力名静默忽略，不报 400 —— 摘要接口应尽量可用，
    前端多传一个未知能力名不该让整张面板拿不到数据。
    """
    wanted = {c.strip() for c in str(raw or "").split(",") if c.strip()}
    valid = wanted & set(CAP_ORDER)
    return frozenset(valid) if valid else frozenset(CAP_ORDER)


def _may_view_details() -> bool:
    """是否允许查看下钻明细（top_hits / patterns）。

    §3.2 安全约束：明细含 case_id、失败链路特征等定位信息，
    要求 `execution.run` 权限；无权限时**不报错**，而是只返回计数字段 ——
    这样低权限用户仍能看到"有多少假 PASS"，只是看不到"具体是哪几条"。
    """
    try:
        from src.models.permission import check_admin_or_permission
        from src.backend.state import _current_project

        ok, _err = check_admin_or_permission(
            "execution.run", project=_current_project()
        )
        return bool(ok)
    except Exception as e:  # noqa: BLE001 —— 权限组件异常时按"无明细权限"降级
        _log.warning("[Quality] 明细权限判定异常(降级为无明细): %s", str(e)[:160])
        return False


def _collect_one(cap: str, project: str, run_id: str, with_details: bool) -> dict:
    """采集单个能力块：先判开关，再走 `_safe` 兜底执行。

    ★ 红线 2：开关关闭时**直接返回三字段的 disabled 块**，
      根本不进入采集器 —— 既省掉一次 DB 查询，也从结构上杜绝了
      "关着的能力却返回了 0" 这类误导性数据。
    """
    flag_key = CAP_FLAGS[cap]
    if not flag_enabled(flag_key):
        return disabled_for(cap)

    if cap in _FLAG_ONLY_CAPS:
        return safe_collect(lambda: collect_flag_only(flag_key, cap), cap)

    if not project:
        # 开关开着但没有项目上下文：如实说明，不返回任何数值
        return {
            "enabled": True,
            "flag_key": flag_key,
            "has_data": False,
            "error": "缺少 project 参数，无法采集",
        }

    collectors = {
        "fake_pass": lambda: collect_fake_pass(project, run_id, with_details),
        "engine_health": lambda: collect_engine_health(project, run_id, with_details),
        "perf_baseline": lambda: collect_perf_baseline(project, run_id),
        "adi": lambda: collect_adi(project),
        "privilege_matrix": lambda: collect_privilege_matrix(project),
        "identity_pool": lambda: collect_identity_pool(project),
    }
    return safe_collect(collectors[cap], cap)
