# -*- coding: utf-8 -*-
"""
用例生成规则 API Blueprint
==========================
提供 Step3 用例生成规则的查询接口（只读，规则由代码文件定义，不存数据库）。
"""

from flask import Blueprint, jsonify
import logging

_log = logging.getLogger(__name__)

case_rules_bp = Blueprint("case_rules", __name__)

from src.backend.routes.case_rules import _routes  # noqa: E402, F811
