# -*- coding: utf-8 -*-
"""
背景图片/配置路由处理函数（v3 — MySQL 图库存储）。
──────────────────────────
public API:
  api_upload_bg        POST /api/upload-bg
  api_bg_image         GET  /api/bg-image            (当前激活)
  api_bg_image_by_id   GET  /api/bg-image/<gid>      (按 ID 取图，用于缩略图)
  api_bg_gallery_list  GET  /api/bg-gallery
  api_bg_gallery_delete DELETE /api/bg-gallery/<gid>
  api_bg_gallery_activate POST /api/bg-gallery/activate
  api_bg_config        GET/POST /api/bg-config

★ v3 改动（2026-06-22）：
- 图片二进制存到 MySQL 表 user_bg_gallery（LONGBLOB），不再使用文件系统
- 每用户最多保留 _BG_GALLERY_MAX 张图（在上传时检查）
- is_current 字段标记"当前在用"，每个用户仅 1 张 is_current=1（应用层事务保证）
- bg_config 仍存 user_credentials_v2（保持兼容）
"""

import json
import logging
import hashlib
import time
from pathlib import Path
from flask import jsonify, request, Response

_log = logging.getLogger(__name__)

# ★ 每用户图库容量上限（超出禁止上传，引导先删除）
_BG_GALLERY_MAX = 20

# ── ★ bg-image 独立小连接池（与任务线程主池隔离，避免互相饿死）──
# 任务线程用主池（max=60），bg-image 最多占 5 个连接，剩余给任务线程。
# _bg_pool 是 PooledDB 实例，_bg_conn() 返回池化连接。
_BG_POOL_MAX = 5
_BG_POOL = None
_BG_POOL_LOCK = __import__("threading").Lock()


def _bg_conn():
    """从 bg-image 专用池借用连接（不占用任务线程主池配额）。"""
    global _BG_POOL
    if _BG_POOL is None:
        with _BG_POOL_LOCK:
            if _BG_POOL is None:
                from dbutils.pooled_db import PooledDB
                import pymysql as _pymysql
                from src.core.db_config import _load_config

                _db = _load_config()
                _BG_POOL = PooledDB(
                    creator=_pymysql,
                    mincached=1,
                    maxcached=_BG_POOL_MAX,
                    maxconnections=_BG_POOL_MAX,
                    blocking=True,
                    host=_db.get("host", "127.0.0.1"),
                    port=int(_db.get("port", 3306)),
                    user=_db.get("user", "root"),
                    password=_db.get("password", ""),
                    database=_db.get("database", "e2e_test_engine"),
                    charset=_db.get("charset", "utf8mb4"),
                    autocommit=True,
                    cursorclass=_pymysql.cursors.DictCursor,
                )
                _log.info("[bg-pool] 独立连接池已初始化: max=%d", _BG_POOL_MAX)
    return _BG_POOL.connection()


# ★ 支持的扩展名 → MIME
_EXT_MIME = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "gif": "image/gif",
    "webp": "image/webp",
}
_MAGIC = {
    b"\xff\xd8\xff": "jpeg",
    b"\x89PNG\r\n\x1a\n": "png",
    b"GIF87a": "gif",
    b"GIF89a": "gif",
    b"RIFF": "webp",
}


def _detect_mime(header: bytes, ext: str) -> tuple:
    """根据文件头返回 (detected_fmt, mime)；若 ext 与 detected 冲突返回 (None, None)。"""
    detected = None
    for magic, fmt in _MAGIC.items():
        if header.startswith(magic):
            detected = fmt
            break
    if detected and detected != ext and not (detected == "jpeg" and ext == "jpg"):
        return None, None
    mime = _EXT_MIME.get(ext, "application/octet-stream")
    return detected or ext, mime


# ═══════════════════════════════════════════════════════════════
# API 实现
# ═══════════════════════════════════════════════════════════════
def api_upload_bg(_BG_UPLOAD_MAX_SIZE):
    """POST /api/upload-bg — 上传新背景图入库，并设为当前激活。"""
    from src.core.config_loader import get_current_user

    uid = get_current_user()
    if not uid:
        return jsonify({"ok": False, "error": "请先登录"}), 401
    if "file" not in request.files:
        return jsonify({"ok": False, "error": "未上传文件"}), 400
    f = request.files["file"]
    if not f.filename:
        return jsonify({"ok": False, "error": "空文件名"}), 400
    ext = f.filename.rsplit(".", 1)[-1].lower() if "." in f.filename else ""
    if ext not in _EXT_MIME:
        return jsonify({"ok": False, "error": "仅支持 jpg/png/gif/webp"}), 400

    # 文件大小
    f.seek(0, 2)
    size = f.tell()
    f.seek(0)
    if size > _BG_UPLOAD_MAX_SIZE:
        return (
            jsonify(
                {
                    "ok": False,
                    "error": f"文件过大（{size // 1024 // 1024}MB），上限 {_BG_UPLOAD_MAX_SIZE // 1024 // 1024}MB",
                }
            ),
            400,
        )
    if size == 0:
        return jsonify({"ok": False, "error": "空文件"}), 400

    # 魔数校验
    header = f.read(12)
    f.seek(0)
    _, mime = _detect_mime(header, ext)
    if mime is None:
        return jsonify({"ok": False, "error": "文件头与扩展名不匹配"}), 400

    data = f.read()

    # ── 容量上限检查 + 入库（事务）──
    from src.users._user_db_core import _conn

    c = _conn()
    cur = None
    try:
        # ★ 修复：c 是 PooledDB 连接对象，没有 execute 方法，必须通过 cursor 执行
        cur = c.cursor()
        cur.execute(
            "SELECT COUNT(*) AS cnt FROM user_bg_gallery WHERE user_id=%s", (uid,)
        )
        cnt_row = cur.fetchone()
        cnt = (
            (cnt_row.get("cnt") if isinstance(cnt_row, dict) else cnt_row[0])
            if cnt_row
            else 0
        )
        if cnt >= _BG_GALLERY_MAX:
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": f"图库已满（{cnt}/{_BG_GALLERY_MAX}），请先删除旧图",
                    }
                ),
                400,
            )

        # 先把现有 is_current 全部置 0
        cur.execute(
            "UPDATE user_bg_gallery SET is_current=0 WHERE user_id=%s AND is_current=1",
            (uid,),
        )
        cur.execute(
            "INSERT INTO user_bg_gallery(user_id, image_data, mime_type, image_size, is_current) "
            "VALUES(%s, %s, %s, %s, 1)",
            (uid, data, mime, len(data)),
        )
        new_id = getattr(cur, "lastrowid", 0) or 0
        c.commit()
        _log.info(
            "[bg-upload] 用户 %s 上传背景图 id=%s mime=%s (%d bytes)",
            uid,
            new_id,
            mime,
            size,
        )
        return jsonify(
            {"ok": True, "id": new_id, "mime_type": mime, "image_size": size}
        )
    except Exception as e:
        try:
            c.rollback()
        except Exception:
            _log.warning("[bg-upload] rollback 失败", exc_info=True)
        _log.error("[bg-upload] 入库失败: %s", e, exc_info=True)
        return jsonify({"ok": False, "error": "入库失败: " + str(e)[:200]}), 500
    finally:
        try:
            if cur:
                cur.close()
        except Exception:
            _log.warning("[bg] cursor close 失败", exc_info=True)
        try:
            c.close()
        except Exception:
            _log.warning("[bg] close 连接失败", exc_info=True)


def api_bg_image():
    """GET /api/bg-image — 取当前用户激活的背景图二进制。

    ★ 优化（2026-07-17）:
    - 用独立小连接池 _bg_conn()，不占用任务线程主池配额
    - ETag + If-None-Match 驱动 304 响应，消除重复传输
    """
    from src.core.config_loader import get_current_user

    uid = get_current_user()
    if not uid:
        return jsonify({"ok": False, "error": "请先登录"}), 401
    c = _bg_conn()
    cur = None
    try:
        cur = c.cursor()
        cur.execute(
            "SELECT image_data, mime_type FROM user_bg_gallery "
            "WHERE user_id=%s AND is_current=1 LIMIT 1",
            (uid,),
        )
        row = cur.fetchone()
        if not row:
            return jsonify({"ok": False, "error": "无背景图"}), 404
        if isinstance(row, dict):
            data = row.get("image_data")
            mime = row.get("mime_type") or "application/octet-stream"
        else:
            data = row[0]
            mime = row[1] or "application/octet-stream"
        # ★ ETag: 基于图片内容哈希，浏览器命中时返回 304 不传 body
        etag = '"' + hashlib.md5(data).hexdigest()[:16] + '"'
        if request.headers.get("If-None-Match") == etag:
            resp = Response(status=304)
            resp.headers["ETag"] = etag
            resp.headers["Cache-Control"] = "private, max-age=3600"
            return resp
        resp = Response(data, mimetype=mime)
        resp.headers["ETag"] = etag
        return resp
    finally:
        try:
            if cur:
                cur.close()
            c.close()
        except Exception:
            _log.warning("[bg] close 连接失败", exc_info=True)


def api_bg_image_by_id(gid):
    """GET /api/bg-image/<gid> — 按 ID 取图（缩略图请求专用，仅限本人）。

    ★ 优化（2026-07-17）:
    - 用独立小连接池 _bg_conn()，不占用任务线程主池配额
    - ETag + If-None-Match 驱动 304 响应，消除重复传输
    """
    from src.core.config_loader import get_current_user

    uid = get_current_user()
    if not uid:
        return jsonify({"ok": False, "error": "请先登录"}), 401
    try:
        gid_int = int(gid)
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "无效 id"}), 400
    c = _bg_conn()
    cur = None
    try:
        cur = c.cursor()
        cur.execute(
            "SELECT image_data, mime_type FROM user_bg_gallery WHERE id=%s AND user_id=%s",
            (gid_int, uid),
        )
        row = cur.fetchone()
        if not row:
            return jsonify({"ok": False, "error": "图片不存在或无权访问"}), 404
        if isinstance(row, dict):
            data = row.get("image_data")
            mime = row.get("mime_type") or "application/octet-stream"
        else:
            data = row[0]
            mime = row[1] or "application/octet-stream"
        # ★ ETag: 基于图片内容哈希，浏览器命中时返回 304 不传 body
        etag = '"' + hashlib.md5(data).hexdigest()[:16] + '"'
        if request.headers.get("If-None-Match") == etag:
            resp = Response(status=304)
            resp.headers["ETag"] = etag
            resp.headers["Cache-Control"] = "private, max-age=3600"
            return resp
        resp = Response(data, mimetype=mime)
        resp.headers["ETag"] = etag
        return resp
    finally:
        try:
            if cur:
                cur.close()
            c.close()
        except Exception:
            _log.warning("[bg] close 连接失败", exc_info=True)


def api_bg_gallery_list():
    """GET /api/bg-gallery — 当前用户图库元信息列表（不含二进制）。"""
    from src.core.config_loader import get_current_user

    uid = get_current_user()
    if not uid:
        return jsonify({"ok": False, "error": "请先登录"}), 401
    from src.users._user_db_core import _conn

    c = _conn()
    cur = None
    try:
        # ★ 修复：c 是 PooledDB 连接对象，没有 execute 方法，必须通过 cursor 执行
        cur = c.cursor()
        cur.execute(
            "SELECT id, mime_type, image_size, is_current, created_at "
            "FROM user_bg_gallery WHERE user_id=%s ORDER BY id DESC",
            (uid,),
        )
        rows = cur.fetchall() or []
        items = []
        for r in rows:
            if isinstance(r, dict):
                items.append(
                    {
                        "id": r.get("id"),
                        "mime_type": r.get("mime_type"),
                        "image_size": r.get("image_size") or 0,
                        "is_current": int(r.get("is_current") or 0),
                        "created_at": str(r.get("created_at") or ""),
                    }
                )
            else:
                items.append(
                    {
                        "id": r[0],
                        "mime_type": r[1],
                        "image_size": r[2] or 0,
                        "is_current": int(r[3] or 0),
                        "created_at": str(r[4] or ""),
                    }
                )
        return jsonify({"ok": True, "items": items, "max": _BG_GALLERY_MAX})
    finally:
        try:
            if cur:
                cur.close()
        except Exception:
            _log.warning("[bg] cursor close 失败", exc_info=True)
        try:
            c.close()
        except Exception:
            _log.warning("[bg] close 连接失败", exc_info=True)


def api_bg_gallery_delete(gid):
    """DELETE /api/bg-gallery/<gid> — 删除图库中的某张图（仅本人）。

    若删的是当前激活图 → 返回 {fallback_default: true} 提示前端切回默认。
    """
    from src.core.config_loader import get_current_user

    uid = get_current_user()
    if not uid:
        return jsonify({"ok": False, "error": "请先登录"}), 401
    try:
        gid_int = int(gid)
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "无效 id"}), 400
    from src.users._user_db_core import _conn

    c = _conn()
    cur = None
    try:
        # ★ 修复：c 是 PooledDB 连接对象，没有 execute 方法，必须通过 cursor 执行
        cur = c.cursor()
        cur.execute(
            "SELECT is_current FROM user_bg_gallery WHERE id=%s AND user_id=%s",
            (gid_int, uid),
        )
        row = cur.fetchone()
        if not row:
            return jsonify({"ok": False, "error": "图片不存在或无权访问"}), 404
        was_current = bool(
            (row.get("is_current") if isinstance(row, dict) else row[0]) or 0
        )
        cur.execute(
            "DELETE FROM user_bg_gallery WHERE id=%s AND user_id=%s", (gid_int, uid)
        )
        c.commit()
        _log.info(
            "[bg-delete] 用户 %s 删除背景图 id=%s (was_current=%s)",
            uid,
            gid_int,
            was_current,
        )
        return jsonify({"ok": True, "fallback_default": was_current})
    except Exception as e:
        try:
            c.rollback()
        except Exception:
            _log.warning("[bg-upload] rollback 失败", exc_info=True)
        _log.error("[bg-delete] 失败: %s", e, exc_info=True)
        return jsonify({"ok": False, "error": "删除失败: " + str(e)[:200]}), 500
    finally:
        try:
            if cur:
                cur.close()
        except Exception:
            _log.warning("[bg] cursor close 失败", exc_info=True)
        try:
            c.close()
        except Exception:
            _log.warning("[bg] close 连接失败", exc_info=True)


def api_bg_gallery_activate():
    """POST /api/bg-gallery/activate — 把图库中某张图设为当前激活。

    Body: {"id": <gid>}
    """
    from src.core.config_loader import get_current_user

    uid = get_current_user()
    if not uid:
        return jsonify({"ok": False, "error": "请先登录"}), 401
    data = request.get_json(silent=True) or {}
    gid = data.get("id")
    try:
        gid_int = int(gid)
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "无效 id"}), 400
    from src.users._user_db_core import _conn

    c = _conn()
    cur = None
    try:
        # ★ 修复：c 是 PooledDB 连接对象，没有 execute 方法，必须通过 cursor 执行
        cur = c.cursor()
        cur.execute(
            "SELECT 1 FROM user_bg_gallery WHERE id=%s AND user_id=%s", (gid_int, uid)
        )
        row = cur.fetchone()
        if not row:
            return jsonify({"ok": False, "error": "图片不存在或无权访问"}), 404
        cur.execute(
            "UPDATE user_bg_gallery SET is_current=0 WHERE user_id=%s AND is_current=1",
            (uid,),
        )
        cur.execute(
            "UPDATE user_bg_gallery SET is_current=1 WHERE id=%s AND user_id=%s",
            (gid_int, uid),
        )
        c.commit()
        return jsonify({"ok": True, "id": gid_int})
    except Exception as e:
        try:
            c.rollback()
        except Exception:
            _log.warning("[bg-upload] rollback 失败", exc_info=True)
        _log.error("[bg-activate] 失败: %s", e, exc_info=True)
        return jsonify({"ok": False, "error": "激活失败: " + str(e)[:200]}), 500
    finally:
        try:
            if cur:
                cur.close()
        except Exception:
            _log.warning("[bg] cursor close 失败", exc_info=True)
        try:
            c.close()
        except Exception:
            _log.warning("[bg] close 连接失败", exc_info=True)


def api_bg_config():
    """GET/POST /api/bg-config — 读取/保存用户级背景配置（跨项目共享）。

    ★ 配置存储在 user_credentials_v2 表，project='*'（个性化属于用户而非项目）
    ★ 包含 kpi_columns 配置（KPI网格每行列数，默认6）
    """
    try:
        from src.core.config_loader import get_current_user

        uid = get_current_user()
        if not uid:
            return jsonify({"ok": False, "error": "请先登录"}), 401

        # ── GET: 读取用户级背景配置 ──
        if request.method == "GET":
            from src.users.manager import get_user_credentials_by_keys

            creds = get_user_credentials_by_keys(uid, ["bg_config"], project="*")
            bg_val = creds.get("bg_config", "")
            if bg_val:
                try:
                    cfg = json.loads(bg_val)
                    # 确保 kpi_columns 有默认值
                    if "kpi_columns" not in cfg:
                        cfg["kpi_columns"] = 6
                    # ★ 确保 card_opacity 有默认值（兼容旧配置）
                    if "card_opacity" not in cfg:
                        cfg["card_opacity"] = 100
                    # ★ 全局字体大小（2026-09-01）：确保 ui_scale 有默认值（兼容旧配置）
                    if "ui_scale" not in cfg:
                        cfg["ui_scale"] = 1
                    return jsonify({"ok": True, **cfg})
                except Exception:
                    _log.debug(
                        "[bg-config] 历史配置 JSON 解析失败（使用默认配置）",
                        exc_info=True,
                    )
            # 兜底：返回前端默认配置
            default_cfg = (
                Path(__file__).resolve().parent.parent.parent.parent.parent
                / "frontend"
                / "BG"
                / "bg_config.json"
            )
            if default_cfg.is_file():
                with open(default_cfg, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                    if "kpi_columns" not in cfg:
                        cfg["kpi_columns"] = 6
                    if "card_opacity" not in cfg:
                        cfg["card_opacity"] = 100
                    if "ui_scale" not in cfg:
                        cfg["ui_scale"] = 1
                    return jsonify({"ok": True, **cfg})
            return jsonify(
                {"ok": True, "mode": "default", "kpi_columns": 6, "card_opacity": 100, "ui_scale": 1}
            )

        # ── POST: 保存用户级背景配置 ──
        data = request.get_json(silent=True) or {}
        # 确保 kpi_columns 有值
        if "kpi_columns" not in data:
            data["kpi_columns"] = 6
        # ★ 确保 card_opacity 有值（兼容前端旧版未传该字段）
        if "card_opacity" not in data:
            data["card_opacity"] = 100
        # ★ 全局字体大小：确保 ui_scale 有值（兼容前端旧版未传该字段）
        if "ui_scale" not in data:
            data["ui_scale"] = 1
        from src.users.manager import set_user_credential, log_action

        # ★ 强制 project="*"，个性化与项目无关
        set_user_credential(
            uid, "bg_config", json.dumps(data, ensure_ascii=False), project="*"
        )
        try:
            log_action(
                uid,
                "bg-save",
                scope_type="user",
                scope_id=uid,
                detail="mode="
                + str(data.get("mode", "?"))
                + ",kpi_columns="
                + str(data.get("kpi_columns", 6)),
            )
        except Exception:
            _log.debug("[bg-config] 审计日志写入跳过（非关键）", exc_info=True)
        return jsonify({"ok": True})
    except Exception as e:
        _log.error("[api_bg_config] 异常: %s", e, exc_info=True)
        return jsonify({"ok": False, "error": str(e)[:200]}), 500
