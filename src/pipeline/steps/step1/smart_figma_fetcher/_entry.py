# -*- coding: utf-8 -*-
"""入口方法 fetch_smart — 从 __init__.py 拆出，零逻辑变更。"""

import logging
import time
from typing import Any

from src.core.log_utils import fmt_log
from src.pipeline.steps.step1._smart_fetcher_utils import extract_file_key
from src.pipeline.steps.step1._smart_fetcher_nodes import extract_nodes_from_document
from src.pipeline.steps.step1._smart_fetcher_format import format_smart_output

_log = logging.getLogger(__name__)


class _EntryMixin:
    """SmartFigmaFetcher 主入口方法。"""

    def fetch_smart(
        self,
        figma_url: str,
        force_refresh: bool = False,
        cache_only: bool = True,
        page_ids: list[str] = None,
    ) -> dict[str, Any]:
        """
        智能分层拉取入口。

        参数:
            figma_url:     Figma 文件完整 URL
            force_refresh: True = 跳过所有缓存，强制全量拉取
            cache_only:    True = 纯缓存模式，不触发任何 API 调用
            page_ids:      指定页面 ID 列表，提供时只拉这些页面（1次 /nodes API）
                           为 None 时走全量拉取

        返回:
            {"success": True/False,
             "figma_text": "格式化 UI 描述（含变更标记）",
             "change_summary": "变更摘要文本",
             "changed_pages": [...],
             "new_pages": [...],
             "total_pages": N,
             "layers": {"layer1": {...}, "layer2": {...}, "layer3": {...}, "layer4": ...}}
        """
        file_key = extract_file_key(figma_url)
        if not file_key:
            return {
                "success": False,
                "error": "无法从 URL 提取 Figma file_key: %s" % figma_url,
            }

        # —— 缓存优先模式：不调 API，纯读本地缓存 ——
        if cache_only and not force_refresh:
            return self._fetch_from_cache_only(file_key)

        # ★ 增量模式：指定页面 ID，只拉变更的页面（1 次 API）
        if page_ids and not force_refresh:
            return self._do_incremental_fetch(file_key, page_ids)

        t0 = time.time()

        # ★ 零配额变更检测：headless 浏览器已通过 /api/figma 轮询更新 _meta_last_result
        if force_refresh:
            try:
                import src.pipeline.steps.step1.mcp.figma_mcp as fmp

                meta = getattr(fmp, "_meta_last_result", None) or {}
                if meta.get("is_cache_fresh"):
                    _log.info(
                        fmt_log(
                            "SmartFigma",
                            "变更检测: 缓存已是最新 (%s)，跳过 API 调用 (配额 0)"
                            % meta.get("design_updated_at", "")[:19],
                        )
                    )
                    return self._fetch_from_cache_only(file_key)
                elif meta.get("success"):
                    _log.info(
                        fmt_log(
                            "SmartFigma",
                            "变更检测: 设计稿已更新 (%s)，开始拉取..."
                            % meta.get("design_updated_at", "")[:19],
                        )
                    )
            except Exception as _e:
                _log.debug(
                    "[SmartFigma] meta 检测失败,回退正常拉取: %s", _e, exc_info=True
                )

        plan_label = (
            "付费"
            if self._is_paid
            else "免费(~%d次/月)" % self._FREE_TIER1_MONTHLY_QUOTA
        )
        _log.info(
            fmt_log("SmartFigma", "分层拉取 (depth=2 目录 + 分批节点, %s)" % plan_label)
        )

        # ★ 分层拉取：depth=2 目录 + 分批节点，网络波动只丢一小批
        try:
            l1_result, document, l3_result = self._full_fetch_with_fallback(
                file_key, force_refresh
            )
        except Exception as e:
            _log.info(fmt_log("SmartFigma", "分层拉取异常: %s" % str(e)[:100]))
            return {"success": False, "error": "分层拉取异常: %s" % str(e)[:120]}

        pages = l1_result["pages"]

        # —— Layer 2: 规则处理层 ——
        from src.pipeline.utils.pause_controller import pause_controller

        if pause_controller.is_paused:
            return {
                "success": True,
                "pages": len(pages),
                "paused": True,
                "paused_at": "Layer 2 前",
            }
        _log.info(fmt_log("Layer 2", "全量保留页面（RAG 按需检索）..."))
        l2_result = self._ai_filter_pages(pages)
        filtered_ids = l2_result["filtered_node_ids"]
        excluded_ids = l2_result["excluded_node_ids"]
        ai_analysis = l2_result.get("ai_analysis", "")
        _log.info(
            fmt_log("Layer 2", "全量保留 %d 个页面（RAG 按需检索）" % len(filtered_ids))
        )

        # —— Layer 3: 全量路径从 document 提取，分层路径 l3_result 已含节点 ——
        if pause_controller.is_paused:
            return {
                "success": True,
                "pages": len(pages),
                "paused": True,
                "paused_at": "Layer 3 前",
            }
        if document:
            # 全量路径：从 document 提取筛选后的节点（零额外 API）
            _log.info(
                fmt_log("Layer 3", "从全量文档提取节点 (%d 个)..." % len(filtered_ids))
            )
            l3_result = extract_nodes_from_document(
                document, filtered_ids, self._node_hashes
            )
        else:
            # 分层路径：l3_result 已含所有节点，只需筛选 filtered_ids
            all_nodes = l3_result.get("nodes", {})
            filtered_nodes = {
                nid: all_nodes[nid] for nid in filtered_ids if nid in all_nodes
            }
            _log.info(
                fmt_log(
                    "Layer 3",
                    "从分层结果筛选节点: %d/%d 个"
                    % (len(filtered_nodes), len(all_nodes)),
                )
            )
            l3_result = {
                "nodes": filtered_nodes,
                "changed_ids": l3_result.get("changed_ids", []),
                "new_ids": l3_result.get("new_ids", []),
                "unchanged_ids": l3_result.get("unchanged_ids", []),
            }
        fetched = len(l3_result.get("nodes", {}))
        _log.info(fmt_log("Layer 3", "提取完成: %d 个有效节点" % fetched))

        # —— Layer 4: 变更感知输出 ——
        _log.info(fmt_log("Layer 4", "生成变更感知输出..."))
        l4_result = format_smart_output(l3_result, l1_result)

        elapsed = time.time() - t0
        _log.info(fmt_log("SmartFigma", "智能拉取完成 (%.1f 秒)" % elapsed))

        # ★ 记录变更会话到持久化日志（含失败节点，供增量重试使用）
        try:
            self._record_change_session(
                {
                    "timestamp": time.time(),
                    "file_key": file_key,
                    "elapsed": elapsed,
                    "changed_pages": l4_result["changed_pages"],
                    "new_pages": l4_result["new_pages"],
                    "total_pages": l4_result["total_pages"],
                    "failed_node_ids": l3_result.get("failed_node_ids", []),
                }
            )
        except Exception as _e:
            _log.debug(
                "[SmartFigma] 变更日志记录失败(不影响主流程): %s", _e, exc_info=True
            )

        return {
            "success": True,
            "figma_text": l4_result["figma_text"],
            "change_summary": l4_result["change_summary"],
            "changed_pages": l4_result["changed_pages"],
            "new_pages": l4_result["new_pages"],
            "total_pages": l4_result["total_pages"],
            "layers": {
                "layer1": {"total_pages": len(pages)},
                "layer2": {
                    "filtered_count": len(filtered_ids),
                    "excluded_count": len(excluded_ids),
                    "excluded_ids": excluded_ids,
                    "ai_analysis": ai_analysis,
                },
                "layer3": {
                    "fetched_nodes": len(l3_result.get("nodes", {})),
                    "changed_nodes": len(l3_result.get("changed_ids", [])),
                    "new_nodes": len(l3_result.get("new_ids", [])),
                    "unchanged_nodes": len(l3_result.get("unchanged_ids", [])),
                },
                "layer4": {"elapsed": elapsed},
            },
        }

    def retry_failed_nodes(self, figma_url: str) -> dict[str, Any]:
        """批量重试最近一次失败节点，按 figma_batch_size 分批并预检配额。"""
        file_key = extract_file_key(figma_url)
        if not file_key:
            return {"success": False, "error": "无法从 URL 提取 Figma file_key"}

        try:
            from src.pipeline.utils.figma_state_store import figma_state_store

            failed = figma_state_store.get_latest_failed_nodes(
                version_label=self._version_label
            )
        except Exception as e:
            _log.error("[SmartFigma] 读取失败节点异常: %s", e, exc_info=True)
            return {
                "success": False,
                "error": "读取失败节点记录失败: %s" % str(e)[:100],
            }

        if not failed:
            _log.info(fmt_log("SmartFigma", "无失败节点需要重试"))
            return {
                "success": True,
                "message": "无失败节点需要重试",
                "retried_count": 0,
                "remaining_failed": 0,
            }

        from src.core.pipeline_config import pipeline_config

        batch_size = int(getattr(pipeline_config, "figma_batch_size", 20) or 20)
        batches = [
            failed[i : i + batch_size] for i in range(0, len(failed), batch_size)
        ]

        from .figma_quota import check_batch_quota

        first_check = check_batch_quota(required_calls=1)
        if not first_check.get("ok"):
            _log.warning(
                fmt_log(
                    "SmartFigma",
                    "配额不足，取消重试: %s" % first_check.get("message", ""),
                )
            )
            return {
                "success": False,
                "error": first_check.get("message", "Figma 配额不足"),
                "retried_count": 0,
                "remaining_failed": len(failed),
            }

        _log.info(
            fmt_log(
                "SmartFigma",
                "批量重试 %d 个失败节点 → %d 批 (每批 %d 个)"
                % (len(failed), len(batches), batch_size),
            )
        )

        remaining_failed = list(failed)
        retried_count = 0
        retry_count = 1
        try:
            sessions = figma_state_store.load_change_sessions(
                version_label=self._version_label
            )
            for s in reversed(sessions):
                if isinstance(s.get("failed_node_ids"), list) and s["failed_node_ids"]:
                    retry_count = int(s.get("retry_count", 0) or 0) + 1
                    break
        except Exception as _e:
            _log.debug("[SmartFigma] 读取 retry_count 失败: %s", _e, exc_info=True)

        for batch_idx, batch in enumerate(batches):
            quota_check = check_batch_quota(required_calls=1)
            if not quota_check.get("ok"):
                _log.warning(
                    fmt_log(
                        "SmartFigma",
                        "批次 %d/%d 配额不足，停止重试: %s"
                        % (batch_idx + 1, len(batches), quota_check.get("message", "")),
                    )
                )
                break

            try:
                _cached, _document, l3_result = self._fetch_incremental(file_key, batch)
            except Exception as e:
                _log.error(
                    "[SmartFigma] 批次 %d/%d 重试异常: %s",
                    batch_idx + 1,
                    len(batches),
                    e,
                    exc_info=True,
                )
                retried_count += len(batch)
                continue

            retried_count += len(batch)
            if l3_result and isinstance(l3_result.get("nodes"), dict):
                succeeded = set(l3_result["nodes"].keys())
                remaining_failed = [
                    nid for nid in remaining_failed if nid not in succeeded
                ]
                _log.info(
                    fmt_log(
                        "SmartFigma",
                        "批次 %d/%d 完成: 成功 %d/%d"
                        % (batch_idx + 1, len(batches), len(succeeded), len(batch)),
                    )
                )
            else:
                _log.warning(
                    fmt_log(
                        "SmartFigma",
                        "批次 %d/%d 无节点返回，保留失败状态"
                        % (batch_idx + 1, len(batches)),
                    )
                )

        # 持久化本次重试后的剩余失败节点（含全部成功时的空列表）
        try:
            figma_state_store.append_change_session(
                {
                    "timestamp": time.time(),
                    "file_key": file_key,
                    "retry_count": retry_count,
                    "failed_node_ids": remaining_failed,
                    "retried_count": retried_count,
                    "original_failed_count": len(failed),
                },
                version_label=self._version_label,
            )
        except Exception as e:
            _log.error("[SmartFigma] 更新失败节点状态异常: %s", e, exc_info=True)
            return {
                "success": False,
                "error": "更新失败节点状态失败: %s" % str(e)[:100],
                "retried_count": retried_count,
                "remaining_failed": len(remaining_failed),
            }

        if remaining_failed:
            return {
                "success": False,
                "error": "仍有 %d/%d 个节点重试失败"
                % (len(remaining_failed), len(failed)),
                "retried_count": retried_count,
                "remaining_failed": len(remaining_failed),
            }
        return {
            "success": True,
            "message": "全部 %d 个失败节点重试成功" % len(failed),
            "retried_count": retried_count,
            "remaining_failed": 0,
        }
