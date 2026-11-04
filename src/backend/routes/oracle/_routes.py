# -*- coding: utf-8 -*-
"""
视觉 Oracle 路由 — 暴露 VLMOracle.check() 给前端人审界面。

职责边界：
  - 仅做 HTTP 薄包装，调用 src/oracle/correctness_vlm.py 的 VLMOracle.check()
  - 不修改 VLM 校验逻辑
  - Ollama 不可用时降级返回（VLMOracle 内部已处理）

通用性（§12.4）：
  1. 跨系统：layout_spec 是通用 JSON 描述
  2. 跨行业：prompt 不含行业术语
  3. 数据完整性：provider 不可用时返回 confidence=0 降级响应
  4. 职责边界：仅 HTTP 包装，不做校验决策
"""
from __future__ import annotations
from src.core.guarded import guard

import logging
from typing import Any

from flask import jsonify, request

_log = logging.getLogger(__name__)


def _register_routes():
    """注册视觉 Oracle 路由到 exec_bp。"""
    from src.backend.routes.execution import exec_bp
    from src.backend.state import _current_project
    from src.models.permission import check_admin_or_permission

    @exec_bp.route("/api/visual-review", methods=["POST"])
    def api_visual_review():
        """★ P1-7: 触发 VLM 视觉复核。

        请求体:
            {
              "layout_spec": {...},       // 期望布局描述（JSON）
              "screenshot": "base64...",   // 截图 base64（可选，缺省=纯文本对比）
              "dom_summary": "...",        // DOM 文本摘要（可选）
              "case_id": "..."             // 关联用例ID（可选，用于日志）
            }

        响应:
            {
              "ok": true,
              "result": {
                "match": true|false|null,
                "diff_regions": [...],
                "grounding": [...],
                "confidence": 0.0~1.0,
                "reason": "...",
                "raw_response": "..."
              },
              "available": true|false  // VLM provider 是否可用
            }

        通用性：Ollama 不可用时返回降级响应（confidence=0），不报错。
        """
        ok, err = check_admin_or_permission("execution.run", project=_current_project())
        if not ok:
            return jsonify({"ok": False, "error": err}), (
                401 if err.startswith("请先登录") else 403
            )

        data = request.get_json(silent=True) or {}
        layout_spec = data.get("layout_spec")
        if not layout_spec:
            return jsonify({"ok": False, "error": "缺少 layout_spec"}), 400

        screenshot = data.get("screenshot")
        dom_summary = str(data.get("dom_summary", "") or "")
        case_id = str(data.get("case_id", "") or "")[:128]

        try:
            from src.oracle.correctness_vlm import get_vlm_oracle

            oracle = get_vlm_oracle()
            available = oracle.is_available()

            # 处理 screenshot：base64 → bytes
            screenshot_bytes = None
            if screenshot and isinstance(screenshot, str):
                import base64

                try:
                    # 支持 data:image/...;base64, 前缀
                    if ";base64," in screenshot:
                        screenshot = screenshot.split(";base64,", 1)[1]
                    screenshot_bytes = base64.b64decode(screenshot)
                except Exception as e:
                    _log.warning("[visual-review] base64 解码失败: %s", str(e)[:80])
                    screenshot_bytes = None

            result = oracle.check(
                layout_spec=layout_spec,
                screenshot=screenshot_bytes,
                dom_summary=dom_summary,
            )

            _log.info(
                "[visual-review] case_id=%s match=%s confidence=%.2f available=%s",
                case_id,
                result.get("match"),
                result.get("confidence", 0),
                available,
            )

            # ★ P1-5 接入(2026-07-25): VLM 复核结果持久化到 visual_review 表
            #   根因: visual_review 表零 INSERT → VLM 复核结果丢失 → Step5 无视觉维度数据
            #   通用性: vlm_diff 通用 JSON，execution_id=0 表示独立复核（无关联执行）
            #   降级: 持久化失败不阻断 API 响应（warning 记录）
            #   职责边界: 路由层只触发持久化，store 负责写入逻辑
            with guard("api_visual_review: from src.oracle.store import write_v", log=_log, level=logging.WARNING):
                from src.oracle.store import write_visual_review
                from src.core.versioning import current_version_id

                _vr_project = _current_project() or ""
                _vr_vid = int(current_version_id(_vr_project) or 0)
                write_visual_review(
                    project=_vr_project,
                    version_id=_vr_vid,
                    execution_id=0,  # 独立复核无关联执行
                    screenshot_path="",  # base64 截图不存路径
                    vlm_diff=result,  # 含 match/diff_regions/grounding/confidence
                    baseline_id=None,
                )

            return jsonify(
                {
                    "ok": True,
                    "result": result,
                    "available": available,
                }
            )
        except Exception as e:
            _log.warning("[visual-review] 调用失败: %s", str(e)[:120], exc_info=True)
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": "视觉复核调用失败",
                        "detail": str(e)[:200],
                    }
                ),
                500,
            )

    @exec_bp.route("/api/visual-review/status", methods=["GET"])
    def api_visual_review_status():
        """★ P1-7: 检查 VLM provider 是否可用（前端据此决定是否展示视觉复核入口）。

        响应:
            {
              "ok": true,
              "available": true|false,
              "provider": "ollama"
            }
        """
        ok, err = check_admin_or_permission("execution.run", project=_current_project())
        if not ok:
            return jsonify({"ok": False, "error": err}), (
                401 if err.startswith("请先登录") else 403
            )
        try:
            from src.oracle.correctness_vlm import get_vlm_oracle

            oracle = get_vlm_oracle()
            return jsonify(
                {
                    "ok": True,
                    "available": oracle.is_available(),
                    "provider": oracle.provider_name,
                }
            )
        except Exception as e:
            _log.warning("[visual-review/status] 检查失败: %s", str(e)[:80])
            return jsonify(
                {
                    "ok": True,
                    "available": False,
                    "error": str(e)[:120],
                }
            )
