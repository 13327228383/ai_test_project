# -*- coding: utf-8 -*-
"""全仓唯一「无效断言」判定（F-00 引入，F-06a 复用）。

职责：判断一条用例/一组校验结果是否包含任何有效断言。
- 无效断言 = total_assertions == 0，或任一 check 走了 UNSUPPORTED 兜底。
- 本函数是全仓唯一真源（single source of truth），禁止在展示层/计算层
  各自再写一套 `total_assertions == 0` 判据（PRD F-00⑥ / F-06a⑦）。

通用性：纯函数，不依赖任何业务/系统/行业知识。
"""

from __future__ import annotations

from typing import Any

# UNSUPPORTED 兜底的分派理由常量（F-01 兜底分支写入）
REASON_UNSUPPORTED_CHECK = "UNSUPPORTED_CHECK"
# 兜底分支的状态常量（fail-closed，F-00 永久终态）
STATUS_CHECK_ERROR = "ERROR"


def is_no_valid_assertion(case: dict[str, Any] | None) -> bool:
    """判断 case 是否无有效断言。

    判定规则（任一命中即 True）：
      1. case 为空 / 非 dict → True（无断言可验证）
      2. 顶层 total_assertions 存在且 == 0 → True
      3. 顶层 checks 为空列表 → True
      4. 任一 check 命中 UNSUPPORTED 兜底（status==ERROR 且
         reason==REASON_UNSUPPORTED_CHECK）→ True
      5. ★ v12(2026-08-09): 例外——即使 checks 为空，若 verdict.evidence_items
         非空（有真实执行证据），则判定为有有效断言（False）。

    Args:
        case: 用例字典，可能来自：
          - test_cases 表行（含 total_assertions / checks 字段）
          - Step4 校验结果（含 checks 列表，每项含 status/reason）

    Returns:
        True = 无有效断言（结论不可用）；False = 存在有效断言
    """
    if not isinstance(case, dict):
        return True

    # ★ v12(2026-08-09): 先检查 verdict.evidence_items —— 有真实执行证据即视为有有效断言
    #   根因: FAIL 用例的 checks 可能为空（intent_payload 未传递），但 verdict 中有
    #   真实的执行证据（text_contains FAIL, no_error PASS 等）。若仅检查 checks，
    #   会导致 FAIL 用例被误判为"无有效断言"→ confidence=0.0 → 无法区分真实失败
    #   和证据不足的误判。
    #   通用性: 跨系统/行业/页面 — 所有执行路径都产生 verdict，不依赖 checks 预定义。
    verdict = case.get("verdict") or case.get("evidence_verdict") or {}
    if isinstance(verdict, dict):
        evidence_items = verdict.get("evidence_items") or []
        if isinstance(evidence_items, list) and len(evidence_items) > 0:
            return False  # 有真实执行证据 → 有有效断言

    # 判据 1/2：总量维度
    total = case.get("total_assertions")
    if total is not None:
        try:
            if int(total) == 0:
                return True
        except (TypeError, ValueError):
            # 总量字段损坏：按无有效断言从严处理（fail-closed）
            return True

    # 判据 3：checks 维度（空即无断言）
    checks = case.get("checks")
    if isinstance(checks, list):
        if not checks:
            return True
        # 判据 4：任一 check 走了 UNSUPPORTED 兜底
        for c in checks:
            if not isinstance(c, dict):
                continue
            if (
                c.get("status") == STATUS_CHECK_ERROR
                and c.get("reason") == REASON_UNSUPPORTED_CHECK
            ):
                return True

    return False


def count_unsupported_checks(checks: list[dict[str, Any]] | None) -> int:
    """统计校验结果中走 UNSUPPORTED 兜底的 check 数量（供展示层标注）。"""
    if not isinstance(checks, list):
        return 0
    return sum(
        1
        for c in checks
        if isinstance(c, dict)
        and c.get("status") == STATUS_CHECK_ERROR
        and c.get("reason") == REASON_UNSUPPORTED_CHECK
    )
