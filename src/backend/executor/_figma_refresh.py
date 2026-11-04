# -*- coding: utf-8 -*-
"""
_figma_refresh_task —— 在后台线程中刷新 Figma 缓存。
从原 executor.py 提取，逻辑保持不变。
"""

from pathlib import Path
import time
import logging

_log = logging.getLogger(__name__)

ROOT = (
    Path(__file__).resolve().parent.parent.parent.parent
)  # src/backend/executor/../../../ = 项目根

from src.backend.state import (
    _current_project,
    _thread_run_id,
    _set_running,
    _set_force_stop,
    _set_paused_step,
    _get_paused_step,
    _active_runs,
    _active_runs_lock,
    _run_progress,
    _run_progress_lock,
    _run_sse_queues,
    _run_sse_queues_lock,
    _update_progress,
)
from src.backend.helpers import _confirm_figma_fetch


def _figma_refresh_task(project: str = ""):
    """在后台线程中刷新 Figma 缓存。"""
    import uuid

    _log.info("##HTML:##<span style='color:#60a5fa'>🔄 Figma 刷新任务已触发...</span>")
    if not _confirm_figma_fetch():
        return
    run_id = "figma-" + uuid.uuid4().hex[:8]
    _thread_run_id.value = run_id
    # 注册到 active_runs 以便前端轮询
    with _active_runs_lock:
        _active_runs[run_id] = {
            "thread": None,
            "force_stop": False,
            "step": 0,
            "project": project or _current_project(),
            "start_ts": time.time(),
        }
    try:
        _log.info("⏳ Figma: 正在启动 MCP 并拉取设计稿（95MB 大文件可能需要数十秒）...")
        from src.pipeline.steps.step1.figma_client import figma_client

        r = figma_client.fetch_figma_file(force_refresh=True)
        if r.get("success"):
            name = r.get("data", {}).get("name", "")
            _log.info(
                f"##HTML:##<span style='color:#22c55e'>✓ Figma 缓存刷新成功: {name}</span>"
            )
        else:
            _log.error(
                f"##HTML:##<span style='color:#ef4444'>✗ Figma 缓存刷新失败: {r.get('error', '')}</span>"
            )
    except Exception as e:
        import traceback

        _log.info(f"##HTML:##<span style='color:#ef4444'>✗ Figma 异常: {e}</span>")
        traceback.print_exc()
    finally:
        _thread_run_id.value = None
        with _active_runs_lock:
            _active_runs.pop(run_id, None)
