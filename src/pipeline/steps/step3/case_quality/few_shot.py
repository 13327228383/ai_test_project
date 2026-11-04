# -*- coding: utf-8 -*-
"""Few-Shot 历史示例加载与格式化 — 从 case_quality.py 拆出。

★ 全 MySQL：从 test_cases JOIN executions 表筛选 PASS 且 risk_level∈{P0,P1}
  的历史高质量测试用例作为 Few-Shot，取代扫磁盘 Validation_Intent-*.xlsx。

★ S3-010: 扩展 SQL 查询返回完整用例内容（case_actions_json/payload_json/expected_outcome），
  解析 actions 和 checks，让 AI 从示例中学习"好的检查点长什么样"。
"""

import json as _json
from src.core.guarded import guard
import logging

_log = logging.getLogger(__name__)


def _parse_actions(case_actions_json: str, payload_json: str) -> list:
    """★ S3-010: 解析 actions 列表。

    优先从 case_actions_json 解析；失败则从 payload_json.actions 降级解析。
    """
    # 优先 case_actions_json
    if case_actions_json:
        with guard("_parse_actions: actions = _json.loads(case_actions_j", log=_log):
            actions = _json.loads(case_actions_json)
            if isinstance(actions, list):
                return actions
    # 降级 payload_json.actions
    if payload_json:
        with guard("_parse_actions: payload = ( _json.loads(payload_json", log=_log):
            payload = (
                _json.loads(payload_json)
                if isinstance(payload_json, str)
                else payload_json
            )
            if isinstance(payload, dict):
                actions = payload.get("actions") or payload.get("steps")
                if isinstance(actions, list):
                    return actions
    return []


def _parse_checks(payload_json: str, expected_outcome: str) -> list:
    """★ S3-010: 解析 checks 列表。

    优先从 payload_json.checks 解析；失败则用 expected_outcome 作为单条 check。
    """
    if payload_json:
        with guard("_parse_checks: payload = ( _json.loads(payload_json", log=_log):
            payload = (
                _json.loads(payload_json)
                if isinstance(payload_json, str)
                else payload_json
            )
            if isinstance(payload, dict):
                checks = payload.get("checks")
                if isinstance(checks, list) and checks:
                    return checks
    # 降级 expected_outcome
    if expected_outcome:
        return [{"expect": expected_outcome}]
    return []


def _compact_action_summary(actions: list) -> str:
    """★ S3-010: 将 actions 压缩为简短摘要（避免超出 few_shot 900字符上限）。"""
    if not actions:
        return ""
    parts = []
    for a in actions[:3]:  # 最多取前3步
        if isinstance(a, dict):
            # 提取动作类型和描述
            action_type = a.get("type") or a.get("action") or a.get("operation") or ""
            desc = a.get("description") or a.get("target_hint") or a.get("name") or ""
            if action_type and desc:
                parts.append("%s:%s" % (action_type, str(desc)[:20]))
            elif desc:
                parts.append(str(desc)[:25])
            elif action_type:
                parts.append(str(action_type)[:20])
        elif isinstance(a, str):
            parts.append(a[:25])
    return " → ".join(parts) if parts else ""


def _compact_check_summary(checks: list) -> str:
    """★ S3-010: 将 checks 压缩为简短摘要。"""
    if not checks:
        return ""
    parts = []
    for c in checks[:2]:  # 最多取前2条检查点
        if isinstance(c, dict):
            expect = c.get("expect") or c.get("expected") or c.get("assertion") or ""
            field = c.get("field") or c.get("target") or ""
            if expect:
                text = "%s%s" % (("[%s] " % field) if field else "", str(expect)[:40])
                parts.append(text)
        elif isinstance(c, str):
            parts.append(c[:40])
    return " | ".join(parts) if parts else ""


def _load_few_shot_examples(module: str, project_root=None) -> list:
    """
    从历史执行记录中筛选高质量 Few-Shot 示例。
    选取测试用例：execution status=PASS + risk_level∈{P0,P1} + 同模块 → 取最多 5 条。

    ★ S3-010: 扩展 SQL 返回 case_actions_json/payload_json/expected_outcome，
    解析 actions 和 checks 供 _format_few_shot 展示核心内容。

    Args:
        module: 当前需求模块名（用于匹配；空则不按模块过滤）
        project_root: 保留签名兼容，MySQL 模式下不再使用
    """
    try:
        from src.core.config_loader import config
        from src.core.db_manager import get_connection

        proj = getattr(config, "project_name", "") or ""
        if not proj:
            return []
        conn = get_connection()
        try:
            # ★ S3-010: 扩展 SELECT 包含 case_actions_json/payload_json/expected_outcome
            select_cols = (
                "vi.case_id, vi.title, vi.risk_level, "
                "vi.case_actions_json, vi.payload_json, vi.expected_outcome, "
                "r.module AS module"
            )
            # JOIN executions 取 PASS 状态；JOIN requirements 取 module（test_cases 无 module 字段）
            if module:
                sql = (
                    "SELECT " + select_cols + " "
                    "FROM test_cases vi "
                    "INNER JOIN executions e ON e.project=vi.project AND e.case_id=vi.case_id "
                    "LEFT JOIN requirements r ON r.project=vi.project AND r.req_id=vi.req_id "
                    "WHERE vi.project=%s AND e.status='PASS' AND vi.risk_level IN ('P0','P1') "
                    "AND (r.module=%s OR r.module LIKE CONCAT('%%%%', %%s, '%%%%')) "
                    "GROUP BY vi.case_id ORDER BY vi.risk_level, MAX(e.executed_at) DESC LIMIT 5"
                )
                rows = conn.execute(sql, (proj, module, module)).fetchall()
            else:
                sql = (
                    "SELECT " + select_cols + " "
                    "FROM test_cases vi "
                    "INNER JOIN executions e ON e.project=vi.project AND e.case_id=vi.case_id "
                    "LEFT JOIN requirements r ON r.project=vi.project AND r.req_id=vi.req_id "
                    "WHERE vi.project=%s AND e.status='PASS' AND vi.risk_level IN ('P0','P1') "
                    "GROUP BY vi.case_id ORDER BY vi.risk_level, MAX(e.executed_at) DESC LIMIT 5"
                )
                rows = conn.execute(sql, (proj,)).fetchall()
        finally:
            with guard("_load_few_shot_examples: conn.close()", log=_log):
                conn.close()
        examples = []
        for r in rows or []:
            title = r.get("title") or ""
            if not title:
                continue
            # ★ S3-010: 解析 actions 和 checks
            actions_json = r.get("case_actions_json") or ""
            payload_json = r.get("payload_json") or ""
            expected_outcome = r.get("expected_outcome") or ""
            actions = _parse_actions(actions_json, payload_json)
            checks = _parse_checks(payload_json, expected_outcome)
            examples.append(
                {
                    "case_id": r.get("case_id", ""),
                    "title": title,
                    "module": r.get("module", "") or "",
                    "risk_level": r.get("risk_level", ""),
                    # ★ S3-010: 新增核心内容字段
                    "actions": actions,
                    "checks": checks,
                    "action_summary": _compact_action_summary(actions),
                    "check_summary": _compact_check_summary(checks),
                }
            )
        return examples[:5]
    except Exception as e:
        _log.debug("[few_shot] MySQL 加载失败: %s", str(e)[:80])
        return []


def _format_few_shot(examples: list) -> str:
    """将 Few-Shot 示例格式化为 Prompt 注入文本。

    ★ S3-010: 展示 actions/checks 核心内容，让 AI 学习"好的检查点长什么样"。
    """
    if not examples:
        return ""
    lines = [
        "\n## 参考示例（同模块历史高质量测试用例）",
        "以下是测试用例生成时可参考的历史高质量测试用例（已人工确认且执行通过）：",
    ]
    for i, ex in enumerate(examples):
        # 基础信息
        lines.append(
            "  %d. [%s] %s (P=%s)"
            % (
                i + 1,
                ex.get("case_id", "?"),
                ex.get("title", ""),
                ex.get("risk_level", "?"),
            )
        )
        # ★ S3-010: 展示 actions 摘要
        action_summary = ex.get("action_summary") or ""
        if action_summary:
            lines.append("     操作: %s" % action_summary)
        # ★ S3-010: 展示 checks 摘要（核心：让 AI 学习好的检查点）
        check_summary = ex.get("check_summary") or ""
        if check_summary:
            lines.append("     检查: %s" % check_summary)
    lines.append(
        "★ 请参考以上测试用例的操作步骤和检查点结构，生成同等质量的新测试用例。\n"
    )
    return "\n".join(lines)
