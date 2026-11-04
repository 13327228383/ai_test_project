# -*- coding: utf-8 -*-
"""AI 服务健康检查 mixin。"""

import time

import requests


class HealthCheckerAiMixin:
    """提供 DeepSeek / OpenAI / Anthropic API 健康检查。"""

    def check_deepseek(self) -> dict:
        """检查 DeepSeek API 可用性。"""
        return self.check_ai_api("deepseek")

    def check_openai(self) -> dict:
        """检查 OpenAI API 可用性。"""
        return self.check_ai_api("openai")

    def check_anthropic(self) -> dict:
        """检查 Anthropic API 可用性。"""
        return self.check_ai_api("anthropic")

    def check_ai_api(self, provider: str = "deepseek") -> dict:
        """
        检查 AI API 可用性。
        通过发送一个极轻量的请求（如获取模型列表或简单 chat）验证 key 是否有效。
        """
        t0 = time.time()
        try:
            from src.core.config_loader import config

            key = config.ai_api_key
            if not key:
                return self._result(
                    f"ai_api_{provider}",
                    False,
                    (time.time() - t0) * 1000,
                    "API Key 未配置",
                )

            base_url = config.ai_base_url
            base_url = base_url.rstrip("/")
            models_path = "models" if base_url.endswith("/v1") else "v1/models"
            if provider == "anthropic":
                url = f"{base_url}/{models_path}"
                headers = {"x-api-key": key, "anthropic-version": "2023-06-01"}
            else:
                url = f"{base_url}/{models_path}"
                headers = {"Authorization": f"Bearer {key}"}

            resp = requests.get(url, headers=headers, timeout=self.TIMEOUT)
            latency = (time.time() - t0) * 1000
            if resp.status_code in (200, 401):
                # 401 说明服务器可达，只是权限问题（某些 provider 模型列表不开放）
                if resp.status_code == 200:
                    return self._result(f"ai_api_{provider}", True, latency)
                # 对 401 做进一步验证：发一个极简 chat
                return self._check_ai_chat(provider, key, base_url, t0)
            return self._result(
                f"ai_api_{provider}", False, latency, f"HTTP {resp.status_code}"
            )
        except requests.exceptions.Timeout:
            return self._result(
                f"ai_api_{provider}", False, (time.time() - t0) * 1000, "请求超时"
            )
        except requests.exceptions.ConnectionError as e:
            return self._result(
                f"ai_api_{provider}", False, (time.time() - t0) * 1000, f"连接失败: {e}"
            )
        except Exception as e:
            return self._result(
                f"ai_api_{provider}", False, (time.time() - t0) * 1000, str(e)[:200]
            )

    def _check_ai_chat(self, provider: str, key: str, base_url: str, t0: float) -> dict:
        """通过极简 chat 请求验证 AI API 可用性。"""
        try:
            # ★ 从配置读取模型名（config 已走 user_credentials），无硬编码兜底
            try:
                from src.core.config_loader import config as _hc_cfg

                default_model = str(getattr(_hc_cfg, "ai_model", "") or "")
            except Exception:
                default_model = ""

            if not default_model:
                return self._result(
                    f"ai_api_{provider}",
                    False,
                    (time.time() - t0) * 1000,
                    "未配置 AI_MODEL，无法进行 chat 健康检查",
                )

            base_url = base_url.rstrip("/")
            messages_path = "messages" if base_url.endswith("/v1") else "v1/messages"
            if provider == "anthropic":
                url = f"{base_url}/{messages_path}"
                headers = {
                    "x-api-key": key,
                    "anthropic-version": "2023-06-01",
                    "Content-Type": "application/json",
                }
                payload = {
                    "model": default_model,
                    "messages": [{"role": "user", "content": "Hi"}],
                    "max_tokens": 1,
                }
            else:
                chat_path = (
                    "chat/completions"
                    if base_url.endswith("/v1")
                    else "v1/chat/completions"
                )
                url = f"{base_url}/{chat_path}"
                headers = {
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                }
                payload = {
                    "model": default_model,
                    "messages": [{"role": "user", "content": "Hi"}],
                    "max_tokens": 1,
                }
            resp = requests.post(
                url, headers=headers, json=payload, timeout=self.TIMEOUT
            )
            latency = (time.time() - t0) * 1000
            if resp.status_code == 200:
                return self._result(f"ai_api_{provider}", True, latency)
            return self._result(
                f"ai_api_{provider}", False, latency, f"Chat HTTP {resp.status_code}"
            )
        except Exception as e:
            return self._result(
                f"ai_api_{provider}", False, (time.time() - t0) * 1000, str(e)[:200]
            )
