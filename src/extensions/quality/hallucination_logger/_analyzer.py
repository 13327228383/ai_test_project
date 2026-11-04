# -*- coding: utf-8 -*-
"""
分析与规则更新 Mixin。

提供 ``analyze_and_update_rules()`` 及 ``_append_to_test_rules()`` 方法。
"""

import logging
from src.core.guarded import guard
from typing import Any

_log = logging.getLogger(__name__)


class HallucinationAnalyzerMixin:
    """分析与规则更新 Mixin —— 分析幻觉样本并自动生成护栏规则。"""

    def analyze_and_update_rules(self) -> dict[str, Any]:
        """分析积累的幻觉样本，自动生成新的护栏规则。"""
        samples = self._load_samples()
        if len(samples) < 3:
            return {
                "updated": False,
                "reason": "样本不足 (<3)",
                "sample_count": len(samples),
            }

        # 按 module 聚类
        patterns = {}
        for s in samples:
            module = s.get("module", "未知")
            if module not in patterns:
                patterns[module] = {"count": 0, "reasons": []}
            patterns[module]["count"] += 1
            patterns[module]["reasons"].append(s.get("failure_reason", ""))

        # 找到高频失败模式（同模块 >=3 次）
        new_rules = []
        for module, info in patterns.items():
            if info["count"] >= 3:
                # 自动生成规则
                reasons_summary = _summarize_reasons(info["reasons"])
                new_rules.append(
                    {
                        "module": module,
                        "frequency": info["count"],
                        "suggested_rule": reasons_summary,
                    }
                )

        if new_rules:
            self._append_to_test_rules(new_rules)
            return {
                "updated": True,
                "new_rules": new_rules,
                "sample_count": len(samples),
            }

        return {
            "updated": False,
            "reason": "无高频模式 (>=3)",
            "sample_count": len(samples),
        }

    def _append_to_test_rules(self, new_rules: list[dict]):
        """将新规则追加到 MySQL test_rules 表。"""
        with guard("_append_to_test_rules: from src.extensions.quality.test_rul", log=_log, level=logging.INFO):
            from src.extensions.quality.test_rules_kb import test_rules_kb

            ok = test_rules_kb.save_patterns(new_rules)
            if ok:
                _log.info(
                    "  [HallucinationLogger] 已追加 %d 条规则到 MySQL test_rules"
                    % len(new_rules)
                )
            else:
                _log.info("  [HallucinationLogger] 规则更新失败")


def _summarize_reasons(reasons: list[str]) -> str:
    """从多条失败原因中提取共同模式。"""
    if not reasons:
        return "未分类"
    keywords = {}
    for r in reasons:
        for word in r.split():
            if len(word) >= 3 and word not in (
                "失败",
                "错误",
                "异常",
                "缺失",
                "不匹配",
            ):
                keywords[word] = keywords.get(word, 0) + 1
    top = sorted(keywords.items(), key=lambda x: -x[1])[:5]
    if top:
        return "高频关键词: %s" % ", ".join(k for k, v in top if v >= 2)
    return reasons[0][:100]
