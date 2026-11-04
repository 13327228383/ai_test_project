# -*- coding: utf-8 -*-
"""过程产物 + 测试结果目录 — 从 paths.py 拆出，零业务逻辑变更。"""

from pathlib import Path


class _ProcessResultMixin:
    """过程产物（需求拆解 / 测试用例 / 测试设计）+ 测试结果（报告/截图/日志）。"""

    # ═══════════════════════════════════════════════════════════════
    # 过程产物/ — Step 2-3 AI 生成的过程文件
    # ═══════════════════════════════════════════════════════════════

    @property
    def process_dir(self) -> Path:
        return self._root / "过程产物"

    # -- 需求拆解 (Step 2) --
    @property
    def process_requirements_dir(self) -> Path:
        return self.process_dir / "需求拆解"

    @property
    def process_requirements_xlsx(self) -> Path:
        return self.process_requirements_dir / "requirements_breakdown.xlsx"

    @property
    def process_breakdown_hash_json(self) -> Path:
        """（向后兼容）全局需求拆解断点哈希文件。"""
        return self.process_requirements_dir / "_breakdown_hash.json"

    def process_breakdown_hash_by_version(self, version_label: str = "") -> Path:
        """★ 版本感知的需求拆解断点哈希文件。"""
        if not version_label:
            from src.users.manager import get_active_version_label

            version_label = get_active_version_label(self._root.name)
        version_label = self._sanitize_version_suffix(version_label)
        suffix = ("_" + version_label) if version_label else ""
        return self.process_requirements_dir / f"_breakdown_hash{suffix}.json"

    # -- 测试用例 (Step 3) --
    @property
    def process_intents_dir(self) -> Path:
        return self.process_dir / "测试用例"

    # -- 测试设计 (Step 3 XMind) --
    @property
    def process_testdesign_dir(self) -> Path:
        return self.process_dir / "测试设计"

    # ═══════════════════════════════════════════════════════════════
    # 测试结果/ — Step 4-5 最终交付物
    # ═══════════════════════════════════════════════════════════════

    @property
    def result_dir(self) -> Path:
        return self._root / "测试结果"

    @property
    def result_report_dir(self) -> Path:
        return self.result_dir / "测试报告"

    @property
    def result_screenshots_dir(self) -> Path:
        return self.result_dir / "执行截图"

    @property
    def result_log_dir(self) -> Path:
        return self.result_dir / "执行日志"
