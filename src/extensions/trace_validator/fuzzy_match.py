# -*- coding: utf-8 -*-
from __future__ import annotations

"""模糊匹配（表/字段/API） — 从 trace_validator.py 拆出，零业务逻辑变更。"""


_FUZZY_MATCH_CUTOFF: float = 0.6  # 表名/API路径模糊匹配阈值
_FIELD_MATCH_CUTOFF: float = 0.7  # 字段名模糊匹配阈值


class _FuzzyMatchMixin:
    """_find_similar_table / _find_similar_field / _find_similar_api — 基于 difflib。"""

    # ══════════════════════════════════════════════════════════
    # 模糊匹配
    # ══════════════════════════════════════════════════════════

    def _find_similar_table(self, table_name: str) -> str | None:
        """查找相似的表名。"""
        from difflib import get_close_matches

        matches = get_close_matches(
            table_name, self._all_tables, n=1, cutoff=_FUZZY_MATCH_CUTOFF
        )
        return matches[0] if matches else None

    def _find_similar_field(self, field_name: str, available: set) -> str | None:
        """查找相似的字段名。"""
        from difflib import get_close_matches

        matches = get_close_matches(
            field_name, available, n=1, cutoff=_FIELD_MATCH_CUTOFF
        )
        return matches[0] if matches else None

    def _find_similar_api(self, api_path: str) -> str | None:
        """查找相似的API路径。"""
        from difflib import get_close_matches

        matches = get_close_matches(
            api_path, self._all_apis, n=1, cutoff=_FUZZY_MATCH_CUTOFF
        )
        return matches[0] if matches else None
