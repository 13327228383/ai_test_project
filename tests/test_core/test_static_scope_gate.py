# -*- coding: utf-8 -*-
"""常驻静态作用域门禁：禁止「局部变量先读后写」（ruff F823 / F821）。

背景（2026-09-27 实测，该类缺陷在本仓库已复发 ≥3 次）：
  - `api_only_executor.py`：`_checks_total/_checks_passed` 只在 `if api_checks:` 分支内
    初始化，却在函数级被 db 轨计数与置信度计算消费 → 任一「仅 DB 断言」的 api_only 用例
    即抛 UnboundLocalError，整个 Step4 秒级中止、executions 零增量（假"跑过了"）。
  - `parallel.py`：模块级已 import config，函数体内又 `from src.core.config_loader import config`
    → `config` 被判为局部变量，登录失败分支读 `config.project_name` 先于赋值 → 并行路径崩溃。
  - 更早：`single_mixins/_base.py` / `serial.py` 同类问题（函数级 import config）。

这类缺陷的共同点：**运行时才炸、且炸在最关键的收尾/落库路径上**，单测不易覆盖。
故用静态检查常驻兜底：F823 = 局部变量赋值前引用；F821 = 未定义名。
"""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _ruff_available() -> bool:
    try:
        r = subprocess.run(
            [sys.executable, "-m", "ruff", "--version"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        return r.returncode == 0
    except Exception:  # noqa: BLE001
        return False


@pytest.mark.skipif(not _ruff_available(), reason="ruff 未安装（静态门禁跳过）")
def test_no_reference_before_assignment_in_src():
    """src/ 全量扫描：F823（局部变量赋值前引用）必须为 0。

    只选 F823：它是「运行时必然抛 UnboundLocalError」的精确信号，且本仓库当前为 0，
    可直接作为回归红线。F821（未定义名）在本仓有 37 处历史命中，多为 TYPE_CHECKING
    注解块与 `except ... as e` 作用域边界（静态可见、运行时无害），纳入会让门禁充满
    噪声而失去信号价值；如需收紧应先单独清理那 37 处，不在本门禁作用域内。
    """
    r = subprocess.run(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "--select",
            "F821,F823",
            "--output-format",
            "concise",
            os.path.join(ROOT, "src"),
        ],
        capture_output=True,
        text=True,
        timeout=600,
        cwd=ROOT,
    )
    findings = (r.stdout or "").strip()
    assert r.returncode == 0, (
        "src/ 存在静态作用域缺陷（运行时会在关键路径抛 UnboundLocalError）:\n" + findings
    )