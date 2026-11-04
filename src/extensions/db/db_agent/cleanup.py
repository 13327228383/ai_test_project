from __future__ import annotations
from src.core.guarded import guard

# DatabaseAgentCleanupMixin — 清理顺序与执行

import logging
from typing import Any

_log = logging.getLogger(__name__)


class DatabaseAgentCleanupMixin:
    """清理顺序与执行方法。"""

    # ---- 清理顺序 ----
    _cleanup_order = []  # [(table, match_column), ...] — load_schema 填充

    def _cleanup_order_fallback(self):
        """内置默认清理顺序（load_schema 未调用时的兜底方案）。"""
        default_tables = [
            # 品牌/产品
            ("BRAND_INFO", "BRAND_CODE"),
            ("PRODUCT_INFO", "PRODUCT_CODE"),
            # 烟叶
            ("TOBACCO_INFO", "TOBACCO_CODE"),
            ("TOBACCO_GRADE", "GRADE_CODE"),
            ("TOBACCO_ORIGIN", "ORIGIN_CODE"),
            ("TOBACCO_YEAR", "YEAR_CODE"),
            # 配方
            ("FORMULATION_BASIC_INFO", "FORMULATION_CODE"),
            ("FORMULATION_FORMULA_DETAIL", None),  # 由 FORMULATION_ID 外键级联
            # 任务
            ("PRODUCT_TASK", "TASK_NAME"),
            ("TOBACCO_TASK", "TASK_NAME"),
            ("TASK_SCHEME", None),  # 由 TASK_ID 外键级联
            # 用户
            ("SYS_USER", "USER_NAME"),
            # 通用字典表
            ("SYS_DICT", "DICT_CODE"),
            ("SYS_TAG", "TAG_NAME"),
            ("TAG_RELATION", None),
            # 文件
            ("FILE_CHUNK", None),
            ("FILE", "FILE_NAME"),
        ]
        self._cleanup_order = [
            (t, c)
            for t, c in default_tables
            if t in self._TABLE_COLUMNS or not self._TABLE_COLUMNS
        ]
        if not self._cleanup_order:
            # 最后的兜底：空表列表，不会产生清理操作
            self._cleanup_order = []

    def cleanup_test_data(self, prefix: str | None = None) -> dict[str, Any]:
        """
        清理由 prepare_test_data 生成的脏数据。扫描业务表删除带 prefix 前缀的数据，使用子查询处理外键级联。
        返回 {"success": True, "deleted": {"TABLE": N, ...}}
        """
        self._ensure_connected()
        prefix = prefix or self.TEST_PREFIX

        deleted = {}

        # 清理顺序：从 _cleanup_order（load_schema 自动生成）获取
        # 如果未调用 load_schema，回退到内置默认顺序
        if not self._cleanup_order:
            self._cleanup_order_fallback()

        cleanup_order = self._cleanup_order

        for table, match_column in cleanup_order:
            if match_column is None:
                continue  # 跳过间接清理（如 TASK_SCHEME 由 PRODUCT_TASK 级联）
            with guard("cleanup_test_data: like_pattern = prefix.replace(''', '", log=_log, level=logging.WARNING):
                like_pattern = prefix.replace("'", "''") + "%"
                qt = self._quote_ident(table)
                qc = self._quote_ident(match_column)
                # ★ 安全修复：使用参数化查询替代字符串拼接
                sql = f"DELETE FROM {qt} WHERE {qc} LIKE %s"
                self._cursor.execute(sql, (like_pattern,))
                rowcount = self._cursor.rowcount
                if rowcount > 0:
                    deleted[table] = rowcount
                    _log.info(f"[DatabaseAgent] 清理 {table}: {rowcount} 行")

        self._conn.commit()

        total = sum(deleted.values())
        _log.info(
            f"[DatabaseAgent] 测试数据清理完成，共清理 {total} 行 ({len(deleted)} 个表)"
        )
        return {"success": True, "deleted": deleted, "total": total}
