# -*- coding: utf-8 -*-
"""
缓存信息摘要（仅读本地 MySQL，不消耗 API 配额）
"""

import json
import threading
import time
from typing import Any
import logging

_log = logging.getLogger(__name__)

from src.pipeline.steps.step1.figma_cache import FigmaCache

# =============================================================================
# 缓存信息摘要
# =============================================================================

_figma_cache_info_cache = None
_figma_cache_info_ts = 0.0
_figma_cache_info_lock = threading.Lock()


def get_figma_cache_info(force: bool = False) -> dict[str, Any]:
    """
    获取 Figma 缓存状态摘要（仅读本地 MySQL，绝不调 Figma API，不消耗配额）。
    force=True: 重读 MySQL 文件（仅在 API 拉取成功写入新数据后使用）。
    默认: 首次读取后会话级缓存，后续直接返回内存副本。
    """
    global _figma_cache_info_cache, _figma_cache_info_ts
    from src.core.config_loader import config

    # ★ 修复：用 project_name 做缓存 key，项目切换后 project_root 不变但 project_name 会变
    cache_key = str(getattr(config, "project_name", ""))

    # ★ 快速路径：无锁检查缓存是否命中（减少锁争用）
    if (
        _figma_cache_info_cache
        and not force
        and _figma_cache_info_cache.get("_proj") == cache_key
    ):
        return {k: v for k, v in _figma_cache_info_cache.items() if k != "_proj"}

    # ★ 需要重新读取 MySQL，加锁防止并发写
    with _figma_cache_info_lock:
        # 双重检查：可能另一个线程已在我们等锁时写入了
        if (
            _figma_cache_info_cache
            and not force
            and _figma_cache_info_cache.get("_proj") == cache_key
        ):
            return {k: v for k, v in _figma_cache_info_cache.items() if k != "_proj"}

        file_key = config.figma_file_key
        from src.core.paths import ProjectPaths as _PP2

        db_path = _PP2(config.project_root).raw_figma_cache_db
        cache = FigmaCache(db_path)

        full_with_meta = cache.get_full_file_with_meta(file_key)
        if not full_with_meta:
            _figma_cache_info_cache = {
                "has_cache": False,
                "file_key": file_key,
                "_proj": cache_key,
            }
            _figma_cache_info_ts = time.time()
            return {"has_cache": False, "file_key": file_key}

        # 分离元数据
        cache_created = full_with_meta.pop("_cache_created", 0) or 0
        cache_updated = full_with_meta.pop("_cache_updated", 0) or 0
        cache_fetched_at = cache_updated or cache_created
        # ★ 修复（2026-06-23）：_cache_updated/_cache_created 从 MySQL DATETIME 读回时为
        # datetime 对象，直接与 now(time.time()=float) 相减触发
        # "unsupported operand type(s) for -: 'float' and 'datetime.datetime'"。
        # 归一化为 float 时间戳，兼容 float/int/datetime/ISO 字符串。
        if cache_fetched_at and not isinstance(cache_fetched_at, (int, float)):
            try:
                if hasattr(cache_fetched_at, "timestamp"):
                    cache_fetched_at = cache_fetched_at.timestamp()
                else:
                    from datetime import datetime as _dt

                    cache_fetched_at = _dt.fromisoformat(
                        str(cache_fetched_at).replace("Z", "+00:00")
                    ).timestamp()
            except Exception as _e:
                _log.debug("[_cache_info] 非关键异常: %s", _e, exc_info=True)
                cache_fetched_at = 0.0

        doc = full_with_meta.get("document", {})
        components = full_with_meta.get("components", {})

        # 统计 CANVAS 和顶级页面
        canvases = []

        def is_frame(c):
            return isinstance(c, dict) and c.get("type") == "FRAME"

        for c in doc.get("children", []):
            if c.get("type") == "CANVAS":
                fkids = [ch for ch in c.get("children", []) if is_frame(ch)]
                canvases.append({"name": c.get("name", ""), "frames": len(fkids)})

        canvas_count = len(canvases)
        page_count = sum(cv["frames"] for cv in canvases)

        # 全量统计节点数
        node_count = [0]

        def count_nodes(n):
            if not isinstance(n, dict):
                return
            node_count[0] += 1
            for ch in n.get("children", []):
                count_nodes(ch)

        count_nodes(doc)

        # 时效性计算
        now = time.time()
        design_mod_str = full_with_meta.get("last_modified", "")
        design_mod_ts = 0.0
        design_age_hours = 0.0
        if design_mod_str:
            try:
                from datetime import datetime

                dt = datetime.fromisoformat(design_mod_str.replace("Z", "+00:00"))
                design_mod_ts = dt.timestamp()
                design_age_hours = (now - design_mod_ts) / 3600
            except Exception as _e:
                _log.debug("[_cache_info] count_nodes 异常: %s", _e, exc_info=True)

        # 抓取时间距今
        fetch_age_hours = (now - cache_fetched_at) / 3600 if cache_fetched_at else 0.0
        # 缓存滞后 = 设计修改时间到缓存抓取时间之间的间隔
        fetch_lag_hours = (
            (cache_fetched_at - design_mod_ts) / 3600
            if design_mod_ts and cache_fetched_at
            else 0.0
        )
        # 是否过时：缓存抓取时间 > 7 天（设计稿随版本发布更新，一星期一版）
        is_stale = fetch_age_hours > 168  # 7天，设计稿随版本发布更新
        # 数据完整：设计修改时间在那之后缓存已拉取（抓取时间 > 修改时间）
        is_synced = not design_mod_ts or cache_fetched_at >= design_mod_ts

        try:
            size_mb = len(json.dumps(full_with_meta, ensure_ascii=False)) / (1024**2)
        except Exception as _e:
            _log.debug("[_cache_info] 非关键异常: %s", _e, exc_info=True)
            size_mb = 0.0

        # ★ 统计该 file_key 所有缓存条目的总大小（含节点详情）
        total_size_mb = size_mb
        try:
            with cache._lock:
                rows = cache._conn.execute(
                    "SELECT SUM(LENGTH(response_json)) FROM %s WHERE file_key = %%s"
                    % cache._TABLE,
                    (file_key,),
                ).fetchone()
            if rows and rows[0]:
                total_size_mb = rows[0] / (1024**2)
        except Exception as _e:
            _log.debug("[_cache_info] 非关键异常: %s", _e, exc_info=True)

        _figma_cache_info_cache = {
            "has_cache": True,
            "file_name": full_with_meta.get("name", ""),
            "file_key": file_key,
            "design_modified": design_mod_str,
            "design_modified_ts": design_mod_ts,
            "design_age_hours": design_age_hours,
            "cache_fetched_at": cache_fetched_at,
            "cache_fetched_str": (
                time.strftime("%Y-%m-%d %H:%M", time.localtime(cache_fetched_at))
                if cache_fetched_at
                else ""
            ),
            "fetch_age_hours": fetch_age_hours,
            "fetch_lag_hours": fetch_lag_hours,
            "is_synced": is_synced,
            "canvas_count": canvas_count,
            "page_count": page_count,
            "component_count": len(components),
            "node_count": node_count[0],
            "size_mb": size_mb,
            "total_size_mb": round(total_size_mb, 1),
            "is_stale": is_stale,
            "_proj": cache_key,
        }
        _figma_cache_info_ts = time.time()
        return {k: v for k, v in _figma_cache_info_cache.items() if k != "_proj"}
