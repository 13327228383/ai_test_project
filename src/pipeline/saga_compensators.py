# -*- coding: utf-8 -*-
"""
saga_compensators.py — Pipeline Saga 补偿钩子
==============================================
从 scheduler.py 拆分出的独立模块，负责各 Step 失败时的 Saga 补偿回滚。

★ 安全拆分原则：
  - 零业务逻辑变更，仅物理位置移动
  - 保持所有导入路径、函数签名、返回值 100% 一致
  - 原文件通过门面模式兼容
"""

import logging
from src.core.guarded import guard
from collections.abc import Callable

_log = logging.getLogger(__name__)


def get_compensate_hook(step_num: int, *, project: str, run_id: str) -> Callable:
    """返回 Step 补偿钩子，实现 PipelineSaga 的逆序回滚。

    Step 1 补偿：清理需求缓存/文件缓存
    Step 2 补偿：无 DB 回滚（拆解结果随版本化存储自动失效；原 requirement_breakdown
                表已随历史版本合并移除，2026-09-22 死区审计清理）
    Step 3 补偿：标记生成测试用例为 invalid
    Step 4 补偿：无 DB 回滚（浏览器资源由执行器 finally 释放；原 browser_sessions/
                temp_test_data 表已随历史版本合并移除，2026-09-22 死区审计清理）
    Step 5 补偿：无需回滚（报告生成幂等）
    """
    if step_num == 1:

        def _compensate_step1():
            _log.info("Saga 补偿 Step 1: 清理当前版本需求缓存")
            with guard("_compensate_step1: from src.pipeline.utils.requirement_", log=_log, level=logging.WARNING):
                from src.pipeline.utils.requirement_store import requirement_store
                from src.core.versioning import current_version_id

                version_id = current_version_id(project)
                if not version_id:
                    _log.warning(
                        "Saga 补偿 Step 1 跳过: 项目 %s 无 active version_id", project
                    )
                    return
                requirement_store.clear_version_requirements(project, version_id)

        return _compensate_step1

    elif step_num == 2:

        def _compensate_step2():
            # ★ 2026-09-22 死区审计：原清理目标 requirement_breakdown 表已随历史
            #   版本合并移除（当前库与 schema.sql 均无此表，Step2 拆解结果改存
            #   requirements/requirement_slices，其回滚由对应 store 的版本化机制
            #   覆盖）——原 SQL 是静默 no-op 死路径，按用户裁决清理。
            _log.info("Saga 补偿 Step 2: 拆解结果随版本化存储自动失效，无需回滚")

        return _compensate_step2

    elif step_num == 3:

        def _compensate_step3():
            _log.info("Saga 补偿 Step 3: 标记生成测试用例为 invalid")
            with guard("_compensate_step3: from src.core.db_manager import get_", log=_log, level=logging.WARNING):
                from src.core.db_manager import get_connection

                conn = get_connection()
                try:
                    conn.execute(
                        "UPDATE test_cases SET status = 'invalid' "
                        "WHERE project = %s AND run_id = %s",
                        (project, run_id),
                    )
                    conn.commit()
                finally:
                    conn.close()

        return _compensate_step3

    elif step_num == 4:

        def _compensate_step4():
            # ★ 2026-09-22 死区审计：原清理目标 browser_sessions / temp_test_data
            #   两表已随历史版本合并移除（当前库与 schema.sql 均无，Step4 资源清理
            #   由 browser_core 的 finally 释放与 executor 收尾覆盖）——原 SQL 是
            #   静默 no-op 死路径，按用户裁决清理。
            _log.info("Saga 补偿 Step 4: 浏览器资源由执行器 finally 释放，无需 DB 回滚")

        return _compensate_step4

    else:

        def _noop():
            _log.info("Saga 补偿: Step %d 无需回滚", step_num)

        return _noop
