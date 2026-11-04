# -*- coding: utf-8 -*-
"""提交前「表单必填项自动补全」——执行层通用能力（唯一实现）。

why: 用例动作只录入需求点名的字段（实测 TC-REQ-017-E01 只填"名称"），表单其余
  必填项（价格/库存/确认勾选）留空 → 靶子校验拦截提交 → "创建成功/列表出现唯一值"
  类断言结构性不成立（一轮 8 条 E01 假 FAIL）。补齐必填项是高级测试工程师的常规
  操作，属执行层能力，不属于某条执行路径；故单点实现，结构化路径与 Agent 引导
  路径共用，避免同一语义两处各写一套。
"""

from __future__ import annotations

import itertools
import logging
import re
import time

from src.core.guarded import guard

_log = logging.getLogger(__name__)

# 可提交表单作用域：优先可见弹窗，其次 POST(或未声明 method) 且含录入控件与提交按钮
# 的 <form>。列表页 GET 搜索表单、行内删除等纯按钮表单天然排除。
_DIALOG_SCOPE_SEL: str = (
    '[role="dialog"], [role="alertdialog"], .ant-modal, .el-dialog, '
    '.modal, .ant-drawer, .el-drawer'
)
# 录入控件（排除 hidden/submit/button/reset/image/file —— 它们不是可填字段）
_ENTRY_CONTROL_SEL: str = (
    "input:not([type=hidden]):not([type=submit]):not([type=button])"
    ":not([type=reset]):not([type=image]):not([type=file]), textarea, select"
)
# 「确认/同意」类复选框语义词（必须勾选否则提交被拦截的通用形态）
_CONFIRM_CHECKBOX_WORDS: tuple = (
    "确认", "同意", "我已", "已阅读", "已知悉", "承诺", "接受条款",
    "agree", "confirm", "accept", "i have read", "terms",
)
# 下拉占位项语义词（值非空但语义为"未选择"，不应被当作有效取值）
_PLACEHOLDER_OPTION_WORDS: tuple = (
    "请选择", "全部", "所有", "不限", "无", "select", "choose", "all", "--", "-1",
)

# 表单控件元数据采集（一次 evaluate，索引与 _ENTRY_CONTROL_SEL 对齐）
_JS_FORM_CONTROL_META: str = """
(scope) => {
    const SKIP_TYPES = ['hidden', 'submit', 'button', 'reset', 'image', 'file'];
    const norm = (s) => (s || '').replace(/\\s+/g, ' ').trim();
    const out = [];
    const els = scope.querySelectorAll('input, select, textarea');
    els.forEach((el) => {
        const tag = el.tagName.toLowerCase();
        const type = norm(el.getAttribute('type') || (tag === 'input' ? 'text' : tag)).toLowerCase();
        if (tag === 'input' && SKIP_TYPES.indexOf(type) >= 0) return;
        let label = '';
        try {
            if (el.labels && el.labels.length) {
                label = Array.from(el.labels).map((l) => l.textContent || '').join(' ');
            }
        } catch (e) {}
        if (!label) {
            let p = el.parentElement, d = 0;
            while (p && d < 3 && !label) { label = norm(p.textContent); p = p.parentElement; d++; }
        }
        const cs = window.getComputedStyle(el);
        out.push({
            i: out.length,
            tag: tag,
            type: type,
            name: norm(el.getAttribute('name')),
            id: el.id || '',
            ph: norm(el.getAttribute('placeholder')),
            aria: norm(el.getAttribute('aria-label')),
            tid: norm(el.getAttribute('data-testid')),
            label: norm(label).slice(0, 80),
            value: el.value == null ? '' : String(el.value),
            checked: !!el.checked,
            required: el.hasAttribute('required') || el.getAttribute('aria-required') === 'true',
            disabled: !!el.disabled,
            readonly: el.readOnly,
            maxlength: el.getAttribute('maxlength') || '',
            min: el.getAttribute('min') || '',
            max: el.getAttribute('max') || '',
            step: el.getAttribute('step') || '',
            visible: (el.offsetParent !== null || el.getClientRects().length > 0)
                     && cs.display !== 'none' && cs.visibility !== 'hidden',
            options: tag === 'select'
                ? Array.from(el.options).map((o) => ({v: o.value, t: norm(o.textContent)}))
                : []
        });
    });
    return out;
}
"""

# 测试数据唯一令牌：时间基(ms) + 进程内自增 → base36；跨用例、跨 run 单调唯一。
_UNIQUE_SEQ = itertools.count(1)
_UNIQUE_ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyz"


def today_iso() -> str:
    """当前本地日期 YYYY-MM-DD（date/month/week 类控件补全用）。"""
    import datetime as _dt

    return _dt.date.today().isoformat()


def unique_token() -> str:
    """返回短且唯一的 base36 令牌（≥7 字符，单调递增）。"""
    _v = int(time.time() * 1000) * 1000 + next(_UNIQUE_SEQ)
    _out = ""
    while _v:
        _v, _r = divmod(_v, 36)
        _out = _UNIQUE_ALPHABET[_r] + _out
    return _out or "0"


def form_sample_value(_type: str, meta: dict) -> str:
    """按控件类型生成合法样例值（通用，零业务硬编码）。

    文本类样例值必须跨用例/跨 run 唯一：固定常量会撞靶子唯一约束，令服务端正确
    拒绝重复键，从而把"创建成功"类断言变成系统性假 FAIL。受 maxlength 约束时截取
    令牌**低位**（低位是进程内自增所在位，变化最快，任何长度下都保持唯一）。
    """
    _tok = unique_token()
    _t = (_type or "text").lower()
    if _t in ("number", "range"):
        try:
            _min = (
                float(meta.get("min")) if str(meta.get("min") or "").strip() else None
            )
        except Exception:
            _min = None
        try:
            _max = (
                float(meta.get("max")) if str(meta.get("max") or "").strip() else None
            )
        except Exception:
            _max = None
        _v = _min if (_min is not None and _min > 0) else 1.0
        if _max is not None and _v > _max:
            _v = _max
        return str(int(_v)) if float(_v).is_integer() else ("%.2f" % _v)
    if _t == "email":
        return "tester_%s@example.com" % _tok
    if _t == "tel":
        _digits = "".join(c for c in _tok if c.isdigit()).ljust(8, "0")[:8]
        return "138" + _digits
    if _t == "url":
        return "https://example.com/%s" % _tok
    if _t == "password":
        return "Test@12345"
    if _t == "date":
        return today_iso()
    if _t == "time":
        return "10:00"
    if _t == "datetime-local":
        return today_iso() + "T10:00"
    if _t == "month":
        return today_iso()[:7]
    if _t == "week":
        return today_iso()[:4] + "-W01"
    _prefix = "TEST_"
    _full = _prefix + _tok
    _text = _full
    _ml = str(meta.get("maxlength") or "").strip()
    if _ml.isdigit() and int(_ml) > 0:
        _limit = int(_ml)
        if _limit >= len(_full):
            _text = _full
        elif _limit > len(_prefix):
            _text = _prefix + _tok[-(_limit - len(_prefix)):]
        else:
            _text = _tok[-_limit:]
        _text = _text or "T"
    return _text


async def resolve_submittable_form_scope(page):
    """定位「可提交表单」作用域 Locator；无则返回 None。

    通用判据（跨系统/行业/页面，零业务硬编码）:
      ① 可见弹窗且含录入控件 → 弹窗即作用域（弹窗表单常无 method/提交按钮在 form 外）；
      ② 否则 method 为 post（或未声明）且「含录入控件 + 含 submit 按钮」的 <form>。
    """
    with guard("resolve_submittable_form_scope: dlgs", log=_log):
        _dlgs = page.locator(_DIALOG_SCOPE_SEL)
        for _i in range(min(await _dlgs.count(), 5)):
            _d = _dlgs.nth(_i)
            try:
                if (
                    await _d.is_visible()
                    and await _d.locator(_ENTRY_CONTROL_SEL).count() > 0
                ):
                    _log.info("[FormComplete] 作用域=可见弹窗(idx=%d)", _i)
                    return _d
            except Exception:
                continue
    with guard("resolve_submittable_form_scope: forms", log=_log):
        _forms = page.locator("form")
        for _i in range(await _forms.count()):
            _f = _forms.nth(_i)
            try:
                _m = str(await _f.get_attribute("method") or "").strip().lower()
                if _m and _m != "post":
                    continue
                if await _f.locator(_ENTRY_CONTROL_SEL).count() <= 0:
                    continue
                if await _f.locator(
                    'button[type="submit"], input[type="submit"]'
                ).count() <= 0:
                    continue
                _log.info("[FormComplete] 作用域=<form> idx=%d", _i)
                return _f
            except Exception:
                continue
    return None


async def complete_form_fields(
    page,
    skip_keys: set,
    timeout_ms: int,
    actions_taken: list,
    evidence_parts: list,
) -> int:
    """提交前补齐表单内「未填」控件（按控件类型填合法样例值），返回补全数。

    通用判据（跨系统/行业/页面，零业务硬编码）:
      ① 作用域 = resolve_submittable_form_scope（弹窗 或 可提交 <form>）；
      ② 只补「未填」控件（value 为空 / 未勾选），已填内容一律不动；
      ③ 只补「非用例点名」控件 —— skip_keys 是用例刻意设定的字段名（含刻意非法值），
         补全它们会掩盖被测校验路径；
      ④ 取值按控件类型生成合法样例：select 取首个非占位选项、number 取合法数值、
         checkbox 勾选「确认/必填」类、文本按 maxlength 截断。
    """
    _scope = await resolve_submittable_form_scope(page)
    if _scope is None:
        _log.info("[FormComplete] 无可提交表单作用域，跳过必填项补全")
        return 0
    try:
        _meta = await _scope.evaluate(_JS_FORM_CONTROL_META)
    except Exception as _e:
        _log.warning("[FormComplete] 控件元数据采集失败: %s", str(_e)[:80])
        return 0
    if not _meta:
        return 0
    _controls = _scope.locator(_ENTRY_CONTROL_SEL)
    _skip_norm = [
        str(k).strip().lower() for k in (skip_keys or set()) if str(k).strip()
    ]
    _radio_seen: set = set()
    _filled = 0
    for _m in _meta:
        try:
            if not _m.get("visible") or _m.get("disabled") or _m.get("readonly"):
                continue
            _type = str(_m.get("type") or "text").lower()
            _tag = str(_m.get("tag") or "input").lower()
            _desc = " ".join(
                str(_m.get(k) or "")
                for k in ("label", "name", "ph", "aria", "id", "tid")
            ).lower()
            # 用例点名的字段（含刻意非法值）不得补全
            if any(_k and _k in _desc for _k in _skip_norm):
                continue
            _ctl = _controls.nth(int(_m.get("i") or 0))
            _vdesc = ""
            if _type == "checkbox":
                if _m.get("checked"):
                    continue
                if not (
                    _m.get("required")
                    or any(_w in _desc for _w in _CONFIRM_CHECKBOX_WORDS)
                ):
                    continue
                await _ctl.check(timeout=min(timeout_ms, 4000))
                _vdesc = "勾选"
            elif _type == "radio":
                _g = str(_m.get("name") or _m.get("id") or _m.get("i"))
                if _g in _radio_seen:
                    continue
                _radio_seen.add(_g)
                if _m.get("checked"):
                    continue
                await _ctl.check(timeout=min(timeout_ms, 4000))
                _vdesc = "选中"
            elif _tag == "select":
                if str(_m.get("value") or "").strip():
                    continue
                _opts = _m.get("options") or []
                _pick = ""
                for _o in _opts:
                    _ov = str(_o.get("v") or "").strip()
                    _ot = str(_o.get("t") or "").strip().lower()
                    if not _ov or any(
                        _w in _ot for _w in _PLACEHOLDER_OPTION_WORDS
                    ):
                        continue
                    _pick = _ov
                    break
                if not _pick:
                    continue
                await _ctl.select_option(value=_pick, timeout=min(timeout_ms, 4000))
                _vdesc = _pick
            else:
                if str(_m.get("value") or "").strip():
                    continue
                _val = form_sample_value(_type, _m)
                if not _val:
                    continue
                await _ctl.fill(_val, timeout=min(timeout_ms, 4000))
                _vdesc = _val
            _filled += 1
            _lbl = str(_m.get("label") or _m.get("name") or _m.get("tid") or "?")[:20]
            actions_taken.append(
                {
                    "type": "fill",
                    "target": _lbl,
                    "value": _vdesc,
                    "strategy": "auto_complete",
                }
            )
            evidence_parts.append("补全'%s': %s" % (_lbl, _vdesc))
            _log.info("[FormComplete] 补全 %r=%r (type=%s)", _lbl, _vdesc, _type)
        except Exception as _ce:
            _log.debug("[FormComplete] 控件补全失败(跳过): %s", str(_ce)[:80])
            continue
    if _filled:
        _log.info("[FormComplete] 共补全 %d 个控件", _filled)
    return _filled
