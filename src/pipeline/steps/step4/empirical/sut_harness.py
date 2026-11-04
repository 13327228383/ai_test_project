# -*- coding: utf-8 -*-
from __future__ import annotations
from src.core.guarded import guard

"""[未复核-需 GLM/DeepSeek 把关] 靶子自管 + 登录（SutHarness）。

★ 职责：将临时脚本 `scripts/e2e_engine/real_browser_verify.py` 中的
  `_ensure_sut_running` / `_seed_verify_db` / `_login_get_cookie` 泛化为引擎常驻组件。

★ 核心约束（来自 design doc Part A §0 / 共享知识 §8）：
  - SUT 自管：threaded=True 实例（避免 Flask 单线程 dev server 与 Chrome 多连接 deadlock）。
  - HOSPITAL_SUT_DB 指向未跟踪可写库（沙箱对跟踪文件加只读，未跟踪库可写）。
  - 登录契约：requests POST /login → 取 session cookie → 返回可直接喂
    `context.add_cookies(...)` 的 cookie 列表，跳过登录重定向。
  - 自行拉起的进程保留（留给后续复用），不主动杀掉（stop() 可选）。

★ 依赖方向：SutHarness 仅依赖标准库 + requests + target_cfgs（启动参数全部来自
  target_cfg["launch"]：cwd/factory/env/seed_cmd，不再写死 hospital_sut），
  不依赖 lifecycle / resolver（避免循环依赖）。
"""

import logging
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from src.pipeline.steps.step4.empirical.target_cfgs import TARGET_CFGS

_log = logging.getLogger(__name__)


def _resolve_python_cmd(cmd):
    """把以 ``python`` 开头的启动命令解析为当前解释器绝对路径。

    通用规则：凡由引擎 ``shell=True`` 拉起的子进程命令，
    若以 ``python`` 开头就替换为 ``sys.executable``——external 环境（如 WindowsApps
    占位符 / 未激活 venv）可能没有可用的 ``python``，会导致 seed/启动静默失败
    （实测 returncode=9009，与 static_serve 既有修复对齐，统一为模块级规则）。
    """
    if not cmd or not isinstance(cmd, str):
        return cmd
    stripped = cmd.lstrip()
    parts = stripped.split(None, 1)
    interp = parts[0] if parts else ""
    rest = (" " + parts[1]) if len(parts) > 1 else ""
    if interp in ("python", "python3"):
        return cmd[: len(cmd) - len(stripped)] + sys.executable + rest
    return cmd

# 项目根（src 向上 4 级；不再写死任何靶子专属路径，路径全部来自 target_cfg.launch）
_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent.parent


class SutHarness:
    """靶子自管：确保可达、自行拉起实例、登录拿凭证。

    支持 browser / api 两种模式（docs/system_design_expend.md §1.1）：
      - browser 模式：拉起 threaded Flask，登录取 session cookie。
      - api 模式：用 requests 探活（GET /health 或 /api/v1/books），
        登录取 Bearer token（不启动浏览器、不读 DOM）。
    """

    def __init__(
        self,
        url: str,
        port: int,
        target_key: str = "",
        db_env: str = "",
        mode: str = "browser",
        auth: dict | None = None,
        launch: dict | None = None,
    ) -> None:
        """初始化。

        Args:
            url: 靶子基地址（如 http://127.0.0.1:5081 / http://127.0.0.1:8010）。
            port: 端口（仅用于自管拉起时传递）。
            target_key: 靶子标识（用于选择 seed 脚本/可写库命名）；缺省空串，
                调用方（TargetEmpiricalVerifier）应显式传入，禁止默认 hospital。
            db_env: 可写库环境变量名（如 HOSPITAL_SUT_DB / SMARTSHOP_DB）；缺省空串，
                若 target_key 为已知注册表 key，则回退到 target_cfg["db_env"]。
            mode: "browser" | "api"。
            auth: 鉴权配置（form_cookie / bearer_token / none）。
            launch: 自管拉起配置（flask_factory / flask_script / static_serve / none）。
        """
        self.url = url.rstrip("/")
        self.port = int(port)
        self.target_key = target_key
        # db_env 去靶子化：显式传入优先；否则若为已知注册表 key，回退 target_cfg["db_env"]。
        if db_env:
            self.db_env = db_env
        elif target_key in TARGET_CFGS:
            self.db_env = TARGET_CFGS[target_key].get("db_env", "")
        else:
            self.db_env = ""
        self.mode = mode
        self.auth = auth or {"type": "form_cookie"}
        self.launch = launch or {"kind": "flask_factory"}
        # 未跟踪可写库（沙箱对跟踪文件只读，未跟踪库可写）。统一落到项目根
        # .verify_dbs/<target_key>.db，避免各靶子默认库路径被 git 跟踪导致写失败
        # （修复 §14 问题 9：默认 DB 路径可写性；如 smartshop 默认
        #  examples/smartshop/smartshop.db 为被跟踪文件，沙箱只读会写失败）。
        self.writable_db = str(_ROOT / ".verify_dbs" / f"{self.target_key}.db")
        with guard("__init__: Path(self.writable_db).parent.mkdir(", log=_log):
            Path(self.writable_db).parent.mkdir(parents=True, exist_ok=True)
        self._proc = None  # 自行拉起的子进程
        self.self_started = False

    # ─────────────────────────────────────────────────────────────────────
    # 公开 API
    # ─────────────────────────────────────────────────────────────────────

    def ensure_running(self) -> bool:
        """确保靶子可达；不可达则按 launch.kind 自行拉起。

        Returns:
            True 表示本次自行拉起了 SUT；False 表示已存在（未拉起）。
        """
        if self._is_reachable():
            _log.info("[SutHarness] %s 已可达，无需拉起", self.url)
            return False

        launch_kind = (self.launch or {}).get("kind", "flask_factory")
        _log.info(
            "[SutHarness] %s 不可达，自行拉起实例 (mode=%s, launch=%s)...",
            self.url, self.mode, launch_kind,
        )
        if launch_kind == "flask_script":
            # api 模式：flask_script 起 REST 服务（如 examples/api_demo/app.py）。
            self._launch_subprocess()
        elif launch_kind == "static_serve":
            # 纯静态服务（如 spa_demo 的 python -m http.server）。
            # 无需种子库、无需登录 cookie（auth.type=none）。
            self._launch_static_serve()
        else:
            # 默认：browser 模式 threaded Flask（hospital_sut）。
            # 需要可写种子库 + 登录 cookie。
            self._seed_writable_db()
            self._launch_threaded()
        return True

    def login_get_cookie(self) -> list[dict]:
        """browser 模式：登录并取 session cookie。

        返回可直接喂 context.add_cookies 的列表。失败返回空列表（调用方如实 blocked）。

        auth.type == "none"（如 spa_demo 纯前端 SPA，无后端登录）→ 直接返回空列表，
        调用方据此跳过 cookie 注入（不报错、不假绿，因为靶子本就无需登录）。
        """
        auth_type = (self.auth or {}).get("type", "form_cookie")
        if auth_type == "none":
            _log.info(
                "[SutHarness] auth.type=none，无需登录，返回空 cookie 列表"
            )
            return []

        import requests

        login_fields = (self.auth or {}).get("login_fields") or {}
        username = login_fields.get("username")
        password = login_fields.get("password")
        login_url = (self.auth or {}).get("login_url")
        if not (username and password and login_url):
            # 凭据/登录端点必须由靶子配置声明；引擎不得猜默认账号（猜中即靶子适配补丁）。
            _log.error(
                "[SutHarness] auth 未声明 login_url/login_fields.username/password，无法登录"
            )
            return []
        user_key = login_fields.get("username_field", "username")
        pass_key = login_fields.get("password_field", "password")
        try:
            s = requests.Session()
            resp = s.post(
                self.url + login_url,
                data={user_key: username, pass_key: password},
                allow_redirects=False,
                timeout=10,
            )
            _log.info(
                "[SutHarness] 登录 POST /login → HTTP %s", resp.status_code
            )
            if resp.status_code not in (302, 200):
                _log.error(
                    "[SutHarness] 登录失败 HTTP=%s body=%s",
                    resp.status_code,
                    resp.text[:200],
                )
                return []
            for c in s.cookies:
                if c.name == "session":
                    return [
                        {
                            "name": c.name,
                            "value": c.value,
                            "domain": "127.0.0.1",
                            "path": "/",
                        }
                    ]
            _log.error("[SutHarness] 未取到 session cookie")
            return []
        except Exception as _e:
            _log.error("[SutHarness] 登录异常: %s", _e)
            return []

    def api_login_get_token(self, role: str = "admin") -> str | None:
        """api 模式：登录取 Bearer token（不启动浏览器、不读 DOM）。

        Args:
            role: "admin" | "user"（用于越权用例 A01）。

        Returns:
            token 字符串；失败返回 None（调用方应如实 blocked，不降级）。
        """
        import requests

        # ★ 与 login_get_cookie 对齐：auth.type==none 的靶子（如 edu_sut/fin_sut）
        #   无登录端点，直接返回 None（调用方据此跳过 token 注入，不报错、不假绿）。
        auth_type = (self.auth or {}).get("type")
        if auth_type == "none":
            _log.info(
                "[SutHarness] api auth.type=none，无需登录，返回 None token"
            )
            return None

        fields = (self.auth or {}).get("login_fields") or {}
        if role == "user":
            username = fields.get("user_username")
            password = fields.get("user_password")
            login_url = fields.get("user_login_url") or (self.auth or {}).get("login_url")
        else:
            username = fields.get("admin_username")
            password = fields.get("admin_password")
            login_url = (self.auth or {}).get("login_url")
        if not (username and password and login_url):
            # 凭据/登录端点必须由靶子配置声明；引擎不得猜默认账号（猜中即靶子适配补丁）。
            _log.error(
                "[SutHarness] auth 未声明 role=%s 的 login_url/账号/密码，跳过登录", role
            )
            return None

        try:
            resp = requests.post(
                self.url + login_url,
                json={"username": username, "password": password},
                timeout=10,
            )
            _log.info(
                "[SutHarness] api 登录 POST %s → HTTP %s",
                login_url, resp.status_code,
            )
            if resp.status_code != 200:
                _log.error(
                    "[SutHarness] api 登录失败 HTTP=%s body=%s",
                    resp.status_code, resp.text[:200],
                )
                return None
            body = resp.json()
            token = self._dig(body, (self.auth or {}).get("token_field", "data.token"))
            if not token:
                _log.error("[SutHarness] api 登录响应缺 token 字段: %s", body)
                return None
            return token
        except Exception as _e:
            _log.error("[SutHarness] api 登录异常: %s", _e)
            return None

    @staticmethod
    def _dig(obj: dict, path: str):
        """按点路径取嵌套字段（如 data.token）。"""
        cur = obj
        for part in path.split("."):
            if not isinstance(cur, dict) or part not in cur:
                return None
            cur = cur[part]
        return cur

    def stop(self) -> None:
        """停止自行拉起的 SUT 进程（含整棵进程树），避免孤儿化。

        ★ 修复实证套件串扰根因：验证结束必须回收 SUT 子进程。
          ``subprocess(shell=True, start_new_session=True)`` 会起 ``cmd.exe`` 包装
          进程并独立成进程组（Unix），真实服务是其孙进程；仅 ``terminate()`` 直子
          无法波及孙进程，导致 SUT 被孤儿化、在多个靶子连续验证时堆积占用端口/内存，
          使后续靶子（如 spa_demo）资源吃紧失败。

        跨平台策略：
          - Windows（os.name == "nt"）：``taskkill /F /T /PID`` 递归杀整棵树。
          - POSIX：``os.killpg(pgid, SIGTERM)`` 后若仍存活再 ``SIGKILL``（整组回收）。
        任何异常都吞掉，绝不向上抛出（stop 多在 finally/__exit__ 中调用）。
        """
        proc = self._proc
        if proc is None:
            return
        pid = getattr(proc, "pid", None)
        if pid is None:
            self._proc = None
            return
        try:
            if os.name == "nt":
                # /T 递归杀整棵树（含 cmd.exe 包装及其孙进程）；/F 强制。
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(pid)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=10,
                )
                _log.info("[SutHarness] 已终止自行拉起的 SUT 进程树 pid=%s", pid)
            else:
                import signal

                pgid = os.getpgid(pid)
                try:
                    os.killpg(pgid, signal.SIGTERM)
                except ProcessLookupError as _e:
                    _log.debug("stop best-effort 容忍: %s", str(_e)[:120])
                # 给一点时间优雅退出，仍存活则强杀整组。
                try:
                    proc.wait(timeout=5)
                except Exception:
                    try:
                        os.killpg(pgid, signal.SIGKILL)
                    except ProcessLookupError as _e:
                        _log.debug("stop best-effort 容忍: %s", str(_e)[:120])
                _log.info("[SutHarness] 已回收 SUT 进程组 pgid=%s pid=%s", pgid, pid)
        except Exception as _e:  # 任一平台兜底：直接 terminate 直子
            _log.debug("[SutHarness] 进程树回收异常，回退 terminate: %s", _e)
            with guard("stop: proc.terminate()", log=_log):
                proc.terminate()
        self._proc = None

    # ─────────────────────────────────────────────────────────────────────
    # 上下文管理器（可选语法糖）：with SutHarness(...) as h: 确保退出时回收
    # ─────────────────────────────────────────────────────────────────────

    def __enter__(self) -> "SutHarness":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        # 退出作用域（无论是否异常）都回收 SUT；返回 False 不吞异常。
        self.stop()
        return False

    # ─────────────────────────────────────────────────────────────────────
    # 内部实现
    # ─────────────────────────────────────────────────────────────────────

    def _is_reachable(self) -> bool:
        """探测靶子是否可达。

        - browser 模式：GET url/（urllib，不引入 requests 依赖用于探测）。
        - api 模式：GET url/health 或 /api/v1/books（用 requests 探活，
          真实校验后端可用而非仅端口占用）。
        """
        if self.mode == "api":
            try:
                import requests

                for probe in ("/health", "/api/v1/books"):
                    try:
                        r = requests.get(self.url + probe, timeout=3)
                        if r.status_code in (200, 401):
                            return True
                    except Exception:
                        continue
                return False
            except Exception:
                return False
        try:
            # 探测 base_url 本身（spa_demo 的 base_url 已是 .../index.html，
            # 追加 "/" 会 404；hospital 的 base_url 是 ...:5081，追加 "/" 得根路径 200）。
            with urllib.request.urlopen(self.url, timeout=3) as r:
                return r.status in (200, 302)
        except Exception:
            return False

    def _seed_writable_db(self) -> None:
        """对未跟踪可写库初始化 schema + 种子（含 admin 账号）。

        去靶子化：不再写死 seed.py / SUT_ROOT，完全由 target_cfg.launch 驱动——
          launch.seed_cmd：种子命令（如 "python seed.py"），缺省则不种子；
          launch.cwd：命令工作目录（即靶子根，种子脚本相对此执行）；
          launch.env：注入的环境变量（含 DB 占位 <writable_db> 解析为可写库路径）。
        """
        launch = self.launch or {}
        seed_cmd = launch.get("seed_cmd")
        if not seed_cmd:
            _log.info(
                "[SutHarness] %s 无 launch.seed_cmd，跳过种子（无需初始化可写库）",
                self.target_key,
            )
            return
        cwd = launch.get("cwd") or str(_ROOT)
        env = dict(os.environ)
        if self.db_env:
            env[self.db_env] = self.writable_db
        for k, v in (launch.get("env") or {}).items():
            env[k] = self.writable_db if v == "<writable_db>" else v
        # ★ 差距1修复：seed 命令也用当前解释器解析（与 static_serve/api 同规则），
        #   避免 external 环境无 ``python`` 时种子静默失败（实测 9009）。
        seed_cmd = _resolve_python_cmd(seed_cmd)
        with guard("_seed_writable_db: _log.info('[SutHarness] 初始化可写库种子 cmd", log=_log, level=logging.ERROR):
            _log.info("[SutHarness] 初始化可写库种子 cmd=%r cwd=%s", seed_cmd, cwd)
            subprocess.run(
                seed_cmd,
                cwd=str(cwd),
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=60,
                check=True,
                shell=True,
            )
            _log.info("[SutHarness] 可写库种子完成: %s", self.writable_db)

    def _launch_threaded(self) -> None:
        """flask_factory 启动 threaded 服务器（去靶子化，不再写死 hospital）。

        完全由 target_cfg.launch 驱动：
          launch.cwd：子进程工作目录（即靶子根），注入 sys.path + os.chdir；
          launch.factory：自包含启动表达式（含导入，如
            "from app import create_app; create_app().run(host=..., port=..., threaded=True)"），
            直接 exec（不再硬编码 from app import create_app）；
          launch.env：注入的环境变量（DB 占位 <writable_db> 解析为可写库路径；
            HOSPITAL_SUT_PORT/HOST 等不再硬编码，由 factory 表达式内联或 launch.env 提供）。
        """
        launch = self.launch or {}
        cwd = launch.get("cwd") or str(_ROOT)
        factory_expr = launch.get("factory")
        if not factory_expr:
            _log.error(
                "[SutHarness] flask_factory 模式缺少 launch.factory，无法拉起 %s",
                self.target_key,
            )
            self.self_started = False
            return
        # 环境变量走 Popen env= 参数注入（绝不把整张环境表塞进 -c 命令行，
        # 否则 Windows 下超长命令会触发 WinError 206「文件名或扩展名太长」；
        # 同时保留去靶子化：全部来自 launch.env / db_env，无硬编码 hospital 路径）。
        env = dict(os.environ)
        if self.db_env:
            env[self.db_env] = self.writable_db
        for k, v in (launch.get("env") or {}).items():
            env[k] = self.writable_db if v == "<writable_db>" else v
        # 启动脚本：仅注入 sys.path(使 `from app import ...` 可解析) + os.chdir(cwd)，
        # 再 exec factory_expr；保持命令行短小，env 由 Popen env= 注入。
        launcher = "import os,sys;"
        launcher += "sys.path.insert(0, %r);" % str(cwd)
        launcher += "os.chdir(%r);" % str(cwd)
        launcher += factory_expr
        self._proc = subprocess.Popen(
            [sys.executable, "-c", launcher],
            cwd=str(cwd),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,  # Unix: 独立进程组，便于 stop() 整组回收
        )
        # 等待起来
        for _ in range(40):
            if self._is_reachable():
                _log.info(
                    "[SutHarness] SUT threaded 实例已拉起 (pid=%s)", self._proc.pid
                )
                self.self_started = True
                return
            time.sleep(1)
        # ★ 修复 §14 问题 8：拉起超时不再"乐观置位"为 True（否则会误导调用方
        #   以为 SUT 可用）。以真实可达性收口，超时即 self_started=False。
        _log.error("[SutHarness] SUT 拉起超时，验证可能无法进行")
        self.self_started = self._is_reachable()

    def _launch_subprocess(self) -> None:
        """api 模式：用 launch.cmd 拉起子进程（如 python examples/api_demo/app.py）。

        启动后轮询 _is_reachable（api 探活 /health 或 /api/v1/books）直到就绪。
        """
        launch = self.launch or {}
        cmd = launch.get("cmd")
        cwd = launch.get("cwd") or str(_ROOT)
        if not cmd:
            _log.error("[SutHarness] api 模式无 launch.cmd，无法拉起")
            self.self_started = False
            return
        env = dict(os.environ)
        for k, v in (launch.get("env") or {}).items():
            if v == "<writable_db>":
                env[k] = self.writable_db
            else:
                env[k] = v
        cmd = _resolve_python_cmd(cmd)  # ★ 差距1修复：统一 python → sys.executable
        _log.info("[SutHarness] 拉起子进程 cmd=%r cwd=%s", cmd, cwd)
        self._proc = subprocess.Popen(
            cmd,
            cwd=cwd,
            env=env,
            shell=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,  # Unix: 独立进程组（含 cmd.exe 包装孙进程）
        )
        # 等待起来（api 探活）
        for _ in range(40):
            if self._is_reachable():
                _log.info(
                    "[SutHarness] api 子进程已就绪 (pid=%s)", self._proc.pid
                )
                self.self_started = True
                return
            time.sleep(1)
        # ★ 修复 §14 问题 8：超时以真实可达性收口，self_started=False。
        _log.error("[SutHarness] api 子进程拉起超时，验证可能无法进行")
        self.self_started = self._is_reachable()

    def _launch_static_serve(self) -> None:
        """static_serve 模式：用 launch.cmd 起静态文件服务（如 python -m http.server）。

        典型场景：spa_demo 纯前端 SPA（index.html + app.js），无后端、无需种子库、
        无需登录 cookie。启动后轮询 _is_reachable（GET / 返回 200）直到就绪。
        """
        launch = self.launch or {}
        cmd = launch.get("cmd")
        cwd = launch.get("cwd") or str(_ROOT)
        if not cmd:
            _log.error("[SutHarness] static_serve 模式无 launch.cmd，无法拉起")
            self.self_started = False
            return
        # 用当前解释器绝对路径替换 "python" 占位，避免 subprocess 环境无 python。
        cmd = _resolve_python_cmd(cmd)  # ★ 差距1修复：统一为模块级通用规则
        env = dict(os.environ)
        for k, v in (launch.get("env") or {}).items():
            env[k] = v
        _log.info("[SutHarness] 拉起静态服务 cmd=%r cwd=%s", cmd, cwd)
        self._proc = subprocess.Popen(
            cmd,
            cwd=cwd,
            env=env,
            shell=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,  # Unix: 独立进程组（含 cmd.exe 包装孙进程）
        )
        # 等待起来（静态服务 GET / 探活）
        for _ in range(40):
            if self._is_reachable():
                _log.info(
                    "[SutHarness] 静态服务已就绪 (pid=%s)", self._proc.pid
                )
                self.self_started = True
                return
            time.sleep(1)
        # ★ 修复 §14 问题 8：超时以真实可达性收口，self_started=False。
        _log.error("[SutHarness] 静态服务拉起超时，验证可能无法进行")
        self.self_started = self._is_reachable()
