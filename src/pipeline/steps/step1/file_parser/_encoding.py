# -*- coding: utf-8 -*-
"""纯文本类文件的编码判定 — Step1 上传解析的唯一入口。

同一语义（"按什么编码读这个文件"）此前在 markdown/sql/txt/csv 四处各自实现，
降级链不一致，且都以 latin-1 兜底（永不失败，但会静默产出乱码）。本模块收敛为
一处：BOM 优先 → UTF-8 → GB18030 → latin-1（带告警），解析结果需回报实际编码。
"""

import logging
from pathlib import Path

_log = logging.getLogger(__name__)

# BOM 必须按长度降序匹配：UTF-32 BOM 以 UTF-16 BOM 为前缀
_BOMS = (
    (b"\xef\xbb\xbf", "utf-8-sig"),
    (b"\xff\xfe\x00\x00", "utf-32"),
    (b"\x00\x00\xfe\xff", "utf-32"),
    (b"\xff\xfe", "utf-16"),
    (b"\xfe\xff", "utf-16"),
)
# 无 BOM 时的降级链（GB18030 覆盖 GBK/GB2312，故不再单独列 gbk）
_FALLBACK_CHAIN = ("utf-8", "gb18030")


def decode_bytes(data: bytes, source: str = "") -> tuple[str, str]:
    """解码字节为文本，返回 (text, encoding)。

    latin-1 兜底永不抛错，但会逐字节映射成乱码字符，因此必须告警而非静默。
    """
    if not data:
        return "", "utf-8"
    for bom, encoding in _BOMS:
        if data.startswith(bom):
            return data.decode(encoding, errors="strict"), encoding
    for encoding in _FALLBACK_CHAIN:
        try:
            return data.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    _log.warning(
        "[file_parser] 无法判定文本编码，按 latin-1 兜底（可能出现乱码）: %s",
        source or "<bytes>",
    )
    return data.decode("latin-1"), "latin-1"


def decode_file(path: Path) -> tuple[str, str]:
    """读取文件并按统一规则解码，返回 (text, encoding)。"""
    return decode_bytes(Path(path).read_bytes(), source=Path(path).name)
