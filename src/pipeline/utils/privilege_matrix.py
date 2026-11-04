# -*- coding: utf-8 -*-
"""越权矩阵生成器（W4-C，P1）—— 纯函数，零 DB 写，零依赖。

输入身份池 {identity_key: {username, role, permissions:[...], data_scope}} →
输出越权用例骨架：
  - horizontal（横向越权）：actor 访问 victim 的数据（data_scope 不同）
  - vertical（纵向越权）：  actor 角色层级低于 required_role 却执行 admin 操作
  - idor（不安全直接对象引用）：不同身份访问同一资源 ID（应被拒绝）

expected_failure 语义：越权访问应被拒绝，拒绝=通过（expected_outcome 含"应被拒绝"）。

★ 2026-08-12 增强：
  - 新增 IDOR 检测维度（build_privilege_matrix 输出 idor 列表）
  - 新增 append_privilege_cases()：接入 step3 用例生成流程
    （PRIVILEGE_MATRIX_ENABLED 开启时自动追加越权测试用例）

接入建议：提供身份池骨架生成工具（读身份池 → 打印骨架）；
Step3 接入通过 append_privilege_cases() 实现（当 PRIVILEGE_MATRIX_ENABLED 开启）。
"""
from __future__ import annotations

from typing import Any

from src.core.pipeline_config import _read_config_value as _cfg


def role_hierarchy() -> list[str]:
    """默认 ["admin","manager","member"]（PRIVILEGE_ROLE_HIERARCHY 可配，逗号分隔）。"""
    raw = str(
        _cfg("PRIVILEGE_ROLE_HIERARCHY", "admin,manager,member")
        or "admin,manager,member"
    )
    roles = [r.strip().lower() for r in raw.split(",") if r.strip()]
    return roles or ["admin", "manager", "member"]


def _role_rank(role: str, hierarchy: list[str]) -> int:
    """角色层级排名（越小越高级）；未知角色视为最低层级。"""
    try:
        return hierarchy.index(str(role or "").strip().lower())
    except ValueError:
        return len(hierarchy)


def _enabled() -> bool:
    """PRIVILEGE_MATRIX_ENABLED 门控（默认 False；仅 CLI/单测显式开启）。

    ★ FIX-4 (2026-08-06 QA): 开关表一致性——build_privilege_matrix 内生效；
      关闭时返回空矩阵（零行为变更）。纯函数仍可被 to_case_skeletons 直接消费。
    """
    return bool(_cfg("PRIVILEGE_MATRIX_ENABLED", False))


def build_privilege_matrix(pool: dict) -> dict[str, Any]:
    """身份池 → 越权组合（纯函数）。

    参数:
        pool: {identity_key: {username, role, permissions:[...], data_scope,
                              resource_ids:[...]}}。
              resource_ids 为可选字段，用于 IDOR 检测（不同身份访问同一资源 ID）。

    返回:
        {horizontal: [...], vertical: [...], idor: [...]}
        idor: [{actor, actor_role, resource_owner, resource_id, action,
                expected_failure:True}]
    """
    pool = pool or {}
    if not _enabled():
        return {"horizontal": [], "vertical": [], "idor": []}
    hierarchy = role_hierarchy()
    horizontal: list[dict] = []
    vertical: list[dict] = []
    idor: list[dict] = []
    actors = [(str(k), dict(v or {})) for k, v in pool.items()]
    for actor_key, actor in actors:
        actor_role = str(actor.get("role", "") or "").strip().lower()
        actor_scope = str(actor.get("data_scope", "") or "").strip()
        # ★ FIX-1 (2026-08-06 QA): data_scope='all'（或最高权限角色）语义——可访问全部数据，
        #   不视为横向越权（否则 admin 访问 member 数据被误标"应拒绝"，实际可访问 → 用例误失败）。
        #   仅 actor 侧判定；victim 为 all 仍可能是越权（低权限 actor 访问全局数据应被拒）。
        _actor_top_priv = (actor_scope == "all") or (
            bool(hierarchy) and actor_role == hierarchy[0]
        )
        if not _actor_top_priv:
            # 横向越权：与其他身份数据域不同 → 访问其数据应被拒绝
            for victim_key, victim in actors:
                if victim_key == actor_key:
                    continue
                victim_scope = str(victim.get("data_scope", "") or "").strip()
                victim_role = str(victim.get("role", "") or "").strip().lower()
                if actor_scope and victim_scope and actor_scope != victim_scope:
                    horizontal.append(
                        {
                            "actor": actor_key,
                            "actor_role": actor_role,
                            "victim": victim_key,
                            "victim_role": victim_role,
                            "action": "访问他人数据",
                            "data": "用户 %s 的数据" % victim_key,
                            "expected_failure": True,
                        }
                    )
        # 纵向越权：actor 角色层级低于（数值更大=权限更低）某操作所需角色 → 执行应被拒绝
        for req_role in hierarchy:
            if _role_rank(actor_role, hierarchy) > _role_rank(req_role, hierarchy):
                vertical.append(
                    {
                        "actor": actor_key,
                        "actor_role": actor_role,
                        "required_role": req_role,
                        "action": "执行 %s 级操作" % req_role,
                        "expected_failure": True,
                    }
                )

    # ★ 2026-08-12 新增：IDOR 检测维度
    #   不同身份访问同一资源 ID → 低权限身份访问他人资源应被拒绝
    _build_idor_cases(actors, hierarchy, idor)

    return {"horizontal": horizontal, "vertical": vertical, "idor": idor}


def _build_idor_cases(
    actors: list[tuple[str, dict]],
    hierarchy: list[str],
    idor: list[dict],
) -> None:
    """构建 IDOR（不安全直接对象引用）检测用例。

    策略：
      1. 收集每个身份拥有的 resource_ids（身份池中可选字段）
      2. 对每个非最高权限身份，尝试访问其他身份的资源 ID → 应被拒绝
      3. 无 resource_ids 时，用身份 key 作为资源占位符生成骨架用例
         （执行时由 page 探索器填充实际资源 ID）

    IDOR 是 OWASP Top 10 经典漏洞：通过修改 URL/参数中的资源 ID
    访问无权访问的资源。检测维度：actor ≠ resource_owner → 应 403/不可见。
    """
    # 收集每个身份的资源 ID
    resource_map: dict[str, list[str]] = {}
    for key, ident in actors:
        rids = ident.get("resource_ids")
        if isinstance(rids, list) and rids:
            resource_map[key] = [str(r) for r in rids if r]
        else:
            # 无显式 resource_ids → 用身份 key 作为占位资源 ID
            resource_map[key] = ["%s_resource_001" % key]

    for actor_key, actor in actors:
        actor_role = str(actor.get("role", "") or "").strip().lower()
        # 最高权限角色跳过（admin 可访问所有资源，不构成 IDOR）
        _actor_top_priv = (
            str(actor.get("data_scope", "") or "").strip() == "all"
        ) or (bool(hierarchy) and actor_role == hierarchy[0])
        if _actor_top_priv:
            continue
        for owner_key, owner in actors:
            if owner_key == actor_key:
                continue
            owner_role = str(owner.get("role", "") or "").strip().lower()
            for rid in resource_map.get(owner_key, []):
                idor.append(
                    {
                        "actor": actor_key,
                        "actor_role": actor_role,
                        "resource_owner": owner_key,
                        "resource_owner_role": owner_role,
                        "resource_id": rid,
                        "action": "访问他人资源 ID",
                        "expected_failure": True,
                    }
                )


def to_case_skeletons(matrix: dict) -> list[dict]:
    """越权组合 → 用例骨架 [{case_id, title, steps, expected_outcome}]。

    expected_failure 语义：越权访问应被拒绝，拒绝=通过（expected_outcome 含"应被拒绝"）。

    ★ 2026-08-12 增强：新增 IDOR 用例骨架生成（PRIV-I 前缀）。
    """
    matrix = matrix or {}
    skeletons: list[dict] = []
    idx = 0
    for h in matrix.get("horizontal", []) or []:
        idx += 1
        skeletons.append(
            {
                "case_id": "PRIV-H%03d" % idx,
                "title": "[越权-横向] %s(%s) 访问 %s(%s) 数据"
                % (
                    h.get("actor"),
                    h.get("actor_role"),
                    h.get("victim"),
                    h.get("victim_role"),
                ),
                "steps": [
                    "以 %s 身份登录" % h.get("actor"),
                    "%s：%s" % (h.get("action"), h.get("data")),
                ],
                "expected_outcome": "越权访问应被拒绝（expected_failure=True）：返回无权限/403/数据不可见即视为通过",
            }
        )
    for v in matrix.get("vertical", []) or []:
        idx += 1
        skeletons.append(
            {
                "case_id": "PRIV-V%03d" % idx,
                "title": "[越权-纵向] %s(%s) 执行 %s 级操作"
                % (v.get("actor"), v.get("actor_role"), v.get("required_role")),
                "steps": [
                    "以 %s 身份登录" % v.get("actor"),
                    v.get("action", ""),
                ],
                "expected_outcome": "越权操作应被拒绝（expected_failure=True）：无权限提示/403 即视为通过",
            }
        )
    # ★ 2026-08-12: IDOR 用例骨架
    for i in matrix.get("idor", []) or []:
        idx += 1
        skeletons.append(
            {
                "case_id": "PRIV-I%03d" % idx,
                "title": "[越权-IDOR] %s(%s) 访问 %s(%s) 的资源 %s"
                % (
                    i.get("actor"),
                    i.get("actor_role"),
                    i.get("resource_owner"),
                    i.get("resource_owner_role"),
                    i.get("resource_id"),
                ),
                "steps": [
                    "以 %s 身份登录" % i.get("actor"),
                    "直接访问资源 ID: %s" % i.get("resource_id"),
                ],
                "expected_outcome": "IDOR 越权访问应被拒绝（expected_failure=True）：返回 403/无权限/资源不可见即视为通过",
            }
        )
    return skeletons


# ─────────────────────────────────────────────────────────────────────────────
# Step3 用例生成流程接入（2026-08-12 新增）
# ─────────────────────────────────────────────────────────────────────────────
