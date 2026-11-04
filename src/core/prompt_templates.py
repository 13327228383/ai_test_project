# -*- coding: utf-8 -*-
"""
★ Prompt 模板集中管理（2026-07-14 重构：DB 唯一事实源）

设计：
1. MySQL prompt_templates 表是唯一事实源，前端可编辑
2. 种子模板（is_seed=1）由 prompt_seeds.py 定义，启动时 seed_if_empty() 写入 DB
3. 环境变量 PROMPT_TEMPLATE_<NAME> 可临时覆盖（调试用）
4. render() 支持 {{占位符}} 语法（mustache 风格，避免与 JSON 花括号冲突）
5. 项目级覆盖（project_prompt_overrides）已移除，统一为系统级

不再支持：
- project_prompt_overrides 表（已删除）
- 代码内置 _builtins 兜底（register 仅用于兼容，不再作为事实源）
- Python .format() 风格占位符（改为 {{name}} mustache 风格）
"""
from __future__ import annotations

import os
import re
import threading
import logging
from typing import Any

_log = logging.getLogger(__name__)

# {{占位符}} 正则（mustache 风格，匹配 {{name}} 但不匹配 {{{name}}} 或 }}}
_PLACEHOLDER_RE = re.compile(r"\{\{(\w+)\}\}")


class PromptTemplateManager:
    """集中式 Prompt 模板管理器（DB 唯一事实源）。"""

    def __init__(self):
        # _builtins 仅用于 register() 兼容，不作为事实源
        self._builtins: dict[str, str] = {}
        # DB 缓存：name → template_text
        self._db_cache: dict[str, str] = {}
        self._loaded_db = False
        self._seeded = False
        self._lock = threading.RLock()

    def register(self, name: str, template: str) -> None:
        """注册内置模板（仅兼容用，DB 才是事实源）。"""
        if not name:
            return
        with self._lock:
            self._builtins[name] = template or ""

    def render(self, name: str, default: str = "", **kwargs) -> str:
        """渲染模板。优先级：环境变量 > DB > 兼容内置 > default。

        {{占位符}} 由 kwargs 注入；缺失的占位符保留原样（不报错）。
        """
        template = self.get(name, default)
        if not kwargs:
            return template

        # mustache 风格占位符替换
        def _replace(m: re.Match) -> str:
            key = m.group(1)
            val = kwargs.get(key)
            return str(val) if val is not None else m.group(0)

        return _PLACEHOLDER_RE.sub(_replace, template)

    def get(self, name: str, default: str = "") -> str:
        """获取模板文本。优先级：环境变量 > DB > 兼容内置 > default。"""
        if not name:
            return default
        env_key = "PROMPT_TEMPLATE_" + name.upper()
        env_val = os.environ.get(env_key)
        if env_val:
            return env_val
        with self._lock:
            self._load_db_templates_once()
            return self._db_cache.get(name) or self._builtins.get(name) or default

    def reset_cache(self) -> None:
        """重置 DB 模板缓存，下次 get() 重新加载。"""
        with self._lock:
            self._db_cache.clear()
            self._loaded_db = False

    def seed_if_empty(self) -> int:
        """DB 为空时写入种子模板。返回写入条数。"""
        with self._lock:
            if self._seeded:
                return 0
            self._seeded = True
            try:
                from src.core.prompt_seeds import PROMPT_SEEDS
                from src.core.db_manager import get_connection

                conn = None
                try:
                    conn = get_connection()
                    # 检查是否已有数据
                    count = conn.execute(
                        "SELECT COUNT(*) AS cnt FROM prompt_templates"
                    ).fetchone()
                    if count and int(count.get("cnt", 0)) > 0:
                        return 0
                    # 写入种子
                    for seed in PROMPT_SEEDS:
                        conn.execute(
                            "INSERT INTO prompt_templates(name, template_text, category, description, is_seed, enabled) "
                            "VALUES(%s,%s,%s,%s,1,1) "
                            "ON DUPLICATE KEY UPDATE template_text=VALUES(template_text), "
                            "category=VALUES(category), description=VALUES(description), is_seed=1",
                            (
                                seed["name"],
                                seed["template_text"],
                                seed.get("category", "其他"),
                                seed.get("description", ""),
                            ),
                        )
                    conn.commit()
                    _log.info(
                        "[prompt_templates] 种子模板已写入 DB: %d 条", len(PROMPT_SEEDS)
                    )
                    # 写入后清缓存，下次 get() 重新加载
                    self._db_cache.clear()
                    self._loaded_db = False
                    return len(PROMPT_SEEDS)
                finally:
                    if conn is not None:
                        try:
                            conn.close()
                        except Exception as _e:
                            _log.debug(
                                "[prompt_templates] seed conn.close 异常: %s",
                                _e,
                                exc_info=True,
                            )
            except Exception as e:
                _log.debug(
                    "[prompt_templates] seed_if_empty 失败: %s", e, exc_info=True
                )
                return 0

    def _load_db_templates_once(self) -> None:
        """从 MySQL 加载模板（DB 唯一事实源）。失败自动降级。"""
        if self._loaded_db:
            return
        self._loaded_db = True
        conn = None
        try:
            from src.core.db_manager import get_connection

            conn = get_connection()
            rows = conn.execute(
                "SELECT name, template_text FROM prompt_templates WHERE enabled=1"
            ).fetchall()
            for row in rows or []:
                self._db_cache[row["name"]] = row["template_text"]
        except Exception as _e:
            _log.debug("[prompt_templates] DB 加载失败: %s", _e, exc_info=True)
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception as _e:
                    _log.debug(
                        "[prompt_templates] load conn.close 异常: %s", _e, exc_info=True
                    )


prompt_templates = PromptTemplateManager()
