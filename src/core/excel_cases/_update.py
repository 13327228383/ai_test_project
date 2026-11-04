# -*- coding: utf-8 -*-
"""
测试用例表 更新操作
"""

import logging
from src.core.guarded import guard
from datetime import datetime

_log = logging.getLogger(__name__)


def update_test_case(case_id: str, updates: dict) -> bool:
    """★ MySQL-only：更新 test_cases 表字段 + 通过 review_store 标记已修改，不再 raw SQL。"""
    if not case_id or not updates:
        return False
    try:
        from src.pipeline.utils.case_store import case_store
        from src.pipeline.utils.review_store import review_store

        # 规范化字段（避免写入非法值）
        normalized_updates = {}
        for key, val in updates.items():
            if key in [
                "title",
                "case_actions_json",
                "expected_outcome",
                "case_type",
                "risk_level",
                "precondition",
            ]:
                normalized_updates[key] = val
        if not normalized_updates:
            return False
        # 更新单条测试用例：允许编辑虚拟继承测试用例时仅物理化这一条，避免把完整展示视图批量落库。
        intent = case_store.get_intent(case_id)
        if not intent:
            return False
        intent.update(normalized_updates)
        if str(intent.get("case_source", "")) == "inherited_virtual":
            intent["case_source"] = "manual_override"
            intent["valid_status"] = "valid"
        ok = case_store.upsert_intents(
            [intent], case_asset_file=str(intent.get("case_asset_file", "") or "")
        )
        if not ok:
            raise RuntimeError("test_cases 更新失败")
        # 标记 case_meta 为已修改
        review_store.mark_intent_modified(case_id)
        _log.info(
            "[MySQL] test_case 已更新: %s fields=%s",
            case_id,
            ",".join(normalized_updates.keys()),
        )
        return True
    except Exception as e:
        _log.info("[ExcelHandler] 测试用例更新失败: %s", str(e)[:80])
        return False


def update_intent_status(
    case_id,
    status,
    screenshot_path,
    failure_reason,
    case_asset_path,
    get_case_db_fn,
    case_row=0,
    failure_category="",
    failure_chain="",
    check_type="",
    expected="",
    actual="",
    details="",
):
    """★ MySQL-only：写入执行记录到 executions 表（按用户隔离），不再操作 Excel。

    Args:
        case_id: 意图 ID
        status: 执行状态
        screenshot_path: 截图路径
        failure_reason: 失败原因
        case_asset_path: 意图资产路径（替代 self._case_asset_path）
        get_case_db_fn: 获取数据库连接的回调函数（替代 self._get_case_db）
        failure_category: 失败分类（PRODUCT_BUG/HARNESS_ERROR/ENV_ERROR 等）
        failure_chain: 失败链路详情
        check_type: 检查类型（pre_gate/browser_exec/api_exec/step_exec）
        expected: 期望值（断言期望）
        actual: 实际值（断言实际结果）
        details: 详情JSON（check 级别明细）
    """
    try:
        from src.core.config_loader import config, get_current_user

        uid = get_current_user() or "anonymous"
        project = config.project_name or ""
        try:
            from src.core.versioning import current_version_id

            active_version_id = current_version_id(project)
        except Exception:
            active_version_id = 0
        case_asset_file = case_asset_path.name if case_asset_path else ""
        # ★ 修复(07-01): 从线程上下文获取 run_id，与 record_execution 路径保持一致
        try:
            from src.backend.state import _thread_run_id

            run_id = getattr(_thread_run_id, "value", "") or ""
        except Exception:
            run_id = ""
        # 直接写入 executions 表（修正不存在的 execution_store）
        ok = False
        c = None
        try:
            c = get_case_db_fn()
            # ★ case_row 防御性转换：空字符串/None 转为 0
            try:
                ir = (
                    case_row
                    if isinstance(case_row, int)
                    else (
                        int(case_row)
                        if case_row not in (None, "", "None") and str(case_row).strip()
                        else 0
                    )
                )
            except (ValueError, TypeError):
                ir = 0
            c.execute(
                """INSERT INTO executions (run_id, version_id, case_id, user_id, project, case_asset_file, case_row, status,
                   screenshot, failure_reason, failure_category, failure_chain, check_type, expected, actual, details, executed_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (
                    run_id,
                    active_version_id,
                    case_id,
                    uid,
                    project,
                    case_asset_file,
                    ir,
                    status,
                    screenshot_path,
                    failure_reason,
                    failure_category,
                    failure_chain,
                    check_type,
                    expected,
                    actual,
                    details,
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                ),
            )
            c.commit()
            ok = True
        finally:
            # ★ 修复（2026-07-17）：必须 close() 归还连接到池。
            # _PooledConnectionWrapper.close() 是归还而非真断开，不会切断同线程其他连接。
            # 不 close 会导致连接泄漏，连接池耗尽后任务线程拿不到连接，校验数据全部丢失。
            if c is not None:
                with guard("update_intent_status: c.close()", log=_log, level=logging.WARNING):
                    c.close()
        if ok:
            _log.info("[MySQL] 执行状态已记录: %s → %s (用户=%s)", case_id, status, uid)
        return bool(ok)
    except Exception as e:
        # ★ 修复（2026-07-17）：异常级别从 INFO 提升到 WARNING，避免连接池耗尽等致命错误被静默吞掉。
        # 违反 AGENTS.md 第5节"异常不得被吞"和第9节防线1。
        _log.warning(
            "[ExcelHandler] 执行状态写入失败（校验数据可能丢失）: %s",
            str(e)[:200],
            exc_info=True,
        )
        return False


# ★ 修复（2026-06-23）：update_verification_status 是 update_intent_status 的历史别名
# （签名完全一致），excel_handler.update_verification_status 仍按此名 import。补别名避免 ImportError。
update_verification_status = update_intent_status
