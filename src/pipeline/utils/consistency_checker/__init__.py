# -*- coding: utf-8 -*-
from __future__ import annotations

"""
流水线一致性检查器
================

该模块只做只读检查，不修改 Excel、MySQL 或任何过程产物。
用途是把"事实源混合"带来的脏数据风险提前暴露出来，避免错误继续流入执行和报告。
"""

import sys
from typing import Any
import logging

_log = logging.getLogger(__name__)

from src.pipeline.utils.consistency_helpers import (
    apply_stale_mapping_repair,
    apply_safe_missing_mapping_repair,
    reset_user_db,
    sync_excel_to_mysql,
    _handle_cli_export,
    _handle_cli_health,
    _handle_cli_sync,
    _handle_cli_reset,
    _handle_cli_repair_output,
    print_mysql_health_report,
)

from src.pipeline.utils.consistency_checker.env import check_mysql_environment
from src.pipeline.utils.consistency_checker.mapping import (
    check_intent_mapping_consistency,
    build_intent_mapping_repair_plan,
)
from src.pipeline.utils.consistency_checker.executability import (
    check_intent_executability,
)
from src.pipeline.utils.consistency_checker.migration import (
    check_mysql_migration_health,
)
from src.pipeline.utils.consistency_checker.printer import (
    print_check_report,
    print_repair_plan,
)


# ---------------------------------------------------------------------------
# 公共 API
# ---------------------------------------------------------------------------


def run_all_checks(
    excel_handler: Any | None = None,
    tracker: Any | None = None,
) -> dict[str, Any]:
    """运行当前已实现的一致性检查。"""
    env = check_mysql_environment()
    mapping = check_intent_mapping_consistency(
        excel_handler=excel_handler, tracker=tracker
    )
    executable = check_intent_executability(excel_handler=excel_handler)
    checks = {
        "mysql_environment": env,
        "intent_mapping": mapping,
        "intent_executability": executable,
    }
    return {
        "ok": all(v.get("ok") for v in checks.values()),
        "checks": checks,
        "summary": {
            "error_count": sum(
                v.get("stats", {}).get("error_count", 0) for v in checks.values()
            ),
            "warning_count": sum(
                v.get("stats", {}).get("warning_count", 0) for v in checks.values()
            ),
        },
    }


def run_and_print_checks(stage: str) -> dict[str, Any]:
    """运行全部检查并打印结果；任何异常都降级为 warning 日志。"""
    try:
        result = run_all_checks()
        print_check_report(stage, result)
        return result
    except Exception as e:
        _log.info("  [一致性检查:%s] 检查失败（不阻断流程）: %s", stage, str(e)[:120])
        return {
            "ok": False,
            "checks": {},
            "summary": {"error_count": 0, "warning_count": 1},
            "error": str(e),
        }


# ---------------------------------------------------------------------------
# CLI 入口
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """命令行入口：只读运行一致性检查。

    默认即使发现错误也返回 0，方便先观察现状；加 --strict 后，
    若存在 error 级问题则返回 1，适合后续接入 CI。
    """
    import argparse

    parser = argparse.ArgumentParser(description="只读运行流水线一致性检查")
    parser.add_argument("--stage", default="手动", help="日志中显示的阶段名称")
    parser.add_argument("--json", action="store_true", help="输出 JSON 结果")
    parser.add_argument(
        "--repair-plan", action="store_true", help="输出只读修复计划，不修改数据"
    )
    parser.add_argument(
        "--repair-stale-only",
        action="store_true",
        help="只删除 MySQL 中 Excel 已不存在的残留映射",
    )
    parser.add_argument(
        "--repair-missing-safe",
        action="store_true",
        help="只补 Excel 显式 req_id 的安全缺失映射",
    )
    parser.add_argument(
        "--export-intents",
        default="",
        help="从 MySQL test_cases 镜像导出 Excel 到指定路径",
    )
    parser.add_argument(
        "--export-requirements",
        default="",
        help="从 MySQL requirements 镜像导出 Excel 到指定路径",
    )
    parser.add_argument(
        "--export-trace-links-json",
        default="",
        help="从 MySQL trace_links 镜像导出 JSON 到指定路径",
    )
    parser.add_argument(
        "--import-trace-links-json",
        default="",
        help="把旧的 trace_links.json 显式导入到 MySQL trace_links 表",
    )
    parser.add_argument(
        "--mysql-health", action="store_true", help="检查 MySQL 事实源迁移健康度"
    )
    parser.add_argument(
        "--sync-excel-to-mysql",
        action="store_true",
        help="把现有 Excel 需求/测试用例回填到 MySQL 镜像；默认预览，配合 --yes 写入",
    )
    parser.add_argument(
        "--reset-user-db",
        action="store_true",
        help="[MySQL 模式已废弃] 旧 MySQL data.db 重置",
    )
    parser.add_argument(
        "--backup",
        action="store_true",
        help="重置用户库时保留备份；默认直接删除测试数据",
    )
    parser.add_argument("--yes", action="store_true", help="确认执行写入/移动类操作")
    parser.add_argument(
        "--strict", action="store_true", help="发现 error 级问题时返回非 0"
    )
    args = parser.parse_args(argv)

    # 按优先级处理各子命令
    ret = _handle_cli_reset(args, reset_user_db)
    if ret >= 0:
        return ret

    ret = _handle_cli_export(args)
    if ret >= 0:
        return ret

    ret = _handle_cli_health(
        args, check_mysql_migration_health, print_mysql_health_report
    )
    if ret >= 0:
        return ret

    ret = _handle_cli_sync(args, sync_excel_to_mysql)
    if ret >= 0:
        return ret

    # 默认：运行全部检查
    result = run_all_checks()
    repair_plan = (
        build_intent_mapping_repair_plan()
        if (args.repair_plan or args.repair_stale_only or args.repair_missing_safe)
        else None
    )
    repair_result = None
    if args.repair_stale_only and repair_plan is not None:
        repair_result = apply_stale_mapping_repair(repair_plan, dry_run=not args.yes)
    if args.repair_missing_safe and repair_plan is not None:
        missing_result = apply_safe_missing_mapping_repair(
            repair_plan, dry_run=not args.yes
        )
        repair_result = repair_result or {}
        repair_result["missing_safe"] = missing_result

    if args.json:
        _handle_cli_repair_output(args, result, repair_plan, repair_result)
    else:
        print_check_report(args.stage, result)
        if repair_plan is not None:
            print_repair_plan(repair_plan)
        if repair_result is not None:
            if "removed" in repair_result:
                mode = "预览" if repair_result.get("dry_run") else "已执行"
                _log.error(
                    "  [残留映射修复:%s] removed=%d failed=%d skipped=%d"
                    % (
                        mode,
                        len(repair_result.get("removed", [])),
                        len(repair_result.get("failed", [])),
                        len(repair_result.get("skipped", [])),
                    )
                )
            if "missing_safe" in repair_result:
                mr = repair_result["missing_safe"]
                mode = "预览" if mr.get("dry_run") else "已执行"
                _log.error(
                    "  [安全缺失映射修复:%s] added=%d failed=%d skipped=%d"
                    % (
                        mode,
                        len(mr.get("added", [])),
                        len(mr.get("failed", [])),
                        len(mr.get("skipped", [])),
                    )
                )
            is_dry = repair_result.get("dry_run") or repair_result.get(
                "missing_safe", {}
            ).get("dry_run")
            if is_dry:
                _log.info("  [映射修复] 当前为 dry-run，如确认请追加 --yes")

    if args.strict and not result.get("ok", False):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
