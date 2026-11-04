# -*- coding: utf-8 -*-
from __future__ import annotations
from src.core.guarded import guard

"""SystemDataResolver — 缓存管理（MySQL diff_snapshots）。"""

import json
import time
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from .constants import ResolverConstants

_log = logging.getLogger(__name__)


class CacheMixin(ResolverConstants):
    """缓存读写、导入/导出、指纹校验。"""

    def _load_cache(self) -> dict[str, Any] | None:
        """从 diff_snapshots(snapshot_type='db_schema') 加载。磁盘 JSON 不再作为运行时事实源。"""
        try:
            from src.pipeline.utils.diff_snapshot_store import diff_snapshot_store

            data = diff_snapshot_store.load_snapshot(
                "db_schema", project=self._project_name or None
            )
            if not isinstance(data, dict):
                return None
            age_h = (time.time() - data.get("cached_at", 0)) / 3600
            if age_h > self.CACHE_MAX_AGE_HOURS:
                _log.info("[SystemDataResolver] MySQL 缓存已过期 (%.1fh)", age_h)
                return None
            return data
        except Exception as e:
            _log.info("[SystemDataResolver] MySQL 缓存读取失败: %s", e)
            return None

    def _save_cache(self, catalog: dict[str, Any]) -> None:
        """写入 diff_snapshots(snapshot_type='db_schema')。磁盘 JSON 仅保留显式导出能力。"""
        with guard("_save_cache: catalog['cached_at'] = time.time()", log=_log, level=logging.INFO):
            catalog["cached_at"] = time.time()
            catalog["cached_at_str"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            catalog["project"] = self._project_name
            from src.pipeline.utils.diff_snapshot_store import diff_snapshot_store

            version_label = "db_schema_%s" % datetime.now().strftime("%Y%m%d_%H%M%S")
            saved = diff_snapshot_store.save_snapshot(
                "db_schema",
                catalog,
                version_label=version_label,
                project=self._project_name or None,
            )
            if saved:
                _log.info("[SystemDataResolver] 已缓存（MySQL db_schema 快照）")
            else:
                _log.error(
                    "[SystemDataResolver] MySQL 缓存写入失败: %s"
                    % (getattr(diff_snapshot_store, "_last_error", "") or "unknown")
                )

    # ── 显式导入 / 导出（磁盘 JSON 仅作为迁移/备份附件，不参与主链路）──
    def export_to_json(self, save_path: str) -> dict[str, Any]:
        """把 MySQL 中的 db_schema 快照导出为 JSON 文件（显式附件）。

        ★ 仅 CLI/迁移场景调用（落盘到 save_path）；Web 路由不调用此方法。
        主链路 _save_cache/_load_cache 已走 MySQL diff_snapshots，本方法为附件级 IO。
        """
        try:
            from src.pipeline.utils.diff_snapshot_store import diff_snapshot_store

            data = diff_snapshot_store.load_snapshot(
                "db_schema", project=self._project_name or None
            )
            if data is None:
                return {
                    "success": False,
                    "error": "MySQL 中无 db_schema 快照",
                    "path": str(save_path),
                }
            target = Path(save_path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            return {"success": True, "path": str(save_path)}
        except Exception as e:
            return {"success": False, "error": str(e), "path": str(save_path)}

    def import_from_json(self, json_path: str) -> dict[str, Any]:
        """从磁盘 JSON 文件回填到 MySQL diff_snapshots(db_schema)（显式迁移）。"""
        try:
            from src.pipeline.utils.diff_snapshot_store import diff_snapshot_store

            src_path = Path(json_path)
            if not src_path.is_file():
                return {
                    "success": False,
                    "error": "JSON 文件不存在",
                    "path": str(json_path),
                }
            data = json.loads(src_path.read_text(encoding="utf-8"))
            version_label = "imported_%s" % datetime.now().strftime("%Y%m%d_%H%M%S")
            saved = diff_snapshot_store.save_snapshot(
                "db_schema",
                data,
                version_label=version_label,
                project=self._project_name or None,
            )
            if saved is None:
                return {
                    "success": False,
                    "error": "写入 MySQL 失败",
                    "path": str(json_path),
                }
            return {"success": True, "path": str(json_path), "version_label": saved}
        except Exception as e:
            return {"success": False, "error": str(e), "path": str(json_path)}

    def _cache_matches_db(self, cached: dict) -> bool:
        """检查缓存的 DB 连接参数 + 表结构哈希是否与当前配置一致。"""
        try:
            from src.core.config_loader import config

            fp = cached.get("db_fingerprint", {})
            if not (
                fp.get("db_type") == config.db_type
                and fp.get("db_host") == config.db_host
                and fp.get("db_name") == config.db_name
            ):
                return False
            cached_hash = cached.get("schema_hash", "")
            if not cached_hash:
                return True
            db_agent = self._connect_db_for_hash()
            if not db_agent:
                return True
            try:
                current_hash = self._compute_schema_hash(
                    db_agent, config.db_type.lower()
                )
                return cached_hash == current_hash
            finally:
                try:
                    db_agent.close()
                except Exception as _e:
                    _log.debug("cache + close(): %s", _e, exc_info=True)
        except Exception as e:
            _log.info(
                "[SystemDataResolver] 缓存匹配校验异常，触发重新发现: %s", str(e)[:100]
            )
            return False

    def _connect_db_for_hash(self):
        """轻量连接（仅用检查哈希，不触发完整发现流程）。"""
        try:
            from src.extensions.db.db_agent import DatabaseAgent

            agent = DatabaseAgent()
            agent.connect()
            return agent
        except Exception as _e:
            _log.debug("轻量 DB 连接失败: %s", _e, exc_info=True)
            return None

    def _compute_schema_hash(self, db_agent, db_type: str) -> str:
        """计算表结构哈希（表名列表 + 每表行数，感知表增删和数据量变化）。"""
        import hashlib

        result = self._query_table_list(db_agent, db_type)
        if not result.get("success") or not result.get("data"):
            return ""
        tables = sorted(
            str(list(row.values())[0])
            for row in result["data"]
            if not self._is_excluded(str(list(row.values())[0]))
        )
        # ★ 加入每表行数，感知数据量变化
        parts = []
        for t in tables[:50]:  # 最多50张表，避免超时
            try:
                safe_t = self._safe_name(t)
                q = (
                    '"'
                    if db_type not in ("mysql", "sqlite")
                    else ("`" if db_type == "mysql" else "")
                )
                cnt_result = db_agent.query(
                    f"SELECT COUNT(*) AS cnt FROM {q}{safe_t}{q}"
                )
                cnt = 0
                if cnt_result.get("success") and cnt_result.get("data"):
                    first = cnt_result["data"][0]
                    cnt = list(first.values())[0] if isinstance(first, dict) else first
                parts.append(f"{t}:{cnt}")
            except Exception:
                parts.append(t)  # 查询失败时只记表名
        h = hashlib.md5(",".join(parts).encode()).hexdigest()
        return h

    def _db_fingerprint(self) -> dict[str, str]:
        from src.core.config_loader import config

        return {
            "db_type": config.db_type,
            "db_host": config.db_host,
            "db_name": config.db_name,
        }
