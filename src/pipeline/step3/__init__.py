# -*- coding: utf-8 -*-
"""Step 3: AI 建模测试用例（含 UI 上下文 + RAG + Traceability）
从 pipeline_scheduler.py 拆分出。

[B1.3 分层说明] 本包属于**编排层**
==================================
注意区分两个同名目录，它们**不是重复代码**，职责不同：
  - ``src/pipeline/step3/``       ← **本包**。编排层：`step3_model_cases()` 决定
    "Step3 这一步做哪些子动作、按什么顺序、何时暂停等待人工审核"。
  - ``src/pipeline/steps/step3/``   能力层：Step3 的具体能力实现
    （xmind_designer / smoke_generator 等，由文档中心 API 直接调用）。

依赖方向应为「本包（编排层） → ``steps/step3/``（能力层）」，反向禁止。
⚠️ 现状：``steps/step3/`` 与 ``steps/step4/`` 中共有反向 import 本包的既有技术债
（详见 ``src/pipeline/__init__.py`` 的分层说明）。本轮（P0）**未搬迁**，
理由是搬迁会大范围改动 import 链、风险高于收益。**新增代码请勿再增加反向依赖。**

模块化拆分说明（v2）：
  - _case_quality.py      → summarize_case_modeling_quality
  - _security.py            → _sanitize_credentials
  - _requirement_selector.py → _read_step3_requirements, _select_requirements_for_case_modeling,
                               _mark_stale_intents_for_changed_requirements, _mark_modeled_intents_for_requirement
  - _fallback_cases.py    → _build_rule_fallback_cases
  - _version_check.py       → _check_and_confirm_version
  - _modeling_engine.py     → _model_full_intents
"""

import logging
from src.core.guarded import guard
from typing import Any

_log = logging.getLogger(__name__)

# ── 常量定义（避免魔法数字）──
_DEFAULT_MIN_INTENTS_PER_REQ = 3  # 每条需求至少需要的测试用例数才算"已覆盖"
_DEFAULT_QUICK_MODE_LIMIT = 3  # 快速模式最多处理的需求条数
_MAX_CROSS_DEPS_DISPLAY = 20  # 跨模块依赖展示上限

# ── 从子模块 re-import（保持向后兼容）──
from ._intent_quality import summarize_case_modeling_quality
from ._security import _sanitize_credentials
from ._requirement_selector import (
    _read_step3_requirements,
    _select_requirements_for_case_modeling,
    _mark_stale_intents_for_changed_requirements,
    _mark_modeled_intents_for_requirement,
)
from ._fallback_cases import _build_rule_fallback_cases
from ._version_check import _check_and_confirm_version
from ._modeling_engine import _model_full_intents


def step3_model_cases(
    self, quick: bool = False, force: bool = False, run_id: str = None
) -> dict[str, Any]:
    """调用 AI 为每条需求建模测试用例 → 写入新版本 Validation_Intent-<timestamp>.xlsx。
    ★ v6: 集成 RAG 精准检索 + Traceability 追溯链路 + 增强覆盖率。
    ★ force=True: 忽略覆盖率，强制全量重新生成。"""
    _log.info(
        "  [DEBUG] step3_model_cases ENTRY: run_id=%s, quick=%s, force=%s",
        run_id,
        quick,
        force,
    )
    if run_id:
        _log.info("  [run_id=%s] Step 3 开始: AI 建模测试用例", run_id)

    # ★ 确保 config 已加载当前项目（防止直接调用时 project_name 为空导致 MySQL 查询返回 0 条）
    from src.core.config_loader import config

    if hasattr(self, "_project_name") and self._project_name:
        if config.project_name != self._project_name:
            config.reload(self._project_name)
            _log.info("  [Config] 已切换到项目: %s", self._project_name)

    # ★ P0: 断点续跑 — 从 MySQL 恢复 Step1 内存上下文
    from src.pipeline.step2_context_restore import _restore_step1_contexts

    _restore_step1_contexts(self)

    from src.ai.ai_client import ai_brain
    from src.core.excel_handler import excel_handler

    all_reqs, _req_source = _read_step3_requirements(excel_handler)
    # ★ B-8-3: 数据完整性校验
    from src.pipeline.utils.data_integrity_checker import validate_step_input

    _integrity3 = validate_step_input(
        "requirements_breakdown",
        all_reqs,
        required_fields=["description", "module"],
    )
    if not _integrity3["valid"]:
        _log.error(
            "[Step3] Step2 产出数据完整性校验失败: %d/%d records 有问题",
            _integrity3["failed"],
            _integrity3["total"],
        )
    # ★ 基于 JSON 追踪器（intent_requirement_map.json）精确判断哪些需求缺测试用例
    from src.pipeline.utils.case_requirement_tracker import case_requirement_tracker

    # ★ MySQL 事实源校验：test_cases 为空时，追踪器数据已失效，自动清零
    from src.pipeline.utils.case_store import case_store

    actual_intents = case_store.list_intents(include_virtual_inherited=False)
    actual_count = len([c for c in actual_intents if c.get("case_id")])
    tracker_count = len(case_requirement_tracker)
    if actual_count == 0 and tracker_count > 0:
        _log.warning(
            "MySQL test_cases 为空（追踪器仍有 %d 条记录），自动清空追踪器",
            tracker_count,
        )
        case_requirement_tracker.clear()
    elif actual_count > 0:
        actual_case_ids = {
            str(c.get("case_id", "") or "").strip()
            for c in actual_intents
            if c.get("case_id")
        }
        try:
            tracker_map = case_requirement_tracker._load_map()
        except Exception as _e:
            _log.debug("[__init__] 非关键异常: %s", _e, exc_info=True)
            tracker_map = {}
        tracker_case_ids = {str(k).strip() for k in tracker_map.keys() if k}
        needs_rebuild = tracker_count == 0 or actual_case_ids != tracker_case_ids
        if needs_rebuild:
            _log.warning(
                "case_requirement_mapping 与 test_cases 不一致，自动重建映射: intents=%d mapping=%d",
                len(actual_case_ids),
                len(tracker_case_ids),
            )
            case_requirement_tracker.clear()
            _log.info(
                "从 MySQL test_cases 重建测试用例追踪器（%d 条测试用例）...",
                actual_count,
            )
        for c in actual_intents:
            cid = c.get("case_id", "")
            rid = c.get("req_id", "")
            if cid and rid:
                case_requirement_tracker.add(cid, rid)
        # save() 已是 no-op（MySQL 实时写入），无需显式调用

    if force:
        _log.info("强制模式：重置旧意图资产、意图映射和需求建模标记，避免新旧结果混用")
        try:
            from src.pipeline.utils.requirement_store import requirement_store

            if not case_store.clear_project_intents():
                _log.error("强制重跑前清理 test_cases 失败")
                return {"success": False, "error": "强制重跑前清理 test_cases 失败"}
            if not requirement_store.reset_all_is_generated():
                _log.error("强制重跑前重置 requirements 状态失败")
                return {
                    "success": False,
                    "error": "强制重跑前重置 requirements 状态失败",
                }
            if not case_requirement_tracker.clear():
                _log.error("强制重跑前清理 intent→req 映射失败")
                return {"success": False, "error": "强制重跑前清理 intent→req 映射失败"}
        except Exception as e:
            _log.error("强制重跑初始化失败: %s", str(e)[:120])
            return {"success": False, "error": "强制重跑初始化失败: %s" % str(e)[:120]}

        # ★ v2.2 修复 (2026-07-07): 不再清空 rag_chunks 表。
        # rag_chunks 是 Step1/Step2 建立的 API/DOM/Figma 索引，Step3 的规则提取
        # _match_apis_from_rag_chunks 依赖它做语义匹配。清空会导致 55 条无 trace_links
        # 的需求全部走 AI（烧 token）。
        # 如需重建 RAG，请重跑 Step1 或在文档中心重建索引。
        _log.info("强制模式：保留 rag_chunks 索引（规则提取的语义匹配依赖它）")

    intent_count_by_req = case_requirement_tracker.count_by_req()

    if force:
        _log.info("强制模式：忽略覆盖率，全量重新生成")
        ungen = all_reqs
        already_gen = 0
    else:
        # 需求至少要有 3 条测试用例才算"已覆盖"
        MIN_INTENTS_PER_REQ = getattr(
            config, "min_intents_per_req", _DEFAULT_MIN_INTENTS_PER_REQ
        )
        ungen, already_gen, change_stats = _select_requirements_for_case_modeling(
            all_reqs, intent_count_by_req, MIN_INTENTS_PER_REQ, force=False
        )
        if already_gen > 0:
            _log.info(
                "已有足够测试用例: %d 条需求（≥%d条/需求），待建模: %d 条",
                already_gen,
                MIN_INTENTS_PER_REQ,
                len(ungen),
            )
        _log.info(
            "版本化生成队列: added=%d modified=%d impacted=%d unchanged=%d removed=%d unknown=%d",
            change_stats.get("added", 0),
            change_stats.get("modified", 0),
            change_stats.get("impacted", 0),
            change_stats.get("unchanged", 0),
            change_stats.get("removed", 0),
            change_stats.get("unknown", 0),
        )

        # ★ 即使全量测试用例不需要建模，测试设计导出 + 冒烟意图导出仍可独立运行（与覆盖率无关）
    skip_full = not ungen or len(ungen) == 0
    if skip_full:
        _log.info(
            "所有需求已有足够测试用例，跳过全量意图建模（测试设计导出 + 冒烟意图导出请从文档中心独立触发）"
        )
    else:
        if quick and len(ungen) > 3:
            ungen = ungen[
                : getattr(config, "quick_mode_limit", _DEFAULT_QUICK_MODE_LIMIT)
            ]
            _log.info("快速模式: 仅处理前 %d 条需求", _DEFAULT_QUICK_MODE_LIMIT)

    # 构建数据库上下文
    data_context = self._build_db_context()
    target_url = ""
    with guard("step3_model_cases: from src.core.config_loader import c", log=_log, level=logging.WARNING):
        from src.core.config_loader import config

        target_url = config.target_url

    project_context = ""
    if target_url:
        project_context = "目标系统 URL: %s" % target_url
    # ★ DB Schema 数据已通过 RAG 检索（rag_context）按需注入，不再全量塞入 project_context
    # 同 YApi 问题：全量表结构 + 样例数据被裁剪到 1000 字符后实际无效
    if data_context:
        _log.info(
            "DB Schema: 数据已就绪 (%d 字符)，将通过 RAG 检索按需提供给 AI",
            len(data_context),
        )

    # ★ 跨Step MySQL通信：从 source_documents 表读取 Step1 上下文（禁止内存变量跨Step）
    from src.pipeline.step1_context_loader import load_step1_contexts

    _step1_ctx = load_step1_contexts(self._project_name)

    ui_ctx = _step1_ctx["ui_context"]
    test_accounts = _step1_ctx["test_accounts_context"]
    api_docs = _step1_ctx["api_docs_context"]

    if ui_ctx:
        _log.info("UI 上下文: 已注入 Figma 设计稿结构 (%d 字符)", len(ui_ctx))
    if test_accounts:
        # ★ 安全修复：脱敏测试账号后再注入 AI Prompt
        _sanitized = _sanitize_credentials(test_accounts)
        project_context += "\n\n" + _sanitized
        _log.info("测试账号: 已注入（脱敏后 %d 字符）", len(_sanitized))
    # ★ YApi 接口数据已通过 RAG 检索（rag_context）按需注入，不再全量塞入 project_context
    # 避免 200+ 条接口 Markdown 被裁剪到 1000 字符后实际无效，浪费 Token 且干扰 AI 判断
    if api_docs:
        _log.info(
            "接口文档: YApi 数据已就绪 (%d 字符)，将通过 RAG 检索按需提供给 AI",
            len(api_docs),
        )

    # ★ 跨模块依赖上下文（Step2 Phase 3 产出；MySQL 是唯一事实源，磁盘 JSON 仅显式导出附件）
    with guard("step3_model_cases: from src.pipeline.utils.diff_snapsho", log=_log, level=logging.WARNING):
        from src.pipeline.utils.diff_snapshot_store import diff_snapshot_store
        from src.core.versioning import current_version_label

        dep_snapshot = diff_snapshot_store.load_snapshot(
            "cross_dependencies",
            version_label=current_version_label(config.project_name) or None,
        )
        if dep_snapshot:
            # ★ 修复：兼容裸 list 和 {"dependencies": [...]} dict 两种格式
            if isinstance(dep_snapshot, dict):
                deps = dep_snapshot.get("dependencies", [])
            elif isinstance(dep_snapshot, list):
                deps = dep_snapshot
            else:
                deps = []
            if deps:
                cross_dep_context = "\n## 跨模块依赖关系\n"
                cross_dep_context += "以下需求之间存在跨模块数据/流程依赖，生成测试用例时请考虑集成测试场景：\n"
                for d in deps[:_MAX_CROSS_DEPS_DISPLAY]:
                    cross_dep_context += "- %s → %s: %s\n" % (
                        d.get("from", "?"),
                        d.get("to", "?"),
                        d.get("reason", ""),
                    )
                project_context += "\n" + cross_dep_context
                _log.info("跨模块依赖: 已注入 %d 条依赖关系 (MySQL)", len(deps))

    # ═══════════════════════════════════════════
    # ★ v6: 初始化 RAG + Traceability
    # ═══════════════════════════════════════════
    rag_enabled = False
    trace_links = {}
    with guard("step3_model_cases: from src.extensions.rag_engine impor", log=_log, level=logging.WARNING):
        from src.extensions.rag_engine import rag_engine

        # ★ 后端重启后内存索引丢失，从 MySQL 重新加载
        if rag_engine.chunk_count == 0:
            _log.info("[Step3] RAG 内存索引为空，从 MySQL 重新加载...")
            rag_engine._load_chunks()
        if rag_engine.chunk_count > 0:
            rag_enabled = True
            _log.info("RAG 引擎: 已就绪 (%d chunks)", rag_engine.chunk_count)

    with guard("step3_model_cases: from src.extensions.traceability_mod", log=_log, level=logging.WARNING):
        from src.extensions.traceability_model import traceability_engine

        # ★ 根因修复(2026-09-27): 追溯链路是 Step2 落库的持久化产物（需求↔API/DB 映射），
        #   force_regen 只重生成用例、不改需求，弃用它会使 trace_data.apis 恒空、
        #   用例退化为无 API/DB 锚点的语言匹配。故 force 下同样加载。
        cached_links = traceability_engine._load_cache()
        if cached_links:
            trace_links = cached_links
            _log.info("追溯链路: 已加载 (%d 条)", len(trace_links))
        else:
            _log.info("追溯链路: 无缓存（如需 Trace 上下文，请先运行 Step2 重建）")

    # ═══════════════════════════════════════════
    # 全量测试用例建模（XMind + 核心验证已剥离到文档中心独立触发）
    # ═══════════════════════════════════════════
    total = 0
    if not quick:
        if skip_full:
            _log.info("所有需求已有足够测试用例，跳过全量意图建模")
        else:
            _log.info("全量测试用例建模")
            # 数据来源汇总（始终显示，无数据源时标黄警告）
            _srcs = []
            if ui_ctx:
                _srcs.append("Figma UI(%d字)" % len(ui_ctx))
            if data_context:
                _srcs.append("DB Schema(%d字)" % len(data_context))
            if rag_enabled:
                _srcs.append("RAG检索")
            if trace_links:
                _srcs.append("追溯链路(%d条)" % len(trace_links))
            if api_docs:
                _srcs.append("API文档(%d字)" % len(api_docs))
            if test_accounts:
                _srcs.append("测试账号")
            if _srcs:
                _log.info("数据来源: %s", " | ".join(_srcs))
            else:
                _log.warning(
                    "数据来源: 无（仅基于需求文档建模，建议检查 Figma/DB/YApi 连接）"
                )

            # ★ 版本一致性检查
            _ver_ok = _check_and_confirm_version(self, config, excel_handler)
            if not _ver_ok:
                _log.warning("版本不一致，用户取消建模，跳过 Step 3")
                return {"success": True, "count": 0, "version_skipped": True}

            cnt, stopped = _model_full_intents(
                ungen,
                project_context=project_context,
                ui_ctx=ui_ctx,
                data_context=data_context,
                rag_enabled=rag_enabled,
                trace_links=trace_links,
                stop_check=self._force_stop_check,
                progress_cb=self._progress_callback,
                run_id=run_id,
            )
            total = cnt
            # ★ H1: 建模完成后，跨版本标 stale — modified 需求的旧版本对应意图条目级失效
            _mark_stale_intents_for_changed_requirements(ungen)
            if stopped:
                return {"success": True, "count": total, "force_stopped": True}
    elif ungen:
        # quick 模式: 仅处理少量需求
        total = 0
        for i, req in enumerate(ungen[:_DEFAULT_QUICK_MODE_LIMIT]):
            _log.info(
                "[%d/%d] 快速建模测试用例",
                i + 1,
                min(_DEFAULT_QUICK_MODE_LIMIT, len(ungen)),
            )
            result = ai_brain.model_test_cases(
                req,
                project_context=project_context,
                ui_context=ui_ctx,
                enable_validation=True,  # ★ quick 模式也开启校验，阻断 cross_check 错误
            )
            if not result.get("success"):
                _err = str(result.get("error", ""))[:120]
                _log.warning(
                    "AI 快速建模失败，使用本地规则兜底 [req=%s]: %s",
                    req.get("req_id", ""),
                    _err,
                )
                result = {
                    "success": True,
                    "data": _build_rule_fallback_cases(req, _err),
                    "_fallback": True,
                }
            if result.get("success"):
                count = excel_handler.parse_and_write_intents(
                    req.get("req_id", ""), result.get("data", "")
                )
                total += count

    _log.info("总共建模 %d 条测试用例", total)

    # ═══════════════════════════════════════════
    # ★ P5: 反向覆盖意图生成（step3_inverse）
    #   对正向意图做异常/安全/顺序反向扩展，产出反向意图喂给 Step4
    #   配置开关：config.yaml step3.inverse_enabled 或 前端 SECURITY_INVERSE_ENABLED
    # ═══════════════════════════════════════════
    if not quick and not skip_full:
        # ★ v6.1(2026-08-12): 优先读取前端开关 SECURITY_INVERSE_ENABLED，
        #   回退到 config.yaml step3.inverse_enabled
        _inverse_on = False
        with guard("step3_model_cases: from src.core.pipeline_config import", log=_log):
            from src.core.pipeline_config import _read_config_value
            _inverse_on = _read_config_value("SECURITY_INVERSE_ENABLED", False)
        if not _inverse_on:
            _inverse_on = str(
                getattr(config, "_yaml_raw", {})
                .get("step3", {})
                .get("inverse_enabled", "off")
            ).lower() in ("1", "true", "yes", "on")
        if _inverse_on:
            with guard("step3_model_cases: from src.pipeline.steps.step3_invers", log=_log, level=logging.WARNING):
                from src.pipeline.steps.step3_inverse import generate_inverse_intents

                _all_positive = case_store.list_intents()
                if _all_positive:
                    _log.info(
                        "[Step3] 反向覆盖扩展: %d 条正向意图 → 生成反向意图...",
                        len(_all_positive),
                    )
                    _inverse_intents = generate_inverse_intents(_all_positive)
                    if _inverse_intents:
                        _inv_total = 0
                        for ii in _inverse_intents:
                            rid = ii.get("req_id", "")
                            if rid:
                                _inv_total += excel_handler.parse_and_write_intents(
                                    rid, [ii]
                                )
                        _log.info("[Step3] 反向覆盖扩展完成: %d 条反向意图", _inv_total)
                    else:
                        _log.info("[Step3] 反向覆盖扩展: 无反向意图生成")
        else:
            _log.debug(
                "[Step3] 反向覆盖未启用（config.yaml step3.inverse_enabled=on 开启）"
            )

    # ═══════════════════════════════════════════
    # ★ v6: 增强覆盖矩阵（含数据源维度）
    #   只要有测试用例+需求数据就计算，不依赖本次是否新建模
    # ═══════════════════════════════════════════
    if not quick:
        with guard("step3_model_cases: from src.extensions.coverage_tracker", log=_log, level=logging.WARNING):
            from src.extensions.coverage_tracker import coverage_tracker

            all_reqs, _cov_req_source = _read_step3_requirements(excel_handler)
            all_intents = case_store.list_intents()

            if not all_reqs or not all_intents:
                if skip_full:
                    _log.info("覆盖矩阵: 无需求或测试用例数据，跳过")
            else:
                # 注入追溯链路数据 → 启用数据源维度评估
                if trace_links:
                    coverage_tracker.set_trace_links(trace_links)

                cov = coverage_tracker.compute_coverage(all_reqs, all_intents)
                summary = cov.get("summary", {})
                _log.info(
                    "覆盖矩阵: %.1f%% (%d/%d 需求缺口)",
                    summary.get("coverage_rate", 0),
                    summary.get("requirements_with_gaps", 0),
                    summary.get("total_requirements", 0),
                )
                gaps = cov.get("gaps", [])
                if gaps:
                    dim_detail = ", ".join(
                        "%s:%d" % (k, v)
                        for k, v in summary.get("missing_by_dimension", {}).items()
                        if v > 0
                    )
                    _log.info("缺口维度: %s（共 %d 项缺口）", dim_detail, len(gaps))

                    # ★ P3: 自动补生缺口意图（配置开关控制）
                    _auto_fill = str(
                        getattr(config, "_yaml_raw", {})
                        .get("step3", {})
                        .get("coverage_auto_fill", "off")
                    ).lower() in ("1", "true", "yes", "on")
                    if _auto_fill:
                        _log.info("[Step3] 自动补生缺口意图: %d 项...", len(gaps))
                        with guard("step3_model_cases: new_intents = coverage_tracker.auto_", log=_log, level=logging.WARNING):
                            new_intents = coverage_tracker.auto_fill_gaps(
                                gaps,
                                all_reqs,
                                ui_context=ui_ctx or "",
                                project_context=project_context or "",
                            )
                            if new_intents:
                                _log.info(
                                    "[Step3] 补生 %d 条缺口意图，写入 MySQL",
                                    len(new_intents),
                                )
                                for ni in new_intents:
                                    rid = ni.get("req_id", "")
                                    if rid:
                                        excel_handler.parse_and_write_intents(rid, [ni])
                            else:
                                _log.info(
                                    "[Step3] 无缺口意图需补生（或 COVERAGE_AUTO_FILL_AI 未开启）"
                                )
                    else:
                        _log.info(
                            "缺口补生未启用（config.yaml step3.coverage_auto_fill=on 开启）"
                        )

                # 持久化矩阵（含新维度）
                coverage_tracker.save_matrix(cov)

                # ★ 优化4: feature_id 维度覆盖率统计（复用现有数据，不新增表）
                # 统计"有 feature_id 的需求是否都有测试用例"，产出未覆盖 feature_id 列表
                # 价值：Step2 多源 feature list 整合后，feature_id 是天然覆盖率锚点，
                #       当前 requirement 维度会漏掉"有 feature 但无测试用例"的缺口
                with guard("step3_model_cases: _reqs_with_fid = [r for r in all_req", log=_log):
                    _reqs_with_fid = [r for r in all_reqs if r.get("feature_id")]
                    _intents_req_ids = set()
                    for _c in all_intents:
                        _rid = str(_c.get("req_id", "") or "").strip()
                        if _rid:
                            _intents_req_ids.add(_rid)
                    _fid_covered = sum(
                        1 for r in _reqs_with_fid if r.get("req_id") in _intents_req_ids
                    )
                    _fid_total = len(_reqs_with_fid)
                    if _fid_total > 0:
                        _fid_rate = round(_fid_covered / _fid_total * 100, 1)
                        _log.info(
                            "feature维度覆盖率: %.1f%% (%d/%d 个 feature_id 有测试用例)",
                            _fid_rate,
                            _fid_covered,
                            _fid_total,
                        )
                        if _fid_covered < _fid_total:
                            _uncovered_fids = [
                                r.get("feature_id")
                                for r in _reqs_with_fid
                                if r.get("req_id") not in _intents_req_ids
                            ]
                            _log.info(
                                "未覆盖 feature_id（前10）: %s", _uncovered_fids[:10]
                            )

            # ★ v6: 保存增强覆盖率报告
            from src.extensions.traceability_model import traceability_engine

            if trace_links:
                with guard("step3_model_cases: cov_report = traceability_engine.com", log=_log, level=logging.WARNING):
                    cov_report = traceability_engine.compute_all_coverage(
                        trace_links, all_intents
                    )
                    trace_section = traceability_engine.format_report_section(
                        cov_report
                    )
                    report_content = (
                        "# 增强覆盖率报告\n\n"
                        + trace_section
                        + traceability_engine.format_trace_matrix_md()
                    )
                    # ★ 07-06 报告分表：写入 generated_reports，不再混入 source_documents
                    try:
                        from src.core.config_loader import config as _cfg_v

                        _version_label = getattr(_cfg_v, "project_version", "") or ""
                    except Exception as _e:
                        _log.debug("[__init__] 非关键异常: %s", _e, exc_info=True)
                        _version_label = ""
                    try:
                        from src.core.versioning import current_version_id

                        _version_id = current_version_id(config.project_name)
                    except Exception as _e:
                        _log.debug(
                            "[Step3] 获取 version_id 失败: %s", _e, exc_info=True
                        )
                        _version_id = 0
                    with guard("step3_model_cases: from src.users.reports import save_g", log=_log, level=logging.WARNING):
                        from src.users.reports import save_generated_report

                        save_generated_report(
                            project=config.project_name,
                            report_type="coverage_report",
                            content=report_content,
                            version_id=_version_id,
                            version_label=_version_label,
                            run_id=run_id or "",
                        )
                        _log.info("增强覆盖率报告已保存到 MySQL generated_reports")


    # ★ 置信度增强：只读一致性检查。当前仅打印，不阻断 Step3。
    with guard("step3_model_cases: from src.pipeline.utils.consistency_", log=_log, level=logging.WARNING):
        from src.pipeline.utils.consistency_checker import run_and_print_checks

        run_and_print_checks("Step3后")

    # ★ 测试用例审核闸门：建模完成后暂停，等待人工审核
    # total == 0 表示所有需求建模均失败，也需触发闸门让用户排查
    # ★ v2: 全部自动通过时跳过暂停（无需人工审核）
    if not quick:
        with guard("step3_model_cases: from src.core.config_loader import c", log=_log, level=logging.WARNING):
            from src.core.config_loader import config

            if config.requirement_review_gate:
                # ★ v2: 检查是否有待审核用例。全部自动通过 → 跳过暂停
                pending_count = 0
                metas = []
                with guard("step3_model_cases: from src.pipeline.utils.review_store", log=_log):
                    from src.pipeline.utils.review_store import review_store

                    metas = review_store.list_case_meta()
                    pending_count = sum(
                        1
                        for m in metas
                        if m.get("review_status", "pending")
                        not in ("approved", "modified")
                    )

                if total == 0:
                    _log.warning(
                        "[审核闸门] 测试用例建模失败（0条产出），暂停等待人工排查..."
                    )
                    msg = "测试用例建模失败（0条产出），请排查问题后重试或手动添加用例"
                elif pending_count == 0 and len(metas) > 0:
                    _log.info(
                        "[审核闸门] 全部 %d 条测试用例已自动通过审核（置信度≥80），跳过人工审核",
                        len(metas),
                    )
                    if run_id:
                        _log.info(
                            "[run_id=%s] Step 3 完成: 共建模 %d 条测试用例",
                            run_id,
                            total,
                        )
                    return {"success": True, "count": total}
                else:
                    _log.info(
                        "[审核闸门] 测试用例建模完成，%d 条待审核...", pending_count
                    )
                    msg = "测试用例建模完成，请在 Web 端审核后继续"
                from src.backend.state import _set_paused_step
                from src.pipeline.utils.pause_controller import pause_controller

                # ★ A1 改造：按 project 设置暂停断点
                from src.core.config_loader import config as _cfg

                _set_paused_step(3, getattr(_cfg, "project_name", "") or "")
                pause_controller.pause()
                return {
                    "success": True,
                    "count": total,
                    "paused_for_case_review": True,
                    "message": msg,
                }

    if run_id:
        _log.info("[run_id=%s] Step 3 完成: 共建模 %d 条测试用例", run_id, total)
    return {"success": True, "count": total}


# ═══════════════════════════════════════════════════════════════
# ★ XMind + 核心验证测试用例已剥离到独立模块
#    - src/pipeline/steps/step3/xmind_designer.py  → generate_xmind_design()
#    - src/pipeline/steps/step3/smoke_generator.py  → generate_smoke_excel()
#    文档中心 API 直接调用，Step 3 流水线不再触发。
# ═══════════════════════════════════════════════════════════════
