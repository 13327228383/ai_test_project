# -*- coding: utf-8 -*-
"""回归测试：差距1 —— SutHarness._resolve_python_cmd 统一 python 解析。

不依赖浏览器/DB：仅验证命令解析规则——以 ``python`` 开头的 seed/启动命令在
external 环境无 ``python`` 时也必须被解析为当前解释器绝对路径（实测 9009 root）。
"""
import sys

from src.pipeline.steps.step4.empirical.sut_harness import _resolve_python_cmd


def test_python_prefix_replaced():
    r = _resolve_python_cmd("python seed.py")
    assert r == sys.executable + " seed.py"
    assert sys.executable not in ("python",)


def test_python3_and_module_forms():
    assert _resolve_python_cmd("python3 abc.py") == sys.executable + " abc.py"
    assert _resolve_python_cmd("python -m http.server 8000") == sys.executable + " -m http.server 8000"
    assert _resolve_python_cmd("python3 -m http.server 8000") == sys.executable + " -m http.server 8000"


def test_bare_python_replaced():
    assert _resolve_python_cmd("python") == sys.executable


def test_non_python_untouched():
    for c in ("app.py", "", None, "  npx serve .", "/usr/bin/node s.js"):
        assert _resolve_python_cmd(c) == c


def test_dedup_with_static_serve():
    # 确认既有 static_serve 内联逻辑已被统一收口到该 helper（等价结果）。
    cmd = "python -m http.server 8000"
    legacy = cmd.replace("python", sys.executable, 1) if cmd.startswith("python") else cmd
    assert _resolve_python_cmd(cmd) == legacy