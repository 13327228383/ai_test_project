# -*- coding: utf-8 -*-
"""
全局配置加载器 (ConfigLoader)
==============================
本模块是系统的"唯一配置入口"，负责按以下优先级加载配置：

  1. MySQL project_configs 表（项目级运行时配置，唯一事实源）
  2. src/core/config.yaml 中的 projects.{name} 节（项目级默认，可选）
  3. src/core/config.yaml 中的全局节（系统级默认）

配置加载后平铺到 ``_config_data`` 字典（UPPERCASE 键名），
所有属性访问器通过 ``self._get("KEY_NAME")`` 统一读取。

【2026-06-18 重构】
原 ~1144 行的单体类已按领域拆分为 config_mixins/ 下的独立 mixin：
- _base.py          — 核心单例、YAML/DB 加载、取值器
- _ai.py            — AI Provider / 模型 / Key / BaseURL
- _confluence.py    — Confluence 配置
- _figma.py         — Figma 配置
- _target.py        — 被测系统配置
- _playwright.py    — 浏览器配置
- _database.py      — 数据库配置
- _smart_session.py — 智能会话 / Flaky / RAG / 视觉模型
- _project_meta.py  — 项目元信息
- _api_docs.py      — 接口文档配置
- _pricing.py       — 模型定价查询

ConfigLoader 继承全部 mixin，对外接口 100% 保持兼容。
"""

import logging

_log = logging.getLogger(__name__)

# ★ 易用性收尾：config_loader 初始化最早处自动加载项目根 .env。
#   裸跑 pytest（不 export STORAGE_MYSQL_*）也能读到配置，不再挂死 30min+。
#   - fail-open：.env 缺失/解析失败不影响启动
#   - setdefault：显式 export 优先于 .env
#   - 幂等：进程内只读一次（base.py 亦会触发，重复调用开销为 0）
from src.core.config_loader._dotenv import load_dotenv_fail_open

load_dotenv_fail_open()

import logging as _logging
from src.core.utils.redact import make_sensitive_filter

# ★ 安全加固(2026-09-03)：config_loader 是最早被 import 的初始化点，在此把
#   全局敏感脱敏 filter 挂到 root logger——所有日志(含异常回显)中的 token/密码
#   形态将就地转 ***，引擎任何地方无需各自处理。幂等：模块级标志只挂一次。
_LOGGING_GUARDED = False


def _install_global_redaction() -> None:
    global _LOGGING_GUARDED
    if _LOGGING_GUARDED:
        return
    _logging.getLogger().addFilter(make_sensitive_filter())
    _LOGGING_GUARDED = True


_install_global_redaction()

from src.core.config_loader.base import _ConfigBaseMixin
from src.core.config_loader.ai import _ConfigAiMixin
from src.core.config_loader.confluence import _ConfigConfluenceMixin
from src.core.config_loader.figma import _ConfigFigmaMixin
from src.core.config_loader.target import _ConfigTargetMixin
from src.core.config_loader.playwright import _ConfigPlaywrightMixin
from src.core.config_loader.database import _ConfigDatabaseMixin
from src.core.config_loader.smart_session import _ConfigSmartSessionMixin
from src.core.config_loader.project_meta import _ConfigProjectMetaMixin
from src.core.config_loader.api_docs import _ConfigApiDocsMixin
from src.core.config_loader.pricing import _ConfigPricingMixin


class ConfigLoader(
    _ConfigBaseMixin,
    _ConfigAiMixin,
    _ConfigConfluenceMixin,
    _ConfigFigmaMixin,
    _ConfigTargetMixin,
    _ConfigPlaywrightMixin,
    _ConfigDatabaseMixin,
    _ConfigSmartSessionMixin,
    _ConfigProjectMetaMixin,
    _ConfigApiDocsMixin,
    _ConfigPricingMixin,
):
    """
    全局配置单例加载器（门面类）。

    使用方式：
        from src.core.config_loader import config
        api_key = config.ai_api_key  # 直接通过属性访问

    无需手动实例化，模块底部的 ``config`` 实例已自动初始化。
    """

    pass


# =============================================================================
# v5：模型注册与发现（已拆分到 src.ai.model_registry）
#   为保持向后兼容，以下符号仍可从本模块导入。
# =============================================================================

from src.ai.model_registry import (
    is_vision_model,
    is_embedding_model,
    fetch_provider_models,
)


# =============================================================================
# 模块级：当前用户 & 单例实例（线程安全，支持多用户并发）
# v3.1：已拆分到 config_session.py，以下为向后兼容 re-export。
# =============================================================================

from src.core.config_session import (
    _thread_local,
    _get_thread_local,
    set_current_user,
    get_current_user,
    set_session_key,
    clear_session_key,
    get_session_key,
    get_session_provider,
    _get_default_tl,
    _session_key_backref,
    _session_provider_backref,
    _detect_provider_from_key,
)

# ★ A4 改造：config 现在是 ConfigRegistry 实例，按 project 隔离。
#   原实现：全局单例，reload(project) 会覆盖 _config_data，并发时项目 A 切换会影响项目 B。
#   新实现：dict[project] → ConfigLoader，每个项目独立 _config_data。
#   兼容策略：保留所有属性访问方法，自动从 thread-local 获取当前 project，委托给对应实例。
import threading as _threading


class ConfigRegistry:
    """★ A4 改造：按 project 隔离的 ConfigLoader 注册表。

    - for_project(project): 返回指定项目的 ConfigLoader（不存在则创建并 reload）。
    - 兼容策略：所有属性访问通过 __getattr__ 委托给当前项目的 ConfigLoader。
    - 当前 project 来源：优先 thread-local（_thread_local._current_project），
      回退到线程本地的 _default_project（向后兼容）。
    - ★ A5 改造：_default_project 改为 thread-local，避免多线程并发 reload 时串扰。
    """

    def __init__(self):
        self._loaders: dict[str, ConfigLoader] = {}
        self._lock = _threading.Lock()
        self._tl = _threading.local()  # ★ A5: thread-local 存储 _default_project
        self._tl.default_project = ""
        # ★ Fix C(2026-09-27): 进程级"最近激活项目"。thread-local 只在**激活该项目
        #   的那个线程**可见；pipeline 内的 `asyncio.to_thread(...)` 工作线程没有
        #   任何线程本地上下文 → `_current()` 回退到 `ConfigLoader(project_name="")`
        #   （无项目配置）→ `TARGET_DB_TYPE` 读成 "" → DatabaseAgent 落到 mysql 默认
        #   分支、库名为空 → 查询期 (1046, 'No database selected') → DB 轨假 FAIL，
        #   进而拖垮整用例 AND（实测 TC-REQ-002-P01）。
        #   通用性: 与业务/靶子无关；仅在工作线程**无任何上下文**时兜底，
        #   线程本地上下文始终优先，故多项目并发不受影响。
        self._global_project = ""
        # 保留一个默认实例，用于无 project 上下文的兼容调用
        self._default = ConfigLoader(project_name="")

    @property
    def _default_project(self) -> str:
        """★ A5: 线程本地的 _default_project。"""
        if not hasattr(self._tl, "default_project"):
            self._tl.default_project = ""
        return self._tl.default_project

    @_default_project.setter
    def _default_project(self, value: str):
        """★ A5: 线程本地的 _default_project setter。"""
        self._tl.default_project = value or ""

    def for_project(self, project: str) -> ConfigLoader:
        """获取指定项目的 ConfigLoader（不存在则创建并 reload）。

        ★ 性能修复：原实现 `with self._lock` 全局锁导致所有并发请求串行化
          （实测 50ms → 2700ms，50倍劣化）。改用 double-checked locking：
          - 快路径：无锁 dict.get()（CPython GIL 保证 dict 读原子性），命中直接返回
          - 慢路径：仅首次加载或缓存失效时加锁创建，之后都走快路径
        """
        if not project:
            return self._default
        # 快路径：无锁读取（dict.get 在 CPython 下受 GIL 保护，原子操作）
        loader = self._loaders.get(project)
        if loader is not None:
            return loader
        # 慢路径：double-checked locking，避免重复创建
        with self._lock:
            loader = self._loaders.get(project)
            if loader is None:
                loader = ConfigLoader(project_name=project)
                self._loaders[project] = loader
            return loader

    def set_current_project(self, project: str):
        """设置当前 thread-local 的 project（供 _current() 使用）。"""
        self._default_project = project or ""
        if project:
            # ★ Fix C: 记录进程级"最近激活项目"，供无上下文的工作线程兜底。
            self._global_project = project

    def _current(self) -> ConfigLoader:
        """获取当前项目的 ConfigLoader。

        优先从 thread-local 获取 project，回退到 _default_project，
        再回退到进程级"最近激活项目"（Fix C，覆盖 asyncio.to_thread 工作线程）。
        """
        project = self._default_project
        try:
            # 尝试从 backend.state 的 thread-local 获取
            from src.backend.state import _current_project

            tl_project = _current_project()
            if tl_project:
                project = tl_project
        except Exception as _e:
            _log.debug(
                "[config_loader] _current_project 读取异常: %s", _e, exc_info=True
            )
        if not project:
            # ★ Fix C: 工作线程无任何线程本地上下文 → 用进程级最近激活项目，
            #   而非无项目配置的空实例（否则 TARGET_DB_* 全部读空）。
            project = self._global_project
        return self.for_project(project)

    def reload(self, project_name: str = None, force: bool = False) -> None:
        """★ 兼容方法：reload 指定项目的配置。"""
        target = project_name or self._default_project
        if target:
            self._default_project = target
            # ★ Fix C: 同步进程级"最近激活项目"。
            self._global_project = target
            loader = self.for_project(target)
            loader.reload(target, force=force)
        else:
            self._default.reload(project_name, force=force)

    def __getattr__(self, name: str):
        """★ 对所有属性访问，委托给当前项目的 ConfigLoader。
        这确保 30+ 个引用点的所有属性访问都能正确路由到对应项目的实例。"""
        return getattr(self._current(), name)


config = ConfigRegistry()
