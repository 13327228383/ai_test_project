# -*- coding: utf-8 -*-
"""禁止词列表 + Prompt 上下文格式化 — 从 test_rules_kb.py 拆出，零业务逻辑变更。"""


MAX_INJECTED_RULES_PER_REQUIREMENT = 10
MAX_RULE_CONTEXT_CHARS = 4000
MAX_MUST_COVER_PER_RULE = 3
MAX_COMMON_BUGS_PER_RULE = 1


class _FormattingMixin:
    """get_forbidden_terms / format_forbidden_context / format_context（注入 prompt 的上下文）。"""

    # ══════════════════════════════════════════════════════════
    # ★ P1-2: 禁止词列表
    # ══════════════════════════════════════════════════════════

    # 内置默认禁止词列表
    DEFAULT_FORBIDDEN_TERMS = [
        "禁止涉及以下未上线功能: [从项目配置读取]",
        "禁止在验证意图中使用外部工具: Burp Suite, Postman, Fiddler, Charles",
        "禁止在验证意图中要求浏览器 DevTools 操作",
        "禁止编造不存在的 API 版本号（如 /v3/ 而系统只有 /v1/）",
        "禁止在 title 中描述具体的 UI 文案（除非需求明确提及）",
        "禁止使用「打开浏览器开发者工具」「拦截API请求」「修改请求参数」等需要DevTools的操作",
        "禁止使用「在后台数据库中修改XX」「在服务器上执行XX」等需要服务器权限的操作",
        "禁止在安全测试中使用 Burp Suite、Postman 等外部工具——改为在输入框注入payload",
    ]

    def get_forbidden_terms(self) -> list[str]:
        """
        获取禁止词列表。
        优先使用项目级配置，否则使用内置默认列表。
        """
        self._ensure_loaded()
        # 项目级规则可以覆盖禁止词
        if self._project_rules:
            project_forbidden = self._project_rules.get("forbidden_terms", [])
            if project_forbidden:
                return project_forbidden
        return list(self.DEFAULT_FORBIDDEN_TERMS)

    def format_forbidden_context(self) -> str:
        """
        将禁止词列表格式化为可注入 Prompt 的规则文本。
        """
        terms = self.get_forbidden_terms()
        if not terms:
            return ""
        lines = [
            "\n## 🚫 禁止生成的验证意图类型",
        ]
        for term in terms:
            lines.append(f"- {term}")
        return "\n".join(lines)

    def format_context(self, matched_rules: list[dict]) -> str:
        """
        将匹配的规则格式化为可注入 prompt 的上下文。

        返回:
            格式化的规则上下文字符串
        """
        if not matched_rules:
            return ""

        constraints = self.get_global_constraints()

        lines = []
        current_chars = 0
        truncated = False

        def add_line(line: str = "") -> bool:
            nonlocal current_chars, truncated
            text = str(line)
            extra = len(text) + (1 if lines else 0)
            if current_chars + extra > MAX_RULE_CONTEXT_CHARS:
                truncated = True
                return False
            lines.append(text)
            current_chars += extra
            return True

        for line in ("\n## 🛡️ 测试规则护栏（★ 必须遵守）", "", "### 匹配的测试模式"):
            if not add_line(line):
                break

        for rule in matched_rules[:MAX_INJECTED_RULES_PER_REQUIREMENT]:
            if not add_line(
                f"\n**{rule.get('name', '')}**（规则ID: {rule.get('id', '')}）"
            ):
                break
            if not add_line("必须覆盖的测试点:"):
                break
            for item in rule.get("must_cover", [])[:MAX_MUST_COVER_PER_RULE]:
                if not add_line(f"  - {item}"):
                    break
            bugs = rule.get("common_bugs", [])
            if bugs:
                if not add_line("常见历史缺陷（避开这些坑）:"):
                    break
                for bug in bugs[:MAX_COMMON_BUGS_PER_RULE]:
                    if not add_line(f"  - ⚠ {bug}"):
                        break

        if constraints:
            add_line("\n### 全局硬约束")
            for c in constraints:
                if not add_line(f"  - {c}"):
                    break

        if truncated and current_chars + 20 <= MAX_RULE_CONTEXT_CHARS:
            add_line("（规则上下文已按预算截断）")

        return "\n".join(lines)
