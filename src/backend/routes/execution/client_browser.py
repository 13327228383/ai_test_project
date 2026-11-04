# -*- coding: utf-8 -*-
"""
客户端浏览器 API
================
支持用户使用自己的浏览器执行测试（CDP 远程连接模式）。

工作流程：
1. 用户在前端选择"客户端浏览器"模式
2. 下载 .bat 启动脚本（自动启动 Chrome 调试模式）
3. 运行脚本后，服务端通过 CDP 连接客户端 Chrome
4. 测试在客户端浏览器执行，用户可实时观看
"""

import io
import logging
from flask import jsonify, request, send_file

_log = logging.getLogger(__name__)


def _register_client_browser_routes():
    """注册客户端浏览器路由到 exec_bp。延迟导入避免循环依赖。"""
    from src.backend.routes.execution import exec_bp

    @exec_bp.route("/api/client-browser/startup-script", methods=["GET"])
    def api_client_browser_startup_script():
        """生成客户端 Chrome 启动脚本（.bat）。

        用户下载后双击运行，自动启动 Chrome 调试模式。
        脚本会自动添加 Windows 防火墙规则，允许服务端连接。
        """
        # 权限检查：必须登录
        from src.core.config_loader import get_current_user

        if not get_current_user():
            return jsonify({"ok": False, "error": "请先登录"}), 401

        # 从请求中获取服务端地址（供脚本回连）
        server_host = request.host.split(":")[0]
        server_port = request.host.split(":")[1] if ":" in request.host else "5120"

        # 生成 .bat 脚本
        bat_content = _generate_bat_script(server_host, server_port)

        # 返回为可下载文件（UTF-8 with BOM，配合 chcp 65001）
        return send_file(
            io.BytesIO(bat_content.encode("utf-8-sig")),
            as_attachment=True,
            download_name="启动测试浏览器.bat",
            mimetype="application/octet-stream",
        )

    @exec_bp.route("/api/client-browser/check", methods=["POST"])
    def api_client_browser_check():
        """检测客户端浏览器是否已启动调试模式。

        前端轮询此接口，直到检测到客户端 Chrome 调试端口可连接。
        POST body: {"endpoint": "http://192.168.1.100:9222"}
        """
        # 权限检查：必须登录
        from src.core.config_loader import get_current_user

        if not get_current_user():
            return jsonify({"ok": False, "error": "请先登录"}), 401

        import asyncio
        from playwright.async_api import async_playwright

        data = request.get_json(silent=True) or {}
        endpoint = data.get("endpoint", "").strip()
        if not endpoint:
            return jsonify({"ok": False, "error": "缺少 endpoint 参数"}), 400

        # 安全校验：先校验协议，再决定是否补全
        if "://" in endpoint:
            # 用户已带协议前缀，只允许 http://
            if not endpoint.startswith("http://"):
                return (
                    jsonify({"ok": False, "error": "endpoint 必须以 http:// 开头"}),
                    400,
                )
        else:
            # 无协议前缀，自动补全
            endpoint = "http://" + endpoint

        # SSRF 防护：只允许 9222 端口（防止端口扫描）
        from urllib.parse import urlparse

        parsed = urlparse(endpoint)
        if parsed.port and parsed.port != 9222:
            return jsonify({"ok": False, "error": "只允许 9222 端口"}), 400

        pw = None
        browser = None
        result = None
        loop = asyncio.new_event_loop()
        try:

            async def _try_connect():
                nonlocal pw, browser
                pw = await async_playwright().start()
                browser = await pw.chromium.connect_over_cdp(endpoint)
                contexts = browser.contexts
                page_count = sum(len(c.pages) for c in contexts)
                return {"ok": True, "page_count": page_count}

            result = loop.run_until_complete(
                asyncio.wait_for(_try_connect(), timeout=5)
            )
        except asyncio.TimeoutError:
            return (
                jsonify({"ok": False, "error": "连接超时，请确认客户端已运行启动脚本"}),
                504,
            )
        except Exception as e:
            _log.warning("[client-browser] 连接检测失败: %s", e)
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": f"无法连接到 {endpoint}，请确认：1) 客户端已运行 .bat 脚本 2) 防火墙已放行 9222 端口",
                    }
                ),
                502,
            )
        finally:
            # 在事件循环内清理资源（CDP 模式 close() 仅断开连接，不杀进程）
            async def _cleanup():
                try:
                    if browser:
                        await browser.close()
                except Exception as _e:
                    _log.debug("[client_browser] 关闭浏览器异常: %s", _e, exc_info=True)
                try:
                    if pw:
                        await pw.stop()
                except Exception as _e:
                    _log.debug(
                        "[client_browser] 停止 playwright 异常: %s", _e, exc_info=True
                    )

            try:
                loop.run_until_complete(_cleanup())
            except Exception as _e:
                _log.debug("[client_browser] 清理事件循环异常: %s", _e, exc_info=True)
            loop.close()

        if result and result.get("ok"):
            return jsonify(
                {
                    "ok": True,
                    "endpoint": endpoint,
                    "page_count": result.get("page_count", 0),
                    "message": "客户端浏览器已连接",
                }
            )
        return jsonify({"ok": False, "error": "连接失败，未获取到结果"}), 502

    @exec_bp.route("/api/client-browser/detect-ip", methods=["GET"])
    def api_client_browser_detect_ip():
        """检测客户端 IP 地址。

        前端调用此接口获取客户端的 IP，用于生成 CDP endpoint。
        """
        # 权限检查：必须登录
        from src.core.config_loader import get_current_user

        if not get_current_user():
            return jsonify({"ok": False, "error": "请先登录"}), 401

        # 获取真实客户端 IP（处理代理情况）
        client_ip = request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
        if not client_ip:
            client_ip = request.headers.get("X-Real-IP", "").strip()
        if not client_ip:
            client_ip = request.remote_addr or ""

        return jsonify(
            {
                "ok": True,
                "client_ip": client_ip,
                "suggested_endpoint": f"http://{client_ip}:9222" if client_ip else "",
            }
        )


def _generate_bat_script(server_host: str, server_port: str) -> str:
    """生成客户端 Chrome 启动脚本。

    脚本功能：
    1. 查找已安装的 Chrome/Edge
    2. 以调试模式启动（--remote-debugging-port=9222）
    3. 添加 Windows 防火墙规则
    4. 提示用户浏览器已就绪
    """
    return rf"""@echo off
chcp 65001 >nul
title E2E 测试浏览器 - 调试模式
echo ========================================
echo   E2E 测试浏览器启动脚本
echo ========================================
echo.

REM ★ 查找已安装的 Chrome 或 Edge
set "BROWSER_PATH="

REM 优先查找 Chrome
if exist "C:\Program Files\Google\Chrome\Application\chrome.exe" (
    set "BROWSER_PATH=C:\Program Files\Google\Chrome\Application\chrome.exe"
)
if exist "C:\Program Files (x86)\Google\Chrome\Application\chrome.exe" (
    set "BROWSER_PATH=C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
)

REM 回退到 Edge
if not defined BROWSER_PATH (
    if exist "C:\Program Files\Microsoft\Edge\Application\msedge.exe" (
        set "BROWSER_PATH=C:\Program Files\Microsoft\Edge\Application\msedge.exe"
        echo [INFO] 未找到 Chrome，使用 Edge 浏览器
    )
)

if not defined BROWSER_PATH (
    echo [ERROR] 未找到 Chrome 或 Edge，请先安装 Chrome 浏览器
    pause
    exit /b 1
)

echo [INFO] 使用浏览器: %BROWSER_PATH%

REM ★ 添加防火墙规则（允许 9222 端口入站）
echo [INFO] 添加防火墙规则...
netsh advfirewall firewall add rule name="E2E_Test_Browser" dir=in action=allow protocol=TCP localport=9222 >nul 2>&1

REM ★ 启动浏览器调试模式
echo [INFO] 启动浏览器（调试模式，端口 9222）...
start "" "%BROWSER_PATH%" --remote-debugging-port=9222 --remote-debugging-address=0.0.0.0 --user-data-dir="%TEMP%\e2e_test_profile" --no-first-run --no-default-browser-check

echo.
echo ========================================
echo   浏览器已启动！
echo ========================================
echo.
echo 请勿关闭此窗口和浏览器窗口。
echo 服务端将自动连接到您的浏览器执行测试。
echo.
echo 服务端地址: {server_host}:{server_port}
echo 调试端口: 9222
echo.
echo 按任意键退出（浏览器将保持运行）...
pause >nul
"""


# ★ 模块加载时自动注册路由
_register_client_browser_routes()
