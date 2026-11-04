# -*- coding: utf-8 -*-
"""
项目元信息配置 mixin

职责：
- project_name / project_version / project_root
"""

from pathlib import Path


class _ConfigProjectMetaMixin:
    """项目元信息配置。"""

    @property
    def project_name(self) -> str:
        return self._project_name

    @property
    def project_version(self) -> str:
        from src.users.manager import get_active_version_label

        label = get_active_version_label(self._project_name)
        return label or self._get("PROJECT_VERSION", "")

    @property
    def project_root(self) -> Path:
        return self._project_root
