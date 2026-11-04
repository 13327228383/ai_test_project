# -*- coding: utf-8 -*-
from __future__ import annotations

"""
business_capabilities.py — 12 个通用业务能力检测器（facade）
==========================================================
在 Step4 的 browser_ai / playwright_engine 拿到 compact_state 与意图文本之后，
本模块负责把意图归类到 12 种预定义业务能力，便于后续走对应的动作模板 / 能力缓存。

对外 API：
    - detect_capability(compact_state, goal_text, intent_schema=None) -> (hint, score) | None
    - register_capability(hint, def_dict)                                        # 动态注册
    - get_capability(hint) -> def_dict | None
    - list_capabilities() -> list[str]

实现采用 mixin 多继承模式，拆分为 4 个文件：
    - _base.py      : _BusinessCapBaseMixin    (常量、辅助函数、埋点)
    - _detectors.py : _BusinessCapDetectorsMixin (12 个 detector)
    - _registry.py  : _BusinessCapRegistryMixin  (注册表、管理 API)
    - _detect.py    : _BusinessCapDetectMixin    (主判定、解释)
"""

from typing import Any

from src.pipeline.steps.step4.plugins.business_capabilities.base import (
    _BusinessCapBaseMixin,
)
from src.pipeline.steps.step4.plugins.business_capabilities.detectors import (
    _BusinessCapDetectorsMixin,
)
from src.pipeline.steps.step4.plugins.business_capabilities.registry import (
    _BusinessCapRegistryMixin,
)
from src.pipeline.steps.step4.plugins.business_capabilities.detect import (
    _BusinessCapDetectMixin,
)


class BusinessCapabilities(
    _BusinessCapBaseMixin,
    _BusinessCapDetectorsMixin,
    _BusinessCapRegistryMixin,
    _BusinessCapDetectMixin,
):
    """12 个通用业务能力的统一入口（规则判定，不调用 AI）。"""


# ---------------------------------------------------------------------------
# 便捷函数（直接调用单例）
# ---------------------------------------------------------------------------
def detect_capability(
    compact_state: Any, goal_text: str, intent_schema: dict[str, Any] | None = None
) -> tuple[str, float] | None:
    return business_capabilities.detect_capability(
        compact_state, goal_text, intent_schema
    )


def register_capability(hint: str, def_dict: dict[str, Any]) -> bool:
    return business_capabilities.register_capability(hint, def_dict)


def get_capability(hint: str) -> dict[str, Any] | None:
    return business_capabilities.get_capability(hint)


def list_capabilities() -> list[str]:
    return business_capabilities.list_capabilities()


# 单例实例
# ★ 方案C 改造：business_capabilities 改为 PerProjectRegistry，按 project 隔离
from src.core.utils.per_project_registry import PerProjectRegistry

business_capabilities = PerProjectRegistry(BusinessCapabilities)
