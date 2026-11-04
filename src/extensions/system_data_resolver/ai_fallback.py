# -*- coding: utf-8 -*-
from __future__ import annotations
from src.core.guarded import guard

"""SystemDataResolver — AI 兜底分类。"""

import logging
from typing import Any

_log = logging.getLogger(__name__)


class AiFallbackMixin:
    """启发式无法确定时，调用 AI 分析 schema 识别表类型。"""

    def _ai_classify_tables(
        self, all_tables: dict[str, list[str]]
    ) -> dict[str, Any] | None:
        if not all_tables:
            return None
        lines = ["数据库表及其字段："]
        for t, cols in list(all_tables.items())[:30]:
            lines.append("- %s: %s" % (t, ", ".join(cols[:15])))
        system_prompt = (
            "你是数据库分析专家。请识别以下 schema 中的用户账号表和角色表。\n"
            "用户账号表特征：包含用户名/账号字段 + 密码字段\n"
            "角色表特征：包含角色名称字段 + ID 主键\n\n"
            "严格输出一个 JSON 对象（不要其他文字）：\n"
            '{"user_table":"表名","user_name_col":"用户名字段","user_pwd_col":"密码字段",'
            '"role_table":"角色表名","role_name_col":"角色名称字段",'
            '"user_role_fk_col":"用户表中指向角色表的外键字段"}\n'
            "找不到则对应值设为 null。"
        )
        with guard("_ai_classify_tables: from src.ai.ai_client import ai_brai", log=_log, level=logging.INFO):
            from src.ai.ai_client import ai_brain

            result = ai_brain._call_api(
                system_prompt=system_prompt,
                user_message="\n".join(lines),
                temperature=0.1,
                max_tokens=512,
                caller_name="Schema分析",
                task_type="schema_analysis",
                force_cloud=True,
            )
            if result.get("success"):
                parsed = ai_brain._parse_json_response(result["data"].strip())
                if parsed.get("success") and isinstance(parsed.get("data"), dict):
                    data = parsed["data"]
                    if data.get("user_table"):
                        _log.info(
                            "[SystemDataResolver] AI 识别: user=%s, role=%s"
                            % (data.get("user_table"), data.get("role_table"))
                        )
                        return data
        return None
