# -*- coding: utf-8 -*-
"""★ T04.1: VLM 后端注册与发现 [已复核-DeepSeek-V3 2026-08-16]
按 priority 排序；get_available() 返回可用列表；verify_with_fallback 依次降级。
"""

from __future__ import annotations
from src.core.guarded import guard

import logging
from typing import Any, Optional

from src.oracle.vlm.protocol import VLMAdapter, VisualStatus, VisualVerdict

_log = logging.getLogger(__name__)


class VLMRegistry:
    """VLM 适配器注册表（priority 升序，低值优先）。"""
    def __init__(self) -> None:
        self._backends: list[VLMAdapter] = []

    def register(self, adapter: VLMAdapter) -> None:
        if adapter is None or any(b.name == adapter.name for b in self._backends):
            return
        self._backends.append(adapter)
        self._backends.sort(key=lambda b: b.priority)

    def get_available(self) -> list[VLMAdapter]:
        return [b for b in self._backends if b.is_available()]

    def get_adapter(self, name: str) -> Optional[VLMAdapter]:
        return next((b for b in self._backends if b.name == name), None)

    def verify_with_fallback(
        self,
        screenshot_bytes: Optional[bytes],
        assertion: str,
        context: dict[str, Any],
    ) -> VisualVerdict:
        """依次尝试可用后端；拿到 PASS/FAIL 即返回，全 NO_DATA 返回首个 NO_DATA。"""
        available = self.get_available()
        if not available:
            return VisualVerdict(
                status=VisualStatus.NO_DATA,
                evidence={"reason": "无可用视觉后端"},
                backend_name="none",
            )
        last: Optional[VisualVerdict] = None
        for backend in available:
            try:
                verdict = backend.verify(screenshot_bytes, assertion, context)
            except Exception as e:  # noqa: BLE001 —— 单后端异常不阻断降级链
                _log.warning("[VLMRegistry] %s 校验异常(降级): %s", backend.name, str(e)[:80])
                continue
            last = verdict
            if verdict.is_conclusive():
                return verdict
        return last or VisualVerdict(
            status=VisualStatus.NO_DATA,
            evidence={"reason": "所有后端均无法判定"},
            backend_name="none",
        )


# 模块级单例：LocalBackend 纯本地零依赖，自注册
registry = VLMRegistry()
with guard("from src.oracle.vlm.backends.local impor", log=_log, level=logging.WARNING):
    from src.oracle.vlm.backends.local import LocalBackend

    registry.register(LocalBackend())
