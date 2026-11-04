# -*- coding: utf-8 -*-
from __future__ import annotations
from src.core.guarded import guard

"""
测试用例表 写入操作
"""

import json
import logging
from typing import Any

_log = logging.getLogger(__name__)


def write_test_cases(test_cases, normalize_fn):
    """★ MySQL-only：全量写入 test_cases 表，不再生成 Excel。

    Args:
        test_cases: 测试用例列表
        normalize_fn: 归一化回调函数（替代 self._normalize_intent_for_write）
    """
    try:
        from src.pipeline.utils.case_store import case_store

        normalized = [normalize_fn(c) for c in test_cases]
        if not case_store.upsert_intents(normalized, case_asset_file=""):
            raise RuntimeError("test_cases 写入 MySQL 失败")
        # ★ 同步写入 intent→req 映射
        from src.pipeline.utils.case_requirement_tracker import case_requirement_tracker

        for c in normalized:
            if isinstance(c, dict) and c.get("case_id") and c.get("req_id"):
                try:
                    case_requirement_tracker.add(c["case_id"], c["req_id"])
                except Exception as _e:
                    _log.debug("import + ai + import: %s", _e, exc_info=True)
        _log.info("[MySQL] test_cases 已写入: %d 条", len(normalized))
        return len(normalized)
    except Exception as e:
        _log.info("[ExcelHandler] test_cases 写入 MySQL 失败: %s", str(e)[:80])
        return 0


def init_test_cases_file():
    """★ MySQL-only：清空当前项目 test_cases/case_requirement_mapping，不再创建空 Excel。"""
    with guard("init_test_cases_file: from src.pipeline.utils.case_store i", log=_log, level=logging.INFO):
        from src.pipeline.utils.case_store import case_store
        from src.pipeline.utils.case_requirement_tracker import case_requirement_tracker

        case_store.clear_project_intents()
        case_requirement_tracker.clear()
        _log.info("[MySQL] test_cases/case_requirement_mapping 已重置")
    return ""


def append_intents(intents, normalize_fn):
    """★ MySQL-only：追加测试用例到 test_cases 表，不再操作 Excel。

    Args:
        intents: 测试用例列表
        normalize_fn: 归一化回调函数（替代 self._normalize_intent_for_write）
    """
    if not intents:
        return 0
    try:
        from src.pipeline.utils.case_store import case_store
        from src.pipeline.utils.case_requirement_tracker import case_requirement_tracker

        normalized = [normalize_fn(c) for c in intents]
        if not case_store.upsert_intents(normalized, case_asset_file=""):
            raise RuntimeError("test_cases 追加写入 MySQL 失败")
        for c in normalized:
            if isinstance(c, dict) and c.get("case_id") and c.get("req_id"):
                try:
                    case_requirement_tracker.add(c["case_id"], c["req_id"])
                except Exception as _e:
                    _log.debug("import + import + ai: %s", _e, exc_info=True)
        _log.info("[MySQL] test_cases 已追加: %d 条", len(normalized))
        return len(normalized)
    except Exception as e:
        _log.info("[ExcelHandler] test_cases 追加写入 MySQL 失败: %s", str(e)[:80])
        return 0


def parse_and_write_intents(
    req_id, ai_text, ai_meta, write_meta_fn, ensure_schema_fn=None
):
    """v9: 解析 AI 返回的 JSON 并写入 MySQL test_cases。
    ensure_schema_fn 已废弃（v9 intent 自身即为 schema），保留参数签名兼容。
    """
    try:
        data = json.loads(ai_text) if isinstance(ai_text, str) else ai_text
    except json.JSONDecodeError:
        _log.warning("    [ExcelHandler] AI 返回非 JSON，跳过")
        return 0
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        return 0
    # ★ 构建 req_id → module 缓存（避免每条用例重复查询）
    _module_cache = {}
    try:
        from src.core.excel_handler import excel_handler
        from src.core.excel_normalizer import lookup_module

        for c in data:
            if isinstance(c, dict) and not c.get("catalog_name"):
                _rid = c.get("req_id", req_id)
                if _rid:
                    _module = lookup_module(
                        _rid, excel_handler.read_requirements, _module_cache
                    )
                    if _module:
                        c["catalog_name"] = _module
    except Exception as _e:
        _log.debug("catalog_name 回填失败: %s", _e, exc_info=True)
    for c in data:
        if isinstance(c, dict) and req_id and not c.get("req_id"):
            c["req_id"] = req_id
        if isinstance(c, dict):
            if ensure_schema_fn:
                ensure_schema_fn(c, req_id=req_id)
    count = len([c for c in data if isinstance(c, dict) and c.get("case_id")])
    if count > 0:
        from src.pipeline.utils.case_requirement_tracker import case_requirement_tracker
        from src.pipeline.utils.case_store import case_store

        if not case_store.upsert_intents(data, case_asset_file=""):
            raise RuntimeError("test_cases 写入 MySQL 失败，已中止测试用例生成")
        # ★ 计算置信度 + 写入 MySQL 元数据
        conf_scores = {}
        risk_levels = {}
        mapping_failed = []
        meta_failed = []
        if ai_meta and isinstance(ai_meta, dict):
            conf_scores = ai_meta.get("confidence_scores", {})
            risk_levels = ai_meta.get("risk_levels", {})
        # ★ v2: 计算意图驱动指标（给每个测试用例打分）
        case_metrics_map: dict[str, dict[str, Any]] = {}
        with guard("parse_and_write_intents: from src.pipeline.steps.step3.case_q", log=_log, level=logging.WARNING):
            from src.pipeline.steps.step3.case_quality import compute_case_metrics

            for c in data:
                if isinstance(c, dict) and c.get("case_id"):
                    m = compute_case_metrics(c)
                    if m:
                        case_metrics_map[str(c["case_id"]).strip()] = m
        # ★ v2: 批量写入意图指标到 case_meta
        if case_metrics_map:
            with guard("parse_and_write_intents: from src.pipeline.utils.review_store", log=_log, level=logging.WARNING):
                from src.pipeline.utils.review_store import review_store

                review_store.write_case_metrics_batch(case_metrics_map)
        for c in data:
            if isinstance(c, dict) and c.get("case_id"):
                cid = c["case_id"]
                if not case_requirement_tracker.add(cid, req_id):
                    mapping_failed.append(str(cid))
                # 写入置信度元数据
                _score = conf_scores.get(cid, 75)
                _risk = risk_levels.get(cid, "medium")
                _intent_m = case_metrics_map.get(str(cid).strip())
                if not write_meta_fn(cid, _score, _risk, case_metrics=_intent_m):
                    meta_failed.append(str(cid))
        if meta_failed:
            _log.error(
                "    [ExcelHandler] 测试用例元数据写入失败: %s",
                ",".join(meta_failed[:10]),
            )
        if mapping_failed:
            raise RuntimeError(
                "测试用例已写入 MySQL test_cases，但 intent→req 映射写入失败: %s。"
                "为避免覆盖率/执行/报告错位，已中止后续流程，请修复 MySQL 后重跑。"
                % ",".join(mapping_failed[:10])
            )
        _log.info("    [MySQL] 已写入 %d 条测试用例", count)
    return count


def write_case_meta(
    case_id: str,
    confidence: int,
    risk_level: str,
    case_asset_path,
    case_metrics: dict[str, Any] | None = None,
    risk_embedding: list | None = None,
    method_valid: int | None = None,
    method_fix_note: str = "",
) -> bool:
    """通过 review_store 写入测试用例元数据（置信度/风险等级/意图指标）。

    ★ T2 流水线强化专项(2026-08-24): 透传 method_valid / method_fix_note
      （Step3 方法论白名单校验结果持久化到 case_meta 两列）。

    Args:
        case_id: 意图 ID
        confidence: 置信度
        risk_level: 风险等级
        case_asset_path: 意图资产路径（替代 self._case_asset_path）
        case_metrics: 意图指标字典
        risk_embedding: 需求文本的 embedding 向量(384维)，用于 k-NN 风险分类器
        method_valid: ★T2 方法论标签校验是否通过（1/0，None 按 1 处理）
        method_fix_note: ★T2 方法论标签修正/标记说明
    """
    from src.pipeline.utils.review_store import review_store

    case_asset_file = case_asset_path.name if case_asset_path else ""
    return bool(
        review_store.write_case_meta(
            str(case_id),
            int(confidence),
            str(risk_level or "medium"),
            case_asset_file=case_asset_file,
            case_metrics=case_metrics,
            risk_embedding=risk_embedding,
            method_valid=method_valid,
            method_fix_note=method_fix_note,
        )
    )
