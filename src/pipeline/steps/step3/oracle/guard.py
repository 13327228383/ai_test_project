# -*- coding: utf-8 -*-
"""ADI 门禁编排（T01/T02/T03，W2 P0-3）—— apply_adi_gate。

行为矩阵：
  ADI_GATE_ENABLED=False（默认）→ 仅写 adi/adi_detail（度量模式，不拦截、不标记）
  ADI_GATE_ENABLED=True 且 adi < ADI_MIN_THRESHOLD
    → case["needs_review"]=True
      case["needs_review_reason"]=f"ADI_INSUFFICIENT(adi={adi} < {thr})"
      report["blocked"].append(case_id)

低分用例**仍落库**（fail-closed ≠ 消失）：保留「引擎生成能力弱」的可观测信号。

★ §14 关键改动 [已复核-DeepSeek-V4-Flash 2026-08-05]：
  消费方 T02（_modeling_intents.py）/ T03（step4_executor.py）均为核心路径；
  未经过强模型（GLM/DeepSeek）复核，请勿视为已审核。
"""
from __future__ import annotations

import logging
from typing import Any

from src.core.pipeline_config import _read_config_value as _cfg
from src.pipeline.steps.step3.oracle.adi import compute_adi

_log = logging.getLogger(__name__)

# Step3 门禁接入后置入的最新报告（供进程内自证 / 探针读取）
LAST_ADI_GATE_REPORT: dict[str, Any] = {}


def apply_adi_gate(cases: list[dict[str, Any]]) -> dict[str, Any]:
    """为每条 case 写入 adi/adi_detail；开关开启时对低分用例标 needs_review。

    返回 report：
      {"enabled", "threshold", "total", "blocked":[case_id],
       "hist":{0:n,1:n,2:n,3:n}, "avg":float, "unresolved_top":[(evidence,cnt)]}
    """
    global LAST_ADI_GATE_REPORT
    enabled = bool(_cfg("ADI_GATE_ENABLED", False))
    threshold = int(_cfg("ADI_MIN_THRESHOLD", 1))
    report: dict[str, Any] = {
        "enabled": enabled,
        "threshold": threshold,
        "total": 0,
        "blocked": [],
        "hist": {0: 0, 1: 0, 2: 0, 3: 0},
        "avg": 0.0,
        "unresolved_top": [],
    }
    scores: list[int] = []
    unresolved_counter: dict[str, int] = {}

    for case in cases or []:
        if not isinstance(case, dict):
            continue
        try:
            adi, detail = compute_adi(case)
        except Exception as e:  # noqa: BLE001 —— fail-closed：异常按 0 分处理并标记
            _log.error(
                "[ADI] compute_adi 异常 case=%s: %s",
                case.get("case_id", "?"),
                e,
                exc_info=True,
            )
            adi, detail = 0, {"error": f"{type(e).__name__}: {e}"}

        case["adi"] = adi
        case["adi_detail"] = detail
        case["needs_review"] = False
        case["needs_review_reason"] = ""
        report["total"] += 1
        scores.append(adi)
        report["hist"][adi] = report["hist"].get(adi, 0) + 1
        for u in detail.get("unresolved", []) or []:
            unresolved_counter[str(u)] = unresolved_counter.get(str(u), 0) + 1

        if enabled and adi < threshold:
            case["needs_review"] = True
            case["needs_review_reason"] = f"ADI_INSUFFICIENT(adi={adi} < {threshold})"
            report["blocked"].append(str(case.get("case_id", "")))

    report["avg"] = round(sum(scores) / len(scores), 2) if scores else 0.0
    report["unresolved_top"] = sorted(unresolved_counter.items(), key=lambda x: -x[1])[
        :10
    ]
    LAST_ADI_GATE_REPORT = report
    return report
