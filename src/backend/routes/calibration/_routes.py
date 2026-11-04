# -*- coding: utf-8 -*-
"""
校准环路由 — 暴露 calibration_samples 统计给前端人审界面。

职责边界：
  - 仅做 HTTP 薄包装，调用 src/calibration/trainer.py 的统计函数
  - 不做采样/写入（采样在人审动作触发时由 case_review 端点调用 sampler）
  - 只读端点，不修改校准数据

通用性（§12.4）：
  1. 跨系统：校准指标适用于任何 PASS/FAIL 二分类系统
  2. 跨行业：不绑定业务字段
  3. 跨页面：不依赖页面结构
  4. 数据完整性：读取失败返回空统计（is_cold_start=True），不阻断
  5. 职责边界：仅读+算，不修改样本
"""
from __future__ import annotations

import logging
from typing import Any

from flask import jsonify

_log = logging.getLogger(__name__)


def _register_routes():
    """注册校准环路由到 exec_bp（与 case_review 同 Blueprint）。"""
    from src.backend.routes.execution import exec_bp
    from src.backend.state import _current_project
    from src.models.permission import check_admin_or_permission

    def _get_active_version_id(project: str) -> int:
        """获取当前激活版本 ID（统一封装）。"""
        try:
            from src.core.versioning import current_version_id

            return int(current_version_id(project) or 0)
        except Exception as e:
            _log.warning("[calibration] 获取 version_id 失败: %s", str(e)[:80])
            return 0

    @exec_bp.route("/api/calibration/summary", methods=["GET"])
    def api_calibration_summary():
        """★ P1-7: 返回当前项目当前版本的校准统计摘要。

        响应:
            {
              "ok": true,
              "project": "...",
              "version_id": 123,
              "stats": {
                "total": 15, "agreement": 12, "agreement_rate": 0.8,
                "false_pass": 2, "false_pass_rate": 0.133,
                "false_fail": 1, "false_fail_rate": 0.067,
                "is_cold_start": false, "min_samples": 3
              },
              "is_calibrated": true,
              "recent_samples": [...]
            }

        通用性：冷启动（样本不足）时返回 is_cold_start=true，前端据此降级展示。
        """
        ok, err = check_admin_or_permission("execution.run", project=_current_project())
        if not ok:
            return jsonify({"ok": False, "error": err}), (
                401 if err.startswith("请先登录") else 403
            )
        project = _current_project() or ""
        if not project:
            return jsonify({"ok": False, "error": "未选择项目"}), 400
        version_id = _get_active_version_id(project)
        if version_id == 0:
            # 无激活版本时返回冷启动空统计（不报错，前端降级展示）
            return jsonify(
                {
                    "ok": True,
                    "project": project,
                    "version_id": 0,
                    "stats": {
                        "total": 0,
                        "agreement": 0,
                        "agreement_rate": 0.0,
                        "false_pass": 0,
                        "false_pass_rate": 0.0,
                        "false_fail": 0,
                        "false_fail_rate": 0.0,
                        "is_cold_start": True,
                        "min_samples": 3,
                    },
                    "is_calibrated": False,
                    "recent_samples": [],
                    "hint": "当前无激活版本，校准数据为空",
                }
            )
        try:
            from src.calibration.trainer import get_calibration_summary

            summary = get_calibration_summary(project, version_id)
            return jsonify(
                {
                    "ok": True,
                    "project": project,
                    "version_id": version_id,
                    "stats": summary.get("stats", {}),
                    "is_calibrated": summary.get("is_calibrated", False),
                    "recent_samples": summary.get("recent_samples", []),
                }
            )
        except Exception as e:
            _log.warning(
                "[calibration] 读取校准摘要失败: %s", str(e)[:120], exc_info=True
            )
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": "读取校准摘要失败",
                        "detail": str(e)[:200],
                    }
                ),
                500,
            )
