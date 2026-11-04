# -*- coding: utf-8 -*-
from __future__ import annotations
from src.core.guarded import guard

"""
Plugin Manager — 插件管理器
==========================
统一管理 Step4 所有插件的生命周期和钩子调用。

设计原则：
- 插件通过 Hook 机制介入执行链路，不修改原有代码
- 每个插件独立文件，独立配置，独立启用/禁用
- 插件异常不影响主流程（try-except 包裹）
"""

import logging
import threading
import time
from enum import Enum
from typing import Any
from asyncio import iscoroutine as _asyncio_iscoroutine

from src.core.circuit_breaker import CircuitBreaker

_log = logging.getLogger(__name__)

# ★ 靶子 DB（旁路审计写入 plugin_interventions）可用性的一次性记忆。
#   why: 该写入不在用例判定必经路径上；靶子 DB 不可达时原实现每条用例都重新
#        付满连接超时（实测 113 次失败累计白烧 202s，占前 4 分钟墙钟 84%）。
_db_avail_lock = threading.Lock()
_db_avail_state: bool | None = None
_db_avail_checked_at = 0.0
_DB_AVAIL_COOLDOWN_SEC = 240.0


def _target_db_available() -> bool:
    """靶子 DB 是否可用：只探测一次，结论缓存；失败结论冷却后允许再探一次。"""
    global _db_avail_state, _db_avail_checked_at
    with _db_avail_lock:
        _now = time.monotonic()
        if (
            _db_avail_state is not None
            and (_now - _db_avail_checked_at) < _DB_AVAIL_COOLDOWN_SEC
        ):
            return _db_avail_state
        try:
            from src.core.config_loader import config

            if not str(getattr(config, "db_name", "") or "").strip():
                _db_avail_state = False
            else:
                from src.extensions.db.db_agent import db_agent

                db_agent.query("SELECT 1")
                _db_avail_state = True
        except Exception:
            _db_avail_state = False
        _db_avail_checked_at = _now
        return _db_avail_state

# ★ 靶子 DB（旁路审计写入 plugin_interventions）可用性的一次性记忆。
#   why: 该写入不在用例判定必经路径上；靶子 DB 未配置/拒绝访问时，原实现每条用例
#        都重新付满连接超时（实测 113 次失败累计白烧 202s，占墙钟 84%）。
_db_avail_lock = threading.Lock()
_db_avail_state: bool | None = None
_db_avail_checked_at = 0.0
_DB_AVAIL_COOLDOWN_SEC = 240.0


def _target_db_available() -> bool:
    """靶子 DB 是否可用：只探测一次，结论缓存；失败结论冷却后允许再探一次。"""
    global _db_avail_state, _db_avail_checked_at
    with _db_avail_lock:
        _now = time.monotonic()
        if (
            _db_avail_state is not None
            and (_now - _db_avail_checked_at) < _DB_AVAIL_COOLDOWN_SEC
        ):
            return _db_avail_state
        try:
            from src.extensions.db.db_agent import db_agent
            from src.core.config_loader import config

            if not str(getattr(config, "db_name", "") or "").strip():
                _db_avail_state = False
            else:
                db_agent.query("SELECT 1")
                _db_avail_state = True
        except Exception:
            _db_avail_state = False
        _db_avail_checked_at = _now
        return _db_avail_state


class PluginHook(str, Enum):
    """插件可介入的执行节点。"""

    BEFORE_ACTION = "before_action"  # 动作执行前
    AFTER_ACTION = "after_action"  # 动作执行后
    BEFORE_STEP = "before_step"  # 步骤执行前
    AFTER_STEP = "after_step"  # 步骤执行后
    BEFORE_INTENT = "before_intent"  # 测试用例执行前
    AFTER_INTENT = "after_intent"  # 测试用例执行后
    ON_FAILURE = "on_failure"  # 失败时
    ON_VERIFY = "on_verify"  # 验证时
    ON_NAVIGATE = "on_navigate"  # 页面导航时
    ON_AI_PLAN = "on_ai_plan"  # AI 规划操作序列时
    BEFORE_CHECK = "before_check"  # ★ B-5-1: 检查执行前（三轨校验前）
    AFTER_CHECK = "after_check"  # ★ B-5-1: 检查执行后（三轨校验后）


class BasePlugin:
    """插件基类。所有插件必须继承此类。"""

    name: str = "base"
    enabled: bool = True
    config: dict[str, Any] = {}

    def __init__(self, config: dict[str, Any] = None):
        self.config = config or {}
        self.enabled = self.config.get("enabled", True)

    def on_hook(self, hook, context: dict[str, Any]) -> dict[str, Any] | None:
        """
        插件入口。子类重写此方法处理特定钩子。

        参数：
            hook: 当前触发的钩子类型（PluginHook 枚举或字符串）
            context: 上下文数据，内容因钩子而异

        返回：
            None — 不干预主流程
            Dict — 干预结果（如修改后的 context、校验结果等）
        """
        # ★ B-5-3: 兼容 PluginHook 枚举和纯字符串（PipelineBus 传入字符串）
        hook_name = hook.value if hasattr(hook, "value") else str(hook)
        handler = getattr(self, f"on_{hook_name}", None)
        if handler and callable(handler):
            try:
                return handler(context)
            except Exception as e:
                _log.warning(
                    "[Plugin:%s] %s 异常: %s", self.name, hook_name, str(e)[:120]
                )
                return None
        return None

    def on_before_action(self, ctx: dict[str, Any]) -> dict[str, Any] | None:
        """动作执行前。ctx 包含: action, engine, page_state"""
        return None

    def on_after_action(self, ctx: dict[str, Any]) -> dict[str, Any] | None:
        """动作执行后。ctx 包含: action, result, before_state, after_state"""
        return None

    def on_before_step(self, ctx: dict[str, Any]) -> dict[str, Any] | None:
        """步骤执行前。ctx 包含: step, intent_schema, case_id"""
        return None

    def on_after_step(self, ctx: dict[str, Any]) -> dict[str, Any] | None:
        """步骤执行后。ctx 包含: step, result, step_result_entry"""
        return None

    def on_before_intent(self, ctx: dict[str, Any]) -> dict[str, Any] | None:
        """测试用例执行前。ctx 包含: intent, case_id, steps"""
        return None

    def on_after_intent(self, ctx: dict[str, Any]) -> dict[str, Any] | None:
        """测试用例执行后。ctx 包含: intent, case_id, passed, intent_step_results"""
        return None

    def on_failure(self, ctx: dict[str, Any]) -> dict[str, Any] | None:
        """失败时。ctx 包含: intent, case_id, failure_detail, step"""
        return None

    def on_verify(self, ctx: dict[str, Any]) -> dict[str, Any] | None:
        """验证时。ctx 包含: assertion, actual, expected"""
        return None

    def on_navigate(self, ctx: dict[str, Any]) -> dict[str, Any] | None:
        """导航时。ctx 包含: url, page_state"""
        return None

    def on_ai_plan(self, ctx: dict[str, Any]) -> dict[str, Any] | None:
        """AI 规划时。ctx 包含: goal, state, planned_actions"""
        return None

    def on_before_check(self, ctx: dict[str, Any]) -> dict[str, Any] | None:
        """★ B-5-1: 检查执行前。ctx 包含: step, checks, track, case_id"""
        return None

    def on_after_check(self, ctx: dict[str, Any]) -> dict[str, Any] | None:
        """★ B-5-1: 检查执行后。ctx 包含: step, check_result, track, passed, case_id"""
        return None


class PluginManager:
    """插件管理器。

    ★ 方案C 改造：移除 __new__ 强制单例，允许 PerProjectRegistry 创建多实例。
    """

    def __init__(self):
        self._plugins: list[BasePlugin] = []
        self._initialized = False

    def register(self, plugin: BasePlugin) -> None:
        """注册插件。"""
        if not plugin.enabled:
            _log.info("[PluginManager] %s 已禁用，跳过注册", plugin.name)
            return
        self._plugins.append(plugin)
        _log.info("[PluginManager] 注册插件: %s", plugin.name)

    def unregister(self, plugin_name: str) -> None:
        """注销插件。"""
        self._plugins = [p for p in self._plugins if p.name != plugin_name]
        _log.info("[PluginManager] 注销插件: %s", plugin_name)

    @staticmethod
    def _invoke_handler(plugin, hook, ctx: dict[str, Any]):
        """同步调用单个插件的 hook handler，返回结果对象（可能是 dict / None / coroutine）。

        BasePlugin 子类走 on_hook（内部再 dispatch 到 on_xxx）；
        裸类直接走 on_{hook_name}。
        ★ 不在此处 catch —— 由调用方决定如何处理异常与 coroutine。
        ★ B-5-3: 兼容 PluginHook 枚举和纯字符串。
        """
        if hasattr(plugin, "on_hook"):
            return plugin.on_hook(hook, ctx)
        hook_name = hook.value if hasattr(hook, "value") else str(hook)
        handler = getattr(plugin, f"on_{hook_name}", None)
        return handler(ctx) if handler and callable(handler) else None

    def emit(self, hook: PluginHook, context: dict[str, Any]) -> dict[str, Any]:
        """
        触发钩子（同步）。依次调用所有注册了该钩子的插件。

        ★ 修复：原实现遇到 async handler 直接 close() 丢弃，导致 async 插件
          （如 visual_baseline.on_after_step）在同步 emit 路径中从不触发。
          现在改为：无运行中事件循环时用新 loop run_until_complete 执行；
          有运行中循环时 ensure_future 调度并 warning（无法同步等待结果）。

        返回：
            合并后的 context（插件可修改）
        """
        if not self._plugins:
            return context

        # ★ Bug#4 修复: 在循环外预先定义 hook_name（之前第 190 行引用未定义的 hook_name
        #   导致 NameError，使所有插件干预记录写入失败）
        hook_name = hook.value if hasattr(hook, "value") else str(hook)

        ctx = dict(context)
        for plugin in self._plugins:
            if not plugin.enabled:
                continue
            with guard("emit: result = PluginManager._invoke_handl", log=_log, level=logging.WARNING):
                result = PluginManager._invoke_handler(plugin, hook, ctx)
                # ★ 修复：async handler 不再静默丢弃
                if _asyncio_iscoroutine(result):
                    result = self._run_coroutine_sync(result, plugin.name, hook_name)
                    if result is None:
                        # 调度模式下无法同步获取结果，跳过
                        continue
                if isinstance(result, dict):
                    ctx.update(result)
                    # ★ S45-006: 写入 plugin_interventions 表
                    self._write_plugin_intervention(
                        plugin.name, hook_name, result, context
                    )
        return ctx

    @staticmethod
    def _run_coroutine_sync(coro, plugin_name: str, hook_name: str):
        """在同步 emit 中执行 async handler。

        - 无运行中事件循环 → 新建 loop run_until_complete 后关闭。
        - 有运行中循环（如在 async 上下文误调 emit）→ ensure_future 调度
          并返回 None（无法同步等待结果），记 warning。
        """
        import asyncio as _asyncio

        try:
            _ = _asyncio.get_running_loop()
            # 有运行中的循环 → 不能 run_until_complete，调度后返回
            _asyncio.ensure_future(coro)
            _log.warning(
                "[PluginManager] %s.%s async handler 在运行中的事件循环里被 "
                "ensure_future 调度（emit 无法同步等待结果，请改用 execute_hook）",
                plugin_name,
                hook_name,
            )
            return None
        except RuntimeError:
            # 无运行中的循环 → 安全创建新 loop 执行
            _loop = _asyncio.new_event_loop()
            try:
                return _loop.run_until_complete(coro)
            except Exception as _e:
                _log.warning(
                    "[PluginManager] %s.%s async handler 执行异常: %s",
                    plugin_name,
                    hook_name,
                    str(_e)[:120],
                )
                return None
            finally:
                _loop.close()

    async def execute_hook(self, hook_name: str, **context: Any) -> dict[str, Any]:
        """
        触发钩子（async）。供 _base.py 等 async 主链路使用。

        ★ 修复断链：原 _base.py 调用 plugin_manager.execute_hook(...) 但该方法从未定义，
          导致 AttributeError 被 serial._execute_all 的 except 兜底、意图静默失败、
          所有插件 hook（含 visual_baseline.on_after_step）从不触发。

        - hook_name: 字符串（如 "after_step"），转 PluginHook 枚举。
        - context: 关键字参数，合并为 ctx 传给插件。
        - 兼容同步 handler（直接拿结果）与 async handler（await 协程）。
        - 返回合并后的 ctx（调用方目前均不使用返回值，但保留以备后用）。
        """
        try:
            hook = PluginHook(hook_name)
        except ValueError:
            _log.warning("[PluginManager] 未知 hook_name: %s", hook_name)
            return dict(context)

        import asyncio as _asyncio

        ctx = dict(context)
        for plugin in self._plugins:
            if not plugin.enabled:
                continue
            with guard("execute_hook: result = PluginManager._invoke_handl", log=_log, level=logging.WARNING):
                result = PluginManager._invoke_handler(plugin, hook, ctx)
                # ★ async handler：on_hook/on_xxx 返回 coroutine → await
                if _asyncio.iscoroutine(result):
                    result = await result
                if isinstance(result, dict):
                    ctx.update(result)
                    # ★ S45-006: 写入 plugin_interventions 表
                    self._write_plugin_intervention(
                        plugin.name, hook_name, result, context
                    )
        return ctx

    def get_plugin(self, name: str) -> BasePlugin | None:
        """按名称获取插件实例。"""
        for p in self._plugins:
            if p.name == name:
                return p
        return None

    def list_plugins(self) -> list[dict[str, Any]]:
        """列出所有已注册插件的状态。"""
        return [
            {"name": p.name, "enabled": p.enabled, "config": p.config}
            for p in self._plugins
        ]

    # 旁路审计写入的熔断器：靶子 DB 未配置/拒绝访问时，不该每条用例都再付一次连接超时。
    _intervention_breaker = CircuitBreaker("plugin_interventions")

    def _write_plugin_intervention(
        self,
        plugin_name: str,
        hook: str,
        result: dict[str, Any],
        context: dict[str, Any],
    ):
        """★ S45-006: 将插件干预写入 plugin_interventions 表。
        只在插件返回实质性干预结果时才写入（非 None 且非空 dict）。"""
        if not result:
            return
        # 旁路审计写入：靶子 DB 不可达时短路，不为旁路重复付连接超时。
        if not _target_db_available():
            _log.debug(
                "[PluginManager] 靶子 DB 不可用，跳过干预写入: %s.%s",
                plugin_name,
                str(hook),
            )
            return
        if not self._intervention_breaker.allow():
            _log.debug(
                "[PluginManager] 干预写入已熔断(剩余%.0fs)，跳过: %s.%s",
                self._intervention_breaker.remaining(),
                plugin_name,
                str(hook),
            )
            return
        _ok = False
        with guard("_write_plugin_intervention: from src.extensions.db.db_agent impo", log=_log, level=logging.WARNING):
            from src.extensions.db.db_agent import db_agent
            from src.core.config_loader import config
            import json as _json

            project = getattr(config, "project_name", "") or ""
            case_id = context.get("case_id", "") or ""
            intent_id = context.get("intent_id", "") or ""

            intervention_data = {
                "plugin": plugin_name,
                "hook": str(hook),
                "result": _json.dumps(result, ensure_ascii=False, default=str)[:2000],
            }
            db_agent.execute(
                "INSERT INTO plugin_interventions (project, case_id, intent_id, plugin_name, "
                "hook, intervention_data, created_at) VALUES (%s, %s, %s, %s, %s, %s, NOW())",
                (
                    project,
                    case_id,
                    intent_id,
                    plugin_name,
                    str(hook),
                    _json.dumps(intervention_data, ensure_ascii=False, default=str)[
                        :2000
                    ],
                ),
            )
            _ok = True
            _log.debug("[PluginManager] 干预已记录: %s.%s", plugin_name, str(hook))
        # 旁路审计写入失败不应拖慢主链路：连续失败即熔断，冷却期内直接跳过。
        if _ok:
            self._intervention_breaker.note_success()
        else:
            self._intervention_breaker.note_failure("db_agent 写入失败(可容忍)")

    def reset(self) -> None:
        """清空所有插件（主要用于测试）。"""
        self._plugins.clear()


# ★ 方案C 改造：plugin_manager 改为 PerProjectRegistry，按 project 隔离
#   原实现：全局单例，插件状态（FlakyGuard/ScorecardOracle 等）跨用户混在一起
from src.core.utils.per_project_registry import PerProjectRegistry

plugin_manager = PerProjectRegistry(PluginManager)
