# -*- coding: utf-8 -*-
"""可访问性（a11y）检查器 — 通用规则驱动（无第三方依赖，无 AI 依赖）。

规则集（可解释、可分级，规则驱动而非硬编码）：
  R1 img-alt-missing   <img> 缺 alt                    -> high
  R2 img-alt-empty     <img> alt 为空串                -> medium
  R3 input-no-label    表单控件无关联 label             -> high
  R4 button-empty      按钮无文本/aria-label            -> medium
  R5 heading-skip      heading 层级跳级（h1->h3 无 h2） -> low
  R6 a-no-href         <a> 缺 href（无法导航）          -> low
  R7 aria-label-empty  aria-label 为空串               -> medium
  R8 duplicate-id      页面内 id 重复                  -> low
  R9 nested-interactive 交互元素(<a>/<button>)嵌套于交互元素内 -> medium
  R10 table-missing-caption 数据表格无 <caption>/aria-label/title -> low
  R11 th-missing-scope  <th> 缺 scope/headers        -> low
  R12 iframe-no-title   <iframe> 缺 title/aria-label  -> medium
  R13 html-lang-missing 文档根 <html> 缺 lang          -> medium
  R14 aria-hidden-focusable aria-hidden 的可见交互元素 -> high
  R15 heading-no-h1     有标题层级但缺 h1               -> low
  R16 title-missing     文档缺 <title> 或 title 为空      -> medium
  R17 missing-main      有 body 但缺 <main> 地标          -> medium
  R18 orphan-label      label[for] 指向不存在的 id        -> medium
  R19 heading-empty     heading 无可见文本                -> low
  R20 aria-broken-ref   aria-*by/controls 指向不存在的 id -> medium
  R21 media-no-caption  <video>/<audio> 无字幕轨道        -> high
  R22 tabindex-positive 元素 tabindex>0 破坏自然 Tab 序     -> low

设计：html.parser 收集元素 + 后处理规则判定，输出确定性发现列表。
挂靠：src/extensions/a11y/（与 security_scanner / perf_baseline 并列）。
[已复核-DeepSeek-V3 2026-08-16]
[2026-08-29 增强：R10 补 <caption> 子元素检测；新增 R12-R15]
[2026-08-29 增强 2:新增 R16-R19(title/main 地标/orphan-label/空标题)]
[2026-08-29 增强 3:R3 放宽可访问名；新增 R20-R22]
"""

from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Optional
import logging

_log = logging.getLogger(__name__)

SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


@dataclass
class A11yFinding:
    """单条可访问性发现。"""

    rule: str
    severity: str  # high / medium / low
    element: str  # 元素标签名
    snippet: str = ""  # 定位片段（尽可能短）
    description: str = ""


#: 无子内容的 void 元素（不压入标签栈）
_VOID_TAGS = frozenset(
    {"area", "base", "br", "col", "embed", "hr", "img", "input",
     "link", "meta", "param", "source", "track", "wbr"}
)


# ──────────────────────────────────────────────────────────────────
# HTML 收集器（纯标准库，无依赖）
# ──────────────────────────────────────────────────────────────────
class _Collector(HTMLParser):
    """线性收集元素与关键上下文，供后处理规则判定。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.elements: list[dict] = []  # {tag, attrs, line, text, parent}
        self._text_stack: list[Optional[str]] = []  # 与元素栈对齐，累积 button/a 文本
        self._tag_stack: list[str] = []  # 开启标签栈（记录父级，供嵌套判定）

    # -- 基础 --
    def _push(
        self,
        tag: str,
        attrs,
        line: int,
        self_closing: bool = False,
        parent: Optional[str] = None,
    ) -> None:
        self.elements.append(
            {
                "tag": tag,
                "attrs": dict(attrs),
                "line": line,
                "text": "",
                "parent": parent,
            }
        )
        self._text_stack.append(
            "" if tag in ("button", "a", "title", "h1", "h2", "h3", "h4", "h5", "h6") else None
        )

    def _pop_text(self, tag: str) -> None:
        # 回填最近未闭合元素的文本（简化：text 记到元素本身）
        for el in reversed(self.elements):
            if el["tag"] == tag and not el["text"]:
                # 文本在 handle_data 里已累积到栈顶，这里已足够
                break
        if self._text_stack:
            self._text_stack.pop()

    def handle_starttag(self, tag: str, attrs) -> None:
        parent = self._tag_stack[-1] if self._tag_stack else None
        self._push(tag, attrs, self.getpos()[0], parent=parent)
        if tag not in _VOID_TAGS:
            self._tag_stack.append(tag)

    def handle_startendtag(self, tag: str, attrs) -> None:
        # <img/> <input/> 等自闭合（无子元素，直接收集）
        parent = self._tag_stack[-1] if self._tag_stack else None
        self._push(tag, attrs, self.getpos()[0], self_closing=True, parent=parent)
        if self._text_stack:
            self._text_stack.pop()

    def handle_endtag(self, tag: str) -> None:
        # 将累积文本写入该元素
        if self._text_stack:
            text = self._text_stack.pop()
            for el in reversed(self.elements):
                if el["tag"] == tag:
                    el["text"] = (el["text"] or "") + (text or "")
                    break
        # 弹出标签栈直到匹配该标签（忽略错配/隐式闭合，保持确定性）
        if tag in self._tag_stack:
            while self._tag_stack:
                if self._tag_stack.pop() == tag:
                    break

    def handle_data(self, data: str) -> None:
        if self._text_stack and self._text_stack[-1] is not None:
            self._text_stack[-1] += data


# ──────────────────────────────────────────────────────────────────
# 规则判定（后处理，确定性）
# ──────────────────────────────────────────────────────────────────
def _is_visible_form_control(tag: str, attrs: dict) -> bool:
    if tag not in ("input", "select", "textarea"):
        return False
    if tag == "input" and attrs.get("type", "text") in ("hidden", "submit", "button", "reset"):
        return False
    return True


def _judge(elements: list[dict]) -> list[A11yFinding]:
    findings: list[A11yFinding] = []
    ids: dict[str, int] = {}
    label_for: set[str] = set()

    # 第一遍：收集 id / label[for] / 场景标志
    has_caption_for: set[int] = set()  # 有 <caption> 子元素的 table 元素下标
    media_with_caption: set[int] = set()  # 有字幕 <track kind="captions"> 的 video/audio 下标
    title_text: str = ""  # 文档 <title> 文本
    for _i, el in enumerate(elements):
        a = el["attrs"]
        eid = a.get("id")
        if eid:
            ids[eid] = ids.get(eid, 0) + 1
        if el["tag"] == "label":
            f = a.get("for")
            if f:
                label_for.add(f)
        # R10 配套：记录 <caption> 的父 table 下标
        if el["tag"] == "caption":
            for _j in range(_i - 1, -1, -1):
                if elements[_j]["tag"] == "table":
                    has_caption_for.add(_j)
                    break
        # R21 配套：记录 <track kind="captions"> 的父媒体元素下标
        if el["tag"] == "track" and (a.get("kind") or "subtitles").strip().lower() == "captions":
            for _j in range(_i - 1, -1, -1):
                if elements[_j]["tag"] in ("video", "audio"):
                    media_with_caption.add(_j)
                    break
        # R16 配套：累积 <title> 文本
        if el["tag"] == "title":
            title_text = (el.get("text") or "").strip()

    headings: list[int] = []
    # R16-R18 文档级标志
    _has_main = any(e["tag"] == "main" for e in elements)
    _has_body = any(e["tag"] == "body" for e in elements)
    _title_missing = (
        not title_text and _has_body
    )  # 有实质文档内容但缺/空 title

    for _i, el in enumerate(elements):
        tag = el["tag"]
        a = el["attrs"]
        snippet = _snippet(tag, a)

        # R18: label[for] 指向不存在的 id（孤悬 label -> 关联失效）
        if tag == "label":
            _f = (a.get("for") or "").strip()
            if _f and not ids.get(_f):
                findings.append(A11yFinding(
                    "orphan-label", "medium", tag, snippet,
                    f"<label for=\"{_f}\"> 指向不存在的元素 id，读屏无法将标签关联到该字段"))

        if tag == "img":
            if "alt" not in a:
                findings.append(A11yFinding(
                    "img-alt-missing", "high", tag, snippet,
                    "图片缺少 alt 属性，屏幕阅读器无法描述图片内容"))
            elif not (a.get("alt") or "").strip():
                findings.append(A11yFinding(
                    "img-alt-empty", "medium", tag, snippet,
                    "图片 alt 为空字符串，信息型图片应提供替代文本"))

        elif _is_visible_form_control(tag, a):
            eid = a.get("id") or ""
            wrapped = False
            # 是否被 label 包裹（收集时简化：检查上一条 label 元素紧跟）
            if not label_for.intersection({eid}) and not eid:
                # 无 id 且无 for 关联：检查包裹（此处按保守判定）
                wrapped = True if _wrapped_by_label(elements, el) else False
            # 可访问名：label 关联 / 包裹，或 aria-label / aria-labelledby / title
            named = (a.get("aria-label") or "").strip() or (a.get("title") or "").strip()
            named = named or bool((a.get("aria-labelledby") or "").strip())
            if not (eid and eid in label_for) and not wrapped and not named:
                findings.append(A11yFinding(
                    "input-no-label", "high", tag, snippet,
                    "表单控件既无关联 <label>（label[for=id] 或包裹），也无 aria-label/labelledby/title 可访问名，键盘/读屏用户无法识别字段含义"))

        elif tag == "button":
            text = (el.get("text") or "").strip()
            if not text and not (a.get("aria-label") or "").strip() and not (a.get("title") or "").strip():
                findings.append(A11yFinding(
                    "button-empty", "medium", tag, snippet,
                    "按钮无可见文本且无 aria-label/title，无法表达操作含义"))

        elif tag == "a":
            if "href" not in a and not (a.get("id") or "").startswith("__"):
                findings.append(A11yFinding(
                    "a-no-href", "low", tag, snippet,
                    "链接缺少 href，无法作为可导航链接"))

        # R9: 嵌套交互元素（<a> 内 <button>、<a> 内 <a>、<button> 内 <a>）
        if tag in ("a", "button") and el.get("parent") in ("a", "button"):
            findings.append(A11yFinding(
                "nested-interactive", "medium", tag, snippet,
                f"交互元素 <{tag}> 嵌套于另一交互元素 <{el['parent']}> 内，"
                "读屏与键盘焦点会冲突（HTML 无效嵌套，应改为独立同级元素）"))

        # R10: 数据表格缺标题（caption / aria-label / title）
        elif tag == "table":
            if _i not in has_caption_for and not (
                (a.get("aria-label") or "").strip() or (a.get("title") or "").strip()
            ):
                findings.append(A11yFinding(
                    "table-missing-caption", "low", tag, snippet,
                    "数据表格缺少 <caption> / aria-label / title，无法说明表格用途"))

        # R11: 表头 <th> 缺 scope 或 headers
        elif tag == "th":
            if not (a.get("scope") or "").strip() and not (a.get("headers") or "").strip():
                findings.append(A11yFinding(
                    "th-missing-scope", "low", tag, snippet,
                    "表头 <th> 缺少 scope 或 headers 属性，读屏无法关联表格行列"))

        elif tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            headings.append(int(tag[1]))
            if not (el.get("text") or "").strip():
                findings.append(A11yFinding(
                    "heading-empty", "low", tag, snippet,
                    f"heading <{tag}> 无可见文本，读屏用户无法获得章节标题内容"))

        # R12: <iframe> 缺 title/aria-label（读屏无法说明内嵌内容）
        if tag == "iframe":
            if not ((a.get("title") or "").strip() or (a.get("aria-label") or "").strip()):
                findings.append(A11yFinding(
                    "iframe-no-title", "medium", tag, snippet,
                    "内嵌 <iframe> 缺少 title/aria-label，读屏用户无法得知内嵌内容用途"))

        # R20: aria-*by / aria-controls 指向不存在的 id（引用失效）
        for _attr, _attr_kind in (
            ("aria-labelledby", "aria-labelledby"),
            ("aria-describedby", "aria-describedby"),
            ("aria-controls", "aria-controls"),
            ("aria-owns", "aria-owns"),
        ):
            _ref = (a.get(_attr) or "").strip()
            if _ref:
                for _tid in _ref.split():
                    if not ids.get(_tid):
                        findings.append(A11yFinding(
                            "aria-broken-ref", "medium", tag, snippet,
                            f"{_attr_kind}=\"{_tid}\" 引用了页面不存在的元素 id，读屏关联/隐式关系将失效"))
                        break

        # R21: <video>/<audio> 无字幕轨道（WCAG 1.2.2 字幕）
        if tag in ("video", "audio"):
            if _i not in media_with_caption:
                findings.append(A11yFinding(
                    "media-no-caption", "high", tag, snippet,
                    f"{tag} 缺少 <track kind=\"captions\"> 字幕轨道，听障用户无法获取媒体听觉内容"))

        # R22: 元素 tabindex>0 破坏自然 Tab 序（应统一用 0 并靠文档顺序）
        _ti = a.get("tabindex")
        if _ti is not None:
            try:
                if int(_ti) > 0:
                    findings.append(A11yFinding(
                        "tabindex-positive", "low", tag, snippet,
                        "元素 tabindex>0 会跳出文档自然 Tab 顺序，破坏键盘聚焦的可预测性（建议用 0）"))
            except (ValueError, TypeError) as _e:
                _log.debug("_judge best-effort 容忍: %s", str(_e)[:120])

        # R13: 文档根 <html> 缺 lang
        if tag == "html" and el.get("parent") is None:
            if not (a.get("lang") or "").strip():
                findings.append(A11yFinding(
                    "html-lang-missing", "medium", tag, snippet,
                    "文档根 <html> 缺少 lang 属性，读屏无法确定页面语言"))

        # R14: aria-hidden 的可见交互元素（隐藏却可聚焦 → 焦点陷阱）
        if a.get("aria-hidden", "").strip().lower() != "false" and "aria-hidden" in a:
            if tag in ("button", "select", "textarea") or (
                tag == "a" and a.get("href")
            ) or (
                tag == "input"
                and a.get("type", "text") not in ("hidden", "submit", "button", "reset")
            ):
                findings.append(A11yFinding(
                    "aria-hidden-focusable", "high", tag, snippet,
                    "元素 aria-hidden 却仍可被聚焦（按钮/链接/表单控件），"
                    "会造成键盘焦点落在不可见内容上，应隐藏时移出 Tab 序"))

        # 通用：aria-label 空
        al = a.get("aria-label")
        if al is not None and not al.strip():
            findings.append(A11yFinding(
                "aria-label-empty", "medium", tag, snippet,
                "aria-label 为空字符串，起不到无障碍命名作用"))

        # 通用：重复 id
        eid = a.get("id")
        if eid and ids.get(eid, 0) > 1:
            findings.append(A11yFinding(
                "duplicate-id", "low", tag, snippet,
                f"页面存在重复 id='{eid}'，label/锚点关联会失效"))

    # heading 跳级
    prev = 0
    for lv in headings:
        if prev and lv > prev + 1:
            findings.append(A11yFinding(
                "heading-skip", "low", f"h{lv}", f"<h{lv}>",
                f"标题层级跳级：h{prev} 之后直接出现 h{lv}（应补 h{prev+1}）"))
        prev = lv
    # R15: 有标题结构但缺 h1
    if headings and 1 not in headings:
        findings.append(A11yFinding(
            "heading-no-h1", "low", "h1", "<h1>",
            "页面存在标题层级但缺少 <h1>，文档大纲不完整，读屏用户难以定位主内容起点"))
    # R16: 文档级标题缺失/为空（有 body 内容但无 <title>）
    if _title_missing:
        findings.append(A11yFinding(
            "title-missing", "medium", "title", "<title>",
            "文档页面缺少 <title> 或 title 为空，读屏无法告知用户当前页面主题"))
    # R17: 缺 <main> 地标（有 body 结构但无 main 助导航）
    if _has_body and not _has_main:
        findings.append(A11yFinding(
            "missing-main", "medium", "main", "<main>",
            "页面有正文但缺少 <main> 地标，读屏用户无法使用快捷方式跳到主体内容"))
    return findings


def _wrapped_by_label(elements: list[dict], el: dict) -> bool:
    """简化判定：元素紧随其前的 label 之后（包裹式 label 的常见形态）。"""
    idx = elements.index(el)
    if idx == 0:
        return False
    return elements[idx - 1]["tag"] == "label"


def _snippet(tag: str, attrs: dict) -> str:
    parts = [tag]
    for k in ("id", "name", "src", "class"):
        v = attrs.get(k)
        if v:
            parts.append(f'{k}="{v}"')
            break
    return "<%s>" % " ".join(parts)


# ──────────────────────────────────────────────────────────────────
# 对外接口
# ──────────────────────────────────────────────────────────────────
def analyze_html(html: str) -> list[A11yFinding]:
    """解析 HTML 并返回全部 a11y 发现（确定性）。"""
    parser = _Collector()
    try:
        parser.feed(html)
    except Exception:
        # 解析失败不静默吞：返回一条显式发现（fail-closed 哲学）
        return [A11yFinding(
            "html-parse-error", "high", "document", "",
            "HTML 解析失败，无法完成可访问性检查")]
    return _judge(parser.elements)


def run_a11y_check(html: str, source: str = "") -> dict:
    """标准入口：返回结构化检查结果。"""
    findings = analyze_html(html)
    by_sev: dict[str, int] = {}
    for f in findings:
        by_sev[f.severity] = by_sev.get(f.severity, 0) + 1
    return {
        "source": source,
        "total": len(findings),
        "by_severity": by_sev,
        "findings": [
            {
                "rule": f.rule,
                "severity": f.severity,
                "element": f.element,
                "snippet": f.snippet,
                "description": f.description,
            }
            for f in findings
        ],
    }
