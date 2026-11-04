# -*- coding: utf-8 -*-
"""
建模引擎（v11）：并行独立意图建模
================================
★ 每个需求独立 AI 调用（含独立 RAG 检索），ThreadPoolExecutor 并行执行。
"""

import logging
from src.core.guarded import guard
import time
from typing import Any
from concurrent.futures import ThreadPoolExecutor, as_completed

# ★ 架构债清理(2026-07-25): 验证函数拆分至 _modeling_validators.py（§4.3 单文件≤800行）
from src.pipeline.step3._modeling_validators import (
    _validate_navigate_urls,
    _find_closest_nav_name,
    _validate_element_names,
    _extract_chinese_terms,
    _cleanup_navigate_urls,
)

# ★ 架构债清理(2026-07-25): RAG 函数拆分至 _modeling_rag.py
from src.pipeline.step3._modeling_rag import (
    _augment_with_multimodal_rag,
    _extract_rag_usage_records,
    _write_rag_usage_logs,
    list_rag_usage_log,
    count_rag_usage_log,
)

# ★ 架构债清理(2026-07-25): 意图写入函数拆分至 _modeling_intents.py
from src.pipeline.step3._modeling_intents import (
    _write_intents_to_store,
    _merge_cases_by_page,
    _collapse_page_scoped_cases,
)

_log = logging.getLogger(__name__)

# ★ 操作类型 → RAG 类型映射（2026-07-12）：按操作语义过滤 RAG 检索类型，
#   避免全量 dump 导致信息稀释。原理：点击按钮不需要 API 端点，查询数据不需要 DOM 元素。
#   通用性：基于操作关键词，零业务硬编码。
_UI_OPERATIONS = {
    "点击",
    "双击",
    "右键",
    "click",
    "double",
    "right",
    "输入",
    "填写",
    "清空",
    "fill",
    "type",
    "clear",
    "选择",
    "勾选",
    "取消勾选",
    "select",
    "check",
    "uncheck",
    "上传",
    "下载",
    "upload",
    "download",
    "悬停",
    "聚焦",
    "失焦",
    "hover",
    "focus",
    "blur",
    "拖拽",
    "滚动",
    "长按",
    "drag",
    "scroll",
}
_DATA_OPERATIONS = {
    "查询",
    "搜索",
    "筛选",
    "query",
    "search",
    "filter",
    "删除",
    "移除",
    "delete",
    "remove",
    "添加",
    "新增",
    "创建",
    "add",
    "create",
    "new",
    "修改",
    "编辑",
    "更新",
    "modify",
    "edit",
    "update",
    "导出",
    "导入",
    "export",
    "import",
    "查看",
    "浏览",
    "详情",
    "view",
    "detail",
    "审批",
    "驳回",
    "通过",
    "approve",
    "reject",
    "分配",
    "指派",
    "配置",
    "assign",
    "config",
    "启用",
    "禁用",
    "重置",
    "enable",
    "disable",
    "reset",
    # ★ 注意：保存/提交/确认 不在此列——它们常作为控件名（"保存按钮"）而非数据操作，
    #   放入会导致"点击保存按钮"被误判为 MIXED。此类操作走 fallback ALL。
}


def _get_rag_types_for_operation(operation: str) -> list:
    """根据操作语义确定需要检索的 RAG 类型。

    原则：
    - UI 操作（点击/输入/选择/上传）→ dom_snapshots + figma_* + similar_requirements
    - 数据操作（查询/删除/添加/修改/导出）→ api_endpoints + db_tables + similar_requirements
    - 混合操作（保存/提交/确认）→ 全部
    - dom_snapshots 和 similar_requirements 始终包含（页面定位 + 参考用例）
    """
    if not operation:
        return None  # None = 全部（向后兼容）

    op_lower = operation.lower().strip()
    is_ui = any(kw in op_lower for kw in _UI_OPERATIONS)
    is_data = any(kw in op_lower for kw in _DATA_OPERATIONS)

    types = ["dom_snapshots", "similar_requirements"]

    if is_ui and not is_data:
        # 纯 UI 操作：只需要 DOM 和 Figma
        types.extend(["figma_pages", "figma_nodes"])
    elif is_data and not is_ui:
        # 纯数据操作：只需要 API 和 DB
        types.extend(["api_endpoints", "db_tables"])
    else:
        # 混合操作或无法判断：全部类型
        return None

    return types


_RAG_TOP_K = 5
_TRACE_CONTEXT_LIMIT = 5
# ★ P2-11 _MM_RAG_* 常量已迁移到 _modeling_rag.py（唯一消费方，避免循环 import）


def _ensure_action_params(all_intents: list[dict]) -> list[dict]:
    """后处理：补齐 AI 生成动作中缺失的必填参数 + 清理泛化问题。

    当前处理：
      - upload_file 缺少 file_path 时，填充默认测试文件占位路径
      - 移除 target_hint 中的"按钮"后缀（如"导入按钮"→"导入"）
      - 替换"文件上传控件"为操作关键词
      - 修复空的 navigate url
    """
    import copy

    fixed = []
    for intent in all_intents:
        if not isinstance(intent, dict):
            fixed.append(intent)
            continue
        intent = copy.deepcopy(intent)
        actions = intent.get("actions")
        if isinstance(actions, str):
            try:
                import json as _json

                actions = _json.loads(actions)
            except Exception:
                actions = []
        if not isinstance(actions, list):
            fixed.append(intent)
            continue

        # ★ 治本修复 (2026-07-17): 获取操作关键词用于清理泛化hint
        _op = str(
            intent.get("operation", "") or intent.get("description", "") or ""
        ).strip()

        for a in actions:
            if not isinstance(a, dict):
                continue
            atype = str(a.get("type", "") or a.get("action", "")).strip()
            if atype == "upload_file":
                fp = a.get("file_path") or a.get("file") or ""
                if not fp:
                    a["file_path"] = "__E2E_DEFAULT_UPLOAD__"

            # ★ 治本修复 (2026-07-17): 清理 target_hint 泛化后缀
            hint = str(a.get("target_hint", "") or "")
            if hint:
                # 1. 移除"按钮"后缀（含"按钮（...）"模式，如"编辑按钮（第一条记录）"→"编辑"）
                if hint.endswith("按钮"):
                    a["target_hint"] = hint[:-2]
                elif "按钮（" in hint:
                    a["target_hint"] = hint.split("按钮（")[0]
                # 2. 替换"文件上传控件"/"上传控件"为通用"上传"
                elif "文件上传" in hint or "上传控件" in hint:
                    a["target_hint"] = "上传"
                # 3. 移除"控件"后缀（如"输入框控件"→"输入框"）
                elif hint.endswith("控件"):
                    a["target_hint"] = hint[:-2]

            # ★ 治本修复 : 修复空的 navigate url
            # ★ 通用修复 (2026-07-18): 当 target_hint 是中文页面名时，不设置为 url
            #   Step4 会通过菜单回退导航到目标页面，url 保持 "/" 即可
            if a.get("type") == "navigate":
                url = a.get("url", "")
                if not url or url == "/":
                    # 优先用 intent 的 target_page，其次用 navigate action 的 target_hint
                    _tp = str(
                        intent.get("target_page", "") or a.get("target_hint", "") or ""
                    ).strip()
                    # ★ 根因修复(2026-09-27): 移除历史 `_tp != "/login"` 排除项。
                    #   它与导航解析器的认证分支（_login_entry_url()，2026-09-27）直接冲突：
                    #   解析器为认证用例写入真实登录入口 url，此处却拒绝把 "/login" 补进 url
                    #   → 落库后 navigate 动作只剩 target_hint、url 为空 = 不可执行指令 →
                    #   Step4 判"导航失败或元素未找到" → 认证类用例整体 ERROR
                    #   （实测 TC-REQ-005-E01）。/login 是系统真实可执行页面，
                    #   "无菜单节点"≠"不可导航"；保留 `_tp != "/"`（根路径仍属泛化兜底）。
                    if _tp and _tp != "/":
                        # 只有当 target_hint 是有效 URL 路径（以 / 开头且不含中文）时才作为 url
                        if _tp.startswith("/") and not any(
                            "\u4e00" <= c <= "\u9fff" for c in _tp
                        ):
                            a["url"] = _tp

        intent["actions"] = actions
        fixed.append(intent)
    return fixed


def _enrich_requirements(
    ungen_reqs: list[dict], rag_enabled: bool, trace_links: dict, data_context: str = ""
) -> tuple:
    """为建模收集上下文：RAG、trace、切片原文。"""
    rag_ctx_parts = []
    trace_ctx_parts = []
    enriched_reqs = []
    # ★ S3-012: req_id → RAG 使用记录列表（暂存，用例写入后关联 case_id/check_id）
    rag_usage_map: dict[str, list[dict]] = {}

    # ★ 单一事实源(07-06): 批量从 requirement_slices 加载原文上下文，不再重复读取 payload_json._chunk_text
    slice_text_map = {}
    with guard("_enrich_requirements: from src.pipeline.utils.requirement_", log=_log, level=logging.WARNING):
        from src.pipeline.utils.requirement_slice_store import requirement_slice_store

        slice_ids = [r.get("slice_id") for r in ungen_reqs if r.get("slice_id")]
        if slice_ids:
            slice_text_map = requirement_slice_store.get_slice_text_map(slice_ids)

    for req in ungen_reqs:
        enriched = dict(req)
        req_id = req.get("req_id", "")

        # 注入切片原文（单一事实源）
        slice_id = enriched.get("slice_id") or enriched.get("_chunk_id")
        if slice_id and slice_id in slice_text_map:
            enriched["_chunk_text"] = slice_text_map[slice_id]

        # RAG
        if rag_enabled:
            with guard("_enrich_requirements: from src.extensions.rag_engine impor", log=_log, level=logging.WARNING):
                from src.extensions.rag_engine import rag_engine

                # ★ 2026-07-12: 按操作类型过滤 RAG 检索类型，避免全量 dump
                #   "点击保存按钮" → 只需 dom_snapshots + figma（不需要 API/DB）
                #   "查询烟叶信息" → 只需 api_endpoints + db_tables（不需要 DOM）
                _op = req.get("operation", "")
                _rag_types = _get_rag_types_for_operation(_op)
                # ★ RAG 查询用 operation（更精准），而非 description（全文）
                _rag_query = dict(req)
                if _op:
                    _rag_query["description"] = _op
                # ★ v4 + S3-012: 使用 retrieve_and_resolve 一站式管道，return_refs=True 获取 entity_refs
                rag_text, entity_refs = rag_engine.retrieve_and_resolve(
                    _rag_query,
                    top_k=_RAG_TOP_K,
                    return_refs=True,
                    retrieve_types=_rag_types,
                )
                # ★ 2026-07-12: 10 层过滤管道 L6-L10（MMR + Cross-Encoder + LLM-as-Judge）
                #   L1-L5 已在 retrieve_and_resolve + _contexts 阶段完成
                if entity_refs:
                    with guard("_enrich_requirements: from src.extensions.rag_engine.rag_f", log=_log):
                        from src.extensions.rag_engine.rag_filter import (
                            filter_rag_results,
                        )

                        _filter_result = filter_rag_results(
                            entity_refs,
                            requirement=req,
                            operation=_op,
                            description=req.get("description", ""),
                        )
                        entity_refs = _filter_result["entity_refs"]
                        _log.debug(
                            "[Step3] RAG 过滤完成: %s", _filter_result.get("stats", {})
                        )
                if rag_text:
                    rag_ctx_parts.append(rag_text)
                    enriched["_rag"] = rag_text
                # ★ S3-012: 收集 RAG 使用记录（暂存，后续关联 case_id/check_id）
                if entity_refs:
                    usage_records = _extract_rag_usage_records(req_id, entity_refs)
                    if usage_records:
                        rag_usage_map[req_id] = usage_records
                        enriched["_rag_usage"] = usage_records

        # ★ P2-11 MultiModalRAG 跨模态增强（防幻觉：追加 requirement+dom 事实源约束）
        #   根因(§10.9): AI 补漏路径缺 DOM 真实控件约束 → 幻觉按钮/路径
        #   通用性: 纯检索增强，异常降级不阻断；跨系统/行业/页面通用
        #   职责边界: 仅增强 _rag 上下文，不改 rag_engine 主链路
        with guard("_enrich_requirements: _mm_op = req.get('operation', '') or", log=_log):
            _mm_op = req.get("operation", "") or req.get("description", "") or req_id
            _mm_text = _augment_with_multimodal_rag(req, _mm_op)
            if _mm_text:
                _existing = enriched.get("_rag", "") or ""
                enriched["_rag"] = (
                    (_existing + "\n" + _mm_text) if _existing else _mm_text
                )

        # Trace（★ 2026-07-12: 按操作类型过滤，避免注入无关资源）
        if trace_links:
            link = trace_links.get(req_id)
            if link:
                parts = []
                _op = req.get("operation", "")
                _is_ui = (
                    any(kw in _op.lower() for kw in _UI_OPERATIONS) if _op else False
                )
                _is_data = (
                    any(kw in _op.lower() for kw in _DATA_OPERATIONS) if _op else False
                )
                _show_all = (not _is_ui and not _is_data) or (_is_ui and _is_data)

                apis = getattr(link, "api_endpoints", None) or (
                    link.get("api_endpoints", []) if isinstance(link, dict) else []
                )
                tables = getattr(link, "db_tables", None) or (
                    link.get("db_tables", {}) if isinstance(link, dict) else {}
                )
                pages = getattr(link, "figma_pages", None) or (
                    link.get("figma_pages", []) if isinstance(link, dict) else []
                )

                # 纯 UI 操作 → 只取页面；纯数据操作 → 只取 API+DB；混合/未识别 → 全取
                if (_show_all or _is_ui) and pages:
                    parts.append(
                        "页面: "
                        + ", ".join(str(p) for p in pages[:_TRACE_CONTEXT_LIMIT])
                    )
                if (_show_all or _is_data) and apis:
                    parts.append(
                        "API: "
                        + ", ".join(
                            (
                                f"{e.get('method','?')} {e.get('path','?')}"
                                if isinstance(e, dict)
                                else str(e)
                            )
                            for e in apis[:_TRACE_CONTEXT_LIMIT]
                        )
                    )
                if (_show_all or _is_data) and tables:
                    if isinstance(tables, dict):
                        parts.append(
                            "DB: "
                            + ", ".join(list(tables.keys())[:_TRACE_CONTEXT_LIMIT])
                        )
                    elif isinstance(tables, list):
                        parts.append(
                            "DB: "
                            + ", ".join(str(t) for t in tables[:_TRACE_CONTEXT_LIMIT])
                        )
                t = " | ".join(parts) if parts else ""
                if t:
                    trace_ctx_parts.append(f"[{req_id}] {t}")
                    enriched["_trace"] = t

        if data_context:
            enriched["_db_context"] = data_context

        enriched_reqs.append(enriched)

    rag_context = "\n".join(rag_ctx_parts[:10]) if rag_ctx_parts else ""
    trace_context = "\n".join(trace_ctx_parts) if trace_ctx_parts else ""

    return enriched_reqs, rag_context, trace_context, rag_usage_map


# ═══════════════════════════════════════════════════════════════
# ★ S3-012: RAG 使用日志辅助函数
# ═══════════════════════════════════════════════════════════════

# ★ _RAG_TYPE_MAP 已迁移到 _modeling_rag.py（唯一消费方，避免循环 import）


def _parallel_model_all_reqs(
    ungen_reqs: list[dict],
    project_context: str = "",
    ui_ctx: str = "",
    data_context: str = "",
    rag_enabled: bool = False,
    trace_links: dict = None,
    stop_check=None,
    progress_cb=None,
    run_id=None,
) -> tuple:
    """并行建模全部需求：每个需求独立 AI 调用，ThreadPoolExecutor 并行执行。
    返回 (意图数, 是否被终止)。

    ★ run_id: 用于写入 per-req 进度到 _run_progress（流水线执行页面消费）。
    """
    from src.core.config_loader import config
    from src.pipeline.steps.step3.case_generator._model import model_test_cases
    from src.pipeline.step3._fallback_cases import _build_rule_fallback_cases
    from src.pipeline.step3._requirement_selector import (
        _load_previous_intents,
    )  # ★ 优化6
    from src.pipeline.step3._control_validation import (
        validate_control_existence,
    )  # ★ 控件存在性预验证

    # ★ 修复(2026-07-18): 初始化 project 防止 UnboundLocalError
    project = ""

    if stop_check and stop_check():
        return 0, True

    total_reqs = len(ungen_reqs)
    _log.info("[Step3] 建模开始: %d 条需求 → 先规则提取，剩余走 AI 补漏", total_reqs)

    # ── 收集上下文（串行，RAG/Trace 检索） ──
    enriched_reqs, rag_context, trace_context, rag_usage_map = _enrich_requirements(
        ungen_reqs, rag_enabled, trace_links or {}, data_context
    )

    # ★ P0 重构：规则优先 + AI 补漏架构
    # 在 AI worker 启动之前，先用规则提取前置层处理需求：
    # - 规则提取成功 → 直接生成用例（标记 _generation_source="rule_extract"，豁免门禁）
    # - 规则提取失败 → 加入 _ai_pending_reqs，由 AI worker 处理
    # 这样避免 AI 调用浪费在规则可覆盖的需求上（省 token + 省时间）
    from src.pipeline.step3._rule_extractor import (
        extract_cases_by_rules,
        _is_system_integration_requirement,
    )

    rule_intents = []
    _ai_pending_reqs = []
    _rule_extracted_count = 0

    # ★ 通用修复(2026-07-21 v19): 跳过系统集成类需求（不适合 UI E2E 测试）
    #   根因(4层追问):
    #     1. 表层: TC-REQ-061-P01 降级用例 text_contains_check 失败
    #     2. 中层: 降级用例检查 operation 滑动窗口子串（"统从办公"等），dashboard 不包含
    #     3. 根因: REQ-061 operation="系统从办公协同获取叶组配方化学成份数据"
    #              是系统集成类需求，没有对应的 UI 页面
    #     4. 深层: Step2 拆解时把"系统建设目标"类描述列为需求条目(is_implicit=1)，
    #              Step3 为它生成 UI 降级用例本身就是错误的
    #   修复: 在 Step3 主流程最早阶段跳过系统集成类需求，不生成任何 UI 用例
    #   通用性: 基于 operation 语义判断，适用于所有系统/行业的系统集成类需求
    #   合理性: 追根溯源到"为系统集成类需求生成 UI 用例"的根因，从源头跳过
    #   覆盖性: 在规则提取之前过滤，覆盖所有用例生成路径（规则提取 + AI 兜底 + fallback）
    #   自我反驳:
    #     1) 反驳: 跳过会不会漏测？ → 系统集成类需求应由 API 测试/集成测试覆盖，不是 UI E2E
    #     2) 反驳: 判断规则是否过于宽松？ → 同时要求"系统"+系统集成类动词，不会误判用户操作
    #     3) 反驳: 跳过的需求在报告中如何体现？ → 日志记录跳过原因，Step5 报告中无此需求用例
    _skipped_sys_integration = []
    _filtered_reqs = []
    for _req in enriched_reqs:
        _op = str(_req.get("operation", "") or "")
        _desc = str(_req.get("description", "") or "")
        if _is_system_integration_requirement(_op, _desc):
            _skipped_sys_integration.append(
                {
                    "req_id": _req.get("req_id", ""),
                    "operation": _op[:60],
                    "reason": "系统集成类需求，不适合 UI E2E 测试，应由 API/集成测试覆盖",
                }
            )
            _log.info(
                "[Step3] ★ v19 跳过系统集成类需求: req_id=%s operation=%r",
                _req.get("req_id", ""),
                _op[:60],
            )
        else:
            _filtered_reqs.append(_req)

    if _skipped_sys_integration:
        _log.info(
            "[Step3] ★ v19 共跳过 %d 条系统集成类需求（不生成 UI 用例）",
            len(_skipped_sys_integration),
        )
        enriched_reqs = _filtered_reqs

    _log.info("[Step3] ★ 规则优先提取阶段开始（共 %d 条需求）", len(enriched_reqs))
    for req in enriched_reqs:
        _req_rag = req.get("_rag", rag_context) or ""
        _rule_cases = extract_cases_by_rules(
            req,
            rag_context=_req_rag,
            trace_context=trace_context,
            project_context=project_context,
        )
        if _rule_cases:
            rule_intents.extend(_rule_cases)
            _rule_extracted_count += 1
        else:
            _ai_pending_reqs.append(req)

    # ── ★ 规则 fallback 保底层（2026-09-16 反转）
    # 在控件级规则提取之后、AI worker 启动之前，对**所有需求**跑 _build_rule_fallback_cases。
    # 产出确定性 6 type 全覆盖（功能/UI/安全/性能/易用/兼容 × 多轨），作为稳定底座。
    # AI worker 继续跑，但只在"规则覆盖缺口"时补漏（如特定业务细节/跨轨联动）。
    # 这就是用户要求的"规则组装优先，规则不能覆盖才走 AI"架构。
    _fallback_intents_all = []
    _all_req_ids = {r.get("req_id", "") for r in enriched_reqs}
    for req in enriched_reqs:
        with guard("_parallel_model_all_reqs: _fb = _build_rule_fallback_cases(req", log=_log, level=logging.WARNING):
            _fb = _build_rule_fallback_cases(req, "规则优先保底层")
            _fallback_intents_all.extend(_fb)
    if _fallback_intents_all:
        rule_intents.extend(_fallback_intents_all)
        _log.info(
            "[Step3] ★ 规则 fallback 保底层: %d 条需求 → %d 条确定性用例（6 type × 多轨）",
            len(_all_req_ids), len(_fallback_intents_all),
        )

    if rule_intents:
        _log.info(
            "[Step3] ★ 规则提取完成: %d/%d 条需求由规则生成（%d 条用例），%d 条走 AI 补漏",
            _rule_extracted_count,
            len(enriched_reqs),
            len(rule_intents),
            len(_ai_pending_reqs),
        )
    else:
        _log.info(
            "[Step3] ★ 规则提取覆盖 0 条需求，全部 %d 条走 AI", len(_ai_pending_reqs)
        )

    # ★ H4-2: 记录 control_type 命中分布指标
    with guard("_parallel_model_all_reqs: from src.backend.state import _threa", log=_log):
        from src.backend.state import _thread_run_id
        from src.core.config_loader import config as _cfg
        from src.extensions.metrics_collector import metrics_collector
        from collections import Counter as _Counter

        _ct_hits = _Counter(ri.get("control_type", "button") for ri in rule_intents)
        _total_calls = _rule_extracted_count + len(_ai_pending_reqs)
        _rid = getattr(_thread_run_id, "value", "") or ""
        if _rid:
            metrics_collector.record_control_type_hit(
                _cfg.project_name,
                _rid,
                hit_counts=dict(_ct_hits),
                total_calls=_total_calls,
            )

    # 后续 worker 只处理规则未覆盖的需求
    enriched_reqs = _ai_pending_reqs
    total_reqs_ai = len(enriched_reqs)

    # ★ 风险分析移入 worker 内懒加载（消除串行瓶颈：25 条需求 × 嵌入调用不再阻塞并行启动）
    from src.extensions.quality.risk_analyzer import assess_requirement_risk

    # ── 并行生成意图 ──
    _max_workers = getattr(config, "step3_parallel_workers", 10)
    _log.info("[Step3] 启动并行: max_workers=%d", _max_workers)

    # P2: 交叉验证已就位，直接导入
    from src.pipeline.steps.step3.case_quality.cross_validation import (
        generate_with_cross_validation,
    )
    from src.pipeline.steps.step3.case_quality.orchestrator import (
        should_run_cross_validation,
    )

    # ★ 捕获主线程 session 上下文（ThreadPoolExecutor 子线程不会自动继承 threading.local）
    _captured_user = None
    _captured_key = None
    _captured_run_id = None  # ★ B-008: 捕获 thread_run_id
    try:
        from src.core.config_session import (
            get_current_user,
            get_session_key,
            set_current_user,
            set_session_key,
        )

        _captured_user = get_current_user()
        _captured_key = get_session_key()
        from src.backend.state import _thread_run_id

        _captured_run_id = getattr(_thread_run_id, "value", None)
    except Exception as _ce:
        _log.debug(
            "[_parallel_model_all_reqs] 捕获线程上下文失败: %s", _ce, exc_info=True
        )

    all_intents = []
    failed_reqs = []
    risk_map = {}  # ★ 从 worker 结果动态收集（替代串行预计算）

    # ★ P0: 先加入规则提取的用例（规则提取成功的用例风险级别默认 medium）
    if rule_intents:
        all_intents.extend(rule_intents)
        for ri in rule_intents:
            risk_map[ri.get("req_id", "")] = "medium"

    def _worker(req):
        """单个需求的意图生成 worker。风险分析在 worker 内懒加载（并行）。"""
        _req_id_for_progress = req.get("req_id", "")
        if run_id and _req_id_for_progress:
            try:
                from src.backend.state import _update_req_progress

                _update_req_progress(run_id, _req_id_for_progress, "running")
            except Exception as _e:
                _log.debug("[Step3] progress 更新失败(running): %s", _e, exc_info=True)
        try:
            if _captured_user:
                set_current_user(_captured_user)
            if _captured_key:
                set_session_key(_captured_key)
            if _captured_run_id:
                from src.backend.state import _thread_run_id as _rid

                _rid.value = _captured_run_id
        except Exception as _re:
            _log.debug(
                "[_parallel_model_all_reqs] 恢复线程上下文失败: %s", _re, exc_info=True
            )
        import threading as _th

        req_id = req.get("req_id", "")
        _log.info(
            "[Step3] 🔵 [%s] worker 启动 (thread=%s)", req_id, _th.current_thread().name
        )
        _is_implicit = int(req.get("is_implicit", 0) or 0)
        _skip_qa = _is_implicit == 2
        risk_level = "low" if _skip_qa else "medium"
        mp = {}
        try:
            _log.info("[Step3] 🔵 [%s] 开始风险分析...", req_id)
            _t0 = time.time()
            risk_level, mp = assess_requirement_risk(req)
            if _skip_qa:
                risk_level = "low"
                _log.info(
                    "[Step3] L3 [%s] 业务隐性候选 → risk强制=low, 将跳过后置质量检查",
                    req_id,
                )
            _log.info(
                "[Step3] 🔵 [%s] 风险分析完成 (risk=%s, 耗时%.1fs)",
                req_id,
                risk_level,
                time.time() - _t0,
            )

            if should_run_cross_validation(risk_level):
                _log.info(
                    "[Step3] 🔀 [%s] risk=%s → 交叉验证 (model_a + model_b)",
                    req_id,
                    risk_level,
                )
                _log.info("[Step3] 🔵 [%s] 开始交叉验证 AI 调用...", req_id)
                _t1 = time.time()
                result = generate_with_cross_validation(
                    requirement=req,
                    project_context=project_context,
                    ui_context=ui_ctx,
                    risk_level=risk_level,
                    run_id=run_id,
                    project_name=getattr(config, "project_name", "") or "",
                )
                _log.info(
                    "[Step3] 🔵 [%s] 交叉验证 AI 调用完成 (耗时%.1fs)",
                    req_id,
                    time.time() - _t1,
                )
                _cv = result.get("_cross_validation", {})
                if _cv:
                    _log.info(
                        "[Step3] 🔀 [%s] 交叉验证: common=%d, only_a=%d, confidence=%s",
                        req_id,
                        _cv.get("common_count", 0),
                        _cv.get("only_a_count", 0),
                        _cv.get("confidence", "?"),
                    )
            else:
                _log.info(
                    "[Step3] 🔵 [%s] 开始 AI 调用 (risk=%s, model=%s)...",
                    req_id,
                    risk_level,
                    mp.get("model", "?"),
                )
                _prev_intents = None
                try:
                    _ct = str(req.get("change_type", "") or "").lower()
                    if _ct in ("modified", "impacted"):
                        _prev_intents = _load_previous_intents(req)
                        if _prev_intents:
                            _log.info(
                                "[Step3] 📌 [%s] 优化6: 注入 %d 条旧版本用例 few-shot",
                                req_id,
                                len(_prev_intents),
                            )
                except Exception as _e:
                    _log.debug(
                        "[Step3] [%s] 读取旧版本用例失败(降级完全重生): %s",
                        req_id,
                        str(_e)[:80],
                    )
                    _prev_intents = None
                _t1 = time.time()
                result = model_test_cases(
                    requirement=req,
                    project_context=project_context,
                    ui_context=ui_ctx,
                    rag_context=req.get("_rag", rag_context),
                    trace_context=trace_context,
                    model_params=mp,
                    enable_validation=True,
                    previous_intents=_prev_intents,
                    # ★ 2026-09-01: 显式穿透 run_id/project，规避 worker 线程 thread-local 丢失
                    run_id=run_id,
                    project_name=getattr(config, "project_name", "") or "",
                )
                _log.info(
                    "[Step3] 🔵 [%s] AI 调用完成 (耗时%.1fs, success=%s)",
                    req_id,
                    time.time() - _t1,
                    result.get("success"),
                )
            # ★ T2 根因修复(2026-08-10): 每次 AI 生成尝试落一行 ai_generation_logs
            #   根因: ai_generation_logs 原仅在 hallucination_logger（失败分类路径）
            #         写入，成功路径从不落库 → 359 用例仅 2 行日志，证据链缺失。
            #   修复: 在 worker 拿到 result 后（成功/失败均）记录一次生成尝试。
            #   通用性: 覆盖 model_test_cases + generate_with_cross_validation 两条路径。
            with guard("_worker: from src.pipeline.utils.ai_generatio", log=_log):
                from src.pipeline.utils.ai_generation_logs_store import (
                    ai_generation_logs_store as _gen_log_store,
                )

                _gen_log_store.log_generation_attempt(
                    requirement=req,
                    result=result,
                    source="step3_ai_generation",
                )
            if result.get("success"):
                intents = result.get("data", [])
                if isinstance(intents, dict):
                    intents = [intents]
                # ★ §12.2规则3 通用修复(2026-07-26): AI 兜底路径也走 DOM 校验门
                #   根因(§10.9 5层):
                #     L1: TC-REQ-002/004 含幻觉按钮"保存"、幻觉输入框"辅材 输入框"未降级
                #     L2: case_source='generated'(AI兜底)，校验门只在 case_rules 注册表路径执行
                #     L3: _validate_actions_against_dom L106 检查 execution_mode，AI 用例无此字段
                #     L4: 校验门覆盖性不足，未覆盖 AI 兜底路径
                #     L5: §12.2规则3"校验前置必须在主流程"未覆盖所有生成路径
                #   修复: 在 AI 兜底用例 return 前，统一校验 DOM 元素存在性
                #   通用性(§12.4): 跨系统/跨行业/跨页面 — 纯 DOM 元素匹配，无业务依赖
                #   覆盖性: 覆盖所有 AI 兜底用例（含 model_test_cases + cross_validation）
                #   全局观: 修复后幻觉 actions 不再流入 Step4，Step5 报告不被污染
                #   职责边界: Step3 内部修复，不越界到 Step4
                #   自我反驳3次:
                #     1) 在 L594 all_intents.extend 后校验? → 需为每个 case 重算 target_page，开销大
                #     2) 在 _worker 内部校验? → 每个 worker 只查一次 DB，开销小，最优
                #     3) 在 model_test_cases 内部校验? → 改动 _model.py 影响面大，不优
                if intents:
                    with guard("_worker: from src.pipeline.step3._rule_extrac", log=_log, level=logging.WARNING):
                        from src.pipeline.step3._rule_extractor_validate import (
                            _validate_actions_against_dom,
                        )
                        from src.pipeline.step3._rule_extractor_dom import (
                            _load_dom_elements_for_page,
                        )
                        from src.pipeline.step3._rule_extractor_pages import (
                            _match_page_path,
                        )
                        from src.pipeline.step3._rule_extractor_api import (
                            _parse_rag_context,
                        )
                        from src.pipeline.step3._rule_extractor_downgrade import (
                            _infer_control_type_from_operation,
                        )

                        _rag_ctx = req.get("_rag", "") or ""
                        _rag_data = _parse_rag_context(_rag_ctx)
                        _matched_apis = (
                            _rag_data.get("apis", [])
                            if isinstance(_rag_data, dict)
                            else []
                        )
                        _target_page = _match_page_path(
                            req,
                            _matched_apis,
                            (
                                _rag_data.get("doms", [])
                                if isinstance(_rag_data, dict)
                                else []
                            ),
                        )
                        if not _target_page:
                            _target_page = "/"
                        _dom_elements = (
                            _load_dom_elements_for_page(_target_page)
                            if _target_page not in ("/", "/login")
                            else {}
                        )
                        _control_type = _infer_control_type_from_operation(
                            req.get("operation", "")
                        )
                        _orig_cnt = len(intents)
                        intents = _validate_actions_against_dom(
                            intents,
                            req,
                            _target_page,
                            {},
                            _control_type,
                            _dom_elements,
                            req_id,
                        )
                        if len(intents) < _orig_cnt:
                            _log.info(
                                "[Step3] ★ §12.2规则3 AI兜底校验门: [%s] %d→%d 条（降级 %d 条幻觉）",
                                req_id,
                                _orig_cnt,
                                len(intents),
                                _orig_cnt - len(intents),
                            )
                _log.info(
                    "[Step3] ✅ [%s] → %d 条意图 (risk=%s, qa=%s)",
                    req_id,
                    len(intents),
                    risk_level,
                    "skipped" if _skip_qa else "done",
                )
                return {
                    "req_id": req_id,
                    "intents": intents,
                    "error": None,
                    "risk_level": risk_level,
                }
            else:
                err = str(result.get("error", "未知错误"))[:120]
                _log.error("[Step3] ❌ [%s] AI 失败: %s", req_id, err)
                return {
                    "req_id": req_id,
                    "intents": [],
                    "error": err,
                    "risk_level": risk_level,
                }
        except Exception as e:
            _log.error("[Step3] ❌ [%s] 异常: %s", req_id, str(e)[:120])
            import traceback as _tb

            _log.error("[Step3] ❌ [%s] 堆栈: %s", req_id, _tb.format_exc())
            return {
                "req_id": req_id,
                "intents": [],
                "error": str(e)[:120],
                "risk_level": risk_level,
            }
        finally:
            try:
                from src.backend.state import _thread_run_id as _rid_cleanup

                _rid_cleanup.value = None
            except Exception as _e:
                _log.debug("[Step3] thread_run_id 清理失败: %s", _e, exc_info=True)

    # ★ 分批提交：避免一次性 submit 大量 future 导致内存耗尽（OOM）
    #   每批提交 _max_workers * 2 个，配合 as_completed 流式处理
    _batch_size = max(_max_workers * 2, 4)
    _log.info(
        "[Step3] 启动并行: max_workers=%d batch_size=%d total=%d (规则已覆盖 %d，AI 待处理 %d)",
        _max_workers,
        _batch_size,
        total_reqs,
        _rule_extracted_count,
        total_reqs_ai,
    )

    # ★ P0: 规则提取覆盖全部需求时，enriched_reqs 为空，while 循环自然跳过
    with ThreadPoolExecutor(max_workers=_max_workers) as executor:
        _pending = list(enriched_reqs)  # 待处理队列
        _active_futures = {}  # {future: req} 当前在飞的 future

        while _pending or _active_futures:
            # 补满 active 窗口（不超过 batch_size）
            while _pending and len(_active_futures) < _batch_size:
                req = _pending.pop(0)
                fut = executor.submit(_worker, req)
                _active_futures[fut] = req

            # 等待至少一个完成
            for future in as_completed(dict(_active_futures)):
                if stop_check and stop_check():
                    _log.warning("[Step3] 收到停止信号，终止等待")
                    executor.shutdown(wait=False)
                    return 0, True

                req = _active_futures.pop(future)
                try:
                    result = future.result()
                except Exception as e:
                    _log.error(
                        "[Step3] ❌ [%s] Future 异常: %s",
                        req.get("req_id", ""),
                        str(e)[:120],
                    )
                    result = {
                        "req_id": req.get("req_id", ""),
                        "intents": [],
                        "error": str(e)[:120],
                        "risk_level": "medium",
                    }

                # ★ 从 worker 结果动态收集 risk_level（供 _write_intents_to_store 使用）
                risk_map[result.get("req_id", "")] = result.get("risk_level", "medium")

                if result["intents"]:
                    all_intents.extend(result["intents"])
                if result["error"]:
                    failed_reqs.append(req)

                # ★ 流水线执行页面：标记该需求完成/失败
                _done_req_id = req.get("req_id", "")
                if run_id and _done_req_id:
                    try:
                        from src.backend.state import _update_req_progress

                        _done_status = "failed" if result.get("error") else "completed"
                        _update_req_progress(run_id, _done_req_id, _done_status)
                    except Exception as _e:
                        _log.debug(
                            "[Step3] progress 更新失败(done): %s", _e, exc_info=True
                        )

                break  # 处理完一个就回到外层 while 继续补满窗口

    # ── 失败兜底（仅 AI 失败的需求走兜底，规则提取成功的不会到这） ──
    if failed_reqs:
        _log.warning(
            "[Step3] %d/%d 条需求 AI 生成失败，使用规则兜底",
            len(failed_reqs),
            total_reqs_ai,
        )
        for req in failed_reqs:
            req_id = req.get("req_id", "")
            fallback = _build_rule_fallback_cases(req, "AI生成失败，使用规则兜底")
            all_intents.extend(fallback)
            _log.info("  兜底 [%s]: %d 条意图", req_id, len(fallback))

    # ── 写入 MySQL ──
    # ★ 07-07 方案 C：后处理增强（开关 COMBO_ASSERTION_ENHANCE_ENABLED，默认关闭）
    #   扫描 actions 自动追加缺失的高价值断言（搜索/表单/下载/删除场景）
    with guard("_parallel_model_all_reqs: from src.pipeline.steps.step3.case_g", log=_log):
        from src.pipeline.steps.step3.case_generator._combo_enhancer import (
            enhance_combo_assertions,
        )

        all_intents = enhance_combo_assertions(all_intents)

    # ★ 规则优先去重层（2026-09-16）：AI 产出中已被规则 fallback 覆盖的用例去重
    #   规则 fallback 产出的用例带 _generation_source="local_rule_fallback"
    #   同一 (req_id, type) 只保留规则版本（确定性更强、selector 更通用）
    _rule_keys = set()
    for _ic in all_intents:
        if _ic.get("_generation_source") == "local_rule_fallback":
            _rule_keys.add((_ic.get("req_id", ""), _ic.get("type", "")))
    if _rule_keys:
        _before = len(all_intents)
        all_intents = [
            _ic for _ic in all_intents
            if not (
                _ic.get("_generation_source") != "local_rule_fallback"
                and (_ic.get("req_id", ""), _ic.get("type", "")) in _rule_keys
            )
        ]
        _removed = _before - len(all_intents)
        if _removed:
            _log.info(
                "[Step3] ★ 规则优先去重: 移除 %d 条与规则 fallback 重叠的 AI 用例（保留规则版本）",
                _removed,
            )

    # ★ 页面级横切用例折叠：
    #   兼容/易用兜底模板断言的是**整页**度量，却被按每条需求各造一份
    #   （accept_sut_0923 实测 172 条全部指向同一页面 `/`，123 条假 FAIL）。
    #   标了 `_scope="page"` 的用例按 (页面, 类型, 模板) 折叠为单条。
    #   必须在 _merge_cases_by_page **之前**执行：折叠后页面级用例只有一条，
    #   不会再被页面合并逻辑按 (页面, 场景, 需求, 类型) 二次拆分。
    # ★ 诊断（2026-09-26）：确认页面级标记是否在到达折叠前存活
    #   实测 accept_sut_0923 只有 44 条带 _scope=page（预期 86×2=172），
    #   需要定位是"生成端未打标"还是"中段被剥离"。
    with guard("_parallel_model_all_reqs: _diag_scope = [c for c in all_intent", log=_log):
        _diag_scope = [c for c in all_intents if isinstance(c, dict) and c.get("_scope") == "page"]
        _diag_rule = [c for c in all_intents
                      if isinstance(c, dict) and c.get("_generation_source") == "local_rule_fallback"]
        _diag_nav = {}
        for _c in _diag_scope:
            _n = ""
            for _a in _c.get("actions", []) or []:
                if isinstance(_a, dict) and _a.get("type") == "navigate":
                    _n = str(_a.get("target_hint", ""))
                    break
            _diag_nav[_n] = _diag_nav.get(_n, 0) + 1
        _log.info(
            "[Step3][DIAG] 折叠前 total=%d rule_fallback=%d _scope=page=%d nav=%s",
            len(all_intents), len(_diag_rule), len(_diag_scope), _diag_nav,
        )

    all_intents = _collapse_page_scoped_cases(all_intents)

    # ★ 页面合并：同一 target_page 的多个用例合并为复合用例（消除重复 + 补全 UI 流程）
    #   必须在写入 MySQL 之前执行，确保合并后的用例入库
    all_intents = _merge_cases_by_page(all_intents)

    # ★ Fix (2026-07-12): 补齐 AI 生成动作缺失的必填参数（如 upload_file 的 file_path）
    all_intents = _ensure_action_params(all_intents)

    # ★ 通用修复(2026-07-18): URL 导航地图校验 — 将 AI 幻觉 URL 替换为页面名称或 "/"
    with guard("_parallel_model_all_reqs: _project_name = getattr(config, 'pro", log=_log, level=logging.WARNING):
        _project_name = getattr(config, "project_name", "") or ""
        _log.warning(
            "[_validate_navigate_urls] 开始URL校验, project=%s, intents=%d",
            _project_name,
            len(all_intents),
        )
        all_intents = _validate_navigate_urls(all_intents, project=_project_name)

    # ★ 通用修复(2026-07-18): 元素名校验 — 将 AI 生成的英文元素名替换为需求文档中的中文原文
    with guard("_parallel_model_all_reqs: _project_name = getattr(config, 'pro", log=_log, level=logging.WARNING):
        _project_name = getattr(config, "project_name", "") or ""
        _version_id = getattr(config, "version_id", 1) or 1
        all_intents = _validate_element_names(
            all_intents, project=_project_name, version_id=_version_id
        )

    # ★ 控件存在性预验证：基于 Step1 DOM 快照修正错误 tab / 标记不可执行用例
    with guard("_parallel_model_all_reqs: from src.core.versioning import curr", log=_log, level=logging.WARNING):
        from src.core.versioning import current_version_label

        project = getattr(config, "project_name", "") or ""
        version_label = current_version_label(project) if project else ""
        all_intents = validate_control_existence(
            all_intents, project=project, version_label=version_label
        )

    # ★ 流水线强化专项 T2(2026-08-24): 方法论白名单后置校验（产出即校验）
    #    在写入 MySQL 前对全部用例执行 scenario_type / test_method 白名单 + 配额校验：
    #       - 低危（别名/大小写/提示词扩展编码）→ 就地自动修正 + case["method_fix_note"]
    #       - 高危（白名单外且不可映射）→ case["method_valid"]=0 + blocked
    #    校验结果并入 _methodology_validation 供 _intent_quality 摘要展示。
    #    仅就地打标/修正，不改变执行语义、不阻断写入（阻断与否由调用方读 blocked 决定）。

    # ★ 根因修复(2026-09-21): 写库前统一方法论解码兜底。生成器多路径（规则/AI/重试/合并）
    #   中，不少路径只产出 FD 或 dict 形状的 test_method，方法论"用了模板却落 FD"。
    #   此处对每个最终 intent 用 canonical_method_code 归纳为权威编码（显式码保留、
    #   FD/未决定按 SDET 直觉升级、非法值兜底 FD），确保写库元数据真实反映所用方法论。
    with guard("_parallel_model_all_reqs: from src.pipeline.steps.step3.case_g", log=_log, level=logging.WARNING):
        from src.pipeline.steps.step3.case_generator._test_method_rules import (
            canonical_method_code,
        )

        _canonical_moved = 0
        for _ic in all_intents:
            if not isinstance(_ic, dict):
                continue
            _prev = str(_ic.get("test_method") or "").strip()
            # ★ case_signal：用例自身语义（标题+类型）。让「类型已是 性能/兼容/安全-XSS…
            #   但 explore_task 无关键词」的用例按自身语义确定性落码，不再坍缩为 FD。
            _case_signal = " ".join(
                str(_ic.get(k) or "") for k in ("title", "type", "case_type")
            )
            _new = canonical_method_code(
                _ic.get("test_method"),
                str(_ic.get("control_type") or "").strip(),
                _ic.get("_explore_task"),
                _case_signal,
            )
            if _prev != _new:
                _ic["test_method"] = _new
                _canonical_moved += 1
        if _canonical_moved:
            _log.info(
                "[Step3] ★ 方法论解码兜底: %d 条用例 test_method 修正为权威码",
                _canonical_moved,
            )

    # ★ 根因修复(2026-09-24): 写库前统一补齐标准展示字段。规则兜底(local_rule_fallback)
    #   及以其为基合并的 page_merged 用例仅写 actions/checks，漏写 test_steps/
    #   precondition/expected_result，导致审核页详情"无操作步骤/无前置条件"。此处对每个
    #   最终 intent 缺失的三字段从 action/check 派生补全，不覆盖已存在的更丰富值。
    with guard("_parallel_model_all_reqs: import json as _fill_json", log=_log, level=logging.WARNING):
        import json as _fill_json

        _fs = _fp = _fe = 0
        for _fc in all_intents:
            if not isinstance(_fc, dict):
                continue
            if not str(_fc.get("test_steps") or "").strip():
                _fa = _fc.get("actions") or []
                _fc["test_steps"] = _fill_json.dumps(_fa, ensure_ascii=False, indent=2)
                if _fa:
                    _fs += 1
            if not str(_fc.get("precondition") or "").strip():
                _fc["precondition"] = "1.系统已登录 2.用户已进入目标功能页面"
                _fp += 1
            if not str(_fc.get("expected_result") or "").strip():
                _fexp = ""
                for _ch in _fc.get("checks") or []:
                    _fexp = str(_ch.get("expect") or _ch.get("expected") or "").strip()
                    if _fexp:
                        break
                _fc["expected_result"] = (
                    _fexp or _fc.get("description") or _fc.get("title") or ""
                )
                _fe += 1
        if _fs or _fp or _fe:
            _log.info(
                "[Step3] ★ 标准展示字段补齐: test_steps=%d precondition=%d expected_result=%d",
                _fs,
                _fp,
                _fe,
            )

    _methodology_validation: dict = {}
    with guard("_parallel_model_all_reqs: from src.pipeline.step3._methodology", log=_log, level=logging.WARNING):
        from src.pipeline.step3._methodology_validator import MethodologyValidator

        _methodology_validation = MethodologyValidator().validate(all_intents)
        if _methodology_validation.get("auto_fixed"):
            _log.info(
                "[Step3] T2 方法论自动修正 %d 条用例",
                _methodology_validation["auto_fixed"],
            )
        if _methodology_validation.get("blocked"):
            _log.warning(
                "[Step3] T2 方法论高危阻断 %d 条用例（case_id=%s），请人工复核标签",
                _methodology_validation["blocked_count"],
                ",".join(
                    _methodology_validation.get("dispositions", {}).get("blocked", [])[:10]
                ),
            )

    # ★ T2 硬门禁落地（STEP3_METHOD_BLOCK=block）：高危违法方法论的用例不流入可执行集
    #    默认 tag（软打标）保持历史行为不变；开启 block 后把 blocked 用例从
    #    all_intents 剔除，避免白名单外 scenario_type/test_method 进入 Step4 执行。
    _method_block_mode = str(getattr(config, "step3_method_block", "tag") or "tag")
    _methodology_validation = dict(_methodology_validation or {})
    if _method_block_mode == "block":
        with guard("_parallel_model_all_reqs: from src.pipeline.step3._methodology", log=_log, level=logging.WARNING):
            from src.pipeline.step3._methodology_validator import MethodologyValidator

            all_intents, _dropped_ids = MethodologyValidator.apply_block_filter(
                all_intents, _methodology_validation, _method_block_mode
            )
            _methodology_validation["blocked_dropped"] = len(_dropped_ids)
            _log.warning(
                "[Step3] T2 硬门禁: 剔除 %d 条非法方法论用例（case_id=%s）",
                len(_dropped_ids),
                ",".join(sorted(_dropped_ids)[:10]),
            )

    _log.info(
        "[Step3] DEBUG: all_intents 共 %d 条，第一个 case_id=%s，flat check=%s",
        len(all_intents),
        all_intents[0].get("case_id", "N/A") if all_intents else "N/A",
        "empty" if not all_intents else "has_data",
    )
    total = _write_intents_to_store(
        all_intents, ungen_reqs, risk_map=risk_map, stop_check=stop_check
    )
    _log.info(
        "[Step3] 并行建模完成: %d/%d 条需求, 共 %d 条意图",
        len({i.get("req_id") for i in all_intents}),
        total_reqs,
        total,
    )

    # ★ S3-012: 写入 RAG 使用日志（用例写入成功后，关联 case_id/check_id）
    if rag_usage_map:
        _rag_log_count = _write_rag_usage_logs(all_intents, rag_usage_map)
        if _rag_log_count > 0:
            _log.info("[S3-012] RAG 使用日志已写入 %d 条", _rag_log_count)

    # ★ v25 根因Z4 修复(2026-07-21): 禁用 cross_requirement 跨需求用例生成
    #   原 bug: _generate_cross_requirement_intents 生成的用例 checks 是自然语言描述
    #          （如"辅材批量导入记录列表显示接装纸导入的数据"），
    #          不是 Step4 可执行的 selector 断言格式，导致 Step4 必失败。
    #   根因链路(5层):
    #     L1 表层: 20条 cross_requirement 用例 Step4 执行失败
    #     L2 中层: checks 格式是 {"check":"自然语言","expect":"自然语言"}，非可执行 selector
    #     L3 根因层: cross_requirement 用例由 AI 生成，不走规则管道，checks 格式不统一
    #     L4 设计层: 违反"一个需求→一个完整测试用例"（AGENTS.md §4.1）
    #     L5 最深根因: 跨需求用例质量不可控，且与规则用例混在一起执行
    #   通用性: 禁用跨需求用例适用于所有系统（规则用例已覆盖原子需求）
    #   覆盖性: 禁用所有 cross_requirement 用例生成路径
    #   全局观: 禁用后 Step3 只生成规则用例，Step4 只执行规则用例，流水线更稳定
    #   自我反驳:
    #     1) 禁用会漏测吗？→ 当前 cross_requirement 用例质量不可控（执行必失败），
    #        不如先禁用保证主流程稳定，后续优化质量再启用
    #     2) 有更通用方案吗？→ 让 cross_requirement 也走规则管道是大重构，
    #        当前最优先是保证 Step4 能执行通过
    #     3) 有更根本方案吗？→ 根本方案是统一所有用例 checks 格式，
    #        但需重构 _generate_cross_requirement_intents，当前禁用最安全
    #   合规: AGENTS.md §4.1 一个需求→一个完整测试用例 + §10.8 禁止治标不治本
    _log.info(
        "[Step3] ★ v25 根因Z4: 跳过 cross_requirement 跨需求用例生成 "
        "（checks 是自然语言非可执行 selector，违反一需求一用例原则）"
    )

    return total, False


def _model_full_intents(
    ungen_reqs,
    project_context="",
    ui_ctx="",
    data_context="",
    rag_enabled=False,
    trace_links=None,
    stop_check=None,
    progress_cb=None,
    run_id=None,
):
    """建模全量测试用例。每个需求独立 AI 调用，ThreadPoolExecutor 并行执行。"""
    return _parallel_model_all_reqs(
        ungen_reqs,
        project_context=project_context,
        ui_ctx=ui_ctx,
        data_context=data_context,
        rag_enabled=rag_enabled,
        trace_links=trace_links,
        stop_check=stop_check,
        progress_cb=progress_cb,
        run_id=run_id,
    )
