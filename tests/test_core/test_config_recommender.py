# -*- coding: utf-8 -*-
"""流水线推荐值统一算法（src/core/_config_recommender.py）单元测试。

覆盖：
  - clamp_meta 收敛（int/float/bool/enum/str + 越界 + None + 异常降级默认）
  - recommend_all 对「全部 9 组」生成推荐值，且每个值都落在 [min,max] 内、类型正确
  - step2/step3 复用 _auto_tune；step4/rag/step_timeout 启发式随项目信号变化
  - 空 schema / 空 items 的健壮性

本测试全部显式传 signals，不依赖数据库；是纯算法单元测试（pytest -m unit）。
"""
from __future__ import annotations

import pytest

from src.core._config_recommender import (
    clamp_meta,
    recommend_all,
)

ALL_GROUPS = [
    "step2",
    "step3",
    "step4",
    "confidence",
    "ai",
    "step_timeout",
    "evidence",
    "verification",
    "rag",
]


def _mini_schema() -> dict:
    """覆盖全部 9 组 + int/float/bool/enum/str 五种类型的精简 schema。"""
    return {
        "step2": {
            "items": {
                "step2_shard_max_chars": {"type": "int", "default": 15000, "min": 3000, "max": 50000},
                "step2_shard_overlap_chars": {"type": "int", "default": 200, "min": 50, "max": 2000},
            }
        },
        "step3": {
            "items": {
                "step3_top_n": {"type": "int", "default": 5, "min": 1, "max": 10},
            }
        },
        "step4": {
            "items": {
                "step4_p1_sample_rate": {"type": "float", "default": 1.0, "min": 0.0, "max": 1.0},
                "step4_include_p2": {"type": "bool", "default": False},
                "step4_plugins_a_enabled": {"type": "bool", "default": True},
                "step4_plugins_b_enabled": {"type": "bool", "default": True},
            }
        },
        "confidence": {
            "items": {
                "confidence_low_threshold": {"type": "float", "default": 0.50, "min": 0.0, "max": 1.0},
                "confidence_medium_threshold": {"type": "float", "default": 0.70, "min": 0.0, "max": 1.0},
                "confidence_high_threshold": {"type": "float", "default": 0.90, "min": 0.0, "max": 1.0},
            }
        },
        "ai": {
            "items": {
                "ai_local_timeout": {"type": "float", "default": 15, "min": 1.0, "max": 120.0},
            }
        },
        "step_timeout": {
            "items": {
                "STEP1_TIMEOUT_SECONDS": {"type": "int", "default": 600, "min": 60, "max": 3600},
                "STEP3_TIMEOUT_SECONDS": {"type": "int", "default": 1800, "min": 60, "max": 7200},
            }
        },
        "evidence": {
            "items": {
                "evt_mode": {
                    "type": "enum",
                    "default": "strict",
                    "options": [{"value": "auto"}, {"value": "strict"}],
                },
            }
        },
        "verification": {
            "items": {
                "ver_note": {"type": "str", "default": "lenient"},
            }
        },
        "rag": {
            "items": {
                "rag_keyword_max_count": {"type": "int", "default": 50, "min": 1, "max": 200},
                "rag_context_snippet_len": {"type": "int", "default": 200, "min": 50, "max": 2000},
            }
        },
    }


def _signals(**kw) -> dict:
    base = {
        "num_reqs": 10,
        "num_docs": 2,
        "doc_total_chars": 500_000,
        "has_trace": True,
        "has_figma": False,
        "has_db": True,
        "has_rag": True,
        "cpu_count": 4,
        "model_window": 128_000,
    }
    base.update(kw)
    return base


# ── clamp_meta：类型收敛 + 越界 ───────────────────────────────────────────
def test_clamp_meta_int_respects_min_max():
    assert clamp_meta(150, {"type": "int", "min": 0, "max": 100}) == 100
    assert clamp_meta(-5, {"type": "int", "min": 0, "max": 100}) == 0
    assert clamp_meta(50, {"type": "int", "min": 0, "max": 100}) == 50


def test_clamp_meta_float_respects_min_max():
    assert clamp_meta(1.5, {"type": "float", "min": 0.0, "max": 1.0}) == 1.0
    assert clamp_meta(-0.5, {"type": "float", "min": 0.0, "max": 1.0}) == 0.0
    assert clamp_meta(0.6, {"type": "float", "min": 0.0, "max": 1.0}) == 0.6


def test_clamp_meta_bool_coerces():
    assert clamp_meta(1, {"type": "bool"}) is True
    assert clamp_meta("", {"type": "bool"}) is False
    assert clamp_meta(None, {"type": "bool"}) is None


def test_clamp_meta_enum_valid_and_invalid():
    meta = {"type": "enum", "default": "strict", "options": [{"value": "auto"}, {"value": "strict"}]}
    assert clamp_meta("auto", meta) == "auto"
    assert clamp_meta("nope", meta) == "strict"  # 非法 -> 回退默认


def test_clamp_meta_str_and_none():
    assert clamp_meta("x", {"type": "str"}) == "x"
    assert clamp_meta(None, {"type": "str"}) is None


def test_clamp_meta_none_meta_returns_none():
    assert clamp_meta(None, {"type": "int"}) is None


# ── recommend_all：全组覆盖 + 值可落地 ────────────────────────────────────
def test_recommend_all_covers_all_9_groups():
    out = recommend_all(_mini_schema(), _signals())
    assert set(out.keys()) == set(ALL_GROUPS)


def test_recommend_all_every_value_within_bounds_and_type():
    schema = _mini_schema()
    out = recommend_all(schema, _signals())
    for gk, group in schema.items():
        assert set(out[gk].keys()) == set(group["items"].keys())
        for key, meta in group["items"].items():
            v = out[gk][key]
            t = meta.get("type", "str")
            if t == "bool":
                assert isinstance(v, bool), f"{gk}.{key} 应为 bool，实为 {v!r}"
            elif t == "int":
                assert isinstance(v, int), f"{gk}.{key} 应为 int，实为 {v!r}"
                assert meta["min"] <= v <= meta["max"], f"{gk}.{key}={v} 越界"
            elif t == "float":
                assert isinstance(v, float), f"{gk}.{key} 应为 float，实为 {v!r}"
                assert meta["min"] <= v <= meta["max"], f"{gk}.{key}={v} 越界"
            elif t == "enum":
                assert v in {o["value"] for o in meta.get("options", [])}
            else:
                assert isinstance(v, str)


# ── 信号驱动的启发式：推荐值随项目规模变化 ────────────────────────────────
def test_step4_heuristics_follow_scale():
    small = recommend_all(_mini_schema(), _signals(num_reqs=10))
    assert small["step4"]["step4_p1_sample_rate"] == 1.0
    assert small["step4"]["step4_include_p2"] is True

    large = recommend_all(_mini_schema(), _signals(num_reqs=60))
    assert large["step4"]["step4_p1_sample_rate"] == 0.5
    assert large["step4"]["step4_include_p2"] is False


def test_step4_plugins_defaults_on():
    out = recommend_all(_mini_schema(), _signals())
    assert out["step4"]["step4_plugins_a_enabled"] is True
    assert out["step4"]["step4_plugins_b_enabled"] is True


def test_rag_snippet_and_keyword_scale_with_docs():
    # 小文档 -> 短片段 + 大关键词数
    small = recommend_all(_mini_schema(), _signals(doc_total_chars=500_000))
    assert small["rag"]["rag_context_snippet_len"] == 200
    assert small["rag"]["rag_keyword_max_count"] == 50
    # 大文档(>1M) -> 长片段 + 收敛关键词数
    big = recommend_all(_mini_schema(), _signals(doc_total_chars=5_000_000))
    assert big["rag"]["rag_context_snippet_len"] == 400
    assert big["rag"]["rag_keyword_max_count"] == 30


def test_step_timeout_scales_with_reqs():
    n10 = recommend_all(_mini_schema(), _signals(num_reqs=10))
    n40 = recommend_all(_mini_schema(), _signals(num_reqs=40))
    assert n10["step_timeout"]["STEP3_TIMEOUT_SECONDS"] < n40["step_timeout"]["STEP3_TIMEOUT_SECONDS"]


def test_step2_step3_reuse_autotune_results():
    # step2/step3 走 _auto_tune：模型窗口越大，安全系数相关的分片更稳（此处仅验证返回非空且可落地）
    out = recommend_all(_mini_schema(), _signals(model_window=200_000))
    assert isinstance(out["step2"]["step2_shard_max_chars"], int)
    assert isinstance(out["step3"]["step3_top_n"], int)


# ── 健壮性：空 schema / 空 items / 异常信号 ──────────────────────────────
def test_recommend_all_empty_schema_returns_empty():
    assert recommend_all({}, _signals()) == {}


def test_recommend_all_group_with_none_items():
    out = recommend_all({"x": {"items": None}}, _signals())
    assert out["x"] == {}


def test_confidence_keeps_strict_ordering_and_scales_with_verification():
    # 无强验证信号 -> 默认档位，且低<中<高
    base = recommend_all(_mini_schema(), _signals(has_db=False, has_trace=False, num_reqs=10))
    c = base["confidence"]
    assert c["confidence_low_threshold"] == 0.50
    assert c["confidence_medium_threshold"] == 0.70
    assert c["confidence_high_threshold"] == 0.90
    assert c["confidence_low_threshold"] < c["confidence_medium_threshold"] < c["confidence_high_threshold"]
    # 三轨验证齐全 -> 三档整体上调，仍保持 低<中<高
    well = recommend_all(_mini_schema(), _signals(has_db=True, has_trace=True, num_reqs=20))
    cw = well["confidence"]
    assert cw["confidence_low_threshold"] == 0.55
    assert cw["confidence_medium_threshold"] == 0.75
    assert cw["confidence_high_threshold"] == 0.90
    assert cw["confidence_low_threshold"] < cw["confidence_medium_threshold"] < cw["confidence_high_threshold"]


def test_ai_timeout_scales_with_model_window_and_scale():
    small = recommend_all(_mini_schema(), _signals(model_window=8_192, num_reqs=10))
    assert small["ai"]["ai_local_timeout"] == 15
    mid = recommend_all(_mini_schema(), _signals(model_window=128_000, num_reqs=10))
    assert mid["ai"]["ai_local_timeout"] == 30
    huge = recommend_all(_mini_schema(), _signals(model_window=400_000, num_reqs=300))
    assert huge["ai"]["ai_local_timeout"] == 60


def test_recommend_all_bad_signal_values_no_crash():
    # 信号类型异常/缺失 -> 保守默认，绝不抛错
    out = recommend_all(_mini_schema(), {"num_reqs": "many", "doc_total_chars": None, "model_window": None})
    assert set(out.keys()) == set(ALL_GROUPS)
    for gk, gi in out.items():
        for k, v in gi.items():
            v  # 仅确保遍历不抛错（值类型/边界由上面全组用例保证）