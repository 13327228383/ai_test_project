# -*- coding: utf-8 -*-
"""版本感知 API（set_version + inherit_hashes_from） — 从 smart_figma_fetcher.py 拆出，零逻辑变更。"""

import logging

_log = logging.getLogger(__name__)


class _VersionAwareMixin:
    """版本切换 + 跨版本继承哈希基线。"""

    def set_version(self, version_label: str):
        """切换到指定版本，重新加载该版本的节点哈希基线和变更日志。

        调用时机：
          - Step 1 拉取前，根据当前活跃版本调用
          - 版本切换后，所有后续的 fetch_smart() 调用自动使用版本化路径
        """
        if version_label != self._version_label:
            self._version_label = version_label
            self._node_hashes = self._load_node_hashes()
            self._change_log = self._load_change_log()

    def inherit_hashes_from(self, from_version_label: str) -> int:
        """从旧版本拷贝节点哈希到当前版本的 MySQL 基线，作为增量检测锚点。

        优先级：
          1. MySQL ``figma_node_hashes`` 表中 from_version_label 条目
          2. from_version_label 对应的旧 JSON 文件（显式导入源）
          3. 全局 JSON 文件（显式导入源，兼容旧版未版本化数据）

        返回：
            继承的哈希数量；0 表示源文件不存在或为空
        """
        if not from_version_label or not self._version_label:
            return 0
        if from_version_label == self._version_label:
            return 0

        from src.core.config_loader import config
        from src.core.paths import ProjectPaths
        from src.pipeline.steps.step1.figma_hasher import (
            load_node_hashes,
            save_node_hashes,
        )
        from src.pipeline.utils.figma_state_store import figma_state_store

        paths = ProjectPaths(config.project_root)
        from_path = paths.raw_figma_node_hashes_by_version(from_version_label)
        to_path = paths.raw_figma_node_hashes_by_version(self._version_label)

        # 优先从 MySQL 读取
        try:
            src = figma_state_store.load_node_hashes(version_label=from_version_label)
            if src:
                figma_state_store.save_node_hashes(
                    src, version_label=self._version_label
                )
                self._node_hashes = dict(src)
                return len(src)
        except Exception as _e:
            _log.debug("[version_aware] 非关键异常: %s", _e, exc_info=True)
        # 回退：从旧 JSON 文件显式导入一次
        old_hashes = load_node_hashes(from_path)
        if old_hashes:
            save_node_hashes(
                to_path.parent, to_path, old_hashes, version_label=self._version_label
            )
            self._node_hashes = dict(old_hashes)
            return len(old_hashes)
        return 0
