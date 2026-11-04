# -*- coding: utf-8 -*-
"""
Figma 设计稿解析器 (FigmaParser)
================================
从 Figma REST API 返回的 document 树中提取关键 UI 结构信息，
生成可供 AI Prompt 使用的结构化文本描述。

核心功能：
1. 遍历 Figma document 节点树，识别 FRAME（页面）和 TEXT/COMPONENT（交互元素）
2. 提取按钮文字、输入框 label/placeholder、导航菜单结构
3. 输出紧凑的结构化文本，适合注入到 AI Prompt 中

使用方式：
    from src.pipeline.steps.step1.figma_parser import parse_figma_document
    ui_desc = parse_figma_document(figma_result["data"]["document"])
    # => "## 页面结构\n- 登录页: 用户名输入框(placeholder='...'), ...\n..."
"""

import logging

_log = logging.getLogger(__name__)


# ── 交互元素类型判断 ──
_INTERACTIVE_NODE_TYPES = {
    "FRAME",
    "COMPONENT",
    "INSTANCE",
    "TEXT",
    "RECTANGLE",
    "ELLIPSE",
    "LINE",
}

# ── 组件名称 → 语义标签映射 ──
_COMPONENT_NAME_HINTS = {
    "button": "按钮",
    "btn": "按钮",
    "input": "输入框",
    "textfield": "输入框",
    "dropdown": "下拉框",
    "select": "下拉框",
    "checkbox": "复选框",
    "radio": "单选框",
    "table": "表格",
    "tab": "标签页",
    "modal": "弹窗",
    "dialog": "弹窗",
    "toast": "提示",
    "menu": "菜单",
    "nav": "导航",
    "sidebar": "侧边栏",
    "breadcrumb": "面包屑",
    "search": "搜索框",
    "pagination": "分页",
    "upload": "上传",
    "form": "表单",
    "card": "卡片",
    "header": "页头",
    "footer": "页脚",
}


def _guess_element_type(name: str, node_type: str) -> str:
    """根据节点名称和类型推测交互元素类型。"""
    name_lower = name.lower() if name else ""
    for hint, label in _COMPONENT_NAME_HINTS.items():
        if hint in name_lower:
            return label
    if node_type == "TEXT":
        return "文本"
    if node_type in ("RECTANGLE", "ELLIPSE", "LINE"):
        return "容器"
    return "组件"


def _extract_text(node: dict) -> str:
    """递归提取节点及其子节点中的所有文本。"""
    texts = []
    if node.get("type") == "TEXT" and node.get("characters"):
        texts.append(node["characters"].strip())
    for child in node.get("children", []):
        texts.append(_extract_text(child))
    return " ".join(t for t in texts if t)


def _collect_frames(
    node: dict,
    depth: int = 0,
    max_depth: int = 4,
    penetration: int = 0,
    max_penetration: int = 12,
) -> list[dict]:
    """
    递归收集 FRAME 节点（代表页面/区域），提取其关键子元素。

    penetration 限制连续穿透非 FRAME/CANVAS 节点的层数，防止
    GROUP → GROUP → ... 深层嵌套导致栈溢出。
    """
    frames = []
    if depth > max_depth or penetration > max_penetration:
        return frames

    node_type = node.get("type", "")
    node_name = node.get("name", "").strip()

    if node_type in ("FRAME", "CANVAS") and node.get("children"):
        frame_info = {"name": node_name, "elements": []}
        _collect_interactive_elements(node, frame_info["elements"], 1, max_depth)
        # ★ Fix55a: 放开过滤 — 即使没有交互元素也保留 FRAME（记录布局结构）
        #   原逻辑: if frame_info["elements"]: frames.append(frame_info)
        #   问题: 纯布局 FRAME 被丢弃，连带深层子节点全部丢失
        frames.append(frame_info)
        for child in node.get("children", []):
            frames.extend(
                _collect_frames(child, depth + 1, max_depth, 0, max_penetration)
            )
    else:
        for child in node.get("children", []):
            frames.extend(
                _collect_frames(
                    child, depth, max_depth, penetration + 1, max_penetration
                )
            )

    return frames


def _collect_interactive_elements(
    node: dict, elements: list[dict], depth: int, max_depth: int
) -> None:
    """收集 FRAME 下的交互元素（按钮文字、输入框等）。"""
    if depth > max_depth or not node.get("children"):
        return

    for child in node.get("children", []):
        child_type = child.get("type", "")
        child_name = child.get("name", "").strip()

        guessed = _guess_element_type(child_name, child_type)

        # 提取文本内容
        text = _extract_text(child)

        # 提取 variant 属性（组件实例的当前状态）
        variants = {}
        variant_defs = {}
        if child_type in ("INSTANCE", "COMPONENT"):
            comp_props = child.get("componentProperties", {})
            if isinstance(comp_props, dict):
                for k, v in comp_props.items():
                    if isinstance(v, dict) and v.get("type") == "VARIANT":
                        variants[k] = v.get("value", "")
        if child_type == "COMPONENT_SET":
            prop_defs = child.get("componentPropertyDefinitions", {})
            if isinstance(prop_defs, dict):
                for k, v in prop_defs.items():
                    if isinstance(v, dict) and v.get("type") == "VARIANT":
                        variant_defs[k] = v.get("variantOptions", [])

        # ★ v5：从 componentProperties 提取文本（COMPONENT/INSTANCE 的文本可能存储在属性中而非子 TEXT 节点）
        if child_type in ("INSTANCE", "COMPONENT") and not text:
            comp_props = child.get("componentProperties", {})
            if isinstance(comp_props, dict):
                prop_texts = []
                for k, v in comp_props.items():
                    if isinstance(v, dict):
                        pv = v.get("value", "")
                        if isinstance(pv, str) and pv.strip():
                            prop_texts.append(pv.strip())
                    elif isinstance(v, str) and v.strip():
                        prop_texts.append(v.strip())
                if prop_texts:
                    text = " ".join(prop_texts)

        # ★ Fix55a: 放开过滤 — 有文本、或非容器、或 TEXT 类型、或有名称的节点都保留
        #   原逻辑: if text or guessed != "容器" or child_type == "TEXT":
        #   问题: 无文本但有名称的 RECTANGLE/GROUP 被丢弃，丢失布局信息
        if text or guessed != "容器" or child_type == "TEXT" or child_name:
            elem = {"text": text, "type": guessed}
            if child_name and child_name.lower() != text.lower():
                elem["name"] = child_name
            if variants:
                elem["variants"] = variants
            if variant_defs:
                elem["variant_options"] = variant_defs
            # 同名同类型元素加序号后缀，避免误去重丢失真实按钮
            same = [
                e
                for e in elements
                if e.get("text") == text and e.get("type") == guessed
            ]
            if same:
                cnt = len(same) + 1
                suffix = "（第%d个）" % cnt
                base = elem.get("name") or elem.get("text") or ""
                if base:
                    elem["name"] = base + suffix
                else:
                    elem["text"] = (text or "") + suffix
            elements.append(elem)

        # 递归处理子元素
        _collect_interactive_elements(child, elements, depth + 1, max_depth)


def _format_variants(elem: dict) -> str:
    """将 variant 信息格式化为简洁标注，如 [State=Hover,Size=Large] 或 (可选:Hover/Disabled/Active)。"""
    parts = []
    variants = elem.get("variants", {})
    if variants:
        parts.append(";".join("%s=%s" % (k, v) for k, v in variants.items()))
    variant_opts = elem.get("variant_options", {})
    if variant_opts:
        for k, opts in variant_opts.items():
            if opts:
                parts.append("%s可选[%s]" % (k, "/".join(str(o) for o in opts[:5])))
    if parts:
        return " [" + ", ".join(parts) + "]"
    return ""


def _format_components(components: dict) -> str:
    """将 Figma 组件库格式化为可注入 AI Prompt 的描述文本（含 variant 可选项）。"""
    if not components:
        return ""
    lines = ["## 设计组件库（来自 Figma）", ""]
    for comp_id, comp_info in list(components.items())[:40]:
        if isinstance(comp_info, dict):
            name = comp_info.get("name", comp_id)
            prop_defs = comp_info.get("componentPropertyDefinitions", {})
            variant_info = ""
            if isinstance(prop_defs, dict):
                var_parts = []
                for k, v in prop_defs.items():
                    if isinstance(v, dict) and v.get("type") == "VARIANT":
                        opts = v.get("variantOptions", [])
                        if opts:
                            var_parts.append(
                                "%s[%s]" % (k, "/".join(str(o) for o in opts[:5]))
                            )
                if var_parts:
                    variant_info = " — " + ", ".join(var_parts)
            lines.append("- %s%s" % (name, variant_info))
        else:
            lines.append("- %s" % str(comp_info))
    return "\n".join(lines)


def _format_ui_description(frames: list[dict]) -> str:
    """
    将收集到的 UI 结构格式化为紧凑的文本描述。
    适合注入到 AI Prompt 中。
    """
    if not frames:
        return "（未从 Figma 设计稿中提取到 UI 结构信息）"

    lines = ["## 页面结构（来自 Figma 设计稿）", ""]

    for frame in frames:
        frame_name = frame["name"]
        elements = frame["elements"]
        if not elements:
            continue

        lines.append("### %s" % frame_name)

        # 分组：按钮、输入框、文本、其他
        buttons = [e for e in elements if e["type"] in ("按钮",)]
        inputs = [e for e in elements if e["type"] in ("输入框", "下拉框", "搜索框")]
        texts = [e for e in elements if e["type"] in ("文本",) and e.get("text")]

        if texts:
            # ★ Fix55a: 放开截断 10→30，30字→50字
            lines.append(
                "- 文本内容: %s"
                % ", ".join('"%s"' % e["text"][:50] for e in texts[:30])
            )
        if buttons:
            btn_descs = []
            # ★ Fix55a: 放开截断 12→30
            for e in buttons[:30]:
                label = e.get("text") or e.get("name", "未命名")
                vars_info = _format_variants(e)
                btn_descs.append("「%s」%s" % (label, vars_info))
            lines.append("- 按钮: %s" % ", ".join(btn_descs))
        if inputs:
            input_descs = []
            # ★ Fix55a: 放开截断 10→30
            for e in inputs[:30]:
                desc = e.get("text") or e.get("name", "未命名")
                vars_info = _format_variants(e)
                input_descs.append("%s%s" % (desc, vars_info))
            lines.append("- 输入控件: %s" % ", ".join(input_descs))

        # 其余交互元素
        others = [e for e in elements if e not in buttons + inputs + texts]
        if others:
            other_descs = []
            # ★ Fix55a: 放开截断 5→15
            for e in others[:15]:
                desc = "%s(%s)" % (e.get("text") or e.get("name", ""), e["type"])
                other_descs.append(desc)
            lines.append("- 其他: %s" % ", ".join(other_descs))

        lines.append("")

    return "\n".join(lines)


def parse_figma_document(
    document: dict, components: dict = None, max_depth: int = None
) -> str:
    """
    解析 Figma document 树，输出 UI 结构描述文本。

    参数：
        document:   Figma API 返回的 document 对象
        components: Figma API 返回的 components 对象（设计组件库）
        max_depth:  最大递归深度。None=从 config.FIGMA_PARSE_MAX_DEPTH 读取（默认 6）

    返回：
        UI 结构描述字符串，可直接注入 AI Prompt
        ★ v5：如果文档中有被截断的深层节点，末尾追加 ⚠️ 标注
    """
    if not document or not isinstance(document, dict):
        return ""

    # ★ v5：配置化深度
    if max_depth is None:
        try:
            from src.core.config_loader import config

            max_depth = config.figma_parse_max_depth
            max_pen = config.figma_parse_max_penetration
        except Exception as _e:
            _log.debug(
                "[figma_parser] parse_figma_document 异常: %s", _e, exc_info=True
            )
            max_depth = 6
            max_pen = 24
    else:
        max_pen = 24  # 穿透限制保持默认

    frames = _collect_frames(document, max_depth=max_depth, max_penetration=max_pen)
    description = _format_ui_description(frames)
    if components:
        comp_desc = _format_components(components)
        if comp_desc:
            description += "\n" + comp_desc

    # ★ v5：检查截断节点 — 重新遍历记录被截断的 FRAME
    truncated = _find_truncated_frames(document, max_depth, max_pen)
    if truncated:
        description += (
            "\n\n> ⚠️ **Figma 深度截断**: %d 个深层节点未解析（当前 max_depth=%d）。\n"
            "> 如需完整解析，请增大 FIGMA_PARSE_MAX_DEPTH 配置（最大 12）。\n"
            "> 截断节点: %s\n"
        ) % (
            len(truncated),
            max_depth,
            ", ".join("%s(%s)" % (t["name"], t["id"]) for t in truncated[:5]),
        )

    return description


def _find_truncated_frames(node: dict, max_depth: int, max_pen: int) -> list[dict]:
    """查找因深度限制被截断的 FRAME 节点。"""
    truncated = []

    def _walk(n, depth, pen):
        if depth > max_depth or pen > max_pen:
            nt = n.get("type", "")
            if nt == "FRAME" and n.get("children"):
                truncated.append(
                    {
                        "name": n.get("name", ""),
                        "id": n.get("id", ""),
                        "depth": depth,
                    }
                )
            return
        for child in n.get("children", []):
            ct = child.get("type", "")
            _walk(child, depth + 1, pen + 1 if ct == "GROUP" else 0)

    for child in node.get("children", []):
        _walk(child, 1, 0)
    return truncated


def parse_figma_file_result(figma_result: dict) -> str:
    """
    从 Figma API 的完整返回结果中提取并解析 UI 结构。

    参数：
        figma_result: api_clients.fetch_figma_file() 的返回结果
                      {"success": True, "data": {"document": {...}, ...}}

    返回：
        UI 结构描述字符串
    """
    if not figma_result.get("success"):
        return ""
    document = figma_result.get("data", {}).get("document", {})
    components = figma_result.get("data", {}).get("components", {})
    return parse_figma_document(document, components=components)


# ── CLI 自测入口 ──
if __name__ == "__main__":
    import sys
    from pathlib import Path

    _root = (
        Path(__file__).resolve().parent.parent.parent.parent.parent
    )  # figma_parser → step1 → steps → pipeline → src → 项目根
    if str(_root) not in sys.path:
        sys.path.insert(0, str(_root))

    from src.pipeline.steps.step1.figma_client import figma_client

    _log.info("正在拉取 Figma 设计稿...")
    result = figma_client.fetch_figma_file()
    if result["success"]:
        _log.info("✓ Figma 文件: %s", result["data"]["name"])
        desc = parse_figma_file_result(result)
        _log.info("\n--- UI 结构描述 ---\n")
        _log.info(desc)
    else:
        _log.error("✗ Figma 拉取失败: %s", result["error"])
