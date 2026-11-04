# -*- coding: utf-8 -*-
from __future__ import annotations

"""
本地浏览器自动检测器 (BrowserDetector)
======================================
从 Windows 注册表和常用安装路径自动发现本地浏览器，
返回统一的结构化列表，供 GUI 下拉选择和 Playwright 引擎使用。

支持的浏览器类型：Edge / Chrome / Firefox / Opera / QQ / Brave
"""

import os, subprocess
import logging
from pathlib import Path

_log = logging.getLogger(__name__)

_REGISTRY_KEYS: dict[str, str] = {
    "chrome": r"chrome.exe",
    "firefox": r"firefox.exe",
    "edge": r"msedge.exe",
    "opera": r"opera.exe",
    "qqbrowser": r"QQBrowser.exe",
    "brave": r"brave.exe",
}

_FALLBACK_PATHS: dict[str, list[str]] = {
    "edge": [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ],
    "chrome": [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ],
    "firefox": [
        r"C:\Program Files\Mozilla Firefox\firefox.exe",
        r"C:\Program Files (x86)\Mozilla Firefox\firefox.exe",
    ],
    "opera": [
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\Opera\opera.exe"),
        r"C:\Program Files\Opera\opera.exe",
    ],
    "qqbrowser": [
        r"C:\Program Files (x86)\Tencent\QQBrowser\QQBrowser.exe",
        r"C:\Program Files\Tencent\QQBrowser\QQBrowser.exe",
    ],
    "brave": [
        os.path.expandvars(
            r"%LOCALAPPDATA%\BraveSoftware\Brave-Browser\Application\brave.exe"
        ),
        r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
    ],
}

_BROWSER_META: dict[str, dict[str, str]] = {
    "edge": {"name": "Microsoft Edge", "engine": "chromium", "channel": "msedge"},
    "chrome": {"name": "Google Chrome", "engine": "chromium", "channel": "chrome"},
    "firefox": {"name": "Mozilla Firefox", "engine": "firefox", "channel": ""},
    "opera": {"name": "Opera", "engine": "chromium", "channel": ""},
    "qqbrowser": {"name": "QQ 浏览器", "engine": "chromium", "channel": ""},
    "brave": {"name": "Brave", "engine": "chromium", "channel": ""},
}


def _query_registry_path(exe_name: str) -> str | None:
    reg_roots = [
        r"HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths",
        r"HKLM\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths",
        r"HKCU\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths",
    ]
    for root in reg_roots:
        key = rf"{root}\{exe_name}"
        try:
            result = subprocess.run(
                ["reg", "query", key, "/ve"], capture_output=True, text=True, timeout=3
            )
            for line in result.stdout.splitlines():
                line = line.strip()
                if ".exe" in line.lower():
                    parts = line.split("REG_SZ")
                    path = parts[-1].strip() if len(parts) > 1 else line
                    path = path.strip()
                    if Path(path).is_file():
                        return str(Path(path))
        except Exception as e:
            _log.debug("注册表查询异常: %s", e, exc_info=True)
            continue
    return None


def _check_fallback_paths(browser_id: str) -> str | None:
    for p in _FALLBACK_PATHS.get(browser_id, []):
        expanded = os.path.expandvars(p)
        if Path(expanded).is_file():
            return str(Path(expanded))
    return None


def detect_browsers() -> list[dict[str, str]]:
    detected: list[dict[str, str]] = []
    for browser_id, exe_name in _REGISTRY_KEYS.items():
        exe_path = _query_registry_path(exe_name)
        if not exe_path:
            exe_path = _check_fallback_paths(browser_id)
        if exe_path and Path(exe_path).is_file():
            meta = _BROWSER_META.get(browser_id, {})
            detected.append(
                {
                    "id": browser_id,
                    "name": meta.get("name", browser_id.title()),
                    "path": exe_path,
                    "engine": meta.get("engine", "chromium"),
                    "channel": meta.get("channel", ""),
                }
            )

    def _sort_key(item):
        order = {
            "edge": 0,
            "chrome": 1,
            "firefox": 2,
            "opera": 3,
            "qqbrowser": 4,
            "brave": 5,
        }
        return order.get(item["id"], 99)

    detected.sort(key=_sort_key)
    return detected


_cached_browsers: list[dict[str, str]] | None = None


def get_installed_browsers(force_refresh: bool = False) -> list[dict[str, str]]:
    global _cached_browsers
    if _cached_browsers is None or force_refresh:
        _cached_browsers = detect_browsers()
    return _cached_browsers
