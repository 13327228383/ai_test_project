# -*- coding: utf-8 -*-
"""图片引用过滤规则：预处理阶段，过滤文本中的图片引用/路径说明。"""

import re
from .base import PreprocessRule

# ── 图片引用正则 ──
_IMAGE_PATTERN = re.compile(
    r"!\[.*?\]\(.*?\)|"  # Markdown 图片语法
    r"https?://[^\s]*\.(?:png|jpg|jpeg|gif|svg|webp|bmp|ico)[^\s]*|"  # 图片 URL
    r"(?:图片|界面|截图|如图|下图|上图|示意图|效果图|参考图|示例图|"
    r"页面截图|页面效果|页面展示|页面样式|界面截图|界面效果)"
    r"(?:如下|所示|如下所示|展示|参考|为例|示例)|"
    r"\(.*?\.(?:png|jpg|jpeg|gif|svg|webp|bmp|ico)\)",  # 文件名引用
    re.IGNORECASE,
)


class ImageFilterRule(PreprocessRule):
    """过滤图片引用，避免被当作需求文本提取。

    过滤：
    1. Markdown 图片语法: ![alt](url)
    2. 图片 URL: http(s)://...*.png/jpg/...
    3. 中文图片引用: "图片如下"、"界面如下所示" 等
    4. 文件名引用: "(xxx.png)"
    """

    rule_id = "image_filter"
    name = "图片引用过滤"
    description = "过滤文本中的图片引用/路径说明，避免被当作需求提取"
    priority = 100

    def apply(self, text: str) -> str:
        if not text:
            return text
        return _IMAGE_PATTERN.sub("", text).strip()
