# -*- coding: utf-8 -*-
"""功能标签规则：标签阶段，按操作关键词聚类打功能标签。"""

import re
from collections import Counter
from typing import Any


from .base import LabelRule


class FeatureLabelRule(LabelRule):
    """按操作关键词聚类打功能标签（纯算法，不合并三元组）。

    从 operation 中提取功能关键词，投票决定标签。
    同功能不同操作 = 不同原子需求（只打标签，不合并）。
    """

    rule_id = "feature_label"
    name = "功能标签"
    description = "按操作关键词聚类打功能标签，不合并三元组（同功能不同操作保持独立）"
    priority = 100

    # ── 功能关键词正则（中文业务领域）──
    _FEATURE_KEYWORDS = re.compile(
        r"品牌|辅材|分类|属性|规格|物料|供应商|客户|订单|合同|"
        r"仓库|库存|采购|销售|财务|报表|审批|权限|配置|设置|"
        r"数据|导入|导出|查询|统计|分析|监控|日志|通知|消息|"
        r"登录|注册|用户|角色|菜单|页面|按钮|表单|表格|"
        r"产品|商品|价格|折扣|优惠|支付|退款|发票|"
        r"地址|区域|物流|配送|签收|退货",
        re.IGNORECASE,
    )

    def apply(
        self, triples: list[dict[str, Any]], context: dict[str, Any] = None
    ) -> list[dict[str, Any]]:
        """给每个三元组打功能标签（不合并）。"""
        if not triples:
            return triples

        # 收集所有操作中的关键词投票
        all_keywords = []
        for t in triples:
            operation = t.get("operation") or ""
            keywords = self._FEATURE_KEYWORDS.findall(operation)
            all_keywords.extend(keywords)

        if not all_keywords:
            return triples

        # 投票决定主标签
        counter = Counter(all_keywords)
        primary_label = counter.most_common(1)[0][0] if counter else ""

        # 打标签
        for t in triples:
            operation = t.get("operation") or ""
            kw = self._FEATURE_KEYWORDS.findall(operation)
            t["feature_label"] = primary_label
            t["_feature_keywords"] = kw

        return triples
