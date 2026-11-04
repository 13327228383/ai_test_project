# -*- coding: utf-8 -*-
from __future__ import annotations

"""
OperationCache 核心缓存操作 Mixin
==================================
缓存读取（get）、写入（set）、成功记录（record_success/commit_pending）。
"""

import json
import time
import logging

_log = logging.getLogger(__name__)


class _OpCacheOpsMixin:
    """OperationCache 核心缓存操作。"""

    def get(
        self,
        goal: str,
        page_url: str,
        compact_state: list[dict],
        config_hash: str = None,
    ) -> list[dict] | None:
        """
        查询缓存，返回操作指令列表或 None。
        命中时自动更新 hit_count 和 last_hit_at。
        """
        from src.pipeline.steps.step4.op_cache import (
            compute_config_hash,
            _get_project_name,
            _active_version_id,
            resolve_ai_ids,
        )

        config_hash = config_hash or compute_config_hash()
        key = self._make_key(goal, page_url, compact_state, config_hash)
        project = _get_project_name()
        vid = _active_version_id(project)
        conn = None
        try:
            conn = self._get_conn()
            row = conn.execute(
                "SELECT actions_json FROM %s WHERE cache_key = %%s AND project = %%s AND success_count > 0 AND (version_id = %%s OR version_id = 0)"
                % self._TABLE,
                (key, project, vid),
            ).fetchone()
            conn.close()
            conn = None
            if row is None:
                with self._stats_lock:
                    self._stats["miss"] = self._stats.get("miss", 0) + 1
                return None
            actions = json.loads(row["actions_json"])
            resolved = resolve_ai_ids(actions, compact_state)
            if resolved is None:
                with self._stats_lock:
                    self._stats["miss"] = self._stats.get("miss", 0) + 1
                self._delete_key_async(key, project=project)
                return None
            self._touch_hit(key, project=project)
            with self._stats_lock:
                self._stats["hit"] = self._stats.get("hit", 0) + 1
            return resolved
        except Exception as e:
            _log.info("  [OpCache] 读取失败: %s", e)
            with self._stats_lock:
                self._stats["miss"] = self._stats.get("miss", 0) + 1
            return None
        finally:
            if conn:
                try:
                    conn.close()
                except Exception as _pass_e:
                    _log.warning("cache_ops.py:60 异常被吞: %s", _pass_e, exc_info=True)

    def set(
        self,
        goal: str,
        page_url: str,
        compact_state: list[dict],
        config_hash: str,
        actions: list[dict],
    ):
        """
        写入缓存。自动剥离 ai_id 字段并替换动态文本为占位符。
        """
        from src.pipeline.steps.step4.op_cache import (
            compute_config_hash,
            _get_project_name,
            _active_version_id,
            _strip_and_placeholder,
            normalize_goal,
            compute_semantic_fingerprint,
        )

        if not actions:
            return None
        if actions[0].get("action") == "_guard_reject":
            return None
        config_hash = config_hash or compute_config_hash()
        key = self._make_key(goal, page_url, compact_state, config_hash)
        cleaned = _strip_and_placeholder(actions)
        project = _get_project_name()
        vid = _active_version_id(project)
        from urllib.parse import urlparse

        parsed = urlparse(page_url or "")
        page_path = parsed.netloc + parsed.path
        conn = None
        try:
            conn = self._get_conn()
            now = time.time()
            conn.execute(
                "REPLACE INTO %s "
                "(cache_key, project, version_id, goal_normalized, page_path, "
                "dom_fingerprint, config_hash, actions_json, hit_count, "
                "success_count, created_at, last_hit_at) "
                "VALUES (%%s, %%s, %%s, %%s, %%s, %%s, %%s, %%s, 0, 0, %%s, NULL)"
                % self._TABLE,
                (
                    key,
                    project,
                    vid,
                    normalize_goal(goal)[:200],
                    page_path,
                    compute_semantic_fingerprint(compact_state),
                    config_hash,
                    json.dumps(cleaned, ensure_ascii=False),
                    now,
                ),
            )
            conn.commit()
            conn.close()
            conn = None
            _log.info("  [OpCache] ✓ 写入缓存 (%s)", key[:12])
            self._enforce_lru_eviction(_get_project_name())
            return key
        except Exception as e:
            _log.info("  [OpCache] 写入失败: %s", e)
            return None
        finally:
            if conn:
                try:
                    conn.close()
                except Exception as _pass_e:
                    _log.warning(
                        "cache_ops.py:112 异常被吞: %s", _pass_e, exc_info=True
                    )

    def record_success(self, key: str, success: bool = True) -> bool:
        """记录缓存执行结果。命中缓存并执行成功时递增 success_count；失败时删除该缓存。"""
        from src.pipeline.steps.step4.op_cache import _get_project_name

        key = str(key or "").strip()
        if not key:
            return False
        conn = None
        try:
            conn = self._get_conn()
            if success:
                conn.execute(
                    "UPDATE %s SET success_count = success_count + 1, last_hit_at = %%s "
                    "WHERE cache_key = %%s AND project = %%s" % self._TABLE,
                    (time.time(), key, _get_project_name()),
                )
            else:
                conn.execute(
                    "DELETE FROM %s WHERE cache_key = %%s AND project = %%s"
                    % self._TABLE,
                    (key, _get_project_name()),
                )
            conn.commit()
            with self._lock:
                self._memory_cache.pop(key, None)
            return True
        except Exception as e:
            _log.debug("[OpCache] record_success 失败: %s", str(e)[:120])
            return False
        finally:
            if conn:
                try:
                    conn.close()
                except Exception as _pass_e:
                    _log.warning(
                        "cache_ops.py:144 异常被吞: %s", _pass_e, exc_info=True
                    )

    def commit_pending(self, key: str, success: bool = True) -> bool:
        """提交 AI 首轮生成的待确认缓存。成功则保留并累计成功次数；失败则删除，避免坏指令沉淀。"""
        return self.record_success(key, success=success)
