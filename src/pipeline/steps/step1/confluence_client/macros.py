# -*- coding: utf-8 -*-
"""Confluence 宏处理 + 富文本提取 — 从 confluence_client.py 拆出，零业务逻辑变更。"""

import re


class _MacrosMixin:
    """_convert_macros + 各 macro 处理器（_macro_jira / _macro_details / _macro_callout 等）。"""

    # ══════════════════════════════════════════════════
    # Confluence 宏提取（v5：不再丢弃，分类转换为可读标注）
    # ══════════════════════════════════════════════════

    @staticmethod
    def _convert_macros(html: str) -> str:
        """将 Confluence Storage 格式的 ac:structured-macro 转换为 Markdown 可读标注。"""

        def _dispatch(match):
            macro_html = match.group(0)
            # 提取 ac:name 属性
            name_match = re.search(r'ac:name="([^"]+)"', macro_html)
            macro_name = name_match.group(1).lower() if name_match else "unknown"

            # 提取 ac:parameter 中的 key-value
            params = {}
            for pm in re.finditer(
                r'<ac:parameter\s+ac:name="([^"]+)">(.*?)</ac:parameter>',
                macro_html,
                re.DOTALL,
            ):
                params[pm.group(1).lower()] = pm.group(2).strip()

            # 提取富文本正文
            body = _MacrosMixin._extract_macro_body(macro_html)

            # 按宏类型分发
            if macro_name == "jira":
                return _MacrosMixin._macro_jira(params, body)
            elif macro_name in ("detail", "details"):
                return _MacrosMixin._macro_details(params, body)
            elif macro_name in ("info", "warning", "tip", "note"):
                return _MacrosMixin._macro_callout(macro_name, body)
            elif macro_name == "task-list":
                return _MacrosMixin._macro_tasklist(params, body)
            elif macro_name == "excerpt":
                return _MacrosMixin._macro_excerpt(body)
            elif macro_name in ("children", "include", "excerpt-include"):
                return _MacrosMixin._macro_reference(macro_name, params)
            else:
                # 无法识别的宏 → 标注丢失
                return _MacrosMixin._macro_unknown(macro_name, params, body)

        return re.sub(
            r"<ac:structured-macro[^>]*>.*?</ac:structured-macro>",
            _dispatch,
            html,
            flags=re.DOTALL,
        )

    @staticmethod
    def _extract_macro_body(macro_html: str) -> str:
        """从 ac:structured-macro 中提取 ac:rich-text-body 的纯文本内容。"""
        body_match = re.search(
            r"<ac:rich-text-body[^>]*>(.*?)</ac:rich-text-body>",
            macro_html,
            flags=re.DOTALL,
        )
        if not body_match:
            return ""
        return _MacrosMixin._extract_macro_text(body_match.group(1))

    @staticmethod
    def _extract_macro_text(html_fragment: str) -> str:
        """将 HTML 片段中的文本提取出来（递归处理嵌套标签，去标签保留文本）。"""
        if not html_fragment:
            return ""
        # 处理常见行内标签
        text = html_fragment
        text = re.sub(r"<br\s*/?>", "\n", text)
        text = re.sub(r"</?p[^>]*>", "\n", text)
        text = re.sub(r"<li[^>]*>(.*?)</li>", r"\n- \1", text, flags=re.DOTALL)
        text = re.sub(
            r"<(?:strong|b)[^>]*>(.*?)</(?:strong|b)>", r"**\1**", text, flags=re.DOTALL
        )
        text = re.sub(
            r"<(?:em|i)[^>]*>(.*?)</(?:em|i)>", r"*\1*", text, flags=re.DOTALL
        )
        text = re.sub(r"<code[^>]*>(.*?)</code>", r"`\1`", text, flags=re.DOTALL)
        text = re.sub(
            r"<(?:s|del|strike)[^>]*>(.*?)</(?:s|del|strike)>",
            r"~~\1~~",
            text,
            flags=re.DOTALL,
        )
        text = re.sub(
            r"<span[^>]*text-decoration:\s*line-through[^>]*>(.*?)</span>",
            r"~~\1~~",
            text,
            flags=re.DOTALL,
        )
        text = re.sub(
            r'<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>', r"[\2](\1)", text, flags=re.DOTALL
        )
        text = re.sub(r"<ac:link[^>]*>.*?</ac:link>", "", text, flags=re.DOTALL)
        # 剩余标签全部去除
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = re.sub(r" {2,}", " ", text)
        return text.strip()

    # ── 各宏类型转换器 ──

    @staticmethod
    def _macro_jira(params: dict, body: str) -> str:
        """JIRA Issue 宏 → [JIRA: KEY-123 摘要]"""
        key = params.get("key", "")
        summary = body or params.get("summary", "")
        if key:
            return "\n\n[JIRA: %s%s]\n\n" % (key, (" — " + summary) if summary else "")
        return "\n\n[JIRA Issue: %s]\n\n" % (summary or "(无描述)")

    @staticmethod
    def _macro_details(params: dict, body: str) -> str:
        """折叠面板 → Markdown 引用块"""
        title = params.get("title", "详情")
        if body:
            return "\n\n> **⏵ %s**\n> \n> %s\n\n" % (title, body.replace("\n", "\n> "))
        return "\n\n> **⏵ %s**\n\n" % title

    @staticmethod
    def _macro_callout(macro_name: str, body: str) -> str:
        """Info/Warning/Tip/Note 面板 → Markdown 引用块"""
        icons = {"info": "ℹ️", "warning": "⚠️", "tip": "💡", "note": "📝"}
        icon = icons.get(macro_name, "📌")
        if body:
            return "\n\n> **%s %s**\n> \n> %s\n\n" % (
                icon,
                macro_name.upper(),
                body.replace("\n", "\n> "),
            )
        return ""

    @staticmethod
    def _macro_tasklist(params: dict, body: str) -> str:
        """任务列表 → Markdown 任务列表"""
        if not body:
            return ""
        lines = body.split("\n")
        result = ["\n"]
        for line in lines:
            stripped = line.strip("- ").strip()
            if not stripped:
                continue
            # 检测任务状态
            if (
                stripped.startswith("✓")
                or stripped.startswith("[x]")
                or stripped.startswith("[X]")
            ):
                clean = re.sub(r"^(✓|\[x\]|\[X\])\s*", "", stripped)
                result.append("- [x] %s" % clean)
            elif stripped.startswith("☐") or stripped.startswith("[ ]"):
                clean = re.sub(r"^(☐|\[ \])\s*", "", stripped)
                result.append("- [ ] %s" % clean)
            else:
                result.append("- [ ] %s" % stripped)
        result.append("")
        return "\n".join(result)

    @staticmethod
    def _macro_excerpt(body: str) -> str:
        """摘录宏 → 引用块"""
        if body:
            return "\n\n> 摘录: %s\n\n" % body.replace("\n", "\n> ")
        return ""

    @staticmethod
    def _macro_reference(macro_name: str, params: dict) -> str:
        """引用宏（children/include 等）→ 标注已省略"""
        page_title = params.get("title", params.get("page", ""))
        if page_title:
            return "\n\n*[子页面「%s」已省略，请手动查看]*\n\n" % page_title
        return "\n\n*[引用内容已省略，请手动查看]*\n\n"

    @staticmethod
    def _macro_unknown(macro_name: str, params: dict, body: str) -> str:
        """无法识别的宏 → 标注丢失内容"""
        detail_parts = []
        if params:
            detail_parts.append(
                ", ".join("%s=%s" % (k, v) for k, v in list(params.items())[:3])
            )
        if body:
            detail_parts.append("正文: %s" % body[:80])
        detail = " | ".join(detail_parts) if detail_parts else "内容未解析"
        return "\n\n*[宏: %s — %s]*\n\n" % (macro_name, detail)
