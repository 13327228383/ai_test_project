# -*- coding: utf-8 -*-
"""P2-10 AgentExplorer record 接入点 — 成功动作序列写入 op_cache_v2。

从 _base.py 拆出（§4.3 单文件≤800行）。

职责边界（§12.1）：
- 仅做数据收集（record），不做决策。
- 在结构化动作执行成功后调用，为未来 replay 积累数据。
- 失败不阻断主流程（只写缓存，不影响执行结果）。

通用性（§12.4）：
- intent_hash 基于 case_id + actions 摘要，不绑定业务术语。
- 适用于所有走 _execute_one_intent 的用例（跨系统/跨行业/跨页面）。
"""
from __future__ import annotations

import logging
from typing import Any

_log = logging.getLogger(__name__)


def record_success_actions(
    current_case_id: str,
    case_id_fallback: str,
    case_meta: dict[str, Any],
    intent: dict[str, Any],
    step: dict[str, Any],
    page_url: str,
    project_name: str,
) -> bool:
    """结构化动作执行成功后，将动作序列记录到 op_cache_v2。

    参数:
        current_case_id: self._current_case_id（由 _base.py 在意图入口设置）
        case_id_fallback: 兜底 case_id
        case_meta: 用例元数据（含 version_id）
        intent: 意图对象（兜底 version_id）
        step: 当前步骤（含 actions 列表）
        page_url: 当前页面 URL（engine._page.url）
        project_name: 项目名（config.project_name）

    返回:
        True=写入成功, False=跳过/失败（非阻断，仅日志）
    """
    try:
        from src.pipeline.steps.step4.op_cache.v2_store import (
            get_v2_store,
            compute_intent_hash,
            compute_page_fingerprint,
        )

        _rec_actions = step.get("actions", []) if step else []
        _rec_case_id = (current_case_id or case_id_fallback or "").strip()
        if not _rec_actions or not _rec_case_id:
            return False
        _rec_project = (project_name or "").strip()
        _rec_vid = int(
            case_meta.get("version_id", 0) or intent.get("version_id", 0) or 0
        )
        _rec_hash = compute_intent_hash(_rec_case_id, _rec_actions)
        _rec_fp = compute_page_fingerprint(page_url or "")
        if get_v2_store().set(
            _rec_project,
            _rec_vid,
            _rec_hash,
            _rec_fp,
            _rec_actions,
        ):
            _log.info(
                "[P2-10] ✓ record: case=%s hash=%s actions=%d",
                _rec_case_id[:20],
                _rec_hash[:12],
                len(_rec_actions),
            )
            return True
        return False
    except Exception as _rec_e:
        _log.debug("[P2-10] record 失败(非阻断): %s", str(_rec_e)[:100])
        return False
