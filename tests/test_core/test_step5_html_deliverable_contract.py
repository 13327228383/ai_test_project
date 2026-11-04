# -*- coding: utf-8 -*-
"""契约：Step5 的两个生产入口都必须产出 HTML 交付物。

缺陷形状
--------
`generate_report(..., generate_html=False)` 是默认值，而流水线的两个生产入口
（`PipelineScheduler._step5_report` / `StreamingPipelineScheduler._make_step5_func`）
都不传该参数 → `html_report` 在真实流水线里**恒不存在**：渲染能力齐全、调用方缺席。
平台侧 file_tree / mysql_doc 早已按 `html_report` 类型取用，属"产物类型被承认但从不生产"。

契约（本测试锁定）
------------------
两个入口调用 `generate_report` 时必须显式 `generate_html=True`；新增入口同样受此约束。
"""

from __future__ import annotations

import pytest

from src.pipeline.steps.step5 import report_generator


class _FakeScheduler:
    _project_name = "proj_x"


def _capture(monkeypatch: pytest.MonkeyPatch) -> dict:
    seen: dict = {}

    def _fake_generate_report(project, run_id=None, generate_html=False):
        seen["project"] = project
        seen["run_id"] = run_id
        seen["generate_html"] = generate_html
        return {"ok": True}

    monkeypatch.setattr(report_generator, "generate_report", _fake_generate_report)
    return seen


def test_pipeline_scheduler_step5_emits_html(monkeypatch: pytest.MonkeyPatch):
    from src.pipeline.scheduler import PipelineScheduler

    seen = _capture(monkeypatch)
    PipelineScheduler._step5_report(_FakeScheduler(), run_id="run_1")
    assert seen["generate_html"] is True, seen
    assert seen["run_id"] == "run_1"


def test_streaming_scheduler_step5_emits_html(monkeypatch: pytest.MonkeyPatch):
    from src.pipeline.streaming_scheduler import StreamingPipelineScheduler

    seen = _capture(monkeypatch)
    fn = StreamingPipelineScheduler._make_step5_func(_FakeScheduler(), "run_2")
    fn()
    assert seen["generate_html"] is True, seen
    assert seen["run_id"] == "run_2"
