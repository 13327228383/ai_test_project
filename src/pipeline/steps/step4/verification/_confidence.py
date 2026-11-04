# -*- coding: utf-8 -*-
"""三轨置信度评分（多因子加权） — 从 verification.py 拆出。

★ 2026-06-23 场景化升级：支持 required_tracks 子集。skipped 轨(passed=True 但 skipped=True)
  不计入置信度分母/分子，避免场景3(required=[ui,db] db fail) 因 api skipped 凑数致置信度虚高。
  不传 required_tracks 时保持原 3 轨行为（向后兼容）。
"""

import logging

_log = logging.getLogger(__name__)


def _compute_confidence(ui: dict, api: dict, db: dict, required_tracks=None) -> tuple:
    """多证据加权评分，返回数值而非简单标签。

    required_tracks: List[str] 子集（如 ["ui","db"]）。传入时只数声明轨，skipped 轨不计入；
                     缺失时按全 3 轨计算（向后兼容）。
    返回: (confidence: float, level: str, recommendation: str, factors: dict)
    """
    all_tracks = [("ui", ui), ("api", api), ("db", db)]
    if required_tracks:
        rt_set = {str(t).strip().lower() for t in required_tracks}
        tracks = [(k, v) for k, v in all_tracks if k in rt_set]
    else:
        tracks = all_tracks

    # skipped 轨(passed=True 但未真实校验)排除后再计 passed，避免凑数虚高
    active = [(k, v) for k, v in tracks if not v.get("skipped")]
    tracks_passed = sum(1 for _, v in active if v["passed"])
    active_n = len(active) or 1

    # 按通过比例映射 base_score（档位值走 pipeline_config，消除硬编码）
    # 用整数比较避免浮点误差（2/3=0.6667 与阈值 0.667 比较会误判落到 0.35）
    try:
        from src.core.pipeline_config import pipeline_config

        _score_all_pass = float(pipeline_config.verification_confidence_all_pass)
        _score_has_pass = float(pipeline_config.verification_confidence_has_pass)
        _score_has_fail = float(pipeline_config.verification_confidence_has_fail)
        _score_all_fail = float(pipeline_config.verification_confidence_all_fail)
    except Exception as _e:
        _log.debug(
            "[verification/confidence] 读取 pipeline_config 档位值失败，使用默认值: %s",
            _e,
        )
        _score_all_pass, _score_has_pass, _score_has_fail, _score_all_fail = (
            0.95,
            0.65,
            0.35,
            0.10,
        )
    if active and tracks_passed >= active_n:
        base_score = _score_all_pass
    elif active and tracks_passed * 3 >= active_n * 2:
        base_score = _score_has_pass
    elif tracks_passed > 0:
        base_score = _score_has_fail
    else:
        base_score = _score_all_fail

    active_keys = {k for k, _ in active}
    factors = {
        "ui_pass": ui["passed"],
        "api_pass": api["passed"],
        "db_pass": db["passed"],
        "tracks_passed": tracks_passed,
        "active_tracks": len(active),
        "required_tracks": (
            list(required_tracks) if required_tracks else ["ui", "api", "db"]
        ),
        "has_api_calls": len(api.get("calls", [])) > 0,
        "has_db_data": len(db.get("data", [])) > 0,
    }

    # 仅对声明且非 skipped 的轨给客观证据 bonus
    bonus = 0.0
    if "api" in active_keys and api.get("calls") and len(api["calls"]) > 0:
        bonus += 0.03
    if "db" in active_keys and db.get("data") and len(db["data"]) > 0:
        bonus += 0.02

    confidence = round(min(1.0, base_score + bonus), 3)

    # ★ P0-08: 阈值走 pipeline_config 用户级配置（_cfg 动态读取）
    try:
        from src.core.pipeline_config import _read_config_value as _cfg

        _high = float(_cfg("confidence_high_threshold", 0.90))
        _medium = float(_cfg("confidence_medium_threshold", 0.70))
    except Exception as _e:
        _log.debug("[verification/confidence] 读取置信度阈值失败，使用默认值: %s", _e)
        _high, _medium = 0.90, 0.70

    if confidence >= _high:
        level = "high"
        recommendation = "声明轨全部通过，结果可信"
    elif confidence >= _medium:
        level = "medium"
        recommendation = "建议复核"
    else:
        level = "low"
        recommendation = "需人工确认"

    # 失败轨诊断（只在声明且 active 的轨里找）
    failed_active = [k for k, v in active if not v["passed"]]
    failed_upper = [t.upper() for t in failed_active]
    if not failed_active:
        pass
    elif len(failed_active) == len(active):
        recommendation = "多轨失败，高置信度判定 FAIL"
    elif (
        "UI" in failed_upper
        and "api" in active_keys
        and api["passed"]
        and "db" in active_keys
        and db["passed"]
    ):
        recommendation = "⚠️ SUSPECT: UI轨AI判断失败但API/DB轨通过，可能AI误判"
    elif "API" in failed_upper:
        recommendation = "API轨失败（客观证据），高置信度判定 FAIL"
    elif "DB" in failed_upper:
        recommendation = "DB轨失败（客观证据），高置信度判定 FAIL"

    return (confidence, level, recommendation, factors)
