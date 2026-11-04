# -*- coding: utf-8 -*-
"""DB/UI 校验通用助手 —— 从 _db_ui.py 拆出（§4.3 单文件≤800行）。

纯函数/纯查询助手，零业务逻辑变更：
- _match_transient_hints: 瞬态提示缓存查询（元素存在类断言兜底）
- _match_transient_text:  瞬态提示缓存查询（文本包含类断言兜底）
- _count_text_excluding_inputs: 页面文本计数（排除输入框值）
"""

import re as _re


# =============================================================================
# ★ 通用修复(2026-07-22 v27): 瞬态提示缓存查询
#   根因: antd/elementUI 的 message/toast 提示出现后数秒自动消失，
#         校验阶段查活 DOM 必然错过 → 必须在动作执行期间捕获（见
#         browser_structured_executor._capture_transient_hints），此处查询缓存。
#   防假PASS设计:
#     - 仅当 selector 语义指向"提示类组件"时才查缓存（普通元素不用缓存兜底）
#     - 错误类选择器只被 error/warning/role=alert 的缓存满足；成功类只被
#       success/info/role=status 满足 —— 双向防止语义错配的假PASS
# =============================================================================
_HINT_SELECTOR_TOKENS = (
    "alert",
    "error",
    "invalid",
    "danger",
    "status",
    "success",
    "message",
    "toast",
    "notification",
    "warn",
)


def _match_transient_hints(page, selector: str) -> str:
    """活 DOM 未命中时，查询动作执行期间捕获的瞬态提示缓存。

    Returns:
        命中的提示文本（前80字符）；未命中/不适用返回 ""
    """
    sel = (selector or "").lower()
    if not any(t in sel for t in _HINT_SELECTOR_TOKENS):
        return ""
    hints = getattr(page, "_transient_hints", None) or []
    if not hints:
        return ""
    wants_error = any(t in sel for t in ("error", "invalid", "danger", "alert"))
    wants_success = any(t in sel for t in ("success", "status"))
    for h in hints:
        cls = (h.get("cls") or "").lower()
        role = (h.get("role") or "").lower()
        text = (h.get("text") or "").strip()
        if not text:
            continue
        if wants_error:
            if "error" in cls or "warn" in cls or role == "alert":
                return text[:80]
            continue  # 错误类选择器不被成功/中性提示满足（防假PASS）
        if wants_success:
            if "success" in cls or "info" in cls or role == "status":
                return text[:80]
            continue  # 成功类选择器不被错误提示满足（防假PASS）
        return text[:80]  # 通用提示语义（message/toast/notification）→ 任一捕获
    return ""


def _match_transient_text(page, expected_text: str) -> str:
    """text_contains 的瞬态缓存查询：期望文本（支持逗号/顿号/竖线分隔的多候选）
    是否为任一捕获提示的子串。

    Returns:
        命中的提示文本（前80字符）；未命中返回 ""
    """
    hints = getattr(page, "_transient_hints", None) or []
    if not hints or not expected_text:
        return ""
    alternatives = [
        a.strip() for a in _re.split(r"[,，、/|]", expected_text) if a.strip()
    ]
    for h in hints:
        text = (h.get("text") or "").strip()
        if not text:
            continue
        for alt in alternatives:
            if alt and alt in text:
                return text[:80]
    return ""


async def _count_text_excluding_inputs(page, text: str) -> int:
    """★ 通用修复(2026-07-22): 统计页面文本出现次数，排除输入框的值。

    根因: Playwright 的 text= 选择器会匹配 <input> 的 value 属性（用户输入），
    导致搜索场景下 N01（text_not_exists）假失败：
    用户在搜索框输入的不存在关键词被 text= 匹配到，误判为"文本存在"。

    通用性:
    - 跨系统: 适用于 React/Vue/Angular/原生 HTML
    - 跨行业: 适用于电商/SaaS/ERP/配方系统
    - 跨页面: 适用于列表页/详情页/表单页/Tab页
    - 覆盖性: 排除 input/textarea/select + contenteditable

    返回: 页面内容（排除输入框值）中包含 text 的文本节点数量。
    """
    if not text:
        return 0
    # ★ : 修复 page.evaluate 无法正确调用箭头函数的问题
    #   根因: page.evaluate(js_code, text) 的 js_code 是多行字符串，Playwright
    #   无法将其识别为可调用函数，导致返回函数对象而非调用结果。
    #   修复: 使用 page.evaluate 内联函数代替字符串表达式
    try:
        _result = await page.evaluate(
            """(searchText) => {
                const walker = document.createTreeWalker(
                    document.body,
                    NodeFilter.SHOW_TEXT,
                    {
                        acceptNode: (node) => {
                            let parent = node.parentElement;
                            while (parent) {
                                const tag = parent.tagName.toLowerCase();
                                if (tag === 'input' || tag === 'textarea' || tag === 'select') {
                                    return NodeFilter.FILTER_REJECT;
                                }
                                if (parent.isContentEditable) {
                                    return NodeFilter.FILTER_REJECT;
                                }
                                parent = parent.parentElement;
                            }
                            return node.textContent.includes(searchText)
                                ? NodeFilter.FILTER_ACCEPT
                                : NodeFilter.FILTER_REJECT;
                        }
                    }
                );
                let count = 0;
                while (walker.nextNode()) count++;
                return count;
            }""",
            text,
        )
        return _result or 0
    except Exception as _ex:
        print(f"【DEBUG _count_text_excluding_inputs error】text={text} error={_ex}", flush=True)
        return 0
