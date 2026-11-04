# -*- coding: utf-8 -*-
"""_control_dom_utils.py — DOM 文本解析 + URL 规范化纯工具函数。

从 _control_validation.py 拆分（§4.3 单文件≤800行）。

职责：
  - 解析 page_dom_snapshots.dom_summary 文本为结构化元素列表
  - 控件文本核心关键词提取与匹配
  - URL 标准化 / 参数排序 / base+tab 拆分

特点：纯函数，无 DB 访问、无副作用、无外部状态依赖。
通用性：适用于所有系统/行业/页面（DOM 文本与 URL 是通用数据结构）。
"""
from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import parse_qs, unquote, urlencode, urlparse, urlunparse

_log = logging.getLogger(__name__)

# 控件文本核心关键词提取：去掉括号内补充说明、特定值等噪声
_CLEAN_PAREN_RE = re.compile(r"[（(].*?[）)]")


def _extract_core_keywords(hint: str) -> list[str]:
    """从 target_hint 中提取可用于 DOM 匹配的核心关键词。"""
    if not hint:
        return []
    # 去掉括号补充说明，如"保存按钮(调用/create)" → "保存按钮"
    cleaned = _CLEAN_PAREN_RE.sub("", hint).strip()
    # 按常见分隔符切分，保留长度 >= 2 的片段
    parts = re.split(r"[\s\-_/|\\]+", cleaned)
    keywords = []
    for p in parts:
        p = p.strip()
        if len(p) >= 2:
            keywords.append(p)
    # 去重并保持顺序
    seen = set()
    result = []
    for k in keywords:
        if k not in seen:
            seen.add(k)
            result.append(k)
    return result


def _parse_dom_summary(summary: str) -> list[dict[str, Any]]:
    """★ 通用修复(2026-07-21 v18): 解析 page_dom_snapshots.dom_summary 文本为元素列表。

    根因(3层追问):
      1. 表层: _control_exists('新建配方', elements) 返回 False，导致用例降级
      2. 中层: dom_summary 中 button 元素的 text 为空（Ant Design button 用 span 嵌套，
              Step1 爬取时未提取 span 内文本）
      3. 根因: Step1 为了弥补 button text 为空，添加了 [PAGE_ELEMENTS] 行汇总真实按钮列表，
              但 Step3 的 _parse_dom_summary 只解析 [N] tag=xxx 格式，不解析 [PAGE_ELEMENTS] 行

    通用性修复:
      - 解析 [PAGE_ELEMENTS] 行的 BUTTONS 列表，转为 tag=button 的元素
      - 解析 [PAGE_ELEMENTS] 行的 INPUTS 列表，转为 tag=input 的元素（带 placeholder）
      - 解析 [PAGE_ELEMENTS] 行的 TABS 列表，转为 role=tab 的元素
      - 适用于所有系统（Ant Design/ElementUI/原生HTML等 UI 框架的 button 都能用）

    覆盖性: 影响所有通过 _parse_dom_summary 解析的用例（即所有 validate_control_existence 调用）

    合理性: 追根溯源到 Step1 爬取和 Step3 解析的格式不一致缺陷。
    """
    elements = []
    if not summary:
        return elements
    for line in summary.strip().splitlines():
        line = line.strip()
        if not line.startswith("["):
            continue
        # ★ v18: 解析 [PAGE_ELEMENTS] 行（Step1 爬取时汇总的真实按钮/输入框/Tab 列表）
        #   格式: [PAGE_ELEMENTS] INPUTS: item1, item2 | BUTTONS: item1, item2 | TABS: item1, item2
        #   item 格式: "文本" 或 "(placeholder)" 或 "文本(placeholder)" 或 "(active)" 等
        if line.startswith("[PAGE_ELEMENTS]"):
            _parse_page_elements_line(line, elements)
            continue
        # 格式: [idx] tag=<tag> | text=<text> | role=<role> | ...
        m = re.match(r"\[\d+\]\s+(.+)", line)
        if not m:
            continue
        rest = m.group(1)
        item: dict[str, Any] = {}
        for part in rest.split(" | "):
            if "=" in part:
                k, v = part.split("=", 1)
                item[k.strip()] = v.strip()
        if item.get("tag"):
            elements.append(item)
    return elements


def _parse_page_elements_line(line: str, elements: list[dict[str, Any]]) -> None:
    """★ v18 新增: 解析 [PAGE_ELEMENTS] 行，提取 BUTTONS/INPUTS/TABS 为元素。

    通用性: 适用于所有系统的 [PAGE_ELEMENTS] 格式（Step1 爬取时统一生成）。
    合理性: 弥补 [N] tag=button text='' 的缺陷（Ant Design/ElementUI 等 UI 框架的 button 用 span 嵌套）。

    格式示例:
      [PAGE_ELEMENTS] INPUTS: (search), 请搜索叶组名称(text), (checkbox) | BUTTONS: , 批量打标, 新建配方 | TABS: 叶组配方, 辅材配方(active)

    解析规则:
      - BUTTONS: 每个逗号分隔项是一个按钮文本（可能为空），转为 {tag: "button", text: "按钮文本", role: "button"}
      - INPUTS: 每个逗号分隔项是 "文本(placeholder)" 或 "(placeholder)" 或 "文本"，转为 {tag: "input", placeholder: "placeholder", text: "文本"}
      - TABS: 每个逗号分隔项是 "Tab名" 或 "Tab名(active)"，转为 {tag: "div", role: "tab", text: "Tab名"}
    """
    # 用 | 分隔各部分
    parts = line.split("|")
    for part in parts:
        part = part.strip()
        if not part:
            continue
        # 找到 "KEY:" 分隔符
        for section_key in ("INPUTS:", "BUTTONS:", "TABS:"):
            if part.startswith(section_key):
                items_str = part[len(section_key) :].strip()
                if not items_str:
                    break
                # 用逗号分隔，但要注意 "文本(placeholder)" 中的逗号不应分隔
                # 简化处理：按逗号分隔，然后 trim
                # 对于 "请搜索叶组名称(text)" 这样的整体，不会有内部逗号
                items = [s.strip() for s in items_str.split(",")]
                if section_key == "BUTTONS:":
                    for item_text in items:
                        if item_text:  # 跳过空文本按钮
                            elements.append(
                                {
                                    "tag": "button",
                                    "text": item_text,
                                    "role": "button",
                                }
                            )
                elif section_key == "INPUTS:":
                    for item_text in items:
                        if not item_text:
                            continue
                        # 解析 "文本(placeholder)" 或 "(placeholder)" 或 "文本"
                        m = re.match(r"^(.*?)\(([^)]*)\)$", item_text)
                        if m:
                            text_part = m.group(1).strip()
                            placeholder_part = m.group(2).strip()
                            el: dict[str, Any] = {"tag": "input"}
                            if text_part:
                                el["text"] = text_part
                            if placeholder_part:
                                el["placeholder"] = placeholder_part
                            elements.append(el)
                        else:
                            # 无括号，纯文本（如 "请搜索叶组名称(text)" 已被上面处理）
                            elements.append({"tag": "input", "text": item_text})
                elif section_key == "TABS:":
                    for item_text in items:
                        if not item_text:
                            continue
                        # 解析 "Tab名(active)" 或 "Tab名"
                        m = re.match(r"^(.*?)\(([^)]*)\)$", item_text)
                        if m:
                            tab_name = m.group(1).strip()
                        else:
                            tab_name = item_text.strip()
                        if tab_name:
                            elements.append(
                                {
                                    "tag": "div",
                                    "role": "tab",
                                    "text": tab_name,
                                }
                            )
                break


def _element_matches(element: dict[str, Any], keyword: str) -> bool:
    """判断单个元素是否匹配关键词（支持 text/placeholder/ariaLabel/role/value）。"""
    kw = keyword.lower()
    fields = [
        element.get("text", ""),
        element.get("placeholder", ""),
        element.get("ariaLabel", ""),
        element.get("aria_label", ""),
        element.get("role", ""),
        element.get("value", ""),
        element.get("title", ""),
    ]
    for field in fields:
        if not field:
            continue
        text = str(field).lower()
        if kw in text or text in kw:
            return True
    return False


# 交互型元素标签/角色：用于 click 等动作的控件校验，排除纯文本容器 div/span
_INTERACTIVE_TAGS = frozenset({"button", "a", "input", "select", "textarea", "label"})
_INTERACTIVE_ROLES = frozenset(
    {
        "button",
        "link",
        "menuitem",
        "tab",
        "option",
        "checkbox",
        "radio",
        "switch",
        "combobox",
        "listbox",
        "textbox",
        "searchbox",
    }
)

# ★ 表单控件标签/角色：仅用于 fill/select_option 动作的控件校验
#   根因(§10.9 5层追问): TC-REQ-018-N01 fill target "【修改配置】 所属模块：模型 输入框"
#   中的"模型"被 _element_matches 匹配到菜单项"模型管理" → 误判控件存在 → fill失败 → ERROR
#   修复: fill/select_option 仅匹配表单控件(input/select/textarea)，排除菜单项/按钮等非表单元素
#   通用性: 跨系统/行业/页面通用，所有表单控件标签/角色一致
_FORM_CONTROL_TAGS = frozenset({"input", "select", "textarea"})
_FORM_CONTROL_ROLES = frozenset(
    {"textbox", "searchbox", "combobox", "listbox", "spinbutton"}
)


def _is_interactive(element: dict[str, Any]) -> bool:
    """判断元素是否可交互（按钮、链接、输入框、tab 等）。"""
    tag = str(element.get("tag", "")).strip().lower()
    if tag in _INTERACTIVE_TAGS:
        return True
    role = str(element.get("role", "") or element.get("aria_role", "")).strip().lower()
    if role in _INTERACTIVE_ROLES:
        return True
    return False


def _is_form_control(element: dict[str, Any]) -> bool:
    """判断元素是否为表单控件（input/select/textarea），用于 fill/select_option 校验。"""
    tag = str(element.get("tag", "")).strip().lower()
    if tag in _FORM_CONTROL_TAGS:
        return True
    role = str(element.get("role", "") or element.get("aria_role", "")).strip().lower()
    if role in _FORM_CONTROL_ROLES:
        return True
    return False


def _control_exists(
    target_hint: str, elements: list[dict[str, Any]], interactive_only: bool = False
) -> bool:
    """判断 target_hint 是否在元素列表中存在匹配。

    Args:
        interactive_only: 仅匹配可交互元素（用于 click 等需要用户操作的动作）
    """
    if not target_hint or not elements:
        return False
    keywords = _extract_core_keywords(target_hint)
    if not keywords:
        return False
    for kw in keywords:
        for el in elements:
            if interactive_only and not _is_interactive(el):
                continue
            if _element_matches(el, kw):
                return True
    return False


def _normalize_url(url: str) -> str:
    """★ 通用修复(2026-07-21 v18): 标准化 URL 用于匹配：解码、去除 host、参数 key 排序、小写。

    根因(3层追问):
      1. 表层: REQ-003 等201条用例全部降级，_control_validation 报告 "click控件缺失"
      2. 中层: url_index.get(norm_nav, []) 返回空 → current_elements=[] → 所有控件判 missing
      3. 根因: _normalize_url 不去除 host，快照 page_url 是绝对URL
              (http://<target-host>:<port>/path)，用例 navigate URL 是相对URL (/path)，
              标准化后两者 host 部分不一致 → 永远不匹配 → 所有用例降级

    通用性:
      - 跨系统通用: 任何系统的快照URL(绝对)与用例URL(相对)都能匹配
      - 跨场景通用: 适用于所有"绝对URL vs 相对URL"的场景
      - 兼容性: 标准化后都是相对URL形式，统一匹配基准

    覆盖性: 影响所有 url_index 查找逻辑（validate_control_existence 主流程 +
            _find_alternative_tab + _find_alternative_page）

    合理性: 追根溯源到 URL 格式不一致的缺陷，从根源消除不匹配。
    """
    if not url:
        return ""
    url = unquote(url).strip()
    parsed = urlparse(url)
    # 对 query 参数按 key 排序
    qs = parse_qs(parsed.query, keep_blank_values=True)
    sorted_qs = sorted(qs.items(), key=lambda x: x[0])
    encoded = urlencode(sorted_qs, doseq=True)
    # ★ v18: 去除 host 部分，只保留 path+query，统一为相对URL形式
    #   原因: 快照 page_url 是绝对URL，用例 navigate URL 是相对URL，
    #         两者 host 部分不一致导致 url_index 查找失败。
    #   通用性: 适用于所有系统，不依赖具体 host。
    parsed = parsed._replace(scheme="", netloc="", query=encoded)
    return urlunparse(parsed).lower()


def _sort_url_params_preserve_case(url: str) -> str:
    """对 URL 参数按 key 排序，但保持原始大小写（用于返回给浏览器）。"""
    if not url:
        return ""
    url = unquote(url).strip()
    parsed = urlparse(url)
    qs = parse_qs(parsed.query, keep_blank_values=True)
    if not qs:
        return url
    sorted_qs = sorted(qs.items(), key=lambda x: x[0])
    encoded = urlencode(sorted_qs, doseq=True)
    return urlunparse(parsed._replace(query=encoded))


def _split_base_and_tab(url: str) -> tuple[str, str]:
    """拆分 URL 为基础路径和 tab 参数值，用于寻找同页面不同 tab。

    base 仅使用 URL path（不含 query），避免实例参数（如 yzCode/materialCode）
    导致同一页面不同 tab 被误判为不同基础路径。控件存在性会在候选 tab
    上通过 _control_exists_for_action 二次校验，防止跨 menuKey 错误切换。
    """
    if not url:
        return ("", "")
    parsed = urlparse(unquote(url).strip())
    qs = parse_qs(parsed.query, keep_blank_values=True)
    tab = ""
    for key in ("tab", "activeTab", "currentTab", "tabKey"):
        if key in qs:
            tab = qs[key][0]
            break
    # 仅使用 path 作为 base，保证同一路由不同 tab 被识别为 sibling
    base = parsed.path.lower().strip("/")
    return base, tab.lower()
