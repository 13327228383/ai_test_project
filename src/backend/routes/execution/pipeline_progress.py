# -*- coding: utf-8 -*-
"""
流水线执行进度聚合 API
======================
GET /api/pipeline-progress
聚合 Step1-5 状态 + 需求条目状态 + 用例关联，供"流水线执行"页面消费。

数据来源：
  - _run_progress (内存) → 当前 step / per-req 进度
  - excel_handler.read_requirements() → 需求列表
  - case_store.list_intents_with_meta() → 用例列表（统计每条需求的用例数）
  - _active_runs → 运行状态 + 起始时间
"""

import time
from src.core.guarded import guard
import logging
from collections import Counter
from flask import jsonify

from ._progress_db import infer_step_from_db

_log = logging.getLogger(__name__)

# Step 元信息（label 与设计稿对齐）
_STEP_META = [
    {"step": 1, "label": "数据提取"},
    {"step": 2, "label": "AI 需求拆解"},
    {"step": 3, "label": "测试用例建模"},
    {"step": 4, "label": "智能执行验证"},
    {"step": 5, "label": "生成报告"},
]


def _collect_steps_status(
    current_step, running, step_done=0, step_total=0, per_step_counts=None
):
    """根据当前 step 推断 5 步骤状态：completed / active / pending。

    每个 step 对象附带监控字段：
      - done: 已完成的子任务数（仅 active step 有意义，来自 _run_progress.step_done）
      - total: 子任务总数（仅 active step 有意义，来自 _run_progress.step_total）
      - count: 该 step 处理的产物数量（completed 时从产物推断，active 时=step_done，pending 时=0）

    ★ 核心原则：所有步骤状态统一以数据库产物表为准，不依赖内存中的 current_step 推断。
      这样可以确保控制台、操作面板、流水线执行等所有页面状态一致。
    """
    per_step_counts = per_step_counts or {}
    steps = []
    for meta in _STEP_META:
        s = meta["step"]
        count = per_step_counts.get(s, 0)
        if running and s == current_step:
            status = "active"
        elif count > 0:
            status = "completed"
        else:
            status = "pending"

        # 监控数据
        if status == "active":
            done = step_done if isinstance(step_done, (int, float)) else 0
            total = step_total if isinstance(step_total, (int, float)) else 0
            count = done
        elif status == "completed":
            # 已完成 step：count 从产物推断，done/total 相等
            count = per_step_counts.get(s, -1)
            done = count
            total = count
        else:
            done = 0
            total = 0
            count = 0

        steps.append(
            {
                "step": s,
                "label": meta["label"],
                "status": status,
                "done": done,
                "total": total,
                "count": count,
            }
        )
    return steps


# ── Step 子项定义（label 与前端 dashboard 卡片子项一致） ──
# Step1 子项有独立的数据源完成情况（基于 step1_status JSON）
# Step2-5 子项无独立产物表，状态跟随 step 状态
_STEP_SUB_ITEMS = {
    1: [
        {
            "key": "version",
            "label": "版本管理",
            "source_key": None,
            "group": "前置准备",
            "category": "left",
        },
        {
            "key": "health_check",
            "label": "健康检查",
            "source_key": None,
            "group": "前置准备",
            "category": "left",
        },
        {
            "key": "config_validate",
            "label": "配置校验",
            "source_key": None,
            "group": "前置准备",
            "category": "left",
        },
        {
            "key": "confluence",
            "label": "Confluence 拉取",
            "source_key": "confluence",
            "group": "外部拉取",
            "category": "left",
        },
        {
            "key": "figma",
            "label": "Figma 解析",
            "source_key": "figma",
            "group": "外部拉取",
            "category": "left",
        },
        {
            "key": "database",
            "label": "数据库 Schema",
            "source_key": "database",
            "group": "外部拉取",
            "category": "left",
        },
        {
            "key": "yapi",
            "label": "YApi 同步",
            "source_key": "yapi",
            "group": "外部拉取",
            "category": "left",
        },
        {
            "key": "upload",
            "label": "上传文件解析",
            "source_key": "upload",
            "group": "外部拉取",
            "category": "left",
        },
        {
            "key": "prototype_recognition",
            "label": "原型图识别",
            "source_key": "prototype_images",
            "group": "内部生成",
            "category": "right",
        },
        {
            "key": "dom_crawl",
            "label": "DOM 爬取",
            "source_key": "dom_crawl",
            "group": "内部生成",
            "category": "right",
        },
        {
            "key": "rag_index",
            "label": "RAG 向量索引构建",
            "source_key": None,
            "group": "内部生成",
            "category": "right",
        },
        {
            "key": "multi_source_features",
            "label": "多源功能提取",
            "source_key": "multi_source_features",
            "group": "内部生成",
            "category": "right",
        },
        {
            "key": "persist",
            "label": "数据持久化入库",
            "source_key": None,
            "group": "内部生成",
            "category": "right",
        },
    ],
    2: [
        {
            "key": "integrity_check",
            "label": "数据完整性校验",
            "group": "前置准备",
            "category": "left",
        },
        {
            "key": "hash_detect",
            "label": "哈希增量检测",
            "group": "前置准备",
            "category": "left",
        },
        {
            "key": "feature_load",
            "label": "功能清单加载",
            "group": "前置准备",
            "category": "left",
        },
        {
            "key": "three_layer_match",
            "label": "三层匹配(关键词/嵌入)",
            "group": "前置准备",
            "category": "left",
        },
        {"key": "slice", "label": "原文切片", "group": "核心拆解", "category": "left"},
        {
            "key": "triple_extract",
            "label": "AI 提取三元组",
            "group": "核心拆解",
            "category": "left",
        },
        {"key": "dedup", "label": "去重合并", "group": "质量校验", "category": "right"},
        {
            "key": "verify",
            "label": "原文核对(防幻觉)",
            "group": "质量校验",
            "category": "right",
        },
        {
            "key": "reverse_coverage",
            "label": "逆向覆盖检查",
            "group": "质量校验",
            "category": "right",
        },
        {
            "key": "sensitive_detect",
            "label": "敏感数据检测",
            "group": "后处理",
            "category": "right",
        },
        {
            "key": "confidence",
            "label": "置信度评分",
            "group": "后处理",
            "category": "right",
        },
        {
            "key": "gate_check",
            "label": "门禁检查",
            "group": "后处理",
            "category": "right",
        },
        {
            "key": "traceability",
            "label": "追溯链路+快照构建",
            "group": "后处理",
            "category": "right",
        },
        {
            "key": "req_review",
            "label": "需求审核闸门",
            "group": "后处理",
            "category": "right",
        },
    ],
    3: [
        {
            "key": "pre_gate_req",
            "label": "准入检查: 需求校验",
            "group": "前置准备",
            "category": "left",
        },
        {
            "key": "tracker_rebuild",
            "label": "追踪器重建",
            "group": "前置准备",
            "category": "left",
        },
        {
            "key": "parse",
            "label": "AI 需求解析",
            "group": "核心建模",
            "category": "left",
        },
        {
            "key": "rag_retrieval",
            "label": "RAG 检索注入",
            "group": "核心建模",
            "category": "left",
        },
        {
            "key": "control_mapping",
            "label": "控件操作映射",
            "group": "核心建模",
            "category": "left",
        },
        {
            "key": "reachability",
            "label": "页面可达性验证",
            "group": "用例构建",
            "category": "right",
        },
        {
            "key": "scenario",
            "label": "场景路径生成",
            "group": "用例构建",
            "category": "right",
        },
        {
            "key": "template",
            "label": "用例模板匹配",
            "group": "用例构建",
            "category": "right",
        },
        {
            "key": "assert",
            "label": "断言规则构建",
            "group": "用例构建",
            "category": "right",
        },
        {
            "key": "case_generation",
            "label": "用例生成入库",
            "group": "用例构建",
            "category": "right",
        },
        {
            "key": "inverse_expand",
            "label": "反向覆盖意图扩展",
            "group": "覆盖优化",
            "category": "right",
        },
        {
            "key": "coverage_matrix",
            "label": "覆盖矩阵+缺口补生",
            "group": "覆盖优化",
            "category": "right",
        },
        {
            "key": "coverage_report",
            "label": "增强覆盖率报告",
            "group": "覆盖优化",
            "category": "right",
        },
        {
            "key": "case_review",
            "label": "用例审核闸门",
            "group": "审核闸门",
            "category": "right",
        },
    ],
    4: [
        {
            "key": "pre_gate_case",
            "label": "准入检查: 用例校验",
            "group": "前置准备",
            "category": "left",
        },
        {
            "key": "deployment_match",
            "label": "部署文档匹配",
            "group": "前置准备",
            "category": "left",
        },
        {
            "key": "trace_precheck",
            "label": "追溯链路预检(防幻觉)",
            "group": "前置准备",
            "category": "left",
        },
        {
            "key": "api_only_route",
            "label": "API-only 快速通道",
            "group": "浏览器执行",
            "category": "left",
        },
        {
            "key": "playwright",
            "label": "Playwright 初始化",
            "group": "浏览器执行",
            "category": "left",
        },
        {
            "key": "navigate",
            "label": "页面导航加载",
            "group": "浏览器执行",
            "category": "left",
        },
        {
            "key": "locate",
            "label": "元素定位验证",
            "group": "浏览器执行",
            "category": "left",
        },
        {
            "key": "self_healing",
            "label": "自愈修复(VisionHeal)",
            "group": "浏览器执行",
            "category": "left",
        },
        {
            "key": "action",
            "label": "操作序列执行",
            "group": "浏览器执行",
            "category": "left",
        },
        {
            "key": "snapshot",
            "label": "快照对比检查",
            "group": "校验断言",
            "category": "right",
        },
        {
            "key": "api_assert",
            "label": "API 响应断言",
            "group": "校验断言",
            "category": "right",
        },
        {
            "key": "sql_check",
            "label": "SQL 数据校验",
            "group": "校验断言",
            "category": "right",
        },
        {
            "key": "fuzzy",
            "label": "Fuzzy 匹配判定",
            "group": "校验断言",
            "category": "right",
        },
        {
            "key": "self_repair",
            "label": "自我修复闭环(HARNESS)",
            "group": "后处理",
            "category": "right",
        },
        {
            "key": "red_team",
            "label": "红队对抗验证",
            "group": "后处理",
            "category": "right",
        },
        {
            "key": "summary",
            "label": "结果汇总写入",
            "group": "后处理",
            "category": "right",
        },
    ],
    5: [
        {
            "key": "pre_gate_exec",
            "label": "准入检查: 执行校验",
            "group": "前置准备",
            "category": "left",
        },
        {
            "key": "aggregate",
            "label": "执行结果聚合",
            "group": "核心生成",
            "category": "left",
        },
        {
            "key": "pass_rate",
            "label": "通过率统计",
            "group": "核心生成",
            "category": "left",
        },
        {"key": "defect", "label": "缺陷归类", "group": "核心生成", "category": "left"},
        {
            "key": "manual_retest",
            "label": "人工补测建议清单",
            "group": "报告章节",
            "category": "right",
        },
        {
            "key": "cross_consistency",
            "label": "跨轨一致性报告(UI/API/DB)",
            "group": "报告章节",
            "category": "right",
        },
        {
            "key": "coverage_report",
            "label": "覆盖矩阵报告",
            "group": "报告章节",
            "category": "right",
        },
        {
            "key": "traceability_report",
            "label": "数据源追溯报告",
            "group": "报告章节",
            "category": "right",
        },
        {
            "key": "diff_report",
            "label": "变更对比报告(4维度)",
            "group": "报告章节",
            "category": "right",
        },
        {
            "key": "smart_insights",
            "label": "智能洞察生成",
            "group": "报告章节",
            "category": "right",
        },
        {
            "key": "blue_red",
            "label": "蓝红对抗分析",
            "group": "报告章节",
            "category": "right",
        },
        {
            "key": "self_verify",
            "label": "报告数据自校验",
            "group": "质量保障",
            "category": "right",
        },
        {
            "key": "run_summary",
            "label": "运行摘要写入(run_summary)",
            "group": "持久化输出",
            "category": "left",
        },
        {
            "key": "export",
            "label": "报告导出",
            "group": "持久化输出",
            "category": "left",
        },
        {
            "key": "distribute",
            "label": "报告分发",
            "group": "持久化输出",
            "category": "left",
        },
    ],
}


def _collect_step1_sub_items(project, version_label, step_status="pending"):
    """从 step1_status JSON 读取 Step1 各数据源的状态。

    相比逐个查询 source_documents.doc_type，step1_status 是 Step1 执行后
    统一写入的单一事实源，包含所有数据源（含 dom_crawl / upload / figma
    等不写入 source_documents 的源），信源统一避免遗漏。
    无 source_key 的非数据源子项（版本管理/健康检查/RAG索引等）跟随 step_status。
    """
    items = [
        {
            "key": sub["key"],
            "label": sub["label"],
            "group": sub.get("group", ""),
            "category": sub.get("category", "left"),
            "status": "pending",
            "count": 0,
        }
        for sub in _STEP_SUB_ITEMS[1]
    ]
    if not project:
        return items
    with guard("_collect_step1_sub_items: from src.core.db_manager import get_", log=_log):
        from src.core.db_manager import get_connection, release_connection

        conn = None
        try:
            conn = get_connection()
            _row = conn.execute(
                "SELECT content FROM source_documents "
                "WHERE project=%s AND version_label=%s AND doc_type='step1_status' "
                "ORDER BY updated_at DESC LIMIT 1",
                (project, version_label),
            ).fetchone()
            if not _row or not _row.get("content"):
                return items
            import json as _json

            _sources_status = _json.loads(_row["content"])
            if not isinstance(_sources_status, dict):
                return items
            for i, sub in enumerate(_STEP_SUB_ITEMS[1]):
                source_key = sub.get("source_key", "")
                if not source_key:
                    continue
                st = _sources_status.get(source_key, {})
                if not isinstance(st, dict):
                    continue
                status = st.get("status", "")
                if status in ("ok", "warning", "failed"):
                    # warning = 有提示性结论但未阻断（如需求-SUT 部分未匹配）
                    items[i]["status"] = "error" if status == "failed" else "completed"
                    items[i]["count"] = 1
                elif status == "skipped":
                    # 未配置/主动跳过在步骤卡片里按"已处理"展示，避免整步完成而子项显示 pending
                    items[i]["status"] = "completed"
                    items[i]["count"] = 0
                elif not status and step_status == "completed":
                    # 状态未记录但 Step1 已完成：子项按已完成展示，避免显示 pending
                    items[i]["status"] = "completed"
                    items[i]["count"] = 0
            # 非数据源子项跟随 step_status；rag_index 按本版本真实切片数判定，
            # 避免"Step1 已完成但 RAG 未构建"时仍显示 green（与 dashboard RAG 卡自洽）。
            for i, sub in enumerate(_STEP_SUB_ITEMS[1]):
                if not sub.get("source_key"):
                    if sub["key"] == "rag_index":
                        try:
                            _ragr = conn.execute(
                                "SELECT COUNT(*) AS cnt FROM rag_chunks "
                                "WHERE scope_type='project' AND scope_id=%s "
                                "AND version_label=%s AND is_active=1",
                                (project, version_label),
                            ).fetchone()
                            items[i]["count"] = int(_ragr["cnt"] if _ragr else 0)
                            items[i]["status"] = (
                                "completed" if items[i]["count"] > 0 else "pending"
                            )
                        except Exception as _re:
                            _log.debug(
                                "[progress-db] rag_chunks 计数失败: %s",
                                str(_re)[:80],
                            )
                            items[i]["status"] = step_status
                    else:
                        items[i]["status"] = step_status
        finally:
            if conn:
                release_connection(conn)
    return items


def _build_step_sub_items(step, step_status, project, version_label, run_id=None):
    """构建 step 的子项状态列表。

    Step1：从 step1_status JSON 读取各数据源独立完成情况。
    Step2-5：子项状态跟随 step 状态，pre_gate_* 子项从 executions 表读取独立状态。
    """
    if step == 1:
        return _collect_step1_sub_items(project, version_label, step_status)
    subs = _STEP_SUB_ITEMS.get(step, [])
    results = []
    for s in subs:
        if s["key"].startswith("pre_gate_"):
            gate_status = (
                _collect_pre_gate_status(step, run_id, project)
                if run_id
                else step_status
            )
            results.append(
                {
                    "key": s["key"],
                    "label": s["label"],
                    "group": s.get("group", ""),
                    "category": s.get("category", "left"),
                    "status": gate_status,
                    "count": 0,
                }
            )
        else:
            results.append(
                {
                    "key": s["key"],
                    "label": s["label"],
                    "group": s.get("group", ""),
                    "category": s.get("category", "left"),
                    "status": step_status,
                    "count": 0,
                }
            )
    return results


def _collect_pre_gate_status(step: int, run_id: str, project: str) -> str:
    """从 executions 表查询 pre_gate 子项的执行状态。"""
    if not run_id:
        return "pending"
    try:
        from src.core.db_manager import get_connection, release_connection

        conn = None
        try:
            conn = get_connection()
            row = conn.execute(
                "SELECT status FROM executions "
                "WHERE run_id=%s AND project=%s AND case_id=%s AND check_type='pre_gate' "
                "ORDER BY executed_at DESC LIMIT 1",
                (run_id, project, f"pre_gate_step{step}"),
            ).fetchone()
            if row:
                st = row.get("status", "")
                if st in ("pass",):
                    return "completed"
                elif st in ("fail", "PRE_GATE", "PRE_GATE_BLOCKED"):
                    return "error"
                return "pending"
            return "pending"
        finally:
            if conn:
                release_connection(conn)
    except Exception as e:
        _log.debug("[pipeline-progress] pre_gate 状态查询失败: %s", str(e)[:80])
        return "pending"


def _extract_req_confidence(req: dict) -> int:
    """从需求 dict 提取置信度（0-100）。

    优先级：
      1. payload 里的 _evidence_confidence（0.0-1.0 → 0-100）
      2. _confidence_level（HIGH=90 / MEDIUM=60 / LOW=30）
      3. -1（无数据）
    """
    ev = req.get("_evidence_confidence")
    if ev is not None:
        try:
            return int(float(ev) * 100)
        except (ValueError, TypeError) as _e:
            _log.debug(
                "[pipeline_progress] _evidence_confidence 解析异常: %s",
                _e,
                exc_info=True,
            )
    level = str(req.get("_confidence_level", "") or "").upper()
    if level == "HIGH":
        return 90
    if level == "MEDIUM":
        return 60
    if level == "LOW":
        return 30
    return -1


def _collect_requirements(current_step, req_progress, req_list, case_count_map):
    """聚合需求条目状态：优先用 req_progress，无则按 step 推断。

    ★ 补充原文/来源/位置字段，供前端需求详情面板展示。
    """
    items = []
    for r in req_list:
        req_id = r.get("req_id", "")
        # 优先取实时 per-req 进度
        rp = req_progress.get(req_id, {})
        status = rp.get("status")
        duration = rp.get("duration")
        # 无实时进度时，req_list 既已从 requirements 表读出，说明该需求已拆解完成
        if not status:
            status = "completed"
        # duration 为空时默认 0（已完成但未记录时长的需求）
        if duration is None:
            duration = 0
        items.append(
            {
                "req_id": req_id,
                "description": r.get("description", ""),
                "module": r.get("module", ""),
                "operation": r.get("operation", ""),
                "condition_expr": r.get("condition_expr", ""),
                "expected_feedback": r.get("expected_feedback", ""),
                "source_quote": r.get("source_quote", ""),
                "source_anchor": r.get("source_anchor", ""),
                "slice_id": r.get("slice_id", ""),
                "is_implicit": bool(r.get("is_implicit", 0)),
                "review_status": r.get("review_status", ""),
                "status": status,
                "duration": duration,
                "case_count": case_count_map.get(req_id, 0),
                # ★ 需求置信度：优先取 payload 里的 _evidence_confidence（0-1 → 0-100）；
                #   无则取 _confidence_level 转换；都没有则返回 -1 表示无数据
                "confidence": _extract_req_confidence(r),
            }
        )
    return items


def _aggregate_pipeline_progress():
    """聚合流水线进度数据。返回 dict 或 (error_dict, http_status)。"""
    import src.backend.state as _st
    from src.backend.state import (
        _active_runs,
        _active_runs_lock,
        _run_progress,
        _run_progress_lock,
    )
    from src.backend.routes.execution.review import _get_review_state

    # ★ 获取当前项目 + 版本隔离参数（按 project_memory 约束：所有表按 (project, version_id) 隔离）
    _proj = _current_project()
    try:
        from src.core.versioning import current_version_id, current_version_label

        _vid = current_version_id(_proj)
        _vlabel = current_version_label(_proj)
    except Exception as _ve:
        _log.debug("[pipeline-progress] 获取版本信息失败: %s", str(_ve)[:80])
        _vid, _vlabel = 0, ""

    # 当前活动 run（★ 按项目过滤：只取当前项目的活动 run）
    with _active_runs_lock:
        active_run_id = None
        active_run = None
        for rid, r in _active_runs.items():
            if r.get("project") == _proj:
                active_run_id = rid
                active_run = r
                break
            # ★ 降级：无当前项目的 run 时，取第一个（兼容旧条目无 project 字段）
        if not active_run_id and _active_runs:
            rid, r = next(iter(_active_runs.items()))
            active_run_id = rid
            active_run = r

    running = active_run_id is not None
    current_step = 0
    step_done = 0
    step_total = 0
    req_progress = {}
    elapsed_seconds = 0

    if active_run:
        current_step = active_run.get("step", 0)
        start_ts = active_run.get("start_ts", 0)
        if start_ts:
            elapsed_seconds = round(time.time() - start_ts, 1)
        with _run_progress_lock:
            prog = _run_progress.get(active_run_id, {})
            current_step = prog.get("current_step", current_step)
            step_done = prog.get("step_done", 0)
            step_total = prog.get("step_total", 0)
            req_progress = dict(prog.get("req_progress", {}))
        # ★ Step3 专属进度：req_progress 由 _modeling_engine.py 逐条需求写入，
        #    但 step_done/step_total 仍为 -1（scheduler 只在步骤开始/结束回调）。
        #    这里从 req_progress 统计已完成数 / 总数，让前端进度条显示 X/Y。
        if current_step == 3 and req_progress:
            _rp_total = len(req_progress)
            _rp_done = sum(
                1
                for v in req_progress.values()
                if isinstance(v, dict) and v.get("status") in ("completed", "failed")
            )
            if _rp_total > 0:
                step_done = _rp_done
                step_total = _rp_total

    # 需求列表
    req_list = []
    with guard("_aggregate_pipeline_progress: from src.core.excel_handler import e", log=_log):
        from src.core.excel_handler import excel_handler

        req_list = excel_handler.read_requirements() or []

    # 用例 → 需求 计数映射 + 用例列表（供前端用例列表面板使用）
    case_count_map = Counter()
    total_cases = 0
    cases_for_ui = []
    try:
        from src.pipeline.utils.case_store import case_store

        cases = case_store.list_intents_with_meta()
        for c in cases:
            rid = c.get("req_id", "")
            if rid:
                case_count_map[rid] += 1
            # 构建前端用例列表所需的最小字段集
            cases_for_ui.append(
                {
                    "case_id": str(c.get("case_id", "") or "").strip(),
                    "title": str(
                        c.get("title") or c.get("intent_summary") or ""
                    ).strip(),
                    "req_id": str(c.get("req_id", "") or "").strip(),
                    "confidence": int(c.get("_confidence", 0) or 0),
                    "review_status": str(
                        c.get("_review_status", "") or "pending"
                    ).strip(),
                    "risk_level": str(c.get("_risk_level", "") or "").strip(),
                    "case_type": str(c.get("case_type", "") or "").strip(),
                    "valid_status": str(
                        c.get("valid_status", "valid") or "valid"
                    ).strip(),
                    # ★ 根因修复(2026-09-02): 透传 required_tracks(元素∈ui/api/db)，
                    #   前端用例详情/执行详情据此只渲染用例真实轨道，避免写死"三轨"误导
                    "required_tracks": [
                        t for t in (c.get("required_tracks") or [])
                        if t in ("ui", "api", "db")
                    ] or None,
                    "actions_count": len(c.get("case_actions", []) or []),
                    "expected_outcome": str(
                        c.get("expected_outcome", "") or ""
                    ).strip()[:120],
                    "created_at": str(c.get("created_at", "") or "").strip(),
                    "updated_at": str(c.get("updated_at", "") or "").strip(),
                }
            )
        total_cases = len(cases)
        # ★ 三轨用例个数统计（不去重：多轨用例在各轨道各计 1）
        track_counts = {"ui": 0, "api": 0, "db": 0}
        types = {}
        for c in cases:
            _rt = c.get("required_tracks") or []
            _seen = set()
            for t in _rt:
                if t in track_counts and t not in _seen:
                    track_counts[t] += 1
                    _seen.add(t)
            # ★ 类型分布（case_type：功能/安全/性能/易用/边界/异常）
            _ct = c.get("case_type") or "未知"
            types[_ct] = types.get(_ct, 0) + 1
    except Exception as e:
        _log.debug("[pipeline-progress] 读取用例列表失败: %s", str(e)[:80])
        track_counts = {"ui": 0, "api": 0, "db": 0}
        types = {}

    # Step5: 报告列表（供前端报告详情面板使用）★ 按 (project, version_id) 隔离
    reports_for_ui = []
    with guard("_aggregate_pipeline_progress: from src.core.db_manager import get_", log=_log):
        from src.core.db_manager import get_connection, release_connection

        conn = None
        try:
            conn = get_connection()
            _rp = conn.execute(
                "SELECT id, project, run_id, report_type, char_count, created_at, updated_at"
                " FROM generated_reports WHERE project=%s AND version_id=%s ORDER BY created_at DESC",
                (_proj, _vid),
            ).fetchall()
            if _rp:
                for _r in _rp:
                    reports_for_ui.append(
                        {
                            "report_id": str(_r["id"]),
                            "run_id": str(_r.get("run_id", "") or ""),
                            "report_type": str(_r.get("report_type", "") or ""),
                            "char_count": int(_r.get("char_count", 0) or 0),
                            "created_at": str(_r.get("created_at", "") or ""),
                            "updated_at": str(_r.get("updated_at", "") or ""),
                        }
                    )
        finally:
            if conn:
                release_connection(conn)

    # Step1: 数据源文档列表（source_documents）★ 按 version_label 隔离
    source_docs_for_ui = []
    with guard("_aggregate_pipeline_progress: from src.core.db_manager import get_", log=_log):
        from src.core.db_manager import get_connection, release_connection

        _conn2 = None
        try:
            _conn2 = get_connection()
            _sd = _conn2.execute(
                "SELECT doc_type, filename, file_format, category, weight, "
                "content_hash, char_count, created_at "
                "FROM source_documents WHERE project=%s AND version_label=%s ORDER BY created_at DESC",
                (_proj, _vlabel),
            ).fetchall()
            for _s in _sd:
                source_docs_for_ui.append(
                    {
                        "doc_type": str(_s.get("doc_type", "") or ""),
                        "filename": str(_s.get("filename", "") or ""),
                        "file_format": str(_s.get("file_format", "") or ""),
                        "category": str(_s.get("category", "") or ""),
                        "weight": float(_s.get("weight", 0) or 0),
                        "content_hash": str(_s.get("content_hash", "") or ""),
                        "char_count": int(_s.get("char_count", 0) or 0),
                        "created_at": str(_s.get("created_at", "") or ""),
                    }
                )
        finally:
            if _conn2:
                release_connection(_conn2)

    # Step4: 执行记录列表（executions）★ 按 (project, version_id) 隔离
    executions_for_ui = []
    with guard("_aggregate_pipeline_progress: from src.core.db_manager import get_", log=_log):
        from src.core.db_manager import get_connection, release_connection

        _conn3 = None
        try:
            _conn3 = get_connection()
            _ex = _conn3.execute(
                "SELECT e.id, e.run_id, e.case_id, e.status, e.failure_reason, "
                "e.failure_category, e.executed_at, e.user_id, "
                "(SELECT t.title FROM test_cases t "
                "WHERE t.project=e.project AND t.version_id=e.version_id AND t.case_id=e.case_id "
                "LIMIT 1) AS title "
                "FROM executions e WHERE e.project=%s AND e.version_id=%s "
                "ORDER BY e.executed_at DESC LIMIT 200",
                (_proj, _vid),
            ).fetchall()
            for _r in _ex:
                executions_for_ui.append(
                    {
                        "id": _r.get("id", None),
                        "run_id": str(_r.get("run_id", "") or ""),
                        "case_id": str(_r.get("case_id", "") or ""),
                        "title": str(_r.get("title", "") or ""),
                        "status": str(_r.get("status", "") or ""),
                        "failure_reason": str(_r.get("failure_reason", "") or "")[:160],
                        "failure_category": str(_r.get("failure_category", "") or ""),
                        "executed_at": str(_r.get("executed_at", "") or ""),
                        "user_id": str(_r.get("user_id", "") or ""),
                    }
                )
        finally:
            if _conn3:
                release_connection(_conn3)

    # 各 step 的产物数量（用于 completed step 的监控数据展示）
    req_count = len(req_list)
    # ★ 无论是否 running，都从 DB 产物推断已完成步骤（传入版本隔离参数）
    #    修复：running 时 db_counts 为空导致 Step 1/4/5 永远显示 pending
    db_step, db_counts = infer_step_from_db(
        _proj, version_id=_vid, version_label=_vlabel
    )
    if not running and db_step > current_step:
        current_step = db_step
    per_step_counts = {
        1: db_counts.get(1, 0),  # Step 1: source_documents
        2: max(req_count, db_counts.get(2, 0)),  # Step 2: requirements
        3: max(total_cases, db_counts.get(3, 0)),  # Step 3: test_cases
        4: db_counts.get(
            4, 0
        ),  # Step 4: executions（★ 修复：只用自身产物表计数，不再被 total_cases 覆盖）
        5: db_counts.get(5, 0),  # Step 5: generated_reports
    }

    steps = _collect_steps_status(
        current_step,
        running,
        step_done=step_done,
        step_total=step_total,
        per_step_counts=per_step_counts,
    )
    # ★ 为每个 step 补充子项状态（Step1 从 step1_status 推断，Step2-5 跟随 step 状态，pre_gate_* 独立）
    for st in steps:
        st["sub_items"] = _build_step_sub_items(
            st["step"], st["status"], _proj, _vlabel, active_run_id
        )
    requirements = _collect_requirements(
        current_step, req_progress, req_list, case_count_map
    )

    return {
        "ok": True,
        "running": running,
        "current_step": current_step,
        "elapsed_seconds": elapsed_seconds,
        "steps": steps,
        "step_done": step_done,
        "step_total": step_total,
        "requirements": requirements,
        "cases": cases_for_ui,
        "total_cases": total_cases,
        "track_counts": track_counts,
        "types": types,
        "reports": reports_for_ui,
        "source_docs": source_docs_for_ui,
        "executions": executions_for_ui,
        "review_state": _get_review_state(),
    }


# ════════════════════════════════════════════════════════════════
# 模块级路由注册（与 status.py 模式一致）
# ════════════════════════════════════════════════════════════════
from src.backend.routes.execution import exec_bp  # noqa: E402
from src.models.permission import check_admin_or_permission  # noqa: E402
from src.backend.state import _current_project  # noqa: E402


@exec_bp.route("/api/pipeline-progress", methods=["GET"])
def api_pipeline_progress():
    """聚合流水线执行进度（步骤 + 需求条目维度）。
    ★ 只读查询接口，所有登录用户可访问（dashboard 需要展示状态）。
    """
    from src.core.config_loader import get_current_user

    uid = get_current_user()
    if not uid:
        return jsonify({"ok": False, "error": "请先登录"}), 401
    try:
        result = _aggregate_pipeline_progress()
        return jsonify({"ok": True, **result})
    except Exception as e:
        _log.error("[api/pipeline-progress] 异常: %s", e, exc_info=True)
        return jsonify({"ok": False, "error": "服务器内部错误"}), 500


@exec_bp.route("/api/report-content/<int:report_id>", methods=["GET"])
def api_report_content(report_id):
    """获取指定报告的内容（供前端报告详情面板使用）。"""
    ok, err = check_admin_or_permission("execution.run", project=_current_project())
    if not ok:
        return jsonify({"ok": False, "error": err}), (
            401 if err.startswith("请先登录") else 403
        )
    try:
        from src.core.db_manager import get_connection, release_connection

        conn = None
        try:
            conn = get_connection()
            row = conn.execute(
                "SELECT content, char_count, report_type FROM generated_reports WHERE id=%s",
                (report_id,),
            ).fetchone()
            if not row:
                return jsonify({"ok": False, "error": "报告不存在"}), 404
            return jsonify(
                {
                    "ok": True,
                    "content": row["content"] or "",
                    "char_count": row["char_count"] or 0,
                    "report_type": row["report_type"] or "",
                }
            )
        finally:
            if conn:
                release_connection(conn)
    except Exception as e:
        _log.error("[api/report-content] 异常: %s", e, exc_info=True)
        return jsonify({"ok": False, "error": "服务器内部错误"}), 500


@exec_bp.route("/api/source-doc-content/<doc_type>", methods=["GET"])
def api_source_doc_content(doc_type):
    """获取指定数据源文档的内容（供前端文档详情面板展示）。

    ★ 按 (project, version_label) 隔离；doc_type 为 source_documents 表的 PK 之一。
    """
    ok, err = check_admin_or_permission("execution.run", project=_current_project())
    if not ok:
        return jsonify({"ok": False, "error": err}), (
            401 if err.startswith("请先登录") else 403
        )
    try:
        from src.core.db_manager import get_connection, release_connection
        from src.core.versioning import current_version_label

        _proj = _current_project()
        _vlabel = current_version_label(_proj)
        conn = None
        try:
            conn = get_connection()
            row = conn.execute(
                "SELECT content, char_count, file_format, filename, category, weight "
                "FROM source_documents WHERE project=%s AND version_label=%s AND doc_type=%s",
                (_proj, _vlabel, doc_type),
            ).fetchone()
            if not row:
                return jsonify({"ok": False, "error": "文档不存在"}), 404
            # ★ 二进制 doc_type（xmind / upload_xlsx 等）content 是 base64，不直接展示
            _fmt = str(row.get("file_format", "") or "").lower()
            _binary_exts = ("xlsx", "xls", "xmind", "png", "jpg", "jpeg", "pdf")
            _is_binary = any(_fmt.endswith("." + e) or _fmt == e for e in _binary_exts)
            content = "" if _is_binary else (row.get("content", "") or "")
            return jsonify(
                {
                    "ok": True,
                    "content": content,
                    "char_count": row.get("char_count", 0) or 0,
                    "file_format": row.get("file_format", "") or "",
                    "filename": row.get("filename", "") or "",
                    "category": row.get("category", "") or "",
                    "weight": float(row.get("weight", 0) or 0),
                    "is_binary": _is_binary,
                }
            )
        finally:
            if conn:
                release_connection(conn)
    except Exception as e:
        _log.error("[api/source-doc-content] 异常: %s", e, exc_info=True)
        return jsonify({"ok": False, "error": "服务器内部错误"}), 500


# ★ 架构债清理(2026-07-24): 追溯链路 API 拆分到 _pipeline_trace.py（§4.3 单文件≤800行）
from ._pipeline_trace import api_pipeline_trace  # noqa: E402, F401 — 触发路由注册
