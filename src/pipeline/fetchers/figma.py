# -*- coding: utf-8 -*-
"""
Step1DataFetcher Figma 拉取 mixin

职责：
- _fetch_figma — Figma 拉取（缓存检查、智能拉取、增量模式、基线继承）
"""

import logging
from src.core.guarded import guard
import sys as _sys
from typing import Any

from src.core.config_loader import config

_log = logging.getLogger(__name__)
_TRUNC_DETAIL = 80
_TRUNC_SUMMARY = 120


def _import_figma_images_to_mysql() -> None:
    """将 MCP download_figma_images 下载的图片导入 MySQL 并清理磁盘（非阻断）。"""
    with guard("_import_figma_images_to_mysql: from src.pipeline.utils.figma_image_", log=_log):
        from src.pipeline.utils.figma_image_store import import_figma_images

        import_figma_images(project="", version_label="")


def _scan_image_nodes(document: dict, max_nodes: int = 50) -> list:
    """递归扫描 Figma document 中的图片节点。

    识别规则：
    - VECTOR 类型节点 → SVG 导出
    - 有 imageRef fill 的节点 → PNG 导出
    - 有 gifRef fill 的节点 → GIF 导出

    Returns:
        [{nodeId, fileName, imageRef?}, ...] 最多 max_nodes 条
    """
    if not isinstance(document, dict):
        return []
    result = []
    _seen = set()

    def _walk(node):
        if len(result) >= max_nodes:
            return
        if not isinstance(node, dict):
            return
        nid = node.get("id", "")
        if not nid or nid in _seen:
            return
        ntype = node.get("type", "")
        fills = node.get("fills") or []

        # VECTOR 节点 → SVG
        if ntype == "VECTOR":
            _seen.add(nid)
            result.append(
                {
                    "nodeId": nid,
                    "fileName": "%s.svg" % nid.replace(":", "-").replace(";", "_"),
                }
            )
            return

        # 有 imageRef/gifRef fill 的节点
        for fill in fills:
            if not isinstance(fill, dict):
                continue
            if fill.get("type") == "IMAGE":
                _seen.add(nid)
                img_ref = fill.get("imageRef", "")
                gif_ref = fill.get("gifRef", "")
                if gif_ref:
                    result.append(
                        {
                            "nodeId": nid,
                            "fileName": "%s.gif"
                            % nid.replace(":", "-").replace(";", "_"),
                            "gifRef": gif_ref,
                        }
                    )
                elif img_ref:
                    result.append(
                        {
                            "nodeId": nid,
                            "fileName": "%s.png"
                            % nid.replace(":", "-").replace(";", "_"),
                            "imageRef": img_ref,
                        }
                    )
                else:
                    # IMAGE fill 无 imageRef → 按 nodeId 渲染 PNG
                    result.append(
                        {
                            "nodeId": nid,
                            "fileName": "%s.png"
                            % nid.replace(":", "-").replace(";", "_"),
                        }
                    )
                break

        # 递归子节点
        for child in node.get("children") or []:
            _walk(child)

    _walk(document)
    return result


def _download_figma_images(figma_url: str, full_data: dict) -> None:
    """扫描 Figma 数据中的图片节点，调用 MCP 下载到 --image-dir 目录。

    下载后的图片由 _import_figma_images_to_mysql() 统一入库。
    非阻断：失败只记日志，不影响主流程。
    """
    with guard("_download_figma_images: from src.pipeline.steps.step1._smart", log=_log, level=logging.WARNING):
        from src.pipeline.steps.step1._smart_fetcher_utils import extract_file_key

        _file_key = extract_file_key(figma_url)
        if not _file_key or not full_data:
            return
        _doc = full_data.get("document", {}) if isinstance(full_data, dict) else {}
        if not _doc:
            return
        _img_nodes = _scan_image_nodes(_doc)
        if not _img_nodes:
            _log.info("[figma_images] 未发现图片节点，跳过下载")
            return
        _log.info("[figma_images] 发现 %d 个图片节点，开始下载...", len(_img_nodes))
        from src.pipeline.steps.step1.smart_figma_fetcher import smart_figma_fetcher

        _result = smart_figma_fetcher._client.download_images(
            file_key=_file_key,
            nodes=_img_nodes,
            local_path=".",
        )
        if _result.get("success"):
            _log.info(
                "[figma_images] ✓ 下载完成: %d 个图片", _result.get("downloaded", 0)
            )
        else:
            _log.warning("[figma_images] 下载失败: %s", _result.get("error", "未知"))


class _Step1FigmaMixin:
    """Figma 拉取能力。"""

    def _fetch_figma(
        self,
        active_ver,
        _version_label,
        prev_active_version_label,
        cf_changed,
        figma_force_refresh,
        cf_ok=False,
        old_req_text="",
        req_text="",
    ):
        """拉取 Figma，返回 (figma_ok, ui_context, figma_result)。

        ★ MySQL 唯一事实源：节点哈希走 figma_node_hashes 表，Confluence 哈希走
          source_confluence_hashes 表，不再依赖磁盘路径参数。
        """
        figma_url = config.figma_url
        figma_ok = False
        ui_context = ""
        figma_result = {}
        _figma_has_cache = False
        _figma_cache_stale = False
        _inherited_count = 0

        if not figma_url:
            _log.info("Figma: 未配置，跳过")
            _log.info("  ○ Figma: 未配置，跳过")
            return figma_ok, ui_context, figma_result

        # 缓存新鲜度检查
        with guard("_fetch_figma: from src.pipeline.steps.step1.figma_", log=_log, level=logging.WARNING):
            from src.pipeline.steps.step1.figma_cache import (
                get_figma_cache_info,
                check_figma_modified_time,
            )

            _figma_has_cache = get_figma_cache_info().get("has_cache", False)
            if _figma_has_cache and not figma_force_refresh:
                meta = check_figma_modified_time()
                if meta.get("success"):
                    if not meta.get("is_cache_fresh", False):
                        _figma_cache_stale = True
                        _log.info(
                            "Figma: 检测到远程设计稿较新，将刷新缓存 (%s)",
                            meta.get("design_updated_at", "")[:19],
                        )
                    else:
                        _log.info("Figma: 远程修改时间校验通过，缓存仍可用")
                else:
                    _log.warning(
                        "Figma 缓存新鲜度校验失败，继续使用现有策略: %s",
                        str(meta.get("error", "未知"))[:_TRUNC_DETAIL],
                    )

        # 版本感知：基线继承（★ MySQL figma_node_hashes 表，不再读磁盘 JSON）
        try:
            from src.pipeline.steps.step1.smart_figma_fetcher import smart_figma_fetcher
            from src.pipeline.steps.step1.figma_hasher import (
                load_node_hashes,
                save_node_hashes,
            )

            if active_ver:
                smart_figma_fetcher.set_version(active_ver["version_label"])
                # set_version 已加载当前版本哈希到 _node_hashes；为空则继承
                if not smart_figma_fetcher._node_hashes:
                    if (
                        prev_active_version_label
                        and prev_active_version_label != active_ver["version_label"]
                    ):
                        _inherited_count = smart_figma_fetcher.inherit_hashes_from(
                            prev_active_version_label
                        )
                    if _inherited_count == 0:
                        # 全局基线（version_label=""）
                        _global_hashes = load_node_hashes(version_label="")
                        if _global_hashes:
                            save_node_hashes(
                                node_hashes=_global_hashes,
                                version_label=active_ver["version_label"],
                            )
                            smart_figma_fetcher._node_hashes = dict(_global_hashes)
                            _inherited_count = len(_global_hashes)
                    if _inherited_count == 0:
                        # 任意历史版本（查 MySQL figma_node_hashes 其他 version_label，按 updated_at 倒序）
                        with guard("_fetch_figma: from src.core.db_manager import get_", log=_log):
                            from src.core.db_manager import get_connection

                            _proj = getattr(config, "project_name", "") or ""
                            _conn = get_connection()
                            try:
                                _rows = _conn.execute(
                                    "SELECT version_label FROM figma_node_hashes "
                                    "WHERE project=%s AND version_label NOT IN (%s, '') "
                                    "GROUP BY version_label ORDER BY MAX(updated_at) DESC LIMIT 1",
                                    (_proj, active_ver["version_label"]),
                                ).fetchall()
                            finally:
                                try:
                                    _conn.close()
                                except Exception as _pass_e:
                                    _log.warning(
                                        "figma.py:104 异常被吞: %s",
                                        _pass_e,
                                        exc_info=True,
                                    )
                            for _r in _rows or []:
                                _cand_hashes = load_node_hashes(
                                    version_label=_r["version_label"]
                                )
                                if _cand_hashes:
                                    save_node_hashes(
                                        node_hashes=_cand_hashes,
                                        version_label=active_ver["version_label"],
                                    )
                                    smart_figma_fetcher._node_hashes = dict(
                                        _cand_hashes
                                    )
                                    _inherited_count = len(_cand_hashes)
                                    break
                    if _inherited_count > 0:
                        _log.info("Figma: 继承 %d 个节点哈希作为基线", _inherited_count)
                    else:
                        _log.info("Figma: 无节点哈希可继承，将全量拉取")
        except ImportError:
            _log.debug(
                "smart_figma_fetcher 版本感知初始化导入失败，将在下方 fetch 块中处理"
            )

        # 判断是否需要拉取
        # ★ MySQL：Confluence 哈希是否存在（取代 cf_hash_path.is_file()，查 source_confluence_hashes）
        _cf_hash_exists = False
        try:
            from src.pipeline.utils.source_hashes_store import source_hashes_store

            _cf_h = source_hashes_store.load_confluence_hash(
                version_label=_version_label
            )
            _cf_hash_exists = bool(_cf_h and _cf_h.get("overall"))
        except Exception as _e:
            _log.debug("[figma] 非关键异常: %s", _e, exc_info=True)
            _cf_hash_exists = False
        should_fetch = (
            cf_changed
            or figma_force_refresh
            or _figma_cache_stale
            or not _cf_hash_exists
            or not _figma_has_cache
        )

        if not should_fetch:
            _log.info("Figma: 跳过 (需求未变更，无需重拉)")
            _log.info("  ○ Figma: 跳过 (需求未变更，无需重拉)")
            return figma_ok, ui_context, figma_result

        _log.info("  ⏳ Figma: 正在拉取...")
        import time

        _t0 = time.time()

        try:
            from src.pipeline.steps.step1.smart_figma_fetcher import smart_figma_fetcher

            # ★ 修复：恢复增量页面 AI 分析（旧 step1.py 的 _analyze_requirement_changes 调用）
            # Confluence 变更时，AI 分析哪些 Figma 页面需要更新；有继承基线时走增量
            incremental_page_ids = None
            if cf_ok and cf_changed and old_req_text and not figma_force_refresh:
                try:
                    from src.pipeline.helpers import analyze_requirement_changes

                    incremental_page_ids = analyze_requirement_changes(
                        old_req_text, req_text
                    )
                    if incremental_page_ids and _inherited_count > 0:
                        _log.info(
                            "Figma: AI 分析得出 %d 个变更页面，增量拉取",
                            len(incremental_page_ids),
                        )
                except Exception as _e:
                    _log.warning(
                        "Figma: 增量分析失败，回退全量: %s", str(_e)[:_TRUNC_DETAIL]
                    )
                    incremental_page_ids = None

            _effective_figma_force_refresh = figma_force_refresh or _figma_cache_stale
            cache_only = not _effective_figma_force_refresh
            figma_result = smart_figma_fetcher.fetch_smart(
                figma_url,
                force_refresh=_effective_figma_force_refresh,
                cache_only=cache_only,
                page_ids=incremental_page_ids,
            )
            figma_ok = figma_result.get("success", False)

            # 缓存为空时回退全量
            if not figma_ok and figma_result.get("cache_empty") and cache_only:
                _log.info("Figma: 缓存为空，自动触发全量 API 拉取...")
                _sys.stdout.flush()
                figma_result = smart_figma_fetcher.fetch_smart(
                    figma_url,
                    force_refresh=True,
                    cache_only=False,
                    page_ids=None,
                )
                figma_ok = figma_result.get("success", False)

            _elapsed = time.time() - _t0
            _log.info(
                "Figma: %s (智能分层拉取, %.1fs)",
                "PASS" if figma_ok else "FAIL",
                _elapsed,
            )
            _log.info(
                "  %s Figma: %s (智能分层拉取, %.1fs)",
                "✓" if figma_ok else "✗",
                "PASS" if figma_ok else "FAIL",
                _elapsed,
            )
            _sys.stdout.flush()

            if figma_ok:
                ui_context = figma_result.get("figma_text", "")
                if ui_context:
                    self._ui_context = ui_context
                    _log.info(
                        "Figma 解析: %d 字符 UI 结构描述已缓存 (Layer 1-4)",
                        len(ui_context),
                    )
                change_summary = figma_result.get("change_summary", "")
                if change_summary:
                    _log.info(
                        "Figma 变更: %s",
                        change_summary[:_TRUNC_SUMMARY].replace("\n", " / "),
                    )

                # ★ 保存 Figma 事实数据到独立表（RAG 索引重建时从此读取）
                with guard("_fetch_figma: from src.pipeline.steps.step1._smart", log=_log, level=logging.WARNING):
                    from src.pipeline.steps.step1._smart_fetcher_utils import (
                        extract_file_key,
                    )

                    _file_key = extract_file_key(figma_url)
                    if _file_key:
                        _full_data = smart_figma_fetcher._client.cache.get_full_file(
                            _file_key
                        )
                        if _full_data:
                            _doc = _full_data.get("document", {})
                            _comps = _full_data.get("components", {})
                            _pages = (
                                figma_result.get("layers", {}).get("layer1", {}) or {}
                            )
                            # 从 layer1 可能只拿到数字，需要直接从 document 提取
                            from src.pipeline.steps.step1._smart_fetcher_utils import (
                                extract_top_pages,
                            )

                            _page_list = extract_top_pages(_doc) if _doc else []
                            from src.pipeline.utils.figma_page_store import (
                                save_figma_to_tables,
                            )

                            _proj = getattr(config, "project_name", "") or ""
                            _stats = save_figma_to_tables(
                                _doc, _comps, _page_list, _proj, _version_label
                            )
                            _log.info(
                                "[figma_store] 已保存: %d pages, %d nodes, %d components",
                                _stats["pages"],
                                _stats["nodes"],
                                _stats["components"],
                            )
                        else:
                            _log.debug(
                                "[figma_store] Figma 全量缓存未命中，跳过独立表保存"
                            )

                # ★ 下载 Figma 图片节点（SVG/PNG），供还原度验收使用
                with guard("_fetch_figma: from src.pipeline.steps.step1._smart", log=_log):
                    from src.pipeline.steps.step1._smart_fetcher_utils import (
                        extract_file_key as _extract_key,
                    )

                    _img_file_key = _extract_key(figma_url)
                    if _img_file_key:
                        _img_full = smart_figma_fetcher._client.cache.get_full_file(
                            _img_file_key
                        )
                        _download_figma_images(figma_url, _img_full)
            else:
                err_msg = figma_result.get("error", "未知")
                _log.error("Figma 错误: %s", err_msg)
        except ImportError:
            _log.warning("smart_figma_fetcher 导入失败，回退到传统拉取")
            from src.pipeline.steps.step1 import fetch_all

            figma_result = fetch_all()
            figma_ok = figma_result.get("figma", {}).get("success", False)
            if figma_ok:
                from src.pipeline.steps.step1.figma_parser import (
                    parse_figma_file_result,
                )

                ui_context = parse_figma_file_result(figma_result["figma"])
                if ui_context:
                    self._ui_context = ui_context
        except Exception as e:
            _log.warning("智能 Figma 拉取失败: %s", e)

        # ★ 全走MySQL：将 MCP download_figma_images 下载的图片导入 MySQL，清理磁盘残留
        _import_figma_images_to_mysql()

        return figma_ok, ui_context, figma_result
