# -*- coding: utf-8 -*-
"""HTML→Markdown 转换主入口 + 表格/图片/链接 — 从 confluence_client.py 拆出，零业务逻辑变更。"""

import re
import html

from .macros import _MacrosMixin


class _HtmlConvertMixin:
    """_html_to_markdown / _convert_tables / _convert_images / _convert_links。"""

    # ══════════════════════════════════════════════════
    # HTML → Markdown 转换（保留表格/图片/链接语义）
    # ══════════════════════════════════════════════════

    @staticmethod
    def _html_to_markdown(html_text: str) -> str:
        """将 Confluence Storage HTML 转为 Markdown，保留结构和关键语义。"""
        if not html_text:
            return ""

        # ── HTML 实体解码（如 &plusmn; → ±, &ge; → ≥, &radic; → √） ──
        html_text = html.unescape(html_text)
        # ★ 避免局部变量名 'html' 与模块 'html' 冲突（Python 在函数内赋值的变量
        # 会被视为局部变量，导致 `html.unescape` 报 UnboundLocalError）
        md_text = _MacrosMixin._convert_macros(html_text)
        md_text = re.sub(r"<ri:page[^>]*/>", "", md_text)
        md_text = re.sub(r"<ri:user[^>]*/>", "", md_text)

        # ── 表格 → Markdown ──
        md_text = _HtmlConvertMixin._convert_tables(md_text)

        # ── 图片 → Markdown ──
        md_text = _HtmlConvertMixin._convert_images(md_text)

        # ── 链接 → Markdown ──
        md_text = _HtmlConvertMixin._convert_links(md_text)

        # ── 块级元素 ──
        md_text = re.sub(r"<br\s*/?>", "\n", md_text)
        md_text = re.sub(r"</?p[^>]*>", "\n\n", md_text)
        md_text = re.sub(r"</?div[^>]*>", "\n", md_text)
        md_text = re.sub(r"<hr\s*/?>", "\n---\n", md_text)

        # ── 标题 ──
        for level in range(6, 0, -1):
            md_text = re.sub(
                r"<h%d[^>]*>(.*?)</h%d>" % (level, level),
                lambda m, l=level: "\n\n" + "#" * l + " " + m.group(1).strip() + "\n\n",
                md_text,
                flags=re.DOTALL,
            )

        # ── 列表 ──
        md_text = re.sub(r"<li[^>]*>(.*?)</li>", r"\n- \1", md_text, flags=re.DOTALL)
        md_text = re.sub(r"</?[ou]l[^>]*>", "\n", md_text)

        # ── 行内格式 ──
        md_text = re.sub(
            r"<(?:strong|b)[^>]*>(.*?)</(?:strong|b)>",
            r"**\1**",
            md_text,
            flags=re.DOTALL,
        )
        md_text = re.sub(
            r"<(?:em|i)[^>]*>(.*?)</(?:em|i)>", r"*\1*", md_text, flags=re.DOTALL
        )
        md_text = re.sub(r"<code[^>]*>(.*?)</code>", r"`\1`", md_text, flags=re.DOTALL)
        # 删除线：<s> / <del> / CSS text-decoration → Markdown ~~text~~
        md_text = re.sub(
            r"<(?:s|del|strike)[^>]*>(.*?)</(?:s|del|strike)>",
            r"~~\1~~",
            md_text,
            flags=re.DOTALL,
        )
        md_text = re.sub(
            r"<span[^>]*text-decoration:\s*line-through[^>]*>(.*?)</span>",
            r"~~\1~~",
            md_text,
            flags=re.DOTALL,
        )

        # ── 剩余标签：去标签保留文本 ──
        md_text = re.sub(r"<[^>]+>", "", md_text)

        # ── 清理空白 ──
        md_text = re.sub(r"\n{3,}", "\n\n", md_text)
        md_text = re.sub(r" {2,}", " ", md_text)
        md_text = md_text.strip()

        return md_text

    @staticmethod
    def _convert_tables(html: str) -> str:
        """将 HTML <table> 转换为 Markdown 表格。"""

        def _table_to_md(match):
            table_html = match.group(0)
            rows = re.findall(r"<tr[^>]*>(.*?)</tr>", table_html, flags=re.DOTALL)
            if not rows:
                return match.group(0)

            md_rows = []
            for ri, row_html in enumerate(rows):
                cells = re.findall(
                    r"<(?:td|th)[^>]*>(.*?)</(?:td|th)>", row_html, flags=re.DOTALL
                )
                # 去除单元格内 HTML
                cells = [
                    re.sub(r"<[^>]+>", "", c)
                    .strip()
                    .replace("\n", " ")
                    .replace("|", "\\|")
                    for c in cells
                ]
                md_rows.append("| " + " | ".join(cells) + " |")

            if not md_rows:
                return match.group(0)

            # 插入表头分隔行
            col_count = md_rows[0].count("|") - 1
            if col_count > 0:
                sep = "|" + "|".join([" --- "] * col_count) + "|"
                md_rows.insert(1, sep)

            return "\n" + "\n".join(md_rows) + "\n"

        return re.sub(r"<table[^>]*>.*?</table>", _table_to_md, html, flags=re.DOTALL)

    @staticmethod
    def _convert_images(html: str) -> str:
        """将 <img> 和 <ac:image> 转换为 Markdown 图片语法。"""

        # 标准 img 标签
        def _img_to_md(m):
            alt = m.group(1) or ""
            src = m.group(2) or ""
            return "![%s](%s)" % (alt.strip(), src.strip())

        html = re.sub(
            r'<img[^>]*alt="([^"]*)"[^>]*src="([^"]*)"[^>]*/?>',
            _img_to_md,
            html,
        )
        html = re.sub(
            r'<img[^>]*src="([^"]*)"[^>]*alt="([^"]*)"[^>]*/?>',
            lambda m: "![%s](%s)" % (m.group(2).strip(), m.group(1).strip()),
            html,
        )
        # 无 alt 的 img
        html = re.sub(
            r'<img[^>]*src="([^"]*)"[^>]*/?>',
            r"[图: \1]",
            html,
        )

        # Confluence <ac:image>
        def _ac_image_to_md(m):
            inner = m.group(1)
            alt_match = re.search(r"<ac:alt>(.*?)</ac:alt>", inner)
            alt = alt_match.group(1).strip() if alt_match else ""
            # Confluence 附件图片有 ri:attachment ri:filename="xxx.png"
            url_match = re.search(r'ri:url="([^"]*)"', inner) or re.search(
                r'ri:filename="([^"]*)"', inner
            )
            url = url_match.group(1).strip() if url_match else ""
            if alt and url:
                return "![%s](%s)" % (alt, url)
            if alt:
                return "[图: %s]" % alt
            return "[图: %s]" % url if url else "[图]"

        html = re.sub(
            r"<ac:image[^>]*>(.*?)</ac:image>", _ac_image_to_md, html, flags=re.DOTALL
        )

        # 清理残留的附件/用户引用标签
        html = re.sub(r"<ri:attachment[^>]*/?>", "", html)
        html = re.sub(r"<ri:url[^>]*/?>", "", html)

        return html

    @staticmethod
    def _extract_image_urls(html: str) -> list[str]:
        """从 Confluence Storage HTML 中提取所有图片 URL。

        返回去重后的 URL 列表，供视觉识别模块使用。
        """
        urls = []
        # <img src="...">
        for m in re.finditer(r'<img[^>]*src="([^"]*)"', html):
            url = m.group(1).strip()
            if url and not url.startswith("data:") and url not in urls:
                urls.append(url)
        # <ac:image> ri:url / ri:filename
        for m in re.finditer(r"<ac:image[^>]*>(.*?)</ac:image>", html, re.DOTALL):
            inner = m.group(1)
            url_match = re.search(r'ri:url="([^"]*)"', inner) or re.search(
                r'ri:filename="([^"]*)"', inner
            )
            if url_match:
                url = url_match.group(1).strip()
                if url and url not in urls:
                    urls.append(url)
        return urls

    @staticmethod
    def _convert_links(html: str) -> str:
        """将 <a> 链接转换为 Markdown。"""

        def _a_to_md(m):
            text = re.sub(r"<[^>]+>", "", m.group(2) or "")
            href = m.group(1) or ""
            if not text.strip():
                return ""
            if href:
                return "[%s](%s)" % (text.strip(), href.strip())
            return text.strip()

        return re.sub(
            r'<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>', _a_to_md, html, flags=re.DOTALL
        )
