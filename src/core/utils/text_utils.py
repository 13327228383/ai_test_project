# -*- coding: utf-8 -*-
"""统一文本处理工具 — 关键词提取、字符串清理等。"""
import re
from collections import Counter


def extract_keywords(text: str) -> list[str]:
    """中英文混合关键词提取（不依赖 jieba）。
    从 rag_engine / deployment_matcher 的重复实现中提取统一版本。"""
    if not text:
        return []

    keywords = []

    # 提取英文单词和标识符（≥3字符）
    english_words = re.findall(r"[a-zA-Z_][a-zA-Z0-9_]{2,}", text)
    keywords.extend(w.lower() for w in english_words)

    # 提取中文 bigram / trigram
    chinese_chars = re.findall(r"[一-鿿]+", text)
    for segment in chinese_chars:
        for n in [2, 3]:
            for i in range(len(segment) - n + 1):
                keywords.append(segment[i : i + n])

    # 提取 API 路径片段
    paths = re.findall(r"/[a-zA-Z0-9/_\-]+", text)
    for p in paths:
        keywords.append(p.lower())

    # 去重，保留出现频率 > 1 或长度 ≥ 3 的词
    counter = Counter(keywords)
    return [kw for kw, cnt in counter.items() if cnt > 1 or len(kw) >= 3]


def normalize_content(text: str) -> str:
    """内容标准化（5步确定性规则，零AI）— 用于哈希计算前统一文本格式。

    依据需求规格说明书 §3.1.4：
    1. 行尾符统一为 \\n（\\r\\n → \\n，\\r → \\n）
    2. 去除每行首尾空白字符（strip()）
    3. 压缩连续空白行：≥ 2 个连续空行 → 保留 1 个空行
    4. 文本末尾最多保留 1 个空行
    5. 不做大小写转换、不做全角/半角转换、不去除行内空白

    用于 source_documents / source_hashes 哈希计算前，避免格式变化导致误判变更。
    """
    if not text:
        return ""
    # 1. 行尾符统一为 \n
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    # 2. 去除每行首尾空白字符
    lines = [line.strip() for line in normalized.split("\n")]
    # 3. 压缩连续空白行：≥ 2 个连续空行 → 保留 1 个空行
    compressed = []
    prev_blank = False
    for line in lines:
        is_blank = len(line) == 0
        if is_blank and prev_blank:
            continue  # 跳过连续空行
        compressed.append(line)
        prev_blank = is_blank
    # 4. 文本末尾最多保留 1 个空行
    while compressed and compressed[-1] == "":
        compressed.pop()
    compressed.append("")  # 末尾恰好 1 个空行
    return "\n".join(compressed)
