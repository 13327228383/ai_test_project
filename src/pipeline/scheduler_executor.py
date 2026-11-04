# -*- coding: utf-8 -*-
"""scheduler_executor.py — 步骤执行器：ThreadPoolExecutor + 超时轮询。

从 scheduler.py 拆分（§4.3 单文件≤800行红线）。
★ 安全拆分原则：
  - 零业务逻辑变更，仅物理位置移动
  - 保持函数签名、返回值 100% 一致
  - 原类方法 self._execute_step_with_timeout → 模块级 execute_step_with_timeout

核心逻辑：
1. 捕获当前线程的 user/project/key/version/run_id/log_file 上下文
2. 创建闭包 _step_with_ctx 在子线程内恢复上下文
3. 提交到 ThreadPoolExecutor 并轮询等待结果或超时
4. 超时/停止时主动清理 _active_runs
"""
from __future__ import annotations
from src.core.guarded import guard

import logging
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from typing import Any

from src.core.log_utils import fmt_log

_log = logging.getLogger(__name__)


def execute_step_with_timeout(
    step_num: int,
    step_func,
    quick: bool,
    figma_force_refresh: bool,
    run_id: str,
    force_step2: bool,
    force_stop_check,
    _get_step_timeout_fn,
) -> dict | None:
    """在 ThreadPoolExecutor 中执行单个步骤，带超时和上下文传递。

    Args:
        step_num: 步骤号 1-5。
        step_func: 步骤函数（self._step1_fetch_data 等）。
        quick: 快速模式。
        figma_force_refresh: 强制刷新 Figma。
        run_id: 流水线运行 ID。
        force_step2: 强制重拆解 Step2（self._force_step2）。
        force_stop_check: 停止检查回调（self._force_stop_check）。
        _get_step_timeout_fn: 超时读取函数（_get_step_timeout）。

    Returns:
        step_result: 步骤执行结果 dict，或 None。
    """
    _step_timeout = _get_step_timeout_fn(step_num)
    # ★ 关键修复：ThreadPoolExecutor 创建的工作线程 thread-local 是全新的，
    # 必须从当前调度器线程显式捕获用户/项目/Key 上下文并在子线程内恢复，
    # 否则 _user_cred() / get_current_user() 在子线程内全为 None，
    # 导致 Confluence/Figma/YApi/DB 凭据全部读为空。
    from src.core.config_loader import (
        get_current_user,
        set_current_user,
        get_session_key,
        set_session_key,
        config as _cfg,
    )
    from src.backend.state import (
        _current_project,
        _set_current_project,
        _thread_run_id,
        _current_version,
        _set_current_version,
    )

    _ctx_user = get_current_user()
    _ctx_key = get_session_key()
    _ctx_project = _current_project() or _cfg._default_project
    _ctx_version = _current_version()
    _ctx_run_id = run_id
    import src.backend.log_capture as _lc

    _ctx_log_file = getattr(_lc._current_log_file, "value", None)
    if _ctx_log_file and isinstance(_ctx_log_file, dict):
        _ctx_log_file["step_label"] = f"step{step_num}"

    def _step_with_ctx(_fn, *_a, **_kw):
        if _ctx_user:
            set_current_user(_ctx_user)
        if _ctx_key:
            set_session_key(_ctx_key)
        if _ctx_project:
            _set_current_project(_ctx_project)
            with guard("_step_with_ctx: _cfg.reload(_ctx_project)", log=_log, level=logging.WARNING):
                _cfg.reload(_ctx_project)
        if _ctx_version:
            _set_current_version(_ctx_version)
        if _ctx_run_id:
            _thread_run_id.value = _ctx_run_id
        if _ctx_log_file:
            _lc._current_log_file.value = _ctx_log_file
        try:
            return _fn(*_a, **_kw)
        finally:
            try:
                _thread_run_id.value = None
            except Exception as _pass_e:
                _log.warning(
                    "scheduler_executor._step_with_ctx 异常被吞: %s",
                    _pass_e,
                    exc_info=True,
                )
            try:
                _lc._current_log_file.value = None
            except Exception as _e:
                _log.debug("[scheduler_executor] 操作异常: %s", _e, exc_info=True)

    _executor = ThreadPoolExecutor(max_workers=1)
    try:
        if step_num == 1:
            _future = _executor.submit(
                _step_with_ctx,
                step_func,
                quick,
                figma_force_refresh=figma_force_refresh,
                run_id=run_id,
            )
        elif step_num == 2:
            _future = _executor.submit(
                _step_with_ctx, step_func, quick, force_step2=force_step2, run_id=run_id
            )
        else:
            _future = _executor.submit(_step_with_ctx, step_func, quick, run_id=run_id)
        _poll_interval = 0.5
        _deadline = time.time() + _step_timeout
        step_result = None
        while not _future.done():
            _remaining = _deadline - time.time()
            if _remaining <= 0:
                if _future.done():
                    try:
                        step_result = _future.result()
                        _log.warning(
                            "[Step%d] ⏰ 超时边界达成，但步骤已实际完成，使用实际结果",
                            step_num,
                        )
                        break
                    except Exception as _fe:
                        _log.warning(
                            "[Step%d] 超时边界达成，步骤已完成但结果异常: %s",
                            step_num,
                            _fe,
                        )
                        step_result = {"success": False, "error": str(_fe)}
                        break
                _log.error(
                    "[Step%d] ##HTML:##<span style='color:#ef4444'>⏰ [超时] 执行超过 %d 秒，强制终止</span>",
                    step_num,
                    _step_timeout,
                )
                _log.error(
                    "[Step%d] %s",
                    step_num,
                    fmt_log("FAIL", "超时（%ds），强制终止" % _step_timeout),
                )
                step_result = {
                    "success": False,
                    "error": "Step %d 执行超时（%d 秒）" % (step_num, _step_timeout),
                }
                with guard("execute_step_with_timeout: from src.backend.state import _activ", log=_log):
                    from src.backend.state import _active_runs, _active_runs_lock

                    with _active_runs_lock:
                        _active_runs.pop(run_id, None)
                    _log.warning("[超时清理] 已从 _active_runs 移除 run_id=%s", run_id)
                break
            try:
                step_result = _future.result(timeout=min(_poll_interval, _remaining))
                break  # 正常完成
            except FutureTimeoutError:
                # 检查停止标志（用户点击"停止"按钮后 0.5s 内即可响应）
                if force_stop_check and force_stop_check():
                    _log.warning(
                        "[Step%d] ##HTML:##<span style='color:#ef4444'>⏹ [STOP] 收到停止信号，立即停止</span>",
                        step_num,
                    )
                    step_result = {
                        "success": False,
                        "error": "已停止（收到停止信号：用户停止或看门狗判挂起）",
                        "force_stopped": True,
                    }
                    with guard("execute_step_with_timeout: from src.backend.state import _activ", log=_log):
                        from src.backend.state import _active_runs, _active_runs_lock

                        with _active_runs_lock:
                            _active_runs.pop(run_id, None)
                    break
    finally:
        # ★ 超时后不等待卡住的工作线程（shutdown(wait=False)），
        # 防止 ThreadPoolExecutor.__exit__ 死等未完成线程导致 UI 状态不能及时复位。
        _executor.shutdown(wait=False)
    return step_result
