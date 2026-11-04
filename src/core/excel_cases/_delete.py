# -*- coding: utf-8 -*-
"""
测试用例表 删除操作
"""

import logging

_log = logging.getLogger(__name__)


def delete_test_case(case_id: str) -> bool:
    """从 test_cases/case_requirement_mapping/case_meta 删除测试用例。"""
    try:
        from src.pipeline.utils.case_store import case_store
        from src.pipeline.utils.case_requirement_tracker import case_requirement_tracker
        from src.pipeline.utils.review_store import review_store

        # 先删 intent-req 映射
        if not case_requirement_tracker.remove_intent(case_id):
            _log.info("[ExcelHandler] intent-req 映射清理失败: %s", case_id)
        # 级联清理 case_meta
        review_store.delete_case_meta(case_id)
        # 删 test_cases 表
        ok = case_store.delete_intent(case_id)
        if ok:
            _log.info("[MySQL] test_case 已删除: %s", case_id)
        return bool(ok)
    except Exception as e:
        _log.info("[ExcelHandler] 测试用例删除失败: %s", e)
        return False


def delete_intent(case_id: str) -> bool:
    """删除测试用例（对外统一接口）。"""
    return delete_test_case(case_id)
