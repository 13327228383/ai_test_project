# -*- coding: utf-8 -*-
"""写入 mixin：upsert / update / reset_all_is_generated / delete。"""

import logging
from src.core.guarded import guard
from datetime import datetime
from typing import Any

from src.core.requirement_semantics import requirement_semantics

_log = logging.getLogger(__name__)


class RequirementStoreWriteMixin:
    """写入相关操作：upsert_requirements, update_requirement, reset_all_is_generated, delete_requirement。"""

    def clear_version_requirements(self, project: str, version_id: int) -> bool:
        """★ 清空指定项目+版本的旧需求（Step2 重跑前调用，避免新旧并存导致计数混淆）。"""
        # ★ P0-1 批C修复(2026-07-25): version_id=0 是合法 legacy 值，不能用 `not version_id`
        if not project or version_id is None or version_id < 0:
            return True
        c = None
        try:
            c = self._get_db()
            if c:
                c.execute(
                    "DELETE FROM requirements WHERE project=%s AND version_id=%s",
                    (project, version_id),
                )
                # ★ SteadyDBConnection 没有 connection 属性，直接用 c.commit()
                try:
                    c.commit()
                except Exception as _e:
                    _log.debug(
                        "c.commit 失败: %s", _e, exc_info=True
                    )  # SteadyDBConnection 的 commit 可能通过其他方式处理
                _log.info(
                    "[RequirementStore] 已清空 project=%s version_id=%s 的旧需求",
                    project,
                    version_id,
                )
            return True
        except Exception as e:
            _log.warning("[RequirementStore] 清空版本需求失败: %s", e)
            return False
        finally:
            if c:
                try:
                    c.close()
                except Exception as _e:
                    _log.debug("conn.close 失败: %s", _e, exc_info=True)

    def clear_version_requirements_except(
        self, project: str, version_id: int, keep_req_ids: list[str]
    ) -> bool:
        """★ 清空指定项目+版本的旧需求，但保留指定 req_id 列表中的需求。

        用于增量更新场景：AI 重新拆解后，unchanged 的需求不应被删除，
        只删除 modified/added/removed 的需求，避免 AI 不稳定导致旧需求丢失。

        Args:
            project: 项目名
            version_id: 版本 ID
            keep_req_ids: 需要保留的 req_id 列表（unchanged 需求）
        """
        if not project or version_id is None or version_id < 0:
            return True
        c = None
        try:
            c = self._get_db()
            if c:
                if keep_req_ids:
                    placeholders = ",".join(["%s"] * len(keep_req_ids))
                    sql = (
                        "DELETE FROM requirements "
                        "WHERE project=%s AND version_id=%s "
                        "AND req_id NOT IN (" + placeholders + ")"
                    )
                    params = [project, version_id] + list(keep_req_ids)
                    c.execute(sql, tuple(params))
                else:
                    c.execute(
                        "DELETE FROM requirements WHERE project=%s AND version_id=%s",
                        (project, version_id),
                    )
                try:
                    c.commit()
                except Exception as _e:
                    _log.debug("c.commit 失败: %s", _e, exc_info=True)
                _log.info(
                    "[RequirementStore] 已清理 project=%s version_id=%s 的旧需求 (保留 %d 条 unchanged)",
                    project,
                    version_id,
                    len(keep_req_ids),
                )
            return True
        except Exception as e:
            _log.warning("[RequirementStore] 清理版本需求(保留模式)失败: %s", e)
            return False
        finally:
            if c:
                try:
                    c.close()
                except Exception as _e:
                    _log.debug("conn.close 失败: %s", _e, exc_info=True)

    def upsert_requirements(self, requirements: list[dict[str, Any]]) -> bool:
        """写入或更新需求镜像。失败返回 False，不抛出。"""
        if not requirements:
            return True
        from src.core.config_loader import config

        project = config.project_name or ""
        from src.core.versioning import current_version_id, assert_version_writable

        # ★ PV-012: 版本状态检查，archived/deleted 版本拒绝写入
        if not assert_version_writable(project):
            _log.warning(
                "[RequirementStore] 版本不可写入，拒绝 upsert %d 条需求",
                len(requirements),
            )
            return False
        active_version_id = current_version_id(project)
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        c = None
        try:
            with self._lock:
                c = self._get_db()
                try:
                    self._ensure_tables(c)
                    skipped = 0
                    for req in requirements:
                        if not isinstance(req, dict):
                            skipped += 1
                            continue
                        rid = str(req.get("req_id", "") or "").strip()
                        if not rid:
                            # fail-closed：无 req_id 的行会被静默丢弃，绝不能回成功
                            skipped += 1
                            _log.error(
                                "[Store] 需求缺少 req_id，已跳过（不写入）: %s",
                                str(req.get("description", ""))[:60],
                            )
                            continue
                        # ★ 需求语义一次性判定（落库咽喉点，唯一产生点）：
                        #   结构化字段随 payload_json 持久化，Step3 生成端/身份指派
                        #   一律读取，不得再从文本重推同一语义。
                        req.update(requirement_semantics(req))
                        c.execute(
                            """
                            INSERT INTO requirements(
                                req_id, project, version_id, stable_req_key, source_anchor,
                                content_hash, semantic_hash, change_type, origin_req_id,
                                origin_version_id, feature_id, slice_id, module, description,
                                operation, condition_expr, expected_feedback, source_quote,
                                _verify_status, _evidence_confidence,
                                is_implicit, is_generated,
                                dependency_req_id, review_status, payload_json,
                                contains_sensitive_data,
                                created_at, updated_at
                            )
                            VALUES(
                                %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s
                            )
                            ON DUPLICATE KEY UPDATE
                                version_id=VALUES(version_id),
                                stable_req_key=VALUES(stable_req_key),
                                source_anchor=VALUES(source_anchor),
                                content_hash=VALUES(content_hash),
                                semantic_hash=VALUES(semantic_hash),
                                change_type=VALUES(change_type),
                                origin_req_id=VALUES(origin_req_id),
                                origin_version_id=VALUES(origin_version_id),
                                feature_id=VALUES(feature_id),
                                slice_id=VALUES(slice_id),
                                module=VALUES(module),
                                description=VALUES(description),
                                operation=VALUES(operation),
                                condition_expr=VALUES(condition_expr),
                                expected_feedback=VALUES(expected_feedback),
                                source_quote=VALUES(source_quote),
                                _verify_status=VALUES(_verify_status),
                                _evidence_confidence=VALUES(_evidence_confidence),
                                is_implicit=VALUES(is_implicit),
                                is_generated=VALUES(is_generated),
                                dependency_req_id=VALUES(dependency_req_id),
                                review_status=VALUES(review_status),
                                payload_json=VALUES(payload_json),
                                contains_sensitive_data=VALUES(contains_sensitive_data),
                                updated_at=VALUES(updated_at)
                            """,
                            (
                                rid,
                                project,
                                req.get("version_id", active_version_id) or 0,
                                str(req.get("stable_req_key", "") or ""),
                                str(req.get("source_anchor", "") or ""),
                                str(req.get("content_hash", "") or ""),
                                str(req.get("semantic_hash", "") or ""),
                                str(req.get("change_type", "unknown") or "unknown"),
                                str(req.get("origin_req_id", "") or ""),
                                req.get("origin_version_id") or 0,
                                req.get("feature_id") or 0,
                                str(req.get("slice_id", "") or ""),
                                str(req.get("module", "") or ""),
                                str(req.get("description", "") or ""),
                                str(req.get("operation", "") or ""),
                                str(req.get("condition_expr", "") or ""),
                                str(req.get("expected_feedback", "") or ""),
                                str(req.get("source_quote", "") or ""),
                                str(req.get("_verify_status", "") or ""),
                                float(req.get("_evidence_confidence", 0) or 0),
                                self._to_bool_int(req.get("is_implicit", False)),
                                self._to_bool_int(req.get("is_generated", False)),
                                str(req.get("dependency_req_id", "") or ""),
                                str(req.get("review_status", "") or ""),
                                self._to_json(req),
                                self._to_bool_int(
                                    req.get("contains_sensitive_data", False)
                                ),
                                now,
                                now,
                            ),
                        )
                    c.commit()
                finally:
                    with guard("upsert_requirements: c.close()", log=_log, level=logging.WARNING):
                        c.close()
                if skipped:
                    self._last_error = "%d 条需求因缺少 req_id 未写入" % skipped
                    _log.warning("[Store] %s", self._last_error)
                    return False
                self._last_error = ""
                return True
        except Exception as e:
            self._last_error = str(e)
            _log.error("[Store] 需求写入失败: %s", e)
            _log.info("  [RequirementStore] requirements 写入失败: %s", str(e)[:120])
            return False

    def update_requirement(self, req_id: str, updates: dict[str, Any]) -> bool:
        """更新单条需求镜像。直接执行 SQL UPDATE，避免先查全表再全量写回的 N+1 模式。"""
        if not updates:
            return True
        req_id = str(req_id or "").strip()
        if not req_id:
            return False
        from src.core.config_loader import config

        project = config.project_name or ""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # 构建动态 SET 子句
        set_parts = []
        params = []
        col_map = {
            "module": "module",
            "description": "description",
            "operation": "operation",
            "condition_expr": "condition_expr",
            "expected_feedback": "expected_feedback",
            "feature_id": "feature_id",
            "slice_id": "slice_id",
            "is_implicit": "is_implicit",
            "is_generated": "is_generated",
            "dependency_req_id": "dependency_req_id",
            "review_status": "review_status",
            "version_id": "version_id",
            "stable_req_key": "stable_req_key",
            "source_anchor": "source_anchor",
            "content_hash": "content_hash",
            "semantic_hash": "semantic_hash",
            "change_type": "change_type",
            "origin_req_id": "origin_req_id",
            "origin_version_id": "origin_version_id",
        }
        for key, col in col_map.items():
            if key in updates:
                val = updates[key]
                if key in ("is_generated", "is_implicit"):
                    val = self._to_bool_int(val)
                elif key in ("version_id", "origin_version_id"):
                    val = val if val not in ("", None) else 0
                else:
                    val = str(val or "")
                set_parts.append(f"{col} = %s")
                params.append(val)

        if not set_parts:
            return True

        try:
            from src.core.versioning import current_version_id

            active_version_id = current_version_id(project)
        except Exception as _e:
            _log.debug("[_write] 非关键异常: %s", _e, exc_info=True)
            active_version_id = 0

        set_parts.append("updated_at = %s")
        params.append(now)
        params.append(req_id)
        params.append(project)
        params.append(active_version_id)

        sql = f"UPDATE requirements SET {', '.join(set_parts)} WHERE req_id = %s AND project = %s AND version_id = %s"

        c = None
        try:
            with self._lock:
                c = self._get_db()
                try:
                    self._ensure_tables(c)
                    affected = c.execute(sql, tuple(params)).rowcount
                    c.commit()
                finally:
                    with guard("update_requirement: c.close()", log=_log, level=logging.WARNING):
                        c.close()
                self._last_error = ""
                # ★ 修复：需求不存在时不再创建空壳行
                #   原行为：fallback 到 upsert_requirements 创建只有 req_id + updates 的空壳行
                #   新行为：记录警告并返回 False，需求应由 Step2 正常写入，不应由 update 路径创建
                if affected == 0:
                    _log.warning(
                        "[Store] update_requirement: 需求 %s 不存在 (project=%s, version_id=%s)，"
                        "拒绝创建空壳行。请检查 Step2 是否正常写入。",
                        req_id,
                        project,
                        active_version_id,
                    )
                    return False
                return True
        except Exception as e:
            self._last_error = str(e)
            _log.info("  [RequirementStore] update_requirement 失败: %s", str(e)[:120])
            return False

    def reset_all_is_generated(self) -> bool:
        """重置当前项目当前版本的生成状态。直接 SQL UPDATE，避免先查全表再全量写回。"""
        from src.core.config_loader import config

        project = config.project_name or ""
        from src.core.versioning import current_version_id

        active_version_id = current_version_id(project)
        c = None
        try:
            with self._lock:
                c = self._get_db()
                try:
                    self._ensure_tables(c)
                    c.execute(
                        "UPDATE requirements SET is_generated = 0 WHERE project = %s AND version_id = %s",
                        (project, active_version_id),
                    )
                    c.commit()
                finally:
                    with guard("reset_all_is_generated: c.close()", log=_log, level=logging.WARNING):
                        c.close()
                self._last_error = ""
                return True
        except Exception as e:
            self._last_error = str(e)
            _log.info(
                "  [RequirementStore] reset_all_is_generated 失败: %s", str(e)[:120]
            )
            return False

    def delete_requirement(self, req_id: str) -> bool:
        """删除当前项目当前版本单条需求镜像。"""
        from src.core.config_loader import config

        project = config.project_name or ""
        from src.core.versioning import current_version_id

        active_version_id = current_version_id(project)
        c = None
        try:
            with self._lock:
                c = self._get_db()
                try:
                    self._ensure_tables(c)
                    c.execute(
                        "DELETE FROM requirements WHERE req_id=%s AND project=%s AND version_id=%s",
                        (str(req_id), project, active_version_id),
                    )
                    c.commit()
                finally:
                    with guard("delete_requirement: c.close()", log=_log, level=logging.WARNING):
                        c.close()
                self._last_error = ""
                return True
        except Exception as e:
            self._last_error = str(e)
            _log.info("  [RequirementStore] requirements 删除失败: %s", str(e)[:120])
            return False
