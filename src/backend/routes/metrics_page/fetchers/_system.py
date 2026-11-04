# -*- coding: utf-8 -*-
"""
系统运行时获取器（psutil 进程/内存/CPU）
"""

import logging
import sys
import time

_log = logging.getLogger(__name__)


def _fetch_system_metrics() -> dict:
    try:
        import psutil
        import platform as _pf

        proc = psutil.Process()
        mem = proc.memory_info()
        try:
            cpu_percent = proc.cpu_percent(interval=0.1)
        except Exception:
            cpu_percent = None

        # ★ sys.platform 在所有 Windows 上都返回 "win32"（含 64 位），
        #   用 platform.platform() 显示可读版本号
        _plat = _pf.platform()
        return {
            "pid": proc.pid,
            "process_rss_mb": round(mem.rss / 1024 / 1024, 1),
            "process_vms_mb": round(mem.vms / 1024 / 1024, 1),
            "cpu_percent": cpu_percent if cpu_percent is not None else None,
            "thread_count": proc.num_threads(),
            "python_version": sys.version.split()[0],
            "platform": _plat,
            "uptime_seconds": int(time.time() - proc.create_time()),
        }
    except Exception as e:
        return {"note": f"psutil 异常: {e}"}
