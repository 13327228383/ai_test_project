# -*- coding: utf-8 -*-
from __future__ import annotations

"""能力缓存的 Key/签名/工具函数 — 从 capability_cache.py 拆出，零逻辑变更。"""

import hashlib
import json
import re
from typing import Any
import logging

_log = logging.getLogger(__name__)


def _now_sql() -> str:
    return "NOW()"


def normalize_intent(text: str) -> str:
    text = str(text or "").strip()
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"[，。；;、,.!！?？]+", " ", text)
    return text[:300]


def _project_name() -> str:
    try:
        from src.core.config_loader import config

        return config.project_name or "default"
    except Exception as _e:
        _log.debug("[keys] _project_name 异常: %s", _e, exc_info=True)
        return "default"


def _active_version_id(project: str = "") -> int:
    """当前项目活跃版本号；无 active 版本/取不到时返回 0（0=跨版本复用 legacy 缓存）。"""
    try:
        from src.core.versioning import current_version_id

        return int(current_version_id(project) or 0)
    except Exception as _e:
        _log.debug("[keys] _active_version_id 异常: %s", _e, exc_info=True)
        return 0


def _capability_name(intent_schema: dict[str, Any]) -> str:
    """v9: type 字段即为能力分类。"""
    return str((intent_schema or {}).get("type") or "").strip() or "navigate_and_verify"


def make_capability_key(project: str, intent_schema: dict[str, Any]) -> str:
    capability = _capability_name(intent_schema)
    intent = normalize_intent(str((intent_schema or {}).get("title") or ""))
    raw = "%s|%s|%s" % (project, capability, intent)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def make_page_signature(
    intent_schema: dict[str, Any], context: dict[str, Any] | None = None
) -> str:
    """能力适用页面签名。"""
    context = context or {}
    stable = {
        "capability": _capability_name(intent_schema),
        "intent": normalize_intent(str((intent_schema or {}).get("title") or "")),
        "url_path": _normalize_url_path(context.get("page_url") or ""),
        "page_title": normalize_intent(context.get("page_title") or ""),
    }
    raw = json.dumps(stable, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _normalize_url_path(url: str) -> str:
    url = str(url or "").strip()
    if not url:
        return ""
    url = re.sub(r"^https?://[^/]+", "", url, flags=re.I)
    url = url.split("#", 1)[0].split("?", 1)[0]
    return url.rstrip("/") or "/"
