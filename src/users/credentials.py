# -*- coding: utf-8 -*-
"""用户凭证管理模块"""
import logging

from src.users._user_db_core import _conn

_log = logging.getLogger(__name__)


def _safe_decrypt(value: str, user_id: str, key: str, project: str) -> str:
    """统一的解密入口：失败时输出 WARNING 日志而非静默吞错。

    返回空串代表两种情况：
    1) 密文本身为空（业务正常）
    2) 解密失败（密钥变更/密文损坏）—— 这种情况下会打 WARNING
    """
    if not value:
        return ""
    from src.users.auth import _decrypt_auto

    plain = _decrypt_auto(value)
    if not plain:
        _log.warning(
            "[credentials] 解密失败 user=%s key=%s project=%s vlen=%d —— "
            "可能是 Fernet 密钥已变更，建议用户重新填写该字段",
            user_id,
            key,
            project,
            len(value),
        )
    return plain


def get_user_credential(user_id: str, key: str, project: str = "") -> str:
    """从 user_credentials_v2 获取用户凭证。

    查找顺序：当前项目 → 当前用户全局(*) → 管理员全局(*) 兜底。
    """
    c = _conn()
    try:
        row = c.execute(
            "SELECT value FROM user_credentials_v2 WHERE user_id=%s AND `key`=%s AND project=%s",
            (user_id, key, project),
        ).fetchone()
        hit_project = project
        if (not row or not row["value"]) and project:
            row = c.execute(
                "SELECT value FROM user_credentials_v2 WHERE user_id=%s AND `key`=%s AND project='*'",
                (user_id, key),
            ).fetchone()
            hit_project = "*"
        # 当前用户没有 → 回退到管理员全局凭证
        if not row or not row["value"]:
            row = c.execute(
                "SELECT value FROM user_credentials_v2 WHERE user_id='admin' AND `key`=%s AND project='*'",
                (key,),
            ).fetchone()
            hit_project = "*"
        return (
            _safe_decrypt(row["value"], user_id, key, hit_project)
            if row and row["value"]
            else ""
        )
    finally:

        c.close()


def set_user_credential(user_id: str, key: str, value: str, project: str = "") -> None:
    """设置用户凭证到 user_credentials_v2。"""
    from src.users.auth import _encrypt_auto

    c = _conn()
    try:
        c.execute(
            "REPLACE INTO user_credentials_v2(user_id, `key`, value, project) VALUES(%s,%s,%s,%s)",
            (user_id, key, _encrypt_auto(value) if value else "", project or ""),
        )
        c.commit()
    finally:

        c.close()


def save_user_credentials_batch(
    user_id: str, fields: dict, project: str = "", sensitive_keys: set = None
) -> dict:
    """性能优化：批量保存用户凭证，单连接复用。

    原先每个字段独立打开/关闭连接（N 字段 = 2N 次 DB ping），
    现在所有查询+写入共用一条连接。

    返回 {"saved": [...], "skipped_sensitive": [...]}
    """
    from src.users.auth import _encrypt_auto

    if not fields:
        return {"saved": [], "skipped_sensitive": []}

    sensitive_keys = sensitive_keys or set()
    c = _conn()
    try:
        # 一次性查出所有现有值（project-specific）
        keys_list = list(fields.keys())
        placeholders = ", ".join(["%s"] * len(keys_list))
        sql = (
            f"SELECT `key`, value FROM user_credentials_v2 "
            f"WHERE user_id=%s AND `key` IN ({placeholders}) AND project=%s"
        )
        existing_map = {}
        for row in c.execute(sql, [user_id] + keys_list + [project]).fetchall():
            if row["value"]:
                existing_map[row["key"]] = _safe_decrypt(
                    row["value"], user_id, row["key"], project
                )

        # 对 project-specific 没找到的 key，查 project='*' fallback
        missing_keys = [k for k in keys_list if k not in existing_map and project]
        if missing_keys:
            ph2 = ", ".join(["%s"] * len(missing_keys))
            sql2 = (
                f"SELECT `key`, value FROM user_credentials_v2 "
                f"WHERE user_id=%s AND `key` IN ({ph2}) AND project='*'"
            )
            for row in c.execute(sql2, [user_id] + missing_keys).fetchall():
                if row["value"] and row["key"] not in existing_map:
                    existing_map[row["key"]] = _safe_decrypt(
                        row["value"], user_id, row["key"], "*"
                    )

        # 决定保存/跳过，批量写入
        saved = []
        skipped_sensitive = []
        for key, value in fields.items():
            v_str = str(value).strip()
            if key in sensitive_keys:
                if not v_str:
                    skipped_sensitive.append(key)
                    continue
                existing = existing_map.get(key, "")
                if existing and str(existing).strip() == v_str:
                    skipped_sensitive.append(key)
                    continue
            c.execute(
                "REPLACE INTO user_credentials_v2(user_id, `key`, value, project) VALUES(%s,%s,%s,%s)",
                (user_id, key, _encrypt_auto(v_str) if v_str else "", project or ""),
            )
            saved.append(key)
        c.commit()
        return {"saved": saved, "skipped_sensitive": skipped_sensitive}
    finally:
        c.close()


def delete_user_credential(user_id: str, key: str, project: str = "") -> None:
    """从 user_credentials_v2 删除凭证。"""
    c = _conn()
    try:
        c.execute(
            "DELETE FROM user_credentials_v2 WHERE user_id=%s AND `key`=%s AND project=%s",
            (user_id, key, project),
        )
        c.commit()
    finally:

        c.close()


def get_all_user_credentials(user_id: str, project: str = "") -> dict:
    """获取用户的所有凭证（自动解密），返回 {key: value} 字典。

    ★ 修复：解密失败的密文不再静默吞掉。
    - 旧行为：把整行丢弃 → 前端表现为"没保存"，用户误以为要重输
    - 新行为：解密失败时 _safe_decrypt 输出 WARNING，并把该 key 以空串返回
              前端能看到 key 存在但值为空，可提示"请重新输入"
    """
    c = _conn()
    try:
        sql = (
            (
                "SELECT `key`, value FROM user_credentials_v2 WHERE user_id=%s AND project IN (%s, '*')",
                (user_id, project),
            )
            if project
            else (
                "SELECT `key`, value FROM user_credentials_v2 WHERE user_id=%s",
                (user_id,),
            )
        )
        return {
            row["key"]: _safe_decrypt(row["value"], user_id, row["key"], project)
            for row in c.execute(*sql).fetchall()
            if row["value"]  # 仅过滤密文本身为空的行；解密失败的行保留为空值
        }
    finally:

        c.close()


def get_user_credentials_by_keys(user_id: str, keys: list, project: str = "") -> dict:
    """按指定 key 列表获取用户凭证（减少不必要的数据传输）。

    参数：
        user_id: 用户 ID
        keys: 需要的凭证 key 名列表，如 ['CONFLUENCE_USERNAME', 'CONFLUENCE_PASSWORD']
        project: 项目名，空 = 所有项目
    返回：
        {key: 明文值} 字典；解密失败时该 key 的值为空串（同时日志会 WARNING）
    查找顺序：当前用户 → 管理员全局(*) 兜底。
    """
    if not keys:
        return {}
    c = _conn()
    try:
        ph = ", ".join(["%s"] * len(keys))
        if project:
            sql = f"SELECT `key`, value FROM user_credentials_v2 WHERE user_id=%s AND `key` IN ({ph}) AND project IN (%s, '*')"
            params = [user_id] + list(keys) + [project]
        else:
            sql = f"SELECT `key`, value FROM user_credentials_v2 WHERE user_id=%s AND `key` IN ({ph})"
            params = [user_id] + list(keys)
        rows = c.execute(sql, tuple(params)).fetchall()
        result = {
            row["key"]: _safe_decrypt(row["value"], user_id, row["key"], project)
            for row in rows
            if row["value"]
        }
        # 当前用户没有的 key → 回退到管理员全局凭证
        missing_keys = [k for k in keys if k not in result]
        if missing_keys and user_id != "admin":
            mph = ", ".join(["%s"] * len(missing_keys))
            admin_rows = c.execute(
                f"SELECT `key`, value FROM user_credentials_v2 WHERE user_id='admin' AND `key` IN ({mph}) AND project='*'",
                tuple(missing_keys),
            ).fetchall()
            for row in admin_rows:
                if row["value"] and row["key"] not in result:
                    result[row["key"]] = _safe_decrypt(
                        row["value"], "admin", row["key"], "*"
                    )
        return result
    finally:
        c.close()


def copy_user_credentials(
    user_id: str, source_project: str, target_project: str
) -> dict:
    """从源项目复制用户凭据到目标项目（仅填充空字段，不覆盖已有值）。

    密文直接复制（不解密再加密），避免 Fernet 密钥变更风险。
    返回 {"ok": True/False, "copied": [...], "skipped_existing": [...], "message": str}
    """
    c = _conn()
    try:
        # 1) 获取源项目所有凭据
        src_rows = c.execute(
            "SELECT `key`, value FROM user_credentials_v2 WHERE user_id=%s AND project=%s AND value != ''",
            (user_id, source_project),
        ).fetchall()

        if not src_rows:
            return {
                "ok": True,
                "copied": [],
                "skipped_existing": [],
                "message": f"源项目 {source_project} 没有凭据可复制",
            }

        # 2) 获取目标项目已有凭据 key（非空值才算已有）
        existing_keys = set()
        for row in c.execute(
            "SELECT `key` FROM user_credentials_v2 WHERE user_id=%s AND project=%s AND value != ''",
            (user_id, target_project),
        ).fetchall():
            existing_keys.add(row["key"])

        # 3) 只复制目标项目不存在的 key（密文直拷）
        copied = []
        skipped = []
        for row in src_rows:
            key = row["key"]
            if key in existing_keys:
                skipped.append(key)
                continue
            c.execute(
                "INSERT INTO user_credentials_v2(user_id, `key`, value, project) VALUES(%s,%s,%s,%s) "
                "ON DUPLICATE KEY UPDATE value=VALUES(value)",
                (user_id, key, row["value"], target_project),
            )
            copied.append(key)

        c.commit()
        return {
            "ok": True,
            "copied": copied,
            "skipped_existing": skipped,
            "message": f"已复制 {len(copied)} 个凭据"
            + (f"，跳过 {len(skipped)} 个已有" if skipped else ""),
        }
    except Exception as e:
        _log.error(
            "[credentials] 复制凭据失败 user=%s %s→%s: %s",
            user_id,
            source_project,
            target_project,
            e,
        )
        return {
            "ok": False,
            "copied": [],
            "skipped_existing": [],
            "message": f"复制失败: {str(e)[:200]}",
        }
    finally:
        c.close()
