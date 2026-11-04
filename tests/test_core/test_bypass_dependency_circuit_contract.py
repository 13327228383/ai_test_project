# -*- coding: utf-8 -*-
"""契约：旁路依赖必须有熔断，不得每条用例重复付超时。

背景（2026-10-16 实测）：
  `plugin_manager._write_plugin_intervention` 是**旁路审计写入**（不在用例判定的
  必经路径上）。靶子系统 DB 不可达时（`TARGET_DB_*` 未配置 / 1045 拒绝访问），
  原实现每条用例都重新建连并等超时 —— 实测 **113 次失败累计白烧 202s，
  占前 4 分钟墙钟的 84%**，单例耗时被它从 3s 级抬到 12~16s。

  「某依赖是否可用」是一条一次性事实，必须只判定一次、下游只读；
  不可达时旁路直接短路，**绝不阻塞主链路**。
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PM = ROOT / "src" / "pipeline" / "steps" / "step4" / "plugins" / "plugin_manager.py"


def _src(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def test_circuit_breaker_semantics():
    """熔断器：连续失败达阈值 → 拒绝；冷却后可再探；成功即恢复。"""
    import time

    from src.core.circuit_breaker import CircuitBreaker

    b = CircuitBreaker("t", trip_after_fails=2, cooldown_seconds=0.5)
    assert b.allow()
    b.note_failure("e1")
    assert b.allow(), "未达阈值不得熔断"
    b.note_failure("e2")
    assert not b.allow(), "达阈值必须熔断"
    assert b.remaining() > 0
    time.sleep(0.6)
    assert b.allow(), "冷却结束应放行探测"
    b.note_success()
    assert b.allow() and b.remaining() == 0.0


def test_db_availability_is_memoized():
    """靶子 DB 可用性只探测一次并缓存（避免每条用例重复建连）。"""
    src = _src(PM)
    assert "def _target_db_available" in src
    assert "_db_avail_state" in src and "_DB_AVAIL_COOLDOWN_SEC" in src
    # 写入前必须先过可用性判定
    idx_fn = src.index("def _write_plugin_intervention")
    seg = src[idx_fn : idx_fn + 1400]
    assert "_target_db_available()" in seg, "旁路写入前必须先做一次性可用性判定"


def test_intervention_write_has_breaker():
    src = _src(PM)
    assert "_intervention_breaker" in src
    assert "note_failure" in src and "note_success" in src
