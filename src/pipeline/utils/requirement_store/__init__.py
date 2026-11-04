# -*- coding: utf-8 -*-
"""
需求 MySQL 镜像存储（包入口）
==============================

MySQL 唯一事实源（第一阶段已完成主链路迁移）。
本模块负责 requirements 表的读写，并提供 health_stats() 供一致性健康检查使用。

【2026-06-18 重构】原 529 行单文件已按职责拆为：
  crud.py     — RequirementStoreCrudMixin（upsert/list/get/update/reset/delete）
  excel_io.py — _ExcelMixin（export_to_excel + REQ_EXPORT_COLUMNS）
  health.py   — _HealthMixin（health_stats 只读聚合）
  __init__.py — RequirementStore facade（基础工具/常量）+ 单例
"""

import json
import logging
import threading
from typing import Any

from .crud import RequirementStoreCrudMixin
from .excel_io import _ExcelMixin, REQ_EXPORT_COLUMNS
from .health import _HealthMixin

_log = logging.getLogger(__name__)


# ★ 性能优化：DDL 只跑一次（首个连接触发），后续请求直接跳过
_requirement_tables_ensured = False


class RequirementStore(RequirementStoreCrudMixin, _ExcelMixin, _HealthMixin):
    """MySQL requirements 表读写工具。"""

    def __init__(self, db_getter=None):
        self._last_error = ""
        self._db_getter = db_getter
        self._lock = (
            threading.RLock()
        )  # ★ 修复死锁：update_requirement 在持锁时调用 upsert_requirements，需要可重入锁

    def _get_db(self):
        if self._db_getter is not None:
            return self._db_getter()
        from src.core.db_manager import get_connection

        return get_connection()

    @staticmethod
    def _ensure_tables(c) -> None:
        """占位函数（2026-06-22：requirements 表已统一由 src/core/schema.sql 负责）。"""
        global _requirement_tables_ensured
        if _requirement_tables_ensured:
            return
        _requirement_tables_ensured = True
        return

    @staticmethod
    def _to_json(value: Any) -> str:
        try:
            return json.dumps(value, ensure_ascii=False, default=str)
        except Exception as e:
            _log.warning("[Store] _to_json 序列化失败，回退为 str: %s", e)
            return json.dumps(str(value), ensure_ascii=False)

    @staticmethod
    def _to_bool_int(value: Any) -> int:
        if isinstance(value, bool):
            return 1 if value else 0
        if isinstance(value, (int, float)):
            return 1 if value else 0
        if isinstance(value, str):
            return (
                1 if value.strip().lower() in ("true", "1", "yes", "y", "已生成") else 0
            )
        return 0

    @staticmethod
    def _parse_row_to_req(item: dict[str, Any]) -> dict[str, Any]:
        """将数据库行字典解析为需求字典。提取公共逻辑，避免重复代码。

        NOTE: 此方法与 case_store.py 中的行解析逻辑结构相似，
        属于有意为之的模块内聚设计，未做跨模块抽象。
        """
        try:
            payload = json.loads(item.get("payload_json") or "{}")
        except Exception as e:
            _log.warning("[Store] payload_json 解析失败，使用空字典: %s", e)
            payload = {}
        req = dict(payload) if isinstance(payload, dict) else {}
        # ★ 通用修复(2026-07-20): project 是核心隔离字段，必须拷回 req 字典
        #   原因：fallback 用例生成器(_build_rule_fallback_cases)需要 req.project 来查询
        #         nav_graph_nodes 表（按 project 隔离的导航地图，原 system_nav_maps 已于 2026-08-05 DROP），project 为空时无法
        #         查到 nav_map，导致 fallback 用例 URL 为空、Step4 无法 URL 直接导航。
        #   通用性：project 是所有跨表查询的隔离键，缺失会导致任何 DB 兜底查询失败。
        for key in (
            "req_id",
            "module",
            "description",
            "dependency_req_id",
            "review_status",
            "project",
        ):
            if item.get(key) not in (None, ""):
                req[key] = item.get(key)
        req["is_generated"] = bool(item.get("is_generated", 0))
        for key in (
            "version_id",
            "stable_req_key",
            "source_anchor",
            "content_hash",
            "semantic_hash",
            "change_type",
            "origin_req_id",
            "origin_version_id",
            "feature_id",
            "slice_id",
        ):
            if item.get(key) not in (None, ""):
                req[key] = item.get(key)
        # ★ 三元组 + 原文引用（schema 后加的列，必须从 DB 行拷回，否则前端详情面板拿不到）
        for key in (
            "operation",
            "condition_expr",
            "expected_feedback",
            "source_quote",
            "is_implicit",
            "contains_sensitive_data",
        ):
            if item.get(key) not in (None, ""):
                req[key] = item.get(key)
        # ★ 期望反馈的唯一收口：库里的 expected_feedback 可能是**推断产物**
        #   （旧版本按 operation 关键词猜的，与需求语义可能相反），下游直接当需求
        #   义务用会让断言与需求反向 → 系统性假 FAIL（实测 REQ-040「登录失败…」
        #   却存"登录成功,欢迎"、REQ-041「不允许新增…403」却存"新增成功…"、
        #   REQ-042/043/045 只读需求却存"操作成功"）。
        #   此处统一经 `resolve_expected_feedback` 落地（极性自洽 → 需求自述 → 推断），
        #   全引擎只此一处，任何下游拿到的都是已收口的值。
        try:
            from src.pipeline.step3._rule_extractor_keywords import (
                resolve_expected_feedback as _resolve_feedback,
            )

            req["expected_feedback"] = _resolve_feedback(
                str(req.get("operation") or ""),
                str(req.get("expected_feedback") or ""),
                str(req.get("description") or ""),
            )
        except Exception as _fb_err:  # noqa: BLE001 —— 收口失败不阻断需求读取
            _log.debug("[Store] expected_feedback 收口跳过: %s", str(_fb_err)[:80])
        # ★ 根因修复(2026-09-20): 证据字段断链 —— requirements 表有 _verify_status /
        #   _evidence_confidence 列（Step2 需求源验证产出），但读取端未拷回，导致 Step5
        #   汇总 evidence_coverage / evidence_calibrated_confidence 基于需求降级时读到
        #   空值（fetch 到空 → 参数名缺失 → 恒 0）。此处补回两列，并派生
        #   _has_independent_evidence（真实"有独立验证来源"的信号），供 _collect_confidence_summary
        #   消费。通用性: 适用于所有项目，不依赖业务，仅把 DB 已存在的证据字段透传到内存对象。
        for key in ("_verify_status", "_evidence_confidence"):
            if item.get(key) not in (None, ""):
                req[key] = item.get(key)
        vs = str(req.get("_verify_status", "") or "").strip()
        # passed/passed_extended/partial_match = 需求原文被真实验证（有独立来源佐证）。
        # 仅以 _verify_status 为准：_evidence_confidence 是需求自身的 AI 信心分，并非
        # 独立验证信号，用于覆盖率会虚高到 100%，故不纳入。
        req["_has_independent_evidence"] = vs in (
            "passed",
            "passed_extended",
            "partial_match",
        )
        return req


requirement_store = RequirementStore()
