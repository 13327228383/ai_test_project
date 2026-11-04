# -*- coding: utf-8 -*-
"""
★ KEEP-27: CleanupManager — 统一的资源清理管理器。
====================================================
将分散在 log_persistence、db_agent、file_cleanup 的清理逻辑
统一抽取为可复用的管理器类。

职责：
  1. 日志清理：按 run_id 聚合，仅保留最近 N 个 run 的日志
  2. 数据库清理：基于外键依赖自动推导清理顺序
  3. 临时文件清理：清理过期的临时文件目录
"""

import logging
from src.core.guarded import guard
import os
import time
from pathlib import Path
from src.core.known_tables import KNOWN_LOGICAL_TABLES as _logical_tables  # noqa: F401  词表唯一源

_log = logging.getLogger("cleanup_manager")


class CleanupManager:
    """统一资源清理管理器。"""

    def __init__(self):
        self._last_cleanup_ts: float = 0.0

    # ═══════════════════════════════════════════════════════════════
    # 日志清理
    # ═══════════════════════════════════════════════════════════════

    @staticmethod
    def _expired_run_ids(table: str, keep_runs: int) -> list:
        """获取超过 keep_runs 个 run 的过期 run_id 列表。"""
        try:
            from src.core.db_manager import get_connection

            conn = get_connection()
            try:
                # ★ 使用子查询代替 LIMIT 魔数，可读性和性能都更好
                cur = conn.execute(
                    f"SELECT DISTINCT run_id FROM {table} WHERE run_id NOT IN ("
                    f"  SELECT DISTINCT run_id FROM ("
                    f"    SELECT DISTINCT run_id FROM {table} ORDER BY MAX(id) DESC LIMIT %s"
                    f"  ) AS recent"
                    f")",
                    (int(keep_runs),),
                )
                rows = cur.fetchall() if cur else []
                return [r["run_id"] for r in rows if r and r.get("run_id")]
            finally:
                conn.close()
        except Exception as e:
            _log.debug("[CleanupManager] 过期 run_id 查询跳过: %s", str(e)[:120])
            return []

    def cleanup_logs(self, keep_runs: int = 20) -> int:
        """按 run_id 聚合，仅保留最近 `keep_runs` 个 run 的日志。
        返回删除的行数。"""
        deleted = 0
        expired = self._expired_run_ids("run_logs", keep_runs)
        if not expired:
            return 0
        with guard("cleanup_logs: from src.core.db_manager import get_", log=_log, level=logging.WARNING):
            from src.core.db_manager import get_connection

            conn = get_connection()
            try:
                for sub in [expired[i : i + 500] for i in range(0, len(expired), 500)]:
                    placeholders = ",".join(["%s"] * len(sub))
                    conn.execute(
                        "DELETE FROM run_logs WHERE run_id IN (%s)" % placeholders, sub
                    )
                    conn.commit()
                    deleted += len(sub)
                _log.info("[CleanupManager] 清理 %d 个旧 run 的日志", deleted)
            finally:
                conn.close()
        return deleted

    # ═══════════════════════════════════════════════════════════════
    # 临时文件清理
    # ═══════════════════════════════════════════════════════════════

    @staticmethod
    def cleanup_temp_files(temp_dir: str = None, max_age_hours: int = 24) -> int:
        """清理超过 `max_age_hours` 的临时文件。返回删除的文件数。

        ★ 默认清理系统 tempfile 目录（tempfile.gettempdir()），非项目 data/ 残留。
        Playwright/导出等瞬态 tempfile 用完即删，此处仅兜底清理漏删的过期文件。
        """
        deleted = 0
        with guard("cleanup_temp_files: import tempfile", log=_log):
            import tempfile

            base = Path(temp_dir) if temp_dir else Path(tempfile.gettempdir())
            if not base.exists():
                return 0
            cutoff = time.time() - max_age_hours * 3600
            for fpath in base.rglob("*"):
                if fpath.is_file() and fpath.stat().st_mtime < cutoff:
                    try:
                        fpath.unlink()
                        deleted += 1
                    except OSError as _e:
                        _log.debug(
                            "[cleanup_manager] 删除临时文件异常: %s", _e, exc_info=True
                        )
            if deleted:
                _log.info("[CleanupManager] 清理 %d 个临时文件", deleted)
        return deleted

    # ═══════════════════════════════════════════════════════════════
    # 健康指标清理
    # ═══════════════════════════════════════════════════════════════

    def cleanup_old_metrics(self, keep_runs: int = 50) -> int:
        """清理 ai_health_metrics 表中超过 `keep_runs` 个 run 的旧数据。"""
        deleted = 0
        expired = self._expired_run_ids("ai_health_metrics", keep_runs)
        if not expired:
            return 0
        with guard("cleanup_old_metrics: from src.core.db_manager import get_", log=_log, level=logging.WARNING):
            from src.core.db_manager import get_connection

            conn = get_connection()
            try:
                for sub in [expired[i : i + 500] for i in range(0, len(expired), 500)]:
                    placeholders = ",".join(["%s"] * len(sub))
                    conn.execute(
                        "DELETE FROM ai_health_metrics WHERE run_id IN (%s)"
                        % placeholders,
                        sub,
                    )
                    conn.commit()
                    deleted += len(sub)
                _log.info("[CleanupManager] 清理 %d 个旧 run 的健康指标", deleted)
            finally:
                conn.close()
        return deleted

    def cleanup_all(
        self, keep_runs: int = 20, temp_dir: str = None, max_age_hours: int = 24
    ) -> dict[str, int]:
        """一键清理所有过期的日志、指标和临时文件。"""
        self._last_cleanup_ts = time.time()
        result = {
            "logs_deleted": self.cleanup_logs(keep_runs),
            "logs_time_deleted": self.cleanup_old_run_logs(),
            "screenshots_deleted": self.cleanup_old_screenshots(),
            "metrics_deleted": self.cleanup_old_metrics(keep_runs),
            "temp_files_deleted": self.cleanup_temp_files(temp_dir, max_age_hours),
            "figma_images_imported": self.cleanup_figma_images(),
            "old_versions_deleted": self.cleanup_old_versions(),
        }
        return result

    # ═══════════════════════════════════════════════════════════════
    # ★ 执行截图 6 个月清理（schema 注释承诺）
    # ═══════════════════════════════════════════════════════════════

    @staticmethod
    def cleanup_old_screenshots(retention_days: int = 183) -> int:
        """删除超过 retention_days 天的 execution_screenshots 记录。
        默认 183 天 ≈ 6 个月。返回删除的记录数。"""
        deleted = 0
        conn = None
        try:
            from src.core.db_manager import get_connection

            conn = get_connection()
            if not conn:
                return 0
            # 检查表是否存在
            col_check = conn.execute(
                "SELECT COUNT(*) as cnt FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'execution_screenshots' "
                "AND COLUMN_NAME = 'created_at'"
            ).fetchone()
            if not col_check or col_check["cnt"] == 0:
                return 0
            result = conn.execute(
                "DELETE FROM execution_screenshots WHERE created_at < NOW() - INTERVAL %s DAY",
                (retention_days,),
            )
            conn.commit()
            deleted = result.rowcount if hasattr(result, "rowcount") else 0
            if deleted:
                _log.info(
                    "[CleanupManager] execution_screenshots: 删除 %d 条超过 %d 天的截图",
                    deleted,
                    retention_days,
                )
        except Exception as e:
            _log.debug("[CleanupManager] 截图清理跳过: %s", str(e)[:80])
        finally:
            if conn:
                try:
                    conn.close()
                except Exception as _e:
                    _log.debug("conn.close 失败: %s", _e, exc_info=True)
        return deleted

    @staticmethod
    def cleanup_old_run_logs(retention_days: int = 183) -> int:
        """删除超过 retention_days 天的 run_logs 记录（时间维度兜底）。
        默认 183 天 ≈ 6 个月。返回删除的记录数。"""
        deleted = 0
        conn = None
        try:
            from src.core.db_manager import get_connection

            conn = get_connection()
            if not conn:
                return 0
            col_check = conn.execute(
                "SELECT COUNT(*) as cnt FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'run_logs' "
                "AND COLUMN_NAME = 'created_at'"
            ).fetchone()
            if not col_check or col_check["cnt"] == 0:
                return 0
            result = conn.execute(
                "DELETE FROM run_logs WHERE created_at < NOW() - INTERVAL %s DAY",
                (retention_days,),
            )
            conn.commit()
            deleted = result.rowcount if hasattr(result, "rowcount") else 0
            if deleted:
                _log.info(
                    "[CleanupManager] run_logs: 删除 %d 条超过 %d 天的日志（时间维度）",
                    deleted,
                    retention_days,
                )
        except Exception as e:
            _log.debug("[CleanupManager] 日志时间清理跳过: %s", str(e)[:80])
        finally:
            if conn:
                try:
                    conn.close()
                except Exception as _e:
                    _log.debug("conn.close 失败: %s", _e, exc_info=True)
        return deleted

    # ═══════════════════════════════════════════════════════════════
    # ★ PV-010: 30天物理删除
    # ═══════════════════════════════════════════════════════════════

    @staticmethod
    def cleanup_old_versions(retention_days: int = 30) -> int:
        """
        ★ PV-010: 物理删除超过 retention_days 天的旧版本数据。

        清理范围：
        - source_documents: 超过保留期的版本记录
        - run_logs: 超过保留期的执行日志
        - execution_results: 超过保留期的执行结果

        返回删除的记录数。
        """
        total_deleted = 0
        conn = None
        try:
            from src.core.db_manager import get_connection

            conn = get_connection()
            if not conn:
                _log.warning(
                    "[CleanupManager] cleanup_old_versions: 无法获取数据库连接"
                )
                return 0

            tables = ["source_documents", "run_logs", "execution_results"]
            for table in tables:
                try:
                    # 检查表是否有 created_at 列
                    col_check = conn.execute(
                        "SELECT COUNT(*) as cnt FROM information_schema.COLUMNS "
                        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND COLUMN_NAME = 'created_at'",
                        (table,),
                    ).fetchone()
                    if not col_check or col_check["cnt"] == 0:
                        continue

                    result = conn.execute(
                        f"DELETE FROM {table} WHERE created_at < NOW() - INTERVAL %s DAY",
                        (retention_days,),
                    )
                    deleted = result.rowcount if hasattr(result, "rowcount") else 0
                    if deleted > 0:
                        total_deleted += deleted
                        _log.info(
                            "[CleanupManager] %s: 删除 %d 条超过 %d 天的记录",
                            table,
                            deleted,
                            retention_days,
                        )
                except Exception as _te:
                    _log.debug(
                        "[CleanupManager] %s 清理跳过(表可能不存在): %s",
                        table,
                        str(_te)[:80],
                    )

            # 同时清理 diff_snapshots
            try:
                from src.pipeline.utils.diff_snapshot_store import diff_snapshot_store

                removed = diff_snapshot_store.cleanup_old_versions(keep_count=10)
                if removed > 0:
                    total_deleted += removed
                    _log.info(
                        "[CleanupManager] diff_snapshots: 删除 %d 条旧版本", removed
                    )
            except Exception as _de:
                _log.debug(
                    "[CleanupManager] diff_snapshots 清理跳过: %s", str(_de)[:80]
                )

            # ★ v6.0(2026-08-12) 新增: 物理清理已软删除超过 retention_days 的 project_versions 记录
            try:
                # 获取待物理删除的版本 ID 列表
                stale_versions = conn.execute(
                    "SELECT id FROM project_versions "
                    "WHERE status='deleted' AND deleted_at < NOW() - INTERVAL %s DAY",
                    (retention_days,),
                ).fetchall()
                if stale_versions:
                    stale_ids = [r["id"] for r in stale_versions]
                    for table in _logical_tables:
                        try:
                            placeholders = ",".join(["%s"] * len(stale_ids))
                            conn.execute(
                                f"DELETE FROM {table} WHERE version_id IN ({placeholders})",
                                stale_ids,
                            )
                        except Exception as _e:
                            _log.debug("cleanup_old_versions best-effort 容忍: %s", str(_e)[:120])
                    # 物理删除版本记录（FK 关联表通过 CASCADE 自动清理）
                    result = conn.execute(
                        "DELETE FROM project_versions "
                        "WHERE status='deleted' AND deleted_at < NOW() - INTERVAL %s DAY",
                        (retention_days,),
                    )
                    pv_deleted = result.rowcount if hasattr(result, "rowcount") else 0
                    if pv_deleted > 0:
                        total_deleted += pv_deleted
                        _log.info(
                            "[CleanupManager] project_versions: 物理删除 %d 条超 %d 天的已软删除版本",
                            pv_deleted,
                            retention_days,
                        )
            except Exception as _pve:
                _log.debug(
                    "[CleanupManager] project_versions 清理跳过: %s", str(_pve)[:80]
                )

        except Exception as e:
            _log.warning("[CleanupManager] cleanup_old_versions 异常: %s", str(e)[:80])
        finally:
            if conn:
                try:
                    conn.close()
                except Exception as _ce:
                    _log.debug("[CleanupManager] 关闭连接失败: %s", _ce)

        return total_deleted

    # ═══════════════════════════════════════════════════════════════
    # ★ PV-011: 物理删除前 7 天通知
    # ═══════════════════════════════════════════════════════════════

    # ═══════════════════════════════════════════════════════════════
    # Figma 图片导入（全走MySQL）
    # ═══════════════════════════════════════════════════════════════

    @staticmethod
    def cleanup_figma_images() -> int:
        """将 MCP download_figma_images 下载的图片导入 MySQL 并清理磁盘。"""
        try:
            from src.pipeline.utils.figma_image_store import import_figma_images

            result = import_figma_images(project="", version_label="")
            return result.get("imported", 0)
        except Exception as e:
            _log.debug("[CleanupManager] Figma 图片导入跳过: %s", e)
            return 0


# ★ 方案C 改造：cleanup_manager 改为 PerProjectRegistry，按 project 隔离
#   原实现：全局单例，清理状态跨项目混合
from src.core.utils.per_project_registry import PerProjectRegistry

cleanup_manager = PerProjectRegistry(CleanupManager)
