# -*- coding: utf-8 -*-
"""
审核 MySQL 事实源（ReviewStore）
==================================

事实源表：
  - case_meta      : 测试用例元数据（置信度 / 风险等级 / 审核状态 / 审核人 / 审核时间）
  - review_log     : 审核操作日志（谁在什么时间对什么做了什么动作）

设计原则：
  1. MySQL 是唯一结构化事实源；本文件是主运行链路唯一的写入/读取入口。
  2. Excel / Markdown / JSON / XMind 不再参与主运行链路兜底，只作为显式导入/导出产物。
  3. 最小可验证改动：只对已经沉淀在 MySQL 的表做读写封装，不新增额外表结构。
"""

from src.pipeline.utils.review_store.base import ALLOWED_REVIEW_STATUS, _ReviewBaseMixin
from src.pipeline.utils.review_store.case_meta import _ReviewIntentMetaMixin
from src.pipeline.utils.review_store.review_log import _ReviewLogMixin
from src.pipeline.utils.review_store.health import _ReviewHealthMixin


class ReviewStore(
    _ReviewBaseMixin, _ReviewIntentMetaMixin, _ReviewLogMixin, _ReviewHealthMixin
):
    """审核事实源（case_meta + review_log）统一读写器。"""

    pass


review_store = ReviewStore()
