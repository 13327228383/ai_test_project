# -*- coding: utf-8 -*-
"""
AiGenerationLogsStore 导入导出 Mixin
=====================================
提供 ``export_to_jsonl`` / ``import_from_jsonl`` 方法，让 JSONL 仅作显式导入导出附件。
"""

import json
import logging
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)


class AiGenerationLogsStoreIoMixin:
    """AiGenerationLogsStore 导入导出能力。"""

    # -----------------------------------------------------------------
    # 导出 / 导入（让 JSONL 仅作显式导入导出附件）
    # -----------------------------------------------------------------
    def export_to_jsonl(self, save_path: str) -> dict[str, Any]:
        """从 MySQL 导出 JSONL 附件（不作为运行时事实源）。"""
        logs = self.load_logs()
        result = {
            "success": False,
            "path": str(save_path),
            "count": len(logs),
            "error": "",
        }
        try:
            target = Path(save_path)
            target.parent.mkdir(parents=True, exist_ok=True)
            with open(str(target), "w", encoding="utf-8") as f:
                for log in logs:
                    f.write(json.dumps(log, ensure_ascii=False) + "\n")
            result["success"] = True
            return result
        except Exception as e:
            result["error"] = str(e)
            self._last_error = str(e)
            return result

    def import_from_jsonl(
        self, jsonl_path: str, replace: bool = False
    ) -> dict[str, Any]:
        """从 JSONL 回填到 MySQL（显式导入，不作为运行时兜底）。"""
        result = {"success": False, "path": str(jsonl_path), "count": 0, "error": ""}
        try:
            source_path = Path(jsonl_path)
            if not source_path.is_file():
                result["error"] = "JSONL 文件不存在: %s" % jsonl_path
                return result
            samples: list[dict[str, Any]] = []
            with open(str(source_path), "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        samples.append(json.loads(line))
                    except json.JSONDecodeError as _e:
                        _log.debug("[ai_gen_logs] JSONL 坏行跳过(§5): %s", _e)
                        continue
            result["count"] = len(samples)
            if not samples:
                result["error"] = "JSONL 中没有可导入的样本"
                return result
            # 写入 MySQL
            success_count = 0
            for s in samples:
                intent_log = {
                    "case_id": s.get("case_id", ""),
                    "req_id": s.get("req_id", ""),
                    "module": s.get("module", ""),
                    "title": s.get("title", ""),
                    "case_actions_json": s.get("steps", s.get("case_actions_json", [])),
                }
                info = {
                    "reason": s.get("failure_reason", ""),
                    "classification": s.get("classification", "unknown"),
                }
                if self.insert_log(
                    intent_log, info, source=s.get("source", "imported_jsonl")
                ):
                    success_count += 1
            if success_count > 0:
                result["success"] = True
                result["count"] = success_count
            else:
                result["error"] = self._last_error or "所有样本写入失败"
            return result
        except Exception as e:
            result["error"] = str(e)
            self._last_error = str(e)
            return result
