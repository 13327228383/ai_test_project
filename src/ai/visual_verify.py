# -*- coding: utf-8 -*-
"""
视觉验证模块
============
封装 AiBrain 的视觉验证能力：截图 + 多模态模型判断。

设计原则：依赖 Provider 的 chat_vision 能力，失败时返回 success=False 不抛异常。
"""

import base64
import time
from typing import Any

from src.ai.json_parser import parse_json_response


class VisualVerifier:
    """视觉验证执行器。"""

    def __init__(
        self,
        provider,
        provider_name: str,
        model: str,
        refresh_config_callback,
        notify_start_callback,
        notify_finish_callback,
        stats_record_callback,
    ):
        """
        参数：
            provider: 具有 chat_vision 方法的 Provider 实例
            provider_name: 当前 provider 名称（用于错误提示）
            model: 当前模型名称
            refresh_config_callback: 无参函数，调用前刷新配置
            notify_start_callback: callable(name)
            notify_finish_callback: callable(name, elapsed, usage, cost, model)
            stats_record_callback: callable(usage, cost)
        """
        self._provider = provider
        self._provider_name = provider_name
        self._model = model
        self._refresh_config = refresh_config_callback
        self._notify_start = notify_start_callback
        self._notify_finish = notify_finish_callback
        self._stats_record = stats_record_callback

    def verify(
        self, screenshot_bytes: bytes, expectation: str, caller_name: str = "视觉验证"
    ) -> dict[str, Any]:
        """
        ★ v5: 视觉验证（Midscene 风格的截图驱动断言）。

        将页面截图发送给支持视觉的模型，判断是否符合预期描述。
        仅在 OpenAI 兼容 Provider 上可用（DeepSeek V/GLM-4V/GPT-4o 等）。
        调用失败时返回 success=False，**不会**抛出异常——调用方应自行降级。

        参数：
            screenshot_bytes: PNG 截图的 bytes（来自 playwright_engine.get_screenshot）
            expectation:      自然语言描述的预期，如"页面显示登录成功"、"按钮为红色"
            caller_name:      调用者名称，用于日志/统计

        返回：
            {"success": bool, "passed": bool, "reason": str, "raw": str, ...}
            - success: API 调用是否成功
            - passed:  视觉判断是否通过（仅当 success=True 时有意义）
        """
        if not screenshot_bytes:
            return {"success": False, "passed": False, "reason": "截图为空", "raw": ""}

        # 仅 OpenAI 兼容 Provider 实现了 chat_vision
        if not hasattr(self._provider, "chat_vision"):
            return {
                "success": False,
                "passed": False,
                "reason": f"当前 Provider ({self._provider_name}) 不支持视觉模型",
                "raw": "",
            }

        # 刷新配置（同 _call_api）
        self._refresh_config()

        # base64 编码（不写磁盘，纯内存）
        try:
            image_b64 = base64.b64encode(screenshot_bytes).decode("ascii")
        except Exception as e:
            return {
                "success": False,
                "passed": False,
                "reason": f"截图编码失败: {e}",
                "raw": "",
            }

        # 结构化 Prompt：让模型返回 JSON 便于解析
        vision_prompt = (
            "你是 UI 测试视觉断言助手。给定一张网页截图和一段自然语言描述，"
            "请判断页面是否符合描述。\n"
            "\n"
            "判断要求：\n"
            "1. 只根据截图内容判断，不要猜测截图外的内容\n"
            "2. 文本、颜色、布局、是否可见都可以作为判断依据\n"
            "3. 当内容部分匹配但有明显不符时，返回 false 并说明原因\n"
            "\n"
            "严格按以下 JSON 格式返回（不要加任何解释，不要包在代码块中）：\n"
            '{"passed": true/false, "reason": "一句话说明判断依据，中文"}\n'
            "\n"
            f"预期描述：{expectation}\n"
        )

        t0 = time.time()
        try:
            self._notify_start(caller_name)

            result = self._provider.chat_vision(
                image_base64=image_b64,
                prompt=vision_prompt,
                temperature=0.0,
                max_tokens=256,
            )
        except Exception as e:
            elapsed = time.time() - t0
            self._notify_finish(caller_name, elapsed, {}, 0.0)
            return {
                "success": False,
                "passed": False,
                "reason": f"视觉模型调用异常: {e}",
                "raw": "",
            }

        elapsed = result.get("_elapsed", time.time() - t0)
        usage = result.get("usage", {})
        cost = result.get("_cost", 0.0)
        raw_data = result.get("data", "")

        # 累计统计（同 _call_api）
        self._stats_record(usage, cost)

        actual_model = result.get("model", self._model)
        self._notify_finish(caller_name, elapsed, usage, cost, actual_model)

        if not result.get("success"):
            return {
                "success": False,
                "passed": False,
                "reason": result.get("error", "视觉模型返回失败")[:200],
                "raw": raw_data,
            }

        # 解析 JSON 判断结果
        passed = False
        reason = raw_data[:100]
        parsed = parse_json_response(raw_data)
        if parsed.get("success"):
            data = parsed["data"]
            if isinstance(data, dict):
                passed = bool(data.get("passed", False))
                reason = str(data.get("reason", "")) or raw_data[:100]
            else:
                # 退化为关键词匹配
                text_low = str(data).lower()
                passed = "true" in text_low or "通过" in text_low or "符合" in text_low
        else:
            # JSON 解析失败，做一个宽松的关键词匹配作为兜底
            text_low = raw_data.lower()
            passed = (
                "true" in text_low or "通过" in text_low or "符合" in text_low
            ) and (
                "false" not in text_low
                and "不通过" not in text_low
                and "不符" not in text_low
            )

        return {
            "success": True,
            "passed": passed,
            "reason": reason or "视觉判断完成",
            "raw": raw_data,
            "usage": usage,
            "_elapsed": elapsed,
            "_cost": cost,
            "_model": actual_model,
        }
