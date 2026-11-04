# -*- coding: utf-8 -*-
"""
file_upload package — 装饰器与常量
"""

import functools
import logging
from pathlib import Path
from flask import jsonify

from src.pipeline.steps.step1.file_parser import FileParser

_log = logging.getLogger(__name__)


def _login_required(f):
    """认证装饰器：检查用户是否已登录，未登录返回 401。"""

    @functools.wraps(f)
    def decorated(*args, **kwargs):
        from src.core.config_loader import get_current_user

        uid = get_current_user()
        if not uid:
            return jsonify({"ok": False, "success": False, "error": "请先登录"}), 401
        return f(*args, **kwargs)

    return decorated


# 扩展名白名单 = 解析器注册表（可解析 + 需转换两类），单一事实源：
# 白名单与解析能力不得各自维护，否则新增格式会在上传入口被拦截而解析器形同虚设
ALLOWED_EXTENSIONS = set(FileParser.EXTENSION_MAP) | set(FileParser.REQUIRES_CONVERSION)
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB

# ★ P1: 文件分类常量
FILE_CATEGORIES = {
    "requirement": "需求文档",
    "data_dict": "数据字典",
    "api_supplement": "接口补充",
    "design_supplement": "设计补充",
    "test_data": "测试数据",
    "ddl_schema": "DDL/Schema",
}

# ★ P1: 格式→默认分类映射
FORMAT_CATEGORY_MAP = {
    ".sql": "ddl_schema",
    ".csv": "test_data",
    ".xlsx": "data_dict",
    ".xls": "data_dict",
    ".json": "data_dict",
    ".md": "requirement",
    ".markdown": "requirement",
    ".docx": "requirement",
    ".doc": "requirement",
    ".pptx": "requirement",
    ".pdf": "requirement",
    ".xmind": "requirement",
    ".txt": "requirement",
}


def _allowed_file(filename: str) -> bool:
    """检查文件扩展名是否允许。"""
    return Path(filename).suffix.lower() in ALLOWED_EXTENSIONS
