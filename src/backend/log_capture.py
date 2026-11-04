# -*- coding: utf-8 -*-

"""

日志捕获与 SSE 广播模块

========================

从 app.py 提取：LogCapture、_broadcast_log、install_log_capture。

"""


import sys

import io

import json

import queue

import re

import threading
import logging

from datetime import datetime

_log = logging.getLogger(__name__)


# ★ Werkzeug HTTP 访问日志正则（用于过滤重复的 API 轮询日志）

_WERKZEUG_ACCESS_LOG_RE = re.compile(
    r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\s+-\s+-"
    r'\s+\[.*\]\s+"(GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS)\s+/\S*\s+HTTP/[\d.]+"\s+\d{3}'
)


# ANSI 转义码正则（终端颜色码，如 \x1b[38;5;148m）

_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")


def _strip_ansi(text: str) -> str:
    """剥离 ANSI 终端颜色转义码，避免干扰日志过滤正则。"""

    return _ANSI_ESCAPE_RE.sub("", text)


def _is_werkzeug_access_log(text: str) -> bool:
    """检测是否为 Werkzeug/Flask HTTP 访问日志行。

    格式: 192.168.x.x - - [date] "GET /api/xxx HTTP/1.1" 200 -

    支持带有 ANSI 颜色码的日志行。

    """

    clean = _strip_ansi(text.strip())

    return bool(_WERKZEUG_ACCESS_LOG_RE.match(clean))


from src.backend.state import (
    _all_log_lines,
    _all_log_lines_lock,
    _sse_queues,
    _sse_queues_lock,
    _thread_run_id,
    _run_sse_queues,
    _run_sse_queues_lock,
    _run_logs,
    _run_logs_lock,
)

import logging

_log = logging.getLogger(__name__)


def _classify_sse_priority(text: str) -> str:
    """★ KEEP-29: SSE 日志优先级分级。"""

    t = (text or "").lower()

    if any(
        k in t for k in ("error", "exception", "失败", "异常", "traceback", "critical")
    ):

        return "high"

    if any(
        k in t for k in ("warning", "warn", "警告", "完成", "success", "passed", "通过")
    ):

        return "normal"

    return "low"


def _put_sse_payload(q, payload: str, priority: str) -> bool:
    """按优先级写入 SSE 队列。高/中优先级在队列满时尝试挤掉旧消息。"""

    try:

        q.put_nowait(payload)

        return True

    except queue.Full:

        if priority in ("high", "normal"):

            try:

                q.get_nowait()

                q.put_nowait(payload)

                return True

            except Exception as e:
                _log.warning("日志捕获队列写入异常: %s", e, exc_info=True)
                return False

        return False


def _now_ts() -> str:
    """统一日志时间戳格式: YYYY-MM-DD HH:MM:SS.mmm (3 位毫秒)。"""
    from datetime import datetime

    return datetime.now().strftime("%Y-%m-%d %H:%M:%S.") + "%03d" % (
        datetime.now().microsecond // 1000
    )


def _send_to_per_run_queue(rid, text, priority, step, level, ts):
    """将日志发送到指定 run_id 的 per-run 队列。"""
    payload = json.dumps(
        {"log": text, "priority": priority, "step": step, "level": level, "ts": ts}
    )
    dead = []
    with _run_sse_queues_lock:
        for q in _run_sse_queues.get(rid, []):
            if not _put_sse_payload(q, payload, priority):
                dead.append(q)
    if dead:
        with _run_sse_queues_lock:
            for q in dead:
                try:
                    _run_sse_queues.get(rid, []).remove(q)
                except (ValueError, KeyError) as _e:
                    _log.debug(
                        "[log_capture] 移除失效 per-run 队列异常: %s", _e, exc_info=True
                    )


def _send_to_all_per_run_queues(text, priority, step, level, ts):
    """将日志发送到所有活跃的 per-run 队列（用于非执行线程产生的日志）。"""
    payload = json.dumps(
        {"log": text, "priority": priority, "step": step, "level": level, "ts": ts}
    )
    dead = []
    with _run_sse_queues_lock:
        for rid_key, qs in list(_run_sse_queues.items()):
            for q in qs:
                if not _put_sse_payload(q, payload, priority):
                    dead.append((rid_key, q))
    if dead:
        with _run_sse_queues_lock:
            for rid_key, q in dead:
                try:
                    _run_sse_queues[rid_key].remove(q)
                except (ValueError, KeyError) as _e:
                    _log.debug(
                        "[log_capture] 移除失效 per-run 队列异常: %s", _e, exc_info=True
                    )


def _broadcast_log(text: str, run_id: str = None, step: str = "", level: str = ""):
    """向 SSE 订阅者广播日志行。
    - 始终发到全局队列（附带 run_id 元数据供前端按 Tab 分发）
    - 若有 run_id，发到该 run 的专属队列；若无，则发到所有活跃的 per-run 队列
    ★ per-run payload 必须与 global payload 字段一致（含 step/level/ts），
      否则前端订阅 /api/log-stream?run_id=xxx 时会丢失时间戳和级别。
    """
    # 1. 全局广播（始终执行，带 run_id 元数据）
    rid = run_id or getattr(_thread_run_id, "value", None)
    priority = _classify_sse_priority(text)
    ts = _now_ts()
    payload_global = json.dumps(
        {
            "log": text,
            "run_id": rid,
            "priority": priority,
            "step": step,
            "level": level,
            "ts": ts,
        }
        if rid
        else {"log": text, "priority": priority, "step": step, "level": level, "ts": ts}
    )

    dead = []

    with _sse_queues_lock:

        for q in _sse_queues:

            if not _put_sse_payload(q, payload_global, priority):

                dead.append(q)

        for q in dead:

            try:

                _sse_queues.remove(q)

            except ValueError as _e:

                _log.debug("[log_capture] 移除失效 SSE 队列异常: %s", _e, exc_info=True)

    # 2. 专属队列（同时发送到 per-run 队列，rid 为空时也发送，确保非执行线程日志可达）
    # ★ payload 字段与 global 一致（含 step/level/ts），避免前端订阅 per-run 队列时丢字段

    if rid:
        _send_to_per_run_queue(rid, text, priority, step, level, ts)
    else:
        # ★ rid 为 None 时（如 metrics-perf、后台任务日志），也发到所有活跃的 per-run 队列
        _send_to_all_per_run_queues(text, priority, step, level, ts)


# ★ 持久化日志文件路径（线程局部，避免跨线程污染）

_current_log_file = threading.local()


# ═══════════════════════════════════════════════════════════════

# LogCapture — 拦截 stdout 并广播到 SSE

# ═══════════════════════════════════════════════════════════════


class LogCapture(io.StringIO):
    """替换 sys.stdout，捕获 print() 输出到日志列表 + SSE 广播。"""

    def __init__(self):

        super().__init__()

        self._lock = threading.Lock()

    def write(self, s):

        with self._lock:

            super().write(s)

        if s.strip():

            # ★ 过滤 Flask/Werkzeug 启动横幅消息，避免污染前端日志框

            stripped = s.strip()

            if self._is_banner(stripped):

                return len(s)

            # ★ 过滤 Werkzeug HTTP 访问日志（前端轮询产生的噪音）

            if _is_werkzeug_access_log(stripped):

                return len(s)

            ts = _now_ts()

            # ★ 从 _current_log_file 读取当前 step（源头标记，不再猜测）
            _logf = getattr(_current_log_file, "value", None)
            if _logf and isinstance(_logf, dict) and _logf.get("step_label"):
                _step = _logf["step_label"].lower()
            else:
                _step = "system"
            # level 仍然从文本推断（error/warn/info/debug）
            _level = _parse_log_level(s)

            with _all_log_lines_lock:

                _all_log_lines.append(
                    {"ts": ts, "text": s, "step": _step, "level": _level}
                )

                if len(_all_log_lines) > 1000:

                    _all_log_lines[:] = _all_log_lines[-1000:]
            # 路由：发到全局 + 专属队列（_broadcast_log 内部处理）

            rid = getattr(_thread_run_id, "value", None)

            if rid:

                # 存入 per-run 日志历史

                with _run_logs_lock:

                    if rid not in _run_logs:

                        _run_logs[rid] = []

                    _run_logs[rid].append(
                        {"ts": ts, "text": s, "step": _step, "level": _level}
                    )

                    if len(_run_logs[rid]) > 500:

                        _run_logs[rid][:] = _run_logs[rid][-500:]

            _broadcast_log(s, run_id=rid, step=_step, level=_level)

            # ★ 持久化：实时写入日志文件（线程局部）
            _logf = getattr(_current_log_file, "value", None)
            if _logf:
                from src.backend.log_persistence import append_run_log

                append_run_log(_logf, s)
            elif rid:
                from src.backend.log_persistence import append_run_log

                temp_logf = {"run_id": rid, "step_label": _step}
                append_run_log(temp_logf, s)
            else:
                from src.backend.log_persistence import append_run_log

                with _run_sse_queues_lock:
                    active_rids = list(_run_sse_queues.keys())
                for _rid in active_rids:
                    temp_logf = {"run_id": _rid, "step_label": _step}
                    append_run_log(temp_logf, s)

        return len(s)

    def flush(self):

        pass

    @staticmethod
    def _is_banner(text: str) -> bool:
        """过滤 Flask/Werkzeug 启动横幅消息，避免污染前端日志框。"""

        _BANNER_PREFIXES = (
            "* Serving Flask app",
            "* Debug mode:",
            "* Running on",
            "Press CTRL+C to quit",
            "WARNING: This is a development server",
            "WARNING: Do not use it in a production",
            "* Restarting with",
            "* Detected change in",
            "* Debugger is active!",
            "* Debugger PIN:",
        )

        return any(text.startswith(p) for p in _BANNER_PREFIXES)


def install_log_capture():

    if not isinstance(sys.stdout, LogCapture):

        sys.stdout = LogCapture()

    # ★ 同时桥接 logging 模块输出到 SSE（logging 默认输出到 stderr，不会被 stdout 捕获）

    _install_logging_bridge()


def _install_logging_bridge():
    """为 root logger 添加一个 Handler，将日志桥接到 SSE 广播。

    仅安装一次（通过 _logging_bridge_installed 标记防止重复）。

    """

    if getattr(_install_logging_bridge, "_installed", False):

        return

    import logging

    class _SSELogHandler(logging.Handler):
        """将 logging 输出桥接到 LogCapture / SSE 广播。"""

        def emit(self, record):

            try:

                msg = self.format(record)

                # ★ 过滤 Werkzeug HTTP 访问日志

                if msg.strip() and _is_werkzeug_access_log(msg.strip()):

                    return

                # 复用 LogCapture.write 的逻辑（但不经过 stdout）

                if msg.strip():

                    ts = _now_ts()

                    line = msg + "\n"

                    rid = getattr(_thread_run_id, "value", None)

                    # ★ 从 _current_log_file 读取当前 step（源头标记，不再猜测）
                    _logf = getattr(_current_log_file, "value", None)
                    if _logf and isinstance(_logf, dict) and _logf.get("step_label"):
                        _step2 = _logf["step_label"].lower()
                    else:
                        _step2 = "system"
                    # level 仍然从文本推断（error/warn/info/debug）
                    _level2 = _parse_log_level(line)

                    with _all_log_lines_lock:

                        _all_log_lines.append(
                            {"ts": ts, "text": line, "step": _step2, "level": _level2}
                        )

                        if len(_all_log_lines) > 1000:

                            _all_log_lines[:] = _all_log_lines[-1000:]

                    if rid:

                        with _run_logs_lock:

                            if rid not in _run_logs:

                                _run_logs[rid] = []

                            _run_logs[rid].append(
                                {
                                    "ts": ts,
                                    "text": line,
                                    "step": _step2,
                                    "level": _level2,
                                }
                            )

                            if len(_run_logs[rid]) > 500:

                                _run_logs[rid][:] = _run_logs[rid][-500:]

                    # ★ 持久化到 MySQL：logging 输出此前只走内存，现与 stdout 一样入库
                    _logf = getattr(_current_log_file, "value", None)
                    if _logf:
                        from src.backend.log_persistence import append_run_log

                        append_run_log(_logf, line)
                    elif rid:
                        # ★ 非执行线程日志（_logf 为 None 但有 rid）：使用 rid 构造临时 file_path 持久化
                        from src.backend.log_persistence import append_run_log

                        temp_logf = {"run_id": rid, "step_label": _step2}
                        append_run_log(temp_logf, line)
                    else:
                        # ★ rid 也为 None 时（如 metrics-perf）：从活跃 per-run 队列获取 run_id 持久化
                        from src.backend.log_persistence import append_run_log

                        with _run_sse_queues_lock:
                            active_rids = list(_run_sse_queues.keys())
                        for _rid in active_rids:
                            temp_logf = {"run_id": _rid, "step_label": _step2}
                            append_run_log(temp_logf, line)

                    _broadcast_log(line, run_id=rid, step=_step2, level=_level2)

            except Exception as _e:
                _log.debug("[SSE] 日志广播失败: %s", _e, exc_info=True)

    handler = _SSELogHandler()

    handler.setLevel(logging.INFO)

    # 简洁格式：时间 级别 消息

    handler.setFormatter(logging.Formatter("%(message)s"))

    # 避免日志循环（此 handler 的广播不再触发其他 handler）

    logging.getLogger().addHandler(handler)

    # ★ 确保 src.* 模块的 INFO 日志能传播到 root handler（Python 默认 root 为 WARNING）

    logging.getLogger("src").setLevel(logging.INFO)

    _install_logging_bridge._installed = True


def _parse_log_level(text: str) -> str:
    """从日志文本解析 level（ERROR/WARN/INFO/DEBUG）。
    step 信息已从 _current_log_file 源头标记，不再靠关键词猜测。"""
    if not text:
        return "INFO"
    _text_upper = text[:200].upper()
    for _kw in ["ERROR", "CRITICAL", "FATAL"]:
        if _kw in _text_upper:
            return "ERROR"
    if "WARNING" in _text_upper or "WARN" in _text_upper:
        return "WARN"
    if "DEBUG" in _text_upper:
        return "DEBUG"
    return "INFO"
