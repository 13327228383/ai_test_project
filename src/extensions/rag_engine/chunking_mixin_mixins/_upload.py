# -*- coding: utf-8 -*-
"""上传文件分块 mixin"""

import hashlib
import json
import logging

from src.core.upload_documents import (
    UPLOAD_CATEGORY_CHUNK_TYPES,
    fetch_uploaded_documents,
)

_log = logging.getLogger("rag_engine")


def _segment_strategy(file_format: str) -> str:
    """★ #13: 根据文件格式返回拆分策略名（用于 meta 标记和调试）。"""
    fmt = (file_format or "").lower().strip()
    if fmt in ("excel", "csv"):
        return "table_row_split"
    if fmt == "xmind":
        return "xmind_topic_split"
    return "outline_segment"


class ChunkingUploadMixin:
    """上传文件分块相关方法。"""

    def _load_uploaded_file_chunks(
        self, project: str = "", version_label: str = ""
    ) -> list[dict]:
        """★ P1: 加载上传文件并按分类分块。

        project/version_label: 显式传入（由 build_index 从入口线程解析后传入）——
                 executor 工作线程里 thread-local 为空，`_get_project_name()` 回退到
                 config 静态值会读错版本分区。
        文档行与正文统一由 fetch_uploaded_documents 提供（含正文，不再二次按
        hash 查一次），保证索引与覆盖审计/需求抽取读到的是同一批行。
        chunk_id 用库行身份 doc_type（project+version 内唯一），不用 content_hash：
        同正文不同格式会算出相同 hash → 两个文档的 chunk_id 撞车。
        """
        chunks = []
        try:
            project = project or self._get_project_name()
            version_label = version_label or self._get_project_version()
            rows = fetch_uploaded_documents(project, version_label)

            for row in rows:
                cat = row.get("category", "requirement")
                fmt = row.get("file_format", "")
                doc_type = row.get("doc_type", "") or "upload"

                # 根据分类映射到RAG chunk type
                chunk_type = UPLOAD_CATEGORY_CHUNK_TYPES.get(cat, "upload")

                content = row.get("content", "") or ""
                if not content.strip():
                    continue

                # 按内容长度分段（每段不超过500字）
                max_len = getattr(self, "_UPLOAD_SEGMENT_MAX_LEN", 500)
                # ★ #13: 传入 file_format 触发结构化拆分（excel/csv/xmind）
                segments = self._segment_text(content, max_len=max_len, file_format=fmt)
                for i, seg in enumerate(segments):
                    # ★ G2 (2026-07-10): 段落级哈希，增量重建时比对
                    #   chapter_hash = MD5(段落文本)[:16]，相同则保留旧向量不重新嵌入
                    _chapter_hash = hashlib.md5(seg.encode("utf-8")).hexdigest()[:16]
                    chunks.append(
                        {
                            "type": chunk_type,
                            "chunk_id": f"upload:{doc_type}:{i}",
                            "text": seg,
                            "meta": json.dumps(
                                {
                                    "filename": row.get("filename", ""),
                                    "format": fmt,
                                    "category": cat,
                                    "segment": i,
                                    # ★ #13: 拆分策略标记
                                    "segment_strategy": _segment_strategy(fmt),
                                },
                                ensure_ascii=False,
                            ),
                            "keywords": self._extract_keywords(seg),
                            "chapter_hash": _chapter_hash,
                        }
                    )

            _log.info("[RagEngine] 上传文件分块: %d chunks", len(chunks))
        except Exception as e:
            _log.warning("[RagEngine] 加载上传文件失败: %s", e)
        return chunks

    def _segment_text(
        self, text: str, max_len: int = None, file_format: str = ""
    ) -> list[str]:
        """按格式选择拆分策略，保证覆盖与长度口径一致。

        ★ 2026-10-24: 取消"超长文档整篇塌缩成一条功能点摘要"的旧行为（章节粒度
          全部丢失，且摘要尾部超出嵌入截断窗口后对检索不可见）。概览只作为额外
          chunk 附加，不替代原文切片。
          - excel/csv: 按表格行拆分（保留表头）
          - xmind: 按一级主题拆分
          - 其它: 标题层级递归切分（_segment_by_outline）→ 自然边界 → 兜底硬切
        """
        if max_len is None:
            max_len = getattr(self, "_UPLOAD_SEGMENT_MAX_LEN", 500)
        # 统一长度口径：chunk 不得超出嵌入窗口，否则尾部无法进入向量
        max_len = min(max_len, getattr(self, "_EMBED_CHUNK_MAX_LEN", 1000))

        if not text or not text.strip():
            return []

        fmt = (file_format or "").lower().strip()

        # ── #13: Excel/CSV 按表格结构化拆分 ──
        if fmt in ("excel", "csv"):
            segs = self._segment_table(text, max_len=max_len)
            if segs:
                return segs
            # 表格拆分失败 → 降级到通用分段

        # ── #13: XMind 按主题节点拆分 ──
        if fmt == "xmind":
            segs = self._segment_xmind(text, max_len=max_len)
            if segs:
                return segs
            # 主题拆分失败 → 降级到通用分段

        segments = self._segment_by_outline(text, max_len=max_len)
        if not segments:
            segments = self._smart_segment(text, max_len=max_len)
        if not segments:
            # 最后兜底：任何策略都失效时按长度切，禁止整篇丢失
            segments = self._hard_cut(text, max_len)
        if not segments:
            return []

        # 长文档额外附一条文档级概览（供"系统有哪些模块"这类全局问题检索）
        if len(text) > 3000:
            try:
                from src.extensions.rag_engine.chunking_mixin_mixins._segment import (
                    extract_feature_points,
                )

                overview = extract_feature_points(text, max_chars=max_len)
                overview = (overview or "").strip()
                # 概览必须是"提炼"而非原文复制（无结构长文时提取器会回退原文段落，
                # 那样只是重复内容，没有检索增量）
                _compact = "".join(overview.split())
                if overview and _compact not in "".join(text.split()):
                    segments.append("[文档概览]\n" + overview)
            except Exception as _e:
                _log.debug("[UploadSegment] 概览提取失败: %s", str(_e)[:80])

        return segments

    def _segment_table(self, text: str, max_len: int = 500) -> list[str]:
        """★ #13: Excel/CSV 表格结构化拆分。

        策略：
        1. 识别 Markdown 表格（| col1 | col2 |）
        2. 第一行表头 + 分隔行 + 数据行
        3. 按数据行数拆分：每段 N 行数据 + 表头（每段保持完整表格结构）
        4. 单行过长（>max_len）→ 整行作为一个 chunk
        """
        if not text or not text.strip():
            return []

        lines = text.split("\n")
        # 识别表格行（以 | 开头）
        table_lines = [ln for ln in lines if ln.strip().startswith("|")]
        if len(table_lines) < 3:
            # 不是标准 Markdown 表格 → 降级
            return []

        # 第一行表头 + 第二行分隔符
        header_line = table_lines[0]
        sep_line = table_lines[1]
        data_lines = table_lines[2:]

        if not data_lines:
            return [text]

        # 按长度估算每段数据行数（表头 + 分隔行 + N 行 ≤ max_len）
        header_block_len = len(header_line) + len(sep_line) + 2  # +2 个换行
        if header_block_len >= max_len:
            # 表头本身就超长 → 整表作为一段
            return ["\n".join(table_lines)]

        # 估算每行平均长度
        avg_row_len = sum(len(ln) for ln in data_lines[:10]) / max(
            min(10, len(data_lines)), 1
        )
        rows_per_seg = max(1, int((max_len - header_block_len) / max(avg_row_len, 20)))

        segments = []
        for i in range(0, len(data_lines), rows_per_seg):
            chunk_lines = [header_line, sep_line] + data_lines[i : i + rows_per_seg]
            seg = "\n".join(chunk_lines)
            if seg.strip():
                segments.append(seg)

        _log.debug(
            "[UploadSegment][table] 拆分: %d 行数据 → %d 段 (每段 %d 行)",
            len(data_lines),
            len(segments),
            rows_per_seg,
        )
        return segments

    def _segment_xmind(self, text: str, max_len: int = 500) -> list[str]:
        """★ #13: XMind 思维导图按一级主题节点拆分。

        策略：
        1. 识别缩进式大纲（- title /   - subtitle）
        2. 按一级主题（无缩进的 - title）分组
        3. 每个一级主题 + 其所有子节点作为一段
        4. 单主题过长（>max_len）→ 按二级主题再拆分
        """
        if not text or not text.strip():
            return []

        lines = text.split("\n")
        # 识别大纲行：以 "- " 开头（可能有缩进）
        outline_lines = [ln for ln in lines if ln.strip().startswith("-")]
        if len(outline_lines) < 2:
            return []

        # 按一级主题分组（无缩进或仅 1 空格缩进视为一级）
        groups: list[list[str]] = []
        current_group: list[str] = []

        for ln in lines:
            stripped = ln.lstrip()
            if not stripped.startswith("-"):
                continue
            # 判断缩进层级：行首空格数
            indent = len(ln) - len(stripped)
            if indent == 0:
                # 一级主题 → 开新组
                if current_group:
                    groups.append(current_group)
                current_group = [ln]
            else:
                # 子节点 → 加入当前组
                if current_group:
                    current_group.append(ln)
                else:
                    # 没有父节点 → 独立成组
                    current_group = [ln]

        if current_group:
            groups.append(current_group)

        if not groups:
            return []

        # 每组转成一段文本，超长则按二级主题再拆分
        segments = []
        for grp in groups:
            seg_text = "\n".join(grp)
            if len(seg_text) <= max_len:
                segments.append(seg_text)
            else:
                # 按二级主题再拆分
                sub_segments = self._split_xmind_by_level2(grp, max_len=max_len)
                segments.extend(sub_segments if sub_segments else [seg_text[:max_len]])

        _log.debug(
            "[UploadSegment][xmind] 拆分: %d 个一级主题 → %d 段",
            len(groups),
            len(segments),
        )
        return segments

    def _split_xmind_by_level2(
        self, group_lines: list[str], max_len: int = 500
    ) -> list[str]:
        """★ #13: XMind 超长主题按二级主题再拆分。"""
        if not group_lines:
            return []

        # 第一行是一级主题
        root_line = group_lines[0]
        child_lines = group_lines[1:]

        # 按二级主题（缩进 2 空格）分组
        sub_groups: list[list[str]] = []
        current_sub: list[str] = []

        for ln in child_lines:
            stripped = ln.lstrip()
            indent = len(ln) - len(stripped)
            if indent <= 2:
                # 二级主题 → 开新子组（含一级主题作为上下文）
                if current_sub:
                    sub_groups.append(current_sub)
                current_sub = [root_line, ln]
            else:
                # 更深层级 → 加入当前子组
                if current_sub:
                    current_sub.append(ln)
                else:
                    current_sub = [root_line, ln]

        if current_sub:
            sub_groups.append(current_sub)

        segments = []
        for sub in sub_groups:
            seg_text = "\n".join(sub)
            if seg_text.strip():
                segments.append(seg_text)

        return segments
