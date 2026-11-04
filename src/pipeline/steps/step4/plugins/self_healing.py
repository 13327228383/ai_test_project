# -*- coding: utf-8 -*-
from __future__ import annotations
from src.core.guarded import guard

"""
自愈合元素定位插件
===================
当元素定位失败时，尝试通过多种策略自动找到替代元素。
不是 ML 学习，而是基于规则的多策略回退（比现有五级兜底更智能）。

介入点：BEFORE_ACTION（定位失败时）+ AFTER_ACTION（验证补丁）

★ 架构债清理(2026-07-25): 9 策略 + 关键词提取迁移至 _self_healing_strategies_mixin.py，
  7 个 AI/视觉辅助方法迁移至 _self_healing_ai_helpers_mixin.py。
  本文件仅保留插件核心：__init__ + on_before_action + on_after_action +
  _get_verified_patch + _persist_patch + get_stats。
"""

import logging
import contextvars
from typing import Any

from src.pipeline.steps.step4.plugins.plugin_manager import BasePlugin

# ★ 架构债清理(2026-07-25): mixin 多继承组装（§4.3 单文件≤800行）
from src.pipeline.steps.step4.plugins._self_healing_strategies_mixin import (
    _SelfHealingStrategiesMixin,
)
from src.pipeline.steps.step4.plugins._self_healing_ai_helpers_mixin import (
    _SelfHealingAiHelpersMixin,
)

_log = logging.getLogger(__name__)

# ★ contextvars：BEFORE_ACTION → AFTER_ACTION 状态传递，asyncio Task 间隔离
_last_heal_info: contextvars.ContextVar = contextvars.ContextVar(
    "sh_last_heal_info", default=None
)


class SelfHealingPlugin(
    _SelfHealingStrategiesMixin, _SelfHealingAiHelpersMixin, BasePlugin
):
    """自愈合元素定位插件。

    ★ 架构债清理(2026-07-25): 通过多继承组装两个 mixin：
      - _SelfHealingStrategiesMixin: 9 种元素定位策略 + 调度器 + 关键词提取
      - _SelfHealingAiHelpersMixin: AI 调用 + 视觉模型 + DOM 摘要 + 截图 + 失败补丁
    保留核心：__init__ / on_before_action / on_after_action / 补丁持久化 / get_stats。
    """

    name = "self_healing"
    enabled = True
    config = {}

    def __init__(self, config: dict[str, Any] = None):
        self.config = config or {}
        self.enabled = self.config.get("enabled", True)
        # 配置：回退策略优先级
        # ★ 原则：谁准谁在前、谁慢谁在后
        #   文本/结构规则（快）→ 视觉模型（慢）→ AI 兜底（最贵）
        self._strategies = self.config.get(
            "strategies",
            [
                "semantic_text",  # 语义文本匹配（快、命中率高）
                "css_simplify",  # ★ CSS 选择器简化：去掉状态类（-selected/-active等）
                "css_class",  # CSS 类名匹配（快、类名语义时较准）
                "role_attribute",  # ARIA role 匹配（快、标准语义时较准）
                "partial_text",  # 部分文本匹配（快、容错但精度下降）
                "nearby_element",  # 邻近元素推断（快、启发式）
                "type_fallback",  # ★ 类型兜底：输入框→第一个 input，按钮→第一个 button（快、避免 AI）
                "visual_similarity",  # 视觉相似度（慢、需多模态模型）
                "ai_locator",  # ★ AI 语义映射兜底（最慢、最贵）
            ],
        )
        self._max_attempts = self.config.get("max_attempts", 3)
        # ★ 配置化：元素类型/role 关键词映射，避免在代码里硬编码业务术语
        #   默认值保留当前行为，但可通过外部配置覆盖，符合通用系统约束
        # ★ v2.0 (2026-07-07): 增加中文关键词，让规则策略能命中通用语义 hint
        #   如"提交按钮"→button 类型、"查询输入框"→textbox 类型
        #   避免 6 种规则策略全失败后必然触发 AI 兜底（token 消耗高）
        self._type_hints = self.config.get(
            "type_hints",
            {
                "button": ["button", "btn", "submit", "按钮"],
                "input": ["input", "text", "field", "输入框", "文本框"],
                "select": ["select", "dropdown", "下拉", "选择框"],
                "checkbox": ["check", "checkbox", "复选框"],
                "textarea": ["textarea", "文本域"],
            },
        )
        self._role_map = self.config.get(
            "role_map",
            {
                "button": ["button", "submit", "按钮"],
                "textbox": ["text", "input", "输入框", "文本框"],
                "link": ["link", "链接"],
                "checkbox": ["check", "复选框"],
                "combobox": ["select", "dropdown", "下拉", "选择框"],
            },
        )
        # 统计（验证闭环）
        self._stats = {
            "heal_attempts": 0,
            "heal_success": 0,
            "heal_verify_success": 0,
            "heal_verify_fail": 0,
            "heal_patched": 0,
        }

    def on_before_action(self, ctx: dict[str, Any]) -> dict[str, Any] | None:
        """
        动作执行前，如果目标元素定位失败，尝试自愈合。

        ctx 包含:
            action: dict — 动作定义
            engine: PlaywrightEngine — 浏览器引擎
            page_state: dict — 当前页面状态（含元素列表）
            element_found: bool — 是否已找到目标元素
        """
        if not self.enabled:
            return None

        if ctx.get("element_found", True):
            _last_heal_info.set(None)  # ★ 清空上次状态
            return None  # 元素已找到，不需要自愈合

        action = ctx.get("action", {})
        page_state = ctx.get("page_state", {})
        # engine 仅视觉相似度策略需要，其他策略不依赖
        engine = ctx.get("engine")

        # ★ B3 单一性：统一读 target_hint
        target_hint = action.get("target_hint", "")
        ai_id = action.get("ai_id", "")

        # ★ 如果 target_hint 是 #ai-N 格式，提取 ai_id 供 nearby_element 策略使用
        if not ai_id and target_hint.startswith("#ai-"):
            ai_id = target_hint[1:]  # 去掉 # 前缀 → "ai-2"

        _log.info(
            "[SelfHealing] 元素定位失败，尝试自愈合: hint=%s, ai_id=%s",
            target_hint,
            ai_id,
        )
        self._stats["heal_attempts"] += 1

        # ★ 优先查 MySQL 已验证补丁（跨运行复用）
        cached_patch = self._get_verified_patch(target_hint, ai_id)
        if cached_patch:
            _log.info("[SelfHealing] ✓ 命中已验证补丁缓存: %s", cached_patch)
            self._stats["heal_success"] += 1
            _last_heal_info.set(
                {
                    "original_hint": target_hint,
                    "new_hint": cached_patch,
                    "strategy": "cached_patch",
                    "action": action,
                }
            )
            return {
                "self_healed": True,
                "self_heal_strategy": "cached_patch",
                "self_heal_original_hint": target_hint,
                "self_heal_new_hint": cached_patch,
                "action": {**action, "target_hint": cached_patch},
            }

        # ★ 对 #ai-N 失效场景：若 action 携带 reasoning，先尝试从 reasoning 提取关键词做文本匹配
        #   这是把 AI 循环内部的 reasoning 兜底统一到 SelfHealing 入口的关键一步
        if target_hint.startswith("#ai-") and action.get("reasoning"):
            _reasoning_kws = self._extract_reasoning_keywords(
                action.get("reasoning", ""), action.get("action", "")
            )
            for _kw in _reasoning_kws:
                _text_hint = f"text={_kw}"
                _healed = self._strategy_semantic_text(
                    _text_hint, page_state.get("elements", [])
                )
                if not _healed:
                    _healed = self._strategy_partial_text(
                        _text_hint, page_state.get("elements", [])
                    )
                if _healed:
                    new_hint = _healed.get("new_hint", "?")
                    _log.info(
                        "[SelfHealing] ✓ reasoning 关键词命中: '%s' → %s", _kw, new_hint
                    )
                    self._stats["heal_success"] += 1
                    _last_heal_info.set(
                        {
                            "original_hint": target_hint,
                            "new_hint": new_hint,
                            "strategy": "reasoning_text",
                            "action": action,
                        }
                    )
                    return {
                        "self_healed": True,
                        "self_heal_strategy": "reasoning_text",
                        "self_heal_original_hint": target_hint,
                        "self_heal_new_hint": new_hint,
                        "action": {**action, "target_hint": new_hint},
                    }

        # 尝试各种回退策略
        attempted = []
        for strategy in self._strategies[: self._max_attempts]:
            attempted.append(strategy)
            try:
                healed = self._try_strategy(
                    strategy, target_hint, ai_id, page_state, engine
                )
                if healed:
                    new_hint = healed.get("new_hint", "?")
                    _log.info("[SelfHealing] ✓ 策略 '%s' 成功: %s", strategy, new_hint)
                    self._stats["heal_success"] += 1
                    _last_heal_info.set(
                        {
                            "original_hint": target_hint,
                            "new_hint": new_hint,
                            "strategy": strategy,
                            "action": action,
                        }
                    )
                    return {
                        "self_healed": True,
                        "self_heal_strategy": strategy,
                        "self_heal_original_hint": target_hint,
                        "self_heal_new_hint": new_hint,
                        "action": {**action, "target_hint": new_hint},
                    }
                else:
                    _log.debug("[SelfHealing] 策略 '%s' 无匹配，跳过", strategy)
            except Exception as e:
                _log.debug("[SelfHealing] 策略 '%s' 失败: %s", strategy, str(e)[:80])
                continue

        _log.warning(
            "[SelfHealing] ✗ 所有策略均失败，放弃自愈合 (尝试了 %d 个策略: %s)",
            len(attempted),
            ", ".join(attempted),
        )
        _last_heal_info.set(None)
        return {"self_healed": False}

    def on_after_action(self, ctx: dict[str, Any]) -> dict[str, Any] | None:
        """
        动作执行后验证：如果刚进行了自愈合修复，验证修复是否真正生效。

        ctx 包含:
            action: dict — 动作定义
            result: bool — 动作执行结果
            before_state: dict — 动作前 DOM 状态
            after_state: dict — 动作后 DOM 状态
            engine: PlaywrightEngine — 浏览器引擎

        返回:
            验证结果 dict (ground_truth_passed 等)，不干预主流程。
            验证通过 → 持久化补丁到 MySQL。
        """
        heal_info = _last_heal_info.get()
        _last_heal_info.set(None)  # ★ 消耗一次

        if not heal_info:
            return None

        result_ok = ctx.get("result", False)
        action = ctx.get("action", heal_info.get("action", {}))

        # 验证逻辑：动作执行成功 + 页面发生了可见变化（非空操作）
        verified = False
        verify_detail = ""

        if not result_ok:
            verify_detail = "动作执行失败"
        else:
            # 检查页面是否真的变了（防止元素存在但不可交互的情况）
            before_state = ctx.get("before_state", {}) or {}
            after_state = ctx.get("after_state", {}) or {}
            before_count = (
                before_state.get("element_count", 0)
                if isinstance(before_state, dict)
                else 0
            )
            after_count = (
                after_state.get("element_count", 0)
                if isinstance(after_state, dict)
                else 0
            )
            before_sig = (
                before_state.get("dom_signature", "")
                if isinstance(before_state, dict)
                else ""
            )
            after_sig = (
                after_state.get("dom_signature", "")
                if isinstance(after_state, dict)
                else ""
            )
            before_url = (
                before_state.get("url", "") if isinstance(before_state, dict) else ""
            )
            after_url = (
                after_state.get("url", "") if isinstance(after_state, dict) else ""
            )
            before_text = (
                before_state.get("text_len", 0) if isinstance(before_state, dict) else 0
            )
            after_text = (
                after_state.get("text_len", 0) if isinstance(after_state, dict) else 0
            )

            if before_sig and after_sig and before_sig != after_sig:
                # DOM 结构签名发生变化（切换 tab、展开折叠、弹窗等）
                verified = True
                verify_detail = (
                    f"DOM 签名发生变化（元素数 {before_count}→{after_count}）"
                )
            elif before_url and after_url and before_url != after_url:
                # 页面导航
                verified = True
                verify_detail = f"页面 URL 发生变化（{before_url}→{after_url}）"
            elif before_count == 0 and after_count == 0:
                # DOM 签名不可用，信任动作执行结果
                verified = True
                verify_detail = "动作执行成功（DOM签名不可用）"
            elif before_text != after_text:
                # 页面文本内容变化（如 fill 输入、提示信息出现）
                verified = True
                verify_detail = f"页面文本发生变化（{before_text}→{after_text} 字符）"
            elif after_count > 0:
                # 兜底：页面非空，信任执行结果
                verified = True
                verify_detail = f"动作执行成功（元素数 {before_count}→{after_count}）"
            else:
                verified = False
                verify_detail = "动作返回成功但页面无可见变化"

        # ★ 提取 case_id（用于补丁精准匹配）
        case_id = (heal_info.get("action", {}) or {}).get("ai_id", "")
        # ★ 提取当前页面 URL（用于补丁溯源）
        page_url = ""
        try:
            engine = ctx.get("engine")
            if engine and hasattr(engine, "_page") and engine._page:
                page_url = (engine._page.url or "")[:1024]
        except Exception as _e:
            _log.debug("[self_healing] 非关键异常: %s", _e, exc_info=True)
        if verified:
            self._stats["heal_verify_success"] += 1
            # ★ 持久化到 MySQL，后续运行直接命中
            _ok = self._persist_patch(
                original_hint=heal_info["original_hint"],
                healed_hint=heal_info["new_hint"],
                strategy=heal_info["strategy"],
                verified=True,
                verify_detail=verify_detail,
                case_id=case_id,
                page_url=page_url,
            )
            self._stats["heal_patched"] += 1
            if _ok:
                _log.info(
                    "[SelfHealing] ✓ 验证通过并持久化: %s → %s (%s)",
                    heal_info["original_hint"][:40],
                    heal_info["new_hint"][:40],
                    heal_info["strategy"],
                )
            else:
                _log.warning(
                    "[SelfHealing] 验证通过但持久化失败: %s → %s (%s)",
                    heal_info["original_hint"][:40],
                    heal_info["new_hint"][:40],
                    heal_info["strategy"],
                )
        else:
            self._stats["heal_verify_fail"] += 1
            # ★ 也记录失败补丁（避免重复尝试同样策略）
            self._persist_patch(
                original_hint=heal_info["original_hint"],
                healed_hint=heal_info["new_hint"],
                strategy=heal_info["strategy"],
                verified=False,
                verify_detail=verify_detail,
                case_id=case_id,
                page_url=page_url,
            )
            _log.warning(
                "[SelfHealing] ✗ 验证失败: %s → %s, 原因=%s",
                heal_info["original_hint"][:40],
                heal_info["new_hint"][:40],
                verify_detail,
            )

        return None  # 不干预主流程（与 GroundTruth 不同）

    def _get_verified_patch(self, target_hint: str, ai_id: str) -> str | None:
        """从 MySQL 查询已验证的自愈补丁。"""
        with guard("_get_verified_patch: from src.core.db_manager import get_", log=_log):
            from src.core.db_manager import get_connection, release_connection
            from src.core.config_loader import config as _cfg

            project = getattr(_cfg, "project_name", "") or ""
            conn = get_connection()
            try:
                row = conn.execute(
                    "SELECT healed_hint FROM self_heal_patches "
                    "WHERE project=%s AND original_hint=%s AND case_id=%s AND verified=1 "
                    "ORDER BY updated_at DESC LIMIT 1",
                    (project, target_hint, ai_id or ""),
                ).fetchone()
                if row:
                    return row["healed_hint"]
            finally:
                release_connection(conn)
        return None

    def _persist_patch(
        self,
        original_hint: str,
        healed_hint: str,
        strategy: str,
        verified: bool,
        verify_detail: str,
        case_id: str = "",
        page_url: str = "",
    ) -> bool:
        """持久化自愈补丁到 MySQL。成功返回 True，失败返回 False。"""
        try:
            from src.core.db_manager import get_connection, release_connection
            from src.core.config_loader import config as _cfg

            project = getattr(_cfg, "project_name", "") or ""
            version_id = 0
            try:
                from src.users.project_auth import get_active_version

                _av = get_active_version(project)
                if _av:
                    version_id = _av.get("id", 0) or 0
            except Exception as _e:
                _log.debug("[self_healing] _persist_patch 异常: %s", _e, exc_info=True)
            conn = get_connection()
            try:
                conn.execute(
                    "INSERT INTO self_heal_patches "
                    "(project, version_id, case_id, original_hint, healed_hint, strategy, "
                    "verified, verify_at, verify_detail, page_url) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, NOW(), %s, %s)",
                    (
                        project,
                        version_id,
                        case_id or "",
                        original_hint or "",
                        healed_hint or "",
                        strategy,
                        1 if verified else 2,
                        verify_detail,
                        page_url or "",
                    ),
                )
                conn.commit()
                return True
            finally:
                release_connection(conn)
        except Exception as e:
            _log.error("[SelfHealing] 补丁持久化失败: %s", e, exc_info=True)
            return False

    def get_stats(self) -> dict[str, Any]:
        """获取自愈统计（含验证闭环指标）。"""
        return dict(self._stats)
