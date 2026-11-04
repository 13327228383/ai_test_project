# -*- coding: utf-8 -*-
"""ADI 深断言感知信号闸门（P0-3 增量，新增模块）。

★ 设计意图（设计文档已归档，见 .workbuddy/memory/MEMORY.md §1.2 / §3）：
  复用 adi.py 的 ADI_SCORE_TABLE（46 族深度评分表），判定单条 check 是否为「深断言」
  （深度分 >= 2）。统计「深断言导致的 fail 数 / 总 fail 数」占比
  （deep_assertion_fail_ratio），当占比超过阈值时，将结论标记为 SIGNAL
  （信号而非回归），从而在 E2 置信度闸门处 **跳过** 假 PASS 拦截
  （深断言拉低 PASS 率属于预期信号，不应被误判为假 PASS / 回归）。

★ 默认零行为变更：
  - 模块级开关 ADI_SIGNAL_GATE_ENABLED 默认 False（仅度量 / 不拦截）。
  - 禁用时 DeepAssertionMetrics 仍可用，但 AdiSignalGate.classify() 直接返回 "OK"
    且对外部零行为变更；collect_adi 中 deep_assertion_fail_ratio 恒为 0.0，绝不报错。
  - 阈值 ADI_SIGNAL_GATE_THRESHOLD 默认 0.5，可从环境变量 / config_loader 读取。

★ fail-closed：任何内部异常 → 返回保守值（ratio=0.0 / "OK"），绝不 raise。

★ §14 关键改动 [未复核-需 GLM/DeepSeek 把关]：
  新增核心判定逻辑 + 接入 step4_executor / w3_collectors / _confidence_gate，
  未经过强模型（GLM/DeepSeek）复核，请勿视为已审核。
"""
from __future__ import annotations
from src.core.guarded import guard

import json
import logging
from typing import Any, Literal

from src.core.pipeline_config import _read_config_value as _cfg
from src.pipeline.steps.step3.oracle.adi import ADI_SCORE_TABLE

_log = logging.getLogger(__name__)

# ── 模块级开关（默认值；运行期 classify 仍通过 _cfg 读取，支持运行时改 env）──
ADI_SIGNAL_GATE_ENABLED: bool = bool(_cfg("ADI_SIGNAL_GATE_ENABLED", False))
ADI_SIGNAL_GATE_THRESHOLD: float = float(_cfg("ADI_SIGNAL_GATE_THRESHOLD", 0.5))
# 深度分 >= 2 视为深断言（值级/跨轨=3，属性/协议/文本=2）
DEEP_ASSERTION_SCORE_MIN: int = 2


def _resolve_check_type(check_type: str) -> int:
    """反查 check 类型的 ADI 深度分（复用 ADI_SCORE_TABLE + 别名兜底）。

    返回 0 ~ 3；归一失败 / 未知族 → 0（fail-closed）。
    """
    try:
        from src.pipeline.steps.step4.browser_tools._taxonomy import resolve_check_type

        normalized = resolve_check_type(check_type) or ""
        normalized = str(normalized)
    except Exception:  # noqa: BLE001
        normalized = ""
    if normalized and normalized in ADI_SCORE_TABLE:
        return int(ADI_SCORE_TABLE[normalized])
    # 直接命中原始族名（部分 evidence 别名直接写族名）
    if check_type in ADI_SCORE_TABLE:
        return int(ADI_SCORE_TABLE[check_type])
    return 0


class DeepAssertionMetrics:
    """单 run 的深断言度量采集。

    输入：单 run 的 check/断言结果列表，每条可为：
      - 字符串：check 类型名（视为一次失败信号，用于静态代理）
      - dict：含 type/check_type/kind/evidence 之一 + 可选 passed/status/ok
    统计：deep_fail（深断言导致的 fail 数）/ total_fail（总 fail 数）/ ratio。
    """

    @staticmethod
    def is_deep(check_type: str) -> bool:
        """判定某 check 类型是否为深断言（深度分 >= 2）。"""
        try:
            return _resolve_check_type(check_type) >= DEEP_ASSERTION_SCORE_MIN
        except Exception:  # noqa: BLE001
            return False

    @classmethod
    def collect(cls, results: list[Any] | None) -> dict[str, Any]:
        """统计深断言 fail 占比，返回 {deep_fail, total_fail, ratio}。

        fail-closed：异常 / 空输入 → {0, 0, 0.0}。
        """
        deep_fail = 0
        total_fail = 0
        try:
            for item in results or []:
                ctype, is_fail = cls._extract(item)
                if not ctype:
                    continue
                if is_fail:
                    total_fail += 1
                    if cls.is_deep(ctype):
                        deep_fail += 1
        except Exception as _e:  # noqa: BLE001
            _log.warning("[DeepAssertionMetrics] collect 异常(返回0): %s", _e)
            return {"deep_fail": 0, "total_fail": 0, "ratio": 0.0}
        ratio = round(deep_fail / total_fail, 4) if total_fail else 0.0
        return {"deep_fail": deep_fail, "total_fail": total_fail, "ratio": ratio}

    @staticmethod
    def _extract(item: Any) -> tuple[str, bool]:
        """从单条 check 结果抽取 (type, is_fail)。

        无法判定 pass/fail 时保守视为失败（is_fail=True），
        用于无执行结果的静态代理场景（collect_adi 探针）。
        """
        if isinstance(item, str):
            return item, True
        if not isinstance(item, dict):
            return "", False
        ctype = (
            item.get("type")
            or item.get("check_type")
            or item.get("kind")
            or item.get("evidence")
            or ""
        )
        ctype = str(ctype)
        status = str(item.get("status", "") or "").upper()
        if status:
            is_fail = status not in ("PASS", "PASSED", "OK")
        elif "passed" in item:
            is_fail = not bool(item["passed"])
        elif "ok" in item:
            is_fail = not bool(item["ok"])
        else:
            is_fail = True
        return ctype, is_fail


class AdiSignalGate:
    """深断言信号闸门：将度量结论映射为 SIGNAL / REGRESSION / OK。

    - OK：无 fail（total_fail == 0）→ 不需要信号。
    - SIGNAL：深断言 fail 占比 > threshold → 信号而非回归（不阻断）。
    - REGRESSION：存在 fail 但深断言占比未超阈值 → 按常规回归处理。
    """

    # 结论枚举
    OK: str = "OK"
    SIGNAL: str = "SIGNAL"
    REGRESSION: str = "REGRESSION"

    @classmethod
    def classify(
        cls,
        metrics: dict[str, Any] | None,
        threshold: float | None = None,
    ) -> Literal["REGRESSION", "SIGNAL", "OK"]:
        """判定信号结论。禁用开关时直接返回 OK（零行为变更）。

        fail-closed：异常 / 缺字段 → OK。
        """
        # 模块级开关：禁用时完全不介入（零行为变更）
        if not bool(_cfg("ADI_SIGNAL_GATE_ENABLED", ADI_SIGNAL_GATE_ENABLED)):
            return cls.OK
        try:
            _thr = (
                float(threshold)
                if threshold is not None
                else float(
                    _cfg("ADI_SIGNAL_GATE_THRESHOLD", ADI_SIGNAL_GATE_THRESHOLD)
                )
            )
            if not isinstance(metrics, dict):
                return cls.OK
            total_fail = int(metrics.get("total_fail", 0) or 0)
            ratio = float(metrics.get("ratio", 0.0) or 0.0)
            if total_fail <= 0:
                return cls.OK
            if ratio > _thr:
                return cls.SIGNAL
            return cls.REGRESSION
        except Exception as _e:  # noqa: BLE001
            _log.warning("[AdiSignalGate] classify 异常(返回OK): %s", _e)
            return cls.OK


def extract_case_check_types(case: dict[str, Any] | None) -> list[str]:
    """从用例 payload 抽取全部 assert/check 的归一化族名（用于静态深断言占比代理）。

    覆盖：case["checks"] / case["payload"]["checks"] / case["verification_plan"]{ui,api,db}.
    仅抽取类型名，不依赖执行结果，供 collect_adi 探针与 step4 预分类使用。
    """
    types: list[str] = []
    if not isinstance(case, dict):
        return types
    checks = case.get("checks") or (case.get("payload") or {}).get("checks") or []
    if isinstance(checks, str):
        try:
            checks = json.loads(checks)
        except Exception:  # noqa: BLE001
            checks = []
    for c in checks or []:
        if isinstance(c, dict):
            t = c.get("type") or c.get("check_type") or c.get("kind")
            if t:
                types.append(str(t))
    plan = case.get("verification_plan") or {}
    if isinstance(plan, str):
        try:
            plan = json.loads(plan)
        except Exception:  # noqa: BLE001
            plan = {}
    if isinstance(plan, dict):
        for track in ("ui", "api", "db"):
            tchecks = (
                plan.get(track) or {}
            ).get("checks") if isinstance(plan.get(track), dict) else None
            for c in (tchecks or []):
                if isinstance(c, dict):
                    t = c.get("type") or c.get("kind")
                    if t:
                        types.append(str(t))
    return types


# 失败轨 → 规范深断言族（与 ADI_SCORE_TABLE 口径一致：db/api 轨值级=3，ui 文本级=2）。
# 失败轨的断言均属真实深度失败，按深断言处理，使「仅浅断言失败」与「深断言失败」
# 在比率上可区分（浅存在性/状态/性能族 score=1 不计入 deep_fail）。
_TRACK_DEEP_FAMILY: dict[str, str] = {
    "db": "db_record_exists",
    "api": "api_response_body",
    "ui": "text_contains",
}


def _is_pass(value: Any) -> bool:
    """归一化 passed 取值为布尔（支持 bool / str / int / 缺省）。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().upper() in ("PASS", "PASSED", "OK", "TRUE", "1", "YES")
    return True


def extract_failed_checks(result: dict[str, Any] | None) -> list[dict[str, Any]]:
    """从单条执行结果抽取失败 check 列表（供 ``DeepAssertionMetrics.collect`` 消费）。

    来源：
      1) ``result["checks"]``：AI 生成断言，含 type 与 passed（精确 per-check 失败）。
      2) ``result["step_results"][_verify_result]``：三轨验证结果，失败且已验证的轨
         映射为对应深断言族（见 ``_TRACK_DEEP_FAMILY``）。

    fail-closed：任何异常 / 缺字段 → 返回 ``[]``（ratio=0.0，不报错）。
    """
    out: list[dict[str, Any]] = []
    if not isinstance(result, dict):
        return out
    # 1) checks[]（精确 per-check 失败，type 权威）
    with guard("extract_failed_checks: for c in (result.get('checks') or []", log=_log):
        for c in (result.get("checks") or []):
            if not isinstance(c, dict):
                continue
            if _is_pass(c.get("passed", True)):
                continue
            ctype = str(
                c.get("type")
                or c.get("check_type")
                or c.get("kind")
                or c.get("evidence")
                or ""
            ).strip()
            if ctype:
                out.append({"type": ctype, "passed": False})
    # 2) step_results._verify_result（轨级失败 → 深断言族）
    with guard("extract_failed_checks: for sr in (result.get('step_results'", log=_log):
        for sr in (result.get("step_results") or []):
            if not isinstance(sr, dict):
                continue
            vr = sr.get("_verify_result")
            if not isinstance(vr, dict):
                continue
            for track in ("ui", "api", "db"):
                tdata = vr.get(track) or {}
                if not isinstance(tdata, dict):
                    continue
                if _is_pass(tdata.get("passed", True)):
                    continue
                if not tdata.get("verified", True):
                    continue
                out.append(
                    {"type": _TRACK_DEEP_FAMILY.get(track, "text_contains"), "passed": False}
                )
    return out
