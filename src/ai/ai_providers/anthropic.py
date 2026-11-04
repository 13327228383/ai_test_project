# -*- coding: utf-8 -*-
from __future__ import annotations

"""Anthropic Provider（原生 Messages 格式）— 从 ai_providers.py 拆出，零业务逻辑变更。"""

import time
import requests
from typing import Any

from .base import BaseProvider


class AnthropicProvider(BaseProvider):
    """
    Anthropic API（原生 Messages 格式）。
    POST /v1/messages  + x-api-key + anthropic-version
    """

    # Anthropic 当前实现：暂不支持 tools（chat() 签名未接收 tools 参数）
    # vision 和 streaming 在 API 层面支持，但当前实现未暴露
    CAPABILITIES = {
        "tools": False,  # ← 当前 chat() 未实现 tools 透传
        "vision": False,  # ← 当前实现未暴露 vision 接口
        "streaming": False,
        "json_mode": False,
    }

    ANTHROPIC_VERSION = "2023-06-01"

    @property
    def source(self) -> str:
        return "anthropic"

    _PRICING = {
        "claude-opus-4-7": {"input": 15.00, "output": 75.00},
        "claude-sonnet-4-6": {"input": 3.00, "output": 15.00},
        "claude-haiku-4-5": {"input": 1.00, "output": 5.00},
        "_default": {"input": 3.00, "output": 15.00},
    }

    def chat(
        self,
        system_prompt: str = "",
        user_message: str = "",
        temperature: float = 0.3,
        max_tokens: int = 8192,
        model: str | None = None,
        **kwargs,
    ) -> dict[str, Any]:
        base = self._base_url.rstrip("/")
        url = f"{base}/messages" if base.endswith("/v1") else f"{base}/v1/messages"
        headers = {
            "x-api-key": self._api_key,
            "anthropic-version": self.ANTHROPIC_VERSION,
            "Content-Type": "application/json",
        }
        effective_model = model or self._model
        payload = {
            "model": effective_model,
            "system": system_prompt,
            "messages": [
                {"role": "user", "content": user_message},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        last_error = None
        for attempt in range(self._max_retries + 1):
            t0 = time.time()
            try:
                resp = requests.post(
                    url, headers=headers, json=payload, timeout=self._timeout
                )
                elapsed = time.time() - t0

                if resp.status_code == 200:
                    data = resp.json()
                    # Anthropic 响应: content = [{type: "text", text: "..."}]
                    content_blocks = data.get("content", [])
                    text = "".join(
                        b.get("text", "")
                        for b in content_blocks
                        if b.get("type") == "text"
                    )
                    usage = data.get("usage", {})
                    cost = self._estimate_cost(usage)
                    return {
                        "success": True,
                        "data": text,
                        "usage": {
                            "prompt_tokens": usage.get("input_tokens", 0),
                            "completion_tokens": usage.get("output_tokens", 0),
                        },
                        "model": self._model,
                        "source": self.source,
                        "_elapsed": elapsed,
                        "_cost": cost,
                    }

                is_retryable = resp.status_code >= 500 or resp.status_code == 429
                last_error = f"Anthropic HTTP {resp.status_code}: {resp.text[:200]}"

                if not is_retryable or attempt >= self._max_retries:
                    return {
                        "success": False,
                        "error": last_error,
                        "error_type": "http_error",
                        "source": self.source,
                        "_elapsed": elapsed,
                    }

            except requests.exceptions.Timeout:
                last_error = "Anthropic 超时"
                if attempt >= self._max_retries:
                    return {
                        "success": False,
                        "error": last_error,
                        "error_type": "timeout",
                        "source": self.source,
                        "_elapsed": time.time() - t0,
                    }
            except requests.exceptions.ConnectionError as e:
                last_error = f"Anthropic 连接异常: {e}"
                if attempt >= self._max_retries:
                    return {
                        "success": False,
                        "error": last_error,
                        "error_type": "connection_error",
                        "source": self.source,
                        "_elapsed": time.time() - t0,
                    }
            except Exception as e:
                return {
                    "success": False,
                    "error": f"Anthropic 异常: {e}",
                    "error_type": "unknown_error",
                    "source": self.source,
                    "_elapsed": time.time() - t0,
                }

            if attempt < self._max_retries:
                delay = 2 ** (attempt + 1)
                time.sleep(delay)

        return {
            "success": False,
            "error": last_error or "重试耗尽",
            "error_type": "retry_exhausted",
            "source": self.source,
            "_elapsed": time.time() - t0,
        }

    def _estimate_cost(self, usage: dict) -> float:
        pricing = self._PRICING.get(self._model, self._PRICING["_default"])
        input_tokens = usage.get("input_tokens", 0)
        output_tokens = usage.get("output_tokens", 0)
        return (input_tokens / 1_000_000) * pricing["input"] + (
            output_tokens / 1_000_000
        ) * pricing["output"]
