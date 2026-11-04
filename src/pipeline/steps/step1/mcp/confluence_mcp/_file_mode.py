# -*- coding: utf-8 -*-
"""
Confluence FileMode 客户端 (FileModeClient)
===========================================
本地文件模式客户端：读取 CONFLUENCE_URL 指定的本地文件路径，
通过 FileParser 解析为标准文本，返回与 Confluence 客户端一致的格式。

支持格式：.md .markdown .csv .xlsx .xls .docx .doc .xmind .json .sql
支持多文件：CONFLUENCE_URL 可用逗号分隔多个路径，或目录路径。
"""

import logging
from pathlib import Path

_log = logging.getLogger(__name__)


class FileModeClient:
    """
    ★ 本地文件模式客户端：读取 CONFLUENCE_URL 指定的本地文件路径，
    通过 FileParser 解析为标准文本，返回与 Confluence 客户端一致的格式。

    支持格式：.md .markdown .csv .xlsx .xls .docx .doc .xmind .json .sql
    支持多文件：CONFLUENCE_URL 可用逗号分隔多个路径，或目录路径。
    """

    def __init__(self):
        from src.core.config_loader import config as _cfg

        self._config = _cfg

    # ── 公共接口：与 ConfluenceMCPClient / ConfluenceClient 保持兼容 ──

    def fetch_confluence_all(self):
        """读取本地文件并返回与 Confluence 拉取结果一致的格式。"""
        raw = self._config.confluence_url or ""
        paths_str = raw.strip() if isinstance(raw, str) else ""
        if not paths_str:
            return {
                "success": False,
                "error": "CONFLUENCE_URL 为空（file 模式需要本地文件路径）",
                "data": {"pages": [], "text": "", "titles": []},
            }

        # 支持逗号分隔多个文件
        candidate_paths = [x for p in paths_str.split(",") if (x := p.strip())]

        # 如果某个路径是目录，展开其中的支持文件
        expanded_paths = []
        for cp in candidate_paths:
            try:
                from pathlib import Path

                p = Path(cp)
                if p.is_dir():
                    from src.pipeline.steps.step1.file_parser import FileParser

                    exts = set(FileParser.EXTENSION_MAP.keys())
                    for f in sorted(p.iterdir()):
                        if f.is_file() and f.suffix.lower() in exts:
                            expanded_paths.append(str(f))
                else:
                    expanded_paths.append(cp)
            except Exception as _e:
                expanded_paths.append(cp)

        if not expanded_paths:
            return {
                "success": False,
                "error": "未找到可解析的本地文件",
                "data": {"pages": [], "text": "", "titles": []},
            }

        # 逐个解析文件，合并文本
        from src.pipeline.steps.step1.file_parser import FileParser

        all_pages = []
        all_titles = []
        text_chunks = []
        success_count = 0
        last_error = ""

        for fpath in expanded_paths:
            try:
                result = FileParser.parse(fpath)
                if result.get("success"):
                    from pathlib import Path

                    title = Path(fpath).name
                    text = result.get("text", "")
                    page_text = f"# {title}\n\n{text}\n\n---\n\n"
                    all_pages.append({"title": title, "text": text, "source": fpath})
                    all_titles.append(title)
                    text_chunks.append(page_text)
                    success_count += 1
                    _log.info(
                        "[Confluence FileMode] 解析成功: %s (%d 字符)", title, len(text)
                    )
                else:
                    last_error = result.get("error", "未知错误")
                    _log.warning(
                        "[Confluence FileMode] 解析失败: %s → %s", fpath, last_error
                    )
            except Exception as e:
                last_error = str(e)
                _log.warning("[Confluence FileMode] 解析异常: %s → %s", fpath, e)

        if success_count == 0:
            return {
                "success": False,
                "error": "所有本地文件解析失败: %s" % (last_error or "未知错误"),
                "data": {"pages": [], "text": "", "titles": []},
            }

        full_text = "".join(text_chunks).strip()
        _log.info(
            "[Confluence FileMode] 总计: %d 个文件, %d 字符",
            success_count,
            len(full_text),
        )
        return {
            "success": True,
            "data": {
                "pages": all_pages,
                "text": full_text,
                "titles": all_titles,
                "source_type": "local_file",
                "file_count": success_count,
            },
        }

    # 为与 REST 客户端接口保持兼容的占位方法
    def fetch_page(self, page_id):
        return self.fetch_confluence_all()

    def close(self):
        pass
