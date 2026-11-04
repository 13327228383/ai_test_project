# -*- coding: utf-8 -*-
"""项目版本上下文工具。

所有派生态（需求、测试用例、映射、覆盖、执行）都应尽量写入当前 active version_id。
这里采用软依赖：取不到版本时返回 None，旧项目和离线脚本仍可运行。
"""

from contextlib import contextmanager
from src.core.guarded import guard
import re
import threading
import logging

_log = logging.getLogger(__name__)

# ★ 线程级版本上下文（非 async 场景，使用 threading.local 而非 contextvars）
_version_ctx = threading.local()


# run_id 非法字符过滤（保留字母数字下划线，其他替换为下划线）
_RUN_ID_SAFE_RE = re.compile(r"[^\w]")


def make_run_id(project: str = "", version_label: str = "") -> str:
    """生成确定性 run_id = '{project}__{version_label}'。

    设计目的：
    - 替代随机 UUID，使重启服务后前端可从 (project, version_label) 推导出 run_id 查历史日志
    - 同一项目同一版本重新执行会复用同一 run_id（由 _start.py 调用方负责 DELETE 旧日志）
    - 不含 step（一个 run 跨多步骤，step 信息在 run_logs.step_num 字段）
    - 不含 user_id（项目权限已隔离用户，同项目内多用户共享测试日志）

    Args:
        project: 项目名，取不到时用 'default'
        version_label: 版本标签，取不到时用 'unversioned'

    Returns:
        确定性 run_id 字符串，如 '某SPA系统__v1.0' / '某SPA系统__20260717_153000'
    """
    p = _RUN_ID_SAFE_RE.sub("_", str(project or "").strip()) or "default"
    v = _RUN_ID_SAFE_RE.sub("_", str(version_label or "").strip()) or "unversioned"
    return f"{p}__{v}"


def current_run_id(project: str = "") -> str:
    """便捷方法：基于当前 session 的 active version_label 生成 run_id。

    供前端 API 调用：前端无需传 run_id，后端自动从 session 推导。
    """
    if not project:
        from src.core.config_loader import config

        project = getattr(config, "project_name", "") or ""
    vlabel = current_version_label(project)
    return make_run_id(project, vlabel)


def resolve_run_id(project: str = "", run_id: str = "") -> str:
    """run_id 权威解析（全仓唯一实现）：显式传入优先 → 线程级上下文 → session 版本归一。

    ★ 根因修复：此前的调用方各自用 ``getattr(self, "_current_run_id", "")``
      之类的写法取 run_id，而该实例属性全仓从未被赋值 → 恒为空串，executions 落库
      run_id='' → 所有按 run_id 的查询（Step5 报告 / 完成判定 / 指标聚合）全部看不见，
      形成「日志声称写入成功、DB 零命中」的假完成。统一收口到本函数，禁止再次散落实现。

    Returns:
        解析出的 run_id；全部来源不可用时返回空串（调用方须显式告警，不得静默）。
    """
    rid = str(run_id or "").strip()
    if rid:
        return rid
    # ① 线程级 run_id（_run_step 在步骤线程内设置，最精确）
    with guard("resolve_run_id: from src.backend.state import _threa", log=_log):
        from src.backend.state import _thread_run_id

        rid = str(getattr(_thread_run_id, "value", "") or "").strip()
        if rid:
            return rid
    # ② 按当前 session 的 active version 归一为 '{project}__{version_label}'
    with guard("resolve_run_id: rid = str(current_run_id(project) or", log=_log):
        rid = str(current_run_id(project) or "").strip()
        if rid:
            if rid.endswith("__unversioned"):
                # 版本上下文缺失：仍写入（好过空串，至少保留 project 维度可查），但显式告警
                _log.warning(
                    "[Versioning] run_id 无法确定版本，退回 %s（版本上下文缺失，"
                    "run 维度归属可能不准）",
                    rid,
                )
            return rid
    return ""


def get_execution_version_id() -> int:
    """返回当前执行上下文指定的 version_id；未指定返回 0。"""
    try:
        return int(getattr(_version_ctx, "execution_version_id", 0) or 0)
    except Exception as _e:
        _log.debug("[Versioning] execution_version_id 读取失败: %s", _e, exc_info=True)
        return 0


@contextmanager
def execution_version_context(version_id: int):
    """临时切换执行/读取版本上下文，不修改 project_versions.active 状态。"""
    old_value = getattr(_version_ctx, "execution_version_id", 0)
    try:
        _version_ctx.execution_version_id = int(version_id or 0)
        yield
    finally:
        _version_ctx.execution_version_id = old_value


def current_version_id(project: str = "") -> int:
    """返回当前项目 active version 的 id；不可用时返回 0 作为 legacy 版本。"""
    try:
        override = get_execution_version_id()
        if override:
            return override
        if not project:
            from src.core.config_loader import config

            project = getattr(config, "project_name", "") or ""
        if not project:
            return 0
        from src.users.user_db import get_active_version

        active = get_active_version(project)
        if not active:
            return 0
        vid = active.get("id") if isinstance(active, dict) else None
        return int(vid) if vid not in (None, "") else 0
    except Exception as _e:
        _log.debug("[Versioning] current_version_id 读取失败: %s", _e, exc_info=True)
        return 0


def current_version_label(project: str = "") -> str:
    """返回当前项目 active version_label；不可用时返回空字符串。"""
    try:
        if not project:
            from src.core.config_loader import config

            project = getattr(config, "project_name", "") or ""
        if not project:
            return ""
        from src.users.user_db import get_active_version_label

        return str(get_active_version_label(project) or "")
    except Exception as _e:
        _log.debug("[Versioning] current_version_label 读取失败: %s", _e, exc_info=True)
        return ""


def current_version_status(project: str = "") -> str:
    """★ PV-012: 返回当前版本状态（draft/active/archived/deleted）。

    优先读 execution_version_context 指定的版本状态；
    不可用（如 legacy 版本 version_id=0 或查询失败）返回空字符串，
    调用方应将空字符串视为"不限制"（向后兼容）。
    """
    try:
        vid = current_version_id(project)
        if not vid:
            return ""  # legacy 版本，不限制
        if not project:
            from src.core.config_loader import config

            project = getattr(config, "project_name", "") or ""
        from src.core.db_manager import get_connection

        conn = None
        try:
            conn = get_connection()
            row = conn.execute(
                "SELECT status FROM project_versions WHERE id=%s AND project=%s",
                (vid, project),
            ).fetchone()
            if not row:
                return ""
            return str(
                (row.get("status") if isinstance(row, dict) else row["status"]) or ""
            )
        finally:
            if conn:
                try:
                    conn.close()
                except (OSError, ConnectionError) as _e:
                    _log.debug("[versioning] conn.close 异常: %s", _e, exc_info=True)
    except Exception as _e:
        _log.debug(
            "[Versioning] current_version_status 读取失败: %s", _e, exc_info=True
        )
        return ""


def assert_version_writable(project: str = "") -> bool:
    """★ PV-012: 检查当前版本是否可写入（status in draft/active）。

    - archived/deleted 状态 → 返回 False（拒绝写入）
    - 空状态（legacy 或查询失败）→ 返回 True（不限制，向后兼容）
    - draft/active → 返回 True
    """
    try:
        status = current_version_status(project)
        if not status:
            return True  # legacy 或不可用，不限制
        if status in ("draft", "active"):
            return True
        _log.warning(
            "[Versioning] 版本状态为 %s，拒绝写入（仅 draft/active 可写）", status
        )
        return False
    except Exception as _e:
        _log.debug(
            "[Versioning] assert_version_writable 检查失败: %s", _e, exc_info=True
        )
        return True  # 检查失败不阻断（向后兼容）
