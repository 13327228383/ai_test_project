# -*- coding: utf-8 -*-
from __future__ import annotations

"""
ActionLibraryStore 写入操作 Mixin
==================================
整体库替换、重置、单条 action upsert/delete。
"""

from typing import Any
import logging

_log = logging.getLogger(__name__)


class _ActionLibraryWriteMixin:
    """ActionLibraryStore 写入操作。"""

    @staticmethod
    def _invalidate_dispatcher_cache() -> None:
        """★ 缓存失效广播（2026-08-04）：写操作后使 action_dispatchers 模块级
        action_id → tool_name 映射缓存失效，避免脏缓存导致查表路径仍用旧映射。

        惰性导入避免循环依赖（action_dispatchers 顶层 import action_library_store）。
        """
        try:
            from src.pipeline.steps.step4.action_dispatchers import (
                invalidate_cache as _inv,
            )

            _inv()
        except Exception as _e:
            _log.debug("[write] dispatcher 缓存失效广播异常: %s", _e, exc_info=True)

    def save_library(self, lib: dict[str, Any]) -> bool:
        """以整库方式写入：清空通用指令库后重写 categories + actions。"""
        now = self._now()
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
            c.execute("DELETE FROM action_actions")
            c.execute("DELETE FROM action_categories")
            categories = lib.get("categories", []) if isinstance(lib, dict) else []
            for order, cat in enumerate(categories):
                if not isinstance(cat, dict):
                    continue
                cat_id = str(cat.get("id", "") or "").strip()
                if not cat_id:
                    continue
                c.execute(
                    """
                    INSERT INTO action_categories(category_id, name, icon, sort_order, updated_at)
                    VALUES(%s,%s,%s,%s,%s)
                    """,
                    (
                        cat_id,
                        str(cat.get("name", cat_id) or cat_id),
                        str(cat.get("icon", "") or ""),
                        order,
                        now,
                    ),
                )
                for action in cat.get("actions", []):
                    if not isinstance(action, dict):
                        continue
                    action_id = str(action.get("id", "") or "").strip()
                    if not action_id:
                        continue
                    c.execute(
                        """
                        INSERT INTO action_actions(
                            category_id, action_id, label, description,
                            keywords_json, params_json, needs_ai_id, is_workflow,
                            example, updated_at, applicable_tags,
                            total_count, success_count, fail_count, tool_name
                        ) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        """,
                        (
                            cat_id,
                            action_id,
                            str(action.get("label", "") or ""),
                            str(action.get("desc", "") or ""),
                            self._to_json(action.get("keywords", []) or []),
                            self._to_json(action.get("params", []) or []),
                            1 if bool(action.get("needs_ai_id")) else 0,
                            1 if bool(action.get("is_workflow")) else 0,
                            str(action.get("example", "") or ""),
                            now,
                            self._to_json(action.get("applicable_tags") or []),
                            int(action.get("total_count") or 0),
                            int(action.get("success_count") or 0),
                            int(action.get("fail_count") or 0),
                            str(action.get("tool_name", "") or ""),
                        ),
                    )
            c.commit()
            self._last_error = ""
            self._invalidate_dispatcher_cache()
            return True
        except Exception as e:
            self._last_error = str(e)
            _log.info("  [ActionLibraryStore] save_library 失败: %s", str(e)[:120])
            return False

        finally:
            if c:
                try:
                    c.close()
                except Exception as _e:
                    _log.debug("[write] 非关键异常: %s", _e, exc_info=True)

    def reset_to_default(self) -> bool:
        from src.pipeline.utils.action_library_store import _load_default_library

        return self.save_library(_load_default_library())

    def upsert_action(
        self,
        category_id: str,
        action: dict[str, Any],
        category_name: str | None = None,
        category_icon: str | None = None,
    ) -> dict[str, Any] | None:
        """新增或更新一个操作；若分类不存在则自动创建。返回清洗后的 action。"""
        cat_id = str(category_id or "").strip()
        if not cat_id or not isinstance(action, dict):
            return None
        action_id = str(action.get("id", "") or "").strip()
        if not action_id:
            return None
        clean_action = {
            "id": action_id,
            "label": str(action.get("label", "") or ""),
            "desc": str(action.get("desc", "") or ""),
            "keywords": list(action.get("keywords", []) or []),
            "params": list(action.get("params", []) or []),
            "applicable_tags": list(action.get("applicable_tags", []) or []),
            "tool_name": str(action.get("tool_name", "") or ""),
        }
        if action.get("is_workflow"):
            clean_action["is_workflow"] = True
        if action.get("needs_ai_id") is not None:
            clean_action["needs_ai_id"] = bool(action["needs_ai_id"])
        if action.get("example"):
            clean_action["example"] = str(action["example"])
        now = self._now()
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
            max_row = (
                c.execute(
                    "SELECT COALESCE(MAX(sort_order), 0) AS max_order FROM action_categories"
                ).fetchone()
                or {}
            )
            max_order = int(
                max_row.get("max_order", 0)
                if isinstance(max_row, dict)
                else (max_row[0] or 0)
            )
            c.execute(
                """
                INSERT IGNORE INTO action_categories(category_id, name, icon, sort_order, updated_at)
                VALUES(%s,%s,%s,%s,%s)
                """,
                (
                    cat_id,
                    category_name or cat_id,
                    category_icon or "📌",
                    max_order + 1,
                    now,
                ),
            )
            if category_name or category_icon:
                existing = (
                    c.execute(
                        "SELECT name, icon FROM action_categories WHERE category_id=%s",
                        (cat_id,),
                    ).fetchone()
                    or {}
                )
                c.execute(
                    "UPDATE action_categories SET name=%s, icon=%s, updated_at=%s WHERE category_id=%s",
                    (
                        category_name
                        or (
                            existing.get("name", cat_id)
                            if isinstance(existing, dict)
                            else cat_id
                        ),
                        category_icon
                        or (
                            existing.get("icon", "📌")
                            if isinstance(existing, dict)
                            else "📌"
                        ),
                        now,
                        cat_id,
                    ),
                )
            c.execute(
                """
                REPLACE INTO action_actions(
                    category_id, action_id, label, description,
                    keywords_json, params_json, needs_ai_id, is_workflow, example, updated_at,
                    applicable_tags, total_count, success_count, fail_count, tool_name
                ) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    cat_id,
                    action_id,
                    clean_action["label"],
                    clean_action["desc"],
                    self._to_json(clean_action["keywords"]),
                    self._to_json(clean_action["params"]),
                    1 if clean_action.get("needs_ai_id", False) else 0,
                    1 if clean_action.get("is_workflow", False) else 0,
                    clean_action.get("example", ""),
                    now,
                    self._to_json(clean_action["applicable_tags"]),
                    int(clean_action.get("total_count") or 0),
                    int(clean_action.get("success_count") or 0),
                    int(clean_action.get("fail_count") or 0),
                    clean_action["tool_name"],
                ),
            )
            c.commit()
            self._last_error = ""
            self._invalidate_dispatcher_cache()
            return clean_action
        except Exception as e:
            self._last_error = str(e)
            _log.info("  [ActionLibraryStore] upsert_action 失败: %s", str(e)[:120])
            return None

        finally:
            if c:
                try:
                    c.close()
                except Exception as _e:
                    _log.debug("[write] 非关键异常: %s", _e, exc_info=True)

    def record_action_execution(self, action_id: str, success: bool) -> bool:
        """记录一次操作执行结果，更新统计计数。"""
        action_id = str(action_id or "").strip()
        if not action_id:
            return False
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
            c.execute(
                """
                UPDATE action_actions
                SET total_count = total_count + 1,
                    success_count = success_count + %s,
                    fail_count = fail_count + %s,
                    updated_at = %s
                WHERE action_id = %s
                """,
                (1 if success else 0, 0 if success else 1, self._now(), action_id),
            )
            c.commit()
            self._last_error = ""
            return True
        except Exception as e:
            self._last_error = str(e)
            _log.info(
                "  [ActionLibraryStore] record_action_execution 失败: %s", str(e)[:120]
            )
            return False
        finally:
            if c:
                try:
                    c.close()
                except Exception as _e:
                    _log.debug("[write] 非关键异常: %s", _e, exc_info=True)

    def delete_action(self, action_id: str) -> bool:
        action_id = str(action_id or "").strip()
        if not action_id:
            return False
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
            cur = c.execute(
                "DELETE FROM action_actions WHERE action_id=%s",
                (action_id,),
            )
            deleted = cur.rowcount if hasattr(cur, "rowcount") and cur.rowcount else 0
            c.commit()
            self._last_error = ""
            self._invalidate_dispatcher_cache()
            return bool(deleted)
        except Exception as e:
            self._last_error = str(e)
            _log.info("  [ActionLibraryStore] delete_action 失败: %s", str(e)[:120])
            return False

        finally:
            if c:
                try:
                    c.close()
                except Exception as _e:
                    _log.debug("[write] 非关键异常: %s", _e, exc_info=True)
