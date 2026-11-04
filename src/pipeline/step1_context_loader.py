# -*- coding: utf-8 -*-
"""Step1 上下文加载器 — 从 MySQL 读取 Step1 产出的 3 个上下文。

需求规格说明书 §4.1: 跨Step只能MySQL通信，禁止内存变量跨Step传递。
此模块替代 PipelineScheduler 的 _ui_context / _api_docs_context /
_test_accounts_context 实例变量，每个 Step 直接调用此函数从 MySQL 读取。
"""

import logging
from src.core.guarded import guard

_log = logging.getLogger(__name__)


def load_step1_contexts(project_name: str) -> dict:
    """从 MySQL 加载 Step1 产出的 3 个上下文。

    返回 dict:
        {
            "ui_context": str,          # Figma UI 描述
            "api_docs_context": str,    # YApi 接口文档
            "test_accounts_context": str,  # 测试账号信息
        }

    数据源:
        - api_docs_context → source_documents 表 (doc_type='yapi')
        - ui_context      → source_documents 表 (doc_type='figma') / figma_pages 表
        - test_accounts_context → 数据库实时查询 (session_ctx.get_test_accounts)
    """
    contexts = {
        "ui_context": "",
        "api_docs_context": "",
        "test_accounts_context": "",
    }

    # 获取当前活跃版本标签
    try:
        from src.users.manager import get_active_version_label

        active_version_label = get_active_version_label(project_name)
    except Exception as _e:
        _log.debug(
            "[step1_context_loader] load_step1_contexts 异常: %s", _e, exc_info=True
        )
        active_version_label = ""

    # 1. API 文档上下文
    with guard("load_step1_contexts: from src.users.user_db import get_so", log=_log, level=logging.WARNING):
        from src.users.user_db import get_source_document

        content = get_source_document(project_name, "yapi", active_version_label)
        if content:
            contexts["api_docs_context"] = content
            _log.info("[step1_context_loader] api_docs_context: %d 字符", len(content))

    # 2. Figma UI 上下文 — 从 figma_pages 表读取（页面名 + 画布，轻量上下文）
    try:
        from src.core.db_manager import get_connection

        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT name, canvas, element_count FROM figma_pages "
                "WHERE project=%s AND version_label=%s ORDER BY name",
                (project_name, active_version_label),
            ).fetchall()
            if rows:
                parts = ["## Figma 设计稿页面列表 (%d 页面)" % len(rows), ""]
                for r in rows:
                    canvas_tag = f" [{r['canvas']}]" if r.get("canvas") else ""
                    parts.append(
                        f"- {r['name']}{canvas_tag} ({r.get('element_count', 0)} 个交互元素)"
                    )
                contexts["ui_context"] = "\n".join(parts)
                _log.info(
                    "[step1_context_loader] ui_context: %d 页面 (从 figma_pages 表)",
                    len(rows),
                )
            else:
                # 降级：表为空时尝试旧 source_documents
                from src.users.user_db import get_source_document

                content = get_source_document(
                    project_name, "figma", active_version_label
                )
                if content:
                    # ★ v5: 用功能点摘要替代一刀切截断
                    from src.pipeline.steps.step2.analyzers.single_pass import (
                        _extract_ui_feature_summary,
                    )

                    contexts["ui_context"] = _extract_ui_feature_summary(
                        content, max_chars=2500
                    )
                    _log.info(
                        "[step1_context_loader] ui_context: %d 字符 (降级 source_documents, 功能点摘要)",
                        len(contexts["ui_context"]),
                    )
        finally:
            try:
                conn.close()
            except Exception as _e:
                _log.debug("[step1_context_loader] 非关键异常: %s", _e, exc_info=True)
    except Exception as e:
        _log.warning("[step1_context_loader] ui_context 加载失败: %s", str(e)[:120])
        # 最终降级
        try:
            from src.users.user_db import get_source_document

            content = get_source_document(project_name, "figma", active_version_label)
            if content:
                # ★ v5: 用功能点摘要替代一刀切截断
                from src.pipeline.steps.step2.analyzers.single_pass import (
                    _extract_ui_feature_summary,
                )

                contexts["ui_context"] = _extract_ui_feature_summary(
                    content, max_chars=2500
                )
        except Exception as _e:
            _log.debug("[step1_context_loader] 非关键异常: %s", _e, exc_info=True)

    # 3. 测试账号上下文
    with guard("load_step1_contexts: from src.core.config_loader import c", log=_log, level=logging.WARNING):
        from src.core.config_loader import config

        if config.db_enabled:
            from src.core.session_context import session_ctx

            test_accounts = session_ctx.get_test_accounts()
            if test_accounts:
                contexts["test_accounts_context"] = test_accounts
                _log.info(
                    "[step1_context_loader] test_accounts_context: %d 字符",
                    len(test_accounts),
                )

    return contexts
