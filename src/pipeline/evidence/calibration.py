# -*- coding: utf-8 -*-
from __future__ import annotations

"""
证据引擎 — 校准数据层 (Calibration Data Layer)
=================================================
核心职责:
  1. record_calibration(): Step4 执行后批量写入校准数据
  2. get_calibration():   读取指定维度历史准确率
  3. _bayesian_posterior(): 贝叶斯后验公式（Beta-Binomial conjugate）

设计原则:
  - 零硬编码: 所有阈值从 pipeline_config 读取
  - 通用系统: 校准维度可扩展，不绑定特定 Step
  - MySQL 唯一事实源: 不落磁盘文件
  - 异常安全: 校准回写失败不影响主流程
"""

import logging
from typing import Any

_log = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════
# 校准维度常量（可扩展，不硬编码具体值）
# ═══════════════════════════════════════════════════════════════

# 所有支持的校准维度 key
CALIBRATION_DIMENSIONS = frozenset(
    {
        "consensus_level",  # HIGH / MEDIUM / LOW
        "perspective_count",  # 1 / 2 / 3+
        "source_quote",  # yes / no
        "requirement_type",  # functional / security / integration
        "risk_level",  # P0 / P1 / P2 / P3
        "case_type",  # 功能 / 安全 / 性能 / 兼容性 / ...
    }
)

# 有效的 calibration_key 集合（白名单校验）
_VALID_KEYS = CALIBRATION_DIMENSIONS


# ═══════════════════════════════════════════════════════════════
# 贝叶斯后验公式
# ═══════════════════════════════════════════════════════════════


def _bayesian_posterior(
    prior: float,
    evidence_hits: float,
    evidence_total: float,
    prior_weight: float = 10.0,
) -> float:
    """
    Beta-Binomial conjugate 贝叶斯后验。

    将 AI 声称的置信度（prior）与历史实证数据（evidence）融合，
    输出校准后的后验置信度。

    参数:
        prior:          AI 声称的置信度 (0.0~1.0)
        evidence_hits:  历史实际正确的次数
        evidence_total: 历史总次数
        prior_weight:   先验伪计数（越大 → 越依赖 AI 先验，越小 → 越快收敛到实证）

    返回:
        校准后置信度 (0.0~1.0)

    示例:
        >>> _bayesian_posterior(0.90, 205, 247, 10)
        0.85  # AI 说 0.90，但历史只有 83% 准确率 → 拉低到 0.85

    数学:
        alpha = prior * prior_weight + evidence_hits
        beta  = (1 - prior) * prior_weight + (evidence_total - evidence_hits)
        posterior = alpha / (alpha + beta)
    """
    if prior <= 0.0 or prior >= 1.0:
        _clamped = max(0.01, min(0.99, prior))
        _log.debug("[Calibration] prior=%.4f 截断为 %.4f（边界保护）", prior, _clamped)
        prior = _clamped

    if evidence_total <= 0:
        return prior

    alpha = prior * prior_weight + evidence_hits
    beta = (1.0 - prior) * prior_weight + (evidence_total - evidence_hits)

    if alpha + beta <= 0:
        return prior

    return alpha / (alpha + beta)


# ═══════════════════════════════════════════════════════════════
# 校准数据写入
# ═══════════════════════════════════════════════════════════════


def record_calibration(
    project: str,
    entries: list[dict[str, Any]],
) -> bool:
    """
    批量写入校准数据（Step4 执行后调用）。

    参数:
        project: 项目名
        entries: [{"key": "consensus_level", "value": "HIGH", "correct": True}, ...]
                 key 必须是 CALIBRATION_DIMENSIONS 之一

    返回:
        True 成功 / False 失败（失败不阻断主流程）

    实现:
        - 对每个 entry，UPDATE total_predictions + 1, total_correct + correct
        - 若该行不存在，INSERT 新行
        - 使用 ON DUPLICATE KEY UPDATE 原子递增
    """
    if not entries:
        return True

    # 入口校验
    for entry in entries:
        key = str(entry.get("key", "") or "").strip()
        if key not in _VALID_KEYS:
            _log.warning("[Calibration] 无效校准键: %s (忽略)", key[:50])
            return False
        value = str(entry.get("value", "") or "").strip()
        if not value:
            _log.warning("[Calibration] 校准值不能为空 (key=%s)", key)
            return False

    conn = None
    try:
        from src.core.db_manager import get_connection

        conn = get_connection()

        # 确保表存在
        _ensure_calibration_table(conn)

        for entry in entries:
            key = str(entry["key"]).strip()
            value = str(entry["value"]).strip()
            # ★ M1: 显式判断 True/1，避免 Python truthiness 把 0/None/"" 都当 False
            correct = 1 if entry.get("correct") in (True, 1) else 0

            conn.execute(
                """
                INSERT INTO confidence_calibration
                    (project, calibration_key, calibration_value, total_predictions, total_correct, accuracy)
                VALUES (%s, %s, %s, 1, %s, %s)
                ON DUPLICATE KEY UPDATE
                    total_predictions = total_predictions + 1,
                    total_correct = total_correct + VALUES(total_correct),
                    accuracy = total_correct / total_predictions
                """,
                (project, key, value, correct, float(correct)),
            )

        conn.commit()
        _log.debug(
            "[Calibration] 写入 %d 条校准数据 (project=%s)", len(entries), project
        )
        return True

    except Exception as e:
        _log.debug("[Calibration] 写入失败: %s", str(e)[:120])
        if conn:
            try:
                conn.rollback()
            except Exception as _pass_e:
                _log.warning("calibration.py:164 异常被吞: %s", _pass_e, exc_info=True)
        return False
    finally:
        if conn:
            try:
                conn.close()
            except Exception as _pass_e:
                _log.warning("calibration.py:169 异常被吞: %s", _pass_e, exc_info=True)


# ═══════════════════════════════════════════════════════════════
# 校准数据读取
# ═══════════════════════════════════════════════════════════════


def get_calibration(
    project: str,
    key: str | None = None,
    value: str | None = None,
) -> dict[str, Any]:
    """
    读取校准数据。

    参数:
        project: 项目名
        key:     可选，筛选校准维度（如 "consensus_level"）。None=返回所有维度
        value:   可选，筛选校准值（如 "HIGH"）。仅 key 非 None 时有效

    返回:
        {
          # ★ 某SPA系统业务名已统一到 config.demo.sample_project
          "project": DEMO_PROJECT,
          "dimensions": {
            "consensus_level": {
              "HIGH": {"predictions": 247, "correct": 205, "accuracy": 0.83},
              "MEDIUM": {"predictions": 120, "correct": 78, "accuracy": 0.65},
              ...
            },
            "perspective_count": {...},
            ...
          },
          "total_predictions": 500,   # 所有维度总预测次数
          "total_accuracy": 0.76,      # 所有维度加权平均准确率
        }

    当 key+value 都指定时返回单条记录:
        {"predictions": 247, "correct": 205, "accuracy": 0.83}
    """
    conn = None
    try:
        from src.core.db_manager import get_connection

        conn = get_connection()

        _ensure_calibration_table(conn)

        if key and value:
            # 单条查询
            row = conn.execute(
                "SELECT total_predictions, total_correct, accuracy "
                "FROM confidence_calibration "
                "WHERE project=%s AND calibration_key=%s AND calibration_value=%s",
                (project, key, value),
            ).fetchone()
            if row:
                return {
                    "predictions": int(row.get("total_predictions", 0) or 0),
                    "correct": int(row.get("total_correct", 0) or 0),
                    "accuracy": float(row.get("accuracy", 0.0) or 0.0),
                }
            return {"predictions": 0, "correct": 0, "accuracy": 0.0}

        # 全量查询
        rows = conn.execute(
            "SELECT calibration_key, calibration_value, total_predictions, total_correct, accuracy "
            "FROM confidence_calibration "
            "WHERE project=%s "
            "ORDER BY calibration_key, calibration_value",
            (project,),
        ).fetchall()

        dimensions: dict[str, dict[str, dict]] = {}
        total_pred = 0
        total_corr = 0

        for row in rows:
            rk = str(row.get("calibration_key", "") or "")
            rv = str(row.get("calibration_value", "") or "")
            pred = int(row.get("total_predictions", 0) or 0)
            corr = int(row.get("total_correct", 0) or 0)
            acc = float(row.get("accuracy", 0.0) or 0.0)

            if rk not in dimensions:
                dimensions[rk] = {}
            dimensions[rk][rv] = {
                "predictions": pred,
                "correct": corr,
                "accuracy": acc,
            }
            total_pred += pred
            total_corr += corr

        total_acc = total_corr / max(total_pred, 1)

        return {
            "project": project,
            "dimensions": dimensions,
            "total_predictions": total_pred,
            "total_accuracy": round(total_acc, 4),
        }

    except Exception as e:
        _log.debug("[Calibration] 读取失败: %s", str(e)[:120])
        return {
            "project": project,
            "dimensions": {},
            "total_predictions": 0,
            "total_accuracy": 0.0,
            "_error": str(e)[:200],
        }
    finally:
        if conn:
            try:
                conn.close()
            except Exception as _pass_e:
                _log.warning("calibration.py:283 异常被吞: %s", _pass_e, exc_info=True)


# ═══════════════════════════════════════════════════════════════
# 内部辅助
# ═══════════════════════════════════════════════════════════════


def _ensure_calibration_table(conn) -> None:
    """确保 confidence_calibration 表存在（由 schema.sql 负责，此处仅兜底）。"""
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS `confidence_calibration` (
                `id`                BIGINT       NOT NULL AUTO_INCREMENT,
                `project`           VARCHAR(128) NOT NULL COMMENT '项目名',
                `calibration_key`   VARCHAR(128) NOT NULL COMMENT '校准维度',
                `calibration_value` VARCHAR(128) NOT NULL COMMENT '校准值',
                `total_predictions` INT          NOT NULL DEFAULT 0,
                `total_correct`     INT          NOT NULL DEFAULT 0,
                `accuracy`          DOUBLE       NOT NULL DEFAULT 0.0,
                `last_updated`      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                PRIMARY KEY (`id`),
                UNIQUE KEY `uk_calibration` (`project`, `calibration_key`, `calibration_value`),
                INDEX `idx_cal_project` (`project`),
                CONSTRAINT `fk_cal_project` FOREIGN KEY (`project`) REFERENCES `projects` (`name`) ON DELETE CASCADE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """
        )
    except Exception as _e:
        # 表可能已由 schema.sql 创建；但若建表失败（如 projects 表不存在导致 FK 失败），
        # 必须用 error 级别日志告警，因为后续 INSERT 会因表不存在而全部丢失校准数据。
        _err_msg = str(_e)[:200]
        _log.error(
            "[Calibration] _ensure_calibration_table 兜底建表失败! 校准数据将丢失: %s",
            _err_msg,
        )
        if (
            "references" in _err_msg.lower()
            or "foreign key" in _err_msg.lower()
            or "fk" in _err_msg.lower()
        ):
            _log.error(
                "[Calibration] ↑ FK 约束失败——请确认 projects 表已由 schema.sql 先行创建"
            )
