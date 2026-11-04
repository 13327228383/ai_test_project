# -*- coding: utf-8 -*-
"""版本一致性检查（从 step3.py 拆分）"""

import logging

_log = logging.getLogger(__name__)


def _check_and_confirm_version(pipeline_self, config, excel_handler) -> bool:
    """版本一致性已由 Step2 的 requirements.content_hash / semantic_hash 链路负责。"""
    _log.info("[版本检查] 使用 Step2 需求哈希链路，跳过旧 requirement_versions 检查")
    return True
