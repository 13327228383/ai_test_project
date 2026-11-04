# -*- coding: utf-8 -*-
"""Layer 3: 批量拉取 + 节点级变更检测 — 从 __init__.py 拆出，零逻辑变更。"""

import logging
import random
import time
from typing import Any

from src.core.log_utils import fmt_log
from src.pipeline.steps.step1.figma_hasher import compute_node_hash

_log = logging.getLogger(__name__)


class _Layer3Mixin:
    """Layer 3: GET /v1/files/:key/nodes?ids=... 批量拉取 + 节点级哈希变更检测。"""

    def _batch_fetch_nodes(
        self, file_key: str, node_ids: list[str], force_refresh: bool = False
    ) -> dict[str, Any]:
        """
        批量拉取节点数据，带节点级哈希变更检测。
        仅在分层回退路径使用（全量拉取成功时不走此方法）。

        返回:
            {"nodes": {node_id: data, ...},
             "changed_ids": [...], "new_ids": [...], "unchanged_ids": [...]}
        """
        if not node_ids:
            return {"nodes": {}, "changed_ids": [], "new_ids": [], "unchanged_ids": []}

        all_nodes = {}
        changed_ids = []
        new_ids = []
        unchanged_ids = []
        failed_node_ids: list[str] = []
        batch_logs = []

        batches = [
            node_ids[i : i + self._BATCH_SIZE]
            for i in range(0, len(node_ids), self._BATCH_SIZE)
        ]

        _log.info(
            fmt_log(
                "Layer 3",
                "开始分批拉取: %d 个节点 → %d 批 (每批 %d 个, 预计 %.0fs)"
                % (
                    len(node_ids),
                    len(batches),
                    self._BATCH_SIZE,
                    len(batches) * (self._BATCH_DELAY_MAX + 5),
                ),
            )
        )

        for batch_idx, batch_ids in enumerate(batches):
            if batch_idx > 0:
                time.sleep(random.uniform(self._BATCH_DELAY_MIN, self._BATCH_DELAY_MAX))

            # ★ 批次进度日志
            _log.info(
                fmt_log(
                    "Layer 3",
                    "批次 %d/%d (%d 节点)..."
                    % (batch_idx + 1, len(batches), len(batch_ids)),
                )
            )

            # —— 单个批次拉取，失败自动重试（最多 5 次，指数退避）——
            nodes_data = {}
            for retry in range(6):
                if retry > 0:
                    time.sleep(1.5 * (2 ** (retry - 1)))  # 1.5s → 3s → 6s
                response = self._client.fetch_figma_file_nodes(
                    node_ids=batch_ids,
                    file_key=file_key,
                    force_refresh=force_refresh,
                )
                self._api_call_count += 1
                if response.get("success"):
                    nodes_data = response["data"].get("nodes", {})
                    if nodes_data:
                        break
                # 不打印每次重试，避免日志噪音
            else:
                batch_logs.append("B%d 失败" % (batch_idx + 1))
                failed_node_ids.extend(batch_ids)
                continue

            batch_count = len(nodes_data)
            batch_logs.append("B%d: %d节点" % (batch_idx + 1, batch_count))

            for node_id, node_info in nodes_data.items():
                node_component = (
                    node_info if isinstance(node_info, dict) else {"raw": node_info}
                )
                all_nodes[node_id] = node_component

                node_hash = compute_node_hash(node_component)
                old_hash = self._node_hashes.get(node_id)

                if old_hash is None:
                    new_ids.append(node_id)
                elif node_hash != old_hash:
                    changed_ids.append(node_id)
                else:
                    unchanged_ids.append(node_id)

                self._node_hashes[node_id] = node_hash

        # 合并输出一行批次汇总
        if batch_logs:
            _log.info(fmt_log("Layer 3", "拉取明细: %s" % " | ".join(batch_logs)))

        # 持久化节点哈希
        self._save_node_hashes()

        return {
            "nodes": all_nodes,
            "changed_ids": changed_ids,
            "new_ids": new_ids,
            "unchanged_ids": unchanged_ids,
            "failed_node_ids": failed_node_ids,
        }

    def _compute_node_hash(self, node_data: dict) -> str:
        """计算节点的稳定哈希。委托到 figma_hasher.compute_node_hash。"""
        return compute_node_hash(node_data)
