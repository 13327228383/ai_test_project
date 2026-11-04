# -*- coding: utf-8 -*-
from __future__ import annotations

"""Provider 抽象基类（CAPABILITIES + chat 接口） — 从 ai_providers.py 拆出，零业务逻辑变更。"""

from typing import Any


class BaseProvider:
    """AI Provider 抽象基类。"""

    # ★ Provider 能力声明 — 解决"模型名判断 vs Provider 实际能力"两张皮问题
    CAPABILITIES = {
        "tools": False,  # 是否支持 Function/Tool Calling
        "vision": False,  # 是否支持视觉输入（图像理解）
        "streaming": False,  # 是否支持流式输出
        "json_mode": False,  # 是否支持原生 JSON 模式
    }

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        timeout: int = 60,
        max_retries: int = 1,
    ):
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout = timeout
        self._max_retries = max_retries

    @property
    def model(self) -> str:
        return self._model

    @property
    def source(self) -> str:
        """数据来源标签（deepseek / openai / anthropic / local）。"""
        raise NotImplementedError

    def supports(self, capability: str) -> bool:
        """查询当前 Provider 是否支持指定能力。"""
        return self.CAPABILITIES.get(capability, False)

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
    ) -> dict[str, Any]:
        """
        发送聊天请求，带指数退避重试。
        model 参数可选：传入时临时覆盖实例的 self._model（线程安全）。
        tools / tool_choice: 工具调用（仅 OpenAI 兼容 Provider 实现）。
        response_format: Structured Output 约束（仅 OpenAI 兼容 Provider 实现，其他 Provider 忽略）。
        seed: 确定性采样种子（仅 OpenAI 兼容 Provider 实现）。相同 seed + 相同参数 → 尽量相同输出。
        返回统一格式：{success, data, usage, model, source, _elapsed, _cost, tool_calls}
        """
        raise NotImplementedError

    @staticmethod
    def _extract_text_from_codeblock(raw: str) -> str:
        """去除可能的 markdown 代码块包裹，保留原始文本以供上层 JSON 解析。"""
        return raw
