# -*- coding: utf-8 -*-
"""
测试用例表 读取操作
"""

import logging

_log = logging.getLogger(__name__)


def read_test_cases():
    """★ MySQL-only：从 test_cases 表读取，不再读 Excel。"""
    try:
        from src.pipeline.utils.case_store import case_store

        return case_store.list_intents()
    except Exception as e:
        _log.info("[ExcelHandler] test_cases 读取 MySQL 失败: %s", str(e)[:80])
        return []


def read_test_cases_with_meta() -> list:
    """★ MySQL-only：从 MySQL 读测试用例+元数据（置信度/审核状态等），Excel 仅作导出物。"""
    from src.pipeline.utils.case_store import case_store
    from src.pipeline.utils.review_store import review_store

    intents = case_store.list_intents()
    if not intents:
        return intents
    try:
        all_meta = review_store.list_case_meta()
        meta_map = {row["case_id"]: row for row in all_meta if row.get("case_id")}
        for intent in intents:
            cid = intent.get("case_id", "")
            meta = meta_map.get(cid)
            if meta:
                intent["_confidence"] = meta.get("confidence", 0)
                intent["_risk_level"] = meta.get("risk_level", "medium")
                intent["_review_status"] = meta.get("review_status", "pending")
    except Exception as _e:
        _log.debug("未知: %s", _e, exc_info=True)
    return intents


def get_intents_by_ids(case_ids: list, read_test_cases_fn=None):
    """按指定 case_id 列表获取测试用例。

    Args:
        case_ids: 意图 ID 列表
        read_test_cases_fn: 读取测试用例的回调函数（可选，默认使用本模块的 read_test_cases）
    """
    if read_test_cases_fn is None:
        read_test_cases_fn = read_test_cases
    if not case_ids:
        return []
    all_intents = read_test_cases_fn()
    id_set = set(str(cid).strip() for cid in case_ids if cid)
    return [c for c in all_intents if str(c.get("case_id", "")).strip() in id_set]


def get_intents_by_indices(indices: list, read_test_cases_fn=None):
    """按数组下标获取测试用例（精确定位到行，不依赖 case_id）。

    Args:
        indices: 下标列表
        read_test_cases_fn: 读取测试用例的回调函数（可选，默认使用本模块的 read_test_cases）
    """
    if read_test_cases_fn is None:
        read_test_cases_fn = read_test_cases
    if not indices:
        return []
    all_intents = read_test_cases_fn()
    idx_set = set(int(i) for i in indices if str(i).isdigit())
    return [all_intents[i] for i in sorted(idx_set) if i < len(all_intents)]
