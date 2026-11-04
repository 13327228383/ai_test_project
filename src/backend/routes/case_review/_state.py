# -*- coding: utf-8 -*-
"""
审核状态管理 — 测试用例审核闸门的状态查询
★ v2: 审核状态已迁移到 case_meta.review_status DB 实体字段，
     不再使用内存 _case_review_state。
"""

import logging

_log = logging.getLogger(__name__)

# ── 阈值常量 ──
# ★ 口径统一(2026-09-05)：低置信度阈值对齐前端审核页的「低(<0.5)」判定(<50)，
#   否则 info 栏「低分用例」用 60、列表柱/筛选用 50，同一数值两边判定里外不一致。
_CONFIDENCE_HIGH = 80
_CONFIDENCE_LOW = 50
_APPROVAL_RATE_THRESHOLD = 90.0


# ══════════════════════════════════════════════════════════
# ★ v2: DB-only 审核状态查询（替代原 _case_review_state 内存 dict）
# ══════════════════════════════════════════════════════════


def _get_case_review_state():
    """★ DB-only：从 case_meta 表汇总审核状态，供前端轮询。
    返回格式兼容旧调用方:
      {"status": "paused"|"approved", "pending": N, "total": N, "approval_rate": float}
    ★ 当 total==0 但流水线暂停在 Step3 时，返回 paused 状态（Step3 失败场景）
    ★ 修复：当 active_version_id=0 时，返回空数据（避免查询 legacy 数据）
    ★ 2026-07-13: 口径与 _case_review_gate_status() 对齐，使用 list_intents_with_meta()
       （含虚拟继承行），避免 UI 预检显示"23 条待审核"而后端闸门通过的口径不一致 bug
    """
    try:
        from src.backend.state import _get_paused_step
        from src.pipeline.utils.case_store import case_store
        from src.core.versioning import current_version_id
        from src.core.config_loader import config

        project = config.project_name or ""
        active_version_id = current_version_id(project)

        # ★ 修复：version_id=0 时返回空数据（避免查询 legacy 数据）
        if active_version_id == 0:
            _log.debug("[用例审核状态] active_version_id=0，跳过查询避免 legacy 数据")
            return {}

        # ★ 口径对齐：使用 list_intents_with_meta（含虚拟继承行），与 _case_review_gate_status 一致
        intents = case_store.list_intents_with_meta()
        total = len(intents)
        if total == 0:
            # ★ 边缘：无用例数据但流水线暂停在 Step3（建模失败/0条产出）
            # ★ A1 改造：按 project 查询暂停状态
            from src.backend.state import _current_project

            if _get_paused_step(_current_project()) == 3:
                return {
                    "status": "paused",
                    "pending": 0,
                    "total": 0,
                    "approval_rate": 0,
                    "has_failures": True,
                    "message": "测试用例建模失败（0条产出），请排查后重试或手动添加用例",
                }
            return {}
        pending = sum(
            1
            for m in intents
            if m.get("_review_status", "pending") not in ("approved", "modified")
        )
        approved = total - pending
        approval_rate = round(approved / max(total, 1) * 100, 1)
        if pending > 0:
            return {
                "status": "paused",
                "pending": pending,
                "total": total,
                "approval_rate": approval_rate,
            }
        else:
            return {
                "status": "approved",
                "approved": approved,
                "total": total,
                "approval_rate": approval_rate,
            }
    except Exception as e:
        _log.warning("[用例审核状态] DB 查询失败: %s", str(e)[:80], exc_info=True)
        return {}



def _read_intents_for_review():
    """★ MySQL-only：审核预览只读 test_cases+case_meta，Excel 仅作导出物。

    test_cases/case_id 是兼容存储名；展示层统一称为"测试用例资产"。
    """
    from src.pipeline.utils.case_store import case_store

    intents = case_store.list_intents_with_meta()
    return [intent for intent in intents if _is_intent_driven_intent(intent)], "mysql"


def _is_intent_driven_intent(intent: dict) -> bool:
    """v9: case_id + title 存在即视为意图驱动资产。"""
    return bool(
        str(intent.get("case_id") or "").strip()
        and str(intent.get("title") or "").strip()
    )
