"""契约门禁：安全用例（SQLi/XSS/CSRF）的三条不变量。

背景（真实缺陷，2026-10-15 实测）：
`_build_security_cases` 生成的 I04/I05/I06 安全用例，`actions` 只有
`navigate + screenshot` —— **payload 从未打进任何输入框**。后果链：

  ① 动作无交互 → `_check_guard._is_pure_verification_case` 判真
  ② → `pop("_explore_task")`，payload 探索引导随之丢失（实测存活率 24/606）
  ③ → 用例退化为"只导航+截图"，注入从未发生
  ④ → 同时 `_build_one` 无差别灌入需求级 `expected_feedback`（如 REQ-011 的
     "系统提示用户名或密码错误"）→ 下游极性闸门据"错误"字样把 `no_error`
     反转为"应提示该错误文案" → 靶子正确防御（页面正常）反被判 FAIL
  ⑤ → AI 归因 PRODUCT_BUG，批量产出假 Bug 报告（实测 19 条全中）

不变量：
  1. 安全用例的 actions **必须含 fill 动作且其 value 是注入 payload**；
  2. `expected_feedback` 必须取**用例自身声明**，不得被需求级无差别覆盖；
  3. 三条安全用例共用**同一个**动作构造器（避免各拼一套）。
"""

import re
from pathlib import Path

_BASE = Path(__file__).resolve().parents[2] / "src" / "pipeline" / "step3" / "case_rules" / "base.py"


def _src() -> str:
    return _BASE.read_text(encoding="utf-8")


def _func_body(name: str) -> str:
    src = _src()
    m = re.search(rf"^    def {name}\(", src, re.M)
    assert m, f"未找到方法定义: {name}"
    rest = src[m.end():]
    nxt = re.search(r"^    def ", rest, re.M)
    return rest[: nxt.start()] if nxt else rest


def test_security_cases_carry_injection_payload_in_actions():
    """三条安全用例都必须走 _build_security_probe_actions（而非裸 nav+screenshot）。"""
    body = _func_body("_build_security_cases")
    calls = re.findall(r"self\._build_security_probe_actions\(", body)
    assert len(calls) == 3, (
        f"SQLi/XSS/CSRF 三条安全用例都应携带注入动作，实际调用 {len(calls)} 次"
    )
    # 不得残留裸 [self._nav(...), self._screenshot()] 作为安全用例动作
    naked = re.findall(r"\[\s*self\._nav\([^)]*\),\s*self\._screenshot\(\)\s*\]", body)
    assert not naked, f"安全用例仍有无注入动作的裸 nav+screenshot: {naked}"


def test_probe_action_builder_injects_payload_into_fill():
    """构造器产出的动作里，必须有一个 fill 的 value 是注入 payload。"""
    src = _src()
    m = re.search(r"def _build_security_probe_actions\(", src)
    assert m, "未找到 _build_security_probe_actions"
    # 抓取函数体（下一个同级 def 前）
    rest = src[m.end():]
    nxt = re.search(r"^    def ", rest, re.M)
    body = rest[: nxt.start()] if nxt else rest
    assert '"type": "fill"' in body or "'type': 'fill'" in body or '_a.get("type") == "fill"' in body, (
        "构造器必须产出 fill 动作"
    )
    assert '"value"' in body, "fill 动作必须带 value（payload）"


def test_expected_feedback_prefers_case_declared():
    """expected_feedback 必须优先取用例自身声明（expected），需求级仅兜底。"""
    body = _func_body("_build_one")
    m = re.search(r'"expected_feedback":\s*\(([\s\S]{0,400}?)\),\n', body)
    assert m, "未找到 expected_feedback 赋值"
    expr = m.group(1)
    assert "expected" in expr and "req" in expr, (
        "expected_feedback 应同时引用用例声明(expected)与需求级(req)并给出优先级"
    )
    # 用例声明必须在需求级之前（短路求值顺序 = 优先级）
    assert expr.index("expected") < expr.index("(req"), (
        "expected_feedback 必须先取用例声明，需求级仅作兜底"
    )
