# -*- coding: utf-8 -*-
"""镜像 src/ai/ai_client_mixins/_base.py 的 _chat_with_hard_timeout 硬超时守护。

覆盖：快速返回 / 超时返回失败 / 线程内异常兜底 / 无超时配置（timeout<=0）直接取。
"""
import time

import pytest

from src.ai.ai_client_mixins._base import _chat_with_hard_timeout


class _FakeProvider:
    """自定义行为 provider：可配置耗时与是否抛异常。"""

    def __init__(self, delay=0.0, error=None, result=None):
        self.delay = delay
        self.error = error
        self.result = result

    def chat(self, **kwargs):
        if self.delay:
            time.sleep(self.delay)
        if self.error:
            raise self.error
        return self.result if self.result is not None else {"success": True, "data": "ok"}


def test_quick_success_returns_result():
    p = _FakeProvider(result={"success": True, "data": "hello"})
    out = _chat_with_hard_timeout(p, 1.0, {})
    assert out == {"success": True, "data": "hello"}


def test_timeout_returns_failure_not_raise():
    p = _FakeProvider(delay=5.0)  # 远超 timeout，应快速返回失败而非挂死
    t0 = time.time()
    out = _chat_with_hard_timeout(p, 0.05, {})
    elapsed = time.time() - t0
    assert out["success"] is False
    assert "超时" in out["error"]
    assert elapsed < 3.0, "硬超时守护应尽快返回，实测 %.2fs" % elapsed


def test_thread_exception_returns_failure():
    p = _FakeProvider(error=RuntimeError("boom"))
    out = _chat_with_hard_timeout(p, 1.0, {})
    assert out["success"] is False
    assert "boom" in out["error"]


def test_no_timeout_config_blocks_until_done():
    p = _FakeProvider(delay=0.05, result={"success": True, "data": "slow-ok"})
    out = _chat_with_hard_timeout(p, 0, {})
    assert out["success"] is True
    assert out["data"] == "slow-ok"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])