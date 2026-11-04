# -*- coding: utf-8 -*-

"""系统数据目录解析器 (SystemDataResolver) — v3.2



自动发现数据库中所有关键业务数据，供 AI 生成测试用例时引用真实数据。



本包通过 Mixin 组合实现，各子模块职责如下：

- _constants   : 列名匹配模式、排除规则、枚举值等常量

- _cache       : MySQL diff_snapshots 缓存读写、导入/导出、指纹校验

- _db_access   : 数据库连接、表/列查询、SQL 安全

- _heuristic   : 启发式用户表 / 角色表 / 业务实体表识别

- _ai_fallback : AI 兜底 schema 分析

- _sampling    : 样本提取与 Prompt 目录文本 / 数据字典格式化



原文件保留 re-export，外部可直接：

    from src.extensions.system_data_resolver import SystemDataResolver, system_data

"""


from typing import Any


from .constants import ResolverConstants

from .cache import CacheMixin

from .db_access import DbAccessMixin

from .heuristic import HeuristicMixin

from .ai_fallback import AiFallbackMixin

from .sampling import SamplingMixin


import logging

_log = logging.getLogger(__name__)


class SystemDataResolver(
    CacheMixin,
    DbAccessMixin,
    HeuristicMixin,
    AiFallbackMixin,
    SamplingMixin,
):
    """系统数据目录解析器 — 自动发现 + 本地缓存。"""

    def __init__(self, project_name: str = ""):

        self._project_name_init = project_name

        # ★ P1: 线程锁，防止并发 _discover 创建多个 DB 连接

        import threading

        self._discover_lock = threading.Lock()

        # ★ P0: 失败缓存 — 目标 DB 不可达时，60s 内不再重试连接
        self._failure_cache = None  # {"catalog": ..., "ts": ...}
        self._FAILURE_CACHE_TTL = 60  # 失败缓存有效期（秒）

    @property
    def _project_name(self) -> str:
        """运行时项目名：优先 config.project_name（模块级单例 import 时项目未定），

        回退构造值。★ 修复（2026-06-23）：原默认 'sample_project' 不在 projects 表，

        导致 diff_snapshots 写入触发 FK 1452（外键约束失败）。"""

        from src.core.config_loader import config

        cur = ""

        try:

            cur = config.project_name or ""

        except Exception:

            cur = ""

        return (cur or self._project_name_init or "").strip()

    @_project_name.setter
    def _project_name(self, val):

        self._project_name_init = val

    # _cache_dir / _cache_path 已删除（_load_cache/_save_cache 已走 MySQL diff_snapshots，

    # 不再有磁盘缓存目录概念）

    # ═══════════════════════════════════════════════════════════════

    # 公共入口

    # ═══════════════════════════════════════════════════════════════

    def resolve(self, force_refresh: bool = False) -> dict[str, Any]:
        """

        获取系统数据目录（优先读缓存，缓存未命中则实时发现）。



        返回:

            {"success": True,

             "catalog_text": "...",   # 可注入 AI Prompt 的文本

             "accounts": [...],       # 测试账号列表

             "classification": {...}} # 表分类结果

        """

        import logging, time

        _log = logging.getLogger(__name__)

        if not force_refresh:

            cached = self._load_cache()

            if cached and self._cache_matches_db(cached):

                _log.info(
                    "[SystemDataResolver] 使用本地缓存 (%d chars, %.1fh 前)"
                    % (
                        len(cached.get("catalog_text", "")),
                        (time.time() - cached.get("cached_at", 0)) / 3600,
                    )
                )

                return cached

            # ★ P0: 失败缓存 — 目标 DB 不可达时，TTL 内不再重试连接（避免每次 AI 调用都超时 5s）
            if self._failure_cache:
                age = time.time() - self._failure_cache["ts"]
                if age < self._FAILURE_CACHE_TTL:
                    _log.info(
                        "[SystemDataResolver] 使用失败缓存 (%.0fs 前失败，跳过重连)",
                        age,
                    )
                    return self._failure_cache["catalog"]

        _log.info("[SystemDataResolver] 开始自动发现系统数据...")

        # ★ P1: 加锁防止并发调用 _discover 创建多个 DB 连接

        with self._discover_lock:

            # ★ 双检失败缓存（加锁后可能已被其他线程更新）
            if not force_refresh and self._failure_cache:
                age = time.time() - self._failure_cache["ts"]
                if age < self._FAILURE_CACHE_TTL:
                    _log.info("[SystemDataResolver] 双检命中失败缓存，跳过")
                    return self._failure_cache["catalog"]
            catalog = self._discover()

        if catalog.get("success"):

            self._save_cache(catalog)

            self._failure_cache = None  # 成功后清除失败缓存

        else:

            # ★ P0: 缓存失败结果，TTL 内不再重试
            self._failure_cache = {"catalog": catalog, "ts": time.time()}
            _log.info(
                "[SystemDataResolver] 发现失败，缓存失败结果 (TTL=%ds): %s",
                self._FAILURE_CACHE_TTL,
                catalog.get("error", "?")[:100],
            )

        return catalog

    # ═══════════════════════════════════════════════════════════════

    # 核心发现流程

    # ═══════════════════════════════════════════════════════════════

    def _discover(self) -> dict[str, Any]:
        """自动发现：连库 -> 读表 -> 分类 -> 提取样本 -> 构建目录。"""

        import logging

        _log = logging.getLogger(__name__)

        from src.core.config_loader import config

        if not config.db_enabled:

            return {
                "success": True,
                "catalog_text": "",
                "accounts": [],
                "classification": {},
                "db_fingerprint": self._db_fingerprint(),
            }

        db_agent = self._connect_db()

        if not db_agent:

            return {
                "success": False,
                "error": "无法连接数据库",
                "catalog_text": "",
                "accounts": [],
                "db_fingerprint": self._db_fingerprint(),
            }

        try:

            db_type = config.db_type.lower()

            all_tables = self._get_all_tables_with_columns(db_agent, db_type)

            if not all_tables:

                return {
                    "success": True,
                    "catalog_text": "（数据库中无业务表或无法读取表结构）",
                    "accounts": [],
                    "classification": {},
                    "db_fingerprint": self._db_fingerprint(),
                }

            cls = self._classify_tables(all_tables)

            if not cls["user_table"] or not cls["role_table"]:

                _log.info(
                    "[SystemDataResolver] 启发式未找到用户/角色表，尝试 AI 分析..."
                )

                ai_cls = self._ai_classify_tables(all_tables)

                if ai_cls:

                    cls.update(ai_cls)

            accounts = (
                self._extract_test_accounts(db_agent, db_type, cls)
                if cls["user_table"]
                else []
            )

            business_samples = self._extract_business_samples(db_agent, db_type, cls)

            catalog_text = self._build_catalog_text(cls, accounts, business_samples)

            schema_hash = (
                self._compute_schema_hash(db_agent, db_type) if db_agent else ""
            )

            _log.info(
                "[SystemDataResolver] 发现完成: %d 账号, %d 业务实体表"
                % (len(accounts), len(business_samples))
            )

            return {
                "success": True,
                "catalog_text": catalog_text,
                "accounts": accounts,
                "biz_samples": business_samples,
                "classification": {
                    "user_table": cls["user_table"],
                    "user_name_col": cls.get("user_name_col"),
                    "user_pwd_col": cls.get("user_pwd_col"),
                    "role_table": cls["role_table"],
                    "role_name_col": cls.get("role_name_col"),
                    "user_role_fk_col": cls.get("user_role_fk_col"),
                    "entity_tables": cls.get(
                        "entity_tables", {}
                    ),  # ★ dict: 附带 code_col/name_col/columns 元数据
                },
                "db_fingerprint": self._db_fingerprint(),
                "schema_hash": schema_hash,
            }

        finally:

            try:

                db_agent.close()

            except Exception as _e:

                _log.debug("close(): %s", _e, exc_info=True)


# ═══════════════════════════════════════════════════════════════════

# 模块级单例

# ═══════════════════════════════════════════════════════════════════

# ★ 方案C 改造：system_data 改为 PerProjectRegistry，按 project 隔离
from src.core.utils.per_project_registry import PerProjectRegistry

system_data = PerProjectRegistry(SystemDataResolver)
