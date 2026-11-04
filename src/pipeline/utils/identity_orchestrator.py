# -*- coding: utf-8 -*-
"""多身份 / 多角色编排（B3，CAPABILITY_GAP_PRD §3.2）—— 登录态隔离 + 越权协同。

存量能力（不重复造轮子，本模块只补缺口）：
  - ``src/pipeline/utils/identity_pool.py``：身份池容器（load/save/resolve）
    + ``apply_identity_override``（config 层 username/password 覆盖）
  - ``src/core/config_loader/target.py``：``_IDENTITY_OVERRIDE`` contextvar 钩子
  - ``serial.py``：per-case 身份热切换（``apply_case_identity`` + finally 恢复）

存量缺口（本模块补齐）：
  1. **登录态未隔离（串号）**：``apply_case_identity`` 只改 config 层 username/password，
     浏览器 cookies / localStorage / sessionStorage **原样保留**。故「用例 A 以 admin
     登录 → 用例 B 声明 user 身份」时，B 实际仍带着 A 的会话在跑 —— 全库 grep
     ``clear_cookies`` 零命中即证据。本模块 ``isolate_session`` 显式清会话。
  2. **无匿名身份**：override 为 None 时回落 TARGET_USERNAME（=某个真实账号），
     无法表达「未登录」。本模块以空凭据 + 不登录表达 ``anonymous``。
  3. **与 GAP-05 越权检测无协同**：GAP-05（``_flag_security_authz_bypass``）需要用例
     声明 ``checks["security_check"]``，而引擎此前「无法自行换一个低权限身份再调一次」
     （见 verification/__init__.py:178 注释）。本模块 ``build_security_check`` 依据当前
     身份自动产出该声明（低权限身份 → ``assume_unauthorized=True``）。

三身份模型：
  ``admin``（高权限） / ``user``（普通用户） / ``anonymous``（匿名未登录）

★ 安全铁律：``MULTI_IDENTITY_ENABLED`` 门控；显式设 False → 所有切换 no-op。
  ★ RC-C 通用修复：未显式配置时**数据驱动**——靶子 ``auth.roles`` 声明
  ≥2 角色即视为启用（凭据单一事实源是靶子配置；否则 RBAC 靶子的访问拒绝用例
  永远以高权身份执行 → 假 FAIL）。爆炸半径受"用例必须声明 identity 才切换"约束。
★ 隔离铁律：切换身份**必须**先 ``isolate_session``，再改 override，最后按需重登录；
  顺序颠倒会导致旧会话被新身份复用（串号）。
★ 切回铁律：per-case 切换后**必须** ``restore_default_identity`` 回到 run 级默认身份；
  否则后续用例带着低权/匿名会话执行 → 跨用例串号。

★ B3 关键改动（2026-08-16）[未复核-需 GLM/DeepSeek 把关]：
  新增多身份编排 + 会话隔离（触及登录态/浏览器上下文）；MULTI_IDENTITY_ENABLED 默认 OFF。
"""
from __future__ import annotations
from src.core.guarded import guard

import hashlib
import json
import logging
from typing import Any

_log = logging.getLogger(__name__)

# ── 三身份角色常量 ──
ROLE_ADMIN = "admin"
ROLE_USER = "user"
ROLE_ANONYMOUS = "anonymous"
VALID_ROLES = (ROLE_ADMIN, ROLE_USER, ROLE_ANONYMOUS)

# 声明为匿名的身份 key 别名（大小写不敏感）
_ANONYMOUS_KEYS = ("anonymous", "anon", "guest", "visitor", "未登录", "匿名")

# 池内 role 字段 → 规范角色的宽松映射（兼容中文/别名）
_ROLE_ALIASES: dict[str, str] = {
    "admin": ROLE_ADMIN,
    "administrator": ROLE_ADMIN,
    "superadmin": ROLE_ADMIN,
    "root": ROLE_ADMIN,
    "管理员": ROLE_ADMIN,
    "超级管理员": ROLE_ADMIN,
    "user": ROLE_USER,
    "normal": ROLE_USER,
    "member": ROLE_USER,
    "viewer": ROLE_USER,
    "operator": ROLE_USER,
    "普通用户": ROLE_USER,
    "用户": ROLE_USER,
    "anonymous": ROLE_ANONYMOUS,
    "guest": ROLE_ANONYMOUS,
    "匿名": ROLE_ANONYMOUS,
}


def _enabled(project: str = "") -> bool:
    """身份编排门控：显式配置优先；未显式配置时按靶子声明**数据驱动**。

    语义（★ RC-C 通用修复 2026-09-24）:
      1. ``MULTI_IDENTITY_ENABLED`` 显式设为 True/False → 以此为准（可显式关停）。
      2. 未显式配置（None）→ 若靶子 ``auth.roles`` 声明 ≥2 个角色则视为启用。

    根因: 靶子配置已是角色凭据的单一事实源，声明多角色即表明需要角色化测试；
      若仍要求人工翻 3 个开关，RBAC 靶子的访问拒绝用例永远以高权身份执行 → 假 FAIL。
    安全性: 爆炸半径受"**用例必须声明 identity 才切换**"约束 —— 无声明则零行为变更；
      未声明 roles 的靶子（api_demo 之外的多数靶子）恒走原路径。
    """
    try:
        from src.core.pipeline_config import _read_config_value as _cfg

        _explicit = _cfg("MULTI_IDENTITY_ENABLED", None)
        if _explicit is not None:
            return bool(_explicit)
    except Exception:  # noqa: BLE001
        return False
    try:
        from src.pipeline.utils.identity_pool import target_roles

        return len(target_roles(project)) >= 2
    except Exception:  # noqa: BLE001
        return False


def normalize_role(raw: Any) -> str:
    """池内 role 字段 → 规范角色（无法识别 → ``user``，就低不就高，避免误判高权限）。"""
    key = str(raw or "").strip().lower()
    if not key:
        return ROLE_USER
    if key in _ROLE_ALIASES:
        return _ROLE_ALIASES[key]
    for alias, role in _ROLE_ALIASES.items():
        if alias in key:
            return role
    return ROLE_USER


class IdentitySpec:
    """一个可编排的身份（不可变语义；``anonymous`` 无凭据）。"""

    __slots__ = ("key", "role", "username", "password", "extra")

    def __init__(
        self,
        key: str = "",
        role: str = ROLE_USER,
        username: str = "",
        password: str = "",
        extra: dict[str, Any] | None = None,
    ):
        self.key = str(key or "").strip()
        self.role = role if role in VALID_ROLES else normalize_role(role)
        self.username = str(username or "").strip()
        self.password = str(password or "")
        self.extra = dict(extra or {})

    @property
    def is_anonymous(self) -> bool:
        """匿名身份（角色为 anonymous 或无用户名）。"""
        return self.role == ROLE_ANONYMOUS or not self.username

    @property
    def is_privileged(self) -> bool:
        """是否高权限身份（仅 admin）。"""
        return self.role == ROLE_ADMIN

    @classmethod
    def anonymous(cls) -> IdentitySpec:
        """构造匿名身份（无凭据、不登录）。"""
        return cls(key=ROLE_ANONYMOUS, role=ROLE_ANONYMOUS)

    @classmethod
    def from_pool_entry(cls, key: str, entry: dict[str, Any]) -> IdentitySpec:
        """身份池条目 → IdentitySpec。

        Args:
            key: 池内身份 key。
            entry: ``{"username":..,"password":..,"role":..,...}``。

        Returns:
            IdentitySpec；``role`` 缺失时按 key 猜测（key 含 admin → admin）。
        """
        entry = entry if isinstance(entry, dict) else {}
        role_raw = entry.get("role") or key
        return cls(
            key=key,
            role=normalize_role(role_raw),
            username=str(entry.get("username", "") or ""),
            password=str(entry.get("password", "") or ""),
            extra={
                k: v
                for k, v in entry.items()
                if k not in ("username", "password", "role")
            },
        )

    def to_override(self) -> dict[str, str]:
        """→ config 层 override dict（匿名 = 空凭据，明确表达「未登录」）。"""
        if self.is_anonymous:
            return {"username": "", "password": ""}
        return {"username": self.username, "password": self.password}

    def to_dict(self) -> dict[str, Any]:
        """可序列化摘要（★ 绝不含密码明文，只标记有无）。"""
        return {
            "key": self.key,
            "role": self.role,
            "username": self.username,
            "has_password": bool(self.password),
            "is_anonymous": self.is_anonymous,
            "is_privileged": self.is_privileged,
        }

    def __repr__(self) -> str:
        return f"IdentitySpec(key={self.key!r}, role={self.role!r})"


# ═══════════════════════════════════════════════════════════════════════════
# 身份解析
# ═══════════════════════════════════════════════════════════════════════════


def resolve_identity_spec(
    case: dict, project: str = ""
) -> tuple[IdentitySpec | None, str]:
    """用例声明 → IdentitySpec。

    读取顺序：``case["identity"]`` → ``case["payload"]["identity"]``（复用
    ``IdentityPool.resolve`` 的声明位约定）。

    Args:
        case: 用例 dict。
        project: 项目名（身份池按项目隔离）。

    Returns:
        ``(spec, reason)``：
          - 未声明 → ``(None, "not_declared")``（调用方用默认身份，零行为变更）
          - 声明匿名 → ``(匿名 spec, "anonymous")``
          - 声明且池中存在 → ``(spec, "resolved")``
          - 声明但池中缺失 → ``(None, "missing:<key>")``（调用方应 BLOCKED，fail-closed）
    """
    if not isinstance(case, dict):
        return None, "not_declared"
    key = str(case.get("identity", "") or "").strip()
    if not key:
        payload = case.get("payload")
        if isinstance(payload, dict):
            key = str(payload.get("identity", "") or "").strip()
    if not key:
        return None, "not_declared"
    if key.lower() in _ANONYMOUS_KEYS:
        return IdentitySpec.anonymous(), "anonymous"
    try:
        from src.pipeline.utils.identity_pool import IdentityPool

        entry = IdentityPool.get_identity(project, key)
    except Exception as e:  # noqa: BLE001
        _log.warning("[IdentityOrch] 身份解析失败(按缺失处理): %s", e)
        entry = {"_missing": key}
    if not isinstance(entry, dict) or not entry or entry.get("_missing"):
        return None, f"missing:{key}"
    return IdentitySpec.from_pool_entry(key, entry), "resolved"


def load_roster(project: str = "") -> dict[str, IdentitySpec]:
    """身份名册 = 靶子声明角色（基础层） ⊕ 身份池（覆盖层） ⊕ anonymous。

    ★ RC-C(2026-09-24): 靶子 ``auth.roles`` 是凭据的单一事实源，先铺底；身份池
      逐键覆盖/追加。任一声明多角色的靶子即刻可编排，无需人工种子化。

    Returns:
        身份名册；无任何来源时至少含 ``anonymous``（匿名不依赖池，天然可编排）。
    """
    roster: dict[str, IdentitySpec] = {}
    with guard("load_roster: from src.pipeline.utils.identity_poo", log=_log, level=logging.WARNING):
        from src.pipeline.utils.identity_pool import IdentityPool, target_roles

        for key, entry in (target_roles(project) or {}).items():
            if isinstance(entry, dict) and entry:
                roster[key] = IdentitySpec.from_pool_entry(key, entry)
        for key, entry in (IdentityPool.load(project) or {}).items():
            if isinstance(entry, dict) and entry:
                roster[key] = IdentitySpec.from_pool_entry(key, entry)
    roster.setdefault(ROLE_ANONYMOUS, IdentitySpec.anonymous())
    return roster


# ═══════════════════════════════════════════════════════════════════════════
# 登录态隔离（B3 核心缺口）
# ═══════════════════════════════════════════════════════════════════════════


def _get_page(engine) -> Any:
    """从 engine 取 page（兼容 engine.page / engine._page）。"""
    if engine is None:
        return None
    return getattr(engine, "page", None) or getattr(engine, "_page", None)


def _get_context(page) -> Any:
    """从 page 取 browser context（无则 None）。"""
    if page is None:
        return None
    return getattr(page, "context", None)


async def isolate_session(engine) -> dict[str, Any]:
    """清空浏览器登录态 —— **防串号的唯一手段**。

    清理项（逐项独立容错，任何一项失败不影响其它项）：
      1. ``context.clear_cookies()``  —— 会话 cookie / JWT cookie
      2. ``localStorage.clear()``     —— SPA 常用的 token 存放点
      3. ``sessionStorage.clear()``   —— 标签页级会话态

    Args:
        engine: BrowserCore engine。

    Returns:
        证据 dict ``{"cookies_cleared":bool, "local_storage_cleared":bool,
        "session_storage_cleared":bool, "errors":[...]}``。
    """
    evidence: dict[str, Any] = {
        "cookies_cleared": False,
        "local_storage_cleared": False,
        "session_storage_cleared": False,
        "errors": [],
    }
    page = _get_page(engine)
    if page is None:
        evidence["errors"].append("no_page")
        return evidence

    context = _get_context(page)
    if context is not None and hasattr(context, "clear_cookies"):
        try:
            await context.clear_cookies()
            evidence["cookies_cleared"] = True
        except Exception as e:  # noqa: BLE001
            evidence["errors"].append(f"clear_cookies:{type(e).__name__}")
            _log.warning("[IdentityOrch] clear_cookies 失败: %s", str(e)[:100])
    else:
        evidence["errors"].append("no_clear_cookies")

    # localStorage / sessionStorage：about:blank 或跨域受限时 evaluate 会抛，逐项容错
    for label, script in (
        ("local_storage_cleared", "() => { window.localStorage.clear(); return true; }"),
        (
            "session_storage_cleared",
            "() => { window.sessionStorage.clear(); return true; }",
        ),
    ):
        try:
            await page.evaluate(script)
            evidence[label] = True
        except Exception as e:  # noqa: BLE001
            evidence["errors"].append(f"{label}:{type(e).__name__}")
            _log.debug("[IdentityOrch] %s 失败(容错): %s", label, str(e)[:80])

    return evidence


async def session_fingerprint(engine) -> str:
    """当前登录态指纹 —— 用于**证明**隔离生效（切换前后指纹必须不同）。

    指纹取材：cookies 的 ``name=value`` 集合 + localStorage 键集合，排序后 sha1。

    Returns:
        16 位十六进制指纹；无 page → ``"no-page"``；空会话 → ``"empty"`` 的指纹。
    """
    page = _get_page(engine)
    if page is None:
        return "no-page"
    parts: list[str] = []
    context = _get_context(page)
    if context is not None and hasattr(context, "cookies"):
        with guard("session_fingerprint: for ck in await context.cookies() or", log=_log):
            for ck in await context.cookies() or []:
                if isinstance(ck, dict):
                    parts.append(f"{ck.get('name','')}={ck.get('value','')}")
    with guard("session_fingerprint: keys = await page.evaluate('() => Ob", log=_log):
        keys = await page.evaluate("() => Object.keys(window.localStorage)")
        if isinstance(keys, list):
            parts.extend(f"ls:{k}" for k in keys)

    if not parts:
        parts = ["empty"]
    raw = "|".join(sorted(str(p) for p in parts))
    return hashlib.sha1(raw.encode("utf-8", "ignore")).hexdigest()[:16]


# ═══════════════════════════════════════════════════════════════════════════
# override 读写（直接用 target.py 原语，避免与 IDENTITY_POOL_ENABLED 双开关耦合）
# ═══════════════════════════════════════════════════════════════════════════


def _set_override(spec: IdentitySpec) -> None:
    """把身份写入 config 层 override（target_username/target_password 立即生效）。"""
    from src.core.config_loader.target import set_identity_override

    set_identity_override(spec.to_override())


def snapshot_override() -> dict | None:
    """当前 override 快照（None = 未设置，即使用 TARGET_USERNAME 默认身份）。"""
    try:
        from src.core.config_loader.target import _current_identity_override

        return _current_identity_override()
    except Exception:  # noqa: BLE001
        return None


def current_identity_anonymous() -> bool:
    """当前生效身份是否**显式声明为匿名**（未登录态）。

    匿名是显式声明的身份语义，不是"凭据缺失"：调用方据此跳过自动登录，
    否则引擎会先把用户登录了再去断言"未登录应被重定向"（验证手段与期望对立）。
    判定: override 已设置且用户名为空 → 匿名；未设置 override（None）→ 非匿名。
    """
    _ov = snapshot_override()
    if _ov is None:
        return False
    return not str(_ov.get("username") or "").strip()


def restore_override(snapshot: dict | None) -> None:
    """恢复 override 快照（异常只记日志，绝不阻断用例结果）。"""
    with guard("restore_override: from src.core.config_loader.target i", log=_log, level=logging.ERROR):
        from src.core.config_loader.target import (
            clear_identity_override,
            set_identity_override,
        )

        if snapshot is None:
            clear_identity_override()
        else:
            set_identity_override(snapshot)


# ═══════════════════════════════════════════════════════════════════════════
# 身份切换
# ═══════════════════════════════════════════════════════════════════════════


async def switch_identity(
    engine, spec: IdentitySpec, project: str = "", relogin: bool = True
) -> dict[str, Any]:
    """切换到指定身份（**先隔离、再改凭据、最后登录**，顺序不可颠倒）。

    Args:
        engine: BrowserCore engine。
        spec: 目标身份。
        project: 项目名（仅用于日志/证据）。
        relogin: 非匿名身份是否立即重登录（False = 只备好凭据，由后续流程按需登录）。

    Returns:
        切换记录 dict::

            {"ok":bool, "identity":{...}, "isolated":{...},
             "fingerprint_before":str, "fingerprint_after":str,
             "session_changed":bool, "relogin_attempted":bool,
             "relogin_ok":bool, "reason":str}

        ``MULTI_IDENTITY_ENABLED=False`` → ``ok=False, reason="disabled"``（no-op）。
    """
    record: dict[str, Any] = {
        "ok": False,
        "identity": spec.to_dict() if spec else {},
        "isolated": {},
        "fingerprint_before": "",
        "fingerprint_after": "",
        "session_changed": False,
        "relogin_attempted": False,
        "relogin_ok": False,
        "reason": "",
    }
    if not _enabled(project):
        record["reason"] = "disabled"
        return record
    if spec is None:
        record["reason"] = "no_spec"
        return record
    if _get_page(engine) is None:
        record["reason"] = "no_page"
        return record

    record["fingerprint_before"] = await session_fingerprint(engine)

    # ① 隔离旧登录态（防串号，必须最先做）
    record["isolated"] = await isolate_session(engine)

    # ② 切换 config 层凭据（匿名 = 空凭据）
    try:
        _set_override(spec)
    except Exception as e:  # noqa: BLE001
        record["reason"] = f"override_failed:{type(e).__name__}"
        _log.error("[IdentityOrch] override 设置失败: %s", e)
        return record

    # ③ 按需重登录（匿名身份**绝不登录**，否则就不是匿名了）
    if relogin and not spec.is_anonymous:
        record["relogin_attempted"] = True
        record["relogin_ok"] = await _relogin(engine, spec)

    record["fingerprint_after"] = await session_fingerprint(engine)
    record["session_changed"] = (
        record["fingerprint_after"] != record["fingerprint_before"]
    )
    record["ok"] = True
    record["reason"] = "switched"
    _log.info(
        "[IdentityOrch] 身份已切换 → %s(role=%s) 隔离=%s 重登录=%s 会话变更=%s",
        spec.key or "-",
        spec.role,
        record["isolated"].get("cookies_cleared"),
        record["relogin_ok"] if record["relogin_attempted"] else "skip",
        record["session_changed"],
    )
    return record


async def _relogin(engine, spec: IdentitySpec) -> bool:
    """用 spec 凭据重新登录（复用存量确定性登录能力，不新造登录逻辑）。"""
    return await _relogin_creds(engine, spec.username, spec.password)


async def _relogin_creds(engine, username: str, password: str) -> bool:
    """用显式凭据重新登录（复用存量确定性登录能力）。"""
    if not username:
        return False
    try:
        from src.pipeline.steps.step4.browser_ai_login import try_deterministic_login

        return bool(await try_deterministic_login(engine, username, password))
    except Exception as e:  # noqa: BLE001
        _log.warning("[IdentityOrch] 重登录失败(%s): %s", username, str(e)[:120])
        return False


async def restore_default_identity(
    engine, snapshot: dict | None, project: str = ""
) -> dict[str, Any]:
    """用例结束后回到 run 级默认身份（**切走必须切回**的不变量）。

    ★ RC-C(2026-09-24): 访问拒绝类用例把会话切到低权/匿名身份后，若不切回，
      后续用例会带着 viewer/未登录会话执行 → 跨用例串号 → 大面积假 FAIL。
      故 per-case 切换必须配对称的恢复：隔离会话 → 恢复 override 快照 →
      按恢复后的默认凭据重登录。

    Args:
        engine: BrowserCore engine。
        snapshot: ``switch_identity`` 之前由 ``snapshot_override()`` 取得的快照。
        project: 项目名（门控数据驱动判定用）。

    Returns:
        ``{"isolated":{...}, "relogin_ok":bool, "restored":bool}``；
        未启用 → ``{"restored":False}``（零行为变更）。
    """
    result: dict[str, Any] = {"isolated": {}, "relogin_ok": False, "restored": False}
    if not _enabled(project):
        return result
    with guard("restore_default_identity: result['isolated'] = await isolate_s", log=_log, level=logging.WARNING):
        result["isolated"] = await isolate_session(engine)
    restore_override(snapshot)
    result["restored"] = True
    _u = _p = ""
    with guard("restore_default_identity: from src.core.config_loader import c", log=_log):
        from src.core.config_loader import config as _cfg

        _u = str(getattr(_cfg, "target_username", "") or "")
        _p = getattr(_cfg, "target_password", "") or ""
    if _u:
        result["relogin_ok"] = await _relogin_creds(engine, _u, _p)
    _log.info(
        "[IdentityOrch] 已恢复 run 级默认身份: user=%r 隔离=%s 重登录=%s",
        _u or "-",
        result["isolated"].get("cookies_cleared"),
        result["relogin_ok"],
    )
    return result


async def release_identity(
    engine, snapshot: dict | None, project: str = ""
) -> dict[str, Any]:
    """用例/批次结束后释放身份：隔离会话 + 恢复 override 快照。

    Args:
        engine: BrowserCore engine。
        snapshot: ``switch_identity`` 之前由 ``snapshot_override()`` 取得的快照。

    Returns:
        ``{"isolated":{...}, "restored":bool}``；未启用 → ``{"restored":False}``。
    """
    result: dict[str, Any] = {"isolated": {}, "restored": False}
    if not _enabled(project):
        return result
    with guard("release_identity: result['isolated'] = await isolate_s", log=_log, level=logging.WARNING):
        result["isolated"] = await isolate_session(engine)
    restore_override(snapshot)
    result["restored"] = True
    return result


# ═══════════════════════════════════════════════════════════════════════════
# 与 GAP-05 越权检测协同
# ═══════════════════════════════════════════════════════════════════════════


def build_security_check(
    spec: IdentitySpec,
    target: str = "",
    methods: list[str] | None = None,
    expect_denied_status: list[int] | None = None,
    fail_on_bypass: bool = False,
) -> dict[str, Any]:
    """依据当前身份产出 GAP-05 的 ``checks["security_check"]`` 声明。

    协同逻辑（verification/__init__.py ``_flag_security_authz_bypass`` 的入参契约）：
      - 低权限身份（user / anonymous）访问受保护端点 → ``assume_unauthorized=True``，
        使 GAP-05 无需依赖 URL 里的身份标记即可判定「整步在未授权身份下执行」；
      - 同时给出 ``unauthorized_markers``（``role=<role>`` / 用户名），
        兼容既有基于标记的匹配路径；
      - admin 身份 → ``authz_gated=False``（高权限访问受保护端点是**正常**行为，
        不应被判越权，避免误报）。

    Args:
        spec: 当前执行身份。
        target: 受保护端点 URL pattern（如 ``/api/admin/*``）。
        methods: 受保护方法列表。
        expect_denied_status: 期望的拒绝状态码（默认 ``[401, 403]``）。
        fail_on_bypass: True = 命中越权时把 ``passed`` 置 False（opt-in 硬失败）。

    Returns:
        GAP-05 声明 dict（可直接放进 verify_step 的 checks 字典）。
    """
    denied = [int(s) for s in (expect_denied_status or [401, 403])]
    if spec is None or spec.is_privileged:
        return {
            "authz_gated": False,
            "target": target,
            "methods": list(methods or []),
            "unauthorized_markers": [],
            "assume_unauthorized": False,
            "expect_denied_status": denied,
            "fail_on_bypass": bool(fail_on_bypass),
            "_identity_role": spec.role if spec else "",
        }
    markers = [f"role={spec.role}", f"identity={spec.key or spec.role}"]
    if spec.username:
        markers.append(spec.username)
    return {
        "authz_gated": True,
        "target": target,
        "methods": list(methods or []),
        "unauthorized_markers": markers,
        # 低权限身份下整步执行 → 无需 URL 标记即可判定未授权（引擎已真正换了身份）
        "assume_unauthorized": True,
        "expect_denied_status": denied,
        "fail_on_bypass": bool(fail_on_bypass),
        "_identity_role": spec.role,
    }


def attach_security_check(
    checks: dict[str, Any],
    spec: IdentitySpec,
    target: str = "",
    methods: list[str] | None = None,
    expect_denied_status: list[int] | None = None,
    fail_on_bypass: bool = False,
) -> dict[str, Any]:
    """把身份感知的 ``security_check`` 注入 verify_step 的 checks 字典（原地增强）。

    这是 B3 ↔ GAP-05 的**注入点**：``verify_step(api_tracker, t0, checks, ...)``
    收到的 checks 字典带上 ``security_check`` 后，``_flag_security_authz_bypass``
    即可在 API 轨上判定越权。已存在 ``security_check`` 时不覆盖（用例显式声明优先）。

    Args:
        checks: verify_step 的 checks 字典。
        spec: 当前执行身份。
        target: 受保护端点 URL pattern。
        methods: 受保护方法列表。
        expect_denied_status: 期望拒绝状态码。
        fail_on_bypass: 命中越权是否硬失败。

    Returns:
        同一个 checks 字典（便于链式调用）。
    """
    if not isinstance(checks, dict):
        return checks
    if isinstance(checks.get("security_check"), dict) and checks["security_check"]:
        return checks  # 用例显式声明优先，绝不覆盖
    checks["security_check"] = build_security_check(
        spec,
        target=target,
        methods=methods,
        expect_denied_status=expect_denied_status,
        fail_on_bypass=fail_on_bypass,
    )
    return checks


# ═══════════════════════════════════════════════════════════════════════════
# 批次编排
# ═══════════════════════════════════════════════════════════════════════════


def build_execution_plan(
    cases: list[dict], project: str = ""
) -> dict[str, Any]:
    """把一批用例按身份分组，产出「按身份切换最少次数」的执行计划。

    同一身份的用例聚成一组连续执行 → 切换次数 = 组数（而非用例数），
    既省登录开销又减少串号窗口。未声明身份的用例归入 ``__default__`` 组。

    Args:
        cases: 用例列表。
        project: 项目名。

    Returns:
        ``{"groups":[{"identity":{...}|None, "identity_key":str, "reason":str,
        "case_ids":[...], "cases":[...]}], "blocked":[{"case_id","reason"}],
        "switch_count":int}``；``blocked`` = 声明了身份但池中缺失（fail-closed）。
    """
    groups: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    blocked: list[dict[str, str]] = []

    for case in cases or []:
        if not isinstance(case, dict):
            continue
        case_id = str(case.get("case_id", "") or "").strip()
        spec, reason = resolve_identity_spec(case, project)
        if spec is None and reason.startswith("missing:"):
            blocked.append({"case_id": case_id, "reason": reason})
            continue
        group_key = spec.key if spec is not None else "__default__"
        if group_key not in groups:
            groups[group_key] = {
                "identity": spec.to_dict() if spec is not None else None,
                "identity_key": group_key,
                "reason": reason,
                "case_ids": [],
                "cases": [],
                "_spec": spec,
            }
            order.append(group_key)
        groups[group_key]["case_ids"].append(case_id)
        groups[group_key]["cases"].append(case)

    ordered = [groups[k] for k in order]
    return {
        "groups": ordered,
        "blocked": blocked,
        "switch_count": len([g for g in ordered if g["_spec"] is not None]),
    }


class IdentityOrchestrator:
    """多身份编排器：按计划切换身份执行一批用例（登录态逐组隔离）。

    典型用法::

        orch = IdentityOrchestrator(engine, project="demo")
        plan = orch.plan(cases)
        for group in plan["groups"]:
            await orch.enter(group)     # 隔离 + 切身份 + 登录
            ...  # 执行 group["cases"]
            await orch.leave()          # 隔离 + 恢复快照
    """

    def __init__(self, engine: Any, project: str = ""):
        """初始化编排器。

        Args:
            engine: BrowserCore engine。
            project: 项目名（身份池按项目隔离）。
        """
        self.engine = engine
        self.project = str(project or "")
        self._snapshot: dict | None = None
        self._active: IdentitySpec | None = None
        self.switch_records: list[dict[str, Any]] = []

    @property
    def active_identity(self) -> IdentitySpec | None:
        """当前生效身份（None = 默认身份）。"""
        return self._active

    def plan(self, cases: list[dict]) -> dict[str, Any]:
        """按身份分组产出执行计划（见 ``build_execution_plan``）。"""
        return build_execution_plan(cases, self.project)

    async def enter(self, group: dict[str, Any]) -> dict[str, Any]:
        """进入一个身份组：快照 override → 切换身份（含隔离 + 登录）。

        Args:
            group: ``plan()["groups"]`` 中的一项。

        Returns:
            切换记录（无身份声明的默认组 → ``{"ok":True,"reason":"default_identity"}``）。
        """
        self._snapshot = snapshot_override()
        spec = group.get("_spec") if isinstance(group, dict) else None
        if spec is None:
            self._active = None
            return {"ok": True, "reason": "default_identity", "identity": None}
        record = await switch_identity(self.engine, spec, self.project)
        self._active = spec if record.get("ok") else None
        self.switch_records.append(record)
        return record

    async def leave(self) -> dict[str, Any]:
        """离开当前身份组：隔离会话 + 恢复 override 快照。"""
        result = await release_identity(self.engine, self._snapshot)
        self._active = None
        self._snapshot = None
        return result

    def security_check_for_active(
        self,
        target: str = "",
        methods: list[str] | None = None,
        fail_on_bypass: bool = False,
    ) -> dict[str, Any]:
        """当前身份对应的 GAP-05 声明（无活动身份 → 视为匿名，最保守）。"""
        spec = self._active or IdentitySpec.anonymous()
        return build_security_check(
            spec, target=target, methods=methods, fail_on_bypass=fail_on_bypass
        )

    def summary(self) -> dict[str, Any]:
        """编排摘要（供证据落盘；★ 不含密码明文）。"""
        return {
            "project": self.project,
            "active": self._active.to_dict() if self._active else None,
            "switches": len(self.switch_records),
            "records": [
                {
                    "identity": r.get("identity"),
                    "ok": r.get("ok"),
                    "session_changed": r.get("session_changed"),
                    "relogin_ok": r.get("relogin_ok"),
                    "reason": r.get("reason"),
                }
                for r in self.switch_records
            ],
        }

    def to_json(self) -> str:
        """摘要 JSON（便于写入证据文件）。"""
        return json.dumps(self.summary(), ensure_ascii=False, indent=2)
