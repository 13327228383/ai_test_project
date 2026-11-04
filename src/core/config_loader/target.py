# -*- coding: utf-8 -*-
from __future__ import annotations
from src.core.guarded import guard

"""
目标被测系统配置 mixin

职责：
- target_url / target_username / target_password
- 业务端口/受保护页面/默认账号兜底
- data_factory 内部 API
- 受保护页面标题关键词 / 业务页面关键词

★ W3 T01 关键改动 [已复核-DeepSeek-V4-Flash 2026-08-05]（2026-08-05）：
  identity override 钩子（模块级 contextvar），供 Step4 run 级身份选择临时覆盖目标账号。
  默认 None → 零行为变更（target_username/target_password 原逻辑不变）。
"""

import contextvars
import logging
import threading
import time

_log = logging.getLogger(__name__)

# 模块级 identity override（contextvar 保证线程/异步隔离；默认 None = 不覆盖）
_IDENTITY_OVERRIDE: contextvars.ContextVar = contextvars.ContextVar(
    "_identity_override", default=None
)
_IDENTITY_OVERRIDE_LOCK = threading.Lock()

# TARGET_URL 可达性探测缓冲（避免每次属性访问都打网络，30s 内复用判定结果）
_url_probe_cache: dict = {}
_URL_PROBE_TTL = 30.0
_URL_PROBE_TIMEOUT = 2.0


def _url_reachable(url: str, timeout: float | None = None, fresh: bool = False) -> bool:
    """轻量探测：URL 是否可达（GET 返回 <500 即视为通）。默认 2s 超时 + 30s 结果缓存。

    仅用于「地址是否仍有效」判定；ERR/超时按不可达处理。
    ``timeout``/``fresh`` 供需要**独立复核**的调用方（批次级健康门禁：长等待到期后
    再问一次环境）使用——不传时行为与缓存语义完全不变，target_url 属性访问仍不阻塞流水线。
    """
    if not url:
        return False
    now = time.time()
    if not fresh:
        hit = _url_probe_cache.get(url)
        if hit and now - hit[0] < _URL_PROBE_TTL:
            return hit[1]
    ok = False
    try:
        import urllib.request

        req = urllib.request.Request(url, method="GET", headers={"User-Agent": "engine-probe"})
        with urllib.request.urlopen(
            req, timeout=float(timeout or _URL_PROBE_TIMEOUT)
        ) as resp:
            ok = resp.status < 500
    except Exception:
        ok = False
    _url_probe_cache[url] = (now, ok)
    return ok


def set_identity_override(identity: dict | None) -> None:
    """设置身份 override（IdentityPool.apply_identity_override 调用）。

    identity 应含 {"username","password",...}；None 等价清除（恢复默认账号）。
    """
    with _IDENTITY_OVERRIDE_LOCK:
        _IDENTITY_OVERRIDE.set(identity)


def clear_identity_override() -> None:
    """清除身份 override（恢复 TARGET_USERNAME/PASSWORD 单身份，零行为变更）。"""
    _IDENTITY_OVERRIDE.set(None)


def _current_identity_override() -> dict | None:
    """返回当前线程/协程生效的身份 override；未设置 → None。"""
    return _IDENTITY_OVERRIDE.get(None)


class _ConfigTargetMixin:
    """目标被测系统配置。"""

    @property
    def target_url(self) -> str:
        """目标系统地址：project_configs.TARGET_URL → 靶子注册表 url/base_url 兜底。

        ★ 桥接回归：与 target_login_path 同构。此前只读 TARGET_URL，
          而 schema.sql 曾把 SmartShop 硬编码播种为 http://127.0.0.1:8000（早期 Django
          版端口），实际靶子（Flask 版）在 8020 —— 配置端口错误/失效时 Step4 全量
          登录失败（连接被拒 → BLOCKED）。现：TARGET_URL 为空**或连不通**时，按
          项目名匹配 target_cfgs 注册表 url（权威真值，零业务硬编码）。
        """
        _cfg = (self._get("TARGET_URL", "") or "").strip()
        if _cfg and _url_reachable(_cfg):
            return _cfg
        if _cfg:
            _log.info(
                "[ConfigTarget] TARGET_URL=%s 不可达，回退靶子注册表", _cfg
            )
        with guard("target_url: from src.pipeline.utils.identity_poo", log=_log):
            from src.pipeline.utils.identity_pool import match_target_key
            from src.pipeline.steps.step4.empirical.target_cfgs import get_target_cfg

            _key = match_target_key(getattr(self, "_project_name", "") or "")
            if _key:
                _cfg2 = (get_target_cfg(_key) or {}).get("url") or ""
                if not _cfg2:
                    _cfg2 = (get_target_cfg(_key) or {}).get("base_url") or ""
                if _cfg2:
                    _log.info(
                        "[ConfigTarget] 地址取自靶子注册表: project=%s target=%s url=%s",
                        getattr(self, "_project_name", ""), _key, _cfg2,
                    )
                    return str(_cfg2).strip()
        return _cfg

    def is_target_reachable(self, timeout: float | None = None, fresh: bool = False) -> bool:
        """目标系统**当前**是否可达（HTTP 探测返回 <500）。

        唯一判据是 ``_url_reachable``（与 target_url 兜底同源，不另造一套探测）。
        供批次级健康门禁使用：不可达时逐条执行会把环境问题伪装成用例失败，
        且单条长操作期间无日志会让 run 级看门狗误杀整轮 run。
        """
        return _url_reachable(self.target_url, timeout=timeout, fresh=fresh)

    @property
    def target_auth_none(self) -> bool:
        """靶子注册表是否声明 auth.type=='none'（无需登录）。

        ★ 2026-09-28 通用回归：empirical 与 Web Step4 的登录判定单源对齐。
          Web 侧曾不看注册表，仅按 "TARGET_USERNAME/PASSWORD 非空" 判"需登录"——
          user_credentials 里残留的全局 TARGET_PASSWORD(project='*') 会让 auth.none
          靶子（如 SmartShop Flask 版）永远被当成需登录 → 逐路径探测 → 404 → BLOCKED。
          现在：匹配到 target_cfg 且 auth.type=='none' → 本属性 True，
          调用方（ai/login.py、session_aware）统一据此跳过自动登录。
        """
        try:
            from src.pipeline.utils.identity_pool import match_target_key
            from src.pipeline.steps.step4.empirical.target_cfgs import get_target_cfg

            _key = match_target_key(getattr(self, "_project_name", "") or "")
            if not _key:
                return False
            _auth = (get_target_cfg(_key) or {}).get("auth") or {}
            return str(_auth.get("type") or "").strip() == "none"
        except Exception as _e:  # noqa: BLE001
            _log.debug("[ConfigTarget] target_auth_none 判定失败(按需登录): %s", str(_e)[:80])
            return False

    @property
    def target_username(self) -> str:
        """目标系统用户名，user_credentials_v2（解密）→ project_configs（明文）→ 默认值兜底。

        ★ W3 T01：身份 override 非 None 时优先返回 override.username（run 级身份选择）。
        """
        _ov = _current_identity_override()
        if _ov is not None:
            return str(_ov.get("username") or "").strip()
        val = self._user_cred("TARGET_USERNAME")
        if val:
            return str(val).strip()
        val = self._get("TARGET_USERNAME", "")
        if val:
            return str(val).strip()
        return (self._get("TARGET_DEFAULT_USERNAME", "admin") or "").strip()

    @property
    def target_password(self) -> str:
        """目标系统密码，user_credentials_v2（解密）→ project_configs（明文）→ 空兜底。

        ★ W3 T01：身份 override 非 None 时优先返回 override.password（run 级身份选择）。
        """
        _ov = _current_identity_override()
        if _ov is not None:
            return str(_ov.get("password") or "").strip()
        val = self._user_cred("TARGET_PASSWORD")
        if val:
            return str(val).strip()
        val = self._get("TARGET_PASSWORD", "")
        if val:
            return str(val).strip()
        return ""

    @property
    def target_success_url_suffix(self) -> str:
        """受保护首页 URL 后缀(用于"受保护首页"/"首页"关键词命中判定)。"""
        return self._get("TARGET_SUCCESS_URL_SUFFIX", ":5001")

    @property
    def target_login_path(self) -> str:
        """登录路径：project_configs.TARGET_LOGIN_PATH → 靶子注册表 auth.login_url → ""。

        ★ 根因修复 RC-AB: 本属性**此前不存在**（全仓仅以
          ``getattr(config, "target_login_path", "")`` 引用）→ 恒得空串 →
          "配置的登录路径恒优先"契约形同虚设 → 登录探测退化为通用候选表
          ``["/admin/", "/admin/login/", "/login/"]``（Django 风格路径打头）。
          靶子 fullcov_sut 的真实登录页是 ``/login``，而 ``/admin/login/`` 返回 404；
          404 页 URL 恰含 "/login" 又被误判为登录页（见 browser_ai_login 同步修复）
          → 真登录页永不尝试 → 登录失败 → 全量用例 HARNESS_ERROR。

        通用性: 只依赖两个通用事实——「项目配置声明了登录路径」或「靶子注册表声明了
          auth.login_url」；零业务硬编码。项目名 ≠ 靶子 key 时经 TARGET_URL 绑定桥接
          （match_target_key），改名项目/多项目共用同一靶子同样受益。
        """
        _p = (self._get("TARGET_LOGIN_PATH", "") or "").strip()
        if _p:
            return _p
        with guard("target_login_path: from src.pipeline.utils.identity_poo", log=_log):
            from src.pipeline.utils.identity_pool import match_target_key
            from src.pipeline.steps.step4.empirical.target_cfgs import get_target_cfg

            _key = match_target_key(getattr(self, "_project_name", "") or "")
            if _key:
                _auth = (get_target_cfg(_key) or {}).get("auth") or {}
                _lp = str(_auth.get("login_url") or "").strip()
                if _lp:
                    _log.info(
                        "[ConfigTarget] 登录路径取自靶子注册表: project=%s target=%s path=%s",
                        getattr(self, "_project_name", ""), _key, _lp,
                    )
                    return _lp
        return ""

    @property
    def target_protected_title_keywords(self) -> list:
        """受保护页面标题关键词列表。"""
        return self._get("TARGET_PROTECTED_TITLE_KEYWORDS", ["管理系统"]) or [
            "管理系统"
        ]

    @property
    def target_page_keywords(self) -> dict:
        """业务关键页面关键词 (protected_home / login_form 等)。"""
        val = self._get("TARGET_PAGE_KEYWORDS", {})
        if isinstance(val, dict) and val:
            return val
        return {
            "protected_home": ["受保护首页", "首页"],
            "login_form": ["登录表单", "登录页"],
        }

    @property
    def data_factory_api_base(self) -> str:
        """data_factory 内部 API base URL (用于AI数据生成)。"""
        return self._get("TARGET_DATA_FACTORY_API_BASE", "http://127.0.0.1:8000")

    @property
    def data_factory_api_timeout(self) -> int:
        return self._get("TARGET_DATA_FACTORY_API_TIMEOUT", 15) or 15
