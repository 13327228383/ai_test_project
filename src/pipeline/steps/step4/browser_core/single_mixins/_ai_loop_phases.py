# -*- coding: utf-8 -*-
"""AI loop 阶段函数模块 — 从 _ai_loop.py 拆分（§4.3 单文件≤800行）。

职责：封装 _run_ai_round_loop 的各阶段逻辑为模块级函数，主循环只保留调度。
  - 无控制流阶段：直接提取（Prompt构建/op_cache查询/JSON解析/指标记录/收尾处理）
  - 含控制流阶段：用状态返回值替代 break/continue（DOM获取/重复检测）

被 _ai_loop.py 的 _run_ai_round_loop 调用（模块级函数，self 通过参数传递）。
"""
import asyncio
from src.core.guarded import guard
import json
import logging
import re
import time as _time

from src.core.config_loader import config
from src.core.pipeline_config import pipeline_config
from src.pipeline.steps.step4.browser_core._smart_locator import match_step_to_element
from src.pipeline.steps.step4.action_hooks import run_action_with_hooks
from src.pipeline.steps.step4.browser_tools.locator import _locate_by_hint
from src.pipeline.steps.step4.playwright_engine.selectors import _escape_css_str

_log = logging.getLogger(__name__)


# ── 阶段1: 循环前指标记录 ──────────────────────────────────────────────
def _record_ai_loop_entry(case_meta):
    """记录 AI loop 兜底进入指标（非阻断）。"""
    with guard("_record_ai_loop_entry: from src.extensions.metrics_collecto", log=_log):
        from src.extensions.metrics_collector import metrics_collector
        from src.backend.state import _thread_run_id

        _rid = getattr(_thread_run_id, "value", "") or ""
        metrics_collector.record_ai_loop_entry(
            project=getattr(config, "project_name", "") or "",
            run_id=_rid,
            case_id=case_meta.get("case_id", "") if isinstance(case_meta, dict) else "",
        )


# ── 阶段2: DOM 获取（含滚动/刷新重试）──────────────────────────────────
async def _get_dom_with_retry(engine, ai_round):
    """获取 DOM 元素，含空 DOM 重试逻辑。

    返回:
        dom_elements (list|None): DOM 元素列表；None 表示获取失败需 break。
    """
    try:
        dom_elements = await engine.get_structured_state()
        if not dom_elements:
            _log.warning("    [AI] DOM 为空，等待后重试...")
            try:
                await engine._page.wait_for_load_state("domcontentloaded", timeout=2000)
            except Exception as _e:
                _log.debug(
                    "[AI] wait_for_load_state 失败，降级 wait_for_timeout: %s",
                    _e,
                    exc_info=True,
                )
                if not engine._page:
                    _log.warning("    [AI] engine._page 在重试期间失效，跳过 DOM 重试")
                    return None
                await engine._page.wait_for_timeout(500)
            if not engine._page:
                _log.warning("    [AI] engine._page 在 DOM 重试前失效，跳过")
                return None
            dom_elements = await engine.get_structured_state()

        if not dom_elements:
            _log.warning(
                "    [AI] DOM 仍为空，尝试滚动+刷新重试 (round=%d)...", ai_round
            )
            try:
                await engine._page.evaluate(
                    "window.scrollTo(0, document.body.scrollHeight);"
                )
                await asyncio.sleep(1)
                await engine._page.evaluate("window.scrollTo(0, 0);")
                await asyncio.sleep(1)
                dom_elements = await engine.get_structured_state()
                if dom_elements:
                    _log.info("    [AI] 滚动后 DOM 恢复: %d 个元素", len(dom_elements))
            except Exception as _e:
                _log.debug("page.evaluate 失败: %s", _e, exc_info=True)

            if not dom_elements:
                _log.warning("    [AI] 滚动后 DOM 仍为空，刷新页面重试...")
                with guard("_get_dom_with_retry: await engine._page.reload(wait_until", log=_log, level=logging.WARNING):
                    await engine._page.reload(wait_until="load", timeout=15000)
                    try:
                        await engine._page.wait_for_load_state(
                            "networkidle", timeout=10000
                        )
                    except Exception as _e:
                        _log.debug("wait_for_load_state 超时: %s", _e, exc_info=True)
                    from src.core.page_ready import wait_page_ready

                    await wait_page_ready(engine._page)
                    dom_elements = await engine.get_structured_state()
                    if dom_elements:
                        _log.info(
                            "    [AI] 刷新后 DOM 恢复: %d 个元素", len(dom_elements)
                        )
                    else:
                        _log.warning("    [AI] 刷新后 DOM 仍为空")
        return dom_elements
    except Exception as e:
        _log.error("    [AI] 获取 DOM 失败: %s", str(e)[:80])
        return None


# ── 阶段3: SmartLocator 分流后的 DOM 构建（medium/LOW/升级策略）─────────
def _build_smart_locator_dom(
    smart_match, dom_elements, case_meta, step, ai_round, self
):
    """根据 SmartLocator 置信度构建 last_dom 字符串。

    返回: last_dom (str)
    """
    last_dom = ""
    if smart_match.confidence == "high":
        # high 置信的 0-token 路径由调用方处理，这里仅构建回退 DOM
        last_dom = json.dumps([smart_match.best_element], ensure_ascii=False)
        last_dom = (
            f"【SmartLocator 已锁定唯一候选元素】\n{last_dom}\n"
            f"请仅基于该元素输出 action_type 与 action_params。"
        )
    elif smart_match.confidence == "medium":
        top_k_elements = [c["element"] for c in smart_match.candidates]
        last_dom = json.dumps(top_k_elements, ensure_ascii=False)
        last_dom = (
            f"【SmartLocator 候选元素 Top-{len(top_k_elements)}】\n{last_dom}\n"
            f"请从候选元素中选择最匹配的一个并输出 action_type/action_params。"
        )
    else:
        # LOW：智能定位兜底
        _keywords = smart_match.keywords or smart_match.synonyms
        if _keywords and dom_elements:
            _matched = []
            _keyword_set = {k.lower() for k in _keywords}
            for el in dom_elements:
                _el_text = (el.get("text", "") or "").lower()
                _el_placeholder = (el.get("placeholder", "") or "").lower()
                _el_label = (el.get("label", "") or "").lower()
                _el_role = (el.get("role", "") or "").lower()
                if any(
                    k in _el_text
                    or k in _el_placeholder
                    or k in _el_label
                    or k in _el_role
                    for k in _keyword_set
                ):
                    _matched.append(el)
            if _matched:
                _log.info(
                    "    [SmartLocator] LOW → 智能筛选: 从 %d 元素中匹配到 %d 个相关元素",
                    len(dom_elements),
                    len(_matched),
                )
                last_dom = json.dumps(_matched, ensure_ascii=False)
                last_dom = (
                    f"【智能定位筛选结果】基于关键词 '{','.join(_keywords)}' 从 {len(dom_elements)} 个元素中筛选出 {len(_matched)} 个相关元素\n{last_dom}\n"
                    f"请从上述元素中选择最匹配的一个并输出 action_type/action_params。"
                )
            else:
                _log.warning(
                    "    [SmartLocator] LOW → 关键词未匹配到元素(%d元素中)，提供兜底DOM子集(top 20)给AI",
                    len(dom_elements),
                )
                _fallback = dom_elements[:20] if dom_elements else []
                if _fallback:
                    last_dom = json.dumps(_fallback, ensure_ascii=False)
                    last_dom = (
                        f"【关键词匹配失败-兜底DOM子集】从 {len(dom_elements)} 个交互元素中取前 {len(_fallback)} 个\n"
                        f"注意：关键词未能精准匹配，请结合截图从以下元素中选择最可能的目标。\n{last_dom}"
                    )
        else:
            if dom_elements:
                _log.warning(
                    "    [SmartLocator] LOW → 无关键词匹配，提供兜底DOM子集(top 20)给AI"
                )
                _fallback = dom_elements[:20]
                last_dom = json.dumps(_fallback, ensure_ascii=False)
                last_dom = f"【兜底DOM子集】从 {len(dom_elements)} 个交互元素中取前 {len(_fallback)} 个\n{last_dom}"
    return last_dom


# ── 阶段3b: 升级策略（滚动查找 + 多入口查找）────────────────────────────
async def _try_upgrade_strategies(
    self,
    engine,
    smart_match,
    dom_elements,
    last_dom,
    ai_round,
    case_meta,
    step,
    step_desc,
):
    """滚动查找 + 多入口查找升级策略。

    返回: last_dom (str) — 可能被更新
    """
    # 升级策略1：滚动查找
    if not last_dom and ai_round == 1 and dom_elements:
        _log.info("    [升级策略] 滚动查找：当前视口未找到元素，尝试滚动页面")
        with guard("_try_upgrade_strategies: _keywords = ( smart_match.keywords o", log=_log):
            _keywords = (
                smart_match.keywords or smart_match.synonyms if smart_match else []
            )
            _scrolled_matched = await self._scroll_and_find(
                engine, _keywords, dom_elements
            )
            if _scrolled_matched:
                _log.info(
                    "    [升级策略] ✓ 滚动查找成功: 找到 %d 个匹配元素",
                    len(_scrolled_matched),
                )
                last_dom = json.dumps(_scrolled_matched, ensure_ascii=False)
                last_dom = (
                    f"【滚动查找结果】滚动页面后发现 {len(_scrolled_matched)} 个匹配元素\n{last_dom}\n"
                    f"请从上述元素中选择最匹配的一个并输出 action_type/action_params。"
                )

    # 升级策略2：多入口查找
    if not last_dom and ai_round == 1:
        _log.info("    [升级策略] 多入口查找：当前页面未找到元素，尝试从导航地图换入口")
        with guard("_try_upgrade_strategies: _keywords = list( smart_match.keywor", log=_log):
            _keywords = list(
                smart_match.keywords or smart_match.synonyms if smart_match else []
            )
            _module = case_meta.get("module", "") if case_meta else ""
            if _module:
                _cn_parts = re.findall(r"[\u4e00-\u9fff]+", _module)
                _keywords.extend(_cn_parts)
                _log.info("    [升级策略] 追加模块关键词: %s", _cn_parts[:5])
            _step_actions = step.get("actions", []) if isinstance(step, dict) else []
            for _sa in _step_actions:
                if isinstance(_sa, dict) and _sa.get("type") == "navigate":
                    _nav_hint = str(_sa.get("target_hint", "") or "").strip()
                    if _nav_hint and _nav_hint != "/":
                        _keywords.append(_nav_hint)
                        _log.info(
                            "    [升级策略] 追加导航目标页面关键词: %s", _nav_hint
                        )
                    break
            _nav_matched = await self._try_alternative_entries(
                engine, _keywords, step_desc
            )
            if _nav_matched:
                _log.info(
                    "    [升级策略] ✓ 多入口查找成功: 从其他页面找到 %d 个匹配元素",
                    len(_nav_matched),
                )
                last_dom = json.dumps(_nav_matched, ensure_ascii=False)
                last_dom = (
                    f"【多入口查找结果】从其他页面入口找到 {len(_nav_matched)} 个匹配元素\n{last_dom}\n"
                    f"请从上述元素中选择最匹配的一个并输出 action_type/action_params。"
                )
    return last_dom


# ── 阶段4: Prompt 构建 ──────────────────────────────────────────────────
def _build_ai_prompt(
    meta_text,
    import_hint,
    table_hint,
    ai_round,
    ai_error,
    step_desc,
    step_expected,
    step,
    engine,
    last_dom,
    last_screenshot,
):
    """构建 AI Prompt（无控制流）。

    返回: ai_prompt (str)
    """
    prompt_parts = [meta_text]
    if import_hint:
        prompt_parts.append(f"【导入提示】当前操作涉及: {import_hint}")
    if table_hint:
        prompt_parts.append(f"【表名提示】相关数据表: {table_hint}")
    if ai_round > 1 and ai_error:
        prompt_parts.append(
            f"【上轮失败】上一轮尝试的操作失败了: {ai_error}\n请分析失败原因并尝试不同的操作方式"
        )
    prompt_parts.extend(
        [
            f"【当前步骤】{step_desc}",
            f"【期望结果】{step_expected}",
            f"【当前 URL】{engine._page.url if engine._page else ''}",
        ]
    )
    # 注入原始 actions 摘要
    _orig_actions = step.get("actions", []) if isinstance(step, dict) else []
    if _orig_actions and isinstance(_orig_actions, list):
        _action_summaries = []
        for _act in _orig_actions:
            if not isinstance(_act, dict):
                continue
            _atype = _act.get("type", "") or _act.get("action", "")
            _hint = _act.get("target_hint", "") or _act.get("selector_hint", "")
            _val = _act.get("value", "") or _act.get("text", "") or _act.get("file", "")
            _summary = f"{_atype}({ _hint})" if _hint else _atype
            if _val:
                _summary += f" → {_val}"
            _action_summaries.append(_summary)
        if _action_summaries:
            prompt_parts.append(
                f"【原始操作步骤（结构化执行失败，请参考这些操作目标进行AI决策）】\n"
                + "\n".join(f"  {i+1}. {s}" for i, s in enumerate(_action_summaries))
            )
    # 弹窗按钮探索指导
    _MODAL_BUTTON_KEYWORDS = frozenset(
        {
            "确定",
            "确 定",
            "确认",
            "确 认",
            "取消",
            "取 消",
            "保存",
            "保 存",
            "提交",
            "提 交",
            "应用",
            "应 用",
            "确定按钮",
            "保存按钮",
            "提交按钮",
        }
    )
    _need_modal_explore = False
    _modal_targets = []
    if _orig_actions and isinstance(_orig_actions, list):
        for _act in _orig_actions:
            if not isinstance(_act, dict):
                continue
            _atype = str(_act.get("type", "") or _act.get("action", "")).strip()
            if _atype not in ("click", "dblclick"):
                continue
            _hint = str(_act.get("target_hint", "") or "").strip()
            if not _hint:
                continue
            _core = _hint.replace("按钮", "").strip()
            if _core in _MODAL_BUTTON_KEYWORDS or _hint in _MODAL_BUTTON_KEYWORDS:
                _need_modal_explore = True
                _modal_targets.append(_hint)
    if not _need_modal_explore:
        _case_ref = step.get("_case", {}) if isinstance(step, dict) else {}
        _cv = (
            _case_ref.get("_control_validation", {})
            if isinstance(_case_ref, dict)
            else {}
        )
        _missing_controls = (
            _cv.get("missing_controls", []) if isinstance(_cv, dict) else []
        )
        if _missing_controls:
            _need_modal_explore = True
            _modal_targets = _missing_controls
    if _need_modal_explore:
        _targets_str = ", ".join(_modal_targets[:3]) if _modal_targets else "目标按钮"
        prompt_parts.append(
            f"★ 弹窗按钮探索策略：目标控件 {_targets_str} 在主页面 DOM 中不存在，可能是弹窗/modal 中的按钮。请按以下策略探索：\n"
            "1. 检查页面上是否有'新建/新增/添加/创建/编辑/导入'等触发按钮\n"
            "2. 点击触发按钮打开弹窗/modal\n"
            "3. 在弹窗中找目标按钮（注意按钮文本可能含空格，如'确 定'）\n"
            "4. 如果弹窗有多个 tab，切换到正确的 tab 再找目标按钮"
        )
    # last_dom 为空时的引导
    if last_dom:
        prompt_parts.append(f"【DOM 片段】\n{last_dom}")
    else:
        from src.core.config_loader import config as _cfg

        _has_vision = bool(
            getattr(_cfg, "vision_model", "")
            and getattr(_cfg, "vision_model_enabled", False)
            and getattr(_cfg, "vision_api_key", "")
            and getattr(_cfg, "vision_api_key", "") != getattr(_cfg, "ai_api_key", "")
        )
        if last_screenshot and _has_vision:
            prompt_parts.append(
                "【DOM 片段】当前页面未匹配到相关DOM元素，请通过截图分析页面并定位目标元素"
            )
        else:
            prompt_parts.append(
                "【DOM 片段】当前页面未匹配到相关DOM元素。请尝试执行 refresh 或 wait 操作等待页面加载完成后重试。"
            )
    prompt_parts.append(
        "★ target 取值（引擎用同一套解析器处理，两种写法等价，任选其一）：\n"
        "1. 自然语言描述（如'搜索输入框'、'保存按钮'）——推荐；\n"
        "2. 声明步骤中给出的定位前缀写法（name= / placeholder= / label= / id= / text=），"
        "可直接沿用【原始操作步骤】里的形式，无需翻译。\n"
        "禁止：使用 '#ai-N' 这类页面瞬时 id（每次渲染都会变，不稳定）。"
    )
    from src.core.config_loader import config as _cfg2

    _has_vision2 = bool(
        getattr(_cfg2, "vision_model", "")
        and getattr(_cfg2, "vision_model_enabled", False)
        and getattr(_cfg2, "vision_api_key", "")
        and getattr(_cfg2, "vision_api_key", "") != getattr(_cfg2, "ai_api_key", "")
    )
    if last_screenshot and _has_vision2:
        prompt_parts.append("【截图】已提供页面截图")
    return "\n\n".join(prompt_parts)


# ── 阶段5a: op_cache_v2 查询 ────────────────────────────────────────────
def _query_op_cache_v2(ai_round, dom_elements, case_meta, step, engine):
    """查询 op_cache_v2（仅首轮）。

    返回: (ai_data, cache_hit, cache_repair_info)
        - ai_data (dict|None): 命中时的决策数据；None 表示未命中
        - cache_hit (bool): 是否命中
        - cache_repair_info (tuple): (project, vid, intent_hash) 供失败 repair
    """
    if not (ai_round == 1 and dom_elements):
        return None, False, ()
    with guard("_query_op_cache_v2: from src.pipeline.steps.step4.op_cac", log=_log):
        from src.pipeline.steps.step4.op_cache.v2_store import (
            get_v2_store,
            compute_intent_hash,
            compute_page_fingerprint,
        )

        _case_id = case_meta.get("case_id", "") if isinstance(case_meta, dict) else ""
        _step_actions = step.get("actions", []) if isinstance(step, dict) else []
        _intent_hash = compute_intent_hash(_case_id, _step_actions)
        _project = getattr(config, "project_name", "") or ""
        _vid = 0
        with guard("_query_op_cache_v2: from src.core.versioning import curr", log=_log):
            from src.core.versioning import current_version_id

            _vid = int(current_version_id(_project) or 0)
        _page_url = engine._page.url if engine._page else ""
        _cached = get_v2_store().get(_project, _vid, _intent_hash)
        if _cached and _cached.get("action_sequence"):
            # fingerprint 比对 warning（repair 软检测）
            try:
                _cur_fp = compute_page_fingerprint(_page_url, dom_elements)
                _cached_fp = _cached.get("page_fingerprint", "") or ""
                if _cached_fp and _cur_fp and _cur_fp != _cached_fp:
                    _log.info(
                        "    [OpCacheV2] ⚠ fingerprint 不一致 (缓存=%s 当前=%s)，"
                        "仍重放(intent_hash 稳定)",
                        _cached_fp[:8],
                        _cur_fp[:8],
                    )
            except Exception as _se1:
                _log.debug(
                    "[_ai_loop] fingerprint 比对异常: %s", str(_se1)[:60], exc_info=True
                )
            _first = _cached["action_sequence"][0] or {}
            _ctype = _first.get("action", "") or _first.get("type", "")
            _cparams = {}
            _hint = _first.get("target_hint", "") or _first.get("selector", "")
            if _ctype in ("click", "fill"):
                _cparams["selector"] = _hint
                if _ctype == "fill":
                    _cparams["value"] = _first.get("text", "") or _first.get(
                        "value", ""
                    )
            elif _ctype == "navigate":
                _cparams["url"] = _hint
            elif _ctype == "assert":
                _cparams["selector"] = _hint
                _cparams["expected"] = _first.get("result", "")
            ai_data = {
                "action_type": _ctype,
                "action_params": _cparams,
                "reasoning": "op_cache_v2 hit",
            }
            # 命中计数 +1（非阻断）
            get_v2_store().increment_hit(_project, _vid, _intent_hash)
            _log.info(
                "    [OpCacheV2] ✓ 缓存命中: %s (跳过 AI 调用, hit_count=%d)",
                _ctype,
                int(_cached.get("hit_count") or 0) + 1,
            )
            return ai_data, True, (_project, _vid, _intent_hash)
    return None, False, ()


# ── 阶段5b: AI 响应 JSON 解析（容错）────────────────────────────────────
def _escape_raw_controls_in_strings(text: str) -> str:
    """★ F1(2026-09-26)：把 JSON **字符串字面量内**的裸控制字符转义。

    实测非法响应成因之一：AI 把换行直接写进 value ——
      {"reasoning": "第一行
       第二行", "action_type": "..."}
    裸换行使 ``json.loads`` 在 delimiter 处报
    ``Expecting ',' delimiter``。只处理字面量内部，不改结构字符。
    """
    out: list = []
    in_str = False
    esc = False
    for ch in text:
        if in_str:
            if esc:
                out.append(ch)
                esc = False
            elif ch == "\\":
                out.append(ch)
                esc = True
            elif ch == '"':
                out.append(ch)
                in_str = False
            elif ch == "\n":
                out.append("\\n")
            elif ch == "\r":
                out.append("\\r")
            elif ch == "\t":
                out.append("\\t")
            else:
                out.append(ch)
        else:
            if ch == '"':
                in_str = True
            out.append(ch)
    return "".join(out)


def _parse_ai_response(ai_response):
    """解析 AI 响应为 dict，含 JSON 容错修复。

    返回: (ai_data, error)
        - ai_data (dict|None): 解析成功的数据；None 表示解析失败
        - error (str): 失败原因；成功时为空
    """
    if not isinstance(ai_response, str):
        return ai_response if isinstance(ai_response, dict) else None, ""
    raw_text = ai_response.strip()
    # 1. 去除 markdown 代码块包裹
    if raw_text.startswith("```"):
        lines = raw_text.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        raw_text = "\n".join(lines).strip()
    # 2. 尝试直接解析
    try:
        return json.loads(raw_text), ""
    except json.JSONDecodeError as e:
        fixed_text = raw_text
        # 3a. Extra data 截断
        if "Extra data" in str(e) and hasattr(e, "pos") and e.pos > 0:
            fixed_text = fixed_text[: e.pos]
            _log.info("    [AI] Extra data 截断修复: pos=%d", e.pos)
        # 3b. 修复末尾多余逗号
        fixed_text = re.sub(r",(\s*[}\]])", r"\1", fixed_text)
        # 3c. 单引号转双引号（谨慎）
        if "'" in fixed_text and '"' not in fixed_text[:50]:
            fixed_text = fixed_text.replace("'", '"')
        # 3d. 提取 JSON 子串
        if "{" in fixed_text and "}" in fixed_text:
            start = fixed_text.find("{")
            end = fixed_text.rfind("}") + 1
            fixed_text = fixed_text[start:end]
        # 3e. ★ F1(2026-09-26)：转义字符串字面量内的裸控制字符
        fixed_text = _escape_raw_controls_in_strings(fixed_text)
        # 再次尝试解析
        try:
            ai_data = json.loads(fixed_text)
            _log.info("    [AI] JSON 容错修复成功")
            return ai_data, ""
        except json.JSONDecodeError:
            # 3f. ★ F1：从首个 '{' 起 raw_decode（容忍尾部截断/多余说明文字）
            _brace = fixed_text.find("{")
            if _brace >= 0:
                with guard("_parse_ai_response: _obj, _ = json.JSONDecoder().raw_dec", log=_log):
                    _obj, _ = json.JSONDecoder().raw_decode(fixed_text[_brace:])
                    if isinstance(_obj, dict) and _obj.get("action_type"):
                        _log.info("    [AI] JSON raw_decode 容错成功")
                        return _obj, ""
            _log.warning("    [AI] 响应解析失败（已尝试容错修复）: %s", str(e)[:80])
            _log.error("    [AI] 原始响应内容: %s", repr(ai_response)[:500])
            return None, f"AI 响应解析失败: {str(e)[:80]}"


# ── 阶段6: 重复检测（纯同步判断，异步操作由调用方处理）─────────────────
def _check_repeat_action(
    action_type,
    action_params,
    _last_action_signature,
    _no_change_count,
    _max_no_change_rounds,
    _no_change_reset_count,
    _MAX_RESET_COUNT,
    page_changed=False,
):
    """检测连续无变化操作（纯同步判断）。

    ★ F2(2026-09-26)：新增 ``page_changed`` —— 调用方**实测**的页面变化信号
      （URL + 可见文本指纹 + fill 幂等验证），确为 True 时判"上一动作已生效"，
      重置重复计数。原实现只比动作签名却输出"页面没有变化"，属无依据断言。

    返回: (decision, ai_error, new_state)
        - decision (str): "proceed" / "break" / "reload_and_continue" / "get_dom_and_continue"
        - ai_error (str): 错误提示（注入下一轮 prompt 或终止）
        - new_state (dict): 更新后的状态 {
            _no_change_count, _last_action_signature, _no_change_reset_count
          }
    """
    _action_signature = f"{action_type}:{action_params.get('selector', '')}:{action_params.get('value', '')}"
    if page_changed or _action_signature != _last_action_signature:
        if page_changed and _action_signature == _last_action_signature:
            _log.info(
                "    [AI] 动作签名重复(%s)但页面确有变化 → 判定动作已生效，重置重复计数",
                action_type,
            )
        return (
            "proceed",
            "",
            {
                "_no_change_count": 0,
                "_last_action_signature": _action_signature,
                "_no_change_reset_count": _no_change_reset_count,
            },
        )
    _no_change_count += 1
    if _no_change_count < _max_no_change_rounds:
        # 未达阈值 → 调用方负责刷新页面 + 提示换策略
        _log.warning(
            "    [AI] 检测到重复操作第%d次(%s)，刷新页面并提示换策略",
            _no_change_count,
            action_type,
        )
        return (
            "reload_and_continue",
            (
                f"你刚才已经尝试过 {action_type}({action_params.get('selector', '')})，"
                f"但页面没有变化。请尝试完全不同的策略：换一个元素、refresh、wait、"
                f"或检查当前页面是否正确。"
            ),
            {
                "_no_change_count": _no_change_count,
                "_last_action_signature": _last_action_signature,
                "_no_change_reset_count": _no_change_reset_count,
            },
        )
    # 达到阈值
    if _no_change_reset_count >= _MAX_RESET_COUNT:
        ai_error = (
            f"AI 连续 {_no_change_count} 轮选择相同操作({action_type})，"
            f"已重置 {_no_change_reset_count} 次仍无法推进，目标元素可能在页面中不存在。"
            f"终止AI循环避免浪费token。"
        )
        _log.warning("    [AI] %s", ai_error)
        return (
            "break",
            ai_error,
            {
                "_no_change_count": _no_change_count,
                "_last_action_signature": _last_action_signature,
                "_no_change_reset_count": _no_change_reset_count,
            },
        )
    # 调用方负责获取实际 DOM 并重置
    return (
        "get_dom_and_continue",
        "",
        {
            "_no_change_count": 0,
            "_last_action_signature": "",
            "_no_change_reset_count": _no_change_reset_count + 1,
        },
    )


# ── 阶段7: 指标记录 ────────────────────────────────────────────────────
def _record_browser_ai_round(ai, self, case_meta, step_desc, ai_round):
    """记录 Step4 浏览器 AI 轮次指标（非阻断）。"""
    with guard("_record_browser_ai_round: from src.backend.state import _threa", log=_log):
        from src.backend.state import _thread_run_id
        from src.extensions.metrics_collector import metrics_collector

        run_id = getattr(_thread_run_id, "value", "") or ""
        if run_id:
            _token_usage = getattr(ai, "_last_usage", None) or {}
            _elapsed = _time.time() - getattr(self, "_round_start_time", _time.time())
            metrics_collector.record_browser_ai_round(
                project=config.project_name,
                run_id=run_id,
                case_id=case_meta.get("case_id", ""),
                step_desc=step_desc,
                round_num=ai_round,
                elapsed_sec=_elapsed,
                prompt_tokens=_token_usage.get("input", 0),
                completion_tokens=_token_usage.get("output", 0),
                model=getattr(ai, "current_model", "") or getattr(ai, "model", ""),
            )


# ── 阶段8: 循环后 op_cache 写入（record 拍）─────────────────────────────
def _record_op_cache_v2(_last_ai_action, _last_dom_for_cache, case_meta, step, engine):
    """循环成功后写 op_cache_v2（非阻断）。"""
    if not _last_ai_action:
        return
    with guard("_record_op_cache_v2: from src.pipeline.steps.step4.op_cac", log=_log):
        from src.pipeline.steps.step4.op_cache.v2_store import (
            get_v2_store,
            compute_intent_hash,
            compute_page_fingerprint,
        )
        from src.pipeline.steps.step4.browser_core.single_mixins._ai_loop_utils import (
            _decide_to_cache_action,
        )

        _cached = _decide_to_cache_action(_last_ai_action, _last_dom_for_cache or [])
        if _cached:
            _case_id = (
                case_meta.get("case_id", "") if isinstance(case_meta, dict) else ""
            )
            _step_actions = step.get("actions", []) if isinstance(step, dict) else []
            _intent_hash = compute_intent_hash(_case_id, _step_actions)
            _project = getattr(config, "project_name", "") or ""
            _vid = 0
            with guard("_record_op_cache_v2: from src.core.versioning import curr", log=_log):
                from src.core.versioning import current_version_id

                _vid = int(current_version_id(_project) or 0)
            _page_url = engine._page.url if engine._page else ""
            _page_fp = compute_page_fingerprint(_page_url, _last_dom_for_cache or [])
            get_v2_store().set(_project, _vid, _intent_hash, _page_fp, [_cached])
            _log.info(
                "    [OpCacheV2] ✓ 成功决策已缓存 (hash=%s...)", str(_intent_hash)[:12]
            )


# ── 阶段9: 循环后成功指标记录 ───────────────────────────────────────────
def _record_ai_loop_pass(case_meta):
    """记录 AI loop 兜底成功指标（非阻断）。"""
    with guard("_record_ai_loop_pass: from src.extensions.metrics_collecto", log=_log):
        from src.extensions.metrics_collector import metrics_collector
        from src.backend.state import _thread_run_id

        _rid = getattr(_thread_run_id, "value", "") or ""
        metrics_collector.record_ai_loop_pass(
            project=getattr(config, "project_name", "") or "",
            run_id=_rid,
            case_id=case_meta.get("case_id", "") if isinstance(case_meta, dict) else "",
        )


# ── 阶段10: SmartLocator high 置信 0-token 直接执行 ─────────────────────
async def _execute_0token_smart_locator(
    smart_match, step, step_desc, engine, _token_usage, self
):
    """SmartLocator high 置信时 0-token 直接执行路径（不调 AI）。

    根因(3层追问):
      1. 表层: SmartLocator high 置信匹配到元素，应直接执行节省 token
      2. 中层: 此前内联在主循环，导致 _ai_loop.py 单文件超 800 行红线
      3. 根因: SmartLocator 0-token 路径是独立阶段，应与其他阶段一样拆分到 phases 模块
    通用性: 适用于所有系统/行业/页面 — 纯 SmartLocator 决策执行，不依赖业务逻辑
    覆盖性: 影响所有 SmartLocator high 置信的 0-token 执行路径
    控制流: 用状态返回值替代 continue — "continue"(成功跳过AI) / "fallback_to_ai"(回退到AI)

    返回: (action, ai_error, last_dom)
        - action (str): "continue" / "fallback_to_ai"
        - ai_error (str): 失败原因（成功时为空）
        - last_dom (str): 回退到AI时使用的 DOM 片段
    """
    # 延迟导入避免循环依赖
    from src.pipeline.steps.step4.browser_core.single_mixins._ai_loop_utils import (
        _enrich_action_tags,
    )

    best_el = smart_match.best_element or {}
    _log.info(
        "    [SmartLocator] high 置信 → 0-token 直接执行: %s",
        best_el.get("selector", best_el.get("text", "?")[:30]),
    )
    ai_error = ""
    try:
        _selector = best_el.get("selector", "")
        # ★ 通用修复(2026-07-22): selector 为空时从元素字段构造
        #   根因: dom_extraction.py 返回的元素字典没有 "selector" 字段，
        #         只有 "id"(ai-N格式)、"html_id"、"text" 等
        if not _selector:
            _el_id = str(best_el.get("id", "") or "").strip()
            if _el_id and _el_id.startswith("ai-"):
                _selector = "ai_id=" + _el_id
            else:
                _el_html_id = str(best_el.get("html_id", "") or "").strip()
                if _el_html_id:
                    _selector = "#" + _el_html_id
        _tag = str(best_el.get("tag", "")).lower()
        _is_input = "input" in _tag or "textarea" in _tag
        _action_type = "fill" if _is_input else "click"

        if _action_type == "click" and _selector:

            async def _do_0token_click(a):
                # ★ 通用修复(2026-07-22): 0-token 路径优先用构造的 _selector
                #   根因: target_hint 是人类可读文本（非 CSS 选择器），
                #         0-token 路径应用 DOM 元素构造的有效选择器
                _action_hint = a.get("target_hint", "") if isinstance(a, dict) else ""
                healed = _selector if _selector else _action_hint
                if healed.startswith("ai_id="):
                    await engine._page.click(
                        '[data-ai-id="%s"]' % _escape_css_str(healed[6:].strip("\"'")),
                        timeout=5000,
                    )
                    return
                try:
                    await engine._page.click(healed, timeout=5000)
                except Exception as _e:
                    _log.debug(
                        "[_ai_loop] _do_0token_click 异常: %s", _e, exc_info=True
                    )
                    _fallback_hint = _action_hint or healed
                    locator = await _locate_by_hint(engine._page, _fallback_hint)
                    if not locator:
                        raise ValueError(f"未找到元素: {_fallback_hint}")
                    await locator.click(timeout=5000)

            result = await run_action_with_hooks(
                engine,
                "click",
                _selector,
                do_action=_do_0token_click,
                token_usage=_token_usage,
            )
            if result.get("success"):
                _enrich_action_tags(_action_type, best_el)
                self._enrich_page_explorer(step, best_el, engine)
                return "continue", "", ""
            ai_error = result.get("error", "click failed")[:80]

        elif _action_type == "fill" and _selector:

            async def _do_0token_fill(a):
                # ★ 通用修复(2026-07-22): 0-token 路径优先用构造的 _selector（同 click）
                _action_hint = a.get("target_hint", "") if isinstance(a, dict) else ""
                healed = _selector if _selector else _action_hint
                healed_text = a.get("text", step_desc)
                if healed.startswith("ai_id="):
                    await engine._page.fill(
                        '[data-ai-id="%s"]' % _escape_css_str(healed[6:].strip("\"'")),
                        healed_text,
                        timeout=5000,
                    )
                    return
                try:
                    await engine._page.fill(healed, healed_text, timeout=5000)
                except Exception as _e:
                    _log.debug("[_ai_loop] _do_0token_fill 异常: %s", _e, exc_info=True)
                    _fallback_hint = _action_hint or healed
                    locator = await _locate_by_hint(engine._page, _fallback_hint)
                    if not locator:
                        raise ValueError(f"未找到元素: {_fallback_hint}")
                    await locator.fill(healed_text, timeout=5000)

            result = await run_action_with_hooks(
                engine,
                "fill",
                _selector,
                do_action=_do_0token_fill,
                action_extra={"text": step_desc},
                token_usage=_token_usage,
            )
            if result.get("success"):
                _enrich_action_tags(_action_type, best_el)
                self._enrich_page_explorer(step, best_el, engine)
                return "continue", "", ""
            ai_error = result.get("error", "fill failed")[:80]

        # 无 selector 或执行失败 → 回退到 AI
        if not _selector:
            ai_error = "no selector in SmartLocator match"
        _log.warning("    [SmartLocator] 0-token 执行失败，回退到 AI: %s", ai_error)
    except Exception as _e:
        _log.warning("    [SmartLocator] 0-token 执行失败，回退到 AI: %s", str(_e)[:80])
        ai_error = str(_e)[:80]

    # 回退到 AI：仅给 AI 唯一最佳候选
    last_dom = json.dumps([smart_match.best_element], ensure_ascii=False)
    last_dom = (
        f"【SmartLocator 已锁定唯一候选元素】\n{last_dom}\n"
        f"请仅基于该元素输出 action_type 与 action_params。"
    )
    return "fallback_to_ai", ai_error, last_dom
