# -*- coding: utf-8 -*-
"""浏览器依赖就绪检测（轻量模块，import 时不加载 playwright）。

用于 HTTP 层 run-trigger 端点的 pre-flight 检查：在真正 spawn 后台线程 /
启动浏览器之前，快速判断「浏览器自动化依赖是否就绪」。缺依赖时立即返回明确
错误（503），**绝不阻塞服务线程**，避免一个卡住的浏览器 launch 拖垮全局。

[已复核-DeepSeek-V3 2026-08-16]
"""

from __future__ import annotations

import os
from pathlib import Path

# 项目根：本文件位于 src/pipeline/steps/step4/playwright_engine/
#   parent*5 = playwright_engine -> step4 -> steps -> pipeline -> src -> 项目根
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent.parent


def is_playwright_installed() -> bool:
    """Playwright Python 包是否已安装（不实际加载 playwright 模块）。

    仅用 importlib 探测 spec，零副作用、毫秒级，可在每个 run-trigger 请求中安全调用。
    """
    try:
        import importlib.util

        return importlib.util.find_spec("playwright") is not None
    except Exception:
        return False


def _project_bundled_chrome_exists() -> bool:
    """项目自带的 chrome-win64/chrome.exe 是否存在。"""
    chrome_path = _PROJECT_ROOT / "chrome-win64" / "chrome.exe"
    return chrome_path.is_file()


def _ms_playwright_chromium_exists() -> bool:
    """ms-playwright 缓存目录中是否存在已安装的 Chromium 可执行文件。"""
    local_appdata = os.environ.get("LOCALAPPDATA", "")
    if not local_appdata:
        return False
    ms_playwright = Path(local_appdata) / "ms-playwright"
    if not ms_playwright.is_dir():
        return False
    for d in ms_playwright.glob("chromium-*"):
        if (d / "chrome-win64" / "chrome.exe").is_file():
            return True
    return False


def _system_browser_exists() -> bool:
    """系统是否安装了可被 Playwright channel 复用的浏览器（Chrome/Edge 等）。"""
    try:
        from src.ai.browser_detector import get_installed_browsers

        return bool(get_installed_browsers())
    except Exception:
        return False


def is_browser_binary_available() -> bool:
    """是否存在任一可用的浏览器二进制（Playwright 内置 Chromium 或系统浏览器）。"""
    return (
        _project_bundled_chrome_exists()
        or _ms_playwright_chromium_exists()
        or _system_browser_exists()
    )


def check_browser_readiness() -> tuple[bool, str]:
    """检测浏览器自动化依赖是否就绪。

    Returns:
        (ready, reason):
        - ready=True  → 依赖就绪，reason 为空串。
        - ready=False → 缺依赖，reason 为可直展示给用户的中文说明。
    """
    if not is_playwright_installed():
        return (
            False,
            "未安装浏览器自动化依赖（Playwright Python 包缺失），"
            "无法执行涉及浏览器的步骤（Step 1/4）。请先 `pip install playwright` 并安装浏览器。",
        )
    if not is_browser_binary_available():
        return (
            False,
            "未安装浏览器（Chromium/Chrome/Edge 缺失）。"
            "请先执行 `playwright install chromium`（或安装 Chrome/Edge 后重试），否则无法启动浏览器自动化。",
        )
    return True, ""
