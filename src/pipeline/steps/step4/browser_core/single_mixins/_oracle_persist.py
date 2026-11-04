# -*- coding: utf-8 -*-
"""★ P1-5: Oracle 结果持久化接入 — 从 _base.py 拆出（§4.3 单文件≤800行）。

职责：在 Step4 执行完成后，将聚合的 oracle 结论写入 oracle_results 表。
仅做薄包装调用，核心聚合逻辑在 src/oracle/store.py。

通用性（§12.4）:
    1. 跨系统: oracle_type=correctness 通用，不含业务术语
    2. 跨行业: 通用
    3. 跨页面: 通用
    4. 数据完整性: 失败不阻断主流程（warning + 降级）
    5. 根因溯源: 解决"oracle_results 零写入"导致 Step5 无 oracle 维度数据
    6. 覆盖性: 所有走 _execute_one_intent 的用例都写入
    7. 全局观: 为 Step5 报告 + P2-12 SelfEval 提供 oracle 数据
    8. 职责边界: 仅持久化，不做校验决策（§13.4）

自我反驳三次:
    1) 写多条(按 step/轨道)? → 过度设计，一条聚合 oracle_result 足够
    2) 用插件 after_intent? → oracle 持久化是核心能力，不该用插件
    3) 在 verify_recipe_intent_result 内写入? → 违反职责边界（校验不该耦合持久化）
"""

import logging
from src.core.guarded import guard

_log = logging.getLogger(__name__)


def persist_oracle_after_execution(
    project, execution_id, case_passed, status, step_results, case_id=""
):
    """★ P1-5: 执行后持久化 oracle 结论（降级安全，不阻断主流程）。

    参数:
        project:      项目名
        execution_id: executions.id
        case_passed:  用例最终是否通过（含置信度闸门后的判定）
        status:       executions.status（PASS/FAIL/MANUAL_REVIEW）
        step_results: 步骤结果列表，每项含 _verify_result 三轨证据
        case_id:      用例编号（供 a11y 维度落库标记归属）
    """
    with guard("persist_oracle_after_execution: from src.oracle.store import ( write", log=_log, level=logging.WARNING):
        from src.oracle.store import (
            write_oracle_result_from_execution,
            write_a11y_result_from_execution,
        )
        from src.core.versioning import current_version_id

        _version_id = int(current_version_id(project) or 0)
        write_oracle_result_from_execution(
            project=project,
            version_id=_version_id,
            execution_id=execution_id,
            case_passed=case_passed,
            status=status,
            step_results=step_results,
        )
        # ★ a11y (2026-08-25): 可访问性维度落库（oracle_type=accessibility），
        #   供 Step5 _a11y_report 聚合。失败仅 warning，不阻断主流程。
        write_a11y_result_from_execution(
            project=project,
            version_id=_version_id,
            execution_id=execution_id,
            case_id=case_id,
            step_results=step_results,
        )
