# -*- coding: utf-8 -*-
"""
ConfigLoader 核心基础 mixin

职责：
- 单例模式（线程安全）
- 系统级 YAML 加载与平铺
- 项目级配置加载（MySQL project_configs 表 + YAML projects 节）
- 通用取值器 _get / 运行时覆盖 _set_runtime
- 用户凭证读取 _user_cred
- reload / switch_project / get_all / __getattr__
"""

from __future__ import annotations
from src.core.guarded import guard

import os
import time
import threading
import re
import json
import logging
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)

# ★ 易用性收尾：导入即自动加载项目根 .env（fail-open；setdefault 不覆盖显式 export）。
#   直接 import base 的调用方同样受益；进程内幂等（只读一次文件）。
from src.core.config_loader._dotenv import load_dotenv_fail_open

load_dotenv_fail_open()


class _ConfigBaseMixin:
    """ConfigLoader 核心基础能力（单例、加载、取值）。"""

    _instance: _ConfigBaseMixin | None = None
    _instance_lock = threading.Lock()
    _config_data: dict = {}
    _reload_lock = (
        threading.Lock()
    )  # ★ 保护 reload() 防止多项目并发 clear-and-rebuild 竞态

    _YAML_KEY_MAP = {
        "VERIFICATION_MODEL": "AI_VERIFICATION_MODEL",
        "DEEPSEEK_BASE_URL": "AI_DEEPSEEK_BASE_URL",
        "DEEPSEEK_TIMEOUT": "AI_TIMEOUT",
        "DEEPSEEK_MODEL": "AI_MODEL",
        "OPENAI_BASE_URL": "AI_OPENAI_BASE_URL",
        "ANTHROPIC_BASE_URL": "AI_ANTHROPIC_BASE_URL",
        "NVIDIA_API_KEY": "AI_NVIDIA_API_KEY",
        "NVIDIA_BASE_URL": "AI_NVIDIA_BASE_URL",
        "NVIDIA_MODEL": "AI_NVIDIA_MODEL",
        "OLLAMA_API_BASE": "OLLAMA_API_BASE",
        "OLLAMA_MODEL": "OLLAMA_MODEL",
        "OLLAMA_ENABLED": "OLLAMA_ENABLED",
        "REQUIREMENTS_EXCEL_PATH": "EXCEL_REQUIREMENTS_PATH",
        "TEST_CASES_EXCEL_PATH": "EXCEL_TEST_CASES_PATH",
        "STORAGE_ENGINE": "STORAGE_ENGINE",
        "STORAGE_MYSQL_HOST": "STORAGE_MYSQL_HOST",
        "STORAGE_MYSQL_PORT": "STORAGE_MYSQL_PORT",
        "STORAGE_MYSQL_USER": "STORAGE_MYSQL_USER",
        "STORAGE_MYSQL_PASSWORD": "STORAGE_MYSQL_PASSWORD",
        "STORAGE_MYSQL_DATABASE": "STORAGE_MYSQL_DATABASE",
        "STORAGE_MYSQL_CHARSET": "STORAGE_MYSQL_CHARSET",
    }

    _ENV_KEY_MAP = {
        "STORAGE_MYSQL_PASSWORD": "MYSQL_PASSWORD",
        "STORAGE_MYSQL_HOST": "MYSQL_HOST",
        "STORAGE_MYSQL_USER": "MYSQL_USER",
        "STORAGE_MYSQL_PORT": "MYSQL_PORT",
        "STORAGE_MYSQL_DATABASE": "MYSQL_DATABASE",
        "FIGMA_KEY": "FIGMA_API_KEY",
        "CONFLUENCE_PASSWORD": "CONFLUENCE_PASSWORD",
        "API_DOCS_PASSWORD": "YAPI_PASSWORD",
        "TARGET_PASSWORD": "TARGET_PASSWORD",
        "DB_PASSWORD": "TARGET_DB_PASSWORD",
        "NVIDIA_API_KEY": "NVIDIA_API_KEY",
    }

    def __new__(cls, project_name: str = "sample_project") -> "_ConfigBaseMixin":
        # ★ A4 改造：移除强制单例，允许 ConfigRegistry 创建多实例
        #   原实现：__new__ 强制全局单例，无法支持多项目并发
        #   新实现：每次实例化创建独立对象，由 ConfigRegistry 管理多实例
        obj = super().__new__(cls)
        obj._initialized = False
        return obj

    def __init__(self, project_name: str = "") -> None:
        if getattr(self, "_initialized", False):
            return

        self._system_yaml_path = Path(__file__).resolve().parent.parent / "config.yaml"
        self._config_json_path = Path(__file__).resolve().parent.parent / "config.json"
        self._yaml_raw: dict = {}
        self._config_data: dict = {}

        base_root = Path(__file__).resolve().parent.parent.parent.parent

        self._load_system_yaml()
        self._load_config_json()

        if project_name:
            self._project_name = project_name
            # ★ 全走 MySQL：不再拼 data/{project}/，_project_root 指向 base_root（过渡保留）
            self._project_root = base_root
            self._load_project_config(project_name)
        else:
            self._project_name = ""
            self._project_root = base_root

        self._initialized = True

    def switch_project(self, project_name: str) -> None:
        if not project_name:
            return
        base_root = Path(__file__).resolve().parent.parent.parent.parent
        self._project_name = project_name
        # ★ 全走 MySQL：不再拼 data/{project}/
        self._project_root = base_root
        self._config_data = {}
        self._load_system_yaml()
        self._load_config_json()
        self._load_project_config(project_name)
        self._last_loaded_project = project_name
        # ★ B-007: 清除 schema 缓存，避免切换项目后旧 schema 串台
        from src.core.db_schema import reset_schema_done

        reset_schema_done()
        _log.info(f"[ConfigLoader] 已切换到项目: '{project_name}'")

    def _load_system_yaml(self) -> None:
        yaml_path = self._system_yaml_path
        if not yaml_path.is_file():
            return
        with guard("_load_system_yaml: import yaml as _yaml", log=_log, level=logging.INFO):
            import yaml as _yaml

            with open(str(yaml_path), "r", encoding="utf-8") as f:
                self._yaml_raw = _yaml.safe_load(f) or {}
            for key, value in self._yaml_raw.items():
                if key == "projects":
                    continue
                self._flatten_yaml_to_uppercase(key, value, "")

    # ── config.json 文件覆盖机制 ─────────────────────────────────────
    #  优先级: MySQL project_configs > config.json > config.yaml
    #  用途: 用户通过 Web UI (config_ui.py) 修改引擎参数，无需改代码
    #  配置项: target_url, target_username, target_password,
    #          admin_app_name, target_login_path,
    #          login_username_selector, login_password_selector,
    #          login_submit_selector
    # ─────────────────────────────────────────────────────────────────
    def _load_config_json(self) -> None:
        """从 config.json 读取用户配置，覆盖 YAML 默认值。

        config.json 位于 src/core/config.json，与 config.yaml 同目录。
        仅当 config.json 中存在非空值时覆盖 _config_data 中对应键。
        """
        json_path = self._config_json_path
        if not json_path.is_file():
            return
        with guard("_load_config_json: with open(str(json_path), 'r', encod", log=_log, level=logging.INFO):
            with open(str(json_path), "r", encoding="utf-8") as f:
                raw = json.load(f) or {}
            if not isinstance(raw, dict) or not raw:
                return
            # 将 config.json 中的键转为 UPPERCASE 后覆盖
            _overridden = 0
            for k, v in raw.items():
                if v is not None and v != "":
                    self._config_data[k.upper()] = v
                    _overridden += 1
            if _overridden:
                _log.info(
                    "[ConfigLoader] config.json 已加载并覆盖 %d 项配置", _overridden
                )

    def _flatten_yaml_to_uppercase(
        self, section: str, value: Any, prefix: str = ""
    ) -> None:
        key_prefix = (prefix + "_" + section).upper() if prefix else section.upper()
        if isinstance(value, dict):
            for k, v in value.items():
                self._flatten_yaml_to_uppercase(k, v, key_prefix)
        elif isinstance(value, (list, tuple)):
            self._config_data[key_prefix] = value
        else:
            self._config_data[key_prefix] = value

    def _load_project_config(self, project_name: str) -> None:
        loaded = False
        base_root = Path(__file__).resolve().parent.parent.parent.parent

        if self._yaml_raw and isinstance(self._yaml_raw.get("projects"), dict):
            proj_cfg = self._yaml_raw["projects"].get(project_name)
            if proj_cfg and isinstance(proj_cfg, dict):
                self._flatten_yaml_to_uppercase("", proj_cfg, "")
                loaded = True
                cfg_detail = ", ".join(
                    f"{k}={str(v)[:40]}"
                    for k, v in proj_cfg.items()
                    if isinstance(v, (str, bool, int, float)) and k not in ("password",)
                )
                _log.info(
                    f"[ConfigLoader] 系统 YAML 项目配置已加载: '{project_name}' ({cfg_detail})"
                )

        with guard("_load_project_config: import json as _json", log=_log, level=logging.INFO):
            import json as _json
            from src.users.manager import _conn

            conn = _conn()
            cur = None
            try:
                cur = conn.cursor()
                cur.execute(
                    "SELECT config_json FROM project_configs WHERE project=%s",
                    (project_name,),
                )
                row = cur.fetchone()
            finally:
                if cur:
                    try:
                        cur.close()
                    except Exception as _e:
                        _log.debug(
                            "execute + fetchone + close(): %s", _e, exc_info=True
                        )
                try:
                    conn.close()
                except Exception as _e:
                    _log.debug("execute + fetchone + close(): %s", _e, exc_info=True)
            if row and row.get("config_json"):
                db_config = (
                    _json.loads(row["config_json"])
                    if isinstance(row["config_json"], str)
                    else row["config_json"]
                )
                if isinstance(db_config, dict) and db_config:
                    # ★ 治根：配置健康检查 — 检测可疑占位符值
                    _SUSPICIOUS_PLACEHOLDERS = {
                        "TARGET_URL": [
                            "https://example.com",
                            "http://example.com",
                            "http://localhost",
                        ],
                        "TARGET_USERNAME": ["testuser", "test", "admin"],
                        "TARGET_PASSWORD": [
                            "testpass",
                            "test",
                            "password",
                            "admin",
                            "123456",
                        ],
                    }
                    for _sus_key, _sus_vals in _SUSPICIOUS_PLACEHOLDERS.items():
                        _actual = db_config.get(_sus_key, "")
                        if _actual and any(
                            _sus in str(_actual).lower() for _sus in _sus_vals
                        ):
                            _log.warning(
                                "[ConfigLoader] ⚠️ 项目 '%s' 的 %s 值疑似占位符 (%s)，"
                                "可能导致目标系统登录失败。请通过 UI 配置页面更新。",
                                project_name,
                                _sus_key,
                                str(_actual)[:40],
                            )
                    self._config_data.update(db_config)
                    loaded = True
                    _log.info(
                        f"[ConfigLoader] MySQL project_configs 已加载并覆盖: '{project_name}' ({len(db_config)} 项)"
                    )

        if not loaded and project_name:
            _log.info(
                f"[ConfigLoader] 项目 '{project_name}' 的 MySQL 配置为空，使用系统默认值"
            )

    @staticmethod
    def _coerce_env_val(raw: str, default: Any) -> Any:
        """★ v5.2(2026-08-09): 根据 default 类型自动转换环境变量字符串值。
        
        根因：环境变量始终是字符串，但 Python bool("false") == True，
        导致 REQUIREMENT_REVIEW_GATE=false 实际仍启用闸门。
        通用性：跨所有配置项，按 default 类型自动转换。
        """
        if raw is None:
            return raw
        if isinstance(default, bool):
            return raw.lower() in ("true", "1", "yes", "on")
        if isinstance(default, int):
            try:
                return int(raw)
            except (ValueError, TypeError):
                return raw
        if isinstance(default, float):
            try:
                return float(raw)
            except (ValueError, TypeError):
                return raw
        return raw

    def _get(self, key: str, default: Any = None) -> Any:
        if key in self._ENV_KEY_MAP:
            env_val = os.environ.get(self._ENV_KEY_MAP[key])
            if env_val not in (None, ""):
                return self._coerce_env_val(env_val, default)
        env_direct = os.environ.get(key)
        if env_direct not in (None, ""):
            return self._coerce_env_val(env_direct, default)
        if hasattr(self, "_override_lock"):
            with self._override_lock:
                if key in self._runtime_overrides:
                    return self._runtime_overrides[key]
        elif hasattr(self, "_runtime_overrides"):
            if key in self._runtime_overrides:
                return self._runtime_overrides[key]
        val = self._config_data.get(key)
        if val is not None:
            return val
        alt = self._YAML_KEY_MAP.get(key)
        if alt and alt != key:
            val = self._config_data.get(alt)
            if val is not None:
                return val
        return default

    def _set_runtime(self, key: str, value: Any):
        if not hasattr(self, "_override_lock"):
            import threading as _thr

            self._override_lock = _thr.Lock()
        if not hasattr(self, "_runtime_overrides"):
            self._runtime_overrides = {}
        with self._override_lock:
            self._runtime_overrides[key] = value

    def _user_cred(self, key: str) -> str:
        """读取用户凭证，三级回退：当前用户 → 项目 owner → 空。

        项目级凭证（DB_USER、FIGMA_KEY、TARGET_USERNAME 等）由项目管理员配置，
        项目成员执行流水线时应自动继承项目 owner 的凭证。
        """
        from src.core.config_session import get_current_user
        from src.users.manager import get_user_credential

        uid = get_current_user()
        if not uid:
            uid = "admin"
        if uid:
            try:
                val = get_user_credential(uid, key, project=self._project_name)
                if val:
                    return val
            except Exception as e:
                logging.getLogger(__name__).warning(
                    f"_user_cred({key}) 当前用户失败: {e}"
                )
        # ★ 回退到项目 owner 的凭证
        if self._project_name:
            try:
                from src.users.project_auth import get_project_owner

                owner = get_project_owner(self._project_name)
                if owner and owner != uid:
                    val = get_user_credential(owner, key, project=self._project_name)
                    if val:
                        return val
            except Exception as e:
                logging.getLogger(__name__).warning(
                    f"_user_cred({key}) 项目 owner 回退失败: {e}"
                )
        return ""

    def get_all(self) -> dict:
        return dict(self._config_data)

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)

        if hasattr(self, "_runtime_overrides") and self._runtime_overrides:
            if name.upper() in self._runtime_overrides:
                return self._runtime_overrides[name.upper()]

        db_cfg = object.__getattribute__(self, "_config_data")
        if name in db_cfg:
            return db_cfg[name]
        if name.upper() in db_cfg:
            return db_cfg[name.upper()]

        yaml_raw = object.__getattribute__(self, "_yaml_raw")
        if yaml_raw and name in yaml_raw:
            return yaml_raw[name]
        lower_map = {k.lower(): v for k, v in yaml_raw.items()} if yaml_raw else {}
        if name.lower() in lower_map:
            return lower_map[name.lower()]

        val = self._get(name.upper())
        if val is not None:
            return val

        raise AttributeError(name)

    def reload(self, project_name: str = None, force: bool = False) -> None:
        target = project_name or self._project_name
        if not force and (
            getattr(self, "_last_loaded_project", None) == target and self._config_data
        ):
            return
        # ★ 修复 BUG-15：全局锁保护 clear-and-rebuild 防止多项目并发覆盖
        with self._reload_lock:
            # 双重检查：获取锁后再次检查是否已被其他线程加载
            if (
                not force
                and getattr(self, "_last_loaded_project", None) == target
                and self._config_data
            ):
                return
            self._config_data.clear()
            self._yaml_raw = {}
            if hasattr(self, "_override_lock"):
                with self._override_lock:
                    self._runtime_overrides.clear()
            elif hasattr(self, "_runtime_overrides"):
                self._runtime_overrides.clear()
            self._load_system_yaml()
            self._load_config_json()
            # ★ 全走 MySQL：不再拼 data/{project}/
            self._project_root = Path(__file__).resolve().parent.parent.parent.parent
            self._project_name = target
            self._load_project_config(target)
            self._last_loaded_project = target
        _log.info("[ConfigLoader] 已重新加载项目 '%s' 的配置", target)
