# -*- coding: utf-8 -*-
"""
OperationCache 统计 + 预热 Mixin
=================================
缓存统计、内存预热。
"""

import json
import time
from typing import Any
import logging

_log = logging.getLogger(__name__)


class _OpCacheStatsMixin:
    """OperationCache 统计、预热。"""

    def get_stats(self, project: str = None) -> dict[str, Any]:
        """仪表盘：返回会话+DB 的缓存统计。"""
        from src.pipeline.steps.step4.op_cache import _get_project_name

        with self._stats_lock:
            sh = self._stats.get("hit", 0)
            sm = self._stats.get("miss", 0)
        st = sh + sm
        sr = round(sh / max(st, 1) * 100, 1)
        total = 0
        top = []
        conn = None
        try:
            conn = self._get_conn()
            cur = conn.execute(
                "SELECT COUNT(*) FROM %s WHERE project=%%s" % self._TABLE,
                (project or _get_project_name(),),
            )
            row = cur.fetchone() if cur else None
            total = int(
                (row.get("COUNT(*)") if isinstance(row, dict) else row[0]) if row else 0
            )
            cur = conn.execute(
                "SELECT cache_key, hit_count, success_count FROM %s WHERE project=%%s ORDER BY hit_count DESC LIMIT 5"
                % self._TABLE,
                (project or _get_project_name(),),
            )
            top = []
            for r in cur.fetchall():
                if isinstance(r, dict):
                    top.append(
                        {
                            "key": str(r.get("cache_key", ""))[:20],
                            "hit": int(r.get("hit_count", 0) or 0),
                            "success": int(r.get("success_count", 0) or 0),
                        }
                    )
                else:
                    top.append({"key": str(r[0])[:20], "hit": r[1], "success": r[2]})
        except Exception as e:
            _log.debug("[OpCache] get_stats 失败: %s", str(e)[:120])
        finally:
            if conn:
                try:
                    conn.close()
                except Exception as _pass_e:
                    _log.warning("stats.py:50 异常被吞: %s", _pass_e, exc_info=True)
        return {
            "hit": sh,
            "miss": sm,
            "hit_rate": sr,
            "total_entries": total,
            "top_hits": top,
        }

    def warmup(self, project: str = None, top_n: int = 20):
        """预热：加载高频缓存到内存。"""
        from src.pipeline.steps.step4.op_cache import _get_project_name

        loaded = []
        conn = None
        try:
            conn = self._get_conn()
            cur = conn.execute(
                "SELECT cache_key, actions_json FROM %s WHERE project=%%s ORDER BY hit_count DESC LIMIT %%s"
                % self._TABLE,
                (project or _get_project_name(), top_n),
            )
            rows = cur.fetchall()
            for key, aj in rows:
                try:
                    actions = json.loads(aj) if isinstance(aj, str) else aj
                    if actions:
                        with self._lock:
                            self._memory_cache[key] = (actions, time.time())
                        loaded.append(key)
                except Exception as _e:
                    _log.debug("[stats] 跳过条目: %s", _e, exc_info=True)
                    continue
        except Exception as _pass_e:
            _log.warning("stats.py:71 异常被吞: %s", _pass_e, exc_info=True)
        finally:
            if conn:
                try:
                    conn.close()
                except Exception as _pass_e:
                    _log.warning("stats.py:75 异常被吞: %s", _pass_e, exc_info=True)
        return loaded[:5]
