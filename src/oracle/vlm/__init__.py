# -*- coding: utf-8 -*-
"""★ T04.1: VLM 视觉校验统一入口 [已复核-DeepSeek-V3 2026-08-16]"""

from src.oracle.vlm.protocol import VLMAdapter, VisualStatus, VisualVerdict
from src.oracle.vlm.registry import VLMRegistry, registry
from src.oracle.vlm.backends.local import (
    DomDiffer,
    ElementAsserter,
    LocalBackend,
    PerceptualHasher,
)

__all__ = [
    "VLMAdapter",
    "VisualStatus",
    "VisualVerdict",
    "VLMRegistry",
    "registry",
    "DomDiffer",
    "ElementAsserter",
    "LocalBackend",
    "PerceptualHasher",
]
