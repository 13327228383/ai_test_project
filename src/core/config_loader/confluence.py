# -*- coding: utf-8 -*-
"""
Confluence 配置 mixin

职责：
- confluence_url / username / password / base_url / page_id
- confluence_urls / page_ids（多页支持）
- confluence_mode / pat / mcp_api_base_path
"""

import re


class _ConfigConfluenceMixin:
    """Confluence 配置。"""

    @property
    def confluence_url(self) -> str:
        return self._get("CONFLUENCE_URL", "")

    @property
    def confluence_username(self) -> str:
        return self._user_cred("CONFLUENCE_USERNAME")

    @property
    def confluence_password(self) -> str:
        return self._user_cred("CONFLUENCE_PASSWORD")

    @property
    def confluence_base_url(self) -> str:
        url = self._get("CONFLUENCE_URL", "")
        if not url:
            return ""
        match = re.match(r"(https?://[^/]+)", url)
        return match.group(1) if match else url

    @property
    def confluence_page_id(self) -> str:
        url = self._get("CONFLUENCE_URL", "")
        if not url:
            return ""
        match = re.search(r"pageId=(\d+)", url)
        return match.group(1) if match else ""

    @property
    def confluence_urls(self) -> list:
        raw = self._get("CONFLUENCE_URL", "")
        if not raw:
            return []
        return [x for u in raw.split(",") if (x := u.strip())]

    @property
    def confluence_page_ids(self) -> list:
        # ★ CONFLUENCE_PAGE_IDS 是独立的资源（页面 ID 列表），与 CONFLUENCE_URL 分离
        raw_ids = self._get("CONFLUENCE_PAGE_IDS", "")
        if raw_ids and raw_ids.strip():
            return [x for p in raw_ids.split(",") if (x := p.strip())]
        # fallback: 未配置 PAGE_IDS 时，从 URL 解析 pageId=（向后兼容）
        ids = []
        for url in self.confluence_urls:
            match = re.search(r"pageId=(\d+)", url)
            if match:
                ids.append(match.group(1))
        return ids

    @property
    def confluence_mode(self) -> str:
        return self._get("CONFLUENCE_MODE", "mcp")

    @property
    def confluence_pat(self) -> str:
        return self._user_cred("CONFLUENCE_PAT") or self._user_cred(
            "CONFLUENCE_PASSWORD"
        )

    @property
    def confluence_mcp_api_base_path(self) -> str:
        return self.confluence_base_url
