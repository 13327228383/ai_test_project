# -*- coding: utf-8 -*-
"""
测试用例→需求 映射追踪 (IntentRequirementTracker)
====================================
MySQL 持久化追踪 case_id → req_id 映射，替代旧的 intent_requirement_map.json。

表: case_requirement_mapping (case_id, req_id, project)
级联: 删除测试用例时同步清理 case_meta + case_requirement_mapping。
"""

import threading
import logging
from collections import Counter

_log = logging.getLogger(__name__)

# ── 常量 ──
_ERR_MSG_TRUNCATE_LEN = 100  # 异常信息截断长度，避免日志过长

# ★ 性能优化：DDL 只跑一次（首个连接触发），后续请求直接跳过
_intent_req_mapping_tables_ensured = False


class IntentRequirementTracker:
    """测试用例→需求映射的 MySQL 持久化追踪器。"""

    def __init__(self):
        self._lock = threading.Lock()
        self._last_error = ""

    # ── DB 连接 ──
    @staticmethod
    def _get_db():
        from src.core.db_manager import get_connection

        return get_connection()

    @staticmethod
    def _ensure_tables(c) -> None:
        """建表已统一由 src/core/schema.sql 负责，此处仅作占位。
        ★ 2026-06-22 改造：schema.sql 是 case_requirement_mapping / case_meta 的唯一事实源。
        - 删除原 CREATE TABLE case_requirement_mapping / case_meta
        - 删除原 ALTER TABLE 兼容补列逻辑
        本函数保留签名以兼容调用方 `self._ensure_tables(c)`。
        """
        global _intent_req_mapping_tables_ensured
        if _intent_req_mapping_tables_ensured:
            return
        _intent_req_mapping_tables_ensured = True
        return

    # ── 写操作 ──

    def add(self, case_id: str, req_id: str) -> bool:
        """添加一条映射。成功返回 True，失败返回 False。"""
        from src.core.config_loader import config

        project = config.project_name or ""
        from src.core.versioning import current_version_id

        active_version_id = current_version_id(project)
        with self._lock:
            c = None
            try:
                c = self._get_db()
                self._ensure_tables(c)
                c.execute(
                    "REPLACE INTO case_requirement_mapping(case_id, req_id, project, version_id, req_version_id, case_version_id, mapping_source) "
                    "VALUES(%s,%s,%s,%s,%s,%s,%s)",
                    (
                        str(case_id),
                        str(req_id),
                        project,
                        active_version_id or 0,
                        active_version_id or 0,
                        active_version_id or 0,
                        "generated",
                    ),
                )
                c.commit()
                return True
            except Exception as e:
                _log.info(
                    "  [IntentRequirementTracker] 映射写入失败: %s → %s (%s)"
                    % (case_id, req_id, str(e)[:_ERR_MSG_TRUNCATE_LEN])
                )
                return False
            finally:
                try:
                    if c:
                        c.close()
                except Exception as e:
                    _log.warning(
                        "  [IntentRequirementTracker] 关闭连接失败(add): %s",
                        str(e)[:_ERR_MSG_TRUNCATE_LEN],
                    )

    def remove_intent(self, case_id: str) -> bool:
        """删除一条映射 + 级联清理 case_meta。成功返回 True，失败返回 False。"""
        from src.core.config_loader import config

        project = config.project_name or ""
        from src.core.versioning import current_version_id

        active_version_id = current_version_id(project)
        with self._lock:
            c = None
            try:
                c = self._get_db()
                self._ensure_tables(c)
                c.execute(
                    "DELETE FROM case_requirement_mapping WHERE case_id=%s AND project=%s AND version_id=%s",
                    (str(case_id), project, active_version_id),
                )
                c.execute(
                    "DELETE FROM case_meta WHERE case_id=%s AND project=%s AND version_id=%s",
                    (str(case_id), project, active_version_id),
                )
                c.commit()
                return True
            except Exception as e:
                _log.info(
                    "  [IntentRequirementTracker] 映射删除失败: %s (%s)"
                    % (case_id, str(e)[:_ERR_MSG_TRUNCATE_LEN])
                )
                return False
            finally:
                try:
                    if c:
                        c.close()
                except Exception as e:
                    _log.warning(
                        "  [IntentRequirementTracker] 关闭连接失败(remove_intent): %s",
                        str(e)[:_ERR_MSG_TRUNCATE_LEN],
                    )

    def save(self):
        """MySQL 已实时写入，此方法保留兼容（空操作）。"""
        pass

    def clear(self) -> bool:
        """清空当前项目的所有映射（Excel 测试用例文件丢失时调用）。"""
        from src.core.config_loader import config

        project = config.project_name or ""
        from src.core.versioning import current_version_id

        active_version_id = current_version_id(project)
        with self._lock:
            c = None
            try:
                c = self._get_db()
                self._ensure_tables(c)
                count = c.execute(
                    "SELECT COUNT(*) as n FROM case_requirement_mapping WHERE project=%s AND version_id=%s",
                    (project, active_version_id),
                ).fetchone()
                n = count["n"] if count else 0
                c.execute(
                    "DELETE FROM case_requirement_mapping WHERE project=%s AND version_id=%s",
                    (project, active_version_id),
                )
                c.commit()
                if n > 0:
                    _log.info(
                        "  [IntentRequirementTracker] 清空映射: %d 条记录 → 测试用例文件已丢失，追踪器重置",
                        n,
                    )
                return True
            except Exception as e:
                _log.info(
                    "  [IntentRequirementTracker] 清空映射失败: %s",
                    str(e)[:_ERR_MSG_TRUNCATE_LEN],
                )
                return False
            finally:
                try:
                    if c:
                        c.close()
                except Exception as e:
                    _log.warning(
                        "  [IntentRequirementTracker] 关闭连接失败(clear): %s",
                        str(e)[:_ERR_MSG_TRUNCATE_LEN],
                    )

    # ── 读操作 ──

    def _load_map(self) -> dict:
        """从 MySQL 加载当前项目的 intent→req Map。"""
        from src.core.config_loader import config

        project = config.project_name or ""
        from src.core.versioning import current_version_id

        active_version_id = current_version_id(project)
        c = None
        try:
            c = self._get_db()
            self._ensure_tables(c)
            rows = c.execute(
                "SELECT case_id, req_id FROM case_requirement_mapping "
                "WHERE project=%s AND version_id=%s",
                (project, active_version_id),
            ).fetchall()
            self._last_error = ""
            return {row["case_id"]: row["req_id"] for row in rows}
        except Exception as e:
            self._last_error = str(e)
            _log.info(
                "  [IntentRequirementTracker] 映射读取失败: %s",
                str(e)[:_ERR_MSG_TRUNCATE_LEN],
            )
            return {}
        finally:
            try:
                if c:
                    c.close()
            except Exception as e:
                _log.warning(
                    "  [IntentRequirementTracker] 关闭连接失败(_load_map): %s",
                    str(e)[:_ERR_MSG_TRUNCATE_LEN],
                )

    def get_req_id(self, case_id: str) -> str:
        """查询测试用例对应的需求ID。"""
        m = self._load_map()
        return m.get(str(case_id), "")

    def get_intents_for_req(self, req_id: str) -> list:
        """获取某个需求的所有测试用例ID。"""
        m = self._load_map()
        return sorted(cid for cid, rid in m.items() if rid == req_id)

    def count_by_req(self) -> dict:
        """返回 {req_id: intent_count} 统计。

        除当前版本物理映射外，还把 unchanged 需求从历史版本虚拟继承的测试用例计入覆盖数。
        """
        m = self._load_map()
        counts = Counter()
        for rid in m.values():
            counts[rid] += 1
        c = None
        try:
            from src.core.config_loader import config
            from src.core.versioning import current_version_id

            project = config.project_name or ""
            active_version_id = current_version_id(project)
            c = self._get_db()
            self._ensure_tables(c)
            rows = c.execute(
                """
                SELECT r.req_id, COUNT(tc.case_id) AS n
                FROM requirements r
                JOIN test_cases tc
                  ON tc.project = r.project
                 AND tc.version_id = r.origin_version_id
                 AND tc.req_id = r.origin_req_id
                WHERE r.project=%s
                  AND r.version_id=%s
                  AND r.change_type='unchanged'
                  AND r.origin_version_id > 0
                  AND tc.valid_status <> 'retired'
                  AND NOT EXISTS (
                      SELECT 1 FROM test_cases cur
                      WHERE cur.project=r.project
                        AND cur.version_id=r.version_id
                        AND cur.case_id=tc.case_id
                  )
                GROUP BY r.req_id
                """,
                (project, active_version_id),
            ).fetchall()
            for row in rows or []:
                rid = row["req_id"]
                counts[rid] = counts.get(rid, 0) + int(row["n"] or 0)
        except Exception as e:
            _log.warning(
                "  [IntentRequirementTracker] 虚拟继承覆盖统计失败: %s",
                str(e)[:_ERR_MSG_TRUNCATE_LEN],
            )
        finally:
            if c:
                try:
                    c.close()
                except Exception as _e:
                    _log.debug("conn.close 失败: %s", _e, exc_info=True)
        return counts

    def __len__(self):
        """返回映射条目数。"""
        return len(self._load_map())


# 单例
case_requirement_tracker = IntentRequirementTracker()
