# -*- coding: utf-8 -*-
from __future__ import annotations

"""
DbCoreOrchestrateMixin: execute_natural_language_sql, execute_raw_sql
"""

from typing import Any  # 类型标注
import logging

_log = logging.getLogger(__name__)


class _DbCoreOrchestrateMixin:
    """核心编排与便捷方法。"""

    def execute_natural_language_sql(
        self,
        intent: str,
        schema_context: str,
        auto_mode: bool | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """
        根据自然语言意图和表结构文档，自动生成并执行 SQL。

        参数：
            intent:          自然语言数据操作描述，如"清空所有任务数据"
            schema_context:  从 db_schema.md 读取的表结构文档全文
            auto_mode:       是否跳过确认直接执行（None 则使用 config 中的默认值）
            dry_run:         True 仅生成 SQL 不执行，用于预览

        返回：
            {"success": True, "sql": "...", "rows_affected": N, "data": [...]}
            或 {"success": False, "error": "...", "sql": "..."}
        """
        auto = auto_mode if auto_mode is not None else self._auto_mode

        # ---------- 第一步：LLM 生成 SQL ----------
        _log.info("[DbExecutor] 正在根据意图生成 SQL ...")
        _log.info("[DbExecutor] 意图: %s", intent[:120])

        gen_result = self._generate_sql(intent, schema_context)
        if not gen_result["success"]:
            return gen_result

        sql = gen_result["data"]["sql"]
        explanation = gen_result["data"].get("explanation", "")

        _log.info("-" * 60)
        _log.info("[DbExecutor] LLM 生成的 SQL:")
        _log.info("-" * 60)
        _log.info(sql.strip())
        _log.info("-" * 60)
        if explanation:
            _log.info("[DbExecutor] 解释: %s", explanation)

        # ---------- 第二步：安全检查 ----------
        if not self._validate_sql(sql):
            return {
                "success": False,
                "error": "SQL 安全校验未通过：禁止的语句类型（如 DROP DATABASE / TRUNCATE / 无条件 DELETE）",
                "sql": sql,
            }

        # ---------- 第三步：dry_run 模式 ----------
        if dry_run:
            _log.info("[DbExecutor] DRY RUN — 仅生成 SQL，不执行")
            return {
                "success": True,
                "sql": sql,
                "explanation": explanation,
                "rows_affected": 0,
                "data": [],
                "dry_run": True,
            }

        # ---------- 第四步：确认或自动执行 ----------
        if not auto:
            import sys as _sys

            if not _sys.stdin.isatty():
                # ★ CI/流水线/后台环境无交互终端 — 拒绝执行，防止永久挂起
                _log.info(
                    "[DbExecutor] ⚠ 非交互环境（无终端），自动确认已禁用。请设置 TARGET_DB_AUTO_MODE=true 或使用 dry_run"
                )
                return {
                    "success": False,
                    "error": "非交互环境下自动确认已禁用，请设置 TARGET_DB_AUTO_MODE=true",
                    "sql": sql,
                }
            _log.info("\n[DbExecutor] ⚠ 即将执行以上 SQL，是否继续？")
            user_input = input("  输入 y 继续执行 / n 跳过: ").strip().lower()
            if user_input != "y":
                _log.info("[DbExecutor] 用户取消执行")
                return {
                    "success": False,
                    "error": "用户取消执行",
                    "sql": sql,
                }

        # ---------- 第五步：执行 SQL ----------
        _log.info("[DbExecutor] 正在执行 SQL ...")
        return self._execute_sql(sql)

    def execute_raw_sql(self, sql: str) -> dict[str, Any]:
        """
        直接执行原始 SQL（跳过 LLM 生成步骤，仍做安全校验）。

        参数：
            sql: 完整的 SQL 语句

        返回：
            {"success": True, "rows_affected": N, "data": [...]}
        """
        _log.info("[DbExecutor] 直接执行 SQL:")
        _log.info("-" * 60)
        _log.info(sql.strip())
        _log.info("-" * 60)

        if not self._validate_sql(sql):
            return {"success": False, "error": "SQL 安全校验未通过"}

        return self._execute_sql(sql)
