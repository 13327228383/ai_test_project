# -*- coding: utf-8 -*-
"""基础定位模块 — 从 locator.py 拆分（§4.3 单文件≤800行）。

职责：_locate_by_hint（前缀分派定位）+ _locate_with_fuzzy_fallback（模糊回退）
      + _find_visible_modal + _locate_in_modal（Modal内定位）。
依赖：_hint_utils（_PREFIX_HANDLERS等）+ _fuzzy_selectors。
"""
import inspect
import logging
import re

from src.pipeline.steps.step4.playwright_engine.selectors import _escape_css_str
from src.pipeline.steps.step4.browser_tools._hint_utils import (
    _normalize_hint,
    _extract_core_keyword,
    _PREFIX_HANDLERS,
    split_candidate_hint,
)
from src.pipeline.steps.step4.browser_tools._fuzzy_selectors import (
    _CLICK_FUZZY_SELECTORS,
    _INPUT_FUZZY_SELECTORS,
)

_log = logging.getLogger(__name__)


# ── 语义类 hint 兜底（描述性 hint → 元素族选择器）──
#   根因(2026-09-27): 生成端产出"主输入框/新增按钮/分类下拉框/确认复选框"等描述性 hint，
#     页面元素文本往往只是动作词或字段名，"主输入框" 这类整体文本匹配必然 0 命中，
#     执行端降级 AI 自由探索 → 多轮不收敛 → HARNESS_ERROR。
#   通用修复: 按通用 UI 语义映射到元素族，再用剩余关键词做 label/placeholder/name 作用域收敛。
#   通用性: 语义词是通用 UI 术语，零业务硬编码。
_INPUT_FAMILY_SEL = (
    'input[type="text"], input[type="search"], input[type="password"], '
    'input[type="email"], input[type="tel"], input[type="url"], '
    'input[type="number"], input:not([type]), textarea, [role="textbox"]'
)
_BUTTON_FAMILY_SEL = (
    'button, [role="button"], input[type="submit"], '
    'input[type="button"], a[role="button"]'
)
_SELECT_FAMILY_SEL = 'select, [role="combobox"]'
_CHECKBOX_FAMILY_SEL = 'input[type="checkbox"], [role="checkbox"]'
_LINK_FAMILY_SEL = 'a, [role="link"]'

# (语义关键词, 元素族选择器, 需剥除的后缀词)
_SEMANTIC_CLASSES = (
    (
        ("输入框", "文本框", "输入域", "textbox"),
        _INPUT_FAMILY_SEL,
        ("输入框", "文本框", "输入域"),
    ),
    (("按钮", "button"), _BUTTON_FAMILY_SEL, ("按钮",)),
    (
        ("下拉框", "下拉菜单", "下拉", "选择框", "combobox", "select"),
        _SELECT_FAMILY_SEL,
        ("下拉框", "下拉菜单", "选择框", "下拉"),
    ),
    (("复选框", "checkbox"), _CHECKBOX_FAMILY_SEL, ("复选框",)),
    (("链接", "link"), _LINK_FAMILY_SEL, ("链接",)),
)

# 通用修饰词（剥除后无区分度 → 允许退化为"元素族的第一个元素"）
_GENERIC_HINT_WORDS = {
    "主", "主要", "主输入", "第一个", "第一", "首选", "默认",
    "表单", "内容", "字段", "项", "值",
}


async def _locate_by_semantic_signature(page, family: str, scope_words: list[str]):
    """字面收敛失败后的语义兜底：枚举元素族候选，按可访问性语义签名选最佳。

    只用本地确定性语义（local_semantics.best_candidate），不发起 LLM 调用，
    避免每条用例增加云端往返。
    返回 Locator 或 None（候选均无语义决断时不硬凑，交由上层继续兜底）。
    """
    if not scope_words or not family:
        return None
    try:
        from src.ai.local_semantics import best_candidate as _bc
    except Exception:  # noqa: BLE001
        _log.debug("[locator] 本地语义不可用，跳过签名兜底", exc_info=True)
        return None
    try:
        _sigs = await page.evaluate(
            """(sel) => Array.from(document.querySelectorAll(sel))
                 .map((el, i) => {
                    if (!el) return null;
                    const r = el.getBoundingClientRect();
                    if (r.width <= 0 || r.height <= 0) return null;
                    let lab = '';
                    if (el.id) {
                        const l = document.querySelector('label[for="' + el.id + '"]');
                        if (l) lab = (l.textContent || '').trim();
                    }
                    const s = [el.getAttribute('placeholder'),
                               el.getAttribute('aria-label'),
                               el.getAttribute('name'),
                               el.getAttribute('id'),
                               el.getAttribute('title'),
                               el.getAttribute('data-testid'),
                               lab].filter(Boolean).join(' | ');
                    return s ? {i: i, s: s} : null;
                 })""",
            family,
        )
    except Exception as _e:  # noqa: BLE001
        _log.debug("[locator] 语义签名采集异常: %s", _e, exc_info=True)
        return None
    _items = [x for x in (_sigs or []) if isinstance(x, dict) and x.get("s")]
    if not _items:
        return None
    for _w in scope_words:
        _hit = _bc(_w, [str(x["s"]) for x in _items])
        if not _hit:
            continue
        for _x in _items:
            if str(_x["s"]) == _hit[0]:
                _log.info(
                    "[locator] ✓ 语义签名命中: hint=%r → %r (score=%.2f)",
                    _w[:30],
                    _hit[0][:40],
                    _hit[1],
                )
                return page.locator(family).nth(int(_x["i"]))
    _log.debug(
        "[locator] 语义签名无决断: words=%s 候选=%d",
        [w[:12] for w in scope_words],
        len(_items),
    )
    return None


async def _locate_by_semantic_class(page, hint: str):
    """把描述性 hint 按 UI 语义类映射到元素族，返回 Locator 或 None。

    仅当关键词有区分度时做作用域收敛（label/placeholder/name）；关键词无区分度
    （如"主输入框"）时退化为元素族第一个元素；关键词有区分度但未命中则返回 None，
    交由上层 self-healing 处理，避免误命中错误元素造成假执行。
    """
    if not hint:
        return None
    _lower = hint.lower()
    _matched = None
    for _keys, _family, _suffixes in _SEMANTIC_CLASSES:
        if any((k in hint) or (k in _lower) for k in _keys):
            _matched = (_family, _suffixes)
            break
    if not _matched:
        return None
    _family, _suffixes = _matched

    _kw = _normalize_hint(hint)
    for _sfx in _suffixes:
        if _kw.endswith(_sfx):
            _kw = _kw[: -len(_sfx)]
    _kw = re.sub(r"^(请|点击|输入|选择|在|查找|搜索|打开)\s*", "", _kw).strip()
    # ★ 同义候选拆词：生成端/Agent 写 "名称/姓名输入框"、"价格、单价输入框" 这类多候选，
    #   拿整体词去 label/placeholder 匹配恒 0 命中 → fill/click 定位失败（实测 fullcov_sut 新增页）。
    #   通用性：分隔符是通用写法，零业务硬编码。
    _scope_words: list[str] = []
    for _piece in [_kw, *re.split(r"[/、,，|]", _kw)]:
        _piece = _piece.strip()
        for _sfx in _suffixes:
            if _piece.endswith(_sfx) and len(_piece) > len(_sfx):
                _piece = _piece[: -len(_sfx)]
        _piece = _piece.strip()
        if (
            len(_piece) >= 2
            and _piece not in _GENERIC_HINT_WORDS
            and _piece not in _scope_words
        ):
            _scope_words.append(_piece)

    _is_generic = not _scope_words

    if _is_generic:
        _cands = [page.locator(_family)]
    else:
        # ★ 作用域收敛必须用链式/过滤定位：不能把逗号列表选择器拼在 scoped 前缀之后，
        #   否则只有第一个分支被限定，其余分支会裸匹配到无关元素（导致误命中假执行）。
        _cands = []
        for _w in _scope_words:
            _esc = _escape_css_str(_w)
            _cands += [
                page.locator(f'label:has-text("{_esc}")').locator(_family),
                page.get_by_label(_w),
                page.locator(f'[placeholder*="{_esc}"]'),
                page.locator(f'[aria-label*="{_esc}"]'),
                page.locator(
                    f'input[name*="{_esc}"], textarea[name*="{_esc}"], select[name*="{_esc}"]'
                ),
                page.locator(_family).filter(has_text=_w),
            ]

    for _cand in _cands:
        try:
            _cand = _cand.first
            if await _cand.count() > 0:
                _log.info(
                    "[locator] ✓ 语义类定位命中: hint='%s'",
                    hint[:30],
                )
                return _cand
        except Exception as _e:
            _log.debug("[locator] 语义类定位异常: %s", _e, exc_info=True)

    # 语义签名兜底：控件文案是产品措辞（"按名称搜索"）而 hint 是需求术语（"查询"），
    # 两者不同形，纯字面收敛必然 0 命中 → 降级自由探索甚至判失败。
    if not _is_generic:
        _sig_loc = await _locate_by_semantic_signature(page, _family, _scope_words)
        if _sig_loc is not None:
            return _sig_loc
    return None


async def _locate_by_hint(page, target_hint: str):
    """根据人类可读描述定位元素。支持格式: text=xxx, placeholder=xxx, label=xxx, id=xxx, name=xxx, ai_id=xxx, role=xxx, css=xxx, tag=xxx, 默认按文本匹配。"""
    hint = (target_hint or "").strip()
    if not hint:
        return None

    # ★ 通用修复(2026-09-27): 候选组拆分 —— 生成端会产出"保存,提交,确定,确认"这类候选
    #   hint（任一命中即可），此前按整体文本匹配必然 0 命中。
    #   分隔符与拆分实现统一委托 `_hint_utils.split_candidate_hint`（单一事实源），
    #   使其它消费同一 hint 的判定（可点击性校验等）不会因各自解析而漂移。
    _cands = split_candidate_hint(hint)
    if len(_cands) > 1:
        for _cand in _cands:
            try:
                _cand_loc = await _locate_by_hint(page, _cand)
                if _cand_loc and (await _cand_loc.count()) > 0:
                    _log.info(
                        "[locator] ✓ 候选命中: hint='%s' → '%s'", hint[:40], _cand
                    )
                    return _cand_loc
            except Exception as _e_cand:
                _log.debug("[locator] 候选尝试异常: %s", _e_cand, exc_info=True)

    # ★ 内嵌属性直取：AI 常把真实属性写进 hint 括号里（如 "名称输入框(name=name)"）。
    #   这是**精确**信息（优于任何文本模糊）；原实现只做整体文本匹配，恒 0 命中。
    #   通用性：name/id 是 HTML 标准属性，零业务硬编码。
    _m_attr = re.search(
        r"[（(]\s*(name|id)\s*=\s*['\"]?([A-Za-z_][\w.\-]*)['\"]?\s*[）)]", hint
    )
    if _m_attr:
        _a_key, _a_val = _m_attr.group(1), _m_attr.group(2)
        try:
            _a_loc = (
                page.locator("#" + _a_val)
                if _a_key == "id"
                else page.locator('[name="' + _escape_css_str(_a_val) + '"]')
            ).first
            if await _a_loc.count() > 0:
                _log.info(
                    "[locator] ✓ 内嵌属性直取命中: hint='%s' → %s=%s",
                    hint[:40],
                    _a_key,
                    _a_val,
                )
                return _a_loc
        except Exception as _e_attr:
            _log.debug("[locator] 内嵌属性直取异常: %s", _e_attr, exc_info=True)

    # ── 前缀列表映射分派 ──
    for prefix, handler in _PREFIX_HANDLERS:
        if hint.startswith(prefix):
            query = hint[len(prefix) :].strip()
            if query:
                try:
                    result = handler(page, query)
                    if inspect.iscoroutine(result):
                        return await result
                    return result
                except Exception as _e:
                    _log.debug("[locator] _locate_by_hint 异常: %s", _e, exc_info=True)

    # ★ CSS ID 选择器 (如 #ai-2) — 直接按 ID 查找，不转为 :text()
    if hint.startswith("#") and len(hint) > 1:
        try:
            loc = page.locator(hint).first
            if await loc.count() > 0:
                return loc
        except Exception as _e:
            _log.debug("[locator] CSS ID 选择器异常: %s", _e, exc_info=True)
        # ID 不存在 → 不继续用 #ai-2 做文本匹配（无意义），返回 None 触发上层回退
        return None

    # ★ 通用修复 (2026-07-17): CSS 类选择器直接定位
    #   AI 可能生成 li.ant-menu-item-selected 等复杂 CSS 选择器，
    #   尝试直接用 Playwright 定位，失败后回退到文本匹配
    if "." in hint and any(
        hint.startswith(tag)
        for tag in (
            "a.",
            "li.",
            "div.",
            "span.",
            "button.",
            "input.",
            "ul.",
            "nav.",
            "tr.",
            "td.",
            "th.",
        )
    ):
        try:
            loc = page.locator(hint).first
            if await loc.count() > 0:
                _log.info("[locator] CSS 选择器直接匹配成功: %s", hint[:60])
                return loc
        except Exception as _e:
            _log.debug("[locator] CSS 选择器异常: %s", _e, exc_info=True)
        # 选择器不存在 → 返回 None，触发上层 self-healing 回退（含 css_simplify 策略）
        return None

    # ★ 交互元素优先(2026-10-14)：按文本定位时，先解析到**可交互元素族**，
    #   再退回"任意含该文本的元素"。
    #   根因：下方默认分支用 `page.get_by_text(hint)`，会命中任何含该文本的元素
    #     —— 表头 `<th>新增</th>`、标题 `<h2>新增资产</h2>`、`<label>` 等文本节点
    #     都会抢先命中；执行端 `_check_element_exists` 随后正确地判"文本命中但非
    #     可点击元素"→ 触发 self-healing → 结构化重放失败 → 回退 AI（实测
    #     TC-REQ-022-UX02 / TC-REQ-024-V02 等 32 条「click(新增/编辑) 页面无变化」）。
    #   通用性：UI 语义角色（link/button/summary）是 Web 标准，零业务硬编码；
    #     无交互元素命中时不改变既有行为（仍走 get_by_text 兜底）。
    _esc_ip = _escape_css_str(hint)
    for _ip_cand in (
        page.get_by_role("link", name=hint, exact=False),
        page.get_by_role("button", name=hint, exact=False),
        page.get_by_role("tab", name=hint, exact=False),
        page.get_by_role("menuitem", name=hint, exact=False),
        page.locator(f'a:has-text("{_esc_ip}")'),
        page.locator(f'button:has-text("{_esc_ip}")'),
        page.locator(f'summary:has-text("{_esc_ip}")'),
        page.locator(f'[role="button"]:has-text("{_esc_ip}")'),
        page.locator(f'[role="link"]:has-text("{_esc_ip}")'),
        page.locator(
            f'input[type="submit"][value*="{_esc_ip}"], '
            f'input[type="button"][value*="{_esc_ip}"], '
            f'input[type="reset"][value*="{_esc_ip}"]'
        ),
    ):
        try:
            _ip = _ip_cand.first
            if await _ip.count() > 0:
                _log.info(
                    "[locator] ✓ 交互元素优先命中: hint='%s' → %s",
                    hint[:30],
                    (await _ip.evaluate("el => el.tagName.toLowerCase()") or "?"),
                )
                return _ip
        except Exception as _e_ip:
            _log.debug("[locator] 交互元素优先异常: %s", _e_ip, exc_info=True)

    # 默认：按文本匹配 —— 精确优先，子串兜底（顺序唯一权威）
    # 为什么：原实现直接用子串匹配再取首个元素，页面上"包含关键词的更早元素"会抢先
    # 命中（如资产名含"编辑"，先于"编辑"操作链接），点击落到错误页面。
    # 精确匹配优先最贴近 hint 意图；无精确命中时行为与原先一致（子串兜底）。
    for _exact in (True, False):
        try:
            locator = page.get_by_text(hint, exact=_exact).first
            if locator and (await locator.count()) > 0:
                return locator
        except Exception as _e:
            _log.debug(
                "[locator] 文本匹配(exact=%s)异常: %s", _exact, _e, exc_info=True
            )

    # ★ 通用修复(2026-07-21 v4): Ant Design Button 文本含空格的通用匹配
    #   原BUG: Ant Design Button 组件会把按钮文本分割成多个 span，
    #          导致 text_content() 返回"确 定"（中间含空格），
    #          get_by_text("确定") 子串匹配不到"确 定"。
    #   通用修复: 当文本匹配失败时，尝试以下通用策略：
    #     1. 在文本字符间插入可选空格：get_by_text("确 定")（正则 \s*）
    #     2. 用 CSS :has-text() 配合 re 匹配（去除空格后比较）
    #   通用性: 适用于所有 Ant Design/ElementUI/Naive UI 等基于 Button 组件的系统
    #   合理性: 这是 UI 框架渲染特性，不是业务逻辑
    #   覆盖性: 覆盖所有 button 文本含空格的场景（确定/取消/保存/提交/确认/应用/删除）
    try:
        import re as _re_space

        # 在每个字符间插入 \s* 形成正则
        _chars = list(hint)
        _regex_pattern = r"\s*".join(_re_space.escape(c) for c in _chars if c)
        if _regex_pattern:
            # Playwright get_by_text 支持 text=/regex/ 格式
            _regex = _re_space.compile(_regex_pattern)
            _space_loc = page.get_by_text(_regex).first
            if _space_loc and (await _space_loc.count()) > 0:
                _log.info(
                    "[locator] ✓ 空格兼容匹配成功: hint='%s' pattern='%s'",
                    hint[:30],
                    _regex_pattern[:60],
                )
                return _space_loc
    except Exception as _e_space:
        _log.debug("[locator] 空格兼容匹配异常: %s", _e_space, exc_info=True)

    # ★ 通用修复(2026-09-26): 描述性短语后缀剥离后的文本匹配
    #   根因: Agent 倾向于用"登录按钮/保存按钮/用户名输入框"等描述性短语指代元素，
    #     但页面上元素的实际文本只有动作词（"登录"/"保存"）或标签词（"用户名"）。
    #     get_by_text("登录按钮") 匹配不到只有"登录"文本的按钮 → 元素定位失败。
    #   通用性: 后缀词（按钮/输入框/下拉框/链接等）是通用 UI 术语，零业务硬编码。
    #   位置: 在空格兼容之后、placeholder 之前——按钮/链接等可点击元素的文本匹配
    #     失败优先用此策略，而非降级到 placeholder（那是输入框的路）。
    try:
        import re as _re_suffix

        _stripped = _re_suffix.sub(
            r"(按钮|输入框|文本框|下拉框|选择框|选择器|控件|标签|链接|菜单|选项|图标)$",
            "",
            hint,
        ).strip()
        if _stripped and _stripped != hint:
            _suffix_loc = page.get_by_text(_stripped, exact=False).first
            if _suffix_loc and (await _suffix_loc.count()) > 0:
                _log.info(
                    "[locator] ✓ 后缀剥离匹配成功: hint='%s' → '%s'",
                    hint[:30],
                    _stripped[:30],
                )
                return _suffix_loc
            # 剥离后再试空格兼容（Ant Design 按钮的"登 录"情况）
            _chars2 = list(_stripped)
            _pat2 = r"\s*".join(_re_suffix.escape(c) for c in _chars2 if c)
            if _pat2:
                _reg2 = _re_suffix.compile(_pat2)
                _sp2 = page.get_by_text(_reg2).first
                if _sp2 and (await _sp2.count()) > 0:
                    _log.info(
                        "[locator] ✓ 后缀剥离+空格兼容匹配成功: hint='%s' → '%s'",
                        hint[:30],
                        _stripped[:30],
                    )
                    return _sp2
    except Exception as _e_suffix:
        _log.debug("[locator] 后缀剥离匹配异常: %s", _e_suffix, exc_info=True)

    # ★ 通用修复(2026-09-27): 语义类兜底（"主输入框"/"分类下拉框"等描述性 hint）
    try:
        _sem_loc = await _locate_by_semantic_class(page, hint)
        if _sem_loc is not None:
            return _sem_loc
    except Exception as _e_sem:
        _log.debug("[locator] 语义类定位异常: %s", _e_sem, exc_info=True)

    # ★ Fix50 (2026-07-09): 文本匹配失败后，尝试 placeholder 属性匹配
    # ★ Fix (2026-07-10): 完整 placeholder 匹配失败时，提取关键词模糊匹配
    try:
        escaped = _escape_css_str(hint)
        ph_loc = page.locator(
            'input[placeholder*="'
            + escaped
            + '"], textarea[placeholder*="'
            + escaped
            + '"]'
        ).first
        if await ph_loc.count() > 0:
            return ph_loc
    except Exception as _e:
        _log.debug("[locator] placeholder fallback 异常: %s", _e, exc_info=True)

    # ★ Fix: 关键词提取 + 模糊 placeholder 匹配
    try:
        import re as _re

        _clean_hint = _re.sub(r"^(请|点击|输入|选择|在|查找|搜索)\s*", "", hint)
        _clean_hint = _re.sub(
            r"(按钮|输入框|下拉框|选择器|控件|标签|链接|文本|区域|表单)$",
            "",
            _clean_hint,
        )
        _keywords = _re.findall(r"[\u4e00-\u9fff]{2,4}", _clean_hint)
        for _kw in _keywords:
            _esc_kw = _escape_css_str(_kw)
            _ph = page.locator(
                'input[placeholder*="'
                + _esc_kw
                + '"], textarea[placeholder*="'
                + _esc_kw
                + '"], '
                'input[aria-label*="' + _esc_kw + '"], '
                'input[name*="' + _esc_kw + '"]'
            ).first
            if await _ph.count() > 0:
                _log.info(
                    "[locator] placeholder 关键词模糊匹配成功: hint='%s' kw='%s'",
                    hint[:30],
                    _kw,
                )
                return _ph
    except Exception as _e:
        _log.debug("[locator] 关键词模糊匹配异常: %s", _e, exc_info=True)

    # 回退：Playwright :text() 伪选择器（需转义引号）
    try:
        escaped = _escape_css_str(hint)
        return page.locator(':text("' + escaped + '")').first
    except Exception as _e:
        _log.debug("[locator] 非关键异常: %s", _e, exc_info=True)
        return None




async def _locate_with_fuzzy_fallback(page, hint: str, kind: str = "click"):
    """先走 _locate_by_hint 精确匹配；失败后用核心关键词子串匹配兜底。

    Args:
        page: Playwright Page 实例
        hint: 原始 target_hint（可含前缀）
        kind: "click" → 按钮类选择器；"input" → 输入框类选择器

    Returns:
        Locator 或 None
    """
    # 1) 精确匹配
    loc = await _locate_by_hint(page, hint)
    if loc:
        try:
            if await loc.count() > 0:
                return loc
        except Exception as _e:
            _log.debug(
                "[locator] fuzzy_fallback 精确匹配计数异常: %s", _e, exc_info=True
            )

    # 2) 核心关键词模糊匹配
    core = _extract_core_keyword(hint)
    if not core or len(core) < 2:
        return None
    templates = _INPUT_FUZZY_SELECTORS if kind == "input" else _CLICK_FUZZY_SELECTORS
    core_esc = _escape_css_str(core)
    for tpl in templates:
        try:
            sel = tpl.format(kw=core_esc)
            cand = page.locator(sel).first
            if await cand.count() > 0:
                _log.info(
                    "[locator] 模糊匹配命中 '%s' → '%s' (sel=%s)", hint, core, tpl[:40]
                )
                return cand
        except Exception as _e:
            _log.debug("[locator] 模糊匹配异常: %s", _e, exc_info=True)
            continue

    # ★ v7.1 通用修复(2026-07-22): 短文本按钮的空格兼容正则匹配
    #   根因: Ant Design/ElementUI Button 组件会把文本分割成多个 span，
    #         导致 textContent 为"确 定"（含空格），button:has-text("确定") 无法匹配。
    #   通用修复: 对短文本（2-4字）按钮，用 Playwright 的 text=/regex/ 形式，
    #             在每个字符间插入 \s*，匹配"确 定"、"取消"、"保 存"等。
    #   通用性: 适用于所有 UI 框架的 Button 文本含空格场景
    #   覆盖性: 仅在普通 has-text 匹配失败后触发，不影响正常匹配
    if kind == "click" and 2 <= len(core) <= 4:
        try:
            import re as _re_v7

            _chars_v7 = list(core)
            _regex_v7 = r"\s*".join(_re_v7.escape(c) for c in _chars_v7 if c)
            if _regex_v7:
                _space_selectors = (
                    'button:has-text("'
                    + _regex_v7
                    + '")',  # Playwright 支持 text=/regex/
                    '.ant-modal-footer button:has-text("' + _regex_v7 + '")',
                    '.ant-btn:has-text("' + _regex_v7 + '")',
                    '.el-button:has-text("' + _regex_v7 + '")',
                )
                for _sp_sel in _space_selectors:
                    try:
                        _sp_cand = page.locator(_sp_sel).first
                        if await _sp_cand.count() > 0:
                            _log.info(
                                "[locator] ✓ 空格正则匹配命中 '%s' → '%s' (sel=%s)",
                                hint,
                                core,
                                _sp_sel[:50],
                            )
                            return _sp_cand
                    except Exception as _se1:
                        _log.debug(
                            "[locator] 异常降级(continue): %s",
                            str(_se1)[:60],
                            exc_info=True,
                        )
                        continue
        except Exception as _e_v7:
            _log.debug("[locator] 空格正则匹配异常: %s", _e_v7, exc_info=True)

    # 3) get_by_role 语义兜底
    #   ★ v6 通用修复(2026-07-20): 原逻辑只查 button/textbox 两个角色，
    #     导致 menuitem/tab/treeitem 等角色无法被语义兜底命中。
    #     修复: kind=click 时遍历所有 W3C ARIA 可点击角色。
    try:
        if kind == "input":
            _input_roles = ("textbox", "combobox", "spinbutton", "searchbox")
            for _r in _input_roles:
                try:
                    role_loc = page.get_by_role(_r, name=core)
                    if await role_loc.count() > 0:
                        _log.info(
                            "[locator] role 语义匹配命中 '%s' → '%s' (role=%s)",
                            hint,
                            core,
                            _r,
                        )
                        return role_loc.first
                except Exception as _se2:
                    _log.debug(
                        "[locator] 异常降级(continue): %s",
                        str(_se2)[:60],
                        exc_info=True,
                    )
                    continue
        else:
            # ★ v6: 遍历所有可点击的 ARIA 角色，不止 button
            _click_roles = (
                "button",
                "link",
                "menuitem",
                "menuitemcheckbox",
                "menuitemradio",
                "tab",
                "treeitem",
                "option",
                "checkbox",
                "radio",
                "switch",
            )
            for _r in _click_roles:
                try:
                    role_loc = page.get_by_role(_r, name=core)
                    if await role_loc.count() > 0:
                        _log.info(
                            "[locator] role 语义匹配命中 '%s' → '%s' (role=%s)",
                            hint,
                            core,
                            _r,
                        )
                        return role_loc.first
                except Exception as _se3:
                    _log.debug(
                        "[locator] 异常降级(continue): %s",
                        str(_se3)[:60],
                        exc_info=True,
                    )
                    continue
    except Exception as _e:
        _log.debug("[locator] role 匹配异常: %s", _e, exc_info=True)

    return None


# ★ v10 通用修复(2026-07-22): Modal-aware 定位辅助函数
async def _find_visible_modal(page):
    """检测页面上是否有可见的 modal/dialog，返回其 Locator 或 None。

    通用检测策略（覆盖常见 UI 框架）：
      - Ant Design: .ant-modal, .ant-drawer-content
      - ElementUI: .el-dialog, .el-drawer
      - Naive UI: .n-modal, .n-drawer-content
      - Bootstrap: .modal.show, .modal
      - 通用: [role="dialog"], [role="alertdialog"]
    """
    _modal_selectors = [
        ".ant-modal",
        ".el-dialog",
        '[role="dialog"]',
        '[role="alertdialog"]',
        ".modal",
        ".n-modal",
        ".ant-drawer-content",
        ".el-drawer",
    ]
    for _sel in _modal_selectors:
        try:
            _loc = page.locator(_sel).first
            if await _loc.count() > 0:
                try:
                    if await _loc.is_visible(timeout=500):
                        return _loc
                except Exception:
                    # is_visible 超时也算存在（可能正在渲染）
                    return _loc
        except Exception as _se4:
            _log.debug(
                "[locator] 异常降级(continue): %s", str(_se4)[:60], exc_info=True
            )
            continue
    return None


async def _locate_in_modal(modal_loc, hint: str):
    """在 modal 内精确匹配 hint 文本对应的按钮元素。

    匹配策略（按优先级）：
      1. 精确文本匹配（get_by_text，含空格兼容）
      2. 按钮选择器 + :has-text（覆盖各种 button 变体）
      3. 正则匹配（处理 Ant Design 按钮文本含空格的情况）
    """
    import re as _re_modal

    _hint_clean = _normalize_hint(hint).strip()
    if not _hint_clean:
        return None

    # 策略1: 在 modal 内 get_by_text 精确匹配
    try:
        _text_loc = modal_loc.get_by_text(_hint_clean, exact=False).first
        if await _text_loc.count() > 0:
            _log.info("[locator] v10 modal 内 text 匹配命中 '%s'", _hint_clean[:30])
            return _text_loc
    except Exception as _se5:
        _log.debug("[locator] 异常降级(pass): %s", str(_se5)[:60], exc_info=True)
        pass

    # 策略2: 在 modal 内用按钮选择器 + :has-text
    _esc = _escape_css_str(_hint_clean)
    _btn_selectors = [
        f'button:has-text("{_esc}")',
        f'[role="button"]:has-text("{_esc}")',
        f'.ant-btn:has-text("{_esc}")',
        f'.el-button:has-text("{_esc}")',
        f'a:has-text("{_esc}")',
        f'span:has-text("{_esc}")',
    ]
    for _sel in _btn_selectors:
        try:
            _cand = modal_loc.locator(_sel).first
            if await _cand.count() > 0:
                _log.info(
                    "[locator] v10 modal 内 button 选择器命中 '%s' (sel=%s)",
                    _hint_clean[:30],
                    _sel[:50],
                )
                return _cand
        except Exception as _se6:
            _log.debug(
                "[locator] 异常降级(continue): %s", str(_se6)[:60], exc_info=True
            )
            continue

    # 策略3: 正则匹配（处理按钮文本含空格，如"开 始 导 入"）
    try:
        _chars = list(_hint_clean)
        _regex_pattern = r"\s*".join(_re_modal.escape(c) for c in _chars if c)
        if _regex_pattern:
            _regex = _re_modal.compile(_regex_pattern)
            _regex_loc = modal_loc.get_by_text(_regex).first
            if await _regex_loc.count() > 0:
                _log.info(
                    "[locator] v10 modal 内正则匹配命中 '%s' (pattern=%s)",
                    _hint_clean[:30],
                    _regex_pattern[:40],
                )
                return _regex_loc
    except Exception as _se7:
        _log.debug("[locator] 异常降级(pass): %s", str(_se7)[:60], exc_info=True)
        pass

    return None
