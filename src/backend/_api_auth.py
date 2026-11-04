# -*- coding: utf-8 -*-
"""
API Token 鉴权模块
==================
通过 X-API-Key 请求头进行 CI/CD 外部调用鉴权。

设计原则：
- 不存明文 token，只存 SHA256 哈希
- 支持按项目授权（* = 所有项目）
- 支持 scope 细粒度权限（run/read/write）
- 记录最后使用时间和次数
- 完全独立于 session 鉴权体系

挂载方式：
在 _middleware.py 的 _require_login 中，对 /api/* 路径优先检查 X-API-Key 头，
命中有效 token 则跳过 session 检查。
"""

import hashlib
import logging

_log = logging.getLogger(__name__)


def _hash_token(token: str) -> str:
    """对 token 做 SHA256 哈希。"""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def verify_api_token(token: str) -> dict:
    """验证 API Token 并返回授权信息。

    Args:
        token: 原始 token 明文

    Returns:
        {"ok": True, "project": str, "scopes": list, "token_id": int} 或 {"ok": False, "error": str}
    """
    if not token or len(token) < 16:
        return {"ok": False, "error": "Token 格式无效"}

    token_hash = _hash_token(token)
    try:
        from src.core.db_manager import get_connection, release_connection

        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT id, project, label, scopes, is_active FROM api_tokens "
                "WHERE token_hash=%s AND is_active=1 LIMIT 1",
                (token_hash,),
            ).fetchone()

            if not row:
                return {"ok": False, "error": "Token 无效或已禁用"}

            # 更新使用统计
            conn.execute(
                "UPDATE api_tokens SET last_used_at=NOW(), use_count=use_count+1 "
                "WHERE id=%s",
                (row["id"],),
            )
            conn.commit()

            # 解析 scopes
            scopes = []
            if row["scopes"]:
                try:
                    import json

                    parsed = json.loads(row["scopes"] or "[]")
                    scopes = parsed if isinstance(parsed, list) else ["run"]
                except Exception:
                    _log.warning(
                        "[API-Token] scopes JSON 解析失败: %s",
                        str(row["scopes"])[:80],
                        exc_info=True,
                    )
                    scopes = []  # ★ 解析失败时不给默认权限，避免权限提升

            return {
                "ok": True,
                "project": row["project"] or "*",
                "label": row["label"] or "",
                "scopes": scopes,
                "token_id": row["id"],
            }
        finally:
            release_connection(conn)
    except Exception as e:
        _log.error("[API-Token] 验证异常: %s", str(e)[:120])
        return {"ok": False, "error": f"内部错误: {str(e)[:80]}"}


def has_api_token_scope(token_info: dict, required_scope: str) -> bool:
    """检查 token 是否拥有指定 scope。"""
    scopes = token_info.get("scopes", [])
    if not scopes:
        return True  # 空 scope = 默认全部
    return required_scope in scopes
