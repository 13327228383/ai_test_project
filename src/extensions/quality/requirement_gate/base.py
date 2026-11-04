# -*- coding: utf-8 -*-
"""
门禁基础 Mixin
包含：__init__, _load_merged_requirements
"""

import logging
from src.core.guarded import guard

_log = logging.getLogger(__name__)


class _GateBaseMixin:
    """门禁基础 Mixin —— 初始化与需求文档加载。"""

    def __init__(self, requirement_text: str = None):
        """
        Args:
            requirement_text: 合并的需求文档文本。如果为None，首次访问时自动加载。
                              ★ 必须与拆解时使用的 raw_text 一致，保证核对基准相同。
        """
        # ★ 只保存配置，不触发网络加载；网络加载延迟到首次访问 requirement_text 时
        self._requirement_text = requirement_text
        self._requirement_lower = requirement_text.lower() if requirement_text else ""

    @property
    def requirement_text(self):
        """延迟加载：首次访问时自动拉取合并需求文档。"""
        if self._requirement_text is None:
            self._requirement_text = self._load_merged_requirements()
            self._requirement_lower = (
                self._requirement_text.lower() if self._requirement_text else ""
            )
        return self._requirement_text

    @requirement_text.setter
    def requirement_text(self, value):
        self._requirement_text = value
        self._requirement_lower = value.lower() if value else ""

    def set_requirement_text(self, requirement_text: str):
        """
        显式设置需求文档文本（用于确保核对基准与拆解时一致）。

        ★ 调用方应传入与 feature_driven_breakdown 相同的 raw_text。
        """
        self.requirement_text = requirement_text or ""
        _log.debug(
            "[RequirementGate] 显式设置需求文本: %d 字符", len(self.requirement_text)
        )

    @staticmethod
    def _load_merged_requirements() -> str:
        """加载合并的需求文档（Confluence + 上传文件）。

        ★  通用性修复：优先消费 Step1 已落库的 source_documents
        （confluence 需求正文），网络实时拉取降级为兜底。根因(3层)：
        ①原实现每次访问都实时请求 Confluence，Step1 白拉一次（重复网络 I/O）；
        ②违反「Step 之间只能通过 MySQL 表传递数据」红线（跨 Step 内存/网络传递）；
        ③目标系统迁移/离线时 Gate 因网络不可达误判「基准缺失」阻断流水线。
        """
        with guard("_load_merged_requirements: from src.core.config_loader import c", log=_log, level=logging.ERROR):
            from src.core.config_loader import config

            # 1. 优先从 source_documents 读取 Step1 拉取的 Confluence 需求正文
            confluence_text = ""
            with guard("_load_merged_requirements: from src.core.db_manager import get_", log=_log, level=logging.WARNING):
                from src.core.db_manager import get_connection, release_connection

                _conn = get_connection()
                try:
                    _project = getattr(config, "project_name", "") or ""
                    _rows = _conn.execute(
                        "SELECT content FROM source_documents "
                        "WHERE project=%s AND doc_type=%s ORDER BY updated_at DESC LIMIT 1",
                        (_project, "confluence"),
                    ).fetchall()
                    if _rows:
                        confluence_text = str(_rows[0].get("content") or "")
                finally:
                    release_connection(_conn)

            # 1b. source_documents 无内容时，降级为实时网络拉取（向后兼容）
            if not confluence_text:
                with guard("_load_merged_requirements: from src.pipeline.steps.step1.conflu", log=_log, level=logging.WARNING):
                    from src.pipeline.steps.step1.confluence_client import (
                        confluence_client,
                    )

                    cf_result = confluence_client.fetch_confluence_raw(
                        config.confluence_page_id
                    )
                    if cf_result and cf_result.get("success") and cf_result.get("data"):
                        confluence_text = cf_result["data"].get("text", "")

            # 2. 加载上传的文档（按当前版本分区）
            from src.pipeline.steps.step1.requirement_merger import (
                load_uploaded_requirements,
                merge_requirements,
            )
            from src.core.versioning import current_version_label as _ver_label_of

            uploaded = load_uploaded_requirements(
                getattr(config, "project_name", "") or "",
                _ver_label_of(getattr(config, "project_name", "") or ""),
            )

            # 3. 合并
            sources = []
            if confluence_text:
                sources.append(
                    {
                        "source_type": "confluence",
                        "text": confluence_text,
                        "weight": 1.0,
                        "metadata": {},
                    }
                )
            sources.extend(uploaded)

            if sources:
                merged = merge_requirements(sources)
                _log.info("[RequirementGate] 加载合并需求文档: %d 字符", len(merged))
                return merged


        return ""
