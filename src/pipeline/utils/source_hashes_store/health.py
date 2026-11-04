# -*- coding: utf-8 -*-
"""
SourceHashesStore 健康统计 mixin：health_stats。
"""

import logging
from src.core.guarded import guard

logger = logging.getLogger(__name__)


class _SourceHashesHealthMixin:
    """各表记录数统计（仅统计当前 project）。"""

    # ------------------------------------------------------------------
    # 健康统计
    # ------------------------------------------------------------------
    def health_stats(self) -> dict[str, int]:
        """返回各表的记录数（仅统计当前 project）。"""
        stats = {"confluence": 0, "yapi": 0, "breakdown": 0}
        c = None
        try:
            c = None
            c = None
            c = None
            c = None
            c = None
            c = None
            c = None
            c = self._get_db()
            self._ensure_tables(c)
            project = self._project()
            for key, table in (
                ("confluence", "source_confluence_hashes"),
                ("yapi", "source_yapi_hashes"),
                ("breakdown", "source_breakdown_hashes"),
            ):
                try:
                    cur = c.execute(
                        "SELECT COUNT(*) FROM %s WHERE project = %s" % table,
                        (project,),
                    )
                    row = cur.fetchone()
                    stats[key] = int(row[0]) if row else 0
                except Exception as exc:
                    logger.warning("Failed to count rows in %s: %s", table, exc)
                    stats[key] = 0
        except Exception as e:
            self._last_error = "health_stats failed: %s" % str(e)
        finally:
            if c:
                with guard("health_stats: c.close()", log=logger, level=logging.WARNING):
                    c.close()
        return stats
