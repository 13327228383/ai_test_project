# -*- coding: utf-8 -*-
from __future__ import annotations
from src.core.guarded import guard

"""
Step4 业务能力缓存（包入口）
============================
能力缓存不是测试脚本库，而是"业务能力 + 适用条件 + 成功证据 + 历史实现"的沉淀。
测试资产仍然以 intent_schema 为核心，implementation 只是该能力的一种运行时成功经验。

【2026-06-18 重构】原 514 行单文件已按职责拆为：
  keys.py     — 4 个 key/签名工具函数 (normalize_intent / make_capability_key / make_page_signature 等)
  schema.py   — _CapabilityCacheSchemaMixin（建表 + 字段补齐 + COMMENT 修正）
  events.py   — _CapabilityCacheEventsMixin（事件写入 + 项目策略 + 容量裁剪）
  __init__.py — CapabilityCache facade（多继承 mixin） + 单例 capability_cache

外部接口 100% 兼容：模块级 capability_cache 单例 + 工具函数 re-export。
"""

import json
import logging
from typing import Any

# ── re-export 工具函数（保持调用方向后兼容） ──
from .keys import (
    _now_sql,
    normalize_intent,
    _project_name,
    _active_version_id,
    _capability_name,
    make_capability_key,
    make_page_signature,
    _normalize_url_path,
)
from .schema import _CapabilityCacheSchemaMixin
from .events import _CapabilityCacheEventsMixin

_log = logging.getLogger(__name__)

# ★ 存量库运行时 ALTER 补列（幂等，参考 browser_evidence.ensure_screenshot_table 模式）
_CAPABILITY_VERSION_COLUMN_ENSURED = False


def _ensure_version_columns():
    """确保 capability_cache / capability_cache_events 含 version_id 列（幂等；缺失才 ALTER）。"""
    global _CAPABILITY_VERSION_COLUMN_ENSURED
    if _CAPABILITY_VERSION_COLUMN_ENSURED:
        return
    with guard("_ensure_version_columns: from src.core.db_manager import get_", log=_log, level=logging.WARNING):
        from src.core.db_manager import get_connection

        conn = None
        try:
            conn = get_connection()
            _altered = False
            for _tbl in ("capability_cache", "capability_cache_events"):
                _rows = conn.execute(
                    "SELECT COLUMN_NAME FROM information_schema.columns "
                    "WHERE table_schema = DATABASE() AND table_name = %s",
                    (_tbl,),
                ).fetchall()
                _cols = {r["COLUMN_NAME"] for r in (_rows or [])}
                if "version_id" not in _cols:
                    conn.execute(
                        "ALTER TABLE `%s` ADD COLUMN version_id BIGINT NOT NULL DEFAULT 0"
                        % _tbl
                    )
                    _altered = True
            if _altered:
                conn.commit()
            _CAPABILITY_VERSION_COLUMN_ENSURED = True
        finally:
            if conn:
                with guard("_ensure_version_columns: conn.close()", log=_log):
                    conn.close()


class CapabilityCache(_CapabilityCacheSchemaMixin, _CapabilityCacheEventsMixin):
    _TABLE = "capability_cache"
    _MAX_PER_PROJECT = 1000
    _EVENT_RETENTION_DAYS = 3

    def __init__(self):
        self._ensure_table()
        _ensure_version_columns()

    def _conn(self):
        from src.core.db_manager import get_connection

        return get_connection()

    def record_success(
        self,
        intent_schema: dict[str, Any],
        verdict: dict[str, Any],
        implementation: dict[str, Any] | None = None,
        confidence: float | None = None,
    ) -> str:
        project = _project_name()
        vid = _active_version_id(project)
        key = make_capability_key(project, intent_schema)
        capability = _capability_name(intent_schema)
        intent = normalize_intent(str((intent_schema or {}).get("title") or ""))
        # v9: preconditions/test_data_constraints 已移除
        evidence_items = (verdict or {}).get("evidence_items") or []
        impl = implementation or {
            "type": "runtime_success_experience",
            "source": "step4_intent_executor",
            "note": "具体动作是运行时产物，不反向写入测试资产。",
        }
        context = {
            "page_url": impl.get("page_url", "") if isinstance(impl, dict) else "",
            "page_title": impl.get("page_title", "") if isinstance(impl, dict) else "",
        }
        page_signature_hash = make_page_signature(intent_schema, context)
        conn = None
        try:
            conn = self._conn()
            conn.execute(
                """
                INSERT INTO capability_cache(
                    capability_key, project, version_id, capability_name, case_normalized,
                    preconditions_json, inputs_schema_json, implementation_json,
                    success_evidence_json, success_count, failure_count, hit_count,
                    last_status, confidence, is_valid, page_signature_hash,
                    invalidation_reason, invalidated_at, last_verified_at,
                    created_at, updated_at
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,1,0,0,'PASS',%s,1,%s,NULL,NULL,NOW(),NOW(),NOW())
                ON DUPLICATE KEY UPDATE
                    preconditions_json=VALUES(preconditions_json),
                    inputs_schema_json=VALUES(inputs_schema_json),
                    implementation_json=VALUES(implementation_json),
                    success_evidence_json=VALUES(success_evidence_json),
                    success_count=success_count+1,
                    last_status='PASS',
                    confidence=VALUES(confidence),
                    is_valid=1,
                    page_signature_hash=VALUES(page_signature_hash),
                    invalidation_reason=NULL,
                    invalidated_at=NULL,
                    last_verified_at=NOW(),
                    updated_at=NOW()
                """,
                (
                    key,
                    project,
                    vid,
                    capability,
                    intent,
                    "[]",
                    "{}",
                    json.dumps(impl, ensure_ascii=False, default=str),
                    json.dumps(evidence_items, ensure_ascii=False, default=str),
                    confidence,
                    page_signature_hash,
                ),
            )
            self._record_event(
                conn,
                project,
                key,
                capability,
                "PASS",
                "能力成功沉淀",
                old_signature_hash="",
                new_signature_hash=page_signature_hash,
                context={"intent": intent, **context},
            )
            self._ensure_policy(conn, project)
            self._prune_project(conn, project)
            conn.commit()
            _log.info("[CapabilityCache] ✓ 成功沉淀能力 %s/%s", capability, key[:10])
            return key
        finally:
            if conn:
                try:
                    conn.close()
                except Exception as _pass_e:
                    _log.warning("__init__.py:119 异常被吞: %s", _pass_e, exc_info=True)

    def get(
        self,
        intent_schema: dict[str, Any],
        current_context: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        project = _project_name()
        vid = _active_version_id(project)
        key = make_capability_key(project, intent_schema)
        capability = _capability_name(intent_schema)
        conn = None
        try:
            conn = self._conn()
            row = conn.execute(
                """
                SELECT * FROM capability_cache
                WHERE capability_key=%s AND project=%s AND success_count>0 AND is_valid=1
                  AND (version_id=%s OR version_id=0)
                """,
                (key, project, vid),
            ).fetchone()
            # ════════════════════════════════════════════════════════
            # ★ 精确匹配失败 → 走 fuzzy_match_engine 做模糊召回
            # ════════════════════════════════════════════════════════
            if not row:
                with guard("get: from src.pipeline.steps.step4.plugin", log=_log, level=logging.WARNING):
                    from src.pipeline.steps.step4.plugins.fuzzy_match_engine import (
                        fuzzy_match_engine,
                    )

                    intent_text = str((intent_schema or {}).get("title") or "")
                    ctx = current_context or {}
                    page_url = ctx.get("page_url", "") if isinstance(ctx, dict) else ""
                    page_sig = (
                        ctx.get("page_signature_hash", "")
                        if isinstance(ctx, dict)
                        else ""
                    )
                    fuzzy_candidates = fuzzy_match_engine.fuzzy_match(
                        current_intent_text=intent_text,
                        current_page_url=page_url,
                        current_page_signature_hash=page_sig,
                        threshold=0.55,
                        top_k=3,
                    )
                    if fuzzy_candidates:
                        best = fuzzy_candidates[0]
                        best_key = best.get("key")
                        best_name = best.get("name", "")
                        best_score = best.get("score", 0)
                        # 尝试用 fuzzy 命中的 key 再查一次主表
                        row2 = conn.execute(
                            "SELECT * FROM capability_cache "
                            "WHERE capability_key=%s AND project=%s AND success_count>0 AND is_valid=1 "
                            "AND (version_id=%s OR version_id=0)",
                            (best_key, project, vid),
                        ).fetchone()
                        if row2:
                            _log.info(
                                "[CapabilityCache] ★ 模糊命中 capability=%s score=%.2f key=%s",
                                best_name,
                                best_score,
                                str(best_key)[:16],
                            )
                            row = row2
                        else:
                            _log.info(
                                "[CapabilityCache] 模糊候选 key=%s 在主表未找到有效条目，跳过",
                                str(best_key)[:16],
                            )
            if not row:
                return None
            new_signature = make_page_signature(intent_schema, current_context or {})
            old_signature = str(row.get("page_signature_hash") or "")
            if old_signature and new_signature and old_signature != new_signature:
                reason = "页面签名变化，停止复用旧能力"
                conn.execute(
                    """
                    UPDATE capability_cache
                    SET is_valid=0, last_status='INVALID', invalidation_reason=%s,
                        invalidated_at=NOW(), last_verified_at=NOW(), updated_at=NOW()
                    WHERE capability_key=%s AND project=%s
                    """,
                    (reason, key, project),
                )
                self._record_event(
                    conn,
                    project,
                    key,
                    capability,
                    "INVALIDATE",
                    reason,
                    old_signature_hash=old_signature,
                    new_signature_hash=new_signature,
                    context=current_context or {},
                )
                self._ensure_policy(conn, project)
                self._prune_project(conn, project)
                conn.commit()
                return None
            conn.execute(
                """
                UPDATE capability_cache
                SET hit_count=hit_count+1, last_hit_at=NOW(), last_verified_at=NOW()
                WHERE capability_key=%s AND project=%s
                """,
                (key, project),
            )
            self._record_event(
                conn,
                project,
                key,
                str(row.get("capability_name") or capability),
                "HIT",
                "能力缓存命中",
                old_signature_hash=old_signature,
                new_signature_hash=new_signature,
                context=current_context or {},
            )
            self._ensure_policy(conn, project)
            self._prune_project(conn, project)
            conn.commit()
            for field in (
                "preconditions_json",
                "inputs_schema_json",
                "implementation_json",
                "success_evidence_json",
            ):
                try:
                    row[field.replace("_json", "")] = json.loads(
                        row.get(field) or "null"
                    )
                except Exception:
                    row[field.replace("_json", "")] = None
            return row
        finally:
            if conn:
                try:
                    conn.close()
                except Exception as _pass_e:
                    _log.warning("__init__.py:260 异常被吞: %s", _pass_e, exc_info=True)


capability_cache = CapabilityCache()
