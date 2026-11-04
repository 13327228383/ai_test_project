# -*- coding: utf-8 -*-
"""Step4 失败根因分类（executions.diagnosis 列）。

将执行异常/失败文本映射到有限枚举，便于按「被测系统问题类别」聚合分析：
  - SUT_DB_NOT_MIGRATED : 被测系统 DB 未迁移（缺表 / auth_user / Django ProgrammingError / OperationalError）
  - SUT_UNREACHABLE      : 被测系统不可达（连接错误 / 超时 / 连接被拒 Errno 10061 / 目标不可达）
  - CREDENTIAL_INVALID   : 凭据无效（HTTP 401/403 / 登录失败 / 凭据错误 / invalid credential）
  - UNKNOWN              : 其他
"""

import logging
from src.core.guarded import guard
from src.pipeline.steps.step4.failure_classifier import _CREDENTIAL_HINTS as _CREDENTIAL_HINTS  # noqa: F401  词表唯一源

_log = logging.getLogger(__name__)

# 诊断枚举值（与 executions.diagnosis 列取值严格对齐）
DIAG_SUT_DB_NOT_MIGRATED = "SUT_DB_NOT_MIGRATED"
DIAG_SUT_UNREACHABLE = "SUT_UNREACHABLE"
DIAG_CREDENTIAL_INVALID = "CREDENTIAL_INVALID"
DIAG_UNKNOWN = "UNKNOWN"

# 优先级：DB 迁移 > 凭据 > 不可达。每类命中关键词即判定，命中即返回。
_DB_MIGRATION_HINTS = (
    "no such table",
    "auth_user",
    "programmingerror",
    "operationalerror",
    "doesn't exist",
    "does not exist",
    "unknown column",
    "relation",
    "migrat",
    "django",
)
_UNREACHABLE_HINTS = (
    "connectionerror",
    "timeout",
    "timed out",
    "errno 10061",
    "connection refused",
    "connection reset",
    "unreachable",
    "failed to connect",
    "cannot connect",
    "econnrefused",
    "network is unreachable",
    "name or service not known",
)


def classify_diagnosis(error_text: str) -> str:
    """将异常/失败文本分类为 diagnosis 枚举值。

    Args:
        error_text: 失败原因/异常文本（建议合并 failure_reason + failure_chain）。

    Returns:
        枚举字符串：SUT_DB_NOT_MIGRATED / SUT_UNREACHABLE / CREDENTIAL_INVALID / UNKNOWN。
    """
    if not error_text:
        return DIAG_UNKNOWN
    text = (error_text or "").lower()
    if any(h in text for h in _DB_MIGRATION_HINTS):
        return DIAG_SUT_DB_NOT_MIGRATED
    if any(h in text for h in _CREDENTIAL_HINTS):
        return DIAG_CREDENTIAL_INVALID
    if any(h in text for h in _UNREACHABLE_HINTS):
        return DIAG_SUT_UNREACHABLE
    return DIAG_UNKNOWN


# ═══════════════════════════════════════════════════════════════════════════
# ★ [已复核-DeepSeek-V3 2026-08-16]（verdict=APPROVED，见 scripts/_tmp/strong_model_review_triage.json）寇豆码 2026-08-16：失败三分类契约桥接
#   失败三分类（P0-6）三选一标签：ENGINE 引擎/断言缺陷 | SUT 被测系统缺陷 | ENV 环境/数据问题。
#   本映射把 diagnosis 枚举（SUT_DB_NOT_MIGRATED 等）归一到三分类，供 Step5
#   _triage.build_failure_triage 作为一条证据信号复用（纯增量，不改既有行为）。
#   字符串值与 src/pipeline/steps/step5/_triage.py 的 TRIAGE_* 常量严格对齐。
# ═══════════════════════════════════════════════════════════════════════════
DIAG_TO_TRIAGE: dict[str, str] = {
    DIAG_SUT_DB_NOT_MIGRATED: "ENV",  # 被测系统 DB 未迁移 → 环境/数据问题
    DIAG_SUT_UNREACHABLE: "ENV",      # 被测系统不可达 → 环境/数据问题
    DIAG_CREDENTIAL_INVALID: "ENV",   # 凭据无效 → 环境/数据问题（测试凭据配置）
    DIAG_UNKNOWN: "UNKNOWN",          # fail-open：证据不足不硬造
}


def diagnosis_to_triage(diag: str) -> str:
    """把 diagnosis 枚举映射到失败三分类（供 Step5 failure_triage 复用）。

    Args:
        diag: classify_diagnosis 的返回值（DIAG_* 枚举）。

    Returns:
        "ENV" / "UNKNOWN"。ENGINE / SUT 需要 Step5 侧更多证据（断言链/期望对比），
        本模块只负责把既有 diagnosis 枚举归一，绝不越权推断。
    """
    return DIAG_TO_TRIAGE.get(diag, "UNKNOWN")


_EXECUTIONS_DIAGNOSIS_ENSURED = False


def ensure_executions_diagnosis_column():
    """确保 executions.diagnosis 列存在（幂等：先查 information_schema.columns，存在则跳过）。"""
    global _EXECUTIONS_DIAGNOSIS_ENSURED
    if _EXECUTIONS_DIAGNOSIS_ENSURED:
        return
    with guard("ensure_executions_diagnosis_column: from src.core.db_manager import get_", log=_log, level=logging.WARNING):
        from src.core.db_manager import get_connection

        conn = get_connection()
        try:
            _rows = conn.execute(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_schema = DATABASE() AND table_name = 'executions' "
                "AND column_name = 'diagnosis' LIMIT 1"
            ).fetchall()
            if not _rows:
                conn.execute(
                    "ALTER TABLE executions ADD COLUMN `diagnosis` VARCHAR(64) DEFAULT NULL "
                    "COMMENT '失败根因分类: SUT_DB_NOT_MIGRATED/SUT_UNREACHABLE/CREDENTIAL_INVALID/UNKNOWN'"
                )
                conn.commit()
            _EXECUTIONS_DIAGNOSIS_ENSURED = True
        finally:
            with guard("ensure_executions_diagnosis_column: conn.close()", log=_log):
                conn.close()
