# -*- coding: utf-8 -*-
"""
AI Provider 抽象适配层
=======================
统一封装 OpenAI / DeepSeek / Anthropic 的 API 差异，
对外暴露一致的 chat() 接口。

Provider 对比：
    OpenAI/DeepSeek:  POST /v1/chat/completions  + Bearer Token
    Anthropic:        POST /v1/messages           + x-api-key
    Ollama (本地):     POST /api/generate          + 无认证

每个 Provider 返回统一格式：
    {"success": True/False, "data": "文本", "usage": {...}, ...}

【2026-06-18 重构】原 436 行单文件按 Provider 拆分：
- base.py            — BaseProvider 抽象类
- openai_compat.py   — _OpenAICompatProvider（chat + chat_vision）
- deepseek_openai.py — DeepSeekProvider + OpenAIProvider
- anthropic.py       — AnthropicProvider
- __init__.py        — get_provider 工厂函数 + re-export
"""

from .base import BaseProvider
from .openai_compat import _OpenAICompatProvider
from .deepseek_openai import DeepSeekProvider, OpenAIProvider
from .anthropic import AnthropicProvider
from .ollama import OllamaProvider


# =============================================================================
# 工厂函数
# =============================================================================


def get_provider(
    name: str,
    api_key: str,
    base_url: str,
    model: str,
    timeout: int = 120,
    max_retries: int = 3,
) -> BaseProvider:
    """
    根据 provider 名称创建对应的 Provider 实例。

    参数：
        name:     "deepseek" / "openai" / "anthropic"
        api_key:  API 密钥
        base_url: API 基础地址
        model:    模型名称
        timeout:  超时秒数
        max_retries: 最大重试次数

    返回：
        BaseProvider 子类实例
    """
    name_lower = name.lower()
    if name_lower == "deepseek":
        return DeepSeekProvider(api_key, base_url, model, timeout, max_retries)
    elif name_lower == "openai" or name_lower == "nvidia":
        return OpenAIProvider(api_key, base_url, model, timeout, max_retries)
    elif name_lower == "anthropic":
        return AnthropicProvider(api_key, base_url, model, timeout, max_retries)
    elif name_lower in ("ollama", "local"):
        return OllamaProvider(api_key, base_url, model, timeout, max_retries)
    else:
        raise ValueError(
            f"不支持的 AI Provider: {name}，可选 deepseek / openai / anthropic / nvidia / ollama"
        )


__all__ = [
    "BaseProvider",
    "_OpenAICompatProvider",
    "DeepSeekProvider",
    "OpenAIProvider",
    "AnthropicProvider",
    "OllamaProvider",
    "get_provider",
]
