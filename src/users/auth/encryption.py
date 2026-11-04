# -*- coding: utf-8 -*-
"""Fernet 密码加密：密钥读取/写入/缓存 + 加解密入口 — 从 auth.py 拆出，零业务逻辑变更。"""

import threading
from src.core.guarded import guard
import logging
from pathlib import Path

_log = logging.getLogger(__name__)

_SYS_CONFIG_KEY = "encryption_key_v2"
# ★ 密钥本地备份（安全降级，非业务数据缓存）——防止 MySQL 短暂不可达时
#    _load_or_create_key() 误判为"首次启动"而生成新密钥，覆盖掉正在使用的旧密钥。
#    主事实源为 MySQL system_config；此 .bak 仅作 MySQL 不可达时的恢复降级路径，
#    不违背"全走 MySQL 零文件残留"——密钥是安全凭据非业务数据，用户决策保留。
_KEY_BACKUP_PATH = "docs/encryption_key_recovery/encryption_key_v2.bak"

_fernet_instance = None
_fernet_lock = threading.Lock()


def _read_key_from_backup() -> bytes:
    """从本地备份文件读取密钥。失败返回 None。"""
    with guard("_read_key_from_backup: if not Path(_KEY_BACKUP_PATH).exists", log=_log):
        if not Path(_KEY_BACKUP_PATH).exists():
            return None
        with open(_KEY_BACKUP_PATH, "r", encoding="utf-8") as f:
            content = f.read().strip()
        if content:
            return content.encode("utf-8")
    return None


def _write_key_to_backup(key: bytes) -> bool:
    """把密钥写入本地备份文件（仅当文件不存在时；不覆盖现有备份）。"""
    try:
        Path(_KEY_BACKUP_PATH).parent.mkdir(parents=True, exist_ok=True)
        if Path(_KEY_BACKUP_PATH).exists():
            _log.debug("[Auth] 本地密钥备份已存在，跳过写入")
            return True
        key_str = key.decode("utf-8") if isinstance(key, bytes) else str(key)
        with open(_KEY_BACKUP_PATH, "w", encoding="utf-8") as f:
            f.write(key_str)
        # Windows 上 chmod 仅影响只读位；POSIX 上设为 0600
        try:
            Path(_KEY_BACKUP_PATH).chmod(0o600)
        except Exception as _e:
            _log.debug("write: %s", _e, exc_info=True)
        _log.info("[Auth] 加密密钥已备份到 %s", _KEY_BACKUP_PATH)
        return True
    except Exception as e:
        _log.warning("[Auth] 写入本地密钥备份失败: %s", str(e)[:120])
        return False


def _has_existing_ciphertext() -> bool:
    """检查 user_credentials_v2 是否已存在密文行（即系统是否已在用密钥）。
    用于判断"读不到密钥"时能否安全地生成新密钥。
    任何 DB 异常都视为"无法确认"，按已存在处理（拒绝生成）以防误覆盖。
    """
    conn = None
    try:
        conn = _get_mysql_conn()
        if conn is None:
            return True  # 连不上 DB 时按"已存在"处理，保守拒绝
        row = conn.execute(
            "SELECT 1 FROM user_credentials_v2 WHERE value IS NOT NULL AND value <> '' LIMIT 1"
        ).fetchone()
        return row is not None
    except Exception as e:
        _log.warning("[Auth] 检查现有密文失败，按'已存在'处理: %s", str(e)[:80])
        return True
    finally:
        if conn:
            try:
                conn.close()
            except Exception as _e:
                _log.debug("fetchone + conn.close + close(): %s", _e, exc_info=True)


def _get_mysql_conn():
    """获取 MySQL 连接（统一入口，处理 db_manager 不可用的情况）。"""
    try:
        from src.core.db_manager import get_connection

        return get_connection()
    except Exception as _e:
        _log.debug("[Encryption] MySQL 连接获取失败: %s", _e, exc_info=True)
        return None


def _read_key_from_mysql() -> bytes:
    """从 MySQL system_config 表读取加密密钥。失败返回 None。"""
    conn = None
    try:
        conn = _get_mysql_conn()
        if conn is None:
            return None
        row = conn.execute(
            "SELECT `value` FROM system_config WHERE `key`=%s", (_SYS_CONFIG_KEY,)
        ).fetchone()
        if row and row["value"]:
            return row["value"].encode("utf-8")
    except Exception as e:
        _log.debug("[Auth] 从 MySQL 读取密钥失败: %s", str(e)[:80])
    finally:
        if conn:
            try:
                conn.close()
            except Exception as _e:
                _log.debug("fetchone + conn.close + close(): %s", _e, exc_info=True)
    return None


def _write_key_to_mysql(key: bytes) -> bool:
    """将加密密钥写入 MySQL system_config 表。失败返回 False。"""
    conn = None
    try:
        conn = _get_mysql_conn()
        if conn is None:
            return False
        key_str = key.decode("utf-8") if isinstance(key, bytes) else str(key)
        conn.execute(
            "INSERT INTO system_config (`key`, `value`, `description`) "
            "VALUES (%s, %s, 'Fernet 密码加密密钥（v2）') "
            "ON DUPLICATE KEY UPDATE `value`=VALUES(`value`)",
            (_SYS_CONFIG_KEY, key_str),
        )
        conn.commit()
        _log.info("[Auth] 加密密钥已写入 MySQL system_config")
        return True
    except Exception as e:
        _log.warning("[Auth] 写入 MySQL 密钥失败: %s", str(e)[:80])
        if conn:
            try:
                conn.rollback()
            except Exception as _e:
                _log.debug("commit: %s", _e, exc_info=True)
        return False
    finally:
        if conn:
            try:
                conn.close()
            except Exception as _e:
                _log.debug("conn.close + close(): %s", _e, exc_info=True)


def _load_or_create_key() -> bytes:
    """加载或创建 Fernet 加密密钥。

    ★ 修复（2026-06-22）：杜绝"读不到就静默生成新密钥"的覆盖事故。

    优先级（从高到低）：
      1. 内存缓存（_fernet_instance）
      2. MySQL system_config 表（主事实源）
      3. 环境变量 ENCRYPTION_KEY_V2（容器/K8s 部署）
      4. 本地备份文件 docs/encryption_key_recovery/encryption_key_v2.bak
         （恢复路径：MySQL 不可达或被清空时）
      5. 全新生成 —— 仅当 user_credentials_v2 表空（确认是首次启动）时才允许；
                     否则抛 RuntimeError，拒绝静默覆盖旧密钥。

    任何成功获取到的密钥都会落地到本地备份（若备份不存在）。
    """
    import os

    global _fernet_instance
    if _fernet_instance is not None:
        return _fernet_instance

    with _fernet_lock:
        if _fernet_instance is not None:
            return _fernet_instance

        key = None
        source = None

        # 1) MySQL system_config（主事实源）
        key = _read_key_from_mysql()
        if key:
            source = "mysql"

        # 2) 环境变量（容器/K8s 部署）
        if not key:
            env_key = os.environ.get("ENCRYPTION_KEY_V2", "")
            if env_key:
                key = env_key.encode("utf-8")
                source = "env"
                _write_key_to_mysql(key)

        # 3) 本地备份（MySQL 短暂不可达时的恢复路径）
        if not key:
            key = _read_key_from_backup()
            if key:
                source = "backup"
                _log.warning(
                    "[Auth] MySQL 未读到密钥，已从本地备份 %s 恢复", _KEY_BACKUP_PATH
                )
                # 回写 MySQL，让主事实源同步
                _write_key_to_mysql(key)

        # 4) 仍读不到 → 仅在首次启动场景（无任何密文）时才允许生成
        if not key:
            if _has_existing_ciphertext():
                raise RuntimeError(
                    "[Auth] 无法获取 Fernet 加密密钥，且 user_credentials_v2 表中已存在密文。\n"
                    "拒绝生成新密钥（会导致所有现存凭据无法解密）。\n"
                    "请按以下顺序排查：\n"
                    "  1. 确认 MySQL 可达，且 system_config 表中 encryption_key_v2 行存在\n"
                    f"  2. 若有本地备份 {_KEY_BACKUP_PATH}，确认其内容完整\n"
                    "  3. 通过环境变量 ENCRYPTION_KEY_V2 提供历史密钥\n"
                    "  4. 若确实丢失，需清空 user_credentials_v2 表所有密文行后再启动"
                )
            from cryptography.fernet import Fernet

            key = Fernet.generate_key()
            source = "generated"
            _log.info("[Auth] 检测到首次启动（无现存密文），生成新加密密钥")
            if not _write_key_to_mysql(key):
                raise RuntimeError(
                    "[Auth] 无法将新生成的加密密钥写入 MySQL system_config 表。"
                    "请检查数据库连接或手动设置 ENCRYPTION_KEY_V2 环境变量。"
                )

        # 不论来源如何，都尝试写入本地备份（若文件已存在则跳过）
        _write_key_to_backup(key)
        _log.info("[Auth] 加密密钥就绪（source=%s）", source)

        from cryptography.fernet import Fernet

        _fernet_instance = Fernet(key)
        return _fernet_instance


def _encrypt(plain: str) -> str:
    """使用 Fernet 加密字符串 → base64 密文。空字符串不加密。"""
    if not plain:
        return ""
    f = _load_or_create_key()
    return f.encrypt(plain.encode("utf-8")).decode("ascii")


def _decrypt(encrypted: str) -> str:
    """使用 Fernet 解密 base64 密文 → 原文。失败返回空字符串。"""
    if not encrypted:
        return ""
    try:
        f = _load_or_create_key()
        return f.decrypt(encrypted.encode("ascii"), ttl=None).decode("utf-8")
    except Exception as _e:
        _log.debug("[Encryption] 解密失败: %s", _e, exc_info=True)
        return ""


def _decrypt_auto(encrypted: str) -> str:
    """解密：仅支持 Fernet (v2) 格式。v1 已废弃。"""
    if not encrypted:
        return ""
    return _decrypt(encrypted)


def _encrypt_auto(plain: str) -> str:
    """使用 Fernet 加密新数据。"""
    if not plain:
        return ""
    return _encrypt(plain)


# ═══════════════════════════════════════════════════════════════
# ★ PV-006: 密钥轮换（重新加密所有凭据）
# ═══════════════════════════════════════════════════════════════
