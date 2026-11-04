# -*- coding: utf-8 -*-
"""
模型注册与发现模块 (ModelRegistry)
===================================
负责模型列表拉取、缓存、视觉能力检测等，从 config_loader.py 拆分而来。

功能：
1. 模型名称 → 多模态能力推断（is_vision_model）
2. 从各 Provider 拉取模型列表（fetch_provider_models）
3. 模型列表持久化缓存（24h 过期，JSON 文件）
4. 后台每日刷新

使用方式：
    from src.ai.model_registry import is_vision_model, fetch_provider_models
"""

import json  # JSON 解析
import logging  # 日志记录
import re  # 正则表达式，模型名称匹配
import ssl  # SSL 上下文
import urllib.request  # HTTP 请求

_log = logging.getLogger(__name__)


# =============================================================================
# ★ v5：模型名称 → 多模态能力推断（主力：名称模式匹配，少数兜底查映射表）
# =============================================================================

# ── 正向模式：模型名含这些关键词 → 大概率支持视觉 ──
_VISION_POSITIVE_PATTERNS = [
    r"vl",  # Vision-Language: deepseek-vl2, qwen-vl-max, glm-4v
    r"vision",  # 直白声明: gpt-4-vision-preview
    r"4o",  # omni: gpt-4o, gpt-4o-mini
    r"omni",  # 多模态: gemini-omni
    r"multimodal",  # 直白声明
    r"gemini",  # Gemini 全系多模态
    r"janus",  # DeepSeek Janus 系列多模态: janus-pro-7b, janus
]

# ── 负向模式：明确不支持的模型类型 ──
_VISION_NEGATIVE_PATTERNS = [
    r"text",  # text-embedding, text-moderation
    r"embedding",  # 嵌入模型
    r"audio",  # 语音模型
    r"tts",  # 文字转语音
    r"whisper",  # 语音识别
    r"moderation",  # 内容审核
    r"dall-e",  # 图片生成（不是理解）
    r"dalle",  # dalle 变体
]

# ── 兜底映射表：名称匹配搞不定的模型 ──
_VISION_MODEL_OVERRIDES = {
    # DeepSeek
    "deepseek-chat": False,  # V3，不支持视觉
    "deepseek-reasoner": False,  # R1，不支持视觉
    "deepseek-v3": False,
    "deepseek-r1": False,
    "deepseek-v4-pro": False,  # V4 不支持视觉（仅文本+工具调用）
    "deepseek-v4-flash": False,  # V4 不支持视觉（仅文本+工具调用）
    "deepseek-vl2": True,  # DeepSeek-VL2 多模态
    "janus-pro-7b": True,  # DeepSeek Janus-Pro 多模态
    # OpenAI
    "gpt-4-turbo": True,  # GPT-4-Turbo 支持视觉
    # Claude 3.0（haiku 3.0 不支持，但正向已覆盖 sonnet/opus）
    "claude-3-haiku": False,
}


def is_vision_model(model_name: str) -> bool:
    """
    从模型名称推断是否支持视觉/多模态。

    判定顺序：
    1. 显式映射表查（_VISION_MODEL_OVERRIDES）—— 精确 key 优先，再模糊子串
    2. 负向模式命中 → False
    3. 正向模式命中 → True
    4. Claude 特殊规则（sonnet/opus → True; haiku ≥ 3.5 → True）
    5. 默认 → False（保守：不认识的模型不算多模态）
    """
    name = model_name.lower()

    # 1. 显式映射表
    if model_name in _VISION_MODEL_OVERRIDES:
        return _VISION_MODEL_OVERRIDES[model_name]
    for key, val in _VISION_MODEL_OVERRIDES.items():
        if key in name:
            return val

    # 2. 负向模式
    for pat in _VISION_NEGATIVE_PATTERNS:
        if re.search(pat, name):
            return False

    # 3. 正向模式
    if any(re.search(pat, name) for pat in _VISION_POSITIVE_PATTERNS):
        return True

    # 4. Claude 特殊规则
    if name.startswith("claude"):
        if "sonnet" in name or "opus" in name:
            return True
        if "haiku" in name:
            m = re.search(r"haiku[-.](\d+)[-.](\d+)", name)
            if m:
                major, minor = int(m.group(1)), int(m.group(2))
                return (major, minor) >= (3, 5)

    # 5. 默认保守
    return False


# ── 嵌入模型识别模式 ──
_EMBEDDING_PATTERNS = [
    r"embed",  # text-embedding-*, nomic-embed-text, mxbai-embed-*, jina-embed-*
    r"bge-",  # BGE series: bge-m3, bge-large-zh-v1.5
    r"-gte",  # GTE series: gte-large, gte-base (Alibaba)
    r"-e5",  # E5 series: e5-large-v2, multilingual-e5
]


def is_embedding_model(model_name: str) -> bool:
    """
    从模型名称推断是否为嵌入/向量模型。

    判定规则：名称匹配嵌入模型关键词模式 → True，否则 False。
    """
    name = model_name.lower()
    return any(re.search(pat, name) for pat in _EMBEDDING_PATTERNS)


def fetch_provider_models(provider: str, api_key: str, base_url: str = "") -> list:
    """
    从提供商 /v1/models 接口实时拉取模型列表。

    返回: [{"id": "...", "object": "model", "owned_by": "..."}, ...]
    失败返回空列表。

    ★ 无缓存：每次调用都走真实 API，确保列表始终最新。
    """
    default_urls = {
        "deepseek": "https://api.deepseek.com/v1/models",
        "openai": "https://api.openai.com/v1/models",
        "anthropic": "https://api.anthropic.com/v1/models",
    }
    if base_url:
        api_base = base_url.rstrip("/")
        url = (
            f"{api_base}/models"
            if api_base.endswith("/v1")
            else f"{api_base}/v1/models"
        )
    else:
        url = default_urls.get(provider, default_urls["deepseek"])

    headers = {}
    if provider == "anthropic":
        headers["x-api-key"] = api_key
        headers["anthropic-version"] = "2023-06-01"
    else:
        headers["Authorization"] = f"Bearer {api_key}"

    try:
        req = urllib.request.Request(url, headers=headers, method="GET")
        ctx = ssl.create_default_context()
        resp = urllib.request.urlopen(req, timeout=10, context=ctx)
        data = json.loads(resp.read().decode("utf-8"))
        return data.get("data", [])
    except Exception as _e:
        _log.debug("模型列表拉取失败: %s", _e, exc_info=True)
        return []
