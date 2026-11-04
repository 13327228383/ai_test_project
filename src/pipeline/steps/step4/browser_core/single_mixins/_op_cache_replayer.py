# -*- coding: utf-8 -*-
"""P2-10 AgentExplorer replay 接入点 — op_cache_v2 命中重放。

从 _base.py 拆出（§4.3 单文件≤800行 + 职责分离）。

三拍设计第二拍（replay）：
  - 查询 op_cache_v2 命中 → 用 cached action_sequence 替换 step["actions"]
  - 后续仍走 _execute_structured_actions（checks 断言不变，不假 PASS）
  - 未命中/fingerprint 不匹配 → 不替换，走原路径（降级）

职责边界（§12.1）：
- 仅做执行优化（重放动作），不做决策（actions 仍由 Step3 生成）
- checks 断言仍由 Step4 执行，replay 不跳过验证
- 失败不阻断主流程（降级走原路径）

通用性（§12.4）：
- intent_hash 基于 case_id+actions，不绑定业务术语
- page_fingerprint 基于 URL 哈希，跨系统/行业/页面通用
"""
from __future__ import annotations
from src.core.guarded import guard

import logging
from typing import Any

_log = logging.getLogger(__name__)


def replay_attempt(
    current_case_id: str,
    case_id_fallback: str,
    case_meta: dict[str, Any],
    intent: dict[str, Any],
    step: dict[str, Any],
    page_url: str,
    project_name: str,
) -> tuple[bool, str]:
    """查询 op_cache_v2 命中时替换 step["actions"]（重放决策）。

    参数:
        current_case_id: self._current_case_id（由 _base.py 在意图入口设置）
        case_id_fallback: 兜底 case_id
        case_meta: 用例元数据（含 version_id）
        intent: 意图对象（兜底 version_id）
        step: 当前步骤（含 actions 列表，命中时会被替换为 cached 副本）
        page_url: 当前页面 URL（engine._page.url）
        project_name: 项目名（config.project_name）

    返回:
        (hit, reason)
        - hit=True: 命中并已替换 step["actions"]，调用方走原执行路径即可
        - hit=False: 未命中/fingerprint 不匹配/异常，调用方走原路径
        降级：任何异常返回 (False, "error")，不阻断
    """
    try:
        from src.agent.explorer import get_explorer
        from src.pipeline.steps.step4.op_cache.v2_store import (
            compute_intent_hash,
            compute_page_fingerprint,
        )

        _rec_case_id = (current_case_id or case_id_fallback or "").strip()
        _rec_actions = step.get("actions", []) if step else []
        if not _rec_actions or not _rec_case_id:
            return False, "empty actions or case_id"
        _rec_project = (project_name or "").strip()
        _rec_vid = int(
            case_meta.get("version_id", 0) or intent.get("version_id", 0) or 0
        )
        _rec_hash = compute_intent_hash(_rec_case_id, _rec_actions)
        _explorer = get_explorer()
        _cached = _explorer.replay(_rec_project, _rec_vid, _rec_hash)
        if not _cached or not _cached.get("hit"):
            return False, "cache miss"
        _cached_seq = _cached.get("action_sequence") or []
        if not _cached_seq:
            return False, "empty cached sequence"
        # ★ fingerprint 校验：当前页面指纹 vs 缓存指纹
        #   不匹配 → repair: 删除失效缓存 + 降级走原路径
        #   根因: 旧缓存对应旧页面，record 不会覆盖（AI 路径不触发 record），需显式清理
        #   通用性: fingerprint 不匹配即删除，跨系统/行业/页面通用
        _current_fp = compute_page_fingerprint(page_url or "")
        _cached_fp = _cached.get("page_fingerprint", "")
        if _cached_fp and _current_fp != _cached_fp:
            _log.info(
                "[P2-10] replay fingerprint 不匹配: current=%s cached=%s (repair 删除失效缓存)",
                _current_fp[:12],
                _cached_fp[:12],
            )
            # ★ P2-10 repair（第三拍）: 清理失效缓存
            with guard("replay_attempt: from src.pipeline.steps.step4.op_cac", log=_log):
                from src.pipeline.steps.step4.op_cache.v2_store import get_v2_store

                get_v2_store().delete(_rec_project, _rec_vid, _rec_hash)
                _log.info(
                    "[P2-10] ✓ repair: 已删除失效缓存 hash=%s (页面已变化)",
                    _rec_hash[:12],
                )
            return False, "fingerprint mismatch"
        # ★ 命中 → 用 cached 副本替换 step["actions"]（不破坏原数据）
        #   后续 _execute_structured_actions 会执行这批 actions + checks 断言
        step["actions"] = [dict(a) for a in _cached_seq]
        _log.info(
            "[P2-10] ✓ replay 命中: case=%s hash=%s actions=%d (hit_count=%d)",
            _rec_case_id[:20],
            _rec_hash[:12],
            len(_cached_seq),
            _cached.get("hit_count", 0),
        )
        return True, "hit"
    except Exception as _re_e:
        _log.debug("[P2-10] replay 失败(非阻断): %s", str(_re_e)[:100])
        return False, "error"
