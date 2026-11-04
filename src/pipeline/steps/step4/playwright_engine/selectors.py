# -*- coding: utf-8 -*-
"""语义选择器构建模块。"""


def _escape_css_str(value: str) -> str:
    """★ T-003: 转义 CSS 选择器值中的特殊字符。

    用于属性选择器值(如 [attr="..."])和 :has-text("...") 文本。
    转义规则:
    - 反斜杠 \\ → \\\\ (必须先转义,避免二次转义)
    - 双引号 " → \\" (防止闭合字符串)
    """
    if not value:
        return ""
    return value.replace("\\", "\\\\").replace('"', '\\"')


class SelectorMixin:
    """从 AI 提供的 target_hint 和 fallback_selectors 构建 Playwright 选择器。"""

    @staticmethod
    def _build_semantic_selector(
        target_hint: str, fallbacks: list, skip_first: bool = False
    ) -> str:
        """从 AI 提供的 target_hint 和 fallback_selectors 构建 Playwright 选择器。"""
        candidates = []
        if target_hint and not skip_first:
            candidates.append(target_hint)
        candidates.extend(fallbacks or [])
        if skip_first and len(candidates) > 1:
            candidates = candidates[1:]

        for hint in candidates:
            hint = hint.strip()
            if not hint:
                continue
            if hint[0] in ("#", ".", "["):
                return hint
            if hint.startswith("css="):
                return hint[4:]
            if hint.startswith("text="):
                v = _escape_css_str(hint[5:])
                return (
                    'button:has-text("%s"), a:has-text("%s"), span:has-text("%s")'
                    % (v, v, v)
                )
            if hint.startswith("placeholder="):
                v = _escape_css_str(hint[12:])
                return 'input[placeholder*="%s"], textarea[placeholder*="%s"]' % (
                    v,
                    v,
                )
            if hint.startswith("label=") or hint.startswith("aria-label="):
                v = _escape_css_str(hint.split("=", 1)[1])
                return '[aria-label*="%s"]' % v
            if hint.startswith("role="):
                parts = hint.split("|")
                role_val = parts[0].split("=", 1)[1].strip()
                name_val = next(
                    (p[5:].strip() for p in parts[1:] if p.startswith("name=")), ""
                )
                # ★ T-001: 用 ARIA 语义选择器组合替代 [role][name] CSS
                # [name="..."] 属性在大多数 HTML 元素上不存在(ARIA accessible name
                # 通过 aria-label/aria-labelledby/text 计算)。get_by_role() 内部使用
                # accessible name 计算,CSS 无法完全模拟,但通过 :has-text() + aria-label
                # 组合可覆盖绝大多数场景。
                # ★ T-003: 对 name_val 和 role_val 进行转义
                role_val_esc = _escape_css_str(role_val)
                if name_val:
                    name_val_esc = _escape_css_str(name_val)
                    # role → 对应的 HTML 标签(用于扩大匹配范围)
                    role_tag_map = {
                        "button": "button",
                        "link": "a",
                        "menuitem": "[role=menuitem]",
                        "tab": "[role=tab]",
                    }
                    tag = role_tag_map.get(role_val)
                    # 组合:标签:has-text + [role] aria-label + [role]:has-text
                    if tag:
                        return (
                            '%s:has-text("%s"), [role="%s"][aria-label*="%s"], '
                            '[role="%s"]:has-text("%s")'
                        ) % (
                            tag,
                            name_val_esc,
                            role_val_esc,
                            name_val_esc,
                            role_val_esc,
                            name_val_esc,
                        )
                    return (
                        '[role="%s"][aria-label*="%s"], ' '[role="%s"]:has-text("%s")'
                    ) % (role_val_esc, name_val_esc, role_val_esc, name_val_esc)
                return '[role="%s"]' % role_val_esc
            if hint.startswith("ai_id="):
                v = _escape_css_str(hint[6:])
                return '[data-ai-id="%s"]' % v
            if hint.startswith("css="):
                return hint[4:]
            # ★ v2.0 (2026-07-07): 通用语义 hint → role/tag 选择器
            #   规则提取生成的 target_hint 是通用语义（如"提交按钮"/"查询输入框"/"下拉选择框"），
            #   用 :has-text("提交按钮") 精确匹配会因页面按钮文本是"保存"/"确定"而失败 → click 失败。
            #   改为按语义类型生成 role/button 选择器，让 click_element 级别4 直接命中。
            #   通用性：不硬编码业务功能名，只识别"XX按钮/XX输入框/XX下拉"等通用语义模式。
            _lower_hint = hint.lower()
            if "按钮" in hint or "button" in _lower_hint:
                return 'button, [role="button"], input[type="submit"], input[type="button"]'
            if "输入框" in hint or "文本框" in hint or "textbox" in _lower_hint:
                # ★ 覆盖所有文本输入类型（text/search/password/email/tel/url/number/无type）
                return 'input[type="text"], input[type="search"], input[type="password"], input[type="email"], input[type="tel"], input[type="url"], input[type="number"], input:not([type]), textarea, [role="textbox"]'
            if (
                "下拉" in hint
                or "选择框" in hint
                or "select" in _lower_hint
                or "combobox" in _lower_hint
            ):
                return 'select, [role="combobox"]'
            if "复选框" in hint or "checkbox" in _lower_hint:
                return 'input[type="checkbox"], [role="checkbox"]'
            if "链接" in hint or "link" in _lower_hint:
                return 'a, [role="link"]'
            # ★ P2-4（QA 复核，2026-08-05）: date 语义分支 — 默认回退只有 button/a:has-text，
            #   input[type=date] 匹配 0 导致 set_date 失败。日期/时间类 hint 命中原生 date 输入。
            if (
                "日期" in hint
                or "时间" in hint
                or "date" in _lower_hint
                or "datetime" in _lower_hint
            ):
                return (
                    'input[type="date"], input[type="datetime-local"], '
                    'input[type="time"], input[type="month"], input[type="week"]'
                )
            # ★ 默认回退：raw text → Playwright :has-text() 伪选择器
            v = _escape_css_str(hint)
            return 'button:has-text("%s"), a:has-text("%s")' % (v, v)

        return ""
