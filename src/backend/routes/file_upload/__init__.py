# -*- coding: utf-8 -*-
"""
Step1 文件上传路由 — ★ P2 优化版
=================================
支持 CSV / Excel / Markdown / Word / XMind / JSON / SQL / TXT 格式的需求文档上传。

★ P2 优化：
  1. 不保存物理文件，只提取数据入库
  2. 支持多文件逐个添加
  3. 文件内容存入 source_documents 表
"""

from flask import Blueprint

file_upload_bp = Blueprint("file_upload", __name__)

from . import _decorators
from . import _upload
from . import _list
from . import _delete
