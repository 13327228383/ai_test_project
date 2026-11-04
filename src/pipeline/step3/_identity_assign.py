# -*- coding: utf-8 -*-
"""访问控制型用例的执行身份指派。

在 Step3 落库前为**访问控制型用例**指派执行身份并随 ``payload_json`` 持久化；
Step4 ``identity_orchestrator`` 据此「先隔离会话 → 再改凭据 → 最后重登录」执行，
用例结束后对称切回 run 级默认身份。不指派时恒以 run 级默认身份执行（零行为变更）。

通用性（零业务硬编码）
----------------------
- 角色来源 = 靶子 ``auth.roles``（凭据单一事实源），不写死任何角色名；
- **靶子未声明 ≥2 角色 → 恒不指派**（无法构造低权身份）；
- 用例已显式声明 identity → 不覆盖（尊重上游显式意图）；
- **用例的执行身份由该用例自身断言决定**，需求类别只决定"可用哪些身份"：
  低权身份的唯一合法依据 = 断言"应被拒绝/不可达"；匿名身份的唯一合法依据 =
  断言涉及"未认证态/登录流程"。需求级文本仅用于选定具体角色。
"""

from __future__ import annotations

import logging
import re

# 拒绝/越权语义：复用 Step3 权威判据（`_rule_extractor_keywords._REDIRECT_LOGIN_RE`），
# 本模块不再自持第二份词表，避免两处漂移。
from src.pipeline.step3._rule_extractor_keywords import _REDIRECT_LOGIN_RE

_log = logging.getLogger(__name__)

# 角色/权限语境（无显式角色名时，据此认定"按角色控制"型需求）。
# 只收**授权语义**词（无权/权限不足/访问控制…）；裸"拒绝"不具授权语义
# （如"重复名称被拒绝"是数据校验），不纳入。
_ROLE_CTX_RE = re.compile(
    r"仅管理员|仅限管理员|管理员可|管理员才能|其他角色|非管理员|"
    r"角色访问|按角色|角色权限|权限控制|权限策略|权限校验|越权|"
    r"无权|无权限|权限不足|权限受限|访问控制|授权|"
    r"\brole\b|\bprivilege\b|\brbac\b|\b403\b|\b401\b",
    re.IGNORECASE,
)


# ── 断言级身份门控 ────────────────────────────────────────────────────
#   身份由**用例自身断言**决定，需求类别只决定"可用哪些身份"——否则需求一旦被判为
#   访问控制型，其全部派生用例（兼容/性能/UI/正向功能）会整体继承低权身份，在
#   受保护页面必然 403 → 断言与执行手段错配的假 FAIL。
#   低权身份的唯一合法依据 = 断言"应被拒绝/不可达"。
_DENY_ASSERTION_RE = re.compile(
    # "应看到错误页/错误提示/404" 与 "403 权限不足" 同属**负向可达性断言**。
    r"拒绝|拦截|无权|无权限|权限不足|权限受限|越权|未授权|禁止访问|不允许访问|"
    r"不可访问|不能访问|重定向[^。；;]{0,10}登录|跳转[^。；;]{0,10}登录|登录墙|"
    r"错误提示|错误页|异常提示|显示错误|提示错误|error_path|"
    r"\b403\b|\b401\b|\b404\b|forbidden|unauthorized|access\s+denied|not\s+found",
    re.IGNORECASE,
)
# 「未认证态」语义的**唯一**词表：需求级（pick_identity）与断言级（case_expects_auth）
# 共用。匿名身份的前提是被测时点为未认证态；"认证通过 / 登录成功 / 凭证有效"是
# 已认证态，恰好相反，不得纳入。
# 「重定向/跳转至登录页」子语义委托 `_REDIRECT_LOGIN_RE`（单一事实源，勿在本表复述）。
_UNAUTH_STATE_RE = re.compile(
    r"未登录|未认证|未授权访问|匿名|游客|"
    r"登录页|登录界面|登录表单|登录框|"
    + _REDIRECT_LOGIN_RE.pattern
    + r"|"
    r"会话超时|会话过期|注销后|"
    r"登录失败|登陆失败|认证失败|鉴权失败|密码错误|口令错误|凭证错误|凭证无效|"
    r"用户名或密码|账号或密码|"
    r"\banonymous\b|\bunauthenticated\b|\bnot\s+logged\s+in\b",
    re.IGNORECASE,
)
# 登出/注销语义：被测前提是**已认证**（退出入口仅在登录态存在），与登录用例相反。
_LOGOUT_ASSERTION_RE = re.compile(
    r"退出|注销|登出|logout|sign\s*out", re.IGNORECASE
)
# 引擎契约中表达"应被拒绝/不应存在"的断言类型（单一来源见 _track_protocol 注册表）。
_DENY_CHECK_TYPES = frozenset({"access_control_check"})

# 否定语境前缀：出现在拒绝关键词**紧前**时，该处不是拒绝断言而是"要求正常"
# （"**非**错误页""**无**错误提示""**不**出现 403""**避免**权限不足"）。
# 不排除会让极性反转 —— 断言"页面正常"的用例被当成"期望被拒绝"，从而不再
# 把真实拒绝页当失败（假 PASS 风险）并错误指派低权身份（D39 同族缺陷）。
_DENY_NEG_PREFIX_1 = ("非", "无", "不", "没")
_DENY_NEG_PREFIX_2 = ("避免", "防止", "杜绝")


def _has_affirmative_denial(text: str) -> bool:
    """``_DENY_ASSERTION_RE`` 中是否有**非否定语境**的命中。"""
    for m in _DENY_ASSERTION_RE.finditer(text):
        s = m.start()
        if s >= 1 and text[s - 1] in _DENY_NEG_PREFIX_1:
            continue
        if s >= 2 and text[s - 2 : s] in _DENY_NEG_PREFIX_2:
            continue
        return True
    return False


def _case_check_blobs(case: dict) -> str:
    """拼装**用例自身**的身份相关文本：用例类型/标题 + 全部断言的类型与期望。

    ★ 与 :func:`_req_text` 的分工：
      ``_req_text`` 回答"需求是不是访问控制型"（决定**候选**身份）；
      ``_case_check_blobs`` 回答"这条用例自身要验证什么"（决定**是否需要**该身份）。
      身份是执行手段，只能由被测断言决定；需求类别不得让全部派生用例继承该身份。
    """
    parts = [str(case.get("case_type") or ""), str(case.get("title") or "")]
    for ch in case.get("checks") or []:
        if not isinstance(ch, dict):
            continue
        parts.append(str(ch.get("expect") or ""))
        _ck = ch.get("check")
        if isinstance(_ck, dict):
            parts.append(str(_ck.get("type") or ""))
            parts.append(str(_ck.get("expected") or ""))
        else:
            parts.append(str(_ck or ""))
    return " ".join(p for p in parts if p)


def case_expects_denial(case: dict) -> bool:
    """用例自身断言是否要求「被拒绝/不可达」（低权身份的唯一合法依据）。"""
    for ch in case.get("checks") or []:
        if not isinstance(ch, dict):
            continue
        _ck = ch.get("check")
        _t = (_ck.get("type") if isinstance(_ck, dict) else None) or ch.get("type")
        if str(_t or "") in _DENY_CHECK_TYPES:
            return True
    return _has_affirmative_denial(_case_check_blobs(case))


def case_expects_logout(case: dict) -> bool:
    """用例自身断言是否涉及**登出/注销**（被测前提是已认证，故绝不可匿名）。"""
    return bool(_LOGOUT_ASSERTION_RE.search(_case_check_blobs(case)))


def case_expects_auth(case: dict) -> bool:
    """用例自身断言是否要求**未认证态 / 登录流程**（匿名身份的唯一合法依据）。

    只看"未认证态"语义，不看"已认证态"（后者必须以被验证账号执行，派匿名会让断言
    不可达）；且必须排除登出/注销（其被测前提是已认证，与登录用例恰好相反）。
    """
    if case_expects_logout(case):
        return False
    return bool(_UNAUTH_STATE_RE.search(_case_check_blobs(case)))


def is_login_scenario(operation: str) -> bool:
    """``operation`` 是否为「登录动作」场景（委托 Step3 单一事实源）。

    登录动作的被测前提是**未认证**（登录表单仅在未登录态可达），故这类用例必须以
    ``anonymous`` 身份执行；词表/判据全部委托 ``_rule_extractor_keywords``。
    """
    try:
        from src.pipeline.step3._rule_extractor_keywords import is_login_operation

        return is_login_operation(operation)
    except Exception:  # noqa: BLE001
        return any(
            k in (operation or "")
            for k in ("登录", "登陆", "凭证", "鉴权", "认证", "口令")
        )


def _role_tokens(role_key: str) -> set[str]:
    """角色 key → 文本匹配用**具体标识符**集合（= 靶子声明的 role key 本身）。
      根因: 别名表把 ``viewer`` 归一到 ``user``，其别名集含 ``用户``/``user``/
        ``member``/``normal`` 等**通用名词** —— 中文需求原文几乎句句含"用户"，
        于是"需求点名了 viewer"恒为真：实测 58/68 条需求误判为访问控制型，
        340/582 条普通功能用例被指派 ``viewer`` → 低权身份下连"新增资产"都被拒
        → 系统性假 FAIL，比修复前更严重（实测复现）。
      通用判据: 文本扫描只能用**具体标识符**（靶子显式声明的 role key）；
        别名表的用途是"外部字符串 → 角色"**归一化**，不是"需求散文 → 角色"**扫描**。
        泛化的"按角色/无权限/非管理员"等表述由 ``_ROLE_CTX_RE`` 覆盖。
    """
    k = str(role_key or "").strip()
    return {k.lower()} if k else set()


def _token_in_text(token: str, low_text: str) -> bool:
    """标识符在文本中是否成词出现（ASCII 加词边界，防 viewer 命中 viewersx 之类）。"""
    t = str(token or "").strip()
    if not t:
        return False
    if t.isascii():
        return (
            re.search(r"(?<![a-z0-9_])" + re.escape(t) + r"(?![a-z0-9_])", low_text)
            is not None
        )
    return t in low_text


def _mentioned_roles(text: str, roles: dict[str, dict]) -> list[str]:
    """需求文本中点名的**非 admin** 声明角色（大小写不敏感，确定性排序）。"""
    from src.pipeline.utils.identity_orchestrator import ROLE_ADMIN, normalize_role

    low = (text or "").lower()
    hit: list[str] = []
    for key in roles:
        if normalize_role(key) == ROLE_ADMIN:
            continue
        if any(_token_in_text(t, low) for t in _role_tokens(key)):
            hit.append(key)
    return sorted(hit)


def _lowest_privilege_role(roles: dict[str, dict]) -> str | None:
    """最低权限的非 admin 声明角色（确定性取序；无非 admin 角色 → None）。"""
    from src.pipeline.utils.identity_orchestrator import ROLE_ADMIN, normalize_role

    cands = [k for k in roles if normalize_role(k) != ROLE_ADMIN]
    return sorted(cands)[0] if cands else None


def pick_identity(
    req_text: str, roles: dict[str, dict], access_control: bool | None = None
) -> str | None:
    """需求语义 → 应执行的身份 key（无法判定 → ``None``，保持默认身份）。

    判定顺序（零业务硬编码）:
      1) 未认证/匿名语义 → ``anonymous``
      2) 非"访问限制语义" → ``None``
      3) 点名的非 admin 角色 → 该角色
      4) 其余 → 最低权限非 admin 角色（断言"低权被拒"须有低权身份才可达）

    ``access_control``：需求落库时一次性判定的结构化字段（``requirements`` 的
    ``payload_json.access_control``，单一事实源 ``core/requirement_semantics``）。
    只有该字段缺失（早于该字段的历史需求）时才回落到同一权威函数重算，
    不另立第二套判据。
    """
    text = str(req_text or "")
    if not text.strip() or not roles:
        return None
    if _UNAUTH_STATE_RE.search(text):
        return "anonymous"
    if isinstance(access_control, bool):
        _is_restriction = access_control
    else:
        # ★ 判据单一化：访问限制语义的**唯一事实源** = ``core/requirement_semantics``
        #   （同一判据也决定 `access_control_check` 断言是否产出）。
        #   根因: 本处此前用 `_DENY_RE`（限制词**单侧**）独立重推同一语义，与生成端
        #     不同源 → 需求被判为访问控制型并发出 `access_control_check`，却未获
        #     执行身份 → 恒以 admin 执行 → 断言结构上不可能满足（引擎缺陷）。
        #   刻意**不**采用裸限制词表: 它会把"状态型拒绝"（"维护中的资产不允许删除"，
        #     与执行身份无关）一并判成访问控制 → 误派低权 → 假 FAIL。
        from src.core.requirement_semantics import is_access_restriction_req

        _is_restriction = is_access_restriction_req(text)
    if not _is_restriction:
        return None
    mentioned = _mentioned_roles(text, roles)
    if mentioned:
        return mentioned[0]
    # 未点名角色、也无"按角色控制"字样，但需求确实表达"受限操作应被拒绝" →
    #   落最低权限的非 admin 声明角色（确定性取序），使"低权被拒"断言可达。
    if _ROLE_CTX_RE.search(text):
        return _lowest_privilege_role(roles)
    return _lowest_privilege_role(roles)


def _req_text(case: dict, meta: dict | None) -> str:
    """组装**需求级**文本（只取需求原文，绝不取生成的用例文案）。

    用例散文（title/description/module）含"拒绝/拦截/权限"等模板词，会与语义门
    相互污染，故一律排除。需求原文的事实源 = requirements 表（``meta``）；
    ``source_quote``/``module`` 属文档结构（章节标题/文档名），只在语义体缺失时兜底。
    """
    m = meta or {}
    body = " ".join(
        str(p) for p in (
            m.get("operation"),
            m.get("description"),
            m.get("expected_feedback"),
        ) if p
    )
    if body.strip():
        return body
    # 语义体缺失（抽取不全）→ 兜底用需求引文/所属章节。
    fallback = " ".join(
        str(p) for p in (m.get("source_quote"), m.get("module")) if p
    )
    if fallback.strip():
        return fallback
    return str(case.get("source_quote") or "")


def assign_intent_identities(
    intents: list[dict],
    project: str = "",
    req_text_map: dict[str, dict] | None = None,
) -> int:
    """为访问控制型用例指派执行身份（原地修改，返回指派条数）。

    Args:
        intents: Step3 落库前的意图列表（原地写入 ``identity`` 键）。
        project: 项目名（解析靶子 ``auth.roles`` 用）。
        req_text_map: ``{req_id: {operation, description, expected_feedback, ...}}``
            —— 需求级原文（用例自身字段缺失时的兜底来源）。

    Returns:
        被指派身份的用例条数（0 = 无适用靶子/无访问控制用例，零行为变更）。

    ★ 根因修复(2026-09-25) RC-AG: 登录动作场景 → ``anonymous``，且**先于角色门控**
      执行 —— 匿名不依赖靶子角色声明（``load_roster`` 恒含 anonymous），故
      "靶子未声明 ≥2 角色"不应阻断登录场景的匿名指派。
    """
    try:
        from src.pipeline.utils.identity_pool import target_roles

        roles = target_roles(project)
    except Exception as e:  # noqa: BLE001
        _log.debug("[identity_assign] 靶子角色解析失败(不指派角色): %s", str(e)[:80])
        roles = {}
    # 角色指派需要 ≥2 角色（才能构造低权身份）；登录匿名不受此门控约束。
    _role_gate = len(roles) >= 2
    meta_map = req_text_map or {}
    assigned = 0
    for case in intents or []:
        if not isinstance(case, dict):
            continue
        if str(case.get("identity") or "").strip():
            continue
        meta = meta_map.get(str(case.get("req_id") or "")) or {}
        _op = str(meta.get("operation") or case.get("operation") or "")
        # ① 登录动作 → 匿名（被测前提是"未认证"，登录表单仅在未登录态可达）
        #   ★ Fix H 门控: 仅当**用例自身断言**涉及登录/认证流程才指派匿名；
        #     同一登录需求派生出的性能/兼容/UI 等用例（断言只有 LCP/布局/可达性）
        #     无认证语义 → 保持默认身份，避免"手段与断言错配"。
        if is_login_scenario(_op) and case_expects_auth(case):
            case["identity"] = "anonymous"
            assigned += 1
            _log.info(
                "[identity_assign] 用例 %s 指派身份 → anonymous（需求 %s 为登录动作场景）",
                case.get("case_id", "?"),
                case.get("req_id", "?"),
            )
            continue
        if not _role_gate:
            continue
        # ② 访问控制型 → 低权/点名角色
        #   门控：仅当用例自身断言要求"被拒绝/不可达"才指派低权身份——需求类别只
        #   决定"可用哪些身份"，否则正向用例在受保护页 403 必然假 FAIL。
        if not case_expects_denial(case):
            continue
        _ac = meta.get("access_control")
        key = pick_identity(
            _req_text(case, meta), roles, _ac if isinstance(_ac, bool) else None
        )
        if not key:
            continue
        # 登出/注销类用例的前提是**已认证** → 匿名身份下退出入口不存在，必自造 ERROR。
        if key == "anonymous" and case_expects_logout(case):
            continue
        case["identity"] = key
        assigned += 1
        _log.info(
            "[identity_assign] 用例 %s 指派身份 → %s（需求 %s 为访问控制型）",
            case.get("case_id", "?"),
            key,
            case.get("req_id", "?"),
        )
    return assigned