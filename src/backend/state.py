# -*- coding: utf-8 -*-
"""
全局状态管理模块
================
从 app.py 提取，所有模块级全局变量和线程安全的 getter/setter。

导入方式：
    from src.backend.state import _current_project, _is_running, ...
"""

import threading
from src.core.guarded import guard
import logging
from enum import Enum

_log = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# ★ S45-011: 显式状态机定义
# ═══════════════════════════════════════════════════════════════


class ExecutionState(str, Enum):
    """执行状态枚举（显式状态机）。

    状态转换路径：
        PENDING → RUNNING → PAUSED → RUNNING (resume)
                          → COMPLETED
                          → FAILED
                          → FORCED_STOPPED
    """

    PENDING = "pending"  # 已创建未启动
    RUNNING = "running"  # 运行中
    PAUSED = "paused"  # 已暂停
    COMPLETED = "completed"  # 正常完成
    FAILED = "failed"  # 异常失败
    FORCED_STOPPED = "forced_stopped"  # 强制停止


# ═══════════════════════════════════════════════════════════════
# ★ P0-05: 全局锁顺序 — 防止死锁
# ═══════════════════════════════════════════════════════════════
# 锁编号越小优先级越高。任何需要同时持有多个锁的代码，必须按编号升序获取。
# 违反顺序会在 DEBUG 级别记录警告（生产环境可通过 LOCK_ORDER_STRICT=1 升级为 RuntimeError）。
# ─────────────────────────────────────────────────────────────

_LOCK_ORDER = {}  # Lock对象 → 编号 (int)
_LOCK_ORDER_NAMES = {}  # Lock对象 → 名称 (str)
_next_lock_order = [0]  # 用list包装以支持闭包修改

_HELD_LOCKS = threading.local()  # 线程级已持有锁编号集合

_STRICT = __import__("os").environ.get("LOCK_ORDER_STRICT", "") == "1"


def _register_lock(lock: threading.Lock, name: str) -> None:
    """将锁注册到全局顺序表（首次注册分配编号）。"""
    if lock not in _LOCK_ORDER:
        _next_lock_order[0] += 1
        _LOCK_ORDER[lock] = _next_lock_order[0]
        _LOCK_ORDER_NAMES[lock] = name


class ordered_lock:
    """上下文管理器：按全局锁顺序获取锁。

    用法:
        with ordered_lock(_lock_a, _lock_b):
            # 同时持有 _lock_a 和 _lock_b
    """

    def __init__(self, *locks: threading.Lock):
        # 按全局编号升序排序，确保始终从低编号到高编号获取
        self._locks = sorted(locks, key=lambda lk: _LOCK_ORDER.get(lk, 9999))
        self._acquired = []

    def __enter__(self):
        held = getattr(_HELD_LOCKS, "set", None) or set()
        for lk in self._locks:
            order = _LOCK_ORDER.get(lk, 0)
            name = _LOCK_ORDER_NAMES.get(lk, "?")
            # 检查是否违反锁顺序：只能在持有更小编号锁之后才能获取更大编号锁
            if held and order > 0:
                max_held = max((_LOCK_ORDER.get(hl, 0) for hl in held), default=0)
                if order < max_held:
                    msg = (
                        f"[LOCK] 锁顺序违规！当前持有 #{max_held}，"
                        f"尝试获取 #{order}({name})，应始终升序获取"
                    )
                    if _STRICT:
                        raise RuntimeError(msg)
                    _log.warning(msg)
            lk.acquire()
            self._acquired.append(lk)
            if order > 0:
                held.add(lk)
        _HELD_LOCKS.set = held
        return self

    def __exit__(self, *args):
        held = getattr(_HELD_LOCKS, "set", None) or set()
        for lk in reversed(self._acquired):
            try:
                lk.release()
            except RuntimeError as _e:
                _log.debug("[state] 释放锁异常: %s", _e, exc_info=True)
            if _LOCK_ORDER.get(lk, 0) > 0:
                held.discard(lk)
        _HELD_LOCKS.set = held
        return False


# ═══════════════════════════════════════════════════════════════
# 当前项目（线程本地存储，实现多用户隔离）
# ═══════════════════════════════════════════════════════════════

_project_thread_local = threading.local()


def _get_project_thread_local():
    """获取当前线程的项目存储，首次访问时初始化默认值。"""
    if not hasattr(_project_thread_local, "project_name"):
        _project_thread_local.project_name = ""
    return _project_thread_local


def _current_project():
    """获取当前项目名（线程本地，多用户隔离）。"""
    return _get_project_thread_local().project_name


def _set_current_project(name):
    """设置当前项目名（线程本地）并同步到 Flask session。"""
    tl = _get_project_thread_local()
    if name:
        from src.users.manager import _conn

        with guard("_set_current_project: _c = _conn()", log=_log):
            _c = _conn()
            # ★ 大小写不敏感查询：始终用 LOWER 比较，确保同一项目名不同大小写写法都能匹配
            _row = _c.execute(
                "SELECT name FROM projects WHERE LOWER(name)=LOWER(%s) AND status='active'",
                (name,),
            ).fetchone()
            if _row:
                name = _row["name"]  # 规范化为 DB 中的原始值
            _c.close()
    tl.project_name = name or ""
    # ★ 同步写入 Flask session，服务器重启后可恢复
    with guard("_set_current_project: from flask import session as flask_s", log=_log):
        from flask import session as flask_session

        if name:
            flask_session["project_name"] = name
        else:
            flask_session.pop("project_name", None)


def _resolve_project(explicit: str = "") -> str:
    """通用项目解析回退链。

    - 优先返回显式传入的 project（请求参数 / body）。
    - 回退到当前项目（thread-local，由 _middleware 从 Flask session 同步）。

    目的：消除各只读路由对 ``current_app.config["PROJECT_NAME"]`` 的依赖
    （该配置从未被设置，导致 KPI / 诊断等接口在缺省 project 时返回 400）。
    """
    p = (explicit or "").strip()
    if p:
        return p
    return (_current_project() or "").strip()


# ═══════════════════════════════════════════════════════════════
# 当前版本（线程本地存储，实现多用户隔离）
# ═══════════════════════════════════════════════════════════════

_version_thread_local = threading.local()


def _get_version_thread_local():
    """获取当前线程的版本存储，首次访问时初始化默认值。"""
    if not hasattr(_version_thread_local, "version_label"):
        _version_thread_local.version_label = ""
    return _version_thread_local


def _current_version() -> str:
    """获取当前版本标签（version_label，线程本地，多用户隔离）。"""
    return _get_version_thread_local().version_label


def _set_current_version(label: str = ""):
    """设置当前版本标签（线程本地）并同步到 Flask session。"""
    tl = _get_version_thread_local()
    tl.version_label = label or ""
    try:
        from flask import session as flask_session

        if label:
            flask_session["version_label"] = label
        else:
            flask_session.pop("version_label", None)
    except Exception as _e:
        _log.debug("[state] 设置版本标签到 session 异常: %s", _e, exc_info=True)


# ═══════════════════════════════════════════════════════════════
# 运行状态（实际状态由 _active_runs 字典管理，_is_running 检查其长度）
# ═══════════════════════════════════════════════════════════════

# ★ A1 改造：_force_stop / _paused_step / _pause_requested 由"进程级单值"改为"按 project 隔离的字典"
#   原实现：单布尔/单整数，停 A 项目会杀 B 项目；A 暂停会覆盖 B 暂停断点。
#   新实现：dict[project] → 状态值，每个项目独立。
_force_stop_by_project: dict[str, bool] = {}
_force_stop_lock = threading.Lock()
_paused_step_by_project: dict[str, int] = {}
_paused_step_lock = threading.Lock()
_pause_requested_by_project: dict[str, bool] = {}
# ★ 版本一致性警告（Step 3 启动时检测，前端弹窗确认）
# ★ A1 改造：由"进程级单值"改为"按 project 隔离的字典"，避免 admin 切换项目时串扰
_version_warning_by_project: dict[str, dict] = {}
_version_warning_lock = threading.Lock()
_thinking_by_project: dict[str, dict] = {}  # ★ A1: 按 project 隔离的 AI 思考状态
_thinking_lock = threading.Lock()


def _get_thinking(project: str = None) -> dict:
    """获取指定项目的 AI 思考状态。project=None 时使用 _current_project()。"""
    if not project:
        project = _current_project()
    with _thinking_lock:
        return dict(
            _thinking_by_project.get(
                project, {"active": False, "name": "", "start_ts": 0.0}
            )
        )


def _set_thinking_active(active: bool, name: str = "", project: str = None):
    """设置 AI 思考状态。"""
    if not project:
        project = _current_project()
    with _thinking_lock:
        if active:
            _thinking_by_project[project] = {
                "active": True,
                "name": name,
                "start_ts": __import__("time").time(),
            }
        else:
            _thinking_by_project.pop(project, None)


def _clear_thinking(project: str = None):
    """清除 AI 思考状态。"""
    if not project:
        project = _current_project()
    with _thinking_lock:
        _thinking_by_project.pop(project, None)


# ★ 向后兼容：模块级全局变量，供旧代码直接赋值（_st._thinking_active = True）
# 新代码应使用 _set_thinking_active() / _get_thinking() 函数
_thinking_active = False
_thinking_name = ""
_thinking_start_ts = 0.0


def _set_running(val: bool):
    """设置运行状态（向后兼容）。
    - True: 空操作，由 _active_runs 字典管理（调用方在创建 _active_runs 条目后调用）。
    - False: 清理所有僵尸 _active_runs 条目（线程已死但未清理）+ 重置 _thinking_active。
      在 stop watchdog / finally 块中作为最后的兜底清理。"""
    global _thinking_active
    if not val:
        with _active_runs_lock:
            stale_ids = [
                rid
                for rid, r in list(_active_runs.items())
                if r.get("thread") is not None and not r["thread"].is_alive()
            ]
            for rid in stale_ids:
                _active_runs.pop(rid, None)
        _thinking_active = False
        # ★ 清空所有项目的 AI 思考状态
        with _thinking_lock:
            _thinking_by_project.clear()


def _is_running(project: str = None) -> bool:
    """检查是否有活跃的 run（兼容旧接口）。

    ★ A1 改造：支持按 project 过滤。
    - project=None（默认）：任何项目有活跃 run 即返回 True（保持向后兼容）。
    - project=非空：仅当该项目有活跃 run 才返回 True。

    ★ 根因修复(2026-09-27): 读前先 _sweep_stale_runs 清僵尸条目，与 /api/status 口径一致。
      原实现直接数 _active_runs，死线程但未 pop 的 run 会让项目切换永久返回
      409「有 X 个任务正在执行中」，用户无法切换项目。
    """
    with _active_runs_lock:
        _sweep_stale_runs(project)
        if project is None:
            return len(_active_runs) > 0
        return any(r.get("project") == project for r in _active_runs.values())


def _set_force_stop(val: bool, project: str = None):
    """设置强制停止标志。

    ★ A1 改造：按 project 隔离。
    - project=None：设置所有项目的 _force_stop（向后兼容，用于全局停止）。
    - project=非空：仅设置该项目的 _force_stop。
    """
    with _force_stop_lock:
        if project is None:
            # 全局停止：对所有已知项目设置 True；False 时清空所有
            if val:
                # 收集当前所有活跃项目
                with _active_runs_lock:
                    active_projects = {
                        r.get("project")
                        for r in _active_runs.values()
                        if r.get("project")
                    }
                for p in active_projects:
                    _force_stop_by_project[p] = True
            else:
                _force_stop_by_project.clear()
        else:
            if val:
                _force_stop_by_project[project] = True
            else:
                _force_stop_by_project.pop(project, None)


def _get_force_stop(project: str = None) -> bool:
    """读取强制停止标志。

    ★ A1 改造：按 project 隔离。
    - project=None：任一项目有停止信号即返回 True（向后兼容，用于全局检查）。
    - project=非空：仅返回该项目的停止信号。
    """
    with _force_stop_lock:
        if project is None:
            return any(_force_stop_by_project.values())
        return _force_stop_by_project.get(project, False)


def _set_paused_step(val: int, project: str = None):
    """设置暂停断点步骤。

    ★ A1 改造：按 project 隔离。
    - project=None：设置所有活跃项目的暂停断点（向后兼容）。
    - project=非空：仅设置该项目的暂停断点。
    """
    with _paused_step_lock:
        if project is None:
            if val <= 0:
                _paused_step_by_project.clear()
            else:
                with _active_runs_lock:
                    active_projects = {
                        r.get("project")
                        for r in _active_runs.values()
                        if r.get("project")
                    }
                for p in active_projects:
                    _paused_step_by_project[p] = val
        else:
            if val <= 0:
                _paused_step_by_project.pop(project, None)
            else:
                _paused_step_by_project[project] = val


def _get_paused_step(project: str = None) -> int:
    """读取暂停断点步骤。

    ★ A1 改造：按 project 隔离。
    - project=None：返回任一活跃项目的暂停断点（向后兼容）。
    - project=非空：仅返回该项目的暂停断点。
    """
    with _paused_step_lock:
        if project is None:
            # 返回任意一个非 0 的暂停断点（向后兼容旧调用方）
            for step in _paused_step_by_project.values():
                if step > 0:
                    return step
            return 0
        return _paused_step_by_project.get(project, 0)


def _set_version_warning(data: dict, project: str = None):
    """设置版本一致性警告状态。data 含 version_label, message, pending 等。

    ★ A1 改造：按 project 隔离。
    - project=None：默认使用 _current_project()。
    """
    global _version_warning_by_project
    if not project:
        project = _current_project()
    with _version_warning_lock:
        _version_warning_by_project[project] = dict(data)


def _get_version_warning(project: str = None) -> dict:
    """获取版本一致性警告状态。

    ★ A1 改造：按 project 隔离。
    - project=None：默认使用 _current_project()。
    """
    global _version_warning_by_project
    if not project:
        project = _current_project()
    with _version_warning_lock:
        return dict(_version_warning_by_project.get(project, {}))


def _clear_version_warning(project: str = None):
    """清除版本一致性警告状态。

    ★ A1 改造：按 project 隔离。
    - project=None：默认使用 _current_project()。
    """
    global _version_warning_by_project
    if not project:
        project = _current_project()
    with _version_warning_lock:
        _version_warning_by_project.pop(project, None)


# ═══════════════════════════════════════════════════════════════
# Token 统计
# ═══════════════════════════════════════════════════════════════

_token_lock = threading.Lock()  # ★ P0-05: 保护所有 token 变量的读写

_total_input_tokens = 0
_total_output_tokens = 0
_total_ai_time = 0.0
_total_ai_cost = 0.0
_cur_input_tokens = 0
_cur_output_tokens = 0
_cur_ai_time = 0.0
_cur_ai_cost = 0.0
_last_model = "—"
_last_dom_count = 0
_last_dom_original = 0
_last_switch_reason = ""

# ★ DB 基线：进程启动时从 ai_health_metrics 快照的历史累计值。
#    用于跨重启累加：合计 = 内存累计(本次会话) + DB基线(历史会话)。
#    延迟加载，避免启动时 DB 未就绪。
_db_token_baseline = None  # None=未加载, {} = 加载但 DB 为空
_db_baseline_lock = threading.Lock()


def _load_db_token_baseline() -> dict:
    """★ 从 DB 加载 token 历史基线（仅首次调用时查询，后续用缓存）。

    延迟加载原因：observer 注册在 app.py 启动阶段，此时 DB pool 可能尚未初始化。
    第一次 /api/token-stats 请求时 DB 一定就绪。
    """
    global _db_token_baseline
    if _db_token_baseline is not None:
        return _db_token_baseline
    with _db_baseline_lock:
        if _db_token_baseline is not None:
            return _db_token_baseline
        baseline = {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "time": 0.0}
        try:
            from src.models.base import get_conn

            conn = get_conn()
            try:
                rows = conn.execute(
                    "SELECT metric_name, SUM(value) as total FROM ai_health_metrics "
                    "WHERE metric_type='token_usage' GROUP BY metric_name"
                ).fetchall()
                for r in rows:
                    name = r["metric_name"]
                    val = float(r["total"] or 0)
                    if name == "prompt_tokens":
                        baseline["input_tokens"] = int(val)
                    elif name == "completion_tokens":
                        baseline["output_tokens"] = int(val)
                    elif name == "cost_usd":
                        baseline["cost_usd"] = val
            finally:
                try:
                    conn.close()
                except Exception:
                    _log.debug("[state] close token基线连接失败", exc_info=True)
        except Exception:
            _log.debug("[state] DB 不可用，基线保持 0", exc_info=True)
        _db_token_baseline = baseline
        return baseline


def _update_token_stats(
    prompt_tokens: int,
    completion_tokens: int,
    elapsed: float,
    cost: float,
    model: str = "—",
) -> None:
    """★ P0-05: 线程安全地更新 token 统计（一次加锁完成所有更新）。"""
    global _total_input_tokens, _total_output_tokens, _total_ai_time, _total_ai_cost
    global _cur_input_tokens, _cur_output_tokens, _cur_ai_time, _cur_ai_cost, _last_model
    with _token_lock:
        _cur_input_tokens = prompt_tokens
        _cur_output_tokens = completion_tokens
        _cur_ai_time = elapsed
        _cur_ai_cost = cost
        _last_model = model
        _total_input_tokens += prompt_tokens
        _total_output_tokens += completion_tokens
        _total_ai_time += elapsed
        _total_ai_cost += cost


def _read_token_stats() -> dict:
    """★ P0-05: 线程安全地读取 token 统计快照。"""
    with _token_lock:
        return {
            "cur_input_tokens": _cur_input_tokens,
            "cur_output_tokens": _cur_output_tokens,
            "cur_ai_time": _cur_ai_time,
            "cur_ai_cost": _cur_ai_cost,
            "total_input_tokens": _total_input_tokens,
            "total_output_tokens": _total_output_tokens,
            "total_ai_time": _total_ai_time,
            "total_ai_cost": _total_ai_cost,
            "last_model": _last_model,
            "last_dom_count": _last_dom_count,
            "last_dom_original": _last_dom_original,
            "last_switch_reason": _last_switch_reason,
        }


def _set_token_meta(
    model: str = None,
    dom_count: int = None,
    dom_original: int = None,
    switch_reason: str = None,
) -> None:
    """★ P0-05: 线程安全地更新 token 元信息（模型、DOM 计数等）。"""
    global _last_model, _last_dom_count, _last_dom_original, _last_switch_reason
    with _token_lock:
        if model is not None:
            _last_model = model
        if dom_count is not None:
            _last_dom_count = dom_count
        if dom_original is not None:
            _last_dom_original = dom_original
        if switch_reason is not None:
            _last_switch_reason = switch_reason


# ═══════════════════════════════════════════════════════════════
# 进度跟踪
# ═══════════════════════════════════════════════════════════════

_run_progress = {}
_run_progress_lock = threading.Lock()


def _update_progress(
    run_id, step_num, step_label, step_done, step_total, from_step, to_step
):
    """线程安全更新某 run 的进度。

    ★ 保留已有 req_progress 字段（Step3 worker 单独写入，避免被本函数覆盖）。
    """
    with _run_progress_lock:
        existing = _run_progress.get(run_id, {})
        _run_progress[run_id] = {
            "mode": "full" if to_step > from_step else "single",
            "current_step": step_num,
            "step_label": step_label,
            "step_done": step_done,
            "step_total": step_total,
            "from_step": from_step,
            "to_step": to_step,
            # ★ 保留 Step3 worker 已写入的 per-req 进度
            "req_progress": existing.get("req_progress", {}),
        }


def _update_req_progress(run_id, req_id, status, duration=None):
    """线程安全更新某 run 内单条需求条目的建模状态。

    :param run_id: 运行 ID
    :param req_id: 需求 ID
    :param status: "running" | "completed" | "failed"
    :param duration: 完成耗时（秒），仅 completed/failed 时填
    """
    if not run_id or not req_id:
        return
    import time as _time

    with _run_progress_lock:
        run_entry = _run_progress.setdefault(run_id, {})
        req_map = run_entry.setdefault("req_progress", {})
        entry = req_map.get(req_id, {})
        # running 时记录起始时间；completed/failed 时计算或写入 duration
        if status == "running":
            entry["start_ts"] = _time.time()
            entry["duration"] = None
        else:
            start_ts = entry.get("start_ts")
            if duration is None and start_ts:
                duration = round(_time.time() - start_ts, 1)
            entry["duration"] = duration
        entry["status"] = status
        req_map[req_id] = entry


# ═══════════════════════════════════════════════════════════════
# 日志系统（SSE 广播基础设施）
# ═══════════════════════════════════════════════════════════════

_all_log_lines = []  # 全部日志行（带时间戳）
_all_log_lines_lock = threading.Lock()
_sse_queues = []  # SSE 订阅者队列
_sse_queues_lock = threading.Lock()

# ── 多 run 并行支持 ──
_thread_run_id = threading.local()  # 线程级 run_id，用于 LogCapture 路由
_active_runs = {}  # run_id → {thread, force_stop, step, project, start_ts}
_active_runs_lock = threading.Lock()
_run_sse_queues = {}  # run_id → [Queue]
_run_sse_queues_lock = threading.Lock()
_run_logs = {}  # run_id → [log_lines]
_run_logs_lock = threading.Lock()

# ★ AI 健康度指标持久化错误追踪
_health_flush_errors = {}  # run_id → 最近一次 flush 错误信息
_health_flush_errors_lock = threading.Lock()


# ═══════════════════════════════════════════════════════════════
# ★ P0-05: 锁顺序注册（锁编号越小优先级越高）
# ═══════════════════════════════════════════════════════════════
# ★ _current_project_lock 已移除（改为 thread-local，无需锁）
_register_lock(_force_stop_lock, "_force_stop_lock")  # 2
_register_lock(_paused_step_lock, "_paused_step_lock")  # 3
_register_lock(_version_warning_lock, "_version_warning_lock")  # 4
_register_lock(_token_lock, "_token_lock")  # 5
_register_lock(_run_progress_lock, "_run_progress_lock")  # 6
_register_lock(_all_log_lines_lock, "_all_log_lines_lock")  # 7
_register_lock(_sse_queues_lock, "_sse_queues_lock")  # 8
_register_lock(_active_runs_lock, "_active_runs_lock")  # 9
_register_lock(_run_sse_queues_lock, "_run_sse_queues_lock")  # 10
_register_lock(_run_logs_lock, "_run_logs_lock")  # 11
_register_lock(_health_flush_errors_lock, "_health_flush_errors_lock")  # 12


def _sweep_stale_runs(project: str = None) -> int:
    """清理僵尸 active_runs 条目（死线程）。

    **安全边界**: 只清理**线程已真正退出**的条目。线程仍存活却长时间无进展时，
      只置 per-run force_stop 令其自行收敛，**绝不 de-list** —— 否则互斥判定会
      在旧 run 仍占用共享 Excel/Playwright 运行时放行新 run（同项目并发）。
      挂起判据 = 进展停滞（与执行槽 watchdog 同一套，长耗时 ≠ 挂起）。

    ★ F5 全局修复（2026-08-29；**已复核 2026-09-04**）：
      从 status.py 内联逻辑（原 L69-91）抽为可复用函数，供 _start.py 各
      409 互斥判定前调用，解决"线程已死但条目未 pop → 同项目 Step2-5
      永久 409"（run_id 确定性使单条泄漏即可阻断后续所有轮次）。

    **安全边界（已复核，2026-09-04）**：只清线程已退出的条目。

    注意: 必须由调用方在 ``with _active_runs_lock:`` 内调用（本函数**不**自行
      加锁，避免与调用方重复持锁死锁 —— _active_runs_lock 是普通 Lock 不可重入）。

    Args:
        project: 非空时仅清理该项目的条目（_start.py 语义）；None 时清理
            全部条目（status.py 轮询语义，向后兼容）。

    Returns:
        清理的条目数；内部任何异常返回 0（fail-open，绝不影响请求线程）。
    """
    try:
        import time as _time

        try:
            from src.backend.routes.execution.run._start import (
                _run_progress_marker as _progress_marker,
                ai_inflight_deadline as _ai_inflight,
                run_stall_budget_sec as _stall_budget,
            )
        except Exception:  # 进度判据不可用时不判挂起（fail-open，绝不误杀）
            _progress_marker = None
        _now = _time.time()
        stale_ids = []
        for rid, r in list(_active_runs.items()):
            if project and r.get("project") != project:
                continue
            t = r.get("thread")
            if t is not None and not t.is_alive():
                # 线程已真正退出：无法再持有锁/写共享资源 → 可安全清理
                stale_ids.append(rid)
            elif t is not None and t.is_alive():
                # ★ 互斥安全边界：线程仍存活即视为仍在运行，**绝不 de-list**。
                #   移除存活 run 会让新 run 误判可启动，造成同项目并发占用共享 Excel/Playwright。
                #   仅当「无进展」超过该 step 的停滞预算才置 force_stop 令其自行收敛退出
                #   （与执行槽 watchdog 同一判据：长耗时 ≠ 挂起）。
                if _progress_marker is None:
                    continue
                start_ts = r.get("start_ts", 0) or 0
                budget = _stall_budget(r.get("step", 0))
                if not start_ts or (_now - start_ts) <= budget:
                    continue
                _last = _progress_marker(rid)
                if _last is None:
                    # 判据读不到 = 不可判，fail-open 不杀（防把活 run 误判挂起）。
                    continue
                idle_ts = _last.timestamp() if hasattr(_last, "timestamp") else start_ts
                if _ai_inflight() > _now or (_now - idle_ts) <= budget:
                    continue
                if not r.get("force_stop"):
                    r["force_stop"] = True
                    _log.warning(
                        "[sweep] run %s 仍在运行但无新进展(%.0f 分钟 > 停滞预算 %.0f 分钟)，"
                        "已置 force_stop 令其自行收敛退出",
                        rid,
                        (_now - idle_ts) / 60,
                        budget / 60,
                    )
                else:
                    _log.warning(
                        "[sweep] run %s 无新进展(%.0f 分钟 > 停滞预算 %.0f 分钟)，"
                        "为防同项目并发**不**移除；已置 force_stop，等待线程退出",
                        rid,
                        (_now - idle_ts) / 60,
                        budget / 60,
                    )
        for rid in stale_ids:
            _log.info(
                "[sweep] 清理僵尸 active_run: %s (线程已退出但未清理)", rid
            )
            _active_runs.pop(rid, None)
            with guard("_sweep_stale_runs: _run_progress.pop(rid, None)", log=_log):
                _run_progress.pop(rid, None)
            with guard("_sweep_stale_runs: _run_sse_queues.pop(rid, None)", log=_log):
                _run_sse_queues.pop(rid, None)
        return len(stale_ids)
    except Exception as _e:
        _log.warning(
            "[sweep] 清理僵尸 active_runs 异常(fail-open): %s", _e, exc_info=True
        )
        return 0
