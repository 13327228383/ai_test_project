# -*- coding: utf-8 -*-
"""Step 2 版本管理工具函数。
从 step2.py 拆分出。"""

import json as _json
from src.core.guarded import guard
import logging
import hashlib
from typing import Any

_log = logging.getLogger(__name__)

# ── 从 step2_hashes 导入哈希函数 ──
from src.pipeline.step2_hashes import (
    compute_requirement_semantic_hash,
    compute_requirement_structural_hash,
    _normalize_requirement_part,
)


def _reset_downstream_for_requirement_change(project: str, reason: str = "") -> None:
    """需求源变化后，清理派生态，避免旧测试用例/旧执行状态造成覆盖和执行假阳性。

    派生态包括：
    - test_cases / case_meta
    - case_requirement_mapping
    - coverage_results / coverage_summaries
    - executions（旧 case_id 的执行状态不能映射到新需求版本）
    """
    with guard("_reset_downstream_for_requirement_change: from src.pipeline.utils.case_store i", log=_log, level=logging.WARNING):
        from src.pipeline.utils.case_store import case_store
        from src.pipeline.utils.case_requirement_tracker import case_requirement_tracker
        from src.pipeline.utils.coverage_store import coverage_store

        case_store.clear_project_intents()
        case_requirement_tracker.clear()
        coverage_store.clear_project_coverage()
        try:
            from src.core.db_manager import get_connection

            c = get_connection()
            try:
                from src.core.versioning import current_version_id

                active_version_id = current_version_id(project)
            except Exception as _e:
                _log.debug(
                    "[step2_versioning] _reset_downstream_for_requirement_change 异常: %s",
                    _e,
                    exc_info=True,
                )
                active_version_id = 0
            c.execute(
                "DELETE FROM executions WHERE project=%s AND version_id=%s",
                (project, active_version_id),
            )
            c.commit()
            try:
                c.close()
            except Exception as _e:
                _log.debug(
                    "[step2_versioning] _reset_downstream_for_requirement_change 异常: %s",
                    _e,
                    exc_info=True,
                )
        except Exception as e:
            _log.warning("[Step2] executions 派生态清理失败: %s", str(e)[:120])
        _log.warning(
            "[Step2] 需求源变化，已清理派生态: project=%s reason=%s",
            project,
            reason or "source_changed",
        )


def _get_previous_version_id(project: str, active_version_id: int) -> int:
    """获取 active 版本之前最近的一个版本 ID；没有则返回 0。"""
    try:
        from src.users.user_db import get_project_versions

        versions = get_project_versions(project)
        ordered = sorted(
            [
                v
                for v in versions
                if int(v.get("id") or 0) != int(active_version_id or 0)
            ],
            key=lambda x: str(x.get("created_at") or ""),
            reverse=True,
        )
        return int(ordered[0].get("id") or 0) if ordered else 0
    except Exception as _e:
        _log.debug(
            "[step2_versioning] _get_previous_version_id 异常: %s", _e, exc_info=True
        )
        return 0


def _load_requirements_by_version(project: str, version_id: int):
    """按版本读取 requirements 快照。"""
    # ★ P0-1 批C修复(2026-07-25): version_id=0 是合法 legacy 值，不能用 `not version_id`
    if not project or version_id is None or version_id < 0:
        return []
    try:
        from src.core.db_manager import get_connection

        c = get_connection()
        try:
            rows = c.execute(
                "SELECT * FROM requirements WHERE project=%s AND version_id=%s ORDER BY req_id",
                (project, version_id),
            ).fetchall()
            result = []
            for row in rows or []:
                item = dict(row)
                try:
                    payload = _json.loads(item.get("payload_json") or "{}")
                except Exception as _e:
                    _log.debug(
                        "[step2_versioning] _load_requirements_by_version 异常: %s",
                        _e,
                        exc_info=True,
                    )
                    payload = {}
                req = dict(payload) if isinstance(payload, dict) else {}
                req.update(
                    {
                        k: item.get(k)
                        for k in item.keys()
                        if item.get(k) not in (None, "")
                    }
                )
                result.append(req)
            return result
        finally:
            try:
                c.close()
            except Exception as _e:
                _log.debug(
                    "[step2_versioning] _load_requirements_by_version 异常: %s",
                    _e,
                    exc_info=True,
                )
    except Exception as e:
        _log.warning("[Step2] 读取历史版本需求失败: %s", str(e)[:120])
        return []


def _req_content_hash(req: dict[str, Any]) -> str:
    """为单条需求生成稳定内容哈希。"""
    text = "%s|%s|%s" % (
        _normalize_requirement_part(req.get("module", "")),
        _normalize_requirement_part(req.get("description", "")),
        _normalize_requirement_part(req.get("dependency_req_id", "")),
    )
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _unique_value_map(rows: list, key: str) -> dict[str, dict[str, Any]]:
    """只保留唯一值映射；重复值不用于自动匹配，避免误连。"""
    result: dict[str, dict[str, Any]] = {}
    duplicated = set()
    for row in rows or []:
        value = str(row.get(key, "") or "").strip()
        if not value:
            continue
        if value in result:
            duplicated.add(value)
        else:
            result[value] = row
    for value in duplicated:
        result.pop(value, None)
    return result


def _compute_keyword_fingerprint(description: str) -> str:
    """从需求描述中提取核心词指纹（跨版本稳定的键组成部分）。

    策略：去格式/数字/标点后，提取 CJK 字符 bigram 作为「关键词」，
    排序去重后取 SHA256 前 12 位。同一需求不同 AI 轮次的描述措辞
    可能有差异，但核心业务词汇（bigram）高度稳定。
    """
    import re as _re

    text = str(description or "").lower()
    # 去 Markdown
    text = _re.sub(r"```.*?```", " ", text, flags=_re.S)
    text = _re.sub(r"`([^`]*)`", r"\1", text)
    # 去标点/空白
    text = _re.sub(r"[#>*_\-|:：,，.。;；!！?？()\[\]{}<>《》\"'/\\\s]+", "", text)
    # 去数字（数字是业务参数，不参与稳定键）
    text = _re.sub(r"\d+", "", text)
    # 提取 CJK bigram 作为「关键词」
    bigrams = set()
    for i in range(len(text) - 1):
        bg = text[i : i + 2]
        if all("一" <= c <= "鿿" for c in bg):
            bigrams.add(bg)
    if not bigrams:
        # 退化：无 CJK 字符时用前 30 个清洗后字符
        fallback = text[:30].strip()
        return hashlib.sha256(fallback.encode("utf-8")).hexdigest()[:12]
    sorted_bigrams = sorted(bigrams)
    return hashlib.sha256("|".join(sorted_bigrams).encode("utf-8")).hexdigest()[:12]


def _compute_stable_req_key(module: str, description: str) -> str:
    """生成跨版本稳定的需求键：{module}::{关键词指纹}。

    替代旧的 req_id 兜底策略——req_id 是 AI 每版重新编号的序号，
    v3 的 REQ-012 和 v4 的 REQ-012 毫无关系。
    """
    module_clean = str(module or "").strip()
    fingerprint = _compute_keyword_fingerprint(description)
    return f"{module_clean}::{fingerprint}"


def _prepare_requirement_changes(
    project: str, requirements: list, force_step2: bool = False
) -> dict[str, Any]:
    """给当前版本需求标注 change_type，并在有历史版本时先复制 V1 基线。

    ★ force_step2=True: 强制重拆解，所有匹配旧版本的需求标为 modified（不标 unchanged），
      确保 Step3 会重新生成测试用例。
    """
    try:
        from src.core.versioning import current_version_id

        active_version_id = current_version_id(project)
    except Exception as _e:
        _log.debug(
            "[step2_versioning] _prepare_requirement_changes 异常: %s",
            _e,
            exc_info=True,
        )
        active_version_id = 0
    if not active_version_id:
        for req in requirements:
            if isinstance(req, dict):
                module_name = str(req.get("module", "") or "")
                description = str(req.get("description", "") or "")
                req["stable_req_key"] = _compute_stable_req_key(
                    module_name, description
                )
                req.setdefault("change_type", "added")
                req.setdefault("version_id", 0)
                req.setdefault("content_hash", _req_content_hash(req))
                req.setdefault(
                    "semantic_hash", compute_requirement_semantic_hash(description)
                )
                req["structural_hash"] = compute_requirement_structural_hash(
                    description
                )
                req.setdefault("source_anchor", module_name)
        return {
            "versioned": False,
            "active_version_id": 0,
            "previous_version_id": 0,
            "removed_ids": [],
            "removed_stable_keys": [],
        }

    previous_version_id = _get_previous_version_id(project, active_version_id)
    old_reqs = (
        _load_requirements_by_version(project, previous_version_id)
        if previous_version_id
        else []
    )
    old_map = {str(r.get("req_id", "") or ""): r for r in old_reqs if r.get("req_id")}
    old_stable_map = _unique_value_map(old_reqs, "stable_req_key")
    old_anchor_map = _unique_value_map(old_reqs, "source_anchor")
    old_content_map = _unique_value_map(old_reqs, "content_hash")
    old_semantic_map = _unique_value_map(old_reqs, "semantic_hash")
    # ★ 骨架层匹配：对旧需求实时计算 structural_hash（数字归一），用于跨版本同实体识别
    old_structural_map: dict[str, dict[str, Any]] = {}
    _old_structural_dups: set = set()
    for r in old_reqs:
        sh = compute_requirement_structural_hash(str(r.get("description", "") or ""))
        if sh in old_structural_map:
            _old_structural_dups.add(sh)
        else:
            old_structural_map[sh] = r
    for sh in _old_structural_dups:
        old_structural_map.pop(sh, None)

    if previous_version_id:
        try:
            from src.core.version_baseline import clone_version_baseline

            clone_counts = clone_version_baseline(
                project, previous_version_id, active_version_id
            )
            _log.info(
                "[Step2] 已复制历史版本基线: from=%s to=%s counts=%s",
                previous_version_id,
                active_version_id,
                clone_counts,
            )
        except Exception as e:
            _log.warning(
                "[Step2] 历史版本基线复制失败，继续按变更需求写入: %s", str(e)[:120]
            )

    matched_old_ids = set()
    for req in requirements:
        if not isinstance(req, dict):
            continue
        rid = str(req.get("req_id", "") or "").strip()
        module_name = str(req.get("module", "") or "")
        description = str(req.get("description", "") or "")

        req["version_id"] = active_version_id
        # ★ A1: stable_req_key 强制赋值为 {module}::{关键词指纹}，不再兜底为 req_id
        req["stable_req_key"] = _compute_stable_req_key(module_name, description)
        req.setdefault("source_anchor", module_name)
        req["content_hash"] = _req_content_hash(req)
        req["semantic_hash"] = compute_requirement_semantic_hash(description)
        # ★ A3: 新增骨架层哈希（数字归一），用于跨版本同实体匹配
        req["structural_hash"] = compute_requirement_structural_hash(description)

        stable_key = str(req.get("stable_req_key", "") or "").strip()
        source_anchor = str(req.get("source_anchor", "") or "").strip()
        # ★ A2: 匹配优先级修正 — stable → structural → semantic → anchor → content → req_id（最后兜底）
        # 只有 stable / structural / semantic 命中才算「同实体」；content/req_id 命中降级为「疑似」
        old = (
            (old_stable_map.get(stable_key) if stable_key else None)
            or (
                old_structural_map.get(req["structural_hash"])
                if req.get("structural_hash")
                else None
            )
            or (
                old_semantic_map.get(req["semantic_hash"])
                if req.get("semantic_hash")
                else None
            )
            or (old_anchor_map.get(source_anchor) if source_anchor else None)
            or old_content_map.get(req["content_hash"])
            or old_map.get(rid)
        )
        if not old:
            req["change_type"] = "added"
            req["is_generated"] = False
            req["origin_req_id"] = ""
            req["origin_version_id"] = 0
        else:
            old_req_id = str(old.get("req_id", "") or rid)
            if old_req_id:
                matched_old_ids.add(old_req_id)
            # ★ A3: 双层哈希判定变更（骨架层 + 实质层）
            old_structural = compute_requirement_structural_hash(
                str(old.get("description", "") or "")
            )
            new_structural = str(req.get("structural_hash", "") or "")
            old_content = str(old.get("content_hash", "") or "") or _req_content_hash(
                old
            )
            new_content = str(req.get("content_hash", "") or "")
            if old_structural == new_structural and old_content == new_content:
                # 骨架同 + 实质同 → 真正没变，无需建模
                # ★ force_step2=True 时强制标为 modified，确保 Step3 重新生成用例
                if force_step2:
                    req["change_type"] = "modified"
                    req["is_generated"] = False
                    _log.debug(
                        "[step2_versioning] force_step2: 需求 %s 强制标为 modified（原 unchanged）",
                        rid,
                    )
                else:
                    req["change_type"] = "unchanged"
                    req["is_generated"] = True  # ★ unchanged 无需建模，始终标为已生成
            else:
                # 骨架同+实质不同（如 11→8 类）或骨架不同（重写） → modified
                req["change_type"] = "modified"
                req["is_generated"] = False
            req["origin_req_id"] = old_req_id
            req["origin_version_id"] = previous_version_id

    removed_ids = sorted(set(old_map.keys()) - matched_old_ids)
    # 收集被移除需求的 stable_req_key，供 RAG chunk 失效使用
    removed_stable_keys = []
    for rid in removed_ids:
        old_req = old_map.get(rid)
        if old_req:
            sk = str(old_req.get("stable_req_key", "") or "").strip()
            if sk:
                removed_stable_keys.append(sk)
    _log.info(
        "[Step2] 需求变更标注完成: active=%s previous=%s added=%d modified=%d unchanged=%d removed=%d",
        active_version_id,
        previous_version_id,
        sum(1 for r in requirements if r.get("change_type") == "added"),
        sum(1 for r in requirements if r.get("change_type") == "modified"),
        sum(1 for r in requirements if r.get("change_type") == "unchanged"),
        len(removed_ids),
    )
    return {
        "versioned": True,
        "active_version_id": active_version_id,
        "previous_version_id": previous_version_id,
        "removed_ids": removed_ids,
        "removed_stable_keys": removed_stable_keys,
    }


def _mark_removed_requirements(
    project: str,
    active_version_id: int,
    removed_ids: list,
    removed_stable_keys: list = None,
) -> None:
    """把 V2 中从 V1 继承但新版本已不存在的需求标记为 removed，并 deactivate 对应 RAG chunk。"""
    if not project or not active_version_id or not removed_ids:
        return
    with guard("_mark_removed_requirements: from src.core.db_manager import get_", log=_log, level=logging.WARNING):
        from src.core.db_manager import get_connection

        c = get_connection()
        try:
            for rid in removed_ids:
                c.execute(
                    "UPDATE requirements SET change_type='removed', is_generated=0, updated_at=NOW() "
                    "WHERE project=%s AND version_id=%s AND req_id=%s",
                    (project, active_version_id, rid),
                )
            # ★ 同步失效 RAG chunk：需求已移除，其 RAG 知识也应标记 is_active=0
            if removed_stable_keys:
                try:
                    from src.core.config_loader import config as _cfg

                    version_label = str(
                        getattr(_cfg, "project_version", "") or ""
                    ).strip()
                except Exception as _e:
                    _log.debug(
                        "[step2_versioning] _mark_removed_requirements 异常: %s",
                        _e,
                        exc_info=True,
                    )
                    version_label = ""
                for sk in removed_stable_keys:
                    chunk_id = f"req:{sk}"
                    c.execute(
                        "UPDATE rag_chunks SET is_active=0, deactivated_version_label=%s, updated_at=NOW() "
                        "WHERE scope_type='project' AND scope_id=%s AND chunk_id=%s AND is_active=1",
                        (version_label, project, chunk_id),
                    )
            c.commit()
        finally:
            try:
                c.close()
            except Exception as _e:
                _log.debug("[step2_versioning] 非关键异常: %s", _e, exc_info=True)


_DELETION_SIGNAL_KEYWORDS = (
    "删除",
    "废弃",
    "移除",
    "下线",
    "不再使用",
    "已停用",
    "deprecated",
    "removed",
    "obsolete",
)


def _detect_deletion_signals(project: str, requirements: list = None) -> int:
    """扫描需求描述中的删除信号，匹配后标 RAG rag_chunks is_active=0。

    规则（从严，E3）：必须同时满足：
    1. 需求描述含删除语义词
    2. 同 module 下能匹配到 stable_req_key 对应的 chunk

    返回：被标失效的 chunk 数。
    """
    if not project or not requirements:
        return 0

    # 收集含删除信号的需求（module + stable_req_key）
    candidates = []
    for req in requirements or []:
        if not isinstance(req, dict):
            continue
        desc = str(req.get("description", "") or "")
        if not any(kw in desc for kw in _DELETION_SIGNAL_KEYWORDS):
            continue
        module_name = str(req.get("module", "") or "").strip()
        stable_key = str(req.get("stable_req_key", "") or "").strip()
        if not module_name or not stable_key:
            continue
        candidates.append(stable_key)

    if not candidates:
        return 0

    # 取当前版本标签用于审计
    try:
        from src.core.config_loader import config as _cfg

        version_label = str(getattr(_cfg, "project_version", "") or "").strip()
    except Exception as _e:
        _log.debug("[step2_versioning] 非关键异常: %s", _e, exc_info=True)
        version_label = ""

    total = 0
    conn = None
    try:
        from src.core.db_manager import get_connection

        conn = get_connection()
        for stable_key in candidates:
            chunk_id = f"req:{stable_key}"
            affected = conn.execute(
                "UPDATE rag_chunks SET is_active=0, "
                "deactivated_version_label=%s, updated_at=NOW() "
                "WHERE scope_type='project' AND scope_id=%s AND chunk_id=%s AND is_active=1",
                (version_label, project, chunk_id),
            ).rowcount
            total += affected or 0
        conn.commit()
        if total:
            _log.info(
                "[Step2] 删除信号识别: %d 个 RAG chunk 已标失效 (version=%s)",
                total,
                version_label or "latest",
            )
    except Exception as e:
        _log.warning("[Step2] 删除信号 RAG 标记失败: %s", str(e)[:120])
    finally:
        if conn:
            try:
                conn.close()
            except Exception as _e:
                _log.debug("[step2_versioning] 非关键异常: %s", _e, exc_info=True)
    return total
