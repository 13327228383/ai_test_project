# -*- coding: utf-8 -*-
"""身份池容器（W3 T03，L0-2）—— 零 DDL：user_credentials_v2 单行 key=IDENTITY_POOL。

Fernet 加密复用 src/users/credentials.py 存量机制（_encrypt_auto/_safe_decrypt）。

设计（W3_DESIGN.md §2 L0-2）：
  - load(project)：读 user_credentials_v2 key=IDENTITY_POOL → 解密 JSON → {identity_key: {...}}
  - save(project, pool)：写回 IDENTITY_POOL key（仅当 IDENTITY_POOL_ENABLED）
  - resolve(case, project)：case["identity"] 或 case.payload["identity"] → 池中身份；
    未声明 → None（默认身份）；声明但不存在 → {"_missing": key}（调用方 BLOCKED）
  - apply_identity_override / clear_identity_override：config 层 override（默认 None 零行为变更）

★ 安全铁律：池内容必须经 _encrypt_auto 加密落库；读取经 _safe_decrypt。

★ W3 T03 关键改动 [已复核-DeepSeek-V4-Flash 2026-08-05]（2026-08-05）：
  身份池容器 + override 应用；零 DDL（user_credentials_v2 单行 key=IDENTITY_POOL）。
  默认 IDENTITY_POOL_ENABLED=False → apply_identity_override no-op（零行为变更）。
"""
from __future__ import annotations
from src.core.guarded import guard

import json
import logging
from typing import Any

from src.core.pipeline_config import _read_config_value as _cfg

_log = logging.getLogger(__name__)

# identity override 读写委托 config_loader/target.py（模块级 contextvar）
from src.core.config_loader.target import (  # noqa: E402
    clear_identity_override as _clear_override,
    set_identity_override as _set_override,
)


# ═══════════════════════════════════════════════════════════════════════════
# 靶子声明角色 —— 身份来源的**基础层**（身份池 = 可选覆盖层）
# ═══════════════════════════════════════════════════════════════════════════
# ★ 根因修复(2026-09-24) RC-C: 身份来源必须分层，否则 RBAC 靶子无法编排。
#   根因: IdentityPool 只读 user_credentials_v2 单行 key=IDENTITY_POOL（默认空），
#     而靶子配置 auth.roles 早已声明 admin/viewer 凭据 → resolve 恒返回
#     {"_missing":"viewer"} → 访问拒绝类用例（"viewer 不允许新增/编辑/删除 403"）
#     无法切换低权身份 → 一律以 run 级默认身份（admin）执行 → 页面呈现 admin 视图
#     （新增按钮可见、可直接访问）→ 断言"应被拒绝"必然假 FAIL；且真实越权漏洞
#     （SUT 漏做角色校验）永远不可能被检出 —— 这是"验证手段与期望性质错配"的
#     又一表现，属引擎缺陷，不是 SUT 缺陷。
#   通用性: 角色凭据的单一事实源本就是靶子配置（auth.roles，与登录配置同处）；
#     身份池是**可选覆盖层**（用于覆盖/追加非配置角色）。分层后任何声明了 roles
#     的靶子即刻可编排，无需人工种子化，零业务硬编码。
#   自我反驳: 会不会把"未声明角色"误判为已解析? → 不会：target_roles 只返回
#     靶子显式声明的角色键，未命中仍返回 {"_missing": key}（调用方 fail-closed）。

_TARGET_KEY_CACHE: dict[str, str] = {}


def _normalize_url(u: Any) -> str:
    """URL 归一化（去空白 / 去尾部斜杠 / 小写）——绑定比对用。"""
    return str(u or "").strip().rstrip("/").lower()


def _match_by_project_binding(project: str) -> str:
    """``project_configs.TARGET_URL`` → TARGET_CFGS key（项目名 ≠ 靶子 key 的桥接）。

    ★ 根因修复(2026-09-24) RC-C 前置断链：项目→靶子的绑定**事实源**是
      ``project_configs.config_json`` 的 ``TARGET_URL``（引擎按该 URL 访问被测系统），
      而不是项目名本身。实测项目 ``accept_sut_final`` 绑定
      ``http://127.0.0.1:8050``（= fullcov_sut 靶子），但项目名不含靶子 key →
      仅按 key 匹配必然失败 → 角色凭据解析为空 → 访问拒绝用例仍以 run 级默认身份
      （admin）执行 → 假 FAIL。身份编排在**更前一段**就失效了，故必须在此补齐。

    通用性: 只依赖"项目配置声明了被测 URL"这一通用事实，零业务硬编码；
      任何以 URL 绑定靶子的项目（含改名/多项目共用同一靶子）均受益。
    """
    p = str(project or "").strip()
    if not p:
        return ""
    with guard("_match_by_project_binding: from src.core.db_manager import get_", log=_log):
        from src.core.db_manager import get_connection
        from src.pipeline.steps.step4.empirical.target_cfgs import TARGET_CFGS

        conn = get_connection()
        try:
            row = conn.fetchone_sql(
                "SELECT config_json FROM project_configs WHERE project=%s", (p,)
            )
        finally:
            conn.close()
        if isinstance(row, dict):
            raw = row.get("config_json") or ""
        elif row:
            raw = row[0] or ""
        else:
            raw = ""
        if not raw:
            return ""
        cfg = json.loads(raw) if isinstance(raw, str) else (raw or {})
        if not isinstance(cfg, dict):
            return ""
        url = _normalize_url(cfg.get("TARGET_URL") or cfg.get("target_url") or "")
        if not url:
            return ""
        for k, v in TARGET_CFGS.items():
            for cand in (
                _normalize_url((v or {}).get("url")),
                _normalize_url((v or {}).get("base_url")),
            ):
                if cand and cand == url:
                    _log.info(
                        "[IdentityPool] 项目 %s 经 TARGET_URL=%s 绑定靶子 %s",
                        p, url, k,
                    )
                    return k
    return ""


def match_target_key(project: str) -> str:
    """project 名 → TARGET_CFGS key（精确 → 大小写不敏感 → target_key 字段 → TARGET_URL 绑定）。

    返回 "" 表示无法解析（调用方按无角色处理，零行为变更）。
    """
    p = str(project or "").strip()
    if not p:
        return ""
    cached = _TARGET_KEY_CACHE.get(p)
    if cached is not None:
        return cached
    key = ""
    try:
        from src.pipeline.steps.step4.empirical.target_cfgs import TARGET_CFGS

        if p in TARGET_CFGS:
            key = p
        else:
            _low = p.lower()
            for k in TARGET_CFGS:
                if k.lower() == _low:
                    key = k
                    break
            if not key:
                for k, v in TARGET_CFGS.items():
                    _tk = str((v or {}).get("target_key") or "")
                    if _tk and _tk.lower() == _low:
                        key = k
                        break
    except Exception as e:  # noqa: BLE001
        _log.debug("[IdentityPool] target_cfgs 解析失败(按无角色处理): %s", str(e)[:80])
        key = ""
    if not key:
        # ★ RC-C 前置断链补齐(2026-09-24)：项目名 ≠ 靶子 key 时经 TARGET_URL 绑定解析。
        key = _match_by_project_binding(p)
    _TARGET_KEY_CACHE[p] = key
    return key


def target_roles(project: str) -> dict[str, dict]:
    """靶子配置 ``auth.roles`` → ``{role_key: {username, password, ...}}``。

    这是身份来源的**基础层**（身份池为覆盖层）。未声明 roles / 无法解析靶子 → ``{}``。
    """
    key = match_target_key(project)
    if not key:
        return {}
    try:
        from src.pipeline.steps.step4.empirical.target_cfgs import get_target_cfg

        cfg = get_target_cfg(key)
    except Exception as e:  # noqa: BLE001
        _log.debug("[IdentityPool] 读取靶子 %s 配置失败: %s", key, str(e)[:80])
        return {}
    roles = ((cfg.get("auth") or {}).get("roles")) or {}
    return {
        str(k): dict(v)
        for k, v in roles.items()
        if isinstance(v, dict) and v
    }


def default_role_key(project: str) -> str:
    """run 级默认身份对应的角色键（角色 username == auth.login_fields.username）。

    用于"切走必须切回"：用例结束恢复到 run 级默认身份。无法判定 → ""。
    """
    key = match_target_key(project)
    if not key:
        return ""
    try:
        from src.pipeline.steps.step4.empirical.target_cfgs import get_target_cfg

        auth = (get_target_cfg(key).get("auth") or {})
    except Exception:  # noqa: BLE001
        return ""
    _lf = auth.get("login_fields") or {}
    _default_user = str(_lf.get("username") or "").strip()
    roles = auth.get("roles") or {}
    if _default_user:
        for rk, rv in roles.items():
            if isinstance(rv, dict) and str(rv.get("username") or "").strip() == _default_user:
                return str(rk)
    return ""


class IdentityPool:
    """身份池容器（JSON 容器，零 DDL）。"""

    @staticmethod
    def _enabled() -> bool:
        return bool(_cfg("IDENTITY_POOL_ENABLED", False))

    @staticmethod
    def _pool_key() -> str:
        return str(
            _cfg("IDENTITY_POOL_KEY", "IDENTITY_POOL") or "IDENTITY_POOL"
        ).strip()

    @staticmethod
    def load(project: str) -> dict[str, dict]:
        """读 user_credentials_v2 key=IDENTITY_POOL → 解密 JSON → {identity_key: {...}}。

        查询失败/解密失败/非 JSON → 返回 {}（调用方按缺失处理）。
        """
        key = IdentityPool._pool_key()
        project = str(project or "").strip()
        try:
            from src.users.credentials import get_user_credential

            raw = get_user_credential("admin", key, project or "*")
            if not raw:
                return {}
            data = json.loads(raw)
            if not isinstance(data, dict):
                return {}
            return {str(k): (v if isinstance(v, dict) else {}) for k, v in data.items()}
        except Exception as e:  # noqa: BLE001
            _log.warning("[IdentityPool] 读取身份池失败(按空池处理): %s", e)
            return {}

    @staticmethod
    def save(project: str, pool: dict) -> bool:
        """写回 IDENTITY_POOL key（仅当 IDENTITY_POOL_ENABLED）。"""
        if not IdentityPool._enabled():
            _log.info("[IdentityPool] IDENTITY_POOL_ENABLED=False，拒绝写身份池")
            return False
        key = IdentityPool._pool_key()
        project = str(project or "").strip()
        try:
            from src.users.credentials import set_user_credential

            raw = json.dumps(pool or {}, ensure_ascii=False)
            set_user_credential("admin", key, raw, project or "*")
            return True
        except Exception as e:  # noqa: BLE001
            _log.error("[IdentityPool] 保存身份池失败: %s", e)
            return False

    @staticmethod
    def resolve(case: dict, project: str) -> dict | None:
        """case["identity"]（str key）或 case.payload["identity"] → 身份凭据。

        分层解析（★ RC-C 通用修复 2026-09-24）：
          1. 身份池（user_credentials_v2 key=IDENTITY_POOL，**覆盖层**，可加密落库）
          2. 靶子配置 auth.roles（**基础层**，与登录配置同源，零人工种子化）
          3. 大小写宽松匹配（"Viewer" → "viewer"）
        未声明 → None（默认身份）；声明但两层皆未命中 → {"_missing": key}（调用方 fail-closed）。
        """
        if not isinstance(case, dict):
            return None
        identity_key = str(case.get("identity", "") or "").strip()
        payload = case.get("payload")
        if not identity_key and isinstance(payload, dict):
            identity_key = str(payload.get("identity", "") or "").strip()
        if not identity_key:
            return None
        return IdentityPool.get_identity(project, identity_key)

    @staticmethod
    def get_identity(project: str, identity_key: str) -> dict | None:
        """按身份键分层解析凭据（池 → 靶子配置 → 大小写宽松）。

        返回 ``None`` 表示"未声明/未知"语义由调用方决定：本方法对未命中返回
        ``{"_missing": key}``（与 resolve 契约一致）。
        """
        key = str(identity_key or "").strip()
        if not key:
            return None
        pool = IdentityPool.load(project)
        if key in pool:
            return dict(pool[key])
        roles = target_roles(project)
        if key in roles:
            return dict(roles[key])
        _low = key.lower()
        for src in (pool, roles):
            for k, v in src.items():
                if str(k).lower() == _low:
                    return dict(v)
        return {"_missing": key}

    # ───────────────────────────────────────────────────────────────────────
    # 2026-08-12 新增：从页面自动提取用户角色信息
    # ───────────────────────────────────────────────────────────────────────

    # DOM 选择器优先级列表：按常见 UI 框架（Ant Design / Element UI / 通用）排列
    _USERNAME_SELECTORS = [
        "[data-username]",
        ".user-name",
        ".username",
        ".user-info .name",
        ".ant-dropdown-link",
        ".el-dropdown-link",
        ".user-nickName",
        ".nickname",
        ".header-user .name",
        '[class*="userInfo"] [class*="name"]',
        '[class*="user-name"]',
    ]
    _ROLE_SELECTORS = [
        "[data-role]",
        ".user-role",
        ".role-name",
        ".role-badge",
        ".ant-tag-role",
        ".el-tag",
        '[class*="roleName"]',
        '[class*="role-tag"]',
    ]
    _PERMISSION_HINT_SELECTORS = [
        ".ant-menu-item",
        ".el-menu-item",
        ".sidebar-menu li",
        "nav a",
        '[class*="menu-item"]',
    ]

    @staticmethod
    async def _safe_text(page, selector: str) -> str:
        """安全提取选择器首个匹配元素的文本内容。"""
        with guard("_safe_text: loc = page.locator(selector).first", log=_log):
            loc = page.locator(selector).first
            if await loc.count() > 0:
                text = await loc.text_content()
                return str(text or "").strip()
        return ""

    @staticmethod
    async def _safe_attr(page, selector: str, attr: str) -> str:
        """安全提取选择器首个匹配元素的属性值。"""
        with guard("_safe_attr: loc = page.locator(selector).first", log=_log):
            loc = page.locator(selector).first
            if await loc.count() > 0:
                val = await loc.get_attribute(attr)
                return str(val or "").strip()
        return ""

    @staticmethod
    async def _collect_texts(page, selector: str) -> list[str]:
        """安全提取选择器所有匹配元素的文本列表。"""
        texts: list[str] = []
        with guard("_collect_texts: loc = page.locator(selector)", log=_log):
            loc = page.locator(selector)
            count = await loc.count()
            for i in range(count):
                t = await loc.nth(i).text_content()
                t = str(t or "").strip()
                if t:
                    texts.append(t)
        return texts

    @staticmethod
    async def auto_detect_identities(page) -> dict[str, Any]:
        """从页面 DOM 自动提取用户角色信息。

        解析策略（通用，零业务硬编码）：
          1. 用户名：遍历 _USERNAME_SELECTORS，取首个命中文本
             或 data-username 属性
          2. 角色：遍历 _ROLE_SELECTORS，取 data-role 属性或文本
          3. 权限提示：收集可见菜单项文本，推断 permissions 列表
          4. data_scope：无显式标识，留空（由调用方/手动补充）

        参数:
            page: Playwright Page 对象（需已加载页面）。

        返回:
            {username, role, permissions:[...], data_scope:""}
            解析失败/page 为 None → 返回 {}。
            多角色场景：仅返回当前登录用户的身份信息。

        ★ IDENTITY_POOL_ENABLED=False 时仍可调用（纯读 DOM，不写库）；
          结果可通过 save() 方法写入身份池。
        """
        if page is None:
            return {}
        identity: dict[str, Any] = {
            "username": "",
            "role": "",
            "permissions": [],
            "data_scope": "",
        }
        try:
            # 1) 用户名：优先 data-username 属性，其次文本
            for sel in IdentityPool._USERNAME_SELECTORS:
                if sel.startswith("[data-"):
                    attr_name = sel.strip("[]")
                    val = await IdentityPool._safe_attr(page, sel, attr_name)
                    if val:
                        identity["username"] = val
                        break
                else:
                    val = await IdentityPool._safe_text(page, sel)
                    if val and len(val) < 100:  # 防止取到大段文本
                        identity["username"] = val
                        break

            # 2) 角色：优先 data-role 属性，其次文本
            for sel in IdentityPool._ROLE_SELECTORS:
                if sel.startswith("[data-"):
                    attr_name = sel.strip("[]")
                    val = await IdentityPool._safe_attr(page, sel, attr_name)
                    if val:
                        identity["role"] = val.lower()
                        break
                else:
                    val = await IdentityPool._safe_text(page, sel)
                    if val and len(val) < 50:
                        identity["role"] = val.lower()
                        break

            # 3) 权限提示：收集可见菜单项文本
            for sel in IdentityPool._PERMISSION_HINT_SELECTORS:
                texts = await IdentityPool._collect_texts(page, sel)
                if texts:
                    identity["permissions"] = texts[:50]  # 防爆炸
                    break

            # 4) 如果用户名/角色都未提取到，返回空（避免误存空身份）
            if not identity["username"] and not identity["role"]:
                _log.debug("[IdentityPool] auto_detect 未提取到用户/角色信息")
                return {}

            _log.info(
                "[IdentityPool] auto_detect: username=%s role=%s permissions=%d",
                identity["username"],
                identity["role"],
                len(identity["permissions"]),
            )
            return identity
        except Exception as e:  # noqa: BLE001
            _log.warning("[IdentityPool] auto_detect_identities 异常(返回空): %s", e)
            return {}

    @staticmethod
    async def detect_and_save_identities(page, project: str) -> bool:
        """便捷方法：自动检测身份 → 存入身份池。

        检测结果以 username 为 key 存入池中（若 username 为空则用 role）。
        仅当 IDENTITY_POOL_ENABLED=True 时写入（save 门控）。

        返回:
            True=检测并写入成功；False=未检测到 / 未启用 / 写入失败。
        """
        if not IdentityPool._enabled():
            _log.info("[IdentityPool] IDENTITY_POOL_ENABLED=False，跳过自动检测")
            return False
        detected = await IdentityPool.auto_detect_identities(page)
        if not detected:
            return False
        pool = IdentityPool.load(project)
        key = str(detected.get("username", "") or "").strip()
        if not key:
            key = str(detected.get("role", "") or "detected_user").strip()
        pool[key] = detected
        return IdentityPool.save(project, pool)


def apply_identity_override(project: str, identity: dict | None) -> None:
    """设置 config 层身份 override（模块级 contextvar + 锁）。

    override 非 None 时 target_username/target_password 优先返回 override 值。
    IDENTITY_POOL_ENABLED=False 时 no-op（零行为变更）。
    """
    if not IdentityPool._enabled():
        return
    if identity is None:
        _clear_override()
        return
    _set_override(
        {
            "username": str(identity.get("username", "") or "").strip(),
            "password": str(identity.get("password", "") or "").strip(),
        }
    )


def clear_identity_override() -> None:
    """清除 identity override（恢复 TARGET_USERNAME/PASSWORD 单身份）。"""
    with guard("clear_identity_override: _clear_override()", log=_log):
        _clear_override()


def apply_case_identity(project: str, case: dict) -> bool:
    """per-case 身份热切换（W4-C）：resolve(case) 命中且 PER_CASE_IDENTITY_ENABLED
    → apply_identity_override → True；未声明 / 身份缺失 / 开关关闭 → False（零行为变更）。

    调用方约定：返回 True 时必须在用例执行后 finally 恢复（见 snapshot_identity_override）。
    """
    if not bool(_cfg("PER_CASE_IDENTITY_ENABLED", False)):
        return False
    if not IdentityPool._enabled():
        return False
    _ident = IdentityPool.resolve(case, project)
    if not _ident or _ident.get("_missing"):
        return False
    apply_identity_override(project, _ident)
    return True


def snapshot_identity_override() -> dict | None:
    """当前生效的身份 override（None=未设置）。供 per-case 切换 finally 恢复 run 级身份。"""
    return _current_identity_override()


def restore_identity_override(project: str, snapshot: dict | None) -> None:
    """恢复 per-case 切换前的身份 override（快照语义，★ FIX-2 2026-08-06 QA）。

    apply_identity_override(project, None) → 清除；传 dict → 恢复 run 级默认身份。
    任何异常只记 error 日志、绝不向上抛（不掩蔽用例执行结果）。
    """
    with guard("restore_identity_override: apply_identity_override(project, sna", log=_log, level=logging.ERROR):
        apply_identity_override(project, snapshot)


def _current_identity_override() -> dict | None:
    """当前线程/协程生效的身份 override（供 target.py 读取）。"""
    try:
        from src.core.config_loader.target import _current_identity_override as _cur

        return _cur()
    except Exception:  # noqa: BLE001
        return None
