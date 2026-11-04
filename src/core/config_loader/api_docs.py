# -*- coding: utf-8 -*-
"""
接口文档（YApi / Swagger）配置 mixin

职责：
- api_docs_url / username / password / project_id / base_url / enabled
"""

import re


class _ConfigApiDocsMixin:
    """接口文档平台配置。"""

    @property
    def api_docs_url(self) -> str:
        return self._get("API_DOCS_URL", "")

    @property
    def api_docs_username(self) -> str:
        return self._user_cred("API_DOCS_USERNAME")

    @property
    def api_docs_password(self) -> str:
        return self._user_cred("API_DOCS_PASSWORD")

    @property
    def api_docs_project_id(self) -> str:
        url = self._get("API_DOCS_URL", "")
        m = re.search(r"/project/(\d+)", url)
        return m.group(1) if m else ""

    @property
    def api_docs_base_url(self) -> str:
        url = self._get("API_DOCS_URL", "")
        m = re.match(r"(https?://[^/]+)", url)
        return m.group(1) if m else ""

    @property
    def api_docs_enabled(self) -> bool:
        return bool(self._get("API_DOCS_URL", ""))
