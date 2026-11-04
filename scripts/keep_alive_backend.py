# -*- coding: utf-8 -*-
"""
后端保活守护（Windows）。
监控本地 8700 端口；若后端进程意外退出/端口不可达，则自动重新拉起，
避免出现「无法访问，请启动服务器」的停摆状态。

用法：
    python scripts/keep_alive_backend.py      # 前台运行（Ctrl+C 退出）
    # 后台常驻： Start-Process python -ArgumentList 'scripts/keep_alive_backend.py'

说明：每次拉起后端即可重新加载 Flask 模板；本守护是最后一道兜底，
      保证服务始终可用，而非替代有目的的重启。
"""
import os
import socket
import subprocess
import sys
import time
import atexit

PORT = 8700
PROBE_TIMEOUT = 0.3
RESTART_DELAY = 3.0      # 每次探测间隔
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND = os.path.join(PROJECT_ROOT, "src", "backend", "app.py")


def is_up(port=PORT):
    """端口能否建立连接，判断服务是否可用。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(PROBE_TIMEOUT)
    try:
        s.connect(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        s.close()


proc = None
stopped = False


def _stop():
    global stopped
    stopped = True
    if proc is not None and proc.poll() is None:
        proc.terminate()


atexit.register(_stop)


def _spawn():
    global proc
    # 清理可能残留的同端口旧服务，避免重复监听失败
    try:
        subprocess.run(["taskkill", "/F", "/FI", "WINDOWTITLE eq E2E_Backend_*"],
                       capture_output=True, timeout=5)
    except Exception:
        pass
    proc = subprocess.Popen([sys.executable, "-u", BACKEND],
                            cwd=PROJECT_ROOT,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return proc


def main():
    global proc, stopped
    print(f"[keep-alive] 监控 127.0.0.1:{PORT}，后端：{BACKEND}")
    if is_up():
        print("[keep-alive] 后端已在运行，进入监视模式（崩溃自动拉起）")
    else:
        print("[keep-alive] 后端未运行，正在启动…")
        _spawn()
    while not stopped:
        if not is_up():
            # 进程崩了或端口不可达：拉起/重启
            if proc is not None and proc.poll() is not None:
                print("[keep-alive] 检测到后端退出，正在重启…")
            elif proc is None:
                print("[keep-alive] 后端未运行，正在启动…")
            _spawn()
        time.sleep(RESTART_DELAY)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        _stop()