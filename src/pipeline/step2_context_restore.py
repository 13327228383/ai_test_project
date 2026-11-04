# -*- coding: utf-8 -*-
"""Step 2 上下文恢复函数。
从 step2.py 拆分出。★ 2026-06-30: _nav_context 已移除（nav_map 整条链路删除）。"""

import logging
from src.core.guarded import guard

_log = logging.getLogger(__name__)


def _restore_step1_contexts(scheduler) -> None:
    """★ P0: 断点续跑时从 MySQL 恢复 Step1 的 3 个内存上下文。

    当 --from-step 2 或 --from-step 3 跳过 Step1 时，PipelineScheduler 的
    _ui_context / _test_accounts_context / _api_docs_context
    全部为空字符串。此函数在 Step2/3 开始时检测并从 MySQL 恢复。

    恢复策略：
      - _api_docs_context → source_documents 表 (doc_type='yapi')
      - _ui_context      → source_documents 表 (doc_type='figma') / figma_pages 表
      - _test_accounts_context → 重新连接数据库查询 (session_ctx.get_test_accounts)
    """
    _any_restored = False
    try:
        from src.users.manager import get_active_version_label

        _active_version_label = get_active_version_label(scheduler._project_name)
    except Exception as _e:
        _log.debug(
            "[step2_context_restore] _restore_step1_contexts 异常: %s",
            _e,
            exc_info=True,
        )
        _active_version_label = ""

    # ── 1. 恢复 API 文档上下文 (_api_docs_context) ──
    if not getattr(scheduler, "_api_docs_context", None):
        with guard("_restore_step1_contexts: from src.users.user_db import get_so", log=_log, level=logging.WARNING):
            from src.users.user_db import get_source_document

            content = get_source_document(
                scheduler._project_name, "yapi", _active_version_label
            )
            if content:
                scheduler._api_docs_context = content
                _any_restored = True
                _log.info(
                    "[断点恢复] _api_docs_context: 从 MySQL 恢复 (%d 字符)",
                    len(content),
                )

    # ── 2. 恢复 Figma UI 上下文 (_ui_context) ──
    if not getattr(scheduler, "_ui_context", None):
        with guard("_restore_step1_contexts: from src.core.db_manager import get_", log=_log, level=logging.WARNING):
            from src.core.db_manager import get_connection

            conn = get_connection()
            try:
                rows = conn.execute(
                    "SELECT name, canvas, element_count FROM figma_pages "
                    "WHERE project=%s AND version_label=%s ORDER BY name",
                    (scheduler._project_name, _active_version_label),
                ).fetchall()
                if rows:
                    parts = ["## Figma 设计稿页面列表 (%d 页面)" % len(rows), ""]
                    for r in rows:
                        canvas_tag = f" [{r['canvas']}]" if r.get("canvas") else ""
                        parts.append(
                            f"- {r['name']}{canvas_tag} ({r.get('element_count', 0)} 个交互元素)"
                        )
                    scheduler._ui_context = "\n".join(parts)
                    _any_restored = True
                    _log.info(
                        "[断点恢复] _ui_context: 从 figma_pages 表恢复 (%d 页面)",
                        len(rows),
                    )
            finally:
                try:
                    conn.close()
                except Exception as _e:
                    _log.debug(
                        "[step2_context_restore] 非关键异常: %s", _e, exc_info=True
                    )

    # ── 3. 恢复测试账号上下文 (_test_accounts_context) ──
    if not getattr(scheduler, "_test_accounts_context", None):
        with guard("_restore_step1_contexts: from src.core.config_loader import c", log=_log, level=logging.WARNING):
            from src.core.config_loader import config

            if config.db_enabled:
                from src.core.session_context import session_ctx

                test_accounts = session_ctx.get_test_accounts()
                if test_accounts:
                    scheduler._test_accounts_context = test_accounts
                    _any_restored = True
                    _log.info(
                        "[断点恢复] _test_accounts_context: 从数据库重新查询 (%d 字符)",
                        len(test_accounts),
                    )

    if _any_restored:
        _log.info("[断点恢复] Step1 内存上下文恢复完成")
    else:
        _log.info("[断点恢复] 无需恢复（所有上下文已存在或无数据）")
