# -*- coding: utf-8 -*-
"""
API JSON 错误处理器（从 app.py 拆分）。

确保所有 /api/ 错误返回 JSON 而非 HTML。
原 app.py 中 _api_error_response 引用了模块级 app.debug，
拆分后改为从 flask.current_app 获取，避免循环依赖。
"""

import traceback
from flask import jsonify, request, current_app


def _api_error_response(status_code, message, exc=None):
    """构建 API JSON 错误响应，开发模式下附带 traceback。"""
    body = {"ok": False, "error": message, "status": status_code}
    if current_app.debug and exc:
        body["traceback"] = traceback.format_exception_only(type(exc), exc)[-1].strip()
    return jsonify(body), status_code


def _handle_400(exc):
    if request.path.startswith("/api/"):
        return _api_error_response(400, "请求参数错误", exc)
    return exc


def _handle_401(exc):
    if request.path.startswith("/api/"):
        return _api_error_response(401, "请先登录")
    return exc


def _handle_403(exc):
    if request.path.startswith("/api/"):
        return _api_error_response(403, "无权访问")
    return exc


def _handle_404(exc):
    if request.path.startswith("/api/"):
        return _api_error_response(404, "接口不存在")
    return exc


def _handle_405(exc):
    if request.path.startswith("/api/"):
        return _api_error_response(405, "请求方法不允许")
    return exc


def _handle_500(exc):
    if request.path.startswith("/api/"):
        original = getattr(exc, "original_exception", exc)
        import traceback as _tb500

        tb_str = _tb500.format_exc()
        # ★ 生产环境不应暴露 traceback 到前端
        import sys as _sys500

        _sys500.stderr.write(f"\n=== 500 ERROR on {request.path} ===\n{tb_str}\n")
        from flask import jsonify, current_app

        current_app.logger.error("500 ERROR on %s: %s", request.path, tb_str)
        body = {"error": "服务器内部错误", "status": 500}
        # traceback 仅在 DEBUG 模式输出
        if current_app.debug:
            body["traceback"] = tb_str
        body["exc_type"] = type(original).__name__
        body["exc_msg"] = str(original)
        return jsonify(body), 500
    return exc
