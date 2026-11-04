# -*- coding: utf-8 -*-
"""Pipeline Saga 补偿机制。"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from collections.abc import Callable
from typing import Any

_log = logging.getLogger(__name__)


@dataclass
class SagaStep:
    step_num: int
    step_name: str
    compensate: Callable[[], Any] | None = None


class PipelineSaga:
    """记录已完成步骤，并在后续失败时按逆序执行补偿。"""

    def __init__(self, run_id: str, project: str):
        self.run_id = run_id
        self.project = project
        self._completed: list[SagaStep] = []

    def mark_completed(
        self, step_num: int, step_name: str, compensate: Callable[[], Any] | None = None
    ) -> None:
        self._completed.append(SagaStep(step_num, step_name, compensate))

    def compensate(self, failed_step: int, reason: str = "") -> dict[str, Any]:
        results = []
        for item in reversed(self._completed):
            if not item.compensate:
                results.append(
                    {"step": item.step_num, "status": "skipped", "reason": "no_hook"}
                )
                continue
            try:
                item.compensate()
                results.append({"step": item.step_num, "status": "success"})
            except Exception as exc:
                _log.warning("Saga 补偿失败 step=%s: %s", item.step_num, exc)
                results.append(
                    {"step": item.step_num, "status": "failed", "error": str(exc)}
                )
        return {"failed_step": failed_step, "reason": reason, "compensated": results}
