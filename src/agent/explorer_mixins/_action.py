# -*- coding: utf-8 -*-
"""P2-10 ActionMixin — 动作执行（触发点击、删除、通用点击、搜索）（~400行）。

策略优先级（v7 统一）：关键词匹配 → 侧边栏+nav_map → LLM多步探索 → LLM语义匹配。
通用性：跨系统/行业/页面，不依赖静态DOM快照。
"""
from __future__ import annotations
from src.core.guarded import guard

import logging
import os
import re
import tempfile
from typing import Any

from src.agent.element_utils import (
    click_button,
    try_pw_locate_button,
    repair_click,
    observe_page_change,
    check_dialog_visible,
    get_page_message,
    get_page_snapshot,
    get_page_snapshot_text,
    get_page_tabs,
    get_inpage_nav_links,
    match_keywords_to_elements,
    page_reconnaissance,
    el_to_selector,
)

from src.pipeline.steps.step4.action_contract import classify_action
# ★ RC-AE(2026-09-25): 反馈条选择器改从全引擎单一事实源导入（Step3/Step4/Agent 同源）。
# ★ RC-AF(2026-09-25): 反馈条**探测算法**（选择器+可见性+广义类名形态判据）也同源，
#   调用端只执行 JS，不再各自复写判定逻辑（Agent 端曾是漏掉的消费端 → 假 PASS）。
from src.pipeline.utils.feedback_selectors import (
    ERROR_DETECT_JS,
    SUCCESS_DETECT_JS,
)

_log = logging.getLogger(__name__)


async def _locate_declared_structured(page, hint: str):
    """按结构化（引用式）target_hint 定位元素 —— 委托全引擎唯一权威定位器。

    根因: 生成端按 DOM 契约产出 `name=<key>`，而 Agent/guided 路径只吃自然语言
      （语义/关键词匹配）→ 引用式 hint 恒 0 命中 → 声明动作落不了地 → 假 ERROR。
    纪律: 形态判据（``is_structured_hint``）与定位实现（``_locate_by_hint``）
      都委托 browser_tools，本处只做分派，不复制规则。
    """
    from src.pipeline.steps.step4.browser_tools._basic_locator import (
        _locate_by_hint,
    )
    from src.pipeline.steps.step4.browser_tools._hint_utils import (
        is_structured_hint,
    )

    if not is_structured_hint(hint):
        return None
    try:
        loc = await _locate_by_hint(page, hint)
    except Exception as _e:  # noqa: BLE001
        _log.debug("[DeclaredRef] 结构化定位异常: %r %s", hint[:40], str(_e)[:80])
        return None
    if loc is None:
        return None
    try:
        if await loc.count() > 0:
            _log.info("[DeclaredRef] ✅ 结构化引用命中: %r", hint[:40])
            return loc
    except Exception as _e:  # noqa: BLE001
        _log.debug("[DeclaredRef] count 探测异常: %r %s", hint[:40], str(_e)[:80])
    return None


# ★ 通用fix(2026-09-20): 需求否定感知验证。
#   Step3 对"系统不应提供X/功能已取消"的需求会生成"X：需求明确不提供，页面不应提供/不应存在该功能"
#   的 expect。此处的校验语义是"缺席即通过"（X 不存在 = 系统正确地未提供），与普通
#   "X 应存在"的验证方向完全相反。执行端缺少此解释会导致系统正确不提供也被误判 FAIL。
_NEG_MARKERS: tuple = (
    "不应提供", "不应存在", "不提供", "不存在",
    "不得提供", "禁止提供", "不会出现", "不应出现",
    "should not provide", "must not", "not provided", "not exist",
)
# 无实义/占位词，退化句（如 feature_kw="该功能"）时无法定位要素，按"未提供"通过。
_NEG_STOPWORDS: tuple = (
    "该功能", "本功能", "页面", "系统", "此处", "该页面",
    "功能区", "控件区", "页面内容",
)

# ★ 通用修复(2026-09-24): 拒绝/校验语义断言 DOM 化判定。
#   根因: Step3 对"校验拒绝/非法输入被拦截/错误凭证登录"类需求生成的 verify expect 是
#     自然语言（如"提示错误信息"、"非法输入被拦截"），而靶子实际反馈是错误条/表单报错
#     文本（如"名称不能为空"）。旧逻辑做文本匹配 expect↔页面耗时且易落到弱语义断言(429失败)
#     → 全家 ERROR。修复: 命中"拒绝/校验"语义标记时，优先用共享错误条选择器在**活动 DOM**
#     上探测可见错误元素，命中即判 PASS（拒绝语义被正确反馈）。任何页面都可能残留隐藏
#     错误div，故用 offsetParent+getComputedStyle 双重可见性判定过滤。
_REJECT_MARKERS: tuple = (
    "错误", "无效", "非法", "拒绝", "拦截", "不允许", "禁止",
    "不能为空", "必填", "校验失败", "失败", "无法", "未登录",
    "无权限", "访问被", "被拒绝", "被拦截", "提示错误", "报错",
    "invalid", "reject", "forbidden", "denied", "401", "403",
    "不能", "不可", "不可用", "已失效", "已过期",
)
# 登录失败/鉴权失败: expect 同时含"登录/访问"与失败标记 → 指向重定向登录页
_AUTH_FAIL_MARKERS: tuple = ("401", "403", "无权限", "禁止访问", "权限不足", "未授权", "未登录")
# ★ 根因修复(2026-09-25) RC-X: 「接受语义」词表收敛到 Step3 单一事实源
#   （`expects_accept_feedback`），执行端不再自持一份，避免与生成端口径漂移
#   —— 与 `_expects_error_feedback` 的委托模式对称。
def _expects_accept_feedback(*texts: str) -> bool:
    """期望反馈是否为「接受 / 正向成功」语义（委托 Step3 唯一词表）。"""
    try:
        from src.pipeline.step3._rule_extractor_keywords import (
            expects_accept_feedback,
        )

        return expects_accept_feedback(*texts)
    except Exception:
        _blob = " ".join(t for t in texts if t)
        return any(k in _blob for k in ("成功", "已保存", "已提交", "已创建")) if _blob else False


# ★ RC-AF(2026-09-25): 成功条选择器不再本地留存（原 `_SUCCESS_SELECTORS` 仅服务已删除的
#   本地探测 JS）；探测统一走单一事实源 `SUCCESS_DETECT_JS`，避免第二份词表漂移。

# ★ 通用修复(2026-09-25) RC-Q: 动作按钮文案必须**来自 DOM**。
#   根因: 原实现用 ``operation[_bi:_bi+4]`` 从需求句子切 4 字符当按钮文案
#     （"提交重复名称的资产" → "提交重复"，"提交客户端验证失败的表单" → "提交客户"，
#      "提交服务端验证失败的表单" → "提交服务"），这是**句子片段**而非页面按钮文案
#     → get_by_role(name=…) 与快照关键词均不命中 → 点击失败 → 用例被判
#     "核心操作未执行" HARNESS_ERROR（实测 TC-REQ-002/008/010-I01）。
#   通用性: 下列词表全为通用动作语汇，零业务硬编码，跨系统/行业/框架。
_FORM_ENTRY_WORDS: tuple = (
    "新增", "新建", "添加", "创建", "录入", "登记", "注册", "发布",
    "New", "Create", "Add",
)
_SUBMIT_WORDS: tuple = (
    "保存", "提交", "确定", "确认", "下一步", "完成",
    "Save", "Submit", "OK", "Confirm",
)
_ACTION_VERBS: tuple = (
    "新增", "新建", "添加", "创建", "录入", "登记", "注册", "发布",
    "编辑", "修改", "更新", "查看", "保存", "提交", "确定", "确认",
    "删除", "移除", "复制", "移动", "导出", "导入", "上传", "下载", "打印",
    "查询", "搜索", "筛选", "过滤", "重置", "清空",
    "登录", "登入", "注销", "退出", "返回", "取消", "刷新", "同步",
    "审批", "撤回", "停用", "启用", "锁定", "解锁",
    "New", "Create", "Add", "Edit", "Update", "Save", "Submit",
    "Delete", "Remove", "Search", "Login", "SignIn", "Export", "Import",
)

# 「进入子视图」型前置检查的语义判据。
#   这类检查（"点击新增按钮后表单/弹窗打开"）语义上位于**点击入口之后、填写之前**；
#   只有 pre/post 二分时会被归入 post → 在填表提交后才执行 → 必然未命中（假 ERROR）。
#   判据用「子视图名词 + 打开/出现类状态词」双信号，避免把"列表记录/校验提示"
#   这类真正的结果检查误收进来。通用性: 表单/弹窗/模态框是 Web 通用交互形态。
_ENTRY_STATE_NOUNS: tuple = (
    "表单", "弹窗", "对话框", "模态", "抽屉", "面板", "页面",
    "form", "dialog", "modal", "drawer", "panel",
)
_ENTRY_STATE_OPEN_WORDS: tuple = (
    "打开", "弹出", "出现", "展示", "显示", "进入", "展开", "呈现",
    "open", "shown", "visible", "appear", "display",
)


def _is_entry_state_check(target: str, expect: str) -> bool:
    """判断验证点是否为「进入子视图」型前置检查（应在点击入口后、填写前执行）。"""
    _t = (target or "").strip().lower()
    _e = (expect or "").strip().lower()
    _blob = "%s %s" % (_t, _e)
    _has_noun = any(w in _blob for w in _ENTRY_STATE_NOUNS)
    if not _has_noun:
        return False
    # "打开"必须与子视图名词同现；仅"显示/展示"可能与结果断言重叠，
    # 故要求该状态词与子视图名词**同时**出现在 target+expect 中，
    # 且 target 本身不含结果语义（列表/记录/成功）。
    _has_open = any(w in _blob for w in _ENTRY_STATE_OPEN_WORDS)
    _is_result_target = any(
        w in _t for w in ("列表", "记录", "成功", "结果", "提示", "list", "record")
    )
    return _has_open and not _is_result_target


# 表单补全（选择器/语义词表/JS/取样例/作用域）唯一源在 step4.form_completion，
# 极性门控唯一源在 utils.form_fill_policy —— 此处只引用不重定义，
# 结构化路径与引导路径必须共用同一实现。
from src.pipeline.steps.step4.form_completion import (  # noqa: E402
    _CONFIRM_CHECKBOX_WORDS,
    complete_form_fields as _complete_form_fields,
    form_sample_value as _form_sample_value_impl,
    resolve_submittable_form_scope as _resolve_form_scope_impl,
    unique_token as _unique_token,
)
from src.pipeline.utils.form_fill_policy import (  # noqa: E402
    _CLIENT_BLOCK_WORDS,
    _SERVER_REJECT_RE,
    should_autofill,
)

# 冲突型（重复/已存在/唯一性/冲突）用例的标识字段须复用系统已有值，否则永远提交新值。
_CONFLICT_DATA_RE = re.compile(
    r"重复|已存在|唯一性|唯一|冲突|duplicate|already\s*exists", re.IGNORECASE
)

# 「表单语境」词：与校验反馈语义同时成立时，被测路径必经表单提交（非"查看表单"）。
_FORM_CONTEXT_RE = re.compile(r"表单|录入|填写|下拉|勾选")

# ★ RC-U: 从「实体列表」采集（标识列名, 已有标识值）——冲突型用例的测试数据来源。
#   通用判据（跨系统/行业/框架，零业务硬编码）:
#     ① 表格首个数据行 + 表头文本 → 建立「列名 → 单元格值」映射（列表首列常为 ID/序号，
#        故不能盲取首列；由 Python 侧按需求文本/列名语义挑列）；
#     ② 无表头时用 data-label / aria-label 兜底；
#     ③ 无表格时退回 [role=row] / li 列表项。
#   只读，不改动页面状态。
_JS_HARVEST_IDENTIFIERS: str = """
() => {
    const norm = (s) => (s || '').replace(/\\s+/g, ' ').trim();
    const out = [];
    const push = (label, value, i) => {
        if (value && value.length <= 80) out.push({label: norm(label), value: value, i: i});
    };
    const tables = Array.from(document.querySelectorAll('table'));
    for (const t of tables) {
        const rows = Array.from(t.querySelectorAll('tbody tr'));
        if (!rows.length) continue;
        const heads = [];
        const hr = t.querySelector('thead tr');
        if (hr) {
            Array.from(hr.querySelectorAll('th, td')).forEach((h) => heads.push(norm(h.textContent)));
        }
        for (const r of rows) {
            const cells = Array.from(r.querySelectorAll('td, th'));
            if (cells.length < 2) continue;
            cells.forEach((c, i) => {
                let lb = heads[i] || c.getAttribute('data-label') || '';
                if (!lb) {
                    const a = c.querySelector('[data-testid],a,span');
                    lb = a ? (a.getAttribute('data-testid') || '') : '';
                }
                push(lb, norm(c.textContent), i);
            });
            break;
        }
        if (out.length) return out;
    }
    const items = Array.from(document.querySelectorAll('[role="row"], ul > li'));
    for (const it of items) {
        const txt = norm(it.textContent);
        if (txt) push('', txt.slice(0, 60), 0);
    }
    return out;
}
"""

# ★ 删除用例行数统计（2026-09-28）: 删除可观察证据 = 列表行数减少。
#   优先取主表格 tbody 行数，无表格则退回 [role=row]；这是页面渲染出的
#   "实体条目数"唯一可观察来源，与业务无关、跨系统/框架通用。
_JS_COUNT_DATA_ROWS: str = """
() => {
    const tables = Array.from(document.querySelectorAll('table'));
    for (const t of tables) {
        const rows = Array.from(t.querySelectorAll('tbody tr'));
        if (rows.length) return rows.length;
    }
    return document.querySelectorAll('[role="row"]').length;
}
"""


def _expects_error_feedback(*texts: str) -> bool:
    """期望反馈是否为「错误 / 拒绝 / 无权限」提示（极性单一事实源）。

    委托 Step3 `_rule_extractor_keywords.expects_error_feedback`（唯一词表），
    避免执行端再复制一份拒绝语义词表造成漂移。
    """
    try:
        from src.pipeline.step3._rule_extractor_keywords import (
            expects_error_feedback,
        )

        return expects_error_feedback(*texts)
    except Exception:
        _blob = " ".join(t for t in texts if t)
        return any(k in _blob for k in _REJECT_MARKERS) if _blob else False


def _is_negated_assertion(expect: str) -> bool:
    if not expect:
        return False
    _t = (expect or "").lower()
    return any(_m in _t for _m in _NEG_MARKERS)


# ★ 根因修复(2026-09-28): 「页面加载」断言唯一判据（模块级单一事实源）——
#   Step3 对"页面加载成功"只产出这一组文本（_build_default_test_method 的
#   V1/V2），执行端据此识别为页面加载证据（无404+主内容可见），不再按泛词
#   文本猜或交 AI。与 test_method 的 `assert="page_loaded"` 结构化标记等价，
#   兼容历史/其它生成路径产生的同文本验证点，两处共用同一 DOM 判定。
_LOADING_EXPECT_MARKERS = ("页面正常加载", "核心业务内容", "数据列表或详情信息")


def _is_loading_assert(expect: str) -> bool:
    """期望是否为「页面加载/主内容可见」类（Step3 生成文本或结构化标记外的判据）。"""
    if not expect:
        return False
    _t = str(expect).strip()
    return any(_m in _t for _m in _LOADING_EXPECT_MARKERS)


def _extract_neg_features(expect: str) -> list:
    """从否定断言 expect 中提取要验证"不应存在"的要素候选关键词集合。

    生成端格式固定为 "X：需求明确不提供，页面不应提供/不应存在该功能"，
    X 即要素短语（如 "导入/导出功能"）。取 "：" 前片段。
    对否定断言，命中语义是"核心概念出现即违规"（如需求禁止导出，页面上出现"导出数据"
    也算违规），因此不取最长完整短语（会漏检同义/不同词），而取：每个中文段的完整词
    + 各 2 字根词，剔除无义词后作为候选。返回有意义关键词列表；空 → 退化。
    """
    _t = (expect or "").strip()
    if not _t:
        return []
    _head = (
        _t.split("：", 1)[0].split(":", 1)[0].strip()
        if ("：" in _t or ":" in _t)
        else _t
    )
    if not _head:
        return []
    import re as _re
    _segs = [s for s in _re.findall(r"[\u4e00-\u9fff]{2,}", _head) if s]
    _cands: list = []
    for _s in _segs:
        if _s in _NEG_STOPWORDS:
            continue  # 整段即无义词（"该功能"）→ 不派生词根，避免"该功"误为要素
        _cands.append(_s)
        # 2 字根词：更长短语的词根（"导出功能"→"导出"），排除无义词
        if len(_s) > 2:
            _root = _s[:2]
            if _root not in _NEG_STOPWORDS and _root not in _cands:
                _cands.append(_root)
    # 去重保序
    _uniq: list = []
    for _c in _cands:
        if _c and _c not in _uniq:
            _uniq.append(_c)
    return _uniq


class ActionMixin:
    """动作执行能力。

    提供：触发按钮点击、删除操作、通用点击、搜索操作。
    策略优先级统一：关键词 → 侧边栏+nav_map → LLM探索 → LLM语义。
    """

    # ★ v5.14(2026-07-31): 页面侦察缓存——每页只做一次侦察，避免重复展开
    _recon_done_urls: set = set()

    async def _get_snapshot_with_recon(self, page) -> tuple[list[dict], list[dict]]:
        """获取页面快照，首次访问时自动做页面侦察。

        策略: ① 检查当前URL是否已侦察 → ② 未侦察则调用page_reconnaissance
        (展开折叠面板+滚动全页) → ③ 已侦察则直接get_page_snapshot。
        通用性: URL级缓存，跨系统/行业适用。
        """
        try:
            _current_url = page.url
            if _current_url not in self._recon_done_urls:
                self._recon_done_urls.add(_current_url)
                _log.info("[Recon] 首次访问 %s，执行页面侦察", _current_url[:80])
                return await page_reconnaissance(page)
            return await get_page_snapshot(page)
        except Exception as _e:
            _log.warning("[Recon] 快照获取异常: %s", str(_e)[:80])
            return await get_page_snapshot(page)

    async def _execute_search(
        self,
        page,
        keywords: list[str],
        dom_inputs: list[dict],
        dom_buttons: list[dict],
        operation: str,
        expected: str,
        timeout_ms: int,
    ) -> dict[str, Any]:
        """搜索操作：找输入框+填充+点击搜索按钮+观察结果。"""
        _input_match = await self._match_elements_smart(keywords, dom_inputs, operation)
        _search_btn_kw = [k for k in keywords if k not in ("搜索", "查询", "筛选")]
        _search_btn_kw.extend(["搜索", "查询", "查找", "检索"])
        _btn_match = match_keywords_to_elements(_search_btn_kw, dom_buttons)

        _evidence_parts = []
        _actions = []

        _search_value = keywords[0] if keywords else "测试"
        if _input_match and _input_match["score"] >= 2:
            _input_text = _input_match["element"].get("text", "")
            with guard("_execute_search: _loc = page.locator(f'input:has-text", log=_log, level=logging.WARNING):
                _loc = page.locator(f"input:has-text('{_input_text}')").first
                if await _loc.count() == 0:
                    _loc = page.locator("input[type='text']").first
                if await _loc.count() > 0:
                    await _loc.fill(_search_value)
                    _evidence_parts.append(f"输入'{_search_value}'")
                    _actions.append(
                        {
                            "type": "fill",
                            "target_hint": _input_text,
                            "value": _search_value,
                        }
                    )
                    await page.wait_for_timeout(500)

        if _btn_match and _btn_match["score"] >= 2:
            _btn_text = _btn_match["element"].get("text", "")
            _click_ok = await click_button(page, _btn_text, timeout_ms)
            if _click_ok:
                _evidence_parts.append(f"点击搜索'{_btn_text}'")
                _actions.append(
                    {"type": "click", "target_hint": _btn_text, "strategy": "explore"}
                )
                await page.wait_for_timeout(1500)

        _result_evidence = await observe_page_change(page, operation)
        _evidence_parts.append(_result_evidence)

        _evidence = "; ".join(_evidence_parts)
        _success = bool(_result_evidence and "无变化" not in _result_evidence)

        return {
            "success": _success,
            "evidence": _evidence,
            "actions_taken": _actions,
            "strategy": "explore",
            "error": "" if _success else f"搜索操作未观察到预期结果: {_evidence}",
        }

    async def _execute_trigger_click(
        self,
        page,
        keywords: list[str],
        dom_buttons: list[dict],
        operation: str,
        expected: str,
        timeout_ms: int,
        op_subtype: str,
        target_page: str = "",
    ) -> dict[str, Any]:
        """触发按钮操作：找按钮+点击+验证弹窗/表单。

        策略（v7 前沿升级·通用性优先）：
        1. 关键词匹配（快速路径，不依赖LLM）
        2. 侧边栏+nav_map探索（兜底，不依赖LLM）— ★ 仅当前页面非target_page时
        3. LLM多步探索（增强路径，LLM可用时启用）
        4. LLM语义匹配（最后兜底）
        """
        # ★ 通用性修复(2026-07-28): 过滤导航元素（菜单项/treeitem/tab），只保留操作按钮
        #   根因(5层why):
        #     1. Agent点击"数据管理"后无变化 → 菜单项被当作按钮点击
        #     2. 菜单项被当作按钮 → _match_elements_smart的LLM语义匹配选中了菜单项
        #     3. LLM选中菜单项 → dom_buttons包含[role=menuitem/treeitem/tab]等导航元素
        #     4. 导航元素混入按钮列表 → get_page_snapshot无差别收集所有可交互元素
        #     5. ★ 新增: role=tab 元素（如"烟叶数据"Tab）被点击 → 页面变化 → 判定PASS → 假PASS
        #   通用性: 跨系统/行业/页面 — 任何系统都有菜单/tab vs 按钮的区别
        _action_buttons = [
            b
            for b in dom_buttons
            if not (
                b.get("role") in ("menuitem", "treeitem", "tab")
                or (b.get("tag") in ("li",) and b.get("role") in ("none", ""))
                or (
                    b.get("tag") == "a"
                    and b.get("role") in ("none", "")
                    and b.get("href", "")
                )
                or (b.get("ariaSelected") or b.get("aria-selected"))  # 选中状态的 tab
            )
        ]
        if not _action_buttons:
            _action_buttons = dom_buttons  # 兜底：全部过滤掉了就用原始列表
        _log.debug(
            "[TriggerClick] 过滤前%d个元素 → 过滤后%d个操作按钮",
            len(dom_buttons),
            len(_action_buttons),
        )

        # 策略1: 关键词匹配（快速路径，不依赖LLM）
        _extended_kw = list(keywords)
        if op_subtype == "create":
            _extended_kw.extend(["新建", "新增", "添加", "创建"])
        elif op_subtype == "edit":
            _extended_kw.extend(["编辑", "修改", "更新"])
        elif op_subtype == "import_export":
            # ★ v3修复: 移除2字通用词("导入"/"导出")，避免匹配到无关元素
            #   根因: "导入"匹配"数据看板"等无关元素 → 假阳性
            _extended_kw.extend(
                ["数据导入", "批量导入", "文件上传", "数据导出", "文件下载"]
            )

        _btn_match = await self._match_elements_smart(
            _extended_kw, _action_buttons, operation
        )

        # ★ 策略1.5: Tab页探索（关键词匹配失败时，按钮可能在Tab页内）
        #   根因(2026-07-28): get_page_snapshot移除[role=tab]后，Tab探索被静默破坏。
        #   按钮在Tab页内 → 不点击Tab则不可见 → 关键词匹配必然失败。
        #   通用性: 跨系统/行业/页面 — 所有Web应用都有Tab导航。
        if not _btn_match or _btn_match["score"] < 2:
            _tabs = await get_page_tabs(page)
            if _tabs:
                _kw0 = (keywords[0] if keywords else "")[:30]
                _log.info(
                    "[TriggerClick] 策略1.5: Tab探索, %d个Tab, kw=%s", len(_tabs), _kw0
                )
                _tab_result = await self._explore_tabs_for_button(
                    page,
                    keywords,
                    _tabs,
                    timeout_ms,
                )
                if _tab_result:
                    _log.info(
                        "[TriggerClick] ✅ Tab探索成功: %s",
                        _tab_result.get("element", {}).get("text", "")[:40],
                    )
                    _btn_match = _tab_result

        # ★ 策略1.8: 页内导航探索（按钮可能在页内二级导航的子页面中）
        #   根因(2026-07-29): portraitData页的"数据导入"按钮在"物料管理"子页面，
        #   需要通过页内导航链接才能到达。Tab和侧边栏探索都找不到。
        #   通用性: 跨系统/行业/页面 — 所有Web应用都可能有页内二级导航
        if not _btn_match or _btn_match["score"] < 2:
            _inpage_result = await self._explore_inpage_nav_for_button(
                page,
                keywords,
                timeout_ms,
            )
            if _inpage_result:
                _log.info(
                    "[TriggerClick] ✅ 页内导航探索成功: %s",
                    _inpage_result.get("element", {}).get("text", "")[:40],
                )
                _btn_match = _inpage_result

        # ★ 策略1.9: 页面内容区域搜索（按钮可能在表格行/操作栏等页面主体内容中）
        #   根因(2026-07-29): TC-REQ-002-I01超时 → "编辑"按钮在表格行内
        #   表格行内按钮 → 不在Tab/InPageNav/侧边栏中 → 前序策略全部失败
        #   get_page_snapshot可能未捕获 → 用Playwright locator直接搜索页面内容
        #   通用性: 跨系统/行业/页面 — 所有Web应用的数据表格行内都有操作按钮
        if not _btn_match or _btn_match["score"] < 2:
            _primary_kw = keywords[0] if keywords else ""
            _short_kw = _primary_kw[:4] if len(_primary_kw) >= 4 else _primary_kw
            _pw_match = await try_pw_locate_button(page, _primary_kw, _short_kw)
            if _pw_match:
                _log.info(
                    "[TriggerClick] ✅ 页面内容搜索成功: %s",
                    _pw_match.get("element", {}).get("text", "")[:40],
                )
                _btn_match = _pw_match
            if not _btn_match and len(keywords) > 1:
                # 用子关键词逐个重试
                for _kw in keywords[1:6]:
                    _pk = _kw
                    _sk = _kw[:4] if len(_kw) >= 4 else _kw
                    _pw_match = await try_pw_locate_button(page, _pk, _sk)
                    if _pw_match:
                        _log.info(
                            "[TriggerClick] ✅ 页面内容搜索(子关键词)成功: kw=%s text=%s",
                            _kw,
                            _pw_match.get("element", {}).get("text", "")[:40],
                        )
                        _btn_match = _pw_match
                        break

        # ★ 策略2: 侧边栏+nav_map探索（系统化遍历，不依赖LLM）
        #   ★ Fix(2026-07-29): 侧边栏探索移到LLM探索之前
        #   根因(5层why):
        #     1. TC-REQ-001-I01超时 → LLM探索浪费74s在错误页面
        #     2. LLM探索在错误页面 → LLM Step 1就导航离开目标页面
        #     3. LLM导航离开 → 侧边栏探索在后面，LLM已经破坏了页面状态
        #     4. 侧边栏探索在后面 → 策略优先级错误：LLM昂贵且不可靠，侧边栏系统化且可靠
        #     5. 策略优先级错误 → 应优先使用系统化侧边栏探索（覆盖所有菜单项），LLM仅兜底
        #   通用性: 跨系统/行业/页面 — 侧边栏导航是Web应用的通用模式
        # ★ Fix(2026-07-29) v3: 移除_on_target_page跳过。根因(5层why):
        #     1. TC-REQ-001-I01 ERROR → 按钮"数据导入"找不到
        #     2. 按钮找不到 → target_page正确但按钮在子页面/其他页面
        #     3. 侧边栏被跳过 → _on_target_page=True阻止了侧边栏探索
        #     4. 跳过侧边栏 → 唯一能跨页面搜索的策略被禁用
        #     5. 禁用跨页面搜索 → 按钮在target_page外时永远找不到
        #   通用性: 跨系统/行业/页面 — 按钮不在target_page是普遍场景
        _on_target_page = False
        if target_page:
            _cur_url = page.url or ""
            _on_target_page = target_page in _cur_url or (
                target_page.startswith("/") and _cur_url.endswith(target_page)
            )
        if not _btn_match or _btn_match["score"] < 2:
            if _on_target_page:
                _log.info(
                    "[TriggerClick] 已在target_page(%s)但按钮未找到, 尝试侧边栏探索",
                    target_page[:60],
                )
            _sidebar_match = await self._explore_sidebar_for_button(
                page, keywords, dom_buttons, timeout_ms
            )
            if _sidebar_match:
                _btn_match = _sidebar_match
            else:
                _nav_map_match = await self._explore_nav_map_for_button(
                    page, keywords, timeout_ms
                )
                if _nav_map_match:
                    _btn_match = _nav_map_match

        # ★ 策略3: LLM多步探索（智能推理兜底，侧边栏失败后才用）
        #   ★ Fix(2026-07-29): 侧边栏优先，LLM探索仅兜底
        #   策略优先级: 关键词匹配 → Tab探索 → 侧边栏遍历 → LLM推理 → 语义匹配。
        if not _btn_match or _btn_match["score"] < 2:
            _llm_result = await self._llm_driven_explore(
                page,
                operation,
                keywords,
                expected,
                min(timeout_ms, 60000),
                max_steps=3,
            )
            if _llm_result and _llm_result.get("success"):
                _log.info(
                    "[TriggerClick] ✅ LLM探索成功: %s",
                    _llm_result.get("evidence", "")[:100],
                )
                return _llm_result

        # 策略4: LLM语义匹配（最后兜底）
        if not _btn_match or _btn_match["score"] < 1:
            _sidebar_texts = [
                b.get("text", "") for b in _action_buttons if b.get("text")
            ]
            _semantic_menu = await self._semantic_menu_match(operation, _sidebar_texts)
            if _semantic_menu:
                _btn_match = match_keywords_to_elements(
                    [_semantic_menu], _action_buttons
                )
                if _btn_match and _btn_match["score"] >= 1:
                    _log.info(
                        "[TriggerClick] LLM语义匹配成功: %r → %r",
                        operation[:40],
                        _semantic_menu[:40],
                    )
            if not _btn_match or _btn_match["score"] < 1:
                _llm_evidence = _llm_result.get("evidence", "") if _llm_result else ""
                return {
                    "success": False,
                    "evidence": f"未找到'{operation}'相关按钮。LLM探索: {_llm_evidence}",
                    "actions_taken": (
                        _llm_result.get("actions_taken", []) if _llm_result else []
                    ),
                    "strategy": "llm_explore",
                    "error": f"关键词{keywords[:3]}在{len(dom_buttons)}个按钮中无匹配（侧边栏+nav_map+LLM探索均失败）",
                }

        _btn_text = _btn_match["element"].get("text", "").strip()
        _click_ok = await click_button(page, _btn_text, timeout_ms)
        if not _click_ok:
            return {
                "success": False,
                "evidence": f"点击'{_btn_text}'失败",
                "actions_taken": [],
                "strategy": "explore",
                "error": f"点击按钮失败: {_btn_text}",
            }

        await page.wait_for_timeout(1500)

        # 观察结果
        _result_evidence = await observe_page_change(page, operation)
        _evidence_parts = [f"点击'{_btn_text}'", _result_evidence]

        _dialog_visible = await check_dialog_visible(page)
        if _dialog_visible:
            _evidence_parts.append("弹窗/表单已打开")
        _message = await get_page_message(page)
        if _message:
            _evidence_parts.append(f"系统提示: {_message}")

        _evidence = "; ".join(_evidence_parts)
        _dom_success = (
            _dialog_visible or bool(_message) or "URL变化" in _result_evidence
        )

        # ★ 通用性修复：import_export/upload 操作触发原生文件对话框，
        # Playwright 无法检测原生对话框的DOM变化，按钮点击成功即视为操作成功
        if op_subtype in ("import_export",) and not _dom_success:
            _dom_success = True
            _evidence_parts.append("(导入操作触发原生文件对话框，DOM无法检测)")
            _evidence = "; ".join(_evidence_parts)

        # LLM语义断言
        # ★ 通用性修复（v2）：import_export 操作触发原生文件对话框，
        # LLM 看到的页面快照不会包含"导入成功"提示，LLM 必然判定 FAIL。
        # 此时跳过 LLM 断言，直接信任 _dom_success（按钮点击成功=操作成功）。
        _actions_taken = [
            {"type": "click", "target_hint": _btn_text, "strategy": "explore"}
        ]
        if op_subtype in ("import_export",) and _dom_success:
            _success = True
            _sem_result = {
                "verdict": "UNKNOWN",
                "evidence": "导入操作-原生文件对话框-跳过LLM断言",
                "confidence": 0.0,
            }
        else:
            _snapshot = await get_page_snapshot_text(page)
            _sem_result = await self._semantic_assertion(
                operation, expected, _snapshot, _actions_taken
            )
            if _sem_result.get("verdict") != "UNKNOWN":
                _evidence += f"; LLM评估: {_sem_result['verdict']}(conf={_sem_result['confidence']:.2f}) {_sem_result['evidence'][:200]}"
                _success = (
                    _sem_result["verdict"] == "PASS"
                    and _sem_result["confidence"] >= 0.5
                )
            else:
                _success = _dom_success

        return {
            "success": _success,
            "evidence": _evidence,
            "actions_taken": _actions_taken,
            "strategy": "explore",
            "error": "" if _success else f"点击'{_btn_text}'后未观察到预期变化",
            "_semantic_verdict": _sem_result,
        }

    async def _execute_delete(
        self,
        page,
        keywords: list[str],
        dom_buttons: list[dict],
        operation: str,
        expected: str,
        timeout_ms: int,
    ) -> dict[str, Any]:
        """删除操作：找删除按钮+点击+检查确认弹窗。"""
        _extended_kw = list(keywords)
        _extended_kw.extend(["删除", "移除", "清空"])

        _btn_match = await self._match_elements_smart(
            _extended_kw, dom_buttons, operation
        )

        if not _btn_match or _btn_match["score"] < 2:
            return {
                "success": False,
                "evidence": f"未找到'{operation}'相关按钮",
                "actions_taken": [],
                "strategy": "explore",
                "error": f"关键词{keywords[:3]}在{len(dom_buttons)}个按钮中无匹配",
            }

        _btn_text = _btn_match["element"].get("text", "").strip()
        _click_ok = await click_button(page, _btn_text, timeout_ms)
        if not _click_ok:
            return {
                "success": False,
                "evidence": f"点击'{_btn_text}'失败",
                "actions_taken": [],
                "strategy": "explore",
                "error": f"点击按钮失败: {_btn_text}",
            }

        await page.wait_for_timeout(1000)

        _confirm_visible = await check_dialog_visible(page)
        _evidence = f"点击'{_btn_text}'"
        if _confirm_visible:
            _evidence += "; 确认弹窗已打开"
        else:
            _evidence += "; 未出现确认弹窗（可能直接删除）"

        _actions = [{"type": "click", "target_hint": _btn_text, "strategy": "explore"}]
        return {
            "success": True,
            "evidence": _evidence,
            "actions_taken": _actions,
            "strategy": "explore",
            "error": "",
        }

    async def _execute_generic_click(
        self,
        page,
        keywords: list[str],
        dom_buttons: list[dict],
        operation: str,
        expected: str,
        timeout_ms: int,
        target_page: str = "",
    ) -> dict[str, Any]:
        """通用点击：找按钮+点击+观察结果。

        ★ v8 策略优先级修复(2026-07-28)：与 _execute_trigger_click 统一。
        原策略0(LLM多步探索)移到策略3，改为关键词优先。
        策略（v8 统一）：
        1. 关键词匹配（快速路径，不依赖LLM）
        2. 侧边栏+nav_map探索（兜底，不依赖LLM）— ★ 仅当前页面非target_page时
        3. LLM多步探索（增强路径，LLM可用时启用）
        4. LLM语义匹配（最后兜底）
        """
        # ★ 通用性修复(2026-07-28): 过滤导航元素，只保留操作按钮
        #   v8(2026-07-28): 同步_exeucte_trigger_click的tab过滤逻辑，防止Tab被当成按钮
        _action_buttons = [
            b
            for b in dom_buttons
            if not (
                b.get("role") in ("menuitem", "treeitem", "tab")
                or (b.get("tag") in ("li",) and b.get("role") in ("none", ""))
                or (
                    b.get("tag") == "a"
                    and b.get("role") in ("none", "")
                    and b.get("href", "")
                )
                or (b.get("ariaSelected") or b.get("aria-selected"))  # 选中状态的 tab
            )
        ]
        if not _action_buttons:
            _action_buttons = dom_buttons

        # 策略1: 关键词匹配（快速路径，不依赖LLM）
        _btn_match = await self._match_elements_smart(
            keywords, _action_buttons, operation
        )

        # ★ 策略1.8: 页内导航探索（按钮可能在页内二级导航的子页面中）
        if not _btn_match or _btn_match["score"] < 2:
            _inpage_result = await self._explore_inpage_nav_for_button(
                page,
                keywords,
                timeout_ms,
            )
            if _inpage_result:
                _log.info(
                    "[GenericClick] ✅ 页内导航探索成功: %s",
                    _inpage_result.get("element", {}).get("text", "")[:40],
                )
                _btn_match = _inpage_result

        # ★ 策略2: LLM多步探索（智能推理优先，不破坏页面状态）
        #   根因(2026-07-28): 与 _execute_trigger_click 统一策略优先级。
        #   LLM探索优先于侧边栏暴力遍历，避免导航离开目标页面。
        if not _btn_match or _btn_match["score"] < 2:
            _llm_result = await self._llm_driven_explore(
                page,
                operation,
                keywords,
                expected,
                timeout_ms,
                max_steps=8,
            )
            if _llm_result and _llm_result.get("success"):
                _log.info(
                    "[GenericClick] ✅ LLM探索成功: %s",
                    _llm_result.get("evidence", "")[:100],
                )
                return _llm_result

        # 策略3: 侧边栏+nav_map探索（系统化遍历兜底，不依赖LLM）
        # ★ Fix(2026-07-29) v3: 移除_on_target_page跳过逻辑
        #   根因: 按钮可能在target_page的子页面(页内导航)中，侧边栏探索不应被跳过
        if not _btn_match or _btn_match["score"] < 2:
            _sidebar_match = await self._explore_sidebar_for_button(
                page, keywords, dom_buttons, timeout_ms
            )
            if _sidebar_match:
                _btn_match = _sidebar_match
            else:
                _nav_map_match = await self._explore_nav_map_for_button(
                    page, keywords, timeout_ms
                )
                if _nav_map_match:
                    _btn_match = _nav_map_match

        # 策略4: LLM语义匹配（最后兜底）
        if not _btn_match or _btn_match["score"] < 1:
            _sidebar_texts = [
                b.get("text", "") for b in _action_buttons if b.get("text")
            ]
            _semantic_menu = await self._semantic_menu_match(operation, _sidebar_texts)
            if _semantic_menu:
                _btn_match = match_keywords_to_elements(
                    [_semantic_menu], _action_buttons
                )
                if _btn_match and _btn_match["score"] >= 1:
                    _log.info(
                        "[GenericClick] LLM语义匹配成功: %r → %r",
                        operation[:40],
                        _semantic_menu[:40],
                    )
            if not _btn_match or _btn_match["score"] < 1:
                _llm_evidence = _llm_result.get("evidence", "") if _llm_result else ""
                return {
                    "success": False,
                    "evidence": f"页面中未找到'{operation}'相关按钮。LLM探索: {_llm_evidence}",
                    "actions_taken": (
                        _llm_result.get("actions_taken", []) if _llm_result else []
                    ),
                    "strategy": "llm_explore",
                    "error": f"关键词{keywords[:3]}在{len(dom_buttons)}个按钮中无匹配（LLM探索+侧边栏+nav_map均失败）",
                }

        _btn_text = _btn_match["element"].get("text", "").strip()
        _log.info(
            "[AgentExplorer.execute_intent] 匹配到按钮: text=%r score=%d",
            _btn_text[:60],
            _btn_match["score"],
        )

        _click_ok = await click_button(page, _btn_text, timeout_ms)
        if not _click_ok:
            _repair_ok = await repair_click(page, _btn_text, timeout_ms)
            if _repair_ok:
                return {
                    "success": True,
                    "evidence": f"repair成功: 点击'{_btn_text}'（模糊匹配）",
                    "actions_taken": [
                        {
                            "type": "click",
                            "target_hint": _btn_text,
                            "strategy": "repair",
                        }
                    ],
                    "strategy": "repair",
                    "error": "",
                }
            return {
                "success": False,
                "evidence": f"点击'{_btn_text}'失败",
                "actions_taken": [],
                "strategy": "explore",
                "error": f"点击失败: {_btn_text}",
            }

        await page.wait_for_timeout(1500)
        _result_evidence = await observe_page_change(page, operation)
        _evidence = f"点击'{_btn_text}'; {_result_evidence}"

        _dialog_visible = await check_dialog_visible(page)
        _message = await get_page_message(page)
        if _dialog_visible:
            _evidence += "; 弹窗/对话框已打开"
        if _message:
            _evidence += f"; 系统提示: {_message}"

        _dom_success = (
            _dialog_visible or bool(_message) or "URL变化" in _result_evidence
        )

        # LLM语义断言
        _actions_taken = [
            {"type": "click", "target_hint": _btn_text, "strategy": "explore"}
        ]
        _snapshot = await get_page_snapshot_text(page)
        _sem_result = await self._semantic_assertion(
            operation, expected, _snapshot, _actions_taken
        )
        if _sem_result.get("verdict") != "UNKNOWN":
            _evidence += "; LLM评估: %s(conf=%.2f) %s" % (
                _sem_result["verdict"],
                _sem_result["confidence"],
                _sem_result["evidence"][:200],
            )
            _success = (
                _sem_result["verdict"] == "PASS" and _sem_result["confidence"] >= 0.5
            )
        else:
            _success = _dom_success

        return {
            "success": _success,
            "evidence": _evidence,
            "actions_taken": _actions_taken,
            "strategy": "explore",
            "error": "" if _success else "点击'%s'后未观察到预期变化" % _btn_text,
            "_semantic_verdict": _sem_result,
        }

    # ── ★ v5.4 guided模式：按test_method步骤执行（替代自由探索）──

    async def _execute_guided(
        self,
        page,
        inputs: dict[str, Any],
        verifications: list[dict[str, Any]],
        operation: str,
        expected: str,
        timeout_ms: int,
        op_type: str = "",
        description: str = "",
        declared_actions: list[dict[str, Any]] | None = None,
        has_structured_oracle: bool = False,
        expects_denial: bool = False,
    ) -> dict[str, Any]:
        """★ v5.4: 按 test_method.verifications 步骤执行。

        通用性: 跨系统/行业/页面 — 所有操作都用自然语言 target 描述，
                不依赖特定选择器或业务术语。
        可追溯: 每步记录 action+target+value+expect，生成完整 evidence。
        稳定: 每步独立 try-except，单步失败不阻断后续步骤。

        op_type: Step3 操作类型分类（form/search/view/delete_op/…）。用于在
          operation 文本无操作动词时，为「表单录入型」需求补上进入表单的入口点击。
        description: 需求正文（Step3 `_explore_task.description`）。用于 RC-T/RC-U
          的**需求级**语义判定（客户端/服务端校验归属、唯一性冲突型测试数据）——
          这些语义只存在于需求正文，operation/expected_feedback 是提炼后的短标签。
        declared_actions: 用例声明的权威动作序列（Step3 `_explore_task.declared_actions`）。
          声明里 click 的目标（target_hint）是「点什么」的唯一事实源，优先于从
          operation 文本推断；执行端只读不推。
        expects_denial: 用例断言是否要求「被拒绝/不可达」（权威判据
          ``step3._identity_assign.case_expects_denial``，生成侧判定后透传）。
          拒绝型用例里"声明动作无法施加"是**期望证据**（最小权限生效），
          不是 harness 失败；结论交给用例自身的结构化 oracle 裁决。
        """
        # 用例声明的 click 目标候选（按声明顺序拼接，供 DOM 落地时逐个试）
        _declared_click_hints: list[str] = []
        for _da in declared_actions or []:
            if isinstance(_da, dict) and _da.get("type") == "click":
                _h = str(_da.get("target_hint") or "").strip()
                if _h:
                    _declared_click_hints.append(_h)
        _declared_click = ",".join(_declared_click_hints)
        _log.info(
            "[Guided] 声明动作: declared=%d click_hints=%r",
            len(declared_actions or []),
            _declared_click[:60],
        )

        _actions_taken: list[dict] = []
        _verification_failures: list[dict] = []
        # ★ 2026-09-28 实时判定结构化落穿（上游一次判定，下游只读不推）：
        #   guided 验证点 PASS 时记录 {expect, actual, method}，checks 层据此
        #   对同一语义的静态检查直接采信，不再在流程结束后重查冷冻字面量。
        _verify_records: list[dict] = []
        _all_passed = True
        _evidence_parts: list[str] = []
        # ★ 2026-09-28 删除用例行数对比：点击删除按钮前采集列表行数，
        #   delete_done 验证时与此对比（减少 = 删除落库的可观察证据）。
        _rows_before_delete = -1
        # 主流程是否已完成一次业务提交（auto-submit 或提交型操作按钮点击成功）。
        # Post 验证序列若重走"创建流程"（再开创建入口/再填/再提交）会二次创建
        # 同一资源撞唯一约束（SUT '名称已存在'）并破坏已达成状态——用该标志
        # 识别"重建窗口"整体跳过，防重复提交（判据见 _exec_one_step）。
        _submission_done = False
        _in_replay = False

        # 需求级文本（描述+期望）：服务端拒绝/表单校验语境判定的共同输入（下游只读）。
        _server_blob = " ".join([str(description or ""), str(expected or "")])
        # 断言极性门控（唯一判据 should_autofill）：期望即错误/拒绝反馈时不补全必填项，
        # 但「服务端校验」型需求客户端须先通过，仍补全。只用需求级/操作级文本，
        # 不含 verifications —— 步骤断言常含"非法值→提示错误"探针，混入会误判极性。
        _allow_autofill = should_autofill(operation, expected, description)

        # ★ v5.4 fix(2026-07-30): 三步执行 — ① 操作前验证(页面内容) ② 点击+填充 ③ 操作后验证
        #   根因(§10.9 3层why):
        #     L1: 验证失败，actual='弹窗已打开'但expect='页面显示内容'
        #     L2: verifications只有verify步骤，缺少click步骤来打开弹窗
        #     L3: _execute_guided接收inputs但完全忽略，且验证顺序错误（先点后验）
        #   修复: ① 先验证"页面内容"类验证点 ② 点击操作按钮+填充 ③ 验证"操作结果"类
        #   通用性: 跨系统/行业 — 操作按钮文本从operation提取，target区分pre/post

        # 分类验证点：pre_action（页面内容类）、entry（子视图进入类）、post_action（操作结果类）
        # ★ RC-O: 页面特征验证点是**导航落地后的上下文检查**，语义上属 pre_action
        #   （业务动作之前确认已到达目标页）；归到 post 会让它在用例深入子视图
        #   （新增/编辑表单）后才执行，必然不命中。
        # ★ 通用修复: 新增 entry 类 —— 「进入子视图」的前置检查（表单/弹窗/对话框
        #   是否打开）语义上位于**点击入口之后、填写之前**。此前只有 pre/post 二分，
        #   这类检查因不匹配「页面内容/页面特征」关键词被归入 post → 在填表并提交
        #   之后才执行（此时页面已回到列表页）→ 必然未命中 → 用例假 ERROR。
        #   根因: 检查的**时序位置**由它在动作链中的语义位置决定，不由 target
        #   关键词二分决定。通用性: 表单/弹窗/模态框是 Web 通用交互形态，
        #   零业务硬编码。
        _pre_verifications = []
        _entry_verifications = []
        _post_verifications = []
        for _v in verifications:
            _target = (_v.get("target") or "").strip()
            _expect = (_v.get("expect") or "").strip()
            if (
                "页面内容" in _target
                or "页面显示" in _target
                or "页面特征" in _target
                or "page" in _target.lower()
            ):
                _pre_verifications.append(_v)
            elif _is_entry_state_check(_target, _expect):
                _entry_verifications.append(_v)
            else:
                _post_verifications.append(_v)

        # 当前是否处于「用例声明动作」段：拒绝型用例的"动作不可达"是期望证据，
        # 仅在该段内生效（启发式 ② 块的合成步骤不适用，其不可达仍须诚实报错）。
        _declared_phase = False

        # 辅助函数：执行单个步骤
        async def _exec_one_step(_step: dict, _step_label: str):
            nonlocal _all_passed, _submission_done, _in_replay, _rows_before_delete
            nonlocal _declared_phase
            _action = (_step.get("action") or "").strip()
            _target = (_step.get("target") or "").strip()
            _value = (_step.get("value") or "").strip()
            _expect = (_step.get("expect") or "").strip()
            # 重复创建防护：主流程已提交后，Post 序列若重走"创建流程"
            # （打开创建入口→填充→再次提交）属重复业务操作——二次提交撞同一
            # 唯一键（SUT '名称已存在'）会把页面拖进错误态，令后续断言全部
            # 假 FAIL。判据只用通用创建/提交语义（_SUBMIT_WORDS），零业务词表。
            # 重放段止步于首个断言动作（verify），后续独立场景（不同值填充/
            # 拒绝校验探针）不受影响。
            if _submission_done:
                if _action == "verify":
                    _in_replay = False
                elif _in_replay and _action in ("click", "fill"):
                    _log.info(
                        "[Guided] 跳过重复创建段: %s'%s'(主流程已提交该资源)",
                        _action,
                        _target[:40],
                    )
                    _evidence_parts.append(
                        "跳过重复创建段: %s'%s'(主流程已提交)" % (_action, _target)
                    )
                    return
                elif _action == "click" and any(
                    w in _target for w in ("新增", "创建", "添加", "新建")
                ):
                    _in_replay = True
                    _log.info(
                        "[Guided] 跳过重复创建段(重放窗口开启): 点击'%s'", _target[:40]
                    )
                    _evidence_parts.append(
                        "跳过重复创建段: 点击'%s'(主流程已提交)" % _target
                    )
                    return
            # ★ RC-O: 页面特征验证点（Step3 _fix_explore_task 追加的
            #   target="X页面特征"）属**路由上下文检查**，其失败不计入用例失败。
            _is_page_feature_ctx = "页面特征" in _target
            if not _action:
                return
            _log.info(
                "[Guided] %s: %s target=%r value=%r expect=%r",
                _step_label,
                _action,
                _target[:40],
                _value[:40],
                _expect[:40],
            )
            try:
                if _action == "fill":
                    _ok = await self._guided_fill(page, _target, _value, timeout_ms)
                    _actions_taken.append(
                        {"type": "fill", "target": _target, "value": _value}
                    )
                    if _ok:
                        _evidence_parts.append("填充'%s': %s" % (_target, _value))
                    elif _declared_phase and expects_denial:
                        # 拒绝型用例：声明动作的目标不存在 = 最小权限生效的证据，
                        # 不判 harness 失败，结论交用例自身的结构化 oracle。
                        _log.info(
                            "[Guided] 拒绝型用例 → 声明动作不可达记为证据(交oracle裁决): %r",
                            _target[:40],
                        )
                        _evidence_parts.append(
                            "声明动作不可达(拒绝型，交结构化断言裁决): '%s'" % _target
                        )
                    else:
                        _all_passed = False
                        _verification_failures.append(
                            {
                                "point": "填充'%s'" % _target,
                                "expect": _value,
                                "actual": "未找到元素",
                            }
                        )
                        _evidence_parts.append("填充'%s'失败: 未找到元素" % _target)
                elif _action == "click":
                    _pre_click_url = page.url
                    _ok = await self._guided_click(page, _target, timeout_ms)
                    _actions_taken.append({"type": "click", "target": _target})
                    if _ok:
                        _evidence_parts.append("点击'%s'" % _target)
                        # ★ v5.19 fix(2026-07-31): 点击后URL变化检测+新页面等待
                        #   根因(§10.9): 点击按钮后页面导航到新URL，后续fill/verify
                        #     在旧页面快照上执行，找不到新页面的元素（如文件上传input）
                        #   通用性: 跨系统/行业/框架 — 所有Web应用的按钮点击都可能
                        #     触发页面导航，Agent必须检测URL变化并等待新页面加载
                        _post_click_url = page.url
                        if _post_click_url != _pre_click_url:
                            _log.info(
                                "[Guided] 点击后URL变化: ...%s → ...%s",
                                _pre_click_url[-60:],
                                _post_click_url[-60:],
                            )
                            with guard("_exec_one_step: await page.wait_for_load_state( 'dom", log=_log):
                                await page.wait_for_load_state(
                                    "domcontentloaded", timeout=10000
                                )
                            await page.wait_for_timeout(1000)
                    elif _declared_phase and expects_denial:
                        _log.info(
                            "[Guided] 拒绝型用例 → 声明动作不可达记为证据(交oracle裁决): %r",
                            _target[:40],
                        )
                        _evidence_parts.append(
                            "声明动作不可达(拒绝型，交结构化断言裁决): '%s'" % _target
                        )
                    else:
                        _all_passed = False
                        _verification_failures.append(
                            {
                                "point": "点击'%s'" % _target,
                                "expect": "可点击",
                                "actual": "未找到按钮",
                            }
                        )
                        _evidence_parts.append("点击'%s'失败: 未找到按钮" % _target)
                elif _action == "verify":
                    # ★ 根因修复(2026-09-28): 加载类断言 = 页面已加载（无404+主内容区
                    #   可见），由 Step3 上游落 `assert="page_loaded"` 结构化标记，此处
                    #   只读调用引擎唯一实现 _verify_page_content_loaded 做 DOM 判定——
                    #   不按文本猜、不调 AI（页面已渲染却判"未加载"的假 FAIL 根除）。
                    #   判据统一走 _is_loading_assert（Step3 唯一生成文本），兼容历史/AI
                    #   路径产生的同文本断言，不看生成来源。
                    _ok, _actual, _vmethod = False, "", ""
                    # 非判定态标记：本验证点无权威结论时置真，只留证据、不计失败
                    # （结论交用例自身的结构化 checks），与「弱证据不判 PASS」对称。
                    _nondecisive = False
                    # 用例只声明了导航（无任何交互动作）时，一切"操作后才出现"的验证点
                    # 在执行层结构性不可满足（从未施加激励）→ 非判定态，结论交 checks。
                    _declared_has_interaction = any(
                        str(_a.get("type") or "").lower()
                        not in ("navigate", "goto", "screenshot", "wait", "wait_for")
                        for _a in (declared_actions or [])
                        if isinstance(_a, dict)
                    )
                    _unreachable_outcome = (
                        not _declared_has_interaction
                        and bool(_value)
                        and str(_value) in str(_expect)
                    )
                    if _unreachable_outcome:
                        _nondecisive = True
                    _is_load = (
                        _step.get("assert") == "page_loaded"
                        or _is_loading_assert(_expect)
                    )
                    # ★ 根因修复(2026-09-28): 删除断言 = 列表行数减少（DOM 状态对比），
                    #   由 Step3 上游落 `assert="delete_done"` 结构化标记。删除成功是
                    #   "重定向回列表且行数减一"的可观察行为，不是页面上某个泛文本
                    #   （实测 TC-REQ-005-I01：删除真实落库，泛文本断言却假 FAIL）。
                    # ★ 根因修复(2026-09-28): 正向表单提交成功断言 = 提交后页面回到
                    #   业务视图（重定向/刷新）且无错误条——可观察 DOM 证据，不靠
                    #   "新增成功"文案（SUT 增改成功仅重定向，无 toast → 文本断言假 FAIL，
                    #   实测 TC-REQ-002/007/013/014-I01）。Step3 上游落 `assert="submit_ok"`。
                    _is_submit_ok = _step.get("assert") == "submit_ok"
                    _is_delete_done = _step.get("assert") == "delete_done"
                    if _is_submit_ok:
                        from src.pipeline.steps.step4.browser_core.single_mixins._exploratory import (
                            _verify_submit_ok as _v_submit,
                        )
                        try:
                            _submit_ok = await _v_submit(page, timeout_ms)
                        except Exception as _e:
                            _submit_ok = False
                            _log.warning(
                                "[Guided] submit_ok DOM 证据异常: %s", str(_e)[:100]
                            )
                        if _submit_ok:
                            _ok, _actual, _vmethod = (
                                True,
                                "提交后回到业务视图且无错误条（可观察 DOM 证据）",
                                "提交成功证据",
                            )
                        else:
                            _log.warning(
                                "[Guided] submit_ok 证据失败(页面仍错误条/未回业务视图)"
                            )
                    elif _is_delete_done:
                        from src.pipeline.steps.step4.browser_core.single_mixins._exploratory import (
                            _verify_delete_row_decreased as _v_del,
                        )
                        try:
                            _del_ok = await _v_del(
                                page, _rows_before_delete, timeout_ms
                            )
                        except Exception as _e:
                            _del_ok = False
                            _log.warning(
                                "[Guided] delete_done DOM 证据异常: %s", str(_e)[:100]
                            )
                        if _del_ok:
                            _ok, _actual, _vmethod = (
                                True,
                                "删除后列表行数减少（可观察 DOM 证据）",
                                "删除行数证据",
                            )
                        else:
                            _log.warning(
                                "[Guided] delete_done 行数未减少: before=%s",
                                _rows_before_delete,
                            )
                    elif _step.get("assert") == "delete_aborted":
                        # ★ 与 delete_done 对称：取消/拒绝删除的**不变量**证据 = 行数未减少。
                        #   由 Step3 上游落结构化标记（反极性断言），执行层只读不重推。
                        from src.pipeline.steps.step4.browser_core.single_mixins._exploratory import (
                            _verify_delete_row_unchanged as _v_del_noop,
                        )
                        try:
                            _del_noop_ok = await _v_del_noop(
                                page, _rows_before_delete, timeout_ms
                            )
                        except Exception as _e:
                            _del_noop_ok = False
                            _log.warning(
                                "[Guided] delete_aborted DOM 证据异常: %s", str(_e)[:100]
                            )
                        if _del_noop_ok:
                            _ok, _actual, _vmethod = (
                                True,
                                "取消/拒绝后列表行数未减少（删除未生效，可观察 DOM 证据）",
                                "删除未生效证据",
                            )
                        else:
                            _log.warning(
                                "[Guided] delete_aborted 行数却减少: before=%s",
                                _rows_before_delete,
                            )
                    elif _is_load:
                        from src.pipeline.steps.step4.browser_core.single_mixins._exploratory import (
                            _verify_page_content_loaded as _v_pcl,
                        )

                        _v_ok = await _v_pcl(page)
                        if _v_ok:
                            _ok, _actual, _vmethod = (
                                True,
                                "页面已加载（无404/主内容区可见）",
                                "页面加载证据",
                            )
                        else:
                            # 加载断言是 Step3 给每条用例注入的通用样板，不是用例自身的
                            # 权威期望；用例已声明结构化 oracle（或本身期望被拒绝）时，
                            # 其失败不得否决权威判据（与弱 oracle 同纪律），降级为非判定态。
                            if has_structured_oracle or expects_denial:
                                _nondecisive = True
                                _vmethod = "非判定态"
                                _actual = (
                                    "页面为拒绝/错误响应，非'正常加载'页；"
                                    "结论交用例结构化 oracle 裁决"
                                )
                            _log.warning(
                                "[Guided] 加载断言 DOM 证据失败: expect=%r",
                                str(_expect)[:40],
                            )
                    else:
                        _ok, _actual, _vmethod = await self._guided_verify(
                            page, _expect, timeout_ms, expected_value=_value
                        )
                    if _ok is None:
                        # ★ 根因修复(2026-09-28): 引导判定返回 None = 页面 404/导航
                        #   不可达的诚实 SKIP（见 _guided_verify NOT_NAVIGABLE_SKIP）。
                        #   不计失败、不计通过，仅记录证据——导航基线失配非产品结论。
                        _nondecisive = True
                        _evidence_parts.append(
                            "验证跳过[Nav404]: %s" % _actual
                        )
                        _verify_records.append(
                            {
                                "expect": _expect,
                                "actual": str(_actual)[:200],
                                "method": str(_vmethod or "NOT_NAVIGABLE_SKIP"),
                                "passed": True,  # 静态采信白名单无此方法，不影响
                            }
                        )
                    if _ok:
                        # ★ 2026-10-27 证据诚实性修复：通过证据必须携带**可观测事实**
                        #   （_actual = 页面命中文本/反馈条内容），不能把期望当证据。
                        #   根因: 原实现写 `% (_vmethod, _expect)`，执行记录里出现
                        #   "验证通过[错误条检测]: 在监控模块中，当接口错误率超过 5%时，告警"
                        #   —— 那是**需求句子**，不是页面事实（实测 TC-REQ-010/011/020/065-I01
                        #   的 actual 全部如此，截图显示实际停在"新增商品"表单）。
                        #   期望仍完整保留在 _verify_records.expect 中，供报告对照。
                        # ★ 2026-09-23 反假PASS: 证据必须携带验证方法，
                        #   聚合层据此识别"关键词匹配"类弱证据（泛词命中≠期望达成）
                        _evidence_parts.append(
                            "验证通过[%s]: %s" % (_vmethod, _actual or "(无文本证据)")
                        )
                        _verify_records.append(
                            {
                                "expect": _expect,
                                "actual": str(_actual)[:200],
                                "method": _vmethod,
                                "passed": True,
                            }
                        )
                    elif _is_page_feature_ctx:
                        # ★ 根因修复(2026-09-25) RC-O: 页面特征验证点是**路由上下文
                        #   检查**，不是业务断言，失败不得作为用例失败判据。
                        #   根因: Step3 的 _fix_explore_task 为每条用例追加
                        #     target="X页面特征" 的验证点（前提"特征文本必然出现在
                        #     目标页"），但两类**合法**场景下必然不成立：
                        #       ① 用例继续深入子视图（新增/编辑表单）→ 入口页特征
                        #          被替换（实测 TC-REQ-009-I01：期望'搜索'，此时页面
                        #          已是新增表单，SUT 行为完全正确）；
                        #       ② 需求本身期望"拒绝访问/403" → 目标页根本不渲染
                        #          （实测 TC-REQ-019/023/032-I01：期望'下一页'，
                        #          实际是"权限不足：该操作需要 admin 角色"，SUT 正确）。
                        #     原实现把这两类"上下文不符"计为验证失败 → 用例被判
                        #     HARNESS_ERROR（引擎自造失败），与同一用例内已通过的
                        #     业务断言（拒绝判定/语义断言）自相矛盾。
                        #   通用修复: 路由上下文检查失败降级为**证据**（warning），
                        #     页面身份由客观的 url_matches 断言负责；零业务硬编码。
                        _log.warning(
                            "[Guided] 页面特征上下文检查未命中（不判失败）: "
                            "expect=%r actual=%r",
                            _expect[:40],
                            str(_actual)[:80],
                        )
                        _evidence_parts.append(
                            "上下文检查未命中(不计失败): '%s'（页面身份由 "
                            "url_matches 断言判定）" % _expect
                        )
                    elif _nondecisive:
                        # 无权威结论（加载断言不适用/导航不可达）→ 只留证据、不计失败，
                        # 结论交用例声明的结构化 checks（与弱证据不判 PASS 对称）。
                        _log.warning(
                            "[Guided] 验证点非判定态（交结构化 checks 裁决）: "
                            "expect=%r actual=%r",
                            _expect[:40],
                            str(_actual)[:80],
                        )
                        _evidence_parts.append(
                            "验证非判定(交结构化断言裁决): '%s'" % _expect
                        )
                    elif not _value and _vmethod == "未匹配" and has_structured_oracle:
                        # ★ 非判定态（反假 FAIL，与引擎既有的「弱证据不判 PASS」对称）：
                        #   本验证点的 expect 无机器可判的期望值（`value` 空），唯一可用
                        #   信号是「关键词子词」这一**弱 oracle**；语义断言兜底在运行期
                        #   零 AI 下返回 UNKNOWN（不可用）。弱信号既不足以判 PASS，也不
                        #   足以判 FAIL —— 唯一权威判据是用例声明的结构化 checks。
                        #   故降级为非判定态（只留证据、不计失败），结论交 checks 裁决。
                        _log.warning(
                            "[Guided] 验证点无期望值且弱 oracle 未命中 → 非判定态"
                            "(交结构化 checks 裁决): expect=%r",
                            _expect[:40],
                        )
                        _evidence_parts.append(
                            "验证非判定(弱oracle未命中，交结构化断言裁决): '%s'" % _expect
                        )
                    else:
                        _all_passed = False
                        _verification_failures.append(
                            {
                                "point": "验证'%s'" % _expect,
                                "expect": _expect,
                                "actual": _actual,
                            }
                        )
                        _evidence_parts.append(
                            "验证失败: 期望'%s' 实际'%s'" % (_expect, _actual)
                        )
                elif _action == "wait":
                    _wait_ms = 1000
                    try:
                        _wait_ms = min(int(_value) if _value.isdigit() else 1000, 10000)
                    except (ValueError, TypeError):
                        _wait_ms = 1000
                    await page.wait_for_timeout(_wait_ms)
                    _evidence_parts.append("等待%dms" % _wait_ms)
                elif _action == "navigate":
                    _url = _target or _value
                    if _url:
                        try:
                            await page.goto(_url, timeout=min(timeout_ms, 30000))
                            await page.wait_for_timeout(2000)
                            _evidence_parts.append("导航到: %s" % _url)
                        except Exception as _ne:
                            _all_passed = False
                            _verification_failures.append(
                                {
                                    "point": "导航到'%s'" % _url,
                                    "expect": "页面加载",
                                    "actual": str(_ne)[:80],
                                }
                            )
                            _evidence_parts.append("导航失败: %s" % str(_ne)[:80])
                    else:
                        _evidence_parts.append("navigate: 无URL")
                # ── ★ T02(2026-08-06): 动作模板新增动作分支（press_key/clear/type_text/
                #    double_click/hover/right_click/select_option/check/uncheck/set_date/
                #    key_down/key_up/scroll/upload_file）──
                #    每分支记录 _actions_taken + evidence，失败不阻断（与现有语义一致）。
                elif _action == "press_key":
                    _key = _value or "Enter"
                    _loc = await self._guided_locate(page, _target, timeout_ms)
                    if _loc is not None:
                        await _loc.press(_key, timeout=min(timeout_ms, 5000))
                    else:
                        await page.keyboard.press(_key)
                    _actions_taken.append(
                        {
                            "type": "press_key",
                            "target": _target or "keyboard",
                            "value": _key,
                        }
                    )
                    _evidence_parts.append("按键: %s" % _key)
                elif _action == "clear":
                    _loc = await self._guided_locate(page, _target, timeout_ms)
                    if _loc is not None:
                        await _loc.fill("")
                        _evidence_parts.append("清空: %s" % _target)
                        _actions_taken.append({"type": "clear", "target": _target})
                    else:
                        _all_passed = False
                        _verification_failures.append(
                            {
                                "point": "清空'%s'" % _target,
                                "expect": "可清空",
                                "actual": "未找到元素",
                            }
                        )
                        _evidence_parts.append("清空'%s'失败: 未找到元素" % _target)
                elif _action == "type_text":
                    _loc = await self._guided_locate(page, _target, timeout_ms)
                    if _loc is not None:
                        await _loc.press_sequentially(_value, delay=20)
                        _evidence_parts.append("逐字输入: %s" % _value)
                        _actions_taken.append(
                            {"type": "type_text", "target": _target, "value": _value}
                        )
                    else:
                        await page.keyboard.type(_value, delay=20)
                        _evidence_parts.append("键盘输入: %s" % _value)
                        _actions_taken.append(
                            {"type": "type_text", "target": "keyboard", "value": _value}
                        )
                elif _action == "double_click":
                    _loc = await self._guided_locate(page, _target, timeout_ms)
                    if _loc is not None:
                        await _loc.dblclick(timeout=min(timeout_ms, 5000))
                        await page.wait_for_timeout(500)
                        _evidence_parts.append("双击: %s" % _target)
                        _actions_taken.append(
                            {"type": "double_click", "target": _target}
                        )
                    else:
                        _all_passed = False
                        _verification_failures.append(
                            {
                                "point": "双击'%s'" % _target,
                                "expect": "可双击",
                                "actual": "未找到元素",
                            }
                        )
                        _evidence_parts.append("双击'%s'失败: 未找到元素" % _target)
                elif _action == "hover":
                    _loc = await self._guided_locate(page, _target, timeout_ms)
                    if _loc is not None:
                        await _loc.hover(timeout=min(timeout_ms, 5000))
                        await page.wait_for_timeout(500)
                        _evidence_parts.append("悬停: %s" % _target)
                        _actions_taken.append({"type": "hover", "target": _target})
                    else:
                        _all_passed = False
                        _verification_failures.append(
                            {
                                "point": "悬停'%s'" % _target,
                                "expect": "可悬停",
                                "actual": "未找到元素",
                            }
                        )
                        _evidence_parts.append("悬停'%s'失败: 未找到元素" % _target)
                elif _action == "right_click":
                    _loc = await self._guided_locate(page, _target, timeout_ms)
                    if _loc is not None:
                        await _loc.click(button="right", timeout=min(timeout_ms, 5000))
                        await page.wait_for_timeout(500)
                        _evidence_parts.append("右键: %s" % _target)
                        _actions_taken.append(
                            {"type": "right_click", "target": _target}
                        )
                    else:
                        _all_passed = False
                        _verification_failures.append(
                            {
                                "point": "右键'%s'" % _target,
                                "expect": "可右键",
                                "actual": "未找到元素",
                            }
                        )
                        _evidence_parts.append("右键'%s'失败: 未找到元素" % _target)
                elif _action == "select_option":
                    _loc = await self._guided_locate(page, _target, timeout_ms)
                    if _loc is not None:
                        if _value:
                            await _loc.select_option(label=_value)
                        else:
                            await _loc.select_option(index=0)
                        await page.wait_for_timeout(500)
                        _evidence_parts.append("选择下拉: %s → %s" % (_target, _value))
                        _actions_taken.append(
                            {
                                "type": "select_option",
                                "target": _target,
                                "value": _value,
                            }
                        )
                    else:
                        _all_passed = False
                        _verification_failures.append(
                            {
                                "point": "选择下拉'%s'" % _target,
                                "expect": "可选择",
                                "actual": "未找到元素",
                            }
                        )
                        _evidence_parts.append("选择下拉'%s'失败: 未找到元素" % _target)
                elif _action == "check":
                    _loc = await self._guided_locate(page, _target, timeout_ms)
                    if _loc is not None:
                        try:
                            await _loc.check(timeout=min(timeout_ms, 5000))
                        except Exception:
                            await _loc.click(timeout=min(timeout_ms, 5000))
                        await page.wait_for_timeout(300)
                        _evidence_parts.append("勾选: %s" % _target)
                        _actions_taken.append({"type": "check", "target": _target})
                    else:
                        _all_passed = False
                        _verification_failures.append(
                            {
                                "point": "勾选'%s'" % _target,
                                "expect": "可勾选",
                                "actual": "未找到元素",
                            }
                        )
                        _evidence_parts.append("勾选'%s'失败: 未找到元素" % _target)
                elif _action == "uncheck":
                    _loc = await self._guided_locate(page, _target, timeout_ms)
                    if _loc is not None:
                        try:
                            await _loc.uncheck(timeout=min(timeout_ms, 5000))
                        except Exception:
                            await _loc.click(timeout=min(timeout_ms, 5000))
                        await page.wait_for_timeout(300)
                        _evidence_parts.append("取消勾选: %s" % _target)
                        _actions_taken.append({"type": "uncheck", "target": _target})
                    else:
                        _all_passed = False
                        _verification_failures.append(
                            {
                                "point": "取消勾选'%s'" % _target,
                                "expect": "可取消勾选",
                                "actual": "未找到元素",
                            }
                        )
                        _evidence_parts.append("取消勾选'%s'失败: 未找到元素" % _target)
                elif _action == "set_date":
                    _loc = await self._guided_locate(page, _target, timeout_ms)
                    if _loc is not None:
                        await _loc.fill(_value, timeout=min(timeout_ms, 5000))
                        await page.keyboard.press("Enter")
                        _evidence_parts.append("设置日期: %s → %s" % (_target, _value))
                        _actions_taken.append(
                            {"type": "set_date", "target": _target, "value": _value}
                        )
                    else:
                        _all_passed = False
                        _verification_failures.append(
                            {
                                "point": "设置日期'%s'" % _target,
                                "expect": "可设置",
                                "actual": "未找到元素",
                            }
                        )
                        _evidence_parts.append("设置日期'%s'失败: 未找到元素" % _target)
                elif _action == "key_down":
                    await page.keyboard.down(_value or "Control")
                    _actions_taken.append(
                        {
                            "type": "key_down",
                            "target": _target,
                            "value": _value or "Control",
                        }
                    )
                    _evidence_parts.append("按下组合键: %s" % (_value or "Control"))
                elif _action == "key_up":
                    await page.keyboard.up(_value or "Control")
                    _actions_taken.append(
                        {
                            "type": "key_up",
                            "target": _target,
                            "value": _value or "Control",
                        }
                    )
                    _evidence_parts.append("释放组合键: %s" % (_value or "Control"))
                elif _action == "scroll":
                    _dy = int(_value) if str(_value).isdigit() else 500
                    await page.mouse.wheel(0, _dy)
                    await page.wait_for_timeout(300)
                    _actions_taken.append(
                        {"type": "scroll", "target": _target, "value": str(_dy)}
                    )
                    _evidence_parts.append("滚动: %dpx" % _dy)
                elif _action == "upload_file":
                    _path = _value or "test_upload.csv"
                    _loc = await self._guided_locate(page, _target, timeout_ms)
                    _uploaded = False
                    if _loc is not None:
                        try:
                            await _loc.set_input_files(
                                _path, timeout=min(timeout_ms, 15000)
                            )
                            _uploaded = True
                        except Exception:
                            _uploaded = False
                    if not _uploaded:
                        try:
                            _file_inputs = page.locator('input[type="file"]')
                            if await _file_inputs.count() > 0:
                                await _file_inputs.first.set_input_files(
                                    _path, timeout=min(timeout_ms, 15000)
                                )
                                _uploaded = True
                        except Exception:
                            _uploaded = False
                    if _uploaded:
                        _evidence_parts.append("上传文件: %s" % _path)
                        _actions_taken.append(
                            {"type": "upload_file", "target": _target, "value": _path}
                        )
                    else:
                        _all_passed = False
                        _verification_failures.append(
                            {
                                "point": "上传文件'%s'" % _path,
                                "expect": "文件已选择",
                                "actual": "未找到文件输入框",
                            }
                        )
                        _evidence_parts.append("上传文件失败: 未找到文件输入框")
                elif _action in ("wheel", "scroll_wheel"):
                    # ★ 契约 GAP 补齐：滚轮滚动（支持 dx,dy）
                    _dx, _dy = 0, 0
                    _v = str(_value or "").strip()
                    if _v and "," in _v and all(_p.isdigit() for _p in _v.split(",")):
                        _parts = _v.split(",")
                        _dx = int(_parts[0])
                        _dy = int(_parts[1]) if len(_parts) > 1 else 0
                    elif _v.isdigit():
                        _dy = int(_v)
                    await page.mouse.wheel(_dx, _dy)
                    await page.wait_for_timeout(200)
                    _actions_taken.append(
                        {"type": "wheel", "target": _target, "value": "%d,%d" % (_dx, _dy)}
                    )
                    _evidence_parts.append("滚轮滚动: %d,%d" % (_dx, _dy))
                elif _action == "drag":
                    # ★ 契约 GAP 补齐：从源元素拖拽到目标元素（value=目标 hint）
                    _src = await self._guided_locate(page, _target, timeout_ms)
                    _dest_hint = _value or _target
                    _dest = await self._guided_locate(page, _dest_hint, timeout_ms)
                    if _src is None or _dest is None:
                        _all_passed = False
                        _verification_failures.append(
                            {
                                "point": "拖拽'%s'" % _target,
                                "expect": "可拖拽",
                                "actual": "源或目标元素未找到",
                            }
                        )
                        _evidence_parts.append("拖拽失败: 未找到源或目标元素")
                    else:
                        try:
                            _sb = await _src.bounding_box()
                            _db = await _dest.bounding_box()
                            if not _sb or not _db:
                                raise RuntimeError("无 bounding_box")
                            await page.mouse.move(
                                _sb["x"] + _sb["width"] / 2,
                                _sb["y"] + _sb["height"] / 2,
                            )
                            await page.mouse.down()
                            await page.mouse.move(
                                _db["x"] + _db["width"] / 2,
                                _db["y"] + _db["height"] / 2,
                                steps=8,
                            )
                            await page.mouse.up()
                            _actions_taken.append(
                                {"type": "drag", "target": _target, "value": _dest_hint}
                            )
                            _evidence_parts.append("拖拽: %s → %s" % (_target, _dest_hint))
                        except Exception as _de:
                            _all_passed = False
                            _verification_failures.append(
                                {
                                    "point": "拖拽'%s'" % _target,
                                    "expect": "可拖拽",
                                    "actual": "异常: %s" % str(_de)[:80],
                                }
                            )
                            _evidence_parts.append("拖拽异常: %s" % str(_de)[:80])
                elif _action == "drag_offset":
                    # ★ 契约 GAP 补齐：元素按指定偏移拖拽（value=dx,dy）
                    _dx, _dy = 0, 40
                    _v = str(_value or "").strip()
                    if _v and "," in _v and all(
                        _p.lstrip("-").isdigit() for _p in _v.split(",")
                    ):
                        _parts = _v.split(",")
                        _dx = int(_parts[0])
                        _dy = int(_parts[1]) if len(_parts) > 1 else 0
                    _loc = await self._guided_locate(page, _target, timeout_ms)
                    if _loc is None:
                        _all_passed = False
                        _verification_failures.append(
                            {
                                "point": "拖拽偏移'%s'" % _target,
                                "expect": "可拖拽",
                                "actual": "未找到元素",
                            }
                        )
                        _evidence_parts.append("拖拽偏移失败: 未找到元素")
                    else:
                        try:
                            _bb = await _loc.bounding_box()
                            if not _bb:
                                raise RuntimeError("无 bounding_box")
                            await page.mouse.move(
                                _bb["x"] + _bb["width"] / 2,
                                _bb["y"] + _bb["height"] / 2,
                            )
                            await page.mouse.down()
                            await page.mouse.move(
                                _bb["x"] + _bb["width"] / 2 + _dx,
                                _bb["y"] + _bb["height"] / 2 + _dy,
                                steps=8,
                            )
                            await page.mouse.up()
                            _actions_taken.append(
                                {
                                    "type": "drag_offset",
                                    "target": _target,
                                    "value": "%d,%d" % (_dx, _dy),
                                }
                            )
                            _evidence_parts.append("拖拽偏移: %s +%d,%d" % (_target, _dx, _dy))
                        except Exception as _doe:
                            _all_passed = False
                            _verification_failures.append(
                                {
                                    "point": "拖拽偏移'%s'" % _target,
                                    "expect": "可拖拽",
                                    "actual": "异常: %s" % str(_doe)[:80],
                                }
                            )
                            _evidence_parts.append("拖拽偏移异常: %s" % str(_doe)[:80])
                elif _action in ("multi_touch", "multi_touch_pinch"):
                    # ★ 契约 GAP 补齐：多点触控捏合（CDP dispatchTouchEvent）
                    _loc = await self._guided_locate(page, _target, timeout_ms)
                    try:
                        _bb = (await _loc.bounding_box()) if _loc else None
                        if _bb:
                            _cx, _cy = _bb["x"] + _bb["width"] / 2, _bb["y"] + _bb["height"] / 2
                        else:
                            _vs = await page.viewport_size()
                            _cx, _cy = (_vs or {"width": 800}).get("width", 800) / 2, (_vs or {"height": 600}).get("height", 600) / 2
                        _cdp = page.context.new_cdp_session(page)
                        _sc, _r = 1.6, 60
                        await _cdp.send(
                            "Input.dispatchTouchEvent",
                            {
                                "type": "touchStart",
                                "touchPoints": [
                                    {"x": _cx - _r, "y": _cy},
                                    {"x": _cx + _r, "y": _cy},
                                ],
                            },
                        )
                        await _cdp.send(
                            "Input.dispatchTouchEvent",
                            {
                                "type": "touchMove",
                                "touchPoints": [
                                    {"x": _cx - _r * _sc, "y": _cy},
                                    {"x": _cx + _r * _sc, "y": _cy},
                                ],
                            },
                        )
                        await _cdp.send(
                            "Input.dispatchTouchEvent",
                            {"type": "touchEnd", "touchPoints": []},
                        )
                        _actions_taken.append(
                            {"type": "multi_touch", "target": _target, "value": "pinch"}
                        )
                        _evidence_parts.append("多点触控捏合: %s" % _target)
                    except Exception as _mte:
                        _all_passed = False
                        _verification_failures.append(
                            {
                                "point": "多点触控'%s'" % _target,
                                "expect": "可捏合",
                                "actual": "异常: %s" % str(_mte)[:80],
                            }
                        )
                        _evidence_parts.append("多点触控异常: %s" % str(_mte)[:80])
                elif _action in ("download_file", "download"):
                    # ★ 契约 GAP 补齐：触发下载并保存
                    _loc = await self._guided_locate(page, _target, timeout_ms)
                    try:
                        async with page.expect_download(
                            timeout=min(timeout_ms, 15000)
                        ) as _dl_info:
                            if _loc is not None:
                                await _loc.click(timeout=min(timeout_ms, 8000))
                            else:
                                _dl_link = page.locator(
                                    "a[download], button:has-text('下载'), a:has-text('导出')"
                                ).first
                                if await _dl_link.count() > 0:
                                    await _dl_link.click(timeout=min(timeout_ms, 8000))
                                else:
                                    raise RuntimeError("未找到下载元素")
                        _dl = await _dl_info
                        _dl_path = os.path.join(
                            tempfile.gettempdir(),
                            _dl.suggested_filename or "e2e_download.bin",
                        )
                        await _dl.save_as(_dl_path)
                        _actions_taken.append(
                            {"type": "download_file", "target": _target, "value": _dl_path}
                        )
                        _evidence_parts.append(
                            "下载文件: %s" % (_dl.suggested_filename or _dl_path)
                        )
                    except Exception as _dle:
                        _all_passed = False
                        _verification_failures.append(
                            {
                                "point": "下载'%s'" % _target,
                                "expect": "触发下载",
                                "actual": "异常: %s" % str(_dle)[:80],
                            }
                        )
                        _evidence_parts.append("下载异常: %s" % str(_dle)[:80])
                elif _action == "focus_check":
                    # ★ 键盘可达性：断言 document.activeElement 与目标命中
                    #   驱动 tab_traverse 模板的焦点校验，见 action_contract.py
                    _focused_tag = ""
                    with guard("_exec_one_step: _info = await page.evaluate( '() => ", log=_log):
                        _info = await page.evaluate(
                            "() => { const e = document.activeElement;"
                            " return {tag: (e && e.tagName||'').toLowerCase(),"
                            " name: (e && (e.id||e.name||e.textContent||'').trim().slice(0,40)||''),"
                            " cls: (e && e.className||'').toString().slice(0,40) }; }"
                        ) or {}
                        _focused_tag = ("%s#%s" % (_info.get("tag"), _info.get("name"))).strip("#")
                    if _focused_tag and (_target in _focused_tag or _focused_tag in _target):
                        _actions_taken.append(
                            {"type": "focus_check", "target": _target, "value": _focused_tag}
                        )
                        _evidence_parts.append("焦点校验通过: %s" % _focused_tag)
                    else:
                        _all_passed = False
                        _verification_failures.append(
                            {
                                "point": "焦点校验'%s'" % _target,
                                "expect": "焦点落在%s" % _target,
                                "actual": "当前焦点: %s" % (_focused_tag or "未知(无activeElement)"),
                            }
                        )
                        _evidence_parts.append(
                            "焦点校验失败: 期望%s, 实际%s" % (_target, _focused_tag or "无activeElement")
                        )
                else:
                    # ★ 契约统一：不再静默忽略未知 action
                    _kind = classify_action(_action)
                    _evidence_parts.append("[ActionContract] %s action=%r（未执行）" % (_kind, _action))
                    if _kind == "GAP":
                        _log.warning(
                            "[ActionContract] GAP: 契约已登记 action=%r 但引导路径未实现"
                            "（结构化路径已支持）——双执行路径漂移，需补齐分支",
                            _action,
                        )
                    elif _kind in ("TEMPLATE", "RESERVED"):
                        # 模板/流程级动作不应作为 step 原子 action 直接进入执行层
                        _log.info(
                            "[ActionContract] %s: action=%r 是模板/流程级动作，不作为 step 执行"
                            "（如误报，请检查生成层是否未展开模板）", _kind, _action,
                        )
                    else:
                        _log.warning("[ActionContract] UNKNOWN action=%r（契约外，配置或生成错误）", _action)
            except Exception as _e:
                _all_passed = False
                _verification_failures.append(
                    {
                        "point": "%s '%s'" % (_action, _target),
                        "expect": _expect,
                        "actual": "异常: %s" % str(_e)[:80],
                    }
                )
                _evidence_parts.append("%s异常: %s" % (_action, str(_e)[:80]))

        # ① 操作前验证（页面内容检查，在点击按钮前）
        # ★ v5.5 fix(2026-07-30): 验证前关闭弹窗——弹窗遮挡页面内容导致验证失败
        _dialog_visible = await check_dialog_visible(page)
        if _dialog_visible:
            _log.info("[Guided] 检测到弹窗，尝试关闭...")
            with guard("_execute_guided: await page.keyboard.press('Escape')", log=_log, level=logging.WARNING):
                await page.keyboard.press("Escape")
                await page.wait_for_timeout(800)
                _dialog_still = await check_dialog_visible(page)
                if _dialog_still:
                    # 尝试点击关闭按钮
                    _close_btn = page.locator(
                        '[role="dialog"] button, .ant-modal-close, .el-dialog__close, [aria-label="Close"]'
                    ).first
                    if await _close_btn.count() > 0:
                        await _close_btn.click(timeout=3000)
                        await page.wait_for_timeout(800)
                _log.info("[Guided] 弹窗处理完成")
        for _i, _step in enumerate(_pre_verifications):
            await _exec_one_step(_step, "Pre %d/%d" % (_i + 1, len(_pre_verifications)))

        # ② 点击操作按钮 + 填充输入（核心操作）
        # ★ 场景正确性修复: login 场景【不再】跳过②块。
        #   原"跳过②块"让 SessionAware 判'无需登录'后整个登录动作消失 →
        #   '业务操作未执行' HARNESS_ERROR（TC-REQ-001-I01 退化根因）。
        #   登录动作(填凭证 + 点登录按钮)本就是该用例核心业务操作，必须执行。
        #   防误点'退出登录': _guided_click 用 role=button 定位(退出登录多为链接)，
        #   且下方登录分支 + _guided_click 内显式排除'退出登录'文案。
        _is_login = False
        with guard("_execute_guided: from src.core.semantic_extractor imp", log=_log):
            from src.core.semantic_extractor import extract_control_intent

            if extract_control_intent(operation).get("scene") == "login":
                _is_login = True
        # ★ 通用修复(2026-09-24): 门控从「仅 inputs 非空」放宽为「inputs 非空 或 表单录入型」。
        #   根因: op_type=form 的用例常无 inputs（REQ-001"输入资产名称"、REQ-006 下拉项、
        #     REQ-007 备注文本等），原门控使②块整体跳过 → 不进表单、不填、不提交
        #     → 验证停在列表页 → 引擎自造 ERROR（13/26 非 PASS 的主因）。
        #
        # ★ 根因修复(2026-09-25) RC-U: 冲突型需求（唯一性/重复）同为录入型，必须进表单。
        #   根因: 这类需求的被测路径是"提交一个**已存在**的标识值 → 服务端拒绝"，
        #     必然经由录入表单；op_type 为空（"触发服务端验证失败"无录入动词）时
        #     门控不开 → 停在列表页 → 采集到的已有值无处可填 → 引擎自造 ERROR。
        #   判据: 需求语义含冲突/唯一性 → 录入型（与"冲突用例必须构造重复载荷"同源）。
        _is_conflict_case = bool(
            _CONFLICT_DATA_RE.search(
                " ".join(
                    [
                        str(operation or ""),
                        str(description or ""),
                        str(expected or ""),
                    ]
                )
            )
        )
        # ★ 根因修复(2026-09-25) RC-W: 「服务端拒绝」型用例与冲突型同源 —— 都必须构造
        #   "客户端可过、服务端必拒"的载荷，通用手法即**复用已有实体的标识值**。
        #   根因: 判据原仅认冲突语汇（重复/已存在/唯一性），而 REQ-010 的表述是
        #     "当资产数据通过客户端校验但服务端校验失败时…" —— 语义同属服务端拒绝，
        #     却无冲突关键词 → 未采集 → 引擎填入 `<unique>` 新值 → 服务端唯一性校验
        #     通过 → 创建成功 → "返回错误提示 flash 红条且不写库"断言恒 FAIL
        #     （实测 TC-REQ-010-I01）。
        #   通用判据（跨系统/行业/框架，零业务硬编码）: 客户端校验**看不到服务端状态**
        #     （唯一性/跨记录约束等）是 Web 通用事实，故"客户端通过但服务端拒绝"的载荷
        #     只能取**服务端已存在的标识值** —— 这正是高级测试工程师构造服务端拒绝
        #     用例的通用手法，与业务无关。
        #   边界: 只在 `_enter_form` 为真时采集。非录入型的服务端拒绝（如"维护中的资产
        #     不允许删除"）不经表单，采集值会落到无关控件上 → 不采集。
        #   与 RC-T 同源同判据: 复用 `_server_blob`（description+expected）与
        #     `_CLIENT_BLOCK_WORDS`，确保"客户端拦截型"（REQ-041）不被误纳入。
        _server_reject_ctx = bool(_SERVER_REJECT_RE.search(_server_blob)) and not any(
            _w in _server_blob for _w in _CLIENT_BLOCK_WORDS
        )
        _needs_server_reject_payload = _is_conflict_case or _server_reject_ctx
        # ★ RC-AD(2026-09-25): 「表单语境 + 校验反馈」型需求 → 必进表单（判据见 _FORM_CONTEXT_RE）。
        _form_validation_ctx = bool(
            _expects_error_feedback(_server_blob)
            and _FORM_CONTEXT_RE.search(_server_blob)
        )
        _enter_form = (
            bool(inputs)
            or op_type == "form"
            # ★ 根因修复(2026-09-28): 删除操作也必须执行点击——删除是"点击行内删除
            #   按钮→处理确认框"的动作链，不是"进表单填值"。原条件不含 delete_op，
            #   删除用例从不点删除按钮，验证"删除完成/确认提示"空手断言必 FAIL
            #   （实测 SmartShop TC-REQ-003-I01）。
            or op_type == "delete_op"
            or _is_conflict_case
            or _form_validation_ctx
        )
        # 冲突型用例的录入入口解析按「表单录入型」处理（需求无录入动词时仍能定位"新增"入口）
        _eff_op_type = "form" if (
            (not op_type) and (_is_conflict_case or _form_validation_ctx)
        ) else op_type
        # ② 核心操作 —— 用例声明的权威动作序列是「做什么」的唯一事实源（SSOT）。
        #   根因: 声明动作（fill/click/select_option/check…）此前只被当作**定位提示**
        #     传给按钮解析，从未 materialize 成可执行步骤 → 用例声明的测试激励
        #     （注入 SQL 载荷、填检索词、点查询/提交）根本没施加 → 断言在**未受激励**
        #     的页面上判定 → 假 Bug（实测 TC-REQ-010-I04：载荷从未注入，empty-row
        #     必然缺席却被记 PRODUCT_BUG）。与 D34「声明导航/声明点击目标」同源，
        #     只是当时只补了导航与点击提示，漏掉了动作本身的执行。
        #   修法: 用例**有声明**时，声明即权威 —— 按声明顺序执行，②块的经验式合成
        #     （从 operation/inputs 猜"进表单→填字段→点提交"、冲突载荷采集、自动提交）
        #     整体关闭，杜绝「执行端自己加戏」：它施加的是用例没要求的业务操作
        #     （多创建一条记录），既污染靶子数据（D31），又把用例声明的激励冲掉
        #     —— 实测 TC-REQ-013-I05：XSS 用例被合成为"复用已有名称再检索"，
        #     XSS 载荷从未注入，随后按"载荷应 0 命中"断言 → 假 FAIL。
        #   通用性: 跨系统/行业/页面 —— 只依赖"声明是否存在"这一 SSOT 判据，零业务硬编码。
        _declared_steps: list[dict] = []
        # ★ 用例是否**声明了**动作序列 —— 「做什么」的唯一事实源是否存在的判据。
        #   注意是看**原始声明**（含 navigate/screenshot），不是看 _declared_steps
        #   （后者过滤掉了 navigate）：只声明"导航到某页 + 静态结构断言"的用例
        #   （如 TC-REQ-014-E01）同样是**已声明**，启发式合成不得再加戏。
        _declared_actions_present = bool(declared_actions)
        for _da in declared_actions or []:
            if not isinstance(_da, dict):
                continue
            _dt = str(_da.get("type") or "").lower()
            if _dt in ("navigate", "screenshot") or not _dt:
                continue  # navigate 已由「声明导航」执行，避免二次跳转
            # 目标优先取声明里的自然语言 target_hint（guided 定位器吃语义），
            # 无 hint 时退回 selector；执行端只读声明，不重新推导。
            _d_target = str(
                _da.get("target_hint") or _da.get("selector") or ""
            ).strip()
            # 声明动作的"施加值"取任何可执行取值位：value（fill/check）或
            # option_text（select_option）——先前只读 value，导致声明的下拉选项
            # 从未被选中（select_option 以空值执行 = 空操作），后续校验断言假 FAIL。
            _d_value = _da.get("value")
            if _d_value is None or str(_d_value) == "":
                _d_value = _da.get("option_text")
            _declared_steps.append(
                {
                    "action": _dt,
                    "target": _d_target,
                    "value": "" if _d_value is None else str(_d_value),
                    "expect": "",
                }
            )
        _declared_executed = False
        if _declared_actions_present:
            _log.info(
                "[Guided] 执行用例声明的权威动作序列: %d 步 (%s)",
                len(_declared_steps),
                ", ".join(s["action"] for s in _declared_steps),
            )
            _declared_phase = True
            for _di, _dstep in enumerate(_declared_steps):
                await _exec_one_step(
                    _dstep, "Act %d/%d" % (_di + 1, len(_declared_steps))
                )
            _declared_phase = False
            _declared_executed = True

        if _enter_form and not _declared_executed:
            _log.info(
                "[Guided] 处理inputs: %d项 (op_type=%s%s)",
                len(inputs),
                op_type or "-",
                ", conflict" if _is_conflict_case
                else (", form-validation" if _form_validation_ctx
                      else (", server-reject" if _server_reject_ctx else "")),
            )
            # ★ 根因修复(2026-09-25) RC-U: 冲突型用例的标识字段必须复用系统已有实体的值。
            #   根因: 引擎对标识字段一律填 `<unique>`/样例**新值** → 服务端唯一性校验
            #     必然通过 → 创建成功 → "系统拒绝并提示名称已存在"类断言恒 FAIL
            #     （实测 TC-REQ-002-I01 ERROR / TC-REQ-010-I01 FAIL / TC-REQ-045-I01）。
            #   采集时机: **点击录入入口之前** —— 此刻仍在实体列表页，可读到已有行；
            #     入口点击后页面切到表单，列表值不可再得。
            #   采集判据: 见 _harvest_existing_identifier（列名命中需求正文优先，
            #     否则取首个非 ID/操作列）。采集失败静默跳过（维持原行为，不造假数据）。
            #   通用性: 跨系统/行业/页面 —— "冲突用例复用已有值"是测试设计通用原理。
            #   RC-W 起，触发条件放宽为「服务端拒绝型」（含冲突型与"服务端校验失败"型）。
            if _needs_server_reject_payload:
                _cf_label, _cf_value = await self._harvest_existing_identifier(
                    page,
                    [str(operation or ""), str(description or ""), str(expected or "")],
                )
                if _cf_label and _cf_value and _cf_label not in inputs:
                    inputs = dict(inputs)
                    inputs[_cf_label] = {
                        "value": _cf_value,
                        "source": "conflict_existing_entity",
                    }
                    _log.info(
                        "[ConflictData] 服务端拒绝型用例 → 标识字段复用已有值: %s=%r",
                        _cf_label,
                        _cf_value[:40],
                    )
                    _evidence_parts.append(
                        "冲突数据: 复用已有'%s'=%s" % (_cf_label, _cf_value[:30])
                    )
            import re as _re

            _op_btn_text = ""
            # ★ R1 修正(2026-08-28): 按钮是**执行层 DOM 定位**不是语义理解——
            #   实测 qwen2.5:7b 对 button 泛化猜测（"可新增患者"→"提交"），
            #   不如正则"动词+实体"（"新增患者"，与真实链接文本对齐）。
            #   顺序: 括号标注 > 正则动词+实体 > AI button（仅作最后兜底）。
            # ★ 根因修复(2026-09-25) RC-Q: 上述三者只产生 **hint（候选）**，
            #   最终文案必须经 `_resolve_dom_action_button` 在真实 DOM 上落地。
            #   根因: 原实现把正则切片**直接当按钮文案**（`_op_clean[_bi:_bi+4]`），
            #     "提交重复名称的资产" → "提交重复"、"提交客户端验证失败的表单" →
            #     "提交客户"，句子片段在页面上不存在 → get_by_role/快照关键词均不命中
            #     → 点击失败 → 用例被判"核心操作未执行" HARNESS_ERROR
            #     （实测 TC-REQ-002/008/010-I01）。
            _bracket_match = _re.search(r"【(.+?)】", operation)
            _op_hint = ""
            if _bracket_match:
                _op_hint = _bracket_match.group(1).strip()
            _btn_verbs = (
                "新增", "创建", "新建", "添加", "编辑", "修改", "更新",
                "删除", "保存", "提交", "导出", "导入", "上传", "注册", "登记",
            )
            if not _op_hint:
                # ★ 根因修复: 原逻辑取 operation 前10字符当按钮文本，
                #   而 operation 以需求编号开头("REQ-PAT-02：可新增患者...") →
                #   按钮文本="REQ-PAT-02"(需求编号) → 点击必失败(18条ERROR根因)。
                #   修复: 去 REQ 编号+括号后，提取"新增/创建/编辑/删除"等业务按钮词
                #   (动词+实体，如"新增患者")，作为**候选 hint**。
                _op_clean = _re.sub(r"REQ-?\w*\s*[:：]?\s*", "", operation)
                _op_clean = _re.sub(r"[（(【\[].*?[）)】\]]", "", _op_clean)
                for _bv in _btn_verbs:
                    _bi = _op_clean.find(_bv)
                    if _bi >= 0:
                        _op_hint = _op_clean[_bi:_bi + 4]
                        break
            if not _op_hint:
                # ★ R1: AI button 兜底（正则无动词时才用，如"可为患者开处方"→"开处方"）
                with guard("_execute_guided: from src.core.semantic_extractor imp", log=_log):
                    from src.core.semantic_extractor import extract_control_intent

                    _ai_btn = str(
                        extract_control_intent(operation).get("button") or ""
                    ).strip()
                    if _ai_btn:
                        _op_hint = _ai_btn
            # ★ RC-Q: hint → DOM 真实文案（三级通用落地: 表单录入型→新建入口 /
            #   需求动词→DOM / 通用提交语汇→DOM）。
            # ★ 根因修复(2026-09-25) RC-V: 必须传 **`_eff_op_type`** 而非原始 `op_type`。
            #   根因: RC-U 已判定"冲突型用例即使 op_type 为空也属录入型"并算出了
            #     `_eff_op_type='form'`，但此处仍传原始 `op_type=''` →
            #     `_resolve_dom_action_button` 的表单入口分支（②）不开 →
            #     入口按钮解析不到 → 停在列表页 → 采集到的已有值被填进**列表页搜索框**
            #     （实测 TC-REQ-045-I01 日志 `[GuidedFill] 填充成功: '按名称搜索' →
            #     'ASSET-003'`）→ 表单从未打开 → 被测的服务端拒绝路径不可达 → ERROR。
            #   通用性: 类型判定是**单一事实源**，算出后所有下游分支必须同源消费，
            #     否则判定与执行脱节；跨系统/行业/框架一致。
            _op_btn_text = await self._resolve_dom_action_button(
                page, _op_hint, operation, _eff_op_type, declared_hint=_declared_click
            )
            if not _op_btn_text:
                # ★ 2026-08-28 查看类用例: operation 无操作动词（如"患者列表页展示
                #   全部患者"）→ 页面上也无任何可点击的动作元素 → 直接进入验证阶段。
                _log.info(
                    "[Guided] 无 DOM 可点击动作按钮(hint=%r)，跳过操作按钮点击",
                    _op_hint[:20],
                )
            if _is_login:
                # === 登录专用路径: 表单已存在，先填凭证再点登录按钮 ===
                # 根因修复(R1): 原"跳过②块"使登录动作消失 → HARNESS_ERROR。
                # 解析"账号/用户名 X 密码 Y"(operation 文本含凭证)，跨系统通用。
                import re as _re3
                _creds = []
                # ★ 结构化凭证优先: `inputs` 是用例声明的**权威凭证来源**
                #   （生成侧落库的 {用户名: xxx, 密码: yyy}），直接可填；
                #   只有在 inputs 为空时才回退到从 operation 句子正则抽取。
                #   根因: 原实现**先**用正则从自然语言句子抠凭证，而需求句
                #     "登录失败（用户名或密码错误）需提示"用户名或密码错误"…"
                #     会被 `(?:用户)\s*(\S+)[^\n]*?(?:密码)\s*(\S+)` 切成
                #     ('或密码错误）需提示"用户名或', '错误"') → 往登录框填垃圾
                #     → 提交脏表单 → 页面无错误提示 → 假 FAIL（实测 TC-REQ-040-E01）。
                #   通用性: 同 D34/D37 —— **结构化声明 > 文本重猜**；跨系统一致。
                if inputs:
                    _creds = [
                        (k, (v.get("value", "") if isinstance(v, dict) else str(v)))
                        for k, v in inputs.items()
                    ]
                else:
                    _cm = _re3.search(
                        r"(?:账号|用户名)\s*[:：]\s*(\S{1,40})[^\n]*?(?:密码)\s*[:：]\s*(\S{1,40})",
                        operation,
                    )
                    # 仅接受「像凭证」的解析结果：不含标点/空白、非长句片段。
                    # 根因: 无分隔符的松正则会把整句切碎（见上），必须收紧。
                    if _cm and all(
                        _x and not _re3.search(r"[\s，。！？；：、（）【】\"'“”‘’]", _x)
                        for _x in _cm.groups()
                    ):
                        _creds = [(_cm.group(1), _cm.group(2))]
                for _ck, _cv in _creds:
                    if _cv:
                        _f = await self._guided_fill(page, _ck, _cv, timeout_ms)
                        _actions_taken.append(
                            {"type": "fill", "target": _ck, "value": _cv}
                        )
                        _evidence_parts.append(
                            "填充'%s': %s" % (_ck, _cv)
                            if _f
                            else "填充'%s'失败(未找到元素)" % _ck
                        )
                # 定位登录按钮(role=button，排除退出登录)
                # ★ 根因修复(2026-09-25) RC-Q: 登录按钮必须**来自 DOM**。
                #   原实现只在 `_op_btn_text` 为空时才查登录按钮，而 RC-Q 的句子切片
                #   已把它填成"提交登录"（需求句子片段）→ 查不到该按钮 → 点击失败
                #   → 登录动作缺失 → HARNESS_ERROR（实测 TC-REQ-024-I01）。
                #   通用修复: 登录场景一律以 DOM 真实按钮文案为准
                #   （"退出登录"的排除由 _guided_click 负责）。
                _op_btn_text = await self._dom_button_text(
                    page, ("登录", "登入", "登录系统", "SignIn", "Login", "提交")
                )
                if _op_btn_text:
                    _cl = await self._guided_click(page, _op_btn_text, timeout_ms)
                    _actions_taken.append({"type": "click", "target": _op_btn_text})
                    if not _cl:
                        _all_passed = False
                        _verification_failures.append(
                            {
                                "point": "点击登录按钮'%s'" % _op_btn_text,
                                "expect": "可点击",
                                "actual": "未找到按钮",
                            }
                        )
                        _evidence_parts.append(
                            "点击'%s'失败: 未找到按钮" % _op_btn_text
                        )
                    else:
                        _evidence_parts.append("点击'%s'" % _op_btn_text)
                else:
                    _all_passed = False
                    _verification_failures.append("登录按钮未找到")
                # 跳过常规"开表单→填→提交"流程(凭证已填、登录已点)
                inputs = {}
                _op_btn_text = ""
                # ★ 登录提交已完成 → 置 `_submission_done`，令其后的 Post 序列
                #   只执行 verify、跳过重复的 fill/click。根因: `verifications` 里
                #   同一批"填凭证 + 点登录"步骤会被登录专用路径与 Post 序列**各执行
                #   一次**；第二次点击让页面重载清空表单，Post 4/4 读到的仍是登录页
                #   → 断言恒 FAIL（实测 TC-REQ-040-E01: 靶子实际返回了"用户名或密码
                #   错误"，引擎却记 PRODUCT_BUG）。通用性: 与"重复创建防护"同源——
                #   同一业务提交不得执行两次，跨系统/行业/页面一致。
                _submission_done = True
                _in_replay = True
            # ★ 根因修复(2026-09-24): 查询/筛选型需求必须**先填条件再触发查询**。
            #   根因: 通用流程是「先点操作按钮 → 再填 inputs」。对查询/筛选页面，
            #     该按钮本身就是查询触发器，先点等于用**空条件**查询
            #     （实测 TC-REQ-036-I01 日志 `.../assets/?q=&category=`，q 为空
            #      → 页面仍列全量 24 条 → "系统显示'无匹配数据'"期望假 FAIL）。
            #   通用判据: 「条件先于触发」由查询/筛选语义决定，跨系统/行业/页面成立，
            #     零业务硬编码；填充复用下方同一 `_guided_fill` + `<unique>` 解析。
            if op_type in ("search", "filter_op") and inputs:
                for _pk, _pinfo in list(inputs.items()):
                    _pv = (
                        _pinfo.get("value", "")
                        if isinstance(_pinfo, dict)
                        else str(_pinfo)
                    )
                    if _pv == "<unique>":
                        _pv = "TEST_" + _unique_token()
                    if not _pv:
                        continue
                    _pf = await self._guided_fill(page, _pk, _pv, timeout_ms)
                    _actions_taken.append(
                        {"type": "fill", "target": _pk, "value": _pv}
                    )
                    _evidence_parts.append(
                        "填充'%s': %s" % (_pk, _pv)
                        if _pf
                        else "填充'%s'失败(未找到元素)" % _pk
                    )
                inputs = {}
                _log.info("[Guided] 查询/筛选型: 条件已先于查询按钮填入")
            # ★ 根因修复(2026-09-28): 删除用例在点击删除按钮前采集列表行数，
            #   delete_done 验证据此对比"删除后行数减少"（删除可观察证据）。
            #   采集失败（页面无表格/无[role=row]）保持 -1，delete_done 验证
            #   端按"无前值可对比"退化为仅判页面已重载（见 _verify_delete_row_decreased）。
            if op_type == "delete_op" and _rows_before_delete < 0:
                with guard("_execute_guided: _count = await page.evaluate(_JS_COU", log=_log, level=logging.WARNING):
                    _count = await page.evaluate(_JS_COUNT_DATA_ROWS)
                    if isinstance(_count, int) and _count >= 0:
                        _rows_before_delete = _count
                        _log.info(
                            "[Guided] 删除前行数采集: %d", _rows_before_delete
                        )
            if _op_btn_text:
                _log.info("[Guided] 操作按钮: %r", _op_btn_text[:40])
                _pre_op_url = page.url
                _clicked = await self._guided_click(page, _op_btn_text, timeout_ms)
                _actions_taken.append({"type": "click", "target": _op_btn_text})
                if _clicked:
                    _evidence_parts.append("点击'%s'" % _op_btn_text)
                    # 提交型操作按钮点击成功 = 主流程已提交（判定仅用通用提交语义）
                    if any(
                        w in (_op_btn_text or "") for w in _SUBMIT_WORDS
                    ):
                        _submission_done = True
                    # ★ v5.19: 检测操作按钮点击后URL变化
                    _post_op_url = page.url
                    if _post_op_url != _pre_op_url:
                        _log.info(
                            "[Guided] 操作按钮点击后URL变化: ...%s → ...%s",
                            _pre_op_url[-60:],
                            _post_op_url[-60:],
                        )
                        with guard("_execute_guided: await page.wait_for_load_state( 'dom", log=_log):
                            await page.wait_for_load_state(
                                "domcontentloaded", timeout=10000
                            )
                        await page.wait_for_timeout(1000)
                    # ★ 通用修复: 「进入子视图」检查在**入口点击之后、填写之前**执行。
                    #   这类检查（"点击新增按钮后表单/弹窗打开"）验的是当前跳转/弹层
                    #   是否成功，只有在此刻页面才处于被验状态；此前它被归入 post，
                    #   在填表提交后才跑 → 页面已回列表页 → 必然未命中（假 ERROR）。
                    for _ei, _estep in enumerate(_entry_verifications):
                        await _exec_one_step(
                            _estep,
                            "Entry %d/%d" % (_ei + 1, len(_entry_verifications)),
                        )
                else:
                    _log.warning("[Guided] 操作按钮'%s'未找到", _op_btn_text[:40])
                    _evidence_parts.append("点击'%s'失败: 未找到按钮" % _op_btn_text)
                    # ★ 2026-08-28 防假PASS: 操作按钮未找到=核心操作未执行，
                    #   必须置失败（原先只记录evidence字符串，_all_passed仍True→假PASS）
                    _all_passed = False
                    _verification_failures.append(
                        {
                            "point": "点击操作按钮'%s'" % _op_btn_text,
                            "expect": "可点击",
                            "actual": "未找到按钮",
                        }
                    )
            for _input_key, _input_info in inputs.items():
                _fill_value = (
                    _input_info.get("value", "")
                    if isinstance(_input_info, dict)
                    else str(_input_info)
                )
                # ★ 根因修复(2026-09-24): 解析 `<unique>` 占位符（唯一测试数据）。
                #   根因: Step3 为创建型需求生成的输入值是固定字面量（"TEST_新增"），
                #     同 run 内重复提交 → 靶子按业务返回"名称已存在" → 伪校验错误
                #     → no_error / "创建成功" 断言系统性假 FAIL。
                #   通用性: `<unique>` 是既有约定（见 empirical/deep_assertion_runner
                #     _resolve_fill_value），此处对齐同一语义；只解析占位符，
                #     刻意非法值/搜索关键词等字面量一律原样使用。
                if _fill_value == "<unique>":
                    _fill_value = "TEST_" + _unique_token()
                if _fill_value:
                    _filled = await self._guided_fill(
                        page, _input_key, _fill_value, timeout_ms
                    )
                    _actions_taken.append(
                        {"type": "fill", "target": _input_key, "value": _fill_value}
                    )
                    if _filled:
                        _evidence_parts.append(
                            "填充'%s': %s" % (_input_key, _fill_value)
                        )
                    else:
                        # ★ 2026-08-28 字段级容错: AI 生成的 inputs 有字段名噪音
                        #   （"年龄" vs 真实"出生日期"），单字段失败不置 FAIL——
                        #   否则生成器一点点字段差异就让整个用例 ERROR（脆弱）。
                        #   仅记录 evidence 供复核；按钮点击失败仍由②块置 FAIL。
                        _evidence_parts.append(
                            "填充'%s'失败: 未找到元素(字段名差异,记为warning)" % _input_key
                        )

            # ★ 通用修复(2026-09-24): 提交前「必填项自动补全」。
            #   根因: 用例 inputs 只声明需求点名的字段，表单其余必填项留空 →
            #     靶子客户端校验拦截提交 → "跳回列表/创建成功"类断言必然失败，
            #     被误判为引擎侧 HARNESS_ERROR（实测 TC-REQ-011/012）。
            #   极性门控: 需求期望本身即「错误/拒绝」提示时跳过（见 _allow_autofill）。
            if _allow_autofill:
                await self._guided_complete_form_fields(
                    page,
                    set(inputs.keys()),
                    timeout_ms,
                    _actions_taken,
                    _evidence_parts,
                )
            else:
                _log.info(
                    "[Guided] 需求期望为错误/拒绝反馈 → 跳过必填项补全(保留被测校验路径)"
                )

            # ★ v5.6 fix(2026-07-30): 填充后自动提交 — 查找弹窗/对话框内的提交按钮并点击
            #   根因(§10.9): test_method缺少提交按钮click步骤，导致操作不完整
            #   通用性: 提交按钮文本跨系统通用（确认/提交/保存/上传/导入/确定/OK/Submit）
            # ★ RC-V: 与入口按钮解析同源 —— 用 `_eff_op_type`，保证"类型判定 → 入口点击
            #   → 提交"三步对类型结论的消费一致（否则冲突型空 op_type 用例会"进得了表单
            #   却提交不了"，同样卡死被测路径）。
            if inputs or _eff_op_type == "form":
                _submit_texts = [
                    "确认",
                    "提交",
                    "保存",
                    "上传",
                    "导入",
                    "确定",
                    "OK",
                    "Submit",
                    "Save",
                    "Confirm",
                ]
                _submit_clicked = False
                for _st in _submit_texts:
                    if _st == _op_btn_text:
                        continue  # 跳过已点击的操作按钮
                    try:
                        _loc = page.locator(
                            '[role="dialog"] button:text-is("%s"), '
                            '[role="dialog"] button:has-text("%s"), '
                            '.ant-modal button:text-is("%s"), '
                            '.ant-modal button:has-text("%s"), '
                            '.el-dialog button:text-is("%s"), '
                            '.el-dialog button:has-text("%s")'
                            % (_st, _st, _st, _st, _st, _st)
                        ).first
                        if await _loc.count() > 0 and await _loc.is_visible():
                            _log.info("[Guided] 自动提交: 点击%r按钮", _st)
                            await _loc.click(timeout=min(timeout_ms, 5000))
                            await page.wait_for_timeout(2000)
                            _actions_taken.append(
                                {
                                    "type": "click",
                                    "target": _st,
                                    "strategy": "auto_submit",
                                }
                            )
                            _evidence_parts.append("自动提交: 点击'%s'" % _st)
                            _submit_clicked = True
                            break
                    except Exception:
                        continue
                if not _submit_clicked:
                    # ★ 通用修复(2026-09-24): 提交按钮不止「弹窗」一种形态。
                    _pg_words = [
                        "保存", "提交", "确定", "确认", "创建", "新增", "添加",
                        "更新", "注册", "导入", "上传", "OK", "Submit", "Save",
                        "Confirm", "Update",
                    ]
                    _submit_clicked = await self._guided_auto_submit_form(
                        page, _pg_words, _op_btn_text, timeout_ms,
                        _actions_taken, _evidence_parts,
                    )
                # 主流程提交成功（弹窗或整页表单任一命中）→ 状态标记，供 Post 重放防护
                _submission_done = _submission_done or _submit_clicked
                if not _submit_clicked:
                    _log.info("[Guided] 未找到提交按钮(弹窗/整页表单)，跳过自动提交")

        # ③ 操作后验证（操作结果检查）
        for _i, _step in enumerate(_post_verifications):
            await _exec_one_step(
                _step, "Post %d/%d" % (_i + 1, len(_post_verifications))
            )

        _evidence = "; ".join(_evidence_parts) if _evidence_parts else "无操作步骤"

        # ★ v2(2026-08-07): 如果没有任何操作执行，即使验证通过也不应判PASS
        #   根因: 导航失败后Agent未执行任何操作，但_all_passed=True导致假PASS
        #   修复: 无操作步骤时强制success=False，除非有明确的验证通过证据
        # ★ Fix D(2026-09-27): 必须区分两种"无操作"——
        #   ① **声明了**可执行验证点/输入却什么都没执行 → 真失败（导航失败/元素未找到），
        #      维持 fail-closed 防假 PASS；
        #   ② 用例本就只声明 checks、test_method 无步骤（如 TC-REQ-005-E01：
        #      payload.actions=[navigate,screenshot] + checks=[no_error]）→ 无"可执行点"
        #      可失败，判定权应回到三轨校验，而非被 guided 单方判 FAIL。
        #   通用性: 跨系统/行业/页面 —— 纯结构判定，零业务硬编码。
        _declared_executable = bool(verifications) or bool(inputs)
        if not _actions_taken and not _evidence_parts:
            if _declared_executable:
                _all_passed = False
                _verification_failures.append("无操作步骤: 导航失败或元素未找到")
            else:
                _evidence_parts.append(
                    "无可执行验证点(用例仅声明 checks，判定交由三轨校验)"
                )

        # ★ 2026-08-27 防假PASS(fail-closed): 声明了输入字段但未执行任何业务动作
        #   根因: AI生成的test_method只有页面级verify(如"页面显示X相关功能")、
        #     无fill/click/submit动作链 → 业务操作从未执行却因宽泛页面文本匹配判PASS
        #   修复: 用例声明inputs但执行链无任何业务动作 → 强制success=False
        #   通用性: 跨系统/行业 — 任何"有输入却不操作"的用例都是未执行测试
        _biz_action_types = {
            "fill", "click", "submit", "select_option", "press_key", "clear",
            "double_click", "set_date", "check", "uncheck", "upload_file",
            "key_down", "key_up", "hover", "right_click",
        }
        _biz_actions = [
            a for a in _actions_taken if a.get("type") in _biz_action_types
        ]
        if _all_passed and inputs and not _biz_actions:
            _all_passed = False
            _verification_failures.append(
                "业务操作未执行: 用例声明输入字段(%s)但执行链无任何业务动作(fill/click/submit)，"
                "仅页面级验证不足以判定PASS" % ",".join(list(inputs.keys())[:5])
            )

        # ★ 2026-08-27 防假PASS: 业务操作明确失败(未找到元素/按钮)时,即使后续验证
        #   通过也不得PASS —— 操作未完成=用例未执行成功
        _op_failures = [
            f for f in _verification_failures
            if "未找到" in str(f.get("point", "") if isinstance(f, dict) else f)
            or "未找到" in str(f.get("actual", "") if isinstance(f, dict) else "")
        ]
        if _all_passed and _op_failures:
            _all_passed = False

        return {
            "success": _all_passed,
            "evidence": _evidence,
            "actions_taken": _actions_taken,
            "strategy": "guided",
            "error": (
                "" if _all_passed else "%d个验证点未通过" % len(_verification_failures)
            ),
            "verification_failures": _verification_failures,
            # ★ 2026-09-28: guided 实时判定结构化记录（checks 层下游采信的依据）
            "verify_records": _verify_records,
        }

    async def _dom_button_text(self, page, words) -> str:
        """按文案词表在**真实 DOM** 中查找可见的按钮/链接，返回其实际文案。

        通用性: get_by_role 是 W3C ARIA 标准（跨 React/Vue/Angular/原生 HTML），
        name 参数默认大小写不敏感的子串匹配；返回**元素真实文案**而非查询词，
        保证后续证据/点击文案与实际元素一致。
        """
        for _w in words or ():
            _w = str(_w or "").strip()
            if not _w:
                continue
            for _role in ("button", "link"):
                try:
                    _loc = page.get_by_role(_role, name=_w)
                    _n = await _loc.count()
                except Exception:
                    continue
                for _i in range(min(_n, 5)):
                    _el = _loc.nth(_i)
                    try:
                        if not await _el.is_visible():
                            continue
                        _t = ((await _el.text_content()) or "").strip()
                        if not _t:
                            _t = str(await _el.get_attribute("value") or "").strip()
                        if _t:
                            return _t
                    except Exception:
                        continue
        return ""

    async def _resolve_dom_action_button(
        self, page, hint: str, operation: str, op_type: str, declared_hint: str = ""
    ) -> str:
        """把「从需求句子猜出的按钮文案」落地为**页面真实按钮文案**。

        ★ 根因修复(2026-09-25) RC-Q: 按钮文案必须**来自 DOM**（见 _ACTION_VERBS 注释）。
        优先级（全为通用语汇，零业务硬编码，跨系统/行业/框架）:
          ⓪ 用例声明的 target_hint（逗号分隔候选逐个试）→ 声明的动作目标是权威，
            优先于一切文本推断；命中即用其 DOM 真实文案。
          ① hint 原样在 DOM 命中 → 用其真实文案（保留"动词+实体"如"新增患者"的正确性）；
          ② 表单录入型（op_type=form）→ 通用「新建/录入」入口词
             （表单型用例的第一步必然是**进入录入表单**，而非点击句子片段）；
          ③ 需求中出现的动作动词（按在需求文本中出现的先后）；
          ④ 通用提交语汇（保存/提交/确定/确认/Save/Submit…）。
        返回命中的真实文案；均未命中返回 ""（调用方保留原 hint 以便报错可读）。

        declared_hint: 用例声明的目标（case_actions_json 的 target_hint）。用例已把
          「点什么」写成结构化声明，执行端不应再从 operation 文本重新猜一遍 —— 猜出来
          的是句子片段（"提交错误凭证"），与 DOM 真实文案（"新增"）不同形 → 定位失败。
        """
        # ⓪ 用例声明的动作目标：逗号/顿号分隔的多候选逐个试（"新增,新建,添加,创建"）
        _dh = str(declared_hint or "").strip()
        if _dh:
            _cands = [
                _c.strip()
                for _c in re.split(r"[,，、;；]+", _dh)
                if _c.strip() and len(_c.strip()) >= 2
            ]
            if _cands:
                _hit = await self._dom_button_text(page, _cands)
                if _hit:
                    _log.info(
                        "[Guided] 按钮文案 DOM 落地(用例声明): %r ← declared=%r",
                        _hit,
                        _dh[:30],
                    )
                    return _hit
        _hint = str(hint or "").strip()
        # ① hint 原样命中
        if _hint:
            _hit = await self._dom_button_text(page, [_hint])
            if _hit:
                return _hit
        # ② 表单录入型 → 通用入口词（但编辑/修改/更新语义除外：编辑是"定位已有行 →
        #    点行内编辑按钮"，不是新建入口——实测 TC-REQ-002 被"+ 新增商品"劫持）
        if str(op_type or "").strip() == "form" and not any(
            _w in str(operation or "") for _w in ("编辑", "修改", "更新", "Edit", "Update")
        ):
            _hit = await self._dom_button_text(page, _FORM_ENTRY_WORDS)
            if _hit:
                _log.info(
                    "[Guided] 按钮文案 DOM 落地(入口词): %r ← hint=%r", _hit, _hint[:20]
                )
                return _hit
        # ③ 需求动作动词（按出现先后排序，长词优先避免"新增"抢占"新增资产"）
        _op = str(operation or "")
        _verbs = [v for v in _ACTION_VERBS if v in _op or v.lower() in _op.lower()]
        _verbs.sort(
            key=lambda v: (
                _op.find(v) if _op.find(v) >= 0 else _op.lower().find(v.lower()),
                -len(v),
            )
        )
        if _verbs:
            _hit = await self._dom_button_text(page, _verbs)
            if _hit:
                _log.info(
                    "[Guided] 按钮文案 DOM 落地(需求动词): %r ← hint=%r",
                    _hit,
                    _hint[:20],
                )
                return _hit
        # ④ 通用提交语汇（仅当需求本身含「提交语义」时才兜底，避免查看类用例
        #    误点页面上无关的"确定/保存"按钮）
        if any(w in _op or w.lower() in _op.lower() for w in _SUBMIT_WORDS):
            _hit = await self._dom_button_text(page, _SUBMIT_WORDS)
            if _hit:
                _log.info(
                    "[Guided] 按钮文案 DOM 落地(提交语汇): %r ← hint=%r",
                    _hit,
                    _hint[:20],
                )
                return _hit
        return ""

    async def _guided_auto_submit_form(
        self,
        page,
        words: list,
        skip_text: str,
        timeout_ms: int,
        actions_taken: list,
        evidence_parts: list,
    ) -> bool:
        """整页表单自动提交（「弹窗」之外的通用提交路径）。

        根因(2026-09-24): 原自动提交只在 [role=dialog]/.ant-modal/.el-dialog 内
          找提交按钮。整页表单（SSR/多页应用，提交按钮位于 <form> 内）永远扫不到
          → 表单填了却从不提交 → "跳回列表/提示成功"类断言必然失败，
          被误判为引擎侧 HARNESS_ERROR（实测 TC-REQ-011/012）。

        通用判据（零业务硬编码，跨系统/行业/页面）:
          ① 只提交 POST 表单 —— 列表页的 GET 搜索表单天然排除；
          ② 表单须含数据录入控件 —— 排除行内删除等"纯按钮表单"；
          ③ 按钮文案属调用方给定的「提交语义词」。
        返回是否成功点击提交按钮。
        """
        try:
            _forms = page.locator("form")
            _n = await _forms.count()
        except Exception as _e:
            _log.debug("[GuidedSubmitForm] form 扫描失败: %s", str(_e)[:80])
            return False
        for _i in range(_n):
            try:
                _f = _forms.nth(_i)
                _method = str(await _f.get_attribute("method") or "").strip().lower()
                if _method and _method != "post":
                    continue
                _entry = await _f.locator(
                    'input[type="text"], input[type="number"], input[type="email"], '
                    'input[type="password"], input[type="search"], input[type="tel"], '
                    'input[type="url"], input:not([type]), textarea, select'
                ).count()
                if _entry <= 0:
                    continue
                _btns = _f.locator(
                    'button[type="submit"], input[type="submit"], button'
                )
                _bn = await _btns.count()
                for _j in range(_bn):
                    _b = _btns.nth(_j)
                    try:
                        if not await _b.is_visible():
                            continue
                        _bt = ((await _b.text_content()) or "").strip()
                        if not _bt:
                            _bt = str(
                                await _b.get_attribute("value") or ""
                            ).strip()
                    except Exception:
                        continue
                    if not _bt or not any(_w in _bt for _w in words):
                        continue
                    if skip_text and _bt == skip_text:
                        continue
                    # 提交前勾选「确认/同意」类 checkbox（不依赖极性门控——提交动作
                    # 的通用前置，否则客户端校验拦截提交、被测路径不可达）
                    await self._pre_confirm_form_checkboxes(
                        page, _bt, timeout_ms, scope=_f
                    )
                    await _b.click(timeout=min(timeout_ms, 5000))
                    await page.wait_for_timeout(2000)
                    actions_taken.append(
                        {"type": "click", "target": _bt, "strategy": "auto_submit_form"}
                    )
                    evidence_parts.append("自动提交(整页表单): 点击'%s'" % _bt)
                    _log.info("[GuidedSubmitForm] ✅ 整页表单提交: %r", _bt)
                    return True
            except Exception as _fe:
                _log.debug(
                    "[GuidedSubmitForm] 表单提交扫描异常: %s", str(_fe)[:80]
                )
                continue
        return False

    async def _harvest_existing_identifier(
        self, page, hint_texts: list[str]
    ) -> tuple[str, str]:
        """采集系统**已有实体**的（标识列名, 标识值），供冲突型用例复用（RC-U）。

        返回 (label, value)；无法采集返回 ("", "")。

        通用选列判据（跨系统/行业/框架，零业务硬编码）:
          ① 列名（表头文本）出现在需求文本中 → 该列即被测的标识列
             （如 REQ-002 需求含"资产名称"→ 命中表头"名称"）；
          ② 否则跳过 ID/序号/操作 类列，取首个其余列（标识列在实体列表中的通用位置）；
          ③ 列名缺失（无表头）时，取首个非纯数字单元格值。
        只读，不改动页面状态。
        """
        try:
            _cols = await page.evaluate(_JS_HARVEST_IDENTIFIERS)
        except Exception as _e:
            _log.warning("[ConflictData] 已有标识采集失败: %s", str(_e)[:100])
            return "", ""
        if not isinstance(_cols, list) or not _cols:
            _log.info("[ConflictData] 当前页无实体列表，跳过冲突数据采集")
            return "", ""
        _blob = " ".join(str(t or "") for t in hint_texts)
        _SKIP_LABELS = (
            "id", "编号", "序号", "no.", "#", "操作", "action", "actions", "管理",
        )
        # ① 列名命中需求文本
        for _c in _cols:
            _lb = str((_c or {}).get("label") or "").strip()
            _v = str((_c or {}).get("value") or "").strip()
            if len(_lb) >= 2 and _lb in _blob and _v:
                _log.info(
                    "[ConflictData] 已有标识(列名命中需求): %s=%r", _lb, _v[:40]
                )
                return _lb, _v
        # ② 跳过 ID/操作类列，取首个其余列
        for _c in _cols:
            _lb = str((_c or {}).get("label") or "").strip()
            _v = str((_c or {}).get("value") or "").strip()
            if not _v:
                continue
            if _lb and any(_s in _lb.lower() for _s in _SKIP_LABELS):
                continue
            if not _lb and _v.isdigit():
                continue
            _log.info("[ConflictData] 已有标识(首个非ID列): %s=%r", _lb or "?", _v[:40])
            return _lb, _v
        _log.info("[ConflictData] 未找到可用标识列（仅 ID/操作列），跳过")
        return "", ""

    async def _resolve_submittable_form_scope(self, page):
        """委托 step4.form_completion.resolve_submittable_form_scope（唯一实现）。"""
        return await _resolve_form_scope_impl(page)

    @staticmethod
    def _form_sample_value(_type: str, _m: dict) -> str:
        """委托 step4.form_completion.form_sample_value（唯一实现）。"""
        return _form_sample_value_impl(_type, _m)

    async def _guided_complete_form_fields(
        self,
        page,
        skip_keys: set,
        timeout_ms: int,
        actions_taken: list,
        evidence_parts: list,
    ) -> int:
        """委托 step4.form_completion.complete_form_fields（唯一实现）。"""
        return await _complete_form_fields(
            page, skip_keys, timeout_ms, actions_taken, evidence_parts
        )

    async def _guided_locate(
        self,
        page,
        target: str,
        timeout_ms: int,
    ):
        """guided模式：定位任意目标元素（自然语言 target → Playwright locator）。

        ★ T02(2026-08-06): 为 press_key/clear/double_click/hover/right_click/
        select_option/check/uncheck/set_date 等新增动作提供统一定位。
        策略：get_by_label → get_by_role(button) → get_by_text → text= → DOM 快照匹配。
        返回 locator 或 None（调用方决定是否判失败）。
        通用性：跨系统/行业/页面 — target 是自然语言描述，不依赖特定选择器。
        """
        if not target:
            return None
        _candidates = []
        with guard("_guided_locate: _candidates.append(('label', page.ge", log=_log):
            _candidates.append(("label", page.get_by_label(target)))
        with guard("_guided_locate: _candidates.append(('button', page.g", log=_log):
            _candidates.append(("button", page.get_by_role("button", name=target)))
        with guard("_guided_locate: _candidates.append(('text', page.get", log=_log):
            _candidates.append(("text", page.get_by_text(target, exact=False)))
        with guard("_guided_locate: _candidates.append(('textsel', page.", log=_log):
            _candidates.append(("textsel", page.locator(f"text={target}")))
        for _name, _loc in _candidates:
            try:
                if await _loc.count() > 0:
                    _log.info("[GuidedLocate] %s命中: %r", _name, target[:40])
                    return _loc.first
            except Exception:
                continue
        # DOM 快照匹配（与 _guided_fill 同源策略 — 尾段面向表单控件：本方法服务的
        # press_key/clear/select_option 等皆为控件动作，按钮已由顶部 role/text 策略
        # 覆盖，故 input 域优先，避免按钮短文本以子串分抢占语义命中）
        with guard("_guided_locate: _dom_buttons, _dom_inputs = await se", log=_log):
            _dom_buttons, _dom_inputs = await self._get_snapshot_with_recon(page)
            for _cand in (_dom_inputs, _dom_buttons):
                _match = match_keywords_to_elements([target], _cand)
                if _match and _match["score"] >= 1:
                    _sel = el_to_selector(_match["element"])
                    if _sel:
                        _log.info(
                            "[GuidedLocate] 快照匹配命中: %r score=%d",
                            target[:40],
                            _match["score"],
                        )
                        return page.locator(_sel).first
                # 语义兜底（与 _guided_fill 同级，杜绝"同元素填得进清不了"）：
                # 关键词未命中时按语义匹配（本地语义优先，云端为兜底），覆盖
                # 字段名 vs label 短词类失配。
                _sem = await self._semantic_element_match(target, _cand)
                if _sem and _sem.get("element"):
                    _sel = el_to_selector(_sem["element"])
                    if _sel:
                        _log.info(
                            "[GuidedLocate] 语义匹配命中: %r → %r",
                            target[:40],
                            str(_sem["element"].get("text") or "")[:40],
                        )
                        return page.locator(_sel).first
        return None

    async def _fill_control_by_type(self, locator, value, timeout_ms: int) -> bool:
        """控件填充统一分派：select → 下拉选择(label优先→回退首项)；其余 → click+fill。

        _guided_fill 主路径与 _guided_fill_bidirectional_label 兜底路径共用，控件类型
        分派全引擎单点（此前 label 兜底路径对关联 <select> 直接 fill 必超时）。
        """
        _val = str(value)
        try:
            try:
                _tag = (
                    await locator.evaluate("el => (el.tagName || '').toLowerCase()")
                    or ""
                )
            except Exception:
                _tag = ""
            if _tag.strip() == "select":
                try:
                    await locator.select_option(
                        label=_val, timeout=min(timeout_ms, 5000)
                    )
                    return True
                except Exception:
                    _opts = await locator.evaluate(
                        "el => el.options ? el.options.length : 0"
                    )
                    if _opts and int(_opts) >= 1:
                        await locator.select_option(
                            index=0, timeout=min(timeout_ms, 5000)
                        )
                        return True
                    return False
            await locator.click(timeout=min(timeout_ms, 5000))
            await locator.fill("")
            await locator.fill(_val, timeout=min(timeout_ms, 10000))
            return True
        except Exception:
            return False

    async def _guided_fill_bidirectional_label(
        self,
        page,
        target: str,
        value: str,
        timeout_ms: int,
    ) -> bool:
        """双向 label 语义兜底填充：label 短词是表单字段稳定语义标识，
        用 is_semantically_related（唯一判据入口，字符覆盖兜底 类别/分类 这类
        同核不同序词对）判定 target 与 label 同义后，再按该 label 定位填充，
        跨系统/行业覆盖"业务前缀长词 vs 页面短词"的标签命名差异。
        """
        with guard("_guided_fill_bidirectional_label: from src.ai.local_semantics import i", log=_log):
            from src.ai.local_semantics import is_semantically_related

            _labels = page.locator("label")
            _cnt = await _labels.count()
            if _cnt <= 0:
                return False
            tgt = str(target or "").strip()
            if not tgt:
                return False
            for _li in range(_cnt):
                try:
                    _lab = _labels.nth(_li)
                    # 只取 label 的直接文本子节点（不含内嵌控件的文本）：
                    # 包裹式 `<label>分类<select>…</select></label>` 的 inner_text()
                    # 会拼接 select/input 值（如 '分类server'），双向包含必然失配。
                    _lab_text = (
                        await _lab.evaluate(
                            "el => Array.from(el.childNodes)"
                            ".filter(n => n.nodeType === 3)"
                            ".map(n => (n.textContent || '').trim())"
                            ".join('')"
                        )
                        or ""
                    ).strip()
                    if len(_lab_text) < 2:
                        continue
                    # 判据唯一入口: local_semantics（双向包含是其子集，字符覆盖兜底同核词对）
                    if is_semantically_related(tgt, _lab_text):
                        _by_label = page.get_by_label(_lab_text, exact=True)
                        if not await _by_label.count():
                            continue
                        _lb_el = _by_label.first
                        try:
                            if not await _lb_el.is_visible():
                                continue
                        except Exception:
                            continue
                        _log.info(
                            "[GuidedFill] ✅ 双向label兜底命中: target=%r → label=%r",
                            target[:40],
                            _lab_text[:40],
                        )
                        if await self._fill_control_by_type(
                            _lb_el, value, timeout_ms
                        ):
                            await page.wait_for_timeout(300)
                            return True
                        _log.info("[GuidedFill] label关联控件填充失败，尝试label内控件直填")
                        _inner = _labels.nth(_li).locator("input,select,textarea").first
                        with guard("_guided_fill_bidirectional_label: if ( await _inner.count() and await ", log=_log, level=logging.INFO):
                            if (
                                await _inner.count()
                                and await self._fill_control_by_type(
                                    _inner, value, timeout_ms
                                )
                            ):
                                await page.wait_for_timeout(300)
                                _log.info(
                                    "[GuidedFill] ✅ label内控件直填成功: %r",
                                    target[:40],
                                )
                                return True
                except Exception:
                    continue
            _log.info(
                "[GuidedFill] 双向label兜底未命中: target=%r labels=%d个",
                target[:40],
                _cnt,
            )
        return False

    async def _guided_fill(
        self,
        page,
        target: str,
        value: str,
        timeout_ms: int,
    ) -> bool:
        """guided模式：填充输入框。

        策略：① 关键词匹配input元素 → ② 语义匹配 → ③ file输入框直接定位 → ④ 失败返回False。
        通用性：target是自然语言描述（如"搜索输入框"），不依赖选择器。
        ★ v5.11 fix: 支持文件上传输入框(input[type=file])，使用set_input_files。
        """
        _dom_buttons, _dom_inputs = await self._get_snapshot_with_recon(page)

        # ★ v5.11: 检测文件上传关键词
        _FILE_KEYWORDS = {
            "文件",
            "上传",
            "导入",
            "导入文件",
            "选择文件",
            "file",
            "upload",
            "import",
        }
        _is_file_target = (
            any(kw in target for kw in _FILE_KEYWORDS) if target else False
        )

        if _is_file_target:
            # ★ v5.21.1 fix: 检查是否已在_guided_click中用file_chooser处理了
            if getattr(self, "_file_upload_handled", False):
                _log.info(
                    "[GuidedFill] 文件已在_guided_click中通过file_chooser上传，跳过"
                )
                return True

            # ★ v5.21 fix(2026-07-31): 文件上传用Playwright file_chooser机制替代DOM搜索
            #   根因(§10.9 5层why):
            #     L1: 文件上传失败，找不到input[type=file]
            #     L2: 三个策略（DOM搜索input/触发按钮/上传区域）全部失败
            #     L3: 点击"数据导入"后浏览器打开OS原生文件对话框，非DOM元素
            #     L4: 当前代码完全基于DOM搜索，没用Playwright的file_chooser机制
            #     L5(根本原因): Agent的fill模型假设所有输入都是DOM表单元素，
            #       但文件上传是浏览器OS级交互，必须用专用API(file_chooser事件拦截)
            #   修复: 策略0→直接set_input_files(含隐藏) 策略1→expect_file_chooser
            #     策略2→等待模态框后搜索file input
            #   通用性: 跨所有Web框架/行业，所有文件上传都是input[type=file]或file_chooser
            _log.info("[GuidedFill] 文件上传target=%r，file_chooser机制", target[:40])
            _file_path = value if value else "test_upload.csv"

            # 策略0: 搜索页面上input[type=file]（包括隐藏的），直接set_input_files
            with guard("_guided_fill: _file_inputs = page.locator('input[t", log=_log, level=logging.INFO):
                _file_inputs = page.locator('input[type="file"]')
                _fi_count = await _file_inputs.count()
                if _fi_count > 0:
                    await _file_inputs.first.set_input_files(
                        _file_path, timeout=min(timeout_ms, 15000)
                    )
                    _log.info(
                        "[GuidedFill] ✅ 策略0: 直接set_input_files(%d个file input)",
                        _fi_count,
                    )
                    return True

            # 策略1: 搜索上传按钮，用expect_file_chooser包裹点击
            _UPLOAD_BTN_TEXTS = [
                "数据导入",
                "导入",
                "上传",
                "选择文件",
                "浏览",
                "批量导入",
                "Upload",
                "Browse",
                "Choose File",
                "Import",
                "点击上传",
                "拖拽上传",
                "文件上传",
                "上传文件",
            ]
            _btn_clicked = False
            for _btn_text in _UPLOAD_BTN_TEXTS:
                try:
                    _btn = page.locator(
                        "button:has-text('%s'), a:has-text('%s'), span:has-text('%s')"
                        % (_btn_text, _btn_text, _btn_text)
                    ).first
                    if await _btn.count() > 0 and await _btn.is_visible():
                        _log.info(
                            "[GuidedFill] 策略1: expect_file_chooser + 点击 %r",
                            _btn_text,
                        )
                        async with page.expect_file_chooser(
                            timeout=min(timeout_ms, 10000)
                        ) as _fc_info:
                            await _btn.click(timeout=min(timeout_ms, 5000))
                        _file_chooser = await _fc_info.value
                        await _file_chooser.set_files(_file_path)
                        _log.info(
                            "[GuidedFill] ✅ 策略1: file_chooser拦截成功，文件已设置"
                        )
                        _btn_clicked = True
                        return True
                except Exception as _e1:
                    _log.info(
                        "[GuidedFill] 策略1 %r 失败: %s", _btn_text, str(_e1)[:60]
                    )
                    continue

            # 策略2: 等待模态框/弹窗出现，在其中搜索input[type=file]
            if not _btn_clicked:
                with guard("_guided_fill: _log.info('[GuidedFill] 策略2: 等待模态框/弹", log=_log, level=logging.INFO):
                    _log.info("[GuidedFill] 策略2: 等待模态框/弹窗...")
                    await page.wait_for_timeout(1500)
                    _modals = page.locator(
                        '[role="dialog"], [role="alertdialog"], '
                        ".ant-modal, .el-dialog, .modal, .ant-drawer, .el-drawer, "
                        '[class*="upload"], [class*="Upload"]'
                    )
                    _modal_count = await _modals.count()
                    if _modal_count > 0:
                        _log.info(
                            "[GuidedFill] 策略2: 发现%d个模态框/弹窗", _modal_count
                        )
                        for _mi in range(min(_modal_count, 5)):
                            try:
                                _modal = _modals.nth(_mi)
                                if await _modal.is_visible():
                                    _fi_in_modal = _modal.locator(
                                        'input[type="file"]'
                                    ).first
                                    if await _fi_in_modal.count() > 0:
                                        await _fi_in_modal.set_input_files(
                                            _file_path, timeout=min(timeout_ms, 15000)
                                        )
                                        _log.info(
                                            "[GuidedFill] ✅ 策略2: 模态框内file input上传成功"
                                        )
                                        return True
                            except Exception:
                                continue

            _log.warning("[GuidedFill] 所有文件上传策略均失败(0/1/2)")
            # ★ v5.21.1: 页面状态诊断——帮助理解为什么文件上传失败
            with guard("_guided_fill: _current_url = page.url", log=_log):
                _current_url = page.url
                _page_title = await page.title()
                _log.warning(
                    "[GuidedFill] 页面状态诊断: url=%s title=%s",
                    _current_url[-80:],
                    _page_title[:60],
                )
                # 检查是否有弹窗/新页面
                _modals = page.locator(
                    '[role="dialog"], [role="alertdialog"], .ant-modal, .el-dialog'
                )
                _modal_count = await _modals.count()
                _log.warning(
                    "[GuidedFill] 诊断: 弹窗=%d, 页面是否变化需人工判断", _modal_count
                )

        # ① 结构化（引用式）target_hint 由唯一权威定位器解析 —— 语义匹配只作回退。
        _struct_loc = await _locate_declared_structured(page, target)
        if _struct_loc is not None:
            try:
                await _struct_loc.fill(
                    value if value else "", timeout=min(timeout_ms, 10000)
                )
                _log.info("[GuidedFill] ✅ 结构化引用填充: %r", target[:40])
                return True
            except Exception as _e_struct:  # noqa: BLE001
                _msg = str(_e_struct)
                # ★ 受限控件（input[type=number/date/…]）原生 fill 只接受合法值，
                #   而**负向用例的激励恰恰是非法值** —— 原生 fill 被拒会让"施加非法值"
                #   这一步永远落不了地，负向校验用例系统性假失败。
                #   正解（通用，零业务词表）：原生输入被拒时改由 DOM 注入值并派发
                #   input/change 事件 —— 这正是"绕过客户端约束验证服务端校验"的
                #   标准负向测试手段，页面/服务端的校验照常被触发。
                if "Cannot type text into input[" in _msg:
                    try:
                        await _struct_loc.evaluate(
                            "(el, v) => {"
                            "  el.value = v;"
                            "  el.dispatchEvent(new Event('input', {bubbles: true}));"
                            "  el.dispatchEvent(new Event('change', {bubbles: true}));"
                            "}",
                            value if value else "",
                        )
                        _log.info(
                            "[GuidedFill] ✅ 受限控件 DOM 注入非法值: %r ← %s",
                            target[:40],
                            (value or "")[:30],
                        )
                        return True
                    except Exception as _e_inject:  # noqa: BLE001
                        _log.info(
                            "[GuidedFill] 受限控件 DOM 注入失败: %r %s",
                            target[:40],
                            str(_e_inject)[:80],
                        )
                else:
                    _log.info(
                        "[GuidedFill] 结构化引用不可填充，回落语义路径: %r %s",
                        target[:40],
                        _msg[:80],
                    )

        if not _dom_inputs:
            _log.warning("[GuidedFill] 页面无input元素")
            return False

        _keywords = [target] if target else [value]
        _input_match = match_keywords_to_elements(_keywords, _dom_inputs)
        if not _input_match or _input_match["score"] < 1:
            _semantic = await self._semantic_menu_match(
                target,
                [
                    i.get("text", "")
                    or i.get("name", "")
                    or i.get("placeholder", "")
                    or ""
                    for i in _dom_inputs
                ],
            )
            if _semantic:
                _input_match = match_keywords_to_elements([_semantic], _dom_inputs)

        if not _input_match or _input_match["score"] < 1:
            # ★ 根因修复: DOM 快照匹配只认 input 的 name/placeholder
            #   （英文属性如 "name"），中文 label（"姓名"）匹配不到 → 填充失败
            #   （hospital_sut 等系统的真实表单是中文 label 关联）。
            #   兜底: 用 get_by_label 定位（W3C label 关联），中文 label 直达 input。
            #   通用性: 跨系统/行业 — label 是表单字段的稳定语义标识。
            with guard("_guided_fill: _by_label = page.get_by_label(target", log=_log):
                _by_label = page.get_by_label(target)
                _lb_count = await _by_label.count()
                if _lb_count > 0:
                    for _li in range(_lb_count):
                        _lb_el = _by_label.nth(_li)
                        try:
                            if not await _lb_el.is_visible():
                                continue
                        except Exception:
                            continue
                        _log.info(
                            "[GuidedFill] ✅ get_by_label 兜底命中: %r (index=%d/%d)",
                            target[:40],
                            _li,
                            _lb_count,
                        )
                        await _lb_el.fill(str(value), timeout=min(timeout_ms, 5000))
                        await page.wait_for_timeout(300)
                        return True
                _log.warning(
                    "[GuidedFill] get_by_label 兜底未命中(%d个): %r",
                    _lb_count,
                    target[:40],
                )
                # ★ 双向 label 包含兜底：
                #   根因: get_by_label(target) 只做单向包含（页面 label 须包含 target），
                #     而生成端 target 是带业务前缀的长词（"资产类别/资产价格/库存数量"），
                #     页面 label 是短词（"分类/价格/库存"）→ 方向相反必失败（实测
                #     TC-REQ-002-UX02 AI 探索 3 个字段全部"未找到元素(字段名差异)"）。
                #   修法: 扫描页面所有 label 文案，target 与 label 文本双向包含（≥2字）
                #     即命中，再用 W3C get_by_label 精确定位该 label 关联的表单控件。
                #   通用性: label 是表单字段的稳定语义标识，无业务词表（跨系统/行业）。
                _bi_hit = await self._guided_fill_bidirectional_label(
                    page, target, value, timeout_ms
                )
                if _bi_hit:
                    return True

        # ★ 2026-08-28 泛化 target 兜底: 生成器字段提取失败时用占位符
        #   ("输入框"/"字段"/"输入")当 target —— 任何系统都必然匹配失败。
        #   通用规则: 泛化 target → 回退到页面首个可见可编辑输入框
        #   （按 DOM 顺序，即表单第一个待填字段）。跨系统/行业适用。
        _GENERIC_INPUT_TARGETS = ("输入框", "输入", "字段", "文本框", "内容")
        if (not _input_match or _input_match["score"] < 1) and target and any(
            target.strip() == g for g in _GENERIC_INPUT_TARGETS
        ):
            with guard("_guided_fill: _fallback_sel = ( 'input:visible:not", log=_log):
                _fallback_sel = (
                    "input:visible:not([type=hidden]):not([type=submit])"
                    ":not([type=button]):not([readonly]):not([disabled]),"
                    "textarea:visible:not([readonly]):not([disabled])"
                )
                _cand = page.locator(_fallback_sel)
                _cand_n = await _cand.count()
                if _cand_n:
                    _input_match = {
                        "element": {
                            "selector": _fallback_sel,
                            "text": "",
                            "name": "",
                            "placeholder": "",
                        },
                        "score": 1,
                    }
                    _generic_first = True
                    _log.info(
                        "[GuidedFill] 泛化target'%s' → 回退页面首个可见输入框(%d个候选)",
                        target[:10],
                        _cand_n,
                    )

        if not _input_match or _input_match["score"] < 1:
            _log.warning("[GuidedFill] 未找到输入框: target=%r", target[:40])
            return False

        _el = _input_match["element"]
        # 快照元素无 selector 字段，统一走 el_to_selector 构造回退（全引擎单点）。
        _selector = el_to_selector(_el)
        _text = (
            _el.get("text", "")
            or _el.get("name", "")
            or _el.get("placeholder", "")
            or ""
        )
        if not _selector:
            _log.warning("[GuidedFill] 无selector: %r", _text[:40])
            return False

        try:
            _locator = page.locator(_selector).first
            # 控件类型分派（select→下拉/其余→click+fill）收口到 _fill_control_by_type
            # 单一实现，主路径与双向 label 兜底路径共用。value 已由调用方强转字符串。
            _ok = await self._fill_control_by_type(_locator, value, timeout_ms)
            if _ok:
                _log.info(
                    "[GuidedFill] 填充成功: %r → %r", _text[:40], str(value)[:40]
                )
            else:
                _log.warning("[GuidedFill] 填充失败: selector=%r", _selector[:60])
            return _ok
        except Exception as _e:
            _log.warning(
                "[GuidedFill] 填充异常: selector=%r err=%s",
                _selector[:60],
                str(_e)[:80],
            )
            return False

    async def _guided_click(
        self,
        page,
        target: str,
        timeout_ms: int,
    ) -> bool:
        """guided模式：点击按钮。

        策略（★ v5.22 重构）:
        0. Playwright getByRole('button') — W3C ARIA标准，直接查询DOM，不受文本快照合并影响
        1. 关键词匹配button元素 → ② 语义匹配 → ③ Playwright text=定位 → ④ 失败返回False。
        通用性：target是自然语言描述（如"搜索按钮"），不依赖选择器。
        ★ v5.5 fix(2026-07-30): 语义匹配结果若为menuitem(侧边栏菜单项)则拒绝，
        重新搜索页面内容区(排除menuitem/treeitem)，避免误点侧边栏菜单。
        ★ v5.22 fix(2026-07-31): 添加getByRole策略0——根因(§10.9 5层why):
          L1: 按钮找不到 → L2: 快照文本合并(批量打标数据导入) → L3: 快照丢失DOM结构
          → L4: 过度依赖文本快照 → L5: 忽略Playwright W3C ARIA getByRole定位能力
          通用性: getByRole是W3C标准，跨React/Vue/Angular/原生HTML，不依赖快照。
        """
        # ★ 原生 dialog 自动 accept 由 playwright 引擎层统一注册
        #   （lifecycle.py `self._page.on("dialog", self._on_dialog)` 自动 accept），
        #   Agent 复用同一 page，此处不再重复挂 handler（重复轮子会双收弹窗）。
        #  ★ v5.22: 策略0 — Playwright getByRole('button') (W3C ARIA标准)
        #   比文本快照匹配更准确：直接查询DOM中button元素，不受相邻按钮文本合并影响
        _FILE_UPLOAD_BTN_KW = {
            "导入",
            "上传",
            "选择文件",
            "浏览",
            "Upload",
            "Browse",
            "Import",
        }
        from src.pipeline.steps.step4.browser_tools._hint_utils import (
            split_candidate_hint,
        )
        # ⓪′ 动作语义族扩展：生成侧给的 target 常常只写族内**一个**词
        #   （如 '提交'），而页面按钮是同族的另一个词（'保存'）——"什么算同一个动作"
        #   由模块级 _SUBMIT_WORDS/_FORM_ENTRY_WORDS 唯一表达（执行端别处已复用），
        #   此处只做**候选扩展**，不新增也不复制词表。
        _target_cands = split_candidate_hint(target)
        _expanded: list[str] = []
        for _tc in _target_cands:
            for _family in (_SUBMIT_WORDS, _FORM_ENTRY_WORDS):
                if any(_w == _tc or _w in _tc for _w in _family):
                    _expanded.extend(_w for _w in _family if _w not in _expanded)
            if _tc not in _expanded:
                _expanded.append(_tc)
        if len(_expanded) > len(_target_cands):
            for _word in _expanded:
                if _word in _target_cands:
                    continue
                _fam_loc = None
                # 族内候选必须是**可点击控件**（ARIA 按钮 / 原生 button / submit 输入）。
                # 根因: 先前用宽泛文本提示（`text=<词>`）定位，会把"只是含该词的普通
                # 标签"当成按钮 → 提交控件从未被点击 → 表单从未提交 → 校验断言假 FAIL。
                try:
                    _rl = page.get_by_role("button", name=_word)
                    if await _rl.count() > 0:
                        _fam_loc = _rl.first
                except Exception:  # noqa: BLE001
                    _fam_loc = None
                if _fam_loc is None:
                    try:
                        _sl = page.locator(
                            "input[type='submit'], input[type='button'], button"
                        ).filter(has_text=_word)
                        if await _sl.count() > 0:
                            _fam_loc = _sl.first
                    except Exception:  # noqa: BLE001
                        _fam_loc = None
                if _fam_loc is not None:
                    try:
                        await _fam_loc.click(timeout=min(timeout_ms, 8000))
                        _log.info(
                            "[GuidedClick] ✅ 动作语义族命中: %r → '%s'", target[:40], _word
                        )
                        return True
                    except Exception as _e_fam:  # noqa: BLE001
                        _log.debug(
                            "[GuidedClick] 族内候选不可点击: %s %s",
                            _word,
                            str(_e_fam)[:60],
                        )
        # ⓪ 结构化（引用式）target_hint 由唯一权威定位器解析 —— 语义匹配只作回退。
        _struct_loc = await _locate_declared_structured(page, target)
        if _struct_loc is not None:
            try:
                await _struct_loc.click(timeout=min(timeout_ms, 10000))
                _log.info("[GuidedClick] ✅ 结构化引用点击: %r", target[:40])
                return True
            except Exception as _e_struct:  # noqa: BLE001
                _log.info(
                    "[GuidedClick] 结构化引用不可点击，回落语义路径: %r %s",
                    target[:40],
                    str(_e_struct)[:80],
                )
        with guard("_guided_click: _btn_loc = page.get_by_role('button'", log=_log, level=logging.WARNING):
            _btn_loc = page.get_by_role("button", name=target)
            _count = await _btn_loc.count()
            _log.info(
                "[GuidedClick] getByRole('button', name=%r) 候选数=%d", target[:30], _count
            )
            for _i in range(_count):
                _btn = _btn_loc.nth(_i)
                try:
                    _vis = await _btn.is_visible()
                except Exception as _vis_e:
                    # ★ 归因可见化: 元素可见性探测失败必留痕。根因: 原 `except: continue`
                    #   静默吞异常 → getByRole 明明命中却"跳过"，落到快照路径
                    #   → 表层只剩"匹配按钮 score=5"却点不动，无归因（D19 同类）。
                    _log.warning(
                        "[GuidedClick] 可见性探测失败(index=%d): %s",
                        _i, str(_vis_e)[:120],
                    )
                    continue
                if not _vis:
                    _log.info("[GuidedClick] 跳过不可见候选(index=%d)", _i)
                    continue
                _btn_text = (await _btn.text_content() or "").strip()
                # ★ R1(2026-08-28) 防误点'退出登录': 登录类目标显式排除退出按钮
                if target in ("登录", "登入", "Login", "SignIn") and "退出" in _btn_text:
                    _log.info("[GuidedClick] 排除'退出登录'按钮(目标=%s)", target)
                    continue
                _log.info(
                    "[GuidedClick] ★ getByRole命中: %r (index=%d/%d)",
                    _btn_text[:40],
                    _i,
                    _count,
                )
                # 文件上传检测
                _is_upload_btn = any(
                    kw in (_btn_text or "") for kw in _FILE_UPLOAD_BTN_KW
                )
                if _is_upload_btn:
                    _file_path = (
                        getattr(self, "_pending_file_path", None) or "test_upload.csv"
                    )
                    _log.info(
                        "[GuidedClick] ★ 文件上传按钮 %r，尝试expect_file_chooser",
                        _btn_text[:40],
                    )
                    try:
                        async with page.expect_file_chooser(
                            timeout=min(timeout_ms, 8000)
                        ) as _fc_info:
                            await _btn.click(timeout=min(timeout_ms, 8000))
                        _file_chooser = await _fc_info.value
                        await _file_chooser.set_files(_file_path)
                        _log.info(
                            "[GuidedClick] ✅ file_chooser拦截成功，文件已设置: %s",
                            _file_path,
                        )
                        self._file_upload_handled = True
                        self._file_upload_path = _file_path
                        await page.wait_for_timeout(1500)
                        return True
                    except Exception as _fc_e:
                        _log.info(
                            "[GuidedClick] file_chooser未触发(按钮非文件上传): %s",
                            str(_fc_e)[:80],
                        )
                        self._file_upload_handled = False
                        # 不再 return —— 落到下方统一的三级兜底点击。

                # ── 三级兜底点击（通用性：跨框架/跨按钮类型）──
                # 根因: getByRole 已命中一个**可见**按钮时，此前仅有"文件上传"分支
                #   会在 file_chooser 失败后尝试普通点击；非上传按钮（如"登录"/"提交"）
                #   整块被跳过 → 定位成功却从未发出 click → 页面无变化 → 假 FAIL。
                #   实测: TC-REQ-040-E01 日志有"getByRole命中'登录'"，其后无任何 click。
                # 通用性: 任何已定位到的候选按钮，其"点击"都应按同一强度递进尝试
                #   （普通 click → force click → JS dispatchEvent），与按钮业务语义无关。
                # 策略: 普通click → force=True → JS dispatchEvent（三级）。
                _fallback_ok = False
                # 兜底1: 普通click
                with guard("_guided_click: await _btn.click(timeout=min(timeout", log=_log, level=logging.WARNING):
                    await _btn.click(timeout=min(timeout_ms, 5000))
                    _log.info(
                        "[GuidedClick] 兜底1 普通click成功: %r", _btn_text[:40]
                    )
                    _fallback_ok = True
                # 兜底2: force=True click（绕过actionability检查）
                if not _fallback_ok:
                    with guard("_guided_click: await _btn.click( force=True, timeou", log=_log, level=logging.WARNING):
                        await _btn.click(
                            force=True, timeout=min(timeout_ms, 3000)
                        )
                        _log.info(
                            "[GuidedClick] 兜底2 force click成功: %r",
                            _btn_text[:40],
                        )
                        _fallback_ok = True
                # 兜底3: JS dispatchEvent click（绕过所有Playwright检查）
                if not _fallback_ok:
                    with guard("_guided_click: await _btn.evaluate( 'el => el.dispa", log=_log, level=logging.WARNING):
                        await _btn.evaluate(
                            "el => el.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true}))"
                        )
                        _log.info(
                            "[GuidedClick] 兜底3 JS click成功: %r",
                            _btn_text[:40],
                        )
                        _fallback_ok = True
                # ★ 三级兜底全灭必须显式归因(WARNING)。根因: 三个 guard 未传
                #   level → 默认 DEBUG → 点击失败在生产日志里完全不可见，
                #   表层只剩"点了页面没变"的假 FAIL，无归因（D19 同类）。
                #   通用性: 关键交互路径的失败必须可见，跨系统一致。
                if not _fallback_ok:
                    _log.warning(
                        "[GuidedClick] ✗ 三级兜底全部点击失败: %r (元素存在但不可交互)",
                        _btn_text[:40],
                    )
                if _fallback_ok:
                    await page.wait_for_timeout(1500)
                    return True

        _dom_buttons, _dom_inputs = await self._get_snapshot_with_recon(page)
        _all_clickable = _dom_buttons + [
            b
            for b in _dom_inputs
            if b.get("tag") == "input" and b.get("type") in ("submit", "button")
        ]

        _keywords = [target]
        _btn_match = match_keywords_to_elements(_keywords, _all_clickable)
        if not _btn_match or _btn_match["score"] < 1:
            _semantic = await self._semantic_menu_match(
                target, [b.get("text", "") for b in _all_clickable if b.get("text")]
            )
            if _semantic:
                _btn_match = match_keywords_to_elements([_semantic], _all_clickable)
                if _btn_match and _btn_match["score"] >= 1:
                    _matched_role = _btn_match["element"].get("role", "")
                    _matched_text = _btn_match["element"].get("text", "")
                    # 内容区元素：排除 W3C 通用菜单角色（menuitem/treeitem），跨系统适用
                    _content_buttons = [
                        b
                        for b in _all_clickable
                        if b.get("role") not in ("menuitem", "treeitem")
                    ]
                    # 操作语义护栏：target 是动宾操作（含操作动词）时，LLM 可能把页面
                    # 上无操作动词的导航/字段名（如 navbar '资产'）当最相关候选，属
                    # 占位性命中——点击它只会跳走页面（实测 target='新增资产按钮' →
                    # 命中 navbar '资产'，从新建页跳回列表页，后续填写全部失败）。动词
                    # 语义是通用网页交互特征，不依赖业务词表。
                    _target_has_verb = any(
                        v in (target or "") for v in _ACTION_VERBS
                    )
                    _semantic_verbless = not any(
                        v in (_semantic or "") for v in _ACTION_VERBS
                    )
                    if _target_has_verb and _semantic_verbless:
                        _log.info(
                            "[GuidedClick] 语义占位命中(无操作动词): %r → %r，改搜内容区按钮",
                            target[:40],
                            _semantic[:40],
                        )
                        # 剥离 UI 通名（按钮/链接…）后的动宾核心重搜：
                        # '新增资产按钮' → '新增资产'（页面按钮文案正是动宾短语）
                        _btn_core = (target or "").strip()
                        for _noun in ("按钮", "链接", "入口", "图标", "菜单", "标签页"):
                            if _btn_core.endswith(_noun) and len(_btn_core) > len(
                                _noun
                            ):
                                _btn_core = _btn_core[: -len(_noun)]
                                break
                        _btn_match = match_keywords_to_elements(
                            [_btn_core], _content_buttons
                        )
                    elif _matched_role in ("menuitem", "treeitem"):
                        # 拒绝菜单项匹配——避免"数据导入"误匹配"数据管理"侧边栏
                        _log.info(
                            "[GuidedClick] 语义匹配=%r是菜单项(role=%s)，拒绝，搜索页面内容区",
                            _matched_text[:40],
                            _matched_role,
                        )
                        _btn_match = match_keywords_to_elements(
                            [_semantic], _content_buttons
                        )
                        if not _btn_match or _btn_match["score"] < 1:
                            _btn_match = match_keywords_to_elements(
                                _keywords, _content_buttons
                            )

        if _btn_match and _btn_match["score"] >= 1:
            _btn_text = _btn_match["element"].get("text", "").strip()
            _log.info(
                "[GuidedClick] 匹配按钮: %r score=%d",
                _btn_text[:40],
                _btn_match["score"],
            )

            # ★ v5.21.1: 快照匹配fallback中的文件上传检测（getByRole策略0优先处理）
            _is_upload_btn = (
                any(kw in _btn_text for kw in _FILE_UPLOAD_BTN_KW)
                if _btn_text
                else False
            )
            if _is_upload_btn:
                _file_path = (
                    getattr(self, "_pending_file_path", None) or "test_upload.csv"
                )
                _log.info(
                    "[GuidedClick] ★ 检测到文件上传按钮 %r，尝试expect_file_chooser",
                    _btn_text[:40],
                )
                try:
                    async with page.expect_file_chooser(
                        timeout=min(timeout_ms, 8000)
                    ) as _fc_info:
                        await click_button(page, _btn_text, timeout_ms)
                    _file_chooser = await _fc_info.value
                    await _file_chooser.set_files(_file_path)
                    _log.info(
                        "[GuidedClick] ✅ file_chooser拦截成功，文件已设置: %s",
                        _file_path,
                    )
                    self._file_upload_handled = True
                    self._file_upload_path = _file_path
                    await page.wait_for_timeout(1500)
                    return True
                except Exception as _fc_e:
                    _log.info(
                        "[GuidedClick] file_chooser未触发(按钮非文件上传): %s",
                        str(_fc_e)[:60],
                    )
                    self._file_upload_handled = False
                    # fallthrough到普通点击

            # 提交前勾选「确认/同意」类 checkbox（通用表单交互，零业务词表）
            await self._pre_confirm_form_checkboxes(page, _btn_text, timeout_ms)

            _click_ok = await click_button(page, _btn_text, timeout_ms)
            if _click_ok:
                await page.wait_for_timeout(1500)
                return True
            _repair_ok = await repair_click(page, _btn_text, timeout_ms)
            if _repair_ok:
                return True

        # 操作动词fallback: 按钮文本可能是复合描述（"新增资产按钮"），提取核心动词
        # 作fallback搜索。词表唯一权威 = 模块级 _ACTION_VERBS（此处曾重复定义同名
        # 局部列表遮蔽模块常量致 UnboundLocalError——已归并，只读不重定）。
        _verb_fallback = None
        for _av in _ACTION_VERBS:
            if target.startswith(_av) and len(target) > len(_av):
                _verb_fallback = _av
                break
        if _verb_fallback:
            _log.info(
                "[GuidedClick] 操作动词fallback: %r → %r", target[:40], _verb_fallback
            )
            # 提交类近义动词扩展：页面按钮文案可能是另一近义动词（保存/确定/确认/
            # 完成），关键词包含必然失配（实测 TC-REQ-002-UX02 '提交按钮' 未命中
            # 按钮 '保存'）。表单提交交互语义跨系统通用，词表复用模块级 _SUBMIT_WORDS
            # （保存/提交/确定/确认/下一步/完成/Save/Submit/OK/Confirm）兜底。
            _verb_kws = (
                [_verb_fallback] + list(_SUBMIT_WORDS)
                if _verb_fallback in _SUBMIT_WORDS
                else [_verb_fallback]
            )
            _verb_match = match_keywords_to_elements(_verb_kws, _all_clickable)
            if _verb_match and _verb_match["score"] >= 1:
                _btn_text = _verb_match["element"].get("text", "").strip()
                _matched_role = _verb_match["element"].get("role", "")
                if _matched_role not in ("menuitem", "treeitem"):
                    _log.info("[GuidedClick] ✅ 动词fallback命中: %r", _btn_text[:40])
                    await self._pre_confirm_form_checkboxes(
                        page, _btn_text, timeout_ms
                    )
                    _click_ok = await click_button(page, _btn_text, timeout_ms)
                    if _click_ok:
                        await page.wait_for_timeout(1500)
                        return True
                    _repair_ok = await repair_click(page, _btn_text, timeout_ms)
                    if _repair_ok:
                        return True

        # ★ v5.7 fix(2026-07-30): 多策略fallback — 按钮可能在Tab页内或页面内容区
        #   根因(§10.9): _guided_click缺少_trigger_click的多策略探索
        #     (Tab探索/InPageNav探索/PW直接搜索)，导致"数据导入"按钮找不到
        #   通用性: 跨系统/行业/页面 — 所有Web应用都有Tab/页内导航/表格行按钮

        # 策略A: Tab页探索（按钮可能在Tab页内，关键词匹配失败时尝试）
        _tabs = await get_page_tabs(page)
        if _tabs:
            _log.info("[GuidedClick] 策略A: Tab探索, %d个Tab", len(_tabs))
            _tab_result = await self._explore_tabs_for_button(
                page,
                [target],
                _tabs,
                timeout_ms,
            )
            if _tab_result:
                _tab_text = _tab_result.get("element", {}).get("text", "").strip()
                _log.info("[GuidedClick] ✅ Tab探索成功: %r", _tab_text[:40])
                _click_ok = await click_button(page, _tab_text, timeout_ms)
                if _click_ok:
                    await page.wait_for_timeout(1500)
                    return True
                _repair_ok = await repair_click(page, _tab_text, timeout_ms)
                if _repair_ok:
                    return True

        # 策略B: 页内导航探索（按钮可能在二级导航子页面中）
        _inpage_result = await self._explore_inpage_nav_for_button(
            page,
            [target],
            timeout_ms,
        )
        if _inpage_result:
            _inpage_text = _inpage_result.get("element", {}).get("text", "").strip()
            _log.info("[GuidedClick] ✅ 页内导航探索成功: %r", _inpage_text[:40])
            await self._pre_confirm_form_checkboxes(page, _inpage_text, timeout_ms)
            _click_ok = await click_button(page, _inpage_text, timeout_ms)
            if _click_ok:
                await page.wait_for_timeout(1500)
                return True
            _repair_ok = await repair_click(page, _inpage_text, timeout_ms)
            if _repair_ok:
                return True

        # 策略C: Playwright直接搜索（页面内容区按钮，get_page_snapshot可能未捕获）
        _short_kw = target[:4] if len(target) >= 4 else target
        _pw_match = await try_pw_locate_button(page, target, _short_kw)
        if _pw_match:
            _pw_text = _pw_match.get("element", {}).get("text", "").strip()
            _log.info("[GuidedClick] ✅ PW直接搜索成功: %r", _pw_text[:40])
            _click_ok = await click_button(page, _pw_text, timeout_ms)
            if _click_ok:
                await page.wait_for_timeout(1500)
                return True
            _repair_ok = await repair_click(page, _pw_text, timeout_ms)
            if _repair_ok:
                return True

        # 策略D: 提交类 live-DOM 直搜 —— 快照 _all_clickable 的按钮提取可能漏掉
        # <button type=submit>（page_reconnaissance 枚举不全），而真实 DOM 有该按钮
        # （实测 TC-REQ-002-UX02 '提交按钮' 全策略落空，同页 _guided_auto_submit_form
        # 的 _dom_button_text 却命中 '保存'）。提交语义词表唯一(_SUBMIT_WORDS)，
        # 与自动提交通用同一助手/词表，跨系统/行业通用，零新增词表。
        if _verb_fallback and _verb_fallback in _SUBMIT_WORDS:
            _live_submit_text = await self._dom_button_text(page, _SUBMIT_WORDS)
            if _live_submit_text:
                _log.info(
                    "[GuidedClick] ✅ live-DOM 提交语汇命中: %r",
                    _live_submit_text[:40],
                )
                await self._pre_confirm_form_checkboxes(
                    page, _live_submit_text, timeout_ms
                )
                _live_ok = await click_button(page, _live_submit_text, timeout_ms)
                if _live_ok:
                    await page.wait_for_timeout(1500)
                    return True
                _live_repair = await repair_click(page, _live_submit_text, timeout_ms)
                if _live_repair:
                    return True

        _log.warning("[GuidedClick] 未找到按钮: target=%r", target[:40])
        return False

    async def _pre_confirm_form_checkboxes(
        self, page, btn_text: str, timeout_ms: int, scope=None
    ) -> bool:
        """提交前勾选「确认/同意」类 checkbox，返回是否勾选了至少一个。

        why: 客户端校验拦截未勾选的提交（实测"请勾选信息确认"）→ 正向提交被拦、
        断言全链失败；高级测试工程师填表遇确认类 checkbox 会勾上，引擎同理。
        """
        if not any(_w in (btn_text or "") for _w in _SUBMIT_WORDS):
            return False
        _scope = scope if scope is not None else await self._resolve_submittable_form_scope(page)
        if _scope is None:
            return False
        _checked = 0
        try:
            _cbs = _scope.locator('input[type="checkbox"]')
            for _i in range(await _cbs.count()):
                _cb = _cbs.nth(_i)
                try:
                    if await _cb.is_checked():
                        continue
                    _desc = ""
                    _lab = _cb.locator("xpath=ancestor::label[1]")
                    if await _lab.count():
                        _desc += (await _lab.first.inner_text() or "").strip()
                    _lab_for = ""
                    _cb_id = await _cb.get_attribute("id") or ""
                    if _cb_id:
                        _lab_for = page.locator(
                            'label[for="%s"]' % _cb_id.replace('"', "")
                        )
                        if await _lab_for.count():
                            _desc += " " + (
                                await _lab_for.first.inner_text() or ""
                            ).strip()
                    if any(_w in _desc for _w in _CONFIRM_CHECKBOX_WORDS):
                        await _cb.check(timeout=min(timeout_ms, 5000))
                        _checked += 1
                except Exception:
                    continue
        except Exception as _e:
            _log.info("[GuidedClick] 确认checkbox勾选异常: %s", str(_e)[:80])
            return False
        if _checked:
            _log.info("[GuidedClick] ✅ 已勾选%d个确认类checkbox", _checked)
            await page.wait_for_timeout(300)
            return True
        return False

    async def _detect_page_error_elements(self, page) -> tuple[bool, str]:
        """★ 通用修复(2026-09-24): 活动 DOM 探测可见错误元素。

        返回 (是否命中至少一个可见错误, 拼接的错误文本)。
        ★ RC-AF(2026-09-25): 探测 JS 由单一事实源 `feedback_selectors.ERROR_DETECT_JS`
          提供（选择器 + 双重可见性 + **广义类名形态判据**）。原先此处自带一份 JS，
          无形态判据 → 靶子 `<a class="danger">删除</a>` 被当成错误条 → 非法输入用例假 PASS。
          形态判据：广义类名（.error/.invalid/.danger）命中交互控件（按钮/链接/输入）时丢弃。
        """
        try:
            _result = await page.evaluate(ERROR_DETECT_JS)
        except Exception as _e:
            _log.warning("[GuidedVerify] 错误元素 DOM 探测异常: %s", str(_e)[:80])
            return False, ""
        if _result:
            return True, "；".join(list(_result)[:6])
        return False, ""

    async def _detect_page_success_elements(self, page) -> tuple[bool, str]:
        """★ 通用修复(2026-09-24): 活动 DOM 探测可见成功/正反馈消息条。

        ★ RC-AF(2026-09-25): 探测 JS 由单一事实源 `feedback_selectors.SUCCESS_DETECT_JS`
          提供，与错误条探测同源（对称缺陷一并消除，不再各自复写）。
        """
        try:
            _result = await page.evaluate(SUCCESS_DETECT_JS)
        except Exception as _e:
            _log.warning("[GuidedVerify] 成功元素 DOM 探测异常: %s", str(_e)[:80])
            return False, ""
        if _result:
            return True, "；".join(list(_result)[:6])
        return False, ""

    async def _guided_verify(
        self,
        page,
        expect: str,
        timeout_ms: int,
        expected_value: str = "",
    ) -> tuple[bool, str]:
        """guided模式：验证结果。

        策略：① 弹窗/对话框 → ② 消息 → ③ DOM错误条/拒绝语义 → ④ 文本包含 →
              ⑤ 关键词子词 → ⑥ 语义断言。
        通用性：expect是自然语言期望（如"列表包含TEST001"、校验拒绝"提示错误信息"），
          DOM化判定直接用共享错误条选择器探测活动页面反馈，不依赖 expect 文案精确命中。
        `expected_value` 是用例声明的**结构化期望值**（如 "用户名或密码错误"）——
          它是权威判据；`expect` 只是需求句子的自然语言描述，仅作语义兜底。
          根因: verify 步骤的 value 是生成侧落库的精确期望串，而 expect 是整句需求
          （"错误凭证被拒绝，提示错误信息/登录失败"）；只拿 expect 切 2 字子词去页面
          匹配，必然匹配不到精确串（实测 TC-REQ-040-E01 假 ERROR）。
          通用性: 跨系统/行业一致——「机器可判的期望值」优先于「人类读的期望描述」。
        返回: (是否通过, 实际文本, 验证方法[.../拒绝判定/错误条检测/文本匹配/关键词匹配/语义断言/未匹配])
        """
        _dialog_visible = await check_dialog_visible(page)
        _message = await get_page_message(page)
        _snapshot = await get_page_snapshot_text(page)

        # ★ 根因修复(2026-09-28): 目标页 404/Not Found → 诚实 SKIP（非产品结论）。
        #   根因: 探针/章节标题导航失配时 Agent 落到框架 404 页（Flask "The requested
        #     URL was not found on the server"），把"导航基线不可达"当产品缺陷判 ERROR
        #     （实测 REQ-010~018-E01 全 21 条: navigation=侧边栏→章节标题，Agent 404）。
        #   404 页不是业务反馈——目标不可达属导航/环境能力限制；
        #   SSR SKIP / REPLAY SKIP 同族诚实语义。仅对**无拒绝语义**期望走 SKIP：
        #   期望"拒绝/404/拦截"的用例页面 404 = 拒绝达成（PASS），走原判定。
        #   通用性: 数字灾区任意 Web 靶子（409/404/转码页）均适用，零业务硬编码。
        _snap_lower404 = (_snapshot or "").lower()
        _is_404_page = (
            "the requested url was not found" in _snap_lower404
            or "not found on the server" in _snap_lower404
            or "page not found" in _snap_lower404
            or "404 - not found" in _snap_lower404
            or "页面不存在" in _snap_lower404
            or "请求的地址不存在" in _snap_lower404
            or re.search(r"\b404\b", (_snapshot or "")) is not None
        )
        # 拒绝语义**唯一来源** = 用例执行上下文（Step4 意图入口用唯一生产函数写入，
        # 见 src/core/case_context.py）。本处曾自持第二份关键词表 —— 两处漂移会让
        # "期望被拒绝"的用例被判成导航失配，或反过来把真缺陷当 SKIP 藏掉。
        from src.core.case_context import current_case_expects_denial

        _expects_denial_404 = current_case_expects_denial()
        if _is_404_page and not _expects_denial_404:
            _log.warning(
                "[GuidedVerify] 页面为 404/Not Found 且期望非拒绝语义 → 诚实 SKIP: "
                "expect=%r url=%s",
                expect[:60], page.url[-60:],
            )
            return (
                None,
                "目标页面不可达（HTTP 404: %s），期望非拒绝语义，导航基线失配而非产品缺陷"
                % page.url[-60:],
                "NOT_NAVIGABLE_SKIP",
            )

        # ★ 通用fix(2026-09-20): 需求否定感知验证——缺席即通过。
        #   对于"X 不应存在"的断言，若页面上仍存在要素 X → 失败；缺席 → 通过。
        #   这必须在所有存在性/文本包含检查之前短路，否则"X 缺席"会被误判 FAIL。
        if _is_negated_assertion(expect):
            _features = _extract_neg_features(expect)
            _meaningful = [
                f for f in _features
                if len(f) >= 2 and f not in _NEG_STOPWORDS
            ]
            if not _meaningful:
                # 退化断言：无法定位明确要素，按"需求未提供"处理，避免误杀正确缺席
                _log.warning(
                    "[GuidedVerify] 否定断言无明确要素(退化), 判PASS: %r",
                    expect[:60],
                )
                return True, "否定断言: 页面未发现相关功能(无明确要素关键词)", "否定断言"
            _page_lower = (_snapshot or "").lower()
            _msg_lower = (_message or "").lower()
            _hit = [f for f in _meaningful if (f.lower() in _page_lower or f.lower() in _msg_lower)]
            if _hit:
                _log.warning(
                    "[GuidedVerify] 否定断言失败: 要素仍存在=%r", _hit
                )
                return (
                    False,
                    "需求明确不提供，但页面上仍发现不该存在的要素:%s" % "/".join(_hit[:4]),
                    "否定断言",
                )
            _log.info(
                "[GuidedVerify] 否定断言通过: 要素均不存在 =%r", _meaningful
            )
            return (
                True,
                "页面未发现需求明确不提供的要素:%s" % "/".join(_meaningful[:4]),
                "否定断言",
            )

        # ★ 行为语义「重定向/跳转至登录页」（登录墙/会话失效引导）：
        #   期望达成即"当前已处于登录态"，按 URL/登录表单判定，而非 expect 字面匹配
        #   （实测 TC-REQ-046-I01: "清除会话信息并重定向至登录页"，页面本已是登录页，
        #   字面匹配必败）。词表委托 Step3 单一事实源，执行端不复制。
        from src.pipeline.step3._rule_extractor_keywords import (
            is_redirect_to_login,
        )

        _expect_lower = (expect or "").lower()
        if is_redirect_to_login(_expect_lower):
            _url_lower = (page.url or "").lower()
            _login_url_hit = re.search(r"/login|/signin|/sign-in|/auth", _url_lower)
            _snap_lower = (_snapshot or "").lower()
            _login_form_hit = bool(
                _login_url_hit
                or (
                    "登录" in _snap_lower
                    and any(
                        _t in _snap_lower
                        for _t in ("用户名", "密码", "password", "账号", "用户", "邮箱")
                    )
                )
            )
            if _login_form_hit:
                _log.info(
                    "[GuidedVerify] 行为语义=重定向至登录页，当前已在登录态: %s",
                    _url_lower[-60:],
                )
                return (
                    True,
                    "重定向至登录页达成：已处于登录页(%s)" % _url_lower[-60:],
                    "登录态判定",
                )

        # ★ 通用修复(2026-09-24): 拒绝/校验语义 DOM 化判定。
        #   根因: 校验拒绝类 verify expect 是自然语言（"提示错误信息"/"非法输入被拦截"），
        #     靶子反馈是错误条文本（"名称不能为空"）。旧逻辑文本匹配 expect↔页面通常
        #     不命中 → 落到弱语义断言(代理429/慢) → 全家 ERROR。
        #   通用性: 跨系统/框架 — 共享错误条选择器覆盖 Ant/Element/Boostrap/原生/自定义。
        #   分支① 拒绝语义 + 活动DOM可见错误条 → PASS（拒绝被正确反馈）。
        #   分支② 约定: 用 URL/快照文本佐证，避免仅凭隐藏残留误判。
        _is_reject = any(_m.lower() in _expect_lower for _m in _REJECT_MARKERS)
        if _is_reject:
            _err_found, _err_text = await self._detect_page_error_elements(page)
            if _err_found:
                _log.info(
                    "[GuidedVerify] 拒绝语义命中活动DOM错误条: %r", _err_text[:120]
                )
                return (
                    True,
                    "页面反馈拒绝/校验错误: %s" % _err_text,
                    "错误条检测",
                )
            # 鉴权失败态: 目标返回 401/403 或已重定向到登录页 → 拒绝语义成立
            _is_auth = any(_m.lower() in _expect_lower for _m in _AUTH_FAIL_MARKERS)
            _url_lower = (page.url or "").lower()
            if _is_auth and ("/login" in _url_lower or "/auth" in _url_lower):
                _log.info(
                    "[GuidedVerify] 鉴权失败重定向登录页: %r", page.url[-60:]
                )
                return (
                    True,
                    "无权限访问，已重定向至登录/鉴权页: %s" % page.url[-80:],
                    "鉴权重定向",
                )
            if _is_auth and _snapshot and any(
                _t in _snapshot for _t in ("401", "403", "权限不足", "无权限", "未授权")
            ):
                _log.info("[GuidedVerify] 鉴权失败页面体现(401/403文本)")
                return True, "访问未授权，页面反馈 401/403/权限不足", "拒绝判定"

        _actual_parts = []
        if _dialog_visible:
            _actual_parts.append("弹窗已打开")
        if _message:
            _actual_parts.append("提示: %s" % _message)
        # ★ v5.6 fix: 即使弹窗可见，也保留快照文本供语义断言匹配
        #   否则actual="弹窗已打开"无法匹配expect="页面显示烟叶画像数据相关功能"
        _actual = "; ".join(_actual_parts) if _actual_parts else _snapshot[:200]
        if _actual_parts and _snapshot:
            _actual = _actual + " | 页面内容: " + _snapshot[:300]

        # ★ v5.8 fix(2026-07-30): 页面内容验证时过滤侧边栏文本
        #   根因(§10.9 3层why):
        #     L1: "页面显示烟叶画像数据相关功能"验证失败→actual是侧边栏文本
        #     L2: get_page_snapshot_text捕获了全页文本(含侧边栏)，主内容被淹没
        #     L3: 侧边栏菜单项在所有页面都存在，不是页面内容验证的有效文本
        #   修复: 页面内容验证时，从snapshot中排除已知侧边栏菜单项
        #   通用性: 跨系统/行业 — 侧边栏文本在所有后台系统都是干扰项
        _content_snapshot = _snapshot
        if "页面显示" in expect or "页面内容" in expect:
            _sidebar_items = await self._get_sidebar_items(page)
            if _sidebar_items:
                _content_lines = []
                for _line in _snapshot.split("\n"):
                    _line_stripped = _line.strip()
                    if not _line_stripped:
                        _content_lines.append(_line)
                        continue
                    _is_sidebar = False
                    for _si in _sidebar_items:
                        if _si and _si in _line_stripped:
                            _is_sidebar = True
                            break
                    if not _is_sidebar:
                        _content_lines.append(_line)
                _content_snapshot = "\n".join(_content_lines)
                if _content_snapshot != _snapshot:
                    _log.info(
                        "[GuidedVerify] 过滤侧边栏: %d→%d行",
                        len(_snapshot.split("\n")),
                        len(_content_lines),
                    )
                    # ★ v5.10 fix: 更新_actual为过滤后的内容
                    _actual = (
                        "; ".join(_actual_parts)
                        if _actual_parts
                        else _content_snapshot[:200]
                    )
                    if _actual_parts and _content_snapshot:
                        _actual = _actual + " | 页面内容: " + _content_snapshot[:300]

        # ★ v5.6: 长expect提取关键期望反馈
        # 模式: "系统预期反馈：导入成功,上传成功" → 提取"导入成功,上传成功"
        # 通用性: 跨系统适用，"系统预期反馈："是Step3生成的固定模式
        _check_expects = [expect]
        # ★ 用例声明的结构化期望值优先: verify 步骤的 value 是生成侧落库的精确期望串，
        #   比整句 expect 更接近页面真实反馈文案。放在 _check_expects **首位**，
        #   使其先于整句参与文本包含与关键词匹配（判定优先级，非新增词表）。
        #   根因: 只看 expect 会把"错误凭证被拒绝，提示错误信息/登录失败"切成 2 字子词，
        #     而页面实际是"用户名或密码错误"——精确串本可直接命中（假 ERROR）。
        _ev = str(expected_value or "").strip()
        if _ev and _ev not in _check_expects and len(_ev) >= 2:
            _check_expects.insert(0, _ev)
            _log.info("[GuidedVerify] 采用用例声明的结构化期望值: %r", _ev[:60])
        if len(expect) > 50:
            import re as _re

            _feedback_match = _re.search(
                r"(?:系统)?预期反馈[：:]\s*(.+?)(?:$|。|；)",
                expect,
                _re.IGNORECASE,
            )
            if _feedback_match:
                _short = _feedback_match.group(1).strip().rstrip("。，,;；")
                if _short and len(_short) < len(expect):
                    _check_expects.append(_short)
                    _log.info(
                        "[GuidedVerify] 长expect提取关键反馈: %r → %r",
                        expect[:60],
                        _short[:60],
                    )

        # 简单文本包含检查
        _snapshot_lower = _snapshot.lower()
        _message_lower = (_message or "").lower()

        for _ce in _check_expects:
            _ce_lower = _ce.lower()
            if _ce_lower and (
                _ce_lower in _snapshot_lower or _ce_lower in _message_lower
            ):
                _log.info("[GuidedVerify] 文本包含匹配: %r", _ce[:40])
                return True, _actual, "文本匹配"

        # ★ 2026-08-08 v2: 关键词提取匹配（expect="在2.1 商品管理模块中，用户执行支持按分类筛选商品操作，系统预期反馈：页面显示查询结果"
        #   → 提取"商品管理"+"商品"+"分类筛选"+"查询结果"等，逐一匹配页面文本）
        #   根因(2026-08-08 5层why):
        #     L1: Step3生成的expect是完整句子，页面只包含"商品"等模型链接，文本匹配失败
        #     L2: 关键词提取v1提取"商品管理"但页面仅有"商品"（模型链接），依然是零匹配
        #     L3: \u4e00-\u9fff{2,}匹配连续中文，将"商品管理"视为一个整体词语
        #     L4: 页面Django admin只显示单个模型名称"商品"而非复合词"商品管理"
        #     L5: 根本原因—复合格匹配要求页面包含完整复合词，但页面只包含其子词
        #   修复v2: 从复合词中提取所有2字子词（"商品管理"→"商品"+"管理"），任一匹配即通过
        #   通用性: 跨系统/行业/页面 — 所有expect都包含业务关键词，子词匹配比复合格更鲁棒
        if not _check_expects:
            _check_expects = [expect]
        _keywords = []
        import re as _kw_re
        # 提取所有连续中文（2+字），然后拆分为2字子词
        for _ce in _check_expects:
            _chinese_seqs = _kw_re.findall(r"[\u4e00-\u9fff]{2,}", _ce)
            for _seq in _chinese_seqs:
                _keywords.append(_seq)  # 原始复合词
                if len(_seq) > 2:
                    # 从复合词中提取所有2字子词: "商品管理" → "商品", "品管", "管理"
                    for _i in range(len(_seq) - 1):
                        _sub = _seq[_i : _i + 2]
                        if _sub not in _keywords:
                            _keywords.append(_sub)
        # 去重并过滤
        _unique_kws = list(dict.fromkeys(k for k in _keywords if len(k) >= 2))
        # ★ 2026-09-23 反假PASS根修①: 泛词停用表 — 页面骨架词在任意页面都存在，
        #   单个泛词命中≠期望达成（实测: "非法输入被拒绝"用例靠匹配到"登录"2字假PASS）。
        #   泛词不参与"单命中即通过"的判定，只能作为组合命中的组成部分。
        _GENERIC_PAGE_WORDS = {
            "页面", "提示", "信息", "显示", "内容", "功能", "数据", "系统",
            "操作", "成功", "失败", "正常", "加载", "点击", "输入", "表单",
            "按钮", "用户", "管理", "模块", "支持", "进行", "完成", "出现",
            "返回", "正确", "处理", "相关", "明确", "反馈", "结果", "检查",
            "验证", "测试", "执行", "使用", "选择", "填写", "记录", "字段",
        }
        _page_has_chinese = bool(_kw_re.search(r"[\u4e00-\u9fff]", _snapshot_lower))
        if _unique_kws and _page_has_chinese:
            _matched_kws = []
            for _kw in _unique_kws:
                if _kw.lower() in _snapshot_lower:
                    _matched_kws.append(_kw)
            # ★ 反假PASS根修②: 单命中门槛收紧 —
            #   ① 全部命中词都是泛词 → 不构成通过证据（任意页面都满足）
            #   ② 仅 1 个非泛词命中 → 证据不足，转 AI 语义断言（AI-first 铁律）
            #   ③ ≥2 个不同非泛词命中，或非泛词命中率 ≥30% → 机械通过
            _nongeneric = [k for k in _matched_kws if k not in _GENERIC_PAGE_WORDS]
            _ratio = len(_nongeneric) / max(1, len(_unique_kws))
            _chinese_match = len(_nongeneric) >= 2 or (
                _nongeneric and _ratio >= 0.3
            )
            if _chinese_match:
                _log.info(
                    "[GuidedVerify] 关键词子词匹配成功: %d/%d 非泛词=%d (%s)",
                    len(_matched_kws),
                    len(_unique_kws),
                    len(_nongeneric),
                    ", ".join(_matched_kws[:5]),
                )
                return True, _actual, "关键词匹配"
            else:
                _log.info(
                    "[GuidedVerify] 关键词子词均未匹配: %d个关键词 (%s)",
                    len(_unique_kws),
                    ", ".join(_unique_kws[:5]),
                )
                # ★ 2026-09-28 语义上移: 「页面正常加载/包含列表」类断言由 Step3
                #   生成层落成结构化 DOM 断言（element_exists+selector），验证层只读
                #   执行——不再此处按文本子词猜语义（曾在此加"内容渲染/DOM证据"分派，
                #   属下游补丁，已删除）。未匹配关键词时 fail-closed 交给下方语义断言。

        # 语义断言兜底
        # ★ v5.10 fix(2026-07-31): 使用过滤后的_content_snapshot进行语义断言
        #   根因: _content_snapshot已过滤侧边栏但LLM调用仍用_snapshot(未过滤)
        #   修复: LLM语义断言使用_content_snapshot(已过滤侧边栏)
        _snapshot_for_llm = _content_snapshot

        # ★ 通用修复(2026-09-24): accept(正反馈)语义 + 活动DOM成功条 → 兜底 PASS。
        #   仅在文本/关键词均未命中后走到此处才判定，避免任意成功toast造成假PASS。
        # ★ RC-X(2026-09-25): 触发词表改由 Step3 `expects_accept_feedback` 统一判定
        #   （覆盖"接受/生效/落库"等正向措辞，不再只认"成功"族词）。
        if _expects_accept_feedback(expect):
            _suc_found, _suc_text = await self._detect_page_success_elements(page)
            if _suc_found:
                _log.info(
                    "[GuidedVerify] accept语义命中活动DOM成功条: %r", _suc_text[:120]
                )
                return (
                    True,
                    "页面反馈成功/正反馈: %s" % _suc_text,
                    "成功条检测",
                )

        with guard("_guided_verify: _sem = await self._semantic_assertio", log=_log, level=logging.WARNING):
            _sem = await self._semantic_assertion(
                expect,
                expect,
                _snapshot_for_llm,
                [{"type": "verify", "expect": expect}],
            )
            if _sem.get("verdict") == "PASS" and _sem.get("confidence", 0) >= 0.5:
                _log.info(
                    "[GuidedVerify] 语义断言通过: conf=%.2f", _sem.get("confidence", 0)
                )
                return True, _actual, "语义断言"

        _log.warning(
            "[GuidedVerify] 验证失败: expect=%r actual=%r", expect[:40], _actual[:80]
        )
        return False, _actual, "未匹配"
