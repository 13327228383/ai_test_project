# -*- coding: utf-8 -*-
"""★ P2-9 CoverageCrawl — 覆盖驱动探索接口（仅接口，先小样本证伪）。

定位：
  替代/补充菜单驱动爬取。菜单驱动只爬菜单项指向的页面，漏掉非菜单入口
  （页面内按钮 / Tab / 链接）。CoverageCrawl 以「交互元素覆盖」为驱动：
  跟踪每个页面可交互元素是否已被"探索"（点击→爬到目标页），
  feedback() 返回未覆盖元素作为下一步爬取目标。

接口（P2 仅接口）：
  - CoverageMap: 覆盖地图数据结构（页面 → 元素 → 是否已探索）
  - feedback(coverage_map) -> list[uncovered_target]: 返回未覆盖交互元素
  - build_from_dom_snapshots(...): 从 page_dom_snapshots 初始化覆盖地图
  - mark_exercised(...): 标记元素已探索（点击后爬到目标页）

通用性（§12.4）：
  1. 跨系统：元素选择器用通用语义（text=/placeholder=/ariaLabel=），不含业务术语
  2. 跨行业：任何 web 应用都有可交互元素
  3. 跨页面：列表页/详情页/表单页/Tab 页均适用
  4. 降级：无元素时 feedback() 返回空列表，不阻断
  5. 职责边界：仅做覆盖决策（返回目标），不执行爬取（执行由 Step1 爬取器负责）

证伪设计（小样本）：
  在现有 page_dom_snapshots（5 页）上构建 coverage_map，验证 feedback() 能识别
  未覆盖的交互元素。若 feedback() 返回非空 → 接口可用（未证伪）。
  全量证伪（是否比菜单驱动爬到更多页）需实跑目标系统 → 属人类决策点。
"""
from __future__ import annotations

import logging
import re
from typing import Any

_log = logging.getLogger(__name__)

# 可交互元素 tag 集合（通用，不含业务术语）
_INTERACTIVE_TAGS = frozenset({"button", "a", "input", "select", "textarea"})
# 可交互 role 集合
_INTERACTIVE_ROLES = frozenset(
    {
        "menuitem",
        "link",
        "button",
        "tab",
        "treeitem",
        "option",
        "checkbox",
        "radio",
    }
)
# input 类型中可交互的（排除 hidden/password 等纯输入）
_INTERACTIVE_INPUT_TYPES = frozenset(
    {
        "button",
        "submit",
        "reset",
        "image",
        "checkbox",
        "radio",
    }
)

# dom_summary 行格式正则：[N] tag=xxx | text=xxx | ...
_DOM_LINE_RE = re.compile(r"^\[(\d+)\]\s*(.*)$")
_KV_RE = re.compile(r"(\w+)=(.*?)(?=\s*\|\s*|$)")

# ★ R1 收尾(2026-08-05) [已复核-DeepSeek-V4-Flash 2026-08-05]：
#   P0-1 读侧迁移后，dom_summary 的生产方换成了
#   `nav_graph_read.build_dom_summary`，产出的是聚合行
#       [PAGE_ELEMENTS] INPUTS: a, b | BUTTONS: c | LINKS: d | TABS: e
#   而本模块的消费方只认旧的逐元素行 `[N] tag=xxx | text=xxx`，
#   两者对不上 → 每个页面都解析出 0 个元素 → 覆盖地图恒空、mark_exercised 恒失败。
#   下面补上聚合行的解析，两种格式并存（旧格式逻辑原样保留）。
_PAGE_ELEMENTS_PREFIX = "[PAGE_ELEMENTS]"

# 聚合行分组名 → (tag, role)。未列出的分组（如 HEADINGS）不臆测其可交互性，直接忽略。
_GROUP_TAG_MAP: dict[str, tuple[str, str]] = {
    "INPUTS": ("input", ""),
    "BUTTONS": ("button", ""),
    "LINKS": ("a", "link"),
    "TABS": ("a", "tab"),
    "SELECTS": ("select", ""),
}

# inputs 在聚合行里渲染为 `文本(placeholder)` 或 `(placeholder)`
_INPUT_PLACEHOLDER_RE = re.compile(r"^(?P<text>.*?)\((?P<ph>[^()]*)\)$")


def _parse_page_elements_line(line: str, start_idx: int) -> list[dict[str, str]]:
    """解析 `[PAGE_ELEMENTS] GROUP: a, b | GROUP: c` 聚合行为元素列表。

    参数:
        line: 以 `[PAGE_ELEMENTS]` 开头的整行文本。
        start_idx: 本行首个元素的编号（与旧格式的 [N] 对齐，仅用于 idx 字段）。

    返回:
        [{idx, tag, text[, placeholder][, role]}, ...]；无法识别的分组被跳过。
    """
    body = line[len(_PAGE_ELEMENTS_PREFIX) :].strip()
    if not body:
        return []

    elements: list[dict[str, str]] = []
    idx = start_idx
    for segment in body.split("|"):
        segment = segment.strip()
        if ":" not in segment:
            continue
        group_raw, items_raw = segment.split(":", 1)
        tag, role = _GROUP_TAG_MAP.get(group_raw.strip().upper(), ("", ""))
        if not tag:
            continue  # fail-closed：未知分组不当作可交互元素
        for item in items_raw.split(","):
            item = item.strip()
            if not item:
                continue
            el: dict[str, str] = {"idx": str(idx), "tag": tag}
            if role:
                el["role"] = role
            matched = _INPUT_PLACEHOLDER_RE.match(item) if tag == "input" else None
            if matched:
                text = matched.group("text").strip()
                placeholder = matched.group("ph").strip()
                if text:
                    el["text"] = text
                if placeholder:
                    el["placeholder"] = placeholder
            else:
                el["text"] = item
            if el.get("text") or el.get("placeholder"):
                elements.append(el)
                idx += 1
    return elements


# _element_selector 生成的选择器前缀（顺序与其优先级一致）
_SELECTOR_PREFIXES: tuple[str, ...] = (
    "text=",
    "placeholder=",
    "ariaLabel=",
    "name=",
    "tag=",
)


def _normalize_selector(selector: str) -> str:
    """剥掉选择器的类型前缀，返回裸文本（用于跨来源比对）。

    ★ R1 收尾(2026-08-05) [已复核-DeepSeek-V4-Flash 2026-08-05]：
      覆盖地图的元素选择器是 `_element_selector` 生成的 "text=新建" 形态，
      而 nav_graph_edges.trigger_action.selector 存的是**裸文本**（sidebar /
      tab / link_text）。两者直接 == 比对永远不相等 →
      `build_from_nav_graph` 的 mark_exercised 从未成功标记过任何元素。
      归一化后再比对即可对齐，且仍是**全等**匹配（不做模糊/子串），
      不会把不同元素误标为已探索。

    参数:
        selector: 任意来源的选择器文本。

    返回:
        裸文本（无匹配前缀时原样返回，已 strip）。
    """
    text = (selector or "").strip()
    for prefix in _SELECTOR_PREFIXES:
        if text.startswith(prefix):
            return text[len(prefix) :].strip()
    return text


def _parse_dom_summary(dom_summary: str) -> list[dict[str, str]]:
    """解析 dom_summary 文本为元素列表。

    支持两种格式（生产方在 P0-1 读侧迁移后从前者切到了后者）：
      1. 逐元素行: [N] tag=xxx | text=xxx | placeholder=xxx | role=xxx | ...
      2. 聚合行:   [PAGE_ELEMENTS] INPUTS: a, b | BUTTONS: c | LINKS: d | TABS: e

    返回: [{idx, tag, text, placeholder, role, ariaLabel, name, type}, ...]
    通用性：纯文本解析，不依赖业务结构。
    """
    if not dom_summary:
        return []
    elements = []
    for line in dom_summary.split("\n"):
        line = line.strip()
        if line.startswith(_PAGE_ELEMENTS_PREFIX):
            elements.extend(_parse_page_elements_line(line, len(elements)))
            continue
        m = _DOM_LINE_RE.match(line)
        if not m:
            continue
        idx = m.group(1)
        rest = m.group(2)
        el: dict[str, str] = {"idx": idx}
        for k, v in _KV_RE.findall(rest):
            el[k] = v.strip()
        elements.append(el)
    return elements


def _is_interactive(el: dict[str, str]) -> bool:
    """判断元素是否为可交互元素（通用规则）。

    判定：tag 属可交互集合 OR role 属可交互集合 OR input[type] 属可交互类型。
    """
    tag = (el.get("tag") or "").lower()
    role = (el.get("role") or "").lower()
    if tag in _INTERACTIVE_TAGS:
        if tag == "input":
            itype = (el.get("type") or "").lower()
            # input 无 type 视为文本输入（可交互但非点击型）；有 type 且属点击型 → 可交互
            return (
                True
                if not itype
                else (itype in _INTERACTIVE_INPUT_TYPES or itype in ("text", "search"))
            )
        return True
    if role in _INTERACTIVE_ROLES:
        return True
    return False


def _element_selector(el: dict[str, str]) -> str:
    """生成元素的通用选择器 hint（与 op_cache target_hint 格式一致）。

    优先级：text > placeholder > ariaLabel > name > tag。
    通用性：不含业务术语，跨系统可复用。
    """
    text = (el.get("text") or "").strip()
    if text:
        return "text=%s" % text
    ph = (el.get("placeholder") or "").strip()
    if ph:
        return "placeholder=%s" % ph
    al = (el.get("ariaLabel") or "").strip()
    if al:
        return "ariaLabel=%s" % al
    name = (el.get("name") or "").strip()
    if name:
        return "name=%s" % name
    return "tag=%s" % (el.get("tag") or "").strip()


def _element_label(el: dict[str, str]) -> str:
    """生成人类可读标签（用于日志/报告）。"""
    return (
        el.get("text")
        or el.get("placeholder")
        or el.get("ariaLabel")
        or el.get("name")
        or el.get("tag")
        or ""
    ).strip()


# ═══════════════════════════════════════════════════════════════
# 覆盖地图
# ═══════════════════════════════════════════════════════════════


class CoverageMap:
    """页面交互元素覆盖地图。

    结构:
      pages: {page_url: {elements: [{selector, tag, text, role, exercised, leads_to}]}}
      crawled_urls: set[page_url]  # 已爬取的 URL
    """

    def __init__(self):
        self.pages: dict[str, dict[str, Any]] = {}
        self.crawled_urls: set[str] = set()

    def add_page(self, page_url: str, dom_summary: str, page_title: str = "") -> int:
        """添加一个已爬取页面及其交互元素。

        返回: 新增的交互元素数。
        通用性：从 dom_summary 解析，不依赖业务结构。
        """
        page_url = (page_url or "").strip()
        if not page_url:
            return 0
        self.crawled_urls.add(page_url)
        elements = _parse_dom_summary(dom_summary)
        interactive = []
        for el in elements:
            if not _is_interactive(el):
                continue
            selector = _element_selector(el)
            if not selector:
                continue
            interactive.append(
                {
                    "selector": selector,
                    "tag": (el.get("tag") or "").lower(),
                    "text": _element_label(el),
                    "role": (el.get("role") or "").lower(),
                    "exercised": False,  # 默认未探索
                    "leads_to": "",  # 点击后到达的页面（未知）
                }
            )
        # 去重（同 selector 只保留一个）
        seen = set()
        unique = []
        for el in interactive:
            if el["selector"] in seen:
                continue
            seen.add(el["selector"])
            unique.append(el)
        self.pages[page_url] = {
            "title": page_title,
            "elements": unique,
        }
        return len(unique)

    def mark_exercised(self, page_url: str, selector: str, leads_to: str = "") -> bool:
        """标记某页面的某元素为已探索（点击后爬到 leads_to 页面）。

        返回: True=标记成功, False=未找到元素。
        """
        page = self.pages.get(page_url)
        if not page:
            return False
        # ★ R1 收尾(2026-08-05)：selector 可能来自 nav_graph 边（裸文本）或覆盖地图
        #   自身（"text=xxx"），归一化后全等比对。fail-closed：空 selector 不标记。
        target = _normalize_selector(selector)
        if not target:
            return False
        for el in page["elements"]:
            if (
                el["selector"] == selector
                or _normalize_selector(el["selector"]) == target
            ):
                el["exercised"] = True
                if leads_to:
                    el["leads_to"] = leads_to
                return True
        return False

    def stats(self) -> dict[str, int]:
        """返回覆盖统计。"""
        total = 0
        exercised = 0
        for page in self.pages.values():
            for el in page["elements"]:
                total += 1
                if el["exercised"]:
                    exercised += 1
        return {
            "pages": len(self.pages),
            "crawled_urls": len(self.crawled_urls),
            "total_elements": total,
            "exercised_elements": exercised,
            "uncovered_elements": total - exercised,
        }


# ═══════════════════════════════════════════════════════════════
# 核心接口：feedback
# ═══════════════════════════════════════════════════════════════


def feedback(
    coverage_map: CoverageMap, *, max_targets: int = 50
) -> list[dict[str, str]]:
    """返回未覆盖的交互元素（下一步爬取目标）。

    策略：
      1. 遍历所有已爬取页面的交互元素
      2. 过滤 exercised=False 的元素
      3. 排除 leads_to 已在 crawled_urls 中的（避免重复爬取已知页面）
      4. 按 page_url 分组返回

    返回: [{page_url, selector, tag, text, role}, ...]
    通用性：纯算法，不调 AI，不依赖业务术语。

    参数:
      coverage_map: 覆盖地图
      max_targets: 最多返回目标数（防止爆炸）
    """
    targets: list[dict[str, str]] = []
    for page_url, page in coverage_map.pages.items():
        for el in page["elements"]:
            if el["exercised"]:
                continue
            # 如果元素 leads_to 已知且该页面已爬过 → 跳过
            leads_to = el.get("leads_to", "")
            if leads_to and leads_to in coverage_map.crawled_urls:
                continue
            targets.append(
                {
                    "page_url": page_url,
                    "selector": el["selector"],
                    "tag": el["tag"],
                    "text": el["text"],
                    "role": el["role"],
                }
            )
            if len(targets) >= max_targets:
                return targets
    return targets


# ═══════════════════════════════════════════════════════════════
# 从 DB 初始化覆盖地图（用于证伪 + 实际爬取初始化）
# ═══════════════════════════════════════════════════════════════


def build_from_dom_snapshots(project: str, version_label: str = "") -> CoverageMap:
    """从导航库页面元素构建覆盖地图。

    用于：
      1. 证伪：在现有页面元素上验证 feedback() 能识别未覆盖元素
      2. 实际爬取：菜单驱动爬完后，用此初始化 coverage_map，再调 feedback() 补爬

    ★ P0-1 读侧迁移(2026-08-05): page_dom_snapshots → nav_graph（read_dom_pages）。
      该表 0 行且**全仓无任何写入方**，原查询恒返回空 → 覆盖地图恒为空 →
      feedback() 认为"全部已覆盖"，补爬机制形同虚设。

    通用性：从 DB 读取结构化字段，不依赖业务结构。
    """
    cm = CoverageMap()
    try:
        from src.pipeline.utils.nav_graph_read import read_dom_pages

        for r in read_dom_pages(project, version_label or None):
            cm.add_page(
                r.get("page_url", "") or "",
                r.get("dom_summary", "") or "",
                r.get("page_title", "") or "",
            )
    except Exception as e:
        _log.error(
            "[CoverageCrawl] build_from_dom_snapshots 失败 project=%s version=%s，"
            "覆盖地图为空将导致补爬失效: %s",
            project,
            version_label,
            e,
            exc_info=True,
        )
    return cm


def build_from_nav_graph(project: str, version_label: str = "") -> CoverageMap:
    """从 nav_graph_nodes + nav_graph_edges 构建覆盖地图（含已探索标记）。

    nav_graph_edges 记录了 from_node→to_node 的导航关系，
    用于标记元素为 exercised=True（已探索）。

    ★ P0-1 读侧迁移(2026-08-05): 去掉 `LEFT JOIN page_dom_snapshots`。
      snapshot_id 指向的 page_dom_snapshots 已 0 行且无写入方，JOIN 恒得
      dom_summary=NULL → 每个页面都是"无元素"。现改用 nav_graph_nodes
      自身的结构化 page_elements 合成 dom_summary（同 read_dom_pages）。

    通用性：基于 nav_graph 通用结构，不依赖业务术语。
    """
    from src.models.base import get_conn

    cm = CoverageMap()
    conn = None
    try:
        from src.pipeline.utils.nav_graph_read import read_dom_pages

        # 1. 加载所有节点的页面元素
        for r in read_dom_pages(project, version_label or None):
            cm.add_page(
                r.get("page_url", "") or "",
                r.get("dom_summary", "") or "",
                r.get("page_title", "") or "",
            )
        conn = get_conn()
        # 2. 加载边，标记 exercised
        edges = conn.execute(
            "SELECT e.from_node, e.to_node, e.trigger_action, "
            "       nf.page_url AS from_url, nt.page_url AS to_url "
            "FROM nav_graph_edges e "
            "JOIN nav_graph_nodes nf ON e.from_node = nf.id "
            "JOIN nav_graph_nodes nt ON e.to_node = nt.id "
            "WHERE e.project=%s AND (%s='' OR e.version_label=%s)",
            (project, version_label, version_label),
        ).fetchall()
        for e in edges:
            from_url = e["from_url"] or ""
            to_url = e["to_url"] or ""
            trigger = e["trigger_action"]
            selector = ""
            if isinstance(trigger, str):
                try:
                    import json

                    tj = json.loads(trigger)
                    selector = (tj or {}).get("selector", "") or (tj or {}).get(
                        "value", ""
                    )
                except Exception as _je:
                    _log.debug(
                        "[CoverageCrawl] trigger JSON解析失败(用空selector): %s",
                        str(_je)[:60],
                    )
                    selector = ""
            if from_url and selector:
                cm.mark_exercised(from_url, selector, to_url)
    except Exception as e:
        _log.warning("[CoverageCrawl] build_from_nav_graph 失败: %s", str(e)[:120])
    finally:
        if conn:
            try:
                conn.close()
            except Exception as e:
                _log.debug("[CoverageCrawl] 关闭连接失败: %s", str(e)[:60])
    return cm
