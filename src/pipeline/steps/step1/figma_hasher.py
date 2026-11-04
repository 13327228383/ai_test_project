# -*- coding: utf-8 -*-
"""
Figma 节点哈希模块
==================
节点级哈希计算 + 持久化，用于变更检测。

**事实源迁移：** 本模块的持久化已从 ``figma_node_hashes.json`` 迁移至
MySQL ``figma_node_hashes`` 表（见 `src/pipeline/utils/figma_state_store.py`）。
JSON 文件不再作为 Step1 主运行链路的事实源。
"""

import hashlib
import json
from pathlib import Path
import logging

_log = logging.getLogger(__name__)


def compute_node_hash(node_data: dict) -> str:
    """计算节点的 MD5 哈希（基于 JSON 序列化内容）。"""
    raw = json.dumps(node_data, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def _default_store():
    """懒加载 figma_state_store，避免模块导入时触发 DB 连接失败。"""
    from src.pipeline.utils.figma_state_store import figma_state_store

    return figma_state_store


def load_node_hashes(
    hash_path: Path = None, *, version_label: str = ""
) -> dict[str, str]:
    """读取节点哈希字典。

    事实源：MySQL ``figma_node_hashes`` 表；当提供 ``hash_path`` 指向的 JSON 文件
    存在时，尝试读取该文件（显式导入场景），否则一律读 MySQL。
    """
    if hash_path is not None and Path(hash_path).is_file():
        # 显式从 JSON 导入（仅作为显式导入源）
        try:
            data = json.loads(Path(hash_path).read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("hashes"), dict):
                return data["hashes"]
        except Exception as _e:
            _log.debug("[figma_hasher] load_node_hashes 异常: %s", _e, exc_info=True)
    # 默认：MySQL
    try:
        return _default_store().load_node_hashes(version_label=version_label)
    except Exception as _e:
        _log.debug("[figma_hasher] load_node_hashes 异常: %s", _e, exc_info=True)
        return {}


def save_node_hashes(
    cache_dir: Path = None,
    hash_path: Path = None,
    node_hashes: dict[str, str] = None,
    version_label: str = "",
) -> None:
    """持久化节点哈希字典。事实源：MySQL ``figma_node_hashes`` 表。"""
    if node_hashes is None:
        return
    try:
        _default_store().save_node_hashes(node_hashes, version_label=version_label)
    except Exception as _e:
        _log.debug("[figma_hasher] save_node_hashes 异常: %s", _e, exc_info=True)
