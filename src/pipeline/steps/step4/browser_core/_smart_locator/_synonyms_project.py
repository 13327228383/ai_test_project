# -*- coding: utf-8 -*-
"""项目业务同义词字典（与当前项目业务相关）。

★ 设计意图
当前 v0.1 留空骨架——业务术语别名应当由你（项目负责人）在跑通正向流程、
看到真实"哪些词漏匹配"后再补充，避免凭空猜测反而引入误匹配。

★ 维护方式
跑一次端到端测试 → 看 single.py 兜底分支的日志：
- 如果出现 "[SmartLocator] 关键词全部未命中" → 把意图描述里的关键词
  + UI 上对应按钮的实际文本，作为一个新簇加进 PROJECT_SYNONYMS
- 跑下一次时这个 case 应当能直接命中

★ 命名建议（项目相关簇）
比如某业务系统配方系统可能有：
    {"拼配", "blend", "混合", "调配"}      # 配方相关
    {"批次", "batch", "lot", "lotno"}      # 生产相关
    {"配方", "formula", "recipe"}
    {"烟丝", "tobacco", "shred"}
    {"指标", "metric", "kpi"}
    {"质检", "qc", "quality check"}
等等。但具体哪些词在 UI 上对应别名，必须看真实页面才知道。
"""

from ._synonyms_common import build_lookup_table


# ★ 此处按实际项目情况补充。当前留空，由 single.py 的日志驱动迭代。
PROJECT_SYNONYMS = [
    # 示例（已注释，不参与匹配；解开注释或替换为真实业务别名）：
    # {"拼配", "blend", "混合", "调配"},
    # {"批次", "batch", "lot"},
]


PROJECT_LOOKUP = build_lookup_table(PROJECT_SYNONYMS)
