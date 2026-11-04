# -*- coding: utf-8 -*-
"""把 docs/book/*.md 合成为单文件 HTML 电子书。

自带 Markdown 转换器（只实现本书用到的语法子集：标题、围栏代码、
表格、有序/无序列表、引用、分隔线、行内代码/粗体/斜体/链接），
不依赖第三方库，保证在任何环境可重跑。

输出：docs/引擎技术设计与实现.html
"""
from __future__ import annotations

import datetime
import html
import os
import re

BOOK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(os.path.dirname(BOOK), "引擎技术设计与实现.html")

ORDER = [f"{i:02d}" for i in range(0, 34)] + ["A", "B", "C", "D"]


def chapter_files() -> list[tuple[str, str]]:
    files = []
    for fn in os.listdir(BOOK):
        if not fn.endswith(".md"):
            continue
        key = fn.split("-")[0]
        if key in ORDER:
            files.append((key, fn))
    files.sort(key=lambda x: ORDER.index(x[0]))
    return files


# ------------------------------------------------------------ 行内处理
_INLINE_CODE = re.compile(r"`([^`]+)`")
_BOLD = re.compile(r"\*\*([^*]+)\*\*")
_ITALIC = re.compile(r"(?<!\*)\*([^*\n]+)\*(?!\*)")


def inline(text: str) -> str:
    out = html.escape(text, quote=False)
    out = _INLINE_CODE.sub(lambda m: "<code>" + m.group(1) + "</code>", out)
    out = _BOLD.sub(lambda m: "<strong>" + m.group(1) + "</strong>", out)
    out = _ITALIC.sub(lambda m: "<em>" + m.group(1) + "</em>", out)
    return out


def slug(text: str, fallback: str) -> str:
    s = re.sub(r"[^\w\u4e00-\u9fff]+", "-", text).strip("-").lower()
    return s or fallback


# ------------------------------------------------------------ 块级转换
def convert(md: str, ch_key: str) -> tuple[str, list[tuple[int, str, str]]]:
    lines = md.split("\n")
    out: list[str] = []
    toc: list[tuple[int, str, str]] = []
    i = 0
    h1_done = False
    sec = 0
    while i < len(lines):
        line = lines[i]

        if line.startswith("```"):
            lang = line[3:].strip() or "text"
            i += 1
            buf = []
            while i < len(lines) and not lines[i].startswith("```"):
                buf.append(lines[i])
                i += 1
            i += 1
            out.append(f'<pre class="code" data-lang="{html.escape(lang)}"><code>'
                       + html.escape("\n".join(buf)) + "</code></pre>")
            continue

        m = re.match(r"^(#{1,4})\s+(.*)$", line)
        if m:
            level = len(m.group(1))
            text = m.group(2).strip()
            if level == 1 and not h1_done:
                cid = f"ch-{ch_key}"
                h1_done = True
                out.append(f'<h1 id="{cid}">{inline(text)}</h1>')
                toc.append((1, text, cid))
            else:
                sec += 1
                cid = f"sec-{ch_key}-{sec}"
                out.append(f'<h{min(level,4)} id="{cid}">{inline(text)}</h{min(level,4)}>')
                toc.append((min(level, 4), text, cid))
            i += 1
            continue

        if re.match(r"^\s*\|.*\|\s*$", line) and i + 1 < len(lines) and re.match(r"^\s*\|[\s:|-]+\|\s*$", lines[i + 1]):
            header = [c.strip() for c in line.strip().strip("|").split("|")]
            align_row = [c.strip() for c in lines[i + 1].strip().strip("|").split("|")]
            aligns = []
            for a in align_row:
                if a.startswith(":") and a.endswith(":"):
                    aligns.append("center")
                elif a.endswith(":"):
                    aligns.append("right")
                else:
                    aligns.append("left")
            i += 2
            rows = []
            while i < len(lines) and re.match(r"^\s*\|.*\|\s*$", lines[i]):
                rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            out.append('<div class="tw"><table>')
            out.append("<thead><tr>" + "".join(
                f'<th class="{aligns[k] if k < len(aligns) else "left"}">{inline(c)}</th>'
                for k, c in enumerate(header)) + "</tr></thead><tbody>")
            for r in rows:
                cells = []
                for k in range(len(header)):
                    val = r[k] if k < len(r) else ""
                    cells.append(f'<td class="{aligns[k] if k < len(aligns) else "left"}">{inline(val)}</td>')
                out.append("<tr>" + "".join(cells) + "</tr>")
            out.append("</tbody></table></div>")
            continue

        if re.match(r"^\s*[-*]\s+", line) or re.match(r"^\s*\d+\.\s+", line):
            html_list, i = _convert_list(lines, i)
            out.append(html_list)
            continue

        if line.startswith(">"):
            buf = []
            while i < len(lines) and lines[i].startswith(">"):
                buf.append(lines[i].lstrip("> ").rstrip())
                i += 1
            out.append("<blockquote>" + "".join(f"<p>{inline(b)}</p>" for b in buf if b) + "</blockquote>")
            continue

        if re.match(r"^\s*(---|\*\*\*)\s*$", line):
            out.append("<hr/>")
            i += 1
            continue

        if not line.strip():
            i += 1
            continue

        buf = []
        while i < len(lines) and lines[i].strip() and not re.match(
                r"^(#{1,4}\s|```|\s*[-*]\s|\s*\d+\.\s|>|\s*\|)", lines[i]):
            buf.append(lines[i].strip())
            i += 1
        if buf:
            out.append("<p>" + inline(" ".join(buf)) + "</p>")
    return "\n".join(out), toc


def _convert_list(lines: list[str], i: int) -> tuple[str, int]:
    ordered = bool(re.match(r"^\s*\d+\.\s+", lines[i]))
    tag = "ol" if ordered else "ul"
    items: list[str] = []
    while i < len(lines):
        m = re.match(r"^(\s*)([-*]|\d+\.)\s+(.*)$", lines[i])
        if not m:
            if lines[i].strip() and lines[i].startswith(("  ", "\t")):
                if items:
                    items[-1] = items[-1].rstrip("</li>") + " " + inline(lines[i].strip()) + "</li>"
                i += 1
                continue
            break
        indent = len(m.group(1))
        if indent >= 2 and items:
            items[-1] = items[-1].rstrip("</li>") + " " + inline(m.group(3)) + "</li>"
        else:
            items.append("<li>" + inline(m.group(3)) + "</li>")
        i += 1
    return f"<{tag}>" + "".join(items) + f"</{tag}>", i


CSS = """
:root{
  --bg:#f7f6f3; --panel:#ffffff; --ink:#1c1b19; --ink-2:#4a4741; --ink-3:#7d7971;
  --line:#e2ded6; --line-2:#d0cbc0; --accent:#8a5a2b; --accent-2:#b8874a;
  --code-bg:#f0eee9; --code-ink:#2c2a26; --mark:#fdf6e8;
}
*{box-sizing:border-box}
html{scroll-behavior:smooth}
body{margin:0;background:var(--bg);color:var(--ink);
  font:16px/1.85 "Segoe UI","PingFang SC","Microsoft YaHei",-apple-system,sans-serif;
  -webkit-font-smoothing:antialiased;text-rendering:optimizeLegibility}
#wrap{display:flex;align-items:flex-start;max-width:1440px;margin:0 auto;padding:0 20px}
#toc{position:sticky;top:0;flex:0 0 292px;max-height:100vh;overflow:auto;
  padding:26px 18px 40px 4px;border-right:1px solid var(--line)}
#toc h2{font-size:12px;letter-spacing:.14em;text-transform:uppercase;color:var(--ink-3);
  margin:0 0 12px;font-weight:600}
#toc input{width:100%;padding:7px 9px;margin-bottom:14px;border:1px solid var(--line-2);
  border-radius:5px;background:var(--panel);font-size:13px;color:var(--ink);outline:none}
#toc input:focus{border-color:var(--accent-2)}
#toc a{display:block;padding:4px 8px;color:var(--ink-2);text-decoration:none;font-size:13px;
  border-radius:4px;border-left:2px solid transparent;line-height:1.5}
#toc a:hover{background:var(--mark);color:var(--ink)}
#toc a.lv1{font-weight:600;color:var(--ink);margin-top:9px}
#toc a.lv3,#toc a.lv4{padding-left:20px;font-size:12.5px;color:var(--ink-3)}
#toc a.lv2{padding-left:12px}
main{flex:1 1 auto;min-width:0;padding:34px 46px 120px;background:var(--bg)}
#cover{border-bottom:3px double var(--line-2);padding-bottom:34px;margin-bottom:38px}
#cover .kicker{font-size:12px;letter-spacing:.2em;text-transform:uppercase;color:var(--accent);font-weight:700}
#cover h1{font-size:38px;line-height:1.25;margin:14px 0 10px;font-weight:700;letter-spacing:-.01em}
#cover .sub{font-size:16px;color:var(--ink-2);margin:0 0 24px}
#cover dl{display:grid;grid-template-columns:auto 1fr;gap:6px 18px;margin:0;font-size:13.5px}
#cover dt{color:var(--ink-3)}
#cover dd{margin:0;color:var(--ink-2)}
h1{font-size:27px;margin:56px 0 18px;padding-top:14px;border-top:1px solid var(--line);
  font-weight:700;letter-spacing:-.005em}
h1:first-of-type{border-top:none}
h2{font-size:20px;margin:34px 0 12px;font-weight:650}
h3{font-size:17px;margin:26px 0 10px;font-weight:650}
h4{font-size:15.5px;margin:20px 0 8px;font-weight:650;color:var(--ink-2)}
p{margin:0 0 14px}
ul,ol{margin:0 0 15px;padding-left:26px}
li{margin:5px 0}
li>ul,li>ol{margin:5px 0}
strong{font-weight:650}
code{font-family:"Cascadia Mono",Consolas,"Courier New",monospace;font-size:13.5px;
  background:var(--code-bg);color:var(--code-ink);padding:1px 5px;border-radius:4px}
pre.code{background:#26241f;color:#e8e4dc;padding:15px 18px;border-radius:7px;overflow:auto;
  margin:0 0 18px;position:relative;border:1px solid #35322b}
pre.code code{background:none;color:inherit;padding:0;font-size:13px;line-height:1.7}
pre.code[data-lang]::before{content:attr(data-lang);position:absolute;top:7px;right:12px;
  font-size:10.5px;letter-spacing:.1em;text-transform:uppercase;color:#8a857a}
blockquote{margin:0 0 16px;padding:10px 18px;border-left:3px solid var(--accent-2);
  background:var(--mark);color:var(--ink-2)}
blockquote p{margin:4px 0}
hr{border:none;border-top:1px solid var(--line);margin:30px 0}
.tw{overflow:auto;margin:0 0 20px;border:1px solid var(--line);border-radius:7px;background:var(--panel)}
table{border-collapse:collapse;width:100%;font-size:14px}
th,td{padding:8px 12px;border-bottom:1px solid var(--line);vertical-align:top;line-height:1.65}
th{background:#efece6;font-weight:650;white-space:nowrap;text-align:left}
tbody tr:last-child td{border-bottom:none}
tbody tr:nth-child(even){background:#fbfaf7}
td.right,th.right{text-align:right}
td.center,th.center{text-align:center}
td code{font-size:12.5px}
#top{position:fixed;right:26px;bottom:26px;background:var(--ink);color:#fff;border:none;
  width:42px;height:42px;border-radius:50%;cursor:pointer;font-size:17px;opacity:.82;display:none}
#top:hover{opacity:1}
@media (max-width:1080px){
  #wrap{padding:0}
  #toc{display:none}
  main{padding:22px 18px 90px}
  #cover h1{font-size:28px}
}
@media print{
  #toc,#top{display:none}
  body{background:#fff;font-size:10.5pt}
  main{padding:0}
  h1{page-break-before:always;border-top:none}
  h1:first-of-type{page-break-before:avoid}
  pre.code{background:#f4f4f4;color:#000;border:1px solid #ccc}
  .tw{page-break-inside:avoid}
}
"""

JS = """
(function(){
  var q=document.getElementById('q'), links=[].slice.call(document.querySelectorAll('#toc a'));
  if(q){q.addEventListener('input',function(){
    var k=q.value.trim().toLowerCase();
    links.forEach(function(a){ a.style.display = (!k || a.textContent.toLowerCase().indexOf(k)>=0) ? '' : 'none'; });
  });}
  var b=document.getElementById('top');
  window.addEventListener('scroll',function(){ b.style.display = window.scrollY>600 ? 'block':'none'; });
  b.addEventListener('click',function(){ window.scrollTo({top:0,behavior:'smooth'}); });
})();
"""


def main() -> None:
    files = chapter_files()
    body_parts = []
    toc_parts = []
    stats = []
    for key, fn in files:
        md = open(os.path.join(BOOK, fn), encoding="utf-8").read()
        html_text, toc = convert(md, key)
        body_parts.append(f'<section class="chapter" data-key="{key}">{html_text}</section>')
        for level, text, cid in toc:
            toc_parts.append(f'<a class="lv{level}" href="#{cid}">{html.escape(text)}</a>')
        stats.append((key, len(md.split("\n"))))

    today = datetime.date.today().isoformat()
    total_lines = sum(s[1] for s in stats)
    cover = f"""<header id="cover">
  <div class="kicker">Technical Design &amp; Implementation</div>
  <h1>E2E 智能测试引擎<br/>技术设计与实现</h1>
  <p class="sub">从需求到报告的五个阶段、三层执行通道与三十万行代码背后的设计决策</p>
  <dl>
    <dt>章节</dt><dd>34 章正文 + 4 个附录，共 {len(files)} 篇</dd>
    <dt>覆盖代码</dt><dd>src/ 下 1,163 个 Python 文件，约 29.2 万行</dd>
    <dt>正文规模</dt><dd>{total_lines:,} 行 Markdown 源</dd>
    <dt>成书日期</dt><dd>{today}</dd>
    <dt>生成方式</dt><dd>源码逐模块精读；文件索引与数据表清单由 AST 脚本机械提取</dd>
  </dl>
</header>"""

    doc = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>E2E 智能测试引擎 · 技术设计与实现</title>
<style>{CSS}</style></head>
<body>
<div id="wrap">
<nav id="toc"><h2>目录</h2><input id="q" type="search" placeholder="筛选章节…"/>
{chr(10).join(toc_parts)}
</nav>
<main>{cover}
{chr(10).join(body_parts)}
</main>
</div>
<button id="top" title="回到顶部">↑</button>
<script>{JS}</script>
</body></html>"""

    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write(doc)
    print(f"输出 {OUT}")
    print(f"章节 {len(files)} 个，正文源 {total_lines:,} 行，HTML {len(doc):,} 字节")


if __name__ == "__main__":
    main()
