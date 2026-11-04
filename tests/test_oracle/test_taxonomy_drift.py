# -*- coding: utf-8 -*-
"""常驻漂移门禁：三词表一致性（F5 根因修复的守护）。

背景（2026-09-27 实测）：
  执行端注册表（UiTrackVerifier/ApiTrackVerifier/DbTrackVerifier）里有 12 个族
  handler 齐备、实测可执行，但未登记进 CHECK_TAXONOMY →
  `resolve_check_type` 归一失败 → ADI 把它们按 0 分计入 unresolved
  （日志：`[ADI] 检查族 'viewport_rendering_check' 未在 ADI_SCORE_TABLE 中`），
  真实深度断言被静默误判为幻觉，并污染 adi_signal 的假 PASS / 回归判据。

本测试把这条链锁死为可执行断言：
  A. 执行注册表（每轨全部类型，含别名与 _check 展开）⊆ CHECK_TAXONOMY
  B. CHECK_TAXONOMY ⊆ ADI_SCORE_TABLE（check_taxonomy_coverage() 为空）
  C. 每个族的裸名与其 `xxx_check` 形态必须归一到同一族（词表形态对称）
"""
from __future__ import annotations

import pytest

from src.pipeline.steps.step3.oracle.adi import (
    ADI_SCORE_TABLE,
    check_taxonomy_coverage,
)
from src.pipeline.steps.step4.browser_tools._taxonomy import (
    CHECK_TAXONOMY,
    resolve_check_type,
)
from src.pipeline.steps.step4.verification._track_protocol import (
    registered_check_types,
)


def test_execution_registry_subset_of_taxonomy():
    """A. 执行端注册的每个 check 类型都必须能归一到 CHECK_TAXONOMY 权威族。"""
    drift: list[tuple[str, str]] = []
    for track, types in registered_check_types().items():
        for ctype in sorted(types):
            resolved = resolve_check_type(ctype)
            if resolved not in CHECK_TAXONOMY:
                drift.append((track, ctype))
    assert not drift, (
        "执行注册表存在未登记族（生成端总能发出、ADI 按 0 分静默误判）: "
        + ", ".join("%s:%s" % (t, c) for t, c in drift)
    )


def test_taxonomy_covered_by_adi_score_table():
    """B. CHECK_TAXONOMY 每一族都必须有 ADI 深度分（否则 unresolvable → 0 分）。"""
    missing = check_taxonomy_coverage()
    assert not missing, "CHECK_TAXONOMY 有族未在 ADI_SCORE_TABLE: %s" % missing


def test_family_name_form_symmetry():
    """C. 裸名与 `xxx_check` 形态必须归一到同一族（形态对称，防半边 0 分）。"""
    bad: list[str] = []
    for family in CHECK_TAXONOMY:
        assert resolve_check_type(family) == family, "族名自归一失败: %s" % family
        twin = family + "_check"
        if resolve_check_type(twin) != family:
            bad.append(twin)
    assert not bad, "以下 `_check` 形态未归一到权威族: %s" % bad


def test_adi_score_table_keys_are_taxonomy_families():
    """D. 评分表键必须都是 CHECK_TAXONOMY 族或 EVIDENCE_ALIAS 显式自造族。

    防「评分表自成一套词表」再次漂移（键写 xxx_check 却与裸名形态不对称）。
    """
    from src.pipeline.steps.step3.oracle.adi import EVIDENCE_ALIAS

    allowed_extra = {fam for fam, _score in EVIDENCE_ALIAS.values()}
    unknown = [k for k in ADI_SCORE_TABLE if k not in CHECK_TAXONOMY and k not in allowed_extra]
    assert not unknown, "ADI_SCORE_TABLE 存在非权威族键: %s" % unknown


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))