# -*- coding: utf-8 -*-
"""风险驱动的测试用例优先级推导（A4, 2026-08-16）。

背景（PRD CAPABILITY_GAP A4）：
  test_cases 表的 priority 字段在 106 条用例中 77 条为空，引擎虽有 priority 相关
  机制但未在生成时填充。本模块实现基于多维风险信号自动推导 priority，并在
  Step3 用例生成落库（case_store.upsert_intents）时填充。

风险信号（可组合加权）：
  ① 业务关键路径：登录 / 支付 / 数据删除 / 导出 / 权限 等核心交易链路
  ② 历史失败频率：executions 表同 case_id 历史 FAIL 次数
  ③ 外部依赖 / 第三方接口：调用外部 API / 网关 / 消息队列
  ④ 安全敏感：鉴权 / 越权 / 注入类动作或断言

输出：
  - 分级 0~3（数值越高风险越大）
  - 标签 low / medium / high / critical（与 testing_methods.base.TestCase.priority 对齐）
  - 推导依据（命中的信号列表），便于审计与回溯
"""

from __future__ import annotations
from src.core.guarded import guard

import logging
from typing import Any

_log = logging.getLogger(__name__)

# 优先级等级（数值越高风险越大）
PRIORITY_LOW: int = 0
PRIORITY_MEDIUM: int = 1
PRIORITY_HIGH: int = 2
PRIORITY_CRITICAL: int = 3

# 等级 → 标签
_PRIORITY_LABELS: dict[int, str] = {
    PRIORITY_LOW: "low",
    PRIORITY_MEDIUM: "medium",
    PRIORITY_HIGH: "high",
    PRIORITY_CRITICAL: "critical",
}

# ① 业务关键路径关键词
_CRITICAL_PATH_KEYWORDS: tuple[str, ...] = (
    "登录", "登入", "支付", "下单", "付款", "扣款", "充值", "退款", "删除", "清除",
    "导出", "权限", "授权", "结算", "转账", "交易", "密码", "改密", "扣费",
    "login", "pay", "payment", "delete", "remove", "export", "permission", "auth",
)

# ③ 外部依赖 / 第三方接口关键词
_EXTERNAL_DEP_KEYWORDS: tuple[str, ...] = (
    "第三方", "外部", "接口", "api", "调用", "http", "rpc", "mq", "消息队列",
    "支付网关", "短信", "邮件", "esb", "webservice", "对接", "网关", "回调",
    "interface", "gateway", "callback",
)

# ④ 安全敏感：鉴权 / 越权 / 注入关键词
_SECURITY_KEYWORDS: tuple[str, ...] = (
    "鉴权", "越权", "授权", "注入", "sql注入", "xss", "越权访问", "未授权",
    "权限校验", "登录态", "token", "认证", "authentication", "authorization",
    "injection", "vertical", "horizontal", "提权",
)

# 权重（与上面 4 个信号对应）
_WEIGHT_CRITICAL_PATH: int = 2
_WEIGHT_EXTERNAL_DEP: int = 1
_WEIGHT_SECURITY: int = 2

# 历史失败频率阈值 → 加权
_HISTORY_FAIL_HIGH: int = 5   # ≥5 次 → +2
_HISTORY_FAIL_MID: int = 2    # ≥2 次 → +1

# 等级阈值（累计加权分）
_SCORE_CRITICAL: int = 4
_SCORE_HIGH: int = 2
_SCORE_MEDIUM: int = 1


def _collect_text(case: dict[str, Any]) -> str:
    """聚合用例所有文本字段，用于风险信号匹配（统一小写）。

    覆盖：顶层字段 + _explore_task + payload + actions/checks 内容，
    确保无论是生成期的 case dict 还是落库前的归一化 dict 都能被识别。
    """
    if not isinstance(case, dict):
        return ""
    parts: list[str] = []
    for _k in (
        "title", "case_type", "control_type", "test_method", "expected_result",
        "precondition", "module", "operation", "expected_feedback",
    ):
        _v = case.get(_k)
        if _v:
            parts.append(str(_v))

    _et = case.get("_explore_task") or {}
    if isinstance(_et, dict):
        for _k in ("operation", "module", "expected_feedback"):
            if _et.get(_k):
                parts.append(str(_et[_k]))

    _payload = case.get("payload") or case.get("payload_json")
    if isinstance(_payload, dict):
        for _k in ("title", "operation", "module", "expected_feedback", "checks", "actions"):
            _v = _payload.get(_k)
            if _v:
                parts.append(str(_v))

    _acts = case.get("actions") or []
    if isinstance(_acts, list):
        for _a in _acts:
            if isinstance(_a, dict):
                parts.append(
                    "%s %s" % (str(_a.get("target", "")), str(_a.get("value", "")))
                )

    _checks = case.get("checks") or []
    if isinstance(_checks, list):
        for _c in _checks:
            if isinstance(_c, dict):
                parts.append(
                    "%s %s" % (str(_c.get("expect", "")), str(_c.get("description", "")))
                )
    return " ".join(parts).lower()


def derive_priority(
    case: dict[str, Any],
    history_fail_count: int = 0,
) -> tuple[int, list[str]]:
    """基于多维风险信号推导用例优先级（0~3）及命中依据。

    Args:
        case: 测试用例 dict（含 title / operation / actions / checks 等字段）。
        history_fail_count: executions 表同 case 历史 FAIL 次数（由调用方查询后传入，
            保持本函数为纯函数，便于单测）。

    Returns:
        (priority_level: int[0..3], reasons: list[str])，reasons 为命中信号名，便于审计。
    """
    if not isinstance(case, dict):
        case = {}
    _text = _collect_text(case)
    _reasons: list[str] = []
    _score = 0

    # ④ 安全敏感
    if any(_kw in _text for _kw in _SECURITY_KEYWORDS):
        _score += _WEIGHT_SECURITY
        _reasons.append("security_sensitive")

    # ① 业务关键路径
    if any(_kw in _text for _kw in _CRITICAL_PATH_KEYWORDS):
        _score += _WEIGHT_CRITICAL_PATH
        _reasons.append("critical_path")

    # ③ 外部依赖 / 第三方接口
    if any(_kw in _text for _kw in _EXTERNAL_DEP_KEYWORDS):
        _score += _WEIGHT_EXTERNAL_DEP
        _reasons.append("external_dependency")

    # ② 历史失败频率
    _fail = int(history_fail_count or 0)
    if _fail >= _HISTORY_FAIL_HIGH:
        _score += 2
        _reasons.append("history_fail_high(%d)" % _fail)
    elif _fail >= _HISTORY_FAIL_MID:
        _score += 1
        _reasons.append("history_fail_mid(%d)" % _fail)

    # 补充：既有 risk_level 提示（high/critical 未命中上述信号时至少 medium）
    _rl = str(case.get("risk_level") or "").strip().lower()
    if _rl in ("high", "critical", "p0", "p1"):
        if "critical_path" not in _reasons and "security_sensitive" not in _reasons:
            _score += 1
            _reasons.append("risk_level_high")

    if _score >= _SCORE_CRITICAL:
        _level = PRIORITY_CRITICAL
    elif _score >= _SCORE_HIGH:
        _level = PRIORITY_HIGH
    elif _score >= _SCORE_MEDIUM:
        _level = PRIORITY_MEDIUM
    else:
        _level = PRIORITY_LOW

    return _level, _reasons


def priority_label(value: int) -> str:
    """将 0~3 等级映射为标签字符串（非法值回退 low）。"""
    try:
        return _PRIORITY_LABELS[int(value)]
    except (ValueError, KeyError, TypeError):
        return "low"


def query_history_fail_count(case_id: str) -> int:
    """查询 executions 表同 case_id 历史 FAIL 次数（供 Step3 生成期调用）。

    通过 case_store 复用项目数据库连接；任何异常均回退 0，不阻塞生成。
    """
    if not case_id:
        return 0
    try:
        from src.pipeline.utils.case_store import case_store

        with case_store._lock:  # noqa: SLF001 - 复用存储层锁，保证读一致
            _conn = case_store._get_db()
            try:
                _conn.execute(
                    "SELECT COUNT(*) FROM executions WHERE case_id=%s AND status='FAIL'",
                    (str(case_id),),
                )
                _row = _conn.fetchone()
                return int(_row[0]) if _row else 0
            finally:
                with guard("query_history_fail_count: _conn.close()", log=_log):
                    _conn.close()
    except Exception as _e:
        _log.debug("[RiskPriority] 历史失败计数查询失败(回退0): %s", _e)
        return 0
