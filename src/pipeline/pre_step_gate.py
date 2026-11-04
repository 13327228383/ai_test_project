# -*- coding: utf-8 -*-
"""
Pre-Step Gate — 步骤启动前统一准入检查
========================================

职责：在步骤 N 启动前，校验步骤 N-1 的产出质量。
统一替代原 step2_gate.py / step3_gate.py 中分散的门禁逻辑。

设计原则：
  - 门禁执行时机：步骤 N 启动时（而非步骤 N-1 结束时）
  - 门禁产出归属：步骤 N-1 的指标（阻断率/警告数写入步骤 N-1 的产物表）
  - 统一阈值：单一 BLOCK_THRESHOLD / WARN_THRESHOLD 定义点
  - 统一返回：GateResult 命名元组，所有调用方使用相同契约

配置：
  _GATE_CONFIG 字典驱动，新增步骤门禁只需加一行配置。
"""

import logging
from src.core.guarded import guard
from collections import namedtuple
from typing import Any, Optional
from src.extensions.quality.requirement_gate.check_mixins._intents import _EXEMPT_SOURCES as _exempted_sources  # noqa: F401  词表唯一源

_log = logging.getLogger(__name__)

# ── 统一阈值（唯一定义点） ──────────────────────────────────
BLOCK_THRESHOLD = 0.20  # 阻断率 > 20% → 硬阻断
WARN_THRESHOLD = 0.05  # 阻断率 5-20% → 警告放行

# ── 统一返回类型 ──────────────────────────────────────────
GateResult = namedtuple(
    "GateResult",
    [
        "passed",  # bool: 是否通过（含警告放行）
        "should_block",  # bool: 是否应阻断后续步骤
        "blocker_rate",  # float: 阻断率 0.0-1.0
        "blocker_count",  # int: 阻断项数量
        "total",  # int: 总检查项数量
        "summary",  # str: 人类可读摘要
        "source_step",  # int: 被检查的步骤号
    ],
)

# ── 门禁配置 ──────────────────────────────────────────────
# key = 当前步骤号, value = {source_step, table, label}
_GATE_CONFIG = {
    3: {"source_step": 2, "table": "requirements", "label": "准入检查: 需求校验"},
    4: {"source_step": 3, "table": "test_cases", "label": "准入检查: 用例校验"},
    5: {"source_step": 4, "table": "executions", "label": "准入检查: 执行校验"},
}


def get_gate_label(step: int) -> Optional[str]:
    """获取步骤 step 的准入检查标签（供前端子项展示）。"""
    cfg = _GATE_CONFIG.get(step)
    return cfg["label"] if cfg else None


def run_pre_step_gate(step: int, project: str, version_id: int = 0) -> GateResult:
    """步骤 step 启动前，校验步骤 step-1 的产出质量。

    Args:
        step: 当前即将执行的步骤号（3/4/5）
        project: 项目名称
        version_id: 版本 ID（0 表示自动从 DB 解析）

    Returns:
        GateResult: passed=True 且 should_block=False 表示可继续执行

    Raises:
        PreStepGateBlockedError: 门禁硬阻断时抛出（供 scheduler 捕获）
    """
    cfg = _GATE_CONFIG.get(step)
    if not cfg:
        # Step 1/2 无前置门禁，直接通过
        return GateResult(
            passed=True,
            should_block=False,
            blocker_rate=0.0,
            blocker_count=0,
            total=0,
            summary="无前置门禁",
            source_step=0,
        )

    # 自动解析 version_id（若未提供）
    if version_id <= 0:
        version_id = _resolve_version_id(project)

    source_step = cfg["source_step"]

    _log.info(
        "[PreGate] Step %d 启动前 → 校验 Step %d 产出 (%s)",
        step,
        source_step,
        cfg["table"],
    )

    if source_step == 2:
        return _check_requirement_gate(project, version_id, source_step)
    elif source_step == 3:
        return _check_case_gate(project, version_id, source_step)
    elif source_step == 4:
        return _check_execution_gate(project, version_id, source_step)
    else:
        return GateResult(
            passed=True,
            should_block=False,
            blocker_rate=0.0,
            blocker_count=0,
            total=0,
            summary="未知前置步骤",
            source_step=source_step,
        )


# ═══════════════════════════════════════════════════════════
# 各步骤门禁实现
# ═══════════════════════════════════════════════════════════


def _check_requirement_gate(
    project: str, version_id: int, source_step: int
) -> GateResult:
    """Step 3 前置门禁：校验 Step 2 需求拆解质量。"""
    try:
        from src.core.db_manager import get_connection, release_connection

        conn = None
        all_reqs = []
        try:
            conn = get_connection()
            rows = conn.execute(
                "SELECT req_id, description, module, operation, source_quote, "
                "source_anchor, _verify_status, _evidence_confidence "
                "FROM requirements WHERE project=%s AND version_id=%s",
                (project, version_id),
            ).fetchall()
            all_reqs = [dict(r) for r in rows] if rows else []
        finally:
            if conn:
                release_connection(conn)

        if not all_reqs:
            _log.warning("[PreGate/Step3] Step 2 无需求产出，跳过门禁")
            return GateResult(
                passed=True,
                should_block=False,
                blocker_rate=0.0,
                blocker_count=0,
                total=0,
                summary="Step 2 无需求产出",
                source_step=source_step,
            )

        from src.extensions.quality.requirement_gate import RequirementGate

        gate = RequirementGate()
        result = gate.check_decomposition(all_reqs)

        total = len(all_reqs)
        blocker_count = len(result.blockers)
        blocker_rate = blocker_count / total if total > 0 else 0.0

        if not result.passed and blocker_rate > BLOCK_THRESHOLD:
            _log.error("[PreGate/Step3] ❌ 阻断: %s", result.summary)
            for b in result.blockers[:5]:
                _log.error("  [PreGate/Step3] 阻断项: %s", b.get("reason", ""))
            raise PreStepGateBlockedError(
                f"需求门禁阻断: {result.summary}. "
                f"阻断率 {blocker_rate:.0%} > 阈值 {BLOCK_THRESHOLD:.0%}。"
            )
        elif blocker_rate > WARN_THRESHOLD:
            _log.warning(
                "[PreGate/Step3] ⚠️ 警告: 阻断率 %.0f%% 未达阈值 %.0f%%，放行",
                blocker_rate * 100,
                BLOCK_THRESHOLD * 100,
            )

        if result.warnings:
            _log.warning("[PreGate/Step3] ⚠️ %d 个低匹配项", len(result.warnings))

        _log.info("[PreGate/Step3] ✅ %s", result.summary)
        return GateResult(
            passed=True,
            should_block=False,
            blocker_rate=blocker_rate,
            blocker_count=blocker_count,
            total=total,
            summary=result.summary,
            source_step=source_step,
        )

    except PreStepGateBlockedError:
        raise
    except Exception as e:
        _log.error("[PreGate/Step3] 校验异常，安全阻断: %s", e)
        raise PreStepGateBlockedError(
            f"需求门禁校验异常: {e}. 为避免幻觉需求流入下游，已阻断。"
        ) from e


def _check_case_gate(project: str, version_id: int, source_step: int) -> GateResult:
    """Step 4 前置门禁：校验 Step 3 测试用例质量。"""
    try:
        from src.core.db_manager import get_connection, release_connection

        conn = None
        all_cases = []
        try:
            conn = get_connection()
            rows = conn.execute(
                "SELECT case_id, req_id, title, case_actions_json, expected_outcome, "
                "valid_status, payload_json FROM test_cases "
                "WHERE project=%s AND version_id=%s "
                "AND valid_status NOT IN ('stale', 'retired')",
                (project, version_id),
            ).fetchall()
            all_cases = [dict(r) for r in rows] if rows else []
            _log.warning(
                "[PreGate/Step4] DEBUG: loaded %d cases from DB", len(all_cases)
            )
            # ★ 从 payload_json 提取 _generation_source + low_confidence 以支持门禁豁免
            import json as _json

            _exempted_count = 0
            _downgrade_count = 0
            for c in all_cases:
                with guard("_check_case_gate: payload = ( _json.loads(c['payload_j", log=_log):
                    payload = (
                        _json.loads(c["payload_json"])
                        if isinstance(c["payload_json"], str)
                        else c["payload_json"]
                    )
                    if isinstance(payload, dict):
                        if "_generation_source" in payload:
                            c["_generation_source"] = payload["_generation_source"]
                            if c["_generation_source"] in _exempted_sources:
                                _exempted_count += 1
                        # ★ §12.2规则3 降级用例豁免(2026-07-26): 提取 low_confidence 供 check_intents 豁免
                        #   根因: page_merged 合并覆盖 _generation_source，门禁需用 low_confidence 识别降级用例
                        #   通用性: low_confidence 是 _build_downgrade_cases 统一设置的降级标记
                        if payload.get("low_confidence") is True:
                            c["low_confidence"] = True
                            _downgrade_count += 1
            _log.warning(
                "[PreGate/Step4] DEBUG: _generation_source extracted: %d/%d exempted, %d downgraded",
                _exempted_count,
                len(all_cases),
                _downgrade_count,
            )
        finally:
            if conn:
                release_connection(conn)

        if not all_cases:
            _log.warning("[PreGate/Step4] Step 3 无用例产出，跳过门禁")
            return GateResult(
                passed=True,
                should_block=False,
                blocker_rate=0.0,
                blocker_count=0,
                total=0,
                summary="Step 3 无用例产出",
                source_step=source_step,
            )

        from src.extensions.quality.requirement_gate import RequirementGate

        gate = RequirementGate()
        result = gate.check_intents(all_cases)

        # 持久化语义匹配分，标记需人工复核的测试用例
        need_review = gate.persist_scores(all_cases, project or "default")
        if need_review:
            _log.warning(
                "[PreGate/Step4] %d 条用例语义对齐分低于 0.55，已标记需人工复核",
                len(need_review),
            )

        total = len(all_cases)
        blocker_count = len(result.blockers)
        blocker_rate = blocker_count / total if total > 0 else 0.0

        should_block = False
        if not result.passed and blocker_rate > BLOCK_THRESHOLD:
            _log.error(
                "[PreGate/Step4] ❌ 阻断: %s (阻断率 %.0f%%)",
                result.summary,
                blocker_rate * 100,
            )
            for b in result.blockers[:5]:
                _log.error("  [PreGate/Step4] 阻断项: %s", b.get("reason", ""))
            should_block = True
        elif blocker_rate > WARN_THRESHOLD:
            _log.warning(
                "[PreGate/Step4] ⚠️ 警告: %s (阻断率 %.0f%%)",
                result.summary,
                blocker_rate * 100,
            )

        if result.warnings:
            _log.warning("[PreGate/Step4] ⚠️ %d 个低匹配项", len(result.warnings))

        if should_block:
            _log.error("[PreGate/Step4] ❌ 阻断: %s", result.summary)
            raise PreStepGateBlockedError(
                f"用例门禁阻断: {result.summary}. "
                f"阻断率 {blocker_rate:.0%} > 阈值 {BLOCK_THRESHOLD:.0%}。"
            )

        _log.info("[PreGate/Step4] ✅ %s", result.summary)
        return GateResult(
            passed=True,
            should_block=False,
            blocker_rate=blocker_rate,
            blocker_count=blocker_count,
            total=total,
            summary=result.summary,
            source_step=source_step,
        )

    except PreStepGateBlockedError:
        raise
    except Exception as e:
        _log.error("[PreGate/Step4] 校验异常，安全阻断: %s", e)
        raise PreStepGateBlockedError(
            f"用例门禁校验异常: {e}. 为避免低质量用例流入下游，已阻断。"
        ) from e


def _check_execution_gate(
    project: str, version_id: int, source_step: int
) -> GateResult:
    """Step 5 前置门禁：校验 Step 4 是否有执行记录。"""
    try:
        from src.core.db_manager import get_connection, release_connection

        conn = None
        try:
            conn = get_connection()
            row = conn.execute(
                "SELECT COUNT(*) AS cnt FROM executions "
                "WHERE project=%s AND version_id=%s",
                (project, version_id),
            ).fetchone()
            total = row["cnt"] if row and row["cnt"] else 0
        finally:
            if conn:
                release_connection(conn)

        if total == 0:
            _log.warning("[PreGate/Step5] Step 4 无执行记录，报告将为空")
            return GateResult(
                passed=True,
                should_block=False,
                blocker_rate=0.0,
                blocker_count=0,
                total=0,
                summary="Step 4 无执行记录（报告将为空）",
                source_step=source_step,
            )

        _log.info("[PreGate/Step5] ✅ Step 4 有 %d 条执行记录", total)
        return GateResult(
            passed=True,
            should_block=False,
            blocker_rate=0.0,
            blocker_count=0,
            total=total,
            summary=f"{total} 条执行记录",
            source_step=source_step,
        )

    except PreStepGateBlockedError:
        raise
    except Exception as e:
        _log.warning("[PreGate/Step5] 校验异常（不阻断）: %s", e)
        return GateResult(
            passed=True,
            should_block=False,
            blocker_rate=0.0,
            blocker_count=0,
            total=0,
            summary=f"执行记录校验异常: {e}",
            source_step=source_step,
        )


# ═══════════════════════════════════════════════════════════
# 异常类型
# ═══════════════════════════════════════════════════════════


class PreStepGateBlockedError(Exception):
    """Pre-Step Gate 硬阻断异常。"""

    pass


def _resolve_version_id(project: str) -> int:
    """当前活跃版本 id（权威来源：project_versions.status='active'）。"""
    with guard("_resolve_version_id: from src.users.project_auth import get_", log=_log, level=logging.WARNING):
        from src.users.project_auth import get_active_version

        ver = get_active_version(project)
        if ver:
            try:
                return int(ver.get("id") or 0)
            except Exception:
                return 0
    return 0
