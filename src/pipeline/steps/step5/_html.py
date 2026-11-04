# -*- coding: utf-8 -*-
"""
HTML 报告生成与保存相关函数
"""
import re
import time
import tempfile
import logging
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)


def _generate_html_report(markdown_content: str, stats: dict[str, Any]) -> str:
    """把 Markdown 报告包装为可浏览的 HTML。"""
    lines = markdown_content.splitlines()
    html_parts = []
    in_table = False
    table_rows = []
    in_ul = False

    def _flush_table(rows):
        if not rows:
            return ""
        header = rows[0]
        body = rows[2:] if len(rows) > 2 else rows[1:]
        out = ['<table border="1" cellspacing="0" cellpadding="6">']
        out.append("<thead><tr>")
        for cell in header:
            out.append("<th>%s</th>" % cell.strip())
        out.append("</tr></thead>")
        if body:
            out.append("<tbody>")
            for row in body:
                out.append("<tr>")
                for cell in row:
                    out.append("<td>%s</td>" % cell.strip())
                out.append("</tr>")
            out.append("</tbody>")
        out.append("</table>")
        return "\n".join(out)

    def _flush_ul(items):
        if not items:
            return ""
        out = ["<ul>"]
        for it in items:
            out.append("<li>%s</li>" % it)
        out.append("</ul>")
        return "\n".join(out)

    ul_items = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("|") and stripped.endswith("|") and len(stripped) > 1:
            if in_ul:
                html_parts.append(_flush_ul(ul_items))
                ul_items = []
                in_ul = False
            cells = stripped[1:-1].split("|")
            table_rows.append(cells)
            in_table = True
            continue
        else:
            if in_table:
                html_parts.append(_flush_table(table_rows))
                table_rows = []
                in_table = False
        if not stripped:
            if in_ul:
                html_parts.append(_flush_ul(ul_items))
                ul_items = []
                in_ul = False
            continue
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m:
            if in_ul:
                html_parts.append(_flush_ul(ul_items))
                ul_items = []
                in_ul = False
            level = len(m.group(1))
            html_parts.append("<h%d>%s</h%d>" % (level, m.group(2), level))
            continue
        if re.match(r"^[-*]\s+", line):
            ul_items.append(re.sub(r"^[-*]\s+", "", line))
            in_ul = True
            continue
        if line.startswith(">"):
            if in_ul:
                html_parts.append(_flush_ul(ul_items))
                ul_items = []
                in_ul = False
            html_parts.append("<blockquote>%s</blockquote>" % line[1:].strip())
            continue
        if re.match(r"^-{3,}$", stripped):
            if in_ul:
                html_parts.append(_flush_ul(ul_items))
                ul_items = []
                in_ul = False
            html_parts.append("<hr/>")
            continue
        if in_ul:
            html_parts.append(_flush_ul(ul_items))
            ul_items = []
            in_ul = False
        para = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", line)
        html_parts.append("<p>%s</p>" % para)
    if in_ul:
        html_parts.append(_flush_ul(ul_items))
    if in_table:
        html_parts.append(_flush_table(table_rows))
    summary_html = ""
    if stats:
        summary_rows = []
        for k, v in stats.items():
            summary_rows.append("<tr><td>%s</td><td>%s</td></tr>" % (k, v))
        summary_html = (
            '<div class="summary"><h2>摘要</h2>'
            '<table border="1" cellspacing="0" cellpadding="6">%s</table></div>'
        ) % "".join(summary_rows)
    style = (
        "<style>"
        "body{font-family:'Microsoft YaHei','PingFang SC',Arial,sans-serif;"
        "background:#f7f9fc;color:#222;max-width:1100px;margin:24px auto;"
        "padding:16px 28px;}"
        "h1,h2,h3{color:#1f4e8c;}"
        "table{border-collapse:collapse;background:#fff;margin:8px 0;width:100%;}"
        "th{background:#eaf1fb;}"
        "blockquote{border-left:4px solid #99b;background:#f1f3f7;"
        "padding:8px 14px;color:#444;}"
        ".summary{background:#fffbe6;padding:12px 16px;border:1px solid #e9d99e;"
        "border-radius:6px;}"
        "</style>"
    )
    body = "\n".join(html_parts)
    html_doc = (
        "<!DOCTYPE html>\n<html lang='zh-CN'><head><meta charset='utf-8'/>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'/>"
        "<title>AI 测试报告</title>%s</head><body>\n%s\n%s\n</body></html>"
    ) % (style, summary_html, body)
    return html_doc


def save_html_report(
    html_content: str,
    project_name: str = "default",
    run_id: str = "",
    version_id: int = 0,
    version_label: str = "",
) -> str:
    """保存 HTML 报告到 MySQL generated_reports(report_type='html_report')，不再落盘。"""
    try:
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        version = version_label or ("v_%s" % timestamp)
        from src.users.reports import save_generated_report

        save_generated_report(
            project=project_name,
            report_type="html_report",
            content=html_content,
            version_id=version_id,
            version_label=version,
            run_id=run_id or "",
        )
        # ★ P2: MySQL doc URI 前缀走 constants 集中点
        from src.core.constants import (
            MYSQL_DOC_URI_HTML_REPORT as _MYSQL_DOC_URI_HTML_REPORT,
        )

        marker = _MYSQL_DOC_URI_HTML_REPORT + "/%s/%s" % (project_name, version)
        _log.info(
            "  HTML 报告已写入 MySQL: project=%s version=%s size=%d",
            project_name,
            version,
            len(html_content),
        )
        return marker
    except Exception as e:
        _log.warning("  HTML 报告写入 MySQL 失败,回落盘: %s", e)
        # 回退（不保证目录存在，尽量尝试）
        try:
            import tempfile

            fname = "AI测试报告_%s.html" % time.strftime("%Y%m%d_%H%M%S")
            fpath = str(Path(tempfile.gettempdir()) / fname)
            with open(fpath, "w", encoding="utf-8") as f:
                f.write(html_content)
            return fpath
        except Exception as _e:
            _log.debug("[_html] 非关键异常: %s", _e, exc_info=True)
            return ""
