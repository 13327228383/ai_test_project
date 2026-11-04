# -*- coding: utf-8 -*-
"""XMind / JSON 解析器（树状/结构化数据）— 从 file_parser.py 拆出，零业务逻辑变更。"""

import json
from pathlib import Path
from typing import Any

from ._encoding import decode_file


class _TreeFormatsMixin:
    """_parse_xmind / _parse_xmind_json / _parse_xmind_xml / _parse_json — 树状/结构化数据。"""

    # ── XMind ──

    @staticmethod
    def _parse_xmind(path: Path, **options) -> dict[str, Any]:
        """解析 XMind 思维导图为文本大纲。"""
        try:
            import zipfile
            import defusedxml.ElementTree as ET
        except ImportError:
            return {
                "success": False,
                "text": "",
                "format": "xmind",
                "metadata": {},
                "error": "缺少 zipfile 或 xml 支持",
            }

        # XMind 文件是 ZIP 格式
        try:
            with zipfile.ZipFile(path, "r") as zf:
                # XMind 2020+ 格式
                if "content.json" in zf.namelist():
                    content = json.loads(zf.read("content.json"))
                    text = _TreeFormatsMixin._parse_xmind_json(content)
                # XMind 8 格式
                elif "content.xml" in zf.namelist():
                    xml_content = zf.read("content.xml").decode("utf-8")
                    text = _TreeFormatsMixin._parse_xmind_xml(xml_content)
                else:
                    return {
                        "success": False,
                        "text": "",
                        "format": "xmind",
                        "metadata": {},
                        "error": "无法识别的 XMind 格式",
                    }
        except Exception as e:
            return {
                "success": False,
                "text": "",
                "format": "xmind",
                "metadata": {},
                "error": "XMind 解析失败: %s" % str(e),
            }

        return {
            "success": True,
            "text": text,
            "format": "xmind",
            "metadata": {"size": len(text)},
            "empty_hint": "XMind 中未提取到任何主题文本",
        }

    @staticmethod
    def _parse_xmind_json(content: dict) -> str:
        """解析 XMind JSON 格式为文本大纲。"""
        lines = []

        def traverse(node, depth=0):
            title = node.get("title", "")
            if title:
                lines.append("  " * depth + "- " + title)
            for child in node.get("children", {}).get("attached", []):
                traverse(child, depth + 1)

        root_topic = content.get("rootTopic", {})
        traverse(root_topic)
        return "\n".join(lines)

    @staticmethod
    def _parse_xmind_xml(xml_content: str) -> str:
        """解析 XMind XML 格式为文本大纲。"""
        import defusedxml.ElementTree as ET

        lines = []
        root = ET.fromstring(xml_content)

        def traverse(elem, depth=0):
            title = elem.attrib.get("title", "")
            if title:
                lines.append("  " * depth + "- " + title)
            for child in elem.findall(".//topic"):
                # 避免重复遍历，只处理直接子节点
                if child.getparent() == elem:
                    traverse(child, depth + 1)

        # 简化处理：提取所有 topic 的 title
        for topic in root.iter("topic"):
            title = topic.attrib.get("title", "")
            if title:
                lines.append(title)

        return "\n".join(lines)

    # ── JSON ──

    @staticmethod
    def _parse_json(path: Path, **options) -> dict[str, Any]:
        """解析 JSON 文件为格式化文本（编码判定统一走 _encoding.decode_file）。"""
        raw, encoding = decode_file(path)
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as _json_e:
            return {
                "success": False,
                "text": "",
                "format": "json",
                "metadata": {"encoding": encoding},
                "error": "JSON 格式错误: %s" % str(_json_e)[:120],
            }

        # 如果是需求文档格式，提取关键字段
        if isinstance(data, dict):
            if "requirements" in data or "reqs" in data:
                # 结构化需求JSON
                reqs = data.get("requirements", data.get("reqs", []))
                lines = []
                for req in reqs:
                    if isinstance(req, dict):
                        lines.append("## %s" % req.get("id", "REQ"))
                        lines.append("**标题**: %s" % req.get("title", ""))
                        lines.append("**描述**: %s" % req.get("description", ""))
                        lines.append("**优先级**: %s" % req.get("priority", ""))
                        lines.append("")
                text = "\n".join(lines)
            else:
                # 通用JSON，格式化输出
                text = json.dumps(data, ensure_ascii=False, indent=2)
        elif isinstance(data, list):
            lines = []
            for item in data:
                if isinstance(item, dict):
                    lines.append(
                        "- %s" % item.get("title", item.get("name", str(item)))
                    )
                else:
                    lines.append("- %s" % str(item))
            text = "\n".join(lines)
        else:
            text = str(data)

        return {
            "success": True,
            "text": text,
            "format": "json",
            "metadata": {"type": type(data).__name__, "encoding": encoding},
            "empty_hint": "JSON 中未提取到任何需求/条目文本",
        }
