# -*- coding: utf-8 -*-
"""
ActionLibraryStore 健康统计 Mixin
==================================
只读统计计数。
"""

import logging

_log = logging.getLogger(__name__)


class _ActionLibraryHealthMixin:
    """ActionLibraryStore 健康统计。"""

    def health_stats(self) -> dict[str, int]:
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
            cat_cnt = c.execute(
                "SELECT COUNT(*) AS cnt FROM action_categories"
            ).fetchone()
            act_cnt = c.execute("SELECT COUNT(*) AS cnt FROM action_actions").fetchone()
            to_int = lambda r: (
                int(r["cnt"] or 0)
                if isinstance(r, dict)
                else (int(r[0] or 0) if r else 0)
            )
            return {
                "category_count": to_int(cat_cnt),
                "action_count": to_int(act_cnt),
            }
        except Exception as e:
            self._last_error = str(e)
            return {"category_count": 0, "action_count": 0}

        finally:
            if c:
                try:
                    c.close()
                except Exception as _e:
                    _log.debug("[health] 非关键异常: %s", _e, exc_info=True)
