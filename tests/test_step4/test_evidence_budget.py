# -*- coding: utf-8 -*-
"""回归测试：逐用例证据落库的体积契约（`_evidence_budget`）。

背景（2026-10-28，清空态全量跑实证）：483 条用例执行完毕却只落库 371 行 ——
118 条用例的 executions 行整行丢失。根因是 C5 证据接线把用例结束时缓冲里的
console/network 条目原样拼进 `details`（TEXT，65535 字节）；单条用例装载期
即可产生上百条 CSP 报错 → 序列化超限 → MySQL 1406 → 落库异常被 best-effort
guard 吞掉，主循环却照常打印「结果」。

本测试锁定三条契约：
  ① 页面级事实（装载期报错）不逐用例复制，只留计数+签名摘要；
  ② 可归因条目按类型限额保留，超限只记丢弃条数（存储不被日志风暴击穿）；
  ③ 单条目文本截断，嵌套结构不膨胀。
"""
from src.pipeline.steps.step4.browser_core.single_mixins._evidence_budget import (
    CASE_CONSOLE_MAX,
    CASE_NETWORK_MAX,
    ENTRY_TEXT_MAX,
    pack_case_evidence,
)
from src.pipeline.steps.step4.playwright_engine.js_load_phase import PAGE_LOAD_KEY


def _page_level(text: str, n: int = 1) -> list:
    return [
        {"type": "console.error", "text": text, "url": "/", PAGE_LOAD_KEY: True}
        for _ in range(n)
    ]


def test_empty_input_returns_empty_dict():
    assert pack_case_evidence([], []) == {}
    assert pack_case_evidence(None, None) == {}


def test_page_level_facts_are_aggregated_not_copied():
    """页面级事实压成摘要：total 计数准确、签名去重、不进入 console_logs。"""
    console = _page_level("Executing inline event handler violates CSP", 163)
    packed = pack_case_evidence(console, [])

    assert "console_logs" not in packed, "页面级事实不得作为用例证据落库"
    digest = packed["page_level_console"]
    assert digest["total"] == 163
    assert len(digest["signatures"]) == 1
    assert digest["signatures"][0]["count"] == 163


def test_case_attributable_entries_kept_up_to_cap():
    """可归因条目保留，超出上限只丢尾部并如实记数（不静默截断）。"""
    console = [
        {"type": "console.error", "text": "boom-%d" % i} for i in range(CASE_CONSOLE_MAX + 7)
    ]
    network = [{"method": "GET", "url": "/p/%d" % i} for i in range(CASE_NETWORK_MAX + 5)]
    packed = pack_case_evidence(console, network)

    assert len(packed["console_logs"]) == CASE_CONSOLE_MAX
    assert packed["console_dropped"] == 7
    assert len(packed["network_logs"]) == CASE_NETWORK_MAX
    assert packed["network_dropped"] == 5


def test_entry_text_is_bounded_even_for_nested_payloads():
    """单条目（含嵌套 dict/list）序列化后不超过 ENTRY_TEXT_MAX 字符。"""
    console = [
        {
            "type": "console.error",
            "text": "x" * 5000,
            "detail": {"stack": ["y" * 900 for _ in range(20)]},
        }
    ]
    packed = pack_case_evidence(console, [])
    entry = packed["console_logs"][0]
    total = sum(len(str(v)) for v in entry.values())
    assert total <= ENTRY_TEXT_MAX
    assert len(entry["text"]) <= ENTRY_TEXT_MAX


def test_mixed_buffer_splits_attribution():
    """同一缓冲内两类事实并存时，各走各的通道，互不污染。"""
    console = _page_level("load-time CSP", 5) + [
        {"type": "console.error", "text": "click-time error"}
    ]
    packed = pack_case_evidence(console, [])
    assert packed["page_level_console"]["total"] == 5
    assert len(packed["console_logs"]) == 1
    assert packed["console_logs"][0]["text"] == "click-time error"


# ---------------------------------------------------------------------------
# 生产者契约：归属标记必须在「全量日志缓冲」与「error 缓冲」同时落标。
# 背景（2026-10-28 终局审计）：lifecycle._on_console 此前只给 error 缓冲打
# PAGE_LOAD_KEY，全量缓冲 `_console_logs`（证据落库的输入源）没有标记 →
# pack_case_evidence 无法切分页面级事实：490 行落库中 page_level_console 命中 0，
# 而 GAP-01（读 error 缓冲）却把同一批装载期报错判为页面级。判定与证据口径不一致。
# ---------------------------------------------------------------------------
from src.pipeline.steps.step4.playwright_engine.lifecycle import LifecycleMixin


class _FakeMsg:
    def __init__(self, level="error", text="boom"):
        self.type = level
        self.text = text
        self.location = {"url": "/"}


class _FakeTracker:
    def __init__(self, in_phase: bool):
        self._in_phase = in_phase

    def is_load_phase(self) -> bool:
        return self._in_phase


def _fake_engine(in_phase: bool):
    obj = LifecycleMixin.__new__(LifecycleMixin)
    obj._console_logs = []
    obj._console_errors = []
    obj._load_phase = _FakeTracker(in_phase)
    return obj


def test_console_marker_present_in_both_buffers():
    """装载期标记对两个缓冲一致：否则证据与判定口径分叉。"""
    for in_phase in (True, False):
        eng = _fake_engine(in_phase)
        eng._on_console(_FakeMsg())
        assert eng._console_logs[0][PAGE_LOAD_KEY] is in_phase
        assert eng._console_errors[0][PAGE_LOAD_KEY] is in_phase


def test_full_log_buffer_feeds_page_level_classification():
    """全量缓冲作为证据源时，装载期报错必须被识别为页面级而非用例证据。"""
    eng = _fake_engine(True)
    eng._on_console(_FakeMsg())
    eng._on_console(_FakeMsg(text="second"))

    packed = pack_case_evidence(eng._console_logs, [])
    assert "console_logs" not in packed
    assert packed["page_level_console"]["total"] == 2


def test_non_error_console_entry_is_case_level():
    """非 error 级日志不属于 JS 缺陷缓冲，但仍是本用例的动作证据。"""
    eng = _fake_engine(False)
    eng._on_console(_FakeMsg(level="log", text="hello"))
    packed = pack_case_evidence(eng._console_logs, [])
    assert len(packed["console_logs"]) == 1
    assert eng._console_errors == []


def test_page_level_signature_uses_level_key_of_full_log_buffer():
    """全量 console 缓冲的条目键是 `level`（非 type/event），签名摘要须带出该类型。"""
    eng = _fake_engine(True)
    eng._on_console(_FakeMsg(level="error", text="csp"))
    packed = pack_case_evidence(eng._console_logs, [])
    sig = packed["page_level_console"]["signatures"][0]
    assert sig["type"] == "error"
    assert sig["text"] == "csp"
