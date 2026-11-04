# -*- coding: utf-8 -*-
"""★ P0-3 3a / P0-4: 三车道路由持久化（review_lane 表读写）。

职责：将需求/用例的三车道路由（green/yellow/red）写入 review_lane 表。
Step2 写需求级路由（case_id=req_id），审核端点读取用例级路由（JOIN test_cases）。
"""
from __future__ import annotations

import logging
from typing import Any, Optional

_log = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# 三车道路由判定（唯一事实源）
# ═══════════════════════════════════════════════════════════════
# 「需求属于哪条车道」是**需求的属性**，由置信度阈值唯一决定。它此前只以
# 流程副作用的形式存在于 Step2 主流程体内，导致不经过 Step2 的项目（外部导入
# 需求、历史项目、单步重跑 Step3）永久没有 lane → 审核闸门无法放行。
# 现在把它下沉为纯函数，Step2 与「持久层缺失时即时派生」共用同一实现。
LANE_GREEN_MIN = 85
LANE_YELLOW_MIN = 60


def classify_lane(req: dict[str, Any]) -> tuple[str, str]:
    """需求 → (lane, reason)。唯一实现，Step2 与派生路径共用。

    green(≥85) / yellow([60,85)) / red(<60)；embed 合并切片强制 yellow。
    """
    if (req or {}).get("_needs_manual_review"):
        return "yellow", "embed合并切片，强制人工审核"
    try:
        from src.pipeline.utils.confidence_scorer import (
            get_avg_confidence,
            score_requirement,
        )

        _score = get_avg_confidence(score_requirement(req or {}))
    except Exception as _e:
        _log.debug("[review_lane_store] 置信度计算失败，降级 yellow: %s", str(_e)[:80])
        return "yellow", "置信度不可计算，保守待审"
    if _score >= LANE_GREEN_MIN:
        return "green", f"置信度={_score}≥{LANE_GREEN_MIN}，自动通过"
    if _score >= LANE_YELLOW_MIN:
        return "yellow", f"置信度={_score}∈[{LANE_YELLOW_MIN},{LANE_GREEN_MIN})，待人工审核"
    return "red", f"置信度={_score}<{LANE_YELLOW_MIN}，低置信度阻塞"


def derive_lanes_from_requirements(project: str, version_id: int) -> list[dict[str, Any]]:
    """从已落库的 requirements 即时派生需求级路由（不依赖 Step2 流程）。

    用于：项目需求由外部导入（YApi/文档/Figma）或缺口路段回填，
    使审核闸门在任何项目状态下都具备可通行判据。
    """
    if not project or version_id is None or version_id < 0:
        return []
    from src.core.db_manager import get_connection

    conn = None
    try:
        conn = get_connection()
        rows = (
            conn.execute(
                "SELECT req_id, module, description, operation, expected_feedback, "
                "source_quote, is_implicit, _evidence_confidence "
                "FROM requirements WHERE project=%s AND version_id=%s",
                (project, version_id),
            ).fetchall()
            or []
        )
    except Exception as e:
        _log.warning("[review_lane_store] 需求读取失败: %s", str(e)[:160])
        return []
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass
    out = []
    for r in rows:
        _d = dict(r)
        _rid = str(_d.get("req_id") or "").strip()
        if not _rid:
            continue
        _lane, _reason = classify_lane(_d)
        out.append({"case_id": _rid, "lane": _lane, "reason": _reason})
    return out


def ensure_review_lanes(project: str, version_id: int) -> int:
    """缺口路段判定：lane 缺失时从 requirements 派生并落库。返回写入条数。

    幂等：已有 lane 则原样返回，不做任何覆盖（不覆盖人工审核结果）。
    """
    if not project or version_id is None or version_id < 0:
        return 0
    _existing = read_review_lanes(project, version_id)
    if _existing:
        return len(_existing)
    _derived = derive_lanes_from_requirements(project, version_id)
    if not _derived:
        return 0
    return write_review_lanes(project, version_id, _derived)


def write_review_lanes(
    project: str,
    version_id: int,
    items: list[dict[str, Any]],
) -> int:
    """批量写入三车道路由到 review_lane 表（先删后插，幂等）。

    参数:
        project:    项目名
        version_id: 版本ID
        items:      路由项列表，每项含:
                    - case_id (str): 用例ID或需求ID（req_id）
                    - lane (str): green/yellow/red
                    - reason (str): 分流原因

    返回:
        写入条数
    """
    # ★ P0-1 批C修复(2026-07-25): version_id=0 是合法 legacy 值（schema DEFAULT 0），
    #   不能用 `not version_id`（0 是 falsy 会被误拒）。
    #   根因: 与 sampler.py 同类 bug — `not version_id` 把 0 当缺失拒绝，
    #         与 schema 设计 0=legacy 不一致，导致 legacy 项目三车道路由全部丢失
    #   通用性: `version_id is None or version_id < 0` 对所有 legacy 项目(vid=0)放行，
    #          仅拦截 None/负数（真正非法值），跨系统/跨版本通用
    #   覆盖性: 同步覆盖本文件 4 处校验（write/read_review/read_case/get_for_case）
    #   合规: AGENTS.md §13.3 — version_id 是 DB 自增ID，0=legacy 是约定
    if not project or version_id is None or version_id < 0 or not items:
        return 0

    from src.core.db_manager import get_connection

    conn = None
    try:
        conn = get_connection()
        # 先清除当前版本旧路由（幂等：重跑时避免重复）
        conn.execute(
            "DELETE FROM review_lane WHERE project=%s AND version_id=%s",
            (project, version_id),
        )

        # 批量插入
        rows = []
        for item in items:
            cid = str(item.get("case_id", "") or "").strip()
            lane = item.get("lane", "yellow")
            reason = str(item.get("reason", ""))[:512]
            routed = 0 if lane == "green" else 1
            if cid:
                rows.append((project, version_id, cid, lane, routed, reason))

        if rows:
            conn.executemany(
                "INSERT INTO review_lane (project, version_id, case_id, lane, routed_to_human, reason) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                rows,
            )
            conn.commit()
            _log.info(
                "[review_lane_store] 写入 %d 条 (green=%d yellow=%d red=%d)",
                len(rows),
                sum(1 for r in rows if r[3] == "green"),
                sum(1 for r in rows if r[3] == "yellow"),
                sum(1 for r in rows if r[3] == "red"),
            )
            return len(rows)
        conn.commit()
        return 0
    except Exception as e:
        _log.warning("[review_lane_store] 写入失败: %s", str(e)[:120])
        if conn:
            try:
                conn.rollback()
            except Exception as _e:
                _log.debug("[review_lane_store] 回滚异常: %s", _e, exc_info=True)
        return 0
    finally:
        if conn:
            try:
                conn.close()
            except Exception as _e:
                _log.debug("[review_lane_store] 关闭连接异常: %s", _e, exc_info=True)


# ═══════════════════════════════════════════════════════════════
# ★ P0-4: 读取函数
# ═══════════════════════════════════════════════════════════════


def read_review_lanes(project: str, version_id: int) -> list[dict[str, Any]]:
    """读取需求级三车道路由（原始数据，case_id=req_id）。

    返回: [{case_id, lane, routed_to_human, reason}, ...]
    """
    if not project or version_id is None or version_id < 0:
        return []
    from src.core.db_manager import get_connection

    conn = None
    try:
        conn = get_connection()
        rows = (
            conn.execute(
                "SELECT case_id, lane, routed_to_human, reason "
                "FROM review_lane WHERE project=%s AND version_id=%s",
                (project, version_id),
            ).fetchall()
            or []
        )
        return [dict(r) for r in rows]
    except Exception as e:
        _log.warning("[review_lane_store] 读取失败: %s", str(e)[:120])
        return []
    finally:
        if conn:
            try:
                conn.close()
            except Exception as _e:
                _log.debug("[review_lane_store] 关闭连接异常: %s", _e, exc_info=True)


def read_case_lanes(project: str, version_id: int) -> list[dict[str, Any]]:
    """读取用例级三车道路由（JOIN test_cases，case_id=用例编号）。

    ★ 通用修复(2026-07-24 P0-4 主流水线根因): 用 COALESCE 优先返回用例 ID。
      根因: review_lane 存需求级路由(case_id=REQ-xxx)，API 端点用用例 ID(TC-REQ-xxx)查找。
            旧 SQL `SELECT rl.case_id AS case_id` 返回需求 ID → lane_map key=REQ-xxx
            → API 用 TC-REQ-xxx 查找不到 → 全部降级为 yellow + "no_lane_data"。
      修复: `COALESCE(tc.case_id, rl.case_id) AS case_id`
            - test_cases 有数据: case_id=tc.case_id(用例ID)，每条用例继承需求级 lane
            - test_cases 为空(Step3未跑): case_id=rl.case_id(需求ID)，降级兜底
      通用性: 任何系统在任何阶段(Step3前/后)都能正确映射需求级 lane 到用例级。
    返回: [{case_id, req_id, lane, routed_to_human, reason}, ...]
    """
    if not project or version_id is None or version_id < 0:
        return []
    from src.core.db_manager import get_connection

    conn = None
    try:
        conn = get_connection()
        rows = (
            conn.execute(
                "SELECT COALESCE(tc.case_id, rl.case_id) AS case_id, "
                "rl.case_id AS req_id, rl.lane, rl.routed_to_human, rl.reason "
                "FROM review_lane rl "
                "LEFT JOIN test_cases tc "
                "  ON tc.req_id = rl.case_id "
                "  AND tc.project = rl.project "
                "  AND tc.version_id = rl.version_id "
                "WHERE rl.project = %s AND rl.version_id = %s",
                (project, version_id),
            ).fetchall()
            or []
        )
        return [dict(r) for r in rows]
    except Exception as e:
        _log.warning("[review_lane_store] 用例级路由读取失败: %s", str(e)[:120])
        return []
    finally:
        if conn:
            try:
                conn.close()
            except Exception as _e:
                _log.debug("[review_lane_store] 关闭连接异常: %s", _e, exc_info=True)


def read_req_lanes(
    project: str, version_id: Optional[int] = None
) -> dict[str, dict[str, Any]]:
    """★ P1-7: 读取需求级三车道路由（供 Step2 审核端点使用）。

    与 read_case_lanes 的区别：
      - read_case_lanes: JOIN test_cases，返回用例级路由（供 Step3 审核端点）
      - read_req_lanes: 直接读 review_lane，返回需求级路由（供 Step2 审核端点）

    参数:
        project:    项目名
        version_id: 版本ID，None 时自动获取当前活跃版本

    返回:
        {req_id: {lane, reason, routed_to_human}, ...}
        读取失败返回空 dict
    """
    if not project:
        return {}
    # 自动获取 version_id
    if version_id is None:
        try:
            from src.core.versioning import current_version_id

            version_id = int(current_version_id(project) or 0)
        except Exception as _e:
            _log.debug("[review_lane_store] 获取 version_id 失败: %s", str(_e)[:60])
            version_id = 0
    if version_id < 0:
        return {}
    from src.core.db_manager import get_connection

    conn = None
    try:
        conn = get_connection()
        rows = (
            conn.execute(
                "SELECT case_id, lane, routed_to_human, reason "
                "FROM review_lane "
                "WHERE project = %s AND version_id = %s",
                (project, version_id),
            ).fetchall()
            or []
        )
        result = {}
        for r in rows:
            d = dict(r)
            rid = d.get("case_id", "")
            if rid:
                result[rid] = {
                    "lane": d.get("lane", "yellow"),
                    "reason": d.get("reason", ""),
                    "routed_to_human": bool(d.get("routed_to_human", False)),
                }
        return result
    except Exception as e:
        _log.warning("[review_lane_store] 需求级路由读取失败: %s", str(e)[:120])
        return {}
    finally:
        if conn:
            try:
                conn.close()
            except Exception as _e:
                _log.debug("[review_lane_store] 关闭连接异常: %s", _e, exc_info=True)


def get_lane_for_case(
    case_id: str, project: str, version_id: int
) -> Optional[dict[str, Any]]:
    """查询单条用例的三车道路由。

    返回: {case_id, lane, routed_to_human, reason} 或 None
    """
    if not case_id or not project or version_id is None or version_id < 0:
        return None
    from src.core.db_manager import get_connection

    conn = None
    try:
        conn = get_connection()
        row = conn.execute(
            "SELECT tc.case_id, rl.lane, rl.routed_to_human, rl.reason "
            "FROM review_lane rl "
            "JOIN test_cases tc "
            "  ON tc.req_id = rl.case_id "
            "  AND tc.project = rl.project "
            "  AND tc.version_id = rl.version_id "
            "WHERE tc.case_id = %s AND rl.project = %s AND rl.version_id = %s "
            "LIMIT 1",
            (case_id, project, version_id),
        ).fetchone()
        return dict(row) if row else None
    except Exception as e:
        _log.warning("[review_lane_store] 单条查询失败: %s", str(e)[:120])
        return None
    finally:
        if conn:
            try:
                conn.close()
            except Exception as _e:
                _log.debug("[review_lane_store] 关闭连接异常: %s", _e, exc_info=True)


def check_lane_gate(case_id: str, project: str, version_id: int) -> dict[str, Any]:
    """★ P0-4: 车道闸门 — 检查用例是否在 red lane，是则阻断执行。

    通用性: 跨系统/行业/页面 — 只依赖 review_lane 表，不依赖业务术语。
    策略:
      1. 优先用 get_lane_for_case（JOIN test_cases，用例级路由）
      2. 若 test_cases 无数据（Step3 未跑），从 case_id 提取 req_id 直查
      3. review_lane 无数据 → 放行（向后兼容，不阻断无车道数据的旧项目）

    返回: {"blocked": bool, "lane": str, "reason": str}
      - blocked=True:  用例在 red lane，禁止执行
      - blocked=False: 用例在 green/yellow lane 或无 lane 数据，允许执行
    """
    if not case_id or not project or version_id is None or version_id < 0:
        return {"blocked": False, "lane": "", "reason": "参数不完整，跳过车道检查"}

    try:
        # 策略1: 用例级路由（JOIN test_cases）
        _lane_info = get_lane_for_case(case_id, project, version_id)

        # 策略2: 用例级路由无数据，从 case_id 提取 req_id 直查
        if _lane_info is None:
            import re

            _m = re.match(r"TC-(REQ-\d+)", case_id)
            if _m:
                _req_id = _m.group(1)
                _conn = None
                try:
                    from src.core.db_manager import get_connection

                    _conn = get_connection()
                    _row = _conn.execute(
                        "SELECT lane, routed_to_human, reason FROM review_lane "
                        "WHERE case_id = %s AND project = %s AND version_id = %s "
                        "LIMIT 1",
                        (_req_id, project, version_id),
                    ).fetchone()
                    _lane_info = dict(_row) if _row else None
                except Exception as _e2:
                    _log.debug("[review_lane_store] req_id直查失败: %s", str(_e2)[:80])
                finally:
                    if _conn:
                        try:
                            _conn.close()
                        except Exception as e:
                            _log.debug("关闭DB连接失败: %s", e)

        if _lane_info is None:
            return {"blocked": False, "lane": "", "reason": "无车道数据，放行"}

        _lane = (_lane_info.get("lane") or "").strip().lower()
        _reason = _lane_info.get("reason", "")

        if _lane == "red":
            _log.warning(
                "[LaneGate] BLOCKED case_id=%s project=%s lane=red reason=%s",
                case_id,
                project,
                _reason[:80],
            )
            return {"blocked": True, "lane": "red", "reason": _reason}

        _log.debug(
            "[LaneGate] ALLOW case_id=%s lane=%s reason=%s",
            case_id,
            _lane,
            _reason[:60],
        )
        return {"blocked": False, "lane": _lane, "reason": _reason}

    except Exception as _e:
        _log.warning("[LaneGate] 检查异常(放行): %s", str(_e)[:120])
        return {"blocked": False, "lane": "", "reason": f"车道检查异常: {str(_e)[:80]}"}
