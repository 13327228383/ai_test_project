# -*- coding: utf-8 -*-
"""
ActionLibraryStore 导入/导出 Mixin
====================================
JSON 文件导入导出操作。
"""

from pathlib import Path
from typing import Any
import json
import logging

_log = logging.getLogger(__name__)


class _ActionLibraryIOMixin:
    """ActionLibraryStore 导入/导出操作。"""

    def export_to_json(self, save_path: str) -> dict[str, Any]:
        """显式导出到文件（CLI/落盘场景）。Web 路由用 export_to_json_bytes 流式下载。"""
        lib = self.load_library()
        result = {"success": False, "path": str(save_path), "error": ""}
        try:
            target = Path(save_path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                json.dumps(lib, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            result["success"] = True
            return result
        except Exception as e:
            result["error"] = str(e)
            self._last_error = str(e)
            return result

    def export_to_json_bytes(self) -> bytes:
        """序列化 action_library 为 JSON bytes（Web 路由流式下载用，不落盘）。"""
        lib = self.load_library()
        return json.dumps(lib, ensure_ascii=False, indent=2).encode("utf-8")

    def import_from_json_text(self, text: str, replace: bool = False) -> dict[str, Any]:
        """从 JSON 文本回填到 MySQL（Web 上传流式用，不落盘）。"""
        result = {"success": False, "category_count": 0, "action_count": 0, "error": ""}
        try:
            data = json.loads(text)
        except Exception as e:
            result["error"] = "解析 JSON 失败: %s" % str(e)[:120]
            self._last_error = result["error"]
            return result
        if not isinstance(data, dict) or not isinstance(data.get("categories"), list):
            result["error"] = "JSON 结构不符合 action_library.json 约定"
            self._last_error = result["error"]
            return result
        cat_cnt = len(data["categories"])
        act_cnt = sum(
            len(c.get("actions", []) or [])
            for c in data["categories"]
            if isinstance(c, dict)
        )
        result["category_count"] = cat_cnt
        result["action_count"] = act_cnt
        if self.save_library(data):
            result["success"] = True
        else:
            result["error"] = self._last_error or "save_library 失败"
        return result

    def import_from_json(self, json_path: str, replace: bool = False) -> dict[str, Any]:
        """显式从文件导入（CLI/落盘场景）。Web 路由用 import_from_json_text。"""
        result = {
            "success": False,
            "path": str(json_path),
            "category_count": 0,
            "action_count": 0,
            "error": "",
        }
        try:
            text = Path(json_path).read_text(encoding="utf-8")
        except Exception as e:
            result["error"] = "读取 JSON 失败: %s" % str(e)[:120]
            self._last_error = result["error"]
            return result
        r = self.import_from_json_text(text, replace=replace)
        r["path"] = str(json_path)
        return r
