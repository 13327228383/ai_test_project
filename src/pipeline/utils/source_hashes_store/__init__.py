# -*- coding: utf-8 -*-
"""
Step1/Step2 源数据哈希存储（MySQL 唯一事实源）
=================================================

定位：把 Step1/Step2 的三份磁盘 JSON 哈希沉淀到 MySQL 三张表，
作为 Step1/Step2 主运行链路的唯一结构化事实源。三份 JSON 文件
（``confluence_hash.json``、``yapi_hash.json``、``breakdown_hash.json``）
自身降级为显式导入源 / 导出物 / 附件角色，不参与 Step1/Step2 主运行
链路兜底。

三张表：
    - source_confluence_hashes  Confluence 源数据哈希（overall + 每页 page_id）
    - source_yapi_hashes        YApi 接口文档源数据哈希（overall）
    - source_breakdown_hashes   Step2 需求拆解哈希（overall + 每页 page_id）

典型调用：
    from src.pipeline.utils.source_hashes_store import source_hashes_store

    # 读写 confluence 哈希
    cf = source_hashes_store.load_confluence_hash(version_label="v1")
    source_hashes_store.save_confluence_hash(
        overall_hash="deadbeef",
        page_hashes={"page-1": "cafebabe"},
        version_label="v1",
    )

    # 读写 yapi 哈希
    yh = source_hashes_store.load_yapi_hash(version_label="v1")
    source_hashes_store.save_yapi_hash(overall_hash="cafebabe", version_label="v1")

    # 读写 breakdown 哈希（Step2 断点续跑用）
    bh = source_hashes_store.load_breakdown_hash(version_label="v1")
    source_hashes_store.save_breakdown_hash(
        overall_hash="deadbeef",
        page_hashes={"page-1": "cafebabe"},
        version_label="v1",
        meta={"req_count": 10},
    )

    # 显式导入 / 导出（JSON 仅作为附件，不参与主链路兜底）
    source_hashes_store.export_confluence_to_json(path, version_label="v1")
    source_hashes_store.import_confluence_from_json(path, version_label="v1")
    source_hashes_store.export_yapi_to_json(path, version_label="v1")
    source_hashes_store.import_yapi_from_json(path, version_label="v1")
    source_hashes_store.export_breakdown_to_json(path, version_label="v1")
    source_hashes_store.import_breakdown_from_json(path, version_label="v1")

    # 健康检查（consistency_checker --db-health 可调用）
    stats = source_hashes_store.health_stats()
"""

from .base import _SourceHashesBaseMixin
from .breakdown import _SourceHashesBreakdownMixin
from .confluence import _SourceHashesConfluenceMixin
from .health import _SourceHashesHealthMixin
from .yapi import _SourceHashesYapiMixin


class SourceHashesStore(
    _SourceHashesBaseMixin,
    _SourceHashesConfluenceMixin,
    _SourceHashesYapiMixin,
    _SourceHashesBreakdownMixin,
    _SourceHashesHealthMixin,
):
    """Confluence / YApi / Breakdown 三份断点哈希的 MySQL 统一存储。

    通过 ``db_getter`` 注入连接后可用于单测；不注入时默认使用
    ``src.core.db_manager.get_connection()``，这与其他 ``*_store.py`` 保持一致。

    实现采用 mixin 多继承模式，各子模块位于 ``source_hashes_store_mixins/`` 子目录。
    """

    pass


# ---------------------------------------------------------------------------
# 默认单例
# ---------------------------------------------------------------------------
source_hashes_store = SourceHashesStore()
