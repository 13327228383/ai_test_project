# -*- coding: utf-8 -*-
"""需求歧义检测规则（A1）：校验阶段适配器 —— 把歧义检测接入 run_check 流水线。

真实检测逻辑在 ``src/pipeline/steps/step2/ambiguity_report.py``（单一实现，避免重复）。
本文件只做适配：把结构化歧义项转成 run_check 约定的 issue 结构
``{"type","severity","message","suggestion"}``，因此**不改变既有流水线契约**，
同时把完整歧义项挂在 issue["ambiguity"] 供需要的调用方深度消费。

severity 映射（歧义 → issue）：
  high   → warning（run_check 既有最高级别，不引入新枚举值）
  medium → info
  low    → info

[未复核-需 GLM/DeepSeek 把关]（新增规则，寇豆码 新增）
"""

from typing import Any

from .base import CheckRule

# 歧义 severity → run_check issue severity（不引入新枚举值，保持契约兼容）
_SEVERITY_MAP: dict[str, str] = {
    "high": "warning",
    "medium": "info",
    "low": "info",
}


class AmbiguityDetectionRule(CheckRule):
    """检测需求歧义并转为 issue（六类规则见 ambiguity_report 模块）。

    覆盖：字段含义冲突 / 缺少前置条件 / 预期结果不唯一 /
    涉及未定义实体 / 约束未量化。（跨需求冲突需全量上下文，
    由 ``ambiguity_report.apply_ambiguity_report`` 在批量阶段检测。）
    """

    rule_id = "ambiguity_detection"
    name = "需求歧义检测"
    description = (
        "检测需求歧义并产出可澄清问题（字段冲突/缺前置/预期不唯一/未定义实体/约束未量化）"
    )
    priority = 95

    def apply(self, req: dict[str, Any]) -> list[dict[str, Any]]:
        """把单条需求的歧义项转为 run_check issue 列表。

        Args:
            req: 需求三元组。

        Returns:
            issue 列表；无歧义时返回空列表。异常时 fail-open 返回空列表（不阻断流水线）。
        """
        try:
            from src.pipeline.steps.step2.ambiguity_report import detect_ambiguities

            items = detect_ambiguities(req, 0)
        except Exception:  # noqa: BLE001
            return []

        issues: list[dict[str, Any]] = []
        for item in items:
            questions = item.get("clarify_questions") or []
            issues.append(
                {
                    "type": "ambiguity_%s" % item.get("type", "unknown"),
                    "severity": _SEVERITY_MAP.get(item.get("severity", "medium"), "info"),
                    "message": "[需求歧义·%s] %s（影响：%s）"
                    % (
                        item.get("type_label", "-"),
                        item.get("ambiguity_point", "-"),
                        item.get("impact", "-"),
                    ),
                    "suggestion": "建议向产品/开发澄清：" + " / ".join(questions)
                    if questions
                    else "建议向产品/开发澄清该歧义点后再生成用例",
                    # 完整结构化歧义项（供需要深度消费的调用方使用）
                    "ambiguity": item,
                }
            )
        return issues
