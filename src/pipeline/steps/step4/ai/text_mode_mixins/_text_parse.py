# -*- coding: utf-8 -*-
from __future__ import annotations
from src.core.guarded import guard

"""
文本解析 mixin — _try_text_mode

职责：纯文本 JSON 操作序列模式
"""

import json
import logging
import time
from datetime import datetime, timezone, timedelta

from src.ai.ai_client import ai_brain
from src.core.prompt_templates import prompt_templates
from src.pipeline.steps.step4.op_cache import op_cache

from src.pipeline.steps.step4.ai.text_mode_mixins._helpers import (
    _record_metric,
    _fill_placeholders,
)

_log = logging.getLogger(__name__)


def _build_action_library_prompt() -> str:
    """从 MySQL action_library_store 加载可用 action 类型清单，注入 text mode prompt。"""
    from src.pipeline.utils.action_library_prompt import (
        build_action_library_prompt as _prompt,
    )

    return _prompt()


class _BrowserAiTextParseMixin:
    """文本模式解析能力。"""

    def _try_text_mode(
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
    ) -> list[dict] | None:
        # 动态 DOM 上限
        try:
            from src.pipeline.steps.step4.plugins.dynamic_dom_sizer import (
                dynamic_dom_sizer,
            )

            _max_el = dynamic_dom_sizer.get_dom_size_limit(goal or "")
        except Exception as _e:
            _log.debug("[_text_parse] _try_text_mode 异常: %s", _e, exc_info=True)
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

        # 语义区域树
        _region_hint = ""
        with guard("_try_text_mode: if len(compact_state) >= 40: from sr", log=_log):
            if len(compact_state) >= 40:
                from src.pipeline.steps.step4.plugins.semantic_dom_tree import (
                    semantic_dom_tree as _sdt,
                )

                _tree = _sdt.build_region_tree(
                    compact_state, goal_text=goal or step_desc or ""
                )
                if _tree and _tree.get("total_regions", 0) >= 2:
                    _region_hint = _sdt.regions_to_prompt_text(
                        _tree, include_elements=False
                    )
                    _ai_hint = _sdt.generate_ai_hint(goal or step_desc or "", _tree)
                    if _ai_hint:
                        _region_hint += "\n" + _ai_hint

        # 语义操作缓存
        is_first_round = not previous_failure and not action_history
        if is_first_round:
            cached = op_cache.get(goal, page_url, compact_state)
            if cached:
                cached = _fill_placeholders(cached)
                _log.info("    [Cache] ✓ 命中 (跳过AI调用)")
                _record_metric("record_op_cache_hit")
                with guard("_try_text_mode: cache_key = op_cache._make_key(goal,", log=_log):
                    cache_key = op_cache._make_key(goal, page_url, compact_state)
                    op_cache.record_success(cache_key, success=True)
                return (cached, None)
            else:
                _record_metric("record_op_cache_miss")

        now_str = datetime.now(timezone(timedelta(hours=8))).strftime(
            "%Y-%m-%d %H:%M:%S"
        )

        page_info_lines = [
            "## 系统元数据",
            "当前环境时间: %s (Asia/Shanghai)" % now_str,
        ]
        if page_url:
            page_info_lines.append("当前页面 URL: %s" % page_url)
        if page_title:
            page_info_lines.append("当前页面标题: %s" % page_title)
        page_info_lines.append(
            "★ 如果页面有日期/时间选择器，必须使用此时间。\n"
            "★ 当前页面 URL 和标题是你判断所在页面的唯一依据，操作前务必确认页面匹配步骤目标！"
        )
        sys_meta = "\n".join(page_info_lines)

        action_lib_prompt = _build_action_library_prompt()
        # ★ 2026-07-14: system_prompt 骨架存 DB（browser_action_system），运行时注入上下文
        #   骨架含 {{action_library}} 和 {{sys_meta}} 占位符，由 render() 替换
        system_prompt = prompt_templates.render(
            "browser_action_system",
            action_library=action_lib_prompt,
            sys_meta=sys_meta,
        )

        api_section = ""
        if api_context:
            api_section = (
                "\n## 最近 API 调用记录（★ 可用于校验操作结果）\n"
                "以下是你最近操作触发的实际 HTTP 请求，可据此判断操作是否真的产生了效果：\n"
                "%s\n"
                "- 涉及登录/表单提交/数据修改时，检查对应 API 是否被正确调用\n"
                "- 预期 API 未出现说明操作可能未生效\n\n"
            ) % api_context

        history_context = ""
        if action_history:
            history_lines = [
                "\n## ⚠️ 本轮之前已执行的操作及页面响应",
                "★ 根据「→」后面的页面变化判断操作是否生效：",
                "★ · 「URL变为XXX」→ 操作触发了页面跳转，说明点对了",
                "★ · 「页面未变化」→ 可能点错或页面响应慢，建议先等待再判断",
                "★ · 不要盲目换操作——如果页面没变化但你觉得点对了，再点一次试试",
            ]
            for h in action_history:
                history_lines.append("  %s" % h)
            history_context = "\n".join(history_lines) + "\n\n"

        failure_context = ""
        if previous_failure:
            failure_context = (
                "\n## ⚠️ 上一轮操作失败原因\n"
                "%s\n\n"
                "★ 请根据以上失败原因调整策略，避免重复同样的错误。\n"
                "★ 找不到目标元素时尝试：展开侧边栏折叠菜单 / 点击头像下拉菜单 / 滚动页面 / 查导航地图\n"
                "★ 页面加载中失败时返回空数组 [] 等待页面就绪。\n\n"
            ) % previous_failure

        _region_section = (
            ("\n## 页面区域结构（语义摘要）\n" + _region_hint + "\n")
            if _region_hint
            else ""
        )

        user_message = (
            "%s%s%s%s%s\n页面可交互元素列表（共 %d 个）:\n```json\n%s\n```\n\n"
            "操作目标:\n%s\n\n请返回要执行的操作序列。"
        ) % (
            sys_meta,
            api_section,
            history_context,
            failure_context,
            _region_section,
            len(compact_state),
            state_json,
            goal,
        )

        _ai_t0 = time.time()
        result = ai_brain._call_api(
            system_prompt=system_prompt,
            user_message=user_message,
            temperature=0.0,
            max_tokens=2048,
            caller_name="浏览器操作",
            task_type="operation_instruction",
        )
        _elapsed = time.time() - _ai_t0
        with guard("_try_text_mode: usage = result.get('usage', {}) or {", log=_log):
            usage = result.get("usage", {}) or {}
            _record_metric(
                "record_browser_ai_round",
                {
                    "case_id": case_id,
                    "step_desc": step_desc or goal,
                    "round_num": int(round_num or 0),
                    "elapsed_sec": _elapsed,
                    "prompt_tokens": int(
                        usage.get("prompt_tokens", 0)
                        or usage.get("input_tokens", 0)
                        or 0
                    ),
                    "completion_tokens": int(
                        usage.get("completion_tokens", 0)
                        or usage.get("output_tokens", 0)
                        or 0
                    ),
                    "model": str(result.get("model", "") or ""),
                },
            )
        if not result.get("success"):
            _log.error("    [AI] 调用失败: %s", result.get("error", "未知"))
            return (None, None)

        parsed = self._parse_ai_response(result.get("data", "").strip())

        # P0 防幻觉校验
        if parsed and len(parsed) > 0:
            validation_error = self._validate_ai_response(parsed, compact_state)
            if validation_error:
                _log.error("    [Guard] ⚡ AI输出校验失败: %s", validation_error[:150])
                return ([{"action": "_guard_reject", "reason": validation_error}], None)

        # 首轮成功操作 → 写入语义缓存
        pending_key = None
        if is_first_round and parsed and len(parsed) > 0:
            if parsed[0].get("action") != "_guard_reject":
                try:
                    pending_key = op_cache._make_key(goal, page_url, compact_state)
                    op_cache.set(goal, page_url, compact_state, None, parsed)
                except Exception as _e:
                    _log.debug("    [Cache] 写入语义缓存异常: %s", _e)
                    pending_key = None

        return (parsed, pending_key)
