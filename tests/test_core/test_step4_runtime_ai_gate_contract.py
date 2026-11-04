# -*- coding: utf-8 -*-
"""契约：运行期 AI 闸门只能有一处判据，且两个收口点都过它。

背景（2026-10-16 实测）：
  · 架构要求「AI 前移到生成侧、运行期零 AI」，但 Step4 运行期实测 0.70 次 AI/例
    （单次 5~30s，是最大耗时项）。
  · AI 调用有两个收口点：走 Router 的 `router.route` 与直连 Provider 的
    `_call_api`；只堵一个仍会被另一个绕过。
  · 正解：判据唯一实现在 `src/ai/runtime_ai_gate.py`，两处收口点只读；
    禁用时返回 `success=False`（调用方既有降级路径接管，不伪造结论）。
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GATE = ROOT / "src" / "ai" / "runtime_ai_gate.py"
ROUTER = ROOT / "src" / "ai" / "router_module" / "_router.py"
DIRECT = ROOT / "src" / "ai" / "ai_client_mixins" / "_base.py"
BASE = ROOT / "src" / "pipeline" / "steps" / "step4" / "browser_core" / "base.py"


def _src(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def test_denied_only_in_step4_runtime_and_when_disabled():
    """闸门语义：运行期内 且 配置关闭 → 拒绝；运行期外（生成侧）不受影响。"""
    from src.ai.runtime_ai_gate import (
        enter_step4_runtime,
        exit_step4_runtime,
        in_step4_runtime,
        runtime_ai_denied,
    )

    try:
        assert not in_step4_runtime()
        assert not runtime_ai_denied(), "生成侧/运行期外不得拦截 AI"

        enter_step4_runtime()
        assert in_step4_runtime()
        assert runtime_ai_denied(), "运行期且默认配置下必须拦截 AI"
    finally:
        exit_step4_runtime()
    assert not runtime_ai_denied()


def test_default_config_disables_runtime_ai():
    from src.core.pipeline_config import pipeline_config

    assert getattr(pipeline_config, "step4_runtime_ai", False) is False


def test_both_call_sites_reference_gate():
    """router.route 与 _call_api 两处都调同一个闸门函数。"""
    assert "runtime_ai_denied" in _src(ROUTER)
    assert "runtime_ai_denied" in _src(DIRECT)


def test_gate_does_not_fabricate_result():
    """禁用时返回 success=False，不得伪造 PASS/FAIL 结论。"""
    router_src = _src(ROUTER)
    assert '"success": False' in router_src
    assert "runtime_ai_disabled" in router_src


def test_run_test_cases_wrapped_by_runtime_guard():
    """Step4 执行入口被运行期守卫包装（含异常分支也能解除标记）。"""
    base_src = _src(BASE)
    assert "@_with_step4_runtime" in base_src
    assert "def _with_step4_runtime" in base_src
    # finally 中解除，保证异常路径不泄漏标记
    idx = base_src.index("def _with_step4_runtime")
    seg = base_src[idx : idx + 1200]
    assert "finally:" in seg and "exit_step4_runtime()" in seg
