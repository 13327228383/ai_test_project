# -*- coding: utf-8 -*-
"""
_model.py — 单条测试用例生成（v3.0 精简版）
==========================================
model_test_cases: 为单条需求生成完整测试用例（冒烟模式等少量场景用）。
批量场景使用 _batch.generate_all_test_cases。
"""

import sys
from src.core.guarded import guard
import re
from pathlib import Path
from typing import Any
import json
import logging

_log = logging.getLogger(__name__)

_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from src.ai.ai_client import ai_brain as _ai_brain
from src.ai.ai_client_mixins._base import _get_session_ctx
from src.core.config_loader import config
from src.core.prompt_templates import prompt_templates


prompt_templates.register("case_model_system", "")

from ._helpers import _clip_context, _CTX_LIMITS
from ._prompts import (
    _CASE_OUTPUT_FORMAT,
    _CASE_RULES,
    _TEST_METHOD_GUIDE,
    _COMBO_FEW_SHOT,
)
from ._contexts import _build_common_contexts
from ._explore_task_builder import (
    extract_module_from_operation,
    build_explore_task,
    enhance_requirement_module,
    wrap_legacy_actions_to_explore_task,
    inject_explore_task_to_cases,
    _build_default_test_method,
)
from ._test_method_rules import (
    apply_method_rules,
    canonical_method_code,
    merge_verifications,
    generate_field_text_contains_checks,
    is_write_operation,
    generate_data_flow_checks,
)
from ._action_templates import apply_action_templates


def _infer_required_tracks_from_checks(checks: Any) -> list[str]:
    """★ 根因修复(2026-08-25): 从 checks 的 track 类型确定性推断 required_tracks。

    根因: AI 生成的用例缺失 v9 规范要求的 required_tracks 核心字段，导致
          validate_intents 的 INTENT_CORE_FIELDS 校验失败。
    通用性: 不依赖 AI 主动输出该字段（prompt 未要求、模型易遗漏），而是以 checks
            已声明的事实（track=ui/api/db）为唯一事实源做投影。checks 有什么轨，
            required_tracks 就是什么轨，绝不空头声明。
    """
    _tracks: list[str] = []
    _seen: set[str] = set()
    if isinstance(checks, list):
        for ch in checks:
            if not isinstance(ch, dict):
                continue
            _t = str(ch.get("track") or "").strip().lower()
            if _t in ("ui", "api", "db") and _t not in _seen:
                _seen.add(_t)
                _tracks.append(_t)
    return _tracks or ["ui"]


def _inject_required_tracks(cases: list) -> None:
    """★ 为缺 required_tracks 的用例注入确定性推导值（从 checks 投影）。

    仅在缺失/为空时注入，保留已正确声明的值（rule 提取路径不经过此处）。
    """
    for _c in cases:
        if not isinstance(_c, dict):
            continue
        _rt = _c.get("required_tracks")
        if _rt:
            continue
        _c["required_tracks"] = _infer_required_tracks_from_checks(_c.get("checks"))
        _log.info(
            "[_model] 注入 required_tracks: case=%s → %s",
            str(_c.get("case_id", ""))[:20],
            _c["required_tracks"],
        )


def _inject_method_and_action_templates(cases: list) -> None:
    """★ T01/T02(2026-08-06): 逐 case 叠加方法论规则模板 + 动作模板。

    主理人决策 #1/#2：
    - 规则模板始终叠加：AI 生成 verifications 保留在前，规则模板追加在后，
      dedup by (action,target)，AI 优先。
    - 动作模板同样叠加（8 模板按 control_type/operation 命中），与规则模板
      共用 merge_verifications 去重。
    通用性：跨系统/行业/页面 — 模板占位符由 case/explore_task 上下文解析。

    ★ 根因修复(2026-08-09, text_contains false positive):
      I01 字段展示类型测试的 checks 使用精确字段 verbose_name（如"科目名称"）
      而非模型 verbose_name（如"会计科目"）。从 _field_verbose_names 读取字段名，
      追加 text_contains 检查，每个字段一个。
    """
    for _c in cases:
        if not isinstance(_c, dict):
            continue
        _et = _c.get("_explore_task")
        if not isinstance(_et, dict):
            continue
        _tm = _et.get("test_method")
        if not isinstance(_tm, dict):
            _tm = {}
            _et["test_method"] = _tm
        if not isinstance(_tm.get("verifications"), list):
            _tm["verifications"] = []
        _control_type = str(_c.get("control_type") or "").strip()
        _op = str(_et.get("operation") or "").strip()
        _existing = _tm["verifications"]
        with guard("_inject_method_and_action_templates: _routed = canonical_method_code( _c.", log=_log, level=logging.WARNING):
            # ★ 根因修复: 唯一解码并写回。此前路由只在选模板时生效，
            #   存的却是 FD，导致 "模板用了 EP/BV/AU…，元数据却是 FD" 的假象。
            #   现在用 canonical_method_code 得出权威码，统一写回 case 顶层字符串
            #   与 _explore_task.test_method.method，模板与落库口径一致。
            _routed = canonical_method_code(
                _c.get("test_method") or _tm.get("method"), _control_type, _et,
                str(_c.get("title") or ""),
            )
            _c["test_method"] = _routed
            _tm["method"] = _routed
            # ★ T01: 传 test_method dict（含 inputs）供占位符解析；编码取路由结果
            _tm_for_rules = dict(_tm)
            _tm_for_rules["method"] = _routed
            _rule_steps = apply_method_rules(_tm_for_rules, _control_type, _et)
            _action_steps = apply_action_templates(_control_type, _op, _tm)
            _tm["verifications"] = merge_verifications(
                _existing, _rule_steps + _action_steps
            )
            _log.info(
                "[_model] 叠加模板: case=%s code=%s control_type=%s verifications %d→%d",
                str(_c.get("case_id", ""))[:20],
                str(_routed)[:8],
                _control_type[:12],
                len(_existing),
                len(_tm["verifications"]),
            )

        # ── ★ 数据流验证(2026-08-09): 写操作自动生成三阶段数据流 check ──
        #   检测条件: 写操作（创建/编辑/删除）+ 有 explore_task
        #   根因: DB轨只在Agent执行路径中标记为NOT_AVAILABLE，只检查"记录存在"或"字段值"，
        #         不追踪数据从"创建→存储→展示"的完整链路。
        #   修复: 确定性规则层自动生成三阶段数据流 check，覆盖 UI→DB→UI 完整链路。
        #   通用性: 跨系统/行业/页面 — 表名/创建值从上下文解析，无业务硬编码。
        #   ★ 注意: 必须放在 I01 字段检查之前，避免被 I01 的 continue 跳过。
        with guard("_inject_method_and_action_templates: _op = str(_et.get('operation') or ''", log=_log, level=logging.WARNING):
            _op = str(_et.get("operation") or "").strip()
            if not is_write_operation(_op):
                pass  # 非写操作，跳过数据流验证
            else:
                _data_flow_checks = generate_data_flow_checks(
                    operation=_op,
                    explore_task=_et,
                    test_method=_tm_for_rules,
                )
                if _data_flow_checks:
                    # 追加到 case.checks（存在则追加，不存在则新建）
                    if not isinstance(_c.get("checks"), list):
                        _c["checks"] = []
                    _existing_checks = _c["checks"]
                    # 按 data_flow_stage 去重（已有同阶段 check 则跳过）
                    _existing_stages: set[str] = set()
                    for _ec in _existing_checks:
                        if isinstance(_ec, dict):
                            _stage = str(_ec.get("data_flow_stage", "") or "").strip()
                            if _stage:
                                _existing_stages.add(_stage)
                    _df_added = 0
                    for _dfc in _data_flow_checks:
                        _stage = str(_dfc.get("data_flow_stage", "") or "").strip()
                        if _stage and _stage not in _existing_stages:
                            _existing_checks.append(_dfc)
                            _existing_stages.add(_stage)
                            _df_added += 1
                    if _df_added > 0:
                        _log.info(
                            "[_model] 数据流验证: case=%s operation=%s 追加 %d 个数据流 check (阶段: %s)",
                            str(_c.get("case_id", ""))[:20],
                            _op[:40],
                            _df_added,
                            ",".join(
                                _dfc.get("data_flow_stage", "?")
                                for _dfc in _data_flow_checks
                                if _dfc.get("data_flow_stage")
                            ),
                        )

        # ── ★ 根因修复(2026-08-09): I01 字段展示类型 — 注入精确字段名 text_contains 检查 ──
        #   检测条件: 字段名列表非空 + (case_id 含 I01 或 expected_feedback 含"字段/展示/显示")
        _field_names = _c.get("_field_verbose_names") or _et.get("_field_verbose_names") or []
        if not isinstance(_field_names, list) or not _field_names:
            continue
        _case_id = str(_c.get("case_id", "") or "").upper()
        _eb = str(_et.get("expected_feedback") or _c.get("expected_feedback") or "").strip()
        _is_field_display = (
            "I01" in _case_id
            or any(kw in _eb for kw in ["字段", "展示", "显示", "展现", "呈现"])
        )
        if not _is_field_display:
            continue
        with guard("_inject_method_and_action_templates: _field_checks = generate_field_text_", log=_log, level=logging.WARNING):
            _field_checks = generate_field_text_contains_checks(_field_names)
            if not _field_checks:
                continue
            # 追加到 case.checks（存在则追加，不存在则新建）
            if not isinstance(_c.get("checks"), list):
                _c["checks"] = []
            _existing_checks = _c["checks"]
            _seen_check_expects: set[str] = set()
            for _ec in _existing_checks:
                if isinstance(_ec, dict):
                    _exp = str(_ec.get("expect", "") or "").strip()
                    if _exp:
                        _seen_check_expects.add(_exp)
            _added = 0
            for _fc in _field_checks:
                _exp = str(_fc.get("expect", "") or "").strip()
                if _exp and _exp not in _seen_check_expects:
                    _existing_checks.append(_fc)
                    _seen_check_expects.add(_exp)
                    _added += 1
            if _added > 0:
                _log.info(
                    "[_model] I01字段展示检查: case=%s 追加 %d 个精确字段名 text_contains (字段: %s)",
                    str(_c.get("case_id", ""))[:20],
                    _added,
                    ",".join(_field_names[:5]),
                )


def _extract_module_from_operation(operation: str) -> str:
    """★ 通用修复(2026-07-28): 从 operation 文本中提取模块名。

    根因: Step2 的 module 字段来自文档 section 标题（如"表格块：第1行"），
          对导航无用。真正的模块名（如"烟叶画像数据"）在 operation 文本中。

    通用性: 跨系统/行业/文档格式 — 正则匹配"所属模块：xxx"模式。
    """
    if not operation:
        return ""
    patterns = [
        r"所属模块[：:]\s*([^\s，,。.]{2,20})",
        r"所属功能[：:]\s*([^\s，,。.]{2,20})",
        r"模块[：:]\s*([^\s，,。.]{2,20})",
    ]
    for pat in patterns:
        m = re.search(pat, operation)
        if m:
            _mod = m.group(1).strip()
            _log.info("[_model] 从operation提取模块名: %r → %r", operation[:60], _mod)
            return _mod
    return ""


def model_test_cases(
    requirement: dict[str, str],
    project_context: str = "",
    ui_context: str = "",
    rag_context: str = "",
    trace_context: str = "",
    rules_context: str = "",
    model_params: dict = None,
    enable_validation: bool = True,
    smoke_mode: bool = False,
    previous_intents: list = None,
    run_id: str = "",
    project_name: str = "",
) -> dict[str, Any]:
    """为单条需求生成完整测试用例（explore_task + checks）。

    ★ 优化6: previous_intents 为旧版本用例列表（modified 需求 few-shot 注入）。
    ★ 2026-09-01: 新增 run_id / project_name 参数。批量场景（THreadPoolExecutor）
      的 worker 线程里 thread-local 的 _thread_run_id 可能为空（与 RAG 构建同类的
      项目绑定丢失），导致 Step3 交叉校验健康指标从不落库（cross_check 恒 0）。
      显式穿透 run_id 后，record_cross_checks 稳定记录，不依赖线程上下文。
    """
    # ★ 通用修复(2026-07-28): 增强 module 字段 — 从 operation 提取真正的模块名
    _op_text = requirement.get("operation", "")
    enhance_requirement_module(requirement)

    (
        ui_section,
        trace_section,
        data_dict_context,
        forbidden_context,
        few_shot_context,
        action_library_context,
    ) = _build_common_contexts(
        requirement,
        ui_context,
        rag_context,
        trace_context,
        rules_context,
        "",
        previous_intents=previous_intents,
    )

    feedback_context = ""
    try:
        from src.extensions.quality.hallucination_logger import (
            generate_failure_feedback_prompt,
        )

        req_id = requirement.get("req_id", "")
        module_hint = requirement.get("module", requirement.get("module_name", ""))
        feedback_context = generate_failure_feedback_prompt(
            req_id=req_id,
            module=module_hint,
            project_name=getattr(config, "project_name", None),
        )
        feedback_context = _clip_context(
            "历史失败反馈", feedback_context, _CTX_LIMITS["feedback"]
        )
    except Exception as _e:
        _log.debug("[_model] model_test_cases 异常: %s", _e, exc_info=True)

    # ── 构建 system_prompt ──
    # ★ 07-07 组合断言方案：
    #   A（场景规则）：始终注入 _CASE_RULES 第13条（成本极低）
    #   B（Few-Shot 示例）：受 COMBO_FEW_SHOT_ENABLED 开关控制（影响 token 成本和 AI 效率）
    #   C（后处理算法）：在 _modeling_engine 中始终启用（无 AI 调用）
    try:
        from src.core.pipeline_config import pipeline_config as _cfg

        _combo_few_shot_enabled = _cfg.combo_few_shot_enabled
    except Exception:
        _combo_few_shot_enabled = False

    if _combo_few_shot_enabled:
        _combo_few_shot_ctx = _COMBO_FEW_SHOT
    else:
        _combo_few_shot_ctx = ""

    # ★ Fix15b : 构建自我修复失败上下文（在 render 之前，作为 kwargs 注入）
    _failure_hint = ""
    try:
        from src.pipeline.steps.step4.self_repair import get_failure_context

        failure_ctx = get_failure_context()
        if failure_ctx and failure_ctx.get("case_id"):
            _failure_hint = (
                "\n\n## ⚠️ 自我修复：上一版用例执行失败，请避免同样问题\n"
                "失败用例 case_id: %s\n"
                "失败原因: %s\n"
                "失败分类: %s\n"
                "★ 请根据以上失败原因调整用例设计：\n"
                '  - 如果是"结构化动作执行失败"：target_hint 要用具体控件描述（如"用户名输入框"而非"主输入框"）\n'
                '  - 如果是"text_contains:✗"：check 要有明确的 assertion 类型（element_exists/url_matches）\n'
                '  - 如果是"DB 校验失败"：确保 SQL 语句正确，表名和字段名从 RAG 上下文获取\n'
                "  - target_hint 必须是自然语言描述，禁止使用 CSS ID 选择器（如 #ai-1）\n"
            ) % (
                failure_ctx.get("case_id", ""),
                failure_ctx.get("failure_reason", "")[:300],
                failure_ctx.get("failure_category", ""),
            )
            _log.info(
                "[_model] 注入自我修复失败上下文: case_id=%s",
                failure_ctx.get("case_id"),
            )
    except ImportError as e:
        _log.debug("self_repair模块导入失败: %s", e)
    except Exception as _e:
        _log.debug("[_model] 读取失败上下文异常: %s", _e)

    # ★ 2026-07-14: system_prompt 骨架存 DB（case_model_system），运行时注入上下文
    #   骨架含 {{占位符}}，由 prompt_templates.render() 替换
    system_prompt = prompt_templates.render(
        "case_model_system",
        test_method_guide=_TEST_METHOD_GUIDE,
        case_output_format=_CASE_OUTPUT_FORMAT,
        case_rules=_CASE_RULES,
        combo_few_shot=_combo_few_shot_ctx,
        action_library=action_library_context,
        ui_section=ui_section,
        trace_section=trace_section,
        data_dict_context=data_dict_context,
        forbidden_context=forbidden_context,
        few_shot_context=few_shot_context,
        feedback_context=feedback_context,
        failure_hint=_failure_hint,
    )

    # ── 构建 user_message（v3.1: 注入原文上下文+条件+功能标签，07-05g）──
    req_id = requirement.get("req_id", "REQ-???")
    description = requirement.get("description", "")
    op = (requirement.get("operation") or "").strip()
    eb = (
        requirement.get("expected_feedback")
        or requirement.get("expected_behavior")
        or ""
    ).strip()
    cond = (requirement.get("condition_expr") or "").strip()
    source_quote = (requirement.get("source_quote") or "").strip()
    chunk_text = (requirement.get("_chunk_text") or "").strip()
    # ★ 单一事实源(07-06): _chunk_text 已从 payload_json 移除，优先从 enrichment 获取；
    #   若缺失（单条调用路径），按 slice_id 从 requirement_slices 补查一次
    if not chunk_text:
        slice_id = requirement.get("slice_id") or requirement.get("_chunk_id")
        if slice_id:
            with guard("model_test_cases: from src.pipeline.utils.requirement_", log=_log):
                from src.pipeline.utils.requirement_slice_store import (
                    requirement_slice_store,
                )

                slice_data = requirement_slice_store.get_slice(slice_id)
                if slice_data:
                    chunk_text = (slice_data.get("text") or "").strip()
    feature_label = (requirement.get("feature_label") or "").strip()

    if op and eb:
        # ★ 增强(07-05g): 注入条件 + 功能标签 + 原文上下文
        #   - 条件(condition_expr): 让 AI 知道前置约束，能生成更精准的测试数据
        #   - 功能标签(feature_label): 让 AI 知道所属功能，便于按功能组织用例
        #   - 原文上下文(来自 requirement_slices.text): 让 AI 在原文语境里做测试理论发散（边界值/等价类/场景法）
        # ★ 增强(07-05h): 注入控件类型 + 测试方法论智能选择
        #   - 按操作识别控件类型，告诉AI适用哪些测试方法论
        #   - 避免强行对按钮做SQL注入等不合理组合
        from src.pipeline.steps.step3.case_generator._control_type import (
            build_control_method_hint,
        )

        control_method_hint = build_control_method_hint(op, eb)

        user_req_parts = [f"需求 {req_id}："]
        if feature_label:
            user_req_parts.append(f"  所属功能: {feature_label}")
        user_req_parts.append(f"  操作: {op}")
        if cond:
            user_req_parts.append(f"  前置条件: {cond}")
        user_req_parts.append(f"  预期反馈: {eb}")
        if source_quote:
            user_req_parts.append(f"  原文锚定: {source_quote}")
        if chunk_text:
            user_req_parts.append(f"  所属切片原文(≤600字):")
            user_req_parts.append(chunk_text)
        user_req_parts.append(f"\n  {control_method_hint}")
        user_req = "\n".join(user_req_parts)
    elif description:
        user_req = f"需求 {req_id}: {description}"
        if chunk_text:
            user_req += f"\n  所属切片原文(≤600字):\n{chunk_text}"
    else:
        user_req = f"需求 {req_id}"
        if chunk_text:
            user_req += f"\n  所属切片原文(≤600字):\n{chunk_text}"

    schema_context = ""
    # ★ 2026-07-12: 纯 UI 操作（点击/输入/选择）不需要 DB Schema，跳过 1400 字符噪音
    _op_type_ui = False
    if isinstance(requirement, dict):
        _op = (requirement.get("operation", "") or "").strip()
        if _op:
            from src.pipeline.step3._modeling_engine import (
                _UI_OPERATIONS,
                _DATA_OPERATIONS,
            )

            _op_lower = _op.lower()
            _op_type_ui = any(kw in _op_lower for kw in _UI_OPERATIONS)
            _op_type_data = any(kw in _op_lower for kw in _DATA_OPERATIONS)
            _op_type_ui = _op_type_ui and not _op_type_data

    if not _op_type_ui and config.system_knowledge_inject_prompt:
        ctx = _get_session_ctx()
        if ctx:
            try:
                schema_context = ctx.for_step3()
                schema_context = _clip_context(
                    "DB Schema", schema_context, _CTX_LIMITS["schema"]
                )
            except Exception as _e:
                _log.debug("[_model] 非关键异常: %s", _e, exc_info=True)

    project_context_clipped = _clip_context(
        "项目上下文", project_context, _CTX_LIMITS["project"]
    )
    # ★ 通用修复(2026-07-18): 强制AI使用需求文档原文，禁止翻译
    _no_translate_rule = (
        "\n★ 铁律：target_hint 必须使用需求文档中的中文原文，严禁翻译成英文。\n"
        '从操作的描述中提取关键术语：如"编辑字段名A"→"字段名A"、"字段名B"→"字段名B"、"字段名C"→"字段名C"。\n'
        "禁止行为：将中文术语翻译成英文（如 fieldnameA、fieldnameB、fieldnameC 等）。\n"
        "这些英文名在中文系统中不存在，会导致Step4执行失败。\n"
    )
    user_message = (
        "%s\n%s\n%s\n%s"
        "请为以上需求生成完整测试用例（explore_task + checks）。\n"
        '★★★ 输出格式强制提醒：必须输出单个 JSON 对象 {"cases": [完整用例, ...]}。\n'
        "每个用例对象必须包含：case_id, req_id, title, scenario_type, test_method, control_type, risk_level, type, precondition, test_steps, expected_result, explore_task, checks。\n"
        "禁止输出旧 actions 数组；禁止输出 markdown 代码块；禁止省略外层对象。"
    ) % (user_req, project_context_clipped, schema_context, _no_translate_rule)

    # ── 模型参数 + token 计算 ──
    mp = model_params or {}
    model_label = mp.get("label", "旗舰(默认)")
    model_name = mp.get("model", config.ai_model)

    from src.ai.token_estimator import compute_max_tokens

    final_max_tokens, tok_info = compute_max_tokens(
        system_prompt, user_message, model=model_name
    )
    if tok_info.get("input_tokens", 0) > 1000:
        flag = "⚠" if tok_info.get("input_near_limit") else ""
        _log.info(
            "    📐 输入≈%d → 输出%d %s(窗口%d 已用%d%%)",
            tok_info["input_tokens"],
            final_max_tokens,
            flag,
            tok_info.get("context_limit", "?"),
            int(
                tok_info["input_tokens"]
                / max(tok_info.get("context_limit", 1), 1)
                * 100
            ),
        )

    # ── 调用 AI ──
    # ★ 修复：将 model_params["model"] 传给 cloud_model，使交叉验证的 model_b 真正生效。
    #   之前 model_name 仅用于 token 估算，实际调用都用 config.ai_model，导致
    #   cross_validation.py 的双模型交叉验证退化为单模型双温度生成。
    # ★ 优化2: 试点 Structured Output（json_object 模式，云模型不支持时安全降级）
    # ★ F2 全局修复 [未复核-需 GLM/DeepSeek 把关]（2026-08-29）：
    #   仅加 seed=ai_stability_seed（temp 保持 mp.get，不破坏双模型交叉验证多样性）。
    from src.core.pipeline_config import _read_config_value as _cfg_seed

    result = _ai_brain._call_api(
        system_prompt=system_prompt,
        user_message=user_message,
        temperature=mp.get("temperature", 0.0),
        max_tokens=final_max_tokens,
        caller_name="测试用例生成(%s)" % model_label,
        task_type="case_modeling",
                     force_cloud=False,
        cloud_model=model_name,
        seed=(None if (_seed_v := _cfg_seed("ai_stability_seed", 42)) is None else int(_seed_v)),
        # response_format={"type": "json_object"},
    )

    if not result["success"]:
        return result

    parsed = _ai_brain._parse_json_response(result["data"].strip())
    parsed["_raw_response"] = result["data"][:4000]  # 诊断：保留原始响应
    if not parsed.get("success"):
        return parsed

    # ── 统一提取 cases 列表（兼容数组/对象两种 LLM 输出）──
    _raw_data = parsed["data"]
    if isinstance(_raw_data, dict) and "cases" in _raw_data:
        cases = _raw_data["cases"]
    elif isinstance(_raw_data, list):
        cases = _raw_data
    else:
        cases = [_raw_data] if _raw_data else []

    # ── 通用修复(2026-07-28): AI 可能直接输出旧 actions 数组，后处理包装成 explore_task ──
    def _is_action_item(item):
        return (
            isinstance(item, dict)
            and "type" in item
            and "target_hint" in item
            and "case_id" not in item
            and "explore_task" not in item
            and "checks" not in item
        )

    if (
        isinstance(cases, list)
        and len(cases) > 0
        and all(_is_action_item(c) for c in cases)
    ):
        _log.warning("    ⚠️ AI 返回旧 actions 数组而非 explore_task 格式，执行通用包装")
        cases = [wrap_legacy_actions_to_explore_task(cases, requirement)]

    parsed["data"] = cases

    # ★ P1-0(2026-07-29): 为每个用例注入 _explore_task（复制 explore_task），
    #   使 Step4 的 handle_agent_intent 能识别并路由到 AgentExplorer 实时探索。
    inject_explore_task_to_cases(cases)

    # ★ 2026-08-09: 后处理 — 从 explore_task 中提取 test_steps 和 expected_result，
    #   确保每个用例包含 IEEE 829 标准字段（precondition/test_steps/expected_result）。
    #   向后兼容：现有用例没有这些字段时，从 explore_task 和 checks 自动提取生成。
    for _c in cases:
        if not isinstance(_c, dict):
            continue
        # ── precondition ──
        if not _c.get("precondition"):
            _et = _c.get("_explore_task") or _c.get("explore_task") or {}
            _op = str(_et.get("operation", "") or "").strip()
            _module = str(_et.get("module", "") or "").strip()
            if _op:
                _c["precondition"] = "1.系统已登录 2.已进入%s页面 3.测试数据已准备" % (_module or "目标")
            else:
                _c["precondition"] = "1.系统已登录 2.测试数据已准备"
        # ── test_steps: 从 explore_task.test_method.verifications 提取 ──
        if not _c.get("test_steps"):
            _et = _c.get("_explore_task") or _c.get("explore_task") or {}
            _tm = _et.get("test_method") or {}
            _verifications = _tm.get("verifications") or []
            if _verifications:
                # 将 verifications 转换为 test_steps 格式 JSON 数组
                _steps = []
                for _v in _verifications:
                    if isinstance(_v, dict):
                        _step_entry = {
                            "step": _v.get("step", len(_steps) + 1),
                            "action": _v.get("action", ""),
                        }
                        _target = _v.get("target", "")
                        _value = _v.get("value", "")
                        if _target:
                            _step_entry["target"] = _target
                        if _value:
                            _step_entry["data"] = _value
                        _steps.append(_step_entry)
                if _steps:
                    _c["test_steps"] = json.dumps(_steps, ensure_ascii=False)
            else:
                # 兜底：从 operation 生成
                _op = str(_et.get("operation", "") or "").strip()
                if _op:
                    _c["test_steps"] = json.dumps(
                        [{"step": 1, "action": _op, "target": "目标页面"}],
                        ensure_ascii=False,
                    )
        # ── expected_result: 从 checks 或 verifications 提取 ──
        if not _c.get("expected_result"):
            _checks = _c.get("checks") or []
            _et = _c.get("_explore_task") or _c.get("explore_task") or {}
            _tm = _et.get("test_method") or {}
            _verifications = _tm.get("verifications") or []
            _expect = ""
            # 优先从 checks 提取
            if _checks:
                _last_check = _checks[-1] if isinstance(_checks, list) else _checks
                if isinstance(_last_check, dict):
                    _expect = str(_last_check.get("expect", "") or "")
            # 其次从 verifications 最后一个 verify 步骤提取
            if not _expect and _verifications:
                for _v in reversed(_verifications):
                    if isinstance(_v, dict) and _v.get("action") == "verify":
                        _expect = str(_v.get("expect", "") or "")
                        break
            # 兜底：从 expected_feedback 或 operation 提取
            if not _expect:
                _eb = _et.get("expected_feedback", "") or _et.get("description", "")
                _op = str(_et.get("operation", "") or "").strip()
                if _eb:
                    _expect = _eb[:80]
                elif _op:
                    _expect = "%s操作成功" % _op
            if _expect:
                _c["expected_result"] = _expect[:200]

    # ★ 方案B兜底(2026-07-29): 确保所有_explore_task都有test_method
    #   根因: AI可能生成_explore_task但不含test_method，inject_explore_task_to_cases
    #         的elif分支可能因未知原因未生效。此兜底循环逐条检查并补全。
    for _c in cases:
        if isinstance(_c, dict) and isinstance(_c.get("_explore_task"), dict):
            _et = _c["_explore_task"]
            if not _et.get("test_method"):
                _c["_explore_task"]["test_method"] = _build_default_test_method(_et)
                _log.info(
                    "[_model] 兜底补test_method: module=%r",
                    str(_et.get("module", ""))[:40],
                )

    # ── ★ 根因修复(2026-08-09): 从系统数据字典提取字段名，注入 I01 字段展示类型测试 ──
    #   提前注入 _field_verbose_names，供 _inject_method_and_action_templates 使用
    #   通用性: 跨系统/行业/页面，字段名来自真实数据字典，无业务硬编码
    _all_field_names: list[str] = []
    with guard("model_test_cases: _fn_module = (requirement.get('modul", log=_log):
        _fn_module = (requirement.get("module") or "").strip()
        if _fn_module:
            from src.extensions.system_data_resolver import system_data
            _catalog = system_data.resolve()
            if _catalog.get("success"):
                _cls = _catalog.get("classification", {})
                _raw_entity = _cls.get("entity_tables", {})
                _entity_tables = dict(_raw_entity) if isinstance(_raw_entity, dict) else {}
                # 收集所有表的字段名（columns）
                _seen_fn: set[str] = set()
                for _tbl_name, _tbl_info in _entity_tables.items():
                    if isinstance(_tbl_info, dict):
                        _cols = _tbl_info.get("columns", [])
                        if isinstance(_cols, list):
                            for _col in _cols:
                                _cs = str(_col).strip()
                                if _cs and _cs not in _seen_fn:
                                    _seen_fn.add(_cs)
                                    _all_field_names.append(_cs)
                if _all_field_names:
                    for _c in cases:
                        if isinstance(_c, dict):
                            _c["_field_verbose_names"] = list(_all_field_names)
                            if isinstance(_c.get("_explore_task"), dict):
                                _c["_explore_task"]["_field_verbose_names"] = list(_all_field_names)
                    _log.info(
                        "[_model] 提取字段名列表: module=%s → %d 个字段",
                        _fn_module[:40],
                        len(_all_field_names),
                    )

    # ★ T01/T02(2026-08-06): 方法论确定性规则层 + 动作模板库叠加
    #   AI 生成 verifications 保留在前，规则模板 + 动作模板追加在后（dedup by action+target）。
    _inject_method_and_action_templates(cases)

    # ★ 根因修复(2026-08-25): 从 checks 投影注入 required_tracks（v9 核心字段）
    _inject_required_tracks(cases)

    # ── Flash 校验器 + 重试 ──
    # ★ 修复：改为"先宽松后严格"。兜底是换方法，不是放宽标准。
    #   第一轮：deep_check=False，只做规则校验+cross_check硬校验，快速过滤格式问题。
    #   若存在 cross_check error（编造表名/API/路径等），直接阻断，不重试。
    #   重试：用 feedback 修复后，deep_check=True 做完整 AI 深度校验。
    if enable_validation:
        if isinstance(cases, dict):
            cases = [cases]
        if isinstance(cases, list) and len(cases) > 0:
            from src.extensions.quality.case_validator import (
                validate_intents,
                format_validation_feedback,
            )

            validation = validate_intents(
                cases,
                req_description=requirement.get("description", ""),
                module=requirement.get("module", ""),
                deep_check=False,
            )
            if not validation.passed:
                # ★ 阻断 cross_check error：编造资源无法通过 prompt 修复，不必浪费第二轮 token
                cross_check_errors = [
                    e for e in validation.errors if e.get("cross_check")
                ]
                if cross_check_errors:
                    _log.error(
                        "    🚫 Flash校验发现 cross_check error，直接阻断: %d 项",
                        len(cross_check_errors),
                    )
                    parsed["_validated"] = False
                    parsed["_blocked"] = True
                    parsed["_block_reason"] = (
                        f"cross_check 阻断 {len(cross_check_errors)} 项"
                    )
                    parsed["_validation_warnings"] = [
                        {
                            "case_id": e.get("case_id", "?"),
                            "reason": e.get("reason", ""),
                        }
                        for e in cross_check_errors
                    ]
                    parsed["_cross_check_counts"] = validation.cross_check_counts
                    # ★ Fix19 (2026-07-07): 输出前 3 个 cross_check error 原因，帮助诊断阻断根因
                    _log.error("    🚫 cross_check 错误样本 (前3项):")
                    for _e in cross_check_errors[:3]:
                        _log.error(
                            "      - [%s] %s",
                            _e.get("cross_check", "?"),
                            _e.get("reason", "")[:120],
                        )
                    # ★ 阻断必须表现为失败，避免调用方误把有 cross_check 错误的用例入库
                    parsed["success"] = False
                    parsed["error"] = parsed["_block_reason"]
                    return parsed

                feedback = format_validation_feedback(validation)
                retry_user_msg = user_message + "\n" + feedback
                _log.error("    🔄 Flash校验未通过，重试...")
                retry_max_tok, _ = compute_max_tokens(
                    system_prompt, retry_user_msg, model=model_name
                )
                # ★ F2 全局修复 [未复核-需 GLM/DeepSeek 把关]：仅加 seed=ai_stability_seed
                from src.core.pipeline_config import _read_config_value as _cfg_seed2

                result2 = _ai_brain._call_api(
                    system_prompt=system_prompt,
                    user_message=retry_user_msg,
                    temperature=mp.get("temperature", 0.0),
                    max_tokens=retry_max_tok,
                    caller_name=model_label + "(重试)",
                    task_type="case_modeling",
                     force_cloud=False,
                    cloud_model=model_name,
                    seed=(None if (_seed_v := _cfg_seed2("ai_stability_seed", 42)) is None else int(_seed_v)),
                    response_format={"type": "json_object"},
                )
                if result2.get("success"):
                    parsed2 = _ai_brain._parse_json_response(result2["data"].strip())
                    if parsed2.get("success"):
                        cases2 = parsed2["data"]
                        if isinstance(cases2, dict):
                            cases2 = [cases2]
                        if isinstance(cases2, list) and len(cases2) > 0:
                            # ★ 方案B修复(2026-07-29): retry路径也必须注入_explore_task + test_method
                            #   根因: retry路径直接return parsed2，绕过了inject_explore_task_to_cases
                            #         和兜底循环，导致retry生成的用例test_method全空。
                            inject_explore_task_to_cases(cases2)
                            for _c2 in cases2:
                                if isinstance(_c2, dict) and isinstance(
                                    _c2.get("_explore_task"), dict
                                ):
                                    _et2 = _c2["_explore_task"]
                                    if not _et2.get("test_method"):
                                        _c2["_explore_task"]["test_method"] = (
                                            _build_default_test_method(_et2)
                                        )
                                        _log.info(
                                            "[_model] retry路径兜底补test_method: module=%r",
                                            str(_et2.get("module", ""))[:40],
                                        )
                            # ★ 根因修复(2026-08-09): retry路径也注入字段名
                            if _all_field_names:
                                for _c2 in cases2:
                                    if isinstance(_c2, dict):
                                        _c2["_field_verbose_names"] = list(_all_field_names)
                                        if isinstance(_c2.get("_explore_task"), dict):
                                            _c2["_explore_task"]["_field_verbose_names"] = list(_all_field_names)
                            # ★ 2026-08-09: retry路径也提取 precondition/test_steps/expected_result
                            import json as _json_retry
                            for _c2 in cases2:
                                if not isinstance(_c2, dict):
                                    continue
                                if not _c2.get("precondition"):
                                    _et2 = _c2.get("_explore_task") or _c2.get("explore_task") or {}
                                    _op2 = str(_et2.get("operation", "") or "").strip()
                                    _mod2 = str(_et2.get("module", "") or "").strip()
                                    _c2["precondition"] = "1.系统已登录 2.已进入%s页面 3.测试数据已准备" % (_mod2 or "目标") if _op2 else "1.系统已登录 2.测试数据已准备"
                                if not _c2.get("test_steps"):
                                    _et2 = _c2.get("_explore_task") or _c2.get("explore_task") or {}
                                    _tm2 = _et2.get("test_method") or {}
                                    _verifications2 = _tm2.get("verifications") or []
                                    if _verifications2:
                                        _steps2 = []
                                        for _v2 in _verifications2:
                                            if isinstance(_v2, dict):
                                                _se2 = {"step": _v2.get("step", len(_steps2) + 1), "action": _v2.get("action", "")}
                                                _tgt2 = _v2.get("target", "")
                                                _val2 = _v2.get("value", "")
                                                if _tgt2:
                                                    _se2["target"] = _tgt2
                                                if _val2:
                                                    _se2["data"] = _val2
                                                _steps2.append(_se2)
                                        if _steps2:
                                            _c2["test_steps"] = _json_retry.dumps(_steps2, ensure_ascii=False)
                                if not _c2.get("expected_result"):
                                    _checks2 = _c2.get("checks") or []
                                    _et2 = _c2.get("_explore_task") or _c2.get("explore_task") or {}
                                    _tm2 = _et2.get("test_method") or {}
                                    _verifications2 = _tm2.get("verifications") or []
                                    _expect2 = ""
                                    if _checks2:
                                        _lc2 = _checks2[-1] if isinstance(_checks2, list) else _checks2
                                        if isinstance(_lc2, dict):
                                            _expect2 = str(_lc2.get("expect", "") or "")
                                    if not _expect2 and _verifications2:
                                        for _v2 in reversed(_verifications2):
                                            if isinstance(_v2, dict) and _v2.get("action") == "verify":
                                                _expect2 = str(_v2.get("expect", "") or "")
                                                break
                                    if not _expect2:
                                        _eb2 = _et2.get("expected_feedback", "") or _et2.get("description", "")
                                        _op2 = str(_et2.get("operation", "") or "").strip()
                                        _expect2 = (_eb2[:80] if _eb2 else ("%s操作成功" % _op2 if _op2 else ""))
                                    if _expect2:
                                        _c2["expected_result"] = _expect2[:200]
                            # ★ T01/T02(2026-08-06): retry路径同样叠加规则+动作模板
                            _inject_method_and_action_templates(cases2)
                            # ★ 根因修复(2026-08-25): retry路径同样注入 required_tracks
                            _inject_required_tracks(cases2)

                            # ★ 重试后用严格校验（deep_check=True），标准不降反升
                            validation2 = validate_intents(
                                cases2,
                                req_description=requirement.get("description", ""),
                                module=requirement.get("module", ""),
                                deep_check=True,
                            )
                            parsed2["_cross_check_counts"] = (
                                validation2.cross_check_counts
                            )
                            parsed2["_validated"] = validation2.passed
                            if not validation2.passed:
                                parsed2["_blocked"] = True
                                parsed2["_block_reason"] = "重试后仍未通过严格校验"
                                parsed2["_validation_warnings"] = [
                                    {
                                        "case_id": e.get("case_id", "?"),
                                        "reason": e.get("reason", ""),
                                    }
                                    for e in validation2.errors
                                ]
                                # ★ 重试后仍未通过严格校验，必须返回失败
                                parsed2["success"] = False
                                parsed2["error"] = parsed2["_block_reason"]
                            # 重试路径也记录交叉校验指标
                            with guard("model_test_cases: from src.backend.state import _threa", log=_log):
                                from src.backend.state import _thread_run_id
                                from src.extensions.metrics_collector import (
                                    metrics_collector,
                                )

                                # ★ 2026-09-01: 优先显式 run_id（worker 线程 thread-local 可能空）
                                _rid2 = run_id or getattr(
                                    _thread_run_id, "value", ""
                                ) or ""
                                if _rid2:
                                    counts2 = validation2.cross_check_counts
                                    metrics_collector.record_cross_checks(
                                        project=project_name or config.project_name,
                                        run_id=_rid2,
                                        api_errors=counts2.get("api", 0),
                                        ui_errors=counts2.get("ui", 0),
                                        enum_errors=counts2.get("enum", 0)
                                        + counts2.get("db_trace", 0),
                                        deep_issues=counts2.get("deep", 0),
                                        rule_warnings=counts2.get("rule", 0),
                                        total_cases=len(cases2),
                                    )
                            return parsed2
                        else:
                            # ★ 重试返回空用例，按失败处理
                            parsed["success"] = False
                            parsed["error"] = "重试后未生成任何用例"
                            return parsed
                    else:
                        # ★ 重试响应解析失败
                        parsed["success"] = False
                        parsed["error"] = (
                            f"重试响应解析失败: {parsed2.get('error', '未知错误')}"
                        )
                        return parsed
                else:
                    # ★ 重试 AI 调用失败
                    parsed["success"] = False
                    parsed["error"] = (
                        f"重试 AI 调用失败: {result2.get('error', '未知错误')}"
                    )
                    return parsed
            else:
                parsed["_validated"] = True
                parsed["_cross_check_counts"] = validation.cross_check_counts

            # 记录 Step 3 交叉校验结果到 ai_health_metrics
            with guard("model_test_cases: from src.backend.state import _threa", log=_log):
                from src.backend.state import _thread_run_id
                from src.extensions.metrics_collector import metrics_collector

                # ★ 2026-09-01: 优先用显式传入的 run_id（worker 线程 thread-local 可能为空）
                _rid = run_id or getattr(_thread_run_id, "value", "") or ""
                if _rid:
                    counts = validation.cross_check_counts
                    metrics_collector.record_cross_checks(
                        project=project_name or config.project_name,
                        run_id=_rid,
                        api_errors=counts.get("api", 0),
                        ui_errors=counts.get("ui", 0),
                        enum_errors=counts.get("enum", 0) + counts.get("db_trace", 0),
                        deep_issues=counts.get("deep", 0),
                        rule_warnings=counts.get("rule", 0),
                        total_cases=len(cases),
                    )

    return parsed
