# -*- coding: utf-8 -*-
"""
POST /api/step3/case/validate — 仅校验用例文件（不上传、不执行）。
返回 VersionMatchResult（matched/expected_version/message/policy）。
"""
import os
from src.core.guarded import guard
import logging
import tempfile
from pathlib import Path

from flask import jsonify, request

from . import case_upload_bp
from ._decorators import _login_required, _current_target_key

_log = logging.getLogger(__name__)

_ALLOWED_EXT = (".csv", ".json")
_MAX_BYTES = 50 * 1024 * 1024


@case_upload_bp.route("/api/step3/case/validate", methods=["POST"])
@_login_required
def validate_case_file():
    """解析并校验上传的用例文件（仅校验，不执行）。

    Form 参数:
        - file: 用例文件（.csv / .json）
        - policy: reject | warn | force（默认 reject）

    Returns:
        {"ok": bool, "match": VersionMatchResult(dict), "parsed": {...}}
    """
    if "file" not in request.files:
        return jsonify({"ok": False, "error": "缺少文件字段"}), 400
    file = request.files["file"]
    if not file or not file.filename:
        return jsonify({"ok": False, "error": "未选择文件"}), 400

    ext = Path(file.filename).suffix.lower()
    if ext not in _ALLOWED_EXT:
        return (
            jsonify({"ok": False, "error": f"不支持的格式: {ext}，仅支持 csv/json"}),
            400,
        )

    policy = (request.form.get("policy") or "reject").strip().lower()
    target = _current_target_key()

    try:
        content = file.read()
        if len(content) > _MAX_BYTES:
            return jsonify({"ok": False, "error": "文件超过 50MB 限制"}), 400
        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
            tmp.write(content)
            tmp_path = tmp.name

        from src.pipeline.steps.step3.case_upload import (
            parse_cases_file,
            validate_target_version,
        )
        from src.pipeline.steps.step3.case_upload import set_current_target

        set_current_target(target)
        parsed = parse_cases_file(tmp_path)
        result = validate_target_version(parsed, policy=policy, current_target=target)
        with guard("validate_case_file: os.unlink(tmp_path)", log=_log):
            os.unlink(tmp_path)
    except ValueError as e:
        # 解析阶段 target/version 缺失 → 明确拒绝（422 语义）
        _log.info("[case_upload] 校验拒绝: %s", e)
        return (
            jsonify(
                {
                    "ok": False,
                    "error": str(e),
                    "match": {
                        "file_target": "",
                        "file_version": "",
                        "current_target": target,
                        "current_version": "",
                        "matched": False,
                        "policy": policy,
                        "message": str(e),
                    },
                }
            ),
            422,
        )
    except Exception as e:
        _log.error("[case_upload] 校验异常: %s", e, exc_info=True)
        return jsonify({"ok": False, "error": f"服务器内部错误: {e}"}), 500

    return jsonify(
        {
            "ok": True,
            "match": result.to_dict(),
            "parsed": {
                "target_key": parsed.get("target_key", ""),
                "version": parsed.get("version", ""),
                "cases": len(parsed.get("cases", [])),
                "authz_probes": len(parsed.get("authz_probes", [])),
            },
        }
    )
