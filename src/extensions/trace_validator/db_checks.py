# -*- coding: utf-8 -*-
from __future__ import annotations
from src.core.guarded import guard

"""DB Checks 校验（三级降级 + 字段校验）— 从 trace_validator.py 拆出，零业务逻辑变更。"""

from typing import Any
import logging

_log = logging.getLogger(__name__)

# 模块常量
_RAG_FALLBACK_THRESHOLD: float = 0.3  # RAG 语义检索相似度阈值
_TEXT_SNIPPET_LENGTH: int = 100  # RAG 文本摘要截断长度
_MAX_DISPLAY_ITEMS: int = 10  # 列表展示时的最大条目数


class _DbChecksMixin:
    """_validate_db_checks / _validate_single_db_check / _rag_fallback_check。"""

    # ══════════════════════════════════════════════════════════
    # DB Checks 校验
    # ══════════════════════════════════════════════════════════

    def _validate_db_checks(
        self, db_checks: list[Any], req_id: str = None
    ) -> dict[str, Any]:
        """校验 db_checks 列表。"""
        result = {"valid": True, "issues": [], "warnings": []}

        for check in db_checks:
            check_result = self._validate_single_db_check(check, req_id)
            if not check_result["valid"]:
                result["valid"] = False
                result["issues"].extend(check_result["issues"])
            result["warnings"].extend(check_result["warnings"])

        return result

    def _validate_single_db_check(
        self, check: Any, req_id: str = None
    ) -> dict[str, Any]:
        """
        校验单个 db_check，支持三级降级策略。
        ★ 注意：降级只用于"表/字段是否存在"的定位判断，不用于断言值判断。
        断言值（expected值）必须严格匹配，不降级。
        """
        result = {"valid": True, "issues": [], "warnings": [], "fallback_used": None}

        # 解析 check 内容
        table_name = None
        field_names = []

        if isinstance(check, dict):
            table_name = check.get("table", "")
            field_check = check.get("field_check", {})
            if isinstance(field_check, dict):
                field_names = list(field_check.keys())
        elif isinstance(check, str):
            table_name = self._extract_table_from_natural(check)
            field_names = self._extract_fields_from_natural(check)
        else:
            result["valid"] = False
            result["issues"].append("不支持的 db_check 格式: %s" % type(check).__name__)
            return result

        if not table_name:
            result["warnings"].append(
                "无法从 db_check 中提取表名: %s" % str(check)[:100]
            )
            return result

        table_upper = table_name.upper()

        # ★ P1: 三级降级校验
        # L1: 精确匹配（追溯链路）
        if table_upper in self._all_tables:
            result["fallback_used"] = "L1_exact"
        else:
            # L2: 模糊匹配（关键词相似）
            similar = self._find_similar_table(table_upper)
            if similar:
                result["warnings"].append(
                    "[L2降级] 表 '%s' 未精确匹配，模糊匹配到 '%s'"
                    % (table_name, similar)
                )
                result["fallback_used"] = "L2_fuzzy"
            else:
                # L3: RAG语义检索（兜底）
                rag_match = self._rag_fallback_check(table_name, "db_table")
                if rag_match:
                    result["warnings"].append(
                        "[L3降级] 表 '%s' 未在追溯链路中找到，但RAG检索到相关上下文: %s"
                        % (table_name, rag_match)
                    )
                    result["fallback_used"] = "L3_rag"
                else:
                    result["issues"].append(
                        "[L1/L2/L3均失败] 表 '%s' 完全找不到依据。可用表: %s"
                        % (
                            table_name,
                            ", ".join(sorted(self._all_tables)[:_MAX_DISPLAY_ITEMS]),
                        )
                    )
                    result["valid"] = False

        # 字段校验（仅在L1/L2通过时）
        if result["valid"] and field_names and table_upper in self._all_fields:
            available_fields = self._all_fields[table_upper]
            for field in field_names:
                field_upper = field.upper()
                if field_upper not in available_fields:
                    similar_field = self._find_similar_field(
                        field_upper, available_fields
                    )
                    if similar_field:
                        result["warnings"].append(
                            "字段 '%s.%s' 未找到，您是否指的是 '%s'？"
                            % (table_name, field, similar_field)
                        )
                    else:
                        result["warnings"].append(
                            "字段 '%s.%s' 未在表结构中找到。可用字段: %s"
                            % (
                                table_name,
                                field,
                                ", ".join(
                                    sorted(available_fields)[:_MAX_DISPLAY_ITEMS]
                                ),
                            )
                        )

        # req_id 关联校验
        if req_id and req_id in self.trace_links:
            link = self.trace_links[req_id]
            if isinstance(link, dict):
                req_tables = link.get("db_tables", {})
            else:
                req_tables = getattr(link, "db_tables", {})
            if isinstance(req_tables, dict):
                req_table_names = set(t.upper() for t in req_tables.keys())
                if table_upper not in req_table_names:
                    result["warnings"].append(
                        "表 '%s' 不在需求 '%s' 的追溯链路中。该需求关联的表: %s"
                        % (table_name, req_id, ", ".join(req_table_names))
                    )

        return result

    def _rag_fallback_check(self, query: str, chunk_type: str) -> str | None:
        """★ P1: RAG降级检索——当精确/模糊匹配都失败时，用RAG语义检索兜底。"""
        with guard("_rag_fallback_check: from src.extensions.rag_engine impor", log=_log, level=logging.WARNING):
            from src.extensions.rag_engine import rag_engine

            # retrieve()接收Dict参数，包装query
            req = {"req_id": "fallback", "module": "", "description": query}
            retrieved = rag_engine.retrieve(req, top_k=1)
            # ★ S3-012: 记录 RAG 使用日志
            try:
                from src.extensions.rag_engine.rag_usage_logger import log_rag_retrieval

                log_rag_retrieval(retrieved, req_id="trace_fallback")
            except Exception as _e:
                _log.debug("[db_checks] RAG 使用日志记录异常: %s", _e, exc_info=True)
            # 根据chunk_type提取对应类型的结果
            type_key_map = {
                "db_table": "db_tables",
                "api": "api_endpoints",
                "figma_page": "figma_pages",
            }
            result_key = type_key_map.get(chunk_type, "db_tables")
            results = retrieved.get(result_key, [])
            if results and len(results) > 0:
                top = results[0]
                score = top.get("score", 0)
                text = top.get("text", "")[:_TEXT_SNIPPET_LENGTH]
                if score > _RAG_FALLBACK_THRESHOLD:
                    return f"(相似度{score:.0%}) {text}..."
        return None
