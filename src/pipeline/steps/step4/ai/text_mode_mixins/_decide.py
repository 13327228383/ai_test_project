# -*- coding: utf-8 -*-
from __future__ import annotations
from src.core.guarded import guard

"""
BrowserAi 单动作决策 mixin — decide_action
============================================

★ 修复 Step4 主链路断链：_ai_loop._run_ai_round_loop 调用 ai.decide_action(prompt, screenshot=...)，
  但该方法从未定义（git 证全空），致 AI 多轮循环（兜底路径）每轮必抛 AttributeError、
  被 _ai_loop.py:139 except 捕获记 "AI 调用失败" 后 break → 意图失败。

契约（对齐 _ai_loop.py:144-157 的消费逻辑）：
  - 入参：prompt(str，_ai_loop 已组装含 DOM 片段/期望/截图提示)；screenshot(bytes，可选)
  - 返回：dict 或 str，含字段 action_type / action_params / reasoning
  - _ai_loop.py:146-149 已兼容 str/dict 两种返回

实现：复用全局 ai_brain._call_api（与截图入库、data_factory AI 定位同一 AI 通道），
  system prompt 指定严格 JSON 输出，action_type 取值由 `decide_action_types()` 从
  执行端可交互动作集合单点派生（见下方"动作词表"说明）。
"""

import json
import logging
from typing import Any

_log = logging.getLogger(__name__)

# ── 动作词表：唯一事实源（由执行端能力单点派生，禁止手抄子集）──
# 为什么：提示词曾硬编码动作子集，与执行端能力脱钩——执行端已注册 check/uncheck，
# AI 却无从得知，含必选勾选框的正向表单因此无法提交。改为从执行端"可交互动作集合"
# 派生，新增动作只需在执行端声明一次。
from src.pipeline.steps.step4.action_hooks import (
    _ELEMENT_ACTION_TYPES as _EXECUTOR_ELEMENT_ACTIONS,
)

# AI 决策层额外可发出的非元素动作（由工具层执行）
_DECIDE_NON_ELEMENT_ACTIONS = ("navigate", "wait", "scroll")
# AI 决策层的控制动作（不落工具层，由 _ai_loop 消费）
_DECIDE_CONTROL_ACTIONS = ("assert", "done", "fail")

# 动作 → (说明, action_params 契约)。参数名以 _ai_loop 消费的键为准
# （selector 会被转成 target_hint 做自然语言定位，故必须写自然语言而非 CSS 选择器）。
_DECIDE_ACTION_DOCS = {
    "click": ("点击元素", '{"selector": "元素描述(自然语言)"}'),
    "fill": ("在输入框填入文字", '{"selector": "输入框描述(自然语言)", "value": "填入值"}'),
    "select_option": ("在下拉框中选择选项", '{"selector": "下拉框描述(自然语言)", "option_text": "选项文字"}'),
    "check": ("勾选 checkbox/radio（幂等：已勾选则安全跳过）", '{"selector": "复选框描述(自然语言)"}'),
    "uncheck": ("取消勾选 checkbox", '{"selector": "复选框描述(自然语言)"}'),
    "hover": ("鼠标悬停（触发 hover 下拉菜单/tooltip）", '{"selector": "元素描述(自然语言)"}'),
    "double_click": ("双击元素", '{"selector": "元素描述(自然语言)"}'),
    "right_click": ("右键点击元素", '{"selector": "元素描述(自然语言)"}'),
    "drag": ("拖拽元素", '{"source_hint": "源元素描述", "target_hint": "目标元素描述"}'),
    "navigate": ("导航到 URL", '{"url": "https://...（可留空并填 target_hint 走菜单导航）"}'),
    "wait": ("等待页面异步操作", '{"seconds": 2}'),
    "scroll": ("滚动页面", '{"direction": "down"}'),
    "assert": (
        "断言校验",
        '{"assert_type": "element_exists|text_contains", "selector": "元素描述", "expected": "期望文本"}',
    ),
    "done": ("当前步骤目标已达成，结束循环", "{}"),
    "fail": ("判定无法完成，结束循环", '{"reason": "失败原因"}'),
}


def decide_action_types() -> tuple:
    """AI 单动作决策可用动作类型（唯一事实源）。

    = 执行端可交互动作集合 + 决策层非元素动作 + 控制动作。
    调用方（提示词、日志、校验）一律从这里取，不得再各自硬编码子集。
    """
    return tuple(
        sorted(
            set(_EXECUTOR_ELEMENT_ACTIONS)
            | set(_DECIDE_NON_ELEMENT_ACTIONS)
            | set(_DECIDE_CONTROL_ACTIONS)
        )
    )


def _build_decide_system_prompt() -> str:
    """按 `decide_action_types()` 生成决策提示词（词表变化自动同步，无需手改）。"""
    _actions = decide_action_types()
    _lines = []
    for _a in _actions:
        _desc, _params = _DECIDE_ACTION_DOCS.get(
            _a, ("（执行端已注册的可交互动作）", '{"selector": "元素描述(自然语言)"}')
        )
        _lines.append("- %s: %s。action_params: %s" % (_a, _desc, _params))
    return (
        "你是浏览器自动化测试的动作决策器。根据给定的页面上下文（DOM 片段、期望结果、截图），"
        "决策下一步执行的单一动作。\n\n"
        "## 动作类型（action_type，仅限以下取值）\n"
        + "\n".join(_lines)
        + "\n\n## 输出格式（严格 JSON，不要 markdown 代码块、不要多余文字）\n"
        + '{"action_type": "%s", ' % "|".join(_actions)
        + '"action_params": {...}, "reasoning": "简短推理"}'
    )


# 单动作决策 system prompt：约束 AI 只输出一个动作
_DECIDE_SYSTEM_PROMPT = _build_decide_system_prompt()

# ★ 根因修复(2026-09-26) E1 —— "确定性失败"判据（通用，不依赖业务/系统）。
#   实测 accept_sut_0923 试点：视觉通道(SiliconFlow) 返回
#     HTTP 400 {"code":20012,"message":"Model does not exist..."} / 402 欠费 / 403 模型停用，
#   而文本通道(主 AI) 完全正常。原实现把这类**确定性故障**与"瞬时空响应"混为一谈：
#   二者都只 log.warning 后 `return None` → 上游 _ai_loop 只能记 "AI 无响应(含N次重试)"，
#   运维无从定位（真因是模型名不存在/欠费，重试一万次也不会成功）。
#   同时视觉通道挂掉即等于 AI 决策全灭 —— 单一通道故障不应等价于"引擎丧失智能"。
#   修复：① 确定性失败 → 熔断该通道并**降级到文本通道**（prompt 已含 DOM 上下文）；
#         ② 真实错误经 _last_error 透出，供上游如实归因（区分 AI 通道故障 vs AI 无响应）。
_HARD_AI_FAIL_MARKERS = (
    "http 400", "http 401", "http 402", "http 403", "http 404",
    "model does not exist", "model not exist", "model_not_found",
    "model disabled", "insufficient", "balance",
    "invalid api key", "invalid_api_key", "unauthorized", "no such model",
)


def is_hard_ai_failure(err) -> bool:
    """该失败是否为确定性（重试无意义）的通道故障：模型不存在/欠费/鉴权失败/请求非法。

    通用性：纯错误文本特征判定，跨 provider/系统/行业，不含任何业务字段。
    """
    _e = str(err or "").lower()
    return any(_m in _e for _m in _HARD_AI_FAIL_MARKERS)


class _BrowserAiDecideMixin:
    """单动作决策能力（兜底路径 _run_ai_round_loop 专用）。

    ★ _last_usage: 最近一次 decide_action 的 token 用量，
       供 _ai_loop 读取后传入 run_action_with_hooks → HarnessGovernancePlugin。
    """

    def decide_action(self, prompt: str, screenshot: Any = "") -> Any | None:
        """单动作决策：给定页面上下文 prompt → 返回单个动作 dict/str。

        返回 None 表示无响应（_ai_loop.py:136 会判定 "AI 无响应" 并 break）。
        ★ 同时将 token usage 存至 self._last_usage，供上层传入 action_hooks。
        ★ Fix57 (2026-07-09): 当有截图且配置了视觉模型时，用 call_vision 发送截图给模型，
          让 AI 能"看到"页面状态，提升定位准确率。无视觉模型时回退到纯文本。
        """
        self._last_usage = {}  # ★ 每次调用重置（惰性初始化，不依赖 __init__）
        # ★ E1(2026-09-26): 失败原因透出（见文件头 _HARD_AI_FAIL_MARKERS 说明）
        self._last_error = ""
        self._last_hard_error = False
        if not prompt:
            _log.warning("[decide_action] prompt 为空，返回 None")
            self._last_error = "prompt 为空"
            return None
        try:
            from src.ai.ai_client import ai_brain
            from src.core.config_loader import config as cfg

            _log.warning(
                "[decide_action] 开始调用 AI, prompt长度=%d, screenshot类型=%s, 长度=%d",
                len(prompt),
                type(screenshot).__name__,
                len(screenshot) if screenshot else 0,
            )

            # ★ 诊断: 打印 API key 状态（掩码）
            with guard("decide_action: from src.core.config_session import ", log=_log, level=logging.WARNING):
                from src.core.config_session import get_session_key, get_current_user

                _sk = get_session_key() or ""
                _uid = get_current_user() or ""
                _cfg_key = cfg.ai_api_key or ""
                _sk_mask = (
                    (_sk[:6] + "***" + _sk[-4:])
                    if len(_sk) > 10
                    else ("EMPTY" if not _sk else "TOO_SHORT")
                )
                _cfg_mask = (
                    (_cfg_key[:6] + "***" + _cfg_key[-4:])
                    if len(_cfg_key) > 10
                    else ("EMPTY" if not _cfg_key else "TOO_SHORT")
                )
                _log.warning(
                    "[decide_action] API key 诊断: session_key=%s, uid=%s, cfg_key=%s",
                    _sk_mask,
                    _uid,
                    _cfg_mask,
                )

            # ★ Fix57 (re-enabled ): 视觉模型路径已修复跨 provider 401 问题
            #   通过 config.vision_api_key / vision_base_url 读取视觉专属配置，
            #   不再用主 ai_api_key 调用异构 provider 的模型。
            #   条件：有截图 + 配置了视觉模型 + 视觉模型已启用 + 视觉 key 与主 key 不同（异构 provider）
            #   若视觉 key 与主 key 相同（同 provider），走文本路径更稳（避免代理服务器无该模型通道）
            _has_vision_key = bool(
                cfg.vision_model
                and cfg.vision_model_enabled
                and cfg.vision_api_key
                and cfg.vision_api_key != cfg.ai_api_key
            )
            use_vision = bool(screenshot and _has_vision_key)

            # ★ E1 熔断：视觉通道已被证实确定性不可用 → 本轮起直接走文本通道，
            #   不再为每个动作重复支付一次必然失败的视觉调用（省时且不再刷错误日志）。
            _vision_dead = str(getattr(self, "_vision_channel_dead", "") or "")
            if _vision_dead:
                use_vision = False

            result = None
            if use_vision:
                import base64

                img_b64 = base64.b64encode(screenshot).decode("utf-8")
                result = ai_brain.call_vision(
                    image_base64=img_b64,
                    prompt=_DECIDE_SYSTEM_PROMPT + "\n\n## 页面上下文\n" + prompt,
                    caller_name="step4_decide_action_vision",
                    temperature=0.1,
                    max_tokens=4096,
                    vision_api_key=cfg.vision_api_key,
                    vision_base_url=cfg.vision_base_url,
                )
                _verr = str((result or {}).get("error", "") or "")
                if not (result or {}).get("success") and is_hard_ai_failure(_verr):
                    # 确定性故障 → 熔断视觉通道，并降级到文本通道（prompt 已含 DOM 上下文）
                    self._vision_channel_dead = _verr[:200]
                    _log.warning(
                        "[decide_action] 视觉通道确定性不可用(%s) → 熔断并降级文本通道",
                        _verr[:160],
                    )
                    result = None

            if result is None:
                # 文本通道（与视觉通道互为备份，任一可用即不丧失 AI 决策能力）
                result = ai_brain._call_api(
                    system_prompt=_DECIDE_SYSTEM_PROMPT,
                    user_message=prompt,
                    temperature=0.1,
                    max_tokens=4096,
                    task_type="general",
                    caller_name="step4_decide_action",
                )
        except Exception as e:
            _log.warning("[decide_action] AI 调用异常: %s", str(e)[:200])
            self._last_error = "AI 调用异常: %s" % str(e)[:200]
            return None

        if not result:
            _log.warning("[decide_action] result 为 None")
            self._last_error = self._last_error or "AI 返回空结果"
            return None
        if not result.get("success"):
            _err = str(result.get("error", "") or "")
            _log.warning(
                "[decide_action] AI 无成功响应: success=%s, error=%s",
                result.get("success"),
                _err[:200],
            )
            # ★ E1: 真实错误透出（上游据此区分"通道故障"与"AI 无响应"，并跳过无意义重试）
            self._last_error = _err or "AI 返回失败"
            self._last_hard_error = is_hard_ai_failure(_err)
            return None

        _log.warning(
            "[decide_action] AI 调用成功, data类型=%s, data长度=%d",
            type(result.get("data")).__name__,
            len(str(result.get("data", ""))),
        )

        # ★ 存储 token usage（供 HarnessGovernancePlugin 追踪）
        #    内部统一格式为 prompt_tokens/completion_tokens，兼容 input_tokens/output_tokens
        usage = result.get("usage") or {}
        if isinstance(usage, dict) and usage:
            self._last_usage = {
                "input": usage.get("prompt_tokens", usage.get("input_tokens", 0)),
                "output": usage.get("completion_tokens", usage.get("output_tokens", 0)),
            }

        data = result.get("data") or ""
        if isinstance(data, (dict, list)):
            return data
        text = str(data).strip()
        if not text:
            _log.warning("[decide_action] AI 返回空内容")
            self._last_error = "AI 返回空内容"
            return None
        # 优先尝试解析 JSON（_ai_loop.py:147 也走 json.loads，保持一致）
        try:
            return json.loads(text)
        except Exception:
            # 解析失败 → 原样返回 str，由 _ai_loop.py:147 再尝试解析 / 兜底
            return text
