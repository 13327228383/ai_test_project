"""契约门禁：run 身份（run_id + step_label）必须在 _run_step 函数入口唯一注入，
且早于任何 _log/print。

背景（真实缺陷）：`_run_step` 原先先 `_thread_run_id.value = opts.run_id` 并立刻
`_log.info("[DEBUG-exec] _run_step 开始 ...")`，而 `begin_run_log(...)` 要到函数中段
才调用 → 函数首行日志落库时 `_current_log_file` 尚为空 → `_parse_step_label("system")`
→ `run_logs.step_num=0`，污染「按 step_num 切片」的完成判据。

不变量：同一语义（本 run 属于哪个 step）只在一处注入，且在任何日志之前。
"""

import re
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "src" / "backend" / "executor" / "_run_step.py"


def _source() -> str:
    return _SRC.read_text(encoding="utf-8")


def _func_body() -> str:
    """截取 _run_step 函数体（从 def 到下一个顶层 def）。"""
    src = _source()
    m = re.search(r"^def _run_step\(", src, re.M)
    assert m, "未找到 _run_step 函数定义"
    rest = src[m.end():]
    nxt = re.search(r"^def ", rest, re.M)
    return rest[: nxt.start()] if nxt else rest


def _offset(text: str, pattern: str) -> int:
    m = re.search(pattern, text)
    assert m, f"未找到锚点: {pattern}"
    return m.start()


def test_run_identity_injected_before_first_log():
    """begin_run_log 必须出现在函数首个 _log 调用之前。"""
    body = _func_body()
    first_log = _offset(body, r"_log\.(debug|info)\(")
    begin_positions = [m.start() for m in re.finditer(r"begin_run_log\(", body)]
    assert begin_positions, "未找到 begin_run_log 调用"
    assert min(begin_positions) < first_log, (
        "begin_run_log 必须早于首个 _log 调用，否则函数开头的日志 step_num=0"
    )


def test_current_log_file_set_before_first_log():
    """_current_log_file.value 赋值必须早于首个 _log 调用。"""
    body = _func_body()
    first_log = _offset(body, r"_log\.(debug|info)\(")
    set_positions = [m.start() for m in re.finditer(r"_current_log_file\.value\s*=", body)]
    # 至少有一处真正的赋值（非 None 清理）在首个 _log 之前
    assigns_before = [p for p in set_positions if p < first_log]
    assert assigns_before, "_current_log_file.value 必须在首个 _log 之前赋值"


def test_run_identity_injected_exactly_once():
    """begin_run_log 在同一函数内只应被调用一次（消除双处注入）。"""
    body = _func_body()
    calls = re.findall(r"^\s*_log_file = begin_run_log\(", body, re.M)
    assert len(calls) == 1, f"begin_run_log 应只调用一次，实际 {len(calls)} 次"
