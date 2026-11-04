# -*- coding: utf-8 -*-
"""
Intent Validator - P0 防幻觉：交叉校验（Package Facade）
"""

import logging

from ._data import load_cross_check_data, _cross_check_cache
from ._api import cross_check_apis
from ._ui import cross_check_ui_elements
from ._enum import cross_check_enums
from ._db_trace import cross_check_db_tables
from ._action import cross_check_actions
from ._runner import run_cross_checks

_log = logging.getLogger(__name__)

__all__ = [
    "load_cross_check_data",
    "cross_check_apis",
    "cross_check_ui_elements",
    "cross_check_enums",
    "cross_check_db_tables",
    "cross_check_actions",
    "run_cross_checks",
]
