# -*- coding: utf-8 -*-
"""
数据库配置 mixin

职责：
- db_type / host / port / user / password / name / file / auto_mode / enabled
"""


class _ConfigDatabaseMixin:
    """数据库配置。"""

    @property
    def db_type(self) -> str:
        return str(self._get("TARGET_DB_TYPE", "")).strip()

    @property
    def db_host(self) -> str:
        return self._get("TARGET_DB_HOST")

    @property
    def db_port(self) -> int:
        raw = self._get("TARGET_DB_PORT")
        return int(raw) if raw else 0

    @property
    def db_user(self) -> str:
        return self._user_cred("DB_USER")

    @property
    def db_password(self) -> str:
        return self._user_cred("DB_PASSWORD")

    @property
    def db_name(self) -> str:
        return self._get("TARGET_DB_NAME")

    @property
    def db_file(self) -> str:
        return self._get("TARGET_DB_FILE", "")

    @property
    def db_auto_mode(self) -> bool:
        return self._get("TARGET_DB_AUTO_MODE", False)

    @property
    def db_enabled(self) -> bool:
        """数据库是否启用。
        显式 TARGET_DB_ENABLED=True 时启用；
        未显式设置时，自动从数据库字段推断（有 host+user+password+name 或 sqlite+file 即视为启用）。
        """
        if self._get("TARGET_DB_ENABLED", False):
            return True
        # ★ 自动推断：字段填全了即视为启用，避免前端未传 TARGET_DB_ENABLED 导致"未配置"
        db_type = str(self._get("TARGET_DB_TYPE", "") or "").strip().lower()
        if db_type == "sqlite":
            # ★ 2026-09-23 根治（sqlite 目标库不可见）：
            #   根因：sqlite 的"库"就是文件，配置项是 TARGET_DB_FILE，本项目并未要求填
            #   TARGET_DB_NAME；原判定只看 TARGET_DB_NAME → 恒 False → config.db_enabled
            #   为假，连带 SystemDataResolver / pipeline.fetchers.database / preflight /
            #   RAG DB 索引全部静默跳过（靶库连不上）。
            #   通用性：sqlite 目标（任何项目/任何库名），file 或 name 任一存在即视为启用。
            return bool(
                str(self._get("TARGET_DB_FILE", "") or "").strip()
                or str(self._get("TARGET_DB_NAME", "") or "").strip()
            )
        else:
            return all(
                [
                    str(self._get("TARGET_DB_HOST", "") or "").strip(),
                    str(self._user_cred("DB_USER") or "").strip(),
                    str(self._user_cred("DB_PASSWORD") or "").strip(),
                    str(self._get("TARGET_DB_NAME", "") or "").strip(),
                ]
            )
