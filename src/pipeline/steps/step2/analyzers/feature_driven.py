# -*- coding: utf-8 -*-
"""
需求文档切片驱动的需求拆解（v4 — 切片驱动，事实源是需求文档）

★ 架构变更(07-05→07-27 Phase1): 需求文档为骨架 + Figma 设计稿为唯一依据
  - 原架构：功能清单匹配 → 关键词锚定截取≤500字 snippet → 喂 AI（锚点无决策价值）
  - 当前架构：需求文档原文智能切片(≤600字/片) → 规则优先提取 + AI 补漏 → 切片覆盖率 + 两次机会
  - ★ Phase1(07-27): 需求文档是骨架（告诉引擎"要测什么"），Figma 设计稿是唯一依据（告诉引擎"长什么样、怎么交互"）
  - 每个切片提取时注入 Figma 设计稿上下文，AI 据此判断 expected_feedback 是否与设计一致
  - source_quote 必须从切片中逐字摘录，5a 在切片中校验（不是全文）

流程：
  1. _smart_chunk_requirement()      → 智能切片（章节优先+超长二次切+≤600字/片）
  2. _extract_from_chunks()          → 阶段1 规则提取（.breakdown_rules，确定性）
  3. 同上，阶段2                     → 仅对规则未覆盖的切片调用 AI 补漏
  4. 切片覆盖率检查 + 两次机会       → 未覆盖切片重试（最多 _CHUNK_RETRY_MAX 次）
  5. _deduplicate_triples()          → 按 (operation, expected_feedback) 去重合并
  6. _verify_source_quotes_in_chunks → 5a 子串匹配（校验 source_quote 在切片中存在）
  7. _check_reverse_coverage_v2()    → 基于 RAG 功能索引的逆向覆盖检查
  8. _apply_feature_labels()         → 功能标签（纯算法聚类，仅打标签不合并）
  9. _apply_completeness_check()     → 需求完整性检查（不阻断，生成建议供审核）

核心原则：
  - 需求文档是骨架（定义"要测什么"），Figma 设计稿是唯一依据（定义"预期行为"）
  - AI 只负责从 ≤600 字的切片中提取三元组（基础≤300字，跨模式合并后最大≤600字），不做全文理解
  - source_quote 强制锚定：必须能在切片中逐字找到
  - 切片覆盖率：未覆盖的切片给 AI 两次机会重新提取
  - 不同可观察反馈 = 不同原子需求
  - 同功能不同操作 = 不同原子需求（只打 feature_label 标签，不合并）
  - slice_id 指向 requirement_slices 单一事实源，_chunk_text 不再写入 payload_json
"""

import logging
from src.core.guarded import guard
import re
from typing import Any

_log = logging.getLogger(__name__)

# ── 从 constants 复用 _ai_brain 和 config ──
from .constants import _ai_brain, config, pipeline_config
from ._fd_preprocess import preprocess_and_extract_tables

# ── 矛盾检测 ──
from ._contradiction import detect_contradictions

# ★ 切片模块（智能切片 + 多技术融合相似度，纯算法）
from ._fd_chunking import _smart_chunk_requirement

# ★ 提取模块（规则优先 + AI 补漏，含词典加载和 _parse_json_output 导入）
from ._fd_extraction import _extract_from_chunks, _CHUNK_MAX_CHARS, _CHUNK_RETRY_MAX

# ★ 校验模块（5a 子串匹配 + 去重 + 逆向覆盖）
from ._fd_verification import (
    _deduplicate_triples,
    _verify_source_quotes_in_chunks,
    _check_reverse_coverage_v2,
)

# ★ 标签模块（功能标签聚类 + 切片持久化 + 完整性检查入口）
from ._fd_labels import (
    _apply_feature_labels,
    _persist_slices,
    _apply_completeness_check,
)


# ══════════════════════════════════════════════════
# 主入口：需求文档切片驱动的需求拆解（v4）
# ══════════════════════════════════════════════════


def feature_driven_breakdown(
    raw_text: str,
    ui_context: str = "",
    project_name: str = "",
    version_label: str = "",
    progress_callback: Any = None,
) -> dict[str, Any]:
    """需求文档切片驱动的需求拆解主入口。

    ★ 架构变更(07-05→07-27 Phase1): 需求文档为骨架 + Figma 设计稿为唯一依据
      - 原架构：功能清单匹配 → 关键词锚定截取≤500字 snippet → 喂 AI（锚点无决策价值）
      - 当前架构：需求文档原文智能切片(≤600字/片) → 规则优先提取 + AI 补漏 → 切片覆盖率 + 两次机会
      - ★ Phase1(07-27): 需求文档是骨架（告诉引擎"要测什么"），Figma 设计稿是唯一依据（告诉引擎"长什么样、怎么交互"）
      - 每个切片提取时注入 Figma 设计稿上下文，AI 据此判断 expected_feedback 是否与设计一致
      - source_quote 必须从切片中逐字摘录，5a 在切片中校验（不是全文）

    流程：
      1. _smart_chunk_requirement()      → 智能切片（章节优先+超长二次切+≤600字/片）
      2. _extract_from_chunks() 阶段1    → ★ 规则优先提取（.breakdown_rules，纯算法无 AI）
      3. _extract_from_chunks() 阶段2    → AI 补漏（仅处理规则未覆盖的切片）
      4. 切片覆盖率检查 + 两次机会       → 未覆盖切片重试（最多 _CHUNK_RETRY_MAX 次）
      5. _deduplicate_triples()          → 去重
      6. _verify_source_quotes_in_chunks → 5a子串匹配（校验source_quote在切片中）
      7. _check_reverse_coverage_v2()    → 逆向覆盖（基于rag_chunks功能索引）
      8. _apply_completeness_check()     → 需求完整性检查（不阻断，生成建议供审核）

    参数:
        raw_text:          原始需求文档全文
        ui_context:        UI 上下文（Figma 设计稿结构）
        project_name:      项目名
        version_label:     版本标签
        progress_callback: 进度回调 (done, total) → None，每个阶段完成时调用

    返回:
        {"success": True, "data": [req_dict, ...], "_stats": {...}}
    """
    # ★ 进度：内部共 7 个阶段（切片→提取→覆盖率→去重→校验→逆向覆盖+标签→编号）
    _TOTAL_PHASES = 7
    _phase = 0

    def _report(done: int, total: int = _TOTAL_PHASES):
        if progress_callback:
            with guard("_report: progress_callback(done, total)", log=_log):
                progress_callback(done, total)

    if not raw_text or not raw_text.strip():
        return {"success": False, "error": "需求文档为空"}

    _log.info(
        "[feature_driven] 开始切片驱动拆解 (project=%s, version=%s, text_len=%d)",
        project_name,
        version_label,
        len(raw_text),
    )

    # ── Step 0: 预处理（切片前过滤噪音 + 提取表格为独立切片）──
    cleaned_text, table_chunks = preprocess_and_extract_tables(raw_text)
    if not cleaned_text or not cleaned_text.strip():
        return {"success": False, "error": "预处理后需求文档为空"}

    # ── Step 1: 智能切片（章节优先+超长二次切+≤600字/片）──
    chunks = _smart_chunk_requirement(cleaned_text, max_chars=_CHUNK_MAX_CHARS)
    if not chunks:
        return {"success": False, "error": "智能切片失败（raw_text 为空或切片为空）"}

    # 把表格切片合并到 chunk 列表（表格不进入普通切片，避免 | 污染）
    if table_chunks:
        chunks.extend(table_chunks)

    # ── Step 1.1: 切片质量过滤（合并短切片 + 跳过纯噪音切片）──
    _PURE_NOISE_PATTERN = re.compile(
        r"^\s*(?:https?://\S+|!\[.*?\]\(.*?\)|(?:图片|图|截图|示意图|流程图|架构图|界面图|原型图|UI图|效果图|界面|原型)\s*(?:如下|所示|如下所示|参考|说明|见|参见)?\s*[:：]?)?\s*$",
        re.IGNORECASE,
    )
    _MIN_CHUNK_LEN = 10
    _MERGE_LIMIT = _CHUNK_MAX_CHARS * 2
    filtered_chunks = []
    noise_skipped = []
    merged_count = 0

    for chunk in chunks:
        text = chunk.get("text", "") or ""
        # 纯噪音切片 → 跳过
        if _PURE_NOISE_PATTERN.match(text):
            noise_skipped.append(chunk["chunk_id"])
            continue

        # 短切片（<10字）→ 尝试合并到前一片（避免换行导致语义断裂）
        if len(text.strip()) < _MIN_CHUNK_LEN and filtered_chunks:
            prev = filtered_chunks[-1]
            merged = prev["text"] + "\n" + text
            if len(merged) <= _MERGE_LIMIT:
                prev["text"] = merged
                # ★ 保留原始 chunk_id 列表，source_quote 校验按原始 ID 查找
                prev.setdefault("merged_chunk_ids", [prev["chunk_id"]]).append(
                    chunk["chunk_id"]
                )
                merged_count += 1
                continue
            # 前一片满了无法合并 → 保留（短切片可能是有意义的标题/编号）

        filtered_chunks.append(chunk)

    if noise_skipped:
        _log.info("[feature_driven] 切片过滤: 跳过 %d 个噪音切片", len(noise_skipped))
    if merged_count:
        _log.info("[feature_driven] 切片过滤: 合并 %d 个短切片到前一片", merged_count)

    chunks = filtered_chunks
    if not chunks:
        return {"success": False, "error": "所有切片均为噪音，过滤后为空"}

    _phase += 1
    _report(_phase)  # 阶段1: 切片完成

    # ── Step 2: 首次切片提取（每片一次AI调用）──
    all_reqs, covered_ids, ai_call_count, rule_count = _extract_from_chunks(
        chunks, ui_context, retry_attempt=0
    )

    _phase += 1
    _report(_phase)  # 阶段2: 首次AI提取完成

    # ── Step 3: 切片覆盖率检查 + 两次机会 ──
    # 未覆盖的切片给AI两次机会重新提取
    coverage_stats = {
        "total_chunks": len(chunks),
        "covered_chunks": len(covered_ids),
        "uncovered_chunks": len(chunks) - len(covered_ids),
        "retry_rounds": 0,
    }

    uncovered_chunks = [c for c in chunks if c["chunk_id"] not in covered_ids]

    for retry_round in range(1, _CHUNK_RETRY_MAX + 1):
        if not uncovered_chunks:
            break

        coverage_stats["retry_rounds"] = retry_round
        _log.info(
            "[feature_driven] 切片覆盖率: %d/%d (%.1f%%), 未覆盖 %d 片, 第 %d 次重试",
            len(covered_ids),
            len(chunks),
            100.0 * len(covered_ids) / max(len(chunks), 1),
            len(uncovered_chunks),
            retry_round,
        )

        retry_reqs, retry_covered, retry_ai_calls, _retry_rule = _extract_from_chunks(
            uncovered_chunks, ui_context, retry_attempt=retry_round
        )
        ai_call_count += retry_ai_calls

        if retry_reqs:
            all_reqs.extend(retry_reqs)
        if retry_covered:
            covered_ids.update(retry_covered)

        uncovered_chunks = [
            c for c in uncovered_chunks if c["chunk_id"] not in covered_ids
        ]

    # 最终覆盖率
    coverage_stats["covered_chunks"] = len(covered_ids)
    coverage_stats["uncovered_chunks"] = len(uncovered_chunks)
    coverage_stats["coverage_rate"] = round(
        100.0 * len(covered_ids) / max(len(chunks), 1), 1
    )

    _log.info(
        "[feature_driven] 切片覆盖率最终: %d/%d (%.1f%%), 未覆盖 %d 片",
        len(covered_ids),
        len(chunks),
        coverage_stats["coverage_rate"],
        len(uncovered_chunks),
    )
    # ★ H4-1: 记录切片覆盖率指标
    with guard("feature_driven_breakdown: from src.backend.state import _threa", log=_log):
        from src.backend.state import _thread_run_id
        from src.extensions.metrics_collector import metrics_collector

        _rid = getattr(_thread_run_id, "value", "") or ""
        if _rid:
            metrics_collector.record_slice_coverage(
                config.project_name,
                _rid,
                total_slices=len(chunks),
                covered_slices=len(covered_ids),
                retry_rounds=coverage_stats.get("retry_rounds", 0),
            )

    if not all_reqs:
        _log.error(
            "[feature_driven] 规则与 AI 在所有 %d 个切片中均未提取到需求（已重试 %d 轮）",
            len(chunks),
            coverage_stats.get("retry_rounds", 0),
        )
        _log.error(
            "[feature_driven] 可能原因: 1) 需求文档为空或仅包含非需求内容 2) 文档格式无任何规则可识别且 AI 响应异常 3) Step1 数据源未拉取到需求正文"
        )
        return {
            "success": False,
            "error": "规则与 AI 在所有切片中均未提取到需求（共 %d 片，已重试 %d 轮）"
            % (len(chunks), coverage_stats.get("retry_rounds", 0)),
        }

    _phase += 1
    _report(_phase)  # 阶段3: 切片覆盖率检查 + 重试完成

    # ── Step 4: 去重（_deduplicate_triples 自动合并相同 (op, fb)，保留更可信的 L1>L2>L3）──
    all_reqs = _deduplicate_triples(all_reqs)

    _phase += 1
    _report(_phase)  # 阶段4: 去重完成

    # ── Step 5: 5a子串匹配（校验source_quote在切片中，不是在原文中）──
    all_reqs, verify_stats = _verify_source_quotes_in_chunks(all_reqs, chunks)

    _phase += 1
    _report(_phase)  # 阶段5: 原文核对完成

    # ── Step 6: 逆向覆盖（基于rag_chunks功能索引，保留作为统计）──
    coverage_result = _check_reverse_coverage_v2(all_reqs, project_name, version_label)

    # ── Step 7: 功能标签（★ 新增 07-05g）
    #    给每个三元组打 feature_label 字段（按 operation 语义聚类，纯算法无 AI）
    #    设计：三元组独立保留，只附加功能标签（同功能不同操作是独立原子需求）
    #    用途：Step3 按功能分组生成用例 + 前端审核按功能筛选展示
    feature_label_stats = _apply_feature_labels(all_reqs)

    # ── Step 7.2: 功能点语义聚合去重（★ T4 流水线强化专项，2026-08-24）
    #    在 _deduplicate_triples 精确去重之上补语义级合并：同义功能点
    #    （如「新增商品」vs「创建商品」）聚合为一条；合并依据可解释
    #    （复用 synonym_library / capability_cache 归一化，无 DB 降级内置同义词）。
    #    ★ 保持原子需求不变量：仅当 operation 语义相似且 expected_feedback
    #      归一化相等/互含时才合并；输出 _stats.semantic_merge 供前端展示。
    semantic_merge_stats: dict[str, Any] = {
        "before": len(all_reqs),
        "after": len(all_reqs),
        "merged_count": 0,
        "logs": [],
    }
    with guard("feature_driven_breakdown: from src.pipeline.steps.step2.analyz", log=_log, level=logging.WARNING):
        from src.pipeline.steps.step2.analyzers._fd_semantic_dedup import (
            semantic_merge_features,
        )

        all_reqs, semantic_merge_stats = semantic_merge_features(all_reqs)
        if semantic_merge_stats["merged_count"]:
            _log.info(
                "[feature_driven] T4 语义聚合: %d 条 → %d 条（合并 %d 条）",
                semantic_merge_stats["before"],
                semantic_merge_stats["after"],
                semantic_merge_stats["merged_count"],
            )

    # ── Step 7.5: 切片持久化（★ 新增 07-05h）
    #    将 Step2 内存中的 chunks 持久化到 requirement_slices 表
    #    用途：关系链路查看（从需求/用例/文档任意节点展开到切片级别）
    #    设计：
    #      - 切片ID稳定（req_raw:0 / req_raw:pre:1 / req_raw:pre:1:2）
    #      - 切片带 feature_label（Phase4聚类结果，按 slice_id 关联）
    #      - 切片带 needs_review 标记（相似度合并触发）
    #    ★ 关联关系：需求.slice_id → requirement_slices.slice_id（一一对应或多对一）
    slice_persist_count = _persist_slices(chunks, all_reqs, project_name, version_label)

    _phase += 1
    _report(_phase)  # 阶段6: 逆向覆盖 + 功能标签 + 切片持久化完成

    # ── Step 7.6: 需求矛盾检测（★ 新增 — 纯算法，零 AI 调用）──
    #    检测同功能下需求之间的语义矛盾（否定词/反义词对）和自矛盾
    #    矛盾信息写入 _contradiction_with 字段，不阻断流程，供审核使用
    contradiction_stats = {"contradiction_pairs": 0, "self_contradictions": 0}
    with guard("feature_driven_breakdown: all_reqs, contradiction_stats = dete", log=_log, level=logging.WARNING):
        all_reqs, contradiction_stats = detect_contradictions(all_reqs)

    # ── Step 8: 需求完整性检查（纯算法，不阻断拆解，生成建议供需求方审核）──
    # ★ 检查结果附加到 req["completeness_issues"]，写入 payload_json
    # ★ 不阻断流程：即使有 issues，需求仍正常入库
    completeness_stats = _apply_completeness_check(all_reqs)

    # ── 编号 ──
    for i, r in enumerate(all_reqs):
        r["req_id"] = f"REQ-{i + 1:03d}"

    # ── 依赖根因修复(2026-08-31)：主流程单轮路径同样解析依赖 ──
    #   ★ 此前依赖解析只在 feature_driven_breakdown_consensus 封装中调用，
    #     而流水线实际走的是本单轮入口（step2.py），导致 AI 输出的依赖局部
    #     编号在语义合并 + 全局重编号后失效，dependency_req_id 恒空/无效，
    #     导航树依赖随之为空。此处补上：把 `_dep_req_hint` 描述线索就近解析
    #     成全局重编号后的最终 req_id（纯算法、确定性、通用，不再调 AI）。
    all_reqs = _resolve_dependency_req_ids(all_reqs)

    # ── 跨片横切依赖兜底(2026-08-31)：与 consensus 封装路径保持一致。
    #   ★ 此前该兜底只挂在 feature_driven_breakdown_consensus 上，而流水线实际走
    #     的是本单轮入口（step2.py），导致跨片横切依赖（鉴权/登录被其它切片需求依赖）
    #     在真实落库路径上恒为空。此处补上，纯算法、确定性、通用，不再调 AI。
    all_reqs = _infer_cross_slice_dependencies(all_reqs)

    # ── Step 8.5: 需求歧义主动上报（★ A1 新增 — 纯算法，零 AI 调用）──
    #    在用例生成前主动产出结构化歧义报告（歧义点/影响/建议澄清问题），
    #    而非静默生成可能错的用例。不阻断流程：仅附加 req["ambiguity_items"]
    #    + 输出 ambiguity_report，由调用方读 should_block_generation 决定是否拦截。
    #    ★ 置于编号之后，使歧义证据能引用稳定的 REQ-XXX 编号
    #    [未复核-需 GLM/DeepSeek 把关]
    ambiguity_report: dict[str, Any] = {}
    with guard("feature_driven_breakdown: from src.pipeline.steps.step2.ambigu", log=_log, level=logging.WARNING):
        from src.pipeline.steps.step2.ambiguity_report import apply_ambiguity_report

        ambiguity_report = apply_ambiguity_report(
            all_reqs,
            source=f"{project_name}@{version_label}" if project_name else "unknown",
        )

    # ── Step 8.6: 澄清会话持久化（★ T3 流水线强化专项）
    #    高危歧义项 → 持久化为可交互澄清会话（clarification_sessions），
    #    供 clarify.html 问答、回答后解除 should_block_generation 闸门。
    #    纯增量：异常/无 DB 环境静默跳过，不阻断拆解。
    with guard("feature_driven_breakdown: from src.pipeline.steps.step2.ambigu", log=_log, level=logging.WARNING):
        from src.pipeline.steps.step2.ambiguity_report import persist_clarification_sessions

        _clarify_sessions = persist_clarification_sessions(
            ambiguity_report,
            project=project_name,
            version_id=version_label,
            run_id="",
        )
        if _clarify_sessions:
            _log.info(
                "[feature_driven] 已持久化澄清会话 %d 个 (project=%s)",
                len(_clarify_sessions),
                project_name,
            )

    _phase += 1
    _report(_phase)  # 阶段7: 编号完成，全部阶段结束

    # ★ 三级信任模型统计：L1 显式 / L2 通用隐性 / L3 业务隐性候选
    _trust_l1 = sum(1 for r in all_reqs if r.get("is_implicit") == 0)
    _trust_l2 = sum(1 for r in all_reqs if r.get("is_implicit") == 1)
    _trust_l3 = sum(1 for r in all_reqs if r.get("is_implicit") == 2)

    _stats = {
        "ai_calls": ai_call_count,
        "rule_extractions": rule_count,
        "chunk_count": len(chunks),
        "covered_chunks": len(covered_ids),
        "uncovered_chunks": len(uncovered_chunks),
        "coverage_rate": coverage_stats["coverage_rate"],
        "retry_rounds": coverage_stats["retry_rounds"],
        "verify_stats": verify_stats,
        "coverage_stats": coverage_result.get("stats", {}),
        "total_reqs": len(all_reqs),
        "trust_l1_explicit": _trust_l1,
        "trust_l2_implicit_common": _trust_l2,
        "trust_l3_implicit_business": _trust_l3,
        "feature_label_stats": feature_label_stats,
        # ★ T4 流水线强化专项: 语义聚合去重统计（before/after/logs）
        "semantic_merge": semantic_merge_stats,
        "completeness_stats": completeness_stats,
        "contradiction_stats": contradiction_stats,
        # ★ A1: 需求歧义主动上报（纯增量字段，异常时为空 dict 不影响既有消费方）
        "ambiguity_report": ambiguity_report,
        "ambiguity_stats": {
            "ambiguity_total": ambiguity_report.get("ambiguity_total", 0),
            "blocking_total": ambiguity_report.get("blocking_total", 0),
            "should_block_generation": ambiguity_report.get(
                "should_block_generation", False
            ),
            "by_type": ambiguity_report.get("by_type", {}),
        },
        # ★ 修复: step2.py 从 _stats 读取 feature_count 但字段缺失 → 始终为 0
        "feature_count": feature_label_stats.get("feature_count", 0),
        # matched_count/undocumented_count 由 _query_multi_source_stats 单独查询，
        # 不在 _stats 中（step2.py 从 _multi_source_stats 获取，此处不重复）
    }

    # ★ v4.0 (2026-07-12): 双向覆盖检查（实时计算，接入流水线）
    #   前向：文档章节 → 切片覆盖 | 反向：需求 → 切片溯源
    _bidirectional_result = {}
    with guard("feature_driven_breakdown: from src.pipeline.step2_coverage imp", log=_log, level=logging.WARNING):
        from src.pipeline.step2_coverage import run_bidirectional_coverage_check

        _bidirectional_result = run_bidirectional_coverage_check(
            all_reqs, chunks, covered_ids
        )
        _stats["bidirectional_coverage"] = _bidirectional_result

    _log.info(
        "[feature_driven] 拆解完成: %d 条需求 (规则=%d, AI调用=%d, 切片 %d/%d, L1=%d, L2=%d, L3=%d)",
        len(all_reqs),
        rule_count,
        ai_call_count,
        len(covered_ids),
        len(chunks),
        _trust_l1,
        _trust_l2,
        _trust_l3,
    )

    # ★ 主题一致性门禁（不阻断，仅标记 out_of_scope + 统计）
    #   在全部提取/去重/校验完成后、返回前，对每条需求与文档主题做语义一致性打分。
    with guard("feature_driven_breakdown: from ._fd_theme_gate import apply_th", log=_log, level=logging.WARNING):
        from ._fd_theme_gate import apply_theme_gate

        all_reqs, theme_gate_stats = apply_theme_gate(
            all_reqs,
            cleaned_text,
            project_name=project_name,
            version_label=version_label,
        )
        _stats["theme_gate"] = theme_gate_stats
        if theme_gate_stats.get("out_of_scope"):
            _log.info(
                "[feature_driven] 主题门禁: %d 条需求疑似偏离整体主题",
                theme_gate_stats["out_of_scope"],
            )

    return {"success": True, "data": all_reqs, "_stats": _stats}


# ══════════════════════════════════════════════════
# ★ 多轮共识提取（长期方案：消除 AI 提取完整性波动）
# ══════════════════════════════════════════════════


def _resolve_dependency_req_ids(merged_reqs: list[dict]) -> list[dict]:
    """★ 依赖根因修复(2026-08-31)：把 AI 提供的依赖目标描述线索解析为最终 req_id。

    背景：Step2 并行按 chunk 各出其局部 req_id，语义合并去重后再被全局重编号，
    故 AI 直接给的 REQ-xxx 依赖号必然失效。这里改用抽取时捕获的 `_dep_req_hint`
    （被依赖需求的描述/操作），在最终编号集内做确定性就近匹配，通用、无需再调 AI。

    匹配策略：
      1. 优先跨模块（同模块高度相似的描述多为语义去重残留，不应判定为依赖）；
      2. 以被依赖描述与候选 (description+operation) 的 token 重合率评分；
      3. 仅当唯一且高于阈值(0.5)才写 dependency_req_id，否则保持空（诚实不硬造）。

    Args:
        merged_reqs: 已全局重编号的需求列表（含 _dep_req_hint）。

    Returns:
        修正 dependency_req_id 后的同一列表。
    """
    texts_by_id: dict[str, str] = {}
    for r in merged_reqs:
        rid = str(r.get("req_id") or "")
        if not rid:
            continue
        desc = str(r.get("description") or "").strip()
        op = str(r.get("operation") or "").strip()
        texts_by_id[rid] = (desc + " " + op).lower()

    def _norm(t: str) -> set[str]:
        """生成用于相似度比对的文本特征集。

        ★ 根因修复(2026-08-31)：原实现用 re.split(r'\\W+') 按分隔符分词，对连续中文
        （如"系统提供用户登录接口"）整句只产出 1 个 token，导致 hint 与候选做 Jaccard
        时交集被分母（候选更长、token 更多）稀释，评分严重低估、依赖恒为空。
        现改为「字符 2-gram + 英文词」混合特征（对拉丁词保留整词），使中文子串也能
        产生大量重叠特征，评分对部分/整体包含关系都鲁棒。
        """
        import re as _re
        s = (t or "").lower().strip()
        if not s:
            return set()
        feats: set[str] = set()
        # ── 中文连续串 → 字符 bigram（表意文字无天然分词，2-gram 使部分包含也有重叠）
        for m in _re.finditer(r"[\u2e80-\u9fff]+", s):
            cjk = m.group()
            if len(cjk) >= 2:
                feats |= {cjk[i:i + 2] for i in range(len(cjk) - 1)}
            else:
                feats.add(cjk)
        # ── 拉丁/数字词块 → 保留整词（先剔除中文，避免被 \w 当整块）
        _latin = _re.sub(r"[\u2e80-\u9fff]+", " ", s)
        feats |= {w for w in _re.split(r"[^\w]+", _latin) if len(w) > 1}
        return feats

    for r in merged_reqs:
        hint = str(r.get("_dep_req_hint") or "").strip()
        if not hint:
            r["dependency_req_id"] = ""
            continue
        me_id = str(r.get("req_id") or "")
        my_mod = str(r.get("module") or "")
        hint_tokens = set(_norm(hint))
        if not hint_tokens:
            r["dependency_req_id"] = ""
            continue
        best_id, best_score = "", 0.0
        tied = False
        for cand_id, cand_text in texts_by_id.items():
            if cand_id == me_id:
                continue
            cand_mod = ""
            for c in merged_reqs:
                if str(c.get("req_id")) == cand_id:
                    cand_mod = str(c.get("module") or "")
                    break
            if my_mod and cand_mod == my_mod:      # 同模块高度相似≈重复，不判为依赖
                continue
            cand_tokens = set(_norm(cand_text))
            inter = hint_tokens & cand_tokens
            if not inter:
                continue
            score = len(inter) / max(1, len(hint_tokens | cand_tokens))
            if score > best_score + 1e-6:
                best_score, best_id, tied = score, cand_id, False
            elif abs(score - best_score) < 1e-6 and score > 0:
                tied = True
        r["dependency_req_id"] = best_id if (best_score >= 0.5 and not tied) else ""
    return merged_reqs


# ── 跨片横切依赖无语义关键词词典 ──────────────────────────────
# 横切能力锚点：命中即视为「提供横切认证/鉴权能力」，可被其它需求依赖。
# 采用高置信「登录/鉴权服务」短语（如 /login、auth/login、获取token），
# 避免把「登录用户POST图书」这类实际上是"依赖者"的需求误判为锚点。
_CROSS_ANCHOR_HINTS = (
    "auth/login", "/login", "获取token", "获取令牌", "颁发token",
    "token鉴权", "登录接口", "登录服务", "提供登录", "登录系统",
    "用户登录", "身份验证", "发放token",
)

# 依赖者线索：描述/条件/source 中出现任一线索，视为「需要先具备某横切能力」。
# 覆常见业务：需先登录、登录后才能操作、鉴权后放行、权限/角色校验等。
_CROSS_DEP_MARKERS = (
    "登录用户", "需登录", "需先登录", "需要登录", "登录后才", "登录后",
    "需鉴权", "需先鉴权", "需要鉴权", "鉴权后", "带上token", "携带token",
    "携带令牌", "检测到令牌", "登录口令", "已登录", "登录过的",
    "通过token", "凭token", "凭token", "携带token",
    # 角色/权限语义：文档语境中提到"管理员/普通用户/拥有权限/无权限"执行操作，
    #   通常隐含需先通过认证登录获取身份；仅当存在唯一登录锚点时才关联（诚实不硬猜）。
    "管理员", "普通用户", "拥有权限", "无权限", "权限校验", "权限验证",
    "未登录", "未授权", "无有效token",
)


def _norm_text_blob(req: dict) -> str:
    """把单条需求的关键文本字段拼成便于子串匹配的低串（用于规则锚点识别）。"""
    parts = [
        str(req.get("operation") or ""),
        str(req.get("description") or ""),
        str(req.get("condition_expr") or ""),
        str(req.get("source_quote") or ""),
    ]
    return " ".join(parts).lower()


def _infer_cross_slice_dependencies(merged_reqs: list[dict]) -> list[dict]:
    """★ 跨片横切依赖兜底(2026-08-31)。

    背景：Step2 按 chunk 并行提取，依赖只允许「本次输出内」引用；若被依赖的横切
    能力需求（如登录/鉴权）落在其它切片，AI 无法填 dependency_req_id → hint 为空
    → `_resolve_dependency_req_ids` 也解析不到，横切依赖全丢。

    本函数在 AI 语义解析之后，用通用规则做跨片补齐：
      1. 找出全部「横切能力锚点」需求（操作/描述含登录/鉴权/权限/token 等语义）；
      2. 对尚未解析出依赖、且文本出现「需登录/鉴权/登录用户/携带token」等显式依赖
         线索的需求，建立到锚点的依赖；
      3. 仅当依赖者不是锚点自身、且仅有唯一锚点时落 dependency_req_id（唯一才可信，
         多锚点时留给人工或保持空，诚实不硬猜）。

    通用性：锚点/线索均为跨行业通用语义（登录/鉴权/权限/会话），不绑定任何靶子
    业务，可作为面向"横切能力"依赖的通用兜底。仅补齐 AI 未覆盖的跨片情况。
    """
    if not merged_reqs:
        return merged_reqs

    anchors: list[dict] = []
    for r in merged_reqs:
        # ★ 锚点判据：以 operation（操作本体）为主，operation 描述的是"做什么"，
        #   更贴近"是否提供该横切能力"；description/source 可能只是"提到"鉴权词
        #   （如"提交图书信息…鉴权通过后创建"），那表示依赖而非提供，不应误判为锚点。
        op = str(r.get("operation") or "").lower()
        if any(_h.lower() in op for _h in _CROSS_ANCHOR_HINTS):
            anchors.append(r)

    if not anchors:
        return merged_reqs

    for r in merged_reqs:
        # 仅补齐尚无依赖的
        if (r.get("dependency_req_id") or "").strip():
            continue
        me_id = str(r.get("req_id") or "")
        me_blob = _norm_text_blob(r)
        # 自身是锚点 → 不再依赖其它锚点（避免登录→登录 自指）
        if any(_a.get("req_id") == me_id for _a in anchors):
            continue
        # 是否显式表达"需先具备横切能力"
        if not any(_m.lower() in me_blob for _m in _CROSS_DEP_MARKERS):
            continue
        # 找被依赖的锚点（优先跨模块，排除自身模块；同模块锚点多为同一业务域邻居，
        #   如"账号预置"依赖"登录"是反向的弱相关，不臆造，只有在无跨模块锚点时兜底）
        my_mod = str(r.get("module") or "")
        candidates = [
            a for a in anchors
            if a.get("req_id") != me_id
            and str(a.get("module") or "") != my_mod
        ]
        # 兜底：本业务域内无其它模块锚点时，才允许依赖唯一同模块锚点
        if not candidates:
            candidates = [a for a in anchors if a.get("req_id") != me_id]
        if not candidates:
            continue
        # 唯一候选才可信
        if len(candidates) == 1:
            r["dependency_req_id"] = str(candidates[0].get("req_id") or "")
            continue
        # 多候选：优先选 operation 含"登录"语义的锚点（登录接口最典型、可信度最高）
        login_ids = [
            a for a in candidates
            if any(h.lower() in str(a.get("operation") or "").lower() for h in _CROSS_ANCHOR_HINTS)
        ]
        if len(login_ids) == 1:
            r["dependency_req_id"] = str(login_ids[0].get("req_id") or "")
    return merged_reqs
