# -*- coding: utf-8 -*-
"""增量拉取 + 全量回退 + 缓存模式 — 从 smart_figma_fetcher.py 拆出，零逻辑变更。

★ P1-5 修复 (2026-06-23): _full_fetch_with_fallback 增加目录指纹检测，
  Layer 1 目录未变时跳过 Layer 3 批量 API 调用，直接从 MySQL 缓存读取节点。
"""

import hashlib
from src.core.guarded import guard
import json
import logging
from typing import Any

from src.core.log_utils import fmt_log
from src.pipeline.steps.step1.figma_hasher import compute_node_hash
from src.pipeline.steps.step1._smart_fetcher_format import format_smart_output
from src.pipeline.steps.step1._smart_fetcher_nodes import (
    batch_fetch_nodes_cached,
    extract_nodes_from_full_doc,
)

_log = logging.getLogger(__name__)


class _IncrementalMixin:
    """_do_incremental_fetch / _fetch_incremental / _full_fetch_with_fallback / _fetch_from_cache_only。"""

    # ==================================================================
    # 增量拉取流程：返回完整结果 dict（跳过全量路径，独立完成 Layer1-4）
    # ==================================================================

    def _do_incremental_fetch(
        self, file_key: str, page_ids: list[str]
    ) -> dict[str, Any]:
        """增量拉取完整流程，返回与 fetch_smart 相同格式的结果。"""
        from src.pipeline.utils.pause_controller import pause_controller

        l1_result, document, l3_result = self._fetch_incremental(file_key, page_ids)

        pages = l1_result["pages"]

        # Layer 2: 全量保留（RAG 按需检索）
        if pause_controller.is_paused:
            return {
                "success": True,
                "pages": len(pages),
                "paused": True,
                "paused_at": "Layer 2 前",
            }
        l2_result = self._ai_filter_pages(pages)
        filtered_ids = l2_result["filtered_node_ids"]
        excluded_ids = l2_result["excluded_node_ids"]
        ai_analysis = l2_result.get("ai_analysis", "")

        # Layer 3: 从增量结果筛选节点
        if pause_controller.is_paused:
            return {
                "success": True,
                "pages": len(pages),
                "paused": True,
                "paused_at": "Layer 3 前",
            }
        all_nodes = l3_result.get("nodes", {})
        filtered_nodes = {
            nid: all_nodes[nid] for nid in filtered_ids if nid in all_nodes
        }
        l3_result = {
            "nodes": filtered_nodes,
            "changed_ids": l3_result.get("changed_ids", []),
            "new_ids": l3_result.get("new_ids", []),
            "unchanged_ids": l3_result.get("unchanged_ids", []),
        }

        # Layer 4: 格式化
        l4_result = format_smart_output(l3_result, l1_result)

        return {
            "success": True,
            "figma_text": l4_result["figma_text"],
            "change_summary": l4_result.get("change_summary", ""),
            "changed_pages": l4_result.get("changed_pages", []),
            "new_pages": l4_result.get("new_pages", []),
            "total_pages": l4_result.get("total_pages", len(pages)),
            "layers": {
                "layer1": {"total_pages": len(pages), "cache_hit": True},
                "layer2": {
                    "filtered_count": len(filtered_ids),
                    "excluded_count": len(excluded_ids),
                    "excluded_ids": excluded_ids,
                    "ai_analysis": ai_analysis,
                },
                "layer3": {
                    "fetched_nodes": len(filtered_nodes),
                    "changed_nodes": len(l3_result.get("changed_ids", [])),
                    "new_nodes": len(l3_result.get("new_ids", [])),
                    "unchanged_nodes": len(l3_result.get("unchanged_ids", [])),
                    "incremental": True,
                },
            },
        }

    # ==================================================================
    # 增量拉取：按指定页面 ID 拉取（1 次 API）
    # ==================================================================

    def _fetch_incremental(self, file_key: str, page_ids: list[str]) -> dict[str, Any]:
        """只拉指定页面，合并到现有缓存数据中。1 次 GET /nodes?ids=..."""
        _log.info(
            fmt_log("SmartFigma", "增量拉取: %d 个页面 (1 次 API)" % len(page_ids))
        )

        # 从缓存读取现有页面列表
        cached = self._check_cached_file(file_key)
        if not cached:
            _log.info(fmt_log("SmartFigma", "缓存为空，回退分层拉取"))
            l1_result, document, l3_result = self._full_fetch_with_fallback(
                file_key, True
            )
            return l1_result, document, l3_result

        # 拉取指定节点
        response = self._client.fetch_figma_file_nodes(
            node_ids=page_ids, file_key=file_key, force_refresh=True
        )
        self._api_call_count += 1
        _log.info(fmt_log("SmartFigma", "增量 API 调用 #%d" % self._api_call_count))

        if not response.get("success"):
            _log.info(fmt_log("SmartFigma", "增量拉取失败，回退全量"))
            l1_result, document, l3_result = self._full_fetch_with_fallback(
                file_key, True
            )
            return l1_result, document, l3_result

        new_nodes = response["data"].get("nodes", {})
        _log.info(fmt_log("SmartFigma", "增量拉取完成: %d 个节点" % len(new_nodes)))

        # 合并：用新节点覆盖缓存中的旧节点
        new_ids = []
        unchanged_ids = []
        # 计算哈希，分类节点
        for nid, ndata in new_nodes.items():
            nc = ndata if isinstance(ndata, dict) else {"raw": ndata}
            h = compute_node_hash(nc)
            old = self._node_hashes.get(nid)
            if old is None:
                new_ids.append(nid)
            elif h == old:
                unchanged_ids.append(nid)
            self._node_hashes[nid] = h
        self._save_node_hashes()
        # 构建结果：changed = 所有节点 - 新增 - 未变更
        new_set = set(new_ids)
        unchanged_set = set(unchanged_ids)
        changed_ids = [
            nid for nid in new_nodes if nid not in new_set and nid not in unchanged_set
        ]
        l3_result = {
            "nodes": new_nodes,
            "changed_ids": changed_ids,
            "new_ids": new_ids,
            "unchanged_ids": unchanged_ids,
        }

        # 返回时 document=None 表示增量路径，Layer 3 直接使用 l3_result
        return cached, None, l3_result

    # ==================================================================
    # 目录指纹（★ P1-5 修复：避免无条件全量拉取）
    # ==================================================================

    @staticmethod
    def _compute_dir_fingerprint(pages: list[dict]) -> str:
        """对页面列表计算 MD5 指纹（ID + name 排序后哈希），用于检测目录结构变化。"""
        if not pages:
            return ""
        payload = sorted((p.get("id", ""), p.get("name", "")) for p in pages)
        return hashlib.md5(
            json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()

    def _load_dir_fingerprint(self, file_key: str) -> str:
        """从 MySQL 读取上次成功拉取后存储的目录指纹。"""
        try:
            from src.pipeline.utils.figma_state_store import figma_state_store

            return figma_state_store.load_directory_fingerprint(
                file_key, version_label=self._version_label
            )
        except Exception as _e:
            _log.debug(
                "[incremental] _load_dir_fingerprint 异常: %s", _e, exc_info=True
            )
            return ""

    def _save_dir_fingerprint(self, file_key: str, fingerprint: str) -> None:
        """将目录指纹存入 MySQL，供下次拉取时比对。"""
        with guard("_save_dir_fingerprint: from src.pipeline.utils.figma_state_", log=_log, level=logging.WARNING):
            from src.pipeline.utils.figma_state_store import figma_state_store

            figma_state_store.save_directory_fingerprint(
                file_key, fingerprint, version_label=self._version_label
            )

    # ==================================================================
    # 全量拉取 + 超大文件回退
    # ==================================================================

    def _full_fetch_with_fallback(self, file_key: str, force_refresh: bool) -> tuple:
        """
        直接分层拉取（depth=2 目录 + 分批节点），跳过全量 depth=0。
        全量 depth=0 对大型设计稿极慢（>120s）、网络波动全丢，不如逐个稳。

        ★ P1-5 修复: Step A 后计算目录指纹，若与上次一致且非 force_refresh，
          跳过 Step B 批量 API 调用，直接从 MySQL 缓存读取节点。

        返回 (l1_result, None, l3_result) — document 为 None，节点已在 l3_result 中。
        """
        # —— 付费账号提示 ——
        if self._is_paid:
            _log.info(
                fmt_log("Layer 1", "分层拉取模式 (depth=2 目录 + 分批节点, 付费)") % ()
            )
        else:
            _log.info(
                fmt_log("Layer 1", "分层拉取模式 (depth=2 目录 + 分批节点, 免费)") % ()
            )

        # —— Step A: 轻量目录 (depth=2) ——
        _log.info(fmt_log("Layer 1", "先拿目录 (depth=2)..."))
        l1_result = self._fetch_directory(file_key, force_refresh)
        if not l1_result.get("success"):
            raise RuntimeError(
                "分层拉取 Layer 1 失败: %s" % l1_result.get("error", "未知")
            )
        pages = l1_result["pages"]
        _log.info(fmt_log("Layer 1", "目录拉取完成: %d 个页面" % len(pages)))

        # —— ★ 目录指纹检测：未变则跳过批量 API 调用 ——
        dir_fingerprint = self._compute_dir_fingerprint(pages)
        old_fingerprint = self._load_dir_fingerprint(file_key)

        if not force_refresh and dir_fingerprint and dir_fingerprint == old_fingerprint:
            _log.info(
                fmt_log(
                    "Layer 1",
                    "目录指纹未变 (%s)，跳过 API 调用，从缓存读取 %d 个页面"
                    % (dir_fingerprint[:8], len(pages)),
                )
            )
            all_ids = [p["id"] for p in pages if p.get("id")]
            l3_result = batch_fetch_nodes_cached(
                self._client.cache, file_key, all_ids, self._node_hashes
            )
            if l3_result.get("success"):
                # ★ 验证所有节点都在缓存中（部分缺失时 batch_fetch_nodes_cached 仍返回 success）
                cached_nodes = l3_result.get("nodes", {})
                missing_in_cache = [nid for nid in all_ids if nid not in cached_nodes]
                if not missing_in_cache:
                    _log.info(
                        fmt_log(
                            "Layer 3",
                            "缓存读取完成: %d 个节点 (0 次 API)" % len(cached_nodes),
                        )
                    )
                    return l1_result, None, l3_result
                # 部分节点缓存缺失 → 回退 API 拉取
                _log.info(
                    fmt_log(
                        "Layer 3",
                        "缓存不完整 (%d/%d 缺失)，回退 API 拉取"
                        % (len(missing_in_cache), len(all_ids)),
                    )
                )
            else:
                # 缓存全空 → 回退 API 拉取
                _log.info(fmt_log("Layer 3", "缓存全空，回退 API 拉取"))

        # —— Step B: 分批拉取节点（每批 _BATCH_SIZE 个，失败自动重试）——
        all_ids = [p["id"] for p in pages if p.get("id")]
        _log.info(
            fmt_log(
                "Layer 3",
                "分批拉取节点 (%d 个, 每批 %d)..." % (len(all_ids), self._BATCH_SIZE),
            )
        )
        l3_result = self._batch_fetch_nodes(file_key, all_ids, force_refresh)
        _log.info(
            fmt_log("Layer 3", "节点拉取完成: %d 个" % len(l3_result.get("nodes", {})))
        )

        # ★ 拉取成功后存储目录指纹，供下次比对
        # ★ 仅在所有请求节点都返回时才存指纹，防止部分批次失败致下次误用不完整缓存
        nodes = l3_result.get("nodes", {})
        failed_node_ids = l3_result.get("failed_node_ids", [])
        if dir_fingerprint and nodes:
            if not failed_node_ids:
                self._save_dir_fingerprint(file_key, dir_fingerprint)
            else:
                _log.warning(
                    fmt_log(
                        "Layer 3",
                        "部分批次未返回节点 (%d/%d 缺失)，"
                        "不保存目录指纹，下次运行将重新拉取"
                        % (len(failed_node_ids), len(all_ids)),
                    )
                )

        return l1_result, None, l3_result

    # ==================================================================
    # 缓存优先模式：纯本地读取，零 API 调用
    # ==================================================================

    def _fetch_from_cache_only(self, file_key: str) -> dict[str, Any]:
        """纯缓存模式：从 MySQL 缓存读取所有数据，不触发任何 API 调用。"""
        _log.info(fmt_log("SmartFigma", "缓存优先模式 — 不调用 API"))

        # Layer 1: 从 MySQL 缓存读取 directory
        cached = self._check_cached_file(file_key)
        if not cached:
            return {
                "success": False,
                "cache_empty": True,
                "error": "Figma 缓存为空，请点击「刷新Figma缓存」按钮拉取最新设计稿",
            }
        pages = cached["pages"]
        _log.info(fmt_log("Layer 1", "基础拉取: 从缓存拉取 %d 个页面" % len(pages)))

        # Layer 2: 纯规则筛选（不调 AI，排除明显废弃/重复页面）
        excluded_ids = []
        excluded_names = {"封面", "icon", "控件", "🗑"}
        for p in pages:
            pid = p.get("id", "")
            pname = p.get("name", "")
            if not pid:
                continue
            lower = pname.lower()
            if (
                any(e in pname for e in excluded_names)
                or "备份" in pname
                or "copy" in lower
                or "副本" in pname
                or "拷贝" in pname
            ):
                excluded_ids.append(pid)
        filtered_ids = [
            p["id"] for p in pages if p["id"] and p["id"] not in excluded_ids
        ]
        _log.info(
            fmt_log(
                "Layer 2",
                "规则处理: 根据规则过滤，剔除 %d 个无效页面，保留 %d/%d 个页面"
                % (len(excluded_ids), len(filtered_ids), len(pages)),
            )
        )

        # Layer 3: 先尝试节点级缓存，否则从全量文档中提取
        l3_result = batch_fetch_nodes_cached(
            self._client.cache, file_key, filtered_ids, self._node_hashes
        )
        # ★ 验证所有请求节点都在缓存中（部分命中时也从全量文档兜底）
        cached_nodes = l3_result.get("nodes", {}) if l3_result.get("success") else {}
        missing_in_cache = [nid for nid in filtered_ids if nid not in cached_nodes]
        if missing_in_cache:
            _log.info(
                fmt_log(
                    "Layer 3",
                    "节点缓存不完整 (%d/%d 缺失)，从全量文档提取..."
                    % (len(missing_in_cache), len(filtered_ids)),
                )
            )
            l3_result = extract_nodes_from_full_doc(
                self._client.cache, file_key, filtered_ids, self._node_hashes
            )
            if not l3_result.get("success"):
                return {
                    "success": False,
                    "cache_empty": True,
                    "error": "无法从缓存中提取 Figma 节点数据（%d 个节点缺失）"
                    % len(missing_in_cache),
                }
        _log.info(
            fmt_log(
                "Layer 3",
                "深度匹配完成: 提取 %d 个有效节点" % len(l3_result.get("nodes", {})),
            )
        )

        # Layer 4: 格式化输出
        l4_result = format_smart_output(l3_result, cached)
        _log.info(fmt_log("SmartFigma", "缓存模式完成"))

        return {
            "success": True,
            "figma_text": l4_result["figma_text"],
            "change_summary": l4_result.get("change_summary", ""),
            "changed_pages": l4_result.get("changed_pages", []),
            "new_pages": l4_result.get("new_pages", []),
            "total_pages": l4_result.get("total_pages", len(pages)),
            "layers": {
                "layer1": {"total_pages": len(pages), "cache_hit": True},
                "layer2": {
                    "filtered_count": len(filtered_ids),
                    "excluded_count": len(excluded_ids),
                    "excluded_ids": excluded_ids,
                    "ai_analysis": "缓存模式（规则筛选，无 AI 调用）",
                },
                "layer3": {
                    "fetched_nodes": len(l3_result.get("nodes", {})),
                    "changed_nodes": len(l3_result.get("changed_ids", [])),
                    "new_nodes": len(l3_result.get("new_ids", [])),
                    "unchanged_nodes": len(l3_result.get("unchanged_ids", [])),
                    "cache_hit": True,
                },
                "layer4": {"elapsed": 0},
            },
        }
