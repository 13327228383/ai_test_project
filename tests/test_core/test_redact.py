# -*- coding: utf-8 -*-
"""回归测试：通用敏感信息脱敏（src/core/utils/redact.py）。"""
import logging

from src.core.utils.redact import (
    make_sensitive_filter,
    redact,
    redact_url_query,
)


def test_bearer_tokens_masked():
    assert redact("using sk-abc1234567890xyz here") == "using *** here"
    v = redact("ark-924f799a-b2d2-4f25-b5e0-c55e4dd5886d-x")
    assert "***" in v and "c55e4dd5886d" not in v
    assert "***" in redact("eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0In0.abcdefg.test")


def test_keyval_secret_masked():
    out = redact("conn password=SuperSecret123;host=127.0.0.1")
    assert "SuperSecret123" not in out
    assert redact("api_key: sk-world-nono").endswith("***") or "***" in redact("api_key: sk-world-nono")


def test_long_random_masked():
    assert redact("job x6f9a1b2c3d4e5f67890a1b2c3d4e5f67890a1b2c3d4e5f6 done").count("***") >= 1


def test_not_masked_plain_text():
    assert redact("hello world plain 1234") == "hello world plain 1234"


def test_redact_is_idempotent():
    s = "token=sk-abcdefghijklmnop"
    assert redact(redact(s)) == redact(s)


def test_redact_url_query():
    u = "http://x/api?token=sk-abcdefghijkl&key=1&other=ok"
    r = redact_url_query(u)
    assert "sk-abcdefghijkl" not in r
    assert "other=ok" in r


def test_logging_filter_masks_message_and_args():
    record = logging.LogRecord(
        "demo", logging.INFO, "f.py", 1,
        "connect with %s ok", ("sk-zzzzzz111122223333",),
        exc_info=None,
    )
    f = make_sensitive_filter()
    assert f.filter(record)
    assert "sk-zzzzzz111122223333" not in record.msg
    assert record.args[0] == "***"