# -*- coding: utf-8 -*-
"""
追溯链路引擎 — AI 驱动的链路构建
================================
调用 AI 批量构建追溯链路；分批处理，每批 10 条需求。
批次 AI 调用并发执行（复用现有 thread-local 上下文传播），
避免顺序循环导致 Step2 后置阶段超时。
AI 失败时回退到启发式补充。

[已复核-DeepSeek-V3 2026-08-16]
B3.2 JSON 解析收敛：``_parse_trace_json`` 已改为转发
``src.ai.json_parser.parse_json_response``（唯一真源），
仅在本地做形状归一化（``list[dict]``，单 dict 需含 ``req_id``），
req_id 业务语义与返回契约均未变。
"""

# 注：原 ``import json`` 已随 B3.2 收敛删除 —— ``_parse_trace_json`` 已转发到
# ``src.ai.json_parser``，本模块不再直接使用 json。
import logging
from src.core.guarded import guard
from concurrent.futures import ThreadPoolExecutor, as_completed

from .models import TraceLink
from .heuristic import HeuristicBuilder

_log = logging.getLogger("traceability_engine")


class AIBuilder:
    """AI 驱动的追溯链路构建器。"""

    def __init__(self):
        self._heuristic = HeuristicBuilder()

    def build_links(
        self,
        requirements: list[dict],
        api_list: list[dict],
        table_list: list[dict],
        page_list: list[dict],
    ) -> dict[str, TraceLink]:
        """调用 AI 批量构建追溯链路。分批处理，每批 10 条需求。

        各批次 AI 调用并发执行（保留原有 thread-local session/user/run_id 传播），
        以避免顺序循环放大云端 API 延迟、拖垮 Step2 时间预算。
        """
        from src.ai.ai_client import ai_brain

        links: dict[str, TraceLink] = {}
        batch_size = 10
        batches = [
            requirements[i : i + batch_size]
            for i in range(0, len(requirements), batch_size)
        ]
        if not batches:
            return links

        # ── 捕获父线程上下文（user/session/run_id），在工作线程中还原，防止泄漏 ──
        try:
            from src.core.config_session import (
                get_session_key,
                get_current_user,
                set_session_key,
                set_current_user,
            )

            _parent_session_key = get_session_key()
            _parent_user = get_current_user()
        except Exception:
            get_session_key = get_current_user = set_session_key = set_current_user = None  # type: ignore
            _parent_session_key = _parent_user = None
        _parent_run_id = None
        try:
            from src.backend.state import _thread_run_id

            _parent_run_id = getattr(_thread_run_id, "value", None)
        except Exception:
            _parent_run_id = None

        # ── 并发度（默认 10，复用 step2 配置）──
        try:
            from src.core.pipeline_config import pipeline_config as _pc

            _concurrency = int(getattr(_pc, "step2_ai_concurrency", 10))
        except Exception:
            _concurrency = 10
        _concurrency = max(1, min(_concurrency, len(batches)))

        def _restore_thread_ctx() -> None:
            with guard("_restore_thread_ctx: if set_current_user is not None: set", log=_log):
                if set_current_user is not None:
                    set_current_user(_parent_user or "")
                if (
                    set_session_key is not None
                    and _parent_session_key
                    and _parent_session_key != "local"
                ):
                    set_session_key(_parent_session_key)
                if _parent_run_id is not None:
                    from src.backend.state import _thread_run_id as _rid

                    _rid.value = _parent_run_id

        def _process_batch(batch: list[dict]) -> dict[str, TraceLink]:
            _restore_thread_ctx()
            batch_links: dict[str, TraceLink] = {}

            system_prompt = (
                "你是系统追溯分析专家。对每条需求，从给定的数据源中找出其关联的：\n"
                "1. API 接口（匹配功能描述和接口路径）\n"
                "2. 数据库表（匹配业务实体和表名）\n"
                "3. 前端页面（匹配功能模块和页面名称）\n\n"
                "## 规则\n"
                "- 只列出确实相关的，不确定的不要强行匹配\n"
                "- API 匹配：看接口路径/功能描述是否与需求功能对应\n"
                "- DB 匹配：看表名/字段名是否对应需求中的业务实体\n"
                "- 页面匹配：看页面名称/导航路径是否对应需求涉及的功能\n"
                "- confidence: high(明确匹配) / medium(推断匹配) / low(模糊匹配)\n\n"
                "## 输出格式（严格 JSON 数组，每个元素对应一条需求）\n"
                "[{\n"
                '  "req_id": "REQ-001",\n'
                '  "api_endpoints": [{"method": "POST", "path": "/api/brand/create", "summary": "创建品牌"}],\n'
                '  "db_tables": {"BRAND_INFO": ["BRAND_CODE", "BRAND_NAME", "STATUS"]},\n'
                '  "figma_pages": ["品牌管理-品牌列表"],\n'
                '  "related_reqs": ["REQ-005"],\n'
                '  "confidence": "high"\n'
                "}]\n"
            )

            # 构建数据源摘要
            api_summary = "\n".join(
                f"- {a.get('method','?')} {a.get('path','?')} ({a.get('summary','')})"
                for a in api_list[:50]
            )
            table_summary = "\n".join(
                f"- {t.get('name','?')}: {', '.join(t.get('columns',[])[:8])}"
                for t in table_list[:30]
            )
            page_summary = "\n".join(
                f"- {p.get('module','')} > {p.get('name','')}" for p in page_list[:40]
            )

            req_text = "\n".join(
                f"{r.get('req_id','?')} [{r.get('module','')}] {r.get('description','')[:120]}"
                for r in batch
            )

            user_message = (
                "## 需求列表\n%s\n\n"
                "## API 接口清单\n%s\n\n"
                "## 数据库表清单\n%s\n\n"
                "## 页面清单\n%s\n\n"
                "请为每条需求构建追溯链路。只输出 JSON 数组。"
            ) % (req_text, api_summary, table_summary, page_summary)

            # 构建 req_id → feature_id 映射（从 requirement 透传）
            req_feature_map = {
                str(r.get("req_id", "")): int(r.get("feature_id") or 0) for r in batch
            }
            with guard("_process_batch: result = ai_brain._call_api( system_", log=_log):
                result = ai_brain._call_api(
                    system_prompt=system_prompt,
                    user_message=user_message,
                    temperature=0.1,
                    max_tokens=4096,
                    caller_name="追溯链路构建",
                    task_type="requirement_analysis",
                    force_cloud=True,
                )
                if result.get("success"):
                    parsed = self._parse_trace_json(result.get("data", ""))
                    for item in parsed:
                        rid = item.get("req_id", "")
                        if rid:
                            batch_links[rid] = TraceLink(
                                req_id=rid,
                                module=item.get("module", ""),
                                api_endpoints=item.get("api_endpoints", []),
                                db_tables=item.get("db_tables", {}),
                                figma_pages=item.get("figma_pages", []),
                                related_reqs=item.get("related_reqs", []),
                                confidence=item.get("confidence", "medium"),
                                feature_id=req_feature_map.get(str(rid), 0),
                            )

            # 为未匹配到的需求做启发式补充
            for req in batch:
                rid = req.get("req_id", "")
                if rid and rid not in batch_links:
                    batch_links[rid] = self._heuristic._heuristic_single_link(
                        req, table_list, page_list, api_list
                    )
            return batch_links

        _log.info(
            "[TraceabilityEngine] 并发 AI 追溯链路构建: %d 需求 (batch=%d, 并发=%d)",
            len(requirements),
            len(batches),
            _concurrency,
        )
        with ThreadPoolExecutor(max_workers=_concurrency) as executor:
            futures = [executor.submit(_process_batch, b) for b in batches]
            for fut in as_completed(futures):
                with guard("build_links: links.update(fut.result())", log=_log):
                    links.update(fut.result())

        return links

    @staticmethod
    def _parse_trace_json(raw: str) -> list[dict]:
        """解析 AI 返回的追溯链路 JSON（**转发到唯一真源** + 本地形状归一）。

        整改 B3.2：原本地的「3 次尝试」实现已删除，JSON 提取改调
        ``src.ai.json_parser.parse_json_response``；
        **"顶层 dict 必须含 req_id 才算一条链路" 的业务语义保留在本地**
        （属追溯业务规则，不应下沉到通用解析器）。

        参数：
            raw: LLM 原始输出文本，允许为空 / 非字符串。

        返回：
            ``list[dict]``；解析失败或语义不符一律返回 ``[]``（不 raise）。
        """
        if not raw or not isinstance(raw, str):
            return []
        from src.ai.json_parser import parse_json_response as _canonical

        result = _canonical(raw)
        if not result.get("success"):
            _log.debug(
                "[TraceabilityEngine] 追溯 JSON 解析失败: %s",
                str(result.get("error"))[:120],
            )
            return []
        data = result.get("data")
        # ── 形状归一：保持整改前的 list[dict] 契约 ──
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        # 业务语义（保留）：单个 dict 只有带 req_id 才视为一条有效链路
        if isinstance(data, dict) and "req_id" in data:
            return [data]
        return []
