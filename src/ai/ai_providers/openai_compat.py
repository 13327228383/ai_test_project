# -*- coding: utf-8 -*-
from __future__ import annotations

"""OpenAI 兼容 Provider 基类（chat + chat_vision，DeepSeek/OpenAI 共用）— 从 ai_providers.py 拆出，零业务逻辑变更。"""

import os
import time
import queue
import threading
import logging
import requests
from typing import Any

from .base import BaseProvider

_log = logging.getLogger(__name__)


class _OpenAICompatProvider(BaseProvider):
    """
    OpenAI 兼容格式的 Provider 基类（DeepSeek / OpenAI 共用）。
    POST /v1/chat/completions  + Authorization: Bearer <key>
    """

    # OpenAI 兼容 Provider 支持 tools、vision、streaming、json_mode
    CAPABILITIES = {
        "tools": True,
        "vision": True,
        "streaming": True,
        "json_mode": True,
    }

    @staticmethod
    def _post_with_deadline(
        url: str, headers: dict, payload: dict, timeout_seconds: float
    ):
        """带硬截止时间的 POST。

        requests 的 read timeout 在部分代理持续保活但不返回完整响应时可能无法按预期结束；
        这里用 daemon 线程给调用方一个硬截止时间，防止 Step2/验收页面长时间卡死。
        """
        result_q = queue.Queue(maxsize=1)

        def _worker():
            try:
                resp = requests.post(
                    url,
                    headers=headers,
                    json=payload,
                    timeout=(min(10.0, timeout_seconds), timeout_seconds),
                )
                result_q.put(("ok", resp))
            except Exception as e:
                result_q.put(("err", e))

        th = threading.Thread(target=_worker, daemon=True)
        th.start()
        try:
            status, value = result_q.get(timeout=timeout_seconds + 1.0)
        except queue.Empty:
            raise requests.exceptions.Timeout(
                f"硬超时 {timeout_seconds:.0f}s：云端模型未在验收窗口内返回"
            )
        if status == "err":
            raise value
        return value

    def chat(
        self,
        system_prompt: str,
        user_message: str,
        temperature: float = 0.3,
        max_tokens: int = 8192,
        model: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | None = None,
        response_format: dict[str, Any] | None = None,
        seed: int | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        base = self._base_url.rstrip("/")
        url = (
            f"{base}/chat/completions"
            if base.endswith("/v1")
            else f"{base}/v1/chat/completions"
        )
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._api_key}",
        }
        effective_model = model or self._model
        payload = {
            "model": effective_model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        # ── 工具调用参数 ──
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = tool_choice or "auto"
        # ★ 优化2: Structured Output 透传（OpenAI 兼容 API response_format 字段）
        # 云模型不支持时会被忽略，不会报错（安全降级）
        if response_format:
            payload["response_format"] = response_format
        # ★ 确定性采样: seed 参数（DeepSeek/SiliconFlow 等 OpenAI 兼容 API 均支持）
        if seed is not None:
            payload["seed"] = seed

        last_error = None
        # 调用级 timeout 覆盖实例级默认（供有兜底降级的辅助调用传短超时，防 LLM 黑洞吞掉用例预算）
        effective_timeout = timeout if timeout is not None else self._timeout
        try:
            # ★ v3.0.1 (2026-07-10): 环境变量仅作 fallback，不再封顶 config.yaml 的 timeout
            #   原 min(120, 75)=75s 导致 config 设 120s 实际只有 75s，违反用户 3 分钟约束
            _env_to = float(os.getenv("AI_CLOUD_REQUEST_TIMEOUT", "0"))
            if _env_to > 0 and _env_to < effective_timeout:
                effective_timeout = _env_to
        except Exception:
            _log.warning("读取超时环境变量失败", exc_info=True)
            effective_timeout = self._timeout

        for attempt in range(self._max_retries + 1):
            t0 = time.time()
            try:
                _log.debug(
                    "[AIProvider] POST %s model=%s timeout=%ss max_tokens=%s",
                    url,
                    effective_model,
                    effective_timeout,
                    max_tokens,
                )
                resp = self._post_with_deadline(
                    url, headers, payload, effective_timeout
                )
                elapsed = time.time() - t0

                if resp.status_code == 200:
                    data = resp.json()
                    msg = data.get("choices", [{}])[0].get("message", {})
                    content = msg.get("content", "")
                    # ★ 推理模型兼容: deepseek-v4-pro 等推理模型将最终答案放在
                    #    reasoning_content 中，content 可能为空（max_tokens 不足以同时输出推理+答案时）。
                    #    优先取 content，为空时 fallback 到 reasoning_content。
                    if not content:
                        content = msg.get("reasoning_content", "")
                    usage = data.get("usage", {})
                    cost = self._estimate_cost(usage)
                    result = {
                        "success": True,
                        "data": content,
                        "usage": {
                            "prompt_tokens": usage.get("prompt_tokens", 0),
                            "completion_tokens": usage.get("completion_tokens", 0),
                        },
                        "model": self._model,
                        "source": self.source,
                        "_elapsed": elapsed,
                        "_cost": cost,
                    }
                    # ── 提取 tool_calls ──
                    raw_tool_calls = msg.get("tool_calls")
                    if raw_tool_calls:
                        result["tool_calls"] = raw_tool_calls
                    # ★ v3.0.1 (2026-07-10): 检查 finish_reason 截断并记录警告
                    _finish_reason = data.get("choices", [{}])[0].get(
                        "finish_reason", ""
                    )
                    if _finish_reason == "length":
                        _log.warning(
                            "[AIProvider] 输出被 max_tokens=%s 截断 (finish_reason=length), "
                            "model=%s, completion_tokens=%s, 考虑增大 max_tokens 或拆分任务",
                            max_tokens,
                            self._model,
                            usage.get("completion_tokens", 0),
                        )
                        result["_truncated"] = True
                        result["_finish_reason"] = "length"
                    return result

                is_retryable = resp.status_code >= 500 or resp.status_code == 429
                last_error = f"{self.source} HTTP {resp.status_code}: {resp.text[:200]}"

                if not is_retryable or attempt >= self._max_retries:
                    return {
                        "success": False,
                        "error": last_error,
                        "error_type": "http_error",
                        "source": self.source,
                        "_elapsed": elapsed,
                    }

            except requests.exceptions.Timeout:
                last_error = f"{self.source} 超时"
                if attempt >= self._max_retries:
                    return {
                        "success": False,
                        "error": last_error,
                        "error_type": "timeout",
                        "source": self.source,
                        "_elapsed": time.time() - t0,
                    }
            except requests.exceptions.ConnectionError as e:
                last_error = f"{self.source} 连接异常: {e}"
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
                    "error": f"{self.source} 异常: {e}",
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

    def embed(self, texts: list[str], model: str | None = None) -> dict[str, Any]:
        """文本嵌入向量化（OpenAI 兼容 /v1/embeddings）。

        SiliconFlow 支持: BAAI/bge-large-zh-v1.5（1024 维，中文优化）
        """
        base = self._base_url.rstrip("/")
        url = (
            f"{base}/embeddings"
            if base.endswith("/v1")
            else f"{base}/v1/embeddings"
        )
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._api_key}",
        }
        effective_model = model or "BAAI/bge-large-zh-v1.5"
        payload = {
            "model": effective_model,
            "input": texts,
        }
        t0 = time.time()
        try:
            resp = self._post_with_deadline(url, headers, payload, self._timeout)
            elapsed = time.time() - t0
            if resp.status_code == 200:
                data = resp.json()
                embeddings = [d["embedding"] for d in data.get("data", [])]
                usage = data.get("usage", {})
                return {
                    "success": True,
                    "data": embeddings,
                    "usage": {
                        "prompt_tokens": usage.get("prompt_tokens", 0),
                        "total_tokens": usage.get("total_tokens", 0),
                    },
                    "model": effective_model,
                    "source": self.source,
                    "_elapsed": elapsed,
                }
            return {
                "success": False,
                "error": f"HTTP {resp.status_code}: {resp.text[:200]}",
                "_elapsed": elapsed,
            }
        except Exception as e:
            return {"success": False, "error": str(e), "_elapsed": time.time() - t0}

    @staticmethod
    def _estimate_cost(usage: dict) -> float:
        """子类覆盖以实现计费。"""
        return 0.0

    def chat_vision(
        self,
        image_base64: str,
        prompt: str,
        temperature: float = 0.0,
        max_tokens: int = 256,
    ) -> dict[str, Any]:
        """
        ★ v5：视觉模型调用（截图→模型识别）。
        OpenAI 兼容格式：content 数组含 text + image_url。
        """
        base = self._base_url.rstrip("/")
        url = (
            f"{base}/chat/completions"
            if base.endswith("/v1")
            else f"{base}/v1/chat/completions"
        )
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._api_key}",
        }
        payload = {
            "model": self._model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/png;base64,{image_base64}",
                                "detail": "low",
                            },
                        },
                    ],
                }
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        t0 = time.time()
        try:
            resp = requests.post(
                url, headers=headers, json=payload, timeout=self._timeout
            )
            elapsed = time.time() - t0
            if resp.status_code == 200:
                data = resp.json()
                content = (
                    data.get("choices", [{}])[0].get("message", {}).get("content", "")
                )
                if not content:
                    content = (
                        data.get("choices", [{}])[0]
                        .get("message", {})
                        .get("reasoning_content", "")
                    )
                usage = data.get("usage", {})
                cost = self._estimate_cost(usage)
                return {
                    "success": True,
                    "data": content,
                    "usage": usage,
                    "model": self._model,
                    "source": self.source,
                    "_elapsed": elapsed,
                    "_cost": cost,
                }
            return {
                "success": False,
                "error": f"HTTP {resp.status_code}: {resp.text[:200]}",
                "_elapsed": elapsed,
            }
        except Exception as e:
            return {"success": False, "error": str(e), "_elapsed": time.time() - t0}
