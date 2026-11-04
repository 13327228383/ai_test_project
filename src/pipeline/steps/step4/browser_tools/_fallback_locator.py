# -*- coding: utf-8 -*-
"""统一 fallback 定位模块 — 从 locator.py 拆分（§4.3 单文件≤800行）。

职责：_locate_with_fallback — 先精确定位，失败后自动模糊回退+intent感知+modal感知。
依赖：_basic_locator + _intent_executor + _hint_utils + _fuzzy_selectors。
供所有 browser_tools 与结构化执行器统一调用。
"""
import logging
from src.core.guarded import guard

from src.pipeline.steps.step4.playwright_engine.selectors import _escape_css_str
from src.pipeline.steps.step4.browser_tools._hint_utils import _extract_core_keyword
from src.pipeline.steps.step4.browser_tools._fuzzy_selectors import (
    _CLICK_FUZZY_SELECTORS,
    _INPUT_FUZZY_SELECTORS,
)
from src.pipeline.steps.step4.browser_tools._basic_locator import (
    _locate_by_hint,
    _find_visible_modal,
    _locate_in_modal,
)
from src.pipeline.steps.step4.browser_tools._intent_executor import _locate_by_intent

_log = logging.getLogger(__name__)


# ── 通用定位包装器：自动 fallback ──
async def _locate_with_fallback(
    page, hint: str, kind: str = "click", selector: str = "", strict: bool = False
):
    """先精确定位，失败后自动模糊回退。供所有工具函数统一使用。

    Args:
        page: Playwright Page 实例
        hint: 原始 target_hint
        kind: "click" → 按钮类；"input" → 输入框类；"upload" → 文件上传类
        selector: 预解析的 CSS 选择器（来自页面探索器缓存），若提供则直接使用，跳过 self-healing
        strict: ★ 断言类调用必须置 True。
            strict=True 且显式 selector 未命中 → 直接返回 None，禁止模糊回退。
            根因：存在性断言（verify_element_exists 等）的语义是「精确判定目标」，
            模糊回退会把 selector 未命中（目标不存在）误判成「命中其它元素」→ 假 PASS，
            对「目标缺失/判伪/删除成功/弹窗消失」类验证危害最大。
            动作类调用（click/fill）保持 strict=False，保留容错回退能力。

    Returns:
        Locator 或 None
    """
    # ★ Phase 1 (2026-07-11): 预解析选择器优先 — 页面探索器缓存的 CSS selector 直接使用
    if selector:
        with guard("_locate_with_fallback: loc = page.locator(selector).first", log=_log):
            loc = page.locator(selector).first
            if await loc.count() > 0:
                _log.info("[locator] 预解析选择器命中: '%s'", selector[:80])
                return loc
            _log.debug("[locator] 预解析选择器未命中: '%s'", selector[:80])
        # ★ 修正实现与契约不符（2026-09-21）：docstring 写明「若提供则直接使用，跳过
        #   self-healing」，原实现在未命中时却继续走模糊回退 → 显式 selector 语义被污染。
        if strict:
            _log.info(
                "[locator] strict 模式：显式 selector 未命中，不模糊回退: '%s'",
                selector[:80],
            )
            return None

    # ★ 方案B (2026-07-23 ADR-017): intent: 语义意图实时感知
    #   根因: Step3 DOM预爬取不完整 → 28%降级 → 架构缺陷
    #   修复: 识别 intent: 前缀，实时在页面DOM找匹配按钮（Ego Lite 思路）
    #   流程: 提取operation语义 → 实时获取页面所有按钮 → _match_button_by_keywords匹配
    #   通用性: 跨系统/跨行业/跨页面(Tab/弹窗/懒加载) — 不依赖预爬取完整性
    #   合规: ADR-017 §6 调整 §12.2规则2 — intent:由Step4实时匹配
    if hint and hint.startswith("intent:"):
        _intent_op = hint[len("intent:") :].strip()
        if _intent_op:
            _intent_loc = await _locate_by_intent(page, _intent_op, kind)
            if _intent_loc is not None:
                _log.info(
                    "[locator] 方案B intent: 实时命中 '%s' (operation=%r)",
                    hint[:50],
                    _intent_op[:30],
                )
                return _intent_loc
            _log.warning(
                "[locator] 方案B intent: 实时未命中 operation=%r → 走原fuzzy流程",
                _intent_op[:30],
            )

    # ★ v10 通用修复(2026-07-22): Modal-aware 精确匹配优先
    #   根因核查(§10.9):
    #     1. 现象: click "开始导入" 时，[locator] 部分关键词命中 '开始导入' → '导入' (len=2)，
    #        匹配到主页面的"数据导入"按钮（错误元素），force click 点击错误元素，导入API未执行
    #     2. 直接原因: 精确匹配 get_by_text("开始导入") 失败（modal内按钮文本含空格或非标准button），
    #        部分关键词回退把"开始导入"截断为"导入"，匹配到主页面错误元素
    #     3. 中间原因: locator 搜索全页面而非 modal 内，导致部分关键词回退匹配到主页面按钮
    #     4. 根本原因: 当 modal 打开时，目标按钮在 modal 内，但 locator 搜索范围是全页面
    #   全局观: 影响所有"动态modal内按钮定位"场景（上传→提交、新建→确定、编辑→保存等）
    #   职责边界: Step4 locator 层元素定位，不越界
    #   通用性自检:
    #     - 跨系统: Ant Design/ElementUI/Naive UI/Bootstrap/Native HTML 的 Modal/Dialog
    #     - 跨行业: 通用（所有需要 modal 内操作的场景）
    #     - 跨页面: 适用于所有含 modal 的页面
    #     - 覆盖性: 覆盖所有 click 场景（modal 打开时优先在 modal 内搜索）
    #   自我反驳三次:
    #     1) 漏洞？→ modal 内没有目标按钮怎么办？→ 回退到全页面搜索（原逻辑）
    #     2) 更通用？→ 用 Playwright 的 :visible 过滤可见元素，但 modal 内按钮也是可见的
    #     3) 更根本？→ 根本问题是搜索范围，限制到 modal 内是最根本的修复
    #   实施: 检测可见 modal，在 modal 内精确匹配 hint（含空格兼容），命中则返回
    if kind == "click":
        _modal_loc = await _find_visible_modal(page)
        if _modal_loc is not None:
            _modal_hit = await _locate_in_modal(_modal_loc, hint)
            if _modal_hit is not None:
                _log.info(
                    "[locator] v10 modal-aware 精确命中 '%s' (在 modal 内)", hint[:50]
                )
                return _modal_hit

    # 0) 当 hint 无前缀时，默认按 text 尝试
    loc = await _locate_by_hint(page, hint)
    if loc:
        try:
            if await loc.count() > 0:
                return loc
        except Exception as _se28:
            _log.debug("[locator] 异常降级(pass): %s", str(_se28)[:60], exc_info=True)
            pass

    # 1) 核心关键词提取 + 模糊匹配
    core = _extract_core_keyword(hint)
    if core and len(core) >= 2:
        if kind == "upload":
            # 上传控件：先找 input[type=file]
            try:
                upload_loc = page.locator('input[type="file"]').first
                if await upload_loc.count() > 0:
                    return upload_loc
            except Exception as _se29:
                _log.debug(
                    "[locator] 异常降级(pass): %s", str(_se29)[:60], exc_info=True
                )
                pass
            # ★ 通用上传按钮文本：先尝试常见上传相关词
            _UPLOAD_KEYWORDS = (
                "上传",
                "导入",
                "选择文件",
                "browse",
                "upload",
                "import",
            )
            for uk in _UPLOAD_KEYWORDS:
                for tpl in (
                    'button:has-text("{kw}")',
                    'a:has-text("{kw}")',
                    'span:has-text("{kw}")',
                ):
                    try:
                        sel = tpl.format(kw=_escape_css_str(uk))
                        cand = page.locator(sel).first
                        if await cand.count() > 0:
                            return cand
                    except Exception as _se30:
                        _log.debug(
                            "[locator] 异常降级(continue): %s",
                            str(_se30)[:60],
                            exc_info=True,
                        )
                        continue
            # 再用核心关键词找按钮
            for tpl in (
                'button:has-text("{kw}")',
                'a:has-text("{kw}")',
                '[role="button"]:has-text("{kw}")',
            ):
                try:
                    sel = tpl.format(kw=_escape_css_str(core))
                    cand = page.locator(sel).first
                    if await cand.count() > 0:
                        _log.info("[locator] upload 模糊命中 '%s' → '%s'", hint, core)
                        return cand
                except Exception as _se31:
                    _log.debug(
                        "[locator] 异常降级(continue): %s",
                        str(_se31)[:60],
                        exc_info=True,
                    )
                    continue
        else:
            templates = (
                _INPUT_FUZZY_SELECTORS if kind == "input" else _CLICK_FUZZY_SELECTORS
            )
            core_esc = _escape_css_str(core)
            for tpl in templates:
                try:
                    sel = tpl.format(kw=core_esc)
                    cand = page.locator(sel).first
                    if await cand.count() > 0:
                        _log.info("[locator] 模糊回退命中 '%s' → '%s'", hint, core)
                        return cand
                except Exception as _se32:
                    _log.debug(
                        "[locator] 异常降级(continue): %s",
                        str(_se32)[:60],
                        exc_info=True,
                    )
                    continue

    # ★ v7.2 通用修复(2026-07-22): 短文本按钮的空格兼容正则匹配
    #   根因核查(§10.9):
    #     1. 现象: REQ-002 P01 click "确定" 失败，日志 _locate_with_fallback 全部失败
    #     2. 直接原因: button:has-text("确定") 无法匹配 Ant Design 的 <button><span>确 定</span></button>
    #     3. 中间原因: Ant Design Button 组件会把短文本分割成多个 span，textContent 含空格
    #     4. 根本原因: Playwright :has-text() CSS 伪类只支持字符串子串匹配，不支持正则
    #   全局观: 影响所有 Ant Design/ElementUI/Naive UI 的短文本按钮（确定/取消/保存/提交/确认/应用）
    #   职责边界: Step4 locator 层元素定位，不越界
    #   通用性自检:
    #     - 跨系统: 适用于所有 UI 框架（Ant Design/ElementUI/Naive UI/Bootstrap）
    #     - 跨行业: 通用
    #     - 跨页面: 适用于所有含 Modal/Dialog 的页面
    #     - 覆盖性: 覆盖所有调用 _locate_with_fallback 的 click 场景
    #   自我反驳三次:
    #     1) 漏洞？→ 正则可能在长文本按钮上误匹配 → 限制 2-4 字短文本
    #     2) 更通用方案？→ get_by_role(name=regex) 是 Playwright 官方推荐的语义化定位
    #     3) 更根本方案？→ 用 get_by_role + 正则 name，这是 Playwright 原生支持的正则匹配方式
    #   实施: 用 get_by_role(role, name=re.compile(r'\s*字\s*字\s*')) 匹配含空格的按钮文本
    #   ★ 关键修正: :has-text("regex") 是错误的——:has-text 只支持字符串子串匹配
    #     正确方式: get_by_role 的 name 参数支持 re.compile() 正则对象
    if kind == "click" and core and 2 <= len(core) <= 4:
        try:
            import re as _re_v72

            _chars_v72 = list(core)
            _regex_v72 = r"\s*".join(_re_v72.escape(c) for c in _chars_v72 if c)
            if _regex_v72:
                _pattern_v72 = _re_v72.compile(_regex_v72)
                # get_by_role 的 name 参数支持正则对象，匹配 accessible name
                # 遍历所有可点击的 ARIA 角色
                _click_roles_v72 = (
                    "button",
                    "link",
                    "menuitem",
                    "menuitemcheckbox",
                    "menuitemradio",
                    "tab",
                    "treeitem",
                    "option",
                )
                for _role_v72 in _click_roles_v72:
                    try:
                        _role_locs_v72 = page.get_by_role(_role_v72, name=_pattern_v72)
                        _role_count_v72 = await _role_locs_v72.count()
                        if _role_count_v72 > 0:
                            _log.info(
                                "[locator] ✓ v7.2 正则 role 匹配命中 '%s' → '%s' (role=%s, count=%d, pattern=%s)",
                                hint,
                                core,
                                _role_v72,
                                _role_count_v72,
                                _regex_v72[:40],
                            )
                            return _role_locs_v72.first
                    except Exception as _se33:
                        _log.debug(
                            "[locator] 异常降级(continue): %s",
                            str(_se33)[:60],
                            exc_info=True,
                        )
                        continue
                # 兜底: get_by_text + 正则（匹配文本节点，可能返回 span 非 button）
                try:
                    _text_locs_v72 = page.get_by_text(_pattern_v72)
                    _text_count_v72 = await _text_locs_v72.count()
                    if _text_count_v72 > 0:
                        _log.info(
                            "[locator] ✓ v7.2 正则 text 匹配命中 '%s' → '%s' (count=%d, pattern=%s)",
                            hint,
                            core,
                            _text_count_v72,
                            _regex_v72[:40],
                        )
                        return _text_locs_v72.first
                except Exception as _se34:
                    _log.debug(
                        "[locator] 异常降级(pass): %s", str(_se34)[:60], exc_info=True
                    )
                    pass
        except Exception as _e_v72:
            _log.debug("[locator] v7.2 空格正则匹配异常: %s", _e_v72, exc_info=True)

    # 2) get_by_role 语义兜底
    try:
        role_map = {"input": "textbox", "click": "button", "upload": "button"}
        role = role_map.get(kind, "button")
        name = core if core and len(core) >= 2 else hint
        role_loc = page.get_by_role(role, name=name)
        if await role_loc.count() > 0:
            _log.info(
                "[locator] role 回退命中 '%s' → role=%s name=%s", hint, role, name
            )
            return role_loc.first
    except Exception as _se35:
        _log.debug("[locator] 异常降级(pass): %s", str(_se35)[:60], exc_info=True)
        pass

    # 2.5) 部分关键词回退：核心词拆成2-3字片段逐个尝试
    # ★ v10 通用修复(2026-07-22): 短文本(≤4字)不做截断
    #   根因: "开始导入"(4字) 被截断为 "导入"(2字)，匹配到主页面"数据导入"按钮（错误元素）
    #   通用性: 4字以内的 hint 已足够精确，截断反而降低精度导致误匹配
    #   覆盖性: 仅影响 len(core)<=4 的场景，长文本(>4字)仍走截断逻辑
    if core and len(core) >= 3 and len(core) > 4:
        templates = _CLICK_FUZZY_SELECTORS
        # 尝试末尾2-3字（如"数据导入"→"导入"）
        for sub_len in (3, 2):
            if len(core) >= sub_len:
                sub = core[-sub_len:]
                for tpl in templates:
                    try:
                        sel = tpl.format(kw=_escape_css_str(sub))
                        cand = page.locator(sel).first
                        if await cand.count() > 0:
                            _log.info(
                                "[locator] 部分关键词命中 '%s' → '%s' (len=%d)",
                                hint,
                                sub,
                                sub_len,
                            )
                            return cand
                    except Exception as _se36:
                        _log.debug(
                            "[locator] 异常降级(continue): %s",
                            str(_se36)[:60],
                            exc_info=True,
                        )
                        continue
        # 再尝试开头2-3字（如"数据导入"→"数据"）
        for sub_len in (3, 2):
            if len(core) >= sub_len:
                sub = core[:sub_len]
                if sub == core[-sub_len:]:
                    continue  # 已尝试过
                for tpl in templates:
                    try:
                        sel = tpl.format(kw=_escape_css_str(sub))
                        cand = page.locator(sel).first
                        if await cand.count() > 0:
                            _log.info(
                                "[locator] 部分关键词命中 '%s' → '%s' (prefix)",
                                hint,
                                sub,
                            )
                            return cand
                    except Exception as _se37:
                        _log.debug(
                            "[locator] 异常降级(continue): %s",
                            str(_se37)[:60],
                            exc_info=True,
                        )
                        continue

    # 3) 去掉后缀再试（"操作按钮" → "操作"）
    if core and core != hint:
        for tpl in _CLICK_FUZZY_SELECTORS:
            try:
                sel = tpl.format(kw=_escape_css_str(core))
                cand = page.locator(sel).first
                if await cand.count() > 0:
                    _log.info("[locator] 去后缀回退命中 '%s' → '%s'", hint, core)
                    return cand
            except Exception as _se38:
                _log.debug(
                    "[locator] 异常降级(continue): %s", str(_se38)[:60], exc_info=True
                )
                continue

    # 3.5) ★ 通用输入框回退：通过文本标签定位附近的输入框
    #       适用于 Ant Design / ElementUI 等框架的复杂表单结构
    if kind == "input" and core and len(core) >= 2:
        try:
            # 策略A：get_by_label（利用 aria-labelledby 或 for/id 关系）
            label_loc = page.get_by_label(core, exact=False).first
            if await label_loc.count() > 0:
                _log.info("[locator] label 关联命中 '%s' → core='%s'", hint, core)
                return label_loc
        except Exception as _se39:
            _log.debug("[locator] 异常降级(pass): %s", str(_se39)[:60], exc_info=True)
            pass
        with guard("_locate_with_fallback: for text_sel in ( 'label:has-text('{", log=_log):
            # 策略B：找包含核心文本的最近父元素，再找其中的 input
            for text_sel in (
                'label:has-text("{kw}")',
                '.ant-form-item-label:has-text("{kw}")',
                '.el-form-item__label:has-text("{kw}")',
                'td:has-text("{kw}")',
                'div:has-text("{kw}")',
            ):
                try:
                    sel = text_sel.format(kw=_escape_css_str(core))
                    text_el = page.locator(sel).first
                    if await text_el.count() > 0 and await text_el.is_visible():
                        # 找到父元素中的 input
                        parent = text_el.locator("..")
                        input_el = parent.locator("input, textarea").first
                        if await input_el.count() > 0:
                            _log.info(
                                "[locator] 文本关联命中 '%s' → core='%s' sel='%s'",
                                hint,
                                core,
                                sel[:60],
                            )
                            return input_el
                        # 再往上一级
                        grandparent = parent.locator("..")
                        input_el2 = grandparent.locator("input, textarea").first
                        if await input_el2.count() > 0:
                            _log.info(
                                "[locator] 文本关联命中(grandparent) '%s' → core='%s'",
                                hint,
                                core,
                            )
                            return input_el2
                        # ★ 通用修复 (2026-07-17): 找 select/dropdown 元素（Ant Design / ElementUI）
                        for select_sel in (
                            ".ant-select",
                            ".el-select",
                            "select",
                            '[role="combobox"]',
                            '[role="listbox"]',
                        ):
                            select_el = parent.locator(select_sel).first
                            if await select_el.count() > 0:
                                _log.info(
                                    "[locator] 文本关联命中(select) '%s' → core='%s' sel='%s'",
                                    hint,
                                    core,
                                    select_sel,
                                )
                                return select_el
                            select_el2 = grandparent.locator(select_sel).first
                            if await select_el2.count() > 0:
                                _log.info(
                                    "[locator] 文本关联命中(select-grandparent) '%s' → core='%s'",
                                    hint,
                                    core,
                                )
                                return select_el2
                except Exception as _se40:
                    _log.debug(
                        "[locator] 异常降级(continue): %s",
                        str(_se40)[:60],
                        exc_info=True,
                    )
                    continue

    # ★ v7.3 诊断(2026-07-22): 定位全部失败时，列出页面上所有 button 文本，帮助诊断
    #   适用于：按钮存在但文本不匹配 / 按钮不存在 / 按钮在 shadow DOM 中
    if kind == "click":
        with guard("_locate_with_fallback: _btn_texts = []", log=_log):
            _btn_texts = []
            _diag_btns = page.locator(
                "button, [role='button'], .ant-btn, .el-button, input[type='submit'], a.btn"
            )
            _diag_count = await _diag_btns.count()
            for _di in range(min(_diag_count, 30)):
                try:
                    _b = _diag_btns.nth(_di)
                    _t = (
                        (await _b.text_content() or "")
                        + " / "
                        + (await _b.get_attribute("value") or "")
                    ).strip()
                    _vis = await _b.is_visible()
                    _btn_texts.append(f"[{'V' if _vis else 'x'}]{_t[:40]}")
                except Exception as _se41:
                    _log.debug(
                        "[locator] 异常降级(continue): %s",
                        str(_se41)[:60],
                        exc_info=True,
                    )
                    continue
            # 检查是否有 dialog/modal
            _diag_modal = ""
            for _modal_sel in (".ant-modal", ".el-dialog", '[role="dialog"]', ".modal"):
                try:
                    _m = page.locator(_modal_sel)
                    if await _m.count() > 0:
                        _diag_modal = f" modal={_modal_sel}(count={await _m.count()})"
                        break
                except Exception as _se42:
                    _log.debug(
                        "[locator] 异常降级(continue): %s",
                        str(_se42)[:60],
                        exc_info=True,
                    )
                    continue
            _log.warning(
                "[locator] v7.3 诊断: hint='%s' page_buttons(%d)=%s%s",
                hint[:30],
                _diag_count,
                _btn_texts[:15],
                _diag_modal,
            )

    _log.warning(
        "[locator] _locate_with_fallback 全部失败: hint='%s' kind='%s'", hint, kind
    )
    return None
