# -*- coding: utf-8 -*-
"""
POST /api/step3/case/upload — 上传用例文件并执行（权限 case.upload）。
校验通过 → run_uploaded_cases → 写上传历史。
"""
import os
from src.core.guarded import guard
import logging
import tempfile
from pathlib import Path

from flask import jsonify, request

from . import case_upload_bp
from ._decorators import _require_perm, _current_target_key

_log = logging.getLogger(__name__)

_ALLOWED_EXT = (".csv", ".json")
_MAX_BYTES = 50 * 1024 * 1024


@case_upload_bp.route("/api/step3/case/upload", methods=["POST"])
@_require_perm("case.upload")
def upload_and_run_cases():
    """上传用例文件，经 target+version 匹配校验后执行。

    Form 参数:
        - file: 用例文件（.csv / .json）
        - policy: reject | warn | force（默认 reject）
        - target: 覆盖当前被测系统（可选）

    Returns:
        {"ok": bool, "match": VersionMatchResult, "report": {...}, "history_id": int}
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
    uploader = request.values.get("uploader") or _current_user_id()

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
            run_uploaded_cases,
            set_current_target,
        )

        set_current_target(target)
        # ① 解析 + 校验
        parsed = parse_cases_file(tmp_path)
        result = validate_target_version(parsed, policy=policy, current_target=target)

        # ② reject 策略下不匹配 → 拒绝执行，写"拒绝"历史
        if not result.matched and result.policy == "reject":
            with guard("upload_and_run_cases: os.unlink(tmp_path)", log=_log):
                os.unlink(tmp_path)
            _write_history(
                filename=file.filename,
                target_key=parsed.get("target_key", ""),
                version=parsed.get("version", ""),
                uploader=uploader,
                matched=False,
                policy=policy,
                message=result.message,
                exec_summary="",
            )
            return (
                jsonify(
                    {"ok": False, "error": result.message, "match": result.to_dict()}
                ),
                422,
            )

        # ③ 匹配（或 warn 策略）→ 执行
        report = run_uploaded_cases(
            tmp_path, target_key=target, report_path=None
        )
        with guard("upload_and_run_cases: os.unlink(tmp_path)", log=_log):
            os.unlink(tmp_path)

        summary = report.get("summary", {}) if isinstance(report, dict) else {}
        all_pass = bool(summary.get("all_pass", False)) if summary else False
        exec_summary = (
            f"all_pass={all_pass}, "
            f"cases={len(parsed.get('cases', []))}, "
            f"probes={len(parsed.get('authz_probes', []))}"
        )
        if result.policy == "warn" and not result.matched:
            exec_summary += " [版本不符-告警模式放行]"

        history_id = _write_history(
            filename=file.filename,
            target_key=parsed.get("target_key", ""),
            version=parsed.get("version", ""),
            uploader=uploader,
            matched=True,
            policy=policy,
            message=result.message,
            exec_summary=exec_summary,
        )

        return jsonify(
            {
                "ok": True,
                "match": result.to_dict(),
                "report": summary,
                # ★ T1: 短路链 ledger 核对摘要（一致/不一致/真值缺失 + 不符列表）
                "ledger_check": report.get("ledger_check") or {
                    "checked": 0,
                    "consistent_true": 0,
                    "consistent_false": 0,
                    "truth_unavailable": 0,
                    "mismatches": [],
                },
                "history_id": history_id,
            }
        )

    except ValueError as e:
        # 解析阶段 target/version 缺失 → 明确拒绝
        _log.info("[case_upload] 上传拒绝: %s", e)
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
        _log.error("[case_upload] 上传执行异常: %s", e, exc_info=True)
        return jsonify({"ok": False, "error": f"执行失败: {e}"}), 500


def _current_user_id() -> str:
    """读取当前登录用户 ID（写入历史用）。"""
    try:
        from src.core.config_loader import get_current_user

        return get_current_user() or "unknown"
    except Exception:
        return "unknown"


def _write_history(
    filename: str,
    target_key: str,
    version: str,
    uploader: str,
    matched: bool,
    policy: str,
    message: str,
    exec_summary: str,
) -> int:
    """写入上传历史（T08）。失败不影响主流程，返回 0 表示未写入。"""
    try:
        from src.backend.case_upload_history import CaseUploadHistory

        rec = CaseUploadHistory.record(
            filename=filename,
            target_key=target_key,
            version=version,
            uploader=uploader,
            matched=matched,
            policy=policy,
            message=message,
            exec_summary=exec_summary,
        )
        return rec.get("id", 0) if rec else 0
    except Exception as e:
        _log.warning("[case_upload] 历史写入失败(不阻断): %s", e)
        return 0
