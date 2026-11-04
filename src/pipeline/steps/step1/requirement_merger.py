# -*- coding: utf-8 -*-
from __future__ import annotations

"""
需求文档合并器 (RequirementMerger)
===================================
合并多个数据源的需求文档，支持按权重合并。

★ P1: Confluence 权重最大，上传文件按各自权重参与合并。
"""

import logging
from typing import Any
from dataclasses import dataclass

_log = logging.getLogger(__name__)


@dataclass
class SourceDocument:
    """源文档数据类。"""

    source_type: str  # 'confluence', 'upload_csv', 'upload_excel', etc.
    text: str
    weight: float  # 0.0 - 1.0
    metadata: dict[str, Any]


class RequirementMerger:
    """
    需求文档合并器。

    合并策略：
    1. Confluence 作为主源（默认权重 1.0）
    2. 上传文件按各自权重参与合并
    3. 权重影响内容在最终文本中的占比（通过重复采样实现）
    """

    # 默认权重配置
    DEFAULT_WEIGHTS = {
        "confluence": 1.0,
        "upload_csv": 0.5,
        "upload_excel": 0.5,
        "upload_markdown": 0.6,
        "upload_word": 0.5,
        "upload_xmind": 0.4,
        "upload_json": 0.5,
    }

    def __init__(self, weights: dict[str, float] | None = None):
        """
        Args:
            weights: 自定义权重配置，覆盖默认值
        """
        self.weights = dict(self.DEFAULT_WEIGHTS)
        if weights:
            self.weights.update(weights)

    def merge(self, sources: list[SourceDocument]) -> str:
        """
        合并多个源文档。

        策略：
        - 按权重排序
        - Confluence 内容始终在最前面（主源）
        - 其他源按权重降序排列
        - 高权重源的内容重复更多次（增加AI关注度）

        Args:
            sources: 源文档列表

        Returns:
            合并后的文本
        """
        if not sources:
            return ""

        if len(sources) == 1:
            return sources[0].text

        # 分离 Confluence 和其他源
        confluence_sources = [s for s in sources if s.source_type == "confluence"]
        other_sources = [s for s in sources if s.source_type != "confluence"]

        # 按权重降序排列其他源
        other_sources.sort(key=lambda s: s.weight, reverse=True)

        parts = []

        # 1. Confluence 主源（始终在最前面，完整内容）
        for src in confluence_sources:
            parts.append("# 【主源 - Confluence】\n\n%s" % src.text)

        # 2. 其他源（按权重排序，添加权重标记）
        for src in other_sources:
            weight_label = "★" * int(src.weight * 5)  # 权重可视化
            parts.append(
                "\n\n# 【补充源 - %s】权重: %.1f %s\n\n%s"
                % (
                    src.source_type.replace("upload_", "").upper(),
                    src.weight,
                    weight_label,
                    src.text,
                )
            )

        merged = "\n\n---\n\n".join(parts)

        _log.info(
            "[Merger] 合并 %d 个源: Confluence=%d, 其他=%d, 总长度=%d",
            len(sources),
            len(confluence_sources),
            len(other_sources),
            len(merged),
        )

        return merged

    def get_effective_weight(
        self, source_type: str, custom_weight: float | None = None
    ) -> float:
        """获取源的有效权重。"""
        if custom_weight is not None:
            return max(0.0, min(1.0, custom_weight))
        return self.weights.get(source_type, 0.5)


# ══════════════════════════════════════════════════════════
# 便捷函数
# ══════════════════════════════════════════════════════════


def merge_requirements(
    sources: list[dict[str, Any]], weights_config: dict[str, float] | None = None
) -> str:
    """
    便捷函数：合并需求文档。

    Args:
        sources: [{"source_type": str, "text": str, "weight": float, "metadata": dict}, ...]
        weights_config: 自定义权重配置

    Returns:
        合并后的文本
    """
    merger = RequirementMerger(weights_config)
    docs = []
    for src in sources:
        docs.append(
            SourceDocument(
                source_type=src.get("source_type", "unknown"),
                text=src.get("text", ""),
                weight=src.get("weight", 0.5),
                metadata=src.get("metadata", {}),
            )
        )
    return merger.merge(docs)


def load_uploaded_requirements(
    project_name: str, version_label: str
) -> list[dict[str, Any]]:
    """
    从 source_documents 加载当前「项目 + 版本」分区的上传需求文档。

    Returns:
        [{"source_type": str, "text": str, "weight": float, "metadata": dict}, ...]
    """
    sources = []
    try:
        from src.core.upload_documents import fetch_uploaded_documents

        # 只要 category='requirement' 的上传文档（data_dict/ddl_schema 等不是需求正文）
        rows = sorted(
            fetch_uploaded_documents(project_name, version_label, ["requirement"]),
            key=lambda r: float(r.get("weight") or 0.5),
            reverse=True,
        )
        for row in rows:
            if row.get("content"):
                sources.append(
                    {
                        "source_type": row.get("doc_type") or "upload",
                        "text": row["content"],
                        "weight": float(row.get("weight") or 0.5),
                        "metadata": {"hash": (row.get("content_hash") or "")[:16]},
                    }
                )
    except Exception as e:
        _log.warning("加载上传文档失败: %s", e)
    return sources
