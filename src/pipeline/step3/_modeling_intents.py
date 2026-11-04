from typing import Any
# -*- coding: utf-8 -*-
"""建模引擎 - 意图写入模块（从 _modeling_engine.py 拆分，单文件<=800行）。

职责：意图写入 MySQL + 跨需求用例生成 + 按页面合并用例。
  _summarize_project_context: 项目上下文摘要（截断到 max_chars）
  _write_intents_to_store: 批量写入意图到 MySQL（1 次 upsert）
  _get_valid_action_names_for_cross: 获取跨需求用例有效 action 名
  _generate_cross_requirement_intents: 生成跨需求复合用例
  _merge_cases_by_page: 按页面合并用例（去重 actions/checks）

被 _modeling_engine.py 主流程调用（_parallel_model_all_reqs）。
依赖：
  _log (logging.getLogger)
  _cleanup_navigate_urls (from _modeling_validators)

根因(3层追问):
  1. 表层: _modeling_engine.py 超 §4.3 红线
  2. 中层: 意图写入函数群与主流程内聚，但逻辑独立
  3. 根因: 意图写入是独立的"持久化"职责，应拆为独立模块
通用性: 适用于所有系统/行业 - 纯意图写入/合并，不依赖业务术语
"""
import logging
from src.core.guarded import guard
import re

from src.pipeline.step3._modeling_validators import _cleanup_navigate_urls

_log = logging.getLogger(__name__)


def _summarize_project_context(project_context: str, max_chars: int = 2000) -> str:
    """★ v6: 用统一功能点提取器替代硬截断，禁止 project_context[:N]。

    项目上下文过长时，用 extract_feature_points 提取功能点摘要。
    """
    if not project_context:
        return ""
    if len(project_context) <= max_chars:
        return project_context
    with guard("_summarize_project_context: from src.extensions.rag_engine.chunk", log=_log):
        from src.extensions.rag_engine.chunking_mixin_mixins._segment import (
            extract_feature_points,
        )

        summary = extract_feature_points(project_context, max_chars=max_chars)
        if summary:
            return summary
    # 降级1：用智能分段按自然边界切分
    try:
        from src.extensions.rag_engine.chunking_mixin_mixins._segment import (
            SmartSegmentMixin,
        )

        segments = SmartSegmentMixin._smart_segment(project_context, max_len=max_chars)
        if segments:
            result_parts = []
            for seg in segments:
                if len("\n".join(result_parts)) + len(seg) > max_chars:
                    break  # 丢弃剩余，禁止硬截断
                result_parts.append(seg)
            result = "\n".join(result_parts)
            if result.strip():
                return result.strip()
    except Exception as _e:
        _log.debug("[Step3] context 降级1失败: %s", _e, exc_info=True)
    # 降级2：按段落拼接，超长时丢弃而非截断
    paragraphs = [x for p in project_context.split("\n\n") if (x := p.strip())]
    result_parts = []
    for para in paragraphs:
        if len("\n".join(result_parts)) + len(para) > max_chars:
            break  # 丢弃剩余，禁止硬截断
        result_parts.append(para)
    return "\n".join(result_parts).strip()


def _write_intents_to_store(
    all_intents: list[dict],
    ungen_reqs: list[dict],
    risk_map: dict = None,
    stop_check=None,
) -> int:
    """将生成的意图批量写入 MySQL（1 次 upsert 处理全部意图，消除 25 次 DB 往返）。
    返回总条数。"""
    from src.core.excel_handler import excel_handler
    from src.pipeline.utils.confidence_scorer import score_test_case
    from src.pipeline.step3._requirement_selector import (
        _mark_modeled_intents_for_requirement,
    )

    if not all_intents or stop_check and stop_check():
        return 0

    # ── 第 1 遍：预处理全部意图（补 req_id、分组、计算元数据） ──
    req_intent_map: dict[str, list[dict]] = {}
    for intent in all_intents:
        if not isinstance(intent, dict):
            continue
        rid = intent.get("req_id", "")
        if not rid:
            continue
        req_intent_map.setdefault(rid, []).append(intent)

    # ★ 收集所有需写入的意图（含 req_id 回填），同时预计算置信度/风险
    flat_intents: list[dict] = []
    all_confidence: dict[str, int] = {}
    all_risk: dict[str, str] = {}
    all_metrics: dict[str, dict[str, Any]] = {}
    all_risk_embeddings: dict[str, list] = {}  # req_id → embedding 向量

    # ★ 透传链路：构建 req_id → requirement 元数据映射（source_quote/feature_id/module/is_implicit）
    # 用于把 Step2 产出的原文锚点透传到 test_case.payload，供 Step4 证据校准使用
    # ★ A3 三级信任差异化：is_implicit 透传后用于 L3 候选用例降级处理
    req_meta_map: dict[str, dict[str, Any]] = {}
    for r in ungen_reqs:
        rid = str(r.get("req_id", "") or "")
        if rid:
            req_meta_map[rid] = {
                "source_quote": str(r.get("source_quote", "") or ""),
                "feature_id": int(r.get("feature_id") or 0),
                "module": str(r.get("module", "") or ""),
                "is_implicit": int(r.get("is_implicit", 0) or 0),
            }

    req_ids_with_intents: set = set()
    for req_id, my_intents in req_intent_map.items():
        # 查风险等级
        if risk_map:
            risk_level = risk_map.get(req_id, "medium")
        else:
            from src.extensions.quality.risk_analyzer import assess_requirement_risk

            risk_level, _ = assess_requirement_risk({"req_id": req_id})

        # ★ 计算需求文本的 embedding（用于 k-NN 风险分类器自学习）
        req_obj = next((r for r in ungen_reqs if r.get("req_id") == req_id), None)
        if req_obj:
            try:
                from src.extensions.semantic_index import embed as _embed_fn

                _emb_text = f"{req_obj.get('module', '')} {req_obj.get('description', '')}".strip()
                if _emb_text:
                    _vec = _embed_fn(_emb_text)
                    if _vec:
                        all_risk_embeddings[req_id] = _vec
            except Exception as _ee:
                _log.debug(
                    "[risk_embed] embedding 失败(req_id=%s): %s",
                    req_id,
                    _ee,
                    exc_info=True,
                )

        # ★ S3-032: case_id 去重 — 同一 req_id 可能生成多个不同模块的用例，AI 可能输出相同 case_id
        _seen_cids: dict[str, int] = {}
        for c in my_intents:
            cid = str(c.get("case_id", "") or "").strip()
            if not cid:
                continue
            # 检测重复 case_id，追加模块后缀确保唯一
            if cid in _seen_cids:
                _seen_cids[cid] += 1
                _module = str(
                    c.get("feature_id", "") or c.get("module", "") or ""
                ).strip()
                _suffix = _module[:8] if _module else str(_seen_cids[cid])
                _new_cid = f"{cid}-{_suffix}"
                _log.warning(
                    "[case_id] 重复 case_id=%s → 重命名为 %s（模块=%s）",
                    cid,
                    _new_cid,
                    _module,
                )
                c["case_id"] = _new_cid
                cid = _new_cid
            else:
                _seen_cids[cid] = 1
            # ★ 回填 req_id（防御：AI 返回的 JSON 可能缺 req_id）
            if not c.get("req_id"):
                c["req_id"] = req_id
            req_ids_with_intents.add(req_id)
            # ★ 透传 Step2 元数据到 intent（写入 payload_json，供 Step4 证据校准 + 追溯链路）
            _meta = req_meta_map.get(str(req_id), {})
            if _meta.get("source_quote") and not c.get("source_quote"):
                c["source_quote"] = _meta["source_quote"]
            if _meta.get("feature_id") and not c.get("feature_id"):
                c["feature_id"] = _meta["feature_id"]
            if _meta.get("module") and not c.get("module"):
                c["module"] = _meta["module"]
            # ★ A3 三级信任差异化：透传 is_implicit + trust_level 标记
            _is_implicit = int(_meta.get("is_implicit", 0) or 0)
            if _is_implicit and not c.get("is_implicit"):
                c["is_implicit"] = _is_implicit
            if _is_implicit == 2 and not c.get("trust_level"):
                c["trust_level"] = "L3_implicit_business"
            elif _is_implicit == 1 and not c.get("trust_level"):
                c["trust_level"] = "L2_implicit_common"
            elif _is_implicit == 0 and not c.get("trust_level"):
                c["trust_level"] = "L1_explicit"
            try:
                all_confidence[cid] = score_test_case(c, all_intents=all_intents)
            except Exception:
                all_confidence[cid] = 75
            # ★ A3 L3 业务隐性候选降级：置信度 ×0.8（宁缺勿滥，比追溯失败 ×0.7 略轻）
            if _is_implicit == 2:
                all_confidence[cid] = int(all_confidence[cid] * 0.8)
                _log.info(
                    "[Step3] L3 候选用例 %s 置信度降级 ×0.8（is_implicit=2 业务隐性）",
                    cid,
                )
            all_risk[cid] = risk_level
            # ★ 计算意图驱动指标
            with guard("_write_intents_to_store: from src.pipeline.steps.step3.case_q", log=_log):
                from src.pipeline.steps.step3.case_quality import compute_case_metrics

                m = compute_case_metrics(c)
                if m:
                    all_metrics[str(cid)] = m
            # ★ 控件存在性预校验结果传播：
            #   - non_testable → valid_status='non_testable'（页面已爬取但控件缺失）
            #   - unverified    → 保持 valid_status='valid'（页面未爬取，Step4 自行探索）
            if c.get("non_testable"):
                c["valid_status"] = "non_testable"
                _reason = c.get("non_testable_reason", "") or ""
                _log.info("[Step3] 用例 %s 标记为 non_testable: %s", cid, _reason[:80])
            elif c.get("_control_validation", {}).get("unverified"):
                _log.info(
                    "[Step3] 用例 %s 页面未爬取，保持 valid（Step4 自行探索）", cid
                )
            flat_intents.append(c)

    if not flat_intents:
        _log.error(
            "[Step3] DEBUG: flat_intents 为空！all_intents=%d req_intent_map=%d",
            len(all_intents),
            len(req_intent_map),
        )
        return 0

    # ── v3.1: 去重 — 仅与 DB 已有用例去重 ──
    # ★ 通用修复 v2 (2026-07-20): 完全移除同批次内跨需求去重。
    #   原因分析（自我反驳三次）：
    #   1) 第一次反驳：是否调高阈值？ → 否，阈值0.85合理，规则提取生成的用例天然相似
    #      （所有 input_rule 都生成"填写+提交+断言"），调阈值治标不治本。
    #   2) 第二次反驳：是否只对比同 case_type？ → 否，仍会误杀（不同需求的 P01 可能动作相同）
    #   3) 第三次反驳：是否完全禁用跨需求去重？ → 是，这是最佳方案：
    #      - 规则提取基于需求 operation 独立生成，不同需求的用例天然独立
    #      - "查询辅材"与"查询辅材配方"动作相似但业务场景不同，不应去重
    #      - 通用测试引擎不应判断"不同需求用例是否冗余"——这是产品决策
    #      - force_regen=True 时 DB 已清空，与 DB 去重不会触发
    #      - 非force_regen时，DB 去重可防止跨版本重复（合理用途）
    #   这符合 AGENTS.md "一个需求→一个完整测试用例（含正/异/边变体）"的设计原则。
    with guard("_write_intents_to_store: from src.pipeline.steps.step3.case_q", log=_log, level=logging.WARNING):
        from src.pipeline.steps.step3.case_quality import (
            deduplicate_intents,
        )  # noqa: F401

        dedup_removed_batch = 0  # 同批次内不再去重

        # 与 DB 已有用例去重（增量场景：非首次建模时 DB 中可能已有同需求用例）
        # ★ S3-014: 使用 apply_batch_dedup 统一编排，获取重复追踪信息
        dedup_removed_db = 0
        with guard("_write_intents_to_store: from src.pipeline.utils.case_store i", log=_log):
            from src.pipeline.utils.case_store import case_store as _cs_dedup
            from src.pipeline.steps.step3.case_quality.orchestrator import (
                apply_batch_dedup,
            )

            existing_intents = _cs_dedup.list_intents()
            _log.info(
                "[Step3] DB 已有用例: %d 条 (同批次内跨需求去重已禁用)",
                len(existing_intents) if existing_intents else 0,
            )
            if existing_intents:
                before_count = len(flat_intents)
                # ★ 安全阀：如果 DB 去重会移除超过 90% 的新用例，说明 DB 数据可能有问题
                #   （如清库不彻底、跨版本残留），跳过 DB 去重，仅保留同批次去重结果
                dedup_result = apply_batch_dedup(flat_intents, existing_intents)
                removed_count = before_count - len(dedup_result["kept"])
                removal_ratio = removed_count / before_count if before_count > 0 else 0
                if removal_ratio > 0.9 and before_count >= 10:
                    _log.warning(
                        "[Step3] DB 去重移除比例过高 (%d/%d = %.0f%%)，跳过 DB 去重以保护新用例。"
                        "可能原因：DB 未完全清空或存在跨版本残留数据。",
                        removed_count,
                        before_count,
                        removal_ratio * 100,
                    )
                    dedup_removed_db = 0
                else:
                    flat_intents = dedup_result["kept"]
                    dedup_removed_db = removed_count
                    if dedup_removed_db > 0:
                        _log.info(
                            "[Step3] 与 DB 已有用例去重: 标记 %d 条重复 (保留追踪信息)",
                            dedup_removed_db,
                        )

        if not flat_intents:
            _log.warning(
                "[Step3] 去重后无可写入用例（全部 %d 条已存在），跳过写入",
                dedup_removed_batch + dedup_removed_db,
            )
            return (
                dedup_removed_batch + dedup_removed_db
            )  # ★ Fix: 返回已存在用例数，而非 0

    # ── 防幻觉：追溯链路校验（trace_validator）─ P0 新增 ──
    # 校验 checks[] 中 precision=exact 的 db 表名/API 路径是否在 trace_links 中真实存在
    with guard("_write_intents_to_store: from src.extensions.traceability_mod", log=_log, level=logging.WARNING):
        from src.extensions.traceability_model import traceability_engine

        trace_cache = traceability_engine._load_cache()
        if trace_cache:
            from src.extensions.trace_validator import validate_checks_against_trace

            db_schema = {}

            # 调用校验
            _log.info("[Step3] 追溯链路校验开始: %d 条意图", len(flat_intents))
            validation_result = validate_checks_against_trace(
                flat_intents, trace_links=trace_cache, db_schema=db_schema
            )

            # 处理结果：标记不通过的用例，降低置信度（不删除，不阻塞入库）
            invalid_count = 0
            for item in validation_result.get("intents", []):
                result = item["result"]
                case_id = item["case_id"]
                if not result.get("valid", True):
                    invalid_count += 1
                    # 在 intent 字典中标记校验结果
                    for intent in flat_intents:
                        if intent.get("case_id") == case_id:
                            intent["_trace_validated"] = False
                            intent["_trace_issues"] = result.get("issues", [])
                            intent["_trace_warnings"] = result.get("warnings", [])
                            # 置信度打 7 折
                            if case_id in all_confidence:
                                all_confidence[case_id] = int(
                                    all_confidence[case_id] * 0.7
                                )
                            break
                    _log.warning(
                        "[Step3-TraceValid] 用例 %s 校验失败: %s",
                        case_id,
                        "; ".join(result.get("issues", [])),
                    )

            if invalid_count > 0:
                _log.warning(
                    "##HTML:##<span style='color:#ef4444;font-weight:bold'>"
                    "⚠️ [Step3 追溯校验] %d 条用例存在幻觉风险，已降低置信度。"
                    "请检查 Step2 数据源（YApi/DB Schema）是否完整拉取，建议重新执行 Step2→Step3。"
                    "</span>",
                    invalid_count,
                )
            else:
                _log.info(
                    "##HTML:##<span style='color:#10b981'>"
                    "✅ [Step3 追溯校验] 全部 %d 条用例通过"
                    "</span>",
                    len(flat_intents),
                )
        else:
            _log.warning(
                "##HTML:##<span style='color:#f59e0b;font-weight:bold'>"
                "⚠️ [Step3 追溯校验] 无追溯链路数据，跳过防幻觉校验。请先执行 Step2 构建追溯链路。"
                "</span>"
            )

    # ── W2 P0-3: ADI 门禁（落库前算分 + 标记）──
    #   插入点严格在「追溯校验之后、upsert_intents 之前」：adi/adi_detail/needs_review
    #   随 payload_json 自动持久化。度量模式（默认 OFF）只写分不拦截；
    #   开启时才标 needs_review（fail-closed，低分仍落库以保留「引擎能力弱」信号）。
    # ★ §14 关键改动 [已复核-DeepSeek-V4-Flash 2026-08-05]：Step3 落库核心路径，需强模型复核。
    try:
        from src.pipeline.steps.step3.oracle.guard import apply_adi_gate

        _adi_report = apply_adi_gate(flat_intents)
        if _adi_report.get("blocked"):
            _log.warning(
                "##HTML:##<span style='color:#ef4444;font-weight:bold'>"
                "⚠️ [ADI 门禁] %d 条用例 ADI 不足（阈值 %d），已标记 needs_review。"
                "低分=引擎能力弱信号，非删除；请检查 Step3 生成的断言深度。"
                "</span>",
                len(_adi_report["blocked"]),
                _adi_report.get("threshold", 1),
            )
        _log.info(
            "[Step3-ADI] 度量: total=%d avg=%.2f hist=%s",
            _adi_report.get("total", 0),
            _adi_report.get("avg", 0.0),
            _adi_report.get("hist", {}),
        )
    except Exception as _e_adi:
        # fail-closed：不阻断已花 8 分钟的建模成果，但必须留 error 日志（不静默 pass）
        _log.error(
            "[Step3-ADI] 门禁异常(不阻断落库，已记录): %s", _e_adi, exc_info=True
        )

    # ── 第 2 遍：1 次批量 upsert 写入全部意图（替代原来 25 次逐需求调用） ──
    # ★ 通用修复(2026-07-19): 写入前清理 navigate URL 中的中文文本
    with guard("_write_intents_to_store: _cleanup_navigate_urls(flat_intents)", log=_log, level=logging.WARNING):
        _cleanup_navigate_urls(flat_intents)
    # ★ v5.9(2026-08-04): 落库前确定性导航解析——LLM 对 prompt 导航规则遵循不可靠
    #   （实测 8 条全选同一菜单），用关键词→真实菜单匹配覆写导航目标，保证需求→页面映射。
    with guard("_write_intents_to_store: from src.core.config_loader import c", log=_log, level=logging.WARNING):
        from src.core.config_loader import config as _nav_cfg

        _nav_project = str(getattr(_nav_cfg, "project_name", "") or "")
        from src.pipeline.step3._navigation_resolver import resolve_intents_batch

        resolve_intents_batch(flat_intents, _nav_project)
    from src.pipeline.utils.case_store import case_store

    # ★ 检查项定稿守卫：
    #   生成端（case_rules/_fallback_cases/_intent_checks）发射的 check 类型若未在
    #   Step4 执行注册表注册，落库后必然判"未知检查类型"假 FAIL。在落库前唯一
    #   咽喉点按执行注册表清洗：剔除不可执行类型 + 极性过滤 + 轨道诚实化。
    #   从此新增 check 类型必须先注册执行器，生成端词表不可能再漂移。
    try:
        from src.pipeline.step3._check_guard import sanitize_intents

        sanitize_intents(flat_intents)
    except Exception as _e_guard:
        # fail-open：守卫自身异常不阻断建模主流程，但必须留痕（不静默）
        _log.error("[Step3] 检查项守卫异常(不阻断落库): %s", _e_guard, exc_info=True)

    # ── 访问控制型用例的执行身份指派（★ RC-C 通用根因修复 2026-09-24）──
    #   根因: 访问拒绝类用例（"viewer 不允许新增…403"、"未登录须重定向登录页"）
    #     此前不携带身份声明，Step4 恒以 run 级默认身份（admin）执行 → 断言
    #     "应被拒绝"必然假 FAIL，且真实越权漏洞永不可检出。
    #   落库前唯一咽喉点指派（随 payload_json 持久化）；靶子未声明 ≥2 角色 →
    #     恒不指派（零行为变更）。详见 _identity_assign 模块说明。
    try:
        from src.core.config_loader import config as _id_cfg
        from src.pipeline.step3._identity_assign import assign_intent_identities

        _req_text_map = {
            str(r.get("req_id", "") or ""): r
            for r in ungen_reqs
            if str(r.get("req_id", "") or "")
        }
        _id_assigned = assign_intent_identities(
            flat_intents,
            project=str(getattr(_id_cfg, "project_name", "") or ""),
            req_text_map=_req_text_map,
        )
        if _id_assigned:
            _log.info(
                "##HTML:##<span style='color:#0ea5e9'>"
                "🔐 [Step3 身份指派] %d 条访问控制用例已声明执行身份"
                "（Step4 将先隔离会话再切身份执行）"
                "</span>",
                _id_assigned,
            )
    except Exception as _e_id:
        # fail-open：指派异常不阻断建模主流程，但必须留痕（不静默）
        _log.error("[Step3] 身份指派异常(不阻断落库): %s", _e_id, exc_info=True)

    if not case_store.upsert_intents(flat_intents, case_asset_file=""):
        _log.error("[Step3] 批量 upsert 失败（%d 条意图）", len(flat_intents))
        return 0
    _log.info("[Step3] 批量 upsert: %d 条意图 → MySQL test_cases", len(flat_intents))

    # ── 第 3 遍：批量元数据写入 ──
    # 3a. 意图→需求映射（批量）
    from src.pipeline.utils.case_requirement_tracker import case_requirement_tracker

    mapping_failed = []
    for intent in flat_intents:
        cid = intent["case_id"]
        # 主需求映射
        if not case_requirement_tracker.add(cid, intent.get("req_id", "")):
            mapping_failed.append(str(cid))
        # ★ 页面合并：为复合用例的合并需求也建立追踪
        for merged_rid in intent.get("_merged_from_req_ids", []) or []:
            merged_rid = str(merged_rid).strip()
            if merged_rid and merged_rid != intent.get("req_id", ""):
                if not case_requirement_tracker.add(cid, merged_rid):
                    _log.warning(
                        "[Step3] 合并需求追踪失败: case=%s req=%s", cid, merged_rid
                    )
    if mapping_failed:
        raise RuntimeError(
            "测试用例已写入 MySQL test_cases，但 intent→req 映射写入失败: %s。"
            "为避免覆盖率/执行/报告错位，已中止后续流程，请修复 MySQL 后重跑。"
            % ",".join(mapping_failed[:10])
        )

    # 3b. 意图指标（1 次批量写入）
    #   只写**真正落库**的用例：指标在生成阶段按候选全量收集，而 sanitize_intents
    #   会把不合规候选剔除（如期望强度超出需求声明），若照单写 meta 会留下
    #   「test_cases 里不存在的 case_id」孤儿行 → 后续覆盖率/审核口径分母虚高。
    if all_metrics:
        _kept_ids = {str(i.get("case_id") or "") for i in flat_intents if isinstance(i, dict)}
        _kept_metrics = {str(cid): m for cid, m in all_metrics.items() if str(cid) in _kept_ids}
        if _kept_metrics:
            with guard("_write_intents_to_store: from src.pipeline.utils.review_store", log=_log, level=logging.WARNING):
                from src.pipeline.utils.review_store import review_store

                review_store.write_case_metrics_batch(_kept_metrics)

    # 3c. 置信度/风险等级元数据（逐条，review_store 无批量接口但单条写入极快）
    meta_failed = []
    for intent in flat_intents:
        cid = intent["case_id"]
        score = all_confidence.get(cid, 75)
        risk = all_risk.get(cid, "medium")
        c_metrics = all_metrics.get(str(cid))
        # ★ 传入 risk_embedding（用于 k-NN 风险分类器自学习）
        _rid = intent.get("req_id", "")
        _emb = all_risk_embeddings.get(_rid) if _rid else None
        if not excel_handler._write_case_meta(
            cid,
            score,
            risk,
            case_metrics=c_metrics,
            risk_embedding=_emb,
            # ★ T2 流水线强化专项: 方法论白名单校验结果持久化（case dict 由
            #   MethodologyValidator 就地写入 method_valid / method_fix_note）
            method_valid=intent.get("method_valid", 1),
            method_fix_note=intent.get("method_fix_note", ""),
        ):
            meta_failed.append(str(cid))
    if meta_failed:
        _log.error("[Step3] 测试用例元数据写入失败: %s", ",".join(meta_failed[:10]))

    # ── 第 4 遍：更新需求状态标记（轻量，每需求 1 次 UPDATE） ──
    for req in ungen_reqs:
        req_id = req.get("req_id", "")
        if req_id not in req_ids_with_intents:
            continue
        try:
            excel_handler.update_is_generated(req_id, True)
        except Exception as _e:
            _log.debug("[_modeling_engine] 非关键异常: %s", _e, exc_info=True)
        _mark_modeled_intents_for_requirement(req)
        excel_handler.mark_requirement_intents_modeled(req_id)

    _log.info(
        "[Step3] 写入完成: %d 条意图 / %d 条需求",
        len(flat_intents),
        len(req_ids_with_intents),
    )

    # 记录 Step 3 用例建模质量到 ai_health_metrics
    with guard("_write_intents_to_store: from src.backend.state import _threa", log=_log):
        from src.backend.state import _thread_run_id
        from src.core.config_loader import config
        from src.extensions.metrics_collector import metrics_collector

        run_id = getattr(_thread_run_id, "value", "") or ""
        if run_id:
            # 按 req_id 汇总建模质量
            for req_id, my_intents in req_intent_map.items():
                if not my_intents:
                    continue
                scores = [
                    all_confidence.get(c.get("case_id", ""), 75) for c in my_intents
                ]
                avg_score = sum(scores) / len(scores) if scores else 0.0
                low_quality = sum(1 for s in scores if s < 50)
                validation_warnings = sum(
                    len(c.get("_trace_warnings", []) or [])
                    + len(c.get("_validation_warnings", []) or [])
                    for c in my_intents
                )
                risk_level = all_risk.get(req_id, "medium")
                metrics_collector.record_case_modeling_quality(
                    project=config.project_name,
                    run_id=run_id,
                    req_id=req_id,
                    generated_count=len(my_intents),
                    avg_quality_score=avg_score,
                    low_quality_count=low_quality,
                    validation_warning_count=validation_warnings,
                    risk_level=risk_level,
                )

    return len(flat_intents)


def _get_valid_action_names_for_cross() -> list[str]:
    """获取可用的操作名列表（从 action_library 加载，供跨需求 AI prompt 注入）。"""
    try:
        from src.pipeline.step3.case_rules import _registry
        from src.pipeline.steps.step4.browser_tools._tool_definitions import (
            _TOOL_PREFIX_MAP,
        )

        # 从 action_library 加载非 workflow 的 action_id
        _store = _registry._load_action_library()
        action_ids = []
        if _store:
            for a in _store:
                aid = a.get("action_id", "")
                if aid and not a.get("is_workflow"):
                    action_ids.append(aid)
        # 补充 hardcoded 但有 tool 映射的（兜底）
        for aid in _TOOL_PREFIX_MAP:
            if aid not in action_ids:
                action_ids.append(aid)
        # 基础操作始终包含
        for base in [
            "navigate",
            "click",
            "fill",
            "screenshot",
            "wait",
            "select_option",
        ]:
            if base not in action_ids:
                action_ids.append(base)
        return action_ids
    except Exception as _e:
        _log.debug("[cross] 获取合法操作名失败: %s", _e)
        return ["navigate", "click", "fill", "screenshot", "wait", "select_option"]


def _page_slug(nav_url: str) -> str:
    """把页面 URL/页面名归一为 case_id 可用的短 slug（确定性、跨系统通用）。

    ★  根因修复：原实现把非 ASCII 字符全部替换为 ``_`` 后 strip，
    非英文页面名（实测 ``资产``/``审计``/``登录场景（无菜单，Agent自主处理）``）
    全部坍缩为 ``ROOT`` → 3 个不同页面的折叠用例 case_id 完全相同
    （``TC-PAGE-ROOT-CP01``）→ 入库 UPSERT 互相覆盖，只留最后一条，
    "每页判一次"退化为"全站只剩一条"。
    通用规则：slug 必须对**不同页面**唯一。ASCII 可读时保留可读形式；
    归一后为空（纯中文等）时改用确定性短哈希，保证唯一且可复现。
    """
    _s = re.sub(r"[^0-9A-Za-z]+", "_", str(nav_url or "")).strip("_").upper()
    if _s:
        return _s
    _raw = str(nav_url or "").strip()
    # 纯符号/空（如 ""、"/"）→ 站点根，语义上就是同一页
    if not re.search(r"[0-9A-Za-z\u4e00-\u9fff]", _raw):
        return "ROOT"
    import hashlib

    return "P" + hashlib.sha1(_raw.encode("utf-8")).hexdigest()[:8].upper()


def _collapse_page_scoped_cases(all_intents: list[dict]) -> list[dict]:
    """★  根因修复：页面级横切用例按 (页面, 类型, 模板) 折叠为单条。

    根因（accept_sut_0923 实测）：兼容/易用兜底模板（UX01/CP01/CP02）断言的是
    **整页**度量（整页空状态 / 整页多分辨率破版 / 整页点击目标尺寸），却被按
    **每条需求**各造一份 → 172 条兼容/易用用例全部指向同一页面 ``/``，同一页面
    事实被计 172 次；其中 123 条已执行且 100% FAIL，是假 FAIL 的最大来源
    （62 条挂 375px 移动端溢出 = 引擎自造需求；61 条挂整页链接尺寸 = 重复计数）。

    通用规则：标了 ``_scope="page"`` 的用例，其断言只与**页面**有关、与 req_id 无关
    → 按 ``(navigate URL, case_type, _scope_key)`` 折叠为一条，actions/checks 取
    并集去重，元数据重写为页面级（case_id/title/description）。

    跨系统成立：任何靶子的页面级度量都不应随需求条数线性膨胀。
    未标 ``_scope="page"`` 的用例（含需求级 UX02）零行为变更。
    """
    if not all_intents:
        return all_intents

    page_cases: dict[tuple, list[dict]] = {}
    others: list[dict] = []

    for intent in all_intents:
        if not isinstance(intent, dict) or intent.get("_scope") != "page":
            others.append(intent)
            continue
        _nav = ""
        for a in intent.get("actions", []) or []:
            if isinstance(a, dict) and a.get("type") == "navigate":
                _nav = str(a.get("target_hint", "") or "").strip()
                if _nav:
                    break
        _key = (_nav, str(intent.get("type", "")), str(intent.get("_scope_key", "")))
        page_cases.setdefault(_key, []).append(intent)

    collapsed: list[dict] = []
    for (_nav, _ctype, _skey), group in page_cases.items():
        if len(group) == 1:
            collapsed.append(group[0])
            continue

        # 取 checks 最丰富的作为基础（保留其 test_method/control_type/tracks）
        primary = dict(max(group, key=lambda c: len(c.get("checks", []) or [])))

        _actions: list[dict] = []
        _seen_a: set = set()
        _checks: list[dict] = []
        _seen_c: set = set()
        for it in group:
            for a in it.get("actions", []) or []:
                if not isinstance(a, dict):
                    continue
                # 去重 key 不含 value：value 是测试数据、不是动作语义；含随机数据
                # （如 UX01 的 ZZ_NOMATCH_<uuid>）时会把同一动作判成不同，折叠失效。
                _k = (str(a.get("type", "")), str(a.get("target_hint", "")))
                if _k not in _seen_a:
                    _seen_a.add(_k)
                    _actions.append(dict(a))
            for c in it.get("checks", []) or []:
                if not isinstance(c, dict):
                    continue
                _k = (str(c.get("check", "")), str(c.get("expect", "")),
                      str(c.get("track", "")))
                if _k not in _seen_c:
                    _seen_c.add(_k)
                    _checks.append(dict(c))

        _slug = _page_slug(_nav)
        primary["actions"] = _actions
        primary["checks"] = _checks
        primary["case_id"] = f"TC-PAGE-{_slug}-{_skey}"
        primary["title"] = f"页面级-{_ctype}-{_nav or '/'}-{_skey}"
        primary["description"] = (
            f"对页面 {_nav or '/'} 执行页面级{_ctype}横切检查（{_skey}）——"
            f"该维度与具体需求无关，全站按页面判定一次，不随需求条数重复计数。"
        )
        primary["_merged_from_req_ids"] = sorted({
            str(i.get("req_id", "")).strip() for i in group if str(i.get("req_id", "")).strip()
        })
        primary["_merged_from_case_ids"] = [str(i.get("case_id", "")) for i in group]
        primary["_page_scope"] = True
        collapsed.append(primary)
        _log.info(
            "[Step3] ★ 页面级折叠: page=%s type=%s key=%s 由 %d 条合并为 1 条",
            _nav or "/", _ctype, _skey, len(group),
        )

    _merged_n = sum(len(g) for g in page_cases.values()) - len(collapsed)
    if _merged_n > 0:
        _log.info(
            "[Step3] ★ 页面级横切用例折叠: %d 条 → %d 条（消除 %d 条同页面重复计数）",
            sum(len(g) for g in page_cases.values()), len(collapsed), _merged_n,
        )
    return others + collapsed


def _merge_cases_by_page(all_intents: list[dict]) -> list[dict]:
    """按页面合并用例：同一 target_page 的多个用例合并为一个复合用例。

    解决问题：
    1. 重复用例：同一页面多个需求各自生成相同 actions（如多次 [navigate, click("批量导出")]）
    2. 不完整流程：原子需求"用户点击确定按钮"→ 生成 [navigate, click("确定")]，
       但"确定"在弹窗中，需要先点"批量导出"才能看到。合并后 actions 包含完整流程。

    通用规则（无业务硬编码）：
    1. 按 (navigate target_hint, scenario_type) 二维分组 → 保证 P01/N01/B01 不混组
    2. 同组用例合并 actions（去重）和 checks（去重），保持需求原始顺序
    3. 无 navigate 的用例保持原样
    4. 单用例页面组不合并
    5. 断言保持独立：每个需求的 checks 保留，只做去重，不修改内容 → 不影响断言准确性
    """
    if not all_intents or len(all_intents) <= 1:
        return all_intents

    # ── 按 (navigate URL, scenario_type) 二维分组 ──
    #   同一页面，同一场景（happy_path/error_path/boundary）才能合并
    #   P01（正向）不能和 N01（异常）合并，否则测试逻辑错误
    page_groups: dict[str, dict[str, list[dict]]] = {}
    ungrouped: list[dict] = []

    for intent in all_intents:
        if not isinstance(intent, dict):
            ungrouped.append(intent)
            continue
        actions = intent.get("actions", [])
        if not isinstance(actions, list) or len(actions) == 0:
            ungrouped.append(intent)
            continue
        # 提取第一个 navigate action 的 target_hint
        nav_url = None
        for a in actions:
            if isinstance(a, dict) and a.get("type") == "navigate":
                nav_url = str(a.get("target_hint", "")).strip()
                if nav_url:
                    break
        if not nav_url:
            ungrouped.append(intent)
            continue
        # 提取 scenario_type（P01/N01/B01 必须分在不同组）
        scenario_type = str(intent.get("scenario_type", "happy_path")).strip()
        # ★ 修复：加入 req_id + case_type 维度，不同需求/不同 type 的用例不合并
        #   type 维度（功能/UI/安全/性能/易用/兼容）是正交测试目标，
        #   不能合并为一个用例，否则丢失 type 覆盖维度
        req_id = str(intent.get("req_id", "")).strip()
        case_type = str(intent.get("type", "功能")).strip()
        # ★ 根因修复(2026-09-28)：合并键加入**用例变体**维度。
        #   根因: SQLi/XSS/CSRF 三条安全用例的 (scenario_type=security, req_id, type=安全)
        #     完全相同 → 被本函数合并成一条，主用例（I04 SQLi）的 checks 吸收其余两条
        #     的断言（CSRF token check 混入 SQLi）、I05/I06 用例整体消失 ——
        #     既丢安全覆盖维度，又让 SQLi 用例断言了不相干的 CSRF token（假 FAIL）。
        #   通用修复: 变体后缀（case_id 最后一个 "-" 段: I04/I05/I06/V01/PF02…）是
        #     用例测试目标的末级标识，必须进入合并键；同一需求内不同变体永不合并，
        #     不同需求同变体（group_key 已含 req_id）本就不该合并（dead code 兜底）。
        _case_variant = str(intent.get("case_id", "")).strip().rsplit("-", 1)[-1]
        group_key = f"{scenario_type}|{req_id}|{case_type}|{_case_variant}"
        # 分组
        if nav_url not in page_groups:
            page_groups[nav_url] = {}
        page_groups[nav_url].setdefault(group_key, []).append(intent)

    # ── 合并每个 (页面, 场景) 组 ──
    merged: list[dict] = []
    total_merged = 0
    total_groups = 0

    for nav_url, scenario_map in page_groups.items():
        for scenario_type, group in scenario_map.items():
            if len(group) <= 1:
                merged.extend(group)
                continue

            # 取第一个用例作为基础（保留其 case_id / req_id / title 等元数据）
            primary = dict(group[0])

            # 合并 actions：去重 (type, target_hint, value)，保持需求原始顺序
            merged_actions: list[dict] = []
            seen_actions: set = set()
            for intent in group:
                for a in intent.get("actions", []):
                    if not isinstance(a, dict):
                        continue
                    key = (
                        str(a.get("type", "")),
                        str(a.get("target_hint", "")),
                        str(a.get("value", "")),
                    )
                    if key not in seen_actions:
                        seen_actions.add(key)
                        merged_actions.append(dict(a))

            # 合并 checks：去重 (check, expect, track)
            # ★ 断言保持独立，只做去重，不修改断言内容 → 断言准确性不受影响
            merged_checks: list[dict] = []
            seen_checks: set = set()
            for intent in group:
                for c in intent.get("checks", []):
                    if not isinstance(c, dict):
                        continue
                    ck = (
                        str(c.get("check", "")),
                        str(c.get("expect", "")),
                        str(c.get("track", "")),
                    )
                    if ck not in seen_checks:
                        seen_checks.add(ck)
                        merged_checks.append(dict(c))

            # 更新 primary case
            primary["actions"] = merged_actions
            primary["checks"] = merged_checks

            # 记录合并信息（用于需求追踪和调试）
            merged_req_ids: list[str] = []
            for intent in group:
                rid = str(intent.get("req_id", "")).strip()
                if rid and rid not in merged_req_ids:
                    merged_req_ids.append(rid)

            primary["_merged_from_req_ids"] = merged_req_ids
            primary["_merged_from_case_ids"] = [
                str(i.get("case_id", "")) for i in group
            ]
            primary["_generation_source"] = "page_merged"
            primary["_page_merge_count"] = len(group)

            merged.append(primary)
            total_merged += len(group) - 1
            total_groups += 1

    result = merged + ungrouped
    if total_merged > 0:
        _log.info(
            "[Step3] ★ 页面合并: %d 条用例 → %d 条复合用例 (消除 %d 条重复，"
            "%d 个页面-场景组)",
            sum(
                sum(len(g) for g in s.values())
                for s in page_groups.values()
                if any(len(g) > 1 for g in s.values())
            ),
            sum(sum(1 for g in s.values() if len(g) > 1) for s in page_groups.values()),
            total_merged,
            total_groups,
        )

    return result
