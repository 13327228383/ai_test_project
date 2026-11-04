# -*- coding: utf-8 -*-
from __future__ import annotations

"""
smart_figma_fetcher 的独立工具函数集合
=======================================
零业务逻辑变更，仅做物理代码搬迁。

[已复核-DeepSeek-V3 2026-08-16]
B3.2 JSON 解析收敛：``parse_json_response`` 已改为转发
``src.ai.json_parser.parse_json_response``（唯一真源），
函数名/签名/``dict | None`` 返回契约均未变，调用点零改动。
"""

import re
import logging

# 注：原 ``import json`` 已随 B3.2 收敛删除 —— 本地 JSON 解析实现已转发到
# ``src.ai.json_parser``，本模块不再直接使用 json。

_log = logging.getLogger(__name__)


# =============================================================================
# file_key 提取
# =============================================================================


def extract_file_key(figma_url: str) -> str:
    """从 Figma URL 提取 file_key。"""
    if not figma_url:
        return ""
    match = re.search(r"figma\.com/(?:design|file)/([^/\s?]+)", figma_url)
    if match:
        return match.group(1)
    if re.match(r"^[A-Za-z0-9]+$", figma_url.strip()):
        return figma_url.strip()
    return ""


# =============================================================================
# JSON 解析辅助
# =============================================================================


def parse_json_response(raw: str) -> dict | None:
    """解析 AI 返回的 JSON（**转发到唯一真源**，保留原函数名与 ``dict | None`` 契约）。

    整改 B3.2：原本地的「3 次尝试」朴素实现已删除，改调
    ``src.ai.json_parser.parse_json_response``（markdown 围栏剥离 + 尾逗号/注释/
    控制字符规范化 + 截断补全 + 平衡括号抽取）。

    参数：
        raw: LLM 原始输出文本，允许为空 / 非字符串。

    返回：
        解析出的 dict；**非 dict（如顶层数组）或解析失败一律返回 ``None``**
        —— 与整改前完全一致，调用点零改动。

    .. note::
       行为差异（均为增强，无退化）：尾逗号、被截断的 JSON 现在能解析成功，
       整改前返回 ``None``。顶层数组仍返回 ``None``（本函数契约只认 dict）。
    """
    if not raw or not isinstance(raw, str):
        return None
    from src.ai.json_parser import parse_json_response as _canonical

    result = _canonical(raw)
    if not result.get("success"):
        _log.debug("[smart_fetcher] JSON 解析失败: %s", str(result.get("error"))[:120])
        return None
    data = result.get("data")
    return data if isinstance(data, dict) else None


# =============================================================================
# 付费 Token 检测
# =============================================================================


def detect_paid_token() -> bool:
    """检测当前 Figma Token 是否为付费账号。

    figd_ 开头 = Personal Access Token（Professional/Organization plan）。
    Token 存储在用户凭证 DB 的 FIGMA_KEY 字段。
    """
    try:
        from src.core.config_loader import config

        token = config.figma_key
        return bool(token and token.startswith("figd_"))
    except Exception as _e:
        _log.debug(
            "[_smart_fetcher_utils] detect_paid_token 异常: %s", _e, exc_info=True
        )
        return False


# =============================================================================
# 页面提取
# =============================================================================


def extract_top_pages(document: dict) -> list[dict]:
    """从 document 树提取顶层页面/FRAME 节点（depth=1 时仅含 CANVAS + 直接子节点）。"""
    pages = []
    for child in document.get("children", []):
        child_type = child.get("type", "")
        child_name = child.get("name", "").strip()
        child_id = child.get("id", "")

        if child_type == "CANVAS":
            for frame in child.get("children", []):
                if frame.get("type") in ("FRAME", "COMPONENT_SET"):
                    pages.append(
                        {
                            "id": frame.get("id", ""),
                            "name": frame.get("name", "").strip(),
                            "type": frame.get("type", ""),
                            "canvas": child_name,
                        }
                    )
        elif child_type in ("FRAME", "COMPONENT_SET"):
            pages.append(
                {
                    "id": child_id,
                    "name": child_name,
                    "type": child_type,
                    "canvas": "",
                }
            )

    return pages


# =============================================================================
# 节点元素递归提取
# =============================================================================


def extract_elements_flat(node: dict, depth: int = 0) -> list[str]:
    """简单递归提取节点中的文本和交互元素名。"""
    if depth > 3:
        return []
    results = []
    node_type = node.get("type", "")
    node_name = node.get("name", "").strip()
    node_text = node.get("characters", "").strip()

    if node_type == "TEXT" and node_text:
        results.append("「%s」" % node_text[:30])
    elif node_name and node_type in ("FRAME", "COMPONENT", "INSTANCE"):
        results.append("[%s]%s" % (node_type, node_name[:30]))

    for child in node.get("children", []):
        results.extend(extract_elements_flat(child, depth + 1))

    return results
