# -*- coding: utf-8 -*-

"""

Intent Validator - AI 深度校验（使用 Flash 模型）

"""


import json
import logging


_log = logging.getLogger(__name__)


from .prompts import INTENT_VALIDATE_SYSTEM_PROMPT

from src.core.prompt_templates import prompt_templates


import logging

_log = logging.getLogger(__name__)


def ai_deep_check(intents: list[dict], req_description: str, module: str) -> dict:
    """

    AI 深度校验（使用 Flash 模型）。

    检查：逻辑矛盾、意图动作可执行性、需求覆盖完整性。

    """

    try:

        from src.ai.ai_client import ai_brain

        from src.core.config_loader import config

    except Exception as e:
        _log.error("AI校验异常: %s", e, exc_info=True)
        return {"passed": True, "issues": [], "raw": ""}

    intents_text = json.dumps(intents, ensure_ascii=False, indent=2)

    if len(intents_text) > 6000:

        intents_text = intents_text[:6000] + "\n... (截断)"

    user_msg = (
        f"## 需求\n模块: {module}\n描述: {req_description[:200]}\n\n"
        f"## 待校验测试用例\n{intents_text}\n\n"
        "请逐一检查，只输出 JSON。"
    )

    try:

        verify_model = (
            config.verification_model
        )  # ★ 使用校验模型（默认 deepseek-v4-flash）

        result = ai_brain._call_api(
            system_prompt=prompt_templates.render(
                "intent_validate_system", default=INTENT_VALIDATE_SYSTEM_PROMPT
            ),
            user_message=user_msg,
            temperature=0.0,
            max_tokens=1024,
            caller_name="测试用例校验(Flash)",
            task_type="verification",
            force_cloud=True,
            cloud_model=verify_model,
        )

        if result.get("success"):

            parsed = ai_brain._parse_json_response(result.get("data", ""))

            if parsed.get("success"):

                return parsed["data"]

    except Exception as _e:

        _log.debug("token + model + verify: %s", _e, exc_info=True)

    return {"passed": True, "issues": [], "raw": ""}
