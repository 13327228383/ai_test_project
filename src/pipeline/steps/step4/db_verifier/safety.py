# -*- coding: utf-8 -*-
"""SQL 安全校验 — 从 db_verifier.py 拆出，零逻辑变更。"""

import re


def _validate_sql_safe(sql: str) -> tuple:
    """
    ★ 安全修复：校验 SQL 是否安全（仅允许 SELECT）。
    返回 (is_safe: bool, reason: str)。
    """
    clean = sql.strip().rstrip(";").strip()
    first_word = clean.split()[0].upper() if clean.split() else ""
    dangerous = {
        "INSERT",
        "UPDATE",
        "DELETE",
        "DROP",
        "ALTER",
        "CREATE",
        "TRUNCATE",
        "GRANT",
        "REVOKE",
        "EXEC",
        "EXECUTE",
        "CALL",
        "REPLACE",
    }
    if first_word in dangerous:
        return False, "禁止执行 %s 语句，仅允许 SELECT" % first_word
    # 检查多语句（分号分隔的注入）
    if ";" in clean:
        return False, "禁止执行多语句 SQL"
    # 检查子查询中的危险操作
    upper_sql = clean.upper()
    # ★ v2.0 (2026-07-07): 用单词边界匹配代替子串匹配
    #   原bug: `kw in upper_sql` 会把 CREATE_TIME/UPDATE_TIME/DELETE_FLAG 等列名当成危险关键字误拦截
    #   修复: 用 \b 正则单词边界，只匹配独立的 CREATE/UPDATE/DELETE 等 SQL 关键字
    for kw in dangerous:
        if kw != "SELECT":
            if re.search(r"\b" + kw + r"\b", upper_sql):
                return False, "SQL 中包含禁止的关键字 %s" % kw
    return True, ""


def _validate_where_safe(where: str) -> tuple:
    """
    校验 WHERE 子句是否安全，防止 SQL 注入。
    仅允许字母、数字、常见 SQL 操作符、引号、空格和基本标点。
    返回 (is_safe: bool, reason: str)。
    """
    if not where:
        return True, ""
    dangerous_patterns = [
        r";",
        r"--",
        r"/\*",
        r"\*/",
        r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE|GRANT|REVOKE|EXEC|EXECUTE|CALL)\b",
    ]
    where_upper = where.upper()
    for pat in dangerous_patterns:
        if re.search(pat, where_upper):
            return False, "WHERE 子句包含不允许的内容: %s" % pat
    return True, ""
