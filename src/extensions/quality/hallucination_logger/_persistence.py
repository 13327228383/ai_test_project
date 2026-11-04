# -*- coding: utf-8 -*-
"""
样本持久化 Mixin。

提供幻觉样本的保存、加载、导入/导出方法（事实源：MySQL ai_generation_logs）。
"""

import logging
from src.core.guarded import guard
from typing import Any

_log = logging.getLogger(__name__)


class HallucinationPersistenceMixin:
    """样本持久化 Mixin —— 保存/加载/导入/导出幻觉样本。"""

    def save_hallucination_sample(self, intent: dict, failure_info: dict):
        """保存幻觉样本到 MySQL ai_generation_logs（事实源），并显式导出 JSONL。"""
        # Step 1：写入 MySQL（主事实源）
        store = self.ai_store
        with guard("save_hallucination_sample: ok = store.insert_log(intent, failur", log=_log, level=logging.INFO):
            ok = store.insert_log(intent, failure_info, source="hallucination_logger")
            if ok:
                _log.info(
                    "  [HallucinationLogger] 样本已写入 MySQL: %s"
                    % intent.get("case_id", "?")
                )
            else:
                _log.error(
                    "  [HallucinationLogger] MySQL 写入失败: %s"
                    % (getattr(store, "last_error", "未知") or "未知")
                )

    def _load_samples(self) -> list[dict]:
        """主事实源：从 MySQL ai_generation_logs 读取 hallucination 分类样本。

        注意：不再回退到磁盘 JSONL 文件，若 MySQL 为空则返回空列表；
        如需导入历史 JSONL，请显式调用 ``import_from_jsonl(path)``。
        """
        with guard("_load_samples: logs = self.ai_store.load_logs(class", log=_log, level=logging.INFO):
            logs = self.ai_store.load_logs(classification="hallucination")
            if logs:
                return logs
        return []

    def export_to_jsonl(self, save_path: str) -> dict[str, Any]:
        """显式从 MySQL 导出 JSONL（不作为运行时事实源）。"""
        try:
            return self.ai_store.export_to_jsonl(save_path)
        except Exception as e:
            return {
                "success": False,
                "path": str(save_path),
                "count": 0,
                "error": "导出失败: %s" % str(e)[:120],
            }

    def import_from_jsonl(self, jsonl_path: str) -> dict[str, Any]:
        """显式从 JSONL 回填到 MySQL（不作为运行时兜底）。"""
        try:
            return self.ai_store.import_from_jsonl(jsonl_path)
        except Exception as e:
            return {
                "success": False,
                "path": str(jsonl_path),
                "count": 0,
                "error": "导入失败: %s" % str(e)[:120],
            }

    def load_from_db(self) -> list[dict[str, Any]]:
        """主链路从 MySQL 读取完整 AI 生成日志（不限分类）。"""
        try:
            return self.ai_store.load_logs()
        except Exception as e:
            _log.info("  [HallucinationLogger] MySQL 读取失败: %s", str(e)[:60])
            return []
