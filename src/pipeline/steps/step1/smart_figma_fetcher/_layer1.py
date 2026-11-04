# -*- coding: utf-8 -*-
from __future__ import annotations

"""Layer 1: 轻量级目录拉取 (depth=2) — 从 __init__.py 拆出，零逻辑变更。"""

import logging
from typing import Any

from src.core.log_utils import fmt_log
from src.pipeline.steps.step1._smart_fetcher_utils import extract_top_pages
from src.pipeline.steps.step1._smart_fetcher_quota import check_quota

_log = logging.getLogger(__name__)


class _Layer1Mixin:
    """Layer 1: GET /v1/files/:key?depth=2 轻量级目录拉取。"""

    def _fetch_directory(
        self, file_key: str, force_refresh: bool = False
    ) -> dict[str, Any]:
        """
        拉取轻量级目录：GET /v1/files/:key?depth=2
        depth=2 才能拿到 CANVAS → FRAME 两级结构（depth=1 只有 CANVAS，无 FRAME 子节点）。
        """
        # 缓存优先：检查是否已有有效缓存
        if not force_refresh:
            cached = self._check_cached_file(file_key)
            if cached:
                _log.info(
                    fmt_log(
                        "Layer 1",
                        "基础拉取: 命中缓存 (%d 页面)，跳过 API 调用"
                        % len(cached.get("pages", [])),
                    )
                )
                return cached

        if not check_quota(self._api_call_count, "Layer 1 目录拉取"):
            return {
                "success": False,
                "error": "本月 Figma API 配额已耗尽，请升级 plan 或下月再试",
            }

        result = self._client.fetch_figma_file(
            file_key=file_key,
            force_refresh=force_refresh,
            depth=2,
        )
        self._api_call_count += 1
        if self._is_paid:
            _log.info(
                fmt_log(
                    "Layer 1",
                    "API 调用 #%d (付费, %d/min 限制)"
                    % (self._api_call_count, self._PAID_RATE_LIMIT_PER_MINUTE),
                )
            )
        else:
            _log.info(
                fmt_log(
                    "Layer 1",
                    "API 调用 #%d (本月上限约 %d)"
                    % (self._api_call_count, self._FREE_TIER1_MONTHLY_QUOTA),
                )
            )

        if not result.get("success"):
            return result

        data = result["data"]
        document = data.get("document", {})

        pages = extract_top_pages(document)
        return {
            "success": True,
            "pages": pages,
            "file_name": data.get("name", ""),
            "last_modified": data.get("last_modified", ""),
        }

    def _check_cached_file(self, file_key: str) -> dict | None:
        """检查 MySQL 缓存中是否已有该文件的数据。"""
        try:
            full_data = self._client.cache.get_full_file(file_key)
            if full_data is not None:
                document = full_data.get("document", {})
                pages = extract_top_pages(document)
                return {
                    "success": True,
                    "pages": pages,
                    "file_name": full_data.get("name", ""),
                    "last_modified": full_data.get("last_modified", ""),
                }
        except Exception as _e:
            _log.debug("[_layer1] _check_cached_file 异常: %s", _e, exc_info=True)
        return None
