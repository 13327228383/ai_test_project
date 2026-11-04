# -*- coding: utf-8 -*-
from __future__ import annotations
from src.core.guarded import guard

"""核心 record + 健康度指标埋点（coverage / cross_check / verification 等）— 从 metrics_collector.py 拆出，零业务逻辑变更。"""

import json
from datetime import datetime
import logging

_log = logging.getLogger(__name__)

# run_id -> project 反查缓存，避免后台线程每次 record 都查库。
_run_project_cache: dict = {}


class _RecordHealthMixin:
    """record() 主入口 + 健康度领域指标方法。"""

    def record(
        self,
        project: str,
        run_id: str,
        step: int,
        metric_type: str,
        metric_name: str,
        category: str = "",
        value: float = 0,
        total: float = 0,
        details: dict | None = None,
        immediate: bool = True,
        version_id: int = 0,
    ):
        """记录一条指标到内存缓冲（immediate=False）或直接写入 MySQL（默认即时写入）。
        immediate=True 时尝试直接写入 DB，写入失败则降级到缓冲（不丢数据）。
        """
        if not version_id:
            try:
                from src.backend.state import _current_version, _current_project

                _label = _current_version() or ""
                _proj = project or _current_project() or ""
                if not project and not _proj and run_id:
                    # ★ 根因修复(2026-09-01)：指标常由后台 executor/ThreadPoolExecutor 线程写库，
                    #   thread-local 的 _current_project()/config 绑定在该线程为空，会把指标写进
                    #   project=""（与 RAG 构建同类的项目绑定丢失），导致健康摘要按真实项目查不到数据。
                    #   run_id 标准格式为 {project}__{version_label}（src/core/versioning.py），
                    #   据此解析 project，稳定且不依赖 DB，规避执行前 executions 尚未落库的时序问题。
                    _proj = project = str(run_id).rsplit("__", 1)[0] or ""
                if _label and _proj:
                    from .persistence import _get_conn

                    _conn = _get_conn()
                    if _conn:
                        try:
                            _row = _conn.execute(
                                "SELECT id FROM project_versions "
                                "WHERE project=%s AND version_label=%s LIMIT 1",
                                (_proj, _label),
                            ).fetchone()
                            if _row and _row["id"]:
                                version_id = int(_row["id"])
                        finally:
                                with guard("record: _conn.close()", log=_log):
                                    _conn.close()
                if not version_id and _proj:
                    # ★ 根因修复(2026-09-20)：上面的版本解析依赖请求上下文 thread-local
                    #   (_current_version)，在后台流水线线程为空 → version_id 恒 0 → 版本隔离失效
                    #   （所有指标挤在 version_id=0，前端按 active version 查不到）。
                    #   回退到 versioning.current_version_id(project)：不依赖 thread-local，
                    #   从 project_versions active 解析，与 record_confidence_run 同源同构（通用）。
                    with guard("record: from src.core.versioning import curr", log=_log):
                        from src.core.versioning import current_version_id

                        _vid = current_version_id(_proj)
                        if _vid:
                            version_id = int(_vid)
            except Exception:
                version_id = 0
        entry = {
            "project": project,
            "version_id": version_id,
            "run_id": run_id,
            "step": step,
            "metric_type": metric_type,
            "metric_name": metric_name,
            "category": category,
            "value": value,
            "total": total,
            "details": json.dumps(details or {}, ensure_ascii=False),
            # ★ 2026-09-22 时区根治：原 datetime.now(timezone.utc) 把 UTC 裸值写进
            #   DATETIME，与全库本地时间约定（executions/run_logs 的 NOW()）错位 8h，
            #   任何本地时间窗查询（前端趋势/验收切片）都会把最近 8h 指标当旧数据。
            #   统一为服务器本地时间，与库内其它表同源。
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        if immediate:
            if not self._insert_single(entry):
                # ★ 收敛（2026-08-26）：MySQL 直写失败 → 降级到文件持久化 outbox
                #   （offline_store，重启不丢，MySQL 恢复后回放）。不再进内存 buffer，
                #   否则同一 entry 会被 flush 路径二次直写，导致与 outbox 重复。
                self._enqueue_metric(entry)
        else:
            with self._buffer_lock:
                self._buffer.setdefault(run_id, []).append(entry)

    def record_step1_source_status(
        self, project: str, run_id: str, sources_status: dict
    ):
        """记录 Step1 数据源拉取状态（★ v4.0 接入）。

        sources_status 格式: {"confluence": {"status": "ok", "detail": "..."}, ...}
        写入 metric_type='step1_source'，每个数据源一条记录。
        """
        status_counts = {"ok": 0, "failed": 0, "skipped": 0, "not_configured": 0}
        for name, info in (sources_status or {}).items():
            st = info.get("status", "unknown")
            status_counts[st] = status_counts.get(st, 0) + 1
            self.record(
                project,
                run_id,
                step=1,
                metric_type="step1_source",
                metric_name=name,
                category=st,
                value=1,
                details={"detail": info.get("detail", "")[:100]},
            )
        # 汇总
        self.record(
            project,
            run_id,
            step=1,
            metric_type="step1_source",
            metric_name="total_ok",
            category="summary",
            value=status_counts["ok"],
        )
        self.record(
            project,
            run_id,
            step=1,
            metric_type="step1_source",
            metric_name="total_failed",
            category="summary",
            value=status_counts["failed"],
        )
        self.record(
            project,
            run_id,
            step=1,
            metric_type="step1_source",
            metric_name="total_skipped",
            category="summary",
            value=status_counts["skipped"],
        )

    def record_coverage(
        self,
        project: str,
        run_id: str,
        forward_rate: float,
        orphan_modules: int,
        uncovered_sections: int,
    ):
        """记录 Step 2 双向覆盖率检查结果。"""
        self.record(
            project,
            run_id,
            step=2,
            metric_type="coverage",
            metric_name="forward_coverage_rate",
            category="forward",
            value=forward_rate,
            total=100,
        )
        self.record(
            project,
            run_id,
            step=2,
            metric_type="coverage",
            metric_name="orphan_modules",
            category="reverse",
            value=orphan_modules,
        )
        self.record(
            project,
            run_id,
            step=2,
            metric_type="coverage",
            metric_name="uncovered_sections",
            category="reverse",
            value=uncovered_sections,
        )

    def record_step2_breakdown_stats(
        self,
        project: str,
        run_id: str,
        feature_count: int = 0,
        matched_count: int = 0,
        undocumented_count: int = 0,
        ai_calls: int = 0,
        total_reqs: int = 0,
        match_stats: dict | None = None,
        verify_stats: dict | None = None,
        coverage_stats: dict | None = None,
        # ★ #14 KPI 三方对齐：三级信任分布（#9/#11）
        trust_l1_explicit: int = 0,
        trust_l2_implicit_common: int = 0,
        trust_l3_implicit_business: int = 0,
        # ★ #14 KPI 三方对齐：模式库补全统计（#10/#11）
        implicit_stats: dict | None = None,
        # ★ #14 KPI 三方对齐：多源功能提取统计（#6/#7/#8）
        multi_source_stats: dict | None = None,
        # ★ 规则覆盖率 vs AI 覆盖率：规则提取数量
        rule_count: int = 0,
    ):
        """★ S2-017 (v3 功能清单驱动): 记录 Step 2 拆解运行指标到 ai_health_metrics。

        对齐 feature_driven._stats 的字段：
        - step2_feature_count:      目标系统功能总数（来自 system_feature_list）
        - step2_matched_count:      匹配到需求文档的功能数（keyword+embed）
        - step2_undocumented_count: 未文档化功能数（需求文档未描述）
        - step2_ai_calls:           AI 提取三元组的调用次数
        - step2_total_reqs:         最终生成的需求条目数
        - match_stats:              三层匹配统计（keyword/embed/undocumented）
        - verify_stats:             原文核对统计（passed/failed/empty/skipped_implicit）
        - coverage_stats:           逆向覆盖统计（covered/uncovered）
        - ★ step2_trust_l1:         L1 显式需求数（is_implicit=0）
        - ★ step2_trust_l2:         L2 通用隐性需求数（is_implicit=1，模式库+AI推断）
        - ★ step2_trust_l3:         L3 业务隐性候选数（is_implicit=2，未文档化未匹配模式库）
        - ★ implicit_stats:         模式库补全统计（l2_matched/l3_unmatched/pattern_count）
        - ★ multi_source_stats:     多源功能提取统计（figma/req/yapi 各源功能数）
        """
        with guard("record_step2_breakdown_stats: details = { 'feature_count': int(fea", log=_log):
            details = {
                "feature_count": int(feature_count),
                "matched_count": int(matched_count),
                "undocumented_count": int(undocumented_count),
                "ai_calls": int(ai_calls),
                "total_reqs": int(total_reqs),
                "match_stats": match_stats or {},
                "verify_stats": verify_stats or {},
                "coverage_stats": coverage_stats or {},
                # ★ #14 三级信任分布
                "trust_l1_explicit": int(trust_l1_explicit),
                "trust_l2_implicit_common": int(trust_l2_implicit_common),
                "trust_l3_implicit_business": int(trust_l3_implicit_business),
                # ★ #14 模式库补全统计
                "implicit_stats": implicit_stats or {},
                # ★ #14 多源功能提取统计
                "multi_source_stats": multi_source_stats or {},
            }
            self.record(
                project,
                run_id,
                step=2,
                metric_type="step2_breakdown",
                metric_name="step2_feature_count",
                category="input",
                value=int(feature_count),
                total=int(feature_count),
                details=details,
            )
            self.record(
                project,
                run_id,
                step=2,
                metric_type="step2_breakdown",
                metric_name="step2_matched_count",
                category="match",
                value=int(matched_count),
                total=max(int(feature_count), 1),
                details=details,
            )
            self.record(
                project,
                run_id,
                step=2,
                metric_type="step2_breakdown",
                metric_name="step2_undocumented_count",
                category="match",
                value=int(undocumented_count),
                total=max(int(feature_count), 1),
                details=details,
            )
            self.record(
                project,
                run_id,
                step=2,
                metric_type="step2_breakdown",
                metric_name="step2_ai_calls",
                category="ai_call",
                value=int(ai_calls),
                total=int(ai_calls),
                details=details,
            )
            self.record(
                project,
                run_id,
                step=2,
                metric_type="step2_breakdown",
                metric_name="step2_rule_count",
                category="rule",
                value=int(rule_count),
                total=int(rule_count),
                details=details,
            )
            self.record(
                project,
                run_id,
                step=2,
                metric_type="step2_breakdown",
                metric_name="step2_total_reqs",
                category="output",
                value=int(total_reqs),
                total=int(total_reqs),
                details=details,
            )
            # ★ #14 三级信任分布独立 metric_name（便于前端按名查询）
            self.record(
                project,
                run_id,
                step=2,
                metric_type="step2_breakdown",
                metric_name="step2_trust_l1",
                category="trust",
                value=int(trust_l1_explicit),
                total=int(total_reqs),
                details=details,
            )
            self.record(
                project,
                run_id,
                step=2,
                metric_type="step2_breakdown",
                metric_name="step2_trust_l2",
                category="trust",
                value=int(trust_l2_implicit_common),
                total=int(total_reqs),
                details=details,
            )
            self.record(
                project,
                run_id,
                step=2,
                metric_type="step2_breakdown",
                metric_name="step2_trust_l3",
                category="trust",
                value=int(trust_l3_implicit_business),
                total=int(total_reqs),
                details=details,
            )

    def record_step2_confidence(
        self,
        project: str,
        run_id: str,
        scores: list = None,
        high_count: int = 0,
        medium_count: int = 0,
        low_count: int = 0,
    ):
        """★ S2-012: 记录 Step 2 置信度分布到 ai_health_metrics。

        规格 §4.10: 每次 Step2 完成后将本批次的置信度分布（均值、标准差、各维度得分分布）
        写入 ai_health_metrics 表（metric_type='step2_confidence'）。

        写入指标：
        - step2_confidence_mean:      平均置信度
        - step2_confidence_stddev:    置信度标准差
        - step2_confidence_count:     需求总数
        - step2_confidence_high:      高置信度数量（≥80）
        - step2_confidence_medium:    中置信度数量（60-79）
        - step2_confidence_low:       低置信度数量（<60）
        """
        with guard("record_step2_confidence: import statistics as _stats", log=_log):
            import statistics as _stats

            if not scores:
                scores = []
            scores = [int(s) for s in scores if isinstance(s, (int, float))]
            count = len(scores)
            mean_val = _stats.mean(scores) if count > 0 else 0.0
            # 标准差需要至少 2 个样本，否则为 0
            stddev_val = _stats.stdev(scores) if count >= 2 else 0.0
            # 若未显式传入分桶计数，则自动计算
            if high_count == 0 and medium_count == 0 and low_count == 0 and count > 0:
                high_count = sum(1 for s in scores if s >= 80)
                medium_count = sum(1 for s in scores if 60 <= s < 80)
                low_count = sum(1 for s in scores if s < 60)
            details = {
                "count": count,
                "mean": round(mean_val, 2),
                "stddev": round(stddev_val, 2),
                "high_count": int(high_count),
                "medium_count": int(medium_count),
                "low_count": int(low_count),
            }
            self.record(
                project,
                run_id,
                step=2,
                metric_type="step2_confidence",
                metric_name="step2_confidence_mean",
                category="distribution",
                value=round(mean_val, 2),
                total=100,
                details=details,
            )
            self.record(
                project,
                run_id,
                step=2,
                metric_type="step2_confidence",
                metric_name="step2_confidence_stddev",
                category="distribution",
                value=round(stddev_val, 2),
                total=100,
                details=details,
            )
            self.record(
                project,
                run_id,
                step=2,
                metric_type="step2_confidence",
                metric_name="step2_confidence_count",
                category="count",
                value=count,
                total=count,
                details=details,
            )
            self.record(
                project,
                run_id,
                step=2,
                metric_type="step2_confidence",
                metric_name="step2_confidence_high",
                category="bucket",
                value=int(high_count),
                total=count,
                details=details,
            )
            self.record(
                project,
                run_id,
                step=2,
                metric_type="step2_confidence",
                metric_name="step2_confidence_medium",
                category="bucket",
                value=int(medium_count),
                total=count,
                details=details,
            )
            self.record(
                project,
                run_id,
                step=2,
                metric_type="step2_confidence",
                metric_name="step2_confidence_low",
                category="bucket",
                value=int(low_count),
                total=count,
                details=details,
            )

    def record_cross_checks(
        self,
        project: str,
        run_id: str,
        api_errors: int = 0,
        ui_errors: int = 0,
        enum_errors: int = 0,
        deep_issues: int = 0,
        rule_warnings: int = 0,
        total_cases: int = 0,
    ):
        """记录 Step 3 交叉校验和验证结果。"""
        entries = [
            ("cross_check", "api_mismatch", "api", api_errors, total_cases),
            ("cross_check", "ui_element_missing", "ui", ui_errors, total_cases),
            ("cross_check", "enum_value_invalid", "enum", enum_errors, total_cases),
            ("deep_check", "ai_deep_issues", "ai", deep_issues, total_cases),
            ("rule_check", "rule_warnings", "rule", rule_warnings, total_cases),
        ]
        for mtype, mname, cat, val, tot in entries:
            if val is not None:
                self.record(
                    project,
                    run_id,
                    step=3,
                    metric_type=mtype,
                    metric_name=mname,
                    category=cat,
                    value=val,
                    total=tot,
                )
        total_errors = api_errors + ui_errors + enum_errors
        self.record(
            project,
            run_id,
            step=3,
            metric_type="cross_check",
            metric_name="hallucination_errors",
            category="overall",
            value=total_errors,
            total=total_cases,
        )

    def record_case_modeling_quality(
        self,
        project: str,
        run_id: str,
        req_id: str = "",
        generated_count: int = 0,
        avg_quality_score: float = 0.0,
        low_quality_count: int = 0,
        validation_warning_count: int = 0,
        risk_level: str = "medium",
    ):
        """记录 Step 3 测试用例建模批次质量趋势。"""
        details = {"req_id": req_id, "risk_level": risk_level}
        self.record(
            project,
            run_id,
            step=3,
            metric_type="case_modeling_quality",
            metric_name="avg_quality_score",
            category=risk_level,
            value=avg_quality_score,
            total=100,
            details=details,
        )
        self.record(
            project,
            run_id,
            step=3,
            metric_type="case_modeling_quality",
            metric_name="low_quality_count",
            category=risk_level,
            value=low_quality_count,
            total=generated_count,
            details=details,
        )
        self.record(
            project,
            run_id,
            step=3,
            metric_type="case_modeling_quality",
            metric_name="validation_warning_count",
            category=risk_level,
            value=validation_warning_count,
            total=generated_count,
            details=details,
        )
        self.record(
            project,
            run_id,
            step=3,
            metric_type="case_modeling_quality",
            metric_name="generated_count",
            category=risk_level,
            value=generated_count,
            total=generated_count,
            details=details,
        )

    def record_guard_rejection(
        self,
        project: str,
        run_id: str,
        action_type: str = "",
        reason_category: str = "",
    ):
        """记录一次 Step 4 守卫拦截。"""
        self.record(
            project,
            run_id,
            step=4,
            metric_type="guard_reject",
            metric_name="guard_rejection",
            category=reason_category,
            value=1,
            details={"action_type": action_type, "reason": reason_category},
        )

    def record_action_attempt(self, project: str, run_id: str, action_type: str):
        """记录一次 AI 动作尝试（用于计算守卫拦截率）。"""
        self.record(
            project,
            run_id,
            step=4,
            metric_type="action_attempt",
            metric_name=action_type,
            category="action",
            value=1,
        )

    def record_verification(
        self,
        project: str,
        run_id: str,
        step_desc: str = "",
        ui_pass: bool = True,
        api_pass: bool = True,
        db_pass: bool = True,
        case_id: str = "",
    ):
        """记录一次 Step 4 三轨校验结果。

        ★ 根因修复(2026-09-02)：新增 case_id，把用例标识写入 details，使执行详情可按
          case_id 聚合出 per-case 三轨通过率（此前仅 run 级可见，前端无法按用例展示
          ui/api/db 各自 through）。兼容不传 case_id 的旧调用（api_only 等保留原语义）。
        """
        for rail, passed in [("ui", ui_pass), ("api", api_pass), ("db", db_pass)]:
            self.record(
                project,
                run_id,
                step=4,
                metric_type="verification",
                metric_name=f"{rail}_pass",
                category=rail,
                value=1 if passed else 0,
                total=1,
                details={
                    "step_desc": step_desc[:100],
                    "case_id": (case_id or "")[:64],
                },
            )
        all_pass = ui_pass and api_pass and db_pass
        if not all_pass:
            self.record(
                project,
                run_id,
                step=4,
                metric_type="verification",
                metric_name="disagreement",
                category="3rail",
                value=1,
                details={
                    "ui": ui_pass,
                    "api": api_pass,
                    "db": db_pass,
                    "step_desc": step_desc[:100],
                    "case_id": (case_id or "")[:64],
                },
            )

    def record_browser_ai_round(
        self,
        project: str,
        run_id: str,
        case_id: str = "",
        step_desc: str = "",
        round_num: int = 0,
        elapsed_sec: float = 0.0,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        model: str = "",
    ):
        """记录 Step4 浏览器 AI 单轮交互耗时与 token 消耗。"""
        details = {
            "case_id": case_id,
            "step_desc": step_desc[:120],
            "round_num": round_num,
            "model": model,
        }
        self.record(
            project,
            run_id,
            step=4,
            metric_type="browser_ai_round",
            metric_name="elapsed_sec",
            category="latency",
            value=float(elapsed_sec or 0.0),
            total=1,
            details=details,
        )
        self.record(
            project,
            run_id,
            step=4,
            metric_type="browser_ai_round",
            metric_name="prompt_tokens",
            category="token",
            value=int(prompt_tokens or 0),
            total=1,
            details=details,
        )
        self.record(
            project,
            run_id,
            step=4,
            metric_type="browser_ai_round",
            metric_name="completion_tokens",
            category="token",
            value=int(completion_tokens or 0),
            total=1,
            details=details,
        )

    def record_flaky_count(
        self, project: str, run_id: str, flaky_count: int, total_cases: int
    ):
        """记录 Step 4 Flaky 检测结果（计数，比率可在查询时计算）。"""
        self.record(
            project,
            run_id,
            step=4,
            metric_type="flaky",
            metric_name="flaky_count",
            category="flaky",
            value=flaky_count,
            total=total_cases,
        )

    def record_flaky_root_cause(
        self,
        project: str,
        run_id: str,
        env_issues: int = 0,
        timing_issues: int = 0,
        data_issues: int = 0,
        ai_hallucination_issues: int = 0,
    ):
        """记录 Step 4 Flaky 根因分类（供 P0 治理 flaky_guard 领域读取）。

        写入 metric_type='flaky_guard'，metric_name 使用前缀匹配约定的字段名:
          env_dns_resolve / timing_async_race / data_inconsistent / ai_hallucination_selector
        """
        if env_issues:
            self.record(
                project,
                run_id,
                step=4,
                metric_type="flaky_guard",
                metric_name="env_dns_resolve",
                category="env",
                value=env_issues,
            )
        if timing_issues:
            self.record(
                project,
                run_id,
                step=4,
                metric_type="flaky_guard",
                metric_name="timing_async_race",
                category="timing",
                value=timing_issues,
            )
        if data_issues:
            self.record(
                project,
                run_id,
                step=4,
                metric_type="flaky_guard",
                metric_name="data_inconsistent",
                category="data",
                value=data_issues,
            )
        if ai_hallucination_issues:
            self.record(
                project,
                run_id,
                step=4,
                metric_type="flaky_guard",
                metric_name="ai_hallucination_selector",
                category="ai",
                value=ai_hallucination_issues,
            )

    def record_op_cache_hit(self, project: str, run_id: str):
        """记录一次 Step 4 操作指令缓存命中。"""
        self.record(
            project,
            run_id,
            step=4,
            metric_type="op_cache",
            metric_name="cache_hit",
            category="cache",
            value=1,
        )

    def record_op_cache_miss(self, project: str, run_id: str):
        """记录一次 Step 4 操作指令缓存未命中。"""
        self.record(
            project,
            run_id,
            step=4,
            metric_type="op_cache",
            metric_name="cache_miss",
            category="cache",
            value=1,
        )

    def record_ai_loop_entry(self, project: str, run_id: str, case_id: str = ""):
        """记录一次 AI loop 兜底进入（查表未命中后走 AI 决策）。"""
        self.record(
            project,
            run_id,
            step=4,
            metric_type="ai_loop",
            metric_name="entered",
            category="ai",
            value=1,
            details={"case_id": case_id},
        )

    def record_ai_loop_pass(self, project: str, run_id: str, case_id: str = ""):
        """记录一次 AI loop 兜底成功（AI 决策使用例通过）。"""
        self.record(
            project,
            run_id,
            step=4,
            metric_type="ai_loop",
            metric_name="passed",
            category="ai",
            value=1,
            details={"case_id": case_id},
        )

    def record_case_complexity(
        self, project: str, run_id: str, case_id: str = "", step_count: int = 0
    ):
        """记录成功用例的步骤数（用例复杂度指标）。"""
        self.record(
            project,
            run_id,
            step=4,
            metric_type="case_complexity",
            metric_name="step_count",
            category="case",
            value=step_count,
            details={"case_id": case_id},
        )

    def record_intent_dominance(
        self,
        project: str,
        run_id: str,
        case_id: str = "",
        score: float = 0.0,
        has_intent_schema: bool = False,
        has_verification_plan: bool = False,
        verdict_recorded: bool = False,
        recipe_hits: int = 0,
        recipe_only_pass: int = 0,
        total_steps: int = 0,
    ):
        """记录 Step4 用例主导度（v3.0: has_intent_schema→has_actions, has_verification_plan→has_checks）。

        分数关注"用例资产 + 检查计划 + 裁决记录"是否形成闭环；recipe/cache 只有在
        缺少证据裁决却直接通过时才扣分，避免把正常的低成本复用误判为步骤驱动。
        """
        with guard("record_intent_dominance: details = { 'case_id': case_id, 'has", log=_log):
            details = {
                "case_id": case_id,
                "has_actions": bool(has_intent_schema),
                "has_checks": bool(has_verification_plan),
                "verdict_recorded": bool(verdict_recorded),
                "recipe_hits": int(recipe_hits or 0),
                "recipe_only_pass": int(recipe_only_pass or 0),
                "total_steps": int(total_steps or 0),
            }
            self.record(
                project,
                run_id,
                step=4,
                metric_type="case_dominance",
                metric_name="dominance_score",
                category="score",
                value=float(score or 0.0),
                total=100,
                details=details,
            )
            self.record(
                project,
                run_id,
                step=4,
                metric_type="case_dominance",
                metric_name="case_total",
                category="count",
                value=1,
                total=1,
                details=details,
            )
            self.record(
                project,
                run_id,
                step=4,
                metric_type="case_dominance",
                metric_name="actions_present",
                category="asset",
                value=1 if has_intent_schema else 0,
                total=1,
                details=details,
            )
            self.record(
                project,
                run_id,
                step=4,
                metric_type="case_dominance",
                metric_name="checks_present",
                category="evidence",
                value=1 if has_verification_plan else 0,
                total=1,
                details=details,
            )
            self.record(
                project,
                run_id,
                step=4,
                metric_type="case_dominance",
                metric_name="verdict_recorded",
                category="verdict",
                value=1 if verdict_recorded else 0,
                total=1,
                details=details,
            )
            self.record(
                project,
                run_id,
                step=4,
                metric_type="case_dominance",
                metric_name="recipe_only_pass",
                category="risk",
                value=int(recipe_only_pass or 0),
                total=max(int(recipe_hits or 0), 1),
                details=details,
            )

    def record_scorecard(
        self,
        project: str,
        run_id: str,
        total_cases: int,
        avg_score: float,
        high_score_cases: int,
        medium_score_cases: int,
        low_score_cases: int,
    ) -> None:
        """记录 Step 3 ScorecardOracle 评分结果到 ai_health_metrics。"""
        with guard("record_scorecard: details = { 'total_cases': total_cas", log=_log):
            details = {
                "total_cases": total_cases,
                "high_score_cases": high_score_cases,
                "medium_score_cases": medium_score_cases,
                "low_score_cases": low_score_cases,
                "avg_score": avg_score,
            }
            self.record(
                project,
                run_id,
                step=3,
                metric_type="scorecard",
                metric_name="avg_score",
                category="quality",
                value=float(avg_score or 0.0),
                total=100,
                details=details,
            )
            self.record(
                project,
                run_id,
                step=3,
                metric_type="scorecard",
                metric_name="total_evaluations",
                category="count",
                value=total_cases,
                total=total_cases,
                details=details,
            )

    def record_ground_truth(
        self,
        project: str,
        run_id: str,
        total_checks: int,
        mismatches_found: int,
        mismatches_by_field: dict[str, int],
    ) -> None:
        """记录 Step 4 GroundTruth 校验结果。"""
        with guard("record_ground_truth: details = { 'total_checks': total_ch", log=_log):
            details = {
                "total_checks": total_checks,
                "mismatches_found": mismatches_found,
                "mismatches_by_field": mismatches_by_field,
            }
            self.record(
                project,
                run_id,
                step=4,
                metric_type="ground_truth",
                metric_name="gt_total",
                category="count",
                value=total_checks,
                total=total_checks,
                details=details,
            )
            self.record(
                project,
                run_id,
                step=4,
                metric_type="ground_truth",
                metric_name="gt_fail",
                category="error",
                value=mismatches_found,
                total=total_checks,
                details=details,
            )

    def record_self_heal(
        self,
        project: str,
        run_id: str,
        total_attempts: int,
        heal_successes: int,
        verify_successes: int,
        verify_fails: int,
    ) -> None:
        """记录 Step 4 自愈合尝试结果。"""
        with guard("record_self_heal: details = { 'total_attempts': total_", log=_log):
            details = {
                "total_attempts": total_attempts,
                "heal_successes": heal_successes,
                "verify_successes": verify_successes,
                "verify_fails": verify_fails,
            }
            self.record(
                project,
                run_id,
                step=4,
                metric_type="self_heal",
                metric_name="sh_total",
                category="count",
                value=total_attempts,
                total=total_attempts,
                details=details,
            )
            self.record(
                project,
                run_id,
                step=4,
                metric_type="self_heal",
                metric_name="sh_success",
                category="success",
                value=heal_successes,
                total=total_attempts,
                details=details,
            )
            self.record(
                project,
                run_id,
                step=4,
                metric_type="self_heal",
                metric_name="sh_verify_success",
                category="verify",
                value=verify_successes,
                total=heal_successes,
                details=details,
            )
            self.record(
                project,
                run_id,
                step=4,
                metric_type="self_heal",
                metric_name="sh_verify_fail",
                category="verify",
                value=verify_fails,
                total=heal_successes,
                details=details,
            )

    def record_visual_baseline(
        self,
        project: str,
        run_id: str,
        snapshots_taken: int,
        diffs_detected: int,
        accepted_diffs: int,
        rejected_diffs: int,
    ) -> None:
        """记录 Step 4 视觉基线对比结果。"""
        with guard("record_visual_baseline: details = { 'snapshots_taken': snaps", log=_log):
            details = {
                "snapshots_taken": snapshots_taken,
                "diffs_detected": diffs_detected,
                "accepted_diffs": accepted_diffs,
                "rejected_diffs": rejected_diffs,
            }
            diff_rate = (
                100.0 * diffs_detected / snapshots_taken if snapshots_taken > 0 else 0.0
            )
            self.record(
                project,
                run_id,
                step=4,
                metric_type="visual_baseline",
                metric_name="snapshots",
                category="count",
                value=float(snapshots_taken),
                total=float(snapshots_taken),
                details=details,
            )
            self.record(
                project,
                run_id,
                step=4,
                metric_type="visual_baseline",
                metric_name="diff_rate",
                category="quality",
                value=diff_rate,
                total=100.0,
                details=details,
            )

    def record_harness_governance(
        self,
        project: str,
        run_id: str,
        dangerous_intercepts: int = 0,
        tool_limit_violations: int = 0,
        token_used: int = 0,
        token_budget_total: int = 0,
    ) -> None:
        """记录 Step 4 Harness 治理插件拦截统计。"""
        with guard("record_harness_governance: details = { 'dangerous_intercepts': ", log=_log):
            details = {
                "dangerous_intercepts": dangerous_intercepts,
                "tool_limit_violations": tool_limit_violations,
                "token_used": token_used,
                "token_budget_total": token_budget_total,
            }
            self.record(
                project,
                run_id,
                step=4,
                metric_type="harness_governance",
                metric_name="dangerous_intercepts",
                category="security",
                value=dangerous_intercepts,
                total=dangerous_intercepts,
                details=details,
            )
            self.record(
                project,
                run_id,
                step=4,
                metric_type="harness_governance",
                metric_name="tool_limit_violations",
                category="limit",
                value=tool_limit_violations,
                total=tool_limit_violations,
                details=details,
            )
            self.record(
                project,
                run_id,
                step=4,
                metric_type="harness_governance",
                metric_name="token_used",
                category="token",
                value=token_used,
                total=max(token_budget_total, 1),
                details=details,
            )
            self.record(
                project,
                run_id,
                step=4,
                metric_type="harness_governance",
                metric_name="token_budget_total",
                category="token",
                value=token_budget_total,
                total=token_budget_total,
                details=details,
            )

    def record_token_budget(
        self,
        project: str,
        run_id: str,
        step: int,
        truncations: int = 0,
        budget_exceeded: int = 0,
        compressions: int = 0,
    ) -> None:
        """记录 TokenBudget 统计。"""
        with guard("record_token_budget: details = { 'truncations': truncatio", log=_log):
            details = {
                "truncations": truncations,
                "budget_exceeded": budget_exceeded,
                "compressions": compressions,
            }
            self.record(
                project,
                run_id,
                step=step,
                metric_type="token_budget",
                metric_name="truncations",
                category="warn",
                value=truncations,
                total=truncations,
                details=details,
            )
            self.record(
                project,
                run_id,
                step=step,
                metric_type="token_budget",
                metric_name="budget_exceeded",
                category="limit",
                value=budget_exceeded,
                total=budget_exceeded,
                details=details,
            )
            self.record(
                project,
                run_id,
                step=step,
                metric_type="token_budget",
                metric_name="compressions",
                category="optimize",
                value=compressions,
                total=compressions,
                details=details,
            )

    def record_performance_fcp(
        self,
        project: str,
        run_id: str,
        case_id: str,
        fcp_ms: float,
        exceeds_threshold: bool,
    ) -> None:
        """记录 Step 4 FCP 性能指标。"""
        with guard("record_performance_fcp: details = { 'case_id': case_id, 'fcp", log=_log):
            details = {
                "case_id": case_id,
                "fcp_ms": fcp_ms,
                "exceeds_threshold": exceeds_threshold,
            }
            self.record(
                project,
                run_id,
                step=4,
                metric_type="fcp",
                metric_name="fcp_ms",
                category="performance",
                value=float(fcp_ms),
                total=5000,
                details=details,
            )

    def record_atomic_rollback(
        self,
        project: str,
        run_id: str,
        rollback_count: int,
        total_atomic_executions: int,
    ) -> None:
        """记录 Step 4 原子操作回滚次数。"""
        with guard("record_atomic_rollback: details = { 'rollback_count': rollba", log=_log):
            details = {
                "rollback_count": rollback_count,
                "total_atomic_executions": total_atomic_executions,
            }
            self.record(
                project,
                run_id,
                step=4,
                metric_type="atomic_rollback",
                metric_name="atomic_rollback",
                category="count",
                value=rollback_count,
                total=max(total_atomic_executions, 1),
                details=details,
            )

    def record_tool_mode(
        self, project: str, run_id: str, tool_mode_calls: int, ai_total_calls: int
    ) -> None:
        """记录 Step 4 工具模式使用率。"""
        with guard("record_tool_mode: details = { 'tool_mode_calls': tool_", log=_log):
            details = {
                "tool_mode_calls": tool_mode_calls,
                "ai_total_calls": ai_total_calls,
            }
            self.record(
                project,
                run_id,
                step=4,
                metric_type="tool_mode",
                metric_name="tool_mode_calls",
                category="count",
                value=tool_mode_calls,
                total=max(ai_total_calls, 1),
                details=details,
            )
            self.record(
                project,
                run_id,
                step=4,
                metric_type="tool_mode",
                metric_name="ai_total_calls",
                category="count",
                value=ai_total_calls,
                total=ai_total_calls,
                details=details,
            )

    # ============================================================
    # ★ H4 (2026-07-10): 6 个新 KPI 埋点方法
    # ============================================================

    def record_slice_coverage(
        self,
        project: str,
        run_id: str,
        total_slices: int,
        covered_slices: int,
        retry_rounds: int = 0,
    ) -> None:
        """H4-1: Step2 切片覆盖率。"""
        with guard("record_slice_coverage: _rate = round(100.0 * covered_slices", log=_log):
            _rate = round(100.0 * covered_slices / max(total_slices, 1), 1)
            self.record(
                project,
                run_id,
                step=2,
                metric_type="slice_coverage",
                metric_name="coverage_rate",
                category="quality",
                value=_rate,
                total=100,
                details={
                    "total": total_slices,
                    "covered": covered_slices,
                    "retry_rounds": retry_rounds,
                },
            )
            self.record(
                project,
                run_id,
                step=2,
                metric_type="slice_coverage",
                metric_name="uncovered",
                category="gap",
                value=total_slices - covered_slices,
                total=total_slices,
            )

    def record_control_type_hit(
        self, project: str, run_id: str, hit_counts: dict, total_calls: int
    ) -> None:
        """H4-2: Step3 control_type 命中率。"""
        with guard("record_control_type_hit: _default_count = hit_counts.get('but", log=_log):
            _default_count = hit_counts.get("button", 0)
            _hit_count = total_calls - _default_count
            _rate = round(100.0 * _hit_count / max(total_calls, 1), 1)
            self.record(
                project,
                run_id,
                step=3,
                metric_type="control_type",
                metric_name="hit_rate",
                category="quality",
                value=_rate,
                total=100,
                details={"hit_counts": hit_counts, "total_calls": total_calls},
            )
            self.record(
                project,
                run_id,
                step=3,
                metric_type="control_type",
                metric_name="total_calls",
                category="count",
                value=total_calls,
            )

    def record_parallel_exec(
        self,
        project: str,
        run_id: str,
        max_parallel: int,
        total_cases: int,
        passed: int,
        failed: int,
        elapsed_sec: float,
    ) -> None:
        """H4-3: Step4 并行执行指标。"""
        with guard("record_parallel_exec: _throughput = round(total_cases / ma", log=_log):
            _throughput = round(total_cases / max(elapsed_sec, 0.1), 2)
            self.record(
                project,
                run_id,
                step=4,
                metric_type="parallel_exec",
                metric_name="max_parallel",
                category="concurrency",
                value=max_parallel,
            )
            self.record(
                project,
                run_id,
                step=4,
                metric_type="parallel_exec",
                metric_name="throughput",
                category="perf",
                value=_throughput,
                details={"total": total_cases, "elapsed_sec": elapsed_sec},
            )
            self.record(
                project,
                run_id,
                step=4,
                metric_type="parallel_exec",
                metric_name="passed",
                category="result",
                value=passed,
            )
            self.record(
                project,
                run_id,
                step=4,
                metric_type="parallel_exec",
                metric_name="failed",
                category="result",
                value=failed,
            )

    def record_manual_retest(
        self,
        project: str,
        run_id: str,
        uncovered_reqs: int,
        failed_cases: int,
        flaky_cases: int,
        undecidable_cases: int,
        risk_counts: dict,
    ) -> None:
        """H4-4: Step5 人工补测清单。"""
        with guard("record_manual_retest: _total = sum(risk_counts.values())", log=_log):
            _total = sum(risk_counts.values())
            self.record(
                project,
                run_id,
                step=5,
                metric_type="manual_retest",
                metric_name="total",
                category="gap",
                value=_total,
                details={
                    "uncovered_reqs": uncovered_reqs,
                    "failed_cases": failed_cases,
                    "flaky_cases": flaky_cases,
                    "undecidable_cases": undecidable_cases,
                    "risk_counts": risk_counts,
                },
            )
            for _risk, _cnt in risk_counts.items():
                self.record(
                    project,
                    run_id,
                    step=5,
                    metric_type="manual_retest",
                    metric_name=_risk.lower() + "_count",
                    category="risk",
                    value=_cnt,
                )

    def record_hash_reuse(
        self, project: str, run_id: str, reused: int, total: int
    ) -> None:
        """H4-5: RAG 章节哈希复用率。"""
        with guard("record_hash_reuse: _rate = round(100.0 * reused / max(t", log=_log):
            _rate = round(100.0 * reused / max(total, 1), 1)
            self.record(
                project,
                run_id,
                step=2,
                metric_type="hash_reuse",
                metric_name="reuse_rate",
                category="incremental",
                value=_rate,
                total=100,
                details={
                    "reused": reused,
                    "total": total,
                    "new_embeddings": total - reused,
                },
            )

    def record_incremental_regen(
        self, project: str, run_id: str, total: int, success: int, failed: int
    ) -> None:
        """H4-6: 增量重生成功率。"""
        with guard("record_incremental_regen: _rate = round(100.0 * success / max(", log=_log):
            _rate = round(100.0 * success / max(total, 1), 1)
            self.record(
                project,
                run_id,
                step=4,
                metric_type="incremental_regen",
                metric_name="success_rate",
                category="quality",
                value=_rate,
                total=100,
                details={"total": total, "success": success, "failed": failed},
            )

    # ── CDP 客户端浏览器指标（2026-07-15）──────────────────────────
    # 记录客户端浏览器模式的连接、执行、断开等指标，
    # 用于对比 CDP 模式 vs 服务端模式的执行效果。

    def record_cdp_browser_session(
        self,
        project: str,
        run_id: str,
        mode: str,
        success: bool,
        connect_duration_ms: float = 0.0,
        endpoint: str = "",
        error: str = "",
        version_id: str = "",
    ) -> None:
        """记录 CDP 浏览器会话（连接/断开）。

        Args:
            mode: "attach"（连接）或 "detach"（断开）
            success: 是否成功
            connect_duration_ms: 连接耗时（毫秒），仅 attach 模式有意义
            endpoint: CDP 端点 URL
            error: 失败原因
            version_id: 版本标识（用于版本隔离）
        """
        with guard("record_cdp_browser_session: self.record( project, run_id, step=4", log=_log):
            self.record(
                project,
                run_id,
                step=4,
                metric_type="cdp_browser",
                metric_name=mode + "_count",
                category="session",
                value=1 if success else 0,
                total=1,
                details={
                    "endpoint": endpoint,
                    "error": error,
                    "duration_ms": round(connect_duration_ms, 1),
                },
                version_id=version_id,
            )
            if mode == "attach" and success:
                self.record(
                    project,
                    run_id,
                    step=4,
                    metric_type="cdp_browser",
                    metric_name="connect_duration_ms",
                    category="latency",
                    value=round(connect_duration_ms, 1),
                    version_id=version_id,
                )

    def record_cdp_browser_execution(
        self,
        project: str,
        run_id: str,
        case_id: str,
        success: bool,
        duration_ms: float = 0.0,
        action_count: int = 0,
        screenshot_size: int = 0,
        version_id: str = "",
    ) -> None:
        """记录 CDP 模式下单条用例的执行指标。

        Args:
            case_id: 测试用例 ID
            success: 用例是否通过
            duration_ms: 执行耗时（毫秒）
            action_count: 执行的 action 数量
            screenshot_size: 截图大小（字节）
            version_id: 版本标识（用于版本隔离）
        """
        with guard("record_cdp_browser_execution: self.record( project, run_id, step=4", log=_log):
            self.record(
                project,
                run_id,
                step=4,
                metric_type="cdp_browser",
                metric_name="case_executed",
                category="result",
                value=1 if success else 0,
                total=1,
                details={
                    "case_id": case_id,
                    "duration_ms": round(duration_ms, 1),
                    "action_count": action_count,
                },
                version_id=version_id,
            )
            self.record(
                project,
                run_id,
                step=4,
                metric_type="cdp_browser",
                metric_name="exec_duration_ms",
                category="latency",
                value=round(duration_ms, 1),
                version_id=version_id,
            )
            if action_count > 0:
                self.record(
                    project,
                    run_id,
                    step=4,
                    metric_type="cdp_browser",
                    metric_name="action_count",
                    category="count",
                    value=action_count,
                    version_id=version_id,
                )
            if screenshot_size > 0:
                self.record(
                    project,
                    run_id,
                    step=4,
                    metric_type="cdp_browser",
                    metric_name="screenshot_size",
                    category="evidence",
                    value=screenshot_size,
                    version_id=version_id,
                )

    def record_cdp_browser_error(
        self,
        project: str,
        run_id: str,
        case_id: str,
        error_type: str,
        error_message: str = "",
        version_id: str = "",
    ) -> None:
        """记录 CDP 模式下的执行错误。

        Args:
            error_type: 错误类型（如 "connect_failed"/"page_not_found"/"timeout"）
            error_message: 错误详情
            version_id: 版本标识（用于版本隔离）
        """
        with guard("record_cdp_browser_error: self.record( project, run_id, step=4", log=_log):
            self.record(
                project,
                run_id,
                step=4,
                metric_type="cdp_browser",
                metric_name="error_count",
                category="error",
                value=1,
                details={
                    "case_id": case_id,
                    "error_type": error_type,
                    "error_message": error_message[:500],
                },
                version_id=version_id,
            )
