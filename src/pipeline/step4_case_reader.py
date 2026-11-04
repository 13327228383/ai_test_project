# -*- coding: utf-8 -*-
"""
step4_case_reader.py — Step4 测试用例读取器
===============================================
从 scheduler.py 拆分出的独立模块，负责 Step4 阶段的多策略测试用例读取。

★ 安全拆分原则：
  - 零业务逻辑变更，仅物理位置移动
  - 保持所有导入路径、函数签名、返回值 100% 一致
  - 原文件通过门面模式兼容
"""

import logging

_log = logging.getLogger(__name__)


def _filter_non_testable(intents: list[dict]) -> list[dict]:
    """过滤 Step3 标记为 non_testable 的用例，避免执行必然失败的用例。"""
    filtered = []
    skipped = 0
    for intent in intents:
        if not isinstance(intent, dict):
            continue
        payload = intent.get("payload") or {}
        if isinstance(payload, str):
            try:
                import json as _json

                payload = _json.loads(payload)
            except Exception:
                payload = {}
        non_testable = bool(intent.get("non_testable") or payload.get("non_testable"))
        if non_testable:
            skipped += 1
            continue
        filtered.append(intent)
    if skipped:
        _log.info("Step4过滤 non_testable 用例: %d 条", skipped)
    return filtered


def read_intents_with_meta(excel_handler, include_virtual_inherited: bool = False):
    """Step4 默认候选测试用例读取。

    默认只取当前版本物理测试用例，避免 V2 执行时自动拉入未变需求的历史虚拟继承测试用例。
    如需整套版本回归，可由调用方显式传 include_virtual_inherited=True。
    """
    from src.pipeline.utils.case_store import case_store

    intents = case_store.list_intents_with_meta(
        include_virtual_inherited=include_virtual_inherited
    )
    intents = _filter_non_testable(intents)
    _log.info(
        "Step4候选读取: MySQL test_cases (%d 条, include_virtual_inherited=%s)",
        len(intents),
        include_virtual_inherited,
    )
    return intents, "mysql"


def read_intents_by_ids(
    excel_handler, case_ids, include_virtual_inherited: bool = False
):
    """Step4 指定 case_id 读取：仅使用 MySQL test_cases。"""
    from src.pipeline.utils.case_store import case_store

    intents = case_store.get_intents_by_ids(
        case_ids, include_virtual_inherited=include_virtual_inherited
    )
    intents = _filter_non_testable(intents)
    _log.info(
        "Step4指定测试用例读取: MySQL test_cases (%d 条, include_virtual_inherited=%s)",
        len(intents),
        include_virtual_inherited,
    )
    return intents, "mysql"


def read_failed_intents(
    excel_handler, user_id=None, include_virtual_inherited: bool = False
):
    """Step4 失败重跑读取：仅使用 MySQL test_cases/executions。"""
    from src.pipeline.utils.case_store import case_store

    intents = case_store.get_failed_intents(
        user_id=user_id, include_virtual_inherited=include_virtual_inherited
    )
    intents = _filter_non_testable(intents)
    _log.info(
        "Step4失败重跑读取: MySQL test_cases (%d 条, include_virtual_inherited=%s)",
        len(intents),
        include_virtual_inherited,
    )
    return intents, "mysql"


def read_unexecuted_cases_by_files(
    excel_handler,
    case_asset_files,
    user_id=None,
    include_virtual_inherited: bool = False,
):
    """Step4 多文件未执行测试用例读取：仅使用 MySQL test_cases.case_asset_file。"""
    from src.pipeline.utils.case_store import case_store

    intents = case_store.get_unexecuted_cases_by_files(
        case_asset_files,
        user_id=user_id,
        include_virtual_inherited=include_virtual_inherited,
    )
    intents = _filter_non_testable(intents)
    _log.info(
        "Step4多文件读取: MySQL test_cases (%d 条, include_virtual_inherited=%s)",
        len(intents),
        include_virtual_inherited,
    )
    return intents, "mysql"


def read_intents_for_versions(
    excel_handler, version_ids, *, case_ids=None, include_executed: bool = False
):
    """多版本执行读取：按版本读取物理测试用例，并按测试用例身份去重。

    不读取虚拟继承测试用例，避免 V1 未变测试用例在 V2 中再次执行。
    如果同一 case_id 在多个勾选版本中都存在，保留后勾选/后读取的版本记录。
    """
    from src.core.versioning import execution_version_context
    from src.pipeline.utils.case_store import case_store

    selected_versions = []
    for vid in version_ids or []:
        try:
            iv = int(vid)
            if iv > 0 and iv not in selected_versions:
                selected_versions.append(iv)
        except Exception as _e:
            _log.debug(
                "[step4_case_reader] read_intents_for_versions 异常: %s",
                _e,
                exc_info=True,
            )
            continue
    if not selected_versions:
        return [], "versioned"

    merged = {}
    for vid in selected_versions:
        with execution_version_context(vid):
            if case_ids:
                intents = case_store.get_intents_by_ids(
                    case_ids, include_virtual_inherited=False
                )
                intents = [
                    c
                    for c in intents
                    if str(c.get("case_source", "")) != "inherited_virtual"
                ]
            else:
                intents = case_store.list_intents_with_meta(
                    include_virtual_inherited=False
                )
            if not include_executed:
                intents = [
                    c
                    for c in intents
                    if str(c.get("verification_status", "")).strip().upper()
                    not in ("PASS", "FAIL")
                ]
            for intent in intents:
                cid = str(intent.get("case_id", "") or "").strip()
                if not cid:
                    continue
                dedupe_key = str(intent.get("origin_case_id", "") or cid).strip()
                intent["_execution_version_id"] = vid
                intent["_execution_dedupe_key"] = dedupe_key
                merged[dedupe_key] = intent
        _log.info(
            "Step4多版本读取: version_id=%s 当前累计去重后 %d 条", vid, len(merged)
        )
    intents = list(merged.values())
    intents = _filter_non_testable(intents)
    return intents, "versioned"
