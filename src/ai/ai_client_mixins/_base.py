# -*- coding: utf-8 -*-
from __future__ import annotations

"""
AiBrain 基础 Mixin
==================
提供初始化、配置刷新、观察者注册、通用 API 调用及视觉验证等核心能力。
"""

import base64
import functools
import json
import logging
import time
from collections.abc import Callable
from queue import Empty, Queue
from threading import Thread
from typing import Any
from src.core.config_loader import config
from src.ai.ai_providers import get_provider, BaseProvider
from src.ai.observers import ObserverNotifier
from src.ai.stats import TokenStats

_log = logging.getLogger(__name__)


# ★ v2：延迟导入 session_ctx（避免循环依赖，仅 Step2/3 需要）
_session_ctx = None


def _get_session_ctx():
    """惰性获取 session_ctx，避免循环导入。"""
    global _session_ctx
    if _session_ctx is None:
        try:
            from src.core.session_context import session_ctx

            _session_ctx = session_ctx
        except ImportError as _e:
            _log.debug("[ai_base] 加载 session_ctx 异常: %s", _e, exc_info=True)
    return _session_ctx


def _chat_with_hard_timeout(provider: BaseProvider, timeout: float, kwargs: dict):
    """带硬超时的同步 Provider.chat 调用（守护线程 + queue）。

    与 openai_compat 的 `_post_with_deadline` 同一思路：个别 Provider 若无内部
    超时/卡死保护时，防止其长时间阻塞调用方线程。超时/异常时不抛异常，返回携带
    明确 error 的失败 dict，交由调用方统一记录与降级。
    """
    q: Queue = Queue(maxsize=1)

    def _run():
        try:
            q.put(provider.chat(**kwargs))
        except BaseException as e:  # noqa: BLE001 — 线程内兜底，避免卡死 queue
            try:
                q.put({"success": False, "error": "Provider.chat 异常: %s" % e})
            except BaseException:
                _log.exception("Provider.chat 线程异常上报失败")

    Thread(target=_run, daemon=True).start()
    try:
        if timeout and timeout > 0:
            return q.get(timeout=timeout)
        return q.get()
    except Empty:
        _log.warning("AI 调用超时（%.1fs），已停止等待", timeout or 0)
        return {"success": False, "error": "AI 调用超时（%.1fs）" % (timeout or 0)}
    except BaseException as e:  # noqa: BLE001
        _log.exception("Queue.get 读结果异常")
        return {"success": False, "error": "AI 调用结果获取失败: %s" % e}


def _track_ai_inflight(fn):
    """把一次 AI 调用登记为「在飞行」（超时取实例当前配置）。

    why: 看门狗的进展信号只看 run_logs/executions；等待大模型返回期间两者都不增长，
         导致正常慢被误判为挂死。本装饰器覆盖**不走 Router 的直连 Provider 分支**
         （use_router=False / task_type 为空 / Router 抛异常后的兜底路径）；
         走 Router 的路径由 Router.route 侧登记，两处合起来才是完整出口。
    """
    @functools.wraps(fn)
    def _wrapped(self, *args, **kwargs):
        from src.ai.ai_inflight import ai_request_inflight

        with ai_request_inflight(getattr(self, "_timeout", None)):
            return fn(self, *args, **kwargs)

    return _wrapped


class _BaseMixin:
    """基础能力：初始化、配置刷新、观察者、API 调用、视觉验证。"""

    def __init__(self) -> None:
        """初始化 AI 大脑，从全局配置读取 AI 参数并创建对应的 Provider。"""
        self._provider_name = config.ai_provider
        self._api_key = config.ai_api_key
        self._base_url = config.ai_base_url
        self._model = config.ai_model
        self._timeout = config.ai_timeout

        # 创建 Provider 实例
        self._provider: BaseProvider = get_provider(
            name=self._provider_name,
            api_key=self._api_key,
            base_url=self._base_url,
            model=self._model,
            timeout=self._timeout,
        )

        # ── 观察者通知器 ──
        self._notifier = ObserverNotifier()

        # ── 累计统计 ──
        self._stats = TokenStats()

        # ── 懒加载业务方法标记 ──
        self._business_methods_mounted = False

    def register_observer(self, on_start: Callable = None, on_finish: Callable = None):
        """
        注册思考状态观察者回调。

        参数：
            on_start:  callable(name: str)          — 思考开始时调用
            on_finish: callable(name, elapsed, usage, cost) — 思考结束时调用
        """
        self._notifier.register(on_start, on_finish)

    def _refresh_config(self):
        """从 config 刷新所有配置（用户登录/切换项目后可能变化）。"""
        new_provider = config.ai_provider
        new_key = config.ai_api_key
        new_base = config.ai_base_url
        new_model = config.ai_model
        new_timeout = config.ai_timeout

        changed = (
            new_key != self._api_key
            or new_base != self._base_url
            or new_model != self._model
            or new_provider != self._provider_name
        )
        if changed:
            self._provider_name = new_provider
            self._api_key = new_key
            self._base_url = new_base
            self._model = new_model
            self._timeout = new_timeout
            self._provider: BaseProvider = get_provider(
                name=self._provider_name,
                api_key=self._api_key,
                base_url=self._base_url,
                model=self._model,
                timeout=self._timeout,
            )

    @_track_ai_inflight
    def _call_api(
        self,
        system_prompt: str,
        user_message: str,
        temperature: float = 0.3,
        max_tokens: int = 8192,
        caller_name: str = "",
        task_type: str = "",
        use_router: bool = True,
        force_cloud: bool = False,
        cloud_model: str | None = None,
        local_timeout: float | None = None,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | None = None,
        response_format: dict[str, Any] | None = None,
        seed: int | None = None,
    ) -> dict[str, Any]:
        """
        通用 AI API 调用方法（按任务类型路由）。

        返回：
            {"success": True, "data": "AI 回复文本", "usage": {...}, "tool_calls": [...]}
            或 {"success": False, "error": "..."}
        """
        # ★ 刷新所有配置（用户登录/切换项目后可能变化）
        self._refresh_config()

        # ★ 运行期 AI 闸门（判据唯一实现在 runtime_ai_gate，此处只读）：
        #   直连 provider 路径同样受控，否则绕过 router 直接打模型。
        from src.ai.runtime_ai_gate import runtime_ai_denied

        if runtime_ai_denied():
            return {
                "success": False,
                "error": "运行期 AI 已禁用(step4_runtime_ai=False)",
            }

        # ★ 路由模块：按任务类型路由到用户指定的 provider 和模型
        if use_router and task_type:
            try:
                from src.ai.router_module import router

                # ★ 智能 max_tokens：根据实际 prompt 内容 + 模型窗口动态计算，替代前端固定配置
                #    compute_max_tokens 已内置 _SANITY_CEILING（如 deepseek-v4-pro=16384），
                #    调用方传入的 max_tokens 仅作 fallback（smart_max 计算失败时兜底）
                from src.ai.token_estimator import compute_max_tokens

                smart_max, smart_info = compute_max_tokens(
                    system_prompt, user_message, model=(cloud_model or self._model)
                )
                final_max = smart_max if smart_max > 0 else max_tokens
                if task_type == "requirement_analysis":
                    # Step2 面向领导验收必须可控：需求拆解只要求 JSON 能力清单，
                    # 不允许智能上调到超大输出导致云端推理模型长时间思考。
                    final_max = min(final_max, max_tokens, 8192)
                import logging

                _smart_log = logging.getLogger(__name__)
                _smart_log.info(
                    "[智能调节] task=%s model=%s input=%d tokens → smart_max=%d (caller_max=%d) → final=%d",
                    task_type,
                    (cloud_model or self._model),
                    smart_info.get("input_tokens", 0) if smart_max > 0 else 0,
                    smart_max,
                    max_tokens,
                    final_max,
                )
                route_result = router.route(
                    prompt=user_message,
                    system_prompt=system_prompt,
                    task_type=task_type,
                    expect_json=True,
                    prefer_cheap=True,
                    cloud_model=cloud_model or self._model,
                    force_cloud=force_cloud,
                    local_timeout=local_timeout,
                    temperature=temperature,
                    max_tokens=final_max,
                    response_format=response_format,
                    seed=seed,
                )
                if route_result.get("success"):
                    usage = route_result.get("usage", {})
                    elapsed = route_result.get("_elapsed", 0)
                    cost = route_result.get("_cost", 0.0)
                    if usage:
                        self._stats.record(usage, cost)
                    actual_model = route_result.get("model", "unknown")
                    self._notifier.notify_finish(
                        caller_name, elapsed, usage, cost, actual_model
                    )
                    result = {
                        "success": True,
                        "data": route_result["data"],
                        "usage": usage,
                    }
                    result["_elapsed"] = elapsed
                    result["_cost"] = cost
                    result["_source"] = route_result.get("source", "unknown")
                    result["_model"] = route_result.get("model", "unknown")
                    return result
                else:
                    elapsed = route_result.get("_elapsed", time.time())
                    self._notifier.notify_finish(caller_name, elapsed, {}, 0.0, "")
                    return {
                        "success": False,
                        "error": route_result.get("error", "路由失败"),
                    }
            except ImportError:
                _log.debug("optional dep not available", exc_info=True)
            except Exception:
                _log.exception("路由调用异常")

        # ── 通知观察者：思考开始 ──
        t0 = time.time()
        self._notifier.notify_start(caller_name)

        # ★ 不走 Router 的路径也同样智能计算 max_tokens
        try:
            from src.ai.token_estimator import compute_max_tokens

            smart_max, smart_info = compute_max_tokens(
                system_prompt, user_message, model=(cloud_model or self._model)
            )
            if smart_max > 0:
                import logging

                _smart_log2 = logging.getLogger(__name__)
                _smart_log2.info(
                    "[智能调节] non-router model=%s input=%d tokens → smart_max=%d (caller_max=%d) → final=%d",
                    (cloud_model or self._model),
                    smart_info.get("input_tokens", 0),
                    smart_max,
                    max_tokens,
                    smart_max,
                )
                max_tokens = smart_max
        except Exception as e:
            import logging

            logging.getLogger(__name__).debug(
                "[_call_api] non-router smart max_tokens 跳过: %s", str(e)[:80]
            )

        # ── 通过 Provider 调用 AI（自动适配 DeepSeek / OpenAI / Anthropic） ──
        # ★ 统一带硬超时守护，防止个别 Provider 无内部卡死保护时阻塞调用方线程
        provider_result = _chat_with_hard_timeout(
            self._provider,
            self._timeout,
            {
                "system_prompt": system_prompt,
                "user_message": user_message,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "model": cloud_model or None,
                "tools": tools,
                "tool_choice": tool_choice,
                "response_format": response_format,
            },
        )

        if not provider_result.get("success"):
            elapsed = time.time() - t0
            self._notifier.notify_finish(caller_name, elapsed, {}, 0.0, "")
            return {
                "success": False,
                "error": "%s API 调用失败: %s"
                % (
                    self._provider_name.capitalize(),
                    provider_result.get("error", "未知错误"),
                ),
            }

        # ── 提取结果 ──
        content = provider_result["data"]
        usage = provider_result.get("usage", {})
        elapsed = provider_result.get("_elapsed", time.time() - t0)
        cost = provider_result.get("_cost", 0.0)

        # ── 计费统计 ──
        self._stats.record(usage, cost)

        # ── 通知观察者：思考结束 ──
        actual_model = provider_result.get("model", self._model)
        self._notifier.notify_finish(caller_name, elapsed, usage, cost, actual_model)

        result = {
            "success": True,
            "data": content,
            "usage": usage,
        }
        result["_elapsed"] = elapsed
        result["_cost"] = cost
        result["_source"] = provider_result.get("source", self._provider_name)
        result["_model"] = actual_model
        if "tool_calls" in provider_result:
            result["tool_calls"] = provider_result["tool_calls"]
        return result

    def call_vision(
        self,
        image_base64: str,
        prompt: str,
        caller_name: str = "视觉调用",
        vision_model: str | None = None,
        vision_api_key: str | None = None,
        vision_base_url: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 256,
    ) -> dict[str, Any]:
        """
        ★ 统一视觉调用入口：所有视觉调用（点击定位/视觉验证）都走这里。

        单一入口职责：
        - 读取 config.vision_model（已通过 user_credentials 配置）
        - 用 config.detect_provider 构造视觉专属 provider（与任务模型 provider 解耦）
        - 调用 provider.chat_vision 并记录统计/通知观察者
        - 返回原始响应文本（不做业务解析，由调用方处理）

        返回：
            {"success": True, "data": "AI 回复文本", "usage": {...}, "_model": ...}
            或 {"success": False, "error": "..."}
        """
        self._refresh_config()

        # ★ 按 vision_provider 路由（根因修复 ）：此前一律用 detect_provider(model)
        #   判定，本地多模态模型（qwen2.5vl/llava 等）被回退到 deepseek 且 ollama 无 provider，
        #   导致本地视觉被禁用。现显式尊重用户选定的来源：
        #     - vision_provider=ollama → 本地 Ollama /api/generate（无鉴权，模型取 ollama_vision_model）
        #     - vision_provider=cloud  → 云端，按 detect_provider 构造专属 provider
        provider_sel = str(getattr(config, "vision_provider", "cloud") or "cloud")
        if provider_sel == "ollama":
            v_model = vision_model or config.ollama_vision_model
            if not v_model:
                return {"success": False, "error": "未配置 Ollama 视觉模型 (VISION_LOCAL_MODEL)"}
            v_api_key = vision_api_key or ""  # Ollama 无鉴权
            v_base_url = vision_base_url or getattr(config, "ollama_api_base", "http://localhost:11434")
            vision_provider_name = "ollama"
        else:
            v_model = vision_model or config.vision_model
            if not v_model:
                return {"success": False, "error": "未配置视觉模型 (VISION_MODEL)"}
            # ★ Fix: 视觉模型独立 API key / base_url
            v_api_key = vision_api_key or config.vision_api_key
            v_base_url = vision_base_url or config.vision_base_url
            # 用 vision_model 构造视觉专属 provider（与任务模型 provider 解耦）
            vision_provider_name = config.detect_provider(v_model)

        vision_provider = get_provider(
            vision_provider_name,
            v_api_key,
            v_base_url,
            v_model,
            timeout=30,
            max_retries=1,
        )

        if not hasattr(vision_provider, "chat_vision"):
            return {
                "success": False,
                "error": f"Provider {vision_provider_name} 不支持视觉模型 (无 chat_vision 方法)",
            }

        t0 = time.time()
        self._notifier.notify_start(caller_name)
        try:
            result = vision_provider.chat_vision(
                image_base64=image_base64,
                prompt=prompt,
                temperature=temperature,
                max_tokens=max_tokens,
            )
        except Exception as e:
            elapsed = time.time() - t0
            self._notifier.notify_finish(caller_name, elapsed, {}, 0.0)
            return {"success": False, "error": f"视觉模型调用异常: {e}"}

        elapsed = result.get("_elapsed", time.time() - t0)
        usage = result.get("usage", {})
        cost = result.get("_cost", 0.0)
        self._stats.record(usage, cost)

        actual_model = result.get("model", v_model)
        self._notifier.notify_finish(caller_name, elapsed, usage, cost, actual_model)

        if not result.get("success"):
            return {"success": False, "error": result.get("error", "视觉模型返回失败")}

        return {
            "success": True,
            "data": result.get("data", ""),
            "usage": usage,
            "_elapsed": elapsed,
            "_cost": cost,
            "_model": actual_model,
        }

    def visual_verify(
        self,
        screenshot_bytes: bytes,
        expectation: str,
        caller_name: str = "视觉验证",
        vision_model: str | None = None,
        vision_api_key: str | None = None,
        vision_base_url: str | None = None,
    ) -> dict[str, Any]:
        """
        ★ v5: 视觉验证（Midscene 风格的截图驱动断言）。

        建立在 call_vision 之上：调用统一入口获取原始响应，再按验证语义解析 JSON。
        支持调用方显式传入 vision_model/vision_api_key/vision_base_url，
        消除 asyncio.to_thread 后台线程 thread-local(user_id) 为空导致 config 无法读 DB 的风险。
        """
        # ★ 根因修复(2026-08-30): 以「模型能力」判定是否可做视觉验证，废弃 provider 名一刀切。
        #   原实现: vision_provider==ollama 即强制跳过，注释错误声称 "ollama 无视觉能力"。
        #   事实上 Ollama 可运行多模态视觉模型（如 qwen2.5vl:*），is_vision_model 能正确识别；
        #   用 provider 字符串代替能力探测，导致可用本地视觉模型被错误禁用（"打折"）。
        #   通用性: 跨 provider/跨模型 —— 能力判定统一走 is_vision_model(model)，与厂商解耦。
        from src.ai.model_registry import is_vision_model

        _vm = vision_model or str(getattr(config, "vision_model", "") or "")
        if not _vm:
            return {
                "success": False,
                "passed": False,
                "reason": "未配置视觉模型，跳过视觉验证",
                "raw": "",
            }
        # 能力判定失败且无显式覆盖 → 该模型非多模态视觉模型，跳过（避免误调用报错）
        if not is_vision_model(_vm):
            return {
                "success": False,
                "passed": False,
                "reason": f"模型 {_vm[:60]} 非多模态视觉模型，跳过视觉验证",
                "raw": "",
            }

        if not screenshot_bytes:
            return {"success": False, "passed": False, "reason": "截图为空", "raw": ""}

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
            "请判断页面是否符合描述。\n\n"
            "判断要求：\n"
            "1. 只根据截图内容判断，不要猜测截图外的内容\n"
            "2. 文本、颜色、布局、是否可见都可以作为判断依据\n"
            "3. 当内容部分匹配但有明显不符时，返回 false 并说明原因\n\n"
            "严格按以下 JSON 格式返回（不要加任何解释，不要包在代码块中）：\n"
            '{"passed": true/false, "reason": "一句话说明判断依据，中文"}\n\n'
            f"预期描述：{expectation}\n"
        )

        # ★ 走统一视觉入口
        result = self.call_vision(
            image_base64=image_b64,
            prompt=vision_prompt,
            caller_name=caller_name,
            vision_model=vision_model,
            vision_api_key=vision_api_key,
            vision_base_url=vision_base_url,
        )

        if not result.get("success"):
            return {
                "success": False,
                "passed": False,
                "reason": result.get("error", "视觉模型调用失败")[:200],
                "raw": "",
            }

        raw_data = result.get("data", "")

        # 解析 JSON 判断结果
        passed = False
        reason = raw_data[:100]
        try:
            from src.ai.json_parser import parse_json_response

            parsed, _ = parse_json_response(raw_data)
            if isinstance(parsed, dict):
                passed = bool(parsed.get("passed", False))
                reason = str(parsed.get("reason", reason))
        except Exception as _e:
            _log.debug("视觉验证结果解析失败: %s", _e, exc_info=True)

        return {
            "success": True,
            "passed": passed,
            "reason": reason,
            "raw": raw_data,
            "usage": result.get("usage", {}),
            "_model": result.get("_model", ""),
        }

    def _notify_finish(
        self, name: str, elapsed: float, usage: dict, cost: float, model: str = None
    ):
        """通知所有观察者思考结束。（向后兼容方法）"""
        if model is None:
            model = getattr(self, "_model", "unknown")
        self._notifier.notify_finish(name, elapsed, usage, cost, model)
