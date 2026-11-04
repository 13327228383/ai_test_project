# -*- coding: utf-8 -*-
"""★ P1-5: Oracle 结果持久化 — oracle_results + visual_review 表写入。

职责边界：
  - 仅负责持久化，不做校验逻辑
  - 提供统一写入接口，降级安全（失败不阻断主流程）
  - 与 src/oracle/correctness_vlm.py（校验逻辑）解耦

通用性（§12.4）：
  1. 跨系统：oracle_type/verdict 是通用分类
  2. 跨行业：不含业务术语
  3. 跨页面：不依赖页面结构
  4. 跨 Tab：不涉及
  5. 数据完整性：写入失败不阻断主流程（warning + 返回 False）
  6. 根因溯源：解决"oracle 结论零持久化"导致 Step5 无 oracle 维度数据
  7. 覆盖性：所有 oracle 写入走统一入口
  8. 全局观：作为 Step4 执行后 + VLM 复核的持久化层，不修改现有校验逻辑
  9. 职责边界：store 只持久化，oracle 只校验
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

_log = logging.getLogger(__name__)

# ── 有效值白名单（防 SQL 注入 + 语义校验）──
# "security"（2026-09-05）：安全横切门禁维度，与 a11y/performance 同为通用横切 oracle。
_VALID_ORACLE_TYPES = {"existence", "correctness", "completeness", "accessibility", "performance", "security"}
_VALID_VERDICTS = {"pass", "fail", "warn", ""}


def write_oracle_result(
    project: str,
    version_id: int,
    execution_id: int,
    oracle_type: str,
    verdict: str,
    score: float = 0.0,
    detail: Optional[dict[str, Any]] = None,
    run_id: str = "",
) -> bool:
    """写入一条 oracle 结论到 oracle_results 表。

    参数:
        project:      项目名
        version_id:   版本ID（0=legacy）
        execution_id: 关联 executions.id（0=独立复核）
        oracle_type:  oracle 类型 (existence/correctness/completeness/...)
        verdict:      结论 (pass/fail/warn)
        score:        得分 0~1
        detail:       明细 (dict, JSON 序列化)
        run_id:       本轮 run id（★2026-09-20：run 级独立复核 execution_id=0 的行
                      必须携带真实 run_id，否则 Step5 聚合器仅经 executions INNER JOIN
                      归属 run 时会丢弃 execution_id=0 的 run 级行——横切墙 security/a11y
                      因此曾对报告不可见）。向上兼容：不传则保持原样（空串）。

    返回:
        True=写入成功, False=写入失败（不抛异常，不阻断主流程）

    通用性：oracle_type/verdict 白名单校验，detail 通用 JSON。
    """
    if not project or execution_id < 0:
        _log.warning(
            "[oracle.store] oracle_result 参数缺失: project=%s exec_id=%s",
            project,
            execution_id,
        )
        return False

    oracle_type = str(oracle_type or "").strip().lower()
    if oracle_type not in _VALID_ORACLE_TYPES:
        _log.warning("[oracle.store] 无效 oracle_type: %s", oracle_type)
        return False

    verdict = str(verdict or "").strip().lower()
    if verdict not in _VALID_VERDICTS:
        _log.warning("[oracle.store] 无效 verdict: %s", verdict)
        return False

    try:
        score_val = max(0.0, min(1.0, float(score or 0)))
    except (TypeError, ValueError):
        score_val = 0.0

    detail_json = None
    if detail is not None:
        try:
            detail_json = json.dumps(detail, ensure_ascii=False)
        except (TypeError, ValueError) as e:
            _log.warning("[oracle.store] detail 序列化失败: %s", str(e)[:80])
            detail_json = None

    from src.core.db_manager import get_connection

    conn = None
    try:
        conn = get_connection()
        conn.execute(
            "INSERT INTO oracle_results "
            "(project, version_id, execution_id, run_id, oracle_type, verdict, score, detail) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (
                project,
                version_id,
                execution_id,
                str(run_id or ""),
                oracle_type,
                verdict,
                score_val,
                detail_json,
            ),
        )
        conn.commit()
        _log.info(
            "[oracle.store] oracle_result 写入: exec=%d type=%s verdict=%s score=%.2f",
            execution_id,
            oracle_type,
            verdict,
            score_val,
        )
        return True
    except Exception as e:
        _log.warning("[oracle.store] oracle_result 写入失败: %s", str(e)[:100])
        if conn:
            try:
                conn.rollback()
            except Exception as _e:
                _log.debug("[oracle.store] 回滚异常: %s", _e, exc_info=True)
        return False
    finally:
        if conn:
            try:
                conn.close()
            except Exception as _e:
                _log.debug("[oracle.store] 关闭连接异常: %s", _e, exc_info=True)


def write_visual_review(
    project: str,
    version_id: int,
    execution_id: int,
    screenshot_path: str = "",
    vlm_diff: Optional[dict[str, Any]] = None,
    baseline_id: Optional[int] = None,
) -> bool:
    """写入一条 VLM 视觉复核记录到 visual_review 表。

    参数:
        project:        项目名
        version_id:     版本ID（0=legacy）
        execution_id:   关联 executions.id（0=独立复核）
        screenshot_path: 截图路径
        vlm_diff:       VLM 差异区域 (dict, JSON 序列化)
        baseline_id:    关联 visual_baselines.id (可选)

    返回:
        True=写入成功, False=写入失败（不抛异常，不阻断主流程）

    通用性：vlm_diff 通用 JSON，不含业务术语。
    """
    if not project or execution_id < 0:
        _log.warning(
            "[oracle.store] visual_review 参数缺失: project=%s exec_id=%s",
            project,
            execution_id,
        )
        return False

    vlm_diff_json = None
    if vlm_diff is not None:
        try:
            vlm_diff_json = json.dumps(vlm_diff, ensure_ascii=False)
        except (TypeError, ValueError) as e:
            _log.warning("[oracle.store] vlm_diff 序列化失败: %s", str(e)[:80])
            vlm_diff_json = None

    from src.core.db_manager import get_connection

    conn = None
    try:
        conn = get_connection()
        conn.execute(
            "INSERT INTO visual_review "
            "(project, version_id, execution_id, baseline_id, screenshot_path, vlm_diff, human_confirm) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (
                project,
                version_id,
                execution_id,
                baseline_id,
                str(screenshot_path or "")[:1024],
                vlm_diff_json,
                "pending",
            ),
        )
        conn.commit()
        _log.info(
            "[oracle.store] visual_review 写入: exec=%d baseline=%s",
            execution_id,
            baseline_id,
        )
        return True
    except Exception as e:
        _log.warning("[oracle.store] visual_review 写入失败: %s", str(e)[:100])
        if conn:
            try:
                conn.rollback()
            except Exception as _e:
                _log.debug("[oracle.store] 回滚异常: %s", _e, exc_info=True)
        return False
    finally:
        if conn:
            try:
                conn.close()
            except Exception as _e:
                _log.debug("[oracle.store] 关闭连接异常: %s", _e, exc_info=True)


def write_oracle_result_from_execution(
    project: str,
    version_id: int,
    execution_id: int,
    case_passed: bool,
    status: str,
    step_results: list,
) -> bool:
    """★ P1-5: 从执行结果聚合 oracle 结论并写入 oracle_results 表。

    聚合逻辑：
    - verdict: pass if case_passed else fail
    - oracle_type: correctness（功能正确性，通用分类）
    - detail: 含 status, step_count, verify_tracks(ui/api/db 三轨证据)

    参数:
        project:      项目名
        version_id:   版本ID（0=legacy）
        execution_id: 关联 executions.id
        case_passed:  用例最终是否通过（含置信度闸门后的判定）
        status:       executions.status（PASS/FAIL/MANUAL_REVIEW）
        step_results: 步骤结果列表，每项含 _verify_result 三轨证据

    返回:
        True=写入成功, False=写入失败（不阻断主流程）

    通用性（§12.4）:
        1. 跨系统: oracle_type=correctness 通用，不含业务术语
        2. 跨行业: 通用
        3. 跨页面: 通用
        4. 数据完整性: 失败不阻断主流程（warning + 返回 False）
        5. 根因溯源: 解决"oracle_results 零写入"导致 Step5 无 oracle 维度数据
        6. 覆盖性: 所有走 _execute_one_intent 的用例都写入
        7. 全局观: 为 Step5 报告 + P2-12 SelfEval 提供 oracle 数据
        8. 职责边界: store 只持久化，不做校验决策（§13.4）
    """
    if not project or execution_id < 0:
        _log.warning(
            "[oracle.store] aggregate 参数缺失: project=%s exec_id=%s",
            project,
            execution_id,
        )
        return False

    # 聚合三轨证据（ui/api/db）
    verify_tracks = []
    if isinstance(step_results, list):
        for sr in step_results:
            if not isinstance(sr, dict):
                continue
            vr = sr.get("_verify_result")
            if not isinstance(vr, dict):
                continue
            for track in ("ui", "api", "db"):
                td = vr.get(track)
                if isinstance(td, dict):
                    verify_tracks.append(
                        {
                            "track": track,
                            "passed": td.get("passed"),
                            "evidence": td.get("evidence"),
                        }
                    )

    detail = {
        "status": str(status or ""),
        "step_count": len(step_results) if isinstance(step_results, list) else 0,
        "verify_tracks": verify_tracks,
    }

    return write_oracle_result(
        project=project,
        version_id=version_id,
        execution_id=execution_id,
        oracle_type="correctness",
        verdict="pass" if case_passed else "fail",
        score=1.0 if case_passed else 0.0,
        detail=detail,
    )


def write_a11y_result_from_execution(
    project: str,
    version_id: int,
    execution_id: int,
    case_id: str,
    step_results: list,
) -> bool:
    """★ 从执行结果聚合 a11y 结论并写入 oracle_results（oracle_type=accessibility）。

    可访问性维度（``src/extensions/a11y`` 8 规则，规则驱动无 AI 依赖）作为独立
    oracle 维度落库，供 Step5 报告聚合出「可访问性」区块。闭环：step4 采集 →
    本函数持久化 → step5 ``_a11y_report`` 聚合渲染。

    聚合语义（fail-open，与 ``_a11y_gate._flag_a11y`` 一致）：
      - 任一 step 声明 a11y 且拿到页面 → checked；无页面 → skipped；
      - 累加各 step 的 total / by_severity / findings；
      - verdict = pass(0 违规) / warn(有违规)，score = 1 - min(total,20)*0.05；
      - 本轮无任何 step 声明 a11y → 不写空记录（返回 False）。

    返回:
        True=写入成功, False=写入失败或无可写数据（不阻断主流程）。
    """
    if not project or execution_id < 0:
        return False

    agg: dict[str, Any] = {
        "checked": 0,
        "skipped": 0,
        "total": 0,
        "by_severity": {},
        "findings": [],
    }
    any_checked = False
    if isinstance(step_results, list):
        for sr in step_results:
            if not isinstance(sr, dict):
                continue
            vr = sr.get("_verify_result")
            if not isinstance(vr, dict):
                continue
            a11y = vr.get("a11y")
            if not isinstance(a11y, dict):
                continue
            if not a11y.get("enabled"):
                continue
            any_checked = True
            if a11y.get("skipped"):
                agg["skipped"] += 1
                continue
            agg["checked"] += 1
            agg["total"] += int(a11y.get("total") or 0)
            for k, v in (a11y.get("by_severity") or {}).items():
                agg["by_severity"][k] = agg["by_severity"].get(k, 0) + int(v or 0)
            for f in (a11y.get("findings") or [])[:50]:
                if isinstance(f, dict):
                    agg["findings"].append(f)

    if not any_checked:
        return False  # 本轮无 a11y 声明，不写空记录

    _total = int(agg["total"] or 0)
    verdict = "pass" if _total == 0 else "warn"
    score = max(0.0, 1.0 - min(_total, 20) * 0.05)

    detail = {
        "case_id": str(case_id or ""),
        "checked": agg["checked"],
        "skipped": agg["skipped"],
        "total": _total,
        "by_severity": agg["by_severity"],
        "findings": agg["findings"],
    }

    return write_oracle_result(
        project=project,
        version_id=version_id,
        execution_id=execution_id,
        oracle_type="accessibility",
        verdict=verdict,
        score=score,
        detail=detail,
    )


def write_perf_result_from_gate(
    project: str,
    version_id: int,
    execution_id: int,
    gate: dict[str, Any],
) -> bool:
    """★ 把 run 级性能门禁结果写入 oracle_results（oracle_type=performance）。

    背景：三横切门禁中 a11y→oracle_results(accessibility)、
    visual→visual_review 均落 MySQL 唯一事实源，唯 perf 只进日志/报告 JSON——违反
    「MySQL 为执行/校验数据唯一事实源」约束且前端不可复算。本函数把 perf 作为
    「性能 oracle」纳入既有通用 oracle_results 表，与 a11y 走同一 write_oracle_result
    入口，不另造表、不绑定任何靶子业务。

    派生语义（fail-open，绝不伪造结论）：
      - 无任何 verdict（样本缺失）→ 不写空记录，返回 False；
      - blocking 或存在 fail → verdict=fail；
      - 全部 verdict == PASS → verdict=pass；
      - 否则（pre_gate / baseline_established / no_data 混合）→ verdict=warn；
      - score = 判定为 PASS 的 key 数 / 总 key 数（0~1，诚实反映通过占比）。

    返回:
        True=写入成功, False=写入失败或无可写数据（不阻断主流程）。
    """
    if not project or execution_id < 0:
        return False

    verdicts = gate.get("verdicts") if isinstance(gate, dict) else None
    if not isinstance(verdicts, list) or not verdicts:
        return False  # 本轮无性能样本，不写空记录

    summary = gate.get("summary") or {}
    blocking = bool(summary.get("blocking")) or bool(
        summary.get("blocking_keys")
    )

    _STATUS = {"pass", "pre_gate", "fail", "baseline_established", "no_data"}
    pass_cnt = 0
    keys: list[dict[str, Any]] = []
    for v in verdicts:
        if not isinstance(v, dict):
            continue
        st = str(v.get("status") or "").lower()
        if st in _STATUS:
            if st == "pass":
                pass_cnt += 1
            keys.append({
                "key": v.get("key", ""),
                "kind": v.get("kind", ""),
                "status": st,
                "metrics": v.get("metrics") or {},
                "violations": v.get("violations") or [],
            })

    if pass_cnt == len(keys) and not blocking:
        verdict = "pass"
    elif blocking or any(k["status"] == "fail" for k in keys):
        verdict = "fail"
    else:
        verdict = "warn"
    score = (pass_cnt / len(keys)) if keys else 0.0

    # 统计口径取自「实际被判定」的 keys（单一来源，保证 detail ≡ verdicts），
    # 不信任 summary 计数——即便 gate 层 summary 缺失，落库 detail 仍自洽可复算。
    _cnt = {"pass": 0, "fail": 0, "pre_gate": 0, "baseline_established": 0, "no_data": 0}
    for _k in keys:
        _cnt[_k["status"]] = _cnt.get(_k["status"], 0) + 1

    detail = {
        "run_id": str(gate.get("run_id") or ""),
        "samples": int(summary.get("total") or float(summary.get("samples") or 0)),
        "total_keys": len(keys),
        "pass": _cnt["pass"],
        "fail": _cnt["fail"],
        "pre_gate": _cnt["pre_gate"],
        "baseline_established": _cnt["baseline_established"],
        "no_data": _cnt["no_data"],
        "blocking": blocking,
        "keys": keys[:100],          # 截断，防 detail 过大
        "error": str(gate.get("error") or ""),
    }

    return write_oracle_result(
        project=project,
        version_id=version_id,
        execution_id=execution_id,
        oracle_type="performance",
        verdict=verdict,
        score=score,
        detail=detail,
    )
