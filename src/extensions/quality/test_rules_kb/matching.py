# -*- coding: utf-8 -*-

"""规则匹配 + 命中率统计 — 从 test_rules_kb.py 拆出，零业务逻辑变更。"""


from typing import Any


import logging

_log = logging.getLogger(__name__)
MAX_CANDIDATE_RULES_PER_REQUIREMENT = 50


class _MatchingMixin:
    """match_rules / get_hit_stats / flush_hit_stats_to_metrics / get_global_constraints。"""

    def match_rules(self, requirement: dict) -> list[dict]:
        """

        根据需求关键词匹配适用的测试规则。



        参数:

            requirement: {"req_id": "REQ-001", "module": "...", "description": "..."}



        返回:

            匹配到的规则列表

        """

        self._ensure_loaded()

        module = (requirement.get("module", "") or "").lower()

        desc = (requirement.get("description", "") or "").lower()

        combined = f"{module} {desc}"

        matched = []

        self._total_match_calls += 1

        for pattern in self._rules.get("patterns", []):

            keywords = pattern.get("match_keywords", [])

            for kw in keywords:

                if kw in combined:

                    matched.append(pattern)

                    # ★ KEEP-16: 记录规则命中

                    rule_id = pattern.get("id", "unknown")

                    self._hit_stats[rule_id] += 1

                    break  # 一个规则只匹配一次

            if len(matched) >= MAX_CANDIDATE_RULES_PER_REQUIREMENT:

                break

        return matched

    def get_hit_stats(self) -> dict[str, Any]:
        """★ KEEP-16: 返回规则命中率统计。"""

        total = max(self._total_match_calls, 1)

        result = {
            "total_calls": self._total_match_calls,
            "total_hits": sum(self._hit_stats.values()),
            "hit_rate": round(sum(self._hit_stats.values()) / total * 100, 1),
            "by_rule": {
                k: {"hits": v, "rate": round(v / total * 100, 1)}
                for k, v in sorted(self._hit_stats.items(), key=lambda x: -x[1])
            },
        }

        return result

    def flush_hit_stats_to_metrics(self, project: str, run_id: str):
        """★ KEEP-16: 将规则命中率写入 metrics_collector。"""

        if not self._hit_stats:

            return

        try:

            from src.extensions.metrics_collector import metrics_collector

            for rule_id, count in self._hit_stats.items():

                metrics_collector.record(
                    project,
                    run_id,
                    step=3,
                    metric_type="rule_hit_rate",
                    metric_name=rule_id,
                    category="rule_hit",
                    value=float(count),
                )

            metrics_collector.record(
                project,
                run_id,
                step=3,
                metric_type="rule_hit_rate",
                metric_name="total_match_calls",
                category="rule_hit",
                value=float(self._total_match_calls),
            )

        except Exception as _e:

            _log.debug("import + metric + metric: %s", _e, exc_info=True)

    def get_global_constraints(self) -> list[str]:
        """获取全局约束（所有需求都适用）。"""

        self._ensure_loaded()

        return list(self._rules.get("global_constraints", []))
