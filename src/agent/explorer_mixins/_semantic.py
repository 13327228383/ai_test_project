# -*- coding: utf-8 -*-
"""P2-10 SemanticMixin — LLM语义匹配与断言（~250行）。

通用性：跨语言/跨框架/跨行业，不依赖关键词匹配。
降级：LLM不可用时返回None/UNKNOWN，调用方使用DOM兜底。
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from src.agent.element_utils import (
    get_page_snapshot_text,
    clean_operation,
)

_log = logging.getLogger(__name__)


class SemanticMixin:
    """LLM语义推理能力。

    提供：菜单语义匹配、菜单排序、元素语义匹配、操作结果语义断言。
    所有方法在LLM不可用时降级返回安全默认值。
    """

    async def _get_page_snapshot_text(self, page) -> str:
        """获取页面文本快照（薄封装，委托给 element_utils）。"""
        return await get_page_snapshot_text(page)

    async def _semantic_menu_match(
        self,
        operation: str,
        menu_items: list[str],
    ) -> Optional[str]:
        """LLM语义匹配候选元素文案（通用：跨语言/跨框架/跨行业）。

        当关键词匹配失败时，使用LLM语义理解找到最相关的候选元素。
        降级: LLM不可用时返回None，调用方继续使用关键词匹配兜底。

        ★ 根因修复(2026-09-24) F8 —— 提示词语境与调用点不符 + 判定路径不可观测：
          根因(1) 语境错配: 本方法被**两类调用点**复用 —— 侧边栏导航（menu 文本）
            与页面按钮定位（_action.py `_guided_click` 传入 button 文案）。但提示词
            写死"在侧边栏菜单中，找到与操作最相关的菜单项" → 对按钮场景，LLM 被
            引导去找"菜单项"，对"查询/搜索"这类**页面按钮**语义直接返回 NONE
            → 语义兜底失效（实测验收文档披露 `[SemanticMenu]` 日志零出现，
            TC-REQ-001-I02/I03 因"点击'查询'失败"判 ERROR）。
          根因(2) 不可观测: LLM 返回 NONE / 返回串匹配不上任何候选时**零日志**，
            排查时无法区分"未调用"/"调用了但没匹配"/"匹配失败"三种状态
            （本次验收文档正是因为零日志而只能猜测语义匹配未生效）。
          通用修复: ① 提示词去掉"侧边栏/菜单"业务语境，改为中性的"候选元素文案"，
            对导航与按钮两类调用点同时成立；② 每条出口路径都留日志
            （NONE / 无匹配 / 未返回），让"智能是否真的调用"可被证据链直接证实。
          通用性: 纯提示词语境与日志补全，零业务术语、零靶子硬编码。
        """
        import asyncio

        if not menu_items:
            _log.info("[SemanticMatch] 候选为空，跳过语义匹配: %r", str(operation)[:40])
            return None
        _clean_op = clean_operation(operation)
        try:
            from src.ai.router_module import router
            from src.ai.llm_circuit import (
                fast_path_remaining,
                note_llm_result,
                should_fast_path,
            )
            from src.core.pipeline_config import _read_config_value as _cfg_seed

            # 本地语义优先：确定性 argmax 判定直接生效（防云端幻觉错配），无决断才让位云 LLM。
            from src.ai.local_semantics import best_candidate as _bc

            _hit = _bc(_clean_op, [str(i) for i in menu_items])
            if _hit:
                _log.info(
                    "[SemanticMatch] 本地语义命中: %r → %r (score=%.2f)",
                    _clean_op[:40],
                    str(_hit[0])[:40],
                    _hit[1],
                )
                return _hit[0]
            if should_fast_path():
                _log.info(
                    "[SemanticMatch] LLM 熔断中(剩余%.0fs)且本地无决断，降级关键词兜底: %r",
                    fast_path_remaining(),
                    _clean_op[:40],
                )
                return None
            _items_str = "\n".join(f"- {item}" for item in menu_items[:25])
            _prompt = (
                f"从下列候选元素文案中，选出与「目标操作」语义最相关的一项。\n"
                f"目标操作: {_clean_op[:100]}\n"
                f"候选文案:\n{_items_str}\n\n"
                f"只返回最匹配的候选文案原文（精确复制，不要解释），都不匹配返回NONE。"
            )
            _result = await asyncio.to_thread(
                router.route,
                _prompt,
                system_prompt="你是Web测试助手，根据语义从候选文案中选出最相关的一项。只返回该文案原文或NONE。",
                task_type="general",
                expect_json=False,
                # ★ F2 全局修复 [未复核-需 GLM/DeepSeek 把关]：探索路径确定性化
                temperature=0.0,
                seed=(None if (_seed_v := _cfg_seed("ai_stability_seed", 42)) is None else int(_seed_v)),
                max_tokens=200,  # ★ 修复: 60→200，避免截断导致假匹配
                # ★ 辅助调用预算：语义匹配有"降级关键词兜底"，LLM 不可用时应快速失败
                #   而非等满 120s×3 次重试（实测 LLM 黑洞期间直接耗尽用例 300s 预算）
                cloud_timeout=20,
                cloud_retry_max=0,
            )
            note_llm_result(bool(_result.get("success")))
            if not _result.get("success"):
                _log.info(
                    "[SemanticMatch] LLM 调用未成功(降级关键词兜底): op=%r err=%s",
                    _clean_op[:40],
                    str(_result.get("error", ""))[:80],
                )
                return None
            _matched = str(_result.get("data") or "").strip().strip('"').strip("'")
            if not _matched or _matched.upper() == "NONE":
                _log.info(
                    "[SemanticMatch] LLM 判定无匹配(NONE): op=%r 候选=%d项",
                    _clean_op[:40],
                    len(menu_items),
                )
                return None
            for _item in menu_items:
                if _item == _matched:
                    _log.info(
                        "[SemanticMatch] LLM精确匹配: %r → %r",
                        _clean_op[:40],
                        _matched[:40],
                    )
                    return _item
            for _item in menu_items:
                # ★ 修复: 模糊匹配要求至少3字符重叠（原2字符太宽松，导致"数据"匹配"数据看板"）
                if len(_matched) >= 3 and _matched[:3] in _item:
                    _log.info(
                        "[SemanticMatch] LLM模糊匹配: %r → %r (via %r)",
                        _clean_op[:40],
                        _item[:40],
                        _matched[:40],
                    )
                    return _item
                if len(_item) >= 3 and _item[:3] in _matched:
                    _log.info(
                        "[SemanticMatch] LLM模糊匹配: %r → %r (via %r)",
                        _clean_op[:40],
                        _item[:40],
                        _matched[:40],
                    )
                    return _item
            _log.info(
                "[SemanticMatch] LLM 返回 %r 但不在候选列表中(降级关键词兜底): op=%r",
                _matched[:40],
                _clean_op[:40],
            )
            return None
        except Exception as _e:
            note_llm_result(False)
            _log.warning("[SemanticMatch] LLM匹配异常(降级关键词兜底): %s", str(_e)[:120])
            return None

    async def _semantic_menu_rank(
        self,
        operation: str,
        menu_items: list[str],
    ) -> list[str]:
        """LLM语义排序全部菜单项。

        与 _semantic_menu_match 不同：返回排序后的完整列表（而非单个最佳匹配）。
        降级: LLM不可用时返回原列表（保持原顺序）。
        """
        import asyncio

        if not menu_items or len(menu_items) <= 1:
            return list(menu_items)
        _clean_op = clean_operation(operation)
        try:
            from src.ai.router_module import router
            from src.ai.llm_circuit import (
                fast_path_remaining,
                note_llm_result,
                should_fast_path,
            )
            from src.core.pipeline_config import _read_config_value as _cfg_seed
            # LLM 熔断时本地语义排序接管（rank 无降级语义会硬付云超时）
            from src.ai.local_semantics import rank_candidates as _rank_local

            if should_fast_path():
                _log.info(
                    "[SemanticRank] LLM 熔断中(剩余%.0fs)，本地语义排序: %d项",
                    fast_path_remaining(),
                    len(menu_items),
                )
                return _rank_local(_clean_op, [str(i) for i in menu_items])

            _items_str = "\n".join(
                f"{i}. {item}" for i, item in enumerate(menu_items[:25])
            )
            _prompt = (
                f"将侧边栏菜单项按与操作的关联度排序（从最相关到最不相关）。\n"
                f"操作: {_clean_op[:100]}\n"
                f"菜单项:\n{_items_str}\n\n"
                f'返回JSON数组: ["最相关", "次相关", ..., "最不相关"]\n'
                f"只返回JSON数组，不要其他文本。"
            )
            _result = await asyncio.to_thread(
                router.route,
                _prompt,
                system_prompt="你是Web测试助手，按语义关联度排序菜单项。只返回JSON数组。",
                task_type="general",
                expect_json=True,
                # ★ F2 全局修复 [未复核-需 GLM/DeepSeek 把关]：探索路径确定性化
                temperature=0.0,
                seed=(None if (_seed_v := _cfg_seed("ai_stability_seed", 42)) is None else int(_seed_v)),
                max_tokens=800,
                # 辅助调用预算：排序有"降级保持原序"兜底，LLM 不可用时快速失败
                cloud_timeout=20,
                cloud_retry_max=0,
            )
            note_llm_result(bool(_result.get("success")))
            if _result.get("success") and _result.get("data"):
                _data = _result["data"]
                if isinstance(_data, str):
                    import json as _json

                    try:
                        _data = _json.loads(_data)
                    except Exception:
                        return list(menu_items)
                if isinstance(_data, list) and len(_data) >= 1:
                    _ranked = []
                    _remaining = set(menu_items)
                    for _item in _data:
                        _item = str(_item).strip().strip('"').strip("'")
                        if _item in _remaining:
                            _ranked.append(_item)
                            _remaining.discard(_item)
                    _ranked.extend(_remaining)
                    _log.info("[SemanticRank] LLM排序完成: %d项", len(_ranked))
                    return _ranked
            return list(menu_items)
        except Exception as _e:
            note_llm_result(False)
            _log.warning("[SemanticRank] LLM排序失败: %s", str(_e)[:80])
            return list(menu_items)

    async def _semantic_element_match(
        self,
        operation: str,
        elements: list[dict],
    ) -> dict[str, Any] | None:
        """LLM语义元素匹配（替代硬编码关键词匹配）。

        当关键词匹配失败时，使用LLM语义理解找到最相关的页面元素。
        降级: LLM不可用时返回None，调用方继续使用关键词匹配兜底。
        返回格式: 与 match_keywords_to_elements 一致 {element, score, hit_count}
        """
        import asyncio, json as _json

        if not elements:
            return None
        _clean_op = clean_operation(operation)
        from src.ai.llm_circuit import should_fast_path as _sfp
        from src.ai.local_semantics import match_element as _match_local

        # 本地语义优先：确定性 argmax 判定直接生效（防云端幻觉错配，实测将
        # '资产名称' 错配成 'category'），无决断才让位云 LLM / 关键词兜底。
        _local = _match_local(_clean_op, elements)
        if _local and _local.get("element"):
            _log.info(
                "[SemanticElement] 本地语义命中: %r → %r (score=%d)",
                _clean_op[:40],
                str(_local["element"].get("text") or "")[:40],
                _local.get("score"),
            )
            return _local
        if _sfp():
            return None
        _el_list = []
        for _el in elements[:30]:
            _info = {
                "text": (_el.get("text") or "")[:60],
                "tag": _el.get("tag", ""),
                "role": _el.get("role", ""),
                "ariaLabel": (_el.get("ariaLabel") or "")[:40],
                "visible": _el.get("visible", True),
            }
            _el_list.append(_info)
        try:
            from src.ai.router_module import router
            from src.ai.llm_circuit import (
                fast_path_remaining,
                note_llm_result,
                should_fast_path,
            )
            from src.core.pipeline_config import _read_config_value as _cfg_seed

            _el_json = _json.dumps(_el_list, ensure_ascii=False)
            _prompt = (
                f"在页面元素列表中，找到与操作语义最匹配的元素。\n"
                f"操作: {_clean_op[:120]}\n"
                f"元素列表(JSON): {_el_json[:3000]}\n\n"
                f'返回JSON: {{"index": 匹配元素索引, "confidence": 0.0-1.0, "reason": "匹配原因"}}\n'
                f'都不匹配返回: {{"index": -1, "confidence": 0.0, "reason": "无匹配"}}'
            )
            _result = await asyncio.to_thread(
                router.route,
                _prompt,
                system_prompt="你是Web测试助手，根据语义匹配页面元素。只返回JSON。",
                task_type="general",
                expect_json=True,
                # ★ F2 全局修复 [未复核-需 GLM/DeepSeek 把关]：探索路径确定性化
                temperature=0.0,
                seed=(None if (_seed_v := _cfg_seed("ai_stability_seed", 42)) is None else int(_seed_v)),
                max_tokens=200,
                # 辅助调用预算：元素匹配有"降级关键词兜底"，LLM 不可用时快速失败
                cloud_timeout=20,
                cloud_retry_max=0,
            )
            note_llm_result(bool(_result.get("success")))
            if _result.get("success") and _result.get("data"):
                _data = _result["data"]
                if isinstance(_data, str):
                    _data = _json.loads(_data)
                _idx = _data.get("index", -1)
                _conf = _data.get("confidence", 0.0)
                if _idx >= 0 and _idx < len(elements) and _conf >= 0.3:
                    _matched = elements[_idx]
                    _log.info(
                        "[SemanticElement] LLM匹配: %r → %r (idx=%d, conf=%.2f)",
                        _clean_op[:40],
                        (_matched.get("text") or "")[:40],
                        _idx,
                        _conf,
                    )
                    return {
                        "element": _matched,
                        "score": int(_conf * 10),
                        "hit_count": 1,
                    }
            return None
        except Exception as _e:
            note_llm_result(False)
            _log.warning("[SemanticElement] LLM匹配失败: %s", str(_e)[:80])
            return None

    async def _semantic_assertion(
        self,
        operation: str,
        expected_feedback: str,
        page_snapshot_text: str,
        actions_taken: list[dict],
    ) -> dict[str, Any]:
        """LLM语义评估操作结果（通用：跨语言/跨行业）。

        替代预生成的通用checks（element_exists+no_error），
        用LLM语义理解判断操作是否真的成功。
        降级: LLM不可用时返回UNKNOWN，调用方使用DOM兜底判断。
        """
        import asyncio

        _clean_op = clean_operation(operation)
        _actions_str = "\n".join(
            f"- {a.get('type','?')}: {a.get('target_hint','?')}"
            for a in (actions_taken or [])[-5:]
        )
        _snapshot = (page_snapshot_text or "")[:2000]
        _expected = (expected_feedback or "")[:200]
        if not _snapshot and not _actions_str:
            return {"verdict": "UNKNOWN", "confidence": 0.0, "evidence": ""}
        try:
            from src.ai.router_module import router
            from src.ai.llm_circuit import (
                fast_path_remaining,
                note_llm_result,
                should_fast_path,
            )
            from src.core.pipeline_config import _read_config_value as _cfg_seed

            # 本地语义优先：证据充分才判定（UNKNOWN=证据不足），无决断才让位云 LLM。
            from src.ai.local_semantics import assert_result as _assert_local

            _local = _assert_local(_clean_op, _expected, _snapshot, actions_taken)
            if _local and _local.get("verdict") != "UNKNOWN":
                _log.info(
                    "[SemanticAssertion] 本地语义判定: %s (%.2f)",
                    _local.get("verdict"),
                    _local.get("confidence", 0.0),
                )
                return _local
            if should_fast_path():
                _log.info(
                    "[SemanticAssertion] LLM 熔断中(剩余%.0fs)且本地证据不足，返回 UNKNOWN",
                    fast_path_remaining(),
                )
                return {
                    "verdict": "UNKNOWN",
                    "confidence": 0.0,
                    "evidence": "本地判定证据不足",
                }
            _prompt = (
                f"评估Web操作结果。\n"
                f"操作: {_clean_op[:150]}\n"
                f"期望: {_expected}\n"
                f"动作: {_actions_str[:300]}\n"
                f"页面状态: {_snapshot}\n\n"
                f'返回JSON: {{"verdict":"PASS"|"FAIL","confidence":0.0-1.0,"evidence":"事实"}}'
            )
            _result = await asyncio.to_thread(
                router.route,
                _prompt,
                system_prompt="你是Web测试评估专家。只返回JSON，evidence基于页面状态事实。",
                task_type="verify",
                expect_json=True,
                # ★ F2 全局修复 [未复核-需 GLM/DeepSeek 把关]：探索路径确定性化
                temperature=0.0,
                seed=(None if (_seed_v := _cfg_seed("ai_stability_seed", 42)) is None else int(_seed_v)),
                max_tokens=2000,  # ★ Fix: 500→2000 防JSON输出截断(finish_reason=length)
                # 辅助调用预算：判定失败返回 UNKNOWN 由 DOM 兜底，LLM 不可用时快速失败
                cloud_timeout=20,
                cloud_retry_max=0,
            )
            note_llm_result(bool(_result.get("success")))
            if _result.get("success") and _result.get("data"):
                _data = _result["data"]
                if isinstance(_data, str):
                    # ★ 根因修复(2026-09-24): 不再就地手写 markdown/截断修补，
                    #   统一走 canonical 单一事实源 src.ai.json_parser.parse_json_response
                    #   （markdown 剥离 + 尾逗号/注释规范化 + 截断补全 + 内嵌 JSON 恢复）。
                    #   根因: 手写版缺"截断补全"，max_tokens 截断时 json.loads 直接抛
                    #   "Unterminated string" → 语义判定不可用 → 假 FAIL。
                    #   通用性: 引擎内所有 LLM JSON 输出共用同一修复器。
                    from src.ai.json_parser import parse_json_response as _pjr

                    _pr = _pjr(_data)
                    if not _pr.get("success"):
                        _log.warning(
                            "[SemanticAssertion] JSON解析失败: %s",
                            str(_pr.get("error", ""))[:120],
                        )
                        return {
                            "verdict": "UNKNOWN",
                            "confidence": 0.0,
                            "evidence": "LLM输出JSON格式错误: %s" % str(_data)[:80],
                        }
                    _data = _pr.get("data")
                if not isinstance(_data, dict):
                    return {
                        "verdict": "UNKNOWN",
                        "confidence": 0.0,
                        "evidence": "LLM返回非对象JSON: %s" % str(_data)[:80],
                    }
                return {
                    "verdict": str(_data.get("verdict", "FAIL")),
                    "confidence": float(_data.get("confidence", 0.0)),
                    "evidence": str(_data.get("evidence", "")),
                }
            return {
                "verdict": "UNKNOWN",
                "confidence": 0.0,
                "evidence": f"LLM调用失败: {_result.get('error','')[:80]}",
            }
        except Exception as _e:
            note_llm_result(False)
            _log.warning("[SemanticAssertion] LLM评估失败: %s", str(_e)[:80])
            return {
                "verdict": "UNKNOWN",
                "confidence": 0.0,
                "evidence": f"LLM异常: {str(_e)[:80]}",
            }
