# -*- coding: utf-8 -*-
"""
ReviewStore 健康统计 + 委托 Mixin
==================================
健康统计和委托到其他 store 的方法。
"""

import logging

_log = logging.getLogger(__name__)


class _ReviewHealthMixin:
    """ReviewStore 健康统计和委托方法。"""

    def update_requirement_review_status(self, req_id: str, status: str) -> bool:
        """更新需求审核状态。委托 requirement_store。"""
        try:
            from src.pipeline.utils.requirement_store import requirement_store

            req_id = str(req_id or "").strip()
            if not req_id:
                return False
            return bool(
                requirement_store.update_requirement(
                    req_id, {"review_status": str(status or "")}
                )
            )
        except Exception as e:
            self._last_error = str(e)
            _log.info(
                "  [ReviewStore] requirement.review_status 更新失败: %s", str(e)[:120]
            )
            return False

    def health_stats(self) -> dict[str, int]:
        """返回只读统计计数，供一致性健康检查使用。"""
        from src.core.config_loader import config

        project = config.project_name or ""
        stats = {
            "case_meta_count": 0,
            "review_log_count": 0,
            "approved_intents": 0,
            "pending_intents": 0,
            "rejected_intents": 0,
            "modified_intents": 0,
        }
        try:
            c = None
            c = None
            c = None
            c = None
            c = None
            c = None
            c = None
            c = None
            c = None
            c = None
            c = None
            c = None
            c = self._get_db()
            self._ensure_tables(c)
            cm = c.execute(
                "SELECT COUNT(*) AS n FROM case_meta WHERE project=%s", (project,)
            ).fetchone()
            stats["case_meta_count"] = int(cm["n"]) if cm else 0
            rl = c.execute(
                "SELECT COUNT(*) AS n FROM review_log WHERE project=%s", (project,)
            ).fetchone()
            stats["review_log_count"] = int(rl["n"]) if rl else 0
            for s in ("approved", "pending", "rejected", "modified"):
                row = c.execute(
                    "SELECT COUNT(*) AS n FROM case_meta WHERE project=%s AND review_status=%s",
                    (project, s),
                ).fetchone()
                stats[f"{s}_intents"] = int(row["n"]) if row else 0
        except Exception as e:
            self._last_error = str(e)
        finally:
            if c:
                try:
                    c.close()
                except Exception as _e:
                    _log.debug("[health] 非关键异常: %s", _e, exc_info=True)
        return stats
