# -*- coding: utf-8 -*-
from __future__ import annotations

"""浏览器后端发现器（BrowserBackendResolver）。

★ 设计前提（环境硬约束，见 docs/system_design.md Part A §0）：
    沙箱环境下 Playwright 自带 / 自动下载的 chromium **无法安装**（网络/权限墙），
    默认跑内置 chromium 会在 `chromium.launch` 阶段卡死或失败。

★ 核心策略顺序（**硬约束**，任何修改不得调换优先级）：
    1. 系统已装 Chrome/Edge channel 优先：用 `channel="chrome"` / `channel="msedge"`
       启动（headless）。这是生产/CI 中"打活靶子"唯一可靠路径。
    2. executable_path：若环境变量/配置显式指定了可执行文件路径且文件存在 → 使用。
    3. Playwright 内置 chromium：**仅最后回退**（沿用原 lifecycle 的
       `ms-playwright/chromium-*` 探测逻辑）。
    4. 全失败必须明确抛 `RuntimeError("no usable browser backend: ...")`，
       绝不静默挂起或假成功（绝不让报告"变绿"）。

★ 依赖方向：resolver **不依赖** lifecycle；lifecycle 单向依赖 resolver。
   这样避免任何循环导入风险。
"""

import os
import shutil
from pathlib import Path

import logging

_log = logging.getLogger(__name__)


class BrowserBackendResolver:
    """按优先级解析可用浏览器启动方式，返回启动参数字典。

    典型返回结构::

        {
            "kind": "system_channel" | "explicit_path" | "playwright_builtin",
            "channel": "chrome" | "msedge" | None,
            "executable_path": "C:/.../chrome.exe" | None,
            "launch_kwargs": {"headless": True, "args": ["--start-maximized"]},
        }

    - ``kind="system_channel"``：通过系统安装 Chrome/Edge 的 channel 启动。
    - ``kind="explicit_path"``：使用显式指定的 executable_path。
    - ``kind="playwright_builtin"``：回退到 Playwright 内置（最后手段）。
    """

    # ★ 默认启动参数：headless 默认 True（CI/无人值守场景），
    #   调用方可通过 resolve 返回的 launch_kwargs 覆盖。
    DEFAULT_LAUNCH_KWARGS: dict = {
        "headless": True,
        "args": ["--start-maximized"],
    }

    # ★ 系统浏览器探测候选（Windows 优先，跨平台兼容）。
    #   顺序即探测优先级：Chrome 先于 Edge。
    SYSTEM_CHANNELS: list[dict] = [
        {
            "channel": "chrome",
            "win_paths": [
                r"C:/Program Files/Google/Chrome/Application/chrome.exe",
                r"C:/Program Files (x86)/Google/Chrome/Application/chrome.exe",
            ],
            "win_which": ["chrome", "google-chrome", "google-chrome-stable"],
            "posix_which": ["google-chrome", "google-chrome-stable", "chrome"],
        },
        {
            "channel": "msedge",
            "win_paths": [
                r"C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
                r"C:/Program Files/Microsoft/Edge/Application/msedge.exe",
            ],
            "win_which": ["msedge"],
            "posix_which": ["microsoft-edge", "msedge"],
        },
    ]

    def resolve(self, engine: str = "chromium") -> dict:
        """解析可用浏览器后端。

        Args:
            engine: 目标浏览器引擎，默认 ``"chromium"``。当前仅 chromium 系被支持；
                firefox/webkit 暂不在 resolver 范畴内（lifecycle 会按 bi 透传）。

        Returns:
            含 ``kind`` / ``channel`` / ``executable_path`` / ``launch_kwargs`` 的字典。

        Raises:
            RuntimeError: 所有策略均失败时抛出，附带可读原因，绝不静默。
        """
        reasons: list[str] = []

        # ── 策略 1：系统已装 Chrome/Edge channel 优先 ───────────────────────
        for spec in self.SYSTEM_CHANNELS:
            channel = spec["channel"]
            candidate = self._try_system_channel(channel)
            if candidate is not None:
                _log.info(
                    "[resolver] 命中系统浏览器 channel: %s", channel
                )
                return candidate
            reasons.append("system_channel(%s)=not_found" % channel)

        # ── 策略 2：显式指定的 executable_path（env/配置） ───────────────────
        explicit_path = self._resolve_explicit_path()
        if explicit_path is not None:
            candidate = self._try_executable_path(explicit_path)
            if candidate is not None:
                _log.info(
                    "[resolver] 使用显式 executable_path: %s", explicit_path
                )
                return candidate
            reasons.append("explicit_path=%s=not_found" % explicit_path)

        # ── 策略 3：Playwright 内置 chromium（仅最后回退） ──────────────────
        candidate = self._try_playwright_builtin(engine)
        if candidate is not None:
            _log.info("[resolver] 回退到 Playwright 内置 chromium")
            return candidate
        reasons.append("playwright_builtin=not_found")

        # ── 策略 4：全失败 → 明确抛错，绝不静默 ───────────────────────────
        raise RuntimeError(
            "no usable browser backend: " + "; ".join(reasons)
        )

    # ─────────────────────────────────────────────────────────────────────
    # 内部策略方法
    # ─────────────────────────────────────────────────────────────────────

    def _try_system_channel(self, channel: str) -> dict | None:
        """探测系统已装 Chrome/Edge，命中则返回 system_channel 结果。

        探测顺序：固定安装路径 → PATH which（跨平台兼容）。
        """
        spec = next(
            (s for s in self.SYSTEM_CHANNELS if s["channel"] == channel), None
        )
        if spec is None:
            return None

        is_windows = os.name == "nt" or os.environ.get("OS", "").startswith(
            "Windows"
        )

        # 1) 固定安装路径探测
        if is_windows:
            for p in spec.get("win_paths", []):
                if Path(p).is_file():
                    return self._make_result(
                        kind="system_channel",
                        channel=channel,
                        executable_path=None,
                    )
        # 2) PATH which 探测（Windows / Linux / Mac 通用）
        which_list = (
            spec.get("win_which", [])
            if is_windows
            else spec.get("posix_which", [])
        )
        for name in which_list:
            found = shutil.which(name)
            if found:
                # which 命中：优先用 channel 启动（更可读、更稳定），
                # 同时记录可执行路径备用。
                return self._make_result(
                    kind="system_channel",
                    channel=channel,
                    executable_path=found,
                )
        return None

    def _resolve_explicit_path(self) -> str | None:
        """从环境变量/标准配置键解析显式 executable_path（不校验存在性）。"""
        for env_key in ("PLAYWRIGHT_CHROME_PATH", "BROWSER_EXECUTABLE_PATH"):
            val = os.environ.get(env_key, "").strip()
            if val:
                return val
        return None

    def _try_executable_path(self, path: str) -> dict | None:
        """校验显式路径存在，存在则返回 explicit_path 结果。"""
        if path and Path(path).is_file():
            return self._make_result(
                kind="explicit_path",
                channel=None,
                executable_path=path,
            )
        return None

    def _try_playwright_builtin(self, engine: str) -> dict | None:
        """Playwright 内置 chromium 最后回退。

        ★ 复用原 lifecycle 的探测逻辑：项目自带 chrome-win64 → LOCALAPPDATA
          ms-playwright/chromium-*/chrome-win64/chrome.exe。若均不存在，
          返回 builtin（无 executable_path），交由 Playwright 自管下载/内置。
        """
        # 项目自带 chrome-win64（相对 backend_resolver.py 向上 5 级到项目根）
        project_root = (
            Path(__file__).resolve().parent.parent.parent.parent.parent.parent
        )
        chrome_path = project_root / "chrome-win64" / "chrome.exe"
        if chrome_path.is_file():
            return self._make_result(
                kind="playwright_builtin",
                channel=None,
                executable_path=str(chrome_path),
            )

        # LOCALAPPDATA/ms-playwright/chromium-*/chrome-win64/chrome.exe
        ms_playwright = Path(
            os.environ.get("LOCALAPPDATA", "")
        ) / "ms-playwright"
        if ms_playwright.is_dir():
            for d in sorted(ms_playwright.glob("chromium-*"), reverse=True):
                chrome_exe = d / "chrome-win64" / "chrome.exe"
                if chrome_exe.is_file():
                    return self._make_result(
                        kind="playwright_builtin",
                        channel=None,
                        executable_path=str(chrome_exe),
                    )

        # 两者皆无：返回 builtin（交由 Playwright 内置），仍属"可用回退"。
        # 仅当 engine 为 chromium 系才视为有效回退。
        if engine in ("chromium", "chrome", "msedge"):
            return self._make_result(
                kind="playwright_builtin",
                channel=None,
                executable_path=None,
            )
        return None

    # ─────────────────────────────────────────────────────────────────────
    # 工具方法
    # ─────────────────────────────────────────────────────────────────────

    def _make_result(
        self,
        kind: str,
        channel: str | None,
        executable_path: str | None,
    ) -> dict:
        """组装统一返回结构（深拷贝默认 launch_kwargs，避免调用方篡改共享默认值）。"""
        return {
            "kind": kind,
            "channel": channel,
            "executable_path": executable_path,
            "launch_kwargs": dict(self.DEFAULT_LAUNCH_KWARGS),
        }


# 模块级便捷实例（lifecycle 直接调用，无需重复实例化）。
_default_resolver = BrowserBackendResolver()


def resolve_backend(engine: str = "chromium") -> dict:
    """模块级便捷函数：等价于 ``BrowserBackendResolver().resolve(engine)``。"""
    return _default_resolver.resolve(engine)
