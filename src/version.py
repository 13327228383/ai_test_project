# -*- coding: utf-8 -*-
"""
引擎版本号 — 单一事实源
======================

★  新增：项目此前无任何系统级版本号管理（无 VERSION 文件、
  无 __version__、无 git tag）。本模块作为版本号的唯一定义点，
  health API 和前端均可从此处读取。

版本号规则（语义化版本）：
  MAJOR.MINOR.PATCH
  - MAJOR: 架构级变更（如 v5 → v6 流水线重构）
  - MINOR: 功能增量（如新增插件、新增 KPI）
  - PATCH: 缺陷修复、文档更新
"""

import os

__all__ = ["__version__", "get_version", "get_version_info"]


def _read_version_file() -> str:
    """从项目根目录的 VERSION 文件读取版本号。"""
    try:
        # src/version.py → 项目根目录
        _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        _path = os.path.join(_root, "VERSION")
        with open(_path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return "unknown"


__version__ = _read_version_file()


def get_version() -> str:
    """返回当前引擎版本号字符串。"""
    return __version__


def get_version_info() -> dict:
    """返回版本详情字典，供 API 响应使用。"""
    return {
        "version": __version__,
        "name": "E2E 智能测试引擎",
        "codename": "通用智能 SDET",
    }
