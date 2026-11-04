# -*- coding: utf-8 -*-
"""
追溯链路引擎 — 对外查询接口
==========================
提供按需求、API、DB 表等维度的查询方法。
"""


from .models import TraceLink


class TraceabilityQueries:
    """追溯链路查询器。"""

    def __init__(self, links: dict[str, TraceLink] = None):
        self._links = links or {}

    def get_apis_for_req(self, req_id: str) -> list[dict]:
        """查询某需求关联的 API 列表。"""
        link = self._links.get(req_id)
        return link.api_endpoints if link else []

    def get_tables_for_req(self, req_id: str) -> dict[str, list[str]]:
        """查询某需求关联的数据库表。"""
        link = self._links.get(req_id)
        return link.db_tables if link else {}

    def get_pages_for_req(self, req_id: str) -> list[str]:
        """查询某需求关联的页面。"""
        link = self._links.get(req_id)
        return link.figma_pages if link else []

    def find_reqs_by_api(self, method: str, path: str) -> list[str]:
        """根据 API 反向查找关联的需求。"""
        result = []
        key = f"{method.upper()}:{path}"
        for rid, link in self._links.items():
            for ep in link.api_endpoints:
                ep_key = f"{ep.get('method', '')}:{ep.get('path', '')}"
                if ep_key == key:
                    result.append(rid)
                    break
        return result

    def find_reqs_by_table(self, table_name: str) -> list[str]:
        """根据数据库表反向查找关联的需求。"""
        result = []
        for rid, link in self._links.items():
            if table_name.upper() in (t.upper() for t in link.db_tables):
                result.append(rid)
        return result

    def get_feature_id_for_req(self, req_id: str) -> int:
        """查询某需求关联的功能清单ID（→ system_feature_list.id）。

        返回 0 表示未关联功能清单。
        """
        link = self._links.get(req_id)
        return link.feature_id if link else 0

    def find_reqs_by_feature(self, feature_id: int) -> list[str]:
        """根据功能清单ID反向查找关联的需求列表。"""
        if not feature_id:
            return []
        return [
            rid for rid, link in self._links.items() if link.feature_id == feature_id
        ]
