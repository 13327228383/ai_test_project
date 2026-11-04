# -*- coding: utf-8 -*-
"""
Step 1: Confluence 客户端 (ConfluenceClient)
============================================
从 Confluence 拉取需求文档的原始内容（HTML / 纯文本）。

使用方式：
    from src.pipeline.steps.step1.confluence_client import confluence_client
    result = confluence_client.fetch_confluence_raw()

【2026-06-18 重构】原 450 行单文件按职责拆分：
- fetch.py        — _FetchMixin（fetch_confluence_page / fetch_confluence_raw / fetch_confluence_all）
- macros.py       — _MacrosMixin（_convert_macros + 7 个 macro 处理器）
- html_convert.py — _HtmlConvertMixin（_html_to_markdown / _convert_tables / _convert_images / _convert_links）
- __init__.py     — facade ConfluenceClient + 单例 + 共享辅助方法 + 配置注入
"""

from typing import Any
import logging

_log = logging.getLogger(__name__)

from src.core.config_loader import config

from .fetch import _FetchMixin
from .macros import _MacrosMixin
from .html_convert import _HtmlConvertMixin


class ConfluenceClient(_FetchMixin, _MacrosMixin, _HtmlConvertMixin):
    """Confluence REST API 封装。

    ★ 修复（2026-06-22）：原本 __init__ 把 config.confluence_* 快照到实例字段，
    模块级单例导致用户在 UI 改账号/URL 后客户端读不到。改成 @property。
    """

    def __init__(self) -> None:
        # 仅缓存进程级稳定的依赖
        self._timeout = 30

    @property
    def _cf_base_url(self) -> str:
        return config.confluence_base_url

    @property
    def _cf_username(self) -> str:
        return config.confluence_username

    @property
    def _cf_password(self) -> str:
        return config.confluence_password

    @staticmethod
    def _ok(data: Any = None, **extra) -> dict[str, Any]:
        """构造成功响应。可通过 data dict 或 **extra 关键字参数传入数据。"""
        if data is None:
            result = {"success": True, "data": dict(extra) if extra else None}
        else:
            result = {"success": True, "data": data}
            result.update(extra)
        return result

    @staticmethod
    def _error(message: str, status_code: int = None) -> dict[str, Any]:
        result = {"success": False, "error": message}
        if status_code is not None:
            result["status_code"] = status_code
        return result


# 单例
confluence_client = ConfluenceClient()
