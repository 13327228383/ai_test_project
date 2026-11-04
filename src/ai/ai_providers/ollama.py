# -*- coding: utf-8 -*-
from __future__ import annotations

"""本地 Ollama Provider（chat + chat_vision） — 原生 /api/generate 接口，无鉴权。

与云端 OpenAI 兼容 Provider 的差异：
    - POST {base}/api/generate        （无 Authorization，需 base64 图片字段）
    - tool_calls / json_mode 原生不支持，由上层 rules 路径兜底

★ 根因修复：此前视觉路径无 Ollama Provider，call_vision 用 detect_provider
  判定模型（本地多模态模型如 qwen2.5vl/llava 被回退到 deepseek），并对 vision_provider=ollama
  一刀切跳过，导致本地多模态模型无法用于视觉点击/视觉验证。新增本类 + get_provider("ollama")
  + call_vision 按 vision_provider 路由，实现真正的本地视觉能力。
"""

import logging
import time
from typing import Any

import requests

from .base import BaseProvider

_log = logging.getLogger(__name__)


class OllamaProvider(BaseProvider):
    """原生 Ollama Provider。

    视觉能力：多模态模型可用时通过 /api/generate 的 images 字段传入 base64 图片。
    token 统计从 prompt_eval_count / eval_count 推断。
    """

    CAPABILITIES = {
        "tools": False,  # 不依赖工具调用，测试动作由规则路径执行
        "vision": True,  # 多模态模型可视觉输入（能力以模型为准）
        "streaming": True,
        "json_mode": False,
    }

    @property
    def source(self) -> str:
        return "ollama"

    @staticmethod
    def _build_url(base: str) -> str:
        return f"{base.rstrip('/')}/api/generate"

    def chat(
        self,
        system_prompt: str,
        user_message: str,
        temperature: float = 0.3,
        max_tokens: int = 8192,
        model: str | None = None,
        tools=None,
        tool_choice: str | None = None,
        response_format: dict[str, Any] | None = None,
        seed: int | None = None,
    ) -> dict[str, Any]:
        effective_model = model or self._model
        if not effective_model:
            return {"success": False, "error": "本地模型名为空", "error_type": "config_missing"}
        url = self._build_url(self._base_url)
        prompt = f"{system_prompt}\n\n{user_message}".strip() if system_prompt else user_message
        payload: dict[str, Any] = {
            "model": effective_model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": temperature, "num_predict": max_tokens},
        }
        if seed is not None:
            payload["options"]["seed"] = int(seed)
        if response_format:
            # Ollama 常规模式不支持 response_format，交由上层 parse 兜底
            _log.debug("[ollama] 忽略 response_format（原生接口不支持）")
        return self._post(url, payload, model=effective_model)

    def chat_vision(
        self,
        image_base64: str,
        prompt: str,
        temperature: float = 0.0,
        max_tokens: int = 256,
    ) -> dict[str, Any]:
        if not self._model:
            return {"success": False, "error": "本地视觉模型名为空", "error_type": "config_missing"}
        url = self._build_url(self._base_url)
        payload: dict[str, Any] = {
            "model": self._model,
            "prompt": prompt,
            "images": [image_base64],  # 原生 base64，无 data: 前缀
            "stream": False,
            "options": {"temperature": temperature, "num_predict": max_tokens},
        }
        return self._post(url, payload, model=self._model)

    def embed(self, texts: list[str], model: str | None = None) -> dict[str, Any]:
        """Ollama 原生嵌入：/api/embed 或 /api/embeddings。"""
        url = f"{self._base_url}/api/embeddings"
        effective_model = model or self._model
        res = [] if isinstance(texts, list) else None
        payload = {"model": effective_model, "input": texts}
        t0 = time.time()
        try:
            resp = requests.post(url, json=payload, timeout=self._timeout)
            elapsed = time.time() - t0
            if resp.status_code == 200:
                data = resp.json()
                # /api/embeddings → {"embeddings": [[...], ...]}; 兼容单条 {"embedding":[...]}
                emb = data.get("embeddings") or (
                    [data.get("embedding")] if data.get("embedding") else []
                )
                return {"success": True, "data": emb, "model": effective_model,
                        "usage": {}, "_elapsed": elapsed}
            return {"success": False, "error": f"HTTP {resp.status_code}: {resp.text[:200]}",
                    "_elapsed": elapsed}
        except Exception as e:
            return {"success": False, "error": str(e), "_elapsed": time.time() - t0}

    def _post(self, url: str, payload: dict, model: str) -> dict[str, Any]:
        t0 = time.time()
        try:
            resp = requests.post(
                url,
                json=payload,
                timeout=self._timeout,
                headers={"Content-Type": "application/json"},
            )
            elapsed = time.time() - t0
            if resp.status_code != 200:
                _log.error("ollama HTTP %d (%.1fs): %s", resp.status_code, elapsed, resp.text[:200])
                return {"success": False, "error": "Ollama HTTP %d: %s" % (resp.status_code, resp.text[:200]),
                        "error_type": "http_error", "source": self.source, "_elapsed": elapsed}
            data = resp.json()
            content = data.get("response", "")
            usage = {
                "prompt_tokens": data.get("prompt_eval_count", 0),
                "completion_tokens": data.get("eval_count", 0),
            }
            _log.info("ollama ✓ (%.1fs) %d chars tokens=[in:%d out:%d] model=%s",
                      elapsed, len(content), usage["prompt_tokens"], usage["completion_tokens"], model)
            return {
                "success": True,
                "data": content,
                "model": model,
                "source": self.source,
                "usage": usage,
                "_elapsed": elapsed,
                "_cost": 0.0,
            }
        except requests.exceptions.Timeout:
            return {"success": False, "error": "本地 Ollama 超时（%.1fs）" % (time.time() - t0),
                    "error_type": "timeout", "source": self.source, "_elapsed": time.time() - t0}
        except requests.exceptions.ConnectionError as e:
            return {"success": False, "error": "本地 Ollama 连接失败: %s" % e,
                    "error_type": "connection_error", "source": self.source, "_elapsed": time.time() - t0}
        except Exception as e:
            return {"success": False, "error": "本地异常: %s" % e,
                    "error_type": "unknown_error", "source": self.source, "_elapsed": time.time() - t0}