# -*- coding: utf-8 -*-
from __future__ import annotations

"""
FigmaMCPClient — 与旧 FigmaClient 保持相同业务接口，底层改为 MCP 调用 + 新缓存。
"""

import time
from typing import Any
import logging
import yaml

_log = logging.getLogger(__name__)

from src.pipeline.steps.step1.figma_cache import FigmaCache
from src.pipeline.steps.step1.figma_quota import quota_tracker

# 用于类型提示（避免循环导入）
from .mcp_stdio import MCPStdioClient


class FigmaMCPClient:
    """
    与旧 FigmaClient 保持相同业务接口，底层改为 MCP 调用 + 新缓存。
    """

    @staticmethod
    def _CACHE_DIR():
        """★ 已废弃：FigmaCache 已迁移至 MySQL figma_mcp_cache 表，不再需要磁盘目录。"""
        from src.core.config_loader import config
        from src.core.paths import ProjectPaths

        return ProjectPaths(config.project_root).cache_figma_dir

    @staticmethod
    def _LEGACY_PATH():
        """★ 已废弃：FigmaCache 已迁移至 MySQL，不再使用 MySQL 文件。"""
        return FigmaMCPClient._CACHE_DIR() / "figma_cache.sqlite"

    def __init__(self, session: MCPStdioClient):
        self._session = session
        self.cache = FigmaCache(None)  # ★ FigmaCache 已走 MySQL，无需磁盘路径

    # ==================================================================
    # 公共 API（与 FigmaClient 兼容）
    # ==================================================================

    # 网络故障重试配置
    _MAX_RETRIES = 5  # 最多重试 5 次（共 6 次尝试）
    _RETRY_DELAYS = [3, 5, 8, 10, 15]  # 渐进退避（秒）

    def fetch_file(
        self, file_key: str, force_refresh: bool = False, depth: int = None
    ) -> dict[str, Any]:
        """
        拉取 Figma 文件完整数据。
        返回: {"success": bool, "data": {file_key, name, document, components, ...}}
        """
        if not file_key:
            return self._error("Figma file_key 为空")
        if depth is None:
            depth = 0

        # —— 缓存优先 ——
        if not force_refresh:
            cached = self.cache.get(file_key, "", depth)
            if cached is not None:
                _log.info(
                    "  [MCP Cache] 命中缓存 (file_key=%s, depth=%d)", file_key, depth
                )
                return self._ok(cached)

        # —— MCP 调用（带网络故障重试）——
        _log.info(
            "  [MCP] 调用 get_figma_data (fileKey=%s, depth=%s)...", file_key, depth
        )
        mcp_args = {"fileKey": file_key}
        if depth > 0:
            mcp_args["depth"] = depth

        last_error = None
        for attempt in range(self._MAX_RETRIES + 1):
            if attempt > 0:
                delay = self._RETRY_DELAYS[attempt - 1]
                _log.info(
                    "  [MCP] 重试 %d/%d (%ds 后)...", attempt, self._MAX_RETRIES, delay
                )
                time.sleep(delay)

            # —— 调用 MCP ——
            try:
                raw_data = self._session.call_tool("get_figma_data", mcp_args)
            except Exception as e:
                last_error = "MCP 调用失败: %s" % e
                _log.error("  [MCP] %s", last_error)
                # ★ 永久错误（403/401/404）不重试，直接返回
                err_str = str(e).lower()
                if any(
                    code in err_str
                    for code in (
                        "403",
                        "401",
                        "404",
                        "invalid token",
                        "unauthorized",
                        "not found",
                    )
                ):
                    _log.error("  [MCP] 永久错误（非网络问题），跳过重试")
                    quota_tracker.record_call(False, last_error)
                    return self._error(last_error)
                continue

            if not raw_data:
                last_error = "MCP 返回空数据"
                _log.error("  [MCP] %s", last_error)
                continue

            # —— 整理为标准格式（兼容 v0.13.2 simplified JSON 与旧格式）——
            # v0.13.2 (--format json): {metadata:{name,components}, nodes:[{id,name,type,children}], ...}
            # 旧版/Figma原始 REST: {name, document:{children:[]}, components:{}, lastModified}
            _metadata = raw_data.get("metadata")
            _metadata = _metadata if isinstance(_metadata, dict) else {}
            _nodes = raw_data.get("nodes")
            _doc = raw_data.get("document")
            if _doc is None and isinstance(_nodes, list):
                # v0.13.2: 无 document 键，用 nodes list 构造（nodes 即顶层页面/节点树）
                _doc = {"children": _nodes}
            result_data = {
                "file_key": file_key,
                "name": raw_data.get("name", "") or _metadata.get("name", ""),
                "document": _doc if _doc is not None else {},
                "components": raw_data.get("components")
                or _metadata.get("components", {}),
                "last_modified": raw_data.get("lastModified", "")
                or _metadata.get("lastModified", ""),
                "thumbnail_url": raw_data.get("thumbnailUrl", ""),
            }

            # —— 有效性校验 ——
            document = result_data.get("document", {})
            has_children = bool(document.get("children"))
            has_name = bool(result_data.get("name"))
            raw_text = raw_data.get("_raw_text", "")

            # ★ MCP 可能把完整数据放在 _raw_text（YAML 格式），需解析
            if raw_text and not has_children:
                _parsed = self._parse_raw_text(raw_text)
                if _parsed:
                    result_data["name"] = (
                        _parsed.get("name") or result_data.get("name") or "Figma File"
                    )
                    result_data["document"] = {"children": _parsed.get("children", [])}
                    document = result_data["document"]
                    has_children = bool(document.get("children"))
                    has_name = bool(result_data.get("name"))
                    if has_children:
                        _log.info(
                            "  [MCP] 从 _raw_text 解析成功: %d 顶层节点, name=%s"
                            % (
                                len(document.get("children", [])),
                                result_data.get("name"),
                            )
                        )

            if not has_children and not has_name:
                # ★ 优先检测 Figma API 具体错误（Token 过期、403、限流、网络等）
                api_error = self._detect_api_error(raw_text) if raw_text else None
                _log.info(
                    "  [MCP] 响应数据无效 (name=%r, children=%d)，不缓存"
                    % (result_data.get("name"), len(document.get("children", [])))
                )
                if api_error:
                    _log.error("  [MCP] 检测到 API 错误: %s", api_error)
                    # 网络类错误 → 重试；永久错误 → 直接返回
                    is_transient = any(
                        kw in api_error
                        for kw in (
                            "网络",
                            "代理",
                            "超时",
                            "connect",
                            "fetch failed",
                            "proxy",
                        )
                    )
                    if is_transient and attempt < self._MAX_RETRIES:
                        last_error = api_error
                        continue
                    quota_tracker.record_call(False, api_error)
                    return self._error(api_error)
                last_error = "MCP 返回数据无效，document 无内容"
                # 数据无效也可能是网络问题导致，重试一次
                if attempt < self._MAX_RETRIES:
                    continue
                quota_tracker.record_call(False, last_error)
                return self._error(last_error)

            # —— 写入缓存 ——
            self.cache.set(file_key, "", depth, result_data)
            quota_tracker.record_call(True)
            _log.info("  [MCP Cache] 已缓存 (file_key=%s, depth=%d)", file_key, depth)
            return self._ok(result_data)

        # 所有重试已用尽
        quota_tracker.record_call(False, last_error or "未知错误")
        return self._error(last_error or "MCP 调用失败")

    def fetch_nodes(
        self, file_key: str, node_ids: list[str], force_refresh: bool = False
    ) -> dict[str, Any]:
        """
        按需拉取指定节点数据（逐个调用 MCP，合并结果）。
        返回: {"success": bool, "data": {file_key, nodes: {id: {...}, ...}}}
        """
        if not file_key:
            return self._error("Figma file_key 为空")
        if not node_ids:
            return self.fetch_file(file_key, force_refresh=force_refresh)

        all_nodes = {}
        missing_ids = []

        # —— 缓存优先：检查每个节点 ——
        if not force_refresh:
            for nid in node_ids:
                cached = self.cache.get(file_key, nid, depth=6)
                if cached is not None:
                    all_nodes[nid] = cached
                else:
                    missing_ids.append(nid)
        else:
            missing_ids = list(node_ids)

        if not missing_ids and not force_refresh:
            _log.info(
                "  [MCP Cache] 所有节点缓存命中 (%d/%d)", len(all_nodes), len(node_ids)
            )
            return self._ok(
                file_key=file_key, nodes=all_nodes, name=self._get_cached_name(file_key)
            )

        # —— MCP 调用：逐个拉取缺失节点 ——
        total_missing = len(missing_ids)
        total_all = len(node_ids)
        _log.info(
            "  [MCP] 拉取 %d/%d 个节点 (逐个 MCP 调用, 预计 %.0f-%.0fs)...",
            total_missing,
            total_all,
            total_missing * 3,
            total_missing * 10,
        )
        api_calls = 0
        api_errors = []
        # ★ 进度日志间隔：每 _PROGRESS_INTERVAL 个节点输出一次进度
        _PROGRESS_INTERVAL = max(1, total_missing // 10)  # 约 10 次进度更新
        for idx, nid in enumerate(missing_ids):
            if idx > 0:
                time.sleep(0.3)  # 节点间小延迟，避免冲击 MCP Server
            # ★ 进度日志（每 _PROGRESS_INTERVAL 个或第一个/最后一个）
            if idx % _PROGRESS_INTERVAL == 0 or idx == total_missing - 1:
                _log.info(
                    "  [MCP] 节点进度: %d/%d (%d%%)",
                    idx + 1,
                    total_missing,
                    int((idx + 1) / total_missing * 100),
                )
            try:
                raw_data = self._session.call_tool(
                    "get_figma_data",
                    {
                        "fileKey": file_key,
                        "nodeId": nid,
                        "depth": 6,
                    },
                )
                api_calls += 1
                if raw_data:
                    # 提取节点数据：兼容 v0.13.2 (nodes 为 list[{id,...}]) 与旧版 (nodes 为 dict{id:{...}})
                    _nodes = raw_data.get("nodes", [])
                    if isinstance(_nodes, dict):
                        node_entry = _nodes.get(nid, raw_data)
                    elif isinstance(_nodes, list):
                        node_entry = next(
                            (
                                n
                                for n in _nodes
                                if isinstance(n, dict) and n.get("id") == nid
                            ),
                            raw_data,
                        )
                    else:
                        node_entry = raw_data
                    node_info = (
                        node_entry
                        if isinstance(node_entry, dict)
                        else {"raw": node_entry}
                    )
                    all_nodes[nid] = node_info
                    self.cache.set(file_key, nid, 6, node_info)
            except Exception as e:
                api_errors.append(str(e))
                _log.info("  [MCP] 节点 %s 拉取失败: %s", nid, e)
                continue

        if api_calls > 0:
            quota_tracker.record_call(
                len(api_errors) == 0, "; ".join(api_errors) if api_errors else None
            )

        _log.info("  [MCP] 完成: %d/%d 个节点已获取", len(all_nodes), len(node_ids))
        return self._ok(
            file_key=file_key, nodes=all_nodes, name=self._get_cached_name(file_key)
        )

    # ==================================================================
    # 图片下载
    # ==================================================================

    def download_images(
        self,
        file_key: str,
        nodes: list[dict[str, str]],
        local_path: str = "",
        png_scale: int = 2,
    ) -> dict[str, Any]:
        """调用 MCP download_figma_images 下载图片节点到 --image-dir 指定目录。

        Args:
            file_key: Figma 文件 key
            nodes:    图片节点列表，每项含 nodeId + fileName（必填），可选 imageRef/gifRef
            local_path: 相对于 --image-dir 的子目录（空则存到 --image-dir 根目录）
            png_scale:  PNG 导出缩放比（默认 2）

        Returns:
            {"success": bool, "downloaded": int, "error": str}
        """
        if not file_key or not nodes:
            return {
                "success": False,
                "downloaded": 0,
                "error": "file_key 或 nodes 为空",
            }
        try:
            mcp_args = {
                "fileKey": file_key,
                "nodes": nodes,
                "localPath": local_path or ".",
                "pngScale": png_scale,
            }
            raw = self._session.call_tool("download_figma_images", mcp_args)
            if raw is not None:
                return {"success": True, "downloaded": len(nodes), "raw": raw}
            return {"success": False, "downloaded": 0, "error": "MCP 返回空"}
        except Exception as e:
            _log.warning("  [MCP] download_figma_images 失败: %s", str(e)[:120])
            return {"success": False, "downloaded": 0, "error": str(e)[:120]}

    # ==================================================================
    # 辅助
    # ==================================================================

    def _get_cached_name(self, file_key: str) -> str:
        """从全量缓存中读取文件名。"""
        full = self.cache.get_full_file(file_key)
        if full:
            return full.get("name", "")
        return ""

    @staticmethod
    def _detect_api_error(raw_text: str) -> str | None:
        """检测 Figma API 返回的错误信息，返回人类可读的描述；无错误则返回 None。

        ★ 加固（2026-06-23）：正常 Figma 数据是长文本（节点树+全局变量，常 >1KB），
        其中节点哈希/坐标/ID 可能含 "403"/"401"/"500" 等数字子串。旧逻辑用朴素
        子串匹配会把正常数据误判为 API 错误（伪 403，如节点哈希 ...bc732403 命中 "403"）。
        改为：仅对短文本（≤1000 字符，符合 API 错误响应特征）做关键字匹配，
        长文本直接判定为正常数据。
        """
        if not raw_text:
            return None
        _rt = raw_text.strip()
        # 长文本是正常 Figma 数据（节点树/全局变量），不可能是 API 错误响应
        if len(_rt) > 1000:
            return None
        # —— HTTP 状态码 + 常见错误关键字 ——
        patterns = [
            # (匹配关键字, 错误描述)
            (
                "Token expired",
                "Figma Token 已过期 (403)，请在 Figma 设置中重新生成 PAT",
            ),
            ("403", "Figma API 返回 403 Forbidden，Token 无效或无权限访问此文件"),
            ("401", "Figma API 返回 401 Unauthorized，Token 未配置或已失效"),
            ("404", "Figma API 返回 404 Not Found，文件不存在或已被删除"),
            ("Rate limit", "Figma API 限流 (429)，请稍后再试"),
            ("Too Many Requests", "Figma API 限流 (429)，请稍后再试"),
            ("503", "Figma API 返回 503，服务暂不可用，请稍后重试"),
            ("500", "Figma API 返回 500 内部错误，请稍后重试"),
            ("timeout", "Figma API 请求超时，请检查网络或稍后重试"),
            ("timed out", "Figma API 请求超时，请检查网络或稍后重试"),
            # 网络连接错误
            (
                "fetch failed",
                "无法连接到 Figma API (api.figma.com)，请检查网络或配置代理",
            ),
            (
                "Could not connect",
                "无法连接到 Figma API，请检查网络或配置 FIGMA_PROXY 代理",
            ),
            ("Failed to make request", "Figma API 请求失败，网络不可达或需要配置代理"),
            ("proxy", "Figma API 需要代理访问，请配置 --proxy 或 FIGMA_PROXY 环境变量"),
        ]
        _lower = _rt.lower()
        for keyword, description in patterns:
            if keyword.lower() in _lower:
                return description
        # 如果 _raw_text 以 "Error" 开头，取第一行作为错误信息
        if _lower.startswith("error"):
            first_line = _rt.split("\n")[0].strip()
            return first_line[:200] if len(first_line) > 200 else first_line
        return None

    @staticmethod
    def _ok(data: Any = None, **extra) -> dict[str, Any]:
        if data is None:
            data = dict(extra) if extra else {}
        return {"success": True, "data": data}

    @staticmethod
    def _parse_raw_text(raw_text: str) -> dict:
        """将 MCP v0.11 返回的 _raw_text YAML 格式解析为 document 结构。"""
        try:
            data = yaml.safe_load(raw_text)
        except Exception as _e:
            _log.debug("[figma_client] _parse_raw_text 异常: %s", _e, exc_info=True)
            return {}

        if not isinstance(data, dict):
            return {}

        name = ""
        children = []

        # 提取 metadata.name
        meta = data.get("metadata", {})
        if isinstance(meta, dict):
            name = str(meta.get("name", ""))

        # 提取 nodes 作为 document.children
        nodes = data.get("nodes", [])
        if isinstance(nodes, list):
            children = nodes

        if children:
            return {"name": name, "children": children}
        return {}

    @staticmethod
    def _error(message: str, status_code: int = None) -> dict[str, Any]:
        result = {"success": False, "error": message}
        if status_code is not None:
            result["status_code"] = status_code
        return result
