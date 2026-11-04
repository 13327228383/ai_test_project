# -*- coding: utf-8 -*-
"""★ P2-12 SelfEvalRunner — 引擎自评估运行器。

定位：
  对比 golden_set（期望结论）与引擎实际产出的 verdict，计算自评估指标：
  accuracy / false_pass_rate / false_fail_rate / coverage。
  替代现有「85 恒定分」自评估（无真值对比），建立可量化的引擎质量基线。

评估指标定义：
  - accuracy: 正确数 / 总数（expected == actual）
  - false_pass_rate: 假PASS数 / 总数（expected=FAIL but actual=PASS）
  - false_fail_rate: 假FAIL数 / 总数（expected=PASS but actual=FAIL）
  - coverage: 已评估数 / golden_set 总数（有实际结果的场景比例）

职责边界（§12.4）：
  - evaluate: 纯算法，计算指标（不查 DB）
  - run: 从 DB 查询 golden_set + actual_results，调用 evaluate
  - 不依赖业务术语，scenario 是通用场景描述
  - 降级：无数据 → 返回空指标，不阻断

通用性（§12.4）：
  1. 跨系统：scenario 是通用文本，不绑定业务
  2. 跨行业：任何测试场景都适用
  3. 降级：无 golden_set / 无 actual_results → 返回空指标
  4. 职责边界：仅评估，不修改 verdict
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from src.eval.golden import GoldenSetStore, get_golden_store

_log = logging.getLogger(__name__)


def _normalize_verdict(verdict: str) -> str:
    """归一化 verdict（大写、去空格）。"""
    return str(verdict or "").strip().upper()


def _is_pass(verdict: str) -> bool:
    """判断 verdict 是否为 PASS。"""
    return _normalize_verdict(verdict) == "PASS"


def _is_fail(verdict: str) -> bool:
    """判断 verdict 是否为 FAIL。"""
    return _normalize_verdict(verdict) == "FAIL"


class SelfEvalRunner:
    """引擎自评估运行器。

    使用方式:
        runner = SelfEvalRunner()
        # 方式1: 纯算法评估（调用方提供数据）
        result = runner.evaluate(golden_items, actual_results)
        # 方式2: 从 DB 查询并评估
        result = runner.run("project", actual_results)
    """

    def __init__(self, store: Optional[GoldenSetStore] = None):
        self._store = store or get_golden_store()
        self._last_error: str = ""

    # ═══════════════════════════════════════════════════════════════
    # evaluate: 纯算法评估（不查 DB）
    # ═══════════════════════════════════════════════════════════════

    def evaluate(
        self,
        golden_items: list[dict[str, Any]],
        actual_results: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        """纯算法评估：对比 golden_set 与实际结果，计算指标。

        参数:
            golden_items: [{case_id, scenario, expected_verdict, expected_confidence}, ...]
            actual_results: {key: {actual_verdict, actual_confidence}, ...}
                           key 可以是 case_id 或 scenario（优先 case_id 匹配）

        返回: {
            total, evaluated, correct, false_pass, false_fail,
            accuracy, false_pass_rate, false_fail_rate, coverage,
            details: [{scenario, expected, actual, match, ...}]
        }
        """
        try:
            self._last_error = ""
            if not golden_items:
                return self._empty_result()

            total = len(golden_items)
            evaluated = 0
            correct = 0
            false_pass = 0
            false_fail = 0
            details: list[dict[str, Any]] = []

            for item in golden_items:
                scenario = item.get("scenario", "")
                case_id = item.get("case_id", "")
                expected_verdict = _normalize_verdict(item.get("expected_verdict", ""))
                expected_conf = float(item.get("expected_confidence") or 0)

                # ★P2-12接入: 优先用 case_id 匹配，无 case_id 时降级用 scenario
                actual = actual_results.get(case_id, {})
                if not actual and scenario:
                    actual = actual_results.get(scenario, {})
                actual_verdict = _normalize_verdict(actual.get("actual_verdict", ""))
                actual_conf = float(actual.get("actual_confidence") or 0)

                if not actual_verdict:
                    # 无实际结果 → 未评估
                    details.append(
                        {
                            "scenario": scenario,
                            "case_id": case_id,
                            "expected_verdict": expected_verdict,
                            "actual_verdict": "",
                            "match": False,
                            "status": "not_evaluated",
                        }
                    )
                    continue

                evaluated += 1
                match = expected_verdict == actual_verdict

                if match:
                    correct += 1
                    status = "correct"
                elif expected_verdict == "FAIL" and _is_pass(actual_verdict):
                    false_pass += 1
                    status = "false_pass"
                elif expected_verdict == "PASS" and _is_fail(actual_verdict):
                    false_fail += 1
                    status = "false_fail"
                else:
                    status = "mismatch"

                details.append(
                    {
                        "scenario": scenario,
                        "case_id": case_id,
                        "expected_verdict": expected_verdict,
                        "actual_verdict": actual_verdict,
                        "expected_confidence": expected_conf,
                        "actual_confidence": actual_conf,
                        "match": match,
                        "status": status,
                    }
                )

            # 计算指标
            accuracy = correct / evaluated if evaluated > 0 else 0.0
            false_pass_rate = false_pass / evaluated if evaluated > 0 else 0.0
            false_fail_rate = false_fail / evaluated if evaluated > 0 else 0.0
            coverage = evaluated / total if total > 0 else 0.0

            return {
                "total": total,
                "evaluated": evaluated,
                "correct": correct,
                "false_pass": false_pass,
                "false_fail": false_fail,
                "accuracy": round(accuracy, 4),
                "false_pass_rate": round(false_pass_rate, 4),
                "false_fail_rate": round(false_fail_rate, 4),
                "coverage": round(coverage, 4),
                "details": details,
            }

        except Exception as e:
            self._last_error = str(e)[:200]
            _log.warning("[SelfEvalRunner] evaluate 失败: %s", str(e)[:120])
            return self._empty_result()

    def _empty_result(self) -> dict[str, Any]:
        """返回空结果。"""
        return {
            "total": 0,
            "evaluated": 0,
            "correct": 0,
            "false_pass": 0,
            "false_fail": 0,
            "accuracy": 0.0,
            "false_pass_rate": 0.0,
            "false_fail_rate": 0.0,
            "coverage": 0.0,
            "details": [],
        }

    # ═══════════════════════════════════════════════════════════════
    # run: 从 DB 查询 golden_set 并评估
    # ═══════════════════════════════════════════════════════════════

    def run(
        self,
        project: str,
        actual_results: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        """从 DB 查询 golden_set，对比 actual_results，计算指标。

        参数:
            project: 项目名
            actual_results: {scenario: {actual_verdict, actual_confidence}, ...}
                           （调用方负责从 executions 表查询并按 scenario 组织）

        返回: 同 evaluate
        """
        try:
            self._last_error = ""
            if not project:
                return self._empty_result()

            golden_items = self._store.get_scenarios(project)
            if not golden_items:
                self._last_error = "no golden_set scenarios"
                return self._empty_result()

            return self.evaluate(golden_items, actual_results)

        except Exception as e:
            self._last_error = str(e)[:200]
            _log.warning("[SelfEvalRunner] run 失败: %s", str(e)[:120])
            return self._empty_result()

    # ═══════════════════════════════════════════════════════════════
    # 辅助方法
    # ═══════════════════════════════════════════════════════════════

    def format_report(self, result: dict[str, Any]) -> str:
        """将评估结果格式化为 Markdown 报告。

        通用性：纯文本，不依赖业务。
        """
        lines = [
            "",
            "## 引擎自评估报告",
            "",
            "| 指标 | 数值 |",
            "|------|------|",
            "| 黄金集总数 | %d |" % result.get("total", 0),
            "| 已评估数 | %d |" % result.get("evaluated", 0),
            "| 正确数 | %d |" % result.get("correct", 0),
            "| 假PASS数 | %d |" % result.get("false_pass", 0),
            "| 假FAIL数 | %d |" % result.get("false_fail", 0),
            "| 准确率 | %.1f%% |" % (result.get("accuracy", 0) * 100),
            "| 假PASS率 | %.1f%% |" % (result.get("false_pass_rate", 0) * 100),
            "| 假FAIL率 | %.1f%% |" % (result.get("false_fail_rate", 0) * 100),
            "| 覆盖率 | %.1f%% |" % (result.get("coverage", 0) * 100),
            "",
        ]

        details = result.get("details", [])
        if details:
            lines += ["### 评估明细", ""]
            lines.append("| 场景 | 期望 | 实际 | 匹配 | 状态 |")
            lines.append("|------|------|------|------|------|")
            for d in details[:50]:
                match_icon = "✅" if d.get("match") else "❌"
                lines.append(
                    "| %s | %s | %s | %s | %s |"
                    % (
                        d.get("scenario", "")[:40],
                        d.get("expected_verdict", ""),
                        d.get("actual_verdict", "") or "—",
                        match_icon,
                        d.get("status", ""),
                    )
                )
            if len(details) > 50:
                lines.append("| ... | 共 %d 条 | ... | ... | ... |" % len(details))

        lines.append("")
        return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
# 单例（惰性初始化）
# ═══════════════════════════════════════════════════════════════

_default_runner: Optional[SelfEvalRunner] = None


def get_runner() -> SelfEvalRunner:
    """获取默认 SelfEvalRunner 实例（单例，惰性初始化）。"""
    global _default_runner
    if _default_runner is None:
        _default_runner = SelfEvalRunner()
    return _default_runner
