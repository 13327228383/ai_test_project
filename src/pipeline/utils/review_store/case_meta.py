# -*- coding: utf-8 -*-
from __future__ import annotations
from src.core.guarded import guard

"""
ReviewStore case_meta CRUD Mixin
==================================
测试用例元数据的写入、读取、更新、删除操作。
"""

from typing import Any
import logging

_log = logging.getLogger(__name__)

# ★ P1-6 接入(2026-07-25): 审核状态 → 校准 verdict 映射（通用二分类）
#   根因: sampler.record_from_human_edit 零调用者 → calibration_samples 0 行 → 校准环休眠
#   通用性(§12.4): verdict 映射基于通用审核语义（PASS=放行/FAIL=否定/PENDING=待定），
#                  不含业务术语，跨系统/行业/页面通用
#   职责边界: review_store 只做映射+触发，sampler 负责写入逻辑
_REVIEW_STATUS_TO_VERDICT: dict[str, str] = {
    "approved": "PASS",  # 人审认可 → PASS
    "modified": "PASS",  # 人审修改后放行 → PASS
    "rejected": "FAIL",  # 人审驳回 → FAIL
    "blocked": "FAIL",  # 系统阻塞 → FAIL
    "pending": "PENDING",  # 待定 → 无信息量，跳过
}


def _invalidate_intents_meta_cache():
    """清除 list_intents_with_meta 的 5 秒 TTL 缓存，确保审核状态变更后立即生效。"""
    try:
        from src.pipeline.utils.case_store.crud.read import _intents_meta_cache

        _intents_meta_cache.clear()
    except Exception as _e:
        _log.debug("[case_meta] 清除 _intents_meta_cache 失败: %s", _e, exc_info=True)


class _ReviewIntentMetaMixin:
    """ReviewStore case_meta 表 CRUD 操作。"""

    @staticmethod
    def _normalize_rate_01(value) -> float:
        """把 0-1 小数或 0-100 百分比统一归一化为 0-1 小数。"""
        if value is None:
            return 0.0
        r = float(value)
        if r > 1:
            r = r / 100
        return max(0.0, min(1.0, r))

    def write_case_meta(
        self,
        case_id: str,
        confidence: int,
        risk_level: str,
        case_asset_file: str = "",
        case_metrics: dict[str, Any] | None = None,
        risk_embedding: list[float] | None = None,
        method_valid: int | None = None,
        method_fix_note: str = "",
    ) -> bool:
        """写入/更新测试用例元数据（置信度 + 风险等级 + 意图指标 + 风险embedding）。
        保持已有审核字段：review_status / reviewed_by / reviewed_at。
        risk_embedding: 需求文本的 embedding 向量(384维)，用于 k-NN 风险分类器。

        ★ T2 流水线强化专项(2026-08-24): 新增 method_valid / method_fix_note
          两列（Step3 方法论白名单校验结果持久化，schema.sql 第46域附 ALTER）。
          缺省时写默认值 method_valid=1 / method_fix_note=''，不破坏既有调用方。
        """
        case_id = str(case_id or "").strip()
        if not case_id:
            self._last_error = "empty case_id"
            return False
        from src.core.config_loader import config

        project = config.project_name or ""
        from src.core.versioning import current_version_id

        version_id = current_version_id(project)
        case_metrics = case_metrics or {}
        import json as _json

        emb_json = (
            _json.dumps(risk_embedding, ensure_ascii=False) if risk_embedding else None
        )

        # ★ confidence 为 0-100 整数；数据库字段为 DECIMAL(5,2)
        try:
            _conf = float(confidence or 0)
        except Exception:
            _conf = 0.0
        _conf = round(max(0.0, min(100.0, _conf)), 2)

        # ★ T2: 方法论白名单校验结果（缺省 1=通过 / 空 fix_note，兼容旧调用方）
        _method_valid = 1 if method_valid is None else (1 if method_valid else 0)
        _method_fix_note = str(method_fix_note or "")[:255]

        try:
            c = self._get_db()
            self._ensure_tables(c)
            # ★ P0-4: 三车道路由（替 conf>=80→approved 二值自动审核）
            #   green(≥85)→approved / yellow([60,85))→pending / red(<60)→blocked
            #   与 step2.py:665 阈值一致，跨系统通用
            #   ON DUPLICATE KEY UPDATE 不覆盖已有 review_status（人审后不被覆盖）
            if _conf >= 85:
                _auto_status = "approved"
            elif _conf >= 60:
                _auto_status = "pending"
            else:
                _auto_status = "blocked"
            c.execute(
                """
                INSERT INTO case_meta(
                    case_id, project, version_id, case_asset_file, confidence, risk_level,
                    risk_embedding,
                    review_status, reviewed_by, reviewed_at,
                    case_coverage, case_completeness, evidence_total,
                    avg_confidence, case_driven_rate, strictness_score,
                    method_valid, method_fix_note
                )
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,'',NULL,%s,%s,%s,%s,%s,%s,%s,%s)
                ON DUPLICATE KEY UPDATE
                    case_asset_file=VALUES(case_asset_file),
                    confidence=VALUES(confidence),
                    risk_level=VALUES(risk_level),
                    risk_embedding=COALESCE(VALUES(risk_embedding), risk_embedding),
                    case_coverage=COALESCE(VALUES(case_coverage), case_coverage, 0),
                    case_completeness=COALESCE(VALUES(case_completeness), case_completeness, 0),
                    evidence_total=COALESCE(VALUES(evidence_total), evidence_total, 0),
                    avg_confidence=COALESCE(VALUES(avg_confidence), avg_confidence, 0),
                    case_driven_rate=COALESCE(VALUES(case_driven_rate), case_driven_rate, 0),
                    strictness_score=COALESCE(VALUES(strictness_score), strictness_score, 0),
                    method_valid=VALUES(method_valid),
                    method_fix_note=VALUES(method_fix_note)
                """,
                (
                    case_id,
                    project,
                    version_id,
                    str(case_asset_file or ""),
                    _conf,
                    str(risk_level or "medium"),
                    emb_json,
                    _auto_status,
                    (
                        float(case_metrics.get("case_coverage", 0))
                        if case_metrics.get("case_coverage") is not None
                        else None
                    ),
                    (
                        float(case_metrics.get("case_completeness", 0))
                        if case_metrics.get("case_completeness") is not None
                        else None
                    ),
                    (
                        float(case_metrics.get("evidence_total", 0))
                        if case_metrics.get("evidence_total") is not None
                        else None
                    ),
                    self._normalize_rate_01(case_metrics.get("avg_confidence")),
                    (
                        float(case_metrics.get("case_driven_rate", 0))
                        if case_metrics.get("case_driven_rate") is not None
                        else None
                    ),
                    (
                        float(case_metrics.get("strictness_score", 0))
                        if case_metrics.get("strictness_score") is not None
                        else None
                    ),
                    _method_valid,
                    _method_fix_note,
                ),
            )
            c.commit()
            self._last_error = ""
            _invalidate_intents_meta_cache()
            return True
        except Exception as e:
            self._last_error = str(e)
            _log.info("  [ReviewStore] case_meta 写入失败: %s", str(e)[:120])
            return False

        finally:
            if c:
                try:
                    c.close()
                except Exception as _e:
                    _log.debug("[case_meta] 非关键异常: %s", _e, exc_info=True)

    def write_case_metrics_batch(self, metrics_map: dict[str, dict[str, Any]]) -> bool:
        """批量写入意图指标（Step3 生成测试用例后调用）。
        metrics_map: { case_id -> {case_completeness, evidence_total, ...} }
        """
        if not metrics_map:
            return True
        from src.core.config_loader import config

        project = config.project_name or ""
        from src.core.versioning import current_version_id

        version_id = current_version_id(project)
        try:
            c = self._get_db()
            self._ensure_tables(c)
            for cid, m in metrics_map.items():
                if not cid or not m:
                    continue
                cid = str(cid).strip()
                ic = float(m.get("case_completeness", m.get("case_coverage", 0)))
                icov = float(m.get("case_coverage", 0))
                et = float(m.get("evidence_total", 0))
                ac = self._normalize_rate_01(m.get("avg_confidence", 0))
                idr = float(m.get("case_driven_rate", 0))
                ss = float(m.get("strictness_score", 0))
                c.execute(
                    "UPDATE case_meta SET case_completeness=%s, case_coverage=%s, evidence_total=%s, "
                    "avg_confidence=%s, case_driven_rate=%s, strictness_score=%s "
                    "WHERE case_id=%s AND project=%s AND version_id=%s",
                    (ic, icov, et, ac, idr, ss, cid, project, version_id),
                )
                if getattr(c, "rowcount", 0) == 0:
                    c.execute(
                        "INSERT IGNORE INTO case_meta(case_id, project, version_id, case_asset_file, "
                        "case_completeness, case_coverage, evidence_total, avg_confidence, case_driven_rate, strictness_score) "
                        "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        (cid, project, version_id, "", ic, icov, et, ac, idr, ss),
                    )
            c.commit()
            self._last_error = ""
            return True
        except Exception as e:
            self._last_error = str(e)
            _log.info("  [ReviewStore] 意图指标批量写入失败: %s", str(e)[:120])
            return False

        finally:
            if c:
                try:
                    c.close()
                except Exception as _e:
                    _log.debug("[case_meta] 非关键异常: %s", _e, exc_info=True)

    def update_avg_confidence(self, case_id: str, avg_confidence: float) -> bool:
        """更新平均置信度（Step4 执行后动态回写）。"""
        case_id = str(case_id or "").strip()
        if not case_id:
            return False
        from src.core.config_loader import config

        project = config.project_name or ""
        from src.core.versioning import current_version_id

        version_id = current_version_id(project)
        try:
            c = self._get_db()
            self._ensure_tables(c)
            rate = self._normalize_rate_01(avg_confidence)
            c.execute(
                "UPDATE case_meta SET avg_confidence=%s "
                "WHERE case_id=%s AND project=%s AND version_id=%s",
                (rate, case_id, project, version_id),
            )
            if getattr(c, "rowcount", 0) == 0:
                c.execute(
                    "INSERT IGNORE INTO case_meta(case_id, project, version_id, case_asset_file, avg_confidence) "
                    "VALUES(%s,%s,%s,%s,%s)",
                    (case_id, project, version_id, "", rate),
                )
            c.commit()
            return True
        except Exception as e:
            # ★ F-14 (2026-08-04): 消除内层静默 — return False 同时记 error 级日志。
            #   根因(3层): ①原代码 except → return False 只写实例字段，DB 写失败被吞；
            #   ②外层 step4_executor.py:484 丢弃返回值 → 双层静默，日志连 warning 都没有；
            #   ③F-10 基线出现「无声数据缺失」，分母都是错的。
            #   修复: 记 error 级日志（含 _last_error 与 case_id），失败可见。
            self._last_error = str(e)
            _log.error(
                "[ReviewStore] avg_confidence 回写失败 case_id=%s project=%s version=%s: %s",
                case_id,
                locals().get("project") or "",
                locals().get("version_id") or "",
                str(e)[:200],
            )
            return False
        finally:
            try:
                c.close()
            except Exception as _e:
                _log.debug("[case_meta] 非关键异常: %s", _e, exc_info=True)

    def update_execution_factors(
        self, case_id: str, confidence: float, factors: dict, level: str = "low"
    ) -> bool:
        """★ v4.1: 写入 System B 执行置信度因子明细（Step4 执行后调用）。
        factors: {assertion_rate, case_completeness, hard_verify_rate, ...}
        """
        case_id = str(case_id or "").strip()
        if not case_id:
            return False
        from src.core.config_loader import config

        project = config.project_name or ""
        from src.core.versioning import current_version_id

        version_id = current_version_id(project)
        import json as _json

        try:
            c = self._get_db()
            self._ensure_tables(c)
            rate = self._normalize_rate_01(confidence)
            factors_json = _json.dumps(factors or {}, ensure_ascii=False)
            c.execute(
                "UPDATE case_meta SET avg_confidence=%s, execution_factors=%s "
                "WHERE case_id=%s AND project=%s AND version_id=%s",
                (rate, factors_json, case_id, project, version_id),
            )
            if getattr(c, "rowcount", 0) == 0:
                c.execute(
                    "INSERT IGNORE INTO case_meta(case_id, project, version_id, case_asset_file, "
                    "avg_confidence, execution_factors) VALUES(%s,%s,%s,%s,%s,%s)",
                    (case_id, project, version_id, "", rate, factors_json),
                )
            c.commit()
            return True
        except Exception as e:
            self._last_error = str(e)
            _log.info("  [ReviewStore] execution_factors 更新失败: %s", str(e)[:120])
            return False
        finally:
            try:
                c.close()
            except Exception as _e:
                _log.debug("[case_meta] 非关键异常: %s", _e, exc_info=True)

    def update_evidence_confidence(
        self,
        case_id: str,
        evidence_conf: float,
        ai_raw: float = 0.0,
        calibration_accuracy: float = None,
        evidence_strength: float = 0.0,
        confidence_level: str = "LOW",
        verdict: str = "ai_only",
        cold_start: bool = True,
    ) -> bool:
        """★ v4.1: 写入 System C 证据校准置信度（Step4 执行后批量调用）。"""
        case_id = str(case_id or "").strip()
        if not case_id:
            return False
        from src.core.config_loader import config

        project = config.project_name or ""
        from src.core.versioning import current_version_id

        version_id = current_version_id(project)
        try:
            c = self._get_db()
            self._ensure_tables(c)
            conf_val = self._normalize_rate_01(evidence_conf)
            c.execute(
                "UPDATE case_meta SET evidence_confidence=%s, evidence_ai_raw=%s, "
                "evidence_cal_accuracy=%s, evidence_strength=%s, evidence_level=%s, "
                "evidence_verdict=%s, evidence_cold_start=%s "
                "WHERE case_id=%s AND project=%s AND version_id=%s",
                (
                    conf_val,
                    round(float(ai_raw or 0), 3),
                    (
                        round(float(calibration_accuracy), 3)
                        if calibration_accuracy is not None
                        else None
                    ),
                    round(float(evidence_strength or 0), 3),
                    str(confidence_level or "LOW"),
                    str(verdict or "ai_only"),
                    1 if cold_start else 0,
                    case_id,
                    project,
                    version_id,
                ),
            )
            if getattr(c, "rowcount", 0) == 0:
                c.execute(
                    "INSERT IGNORE INTO case_meta(case_id, project, version_id, case_asset_file, "
                    "evidence_confidence, evidence_ai_raw, evidence_cal_accuracy, evidence_strength, "
                    "evidence_level, evidence_verdict, evidence_cold_start) "
                    "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        case_id,
                        project,
                        version_id,
                        "",
                        conf_val,
                        round(float(ai_raw or 0), 3),
                        (
                            round(float(calibration_accuracy), 3)
                            if calibration_accuracy is not None
                            else None
                        ),
                        round(float(evidence_strength or 0), 3),
                        str(confidence_level or "LOW"),
                        str(verdict or "ai_only"),
                        1 if cold_start else 0,
                    ),
                )
            c.commit()
            return True
        except Exception as e:
            self._last_error = str(e)
            _log.info("  [ReviewStore] evidence_confidence 更新失败: %s", str(e)[:120])
            return False
        finally:
            try:
                c.close()
            except Exception as _e:
                _log.debug("[case_meta] 非关键异常: %s", _e, exc_info=True)

    def update_strictness_score(self, case_id: str, strictness: float) -> bool:
        """更新严格性评分（Step4 执行后调用）。
        strictness = high/medium/optional 加权评分 (0-100)
        """
        case_id = str(case_id or "").strip()
        if not case_id:
            return False
        from src.core.config_loader import config

        project = config.project_name or ""
        from src.core.versioning import current_version_id

        version_id = current_version_id(project)
        try:
            c = self._get_db()
            self._ensure_tables(c)
            rate = max(0.0, min(100.0, float(strictness or 0)))
            c.execute(
                "UPDATE case_meta SET strictness_score=%s "
                "WHERE case_id=%s AND project=%s AND version_id=%s",
                (rate, case_id, project, version_id),
            )
            if getattr(c, "rowcount", 0) == 0:
                c.execute(
                    "INSERT IGNORE INTO case_meta(case_id, project, version_id, case_asset_file, strictness_score) "
                    "VALUES(%s,%s,%s,%s,%s)",
                    (case_id, project, version_id, "", rate),
                )
            c.commit()
            return True
        except Exception as e:
            _log.info("  [ReviewStore] strictness_score 更新失败: %s", str(e)[:120])
            return False

        finally:
            # ★ 修复（2026-08-06 QA 独立验证）：无 DB 配置时 _get_db() 抛异常，
            #   局部变量 c 从未赋值，原 `if c:` 直接 UnboundLocalError →
            #   finally 兜底失效且异常上抛（探针①在 step4_execute 真实路径必崩）。
            #   改用 try 安全读取，避免 finally 引用未定义变量。
            try:
                _c = c
            except Exception:
                _c = None
            if _c:
                try:
                    _c.close()
                except Exception as _e:
                    _log.debug("[case_meta] 非关键异常: %s", _e, exc_info=True)

    def update_case_review_status(
        self, case_id: str, status: str, reviewer: str = "", project: str = ""
    ) -> bool:
        """更新测试用例审核状态 + 审核人 + 审核时间。不存在时插入新行。"""
        case_id = str(case_id or "").strip()
        if not case_id:
            self._last_error = "empty case_id"
            return False
        status = self._normalize_status(status)
        reviewer = str(reviewer or "")
        from src.core.config_loader import config

        project = project or config.project_name or ""
        from src.core.versioning import current_version_id

        version_id = current_version_id(project)
        now = self._now()
        # ★ P1-6: 读取旧 review_status（作为 predicted_verdict，用于校准样本对比）
        _before_status = ""
        # ★ P2-12: 读取 case title（作为 golden_set scenario，人审结论注入真值用）
        _case_title = ""
        try:
            c = self._get_db()
            self._ensure_tables(c)
            try:
                rows = c.execute(
                    "SELECT version_id, title FROM test_cases WHERE project=%s AND case_id=%s "
                    "ORDER BY version_id DESC LIMIT 1",
                    (project, case_id),
                ).fetchall()
                if rows:
                    version_id = int(rows[0].get("version_id", version_id))
                    _case_title = str(rows[0].get("title", "") or "").strip()
            except Exception as _e:
                _log.debug("[case_meta] 非关键异常: %s", _e, exc_info=True)
            # ★ P1-6: SELECT 旧 review_status（UPDATE 前读取，作为校准样本 predicted_verdict）
            try:
                _old_row = c.execute(
                    "SELECT review_status FROM case_meta "
                    "WHERE case_id=%s AND project=%s AND version_id=%s",
                    (case_id, project, version_id),
                ).fetchone()
                if _old_row:
                    _before_status = str(
                        _old_row.get("review_status", "") or ""
                    ).strip()
            except Exception as _e:
                _log.debug(
                    "[case_meta] 读取旧 review_status 失败(不阻塞): %s",
                    _e,
                    exc_info=True,
                )
            c.execute(
                "UPDATE case_meta SET review_status=%s, reviewed_by=%s, reviewed_at=%s "
                "WHERE case_id=%s AND project=%s AND version_id=%s",
                (status, reviewer, now, case_id, project, version_id),
            )
            if getattr(c, "rowcount", 0) == 0:
                c.execute(
                    "INSERT IGNORE INTO case_meta(case_id, project, version_id, review_status, "
                    "reviewed_by, reviewed_at) VALUES(%s,%s,%s,%s,%s,%s)",
                    (case_id, project, version_id, status, reviewer, now),
                )
            c.commit()
            self._last_error = ""
            # ★ 清除 list_intents_with_meta 5 秒 TTL 缓存，确保审核状态变更后立即生效
            _invalidate_intents_meta_cache()
            # ★ P1-6 接入(2026-07-25): 审核状态变更后回流校准样本到 calibration_samples
            #   单一接入点覆盖所有审核端点（approve/reject/edit/lane/confirm/batch-approve）
            #   降级: sampler 失败不阻断审核状态写入（warning 记录）
            self._record_calibration_sample_safe(
                case_id, project, version_id, _before_status, status
            )
            # ★ P2-12 接入(2026-07-25): 从人审结论注入 golden_set 种子
            #   人审是真值来源 → approved→PASS / rejected→FAIL
            #   SelfEvalRunner 用 golden_set 对比引擎实际 verdict 计算 accuracy/false_pass_rate
            #   单一接入点覆盖所有审核端点（与 P1-6 校准样本回流对称）
            #   降级: 注入失败不阻断审核状态写入（warning 记录）
            self._inject_golden_set_seed_safe(
                case_id, project, version_id, status, _case_title
            )
            return True
        except Exception as e:
            self._last_error = str(e)
            _log.info(
                "  [ReviewStore] case_meta.review_status 更新失败: %s", str(e)[:120]
            )
            return False

        finally:
            if c:
                try:
                    c.close()
                except Exception as _e:
                    _log.debug("[case_meta] 非关键异常: %s", _e, exc_info=True)

    def _record_calibration_sample_safe(
        self,
        case_id: str,
        project: str,
        version_id: int,
        before_status: str,
        after_status: str,
    ) -> None:
        """★ P1-6: 安全地记录校准样本到 calibration_samples（失败不阻断主流程）。

        将审核状态映射为 verdict 后调用 sampler.record_from_human_edit：
          - before_status（系统预测）→ predicted_verdict
          - after_status（人审结论）→ human_verdict
          - PENDING 状态无信息量，跳过不记录

        通用性(§12.4):
          1. 跨系统: verdict 字段为 PASS/FAIL 通用二分类
          2. 跨行业: 不含业务术语
          3. 跨页面/Tab: 不依赖页面结构
          4. 降级安全: sampler 失败仅 warning，不抛异常
          5. 职责边界: review_store 只触发采样，sampler 负责写入
        """
        pred = _REVIEW_STATUS_TO_VERDICT.get(before_status, "")
        human = _REVIEW_STATUS_TO_VERDICT.get(after_status, "")
        # PENDING 无信息量，跳过；空值（未知状态）也跳过
        # ★ 根因修复(2026-09-05): 原守卫只判 human=="PENDING"，未判 pred。
        #   而 before_status(预测侧) 对黄车道用例几乎必然原态 pending→PENDING，
        #   导致所有样本 pred=PENDING vs human=PASS → 一致率恒为 0%（污染校准环）。
        #   PENDING 意为"无 PASS/FAIL 信号"，两侧任一侧为 PENDING 都不可比，
        #   故两侧都必须是真实 PASS/FAIL 才记录样本。
        if not pred or not human or human == "PENDING" or pred == "PENDING":
            return
        with guard("_record_calibration_sample_safe: from src.calibration.sampler import ", log=_log, level=logging.WARNING):
            # 延迟 import 避免循环依赖（sampler 反向 import review_store 会循环）
            from src.calibration.sampler import record_from_human_edit

            record_from_human_edit(
                project=project,
                version_id=version_id,
                case_id=case_id,
                before_verdict=pred,
                after_verdict=human,
                features=None,  # 后续可扩展特征向量
            )

    def _inject_golden_set_seed_safe(
        self,
        case_id: str,
        project: str,
        version_id: int,
        after_status: str,
        case_title: str,
    ) -> None:
        """★ P2-12: 安全地从人审结论注入 golden_set 种子（失败不阻断主流程）。

        人审结论是真值来源：
          - approved → expected_verdict=PASS
          - rejected → expected_verdict=FAIL
          - pending/modified/blocked → 无真值信息量，跳过
        SelfEvalRunner 用 golden_set 对比引擎实际 verdict，计算
        accuracy/false_pass_rate/false_fail_rate（§13.5 报告维度）。

        通用性(§12.4):
          1. 跨系统: scenario=case_title，通用文本不绑定业务
          2. 跨行业: 任何测试场景都适用
          3. 跨页面/Tab: 不依赖页面结构
          4. 降级安全: has_case 去重 + 失败仅 warning，不阻断审核
          5. 职责边界: review_store 只触发注入，GoldenSetStore 负责 CRUD
          6. 覆盖性: 单一接入点覆盖所有审核端点（与 P1-6 对称）
        """
        # 只有 approved/rejected 有真值信息量
        if after_status not in ("approved", "rejected"):
            return
        if not case_title:
            _log.debug("[P2-12] 跳过 golden_set 注入: 无 case_title, case=%s", case_id)
            return
        with guard("_inject_golden_set_seed_safe: from src.eval.golden import get_gold", log=_log, level=logging.WARNING):
            # 延迟 import 避免循环依赖
            from src.eval.golden import get_golden_store

            _store = get_golden_store()
            # 去重：已存在则跳过（避免多次审核导致重复注入）
            if _store.has_case(project, case_id):
                return
            expected_verdict = "PASS" if after_status == "approved" else "FAIL"
            new_id = _store.add_scenario(
                project=project,
                scenario=case_title,
                expected_verdict=expected_verdict,
                expected_confidence=0.95,  # 人审结论高置信度真值
                notes="Auto-injected from human review",
                case_id=case_id,
            )
            if new_id > 0:
                _log.info(
                    "[P2-12] golden_set 种子注入成功: case=%s verdict=%s",
                    case_id,
                    expected_verdict,
                )

    def mark_intent_modified(self, case_id: str) -> bool:
        """编辑测试用例后标记为已修改，不覆盖置信度/风险等级。"""
        return self.update_case_review_status(case_id, "modified", reviewer="")

    def update_case_meta_risk_level(self, case_id: str, risk_level: str) -> bool:
        """★ 只更新 case_meta.risk_level（high/medium/low），不触碰其它字段。

        用例编辑页的「风险等级(高/中/低)」与 test_cases.risk_level(P0/P1/P2 优先级方案)
        是两套独立概念：前者是审核/KPI 口径的风险，后者是优先级 P 方案。
        本方法只写审核口径的 case_meta.risk_level，避免污染 test_cases.risk_level。
        """
        case_id = str(case_id or "").strip()
        if not case_id:
            return False
        risk = str(risk_level or "").strip().lower()
        if risk not in ("high", "medium", "low"):
            _log.debug(
                "[case_meta] 非法 risk_level=%s 忽略, case=%s",
                risk_level,
                case_id,
            )
            return False
        from src.core.config_loader import config
        from src.core.versioning import current_version_id

        project = config.project_name or ""
        version_id = current_version_id(project)
        c = None
        try:
            c = self._get_db()
            self._ensure_tables(c)
            c.execute(
                "UPDATE case_meta SET risk_level=%s "
                "WHERE case_id=%s AND project=%s AND version_id=%s",
                (risk, case_id, project, version_id),
            )
            if getattr(c, "rowcount", 0) == 0:
                c.execute(
                    "INSERT IGNORE INTO case_meta(case_id, project, version_id, risk_level) "
                    "VALUES(%s,%s,%s,%s)",
                    (case_id, project, version_id, risk),
                )
            c.commit()
            self._last_error = ""
            _invalidate_intents_meta_cache()
            return True
        except Exception as e:
            self._last_error = str(e)
            _log.info(
                "  [ReviewStore] case_meta.risk_level 更新失败 case=%s: %s",
                case_id,
                str(e)[:120],
            )
            return False
        finally:
            if c:
                try:
                    c.close()
                except Exception as _e:
                    _log.debug("[case_meta] 非关键异常: %s", _e, exc_info=True)

    def update_intent_confidence(
        self, case_id: str, confidence: int, project: str = ""
    ) -> bool:
        """更新测试用例置信度评分。"""
        case_id = str(case_id or "").strip()
        if not case_id:
            self._last_error = "empty case_id"
            return False
        from src.core.config_loader import config

        project = project or config.project_name or ""
        from src.core.versioning import current_version_id

        version_id = current_version_id(project)
        _conf = self._normalize_rate_01(confidence)
        try:
            c = self._get_db()
            self._ensure_tables(c)
            c.execute(
                "UPDATE case_meta SET confidence=%s WHERE case_id=%s AND project=%s AND version_id=%s",
                (_conf, case_id, project, version_id),
            )
            c.commit()
            self._last_error = ""
            return True
        except Exception as e:
            self._last_error = str(e)
            _log.info("  [ReviewStore] case_meta.confidence 更新失败: %s", str(e)[:120])
            return False

        finally:
            if c:
                try:
                    c.close()
                except Exception as _e:
                    _log.debug("[case_meta] 非关键异常: %s", _e, exc_info=True)

    def list_case_meta(self) -> list[dict[str, Any]]:
        """读取当前项目当前版本全部测试用例元数据。"""
        from src.core.config_loader import config
        from src.core.versioning import current_version_id

        project = config.project_name or ""
        version_id = current_version_id(project)
        try:
            c = self._get_db()
            self._ensure_tables(c)
            rows = c.execute(
                "SELECT * FROM case_meta WHERE project=%s AND version_id=%s ORDER BY case_id",
                (project, version_id),
            ).fetchall()
            return [dict(row) for row in rows]
        except Exception as e:
            self._last_error = str(e)
            _log.info("  [ReviewStore] case_meta 批量读取失败: %s", str(e)[:120])
            return []

        finally:
            if c:
                try:
                    c.close()
                except Exception as _e:
                    _log.debug("[case_meta] 非关键异常: %s", _e, exc_info=True)

    def delete_case_meta(self, case_id: str) -> bool:
        """删除单条测试用例元数据（级联清理用。）"""
        case_id = str(case_id or "").strip()
        if not case_id:
            return False
        from src.core.config_loader import config
        from src.core.versioning import current_version_id

        project = config.project_name or ""
        version_id = current_version_id(project)
        try:
            c = self._get_db()
            self._ensure_tables(c)
            c.execute(
                "DELETE FROM case_meta WHERE case_id=%s AND project=%s AND version_id=%s",
                (case_id, project, version_id),
            )
            c.commit()
            return True
        except Exception as e:
            self._last_error = str(e)
            return False

        finally:
            if c:
                try:
                    c.close()
                except Exception as _e:
                    _log.debug("[case_meta] 非关键异常: %s", _e, exc_info=True)
