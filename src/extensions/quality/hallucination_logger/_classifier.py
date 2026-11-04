# -*- coding: utf-8 -*-
"""
失败原因分类 Mixin。

提供 ``classify_failure()`` 方法及分类信号常量。
"""

import json
import logging

_log = logging.getLogger(__name__)


class HallucinationClassifierMixin:
    """失败原因分类 Mixin —— 区分环境问题 / 幻觉 / 未知。"""

    ENV_SIGNALS = [
        "net::ERR_",
        "timeout",
        "502",
        "503",
        "504",
        "连接被拒绝",
        "Connection refused",
        "ERR_CONNECTION",
        "Tunnel",
        "proxy",
        "DNS",
        "ECONNREFUSED",
    ]

    HALLUCINATION_SIGNALS = [
        "ai-99",
        "元素未找到",
        "语义重定位失败",
        "API路径不存在",
        "测试用例不明确",
        "预期结果与页面内容不匹配",
        "_guard_reject",
        "ai_id_out_of_range",
        "whitelist",
        "元素不存在于当前页面",
    ]

    def classify_failure(
        self, intent: dict, step_results: list, failure_detail: str = ""
    ) -> str:
        """
        自动分类失败原因。
        返回: "env" | "hallucination" | "unknown"
        """
        combined = json.dumps(step_results, ensure_ascii=False) + " " + failure_detail
        combined_lower = combined.lower()

        env_score = sum(1 for sig in self.ENV_SIGNALS if sig.lower() in combined_lower)
        hal_score = sum(
            1 for sig in self.HALLUCINATION_SIGNALS if sig.lower() in combined_lower
        )

        if hal_score > env_score:
            return "hallucination"
        elif env_score > hal_score:
            return "env"
        return "unknown"
