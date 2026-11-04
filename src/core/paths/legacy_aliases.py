# -*- coding: utf-8 -*-
"""仍在使用的属性（raw_figma_cache_db / process_figma_change_log_by_version）— 从 paths.py 拆出。"""

from pathlib import Path


class _LegacyAliasMixin:
    """仍在使用中的便捷属性和方法。"""

    # ═══════════════════════════════════════════════════════════════
    # find_latest_validation_intent 已删除：process_intents_dir 指向已删的 data/ 目录，
    # glob 恒空且零调用方；测试用例统一从 MySQL test_cases 表读取。
    # ensure_dirs 已删除：全走 MySQL，零调用方，不再落盘创建目录。
    # ═══════════════════════════════════════════════════════════════

    # ── 仍在使用的属性 ──
    @property
    def raw_figma_cache_db(self) -> Path:
        return self.cache_figma_db

    def process_figma_change_log_by_version(self, version_label: str = "") -> Path:
        """★ 版本感知的 Figma 变更日志文件。"""
        if not version_label:
            from src.users.manager import get_active_version_label

            version_label = get_active_version_label(self._root.name)
        version_label = self._sanitize_version_suffix(version_label)
        suffix = ("_" + version_label) if version_label else ""
        return self.cache_figma_dir / f"figma_change_log{suffix}.json"
