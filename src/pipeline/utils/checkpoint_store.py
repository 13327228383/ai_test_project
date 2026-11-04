# -*- coding: utf-8 -*-
from __future__ import annotations
from src.core.guarded import guard

"""
流水线检查点存储 (CheckpointStore)
====================================

定位：把流水线每个 Step 的执行状态持久化到 MySQL ``pipeline_checkpoints`` 表，
支持断点续跑（resume）和运行追溯（run_id 贯穿）。

典型调用：
    from src.pipeline.utils.checkpoint_store import checkpoint_store

    # 记录检查点
    checkpoint_store.save_checkpoint(
        run_id="abc123", project=DEMO_PROJECT, step=1,
        status="success", error_msg=""
    )

    # 查询项目最近一次成功检查点
    last = checkpoint_store.get_last_checkpoint(project=DEMO_PROJECT)
    # 返回: {"run_id": "abc123", "step": 2, "status": "success", ...}
"""

from datetime import datetime
from typing import Any
import hashlib
import json
import logging

_log = logging.getLogger(__name__)


class CheckpointStore:
    """MySQL ``pipeline_checkpoints`` 表读写工具。"""

    def __init__(self, db_getter=None):
        self._last_error = ""
        self._db_getter = db_getter

    # -----------------------------------------------------------------
    @property
    def last_error(self) -> str:
        return self._last_error

    # -----------------------------------------------------------------
    # DB 连接
    # -----------------------------------------------------------------
    def _get_db(self):
        if self._db_getter:
            return self._db_getter()
        from src.core.db_manager import get_connection

        return get_connection()

    @staticmethod
    def _ensure_tables(c) -> None:
        """确保 pipeline_checkpoints 存在 version_id 列（存量库运行时 ALTER 兜底）。

        schema.sql 已含该列；对存量库缺列时补上，缺失才 ALTER（幂等）。
        """
        with guard("_ensure_tables: _cols = { r['COLUMN_NAME'] for r in ", log=_log, level=logging.WARNING):
            _cols = {
                r["COLUMN_NAME"]
                for r in c.execute(
                    "SELECT COLUMN_NAME FROM information_schema.columns "
                    "WHERE table_schema = DATABASE() AND table_name = 'pipeline_checkpoints'"
                ).fetchall()
            }
            if "version_id" not in _cols:
                c.execute(
                    "ALTER TABLE pipeline_checkpoints "
                    "ADD COLUMN version_id BIGINT NOT NULL DEFAULT 0"
                )
                c.commit()

    @staticmethod
    def _active_version(project: str = "") -> int:
        """当前项目 active version_id（0=legacy）。"""
        from src.core.versioning import current_version_id

        return int(current_version_id(project) or 0)

    # -----------------------------------------------------------------
    # 写：保存/更新检查点
    # -----------------------------------------------------------------
    def save_checkpoint(
        self, run_id: str, project: str, step: int, status: str, error_msg: str = ""
    ) -> bool:
        """保存或更新检查点记录。status: running / success / failed。"""
        if not run_id or not project:
            self._last_error = "run_id 和 project 不能为空"
            return False
        c = None  # ★ P0-04: 预初始化，防止 finally 块 NameError
        try:
            c = self._get_db()
            self._ensure_tables(c)
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            # ★ 计算完整性校验和（防止 DB 数据损坏不可感知）
            integrity_raw = json.dumps(
                {
                    "run_id": run_id,
                    "project": project,
                    "step": step,
                    "status": status,
                    "now": now,
                },
                sort_keys=True,
            )
            integrity = hashlib.sha256(integrity_raw.encode()).hexdigest()[:16]
            # ★ v5.0 修复: 用 INSERT ... ON DUPLICATE KEY UPDATE 替代 REPLACE INTO
            #   根因: REPLACE INTO 整行替换，status=success 时 started_at 被覆盖为 now，
            #         导致 started_at=completed_at，duration=0s。
            #   通用性: MySQL 标准 UPSERT 语法，适用于所有项目所有 step。
            #   修复: 首次 INSERT（running）写入 started_at；后续 UPDATE 只更新
            #         status/completed_at/error_msg/integrity，保留原 started_at。
            #   ★ v5.0.1 补丁: status=running 时重置 started_at（重新执行场景），
            #     status=success/failed 时保留原 started_at（正常完成场景）。
            c.execute(
                """
                INSERT INTO pipeline_checkpoints(
                    run_id, project, version_id, step, status, started_at, completed_at, error_msg, integrity
                ) VALUES(%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    status = VALUES(status),
                    started_at = IF(VALUES(status) = 'running', VALUES(started_at), started_at),
                    completed_at = VALUES(completed_at),
                    error_msg = VALUES(error_msg),
                    integrity = VALUES(integrity)
                """,
                (
                    run_id,
                    project,
                    self._active_version(project),
                    step,
                    status,
                    now,
                    now if status in ("success", "failed") else None,
                    error_msg or None,
                    integrity,
                ),
            )
            c.commit()
            self._last_error = ""
            return True
        except Exception as e:
            self._last_error = str(e)
            _log.info("  [CheckpointStore] 保存检查点失败: %s", str(e)[:120])
            return False
        finally:
            if c:
                with guard("save_checkpoint: c.close()", log=_log):
                    c.close()

    # -----------------------------------------------------------------
    # 读：查询指定 run 的所有检查点
    # -----------------------------------------------------------------
    def get_checkpoints(self, run_id: str) -> list:
        """返回某 run_id 的全部检查点，按 step 升序。"""
        c = None  # ★ P0-04: 预初始化，防止 finally 块 NameError
        try:
            c = self._get_db()
            self._ensure_tables(c)
            rows = c.execute(
                "SELECT * FROM pipeline_checkpoints WHERE run_id=%s "
                "AND version_id=%s ORDER BY step ASC",
                (run_id, self._active_version()),
            ).fetchall()
            return [dict(r) for r in rows] if rows else []
        except Exception as e:
            self._last_error = str(e)
            _log.info("  [CheckpointStore] 查询检查点失败: %s", str(e)[:120])
            return []

        finally:
            if c:
                with guard("get_checkpoints: c.close()", log=_log):
                    c.close()

    # -----------------------------------------------------------------
    # 读：查询项目最近一次成功检查点
    # -----------------------------------------------------------------
    def get_last_checkpoint(
        self, project: str, run_id: str = None
    ) -> dict[str, Any] | None:
        """返回某项目最近一次成功的检查点（用于 resume）。

        策略：返回步骤号最大的成功记录，确保 resume 时从最新的成功步骤继续。
        当 run_id 提供时，仅匹配该 run_id 的 checkpoint，避免跨 run 误恢复。
        """
        c = None  # ★ P0-04: 预初始化，防止 finally 块 NameError
        try:
            c = self._get_db()
            self._ensure_tables(c)
            # ★ 先找到该项目下最大的成功步骤号
            if run_id:
                row = c.execute(
                    """
                    SELECT * FROM pipeline_checkpoints
                    WHERE project=%s AND status='success' AND run_id=%s AND version_id=%s
                    ORDER BY step DESC, started_at DESC LIMIT 1
                    """,
                    (project, run_id, self._active_version(project)),
                ).fetchone()
            else:
                row = c.execute(
                    """
                    SELECT * FROM pipeline_checkpoints
                    WHERE project=%s AND status='success' AND version_id=%s
                    ORDER BY step DESC, started_at DESC LIMIT 1
                    """,
                    (project, self._active_version(project)),
                ).fetchone()
            if row is None:
                return None
            return (
                dict(row)
                if hasattr(row, "keys")
                else {
                    "run_id": row[0],
                    "project": row[1],
                    "step": row[2],
                    "status": row[3],
                    "started_at": row[4],
                    "completed_at": row[5],
                    "error_msg": row[6],
                }
            )
        except Exception as e:
            self._last_error = str(e)
            _log.info("  [CheckpointStore] 查询最后检查点失败: %s", str(e)[:120])
            return None

        finally:
            if c:
                with guard("get_last_checkpoint: c.close()", log=_log):
                    c.close()

    # -----------------------------------------------------------------
    # 读：查询某 run 当前已成功的最大 step
    # -----------------------------------------------------------------
    def get_last_successful_step(self, run_id: str) -> int:
        """返回某 run_id 已成功的最大 step 编号，无则返回 0。"""
        c = None  # ★ P0-04: 预初始化，防止 finally 块 NameError
        try:
            c = self._get_db()
            self._ensure_tables(c)
            row = c.execute(
                """
                SELECT MAX(step) AS max_step FROM pipeline_checkpoints
                WHERE run_id=%s AND status='success' AND version_id=%s
                """,
                (run_id, self._active_version()),
            ).fetchone()
            if row is None:
                return 0
            val = row["max_step"] if isinstance(row, dict) else (row[0] if row else 0)
            return int(val or 0)
        except Exception as e:
            self._last_error = str(e)
            return 0

        finally:
            if c:
                with guard("get_last_successful_step: c.close()", log=_log):
                    c.close()

    # -----------------------------------------------------------------
    # 清理：启动时清理僵尸检查点（status=running 但无活跃内存任务）
    # -----------------------------------------------------------------
    def cleanup_zombie_checkpoints(self) -> int:
        """★ 启动时自动清理僵尸检查点。

        根因: 后端崩溃/强制杀死时，pipeline_checkpoints 中 status=running
              的记录无法被更新为 success/failed，导致下次启动时流水线假阻塞。
              上次会话 (2026-07-27) 因僵尸检查点阻塞了约 30 分钟。

        策略: 后端启动时，所有 status=running 的检查点重置为 failed，
              因为此时内存中没有任何活跃任务（刚启动）。
              仅当后端在运行中（非启动阶段）调用此方法时，才检查活跃任务。

        通用性: 适用于所有项目、所有 step，不绑定业务术语。
        职责边界: 仅清理检查点状态，不删除数据（保留历史追溯）。

        返回: 清理的僵尸检查点数量。
        """
        c = None
        cleaned = 0
        try:
            from datetime import datetime

            c = self._get_db()
            self._ensure_tables(c)
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            # 查询所有 running 状态的检查点
            rows = c.execute(
                "SELECT run_id, project, step FROM pipeline_checkpoints WHERE status='running'"
            ).fetchall()
            if not rows:
                return 0
            zombie_runs = []
            for row in rows:
                _run_id = row["run_id"] if isinstance(row, dict) else row[0]
                _project = row["project"] if isinstance(row, dict) else row[1]
                # 检查是否有活跃内存任务
                _has_active = False
                with guard("cleanup_zombie_checkpoints: from src.backend.state import _is_ru", log=_log):
                    from src.backend.state import _is_running

                    _has_active = bool(_is_running(_project))
                if not _has_active:
                    zombie_runs.append((_run_id, _project))
            if not zombie_runs:
                return 0
            for _run_id, _project in zombie_runs:
                c.execute(
                    "UPDATE pipeline_checkpoints SET status='failed', "
                    "completed_at=%s, error_msg='zombie cleanup on startup' "
                    "WHERE run_id=%s AND status='running'",
                    (now, _run_id),
                )
                cleaned += 1  # 每个 run_id 对应一条 running 记录
            c.commit()
            if cleaned > 0:
                _log.info(
                    "[CheckpointStore] 僵尸检查点清理: %d 条 (run_ids=%s)",
                    cleaned,
                    ", ".join([z[0][:12] for z in zombie_runs]),
                )
        except Exception as e:
            self._last_error = str(e)
            _log.warning(
                "[CheckpointStore] 僵尸检查点清理异常(非阻断): %s", str(e)[:120]
            )
        finally:
            if c:
                with guard("cleanup_zombie_checkpoints: c.close()", log=_log):
                    c.close()
        return cleaned

    # -----------------------------------------------------------------
    # 清理：删除某 run 的所有检查点
    # -----------------------------------------------------------------
    def delete_run(self, run_id: str) -> bool:
        """删除某 run_id 的全部检查点。"""
        c = None  # ★ P0-04: 预初始化，避免 finally 块 NameError
        try:
            c = self._get_db()
            self._ensure_tables(c)
            c.execute("DELETE FROM pipeline_checkpoints WHERE run_id=%s", (run_id,))
            c.commit()
            return True
        except Exception as e:
            self._last_error = str(e)
            return False
        finally:
            if c:
                with guard("delete_run: c.close()", log=_log):
                    c.close()


# 单例
checkpoint_store = CheckpointStore()
