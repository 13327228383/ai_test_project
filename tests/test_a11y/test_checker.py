# -*- coding: utf-8 -*-
"""单元测试：a11y 通用检查器（src/extensions/a11y/checker.py）。

覆盖全部规则（R1-R11），重点验证 R9 嵌套交互元素 / R10 表格标题 /
R11 表头 scope（结构增强的父栈采集回归），并保证既有规则不回退。
"""

import pytest

from src.extensions.a11y.checker import analyze_html


def _rules(html: str):
    return [f.rule for f in analyze_html(html)]


# ── R9：嵌套交互元素 ─────────────────────────────────────────────
def test_nested_button_inside_a():
    rules = _rules('<a href="/x"><button>确定</button></a>')
    assert "nested-interactive" in rules


def test_nested_a_inside_button():
    rules = _rules('<button><a href="/x">链接</a></button>')
    assert "nested-interactive" in rules


def test_sibling_interactives_not_nested():
    # 同级不嵌套 → 不触发
    rules = _rules('<a href="/x">再试</a><button>提交</button>')
    assert "nested-interactive" not in rules


# ── R10：数据表格缺标题 ──────────────────────────────────────────
def test_table_missing_caption():
    rules = _rules('<table><tr><td>1</td></tr></table>')
    assert "table-missing-caption" in rules


def test_table_with_aria_label_ok():
    rules = _rules('<table aria-label="价格表"><tr><td>1</td></tr></table>')
    assert "table-missing-caption" not in rules


# ── R11：表头缺 scope/headers ────────────────────────────────────
def test_th_missing_scope():
    rules = _rules("<table><tr><th>名称</th></tr></table>")
    assert "th-missing-scope" in rules


def test_th_with_scope_ok():
    rules = _rules('<table><tr><th scope="col">名称</th></tr></table>')
    assert "th-missing-scope" not in rules


# ── 回归：既有规则不回退 ─────────────────────────────────────────
def test_existing_rules_still_fire():
    html = (
        '<img src="x.png">'                       # R1 img-alt-missing
        '<button type="button"></button>'          # R4 button-empty
        '<h1>标题</h1><h3>三级</h3>'               # R5 heading-skip
        '<div id="dup"></div><span id="dup"></span>'  # R8 duplicate-id
    )
    rules = _rules(html)
    assert "img-alt-missing" in rules
    assert "button-empty" in rules
    assert "heading-skip" in rules
    assert "duplicate-id" in rules


def test_parent_stack_does_not_break_text_collection():
    # 父栈引入后，button 文本应仍能正确收集（node 不误判为 empty）
    html = '<a href="/x"><button>保存并继续</button></a>'
    findings = analyze_html(html)
    # 不应因嵌套把 button 误报为 button-empty（它有文本）
    assert "button-empty" not in [f.rule for f in findings]


# ── R10 增强：<caption> 子元素应被识别为表格标题 ─────────────────
def test_table_with_caption_ok():
    rules = _rules("<table><caption>价格表</caption><tr><td>1</td></tr></table>")
    assert "table-missing-caption" not in rules


def test_table_caption_and_aria_both_count():
    rules = _rules('<table title="统计表"><tr><td>1</td></tr></table>')
    assert "table-missing-caption" not in rules


# ── R12：iframe 缺 title/aria-label ─────────────────────────────
def test_iframe_missing_title():
    rules = _rules('<iframe src="https://x.example/a"></iframe>')
    assert "iframe-no-title" in rules


def test_iframe_with_title_ok():
    rules = _rules('<iframe src="https://x.example/a" title="地图"></iframe>')
    assert "iframe-no-title" not in rules


def test_iframe_with_aria_label_ok():
    rules = _rules('<iframe src="https://x.example/a" aria-label="地图"></iframe>')
    assert "iframe-no-title" not in rules


# ── R13：文档根 html 缺 lang ────────────────────────────────────
def test_html_missing_lang():
    rules = _rules('<html><body>内容</body></html>')
    assert "html-lang-missing" in rules


def test_html_with_lang_ok():
    rules = _rules('<html lang="zh-CN"><body>内容</body></html>')
    assert "html-lang-missing" not in rules


# ── R14：aria-hidden 的可聚焦交互元素 ───────────────────────────
def test_aria_hidden_focusable_button():
    rules = _rules('<button aria-hidden="true">提交</button>')
    assert "aria-hidden-focusable" in rules


def test_aria_hidden_focusable_link():
    rules = _rules('<a href="/x" aria-hidden="true">去往</a>')
    assert "aria-hidden-focusable" in rules


def test_aria_hidden_non_interactive_ok():
    # 纯装饰若不需要聚焦则不触发（此处为普通 div）
    rules = _rules('<div aria-hidden="true">装饰</div>')
    assert "aria-hidden-focusable" not in rules


# ── R15：有标题结构但缺 h1 ──────────────────────────────────────
def test_headings_without_h1_flagged():
    rules = _rules("<h2>章节</h2><h3>小节</h3>")
    assert "heading-no-h1" in rules


def test_has_h1_ok():
    rules = _rules("<h1>主标题</h1><h2>章节</h2>")
    assert "heading-no-h1" not in rules


# ── R16：文档丢失 <title> ──────────────────────────────────────
def test_title_missing_flagged():
    rules = _rules('<html><body><h1>页</h1></body></html>')
    assert "title-missing" in rules


def test_title_empty_flagged():
    rules = _rules('<html><head><title></title></head><body></body></html>')
    assert "title-missing" in rules


def test_title_present_ok():
    rules = _rules('<html><head><title>商品列表</title></head><body></body></html>')
    assert "title-missing" not in rules


def test_fragment_without_body_no_title_check():
    # 无 body 的 HTML 片段不触发文档级 title 检查（避免误报测试片段）
    rules = _rules('<img src="x.png">')
    assert "title-missing" not in rules


# ── R17：缺 main 地标 ──────────────────────────────────────────
def test_missing_main_flagged():
    rules = _rules('<html><body><p>内容</p></body></html>')
    assert "missing-main" in rules


def test_main_present_ok():
    rules = _rules('<!doctype html><html><body><main><h1>页</h1></main></body></html>')
    assert "missing-main" not in rules


# ── R18：孤悬 label ────────────────────────────────────────────
def test_orphan_label_flagged():
    rules = _rules('<label for="no-such-id">名称</label>')
    assert "orphan-label" in rules


def test_label_with_resolved_control_ok():
    rules = _rules('<label for="name">名称</label><input id="name">')
    assert "orphan-label" not in rules


# ── R19：空标题 heading ────────────────────────────────────────
def test_heading_empty_flagged():
    rules = _rules("<h2></h2>")
    assert "heading-empty" in rules


def test_heading_with_text_ok():
    rules = _rules("<h2>有文字</h2>")
    assert "heading-empty" not in rules


# ── R3 放宽：可访问名（aria-label/labelledby/title）不再误报 ─────
def test_input_with_aria_label_no_longer_reported():
    rules = _rules('<input aria-label="用户名">')
    assert "input-no-label" not in rules


def test_input_with_title_name_ok():
    rules = _rules('<input title="搜索">')
    assert "input-no-label" not in rules


def test_input_with_labelledby_ok():
    rules = _rules('<span id="lbl">用户名</span><input aria-labelledby="lbl">')
    assert "input-no-label" not in rules


def test_unlabelled_input_still_reported():
    rules = _rules('<input>')
    assert "input-no-label" in rules


# ── R20：损坏的 aria 引用 ───────────────────────────────────────
def test_aria_broken_ref_flagged():
    rules = _rules('<div aria-controls="no-panel">开关</div>')
    assert "aria-broken-ref" in rules


def test_aria_labelledby_missing_flagged():
    rules = _rules('<input aria-labelledby="ghost">')
    assert "aria-broken-ref" in rules


def test_aria_ref_resolved_ok():
    rules = _rules('<div id="p">说明</div><input aria-describedby="p">')
    assert "aria-broken-ref" not in rules


# ── R21：音视频无字幕轨道 ──────────────────────────────────────
def test_video_without_caption_flagged():
    rules = _rules('<video src="m.mp4"></video>')
    assert "media-no-caption" in rules


def test_video_with_caption_track_ok():
    rules = _rules('<video src="m.mp4"><track kind="captions" src="c.vtt"></video>')
    assert "media-no-caption" not in rules


# ── R22：正 tabindex 破坏 Tab 序 ───────────────────────────────
def test_positive_tabindex_flagged():
    rules = _rules('<button tabindex="3">跳过</button>')
    assert "tabindex-positive" in rules


def test_zero_or_negative_tabindex_ok():
    rules = _rules('<button tabindex="0">正常</button><button tabindex="-1">聚焦点</button>')
    assert "tabindex-positive" not in rules