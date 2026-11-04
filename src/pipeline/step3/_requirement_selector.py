# -*- coding: utf-8 -*-
"""需求选择与状态标记（从 step3.py 拆分）"""

import logging
from src.core.guarded import guard
from typing import Any

_log = logging.getLogger(__name__)


def _read_step3_requirements(excel_handler):
    """Step3 需求读取：仅使用 MySQL requirements。"""
    try:
        from src.pipeline.utils.requirement_store import requirement_store

        reqs = requirement_store.list_requirements()
        _log.info("Step3需求读取: MySQL requirements (%d 条)", len(reqs))
        return reqs, "mysql"
    except Exception as e:
        _log.error("Step3 需求读取失败：MySQL requirements 不可用: %s", str(e)[:120])
        raise RuntimeError(
            "Step3 需求读取失败：MySQL requirements 不可用: %s" % str(e)[:120]
        )


def _select_requirements_for_case_modeling(
    all_reqs, intent_count_by_req, min_intents_per_req: int, force: bool = False
):
    """根据版本 change_type 选择真正需要生成测试用例的需求。

    - added / modified / impacted：必须进入生成队列
    - removed：不生成
    - unchanged：继承基线测试用例，已有足够测试用例则跳过
    - unknown/空：兼容旧逻辑，按覆盖数量判断
    """
    if force:
        return list(all_reqs or []), 0, {"forced": len(all_reqs or [])}
    ungen = []
    already_gen = 0
    stats = {
        "added": 0,
        "modified": 0,
        "impacted": 0,
        "unchanged": 0,
        "removed": 0,
        "unknown": 0,
    }
    for req in all_reqs or []:
        rid = str(req.get("req_id", "") or "")
        change_type = str(req.get("change_type", "") or "unknown").lower()
        if change_type in ("added", "modified", "impacted"):
            stats[change_type] += 1
            ungen.append(req)
        elif change_type == "removed":
            stats["removed"] += 1
            already_gen += 1
        elif change_type == "unchanged":
            stats["unchanged"] += 1
            already_gen += 1
            # ★ 零继承设计：unchanged 需求不建模、不在当前版本生成意图。
            # 需要执行时 Step4 跨版本按 stable_req_key 取老版本 valid 意图。
            # 不进入 ungen 队列（既不调 AI 也不生成 v4 意图）。
        else:
            stats["unknown"] += 1
            if intent_count_by_req.get(rid, 0) >= min_intents_per_req:
                already_gen += 1
            else:
                ungen.append(req)
    return ungen, already_gen, stats


def _mark_stale_intents_for_changed_requirements(reqs) -> None:
    """跨版本条目级 stale 标记：modified 需求的旧版本对应意图标为 stale。

    调用时机：建模完成后（新意图已写入 test_cases）。
    精准到意图条目级——只标被新版本取代的具体意图（同 case_type），
    不动该需求其他无关意图。

    例：v3 REQ-001 有 3 条意图（api_check / db_check / ui_check），
    v4 只重生了 api_check + db_check → 只标 v3 的 api_check + db_check 为 stale，
    v3 的 ui_check 保持 valid（v4 没取代它）。
    """
    # 收集 modified 需求及其 origin 信息
    modified_reqs = []
    for r in reqs or []:
        if str(r.get("change_type", "") or "").lower() not in ("modified", "impacted"):
            continue
        origin_ver = r.get("origin_version_id")
        origin_rid = str(r.get("origin_req_id", "") or "").strip()
        new_rid = str(r.get("req_id", "") or "").strip()
        if not origin_ver or not origin_rid or not new_rid:
            continue
        modified_reqs.append((int(origin_ver), origin_rid, new_rid))

    if not modified_reqs:
        return

    with guard("_mark_stale_intents_for_changed_requirements: from src.core.config_loader import c", log=_log, level=logging.WARNING):
        from src.core.config_loader import config
        from src.core.versioning import current_version_id
        from src.core.db_manager import get_connection

        project = getattr(config, "project_name", "") or ""
        version_id = current_version_id(project)
        c = get_connection()
        total_stale = 0
        try:
            for origin_ver, origin_rid, new_rid in modified_reqs:
                # 1. 查当前版本新生成的意图类型集合
                new_types_rows = c.execute(
                    "SELECT DISTINCT case_type FROM test_cases "
                    "WHERE project=%s AND version_id=%s AND req_id=%s AND valid_status<>'retired'",
                    (project, version_id, new_rid),
                ).fetchall()
                new_types = {
                    str(r["case_type"] or "").strip()
                    for r in (new_types_rows or [])
                    if r.get("case_type")
                }
                if not new_types:
                    continue

                # 2. 逐类型标旧版本对应意图为 stale（条目级精准匹配）
                for itype in new_types:
                    affected = c.execute(
                        "UPDATE test_cases SET valid_status='stale', updated_at=NOW() "
                        "WHERE project=%s AND version_id=%s AND req_id=%s AND case_type=%s "
                        "AND valid_status='valid'",
                        (project, origin_ver, origin_rid, itype),
                    ).rowcount
                    total_stale += affected or 0

            c.commit()
        finally:
            try:
                c.close()
            except Exception as _e:
                _log.debug("[_requirement_selector] 非关键异常: %s", _e, exc_info=True)
        if total_stale:
            _log.info(
                "已跨版本标 stale: %d 条旧版本意图 (%d 个 modified 需求)",
                total_stale,
                len(modified_reqs),
            )


def _mark_modeled_intents_for_requirement(req: dict[str, Any]) -> None:
    """生成完成后给当前版本测试用例补充来源状态。"""
    rid = str(req.get("req_id", "") or "").strip()
    if not rid:
        return
    change_type = str(req.get("change_type", "") or "unknown").lower()
    case_source = (
        "regenerated" if change_type in ("modified", "impacted") else "generated"
    )
    with guard("_mark_modeled_intents_for_requirement: from src.core.config_loader import c", log=_log, level=logging.WARNING):
        from src.core.config_loader import config
        from src.core.versioning import current_version_id
        from src.core.db_manager import get_connection

        project = getattr(config, "project_name", "") or ""
        version_id = current_version_id(project)
        c = get_connection()
        try:
            c.execute(
                "UPDATE test_cases SET case_source=%s, valid_status='valid', updated_at=NOW() "
                "WHERE project=%s AND version_id=%s AND req_id=%s AND valid_status <> 'retired' "
                "AND NOT (case_source='inherited' AND valid_status='stale')",
                (case_source, project, version_id, rid),
            )
            c.commit()
        finally:
            try:
                c.close()
            except Exception as _e:
                _log.debug(
                    "[_requirement_selector] _mark_modeled_intents_for_requirement 异常: %s",
                    _e,
                    exc_info=True,
                )


def _load_previous_intents(
    req: dict[str, Any], max_count: int = 3
) -> list[dict[str, Any]]:
    """★ 优化6: 读取旧版本同 stable_req_key 的测试用例（供 few-shot 注入）。

    仅对 modified/impacted 需求有意义。读取旧版本的 valid 用例，
    限制 max_count 条以控制 token。只返回 title + case_type + actions，
    不返回 checks（避免 AI 复制旧 check 导致新需求覆盖不足）。

    读取失败时返回空列表（降级为完全重生，不阻塞）。
    """
    stable_key = str(req.get("stable_req_key", "") or "").strip()
    if not stable_key:
        return []
    origin_ver = req.get("origin_version_id")
    if not origin_ver:
        return []
    try:
        from src.core.config_loader import config
        from src.core.db_manager import get_connection

        project = getattr(config, "project_name", "") or ""
        c = get_connection()
        try:
            rows = c.execute(
                "SELECT title, case_type, case_actions_json, risk_level "
                "FROM test_cases "
                "WHERE project=%s AND version_id=%s AND req_id=%s "
                "AND valid_status='valid' AND case_actions_json IS NOT NULL "
                "ORDER BY risk_level, created_at LIMIT %s",
                (project, origin_ver, req.get("origin_req_id", ""), max_count),
            ).fetchall()
            result = []
            for r in rows or []:
                item = (
                    dict(r)
                    if hasattr(r, "keys")
                    else {
                        "title": r[0],
                        "case_type": r[1],
                        "case_actions_json": r[2],
                        "risk_level": r[3],
                    }
                )
                result.append(
                    {
                        "title": str(item.get("title", "") or ""),
                        "case_type": str(item.get("case_type", "") or ""),
                        "actions": str(item.get("case_actions_json", "") or ""),
                        "risk_level": str(item.get("risk_level", "") or ""),
                    }
                )
            if result:
                _log.info(
                    "[优化6] [%s] 读取旧版本 %d 条用例作为 few-shot",
                    req.get("req_id", ""),
                    len(result),
                )
            return result
        finally:
            try:
                c.close()
            except Exception as _e:
                _log.debug("conn.close 失败: %s", _e, exc_info=True)
    except Exception as e:
        _log.debug(
            "[优化6] 读取旧版本用例失败 [%s]: %s", req.get("req_id", ""), str(e)[:80]
        )
        return []
