# -*- coding: utf-8 -*-
"""
审核路由 — 测试用例审核端点（注册到 exec_bp）
"""
from __future__ import annotations
from src.core.guarded import guard

import logging
from typing import Any
from flask import jsonify, request

_log = logging.getLogger(__name__)

from src.backend.state import _current_project, _set_paused_step, _get_paused_step
from src.models.permission import check_admin_or_permission

# ── 从拆分子模块 import ──
from ._remodel import _remodel_intent
from ._state import (
    _CONFIDENCE_HIGH,
    _CONFIDENCE_LOW,
    _APPROVAL_RATE_THRESHOLD,
    _get_case_review_state,
    _read_intents_for_review,
)


# ══════════════════════════════════════════════════════════
# 辅助函数
# ══════════════════════════════════════════════════════════


def _get_current_user():
    try:
        from src.core.config_loader import get_current_user

        return get_current_user() or "system"
    except Exception as e:
        _log.warning("[测试用例审核] 获取当前用户失败: %s", e)
        return "system"


def _update_case_review_status(case_id: str, project: str, status: str):
    """★ MySQL-only：通过 review_store 更新测试用例审核状态，不再 raw SQL。"""
    from src.pipeline.utils.review_store import review_store

    reviewer = _get_current_user()
    return review_store.update_case_review_status(
        case_id, status, reviewer=reviewer, project=project
    )


def _update_intent_confidence(case_id: str, project: str, confidence: int):
    """★ MySQL-only：通过 review_store 更新测试用例置信度。"""
    from src.pipeline.utils.review_store import review_store

    review_store.update_intent_confidence(case_id, confidence, project=project)


def _log_review(
    project: str, item_type: str, item_id: str, action: str, comment: str = ""
):
    """★ MySQL-only：通过 review_store 写入审核日志。"""
    from src.pipeline.utils.review_store import review_store

    reviewer = _get_current_user()
    review_store.add_review_log(
        item_type, item_id, action, reviewer=reviewer, comment=comment, project=project
    )


# ══════════════════════════════════════════════════════════
# 路由注册
# ══════════════════════════════════════════════════════════


def _register_routes():
    from src.backend.routes.execution import exec_bp

    @exec_bp.route("/api/cases", methods=["GET"])
    def api_intents_list():
        """列出当前项目当前版本所有测试用例（供 Step4 配置弹窗指定意图使用）。"""
        ok, err = check_admin_or_permission("execution.run", project=_current_project())
        if not ok:
            return jsonify({"ok": False, "error": err}), (
                401 if err.startswith("请先登录") else 403
            )
        project = _current_project()
        if not project:
            return jsonify({"ok": False, "error": "未选择项目"}), 400
        try:
            from src.pipeline.utils.case_store import case_store

            raw = case_store.list_intents_with_meta()
            intents = []

            def _resolve_priority(c) -> str:
                """优先级 P 方案(P0-P3)统一解析。
                test_cases.risk_level 存 P 方案(如 P1)；priority 列存 critical/high 枚举。
                优先用 P 方案原值，其次把 priority 枚举映射到 P。
                """
                rl = str(c.get("risk_level") or "").strip().upper()
                if rl in ("P0", "P1", "P2", "P3"):
                    return rl
                _prio_zh = str(c.get("priority") or "").strip().lower()
                _map = {
                    "critical": "P0",
                    "high": "P1",
                    "medium": "P2",
                    "low": "P3",
                }
                if _prio_zh in _map:
                    return _map[_prio_zh]
                return "P2"

            def _resolve_risk(c) -> str:
                """风险等级口径统一解析 → high/medium/low。
                优先 case_meta._risk_level(审核/KPI 口径)；回退 test_cases.risk_level；
                最后把 P 方案映射到风险(P0→high P1→medium P2/P3→low)。
                """
                cm = str(c.get("_risk_level") or "").strip().lower()
                if cm in ("high", "medium", "low"):
                    return cm
                rl = str(c.get("risk_level") or "").strip().lower()
                if rl in ("high", "medium", "low"):
                    return rl
                _p2r = {"p0": "high", "p1": "medium", "p2": "low", "p3": "low"}
                if rl in _p2r:
                    return _p2r[rl]
                return "medium"

            for c in raw:
                if not c.get("case_id"):
                    continue
                case_id = str(c.get("case_id", "") or "").strip()
                # expected_result 是真实预期；expected_outcome 列=title(历史语义)，不要用
                exp_res = (
                    str(c.get("expected_result") or "")
                    or str((c.get("payload") or {}).get("expected_result") or "")
                ).strip()
                # 执行轨道标签：由 checks 的 track 聚合（与执行端 steps_from_case 口径一致），
                # 供 pipeline 详情页渲染 track 标签；actions 计数字段同样缺失会显示空白/0。
                _tracks: list = []
                for _ch in c.get("checks") or (c.get("payload") or {}).get("checks", []) or []:
                    _t = _ch.get("track") if isinstance(_ch, dict) else None
                    if isinstance(_t, str) and _t.strip() and _t.strip() not in _tracks:
                        _tracks.append(_t.strip())
                intents.append(
                    {
                        "case_id": case_id,
                        "title": str(
                            c.get("title", "") or c.get("intent_title", "") or ""
                        ).strip(),
                        "req_id": str(c.get("req_id", "") or "").strip(),
                        "case_type": str(
                            c.get("case_type", "") or (c.get("payload") or {}).get("type", "") or ""
                        ).strip(),
                        "priority": _resolve_priority(c),
                        "risk_level": _resolve_risk(c),
                        "valid_status": str(
                            c.get("valid_status", "valid") or "valid"
                        ).strip(),
                        "version_id": c.get("version_id", 0),
                        # ── 用例内容（供编辑页填充，之前被丢弃导致空表单） ──
                        "intent_summary": str(
                            c.get("intent_summary")
                            or c.get("summary")
                            or (c.get("payload") or {}).get("intent_summary")
                            or (c.get("payload") or {}).get("description")
                            or c.get("title")
                            or ""
                        ).strip(),
                        "precondition": c.get("precondition")
                        or (c.get("payload") or {}).get("precondition", ""),
                        "expected_outcome": exp_res or "",
                        "expected_result": exp_res or "",
                        "test_steps": c.get("test_steps")
                        or (c.get("payload") or {}).get("test_steps", ""),
                        "actions": c.get("actions")
                        or (c.get("payload") or {}).get("actions", []),
                        "checks": c.get("checks")
                        or (c.get("payload") or {}).get("checks", []),
                        "confidence": c.get("_confidence"),
                        "review_status": str(c.get("_review_status", "") or "").strip(),
                        # ── pipeline 详情页依赖字段（缺失会显示空白/0） ──
                        "required_tracks": _tracks,
                        "actions_count": len(
                            c.get("actions") or (c.get("payload") or {}).get("actions", [])
                        ),
                        "created_at": c.get("created_at") or "",
                        "updated_at": c.get("updated_at") or "",
                    }
                )
            return jsonify({"ok": True, "intents": intents, "total": len(intents)})
        except Exception as e:
            _log.warning("[api/intents] 获取意图列表失败: %s", str(e)[:120])
            return jsonify({"ok": False, "error": "获取意图列表失败"}), 500

    @exec_bp.route("/api/cases/review", methods=["GET"])
    def api_intents_review_list():
        """列出所有测试用例资产 + 元数据（用于审核面板）。"""
        ok, err = check_admin_or_permission("execution.run", project=_current_project())
        if not ok:
            return jsonify({"ok": False, "error": err}), (
                401 if err.startswith("请先登录") else 403
            )
        project = _current_project()
        if not project:
            return jsonify({"ok": False, "error": "未选择项目"}), 400
        intents, source = _read_intents_for_review()
        # ★ §4.17-B(2026-10-28): 存量用例的**占位导航**（target_hint 为 "/"、"/login"
        #   且无 url）在语义上就是"目标页未解析"，只是历史数据里没有该标注。评审页
        #   必须如实显示，不能把站点根伪装成目标页。判据复用生成端唯一源
        #   `is_placeholder_nav`（此处不新建词表），只做读时投影，不回写 DB。
        from src.pipeline.step3._navigation_resolver import mark_nav_unresolved

        for _c in intents:
            if isinstance(_c, dict):
                mark_nav_unresolved(_c, "legacy_placeholder")
        for c in intents:
            c.setdefault("case_id", c.get("case_id", ""))
            c.setdefault("intent_title", c.get("title", ""))
            c.setdefault("case_type", c.get("case_type", ""))
            c.setdefault("risk_risk_level", c.get("risk_level", ""))
        # 计算统计
        total = len(intents)
        approved = sum(1 for c in intents if c.get("_review_status") == "approved")
        high_conf = sum(
            1 for c in intents if c.get("_confidence", 0) >= _CONFIDENCE_HIGH
        )
        low_conf = sum(1 for c in intents if c.get("_confidence", 0) < _CONFIDENCE_LOW)
        high_risk = sum(1 for c in intents if c.get("_risk_level") == "high")

        # ★ v2: 意图驱动指标统计
        # 整改 B3.1：原内联 `_safe_float(c, key)` 副本已收敛到 src/core/utils/num.py
        # 单一真源（保留私名与 (mapping, key) 签名，下方全部调用点零改动）。
        from src.core.utils.num import safe_float_of as _safe_float

        intents_with_data = [
            c for c in intents if _safe_float(c, "_case_driven_rate") > 0
        ]
        avg_intent = sum(_safe_float(c, "_case_driven_rate") for c in intents) / max(
            total, 1
        )
        high_intent = sum(
            1 for c in intents if _safe_float(c, "_case_driven_rate") >= 70
        )
        avg_case_completeness = sum(
            _safe_float(c, "_case_completeness") for c in intents
        ) / max(total, 1)
        avg_evidence = sum(_safe_float(c, "_evidence_total") for c in intents) / max(
            total, 1
        )
        avg_strictness = sum(
            _safe_float(c, "_strictness_score") for c in intents
        ) / max(total, 1)
        # ★ v4.1: System B 执行置信度 + System C 证据校准统计
        avg_exec_conf = sum(_safe_float(c, "_avg_confidence") for c in intents) / max(
            total, 1
        )
        avg_evidence_conf = sum(
            _safe_float(c, "_evidence_confidence") for c in intents
        ) / max(total, 1)
        evidence_calibrated = sum(
            1 for c in intents if c.get("_evidence_verdict") == "evidence_calibrated"
        )
        return jsonify(
            {
                "ok": True,
                "intents": intents,
                "stats": {
                    "total": total,
                    "approved": approved,
                    "high_confidence": high_conf,
                    "low_confidence": low_conf,
                    "high_risk": high_risk,
                    "approval_rate": round(approved / max(total, 1) * 100, 1),
                    # ★ v2: 意图驱动指标
                    "case_driven_rate": round(avg_intent, 1),
                    "case_completeness": round(avg_case_completeness, 1),
                    "evidence_total": round(avg_evidence, 1),
                    "strictness_score": round(avg_strictness, 1),
                    "high_intents": high_intent,
                    "intents_with_data": len(intents_with_data),
                    # ★ v4.1: 三套置信度统计
                    "avg_execution_confidence": round(avg_exec_conf, 1),
                    "avg_evidence_confidence": round(avg_evidence_conf, 1),
                    "evidence_calibrated_count": evidence_calibrated,
                },
                "source": source,
            }
        )

    @exec_bp.route("/api/cases/review/approve", methods=["POST"])
    def api_intents_review_approve():
        """确认单条测试用例。"""
        ok, err = check_admin_or_permission("execution.run", project=_current_project())
        if not ok:
            return jsonify({"ok": False, "error": err}), (
                401 if err.startswith("请先登录") else 403
            )
        data = request.get_json(silent=True) or {}
        case_id = str(data.get("case_id", "")).strip()
        if not case_id:
            return jsonify({"ok": False, "error": "缺少 case_id"}), 400
        project = _current_project() or ""
        if not _update_case_review_status(case_id, project, "approved"):
            # MySQL 1412: 表结构刚被兼容迁移检查修改，重试一次即可恢复。
            if not _update_case_review_status(case_id, project, "approved"):
                from src.pipeline.utils.review_store import review_store

                return (
                    jsonify(
                        {
                            "ok": False,
                            "error": "审核状态写入失败",
                            "case_id": case_id,
                            "detail": review_store.last_error,
                        }
                    ),
                    500,
                )
        _log_review(project, "test_case", case_id, "approve")
        _log.info("[测试用例审核] 已确认: %s", case_id)
        return jsonify({"ok": True, "case_id": case_id})

    @exec_bp.route("/api/cases/review/reject", methods=["POST"])
    def api_intents_review_reject():
        """驳回单条测试用例（触发 AI 重新建模）。"""
        ok, err = check_admin_or_permission("execution.run", project=_current_project())
        if not ok:
            return jsonify({"ok": False, "error": err}), (
                401 if err.startswith("请先登录") else 403
            )
        data = request.get_json(silent=True) or {}
        case_id = str(data.get("case_id", "")).strip()
        if not case_id:
            return jsonify({"ok": False, "error": "缺少 case_id"}), 400
        project = _current_project() or ""
        comment = data.get("comment", "")
        # 标记驳回
        _update_case_review_status(case_id, project, "rejected")
        _log_review(project, "test_case", case_id, "reject", comment)
        # ★ AI 重新建模：获取需求ID → 调用 AI → 去重 → 替换测试用例资产
        remodel_result = _remodel_intent(case_id)
        if remodel_result.get("ok"):
            _update_case_review_status(case_id, project, "pending")
            dup_count = len(remodel_result.get("duplicates", []))
            _log.info(
                "[测试用例审核] 已重新建模: %s (去重移除 %d 条)", case_id, dup_count
            )
            return jsonify(
                {
                    "ok": True,
                    "case_id": case_id,
                    "regenerated": True,
                    "duplicates_removed": dup_count,
                }
            )
        reason = remodel_result.get("reason", "")
        if reason == "all_duplicates":
            return jsonify(
                {
                    "ok": True,
                    "case_id": case_id,
                    "regenerated": False,
                    "hint": "AI 重新生成的用例与同需求已有用例全部重复，未替换。请手动编辑或调整已有用例",
                    "duplicates": remodel_result.get("duplicates", []),
                }
            )
        return jsonify(
            {
                "ok": True,
                "case_id": case_id,
                "regenerated": False,
                "hint": "测试用例已驳回但 AI 重新建模失败，请手动编辑",
                "reason": reason,
            }
        )

    @exec_bp.route("/api/cases/review/edit", methods=["POST"])
    def api_intents_review_edit():
        """★ MySQL-only：编辑测试用例字段（写入 test_cases + case_meta）。

        不再回写 Excel；Excel 仅作为显式导入/导出产物。

        ★ 权限（2026-08-27 产品决策）：执行者（project_member）可修改用例，
          但仅限其被分配的项目；修改会写 review_log（modify）追溯。
        """
        from src.core.config_loader import get_current_user
        from src.users.manager import is_admin
        from src.models.permission import has_permission, can_execute_in_project

        uid = get_current_user()
        if not uid:
            return jsonify({"ok": False, "error": "请先登录"}), 401
        project = _current_project() or ""
        if not (
            is_admin(uid)
            or (has_permission(uid, "case.edit") and can_execute_in_project(uid, project))
        ):
            return jsonify({"ok": False, "error": "无权修改用例（需要权限: case.edit 且为项目成员）"}), 403
        data = request.get_json(silent=True) or {}
        case_id = str(data.get("case_id", "")).strip()
        if not case_id:
            return jsonify({"ok": False, "error": "缺少 case_id"}), 400
        project = _current_project() or ""
        updates = data.get("updates", {})
        if not updates:
            return jsonify({"ok": False, "error": "缺少更新字段"}), 400
        from src.pipeline.utils.case_store import case_store

        # ★ 风险/优先级双口径安全落库(2026-09-05)：
        #   - 编辑页「风险等级」(高/中/低) → case_meta.risk_level(审核/KPI 口径)，不写 test_cases
        #   - 编辑页「优先级」(P0/P1/P2/P3) → test_cases.risk_level(P 方案)
        #   避免旧逻辑把「高/中/低」写进 test_cases.risk_level、把「P 值」写进 priority 枚举列，
        #   导致 P0/P1/P2 与 critical/high 两套方案互相污染。
        import re as _re_prio

        risk_zh = updates.get("risk_level")
        prio = updates.get("priority")
        updates.pop("risk_level", None)
        updates.pop("priority", None)
        if prio and _re_prio.match(r"^P[0-3]$", str(prio).strip(), _re_prio.IGNORECASE):
            updates["risk_level"] = str(prio).strip().upper()  # P 方案 → test_cases.risk_level
        # 普通字节由 update_intent 直接写 test_cases.risk_level（兼容既有非 P 调用方）
        ok2 = case_store.update_intent(case_id, updates)
        if not ok2:
            return jsonify({"ok": False, "error": "MySQL test_cases 更新失败"}), 500
        # 高/中/低 → case_meta.risk_level
        if risk_zh and str(risk_zh).strip():
            from src.pipeline.utils.review_store import review_store

            _risk_en = {
                "高": "high",
                "中": "medium",
                "低": "low",
                "high": "high",
                "medium": "medium",
                "low": "low",
            }.get(str(risk_zh).strip(), "medium")
            review_store.update_case_meta_risk_level(case_id, _risk_en)
        # 重新计算置信度（从 MySQL 读取最新数据）
        with guard("api_intents_review_edit: from src.pipeline.utils.confidence_s", log=_log, level=logging.WARNING):
            from src.pipeline.utils.confidence_scorer import score_test_case

            updated = case_store.get_intent(case_id)
            if updated:
                new_score = score_test_case(updated)
                _update_intent_confidence(case_id, project, new_score)
        _log_review(project, "test_case", case_id, "modify")
        _log.info("[测试用例审核] 已编辑(MySQL): %s", case_id)
        return jsonify({"ok": True, "case_id": case_id})

    # ══════════════════════════════════════════════════════════
    # ★ P0-4: 三车道审核端点（lane-aware）
    # ══════════════════════════════════════════════════════════

    def _get_active_version_id(project: str) -> int:
        """★ P0-4: 获取当前激活版本 ID（统一封装，避免每个端点重复 import）。"""
        try:
            from src.core.versioning import current_version_id

            return int(current_version_id(project) or 0)
        except Exception as e:
            _log.warning("[P0-4] 获取 version_id 失败: %s", str(e)[:80])
            return 0

    def _load_lane_map(project: str, version_id: int) -> dict[str, dict[str, Any]]:
        """★ P0-4: 读取用例级 lane 映射 {case_id: {lane, reason, routed_to_human}}。

        通用性：通过 review_lane_store.read_case_lanes 读取，JOIN test_cases
               将需求级路由映射到用例级。lane 缺失时按需求属性即时派生并回填，
               使外部导入需求的项目同样具备可通行的三车道判据（不降级为全批全过）。
        """
        if not project or version_id is None or version_id < 0:
            return {}
        try:
            from src.pipeline.utils.review_lane_store import (
                ensure_review_lanes,
                read_case_lanes,
            )

            # 缺口路段判定：持久层无 lane 时从 requirements 派生（幂等，不做全批放行）
            try:
                ensure_review_lanes(project, version_id)
            except Exception as _e:
                _log.warning("[P0-4] lane 派生失败(不阻断): %s", str(_e)[:80])
            rows = read_case_lanes(project, version_id) or []
            return {
                str(r.get("case_id") or "").strip(): {
                    "lane": r.get("lane", "yellow"),
                    "reason": r.get("reason", ""),
                    "routed_to_human": bool(r.get("routed_to_human", 1)),
                }
                for r in rows
                if r.get("case_id")
            }
        except Exception as e:
            _log.warning("[P0-4] 读取 lane 映射失败: %s", str(e)[:80])
            return {}

    @exec_bp.route("/api/review/lane", methods=["GET"])
    def api_review_lane_list():
        """★ P0-4: 返回当前项目当前版本的三车道路由清单。

        响应: {ok, lanes: [{case_id, req_id, lane, reason, routed_to_human, review_status}],
               summary: {green, yellow, red, total}}
        通用性：JOIN test_cases 提供用例级视图；review_status 来自 case_meta。
        """
        ok, err = check_admin_or_permission("execution.run", project=_current_project())
        if not ok:
            return jsonify({"ok": False, "error": err}), (
                401 if err.startswith("请先登录") else 403
            )
        project = _current_project() or ""
        if not project:
            return jsonify({"ok": False, "error": "未选择项目"}), 400
        version_id = _get_active_version_id(project)
        if version_id == 0:
            return jsonify({"ok": False, "error": "当前无激活版本"}), 400
        # 读取 lane + 用例审核状态
        lane_map = _load_lane_map(project, version_id)
        intents, _src = _read_intents_for_review()
        lanes_out = []
        for c in intents:
            cid = str(c.get("case_id", "") or "").strip()
            if not cid:
                continue
            lane_info = lane_map.get(
                cid,
                {"lane": "yellow", "reason": "no_lane_data", "routed_to_human": True},
            )
            lanes_out.append(
                {
                    "case_id": cid,
                    "req_id": str(c.get("req_id", "") or "").strip(),
                    "title": str(
                        c.get("title", "") or c.get("intent_title", "") or ""
                    ).strip(),
                    "lane": lane_info["lane"],
                    "reason": lane_info["reason"],
                    "routed_to_human": lane_info["routed_to_human"],
                    "review_status": c.get("_review_status", "pending"),
                    "confidence": c.get("_confidence", 0),
                }
            )
        summary = {
            "green": sum(1 for x in lanes_out if x["lane"] == "green"),
            "yellow": sum(1 for x in lanes_out if x["lane"] == "yellow"),
            "red": sum(1 for x in lanes_out if x["lane"] == "red"),
            "total": len(lanes_out),
        }
        return jsonify(
            {"ok": True, "lanes": lanes_out, "summary": summary, "source": "mysql"}
        )

    @exec_bp.route("/api/review/lane/confirm", methods=["POST"])
    def api_review_lane_confirm():
        """★ P0-4: 人工确认单条 lane 用例（yellow/red 必须经过此端点）。

        请求体: {case_id: str, action: "approve"|"reject"|"modify", comment?: str}
        通用性：与现有 approve/reject/edit 端点互补，但显式记录 lane 上下文。
        """
        ok, err = check_admin_or_permission("execution.run", project=_current_project())
        if not ok:
            return jsonify({"ok": False, "error": err}), (
                401 if err.startswith("请先登录") else 403
            )
        data = request.get_json(silent=True) or {}
        case_id = str(data.get("case_id", "")).strip()
        action = str(data.get("action", "approve")).strip()
        comment = str(data.get("comment", "") or "")[:512]
        if not case_id:
            return jsonify({"ok": False, "error": "缺少 case_id"}), 400
        if action not in ("approve", "reject", "modify"):
            return (
                jsonify({"ok": False, "error": "action 必须为 approve/reject/modify"}),
                400,
            )
        project = _current_project() or ""
        # 映射 action 到 review_status
        status_map = {"approve": "approved", "reject": "rejected", "modify": "modified"}
        new_status = status_map[action]
        # 查询 lane（用于日志上下文，不阻塞操作）
        version_id = _get_active_version_id(project)
        lane_info = None
        if version_id:
            with guard("api_review_lane_confirm: from src.pipeline.utils.review_lane_", log=_log, level=logging.WARNING):
                from src.pipeline.utils.review_lane_store import get_lane_for_case

                lane_info = get_lane_for_case(case_id, project, version_id)
        lane_label = (lane_info or {}).get("lane", "unknown")
        # 写入审核状态
        if not _update_case_review_status(case_id, project, new_status):
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": "审核状态写入失败",
                        "case_id": case_id,
                    }
                ),
                500,
            )
        _log_review(
            project,
            "test_case",
            case_id,
            action,
            comment=(
                f"[lane={lane_label}] {comment}" if comment else f"[lane={lane_label}]"
            ),
        )
        _log.info(
            "[P0-4] lane 确认: case_id=%s lane=%s action=%s",
            case_id,
            lane_label,
            action,
        )
        return jsonify(
            {
                "ok": True,
                "case_id": case_id,
                "lane": lane_label,
                "action": action,
                "new_status": new_status,
            }
        )

    @exec_bp.route("/api/cases/review/batch-approve", methods=["POST"])
    def api_intents_review_batch_approve():
        """★ P0-4 重构: 批量自动放行——仅放行 green lane，yellow/red 跳过送人审。

        闸门保护：review_lane 表无数据（Step2 未生成分流）时拒绝批量，返回 400
                 提示重跑 Step2 或逐条审核。禁止降级全批（原行为击穿质量闸门）。
        通用性：green(routed_to_human=0) 自动放行符合 P0-4；yellow/red 必须人审。
        """
        ok, err = check_admin_or_permission("execution.run", project=_current_project())
        if not ok:
            return jsonify({"ok": False, "error": err}), (
                401 if err.startswith("请先登录") else 403
            )
        project = _current_project() or ""
        version_id = _get_active_version_id(project)
        if version_id == 0:
            return jsonify({"ok": False, "error": "当前无激活版本，无法批量审核"}), 400
        lane_map = _load_lane_map(project, version_id)
        # ★ 闸门保护：无 lane 数据禁止批量（原降级全批击穿质量闸门，已删除）
        if not lane_map:
            _log.warning(
                "[P0-4] 批量审核拒绝: review_lane 无数据 (project=%s vid=%d)",
                project,
                version_id,
            )
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": "未启用三车道分流（review_lane 表无数据），禁止一键全过。请重跑 Step2 生成分流，或逐条审核用例",
                        "degraded": False,
                    }
                ),
                400,
            )
        intents, _source = _read_intents_for_review()
        approved = 0
        skipped_yellow = 0
        skipped_red = 0
        skipped_already = 0
        for c in intents:
            cid = str(c.get("case_id", "") or "").strip()
            if not cid:
                continue
            if c.get("_review_status") in ("approved", "modified"):
                skipped_already += 1
                continue
            lane_info = lane_map.get(cid, {"lane": "yellow"})
            lane = lane_info.get("lane", "yellow")
            if lane == "green":
                _update_case_review_status(cid, project, "approved")
                approved += 1
            elif lane == "red":
                skipped_red += 1
            else:  # yellow 或未知
                skipped_yellow += 1
        _log.info(
            "[P0-4] 批量放行: green=%d skipped(yellow=%d red=%d already=%d)",
            approved,
            skipped_yellow,
            skipped_red,
            skipped_already,
        )
        return jsonify(
            {
                "ok": True,
                "approved": approved,
                "skipped_yellow": skipped_yellow,
                "skipped_red": skipped_red,
                "already_approved": skipped_already,
                "degraded": False,
                "message": (
                    "green 已自动放行 "
                    + str(approved)
                    + " 条"
                    + (
                        "，yellow/red 共 "
                        + str(skipped_yellow + skipped_red)
                        + " 条需逐条人审"
                        if (skipped_yellow + skipped_red) > 0
                        else ""
                    )
                ),
            }
        )

    @exec_bp.route("/api/cases/review/confirm", methods=["POST"])
    def api_intents_review_confirm():
        """完成审核，关闭闸门，允许 Step4 继续。"""
        ok, err = check_admin_or_permission("execution.run", project=_current_project())
        if not ok:
            return jsonify({"ok": False, "error": err}), (
                401 if err.startswith("请先登录") else 403
            )
        project = _current_project() or ""
        intents, _source = _read_intents_for_review()
        total = len(intents)
        pending = sum(
            1 for c in intents if c.get("_review_status") in ("pending", None, "")
        )
        high_risk_pending = sum(
            1
            for c in intents
            if c.get("_risk_level") == "high"
            and c.get("_review_status") not in ("approved", "modified")
        )
        # 强制条件：高风险必须全确认
        if high_risk_pending > 0:
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": "还有 %d 条高风险测试用例未确认，必须先确认高风险测试用例"
                        % high_risk_pending,
                        "high_risk_pending": high_risk_pending,
                    }
                ),
                400,
            )
        # 建议条件：确认率 >=90%
        approval_rate = (total - pending) / max(total, 1) * 100
        if approval_rate < _APPROVAL_RATE_THRESHOLD and pending > 0:
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": "确认率 %.0f%% 不足 90%%，还有 %d 条待确认"
                        % (approval_rate, pending),
                        "pending": pending,
                        "approval_rate": round(approval_rate, 1),
                    }
                ),
                400,
            )
        # 解除暂停并恢复流水线
        from src.pipeline.utils.pause_controller import pause_controller

        # ★ A1 改造：按 project 读取/清除暂停状态
        _cur_proj = _current_project()
        paused_step = _get_paused_step(_cur_proj)
        _set_paused_step(0, _cur_proj)
        pause_controller.resume()
        # ★ 恢复流水线从暂停的步骤继续执行（Step3 完成→Step4）
        if paused_step > 0:
            with guard("api_intents_review_confirm: import uuid, threading, time as _tim", log=_log, level=logging.WARNING):
                import uuid, threading, time as _time
                from src.backend.routes.execution import (
                    _active_runs,
                    _active_runs_lock,
                    _make_session_runner,
                )
                from src.backend.executor._run_step import _run_step, StepRunOptions

                # ★ 通用修复(2026-07-20 v3.7): 审核通过恢复流水线时从下一步开始，禁用 checkpoint 恢复
                #   根因: 之前 resume=True 会从 checkpoint 恢复，但旧 run_id 的 checkpoint 残留
                #         (如 Step 3 success)会导致 scheduler.run() 跳过 Step 3，test_cases 永远为 0。
                #   通用性: 适用于所有审核恢复场景（需求审核→Step3，用例审核→Step4）。
                #   合理性: 审核通过意味着当前步骤已完成，应从 paused_step+1 开始，不依赖 checkpoint。
                #   覆盖性: 同步修复 execution/review.py 的需求审核恢复逻辑。
                resume_step = paused_step + 1
                if resume_step > 5:
                    _log.warning(
                        "[测试用例审核] paused_step=%d 已是最后一步，无需恢复",
                        paused_step,
                    )
                    _log.info(
                        "[测试用例审核] 审核完成: %d/%d 已确认 (%.1f%%)",
                        total - pending,
                        total,
                        approval_rate,
                    )
                    return jsonify(
                        {
                            "ok": True,
                            "total": total,
                            "pending": pending,
                            "approval_rate": round(approval_rate, 1),
                        }
                    )
                # ★ 清除该项目所有旧 checkpoint，避免残留导致跳过步骤
                with guard("api_intents_review_confirm: from src.core.db_manager import ( ge", log=_log, level=logging.WARNING):
                    from src.core.db_manager import (
                        get_connection as _gc,
                        release_connection as _rc,
                    )

                    _conn = _gc()
                    try:
                        _conn.execute(
                            "DELETE FROM pipeline_checkpoints WHERE project=%s",
                            (project,),
                        )
                        _conn.commit()
                        _log.info(
                            "[测试用例审核] 已清除项目 %s 的所有旧 checkpoint", project
                        )
                    finally:
                        _rc(_conn)
                run_id = uuid.uuid4().hex[:12]
                with _active_runs_lock:
                    _active_runs[run_id] = {
                        "thread": None,
                        "force_stop": False,
                        "step": resume_step,
                        "project": project,
                        "start_ts": _time.time(),
                    }
                opts = StepRunOptions(
                    step_num=resume_step,
                    run_id=run_id,
                    step4_options={},
                    end_step=5,
                    resume=False,  # ★ 禁用 checkpoint 恢复，避免跳过步骤
                )
                t = threading.Thread(
                    target=_make_session_runner(_run_step, opts), daemon=True
                )
                with _active_runs_lock:
                    _active_runs[run_id]["thread"] = t
                t.start()
                _log.info(
                    "[测试用例审核] 审核完成（%d/%d 已确认），从 Step %d 恢复执行 (run_id=%s)",
                    total - pending,
                    total,
                    resume_step,
                    run_id,
                )
        else:
            _log.info(
                "[测试用例审核] 审核完成: %d/%d 已确认 (%.1f%%)",
                total - pending,
                total,
                approval_rate,
            )
        return jsonify(
            {
                "ok": True,
                "total": total,
                "pending": pending,
                "approval_rate": round(approval_rate, 1),
            }
        )

    @exec_bp.route("/api/cases/review/state", methods=["GET"])
    def api_intents_review_state():
        """查询测试用例审核闸门状态。"""
        return jsonify(_get_case_review_state())

    @exec_bp.route("/api/cases/<case_id>", methods=["DELETE"])
    def api_intents_delete(case_id):
        """★ MySQL-only：删除测试用例资产 + 级联清理 MySQL 元数据 / 映射。
        不再操作 Excel。
        """
        ok, err = check_admin_or_permission("execution.run", project=_current_project())
        if not ok:
            return jsonify({"ok": False, "error": err}), (
                401 if err.startswith("请先登录") else 403
            )
        case_id = str(case_id).strip()
        if not case_id:
            return jsonify({"ok": False, "error": "缺少 case_id"}), 400
        project = _current_project() or ""
        from src.pipeline.utils.case_requirement_tracker import case_requirement_tracker
        from src.pipeline.utils.case_store import case_store
        from src.pipeline.utils.review_store import review_store

        # 先清 intent→req 映射
        with guard("api_intents_delete: case_requirement_tracker.remove_inte", log=_log, level=logging.WARNING):
            case_requirement_tracker.remove_intent(case_id)
        # 级联清理 case_meta
        with guard("api_intents_delete: review_store.delete_case_meta(case_i", log=_log, level=logging.WARNING):
            review_store.delete_case_meta(case_id)
        deleted = case_store.delete_intent(case_id)
        _log_review(project, "test_case", case_id, "delete")
        _log.info("[测试用例审核] 已删除(MySQL): %s", case_id)
        return jsonify({"ok": bool(deleted), "case_id": case_id})
