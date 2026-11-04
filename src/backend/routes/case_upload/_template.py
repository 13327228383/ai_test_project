# -*- coding: utf-8 -*-
"""
GET /api/step3/case/template — 下载预填版本用例模板（权限 case.template）。
"""
import tempfile
from src.core.guarded import guard
import logging
from pathlib import Path

from flask import Response, jsonify, request

from . import case_upload_bp
from ._decorators import _require_perm, _current_target_key, _login_required

_log = logging.getLogger(__name__)

_ALLOWED_FMT = ("csv", "json")


@case_upload_bp.route("/api/step3/case/template", methods=["GET"])
@_require_perm("case.template")
def download_case_template():
    """下载当前被测系统的用例模板（预填 target_key + version）。

    Query params:
        - fmt: csv | json（默认 csv）
        - target: 指定被测系统 key（可选，默认当前被测系统）

    Returns:
        文件下载响应（Content-Disposition: attachment）。
    """
    fmt = (request.args.get("fmt") or "csv").strip().lower()
    if fmt not in _ALLOWED_FMT:
        return jsonify({"ok": False, "error": f"不支持的格式: {fmt}，仅支持 csv/json"}), 400

    target = _current_target_key()
    try:
        from src.pipeline.steps.step3.case_upload import (
            set_current_target,
            write_template,
        )

        set_current_target(target)
        suffix = ".csv" if fmt == "csv" else ".json"
        with tempfile.NamedTemporaryFile(
            suffix=suffix, delete=False, prefix="case_template_"
        ) as tmp:
            tmp_path = tmp.name
        write_template(tmp_path, fmt=fmt, current_target=target)
        data = Path(tmp_path).read_bytes()
        with guard("download_case_template: Path(tmp_path).unlink()", log=_log):
            Path(tmp_path).unlink()
    except Exception as e:
        _log.error("[case_upload] 模板生成失败: %s", e, exc_info=True)
        return jsonify({"ok": False, "error": f"模板生成失败: {e}"}), 500

    filename = f"case_template_{target}.{fmt}"
    return Response(
        data,
        mimetype="text/csv" if fmt == "csv" else "application/json",
        headers={
            "Content-Disposition": f"attachment; filename={filename}",
            "Cache-Control": "no-store",
        },
    )


@case_upload_bp.route("/api/step3/case/current-target", methods=["GET"])
@_login_required
def current_target_info():
    """返回当前被测系统的 target_key + version（前端信息栏展示用）。"""
    target = _current_target_key()
    try:
        from src.pipeline.steps.step4.empirical.target_cfgs import get_target_cfg

        cfg = get_target_cfg(target)
        version = cfg.get("version", "1.0.0")
    except Exception:
        version = "1.0.0"
    return jsonify({"ok": True, "target_key": target, "version": version})
