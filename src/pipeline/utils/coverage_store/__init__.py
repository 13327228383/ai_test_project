# -*- coding: utf-8 -*-
"""
覆盖率 MySQL 事实源存储
========================

定位：把原有的 ``coverage_matrix.xlsx`` 沉淀到 MySQL ``coverage_results`` 表，
作为 Step3/Step5/报告链路的唯一结构化事实源。``coverage_matrix.xlsx`` 自身降级为
显式导出物/附件角色，不再参与主运行链路兜底。

核心字段：
    - project              项目名（隔离多项目）
    - run_id               运行批次标识（同一项目可有多轮，以时间戳区分）
    - req_id               需求编号
    - dim_positive         正向覆盖状态（covered / missing / n/a）
    - dim_negative         异常覆盖状态
    - dim_boundary         边界值覆盖状态
    - dim_security         安全覆盖状态
    - dim_api_check        API轨校验覆盖状态
    - dim_api_coverage     API接口覆盖状态（v6）
    - dim_db_coverage      DB表覆盖状态（v6）
    - dim_page_coverage    页面覆盖状态（v6）
    - dim_trace_complete   追溯链路完整性（v6）
    - total_cases          该需求的测试用例数
    - has_gaps             是否存在缺口（0/1）
    - gap_dims_json        缺口维度列表（JSON 数组）
    - payload_json         完整覆盖结果原始 JSON，供回退/导出/再生成
    - created_at / updated_at
"""
import logging

from .base import _CoverageBaseMixin, _ALL_DIMS
from .write import _CoverageWriteMixin
from .read import _CoverageReadMixin
from .io import _CoverageIOMixin
from .health import _CoverageHealthMixin

_log = logging.getLogger(__name__)


class CoverageStore(
    _CoverageBaseMixin,
    _CoverageWriteMixin,
    _CoverageReadMixin,
    _CoverageIOMixin,
    _CoverageHealthMixin,
):
    """MySQL ``coverage_results`` 表读写工具。"""

    pass


coverage_store = CoverageStore()
