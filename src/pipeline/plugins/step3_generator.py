# -*- coding: utf-8 -*-
from __future__ import annotations

"""
Step3 测试用例生成策略插件
======================
支持动态注册测试用例生成策略和质量校验规则。

介入点：STEP3_START（注入策略）
"""

import logging
from collections.abc import Callable
from typing import Any

_log = logging.getLogger(__name__)


class Step3GeneratorPlugin:
    """Step3 测试用例生成策略插件。"""

    name = "step3_generator"
    enabled = True
    config = {}

    def __init__(self, config: dict[str, Any] = None):
        self.config = config or {}
        self.enabled = self.config.get("enabled", True)
        # 生成策略注册表
        self._generators: dict[str, dict] = {}
        # 质量校验规则注册表
        self._validators: dict[str, dict] = {}
        # 行业模板注册表
        self._templates: dict[str, dict] = {}

    def register_generator(
        self,
        name: str,
        generate_func: Callable,
        applies_to: list[str] = None,
        priority: int = 50,
    ):
        """
        注册测试用例生成策略。

        参数：
            name: 策略标识（如 "hipaa_compliant", "performance_stress"）
            generate_func: 生成函数，签名 generate_func(requirements, context) → intents_list
            applies_to: 适用的模块列表
            priority: 优先级
        """
        self._generators[name] = {
            "func": generate_func,
            "applies_to": applies_to or [],
            "priority": priority,
        }
        _log.info("[Step3Generator] 注册生成策略: %s (priority=%d)", name, priority)

    def register_validator(
        self,
        name: str,
        validate_func: Callable,
        applies_to: list[str] = None,
        severity: str = "warning",
    ):
        """
        注册质量校验规则。

        参数：
            name: 规则标识（如 "wcag_accessibility", "gdpr_compliance"）
            validate_func: 校验函数，签名 validate_func(intent, context) → validation_result
            applies_to: 适用的模块列表
            severity: 严重级别（error/warning/info）
        """
        self._validators[name] = {
            "func": validate_func,
            "applies_to": applies_to or [],
            "severity": severity,
        }
        _log.info("[Step3Generator] 注册校验规则: %s (severity=%s)", name, severity)

    def register_template(self, name: str, template: dict, industry: str = "general"):
        """
        注册行业测试用例模板。

        参数：
            name: 模板标识
            template: 模板定义（包含 title、checks[] 示例等）
            industry: 行业标识（如 "finance", "healthcare", "iot"）
        """
        self._templates[name] = {
            "template": template,
            "industry": industry,
        }
        _log.info("[Step3Generator] 注册模板: %s (industry=%s)", name, industry)

    def on_step3_start(self, ctx: dict[str, Any]) -> dict[str, Any] | None:
        """
        Step3 开始时，注入生成策略、校验规则和模板。

        ctx 包含:
            project: str
            requirements: list
            generators: dict — 当前生成策略
            validators: dict — 当前校验规则
            templates: dict — 当前模板
        """
        if not self.enabled:
            return None

        result = {}

        # 注入生成策略
        generators = ctx.get("generators", {})
        for name, gen in self._generators.items():
            if name not in generators:
                generators[name] = gen
        if generators:
            result["generators"] = generators

        # 注入校验规则
        validators = ctx.get("validators", {})
        for name, val in self._validators.items():
            if name not in validators:
                validators[name] = val
        if validators:
            result["validators"] = validators

        # 注入模板
        templates = ctx.get("templates", {})
        for name, tmpl in self._templates.items():
            if name not in templates:
                templates[name] = tmpl
        if templates:
            result["templates"] = templates

        if result:
            _log.info(
                "[Step3Generator] 注入 %d 个策略, %d 个校验, %d 个模板",
                len(generators),
                len(validators),
                len(templates),
            )
        return result if result else None

    def on_step3_complete(self, ctx: dict[str, Any]) -> dict[str, Any] | None:
        """
        Step3 完成后，记录生成质量统计。

        ctx 包含:
            project: str
            intents: list — 生成的测试用例
            validation_results: dict — 校验结果
        """
        if not self.enabled:
            return None

        intents = ctx.get("intents", [])
        validations = ctx.get("validation_results", {})

        stats = {
            "total_cases": len(intents),
            "generators_used": list(
                set(c.get("_generator", "default") for c in intents)
            ),
            "validators_applied": list(validations.keys()),
            "issues_by_severity": {
                "error": sum(
                    1
                    for v in validations.values()
                    for r in v.get("results", [])
                    if r.get("severity") == "error"
                ),
                "warning": sum(
                    1
                    for v in validations.values()
                    for r in v.get("results", [])
                    if r.get("severity") == "warning"
                ),
            },
        }

        _log.info("[Step3Generator] 生成统计: %s", stats)
        return {"generation_stats": stats}

    def list_generators(self) -> dict[str, dict]:
        """列出所有已注册的生成策略。"""
        return {
            name: {"priority": g["priority"], "applies_to": g["applies_to"]}
            for name, g in self._generators.items()
        }

    def list_validators(self) -> dict[str, dict]:
        """列出所有已注册的校验规则。"""
        return {
            name: {"severity": v["severity"], "applies_to": v["applies_to"]}
            for name, v in self._validators.items()
        }

    def list_templates(self) -> dict[str, dict]:
        """列出所有已注册的模板。"""
        return {
            name: {"industry": t["industry"]} for name, t in self._templates.items()
        }
