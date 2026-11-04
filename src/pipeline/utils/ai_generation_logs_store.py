# -*- coding: utf-8 -*-
"""
AI 生成日志 MySQL 事实源存储
=============================

定位：把原有的 ``hallucination_samples.jsonl`` 沉淀到 MySQL ``ai_generation_logs``
表，作为 Step4 执行链路的唯一结构化事实源。``hallucination_samples.jsonl`` 自身降级
为显式导入源/导出物/附件角色，不参与 Step4 主运行链路兜底。

核心字段：
    - project              项目名
    - log_id               记录 ID（主键）
    - case_id              测试用例 ID
    - req_id               所属需求 ID
    - module               模块
    - title                测试用例标题
    - failure_reason       失败原因文本
    - classification       分类：env / hallucination / unknown
    - raw_steps_json       原始步骤 JSON（★ BUG-3: 与活库列名一致，勿写成 raw_case_actions_json）
    - source               日志来源（hallucination_logger / manual / 其他）
    - created_at           时间戳

实现拆分为 facade + mixins 模式，具体能力见 ``ai_generation_logs_store_mixins`` 子包。
"""

import logging

from .ai_generation_logs_store_mixins._base import AiGenerationLogsStoreBaseMixin
from .ai_generation_logs_store_mixins._write import AiGenerationLogsStoreWriteMixin
from .ai_generation_logs_store_mixins._read import AiGenerationLogsStoreReadMixin
from .ai_generation_logs_store_mixins._health import AiGenerationLogsStoreHealthMixin
from .ai_generation_logs_store_mixins._io import AiGenerationLogsStoreIoMixin

_log = logging.getLogger(__name__)


class AiGenerationLogsStore(
    AiGenerationLogsStoreBaseMixin,
    AiGenerationLogsStoreWriteMixin,
    AiGenerationLogsStoreReadMixin,
    AiGenerationLogsStoreHealthMixin,
    AiGenerationLogsStoreIoMixin,
):
    """MySQL ``ai_generation_logs`` 表读写工具。"""

    pass


ai_generation_logs_store = AiGenerationLogsStore()
