# -*- coding: utf-8 -*-
"""
原型图视觉识别模块
==================
从需求文档中提取图片，调用视觉模型识别 UI 结构，输出结构化 JSON。

职责：
1. 图片下载（Confluence 附件 / Word 嵌入 / 通用 URL）
2. base64 编码
3. 调用 call_vision() 识别（详细版 prompt）
4. JSON 解析 + 降级处理

使用方式：
    from src.pipeline.steps.step1.prototype_recognizer import recognize_prototype_images
    results = recognize_prototype_images(image_urls, base_url="https://confluence.example.com")
"""

import base64
from src.core.guarded import guard
import hashlib
import logging
import re
from typing import Any, Optional

import requests

from src.ai.ai_client import ai_brain
from src.ai.json_parser import parse_json_response

_log = logging.getLogger(__name__)

# ── 视觉识别 Prompt（详细版 ~500 token 输出）──
_VISION_PROMPT = (
    "你是 UI 原型图分析助手。请分析这张产品原型截图，提取以下结构化信息：\n\n"
    "1. 页面名称：页面标题/功能名\n"
    "2. 布局结构：左侧导航/右侧内容/顶部工具栏等\n"
    "3. 控件清单：每个控件的类型(button/input/table/select等)、文本/placeholder、位置区域\n"
    "4. 表格字段：如有表格，列出所有列名\n"
    '5. 状态值：页面中出现的所有状态枚举值（如"已完成"、"待审核"）\n'
    "6. 导航关系：左侧菜单项及层级关系\n"
    '7. 操作入口：可点击的操作按钮和链接（如"新建"、"查看"、"编辑"、"删除"）\n\n'
    "严格按以下 JSON 格式返回（不要加任何解释，不要包在代码块中）：\n"
    '{"page_name": "...", "layout": "...", "controls": [{"type": "...", "text": "...", "area": "..."}], '
    '"table_columns": ["..."], "status_values": ["..."], '
    '"navigation": ["..."], "actions": ["..."]}\n'
)

# ── 已识别缓存（进程级，按图片内容 hash 去重）──
_recognized_cache: dict[str, dict] = {}


def _download_image(url: str, base_url: str = "", timeout: int = 15) -> Optional[bytes]:
    """下载图片，返回 bytes。失败返回 None。

    支持：
    - 绝对 URL（http/https）
    - Confluence 相对路径（/download/attachments/... 或 /wiki/download/...）
    """
    if not url:
        return None

    # 补全为绝对 URL
    if not url.startswith(("http://", "https://", "data:")):
        if base_url:
            url = base_url.rstrip("/") + "/" + url.lstrip("/")
        else:
            _log.debug(
                "[PrototypeRecognizer] 图片 URL 无 base_url 且非绝对路径: %s", url[:80]
            )
            return None

    # data URI 直接解码
    if url.startswith("data:"):
        m = re.match(r"data:[^;]+;base64,(.+)", url)
        if m:
            with guard("_download_image: return base64.b64decode(m.group(1))", log=_log):
                return base64.b64decode(m.group(1))
        return None

    with guard("_download_image: resp = requests.get(url, timeout=tim", log=_log):
        resp = requests.get(url, timeout=timeout)
        if resp.status_code == 200 and resp.content:
            return resp.content
        _log.debug(
            "[PrototypeRecognizer] 图片下载失败 HTTP %d: %s", resp.status_code, url[:80]
        )
    return None


def _bytes_to_base64(image_bytes: bytes) -> str:
    """图片 bytes → base64 字符串。"""
    return base64.b64encode(image_bytes).decode("ascii")


def _content_hash(image_bytes: bytes) -> str:
    """图片内容 MD5 哈希（前 16 位），用于去重。"""
    return hashlib.md5(image_bytes).hexdigest()[:16]


def _recognize_single(
    image_base64: str,
    content_hash: str,
    vision_model: str = None,
    vision_api_key: str = None,
    vision_base_url: str = None,
) -> dict[str, Any]:
    """调用视觉模型识别单张图片。

    返回：
        {"success": True, "data": {...结构化JSON...}, "_model": "..."}
        或 {"success": False, "error": "..."}
    """
    # 缓存命中
    if content_hash in _recognized_cache:
        cached = _recognized_cache[content_hash]
        _log.debug("[PrototypeRecognizer] 缓存命中: hash=%s", content_hash)
        return {"success": True, "data": cached, "_cached": True}

    result = ai_brain.call_vision(
        image_base64=image_base64,
        prompt=_VISION_PROMPT,
        caller_name="原型图识别",
        vision_model=vision_model,
        vision_api_key=vision_api_key,
        vision_base_url=vision_base_url,
        temperature=0.0,
        max_tokens=1024,
    )

    if not result.get("success"):
        return result

    # 解析 JSON
    raw = result.get("data", "")
    parsed = parse_json_response(raw)
    if parsed.get("success") and isinstance(parsed.get("data"), dict):
        structured = parsed["data"]
        _recognized_cache[content_hash] = structured
        result["data"] = structured
        return result

    # JSON 解析失败 → 降级为原始文本
    _log.warning("[PrototypeRecognizer] JSON 解析失败，降级为原始文本: %s", raw[:100])
    fallback = {
        "page_name": "",
        "layout": "",
        "controls": [],
        "table_columns": [],
        "status_values": [],
        "navigation": [],
        "actions": [],
        "_raw_fallback": raw[:500],
    }
    _recognized_cache[content_hash] = fallback
    result["data"] = fallback
    return result


def recognize_prototype_images(
    image_urls: list[str],
    base_url: str = "",
    vision_model: str = None,
    vision_api_key: str = None,
    vision_base_url: str = None,
) -> list[dict[str, Any]]:
    """批量识别原型图。

    Args:
        image_urls: 图片 URL 列表（Confluence 附件路径 / 绝对 URL / data URI）
        base_url: Confluence base URL（用于补全相对路径）
        vision_model: 视觉模型名（不传则读 config.vision_model）
        vision_api_key: 视觉模型 API key（不传则读 config.vision_api_key）
        vision_base_url: 视觉模型 base URL（不传则读 config.vision_base_url）

    Returns:
        [{"url": "...", "success": True, "data": {...}, ...}, ...]
        失败项 success=False，不阻断后续图片。
    """
    if not image_urls:
        return []

    results = []
    for url in image_urls:
        url = url.strip()
        if not url:
            continue

        # 跳过 Markdown 图片语法残留
        url = re.sub(r"^!\[[^\]]*\]\(([^)]+)\)$", r"\1", url).strip()
        if url.startswith("[图:") or url.startswith("[图]"):
            _log.debug("[PrototypeRecognizer] 跳过非 URL 占位符: %s", url[:60])
            continue

        # 下载
        image_bytes = _download_image(url, base_url=base_url)
        if not image_bytes:
            results.append({"url": url, "success": False, "error": "图片下载失败"})
            continue

        # 去重
        chash = _content_hash(image_bytes)

        # 识别
        image_b64 = _bytes_to_base64(image_bytes)
        rec_result = _recognize_single(
            image_b64,
            chash,
            vision_model=vision_model,
            vision_api_key=vision_api_key,
            vision_base_url=vision_base_url,
        )

        entry = {"url": url, "content_hash": chash}
        entry.update(rec_result)
        results.append(entry)

        if rec_result.get("success"):
            _log.info(
                "[PrototypeRecognizer] ✓ 识别成功: %s (hash=%s, model=%s)",
                url[:60],
                chash,
                rec_result.get("_model", "?"),
            )
        else:
            _log.warning(
                "[PrototypeRecognizer] ✗ 识别失败: %s — %s",
                url[:60],
                rec_result.get("error", "?")[:80],
            )

    _log.info(
        "[PrototypeRecognizer] 批量识别完成: %d/%d 成功",
        sum(1 for r in results if r.get("success")),
        len(results),
    )
    return results


def recognize_word_images(
    image_bytes_list: list[tuple[str, bytes]],
    vision_model: str = None,
    vision_api_key: str = None,
    vision_base_url: str = None,
) -> list[dict[str, Any]]:
    """识别 Word 文档中嵌入的图片。

    Args:
        image_bytes_list: [(图片文件名, 图片bytes), ...]
        vision_model/api_key/base_url: 同 recognize_prototype_images

    Returns:
        [{"filename": "...", "success": True, "data": {...}, ...}, ...]
    """
    if not image_bytes_list:
        return []

    results = []
    for filename, image_bytes in image_bytes_list:
        if not image_bytes:
            continue

        chash = _content_hash(image_bytes)
        image_b64 = _bytes_to_base64(image_bytes)

        rec_result = _recognize_single(
            image_b64,
            chash,
            vision_model=vision_model,
            vision_api_key=vision_api_key,
            vision_base_url=vision_base_url,
        )

        entry = {"filename": filename, "content_hash": chash}
        entry.update(rec_result)
        results.append(entry)

        if rec_result.get("success"):
            _log.info(
                "[PrototypeRecognizer] ✓ Word图片识别成功: %s (hash=%s)",
                filename,
                chash,
            )
        else:
            _log.warning(
                "[PrototypeRecognizer] ✗ Word图片识别失败: %s — %s",
                filename,
                rec_result.get("error", "?")[:80],
            )

    _log.info(
        "[PrototypeRecognizer] Word图片批量识别完成: %d/%d 成功",
        sum(1 for r in results if r.get("success")),
        len(results),
    )
    return results


def format_prototype_for_rag(recognized: dict[str, Any]) -> str:
    """将视觉识别结果格式化为 RAG chunk 文本。

    Args:
        recognized: recognize_prototype_images 返回的单条 data 字段

    Returns:
        格式化的文本（供 RAG 索引使用）
    """
    if not recognized or not isinstance(recognized, dict):
        return ""

    parts = []
    page_name = recognized.get("page_name", "")
    if page_name:
        parts.append("原型图页面: %s" % page_name)

    layout = recognized.get("layout", "")
    if layout:
        parts.append("布局: %s" % layout)

    controls = recognized.get("controls", [])
    if controls:
        ctrl_strs = []
        for c in controls:
            if isinstance(c, dict):
                ctrl_strs.append(
                    "%s(%s)" % (c.get("text", c.get("type", "")), c.get("type", ""))
                )
            elif isinstance(c, str):
                ctrl_strs.append(c)
        if ctrl_strs:
            parts.append("控件: %s" % ", ".join(ctrl_strs[:20]))

    table_columns = recognized.get("table_columns", [])
    if table_columns:
        parts.append("表格字段: %s" % ", ".join(str(c) for c in table_columns))

    status_values = recognized.get("status_values", [])
    if status_values:
        parts.append("状态值: %s" % ", ".join(str(s) for s in status_values))

    navigation = recognized.get("navigation", [])
    if navigation:
        parts.append("导航菜单: %s" % " > ".join(str(n) for n in navigation))

    actions = recognized.get("actions", [])
    if actions:
        parts.append("操作入口: %s" % ", ".join(str(a) for a in actions))

    # 降级文本
    raw_fallback = recognized.get("_raw_fallback", "")
    if raw_fallback and not parts:
        parts.append("视觉识别(原始): %s" % raw_fallback[:300])

    return "\n".join(parts) if parts else ""
