# -*- coding: utf-8 -*-
from __future__ import annotations

"""
全局 MCP 会话管理（进程级单例）
"""

import threading
import logging

_log = logging.getLogger(__name__)

from .mcp_stdio import MCPStdioClient


_mcp_session: MCPStdioClient | None = None
_session_lock = threading.Lock()  # ★ 只保护 _mcp_session/_session_token 的读写（短持锁）
_start_lock = threading.Lock()    # ★ 串行化真正的子进程启动 + 握手（阻塞，锁外执行）
_session_token: str = ""  # ★ 记录创建会话时使用的 token，用于检测 token 变更

# ★ 启动握手超时（秒）：figma-developer-mcp 若在环境下拉起后无响应，initialize 最长等
#   _STARTUP_TIMEOUT 秒即降级，避免把整条流水线/CLI 入口卡死（原全局锁内最长等 120s 且不可中断）。
_STARTUP_TIMEOUT: float = 30.0


def _build_env(token: str) -> dict:
    """构造 MCP 子进程环境（统一封装，避免主函数内重复）。"""
    return {
        "FIGMA_API_KEY": token or "",
        "NO_PROXY": "api.figma.com",
        "no_proxy": "api.figma.com",
    }


def start_mcp_server(token: str = None):
    """
    启动 MCP Server 后台进程。线程安全，重复调用无副作用，且绝不无限阻塞。

    参数:
        token: Figma PAT (figd_...)。不传则从 config 读取。

    ★ 根因修复（2026-08-24）：原实现把「子进程启动 + initialize 握手」整体包在
      _session_lock 全局锁内，握手最长阻塞 120s 且 stop_mcp_server 也在 finally
      里抢同一把锁 → 外部依赖不可用时整条流水线/`main.py --status` 无限卡死。
      现改为：锁只保护会话引用的读写（短持锁），实际阻塞拉起的部分放在锁外执行，
      并用 _start_lock 保证并发只拉起一个进程。未配置 token / 拉起失败 / 握手超时
      都会快速返回并降级（不抛异常），让流水线改用本地设计文档等数据源。
    """
    global _mcp_session, _session_token
    if not token:
        from src.core.config_loader import config

        token = config.figma_key

    # —— 快速路径：已有存活会话（短持锁判断，不阻塞第三方 stop）——
    with _session_lock:
        if _mcp_session is not None and _mcp_session.is_alive():
            # token 变了（且新 token 非空）→ 需要重建，交由下方慢路径
            if token and token != _session_token:
                _log.info("  [MCP] Token 已变更，重启 MCP Server...")
                _mcp_session.stop()
                _mcp_session = None
                _session_token = ""
            else:
                _log.info("  [MCP] Server 已在运行，跳过启动")
                return

    if not token:
        _log.info("  [MCP] Figma token 未配置，跳过 MCP 启动")
        return

    # —— 慢路径：真正启动（阻塞，放在全局会话锁之外，保证 stop 随时可拿锁）——
    # 串行化，避免 N 个调用方同时拉起 N 个子进程
    with _start_lock:
        # 双重检查：可能慢路径等待期间别的调用已拉起会话
        with _session_lock:
            if _mcp_session is not None and not _mcp_session.is_alive():
                _mcp_session = None
            if _mcp_session is not None and _mcp_session.is_alive():
                _log.info("  [MCP] Server 已在运行（并发启动，跳过）")
                _session_token = token or ""
                return

        # ★ --format json：figma-developer-mcp v0.13.2 默认返回 YAML/tree 格式，
        # FigmaMCPClient._parse_raw_text 不兼容新格式会解析失败，且 _detect_api_error
        # 会在正常数据（节点哈希含 "403" 等子串）上误判为伪 403。改用 JSON 输出，
        # 其 nodes (list of {id,name,type,children}) 直接匹配下游 document.children。
        # ★ 版本回退：--format json 是 v0.13+ 参数，旧版不识别会立即退出。
        # 先尝试带 --format json 启动，握手失败则回退纯 --stdio（旧版返回
        # 旧 simplified 格式，FigmaMCPClient._parse_raw_text 兼容）。
        env = _build_env(token)
        session, handshake_ok = _try_start_session(["--stdio", "--format", "json"], env)
        if session is None or not handshake_ok:
            if session is not None:
                _log.warning(
                    "  [MCP] --format json 不受支持（旧版 figma-developer-mcp），回退纯 --stdio"
                )
                session.stop()
            session, _ = _try_start_session(["--stdio"], env)

        # 写回快照（短持锁）
        with _session_lock:
            _mcp_session = session
            _session_token = token or ""


def _try_start_session(args, env):
    """启动一次 MCP Server 子进程并尝试握手。

    返回 (session, handshake_ok)：
    - session: MCPStdioClient 实例（启动失败则为 None）
    - handshake_ok: initialize 握手是否成功
    旧版 figma-developer-mcp 不识别 --format 参数时子进程会立即退出，握手会
    抛 BrokenPipe/断开异常 → handshake_ok=False，调用方据以回退纯 --stdio。
    """
    session = MCPStdioClient(
        command="figma-developer-mcp",
        args=args,
        env=env,
        timeout=_STARTUP_TIMEOUT,  # ★ 启动握手超时（默认 30s），避免无响应子进程拖垮流水线
    )
    try:
        session.start()
    except Exception as e:
        _log.warning("  [MCP] 启动失败 (args=%s): %s", args, e)
        return None, False
    if not session.is_alive():
        _log.warning("  [MCP] 子进程启动后立即退出 (args=%s)", args)
        return None, False
    try:
        info = session.initialize()
        _log.info(
            "  [MCP] 握手完成 (server: %s v%s)"
            % (
                info.get("serverInfo", {}).get("name", "?"),
                info.get("serverInfo", {}).get("version", "?"),
            )
        )
        return session, True
    except Exception as e:
        _log.warning("  [MCP] 握手失败 (args=%s): %s", args, e)
        return session, False


def stop_mcp_server():
    """停止 MCP Server 后台进程。"""
    global _mcp_session
    with _session_lock:
        if _mcp_session is not None:
            _mcp_session.stop()
            _mcp_session = None


def get_mcp_session() -> MCPStdioClient:
    """获取当前 MCP 会话（未运行则自动启动）。"""
    global _mcp_session
    if _mcp_session is None or not _mcp_session.is_alive():
        start_mcp_server()
    return _mcp_session
