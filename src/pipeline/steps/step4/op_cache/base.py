# -*- coding: utf-8 -*-
"""
OperationCache 基础工具 Mixin
==============================
提供数据库连接、建表、缓存键构建等通用方法。
"""

import hashlib
import threading
import time
import logging

_log = logging.getLogger(__name__)


class _OpCacheBaseMixin:
    """OperationCache 基础工具。"""

    _TABLE = "step4_op_cache"
    _MAX_AGE_SECONDS = 30 * 86400

    @property
    def _max_capacity(self) -> int:
        """★ P0-11: LRU 容量从 config.op_cache.max_capacity / pipeline_config.op_cache_max_capacity 读取(默认 5000)。"""
        try:
            from src.core.config_loader import config as _cfg

            v = int(_cfg._get("OP_CACHE_MAX_CAPACITY", 5000) or 5000)
            return max(100, min(100000, v))
        except Exception:
            try:
                from src.core.pipeline_config import pipeline_config

                return max(100, min(100000, int(pipeline_config.op_cache_max_capacity)))
            except Exception as _e:
                _log.debug("[base] _max_capacity 异常: %s", _e, exc_info=True)
                return 5000

    def __init__(self):
        self._lock = threading.Lock()
        self._stats: dict[str, int] = {"hit": 0, "miss": 0}
        self._stats_lock = threading.Lock()
        self._memory_cache: dict[str, tuple] = {}
        self._ensure_table()

    def _get_conn(self):
        """获取数据库连接。"""
        from src.models.base import get_conn

        return get_conn()

    def _ensure_table(self):
        """建表（幂等）。schema.sql v3.0 已统一管理 DDL，此处仅做兜底。"""
        conn = None
        try:
            conn = self._get_conn()
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS %s (
                    cache_key       VARCHAR(64) PRIMARY KEY COMMENT '缓存键（MD5哈希）',
                    project         VARCHAR(128) NOT NULL COMMENT '项目名 → projects.name',
                    version_id      BIGINT NOT NULL DEFAULT 0 COMMENT '项目版本ID → project_versions.id，0=跨版本复用',
                    goal_normalized TEXT NOT NULL COMMENT '归一化的操作目标',
                    page_path       TEXT NOT NULL COMMENT '页面路径（netloc + path）',
                    dom_fingerprint TEXT NOT NULL COMMENT 'DOM结构指纹',
                    config_hash     VARCHAR(16) NOT NULL COMMENT '配置哈希',
                    actions_json    LONGTEXT NOT NULL COMMENT 'AI返回的操作指令JSON',
                    hit_count       INT NOT NULL DEFAULT 0 COMMENT '命中次数',
                    success_count   INT NOT NULL DEFAULT 0 COMMENT '成功执行次数',
                    created_at      DOUBLE NOT NULL COMMENT '创建时间戳（Unix秒）',
                    last_hit_at     DOUBLE NULL COMMENT '最后命中时间戳（Unix秒）',
                    INDEX idx_%s_project (project),
                    INDEX idx_%s_config (config_hash)
                ) COMMENT='Step4 操作指令语义缓存表 — 基于页面DOM指纹+步骤目标的语义级缓存'
            """
                % (self._TABLE, self._TABLE, self._TABLE)
            )
            conn.commit()
            # ★ 存量库运行时 ALTER 补列（幂等；缺失才 ALTER，参考 browser_evidence.ensure_screenshot_table 模式）
            _rows = conn.execute(
                "SELECT COLUMN_NAME FROM information_schema.columns "
                "WHERE table_schema = DATABASE() AND table_name = %s",
                (self._TABLE,),
            ).fetchall()
            if "version_id" not in {r["COLUMN_NAME"] for r in (_rows or [])}:
                conn.execute(
                    "ALTER TABLE `%s` ADD COLUMN version_id BIGINT NOT NULL DEFAULT 0"
                    % self._TABLE
                )
                conn.commit()
        except Exception as e:
            _log.info("  [OpCache] 建表失败: %s", e)
        finally:
            if conn:
                try:
                    conn.close()
                except Exception as _pass_e:
                    _log.warning("base.py:83 异常被吞: %s", _pass_e, exc_info=True)

    def _make_key(
        self,
        goal: str,
        page_url: str,
        compact_state: list[dict],
        config_hash: str = None,
    ) -> str:
        """构建缓存 key。"""
        from urllib.parse import urlparse
        from src.pipeline.steps.step4.op_cache import (
            normalize_goal,
            compute_semantic_fingerprint,
            compute_config_hash,
        )

        ng = normalize_goal(goal)[:120]
        parsed = urlparse(page_url or "")
        page_path = parsed.netloc + parsed.path
        fp = compute_semantic_fingerprint(compact_state)
        ch = config_hash or compute_config_hash()
        raw = "%s|%s|%s|%s" % (ng, page_path, fp, ch)
        return hashlib.md5(raw.encode("utf-8")).hexdigest()

    def _touch_hit(self, key: str, project: str = None):
        """更新缓存命中计数和时间，并推进 LRU 访问顺序。"""
        from src.pipeline.steps.step4.op_cache import _get_project_name

        project = project or _get_project_name()
        conn = None
        try:
            conn = self._get_conn()
            conn.execute(
                "UPDATE %s SET hit_count = hit_count + 1, last_hit_at = %%s "
                "WHERE cache_key = %%s AND project = %%s" % self._TABLE,
                (time.time(), key, project),
            )
            conn.commit()
            conn.close()
            conn = None
        except Exception as _pass_e:
            _log.warning("base.py:113 异常被吞: %s", _pass_e, exc_info=True)
        finally:
            if conn:
                try:
                    conn.close()
                except Exception as _pass_e:
                    _log.warning("base.py:117 异常被吞: %s", _pass_e, exc_info=True)

    def _delete_key_async(self, key: str, project: str = None):
        """异步删除一条缓存（小型操作，直接同步执行）。"""
        from src.pipeline.steps.step4.op_cache import _get_project_name

        project = project or _get_project_name()
        conn = None
        try:
            conn = self._get_conn()
            conn.execute(
                "DELETE FROM %s WHERE cache_key = %%s AND project = %%s" % self._TABLE,
                (key, project),
            )
            conn.commit()
            conn.close()
            conn = None
        except Exception as _pass_e:
            _log.warning("base.py:132 异常被吞: %s", _pass_e, exc_info=True)
        finally:
            if conn:
                try:
                    conn.close()
                except Exception as _pass_e:
                    _log.warning("base.py:136 异常被吞: %s", _pass_e, exc_info=True)
