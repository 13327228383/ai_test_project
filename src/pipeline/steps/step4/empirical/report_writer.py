# -*- coding: utf-8 -*-
from __future__ import annotations

"""实证报告落盘（EmpiricalReportWriter）。

★ 职责：将 TargetEmpiricalVerifier 产出的报告 dict 落盘为 JSON。
  report schema（兼容扩展，见 docs/system_design_expend.md §3.4）：
    {target, target_key, mode, self_started_sut, backend, cases:[...], summary:{all_pass,...}}
  - 顶层 mode：browser | api；api 模式 backend={"kind":"api_requests"}。
  - cases 含 blocked_by 字段，取值扩展三值：
      browser_native_validation（hospital 原生校验）/
      client_validation（spa validate()）/
      api_param_validation（api 40001）。
  - 纯 IO 封装，不做任何断言/降级。
"""

import json
import logging
from pathlib import Path

_log = logging.getLogger(__name__)


def write(report: dict, path: str) -> None:
    """将实证报告 dict 落盘为 JSON（UTF-8，缩进 2）。

    Args:
        report: 报告字典（target / self_started_sut / backend / cases / summary）。
        path: 输出文件路径（父目录不存在则创建）。
    """
    out = Path(path)
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        _log.info("[report_writer] 已写出实证报告: %s", out)
    except Exception as _e:
        _log.error("[report_writer] 写报告失败: %s", _e, exc_info=True)
        raise
