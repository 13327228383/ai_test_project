# -*- coding: utf-8 -*-
"""
认证 Blueprint 核心定义与公共依赖
"""

from flask import Blueprint
import logging

_log = logging.getLogger(__name__)

from src.backend.helpers import _validate_local_ollama, _validate_key

auth_bp = Blueprint("auth", __name__)
