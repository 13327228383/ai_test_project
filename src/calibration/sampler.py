# -*- coding: utf-8 -*-
"""★ P1-6: 校准样本采样器 — 人改回流到 calibration_samples 表。

职责边界：
  - 仅负责样本采集与写入，不做统计/训练逻辑
  - 提供 record_sample() 通用接口 + record_from_human_edit() 便捷封装
  - 降低 min_samples 门槛（冷启动允许少量样本）

通用性（§12.4）：
  1. 跨系统：verdict 字段为 PASS/FAIL 通用二分类
  2. 跨行业：features 是通用 JSON，不含业务术语
  3. 跨页面：不依赖页面结构
  4. 跨 Tab：不涉及 Tab
  5. 数据完整性：写入失败不阻断主流程（warning + 返回 False）
  6. 根因溯源：解决"人改不回流"导致系统无法从人工修正中学习
  7. 覆盖性：所有调用方统一走 record_sample() 入口
  8. 全局观：作为校准环入口，不修改现有审核流程
  9. 职责边界：仅采样，不决策（不改变 verdict）
"""
from __future__ import annotations
from src.core.guarded import guard

import json
import logging
from typing import Any, Optional

_log = logging.getLogger(__name__)

# ── 冷启动门槛（可配置，降低门槛支持少量样本启动）──
DEFAULT_MIN_SAMPLES = 3  # P1-6: 从原 10 降低到 3，支持冷启动

# ── 有效 verdict 值（白名单校验）──
_VALID_VERDICTS = {"PASS", "FAIL", "PENDING", "SKIP"}


def _normalize_verdict(v: Any) -> str:
    """归一化 verdict 为大写字符串（容错小写/混合大小写）。"""
    if not v:
        return ""
    s = str(v).strip().upper()
    # 容错常见变体
    if s in ("P", "PASS", "PASSED", "通过"):
        return "PASS"
    if s in ("F", "FAIL", "FAILED", "失败"):
        return "FAIL"
    if s in ("PEND", "PENDING", "待定"):
        return "PENDING"
    if s in ("SKIP", "SKIPPED", "跳过"):
        return "SKIP"
    return s if s in _VALID_VERDICTS else ""


def record_sample(
    project: str,
    version_id: int,
    case_id: str,
    predicted_verdict: str,
    human_verdict: str,
    features: Optional[dict[str, Any]] = None,
    source: str = "human_edit",
) -> bool:
    """记录一条校准样本到 calibration_samples 表。

    参数:
        project:           项目名
        version_id:        版本ID
        case_id:           关联用例ID
        predicted_verdict: 模型/系统预测结论（PASS/FAIL）
        human_verdict:     人工结论（PASS/FAIL）
        features:          特征向量（JSON，供校准模型，可选）
        source:            样本来源（human_edit/vlm_judgment/execution_result）

    返回:
        True=写入成功, False=写入失败（不抛异常，不阻断主流程）

    通用性：verdict 归一化后存储，features 通用 JSON。
    """
    # ★ P0-1 批B修复(2026-07-25): version_id=0 是合法 legacy 值（schema DEFAULT 0），
    #   不能用 `not version_id`（0 是 falsy 会被误拒）。
    #   根因: 原 `not version_id` 把 0 当缺失拒绝 → 与 schema 设计 0=legacy 不一致
    #         → op_cache_v2 用 `int(version_id or 0)` 兜底不拒 0，两表写入侧不一致
    #   通用性: `version_id is None or version_id < 0` 对所有 legacy 项目(vid=0)放行，
    #          仅拦截 None/负数（真正非法值），跨系统/跨版本通用
    #   合规: AGENTS.md §13.3 输入来源合规 — version_id 是 DB 自增ID，0=legacy 是约定
    if not project or version_id is None or version_id < 0 or not case_id:
        _log.warning(
            "[calibration.sampler] 参数缺失: project=%s vid=%s case=%s",
            project,
            version_id,
            case_id,
        )
        return False

    pred = _normalize_verdict(predicted_verdict)
    human = _normalize_verdict(human_verdict)
    if not pred or not human:
        _log.warning(
            "[calibration.sampler] verdict 归一化失败: pred=%s human=%s",
            predicted_verdict,
            human_verdict,
        )
        return False

    # 序列化 features
    features_json = None
    if features is not None:
        try:
            features_json = json.dumps(features, ensure_ascii=False)
        except (TypeError, ValueError) as e:
            _log.warning("[calibration.sampler] features 序列化失败: %s", str(e)[:80])
            features_json = None

    from src.core.db_manager import get_connection

    conn = None
    try:
        conn = get_connection()
        conn.execute(
            "INSERT INTO calibration_samples "
            "(project, version_id, case_id, predicted_verdict, human_verdict, features) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (project, version_id, case_id, pred, human, features_json),
        )
        conn.commit()
        _log.info(
            "[calibration.sampler] 样本写入: case=%s pred=%s human=%s source=%s",
            case_id,
            pred,
            human,
            source,
        )
        return True
    except Exception as e:
        _log.warning("[calibration.sampler] 写入失败: %s", str(e)[:100])
        if conn:
            try:
                conn.rollback()
            except Exception as _e:
                _log.debug("[sampler] 回滚异常: %s", _e, exc_info=True)
        return False
    finally:
        if conn:
            try:
                conn.close()
            except Exception as _e:
                _log.debug("[sampler] 关闭连接异常: %s", _e, exc_info=True)


def record_from_human_edit(
    project: str,
    version_id: int,
    case_id: str,
    before_verdict: str,
    after_verdict: str,
    features: Optional[dict[str, Any]] = None,
) -> bool:
    """便捷封装：从人审修改记录样本（before=predicted, after=human）。

    通用性：适用于审核端点（edit/lane/confirm）调用，记录"修改前→修改后"对。
    """
    return record_sample(
        project=project,
        version_id=version_id,
        case_id=case_id,
        predicted_verdict=before_verdict,
        human_verdict=after_verdict,
        features=features,
        source="human_edit",
    )


def record_from_vlm(
    project: str,
    version_id: int,
    case_id: str,
    vlm_verdict: str,
    human_verdict: str,
    features: Optional[dict[str, Any]] = None,
) -> bool:
    """便捷封装：从 VLM 判定记录样本（vlm=predicted, human=human）。

    通用性：适用于 VLM oracle 与人工结论对比场景。
    """
    return record_sample(
        project=project,
        version_id=version_id,
        case_id=case_id,
        predicted_verdict=vlm_verdict,
        human_verdict=human_verdict,
        features=features,
        source="vlm_judgment",
    )


def get_min_samples() -> int:
    """获取冷启动最小样本数门槛（可配置）。"""
    with guard("get_min_samples: from src.core.config_loader import c", log=_log):
        from src.core.config_loader import config

        cal = getattr(config, "calibration", None)
        if cal is not None:
            return int(getattr(cal, "min_samples", DEFAULT_MIN_SAMPLES))
    return DEFAULT_MIN_SAMPLES
