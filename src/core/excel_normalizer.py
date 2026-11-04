# -*- coding: utf-8 -*-
"""
Excel 测试用例归一化工具函数
============================
从 ExcelHandler 类中提取的归一化方法，处理意图数据规范化、schema 保证、模块查找。
"""

from datetime import datetime
import logging

_log = logging.getLogger(__name__)


def normalize_intent_for_write(intent_data):
    """归一化测试用例数据，填充默认值、提取 schema 摘要、规范化标题/类型。

    原为 ExcelHandler._normalize_intent_for_write，去掉 self 依赖。
    内部直接调用 case_normalizer 模块函数。
    """
    import copy
    from src.core import case_normalizer as _n

    data = copy.deepcopy(intent_data)
    data.setdefault("catalog_level", "..")
    data.setdefault("catalog_name", "")
    data.setdefault("precondition", "")
    data.setdefault("auto_flag", "否")
    data.setdefault("threshold_flag", "否")
    data.setdefault("author", "")
    data.setdefault("verification_time", "")
    if not data.get("created_date"):
        data["created_date"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    data.setdefault("verification_status", "")
    data.setdefault("risk_level", "P2")
    data.setdefault("req_id", "")

    # v9: 摘要从 title / title 提取
    what = str(data.get("title") or "")
    title = str(data.get("title") or "")
    data["intent_summary"] = what or title
    data.setdefault("expected_outcome", what or title)
    data.setdefault("case_type", str(data.get("type") or ""))

    raw_title = data.get("title", "")
    data["title"] = _n._normalize_title(raw_title)
    raw_type = data.get("case_type", data.get("type", ""))
    data["case_type"] = _n._normalize_case_type(raw_type)
    return data


def lookup_module(ref_req_id: str, read_requirements_fn, module_cache: dict) -> str:
    """根据需求编号查找所属模块（带缓存）。

    原为 ExcelHandler._lookup_module，通过回调参数替代 self.read_requirements()，
    通过外部传入 module_cache 字典替代 self._module_cache。

    Args:
        ref_req_id: 需求编号
        read_requirements_fn: 返回需求列表的回调函数（替代 self.read_requirements()）
        module_cache: 外部维护的缓存字典（替代 self._module_cache）
    """
    if not ref_req_id:
        return ""
    if not module_cache:
        try:
            for req in read_requirements_fn():
                rid = req.get("req_id", "")
                if rid:
                    module_cache[rid] = req.get("module", "")
        except Exception as _e:
            _log.debug("cache + cache + read: %s", _e, exc_info=True)
    return module_cache.get(ref_req_id, "")
