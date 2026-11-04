# -*- coding: utf-8 -*-
"""
Token 估算器 (TokenEstimator)
===============================
轻量级 token 计数（不依赖 tiktoken，零外部依赖）。
用于动态调整 max_tokens，避免输出截断或浪费。

估算策略：
- 中文字符 ≈ 1.2~1.8 token/char → 取 1.5
- 英文/数字 ≈ 0.25~0.35 token/char → 取 0.3
- 混合文本取保守值 2.5 chars/token（即 0.4 token/char）
- JSON 结构化文本比自然语言略费 token → 额外 +10%

模型上下文窗口（保守值，留余量）：
- deepseek-v4-pro:   120K（官方 128K，留 8K 安全边际）
- deepseek-v4-flash: 120K
- 其他 deepseek:      60K
- 默认:               32K
"""

import re

# ── 模型上下文窗口 ──
_MODEL_CONTEXT_LIMITS = {
    "deepseek-v4-pro": 120000,
    "deepseek-v4-flash": 120000,
    "deepseek-chat": 60000,
    "deepseek-reasoner": 60000,
    "gpt-4o": 120000,
    "gpt-4o-mini": 120000,
    "claude-sonnet-4-6": 180000,
    "claude-haiku-4-5": 180000,
    "claude-opus-4-7": 180000,
}
_DEFAULT_LIMIT = 32000

# 安全边际（防止边界溢出）
_SAFETY_MARGIN = 2000

# 理性天花板（模型窗口很大时防止极端情况，实际够用不会触发）
# max_tokens 设大了模型用完就停，不会浪费 — 天花板只是安全兜底
# ★ 性能修复(07-04): default 从 8192 降到 4096（当时基准模型输出 1500-3000 tokens）
# ★ 根因修复(2026-09-27): default 恢复到 8192。
#   根因: 本表按**模型名精确匹配**，未登记模型（实测 agnes-2.5-flash）落入 default
#     4096；该类模型 completion 含推理 token，4096 被推理占满 → finish_reason=length
#     硬截断 → JSON 不完整 → 严格校验失败 → 调用方重试仍按同一 ceiling 计算 → 再次
#     截断 → 0 产出。实测 SelfRepair 单条重建白烧 71s（45s+26s）且成功 0 条。
#   通用性: 天花板存在的意义只是"防极端"，不是"按预期输出长度卡"; 截断的代价（重试
#     二次调用 + 整条用例失败）远高于一次稍长的生成，与该函数"宁大勿小"原则一致。
#     4096 作为默认值对含推理 token 的模型已被实测证伪。
_SANITY_CEILING = {
    "deepseek-v4-pro": 16384,
    "deepseek-v4-flash": 8192,
    "default": 8192,
}


def estimate_tokens(text: str) -> int:
    """
    估算文本的 token 数。

    策略：分别统计中文和英文/数字的字符数，加权计算。
    """
    if not text:
        return 0

    # 中文字符（含中文标点）
    chinese_chars = len(re.findall(r"[一-鿿　-〿＀-￯]", text))
    # 其他字符（英文、数字、符号、空格、换行等）
    other_chars = len(text) - chinese_chars

    # 中文: ~1.5 char/token (DeepSeek/ChatGLM 实测 ~0.75-1.8 token/char，保守取1.50)
    # 英文+符号: ~4 char/token → 0.25 token/char
    # 加权
    tokens = int(chinese_chars * 1.50 + other_chars * 0.25)

    # JSON 结构化文本比纯文本略费 token（引号、逗号、花括号）→ +10%
    if text.strip().startswith(("{", "[")):
        tokens = int(tokens * 1.1)

    return max(tokens, 1)


def estimate_messages_tokens(system_prompt: str, user_message: str) -> int:
    """
    估算 system_prompt + user_message 的总 token 数。
    加上消息格式开销（每条消息 ~4 tokens 的结构标记）。
    """
    sys_tokens = estimate_tokens(system_prompt) if system_prompt else 0
    usr_tokens = estimate_tokens(user_message) if user_message else 0
    # 消息结构开销: system + user 两条消息 × 4 tokens
    overhead = 8
    return sys_tokens + usr_tokens + overhead


def compute_max_tokens(
    system_prompt: str,
    user_message: str,
    model: str = "deepseek-v4-pro",
) -> tuple[int, dict]:
    """
    根据输入大小动态计算 max_tokens。
    原则：max_tokens 高了不浪费（模型用完即停），低了会截断 → 宁大勿小。

    参数:
        system_prompt: 系统提示词
        user_message:  用户消息
        model:         模型名称

    返回:
        (max_tokens, info_dict)
    """
    input_tokens = estimate_messages_tokens(system_prompt, user_message)
    context_limit = _MODEL_CONTEXT_LIMITS.get(model, _DEFAULT_LIMIT)
    sanity_ceiling = _SANITY_CEILING.get(model, _SANITY_CEILING["default"])

    # 可用输出空间 = 窗口 - 输入 - 安全边际
    available = context_limit - input_tokens - _SAFETY_MARGIN

    if available < 512:
        # 输入接近窗口上限，强制保底（几乎不可能，120K 窗口通常用不到 5%）
        max_tokens = 512
        warning = True
    else:
        # 取可用空间和理性天花板中较小值
        # 正常情况 available >> ceiling，所以 ceiling 不限制输出
        # 只有当输入异常大（接近窗口）时，available 才起作用
        max_tokens = min(available, sanity_ceiling)
        warning = False

    info = {
        "input_tokens": input_tokens,
        "context_limit": context_limit,
        "max_tokens": max_tokens,
        "input_near_limit": warning,
    }

    return max_tokens, info
