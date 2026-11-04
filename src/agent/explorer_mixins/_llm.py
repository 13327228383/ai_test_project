# -*- coding: utf-8 -*-
"""P2-10 LLMMixin — LLM驱动的多步探索闭环（~350行）。

对标 BrowserGym/WebVoyager：LLM是大脑，Playwright是手。
通用性：LLM理解语义，不依赖硬编码路径。

[已复核-DeepSeek-V3 2026-08-16]
B3.2 JSON 解析收敛：``_extract_json_from_text`` 的策略1/策略2（正则抠花括号 +
``json.loads``）已改为转发 ``src.ai.json_parser.parse_json_response``（唯一真源）；
策略3（action/target/found 正则兜底）属本模块 LLM 决策协议语义，保留本地。
返回契约 ``dict | None`` 未变。**行为有 3 处正向变化**（嵌套 JSON 不再误取内层对象、
截断 JSON 可被修复、尾逗号 JSON 可被解析），详见 T01 交付说明。
"""
from __future__ import annotations
from src.core.guarded import guard

import logging
from typing import Any

from src.agent.element_utils import (
    get_page_snapshot,
    click_button,
    get_page_snapshot_text,
    clean_operation,
)

_log = logging.getLogger(__name__)


def _extract_json_from_text(text: str) -> dict | None:
    """从LLM输出文本中提取JSON对象（**通用解析部分已转发到唯一真源**）。

    [B3.2 收敛] 原策略1/策略2（正则抠 ``{...}`` + ``json.loads``）能力已被
    ``src.ai.json_parser.parse_json_response`` 完全覆盖且更强（支持 ```json 围栏、
    截断修复、单引号/尾逗号修复等）。此处只做**调用收敛**，不改业务语义：

    - 通用 JSON 提取 → 转发唯一真源；
    - 策略3（action/target/found 正则兜底）属于本模块 LLM 决策协议的业务语义，
      唯一真源不应承载，故**保留在本地**；
    - 返回值契约不变：仍为 ``dict | None``（真源可能返回 list，此处取首个 dict）。
    """
    import re as _re

    if not text or not isinstance(text, str):
        return None

    # 策略1+2（已收敛）: 委托唯一真源做通用 JSON 提取与修复
    from src.ai.json_parser import parse_json_response as _canonical

    _parsed = _canonical(text)
    if _parsed.get("success"):
        _data = _parsed.get("data")
        if isinstance(_data, dict):
            return _data
        if isinstance(_data, list):
            for _item in _data:
                if isinstance(_item, dict):
                    return _item
    else:
        _log.debug("JSON解析失败: %s", str(_parsed.get("error", ""))[:120])

    # 策略3: 尝试从文本中提取 action/target 键值对
    _result = {}
    _action_match = _re.search(r'"action"\s*:\s*"([^"]*)"', text)
    _target_match = _re.search(r'"target"\s*:\s*"([^"]*)"', text)
    _found_match = _re.search(r'"found"\s*:\s*(true|false)', text)
    if _action_match:
        _result["action"] = _action_match.group(1)
    if _target_match:
        _result["target"] = _target_match.group(1)
    if _found_match:
        _result["found"] = _found_match.group(1) == "true"
    if _result:
        _result.setdefault("reasoning", "")
        _result.setdefault("evidence", "")
        _result.setdefault("value", "")
        return _result

    return None


class LLMMixin:
    """LLM驱动的多步探索能力。

    提供：LLM探索闭环、页面描述构建、LLM决策、LLM动作执行。
    感知→推理→行动→观察 四步循环。
    """

    async def _llm_driven_explore(
        self,
        page,
        operation: str,
        keywords: list[str],
        expected: str,
        timeout_ms: int,
        max_steps: int = 8,
    ) -> dict[str, Any] | None:
        """LLM驱动的多步探索闭环（BrowserGym/WebVoyager范式）。

        核心理念：LLM是大脑，Playwright是手。每步感知→推理→行动→观察。
        替代当前线性搜索（sidebar→nav_map→LLM），解决"找不到按钮"的根因。

        通用性：LLM理解语义（"数据导入"关联"物料管理→辅材"），不依赖硬编码路径。
        跨框架/跨语言/跨行业——只要LLM能理解页面结构，就能找到目标按钮。
        """
        import asyncio, json as _json

        _clean_op = clean_operation(operation)
        _current_url = page.url
        _explored_urls = {_current_url}
        _actions_taken = []
        _last_snapshot = ""

        _snapshot_text = await self._build_page_description(page)
        _log.info(
            "[LLM-Explore] 开始多步探索: op=%r steps≤%d current_url=%s",
            _clean_op[:60],
            max_steps,
            _current_url[:60],
        )

        for _step in range(max_steps):
            _log.info(
                "[LLM-Explore] Step %d/%d: url=%s", _step + 1, max_steps, page.url[:60]
            )

            # ── 感知：获取当前页面完整状态 ──
            _snapshot_text = await self._build_page_description(page)
            if not _snapshot_text:
                _log.warning("[LLM-Explore] Step %d: 页面快照为空", _step + 1)
                break

            # ── 推理：LLM决定下一步动作 ──
            _decision = await self._llm_decide_next_action(
                operation=_clean_op,
                expected=expected,
                page_description=_snapshot_text,
                actions_history=_actions_taken,
                step_num=_step + 1,
                max_steps=max_steps,
            )

            if not _decision:
                _log.warning("[LLM-Explore] Step %d: LLM决策失败", _step + 1)
                break

            _action_type = _decision.get("action", "")
            _target = _decision.get("target", "")
            _reasoning = _decision.get("reasoning", "")
            _log.info(
                "[LLM-Explore] Step %d 决策: action=%s target=%r reasoning=%s",
                _step + 1,
                _action_type,
                _target[:40],
                _reasoning[:60],
            )

            # 检查是否达成目标
            if _action_type == "done":
                _found = _decision.get("found", False)
                _evidence = _decision.get("evidence", "")
                if _found:
                    _log.info("[LLM-Explore] ✅ LLM判定目标达成: %s", _evidence[:100])
                    return {
                        "success": True,
                        "evidence": _evidence,
                        "actions_taken": _actions_taken,
                        "strategy": "llm_explore",
                        "error": "",
                        "_llm_steps": _step + 1,
                    }
                else:
                    _log.info("[LLM-Explore] ❌ LLM判定无法达成: %s", _evidence[:100])
                    break

            if _action_type == "not_found":
                _log.info(
                    "[LLM-Explore] ❌ LLM判定目标不存在: %s",
                    _decision.get("evidence", "")[:100],
                )
                break

            # ── 行动：执行LLM决定的动作 ──
            _exec_result = await self._execute_llm_action(
                page,
                _action_type,
                _target,
                _decision,
                timeout_ms,
            )

            _actions_taken.append(
                {
                    "type": _action_type,
                    "target": _target,
                    "step": _step + 1,
                    "reasoning": _reasoning,
                    "result": _exec_result.get("status", "unknown"),
                }
            )

            # ── 观察：检查结果 ──
            if _exec_result.get("status") == "url_changed":
                _new_url = page.url
                if _new_url not in _explored_urls:
                    _explored_urls.add(_new_url)
                    _log.info(
                        "[LLM-Explore] Step %d: 导航到新页面 %s",
                        _step + 1,
                        _new_url[:60],
                    )
                await asyncio.sleep(1.0)
                continue

            if _exec_result.get("status") == "element_clicked":
                await asyncio.sleep(1.0)
                _new_snapshot = await self._build_page_description(page)
                if _new_snapshot != _snapshot_text:
                    _last_snapshot = _new_snapshot
                continue

            if _exec_result.get("status") == "failed":
                _log.warning(
                    "[LLM-Explore] Step %d: 动作执行失败: %s",
                    _step + 1,
                    _exec_result.get("error", "")[:80],
                )
                continue

            await asyncio.sleep(0.8)

        _log.info(
            "[LLM-Explore] ❌ %d步后未找到目标按钮: %s", max_steps, _clean_op[:60]
        )
        return None

    async def _build_page_description(self, page) -> str:
        """构建页面描述文本（供LLM决策使用）。

        包含：URL、页面标题、侧边栏菜单项、所有可交互元素（按钮/链接/输入框）、Tab列表。
        通用性：只用原生HTML+ARIA，不依赖框架特定类名。
        """
        try:
            _url = page.url or ""
            _title = await page.title() or ""
            _parts = [f"URL: {_url}", f"Title: {_title}"]

            # 侧边栏菜单项（导航菜单）
            _menu_items = []
            for _sel in [
                "[role='menuitem']",
                "[role='treeitem']",
                "nav a",
                "nav li",
                "[role='navigation'] a",
                "[role='navigation'] li",
            ]:
                with guard("_build_page_description: _locs = page.locator(_sel)", log=_log):
                    _locs = page.locator(_sel)
                    for _i in range(min(await _locs.count(), 30)):
                        _txt = (await _locs.nth(_i).text_content() or "").strip()
                        if _txt and len(_txt) <= 50 and _txt not in _menu_items:
                            _menu_items.append(_txt)
            if _menu_items:
                _parts.append(
                    f"侧边栏菜单({len(_menu_items)}项): " + ", ".join(_menu_items[:20])
                )

            # Tab列表
            _tabs = []
            with guard("_build_page_description: _tab_locs = page.locator('[role='tab", log=_log):
                _tab_locs = page.locator("[role='tab']")
                for _i in range(min(await _tab_locs.count(), 10)):
                    _txt = (await _tab_locs.nth(_i).text_content() or "").strip()
                    if _txt and _txt not in _tabs:
                        _tabs.append(_txt)
            if _tabs:
                _parts.append(f"页面Tab: {', '.join(_tabs)}")

            # 可交互按钮
            _btns, _inputs = await get_page_snapshot(page)
            _btn_texts = []
            for _b in _btns[:30]:
                _t = _b.get("text", "").strip()
                _r = _b.get("role", "")
                _tag = _b.get("tag", "")
                _vis = "可见" if _b.get("visible") else "隐藏"
                if _t:
                    _btn_texts.append(f"{_t}[{_tag}{'/'+_r if _r else ''}/{_vis}]")
            if _btn_texts:
                _parts.append(
                    f"可交互元素({len(_btn_texts)}个): " + "; ".join(_btn_texts[:25])
                )

            # 输入框
            _input_texts = []
            for _inp in _inputs[:10]:
                _ph = _inp.get("placeholder", "") or _inp.get("text", "")
                if _ph:
                    _input_texts.append(_ph)
            if _input_texts:
                _parts.append(f"输入框: {', '.join(_input_texts[:8])}")

            return "\n".join(_parts)
        except Exception as _e:
            _log.warning("[BuildPageDesc] 构建页面描述失败: %s", str(_e)[:80])
            return f"URL: {page.url}\n(页面描述构建异常)"

    async def _llm_decide_next_action(
        self,
        operation: str,
        expected: str,
        page_description: str,
        actions_history: list[dict],
        step_num: int,
        max_steps: int,
    ) -> dict[str, Any] | None:
        """LLM决策下一步动作（核心推理引擎）。

        输入：操作目标 + 页面描述 + 历史动作
        输出：JSON {action, target, reasoning, found?, evidence?}
        action类型: click_menu(点击侧边栏菜单) | click_button(点击按钮) |
                    click_tab(点击Tab) | fill_input(填充输入框) |
                    navigate(导航到URL) | done(目标达成) | not_found(无法找到)
        """
        import asyncio, json as _json

        _history_str = ""
        if actions_history:
            _history_str = "已执行动作:\n" + "\n".join(
                f"  {i+1}. {a.get('type','?')}: {a.get('target','?')} → {a.get('result','?')}"
                for i, a in enumerate(actions_history[-5:])
            )

        _prompt = (
            f"你是一个Web测试Agent，需要在当前页面中找到并点击目标按钮。\n\n"
            f"## 目标操作\n"
            f"操作: {operation[:150]}\n"
            f"期望反馈: {expected[:150]}\n\n"
            f"## 当前页面状态\n"
            f"{page_description[:2000]}\n\n"
            f"## 历史动作\n"
            f"{_history_str if _history_str else '(无，这是第1步)'}\n\n"
            f"## 当前步骤: {step_num}/{max_steps}\n\n"
            f"## ★ 铁律：你已经在正确的目标页面上！\n"
            f"- 目标按钮就在当前页面或其Tab页内，不要导航离开！\n"
            f"- 第1步必须：① 检查「可交互元素」中是否有目标按钮 ② 检查页面Tab中是否有目标按钮\n"
            f"- 只有当前页面所有Tab都探索完（≥2个Tab）且确认按钮不在当前页时，才考虑click_menu\n"
            f"- 如果这是第1步且你没有先探索当前页面就直接click_menu，你的决策是错误的\n\n"
            f"## 决策规则（严格按优先级顺序，违反即错误）\n"
            f"1. 如果目标按钮的文本在「可交互元素」中直接出现 → action=click_button, target=按钮文本\n"
            f"2. 如果页面有Tab列表，且目标按钮可能在某个Tab下 → action=click_tab, target=Tab文本\n"
            f"   （★ 必须探索！不要跳过Tab直接点菜单！）\n"
            f"3. 如果≥2个Tab都探索完仍未找到按钮 → action=click_menu, target=菜单项文本\n"
            f"4. 如果操作需要先填搜索框 → action=fill_input, target=输入框标识, value=搜索值\n"
            f"5. 如果遍历了所有路径仍找不到 → action=not_found, evidence=原因\n"
            f"6. 如果已找到目标按钮并点击 → action=done, found=true, evidence=结果描述\n\n"
            f"## 语义关联提示\n"
            f"- 「{operation[:30]}」可能与侧边栏中的相关菜单项有关联\n"
            f"- 但这不是第1步的优先事项！第1步必须先探索当前页面\n"
            f"- 按钮可能在当前页面的某个区域，仔细检查「可交互元素」列表\n\n"
            f'返回JSON: {{"action":"click_menu|click_button|click_tab|fill_input|done|not_found",'
            f'"target":"目标元素文本","reasoning":"决策理由(20字内)",'
            f'"found":true/false,"evidence":"证据描述"}}'
        )

        try:
            from src.ai.router_module import router
            from src.core.pipeline_config import _read_config_value as _cfg_seed

            _result = await asyncio.to_thread(
                router.route,
                _prompt,
                system_prompt=(
                    "你是Web自动化测试Agent。根据页面状态决定下一步操作。"
                    "只返回JSON，不要其他文本。target必须是页面描述中真实存在的文本。"
                ),
                task_type="general",
                expect_json=True,
                # ★ F2 全局修复 [未复核-需 GLM/DeepSeek 把关]（2026-08-29）：
                #   探索路径确定性化（同 case 每次探索路径不同正是测试抖动来源）。
                temperature=0.0,
                seed=(None if (_seed_v := _cfg_seed("ai_stability_seed", 42)) is None else int(_seed_v)),
                max_tokens=2000,  # ★ Fix: 1200→2000 防JSON截断
            )
            if _result.get("success") and _result.get("data"):
                _data = _result["data"]
                if isinstance(_data, str):
                    try:
                        _data = _json.loads(_data)
                    except Exception:
                        # ★ 修复：JSON解析失败时尝试从文本中提取JSON
                        _text = _data
                        _log.warning(
                            "[LLM-Decide] JSON解析失败，尝试从文本提取: %s", _text[:100]
                        )
                        _data = _extract_json_from_text(_text)
                        if not _data:
                            _log.warning("[LLM-Decide] 无法从文本提取JSON")
                            return None
                return {
                    "action": str(_data.get("action", "")),
                    "target": str(_data.get("target", "")),
                    "reasoning": str(_data.get("reasoning", "")),
                    "found": bool(_data.get("found", False)),
                    "evidence": str(_data.get("evidence", "")),
                    "value": str(_data.get("value", "")),
                }
            _log.warning("[LLM-Decide] LLM调用失败: %s", _result.get("error", "")[:80])
            return None
        except Exception as _e:
            _log.warning("[LLM-Decide] 异常: %s", str(_e)[:80])
            return None

    async def _execute_llm_action(
        self,
        page,
        action_type: str,
        target: str,
        decision: dict[str, Any],
        timeout_ms: int,
    ) -> dict[str, Any]:
        """执行LLM决定的动作。"""
        import asyncio

        _url_before = page.url or ""

        try:
            if action_type == "click_menu":
                _ok = await click_button(page, target, min(timeout_ms, 15000))
                if _ok:
                    await asyncio.sleep(1.0)
                    _url_after = page.url or ""
                    if _url_after != _url_before:
                        return {"status": "url_changed", "new_url": _url_after}
                    return {"status": "element_clicked", "target": target}
                return {"status": "failed", "error": f"点击菜单项失败: {target}"}

            elif action_type == "click_button":
                _ok = await click_button(page, target, min(timeout_ms, 10000))
                if _ok:
                    await asyncio.sleep(0.8)
                    return {"status": "element_clicked", "target": target}
                return {"status": "failed", "error": f"点击按钮失败: {target}"}

            elif action_type == "click_tab":
                _tab_loc = page.locator(f"[role='tab']:has-text('{target}')").first
                if await _tab_loc.count() > 0:
                    await _tab_loc.click(timeout=5000)
                    await asyncio.sleep(0.8)
                    return {"status": "element_clicked", "target": target}
                return {"status": "failed", "error": f"Tab不存在: {target}"}

            elif action_type == "fill_input":
                _value = decision.get("value", "")
                _input_loc = page.locator("input[type='text']").first
                if await _input_loc.count() > 0:
                    await _input_loc.fill(_value)
                    await asyncio.sleep(0.3)
                    return {"status": "element_clicked", "target": target}
                return {"status": "failed", "error": f"输入框不存在: {target}"}

            elif action_type == "navigate":
                _nav_url = decision.get("value", "") or target
                if _nav_url and not _nav_url.startswith("http"):
                    _nav_url = (
                        f"https://{_nav_url}"
                        if not _nav_url.startswith("/")
                        else _nav_url
                    )
                if _nav_url:
                    try:
                        await page.goto(
                            _nav_url,
                            timeout=min(timeout_ms, 20000),
                            wait_until="domcontentloaded",
                        )
                        await asyncio.sleep(1.0)
                        return {"status": "url_changed", "new_url": page.url}
                    except Exception:
                        return {"status": "failed", "error": f"导航失败: {_nav_url}"}
                return {"status": "failed", "error": "无导航URL"}

            else:
                return {"status": "failed", "error": f"未知动作类型: {action_type}"}

        except Exception as _e:
            return {"status": "failed", "error": str(_e)[:100]}
