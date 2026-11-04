# -*- coding: utf-8 -*-
"""
Step1 辅助函数：线程上下文工具 + 并行数据源包装器 + 数据清理。

提取自 step1.py。
"""
import logging

from src.core.config_loader import (
    config,
    get_current_user,
    set_current_user,
    get_session_key,
    set_session_key,
)
from src.backend.state import _current_project, _set_current_project

_log = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# 线程上下文恢复工具
# ═══════════════════════════════════════════════════════════════


def _capture_thread_ctx():
    """捕获当前线程的用户/项目/Key/run_id 上下文（供子线程恢复）。"""
    from src.backend.state import _thread_run_id

    return {
        "user": get_current_user(),
        "key": get_session_key(),
        "project": _current_project() or config._default_project,
        "run_id": getattr(_thread_run_id, "value", None),
    }


def _restore_thread_ctx(ctx: dict):
    """在子线程中恢复上下文。"""
    if ctx.get("user"):
        set_current_user(ctx["user"])
    if ctx.get("key"):
        set_session_key(ctx["key"])
    if ctx.get("project"):
        _set_current_project(ctx["project"])
        # ★ 同时设置 ConfigRegistry 的 thread-local（CLI 模式必需）
        config._default_project = ctx["project"]
    # ★ 恢复 run_id：Step1 四数据源并行拉取在 ThreadPoolExecutor 子线程中执行，
    # thread-local 的 _thread_run_id 不会自动继承，不恢复则 fetcher 的 _log.info()
    # 发出的 SSE payload 不带 run_id，前端无法路由到 Step tab。
    if ctx.get("run_id"):
        from src.backend.state import _thread_run_id

        _thread_run_id.value = ctx["run_id"]


# ═══════════════════════════════════════════════════════════════
# 并行数据源拉取包装器（每个包装器在子线程中运行一个数据源）
# ═══════════════════════════════════════════════════════════════


def _fetch_confluence_in_thread(
    fetcher,
    active_ver,
    _version_label,
    figma_force_refresh,
    force_overwrite,
    run_id,
    ctx,
):
    """在子线程中拉取 Confluence。"""
    _restore_thread_ctx(ctx)
    return fetcher._fetch_confluence(
        active_ver, _version_label, figma_force_refresh, force_overwrite, run_id
    )


def _fetch_figma_in_thread(
    fetcher,
    active_ver,
    _version_label,
    prev_active_version_label,
    figma_force_refresh,
    force_overwrite,
    ctx,
):
    """在子线程中拉取 Figma（★ 并行模式下 cf_changed=True，走独立缓存判断）。"""
    _restore_thread_ctx(ctx)
    return fetcher._fetch_figma(
        active_ver,
        _version_label,
        prev_active_version_label,
        cf_changed=True,  # ★ 并行模式：默认假设需求已变更
        figma_force_refresh=figma_force_refresh or force_overwrite,
        cf_ok=False,  # ★ 无 Confluence 结果可用
        old_req_text="",
        req_text="",
    )


def _fetch_database_in_thread(fetcher, _version_label, ctx):
    """在子线程中查询数据库测试账号 + DB Schema。

    ★ v5: 传入 _version_label 与 _project（与 yapi/persist 一致）。
          _project 取自 ctx["project"]（_current_project() 在线程捕获阶段已正确赋值），
          禁止从 config.project_name 取（config 无该公有属性 → getattr 返回 '' →
          INSERT 静默跳过 → 假ok）。这是 v5 修复残留的最后一处根治点。
    """
    _restore_thread_ctx(ctx)
    return fetcher._fetch_database(ctx.get("project", ""), _version_label)


def _fetch_yapi_in_thread(
    fetcher, _version_label, figma_force_refresh, force_overwrite, ctx
):
    """在子线程中拉取 YApi。"""
    _restore_thread_ctx(ctx)
    return fetcher._fetch_yapi(_version_label, figma_force_refresh or force_overwrite)


def _clear_step1_data():
    """强制覆盖：清空当前项目全部派生数据与输入态（源码变更后全量重取）。

    输入态一并清空是"强制覆盖"的语义：旧源失效后所有下游产物都不可信，
    因此不再按 Step1 相关表逐项挑选，统一走 schema 派生的项目级清空。
    """
    from src.core.config_loader import config as _cfg
    from src.core.project_purge import purge_project

    project = getattr(_cfg, "project_name", "") or ""
    result = purge_project(project, include_inputs=True)
    _log.info(
        "  [强制覆盖] 清除完成: %d 行删除 / %d 张表, %d 个错误",
        result["total"], len(result["tables"]), len(result["errors"]),
    )
    if result["errors"]:
        _log.warning("  [强制覆盖] 清除过程中有错误（不阻断）: %s", result["errors"])

