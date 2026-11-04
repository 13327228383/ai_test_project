# -*- coding: utf-8 -*-
"""验证台账核对钩子（VerificationHook）—— 流水线强化专项 T1。

★ 职责：把「短路直传用例路径」（case_upload.run_uploaded_cases → execute(plan)）
  从「执行完即结束」升级为「执行完必核对台账」：逐用例将引擎预期写入
  LedgerStore，读取独立真值（parse_spec_truth 内置映射 + 靶子 spec.md），
  经 VerificationRunner.compare 比对；不一致自动经 RemediationStateMachine
  置 open（disposition=fix_target）进入补做台账；真值缺失标 truth_unavailable
  （绝不静默通过）。执行报告附带 `ledger_check` 摘要。

★ 设计约束（对齐 design §7.6）：
  - 纯挂载钩子：不改 target_cfgs.py 探针语义、不改 parse_cases_file 解析逻辑。
  - 依赖方向：仅依赖 verification 层（runner/schema），不反向依赖 pipeline。
  - 挂载方式：attach_to_report(report, parsed, target_key) 返回 report 副本，
    内部完成全部台账写入，调用方（case_upload.run_uploaded_cases）只需在
    execute(plan) 返回后调用一次。
"""
from __future__ import annotations

import logging
from typing import Any

from src.verification.runner import VerificationRunner, parse_spec_truth
from src.verification.schema import Consistency

_log = logging.getLogger(__name__)


class VerificationHook:
    """短路链台账核对钩子。

    典型用法::

        hook = VerificationHook(ledger_dir=...)
        report = execute(plan)
        report = hook.attach_to_report(report, parsed, target_key)
        # report["ledger_check"] 已附加
    """

    def __init__(self, ledger_dir: str | None = None) -> None:
        """初始化。

        Args:
            ledger_dir: 台账根目录；缺省使用 VerificationRunner 默认目录
                （src/verification/ledger），便于测试注入临时目录。
        """
        self.runner = VerificationRunner(ledger_dir=ledger_dir)

    # ─────────────────────────────────────────────────────────────────
    # 公开 API
    # ─────────────────────────────────────────────────────────────────
    def attach_to_report(
        self,
        report: dict[str, Any],
        parsed: dict[str, Any],
        target_key: str,
    ) -> dict[str, Any]:
        """在短路执行报告上附加 `ledger_check` 台账核对摘要。

        Args:
            report: execute(plan) 返回的报告 dict（含 summary / cases 等）。
            parsed: parse_cases_file 的解析结果（含 cases / authz_probes）。
            target_key: 靶子 key（ledger 按 target 落盘）。

        Returns:
            report 副本，并附加 ``report["ledger_check"]``：
              {checked, consistent_true, consistent_false, truth_unavailable,
               mismatches: [...], by_case: {...}}
        """
        report = dict(report or {})
        target = str(target_key or report.get("target_key") or report.get("target") or "")
        if not target:
            _log.warning("[VerificationHook] 缺少 target，跳过台账核对")
            report["ledger_check"] = {
                "checked": 0,
                "consistent_true": 0,
                "consistent_false": 0,
                "truth_unavailable": 0,
                "mismatches": [],
                "by_case": {},
                "skipped_reason": "缺少 target_key",
            }
            return report

        truth_map = parse_spec_truth(target)
        # 兼容多种执行报告结构：浏览器/API 模式结果在 `cases`，权限矩阵模式在 `probes`
        results = list(report.get("cases") or []) + list(report.get("probes") or [])
        by_case: dict[str, dict[str, Any]] = {}

        # 优先按用例结果逐条核对；无结果但 parsed 声明了用例时，标记 truth_unavailable。
        checked_ids: list[str] = []
        for result in results:
            case_id = str(
                result.get("case_id") or result.get("id") or result.get("title") or ""
            )
            if not case_id:
                continue
            checked_ids.append(case_id)
            by_case[case_id] = self._compare_one(
                runner=self.runner,
                target=target,
                case_id=case_id,
                result=result,
                truth_map=truth_map,
            )

        # 兜底：parsed 声明了用例但报告中无对应结果（执行被短路/异常）
        for case in parsed.get("cases") or []:
            case_id = str(case.get("case_id") or "")
            if not case_id or case_id in by_case:
                continue
            checked_ids.append(case_id)
            by_case[case_id] = self._compare_one(
                runner=self.runner,
                target=target,
                case_id=case_id,
                result=case,
                truth_map=truth_map,
            )

        for probe in parsed.get("authz_probes") or []:
            probe_id = str(probe.get("id") or probe.get("case_id") or "")
            if not probe_id or probe_id in by_case:
                continue
            checked_ids.append(probe_id)
            by_case[probe_id] = self._compare_one(
                runner=self.runner,
                target=target,
                case_id=probe_id,
                result=probe,
                truth_map=truth_map,
            )

        consistent_true = sum(
            1 for v in by_case.values() if v.get("consistent") == Consistency.TRUE.value
        )
        consistent_false = sum(
            1 for v in by_case.values() if v.get("consistent") == Consistency.FALSE.value
        )
        truth_unavailable = sum(
            1
            for v in by_case.values()
            if v.get("consistent") == Consistency.TRUTH_UNAVAILABLE.value
        )
        mismatches = [
            v for v in by_case.values() if v.get("consistent") == Consistency.FALSE.value
        ]

        report["ledger_check"] = {
            "checked": len(checked_ids),
            "consistent_true": consistent_true,
            "consistent_false": consistent_false,
            "truth_unavailable": truth_unavailable,
            "mismatches": mismatches,
            "by_case": by_case,
        }
        _log.info(
            "[VerificationHook] ledger 核对完成 target=%s checked=%d "
            "一致=%d 不一致=%d 真值缺失=%d",
            target,
            len(checked_ids),
            consistent_true,
            consistent_false,
            truth_unavailable,
        )
        return report

    # ─────────────────────────────────────────────────────────────────
    # 内部实现
    # ─────────────────────────────────────────────────────────────────
    def _compare_one(
        self,
        runner: VerificationRunner,
        target: str,
        case_id: str,
        result: dict[str, Any],
        truth_map: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        """对单条用例执行 write_engine_expected → compare，返回核对结论。"""
        try:
            expected = self._build_engine_expected(result)
            runner.write_engine_expected(target, case_id, expected)
            truth_value = truth_map.get(case_id)
            cons = runner.compare(
                target,
                case_id,
                truth_value,
                truth_source="spec",
                truth_detail=self._truth_detail(truth_value),
            )
            rec = runner.store.get(target, case_id)
            return {
                "case_id": case_id,
                "consistent": cons.value,
                "status": rec.status if rec else "",
                "truth_value": truth_value,
                "truth_detail": self._truth_detail(truth_value),
                "engine_expected": expected,
            }
        except Exception as e:  # noqa: BLE001 —— 单条失败不阻断整批核对
            _log.warning(
                "[VerificationHook] 用例核对异常 case=%s: %s", case_id, str(e)[:200]
            )
            return {
                "case_id": case_id,
                "consistent": "error",
                "status": "",
                "truth_value": None,
                "truth_detail": str(e)[:200],
                "engine_expected": {},
            }

    def _build_engine_expected(self, result: dict[str, Any]) -> dict[str, Any]:
        """从单条执行结果构造「引擎预期」dict（与 VerificationRunner 比对口径对齐）。

        提取 status_code / overall 等可判定字段；无字段时保留原始结果
        （比对侧 `_is_consistent` 会逐 key 检查真值字段，缺失即不一致）。
        """
        expected: dict[str, Any] = {}
        for key in (
            "status_code",
            "code",
            "overall",
            "result",
            "expected",
            "allowed",
            "denied",
        ):
            if result.get(key) is not None:
                expected[key] = result[key]
        if not expected and result:
            # 无标准字段时，保留关键判断字段（method/path/expected 等）
            for key in ("method", "path", "expected", "role", "fill_invalid"):
                if result.get(key) is not None:
                    expected[key] = result[key]
        return expected

    @staticmethod
    def _truth_detail(truth_value: dict[str, Any] | None) -> str:
        """从真值 dict 提取可读说明。"""
        if not truth_value:
            return ""
        detail = truth_value.get("detail") or truth_value.get("desc") or ""
        return str(detail)
