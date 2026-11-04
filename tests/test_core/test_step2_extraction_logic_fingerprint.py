# -*- coding: utf-8 -*-
"""契约测试：Step2 的 skip 守卫必须把「提取逻辑指纹」计入缓存键。

根因（2026-10-15 实测 fullcov_sut）：
  Step2 的 skip 守卫（`step2.py`）历史只比较**文档语义哈希**（需求正文是否变），
  不比较**产出需求的提取逻辑**。于是修复提取语义（如 infer_expected_feedback
  的极性判定）后，正文未变 → 守卫判「无变更」→ 直接复用 requirements 表里的**旧值**，
  修复永远到不了下游。

  实测证据：2026-10-14 修正极性判定后未重拆，requirements（写入于 2026-09-28）
  仍留 9 条 `expected_feedback='操作成功,成功'`；其中 REQ-049/050 的 operation
  表达「否则客户端拦截」/「校验失败时返回错误提示」却配「操作成功」→ 极性矛盾 → 假 FAIL。

本测试锁死修复，防回退：
  1. `compute_extraction_logic_fingerprint` 存在且确定性（同码同值）。
  2. 指纹覆盖**语义产出函数所在模块**（词表模块必须在内）。
  3. `step2.py` 的 skip 守卫条件必须显式包含逻辑指纹比对（源码级断言）。
  4. 两处 `save_breakdown_hash` 的 meta 都必须持久化指纹（否则下一轮永远判「变了」）。
"""

from __future__ import annotations

import hashlib
import importlib.util
import inspect
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


def _read(rel: str) -> str:
    return (REPO / rel).read_text(encoding="utf-8")


def test_fingerprint_exists_and_deterministic():
    from src.pipeline.step2_hashes import compute_extraction_logic_fingerprint

    a = compute_extraction_logic_fingerprint()
    b = compute_extraction_logic_fingerprint()
    assert isinstance(a, str) and len(a) == 64, "指纹应为 sha256 hex"
    assert a == b, "同码同值（确定性）"
    int(a, 16)  # 必须可解析为十六进制


def test_fingerprint_covers_semantic_modules():
    """词表/提取模块必须在被哈希的清单里 —— 否则改极性逻辑不会使缓存失效。"""
    src = _read("src/pipeline/step2_hashes.py")
    for must in (
        "_rule_extractor_keywords.py",  # infer_expected_feedback / 极性判据
        "numbered_list.py",  # 规则兜底三元组
        "_fd_extraction.py",  # AI-first + 规则兜底入口
    ):
        assert must in src, "提取逻辑指纹必须覆盖 %s" % must


def test_fingerprint_changes_when_source_changes(tmp_path, monkeypatch):
    """源码变化 → 指纹必须变化（用真实模块验证，非 mock）。"""
    from src.pipeline import step2_hashes as M

    base = M.compute_extraction_logic_fingerprint()

    # 篡改清单里任一文件的读取结果，指纹必须不同
    real_open = open
    victim = M._AGGREGATOR_SOURCES[0]

    def fake_open(path, *a, **kw):
        if str(path).replace("\\", "/").endswith(victim):
            import io

            return io.BytesIO(b"# mutated-for-test")
        return real_open(path, *a, **kw)

    monkeypatch.setattr("builtins.open", fake_open)
    changed = M.compute_extraction_logic_fingerprint()
    assert changed != base, "源码变了但指纹没变 → 缓存不会失效（回退）"


def test_step2_skip_guard_includes_logic_fingerprint():
    """源码级断言：skip 守卫必须显式比对逻辑指纹。"""
    src = _read("src/pipeline/step2.py")
    assert "compute_extraction_logic_fingerprint" in src, "必须 import 指纹函数"
    assert "_logic_changed" in src, "skip 守卫必须引入逻辑变更标志"
    # 守卫条件里必须出现该标志（而非仅赋值）
    guard = re.search(
        r"if\s*\((.*?)\):\s*\n\s+_log\.info\(\s*\n\s*\"已有\s*%d\s*条需求且语义哈希未变更",
        src,
        re.S,
    )
    assert guard, "未找到 skip 守卫条件块"
    assert "_logic_changed" in guard.group(1), "守卫条件必须包含 _logic_changed"


def test_both_save_sites_persist_fingerprint():
    """两处 save_breakdown_hash 的 meta 都要写入指纹，否则每轮都判『变了』。"""
    src = _read("src/pipeline/step2.py")
    n = src.count("save_breakdown_hash(")
    assert n >= 2, "预期至少两处 save_breakdown_hash（找到 %d）" % n
    m = src.count("extraction_logic_fp")
    assert m >= 2, (
        "两处 meta 都要持久化 extraction_logic_fp（找到 %d 处）" % m
    )
