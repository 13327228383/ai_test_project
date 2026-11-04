# -*- coding: utf-8 -*-
"""
OperationCache LRU 淘汰 + 失效 Mixin
======================================
LRU 容量控制、按成功率淘汰坏缓存。
"""

import logging

_log = logging.getLogger(__name__)


class _OpCacheEvictionMixin:
    """OperationCache LRU 淘汰和主动失效操作。"""

    def _enforce_lru_eviction(self, project: str) -> int:
        """
        ★ P2-4: LRU 容量淘汰。
        当某项目的缓存条目超过 _max_capacity (config.op_cache.max_capacity) 时，
        淘汰最久未命中（COALESCE(last_hit_at, created_at) 最小）的条目。
        返回: 淘汰数量。
        """
        conn = None
        try:
            conn = self._get_conn()
            cur = conn.execute(
                "SELECT COUNT(*) AS cnt FROM %s WHERE project = %%s" % self._TABLE,
                (project,),
            )
            row = cur.fetchone()
            count = row[0] if row else 0
            _max_cap = self._max_capacity  # ★ 走 config 动态容量
            excess = count - _max_cap
            if excess <= 0:
                conn.close()
                return 0

            conn.execute(
                "DELETE FROM %s WHERE cache_key IN ("
                "  SELECT cache_key FROM ("
                "    SELECT cache_key FROM %s WHERE project = %%s "
                "    ORDER BY COALESCE(last_hit_at, created_at) ASC LIMIT %%s"
                "  ) AS _sub)" % (self._TABLE, self._TABLE),
                (project, excess),
            )
            n = conn.total_changes
            conn.commit()
            conn.close()
            conn = None
            if n > 0:
                with self._lock:
                    self._memory_cache.clear()
                _log.info(
                    "[OpCache] LRU淘汰 %d 条缓存 (项目:%s 原总量:%d 上限:%d)",
                    n,
                    project,
                    count,
                    _max_cap,
                )
            return n
        except Exception as e:
            _log.debug("[OpCache] LRU淘汰失败: %s", e)
            return 0
        finally:
            if conn:
                try:
                    conn.close()
                except Exception as _pass_e:
                    _log.warning("eviction.py:65 异常被吞: %s", _pass_e, exc_info=True)

    def invalidate_by_success_rate(self, project: str = None, min_rate: float = 0.3):
        """淘汰成功率低于阈值的坏缓存。"""
        from src.pipeline.steps.step4.op_cache import _get_project_name

        conn = None
        try:
            conn = self._get_conn()
            conn.execute(
                "DELETE FROM %s WHERE project=%%s AND hit_count>0 AND CAST(success_count AS DECIMAL(10,4))/hit_count < %%s"
                % self._TABLE,
                (project or _get_project_name(), min_rate),
            )
            n = conn.total_changes
            conn.commit()
            with self._lock:
                self._memory_cache.clear()
            return n
        except Exception as _e:
            _log.debug(
                "[eviction] invalidate_low_success 失败(降级返回0): %s", str(_e)[:100]
            )
            return 0
        finally:
            if conn:
                try:
                    conn.close()
                except Exception as _pass_e:
                    _log.warning("eviction.py:106 异常被吞: %s", _pass_e, exc_info=True)
