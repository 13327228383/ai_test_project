# -*- coding: utf-8 -*-
"""P2-10 AgentExplorer — Agent 驱动页面探索器（对标 BrowserGym/WebVoyager）。

三拍：explore(覆盖决策) / replay(缓存重放) / repair(失败修复)。
通用性：tag/role 语义驱动，不绑定业务；跨系统/行业/页面适用。

v8 重构(2026-07-28): 拆分 mixin 架构，每个文件 ≤ 800 行。
  - ElementMixin: 元素发现、快照、匹配
  - SidebarMixin: 侧边栏/NavMap/Tab 探索
  - LLMMixin: LLM 驱动的多步探索闭环
  - ActionMixin: 动作执行（触发点击、删除、通用点击、搜索）
  - SemanticMixin: LLM 语义匹配与断言
  - element_utils: 模块级工具函数
"""
from __future__ import annotations
from src.core.guarded import guard

import logging
from typing import Any, Optional

from src.agent.coverage_crawl import CoverageMap, feedback
from src.agent.explorer_mixins import (
    ElementMixin,
    SidebarMixin,
    LLMMixin,
    ActionMixin,
    SemanticMixin,
)
from src.agent.element_utils import (
    get_page_snapshot,
    extract_keywords,
    clean_operation,
    classify_operation,
    compute_intent_hash,
    match_keywords_to_elements,
    click_button,
)

_log = logging.getLogger(__name__)

# 元素优先级：button > a > input > select > textarea > 其他
_TAG_PRIORITY: dict[str, int] = {
    "button": 5,
    "a": 4,
    "input": 3,
    "select": 2,
    "textarea": 1,
}

_REPAIR_STRATEGIES = frozenset({"retry", "fallback_selector", "skip", "mark_dead"})


class AgentExplorer(ElementMixin, SidebarMixin, LLMMixin, ActionMixin, SemanticMixin):
    """Agent 驱动页面探索器。

    通过多重继承组合所有探索能力：
      ElementMixin  → 元素发现、快照、匹配
      SidebarMixin  → 侧边栏/NavMap/Tab 探索
      LLMMixin      → LLM 多步探索闭环
      ActionMixin   → 动作执行
      SemanticMixin → 语义匹配与断言
    """

    def __init__(self):
        self._last_error: str = ""

    # ── explore: 决策下一步探索动作 ──

    def explore(
        self,
        coverage_map: CoverageMap,
        current_page_url: str,
        dom_elements: Optional[list[dict]] = None,
    ) -> Optional[dict[str, Any]]:
        """决策下一步探索动作（P2-9 CoverageCrawl 接口）。"""
        try:
            self._last_error = ""
            targets = feedback(coverage_map, max_targets=100)
            if not targets:
                return None
            _url = (current_page_url or "").strip()
            _current = [t for t in targets if t.get("page_url", "") == _url]
            candidates = _current if _current else targets
            if not candidates:
                return None
            candidates.sort(
                key=lambda t: _TAG_PRIORITY.get(t.get("tag", ""), 0), reverse=True
            )
            chosen = candidates[0]
            tag = (chosen.get("tag") or "").lower()
            _sel = chosen.get("selector", "")
            if not _sel:
                return None
            _type = (
                "fill"
                if (
                    tag in ("input", "textarea")
                    and (_sel.startswith("text=") or _sel.startswith("placeholder="))
                )
                else "click"
            )
            return {
                "type": _type,
                "target_hint": _sel,
                "selector": _sel,
                "tag": tag,
                "text": chosen.get("text", ""),
                "role": chosen.get("role", ""),
                "page_url": chosen.get("page_url", ""),
                "reason": "explore uncovered: %s" % tag,
            }
        except Exception as e:
            self._last_error = str(e)[:200]
            _log.warning("[AgentExplorer] explore 失败: %s", str(e)[:120])
            return None

    # ── replay: 查询 op_cache_v2 ──

    def replay(
        self,
        project: str,
        version_id: int,
        intent_hash: str,
    ) -> Optional[dict[str, Any]]:
        """查询 op_cache_v2 获取可重放的动作序列。"""
        try:
            self._last_error = ""
            from src.pipeline.steps.step4.op_cache.v2_store import get_v2_store

            store = get_v2_store()
            cached = store.get(project, version_id, intent_hash)
            if not cached or not cached.get("action_sequence"):
                return {
                    "hit": False,
                    "action_sequence": [],
                    "page_fingerprint": "",
                    "hit_count": 0,
                }
            store.increment_hit(project, version_id, intent_hash)
            return {
                "hit": True,
                "action_sequence": cached["action_sequence"],
                "page_fingerprint": cached.get("page_fingerprint", ""),
                "hit_count": cached.get("hit_count", 0) + 1,
            }
        except Exception as e:
            self._last_error = str(e)[:200]
            _log.warning("[AgentExplorer] replay 失败: %s", str(e)[:120])
            return None

    # ── repair: 决策修复策略 ──

    def repair(
        self,
        failed_action: dict[str, Any],
        dom_elements: list[dict],
        coverage_map: CoverageMap,
    ) -> dict[str, Any]:
        """决策修复策略（不执行 AI）。

        策略：
          1. 分析失败原因（元素不存在/页面变化）
          2. 在 DOM 中查找相似元素（文本/placeholder 模糊匹配）
          3. 选择修复策略：fallback_selector / retry / mark_dead / skip
        """
        try:
            self._last_error = ""
            selector = (
                failed_action.get("selector") or failed_action.get("target_hint") or ""
            ).strip()
            if not selector:
                return {
                    "strategy": "skip",
                    "new_action": None,
                    "reason": "empty selector",
                }

            similar = self._find_similar_element(selector, dom_elements)
            if similar:
                new_action = dict(failed_action)
                new_selector = similar.get("selector", "")
                if new_selector and new_selector != selector:
                    new_action["selector"] = new_selector
                    new_action["target_hint"] = new_selector
                    new_action["reason"] = "fallback to similar element"
                    return {
                        "strategy": "fallback_selector",
                        "new_action": new_action,
                        "reason": "found similar element: %s" % new_selector[:50],
                    }

            if self._element_exists(selector, dom_elements):
                return {
                    "strategy": "retry",
                    "new_action": failed_action,
                    "reason": "element exists, retry click",
                }

            page_url = failed_action.get("page_url", "")
            if page_url and coverage_map:
                coverage_map.mark_exercised(page_url, selector, "/dead")
            return {
                "strategy": "mark_dead",
                "new_action": None,
                "reason": "element not found and no similar, mark as dead",
            }

        except Exception as e:
            self._last_error = str(e)[:200]
            _log.warning("[AgentExplorer] repair 失败: %s", str(e)[:120])
            return {
                "strategy": "skip",
                "new_action": None,
                "reason": "repair error: %s" % str(e)[:80],
            }

    def _find_similar_element(
        self,
        selector: str,
        dom_elements: list[dict],
    ) -> Optional[dict[str, str]]:
        """在 DOM 中查找相似元素（文本/placeholder 模糊匹配）。"""
        if not selector or not dom_elements:
            return None

        keyword = ""
        for prefix in ("text=", "placeholder=", "ariaLabel=", "name="):
            if selector.startswith(prefix):
                keyword = selector[len(prefix) :].strip()
                break
        if not keyword:
            return None

        keyword_lower = keyword.lower()
        for el in dom_elements:
            if not isinstance(el, dict):
                continue
            for field in ("text", "placeholder", "ariaLabel", "name"):
                val = (el.get(field) or "").strip()
                if not val:
                    continue
                val_lower = val.lower()
                if keyword_lower in val_lower or val_lower in keyword_lower:
                    if field == "text":
                        return {"selector": "text=%s" % val, "tag": el.get("tag", "")}
                    if field == "placeholder":
                        return {
                            "selector": "placeholder=%s" % val,
                            "tag": el.get("tag", ""),
                        }
                    if field == "ariaLabel":
                        return {
                            "selector": "ariaLabel=%s" % val,
                            "tag": el.get("tag", ""),
                        }
                    if field == "name":
                        return {"selector": "name=%s" % val, "tag": el.get("tag", "")}
        return None

    def _element_exists(self, selector: str, dom_elements: list[dict]) -> bool:
        """检查元素是否在 DOM 中存在（精确匹配）。"""
        if not selector or not dom_elements:
            return False
        for el in dom_elements:
            if not isinstance(el, dict):
                continue
            for prefix, field in [
                ("text=", "text"),
                ("placeholder=", "placeholder"),
                ("ariaLabel=", "ariaLabel"),
                ("name=", "name"),
            ]:
                if selector.startswith(prefix):
                    val = (el.get(field) or "").strip()
                    if val and selector == "%s%s" % (prefix, val):
                        return True
        return False

    # ── execute_intent: 实时探索执行 ──

    async def execute_intent(
        self,
        page,
        task: dict[str, Any],
        timeout_ms: int = 30000,
        project: str = "",
        version_id: int = 0,
        skip_replay: bool = False,
    ) -> dict[str, Any]:
        """浏览器中实时探索执行（对标 BrowserGym/WebVoyager）。

        三拍：replay(查缓存)→explore(实时匹配)→repair(失败修复)。
        操作类型自动识别：搜索/查询→fill+click, 新建/编辑→触发按钮+验证弹窗,
        删除→确认弹窗, 导入/上传→文件上传, 其他→通用点击。

        ★ 通用性修复：skip_replay=True（权威需求）→ 跳过策略1 replay，
          强制实时执行。权威需求（req_id + is_implicit=0）必须每次真实执行业务动作
          并验证 → PASS/FAIL，绝不允许缓存重放跳过"成功后断言"掩盖需求满足性。
          仅非权威/隐含/AI 虚构需求允许 replay 优化。
        """
        try:
            self._last_error = ""
            operation = (task.get("operation") or "").strip()
            target_page = (task.get("target_page") or "").strip()
            desc = (task.get("description") or operation or "").strip()
            expected = (task.get("expected_feedback") or "").strip()

            if not desc:
                return {
                    "success": False,
                    "evidence": "",
                    "actions_taken": [],
                    "strategy": "fallback",
                    "error": "空任务描述，无法探索",
                }
            # ★ 根因修复(2026-09-25) RC-AC: 前置门禁原判据是 ``not operation``，
            #   但函数真正的前置条件是"存在可执行的语义任务"，即 ``desc``
            #   （= description or operation）。实测 TC-REQ-067-N01/B01（检索能力
            #   的边界/异常变体）规则生成端给出了完整 description 与 4 条
            #   case_actions + 2 个验证点，却因 operation 为空被**过早硬失败**
            #   （"空任务描述，无法探索"）→ 假 FAIL，而 guided 分支只把 operation
            #   当语义透传、并不依赖它。
            #   通用修复: 门禁落在语义任务文本上；guided 执行不因 operation 缺失而阻断。
            #   操作文本缺失时以需求正文兜底，保证下游语义判定（极性/控件意图）仍有输入。
            if not operation:
                operation = desc

            _log.info(
                "[AgentExplorer.execute_intent] 开始探索: op=%r target=%s",
                operation[:80],
                target_page[:80],
            )

            # ── 策略1: replay — 查 op_cache_v2 ──
            #   ★ 通用性修复：权威需求（skip_replay=True）跳过 replay，
            #     强制实时执行，保证需求每次被真实验证（PASS/FAIL），绝不允许
            #     缓存重放跳过验证造成 SKIP(REPLAY_NO_ACTION)。
            _intent_hash = compute_intent_hash(operation, target_page)
            if not skip_replay:
                _cached = self.replay(project, version_id, _intent_hash)
                if _cached and _cached.get("hit"):
                    _cached_actions = _cached.get("action_sequence") or []
                    # ★ 2026-08-28 防假PASS(fail-closed): replay 命中但缓存动作序列
                    #   无任何业务动作(fill/click/submit等) → 不信赖缓存，落实时执行。
                    #   根因: 假PASS 的 guided 执行结果也会被缓存(evidence"验证通过:
                    #     页面显示X相关功能"但操作从未执行)，replay 直接 success=True
                    #     会持续假PASS且无任何验证。
                    #   通用性: 跨系统/行业 — 缓存重放必须保证"动作序列本身可证伪"。
                    _cached_biz = [
                        a for a in _cached_actions
                        if a.get("type") in (
                            "fill", "click", "submit", "select_option", "press_key",
                            "clear", "double_click", "set_date", "check", "uncheck",
                            "upload_file",
                        )
                    ]
                    if _cached_actions and _cached_biz:
                        _log.info(
                            "[AgentExplorer.execute_intent] replay命中: hit_count=%d actions=%d biz=%d",
                            _cached.get("hit_count", 0),
                            len(_cached_actions),
                            len(_cached_biz),
                        )
                        return {
                            "success": True,
                            "evidence": "op_cache_v2 replay命中",
                            "actions_taken": _cached_actions,
                            "strategy": "replay",
                            "error": "",
                        }
                    _log.warning(
                        "[AgentExplorer.execute_intent] replay缓存不可信(%d条动作, %d条业务动作)，"
                        "转实时执行防假PASS",
                        len(_cached_actions),
                        len(_cached_biz),
                    )
            else:
                _log.info(
                    "[AgentExplorer.execute_intent] 权威需求 skip_replay=True → 跳过缓存重放，实时执行"
                )

            # ★ v5.4: 策略1.5 — guided: 按_execution_guide执行（替代自由探索）
            _guide = task.get("_execution_guide") or {}
            if _guide.get("mode") == "guided":
                _log.info(
                    "[AgentExplorer.execute_intent] 使用guided模式: verifications=%d",
                    len(_guide.get("verifications", [])),
                )
                _inputs = _guide.get("inputs") or {}
                _verifications = _guide.get("verifications") or []
                _guided_result = await self._execute_guided(
                    page,
                    _inputs,
                    _verifications,
                    operation,
                    expected,
                    timeout_ms,
                    op_type=str(_guide.get("op_type") or ""),
                    # ★ 根因修复(2026-09-25): description 必须透传。
                    #   根因: _execute_guided 早已声明 description 形参（供 RC-T/RC-U 做
                    #     需求级语义判定），但调用点从未传值 → 形参恒为 "" →
                    #     RC-T 的「服务端校验型」判据（读 description）永不命中 →
                    #     表单不补全 → 请求被客户端校验拦截 → 被测的服务端拒绝路径
                    #     不可达 → 断言恒 FAIL（实测 TC-REQ-010-I01）。
                    #   通用性: 需求正文是需求级语义的权威来源，跨系统/行业一致。
                    description=desc,
                    # ★ 用例声明的权威动作序列（同源透传，执行端只读不推）。
                    declared_actions=_guide.get("declared_actions") or [],
                    # ★ 用例是否声明了结构化断言（权威 oracle）。同源透传：
                    #   执行端据此在弱 oracle 不可判定时降级为非判定态，交 checks 裁决。
                    has_structured_oracle=bool(_guide.get("structured_checks")),
                    # ★ 拒绝型极性（上游 Step3 权威判定，执行端只读不推）。
                    expects_denial=bool(_guide.get("expects_denial")),
                )
                # ★ v5.4 R3: guided成功时缓存到op_cache_v2
                if _guided_result.get("success") and project and _intent_hash:
                    with guard("execute_intent: from src.pipeline.steps.step4.op_cac", log=_log, level=logging.WARNING):
                        from src.pipeline.steps.step4.op_cache.v2_store import (
                            get_v2_store,
                        )

                        _store = get_v2_store()
                        _store.set(
                            project,
                            version_id,
                            _intent_hash,
                            "",  # page_fingerprint 留空，guided模式不依赖页面指纹
                            _guided_result.get("actions_taken", []),
                        )
                        _log.info(
                            "[AgentExplorer] guided结果缓存到op_cache_v2: hash=%s",
                            _intent_hash[:12],
                        )
                return _guided_result

            # ── 策略2: explore — 实时探索（操作类型感知）──
            _op_type = classify_operation(operation)
            _log.info(
                "[AgentExplorer.execute_intent] 操作类型: %s (op=%r)",
                _op_type,
                operation[:60],
            )

            _clean_op = clean_operation(operation)
            _log.info(
                "[AgentExplorer.execute_intent] 清洗后operation: %r (原: %r)",
                _clean_op[:60],
                operation[:60],
            )
            _keywords = extract_keywords(_clean_op)
            _log.info("[AgentExplorer.execute_intent] 关键词: %s", _keywords[:5])

            _dom_buttons, _dom_inputs = await get_page_snapshot(page)

            if not _dom_buttons and not _dom_inputs:
                return {
                    "success": False,
                    "evidence": "页面无可交互元素",
                    "actions_taken": [],
                    "strategy": "explore",
                    "error": "页面快照为空，无按钮和输入框",
                }

            _log.info(
                "[AgentExplorer] DEBUG has_trigger=%s has_search=%s op_type=%s",
                hasattr(self, "_execute_trigger_click"),
                hasattr(self, "_execute_search"),
                _op_type,
            )
            # ★ Fix(2026-07-29): 传递 target_page 给执行方法，防止SidebarExplore从正确页面导航离开
            #   根因: SidebarExplore在target_page正确时仍导航到侧边栏菜单项，导致Agent离开正确页面
            if _op_type == "search":
                return await self._execute_search(
                    page,
                    _keywords,
                    _dom_inputs,
                    _dom_buttons,
                    operation,
                    expected,
                    timeout_ms,
                )
            elif _op_type == "create":
                return await self._execute_trigger_click(
                    page,
                    _keywords,
                    _dom_buttons,
                    operation,
                    expected,
                    timeout_ms,
                    "create",
                    target_page,
                )
            elif _op_type == "edit":
                return await self._execute_trigger_click(
                    page,
                    _keywords,
                    _dom_buttons,
                    operation,
                    expected,
                    timeout_ms,
                    "edit",
                    target_page,
                )
            elif _op_type == "delete":
                return await self._execute_delete(
                    page,
                    _keywords,
                    _dom_buttons,
                    operation,
                    expected,
                    timeout_ms,
                )
            elif _op_type == "import_export":
                return await self._execute_trigger_click(
                    page,
                    _keywords,
                    _dom_buttons,
                    operation,
                    expected,
                    timeout_ms,
                    "import_export",
                    target_page,
                )
            else:
                return await self._execute_generic_click(
                    page,
                    _keywords,
                    _dom_buttons,
                    operation,
                    expected,
                    timeout_ms,
                    target_page,
                )

        except Exception as _e:
            self._last_error = str(_e)[:200]
            _log.warning("[AgentExplorer.execute_intent] 异常: %s", str(_e)[:120])
            return {
                "success": False,
                "evidence": "",
                "actions_taken": [],
                "strategy": "fallback",
                "error": f"探索异常: {str(_e)[:120]}",
            }


# ── 单例（惰性初始化） ──

_default_explorer: Optional[AgentExplorer] = None


def get_explorer() -> AgentExplorer:
    """获取默认 AgentExplorer 实例（单例，惰性初始化）。"""
    global _default_explorer
    if _default_explorer is None:
        _default_explorer = AgentExplorer()
    return _default_explorer
