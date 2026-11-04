from __future__ import annotations
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from src.extensions.db.db_agent.connection import DatabaseAgent
from src.core.guarded import guard

# DatabaseAgentConnectionMixin — 连接与配置相关

import logging
import re

_log = logging.getLogger(__name__)


class DatabaseAgentConnectionMixin:
    """连接与配置相关的方法。"""

    # ---- 测试数据标记 ----
    TEST_PREFIX = "TEST_"
    _TAG_UID_LENGTH = 8
    # 测试用户密码由 _scenario_create_user 动态生成，不硬编码。
    # 如需复用生产 admin 密码哈希，请在项目 config.py 中设置 TEST_USER_PASSWORD_HASH。
    ROLE_SUPER_ADMIN = 1
    ROLE_VISITOR = 2

    # ---- 初始化与连接 ----

    def __init__(
        self,
        host: str | None = None,
        port: int | None = None,
        user: str | None = None,
        password: str | None = None,
        database: str | None = None,
        db_type: str | None = None,
        auto_commit: bool = True,
    ) -> None:
        """初始化数据库代理。未传入的连接参数从 config_loader 读取。"""
        self._host = host
        self._port = port
        self._user = user
        self._password = password
        self._database = database
        self._db_type = db_type
        self._auto_commit = auto_commit

        # 从全局配置补充缺失的连接参数
        self._resolve_config()

        self._conn = None
        self._cursor = None

    def _resolve_config(self) -> None:
        """从 config_loader 读取未显式传入的连接参数。config_loader 不可用时保留 None，connect() 时报错。"""
        with guard("_resolve_config: from src.core.config_loader import c", log=_log, level=logging.WARNING):
            from src.core.config_loader import config

            if self._host is None:
                self._host = config.db_host
            if self._port is None:
                self._port = config.db_port
            if self._user is None:
                self._user = config.db_user
            if self._password is None:
                self._password = config.db_password
            if self._database is None:
                self._database = config.db_name
            if self._db_type is None:
                self._db_type = config.db_type

            # ★ 2026-09-23 根治（sqlite 目标库连不上）：
            #   根因：sqlite 的"数据库"就是文件路径，配置项为 TARGET_DB_FILE；
            #   而 _database 一律取 config.db_name(TARGET_DB_NAME)。sqlite 项目通常只配
            #   TARGET_DB_FILE（不填 NAME）→ sqlite3.connect(None) 抛
            #   "expected str, bytes or os.PathLike object, not NoneType"。
            #   SystemDataResolver / db 轨 / 数据发现全部静默失败（"无法连接数据库"）。
            #   通用性：任何 sqlite 目标库；mysql/pg/oracle 行为不变（仍用库名）。
            if (
                str(self._db_type or "").strip().lower() == "sqlite"
                and not str(self._database or "").strip()
            ):
                self._database = str(config.db_file or "").strip() or None
        # ★ Fix F(2026-09-27)：记录本次解析所依据的配置签名，供 connect() 检测
        #   "项目切换/配置更新后配置是否已过期"。见 connect() 中的强制重解析。
        self._cfg_signature = self._current_cfg_signature()

    @staticmethod
    def _current_cfg_signature() -> tuple:
        """当前配置中与 DB 连接相关的签名（用于检测配置漂移）。"""
        try:
            from src.core.config_loader import config

            return (
                str(config.db_type or ""),
                str(config.db_name or ""),
                str(config.db_file or ""),
                str(getattr(config, "_project_name", "") or ""),
            )
        except Exception:
            return ("", "", "", "")

    def refresh_config_if_stale(self) -> None:
        """★ Fix F 根因修复(2026-09-27)：项目切换/配置更新后强制重解析连接参数。

        根因：`DatabaseAgent` 由 `_ThreadSafeDbAgent` **按线程缓存实例**，
        `_resolve_config()` 仅在构造时执行一次，且只填充 None 值
        （`if self._host is None: ...`）→ 项目切换后仍持有旧项目的 DB 配置，
        继续连旧库（如"引擎 MySQL 未配库名"）→ `(1046, 'No database selected')`
        → DB 断言必败并拖垮 AND 轨（假 FAIL）。

        通用性(§1)：以"配置签名"为判据，任何项目切换/DB 配置更新都能触发重解析，
          与具体业务/靶子无关；签名未变时零开销、零行为变更。
        """
        try:
            if getattr(self, "_cfg_signature", None) == self._current_cfg_signature():
                return
        except Exception:
            return
        self._host = self._port = self._user = self._password = self._database = (
            self._db_type
        ) = None
        self._resolve_config()
        _log.info(
            "[DatabaseAgent] 检测到 DB 配置漂移，已重解析: type=%s db/file=%s",
            self._db_type,
            self._database,
        )

    # connect() 由 DbAdapterMixin 提供

    def close(self) -> None:
        """关闭数据库连接。"""
        with guard("close: if self._cursor: self._cursor.close(", log=_log, level=logging.WARNING):
            if self._cursor:
                self._cursor.close()
        with guard("close: if self._conn: self._conn.close()", log=_log, level=logging.WARNING):
            if self._conn:
                self._conn.close()
        self._cursor = None
        self._conn = None
        _log.info("[DatabaseAgent] 连接已关闭")

    # ---- SQL 执行 & 查询 ----

    # execute(), query(), _supports_native_params(), _format_sql(), _get_last_insert_id()
    # 由 DbAdapterMixin 提供

    def _ensure_connected(self) -> None:
        if self._conn is None:
            self.connect()

    @staticmethod
    def _sanitize_ident(name: str) -> str:
        """剔除标识符中的非法字符，仅保留字母数字下划线。"""
        return re.sub(r"[^A-Za-z0-9_]", "", str(name))

    # _limit_clause(), _quote_ident() 由 DbAdapterMixin 提供

    # ---- 辅助方法 ----

    def __enter__(self) -> "DatabaseAgent":
        """上下文管理器入口。"""
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """上下文管理器出口。"""
        self.close()

    def reload_config(self) -> None:
        """项目切换后重新读取配置并重置连接"""
        self.close()
        self._host = self._port = self._user = self._password = self._database = (
            self._db_type
        ) = None
        self._resolve_config()
