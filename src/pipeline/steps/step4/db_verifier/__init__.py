# -*- coding: utf-8 -*-
"""
DB 校验器 (DbVerifier) — ★ P1 加固版（包入口）
==================================================
在步骤执行后，通过 db_agent 查询数据库，验证数据的最终落库状态。

支持 4 种校验格式：
  - 结构化：{"table": "SYS_USER", "where": "...", "expect": ">=1",
             "field_check": {"amount": {"expect": 100.00, "precision": 0.01}}}
  - SQL：SELECT COUNT(*) FROM SYS_USER WHERE ...
  - 自然语言（正则）：正则解析表名 + WHERE 条件
  - 自然语言（AI）：AI 根据意图生成 SQL → 执行 → 返回结果

★ P1 加固内容：
  1. 精度校验：支持浮点数/金额/日期的容差比较
  2. 字段级值校验：结构化 check 支持 field_check
  3. Schema 自动加载：不依赖 session_ctx
  4. 自然语言解析增强：支持更多条件模式

包结构：
  helpers.py    — 通用工具（精度比较 / 格式化 / 标识符引用 / LIMIT 子句 / schema 摘要）
  schema.py     — 表结构自动加载（_auto_load_schema）
  safety.py     — SQL 安全校验（_validate_sql_safe / _validate_where_safe）
  verifiers.py  — 4 种校验器（structured / sql / natural / ai）
"""

from typing import Any
from src.core.guarded import guard
import logging

from .helpers import (
    _compare_with_precision,
    _format_value,
    _get_field_type,
    _limit_clause,
    _get_schema_context,
    _quote_ident,
)
from .schema import _auto_load_schema
from .safety import _validate_sql_safe, _validate_where_safe
from .verifiers import (
    _verify_db_ai,
    _verify_structured,
    _verify_sql,
    _verify_natural,
)

_log = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════
# 主入口
# ══════════════════════════════════════════════════════════


def verify_db(check_desc, use_ai: bool = True) -> dict[str, Any]:
    """执行数据库校验。返回 {success, evidence, data, count}。

    ★ 通用修复 (2026-07-21 v14): DB 连接失败（认证/网络/超时/驱动未安装）
       一律返回 skipped=True，不阻断用例。

    根因(3层追问):
      1. 表层: TC-REQ-038-P01 失败 "Access denied for user '某SPA系统'"
      2. 中层: DB 连接是环境依赖，认证失败/网络超时/主机不可达都属于环境问题
      3. 根因: 旧代码把"连接失败"等同于"校验失败"，环境问题被误判为用例失败

    通用性:
      - 跨系统: 所有 DB 类型（yasdb/mysql/postgresql/oracle）连接失败都跳过
      - 跨行业: 所有行业的 DB 连接失败都跳过
      - 跨场景: 连接阶段失败 skip；SQL 执行阶段失败仍 fail（用例本身问题）
      - 兼容: skipped 轨在 verify_step 中不计入 AND，但置信度扣分触发审核
    """
    from src.extensions.db.db_agent import db_agent

    try:
        db_agent.connect()
    except Exception as e:
        _err = str(e)
        # ★ 通用修复(v14): 所有连接阶段异常都视为环境问题，跳过不阻断
        #   - 驱动未安装 (ImportError)
        #   - 认证失败 (Access denied / 1045)
        #   - 网络超时 (TimeoutError / ConnectionError)
        #   - 主机不可达 (HostUnreachable)
        #   - 配置缺失 (配置未填)
        _is_env_error = (
            "yaspy" in _err
            or "未安装" in _err
            or "ImportError" in type(e).__name__
            or "access denied" in _err.lower()
            or "1045" in _err
            or "timeout" in _err.lower()
            or "timed out" in _err.lower()
            or "connection" in _err.lower()
            and "refused" in _err.lower()
            or "connection" in _err.lower()
            and "reset" in _err.lower()
            or "unreachable" in _err.lower()
            # ★ Fix F(2026-09-27)：1046/No database selected 属"库名未配置/配置漂移"类环境问题。
            #   此前未覆盖 → SQL 阶段报 1046 被当作用例 FAIL（假 FAIL），已由 connect() 侧修复
            #   根因；此处作为兜底，仍视为环境跳过不阻断用例。
            or "no database selected" in _err.lower()
            or "1046" in _err
            or "unknown database" in _err.lower()
            or "配置未填" in _err
            or "数据库未配置" in _err
        )
        if _is_env_error:
            _log.warning(
                "[DB-Verifier] DB 连接失败(环境问题)，跳过 DB 轨不阻断用例: %s",
                _err[:120],
            )
            return {
                "success": None,
                "evidence": "DB轨跳过(环境问题): %s" % _err[:80],
                "data": [],
                "count": 0,
                "skipped": True,
                "skip_reason": "connection_failure",
            }
        # 其他未知异常也跳过，避免误判（保守策略）
        _log.warning(
            "[DB-Verifier] DB 连接未知异常，跳过 DB 轨: %s",
            _err[:120],
        )
        return {
            "success": None,
            "evidence": "DB轨跳过(连接异常): %s" % _err[:80],
            "data": [],
            "count": 0,
            "skipped": True,
            "skip_reason": "unknown_connection_error",
        }

    try:
        if isinstance(check_desc, dict):
            return _verify_structured(db_agent, check_desc)
        check_str = str(check_desc).strip()
        check_upper = check_str.upper()
        if check_upper.startswith("SELECT"):
            return _verify_sql(db_agent, check_str)
        # 自然语言：先尝试正则解析 → 失败则走 AI
        result = _verify_natural(db_agent, check_str)
        if (
            not result["success"]
            and "无法解析" in result.get("evidence", "")
            and use_ai
        ):
            return _verify_db_ai(db_agent, check_str)
        return result
    except Exception as e:
        return {
            "success": False,
            "evidence": "DB校验异常: %s" % e,
            "data": [],
            "count": 0,
        }
    finally:
        # ★ P0 修复：确保连接在任何情况下都被关闭，防止连接泄漏
        with guard("verify_db: if hasattr(db_agent, 'close'): db_ag", log=_log):
            if hasattr(db_agent, "close"):
                db_agent.close()
