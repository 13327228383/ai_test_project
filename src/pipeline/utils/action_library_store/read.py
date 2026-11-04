# -*- coding: utf-8 -*-
"""
ActionLibraryStore 读取操作 Mixin
==================================
整体库加载、白名单加载、导入关键词加载。
"""

from typing import Any, Set
import logging

_log = logging.getLogger(__name__)


class _ActionLibraryReadMixin:
    """ActionLibraryStore 读取操作。"""

    def load_library(self) -> dict[str, Any]:
        """以原先 JSON 结构返回 categories + actions，便于前端和旧代码兼容。"""
        return self._load_library_impl(depth=0)

    def _load_library_impl(self, depth: int = 0) -> dict[str, Any]:
        from src.pipeline.utils.action_library_store import _load_default_library

        if depth > 1:
            return _load_default_library()
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
            cat_rows = c.execute(
                "SELECT category_id, name, icon FROM action_categories ORDER BY sort_order, category_id"
            ).fetchall()
            if not cat_rows:
                self.reset_to_default()
                return self._load_library_impl(depth + 1)
            action_rows = c.execute(
                """
                SELECT category_id, action_id, label, description,
                       keywords_json, params_json, needs_ai_id, is_workflow, example,
                       applicable_tags, total_count, success_count, fail_count, tool_name
                FROM action_actions
                ORDER BY category_id, action_id
                """
            ).fetchall()
            actions_by_cat: dict[str, list[dict[str, Any]]] = {}
            for row in action_rows:
                item = (
                    dict(row)
                    if isinstance(row, dict)
                    else {
                        "category_id": row[0],
                        "action_id": row[1],
                        "label": row[2],
                        "description": row[3],
                        "keywords_json": row[4],
                        "params_json": row[5],
                        "needs_ai_id": row[6],
                        "is_workflow": row[7],
                        "example": row[8],
                        "applicable_tags": row[9],
                        "total_count": row[10],
                        "success_count": row[11],
                        "fail_count": row[12],
                        "tool_name": row[13],
                    }
                )
                action = {
                    # ★ API 契约：DB列名 action_id/description 对外统一暴露为 id/desc
                    "id": item["action_id"],
                    "label": item.get("label", "") or "",
                    "desc": item.get("description", "") or "",
                    "keywords": self._from_json(item.get("keywords_json"), []),
                    "params": self._from_json(item.get("params_json"), []),
                    "applicable_tags": self._from_json(item.get("applicable_tags"), []),
                    "total_count": int(item.get("total_count") or 0),
                    "success_count": int(item.get("success_count") or 0),
                    "fail_count": int(item.get("fail_count") or 0),
                    "tool_name": (item.get("tool_name") or "").strip(),
                }
                if item.get("needs_ai_id"):
                    action["needs_ai_id"] = True
                if item.get("is_workflow"):
                    action["is_workflow"] = True
                if item.get("example"):
                    action["example"] = item["example"]
                actions_by_cat.setdefault(item["category_id"], []).append(action)
            categories = []
            for row in cat_rows:
                item = (
                    dict(row)
                    if isinstance(row, dict)
                    else {
                        "category_id": row[0],
                        "name": row[1],
                        "icon": row[2],
                    }
                )
                categories.append(
                    {
                        "id": item["category_id"],
                        "name": item.get("name") or item["category_id"],
                        "icon": item.get("icon", "") or "",
                        "actions": actions_by_cat.get(item["category_id"], []),
                    }
                )
            self._last_error = ""
            return {"version": 1, "categories": categories}
        except Exception as e:
            self._last_error = str(e)
            _log.info("  [ActionLibraryStore] load_library 失败: %s", str(e)[:120])
            return _load_default_library()

        finally:
            if c:
                try:
                    c.close()
                except Exception as _e:
                    _log.debug("[read] 非关键异常: %s", _e, exc_info=True)

    def load_allowed_action_ids(self, _depth: int = 0) -> Set[str]:
        """Step4 浏览器 AI 白名单（排除 is_workflow）。"""
        from src.pipeline.utils.action_library_store import _load_default_library

        if _depth > 1:
            return {
                "click",
                "fill",
                "scroll",
                "verify",
                "select_option",
                "press_key",
                "hover",
                "wait",
                "navigate",
                "screenshot",
                "api_verify",
                "db_verify",
                "double_click",
                "right_click",
                "check",
                "uncheck",
                "upload_file",
                "drag",
            }
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
            rows = c.execute(
                "SELECT DISTINCT action_id FROM action_actions WHERE is_workflow=0"
            ).fetchall()
            result = set()
            for row in rows:
                if isinstance(row, dict):
                    result.add(str(row.get("action_id", "") or ""))
                elif row:
                    result.add(str(row[0] or ""))
            if result:
                return result
            self.reset_to_default()
            return self.load_allowed_action_ids(_depth + 1)
        except Exception as e:
            self._last_error = str(e)
            _log.info(
                "  [ActionLibraryStore] load_allowed_action_ids 失败: %s", str(e)[:120]
            )
            return {
                "click",
                "fill",
                "scroll",
                "verify",
                "select_option",
                "press_key",
                "hover",
                "wait",
                "navigate",
                "screenshot",
                "api_verify",
                "db_verify",
                "double_click",
                "right_click",
                "check",
                "uncheck",
                "upload_file",
                "drag",
            }

        finally:
            if c:
                try:
                    c.close()
                except Exception as _e:
                    _log.debug("[read] 非关键异常: %s", _e, exc_info=True)

    def load_action_tool_map(self, _depth: int = 0) -> dict[str, str]:
        """从 DB 加载 action_id → tool_name 映射，供 dispatcher 使用。DB 不可用时兜底到种子数据。

        ★ 启动自愈（2026-08-04）：检测到 DB 有行但 tool_name 全空时，触发幂等回填
        （_backfill_tool_names，UPDATE 不 DELETE），不再调用 reset_to_default()
        （会丢用户自定义 + 统计计数）。
        """
        if _depth > 1:
            return {}
        from src.pipeline.utils.action_library_store import _load_default_library

        c = None
        try:
            c = self._get_db()
            self._ensure_tables(c)
            rows = c.execute(
                "SELECT action_id, tool_name FROM action_actions WHERE tool_name != ''"
            ).fetchall()
            result = {}
            for row in rows:
                if isinstance(row, dict):
                    aid = str(row.get("action_id", "") or "")
                    tn = str(row.get("tool_name", "") or "")
                elif row:
                    aid = str(row[0] or "")
                    tn = str(row[1] or "")
                if aid and tn:
                    result[aid] = tn
            if result:
                return result
            # ★ 启动自愈：先判断「有行但全空」（区别于「无行」）
            _cnt_row = c.execute("SELECT COUNT(*) AS c FROM action_actions").fetchone()
            _total = (
                int(_cnt_row.get("c") or 0)
                if isinstance(_cnt_row, dict)
                else int(_cnt_row[0] or 0) if _cnt_row else 0
            )
            if _total > 0:
                self._backfill_tool_names(c)
                rows = c.execute(
                    "SELECT action_id, tool_name FROM action_actions WHERE tool_name != ''"
                ).fetchall()
                result = {}
                for row in rows:
                    if isinstance(row, dict):
                        aid = str(row.get("action_id", "") or "")
                        tn = str(row.get("tool_name", "") or "")
                    elif row:
                        aid = str(row[0] or "")
                        tn = str(row[1] or "")
                    if aid and tn:
                        result[aid] = tn
                if result:
                    return result
            # 无行或回填后仍空 → 种子兜底（仅返回，不落库、不重置）
            lib = _load_default_library()
            result = {}
            for cat in lib.get("categories", []):
                for a in cat.get("actions", []):
                    tn = (a.get("tool_name") or "").strip()
                    if tn:
                        result[a["id"]] = tn
            return result
        except Exception as e:
            self._last_error = str(e)
            _log.info(
                "  [ActionLibraryStore] load_action_tool_map 失败: %s", str(e)[:120]
            )
            # fallback: 从种子数据构建映射
            lib = _load_default_library()
            result = {}
            for cat in lib.get("categories", []):
                for a in cat.get("actions", []):
                    tn = (a.get("tool_name") or "").strip()
                    if tn:
                        result[a["id"]] = tn
            return result
        finally:
            if c:
                try:
                    c.close()
                except Exception as _e:
                    _log.debug("[read] 非关键异常: %s", _e, exc_info=True)

    def load_action_tool_status_map(self, _depth: int = 0) -> dict[str, dict[str, Any]]:
        """加载 action_id → {tool_name, is_workflow} 完整映射（含空 tool_name 行）。

        与 ``load_action_tool_map`` 的区别：
        - 返回全部行的状态（含 EMPTY/WORKFLOW），供 dispatcher 三态分派
          （VALID/EMPTY/INVALID/WORKFLOW）。
        - 检测 tool_name 全空（有行但全空）时触发幂等回填 ``_backfill_tool_names``，
          不 DELETE、不重置统计、不覆盖用户自定义。
        """
        if _depth > 1:
            return {}
        c = None
        try:
            c = self._get_db()
            self._ensure_tables(c)
            rows = c.execute(
                "SELECT action_id, tool_name, is_workflow FROM action_actions"
            ).fetchall()
            result = self._rows_to_status_map(rows)
            total = len(result)
            non_empty = sum(1 for v in result.values() if v.get("tool_name"))
            # ★ 启动自愈：有行但 tool_name 全空 → 幂等回填（UPDATE 不 DELETE）
            if total > 0 and non_empty == 0:
                self._backfill_tool_names(c)
                rows = c.execute(
                    "SELECT action_id, tool_name, is_workflow FROM action_actions"
                ).fetchall()
                result = self._rows_to_status_map(rows)
            if result:
                return result
            # 无行 → 种子兜底（仅返回，不落库、不重置）
            return self._seed_status_map()
        except Exception as e:
            self._last_error = str(e)
            _log.info(
                "  [ActionLibraryStore] load_action_tool_status_map 失败: %s",
                str(e)[:120],
            )
            return self._seed_status_map()
        finally:
            if c:
                try:
                    c.close()
                except Exception as _e:
                    _log.debug("[read] 非关键异常: %s", _e, exc_info=True)

    @staticmethod
    def _rows_to_status_map(rows) -> dict[str, dict[str, Any]]:
        """把 action_actions 查询行（action_id, tool_name, is_workflow）转为状态映射。"""
        result: dict[str, dict[str, Any]] = {}
        for row in rows:
            if isinstance(row, dict):
                aid = str(row.get("action_id", "") or "")
                tn = str(row.get("tool_name", "") or "").strip()
                wf = bool(row.get("is_workflow"))
            elif row:
                aid = str(row[0] or "")
                tn = str(row[1] or "").strip()
                wf = bool(row[2] or 0)
            else:
                continue
            if aid:
                result[aid] = {"tool_name": tn, "is_workflow": wf}
        return result

    @staticmethod
    def _seed_status_map() -> dict[str, dict[str, Any]]:
        """从种子数据构建状态映射（兜底，不落库）。"""
        from src.pipeline.utils.action_library_store import _load_default_library

        lib = _load_default_library()
        result: dict[str, dict[str, Any]] = {}
        for cat in lib.get("categories", []):
            for a in cat.get("actions", []):
                aid = str(a.get("id", "") or "").strip()
                if aid:
                    result[aid] = {
                        "tool_name": str(a.get("tool_name", "") or "").strip(),
                        "is_workflow": bool(a.get("is_workflow")),
                    }
        return result

    def _backfill_tool_names(self, c=None) -> bool:
        """★ 启动自愈：幂等回填 action_actions.tool_name（UPDATE，不 DELETE）。

        从 defaults 种子数据按 action_id 回填 tool_name：
        - 仅更新「tool_name 为空 且 种子有 tool_name」的行（幂等）。
        - import_workflow / db_verify 等 workflow/复合动作保持空（is_workflow 标记）。
        - 不重置 total_count/success_count/fail_count 等统计字段，不覆盖用户自定义。
        - 禁止调用 reset_to_default() 兜底（会 DELETE 全表）。

        Returns:
            True 表示执行成功（无论是否更新了行）。
        """
        from src.pipeline.utils.action_library_store import _load_default_library

        own_conn = c is None
        try:
            if c is None:
                c = self._get_db()
                self._ensure_tables(c)
            # 从种子数据构建 action_id → tool_name 映射（含空值，用于跳过 workflow）
            lib = _load_default_library()
            seed: dict[str, str] = {}
            for cat in lib.get("categories", []):
                for a in cat.get("actions", []):
                    aid = str(a.get("id", "") or "").strip()
                    if aid:
                        seed[aid] = str(a.get("tool_name", "") or "").strip()
            updated = 0
            for aid, tn in seed.items():
                if not tn:
                    continue  # workflow/复合动作保持空
                cur = c.execute(
                    """
                    UPDATE action_actions
                    SET tool_name = %s, updated_at = %s
                    WHERE action_id = %s
                      AND (tool_name IS NULL OR tool_name = '')
                    """,
                    (tn, self._now(), aid),
                )
                rc = cur.rowcount if hasattr(cur, "rowcount") and cur.rowcount else 0
                updated += int(rc or 0)
            # ★ 必须提交：即使连接由调用方传入（own_conn=False），回填也要持久化；
            #   否则连接归还连接池时 reset=True 会 ROLLBACK 丢弃未提交的 UPDATE。
            try:
                c.commit()
            except Exception as _ce:
                _log.debug("[read] 回填提交失败: %s", _ce, exc_info=True)
            if updated:
                _log.info(
                    "  [ActionLibraryStore] 启动自愈回填 tool_name: %d 条", updated
                )
            self._last_error = ""
            return True
        except Exception as e:
            self._last_error = str(e)
            _log.info(
                "  [ActionLibraryStore] _backfill_tool_names 失败: %s", str(e)[:120]
            )
            return False
        finally:
            if own_conn and c:
                try:
                    c.close()
                except Exception as _e:
                    _log.debug("[read] 非关键异常: %s", _e, exc_info=True)

    def load_import_keywords(self, _depth: int = 0) -> tuple[str, ...]:
        """DataFactory 导入检测关键词（仅 is_workflow 操作的 keywords）。"""
        if _depth > 1:
            return (
                "导入",
                "上传",
                "下载模板",
                "下载模版",
                "模板下载",
                "模版下载",
                "批量导入",
                "导入数据",
                "上传文件",
                "导入文件",
                "import",
                "upload",
                "download template",
            )
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
            rows = c.execute(
                "SELECT keywords_json FROM action_actions WHERE is_workflow=1"
            ).fetchall()
            keywords: list[str] = []
            for row in rows:
                if isinstance(row, dict):
                    kws = self._from_json(row.get("keywords_json"), [])
                elif row:
                    kws = self._from_json(row[0], [])
                else:
                    kws = []
                if isinstance(kws, list):
                    keywords.extend(str(k) for k in kws if k)
            if keywords:
                return tuple(keywords)
            self.reset_to_default()
            return self.load_import_keywords(_depth + 1)
        except Exception as e:
            self._last_error = str(e)
            _log.info(
                "  [ActionLibraryStore] load_import_keywords 失败: %s", str(e)[:120]
            )
            return (
                "导入",
                "上传",
                "下载模板",
                "下载模版",
                "模板下载",
                "模版下载",
                "批量导入",
                "导入数据",
                "上传文件",
                "导入文件",
                "import",
                "upload",
                "download template",
            )

        finally:
            if c:
                try:
                    c.close()
                except Exception as _e:
                    _log.debug("[read] 非关键异常: %s", _e, exc_info=True)

    def load_control_type_mappings(self) -> dict[str, Any]:
        """★ f4: 加载控件类型映射（control_type → tags / control_type → preferred actions）。

        当前返回模块级默认值（_DEFAULT_CONTROL_TYPE_TAGS + _DEFAULT_CONTROL_TYPE_PREFERENCES）。
        未来可扩展为从 MySQL 配置表读取，实现项目级覆盖。

        Returns:
            {"control_type_tags": {...}, "control_type_preferences": {...}}
        """
        from src.pipeline.utils.action_library_store import (
            _DEFAULT_CONTROL_TYPE_TAGS,
            _DEFAULT_CONTROL_TYPE_PREFERENCES,
        )

        return {
            "control_type_tags": dict(_DEFAULT_CONTROL_TYPE_TAGS),
            "control_type_preferences": dict(_DEFAULT_CONTROL_TYPE_PREFERENCES),
        }
