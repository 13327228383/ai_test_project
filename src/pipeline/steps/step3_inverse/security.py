# -*- coding: utf-8 -*-
"""安全反向覆盖生成器：越权 / 注入 / CSRF / XSS / 敏感数据泄漏。

覆盖维度
--------
- privilege_escalation : 低权限账号尝试访问高权限操作
- sql_injection        : 输入框注入 SQL/NoSQL 探测载荷（轮询全部 payload）
- xss                  : 输入框注入 XSS 探测载荷（轮询全部 payload）
- csrf                 : 缺失/伪造 CSRF token 提交
- sensitive_leak       : 接口响应/页面是否泄漏敏感字段（密码、token、身份证）

v0.3 更新
---------
- XSS/SQL 注入探测轮询全部 payload（每个 payload 生成独立反向用例）
- 实现 privilege_escalation 反向用例生成
- 实现 csrf 反向用例生成
- 提供 SecurityInverseExpander 类（OOP 接口）
"""

from typing import Any
import copy
import logging

from ._base import InverseGenContext, build_inverse_intent

_log = logging.getLogger(__name__)


# 一组通用的 XSS 探测载荷（只填进文本类输入框，不真正执行）
_XSS_PAYLOADS = [
    "<script>window.__xss_marker=1</script>",
    '"><img src=x onerror=alert(1)>',
    "javascript:alert(1)",
    "<svg onload=alert(1)>",
    "';alert(String.fromCharCode(88,83,83))//",
    "<iframe src=javascript:alert(1)>",
]

# SQL 注入探测载荷
_SQL_INJECTION_PAYLOADS = [
    "' OR '1'='1",
    "'; DROP TABLE users; --",
    "1' UNION SELECT NULL, version() --",
    "admin'--",
    "' OR 1=1#",
    "' AND SLEEP(5)--",
    "1; SELECT pg_sleep(5)--",
]

# 敏感字段关键词
_SENSITIVE_FIELDS = [
    "password",
    "passwd",
    "pwd",
    "secret",
    "token",
    "api_key",
    "access_token",
    "refresh_token",
    "session_id",
    "sessionid",
    "credit_card",
    "card_number",
    "cvv",
    "id_card",
    "身份证",
    "mobile",
    "phone",
    "email",
    "密码",
    "手机号",
]

# CSRF 探测载荷名称（单一事实源: feedback_selectors，2026-09-28 统一词表）
try:
    from src.pipeline.utils.feedback_selectors import CSRF_TOKEN_FIELD_NAMES as _CSRF_TOKEN_FIELDS
except Exception:  # noqa: BLE001 —— 兜底防循环 import
    _CSRF_TOKEN_FIELDS = [
        "csrfmiddlewaretoken", "csrf_token", "_csrf", "csrfToken",
        "authenticity_token", "_token", "__RequestVerificationToken",
    ]

# 越权探测：低权限角色列表
_PRIVILEGE_ROLES = [
    {"role": "regular_user", "label": "普通用户"},
    {"role": "guest", "label": "访客"},
]


class SecurityInverseExpander:
    """安全反向覆盖扩展器（OOP 接口）。

    用法::

        expander = SecurityInverseExpander()
        inverse_intents = expander.expand(positive_intent, ctx)

    或使用模块级函数::

        from src.pipeline.steps.step3_inverse.security import expand
        inverse_intents = expand(positive_intent, ctx)
    """

    def expand(
        self, positive: dict[str, Any], ctx: InverseGenContext
    ) -> list[dict[str, Any]]:
        """对单个正向意图扩展出安全维度的反向意图。

        返回:
            反向意图列表（XSS + SQL注入 + 敏感泄漏 + 越权 + CSRF 探测）
        """
        return _expand_impl(positive, ctx)


def expand(positive: dict[str, Any], ctx: InverseGenContext) -> list[dict[str, Any]]:
    """对单个正向意图扩展出安全维度的反向意图（模块级函数）。

    返回:
        反向意图列表（XSS + SQL注入 + 敏感泄漏 + 越权 + CSRF 探测）
    """
    return _expand_impl(positive, ctx)


def _expand_impl(
    positive: dict[str, Any], ctx: InverseGenContext
) -> list[dict[str, Any]]:
    """安全反向覆盖扩展实现。"""
    inverse: list[dict[str, Any]] = []

    if _has_text_input_step(positive):
        # XSS 探测：轮询全部 payload，每个 payload 生成一个独立反向用例
        inverse.extend(_build_xss_probes(positive, ctx))
        # SQL 注入探测：轮询全部 payload
        inverse.extend(_build_sql_injection_probes(positive, ctx))

    # 敏感信息泄漏探测：对所有有 API 调用的意图生成
    if _has_api_step(positive):
        inverse.append(_build_sensitive_leak_probe(positive, ctx))

    # 越权探测：对所有有 API/导航步骤的意图生成
    if _has_api_step(positive) or _has_navigate_step(positive):
        inverse.extend(_build_privilege_escalation_probes(positive, ctx))

    # CSRF 探测：对所有包含表单提交/状态变更操作的意图生成
    if _has_form_submit_step(positive) or _has_api_step(positive):
        inverse.extend(_build_csrf_probes(positive, ctx))

    return inverse


# ──────────────────────────────────────────────────────
# 辅助检测函数
# ──────────────────────────────────────────────────────


def _has_text_input_step(positive: dict[str, Any]) -> bool:
    steps = positive.get("steps") or []
    if not isinstance(steps, list):
        return False
    return any(isinstance(s, dict) and s.get("action") == "fill" for s in steps)


def _has_api_step(positive: dict[str, Any]) -> bool:
    """检查意图是否包含 API 调用步骤。"""
    steps = positive.get("steps") or []
    if not isinstance(steps, list):
        return False
    if any(
        isinstance(s, dict)
        and s.get("action", "")
        in ("navigate", "click", "fill", "select_option", "upload")
        for s in steps
    ):
        return True
    checks = positive.get("checks") or []
    if any(isinstance(c, dict) and c.get("track") == "api" for c in checks):
        return True
    return False


def _has_navigate_step(positive: dict[str, Any]) -> bool:
    """检查意图是否包含导航步骤。"""
    steps = positive.get("steps") or []
    if not isinstance(steps, list):
        return False
    return any(
        isinstance(s, dict) and s.get("action") == "navigate" for s in steps
    )


def _has_form_submit_step(positive: dict[str, Any]) -> bool:
    """检查意图是否包含表单提交步骤（click + fill 组合）。"""
    steps = positive.get("steps") or []
    if not isinstance(steps, list):
        return False
    has_fill = any(isinstance(s, dict) and s.get("action") == "fill" for s in steps)
    has_click = any(isinstance(s, dict) and s.get("action") == "click" for s in steps)
    return has_fill and has_click


# ──────────────────────────────────────────────────────
# XSS 探测（轮询全部 payload）
# ──────────────────────────────────────────────────────


def _build_xss_probes(
    positive: dict[str, Any], ctx: InverseGenContext
) -> list[dict[str, Any]]:
    """为每个 XSS payload 生成独立的反向探测用例。

    轮询全部 _XSS_PAYLOADS，每个 payload 生成一个独立的反向意图，
    确保 XSS 防御覆盖所有攻击向量（不仅测试第一个 payload）。
    """
    probes: list[dict[str, Any]] = []

    for payload_idx, xss_payload in enumerate(_XSS_PAYLOADS):
        src_steps = positive.get("steps") or []
        probe_steps: list[dict[str, Any]] = []

        for step in src_steps:
            if not isinstance(step, dict):
                continue
            new_step = copy.deepcopy(step)
            if new_step.get("action") == "fill":
                # 替换 value 为当前 XSS 探测载荷
                new_step["value"] = xss_payload
                new_step["_inverse_payload"] = "xss"
                new_step["_payload_index"] = payload_idx
            probe_steps.append(new_step)

        # 追加防御断言
        probe_steps.append(
            {
                "action": "verify",
                "description": (
                    f"验证 XSS 载荷 #{payload_idx + 1} 是否被转义/过滤"
                    f"——页面不应执行注入的脚本 ({xss_payload[:40]}...)"
                ),
                "expected": (
                    "页面未弹出 alert，未出现 <script> 注入痕迹"
                    "（被转义为 &lt;script&gt;）"
                ),
                "_inverse_assertion": "xss_escaped",
                "_payload_index": payload_idx,
            }
        )

        probe = build_inverse_intent(
            source_intent=positive,
            dimension="security",
            sub_kind="xss",
            title_suffix=f"[XSS 探测 #{payload_idx + 1}]",
            description=(
                f"基于正向意图 {positive.get('case_id')} 进行 XSS 探测（payload #{payload_idx + 1}）："
                f"在所有 fill 步骤注入 XSS 载荷「{xss_payload[:50]}」，"
                f"验证页面输出层是否做了 HTML 转义。"
            ),
            steps=probe_steps,
            risk_points=[
                "输入未转义直接渲染到 DOM",
                "存储型 XSS：载荷被持久化后污染其他用户",
                "事件属性（onerror/onload）未过滤",
                "javascript: 伪协议未拦截",
            ],
            expected_defense="所有用户输入在输出时被 HTML 转义；CSP 阻止内联脚本执行",
            ctx=ctx,
        )
        probes.append(probe)

    _log.debug(
        "[SecurityInverse] XSS 探测: 生成 %d 个反向用例（%d 个 payload）",
        len(probes),
        len(_XSS_PAYLOADS),
    )
    return probes


# ──────────────────────────────────────────────────────
# SQL 注入探测（轮询全部 payload）
# ──────────────────────────────────────────────────────


def _build_sql_injection_probes(
    positive: dict[str, Any], ctx: InverseGenContext
) -> list[dict[str, Any]]:
    """为每个 SQL 注入 payload 生成独立的反向探测用例。

    轮询全部 _SQL_INJECTION_PAYLOADS，每个 payload 生成一个独立的反向意图，
    确保参数化查询防御覆盖所有注入向量。
    """
    probes: list[dict[str, Any]] = []

    for payload_idx, sql_payload in enumerate(_SQL_INJECTION_PAYLOADS):
        src_steps = positive.get("steps") or []
        probe_steps: list[dict[str, Any]] = []

        for step in src_steps:
            if not isinstance(step, dict):
                continue
            new_step = copy.deepcopy(step)
            if new_step.get("action") == "fill":
                new_step["value"] = sql_payload
                new_step["_inverse_payload"] = "sql_injection"
                new_step["_payload_index"] = payload_idx
            probe_steps.append(new_step)

        probe_steps.append(
            {
                "action": "verify",
                "description": (
                    f"验证 SQL 注入载荷 #{payload_idx + 1} 是否被参数化查询拦截"
                    f"——不应返回异常数据或报错 ({sql_payload[:40]}...)"
                ),
                "expected": (
                    "后端使用参数化查询，注入载荷被当作普通字符串处理；"
                    "不返回 SQL 错误信息"
                ),
                "_inverse_assertion": "sql_injection_blocked",
                "_payload_index": payload_idx,
            }
        )

        probe = build_inverse_intent(
            source_intent=positive,
            dimension="security",
            sub_kind="sql_injection",
            title_suffix=f"[SQL注入探测 #{payload_idx + 1}]",
            description=(
                f"基于正向意图 {positive.get('case_id')} 进行 SQL 注入探测"
                f"（payload #{payload_idx + 1}）："
                f"在 fill 步骤注入 SQL 载荷「{sql_payload[:50]}」，"
                f"验证后端是否使用参数化查询。"
            ),
            steps=probe_steps,
            risk_points=[
                "字符串拼接 SQL 导致注入",
                "未使用 ORM 或参数化查询",
                "错误信息直接返回给前端暴露 DB 结构",
                "时间盲注：SLEEP/BENCHMARK 未被拦截",
            ],
            expected_defense=(
                "所有 DB 查询使用参数化；注入载荷被安全处理；不返回 SQL 错误详情"
            ),
            ctx=ctx,
        )
        probes.append(probe)

    _log.debug(
        "[SecurityInverse] SQL 注入探测: 生成 %d 个反向用例（%d 个 payload）",
        len(probes),
        len(_SQL_INJECTION_PAYLOADS),
    )
    return probes


# ──────────────────────────────────────────────────────
# 敏感信息泄漏探测
# ──────────────────────────────────────────────────────


def _build_sensitive_leak_probe(
    positive: dict[str, Any], ctx: InverseGenContext
) -> dict[str, Any]:
    """检查 API 响应和页面是否泄漏敏感字段。"""
    src_steps = positive.get("steps") or []
    probe_steps: list[dict[str, Any]] = []

    for step in src_steps:
        if isinstance(step, dict):
            probe_steps.append(copy.deepcopy(step))

    probe_steps.append(
        {
            "action": "verify",
            "description": (
                "检查 API 响应体和页面源码中是否包含敏感字段"
                "（password/token/身份证等）"
            ),
            "expected": (
                "API 响应不返回敏感字段或对其脱敏（如 ***）；"
                "页面不展示明文敏感信息"
            ),
            "_inverse_assertion": "no_sensitive_leak",
            "_sensitive_fields": _SENSITIVE_FIELDS,
        }
    )

    return build_inverse_intent(
        source_intent=positive,
        dimension="security",
        sub_kind="sensitive_leak",
        title_suffix="[敏感信息泄漏检测]",
        description=(
            f"基于正向意图 {positive.get('case_id')} 进行敏感信息泄漏检测："
            f"检查 API 响应和页面是否暴露敏感字段。"
        ),
        steps=probe_steps,
        risk_points=[
            "API 响应直接返回 password/token 等敏感字段",
            "前端页面展示明文敏感信息",
            "日志中记录敏感数据",
        ],
        expected_defense="敏感字段在 API 响应中脱敏或排除；前端不展示明文敏感信息",
        ctx=ctx,
    )


# ──────────────────────────────────────────────────────
# 越权探测（privilege_escalation）
# ──────────────────────────────────────────────────────


def _build_privilege_escalation_probes(
    positive: dict[str, Any], ctx: InverseGenContext
) -> list[dict[str, Any]]:
    """越权探测：低权限账号尝试访问高权限操作。

    为每个低权限角色生成一个反向用例，验证系统是否正确拒绝了
    低权限用户的访问请求。

    检测策略:
    1. 修改导航步骤中的 URL，尝试访问管理员路径
    2. 在 API 调用中修改用户身份标识
    3. 验证系统返回 403/重定向到登录页（而非正常数据）
    """
    probes: list[dict[str, Any]] = []

    for role_info in _PRIVILEGE_ROLES:
        src_steps = positive.get("steps") or []
        probe_steps: list[dict[str, Any]] = []

        for step in src_steps:
            if not isinstance(step, dict):
                continue
            new_step = copy.deepcopy(step)

            # 修改导航步骤：尝试访问管理员路径
            if new_step.get("action") == "navigate":
                _url = new_step.get("url", "") or new_step.get("target", "")
                if _url:
                    # 尝试访问 admin 子路径
                    if "/admin/" not in _url:
                        new_step["_original_url"] = _url
                        new_step["_escalation_attempt"] = "admin_path"
                        new_step["description"] = (
                            f"越权探测：以{role_info['label']}身份访问管理员路径"
                        )

            # 修改 fill 步骤：尝试注入管理员账号
            if new_step.get("action") == "fill":
                _field = (
                    new_step.get("field", "")
                    or new_step.get("selector", "")
                    or new_step.get("label", "")
                )
                _field_lower = str(_field).lower()
                if any(
                    kw in _field_lower
                    for kw in ("username", "user", "account", "账号", "用户")
                ):
                    new_step["_original_value"] = new_step.get("value", "")
                    new_step["value"] = "low_privilege_user"
                    new_step["_escalation_attempt"] = "user_switch"
                    new_step["description"] = (
                        f"越权探测：使用低权限账号({role_info['role']})登录"
                    )
                elif any(
                    kw in _field_lower for kw in ("password", "pwd", "密码")
                ):
                    new_step["value"] = "low_privilege_pass"
                    new_step["_escalation_attempt"] = "user_switch"

            probe_steps.append(new_step)

        # 追加越权验证断言
        probe_steps.append(
            {
                "action": "verify",
                "description": (
                    f"验证越权探测：{role_info['label']}({role_info['role']})"
                    f"不应能访问高权限操作或看到管理数据"
                ),
                "expected": (
                    "系统返回 403 Forbidden 或重定向到登录页；"
                    "不展示管理员专属数据或操作按钮"
                ),
                "_inverse_assertion": "privilege_escalation_blocked",
                "_escalation_role": role_info["role"],
            }
        )

        probe = build_inverse_intent(
            source_intent=positive,
            dimension="security",
            sub_kind="privilege_escalation",
            title_suffix=f"[越权探测-{role_info['label']}]",
            description=(
                f"基于正向意图 {positive.get('case_id')} 进行越权探测："
                f"以{role_info['label']}({role_info['role']})身份"
                f"尝试访问高权限操作，验证系统是否正确执行了权限校验。"
            ),
            steps=probe_steps,
            risk_points=[
                "后端未校验当前用户角色，直接信任前端传入的 user_id",
                "API 端点缺少 @permission_required 装饰器",
                "前端隐藏了管理按钮但后端 API 仍可被直接调用",
                "Session/Token 未绑定角色，仅校验登录状态",
            ],
            expected_defense=(
                "后端对每个操作校验当前用户角色；"
                "低权限用户访问管理操作返回 403；"
                "API 端点使用 RBAC/ABAC 权限模型"
            ),
            ctx=ctx,
        )
        probes.append(probe)

    _log.debug(
        "[SecurityInverse] 越权探测: 生成 %d 个反向用例（%d 个角色）",
        len(probes),
        len(_PRIVILEGE_ROLES),
    )
    return probes


# ──────────────────────────────────────────────────────
# CSRF 探测
# ──────────────────────────────────────────────────────


def _build_csrf_probes(
    positive: dict[str, Any], ctx: InverseGenContext
) -> list[dict[str, Any]]:
    """CSRF 探测：缺失/伪造 CSRF token 提交。

    生成两个反向用例：
    1. 缺失 CSRF token：提交表单时移除 CSRF token 字段
    2. 伪造 CSRF token：提交表单时使用无效的 CSRF token

    验证系统是否正确拒绝了没有有效 CSRF token 的请求。
    """
    probes: list[dict[str, Any]] = []

    # --- 探测 1: 缺失 CSRF token ---
    src_steps = positive.get("steps") or []
    probe_steps_missing: list[dict[str, Any]] = []

    for step in src_steps:
        if not isinstance(step, dict):
            continue
        new_step = copy.deepcopy(step)
        # 在 fill 步骤中移除 CSRF token 相关字段
        if new_step.get("action") == "fill":
            _field = str(
                new_step.get("field", "")
                or new_step.get("selector", "")
                or new_step.get("label", "")
            ).lower()
            if any(csrf_field in _field for csrf_field in _CSRF_TOKEN_FIELDS):
                # 跳过 CSRF token 填充步骤（模拟缺失）
                new_step["_skip_csrf"] = True
                new_step["description"] = "CSRF 探测：跳过 CSRF token 填充"
                new_step["value"] = ""
        probe_steps_missing.append(new_step)

    # 追加 CSRF 验证断言
    probe_steps_missing.append(
        {
            "action": "verify",
            "description": (
                "验证 CSRF 探测：缺失 CSRF token 的表单提交应被拒绝"
            ),
            "expected": (
                "系统返回 403 Forbidden 或 'CSRF token missing' 错误；"
                "不执行状态变更操作（如不创建/修改/删除数据）"
            ),
            "_inverse_assertion": "csrf_missing_blocked",
        }
    )

    probes.append(
        build_inverse_intent(
            source_intent=positive,
            dimension="security",
            sub_kind="csrf",
            title_suffix="[CSRF探测-缺失token]",
            description=(
                f"基于正向意图 {positive.get('case_id')} 进行 CSRF 探测："
                f"提交表单时移除 CSRF token，验证系统是否拒绝无 token 的请求。"
            ),
            steps=probe_steps_missing,
            risk_points=[
                "后端未校验 CSRF token",
                "CSRF 中间件未启用或被绕过",
                "SameSite Cookie 未设置",
            ],
            expected_defense=(
                "后端校验 CSRF token；缺失 token 的请求返回 403；"
                "使用 SameSite=Strict/Lax Cookie"
            ),
            ctx=ctx,
        )
    )

    # --- 探测 2: 伪造 CSRF token ---
    probe_steps_forged: list[dict[str, Any]] = []

    for step in src_steps:
        if not isinstance(step, dict):
            continue
        new_step = copy.deepcopy(step)
        if new_step.get("action") == "fill":
            _field = str(
                new_step.get("field", "")
                or new_step.get("selector", "")
                or new_step.get("label", "")
            ).lower()
            if any(csrf_field in _field for csrf_field in _CSRF_TOKEN_FIELDS):
                # 使用伪造的 CSRF token
                new_step["value"] = "forged_invalid_csrf_token_12345"
                new_step["_csrf_forged"] = True
                new_step["description"] = "CSRF 探测：使用伪造的 CSRF token"
        probe_steps_forged.append(new_step)

    probe_steps_forged.append(
        {
            "action": "verify",
            "description": (
                "验证 CSRF 探测：伪造 CSRF token 的表单提交应被拒绝"
            ),
            "expected": (
                "系统返回 403 Forbidden 或 'CSRF token invalid' 错误；"
                "不执行状态变更操作"
            ),
            "_inverse_assertion": "csrf_forged_blocked",
        }
    )

    probes.append(
        build_inverse_intent(
            source_intent=positive,
            dimension="security",
            sub_kind="csrf",
            title_suffix="[CSRF探测-伪造token]",
            description=(
                f"基于正向意图 {positive.get('case_id')} 进行 CSRF 探测："
                f"提交表单时使用伪造的 CSRF token，验证系统是否拒绝无效 token 的请求。"
            ),
            steps=probe_steps_forged,
            risk_points=[
                "后端仅检查 CSRF token 存在性但不验证有效性",
                "CSRF token 未绑定用户 Session",
                "CSRF token 可预测或可枚举",
            ],
            expected_defense=(
                "后端验证 CSRF token 的有效性（与 Session 绑定）；"
                "无效 token 的请求返回 403"
            ),
            ctx=ctx,
        )
    )

    _log.debug(
        "[SecurityInverse] CSRF 探测: 生成 %d 个反向用例", len(probes)
    )
    return probes


__all__ = ["expand", "SecurityInverseExpander"]
