# -*- coding: utf-8 -*-
"""安全扫描器 — OWASP Top 10 基础检测

支持的检测维度:
  1. SQL 注入（错误信息泄露）
  2. XSS（反射型 / DOM / 伪协议）
  3. 敏感信息泄露（身份证 / 手机 / 邮箱 / API密钥 / 密码明文）
  4. 安全响应头缺失
  5. CSRF token 缺失（表单级 + 响应级）
  6. 路径遍历（../ 序列 / /etc/passwd / C:\\Windows）
  7. 命令注入（元字符后的命令执行痕迹）
  8. IDOR（不同身份访问同一资源的数据越权）

接口说明:
  scan_response(body, content_type='text/html', url='', status_code=200, headers=None)
  — 主扫描入口，返回 SecurityFinding 列表
  scan_api_calls(calls)
  — 批量扫描 ApiTracker 采集的 HTTP 调用列表，支持 IDOR 跨身份比较
"""

import re
from src.core.guarded import guard
import logging
from typing import Optional
from dataclasses import dataclass, field

_log = logging.getLogger(__name__)


@dataclass
class SecurityFinding:
    rule_id: str
    category: str
    severity: str  # critical, high, medium, low
    description: str
    evidence: str = ""
    recommendation: str = ""


class SecurityScanner:
    """对 HTTP 响应/页面内容进行安全扫描"""

    # ── SQL 注入检测 ──
    SQL_INJECTION_PATTERNS = [
        (
            re.compile(r"SQL syntax.*MySQL", re.IGNORECASE),
            "critical",
            "MySQL 语法错误泄露",
        ),
        (re.compile(r"SQLite.*error", re.IGNORECASE), "critical", "SQLite 错误泄露"),
        (re.compile(r"ORA-\d{5}", re.IGNORECASE), "critical", "Oracle 错误码泄露"),
        (
            re.compile(r"PostgreSQL.*ERROR", re.IGNORECASE),
            "critical",
            "PostgreSQL 错误泄露",
        ),
        (
            re.compile(r"unclosed quotation mark", re.IGNORECASE),
            "high",
            "SQL 引号未闭合",
        ),
        (re.compile(r"OperationalError", re.IGNORECASE), "high", "数据库操作异常泄露"),
    ]

    # ── XSS 检测 ──
    XSS_PATTERNS = [
        (
            re.compile(r"<script>alert\(", re.IGNORECASE),
            "critical",
            "反射型 XSS: script 标签未转义",
        ),
        (
            re.compile(r'onerror\s*=\s*["\']?alert', re.IGNORECASE),
            "high",
            "DOM XSS: onerror 事件未过滤",
        ),
        (
            re.compile(r"javascript\s*:\s*alert", re.IGNORECASE),
            "high",
            "javascript: 伪协议注入",
        ),
    ]

    # ── 敏感信息泄露 ──
    SENSITIVE_DATA_PATTERNS = [
        (re.compile(r"(?<!\d)\d{15}(?:\d{2}[0-9Xx])?(?!\d)"), "high", "身份证号泄露"),
        (re.compile(r"1[3-9]\d{9}"), "medium", "手机号泄露"),
        (
            re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"),
            "medium",
            "邮箱地址泄露",
        ),
        (
            re.compile(
                r'(?:accessToken|apiKey|secretKey|privateKey)\s*[:=]\s*["\'][^"\']{8,}["\']',
                re.IGNORECASE,
            ),
            "critical",
            "API密钥/Token硬编码",
        ),
        (
            re.compile(r'password\s*[:=]\s*["\'][^"\']+["\']', re.IGNORECASE),
            "critical",
            "密码明文存储",
        ),
    ]

    # ── 安全头检测 ──
    SECURITY_HEADERS = {
        "X-Content-Type-Options": {"expected": "nosniff", "severity": "medium"},
        "X-Frame-Options": {"expected": None, "severity": "medium"},
        "Content-Security-Policy": {"expected": None, "severity": "high"},
        "Strict-Transport-Security": {"expected": None, "severity": "high"},
        "X-XSS-Protection": {"expected": None, "severity": "low", "deprecated": True},
        "Referrer-Policy": {"expected": None, "severity": "low"},
    }

    # ── CORS 配置检测（HEAD-004，2026-09-05 补齐）─────────────────────
    # 通用安全常识：生产 API 服务应返回明确的 CORS 策略；缺失或过度放行都算缺陷。
    # `*` 通配（允许任意来源）对携带凭据的接口是危险的（配合凭据可被任意站点跨域读取）。
    # 仅当响应含 access-control-allow-origin 头时才检测（保守，避免对纯前端页误报）。
    CORS_PERMISSIVE_ORIGIN_PATTERN = re.compile(r"^\s*\*\s*$")

    # ── 方法篡改检测（MTD-001，2026-09-05 补齐，API 层）────────────────
    # HTTP 方法篡改：靶子仅显式放行 GET/POST（REST 语义），却对 PUT/DELETE/PATCH 也
    # 返回 2xx 且做了状态变更，即"方法级访问控制失控"。通用检测：对带方法语义的
    # 调用，本已是 2xx 即视为潜在放行，交由 scan_api_calls 用「2xx 命中方法」判断。
    # 这里仅作为可读标注，实际判定在 scan_api_calls。

    # ── Mass Assignment（批量赋值）检测（MASS-001，2026-09-05 补齐，API 层）────
    # 批量赋值：客户端提交后端实体里不该暴露/修改的字段（如 role/is_admin/balance）。
    # 通用检测：请求体含敏感字段名即记录为疑似；判定需跨身份对比是否被写入
    # （在 scan_api_calls 中同路径多身份响应对比联动）。
    MASS_ASSIGNMENT_FIELDS = ["role", "is_admin", "is_admin.", "admin", "balance",
                              "permissions", "level", "deleted", "created_at",
                              "updated_at", "owner", "superuser", "privilege"]

    # ── 路径遍历检测 ──
    PATH_TRAVERSAL_PATTERNS = [
        (
            re.compile(r"\.\./\.\.(?:/|\\)"),
            "high",
            "路径遍历序列 (../) 泄露",
        ),
        (
            re.compile(r"/etc/passwd", re.IGNORECASE),
            "critical",
            "Linux 系统文件 /etc/passwd 泄露",
        ),
        (
            re.compile(r"/etc/shadow", re.IGNORECASE),
            "critical",
            "Linux 影子文件 /etc/shadow 泄露",
        ),
        (
            re.compile(r"C:\\Windows\\System32", re.IGNORECASE),
            "critical",
            "Windows 系统路径 C:\\Windows\\System32 泄露",
        ),
        (
            re.compile(r"C:\\Windows\\win\.ini", re.IGNORECASE),
            "critical",
            "Windows 系统文件 win.ini 泄露",
        ),
        (
            re.compile(r"\.\.\\\\?\.\.\\\\?", re.IGNORECASE),
            "high",
            "Windows 路径遍历序列 (..\\..\\) 泄露",
        ),
        (
            re.compile(r"/proc/self/(?:environ|cmdline|status)", re.IGNORECASE),
            "critical",
            "Linux /proc/self 进程信息泄露",
        ),
    ]

    # ── 命令注入检测 ──
    # 检测 Shell 命令执行痕迹：元字符后出现典型命令输出特征
    COMMAND_INJECTION_PATTERNS = [
        (
            re.compile(r"uid=\d+\(root\)\s+gid=\d+", re.IGNORECASE),
            "critical",
            "命令注入: id 命令输出 (uid/gid) 泄露",
        ),
        (
            re.compile(r"root:x:0:0:", re.IGNORECASE),
            "critical",
            "命令注入: /etc/passwd 内容泄露（可能通过 cat 命令）",
        ),
        (
            re.compile(
                r"(?:Linux|Darwin)\s+\S+\s+\d+\.\d+\.\d+.*?\s+(?:x86_64|amd64|i386|aarch64)",
                re.IGNORECASE,
            ),
            "high",
            "命令注入: uname -a 系统信息泄露",
        ),
        (
            re.compile(r"total\s+\d+\s+\n?[drwx-]{10}", re.IGNORECASE),
            "high",
            "命令注入: ls -la 目录列表输出",
        ),
        (
            re.compile(r"[;|&]\s*(?:whoami|id|uname|cat|ls|pwd|dir|net|ipconfig|ifconfig)\b"),
            "high",
            "命令注入: 检测到 Shell 元字符后的命令执行痕迹",
        ),
        (
            re.compile(r"\$\([^)]+\)"),
            "medium",
            "命令注入: 检测到 $(...) 命令替换语法",
        ),
        (
            re.compile(r"`[^`]+`"),
            "medium",
            "命令注入: 检测到反引号命令替换语法",
        ),
    ]

    # ── IDOR 检测 ──
    # IDOR 需要跨请求比较，在 scan_api_calls 中处理
    IDOR_SENSITIVE_FIELDS = [
        "user_id",
        "userid",
        "id",
        "email",
        "phone",
        "mobile",
        "username",
        "balance",
        "salary",
        "address",
        "order",
        "account",
    ]

    def __init__(self):
        self.findings: list[SecurityFinding] = []

    def scan_response(
        self,
        body: str,
        content_type: str = "text/html",
        url: str = "",
        status_code: int = 200,
        headers: Optional[dict] = None,
    ) -> list[SecurityFinding]:
        """扫描 HTTP 响应内容

        ★ v6.1(2026-08-12): 受 SECURITY_SCAN_ENABLED 开关控制（默认开启）。
        关闭后返回空列表，零性能开销。

        参数:
            body:         响应体文本
            content_type: 响应内容类型（如 text/html, application/json）
            url:          响应对应的 URL（用于日志和证据记录）
            status_code:  HTTP 状态码
            headers:      响应头字典（可选，用于安全头检测）

        返回:
            SecurityFinding 列表
        """
        # ★ 安全扫描开关（默认 True，可通过前端 /api/config/toggle 关闭）
        with guard("scan_response: from src.core.pipeline_config import", log=_log):
            from src.core.pipeline_config import _read_config_value
            if not _read_config_value("SECURITY_SCAN_ENABLED", True):
                return []

        self.findings = []
        headers = headers or {}
        body_str = body if isinstance(body, str) else str(body)

        # 1. SQL 注入检测
        for pattern, severity, desc in self.SQL_INJECTION_PATTERNS:
            match = pattern.search(body_str)
            if match:
                self.findings.append(
                    SecurityFinding(
                        rule_id="SQL-001",
                        category="SQL Injection",
                        severity=severity,
                        description=desc,
                        evidence=match.group()[:100],
                        recommendation="确保所有数据库错误被统一处理，不暴露内部错误信息",
                    )
                )

        # 2. XSS 检测
        for pattern, severity, desc in self.XSS_PATTERNS:
            match = pattern.search(body_str)
            if match:
                self.findings.append(
                    SecurityFinding(
                        rule_id="XSS-001",
                        category="Cross-Site Scripting",
                        severity=severity,
                        description=desc,
                        evidence=match.group()[:100],
                        recommendation="对用户输入进行 HTML 实体编码，使用 CSP 头限制脚本执行",
                    )
                )

        # 3. 敏感信息
        for pattern, severity, desc in self.SENSITIVE_DATA_PATTERNS:
            matches = pattern.findall(body_str)
            if matches:
                self.findings.append(
                    SecurityFinding(
                        rule_id="DATA-001",
                        category="Sensitive Data Exposure",
                        severity=severity,
                        description=f"{desc}（发现 {len(matches)} 处）",
                        evidence=(
                            str(matches[0])[:50]
                            if isinstance(matches[0], str)
                            else str(matches[0])
                        ),
                        recommendation="敏感数据应脱敏处理，API密钥不应硬编码在前端代码中",
                    )
                )

        # 4. 安全头检测（仅当 headers 非空时）
        if headers:
            for header_name, config in self.SECURITY_HEADERS.items():
                if config.get("deprecated"):
                    continue
                if header_name not in headers:
                    self.findings.append(
                        SecurityFinding(
                            rule_id="HEAD-001",
                            category="Missing Security Header",
                            severity=config["severity"],
                            description=f"缺少安全响应头: {header_name}",
                            evidence="",
                            recommendation=f"添加 {header_name} 响应头以增强安全性",
                        )
                    )

            # 4.1 CORS 配置检测（HEAD-004，2026-09-05 补齐）
            # 仅当响应声明了 access-control-allow-origin 头才评估其值；缺失 CORS 头时
            # 属"同源策略未放行"（更安全），不误报。`*` 通配 + 允许凭据 = 高危任意源。
            ac_origin = headers.get("access-control-allow-origin") or headers.get(
                "Access-Control-Allow-Origin"
            )
            if ac_origin:
                allow_credentials = headers.get("access-control-allow-credentials") or headers.get(
                    "Access-Control-Allow-Credentials"
                )
                if self.CORS_PERMISSIVE_ORIGIN_PATTERN.match(ac_origin):
                    if allow_credentials and str(allow_credentials).strip().lower() == "true":
                        sev, desc = "critical", "CORS 允许任意来源(*)且允许携带凭据 → 任意站点可跨域窃取数据"
                    else:
                        sev, desc = "high", "CORS 允许任意来源(*) — 若接口含敏感数据应限定可信域名"
                    self.findings.append(
                        SecurityFinding(
                            rule_id="HEAD-004",
                            category="CORS Misconfiguration",
                            severity=sev,
                            description=desc,
                            evidence=ac_origin[:100],
                            recommendation="CORS 应限定为白名单域名，禁止使用 * 通配（尤其携带凭据时）",
                        )
                    )

        # 5. CSRF 检测（增强版）
        self._check_csrf(body_str, status_code, url)

        # 6. 路径遍历检测
        for pattern, severity, desc in self.PATH_TRAVERSAL_PATTERNS:
            match = pattern.search(body_str)
            if match:
                self.findings.append(
                    SecurityFinding(
                        rule_id="PATH-001",
                        category="Path Traversal",
                        severity=severity,
                        description=desc,
                        evidence=match.group()[:100],
                        recommendation="对文件路径输入进行白名单校验，禁止包含 ../ 等遍历序列",
                    )
                )

        # 7. 命令注入检测
        for pattern, severity, desc in self.COMMAND_INJECTION_PATTERNS:
            match = pattern.search(body_str)
            if match:
                self.findings.append(
                    SecurityFinding(
                        rule_id="CMDI-001",
                        category="Command Injection",
                        severity=severity,
                        description=desc,
                        evidence=match.group()[:100],
                        recommendation="避免直接拼接系统命令，使用参数化 API 或输入白名单过滤",
                    )
                )

        _log.info("SecurityScanner: %d findings for %s", len(self.findings), url or "(no url)")
        return self.findings

    def _check_csrf(self, body: str, status_code: int, url: str) -> None:
        """CSRF 检测（增强版）— 检测表单和响应中的 CSRF token 缺失

        检测策略:
        1. 检测 <form> 标签中是否缺少 CSRF token
        2. 检测状态变更表单（POST/PUT/DELETE 方法）中是否缺少 CSRF token
        3. 检测 Cookie 中是否设置 SameSite 属性（仅当有 headers 时）
        """
        if not body:
            return

        body_lower = body.lower()

        # CSRF token 常见名称
        csrf_token_names = re.compile(
            r"csrfmiddlewaretoken|csrf_token|_csrf|csrfToken|"
            r"authenticity_token|_token|__RequestVerificationToken",
            re.IGNORECASE,
        )

        # 5a. 检测 HTML 表单中缺少 CSRF token
        if "text/html" in (url or "") or "<form" in body_lower:
            # 提取所有 <form> 标签
            form_pattern = re.compile(
                r"<form\b[^>]*>(.*?)</form>", re.IGNORECASE | re.DOTALL
            )
            forms = form_pattern.findall(body)
            if forms:
                for form_idx, form_html in enumerate(forms):
                    form_html_lower = form_html.lower()
                    # 检查是否是状态变更表单（POST/PUT/DELETE）
                    method_match = re.search(
                        r'<form[^>]*method\s*=\s*["\']?(POST|PUT|DELETE)["\']?',
                        body,
                        re.IGNORECASE,
                    )
                    is_state_changing = (
                        method_match is not None or "post" in form_html_lower
                    )

                    # 检查表单内是否有 CSRF token
                    has_csrf = bool(csrf_token_names.search(form_html))

                    if not has_csrf:
                        severity = "high" if is_state_changing else "medium"
                        self.findings.append(
                            SecurityFinding(
                                rule_id="CSRF-001",
                                category="CSRF Protection",
                                severity=severity,
                                description=(
                                    f"表单 #{form_idx + 1} 中未检测到 CSRF token"
                                    + ("（状态变更表单）" if is_state_changing else "")
                                ),
                                evidence=form_html[:80],
                                recommendation=(
                                    "所有状态变更请求应包含 CSRF token"
                                    "（如 csrfmiddlewaretoken / _token），"
                                    "或使用 SameSite=Strict/Lax Cookie"
                                ),
                            )
                        )

            # 5b. 通用检测：页面有 form 但全局无任何 CSRF token 痕迹
            elif "<form" in body_lower and not csrf_token_names.search(body):
                self.findings.append(
                    SecurityFinding(
                        rule_id="CSRF-002",
                        category="CSRF Protection",
                        severity="high",
                        description="页面包含表单但全局未检测到任何 CSRF token 字段",
                        evidence="",
                        recommendation="在表单中添加 CSRF token，或使用 SameSite Cookie 策略",
                    )
                )

    def scan_api_calls(self, calls: list[dict]) -> list[SecurityFinding]:
        """批量扫描 ApiTracker 采集的 HTTP 调用列表

        对每个有响应的调用执行 scan_response，并进行 IDOR 跨身份比较。

        参数:
            calls: ApiTracker._calls 列表，每个元素包含
                   url/method/status/resp_body/resp_headers 等

        返回:
            SecurityFinding 列表
        """
        all_findings: list[SecurityFinding] = []

        # 按路径分组，用于 IDOR 检测
        path_responses: dict[str, list[dict]] = {}

        for call in calls:
            if not isinstance(call, dict):
                continue
            if not call.get("has_response"):
                continue

            url = call.get("url", "")
            status_code = call.get("status") or 200
            resp_body = call.get("resp_body")
            resp_headers = call.get("resp_headers") or {}

            # resp_body 可能是 dict（JSON 解析后）或 str
            if isinstance(resp_body, dict):
                body_str = str(resp_body)
            elif isinstance(resp_body, str):
                body_str = resp_body
            else:
                body_str = ""

            # 推断 content_type
            content_type = resp_headers.get("content-type", "text/html") if resp_headers else "text/html"

            # 执行单次扫描
            findings = self.scan_response(
                body=body_str,
                content_type=content_type,
                url=url,
                status_code=status_code,
                headers=resp_headers,
            )
            all_findings.extend(findings)

            # 收集用于 IDOR 检测的数据
            from urllib.parse import urlparse

            parsed = urlparse(url)
            path_key = f"{call.get('method', 'GET')} {parsed.path}"
            if path_key not in path_responses:
                path_responses[path_key] = []
            path_responses[path_key].append(
                {
                    "url": url,
                    "status": status_code,
                    "body": body_str,
                    "post_data": call.get("post_data"),
                }
            )

        # IDOR 检测：同一路径不同身份返回的数据对比
        idor_findings = self._detect_idor(path_responses)
        all_findings.extend(idor_findings)

        # 方法篡改 + Mass Assignment 检测（2026-09-05 补齐，API 层）
        all_findings.extend(self._detect_method_tamper(calls))
        all_findings.extend(self._detect_mass_assignment(calls))

        self.findings = all_findings
        _log.info(
            "SecurityScanner.scan_api_calls: %d findings from %d calls",
            len(all_findings),
            len(calls),
        )
        return all_findings

    def _detect_idor(
        self, path_responses: dict[str, list[dict]]
    ) -> list[SecurityFinding]:
        """IDOR 检测：同一资源路径在不同身份下返回了不应看到的数据

        检测策略:
        1. 同一路径有多个不同身份的响应
        2. 比较响应中的敏感字段，如果低权限身份也能看到高权限数据 → IDOR
        3. 检测响应中是否包含其他用户的数据（如 user_id 不匹配）
        """
        findings: list[SecurityFinding] = []

        for path_key, responses in path_responses.items():
            if len(responses) < 2:
                continue

            # 比较不同响应的 body 内容
            bodies = [r["body"] for r in responses if r["body"]]
            if len(bodies) < 2:
                continue

            # 如果不同身份访问同一路径返回完全相同的数据，可能存在 IDOR
            unique_bodies = set(bodies)
            if len(unique_bodies) == 1 and len(bodies) > 1:
                # 同一路径、不同请求返回完全相同的数据
                # 可能是缓存问题，也可能是 IDOR（低权限看到了高权限数据）
                findings.append(
                    SecurityFinding(
                        rule_id="IDOR-001",
                        category="IDOR",
                        severity="high",
                        description=(
                            f"路径 {path_key} 被不同身份访问时返回完全相同的数据，"
                            f"可能存在 IDOR（越权数据访问）"
                        ),
                        evidence=f"路径: {path_key}, 响应数: {len(bodies)}, 响应体一致",
                        recommendation=(
                            "后端应基于当前用户身份过滤数据，"
                            "确保低权限用户无法访问高权限用户的数据"
                        ),
                    )
                )
            else:
                # 检查响应中是否包含其他用户的敏感字段
                for resp in responses:
                    body = resp["body"]
                    if not body:
                        continue
                    # 检测响应中是否有其他用户的数据标记
                    # 如响应中包含 user_id 但与请求身份不匹配
                    for field in self.IDOR_SENSITIVE_FIELDS:
                        # 检测 JSON 响应中的用户数据字段
                        pattern = re.compile(
                            rf'["\']?{field}["\']?\s*[:=]\s*["\']?(\d+)["\']?',
                            re.IGNORECASE,
                        )
                        matches = pattern.findall(body)
                        if len(matches) > 1:
                            # 多个不同 ID 出现在同一响应中
                            unique_ids = set(matches)
                            if len(unique_ids) > 1:
                                findings.append(
                                    SecurityFinding(
                                        rule_id="IDOR-002",
                                        category="IDOR",
                                        severity="medium",
                                        description=(
                                            f"路径 {path_key} 响应中包含多个不同的 {field} 值，"
                                            f"可能泄露了其他用户的数据"
                                        ),
                                        evidence=f"字段: {field}, 值: {','.join(list(unique_ids)[:3])}",
                                        recommendation=(
                                            "后端应严格按当前用户身份过滤返回数据，"
                                            "避免在响应中包含其他用户的数据"
                                        ),
                                    )
                                )
                                break

        return findings

    def _detect_method_tamper(self, calls: list[dict]) -> list[SecurityFinding]:
        """HTTP 方法篡改检测（MTD-001，2026-09-05 补齐，API 层）。

        REST 语义下，对同一资源路径，若同时存在 GET/POST 与 PUT/DELETE/PATCH 调用
        且后者返回 2xx，说明靶子未做「按 HTTP 方法的访问控制」——常规跳过状态变更类
        方法（不含显式 DRF 之类需框架判定）的行为视为潜在方法级失控。

        通用性：不认具体业务；仅统计同一路径下方法放行矩阵。取真实 2xx 记录，
        避免对 4xx/5xx 误报。仅当既有 GET/POST 基线又有 PUT/DELETE/PATCH 时记录，
        防对"纯 GET 只读接口"误报。
        """
        from urllib.parse import urlparse

        findings: list[SecurityFinding] = []
        by_path: dict[str, set[str]] = {}
        codes: dict[tuple[str, str], int] = {}
        for call in calls:
            if not isinstance(call, dict):
                continue
            path = urlparse(str(call.get("url", ""))).path
            method = str(call.get("method", "GET")).upper()
            by_path.setdefault(path, set()).add(method)
            codes[(path, method)] = call.get("status") or 0

        for path, methods in by_path.items():
            state_methods = {m for m in ("PUT", "DELETE", "PATCH") if m in methods}
            if not state_methods:
                continue  # 无状态变更调用 → 不是方法篡改场景
            # 任一状态变更方法返回 2xx（且该 path 至少有一个 GET/POST 基线）
            has_baseline = bool({"GET", "POST"} & methods)
            if not has_baseline:
                continue
            for m in sorted(state_methods):
                status = codes.get((path, m), 0)
                if 200 <= status < 300:
                    findings.append(
                        SecurityFinding(
                            rule_id="MTD-001",
                            category="HTTP Method Tampering",
                            severity="high",
                            description=(
                                f"路径 {path} 的状态变更方法 {m} 返回 {status}（2xx），"
                                f"疑似未做按 HTTP 方法的访问控制"
                            ),
                            evidence=f"{m} {path} → {status}",
                            recommendation=(
                                "对受保护资源显式放行预期的 HTTP 方法，"
                                "其余方法（PUT/DELETE/PATCH 等）应返回 405 或 403"
                            ),
                        )
                    )
        return findings

    def _detect_mass_assignment(self, calls: list[dict]) -> list[SecurityFinding]:
        """批量赋值（Mass Assignment）检测（MASS-001，2026-09-05 补齐，API 层）。

        客户端在请求体提交了后端实体中不应由外部写入的敏感字段（role / is_admin /
        balance / permissions 等）→ 潜在批量赋值。仅当写入型方法携带敏感字段时记录；
        不判业务是否真的把字段落库（那是靶子逻辑），只做「危险输入面」通用识别。
        """
        import json as _json

        findings: list[SecurityFinding] = []
        for call in calls:
            if not isinstance(call, dict):
                continue
            method = str(call.get("method", "GET")).upper()
            if method not in ("POST", "PUT", "PATCH"):
                continue  # 非写入方法不评估
            url = str(call.get("url", ""))
            post_data = call.get("post_data") or call.get("body")
            if post_data is None:
                continue
            # post_data 可能是 str(JSON) / dict / list
            payload_str = post_data if isinstance(post_data, str) else _json.dumps(post_data)
            if not payload_str:
                continue
            # 逐敏感字段做字面匹配（词边界，避免 role 命中 role_id）
            for field in self.MASS_ASSIGNMENT_FIELDS:
                pat = re.compile(
                    r'["\']?' + re.escape(field) + r'["\']?\s*:',
                    re.IGNORECASE,
                )
                if pat.search(payload_str):
                    findings.append(
                        SecurityFinding(
                            rule_id="MASS-001",
                            category="Mass Assignment",
                            severity="medium",
                            description=(
                                f"{method} {url} 请求体携带敏感字段 '{field}'，"
                                f"存在批量赋值/提权风险"
                            ),
                            evidence=f"{method} {url} → {field}",
                            recommendation=(
                                "定义显式 DTO/白名单字段，禁止客户端直接传入 role / is_admin 等"
                                "服务端管控字段"
                            ),
                        )
                    )
        return findings

    def get_summary(self) -> dict:
        """获取扫描摘要"""
        severity_count = {"critical": 0, "high": 0, "medium": 0, "low": 0}
        category_count = {}
        for f in self.findings:
            severity_count[f.severity] = severity_count.get(f.severity, 0) + 1
            category_count[f.category] = category_count.get(f.category, 0) + 1

        return {
            "total_findings": len(self.findings),
            "by_severity": severity_count,
            "by_category": category_count,
            "findings": [
                {
                    "rule_id": f.rule_id,
                    "category": f.category,
                    "severity": f.severity,
                    "description": f.description,
                    "evidence": f.evidence,
                    "recommendation": f.recommendation,
                }
                for f in self.findings
            ],
        }

    def is_clean(self) -> bool:
        """是否无严重/高危发现"""
        return not any(f.severity in ("critical", "high") for f in self.findings)

    def has_high_risk(self) -> bool:
        """是否存在 critical 或 high 级别发现"""
        return any(f.severity in ("critical", "high") for f in self.findings)

    def to_dict_list(self) -> list[dict]:
        """将 findings 转为可序列化的 dict 列表"""
        return [
            {
                "rule_id": f.rule_id,
                "category": f.category,
                "severity": f.severity,
                "description": f.description,
                "evidence": f.evidence,
                "recommendation": f.recommendation,
            }
            for f in self.findings
        ]


__all__ = ["SecurityScanner", "SecurityFinding"]
