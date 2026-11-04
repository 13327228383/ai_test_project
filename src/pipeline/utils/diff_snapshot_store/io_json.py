# -*- coding: utf-8 -*-
from __future__ import annotations
from src.core.guarded import guard

"""显式 JSON 导入/导出 — 从 diff_snapshot_store.py 拆出，零逻辑变更。"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)


class _IoJsonMixin:
    """JSON 文件仅作附件，不参与主链路兜底。"""

    def export_to_json(
        self, snapshot_type: str, save_path: str, version_label: str | None = None
    ) -> dict[str, Any]:
        """从 MySQL 导出快照为 JSON 文件（显式导出，不作为运行时事实源）。"""
        result = {
            "success": False,
            "path": str(save_path),
            "snapshot_type": snapshot_type,
            "version_label": None,
            "error": "",
        }
        try:
            data = self.load_snapshot(snapshot_type, version_label=version_label)
            if data is None:
                result["error"] = self._last_error or "未找到可导出的快照"
                return result
            # 取实际 version_label（用于记录）
            try:
                if version_label is None:
                    from src.core.config_loader import config

                    project = getattr(config, "project_name", "") or ""
                else:
                    project = "" if version_label else ""
            except Exception as e:
                _log.warning(
                    "[DiffSnapshotStore] export_to_json 读取 config 失败: %s",
                    str(e)[: self.ERROR_MSG_TRUNCATE_LEN],
                )
                project = ""
            target = Path(save_path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                json.dumps(data, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
            result["success"] = True
            result["version_label"] = version_label
            return result
        except Exception as e:
            result["error"] = str(e)
            self._last_error = str(e)
            return result

    def import_from_json(
        self, snapshot_type: str, json_path: str, version_label: str | None = None
    ) -> dict[str, Any]:
        """从 JSON 文件回填到 MySQL（显式导入，不作为运行时兜底）。"""
        result = {
            "success": False,
            "path": str(json_path),
            "snapshot_type": snapshot_type,
            "version_label": None,
            "error": "",
        }
        try:
            source = Path(json_path)
            if not source.is_file():
                result["error"] = "JSON 文件不存在: %s" % json_path
                return result
            try:
                data = json.loads(source.read_text(encoding="utf-8"))
            except json.JSONDecodeError as e:
                result["error"] = "JSON 解析失败: %s" % str(e)
                return result
            if version_label is None:
                # 尝试用文件的父目录名（/{timestamp}/{type}.json）
                parent_name = source.parent.name
                if parent_name and len(parent_name) >= self.VERSION_LABEL_MIN_LEN:
                    version_label = parent_name
                else:
                    version_label = "imported_%s" % datetime.now().strftime(
                        "%Y%m%d_%H%M%S"
                    )
            saved = self.save_snapshot(snapshot_type, data, version_label=version_label)
            if saved is not None:
                result["success"] = True
                result["version_label"] = saved
            else:
                result["error"] = self._last_error or "写入 MySQL 失败"
            return result
        except Exception as e:
            result["error"] = str(e)
            self._last_error = str(e)
            return result

    def import_directory(self, dir_path: str, force: bool = False) -> dict[str, Any]:
        """把整个快照目录导入到 MySQL（一次性迁移脚本）。

        目录结构：``dir_path/{version_label}/{snapshot_type}.json``
        """
        result = {
            "success": False,
            "path": str(dir_path),
            "imported": 0,
            "skipped": 0,
            "errors": [],
        }
        try:
            root = Path(dir_path)
            if not root.is_dir():
                result["errors"].append("目录不存在: %s" % dir_path)
                return result
            for ver_dir in sorted([d for d in root.iterdir() if d.is_dir()]):
                version_label = ver_dir.name
                for known_type in self.KNOWN_TYPES:
                    json_file = ver_dir / ("%s.json" % known_type)
                    if not json_file.is_file():
                        continue
                    # 若已有同版本同类型则跳过
                    if not force:
                        with guard("import_directory: existing = self.load_snapshot( known", log=_log, level=logging.WARNING):
                            existing = self.load_snapshot(
                                known_type, version_label=version_label
                            )
                            if existing is not None:
                                result["skipped"] += 1
                                continue
                    r = self.import_from_json(
                        known_type, str(json_file), version_label=version_label
                    )
                    if r.get("success"):
                        result["imported"] += 1
                    else:
                        result["errors"].append(
                            "%s/%s: %s"
                            % (version_label, known_type, r.get("error", ""))
                        )
            result["success"] = result["imported"] > 0 or not result["errors"]
            return result
        except Exception as e:
            result["errors"].append(str(e))
            self._last_error = str(e)
            return result
