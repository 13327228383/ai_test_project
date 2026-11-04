# -*- coding: utf-8 -*-
"""
review_gate.py — 需求审核闸门
=============================
从 scheduler.py 拆分出的独立模块，负责 Step2 后的需求拆解结果人工审核。

★ v2 变更 (2026-07-01):
  - 审核状态已迁移到 requirements.review_status DB 实体字段
  - 不再写 _review_state 内存状态
  - 只返回 review_needed: True 让 scheduler 暂停
  - 前端轮询 /api/status → review_state 直接查 DB
"""

import logging
from src.core.guarded import guard
from typing import Any

_log = logging.getLogger(__name__)


def apply_review_gate(count: int, mode: str, **extra) -> dict[str, Any]:
    """
    ★ v2：需求审核闸门。DB-only，不再维护内存状态。

    审核状态存储在 requirements.review_status DB 字段中，
    前端通过 /api/status → review_state（查 DB）获取。
    ★ 全部自动通过时跳过暂停（无需人工审核）。
    """
    try:
        from src.core.config_loader import config
    except Exception as _e:
        _log.error(
            "[审核闸门] 加载 config 失败，无法确定闸门配置，阻断执行: %s", str(_e)[:80]
        )
        return {
            "success": False,
            "error": "审核闸门配置加载失败: %s" % str(_e)[:80],
            "count": count,
            "mode": mode,
            **extra,
        }

    if not config.requirement_review_gate:
        _log.warning(
            "[审核闸门] requirement_review_gate=False，闸门被跳过（风险：无人工审核）"
        )
        return {
            "success": True,
            "count": count,
            "mode": mode,
            "gate_skipped": True,
            "warning": "审核闸门已关闭",
            **extra,
        }

    # ★ v2: 检查是否有待审核需求。全部自动通过 → 跳过暂停
    pending = 0
    with guard("apply_review_gate: from src.core.excel_handler import e", log=_log):
        from src.core.excel_handler import excel_handler

        reqs = excel_handler.read_requirements()
        pending = sum(
            1
            for r in reqs
            if r.get("review_status", "pending") not in ("approved", "modified")
        )
        if pending == 0 and len(reqs) > 0:
            _log.info(
                "[审核闸门] 全部 %d 条需求已自动通过审核（green 车道，P0-3a 锚点合取分≥85），跳过人工审核",
                len(reqs),
            )
            return {"success": True, "count": count, "mode": mode, **extra}

    _log.info(
        "[审核闸门] 需求拆解完成，%d 条待审核（将在启动 Step3 前弹窗确认）", pending
    )
    return {
        "success": True,
        "count": count,
        "mode": mode,
        "review_needed": True,
        "message": "需求拆解完成，审核通过后可启动 Step3",
        **extra,
    }
