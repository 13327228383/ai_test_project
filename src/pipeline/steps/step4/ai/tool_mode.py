# -*- coding: utf-8 -*-
"""
BrowserAi 工具调用模式 mixin

职责：
- _try_tool_mode — 原生 Function Calling 模式
"""

import json
from src.core.guarded import guard
import logging
import time
from datetime import datetime, timezone, timedelta
from typing import Any

from src.core.config_loader import config
from src.extensions.metrics_collector import record_metric as _record_metric
from src.pipeline.steps.step4.browser_tools import (
    build_tool_schemas,
    validate_tool_call,
    execute_tool_calls,
)

_log = logging.getLogger(__name__)


class _BrowserAiToolModeMixin:
    """工具调用模式能力。"""

    async def _try_tool_mode(
        self,
        goal: str,
        state: list[dict],
        page_url: str = "",
        page_title: str = "",
        api_context: str = "",
        previous_failure: str = "",
        action_history: list = None,
        case_id: str = "",
        step_desc: str = "",
        round_num: int = 0,
    ):
        from src.ai.ai_client import ai_brain

        tool_mode_enabled = getattr(config, "tool_calling_enabled", None)
        if tool_mode_enabled is None:
            tool_mode_enabled = self._model_supports_tools()
        if not tool_mode_enabled:
            _log.debug("    [ToolMode] 当前模型不支持工具调用，跳过")
            return None

        # 动态 DOM 上限
        goal_for_size = goal or ""
        try:
            from src.pipeline.steps.step4.plugins.dynamic_dom_sizer import (
                dynamic_dom_sizer,
            )

            _max_el = dynamic_dom_sizer.get_dom_size_limit(goal_for_size)
        except Exception as _e:
            _log.debug("[tool_mode] _try_tool_mode 异常: %s", _e, exc_info=True)
            _max_el = self.MAX_DOM_ELEMENTS
        compact_state = []
        for el in state[:_max_el]:
            item = {"tag": el.get("tag", ""), "id": el.get("id", "")}
            for k in (
                "text",
                "type",
                "name",
                "role",
                "aria-label",
                "href",
                "value",
                "placeholder",
            ):
                if k in el:
                    item[k] = el[k]
            compact_state.append(item)
        state_json = json.dumps(compact_state, ensure_ascii=False, indent=2)

        # 区域树
        _tool_region_hint = ""
        try:
            if len(compact_state) >= 40:
                from src.pipeline.steps.step4.plugins.semantic_dom_tree import (
                    semantic_dom_tree as _sdt2,
                )

                _tree2 = _sdt2.build_region_tree(
                    compact_state, goal_text=goal or step_desc or ""
                )
                if _tree2 and _tree2.get("total_regions", 0) >= 2:
                    _tool_region_hint = _sdt2.regions_to_prompt_text(
                        _tree2, include_elements=False
                    )
                    _hint2 = _sdt2.generate_ai_hint(goal or step_desc or "", _tree2)
                    if _hint2:
                        _tool_region_hint += "\n" + _hint2
        except Exception as _e:
            _log.debug("[tool_mode] 非关键异常: %s", _e, exc_info=True)

        # 系统提示词
        now_str = datetime.now(timezone(timedelta(hours=8))).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        sys_meta = "当前环境时间: %s (Asia/Shanghai)" % now_str
        if page_url:
            sys_meta += "\n当前页面 URL: %s" % page_url
        if page_title:
            sys_meta += "\n当前页面标题: %s" % page_title

        from src.pipeline.utils.action_library_prompt import (
            build_action_library_prompt as _alib_prompt,
        )

        action_lib_prompt = _alib_prompt()

        system_prompt = (
            "你是浏览器自动化操作助手。通过调用工具来操作页面。\n\n"
            "%s\n\n" + action_lib_prompt + "## 操作规则\n"
            "1. 根据操作目标和页面元素列表，选择合适的工具调用\n"
            "2. 每次可以调用多个工具（按操作顺序排列）\n"
            "3. target_hint 使用 text= / placeholder= / label= / CSS选择器 格式\n"
            "4. 操作完成后调用 finish 工具报告结果\n"
            "5. 如果页面还在加载或找不到目标元素，返回空操作或调用 finish(success=false)\n"
            "6. 日期时间选择器必须使用当前环境时间: %s\n"
            "7. ★★★ 自评要求（极其重要）：调用 finish 时必须填写 evaluation 字段，"
            "   分析上一步操作是否按预期完成：a) 页面发生了什么变化 b) 与操作目标的差距 c) 为什么认为成功或失败\n"
        ) % (sys_meta, now_str)

        # 用户消息
        failure_section = ""
        if previous_failure:
            failure_section = (
                "\n## 上一轮操作失败原因\n%s\n请调整策略。\n" % previous_failure
            )
        history_section = ""
        if action_history:
            history_lines = ["\n## 已执行的操作历史"]
            for h in action_history:
                history_lines.append("  %s" % h)
            history_section = "\n".join(history_lines) + "\n"
        _tool_region_section = (
            ("\n## 页面区域结构（语义摘要）\n" + _tool_region_hint + "\n")
            if _tool_region_hint
            else ""
        )
        user_message = (
            "%s%s%s\n页面可交互元素列表（共 %d 个）:\n```json\n%s\n```\n\n"
            "操作目标: %s\n\n请调用工具执行操作。"
        ) % (
            failure_section,
            history_section,
            _tool_region_section,
            len(compact_state),
            state_json,
            goal,
        )

        tool_schemas = build_tool_schemas()

        _ai_t0 = time.time()
        try:
            result = ai_brain._call_api(
                system_prompt=system_prompt,
                user_message=user_message,
                temperature=0.0,
                max_tokens=4096,
                caller_name="工具调用模式",
                task_type="operation_instruction",
                tools=tool_schemas,
                tool_choice="auto",
            )
        except Exception as e:
            _log.debug("    [ToolMode] 调用异常，回退文本模式: %s", e)
            return None

        _elapsed = time.time() - _ai_t0

        if not result.get("success"):
            _log.debug(
                "    [ToolMode] AI 调用失败，回退文本模式: %s",
                result.get("error", "")[:100],
            )
            return None

        raw_tool_calls = result.get("tool_calls")
        if not raw_tool_calls:
            _log.debug("    [ToolMode] AI 未返回 tool_calls，回退文本模式")
            return None

        validated_calls = []
        for tc in raw_tool_calls:
            ok, err, tool_name, args = validate_tool_call(tc)
            if not ok:
                _log.warning("    [ToolMode] 工具校验失败: %s (%s)", tool_name, err)
                continue
            validated_calls.append({"name": tool_name, "arguments": args})

        if not validated_calls:
            _log.debug("    [ToolMode] 所有 tool_call 校验失败，回退文本模式")
            return None

        finish_indices = [
            i for i, c in enumerate(validated_calls) if c.get("name") == "finish"
        ]
        if finish_indices:
            if len(finish_indices) > 1:
                _log.warning("    [ToolMode] 多个 finish 调用，只保留最后一个")
                validated_calls = [
                    c
                    for i, c in enumerate(validated_calls)
                    if c.get("name") != "finish" or i == finish_indices[-1]
                ]
            elif finish_indices[0] != len(validated_calls) - 1:
                _log.warning("    [ToolMode] finish 不是最后一个调用，已调整到最后")
                finish_call = validated_calls.pop(finish_indices[0])
                validated_calls.append(finish_call)

        exec_results = await execute_tool_calls(validated_calls, self._engine)
        actions = self._tool_results_to_actions(validated_calls, exec_results)

        with guard("_try_tool_mode: usage = result.get('usage', {}) or {", log=_log):
            usage = result.get("usage", {}) or {}
            _record_metric(
                "record_browser_ai_round",
                {
                    "case_id": case_id,
                    "step_desc": step_desc or goal,
                    "round_num": int(round_num or 0),
                    "elapsed_sec": _elapsed,
                    "prompt_tokens": int(usage.get("prompt_tokens", 0) or 0),
                    "completion_tokens": int(usage.get("completion_tokens", 0) or 0),
                    "model": str(result.get("model", "") or ""),
                },
            )

        _log.info(
            "    [ToolMode] 完成: %d 个工具调用, %d 个转换为 actions",
            len(validated_calls),
            len(actions),
        )
        return (actions, None)
