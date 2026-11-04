# -*- coding: utf-8 -*-
"""
Figma 配置 mixin

职责：
- figma_url / username / key / login_password / file_key
"""

import re


class _ConfigFigmaMixin:
    """Figma 配置。"""

    @property
    def figma_url(self) -> str:
        return self._get("FIGMA_URL", "")

    @property
    def figma_username(self) -> str:
        return self._user_cred("FIGMA_USERNAME")

    @property
    def figma_key(self) -> str:
        return self._user_cred("FIGMA_KEY")

    @property
    def figma_login_password(self) -> str:
        return self._user_cred("FIGMA_PASSWORD")

    @property
    def figma_file_key(self) -> str:
        url = self._get("FIGMA_URL", "")
        if not url:
            return ""
        # ★ 兼容 design 和 file 两种路径格式（与 extract_file_key 保持一致）
        match = re.search(r"figma\.com/(?:design|file)/([^/\s?]+)", url)
        return match.group(1) if match else ""
