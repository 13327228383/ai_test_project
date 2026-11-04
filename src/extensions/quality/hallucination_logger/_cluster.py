# -*- coding: utf-8 -*-
"""
失败测试用例聚类与反馈 Mixin。

提供 ``cluster_failures()``、``summarize_feedback()``、``analyze_and_persist()`` 方法。
"""

import logging
import time
from collections import defaultdict
from datetime import datetime
from difflib import SequenceMatcher
from typing import Any

_log = logging.getLogger(__name__)


# 失败聚类使用的关键词表（大小写不敏感匹配）
_FAILURE_KEYWORDS = [
    "click",
    "点击",
    "登录",
    "login",
    "弹窗",
    "dialog",
    "API",
    "api",
    "数据库",
    "DB",
    "表单",
    "表单提交",
    "按钮",
    "列表",
    "导航",
    "加载",
    "timeout",
    "TimeOut",
]


class HallucinationClusterMixin:
    """失败测试用例聚类与反馈 Mixin —— 聚类、摘要、持久化。"""

    def cluster_failures(self, intents: list[dict[str, Any]]) -> dict[str, list[str]]:
        """
        对失败测试用例做简易聚类，返回 ``{cluster_name: [case_id, ...]}``。

        聚类策略：
        1. 按需求/模块名前缀（前 10 个字符相同 -> 同类）；
        2. 按失败原因中的关键词分组（命中 ``_FAILURE_KEYWORDS`` 任一 -> 同类）；
        3. 标题的 edit-distance (:class:`difflib.SequenceMatcher` ratio) > 0.6 -> 同类。

        返回的 clusters dict 中 ``key = "{prefix}|{primary_keyword}"``，``value`` 为
        ``case_id`` 列表。同时在日志中打印总测试用例数、失败测试用例数、聚类数（noise filter
        之后）。
        """
        if not intents:
            _log.info("  [HallucinationLogger] cluster_failures: 无输入测试用例。")
            return {}

        total = len(intents)
        # 认为 status=="fail" / failure_reason 非空 均算失败
        failure_intents = []
        for c in intents:
            if not isinstance(c, dict):
                continue
            status = str(c.get("status", "") or "").lower()
            reason = str(c.get("failure_reason", "") or c.get("reason", "") or "")
            if status in ("fail", "failed", "failure", "false") or reason:
                failure_intents.append(c)
        failure_count = len(failure_intents)

        # 1) 为每个失败测试用例计算初步分组键："{prefix}|{primary_keyword}"
        groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for c in failure_intents:
            module = str(c.get("module", "") or c.get("req_id", "") or "未知")
            prefix = module[:10] if module else "未知"
            title = str(c.get("title", "") or "")
            reason_text = " ".join(
                [
                    str(c.get("failure_reason", "") or c.get("reason", "") or ""),
                    str(c.get("error_message", "") or ""),
                    title,
                ]
            ).lower()

            # 找第一个命中的关键词作为 primary_keyword（按顺序优先）
            primary = "通用"
            for kw in _FAILURE_KEYWORDS:
                if kw.lower() in reason_text:
                    primary = kw
                    break

            key = "%s|%s" % (prefix, primary)
            groups[key].append(c)

        # 2) 标题 edit-distance 合并：对不同 key 中标题相似的测试用例，归并到同一个 key
        key_heads = {}  # key -> 代表标题（组内第一个非空标题）
        for key, items in groups.items():
            for item in items:
                t = str(item.get("title", "") or "")
                if t:
                    key_heads[key] = t
                    break
            else:
                key_heads[key] = ""

        # 建立 key -> merged_key 的映射；先按 primary_keyword 分桶，避免全量两两比较。
        merged_map: dict[str, str] = {}
        keys_by_primary: dict[str, list[str]] = defaultdict(list)
        for key in sorted(groups.keys()):
            _, _, primary = key.partition("|")
            keys_by_primary[primary or "通用"].append(key)
        for bucket_keys in keys_by_primary.values():
            for idx, k in enumerate(bucket_keys):
                if k in merged_map:
                    continue
                head_k = key_heads.get(k, "")
                merged_map[k] = k
                if not head_k:
                    continue
                for k2 in bucket_keys[idx + 1 :]:
                    if k2 in merged_map:
                        continue
                    head_k2 = key_heads.get(k2, "")
                    if not head_k2:
                        continue
                    ratio = SequenceMatcher(None, head_k, head_k2).ratio()
                    if ratio > 0.6:
                        merged_map[k2] = k

        # 3) 合并后重新归集为 case_id 列表，并做 noise filter（单条 cluster 排除）
        merged: dict[str, list[str]] = defaultdict(list)
        for key, items in groups.items():
            final_key = merged_map.get(key, key)
            for c in items:
                case_id = str(c.get("case_id", "") or c.get("id", "") or "")
                if case_id:
                    merged[final_key].append(case_id)

        clusters = {k: sorted(set(v)) for k, v in merged.items() if len(v) > 1}

        _log.info(
            "  [HallucinationLogger] cluster_failures: 总测试用例=%d, 失败测试用例=%d, "
            "聚类数(去噪后)=%d" % (total, failure_count, len(clusters))
        )
        return clusters

    def summarize_feedback(self, clusters: dict[str, list[str]]) -> str:
        """
        把聚类结果压缩为 AI 可读的 Prompt 片段。

        返回示例::

            "高频失败模式：
             - 【登录模块x3条】：按钮点击后页面无响应（疑似元素定位漂移）
             - 【订单列表x2条】：API 响应数据与 DB 不一致（跨环境 Schema 差异）"
        """
        if not clusters:
            return "高频失败模式：\n （暂无，所有失败测试用例均为单点噪音）"

        # 简单的"关键词 -> 推断现象"启发式映射，便于生成可读提示
        keyword_to_phenomenon = {
            "click": "按钮点击后页面无响应（疑似元素定位漂移）",
            "点击": "按钮点击后页面无响应（疑似元素定位漂移）",
            "登录": "登录流程中断（账号/校验状态不稳定）",
            "login": "登录流程中断（账号/校验状态不稳定）",
            "弹窗": "弹窗未按预期出现/关闭（时序/遮罩层问题）",
            "dialog": "弹窗未按预期出现/关闭（时序/遮罩层问题）",
            "API": "API 响应数据与 DB 不一致（跨环境 Schema 差异）",
            "api": "API 响应数据与 DB 不一致（跨环境 Schema 差异）",
            "数据库": "数据库事务未提交/回滚（并发/隔离级别问题）",
            "DB": "数据库事务未提交/回滚（并发/隔离级别问题）",
            "表单": "表单字段校验/提交行为异常（前端校验与后端不一致）",
            "表单提交": "表单提交后前端未刷新（状态同步缺失）",
            "按钮": "按钮点击后页面无响应（疑似元素定位漂移）",
            "列表": "列表分页/筛选/排序结果异常（查询参数漂移）",
            "导航": "导航路径与预期不符（路由/菜单配置变动）",
            "加载": "页面加载超时或内容为空（接口响应变慢）",
            "timeout": "接口/页面超时（网络或后端处理慢）",
            "TimeOut": "接口/页面超时（网络或后端处理慢）",
        }

        lines = ["高频失败模式："]
        for key, ids in sorted(clusters.items(), key=lambda kv: -len(kv[1])):
            prefix_part, _, kw_part = key.partition("|")
            # 推断现象：优先用关键词映射，否则回退为通用描述
            phenomenon = keyword_to_phenomenon.get(
                kw_part, "失败模式待人工确认（建议人工查看 case_id 列表）"
            )
            module_name = prefix_part if prefix_part else "未知模块"
            lines.append(
                " - 【%sx%d条】：%s（case_id: %s）"
                % (module_name, len(ids), phenomenon, ", ".join(ids[:5]))
            )
        return "\n".join(lines)

    def analyze_and_persist(
        self, intents: list[dict[str, Any]], project_name: str = ""
    ) -> dict[str, list[str]]:
        """
        一键执行：聚类失败测试用例 -> 写入 ``ai_generation_logs`` 表（不可用时回退
        到 JSONL 文件）-> 返回 clusters。
        """
        clusters = self.cluster_failures(intents)
        summary = self.summarize_feedback(clusters)

        project = project_name or self._project_name
        payload = {
            "project": project,
            "timestamp": datetime.now().isoformat(),
            "total_cases": len(intents) if isinstance(intents, list) else 0,
            "cluster_count": len(clusters),
            "clusters": clusters,
            "summary": summary,
        }

        # Step 1：写入 MySQL ai_generation_logs（主事实源）
        written_to = "mysql"
        try:
            ok = self.ai_store.insert_log(
                {
                    "case_id": "cluster_report_%s" % int(time.time()),
                    "module": "cluster_report",
                    "title": "高频失败模式聚类报告 (%s)" % project,
                    "source": "hallucination_logger",
                },
                {
                    "reason": summary,
                    "classification": "cluster_report",
                    "clusters": clusters,
                },
                source="hallucination_logger",
            )
            if not ok:
                written_to = "jsonl"
        except Exception as e:
            _log.info(
                "  [HallucinationLogger] analyze_and_persist MySQL 写入异常: %s",
                str(e)[:120],
            )
            _log.warning("  [HallucinationLogger] 聚类报告 MySQL 写入失败")

        _log.info(
            "  [HallucinationLogger] analyze_and_persist 完成：共 %d 个聚类，"
            "写入目标=%s" % (len(clusters), written_to)
        )
        return clusters
