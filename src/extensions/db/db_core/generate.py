# -*- coding: utf-8 -*-
"""
DbCoreGenerateMixin: _generate_sql — LLM SQL 生成（含大段 prompt 模板）
"""

from typing import Any  # 类型标注
import logging

_log = logging.getLogger(__name__)

from src.ai.ai_client import ai_brain  # AI 大脑（复用 DeepSeek 调用）


class _DbCoreGenerateMixin:
    """LLM SQL 生成方法。"""

    def _generate_sql(self, intent: str, schema_context: str) -> dict[str, Any]:
        """
        调用 LLM 根据意图和表结构生成 SQL 语句。

        参数：
            intent:          自然语言数据操作意图
            schema_context:  表结构文档

        返回：
            {"success": True, "data": {"sql": "...", "explanation": "..."}}
        """
        system_prompt = (
            "你是一名资深 DBA（数据库管理员），精通 MySQL / PostgreSQL / SQLite / YasDB / Oracle 的 SQL 编写。\n"
            "你的任务是根据用户提供的「表结构文档」和「自然语言数据操作意图」，自动生成正确、"
            "安全的 SQL 语句。\n\n"
            "## 输出格式\n"
            "请严格输出一个 JSON 对象，包含以下字段：\n"
            "- sql: 生成的 SQL 语句（完整可执行，支持多条语句用分号分隔）\n"
            "- explanation: 简要说明 SQL 做了什么（50-200 字）\n\n"
            "## 编写规则\n"
            "1. 仔细阅读表结构文档，理解表名、字段名、字段类型、主键、外键和约束\n"
            "2. 根据用户意图，选择正确的表进行操作（INSERT / UPDATE / DELETE）\n"
            "3. UPDATE 和 DELETE 必须带 WHERE 条件，除非用户明确要求操作所有行\n"
            "4. INSERT 必须列出字段名并匹配正确的值类型\n"
            "5. 如果意图涉及清空某表，使用 DELETE FROM table（保留自增计数器）\n"
            "6. 如果意图涉及大量数据构造，合理使用批量 INSERT 语法\n"
            "7. 字段值必须与表结构中定义的字段类型匹配（如 int 字段不能赋字符串）\n"
            "8. 自动处理 NULL、默认值、自增主键等特殊情况\n"
            "9. 生成的 SQL 必须对 %s 数据库完全有效\n"
            "   - Oracle / YasDB: 用 FETCH FIRST N ROWS ONLY 替代 LIMIT\n"
            '   - Oracle / YasDB: 用双引号 " 引用标识符，不用反引号 `\n'
            "   - YasDB: 日期用 TIMESTAMP 或 DATE 字面量，字符串用单引号\n"
            "10. 不要输出任何 JSON 之外的解释文字\n\n"
            "## 输出示例\n"
            "{{\n"
            '  "sql": "DELETE FROM tasks WHERE status = \'completed\';",\n'
            '  "explanation": "删除 tasks 表中所有状态为 completed 的任务记录，保留待处理和进行中的任务"\n'
            "}}\n" % self._db_type.upper()
        )

        user_message = (
            "数据库类型：%s\n"
            "数据库名：%s\n\n"
            "=== 表结构文档 ===\n"
            "%s\n\n"
            "=== 自然语言意图 ===\n"
            "%s\n\n"
            "请为以上意图生成正确的 SQL 语句。"
            % (
                self._db_type.upper(),
                self._db_name,
                schema_context,
                intent,
            )
        )

        # 调用 AI 大脑（路由模块：按任务类型路由）
        result = ai_brain._call_api(
            system_prompt=system_prompt,
            user_message=user_message,
            temperature=0.1,  # 低温度保证 SQL 稳定性
            max_tokens=8192,  # ★ v10: 推理模型安全下限（原2048→Router下限8192兜底）
            task_type="operation_instruction",
        )

        if not result["success"]:
            return {
                "success": False,
                "error": "LLM 调用失败: %s" % result.get("error", "未知错误"),
            }

        # 解析 JSON 响应
        parsed = ai_brain._parse_json_response(result["data"].strip())
        if not parsed["success"]:
            return {
                "success": False,
                "error": "LLM 返回的 JSON 解析失败: %s" % parsed.get("error", ""),
            }

        sql_data = parsed["data"]
        if isinstance(sql_data, list) and len(sql_data) > 0:
            sql_data = sql_data[0]

        if not sql_data.get("sql", "").strip():
            return {
                "success": False,
                "error": "LLM 未生成有效 SQL",
            }

        return {"success": True, "data": sql_data}
