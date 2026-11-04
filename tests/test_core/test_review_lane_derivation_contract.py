# -*- coding: utf-8 -*-
"""D43 契约：三车道路由判定必须是可独立调用的纯函数，且在持久层缺失时可从需求派生。

根因（实测）：lane 判定此前只以流程副作用形式存在于 step2.py 主流程体内，
不经过 Step2 的项目（外部导入需求 / 历史项目 / 单步重跑 Step3）永久没有 lane
→ 审核闸门在"确认率恒为 0"上死锁，batch-approve 返回 400。
"""
from __future__ import annotations

import inspect

from src.pipeline.utils import review_lane_store as L


class TestLaneClassificationIsSingleSource:
    """lane 判定 = 需求属性，必须可独立于 Step2 流程调用。"""

    def test_classify_lane_exists_and_is_pure(self):
        assert hasattr(L, "classify_lane")
        sig = inspect.signature(L.classify_lane)
        assert list(sig.parameters) == ["req"], "classify_lane 必须只吃 req（纯函数）"
        lane, reason = L.classify_lane({})
        assert lane in ("green", "yellow", "red")
        assert isinstance(reason, str) and reason

    def test_manual_review_forces_yellow(self):
        lane, _ = L.classify_lane({"_needs_manual_review": True, "description": "x" * 500})
        assert lane == "yellow", "embed 合并切片必须强制 yellow"

    def test_thresholds_are_module_level_constants(self):
        assert L.LANE_GREEN_MIN == 85
        assert L.LANE_YELLOW_MIN == 60

    def test_high_confidence_green(self):
        # 构造一个六维全满的需求，应落 green
        req = {
            "description": "管理员可在商品页新增商品，填写名称、价格、库存后保存。" * 3,
            "source": "需求文档 3.1 商品管理",
            "source_quote": "管理员可在商品页新增商品",
            "expected_feedback": "保存后重定向回列表",
            "section": "3.1",
            "module": "商品管理",
            "operation": "新增商品",
        }
        lane, _ = L.classify_lane(req)
        assert lane in ("green", "yellow"), f"高置信需求不应落 red，实际 {lane}"


class TestDerivationFallback:
    def test_derive_function_exists(self):
        assert hasattr(L, "derive_lanes_from_requirements")
        assert hasattr(L, "ensure_review_lanes")

    def test_ensure_is_callable_with_bad_args(self):
        # 非法入参必须安全返回 0，不抛异常
        assert L.ensure_review_lanes("", 0) == 0
        assert L.ensure_review_lanes("", -1) == 0

    def test_classify_failure_degrades_to_yellow(self):
        """置信度计算异常时必须保守降级 yellow，而不是崩溃或误放行 green。"""
        class _Boom(dict):
            def get(self, *a, **k):  # noqa: D102
                raise RuntimeError("boom")

        try:
            lane, reason = L.classify_lane(_Boom())
        except RuntimeError:
            raise AssertionError("classify_lane 不得把置信度异常透传出去")
        assert lane in ("green", "yellow", "red")
        assert reason


class TestStep2DelegatesToSingleSource:
    """step2 不得再自行推导 lane —— 否则又是同一语义两处推导。"""

    def test_step2_calls_classify_lane(self):
        src = inspect.getsource(__import__("src.pipeline.step2", fromlist=["x"]))
        assert "classify_lane" in src, "step2 必须委托 review_lane_store.classify_lane"

    def test_step2_has_no_inline_threshold_pair(self):
        """不得再出现 `conf_score >= 85` / `conf_score >= 60` 这类内联双阈值。"""
        src = inspect.getsource(__import__("src.pipeline.step2", fromlist=["x"]))
        assert "conf_score >= 85" not in src, "step2 内联 lane 阈值 = 重复推导"
        assert "conf_score >= 60" not in src, "step2 内联 lane 阈值 = 重复推导"


class TestGateAndEndpointHaveDerivationHook:
    """闸门与审核端点都必须能派生 lane，否则外部导入需求的项目永久死锁。"""

    @staticmethod
    def _exec_route_src() -> str:
        import importlib.util

        spec = importlib.util.find_spec("src.backend.routes.execution")
        with open(spec.origin, encoding="utf-8") as fh:
            return fh.read()

    def test_lane_map_loader_ensures_lanes(self):
        from src.backend.routes.case_review import _routes as R

        src = inspect.getsource(R)
        assert "ensure_review_lanes" in src, "_load_lane_map 必须调用 ensure_review_lanes"

    def test_execution_gate_ensures_lanes(self):
        assert "ensure_review_lanes" in self._exec_route_src(), (
            "Step4 闸门必须调用 ensure_review_lanes"
        )

    def test_gate_uses_versioning_single_source(self):
        assert "current_version_id" in self._exec_route_src(), (
            "闸门取版本必须复用 src.core.versioning 唯一实现"
        )
