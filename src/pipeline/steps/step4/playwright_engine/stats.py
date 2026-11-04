# -*- coding: utf-8 -*-
"""点击兜底命中率统计模块。"""


class ClickFallbackStats:
    """★ KEEP-07: 五级点击回退命中率统计。
    记录每次点击操作在哪个兜底级别成功，用于计算各级别命中率，
    指导优化 ai_id 标注质量或语义选择器覆盖度。"""

    def __init__(self):
        self.reset()

    def reset(self):
        self._levels = {
            "level_0_direct": 0,
            "level_1_scroll": 0,
            "level_2_force": 0,
            "level_3_js": 0,
            "level_4_semantic": 0,
            "level_5_visual": 0,
            "total_attempts": 0,
        }

    def record(self, level: int):
        """记录一次成功点击所在的兜底级别。level: 0-5。"""
        key_map = {
            0: "level_0_direct",
            1: "level_1_scroll",
            2: "level_2_force",
            3: "level_3_js",
            4: "level_4_semantic",
            5: "level_5_visual",
        }
        key = key_map.get(level)
        if key:
            self._levels[key] += 1
        self._levels["total_attempts"] += 1

    def snapshot(self) -> dict:
        """返回当前命中率快照。"""
        total = max(self._levels["total_attempts"], 1)
        result = {"total": self._levels["total_attempts"]}
        for k, v in self._levels.items():
            if k == "total_attempts":
                continue
            result[k] = v
            result[k + "_rate"] = round(v / total * 100, 1)
        # 直连命中率 = level_0 / total
        result["direct_hit_rate"] = round(
            self._levels.get("level_0_direct", 0) / total * 100, 1
        )
        return result


# ★ 方案C 改造：_click_stats 改为 PerProjectRegistry，按 project 隔离
#   原实现：全局单例，点击兜底命中率统计跨项目混合
from src.core.utils.per_project_registry import PerProjectRegistry

_click_stats = PerProjectRegistry(ClickFallbackStats)
