# -*- coding: utf-8 -*-
"""api_demo — API 型靶子系统（Flask REST 服务，端口 8010）
============================================================
[未复核-需 GLM/DeepSeek 把关] 新靶子系统（2026-08-10 新增，E2E 通用性验证用）。

体现 API 型系统的典型特征：
  1. RESTful CRUD 资源（/api/v1/books）
  2. Token 鉴权（登录换 token，请求头 Authorization: Bearer <token>）
  3. 参数校验（非法入参 → 400 业务错误码）
  4. 业务边界（越权 → 403；不存在资源 → 404）
  5. 明确错误码（code 字段，成功 code=0）
  6. 启动自动种子数据（seed.py 提供 admin/user 账号 + 初始书籍）

启动：  python api_demo/app.py          （默认 127.0.0.1:8010）
健康检查：GET /health
"""

import secrets
import sys
from pathlib import Path

from flask import Flask, jsonify, request

# 兼容两种启动方式：
#   1) python api_demo/app.py        （直接运行，脚本目录被加入 sys.path）
#   2) python -m api_demo.app / 子进程（cwd=项目根，需显式把项目根加入 sys.path）
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

try:
    from api_demo.seed import SEED_BOOKS, SEED_USERS
except ImportError:  # pragma: no cover - 直接以脚本方式运行时的兜底
    from seed import SEED_BOOKS, SEED_USERS

app = Flask(__name__)

# ── 内存数据存储（原型靶子系统：无外部 DB 依赖，重启即重置） ──
USERS: dict = {}          # username -> {"password": str, "role": str}
BOOKS: dict = {}          # book_id -> {"id": int, "title": str, "author": str, "price": float, "stock": int}
TOKENS: dict = {}         # token -> username
_BOOK_SEQ: list = [0]

# ── 统一错误码（明确错误码，方便测试断言与业务侧排查） ──
CODE_OK = 0
CODE_PARAM_ERROR = 40001      # 参数校验失败
CODE_UNAUTHORIZED = 40101     # 未认证 / token 无效
CODE_FORBIDDEN = 40301        # 权限不足（越权）
CODE_NOT_FOUND = 40401        # 资源不存在


def _next_book_id() -> int:
    """返回下一个自增书籍 ID。"""
    _BOOK_SEQ[0] += 1
    return _BOOK_SEQ[0]


def seed_if_empty() -> None:
    """启动时自动种子：账号 + 初始书籍（仅当存储为空）。"""
    if not USERS:
        for username, info in SEED_USERS.items():
            USERS[username] = {"password": info["password"], "role": info["role"]}
    if not BOOKS:
        for item in SEED_BOOKS:
            book_id = _next_book_id()
            BOOKS[book_id] = {
                "id": book_id,
                "title": item["title"],
                "author": item["author"],
                "price": item["price"],
                "stock": item["stock"],
            }


def _unauthorized(message: str = "未认证或 token 无效"):
    """401 统一响应。"""
    return jsonify({"code": CODE_UNAUTHORIZED, "message": message}), 401


def _forbidden(message: str = "权限不足"):
    """403 统一响应。"""
    return jsonify({"code": CODE_FORBIDDEN, "message": message}), 403


def _not_found(message: str = "资源不存在"):
    """404 统一响应。"""
    return jsonify({"code": CODE_NOT_FOUND, "message": message}), 404


def _param_error(message: str):
    """400 统一响应。"""
    return jsonify({"code": CODE_PARAM_ERROR, "message": message}), 400


def _current_user() -> str:
    """从 Authorization 头解析当前登录用户名；未认证返回空串。"""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return ""
    token = auth[len("Bearer "):].strip()
    return TOKENS.get(token, "")


def _require_user():
    """鉴权装饰器：无有效 token → 401。"""
    username = _current_user()
    if not username or username not in USERS:
        return _unauthorized(), None
    return None, username


def _validate_book_payload(payload: dict) -> str:
    """书籍参数校验；返回错误消息，空串表示通过。"""
    if not isinstance(payload, dict):
        return "请求体必须为 JSON 对象"
    title = payload.get("title")
    if not title or not str(title).strip():
        return "title 不能为空"
    if len(str(title)) > 120:
        return "title 长度不能超过 120"
    author = payload.get("author", "")
    if len(str(author)) > 80:
        return "author 长度不能超过 80"
    try:
        price = float(payload.get("price", 0))
    except (TypeError, ValueError):
        return "price 必须为数字"
    if price <= 0:
        return "price 必须大于 0"
    try:
        stock = int(payload.get("stock", 0))
    except (TypeError, ValueError):
        return "stock 必须为整数"
    if stock < 0:
        return "stock 不能为负数"
    return ""


def _book_to_dict(book: dict) -> dict:
    """书籍对象序列化为响应字典（不含内部字段）。"""
    return {
        "id": book["id"],
        "title": book["title"],
        "author": book["author"],
        "price": book["price"],
        "stock": book["stock"],
    }


@app.route("/health", methods=["GET"])
def health():
    """健康检查端点。"""
    return jsonify({"status": "ok", "service": "api_demo", "books": len(BOOKS)}), 200


@app.route("/api/v1/auth/login", methods=["POST"])
def login():
    """登录：校验账号密码，成功返回 token。"""
    payload = request.get_json(silent=True) or {}
    username = str(payload.get("username", ""))
    password = str(payload.get("password", ""))
    user = USERS.get(username)
    if not user or user["password"] != password:
        return _unauthorized("用户名或密码错误")
    token = secrets.token_hex(16)
    TOKENS[token] = username
    return jsonify({
        "code": CODE_OK,
        "message": "ok",
        "data": {"token": token, "username": username, "role": user["role"]},
    }), 200


@app.route("/api/v1/books", methods=["GET"])
def list_books():
    """书籍列表（需登录）。"""
    err, _username = _require_user()
    if err is not None:
        return err
    books = [_book_to_dict(b) for b in sorted(BOOKS.values(), key=lambda x: x["id"])]
    return jsonify({"code": CODE_OK, "message": "ok", "data": {"total": len(books), "books": books}}), 200


@app.route("/api/v1/books/<int:book_id>", methods=["GET"])
def get_book(book_id: int):
    """书籍详情（需登录；不存在 → 404）。"""
    err, _username = _require_user()
    if err is not None:
        return err
    book = BOOKS.get(book_id)
    if not book:
        return _not_found("书籍不存在: id=%s" % book_id)
    return jsonify({"code": CODE_OK, "message": "ok", "data": _book_to_dict(book)}), 200


@app.route("/api/v1/books", methods=["POST"])
def create_book():
    """新增书籍（需登录；参数非法 → 400）。"""
    err, _username = _require_user()
    if err is not None:
        return err
    payload = request.get_json(silent=True) or {}
    validate_msg = _validate_book_payload(payload)
    if validate_msg:
        return _param_error(validate_msg)
    book_id = _next_book_id()
    BOOKS[book_id] = {
        "id": book_id,
        "title": str(payload["title"]).strip(),
        "author": str(payload.get("author", "")).strip(),
        "price": float(payload["price"]),
        "stock": int(payload["stock"]),
    }
    return jsonify({"code": CODE_OK, "message": "ok", "data": _book_to_dict(BOOKS[book_id])}), 201


@app.route("/api/v1/books/<int:book_id>", methods=["PUT"])
def update_book(book_id: int):
    """更新书籍（需登录；不存在 → 404；参数非法 → 400）。"""
    err, _username = _require_user()
    if err is not None:
        return err
    book = BOOKS.get(book_id)
    if not book:
        return _not_found("书籍不存在: id=%s" % book_id)
    payload = request.get_json(silent=True) or {}
    merged = {
        "title": payload.get("title", book["title"]),
        "author": payload.get("author", book["author"]),
        "price": payload.get("price", book["price"]),
        "stock": payload.get("stock", book["stock"]),
    }
    validate_msg = _validate_book_payload(merged)
    if validate_msg:
        return _param_error(validate_msg)
    book.update({
        "title": str(merged["title"]).strip(),
        "author": str(merged["author"]).strip(),
        "price": float(merged["price"]),
        "stock": int(merged["stock"]),
    })
    return jsonify({"code": CODE_OK, "message": "ok", "data": _book_to_dict(book)}), 200


@app.route("/api/v1/books/<int:book_id>", methods=["DELETE"])
def delete_book(book_id: int):
    """删除书籍（需登录；仅 admin 角色可删 → 越权 403；不存在 → 404）。"""
    err, username = _require_user()
    if err is not None:
        return err
    user = USERS.get(username, {})
    if user.get("role") != "admin":
        return _forbidden("仅 admin 角色可删除书籍")
    book = BOOKS.get(book_id)
    if not book:
        return _not_found("书籍不存在: id=%s" % book_id)
    removed = BOOKS.pop(book_id)
    return jsonify({"code": CODE_OK, "message": "ok", "data": _book_to_dict(removed)}), 200


if __name__ == "__main__":
    seed_if_empty()
    # 真实可启动：禁用 reloader 避免子进程双实例；端口 8010（避开 8000/5001）
    app.run(host="127.0.0.1", port=8010, debug=False, use_reloader=False, threaded=True)
