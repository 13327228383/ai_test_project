# -*- coding: utf-8 -*-
"""★ P1-6: 校准统计训练器 — 从 calibration_samples 读取样本 + 计算校准指标。

职责边界：
  - 仅负责样本读取与统计计算，不做采样/写入
  - 提供校准指标（假PASS率/假FAIL率/一致率）供 VLM oracle / 置信度校准使用
  - 冷启动检测（样本不足时返回 is_cold_start=True，调用方据此时降级）

通用性（§12.4）：
  1. 跨系统：统计指标适用于任何 PASS/FAIL 二分类系统
  2. 跨行业：不绑定业务字段，纯统计计算
  3. 跨页面：不依赖页面结构
  4. 跨 Tab：不涉及
  5. 数据完整性：读取失败返回空统计（不阻断）
  6. 根因溯源：解决"系统无法从人工修正中学习"导致重复犯错
  7. 覆盖性：所有校准查询统一走本模块
  8. 全局观：作为校准环出口，供 oracle/置信度模块消费
  9. 职责边界：仅读+算，不修改样本
"""
from __future__ import annotations
from src.core.guarded import guard

import json
import logging
from typing import Any, Optional

_log = logging.getLogger(__name__)


def load_samples(
    project: str,
    version_id: int,
    limit: int = 1000,
) -> list[dict[str, Any]]:
    """读取校准样本列表。

    参数:
        project:    项目名
        version_id: 版本ID
        limit:      最大返回条数（默认 1000）

    返回:
        [{id, case_id, predicted_verdict, human_verdict, features, created_at}, ...]
        读取失败返回空列表
    """
    # ★ P0-1 批C修复(2026-07-25): version_id=0 是合法 legacy 值，不能用 `not version_id`
    #   根因/通用性/覆盖性 同 review_lane_store.py / sampler.py
    if not project or version_id is None or version_id < 0:
        return []
    from src.core.db_manager import get_connection

    conn = None
    try:
        conn = get_connection()
        rows = (
            conn.execute(
                "SELECT id, case_id, predicted_verdict, human_verdict, features, created_at "
                "FROM calibration_samples "
                "WHERE project=%s AND version_id=%s "
                "ORDER BY created_at DESC LIMIT %s",
                (project, version_id, int(limit)),
            ).fetchall()
            or []
        )
        result = []
        for r in rows:
            d = dict(r)
            # 反序列化 features JSON
            if d.get("features"):
                try:
                    d["features"] = (
                        json.loads(d["features"])
                        if isinstance(d["features"], str)
                        else d["features"]
                    )
                except (TypeError, ValueError, json.JSONDecodeError) as _e:
                    _log.debug(
                        "[trainer] 反序列化 features 异常: %s", _e, exc_info=True
                    )
            result.append(d)
        return result
    except Exception as e:
        _log.warning("[calibration.trainer] 读取样本失败: %s", str(e)[:100])
        return []
    finally:
        if conn:
            with guard("load_samples: conn.close()", log=_log):
                conn.close()


def get_sample_count(project: str, version_id: int) -> int:
    """获取校准样本总数。"""
    if not project or version_id is None or version_id < 0:
        return 0
    from src.core.db_manager import get_connection

    conn = None
    try:
        conn = get_connection()
        row = conn.execute(
            "SELECT COUNT(*) AS cnt FROM calibration_samples "
            "WHERE project=%s AND version_id=%s",
            (project, version_id),
        ).fetchone()
        return int(row["cnt"]) if row else 0
    except Exception as e:
        _log.warning("[calibration.trainer] 计数失败: %s", str(e)[:80])
        return 0
    finally:
        if conn:
            with guard("get_sample_count: conn.close()", log=_log):
                conn.close()


def compute_calibration_stats(project: str, version_id: int) -> dict[str, Any]:
    """计算校准统计指标（假PASS率/假FAIL率/一致率/冷启动检测）。

    返回:
        {
            "total": int,               # 总样本数
            "agreement": int,            # 一致数（pred==human）
            "agreement_rate": float,     # 一致率 0~1
            "false_pass": int,           # 假PASS数（pred=PASS, human=FAIL）
            "false_pass_rate": float,    # 假PASS率 0~1
            "false_fail": int,           # 假FAIL数（pred=FAIL, human=PASS）
            "false_fail_rate": float,    # 假FAIL率 0~1
            "is_cold_start": bool,       # 是否冷启动（total < min_samples）
            "min_samples": int,          # 当前门槛
        }

    通用性：指标定义通用，适用于任何 PASS/FAIL 二分类系统。
    """
    from src.calibration.sampler import get_min_samples

    min_samples = get_min_samples()

    samples = load_samples(project, version_id)
    total = len(samples)
    if total == 0:
        return {
            "total": 0,
            "agreement": 0,
            "agreement_rate": 0.0,
            "false_pass": 0,
            "false_pass_rate": 0.0,
            "false_fail": 0,
            "false_fail_rate": 0.0,
            "is_cold_start": True,
            "min_samples": min_samples,
        }

    agreement = 0
    false_pass = 0  # pred=PASS but human=FAIL
    false_fail = 0  # pred=FAIL but human=PASS

    for s in samples:
        pred = str(s.get("predicted_verdict", "")).strip().upper()
        human = str(s.get("human_verdict", "")).strip().upper()
        if pred == human:
            agreement += 1
        elif pred == "PASS" and human == "FAIL":
            false_pass += 1
        elif pred == "FAIL" and human == "PASS":
            false_fail += 1

    return {
        "total": total,
        "agreement": agreement,
        "agreement_rate": round(agreement / total, 4),
        "false_pass": false_pass,
        "false_pass_rate": round(false_pass / total, 4),
        "false_fail": false_fail,
        "false_fail_rate": round(false_fail / total, 4),
        "is_cold_start": total < min_samples,
        "min_samples": min_samples,
    }


def is_calibrated(project: str, version_id: int) -> bool:
    """检查是否已有足够样本完成校准（非冷启动）。

    通用性：供 oracle/置信度模块判断"是否信任校准结果"。
    """
    from src.calibration.sampler import get_min_samples

    return get_sample_count(project, version_id) >= get_min_samples()


def get_calibration_summary(project: str, version_id: int) -> dict[str, Any]:
    """获取校准摘要（供 API/前端展示）。

    通用性：聚合 load_samples + compute_calibration_stats，一次调用获取全量信息。
    """
    stats = compute_calibration_stats(project, version_id)
    return {
        "stats": stats,
        "is_calibrated": not stats["is_cold_start"],
        "recent_samples": load_samples(project, version_id, limit=10),
    }
