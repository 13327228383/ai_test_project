# -*- coding: utf-8 -*-
from __future__ import annotations

"""
FlakyGuard 插件 — Flaky Test 检测与修复
=======================================
解决 P0-5: 不稳定测试污染测试套件。

核心能力：
- Flaky 检测：同一测试用例多次执行结果不一致
- 根因分类：区分环境问题、时序问题、数据问题、AI 幻觉
- 自动修复建议：根据根因给出修复策略
- 隔离标记：将 Flaky 测试用例标记为隔离状态，不阻断流水线

介入点：AFTER_INTENT / ON_FAILURE
"""

import logging
from typing import Any
from datetime import datetime

_log = logging.getLogger(__name__)


class FlakyGuardPlugin:
    """Flaky Test 检测与修复插件。"""

    name = "flaky_guard"
    enabled = True
    config = {}

    def __init__(self, config: dict[str, Any] = None):
        self.config = config or {}
        self.enabled = self.config.get("enabled", True)

        # Flaky 判定配置
        self._min_runs_for_detection = self.config.get("min_runs_for_detection", 3)
        self._flaky_threshold = self.config.get(
            "flaky_threshold", 0.3
        )  # 失败率 30% 视为 Flaky
        self._auto_retry_count = self.config.get("auto_retry_count", 2)

        # 历史记录
        self._intent_history: dict[str, list[dict]] = {}  # case_id → [run_results]
        self._flaky_intents: dict[str, dict] = {}  # case_id → flaky_info

        # 统计
        self._stats = {
            "total_cases_tracked": 0,
            "flaky_detected": 0,
            "flaky_fixed": 0,
            "flaky_isolated": 0,
            "auto_retries": 0,
        }

        # ★ 从 MySQL 加载历史执行记录与已检测 Flaky 用例
        self._load_history_from_db()
        self._load_detected_from_db()

    def on_after_intent(self, ctx: dict[str, Any]) -> dict[str, Any] | None:
        """
        测试用例执行后：记录结果并检测 Flaky。

        ctx 包含:
            intent: dict
            case_id: str
            passed: bool
            intent_step_results: list
            failure_detail: str
        """
        if not self.enabled:
            return None
        try:
            from src.core.config_loader import config

            if not config.flaky_detection_enabled:
                return None
        except Exception as _ce:
            _log.warning("[flaky_guard] 配置读取失败,默认放行: %s", _ce, exc_info=True)

        case_id = ctx.get("case_id", "")
        passed = ctx.get("passed", False)
        failure_detail = ctx.get("failure_detail", "")

        if not case_id:
            return None

        self._stats["total_cases_tracked"] += 1

        # 记录本次执行结果
        step_results = ctx.get("intent_step_results") or ctx.get("step_results") or []
        run_record = {
            "timestamp": datetime.now().isoformat(),
            "passed": passed,
            "failure_detail": failure_detail,
            "step_count": len(step_results),
        }

        if case_id not in self._intent_history:
            self._intent_history[case_id] = []
        self._intent_history[case_id].append(run_record)

        # 保留最近 20 次记录
        if len(self._intent_history[case_id]) > 20:
            self._intent_history[case_id] = self._intent_history[case_id][-20:]

        # ★ 持久化到 MySQL
        self._save_run_record_to_db(case_id, run_record)

        # 检测 Flaky
        flaky_info = self._detect_flaky(case_id)
        if flaky_info:
            self._stats["flaky_detected"] += 1
            self._flaky_intents[case_id] = flaky_info

            # 根因分析
            root_cause = self._analyze_root_cause(case_id, flaky_info)
            fix_suggestion = self._suggest_fix(root_cause, case_id)

            _log.warning(
                "[FlakyGuard] ⚠ 检测到 Flaky: %s (失败率=%.1f%%, 根因=%s, 建议=%s)",
                case_id,
                flaky_info["failure_rate"] * 100,
                root_cause,
                fix_suggestion,
            )

            # ★ 持久化检测结果
            self._save_detected_to_db(case_id, flaky_info, root_cause, fix_suggestion)

            return {
                "flaky_detected": True,
                "flaky_case_id": case_id,
                "flaky_failure_rate": flaky_info["failure_rate"],
                "flaky_root_cause": root_cause,
                "flaky_fix_suggestion": fix_suggestion,
                "flaky_isolated": True,  # 标记为隔离，不阻断流水线
            }

        return None

    def on_failure(self, ctx: dict[str, Any]) -> dict[str, Any] | None:
        """
        失败时：自动重试并记录。

        ctx 包含:
            intent: dict
            case_id: str
            failure_detail: str
            step: dict
        """
        if not self.enabled:
            return None
        try:
            from src.core.config_loader import config

            if not config.flaky_detection_enabled:
                return None
        except Exception as _e:
            _log.debug("[flaky_guard] on_failure 异常: %s", _e, exc_info=True)

        case_id = ctx.get("case_id", "")
        failure_detail = ctx.get("failure_detail", "")

        # 检查是否是已知 Flaky
        if case_id in self._flaky_intents:
            flaky_info = self._flaky_intents[case_id]
            _log.info("[FlakyGuard] 已知 Flaky 测试用例失败: %s (已隔离)", case_id)
            return {
                "flaky_known": True,
                "flaky_case_id": case_id,
                "flaky_failure_rate": flaky_info["failure_rate"],
                "flaky_should_retry": False,  # 已知 Flaky 不再重试
            }

        return None

    def _detect_flaky(self, case_id: str) -> dict | None:
        """检测测试用例是否为 Flaky。"""
        history = self._intent_history.get(case_id, [])
        if len(history) < self._min_runs_for_detection:
            return None

        total = len(history)
        failures = sum(1 for r in history if not r["passed"])
        failure_rate = failures / total

        # 失败率在阈值范围内 → Flaky
        if 0 < failure_rate < 1.0 and failure_rate >= self._flaky_threshold:
            return {
                "case_id": case_id,
                "total_runs": total,
                "failures": failures,
                "failure_rate": failure_rate,
                "first_seen": history[0]["timestamp"],
                "last_seen": history[-1]["timestamp"],
            }

        return None

    def _analyze_root_cause(self, case_id: str, flaky_info: dict) -> str:
        """分析 Flaky 的根因。"""
        history = self._intent_history.get(case_id, [])
        if not history:
            return "unknown"

        # 收集失败详情
        failure_details = [r["failure_detail"] for r in history if not r["passed"]]
        if not failure_details:
            return "unknown"

        # 根因分类规则（优先级：环境 > 时序 > 数据 > AI 幻觉）
        detail_text = " ".join(failure_details).lower()

        # 环境问题（优先检查，因为 connection timeout 包含 timeout）
        if any(
            kw in detail_text
            for kw in ["503", "502", "504", "refused", "unreachable", "网络"]
        ):
            return "environment"
        # connection error 但不是 timeout
        if "connection" in detail_text and "timeout" not in detail_text:
            return "environment"

        # 时序问题
        if any(
            kw in detail_text
            for kw in ["timeout", "等待", "wait", "not visible", "stale"]
        ):
            return "timing"

        # 数据问题
        if any(
            kw in detail_text
            for kw in ["数据", "不存在", "duplicate", "constraint", "unique"]
        ):
            return "data"

        # AI 幻觉
        if any(
            kw in detail_text
            for kw in [
                "幻觉",
                "hallucination",
                "invalid action",
                "unknown element",
                "ai_error",
            ]
        ):
            return "ai_hallucination"

        # 如果失败详情各不相同 → 可能是环境不稳定
        unique_errors = set(failure_details)
        if len(unique_errors) > len(failure_details) * 0.5:
            return "environment"

        return "unknown"

    def _suggest_fix(self, root_cause: str, case_id: str) -> str:
        """根据根因给出修复建议。"""
        suggestions = {
            "timing": "增加显式等待/智能等待；检查异步加载；添加 retry 机制",
            "environment": "检查服务可用性；增加健康检查；使用 Mock 服务隔离依赖",
            "data": "使用独立测试数据；添加数据清理；使用事务回滚",
            "ai_hallucination": "增强 prompt 约束；添加 Ground Truth 校验；使用确定性路径",
            "unknown": "增加日志记录；人工分析失败模式；考虑拆分测试用例",
        }
        return suggestions.get(root_cause, "未知根因，建议人工分析")

    def get_flaky_intents(self) -> dict[str, dict]:
        """获取所有检测到的 Flaky 测试用例。"""
        return dict(self._flaky_intents)

    def get_intent_history(self, case_id: str) -> list[dict]:
        """获取指定测试用例的执行历史。"""
        return self._intent_history.get(case_id, [])

    def get_stats(self) -> dict[str, Any]:
        """获取 Flaky 检测统计。"""
        return {
            **self._stats,
            "flaky_intents_count": len(self._flaky_intents),
            "flaky_intents": list(self._flaky_intents.keys()),
        }

    def clear_history(self, case_id: str = None):
        """清除历史记录（用于测试或重置）。"""
        if case_id:
            self._intent_history.pop(case_id, None)
            self._flaky_intents.pop(case_id, None)
        else:
            self._intent_history.clear()
            self._flaky_intents.clear()

    # ------------------------------------------------------------------
    # MySQL 持久化
    # ------------------------------------------------------------------

    def _load_history_from_db(self):
        """从 MySQL 加载最近 20 条执行历史（按 case_id）。"""
        try:
            from src.core.config_loader import config as _cfg
            from src.core.versioning import current_version_id
            from src.core.db_manager import get_connection

            project = getattr(_cfg, "project_name", "") or ""
            if not project:
                return
            version_id = current_version_id(project)

            conn = None
            try:
                conn = get_connection()
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT case_id, passed, failure_detail, step_count, recorded_at
                    FROM flaky_guard_history
                    WHERE project=%s AND version_id=%s
                    ORDER BY recorded_at DESC
                    LIMIT 2000
                    """,
                    (project, version_id),
                )
                rows = cursor.fetchall()
                cursor.close()
            finally:
                if conn:
                    try:
                        conn.close()
                    except Exception as _e:
                        _log.debug("conn.close 失败: %s", _e, exc_info=True)

            # 按 case_id 分组，每 case 保留最近 20 条
            temp: dict[str, list[dict]] = {}
            for row in rows:
                cid = row.get("case_id", "")
                if not cid:
                    continue
                temp.setdefault(cid, []).append(
                    {
                        "timestamp": (
                            row.get("recorded_at", datetime.now()).isoformat()
                            if isinstance(row.get("recorded_at"), datetime)
                            else str(row.get("recorded_at") or "")
                        ),
                        "passed": bool(row.get("passed", 0)),
                        "failure_detail": row.get("failure_detail", "") or "",
                        "step_count": row.get("step_count", 0) or 0,
                    }
                )

            for cid, records in temp.items():
                self._intent_history[cid] = records[:20]
                self._intent_history[cid].reverse()  # 恢复时间正序
        except Exception as _e:
            _log.debug("[FlakyGuard] 加载历史记录失败: %s", _e, exc_info=True)

    def _save_run_record_to_db(self, case_id: str, run_record: dict):
        """将单条执行结果写入 MySQL。"""
        try:
            from src.core.config_loader import config as _cfg
            from src.backend.state import _thread_run_id
            from src.core.versioning import current_version_id
            from src.core.db_manager import get_connection

            project = getattr(_cfg, "project_name", "") or ""
            if not project:
                return
            version_id = current_version_id(project)
            run_id = getattr(_thread_run_id, "value", "") or ""

            conn = get_connection()
            cursor = conn.cursor()
            try:
                cursor.execute(
                    """
                    INSERT INTO flaky_guard_history
                    (run_id, project, version_id, case_id, passed, failure_detail, step_count)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        run_id,
                        project,
                        version_id,
                        case_id,
                        1 if run_record.get("passed") else 0,
                        run_record.get("failure_detail", "") or "",
                        run_record.get("step_count", 0) or 0,
                    ),
                )
                conn.commit()
            finally:
                cursor.close()
                conn.close()
        except Exception as _e:
            _log.debug("[FlakyGuard] 保存执行记录失败: %s", _e, exc_info=True)

    def _load_detected_from_db(self):
        """从 MySQL 加载已标记的 Flaky 用例。"""
        try:
            from src.core.config_loader import config as _cfg
            from src.core.versioning import current_version_id
            from src.core.db_manager import get_connection

            project = getattr(_cfg, "project_name", "") or ""
            if not project:
                return
            version_id = current_version_id(project)

            conn = None
            try:
                conn = get_connection()
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT case_id, failure_rate, total_runs, failures,
                           root_cause, first_seen, last_seen
                    FROM flaky_guard_detected
                    WHERE project=%s AND version_id=%s
                    """,
                    (project, version_id),
                )
                rows = cursor.fetchall()
                cursor.close()
            finally:
                if conn:
                    try:
                        conn.close()
                    except Exception as _e:
                        _log.debug("conn.close 失败: %s", _e, exc_info=True)

            for row in rows:
                cid = row.get("case_id", "")
                if not cid:
                    continue
                self._flaky_intents[cid] = {
                    "case_id": cid,
                    "total_runs": row.get("total_runs", 0),
                    "failures": row.get("failures", 0),
                    "failure_rate": float(row.get("failure_rate", 0.0) or 0.0),
                    "first_seen": str(row.get("first_seen") or ""),
                    "last_seen": str(row.get("last_seen") or ""),
                    "root_cause": row.get("root_cause", "unknown"),
                }
        except Exception as _e:
            _log.debug("[FlakyGuard] 加载检测结果失败: %s", _e, exc_info=True)

    def _save_detected_to_db(
        self, case_id: str, flaky_info: dict, root_cause: str, fix_suggestion: str
    ):
        """将检测到的 Flaky 用例写入 MySQL（幂等更新）。"""
        try:
            from src.core.config_loader import config as _cfg
            from src.core.versioning import current_version_id
            from src.core.db_manager import get_connection

            project = getattr(_cfg, "project_name", "") or ""
            if not project:
                return
            version_id = current_version_id(project)

            conn = get_connection()
            cursor = conn.cursor()
            try:
                cursor.execute(
                    """
                    INSERT INTO flaky_guard_detected
                    (project, version_id, case_id, failure_rate, total_runs, failures,
                     root_cause, fix_suggestion, first_seen, last_seen)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
                    ON DUPLICATE KEY UPDATE
                        failure_rate=VALUES(failure_rate),
                        total_runs=VALUES(total_runs),
                        failures=VALUES(failures),
                        root_cause=VALUES(root_cause),
                        fix_suggestion=VALUES(fix_suggestion),
                        last_seen=NOW()
                    """,
                    (
                        project,
                        version_id,
                        case_id,
                        float(flaky_info.get("failure_rate", 0.0) or 0.0),
                        int(flaky_info.get("total_runs", 0) or 0),
                        int(flaky_info.get("failures", 0) or 0),
                        root_cause,
                        fix_suggestion,
                    ),
                )
                conn.commit()
            finally:
                cursor.close()
                conn.close()
        except Exception as _e:
            _log.debug("[FlakyGuard] 保存检测结果失败: %s", _e, exc_info=True)
