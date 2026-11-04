# -*- coding: utf-8 -*-
"""
CLI 处理辅助
"""

import json
import logging

_log = logging.getLogger(__name__)


def _handle_cli_export(args) -> int:
    """处理 CLI 导出相关命令。"""
    if args.export_intents:
        from src.pipeline.utils.case_store import case_store

        export_result = case_store.export_to_excel(args.export_intents)
        if args.json:
            _log.info(
                json.dumps(
                    {"export_intents": export_result},
                    ensure_ascii=False,
                    indent=2,
                    default=str,
                )
            )
        else:
            if export_result.get("success"):
                _log.info(
                    "  [测试用例导出] 已导出 %d 条测试用例 → %s"
                    % (export_result.get("count", 0), export_result.get("path", ""))
                )
            else:
                _log.error(
                    "  [测试用例导出] 失败: %s", export_result.get("error", "未知错误")
                )
        return 0 if export_result.get("success") else 1

    if args.export_requirements:
        from src.pipeline.utils.requirement_store import requirement_store

        export_result = requirement_store.export_to_excel(args.export_requirements)
        if args.json:
            _log.info(
                json.dumps(
                    {"export_requirements": export_result},
                    ensure_ascii=False,
                    indent=2,
                    default=str,
                )
            )
        else:
            if export_result.get("success"):
                _log.info(
                    "  [需求导出] 已导出 %d 条需求 → %s"
                    % (export_result.get("count", 0), export_result.get("path", ""))
                )
            else:
                _log.error(
                    "  [需求导出] 失败: %s", export_result.get("error", "未知错误")
                )
        return 0 if export_result.get("success") else 1

    if args.export_trace_links_json:
        from src.pipeline.utils.trace_link_store import trace_link_store

        export_result = trace_link_store.export_to_json(args.export_trace_links_json)
        if args.json:
            _log.debug(
                json.dumps(
                    {"export_trace_links_json": export_result},
                    ensure_ascii=False,
                    indent=2,
                    default=str,
                )
            )
        else:
            if export_result.get("success"):
                _log.info(
                    "  [追溯链路导出] 已导出 %d 条 → %s"
                    % (export_result.get("count", 0), export_result.get("path", ""))
                )
            else:
                _log.error(
                    "  [追溯链路导出] 失败: %s", export_result.get("error", "未知错误")
                )
        return 0 if export_result.get("success") else 1

    if args.import_trace_links_json:
        from src.pipeline.utils.trace_link_store import trace_link_store

        import_result = trace_link_store.import_from_json(
            args.import_trace_links_json, replace=args.yes
        )
        if args.json:
            _log.debug(
                json.dumps(
                    {"import_trace_links_json": import_result},
                    ensure_ascii=False,
                    indent=2,
                    default=str,
                )
            )
        else:
            if import_result.get("success"):
                mode = "覆盖模式" if args.yes else "增量模式（不清理旧）"
                _log.info(
                    "  [追溯链路导入] %s: 已导入 %d 条 → MySQL trace_links"
                    % (mode, import_result.get("count", 0))
                )
                if not args.yes:
                    _log.info("  [追溯链路导入] 如需清空重建，请追加 --yes")
            else:
                _log.error(
                    "  [追溯链路导入] 失败: %s", import_result.get("error", "未知错误")
                )
        return 0 if import_result.get("success") else 1

    return -1


def _handle_cli_health(args, check_migration_health_fn, print_health_report_fn) -> int:
    """处理 CLI 健康检查命令。"""
    if args.mysql_health:
        health = check_migration_health_fn()
        if args.json:
            _log.info(
                json.dumps(
                    {"mysql_health": health}, ensure_ascii=False, indent=2, default=str
                )
            )
        else:
            print_health_report_fn(health)
        return 1 if args.strict and not health.get("ok") else 0
    return -1


def _handle_cli_sync(args, sync_fn) -> int:
    """处理 CLI 同步命令。"""
    if args.sync_excel_to_mysql:
        sync_result = sync_fn(dry_run=not args.yes)
        if args.json:
            _log.info(
                json.dumps(
                    {"sync_excel_to_mysql": sync_result},
                    ensure_ascii=False,
                    indent=2,
                    default=str,
                )
            )
        else:
            mode = "预览" if sync_result.get("dry_run") else "写入"
            _log.info(
                "  [Excel→MySQL回填:%s] requirements=%d, test_cases=%d, explicit_mappings=%d, case_asset_file=%s"
                % (
                    mode,
                    sync_result.get("requirements", 0),
                    sync_result.get("test_cases", 0),
                    sync_result.get("explicit_mappings", 0),
                    sync_result.get("case_asset_file", ""),
                )
            )
            if sync_result.get("dry_run"):
                _log.info("  [Excel→MySQL回填] 如确认写入，请追加 --yes")
            else:
                w = sync_result.get("written", {})
                _log.info(
                    "  [Excel→MySQL回填] 已写入 requirements=%d, test_cases=%d, case_requirement_mapping=%d"
                    % (
                        w.get("requirements", 0),
                        w.get("test_cases", 0),
                        w.get("case_requirement_mapping", 0),
                    )
                )
                if sync_result.get("failed"):
                    _log.error(
                        "  [Excel→MySQL回填] 失败项: %s", sync_result.get("failed")
                    )
        return 1 if sync_result.get("failed") else 0
    return -1


def _handle_cli_reset(args, reset_fn) -> int:
    """处理 CLI 重置命令。"""
    if args.reset_user_db:
        reset_result = reset_fn(confirm=args.yes, backup=args.backup)
        if args.json:
            _log.info(
                json.dumps(
                    {"reset_user_db": reset_result},
                    ensure_ascii=False,
                    indent=2,
                    default=str,
                )
            )
        else:
            if args.yes:
                if args.backup:
                    _log.info(
                        "  [用户库重置] 已备份并移走 %d 个文件 → %s"
                        % (
                            len(reset_result.get("moved", [])),
                            reset_result.get("backup_dir"),
                        )
                    )
                else:
                    _log.info(
                        "  [用户库重置] 已删除 %d 个测试数据库相关文件"
                        % (len(reset_result.get("deleted", [])))
                    )
            else:
                action = "备份并移走" if args.backup else "直接删除"
                _log.info(
                    "  [用户库重置] 预览：将%s %d 个测试数据库相关文件"
                    % (action, len(reset_result.get("planned", [])))
                )
                _log.info(
                    "  [用户库重置] 如确认测试数据可丢弃，请追加 --yes；如需备份再追加 --backup"
                )
        return 0
    return -1


def _handle_cli_repair_output(args, result, repair_plan, repair_result) -> None:
    """处理修复结果的输出打印。"""
    if args.json:
        payload = result
        if repair_plan is not None or repair_result is not None:
            payload = {"checks": result, "repair_plan": repair_plan}
            if repair_result is not None:
                payload["repair_result"] = repair_result
        _log.info(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    else:
        if repair_plan is not None:
            _log.info(
                "  [修复计划] 共 %d 项；可安全自动处理 %d 项；需人工确认 %d 项"
                % (
                    len(repair_plan.get("actions", [])),
                    repair_plan.get("stats", {}).get("safe_action_count", 0),
                    repair_plan.get("stats", {}).get("review_action_count", 0),
                )
            )
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
