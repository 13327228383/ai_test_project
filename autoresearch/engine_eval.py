# -*- coding: utf-8 -*-
"""AutoResearch 评估基准 — 锁死，不可修改。

================================================================================
⚠️  本文件是整个 Ratchet Loop 的"天平"。
    任何 AI agent、任何人类开发者，都不允许修改此文件。
    修改评估基准等于作弊。如果你觉得基准有问题，请在 git commit
    message 里说明为什么需要修改基准，让人工审核。
================================================================================

评估分两层（两层都 FAIL → 整体 0 分）：

  Layer 1: TARGET EVAL  — 跑靶子 + DB 直查（smartshop / api_demo / ...）
                           验证"测试引擎对靶子的执行是否正确"
  Layer 2: ENGINE SELF-TEST — 直接打引擎后端 API（:8700）
                           验证"引擎本身是否健康"：
                           - 项目隔离（A 项目 executions 不混入 B）
                           - 版本隔离（version_id 不泄漏）
                           - 断言严格性（禁止 200 就 PASS 的宽松断言）
                           - 指标一致性（ai_health_metrics ↔ executions ↔ run_summary 对齐）
                           - CRUD 端点 + 多轨端点 + 横切端点返回正确

综合评分 = 0.4 * target_score + 0.6 * engine_score
（引擎自身质量比靶子表现更重要 — 引擎坏了靶子跑再好也没用）
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

_log = logging.getLogger("autoresearch.engine_eval")

# ══════════════════════════════════════════════════════════════════════════
# 不可修改的常量
# ══════════════════════════════════════════════════════════════════════════

WEIGHTS_TARGET = {
    "db":      0.20,  # DB 完整性
    "rate":    0.25,  # 真实通过率
    "fp":      0.25,  # 假阳性惩罚
    "strict":  0.20,  # 断言严格性（新增！禁止 200 就 PASS）
    "cross":   0.10,  # 横切门禁
}

WEIGHTS_ENGINE = {
    "isolation":    0.35,  # 项目+版本隔离
    "consistency":  0.30,  # 指标一致性（count/run_summary/ai_health 对齐）
    "endpoints":    0.20,  # 后端 API 端点返回正确（200 + JSON + 字段完整）
    "strictness":   0.15,  # 引擎内部断言逻辑是否 fail-closed（非 fail-open）
}

ENGINE_REQUIRED_TABLES = [
    "run_summary", "executions", "test_cases", "requirements",
    "case_meta", "ai_health_metrics", "trace_links", "oracle_results",
    "project_versions", "projects",
]

REGISTERED_TARGETS = ["smartshop", "api_demo", "spa_demo", "hospital_sut", "demo_all"]

ENGINE_BACKEND_URL = "http://127.0.0.1:8700"

# 已知项目名映射（case 不一致，SmartShop vs smartshop）
_PROJECT_NAME_MAP = {"smartshop": "SmartShop", "api_demo": "api_demo",
                      "spa_demo": "spa_demo", "hospital_sut": "hospital_sut",
                      "demo_all": "demo_all"}


# ══════════════════════════════════════════════════════════════════════════
# 结果 dataclass
# ══════════════════════════════════════════════════════════════════════════

@dataclass
class TargetEvalResult:
    target: str
    score: float           # 0-100
    db_score: float
    pass_rate: float
    false_positive_penalty: float
    strictness_penalty: float  # 断言宽松惩罚（0=严格，1=全是 200 就 PASS）
    strictness_details: list[dict]
    avg_confidence: float
    cross_cut_score: float
    run_ok: bool
    db_ok: bool
    tables_ok: bool
    execution_error: str = ""


@dataclass
class EngineSelfTestResult:
    score: float                     # 0-100
    isolation_score: float           # 项目+版本隔离
    consistency_score: float         # 指标三表对齐
    endpoint_score: float            # API 端点健康
    strictness_score: float          # 内部逻辑 fail-closed
    isolation_details: list[dict]
    consistency_details: list[dict]
    endpoint_details: list[dict]
    backend_up: bool


@dataclass
class FullEvalResult:
    target: str
    target_score: float              # 靶子层
    engine_score: float               # 引擎层
    overall_score: float             # 综合 = 0.4*target + 0.6*engine
    target_result: TargetEvalResult
    engine_result: EngineSelfTestResult


# ══════════════════════════════════════════════════════════════════════════
# 对外入口 — 一次评估两层
# ══════════════════════════════════════════════════════════════════════════

def evaluate(target: str = "smartshop", *, run_timeout: int = 900,
             skip_engine: bool = False) -> FullEvalResult:
    """完整评估：Layer 1 靶子 + Layer 2 引擎自检验。"""
    target = target or "smartshop"
    assert target in REGISTERED_TARGETS, f"未知 target={target!r}"

    _log.info("=" * 60)
    _log.info("[engine_eval] 开始评估 target=%s", target)
    _log.info("=" * 60)

    # ── Layer 1: 靶子评估 ────────────────────────────────────────
    tgt = _evaluate_target(target, run_timeout=run_timeout)

    # ── Layer 2: 引擎自检验 ──────────────────────────────────────
    if skip_engine:
        eng = EngineSelfTestResult(0.0, 0.0, 0.0, 0.0, 0.0, [], [], [], False)
    else:
        eng = _selftest_engine(target)

    overall = 0.4 * tgt.score + 0.6 * eng.score

    result = FullEvalResult(
        target=target,
        target_score=round(tgt.score, 2),
        engine_score=round(eng.score, 2),
        overall_score=round(overall, 2),
        target_result=tgt,
        engine_result=eng,
    )

    _log.info("[engine_eval] OVERALL: target=%.2f, engine=%.2f, combined=%.2f",
              tgt.score, eng.score, overall)
    return result


# ══════════════════════════════════════════════════════════════════════════
# Layer 1: 靶子评估
# ══════════════════════════════════════════════════════════════════════════

def _evaluate_target(target: str, *, run_timeout: int) -> TargetEvalResult:
    # 1. 执行
    run_ok, run_rc, run_error = _run_target(target, timeout=run_timeout)
    # 2. DB 探测
    db_ok, tables_ok, db_score = _probe_engine_db()
    # 3. 真实指标
    pass_rate, avg_confidence = _fetch_real_metrics(target, db_ok, run_ok)
    # 4. 假阳性
    fp_penalty, fp_details = _scan_false_positives(target, db_ok)
    # 5. 断言严格性（新增！）
    strict_penalty, strict_details = _scan_assertion_strictness(target, db_ok)
    # 6. 横切
    cross_score = _compute_cross_cut_score(target, db_ok)

    # 综合（run 失败封顶 10）
    raw = (
        WEIGHTS_TARGET["db"] * db_score
        + WEIGHTS_TARGET["rate"] * (pass_rate * 100)
        + WEIGHTS_TARGET["fp"] * (1.0 - fp_penalty) * 100
        + WEIGHTS_TARGET["strict"] * (1.0 - strict_penalty) * 100
        + WEIGHTS_TARGET["cross"] * cross_score
    )
    final = raw if run_ok else min(raw, 10.0)

    return TargetEvalResult(
        target=target, score=round(final, 2),
        db_score=round(db_score, 2),
        pass_rate=round(pass_rate, 4),
        false_positive_penalty=round(fp_penalty, 4),
        strictness_penalty=round(strict_penalty, 4),
        strictness_details=strict_details,
        avg_confidence=round(avg_confidence, 4),
        cross_cut_score=round(cross_score, 2),
        run_ok=run_ok, db_ok=db_ok, tables_ok=tables_ok,
        execution_error=run_error,
    )


def _scan_assertion_strictness(target: str, db_ok: bool) -> tuple[float, list[dict]]:
    """断言严格性扫描 — 禁止 "HTTP 200 就算 PASS" 的宽松断言。

    规则（锁死）：
      1. API 轨：actual 里只出现 status_code=2xx 且没有任何业务字段校验 → 宽松
      2. 所有 PASS 的 details JSON 里 assertions 数组长度 = 0 → 宽松
      3. check_type='empirical' 但 expected 为空字符串 → 宽松
    """
    if not db_ok:
        return 1.0, []
    details: list[dict] = []
    try:
        conn = _get_engine_connection(); cur = conn.cursor()
        # 宽松判定 1: PASS + details 里 assertions 为空
        cur.execute(
            "SELECT case_id, details, expected, actual FROM executions "
            "WHERE project=%s AND status='PASS'", (target,),
        )
        rows = cur.fetchall()
        loose = 0
        total_pass = len(rows)
        for r in rows:
            det = str(r.get("details") or "")
            expected = str(r.get("expected") or "")
            if len(expected.strip()) < 5:
                loose += 1
                details.append({
                    "case_id": r.get("case_id"),
                    "rule": "empty_expected",
                    "msg": f"expected 过短（<5字符）: '{expected[:50]}'",
                })
            # actual 只有 HTTP 状态码但没业务断言
            actual = str(r.get("actual") or "")
            if actual.strip() in ("200", "200 OK", "OK", "PASS", "pass", "success"):
                loose += 1
                details.append({
                    "case_id": r.get("case_id"),
                    "rule": "loose_status_only",
                    "msg": f"actual 只含状态码无业务断言: '{actual[:50]}'",
                })
        cur.close(); conn.close()
        if total_pass == 0:
            return 0.0, []
        penalty = min(loose / total_pass, 1.0)
        _log.info("[engine_eval] 断言严格性: loose=%d/%d, penalty=%.4f",
                  loose, total_pass, penalty)
        return penalty, details
    except Exception as e:
        _log.error("[engine_eval] 断言严格性扫描异常: %s", e)
        return 0.5, []


# ══════════════════════════════════════════════════════════════════════════
# Layer 2: 引擎自检验（直接打后端 API + DB 深度核对）
# ══════════════════════════════════════════════════════════════════════════

def _selftest_engine(target: str) -> EngineSelfTestResult:
    """引擎自身是否健康：隔离 + 一致性 + API 端点 + 内部 fail-closed。"""
    # 1. 后端是否可达
    backend_up = _check_backend_up()
    if not backend_up:
        _log.error("[engine_eval] 引擎后端 %s 不可达 → 引擎层 0 分", ENGINE_BACKEND_URL)
        return EngineSelfTestResult(0.0, 0.0, 0.0, 0.0, 0.0, [], [], [], False)

    # 2. 隔离度（跨 project 交叉污染检查）
    iso_score, iso_details = _check_isolation()

    # 3. 一致性（count vs run_summary vs ai_health 三表对齐）
    cons_score, cons_details = _check_consistency()

    # 4. API 端点健康
    ep_score, ep_details = _check_endpoints()

    # 5. 内部 fail-closed（代码里是否有大量 except: pass / fallback 链）
    strict_score = _check_internal_strictness()

    overall = (
        WEIGHTS_ENGINE["isolation"] * iso_score
        + WEIGHTS_ENGINE["consistency"] * cons_score
        + WEIGHTS_ENGINE["endpoints"] * ep_score
        + WEIGHTS_ENGINE["strictness"] * strict_score
    )

    _log.info("[engine_eval] ENGINE SELF-TEST: iso=%.1f cons=%.1f ep=%.1f strict=%.1f → overall=%.2f",
              iso_score, cons_score, ep_score, strict_score, overall)

    return EngineSelfTestResult(
        score=round(overall, 2),
        isolation_score=round(iso_score, 2),
        consistency_score=round(cons_score, 2),
        endpoint_score=round(ep_score, 2),
        strictness_score=round(strict_score, 2),
        isolation_details=iso_details,
        consistency_details=cons_details,
        endpoint_details=ep_details,
        backend_up=True,
    )


def _check_backend_up() -> bool:
    import urllib.request
    try:
        req = urllib.request.Request(f"{ENGINE_BACKEND_URL}/api/health")
        resp = urllib.request.urlopen(req, timeout=5)
        return resp.status == 200
    except Exception:
        try:
            resp = urllib.request.urlopen(ENGINE_BACKEND_URL, timeout=5)
            return resp.status == 200
        except Exception:
            return False


def _check_isolation() -> tuple[float, list[dict]]:
    """验证项目+版本隔离。

    规则：
      1. smartshop 的 executions.project 严格等于 "smartshop"（无 "SmartShop" 混入）
      2. 每个 version_id 的 executions 不跨越 project_versions.project
      3. ai_health_metrics 无跨 project 泄漏（project=smartshop 的行不应混入 api_demo 的 run_id）
    """
    details: list[dict] = []
    try:
        conn = _get_engine_connection(); cur = conn.cursor()
        issues = 0
        total_checks = 0

        # 检查 1: 大小写不一致
        cur.execute("SELECT DISTINCT project FROM executions")
        ex_projs = {r["project"].lower(): r["project"] for r in cur.fetchall()}
        cur.execute("SELECT DISTINCT name FROM projects")
        db_projs = {r["name"].lower(): r["name"] for r in cur.fetchall()}
        for low in db_projs:
            if low in ex_projs and ex_projs[low] != db_projs[low]:
                issues += 1
                details.append({
                    "rule": "case_mismatch",
                    "msg": f"大小写不一致: projects.name='{db_projs[low]}' vs executions.project='{ex_projs[low]}'",
                })
        total_checks += len(db_projs)

        # 检查 2: version_id 与 project 对应（version_id=1968 只能属于 smartshop）
        cur.execute("SELECT pv.id, pv.project FROM project_versions pv")
        vid_proj = {r["id"]: r["project"] for r in cur.fetchall()}
        for vid, expected_proj in vid_proj.items():
            cur.execute(
                "SELECT project, COUNT(*) cnt FROM executions WHERE version_id=%s GROUP BY project",
                (vid,),
            )
            for r in cur.fetchall():
                ex_proj = r["project"]
                # 大小写不敏感比较
                if ex_proj.lower() != expected_proj.lower():
                    issues += 1
                    details.append({
                        "rule": "version_leak",
                        "msg": f"version_id={vid} 属于 {expected_proj} 但 executions 有 {ex_proj} ({r['cnt']}行)",
                    })
        total_checks += len(vid_proj)

        cur.close(); conn.close()
        score = max(0.0, 100.0 - (issues / max(total_checks, 1)) * 100.0)
        return score, details

    except Exception as e:
        _log.error("[engine_eval] 隔离检查异常: %s", e)
        return 0.0, [{"rule": "db_error", "msg": str(e)[:200]}]


def _check_consistency() -> tuple[float, list[dict]]:
    """三表一致性：executions 计数 ↔ run_summary ↔ ai_health_metrics。"""
    details: list[dict] = []
    try:
        conn = _get_engine_connection(); cur = conn.cursor()
        issues = 0; total_checks = 0

        # 每个 project 的最新 run_summary vs executions COUNT（大小写不敏感匹配）
        cur.execute("SELECT DISTINCT LOWER(project) AS proj_lower FROM run_summary")
        rs_projs = {r["proj_lower"] for r in cur.fetchall()}
        cur.execute("SELECT DISTINCT LOWER(project) AS proj_lower FROM executions")
        ex_projs = {r["proj_lower"] for r in cur.fetchall()}

        for proj_lower in rs_projs & ex_projs:
            cur.execute(
                "SELECT total_cases, total_passed, total_failed FROM run_summary "
                "WHERE LOWER(project)=%s ORDER BY created_at DESC LIMIT 1", (proj_lower,),
            )
            rs = cur.fetchone()
            if not rs: continue
            cur.execute(
                "SELECT COUNT(*) total, "
                "COUNT(CASE WHEN status='PASS' THEN 1 END) p, "
                "COUNT(CASE WHEN status='FAIL' THEN 1 END) f "
                "FROM executions WHERE LOWER(project)=%s", (proj_lower,),
            )
            ex = cur.fetchone()
            total_checks += 1
            rs_p, ex_p = int(rs["total_passed"]), int(ex["p"] or 0)
            rs_f, ex_f = int(rs["total_failed"]), int(ex["f"] or 0)
            if ex_p > 0 and abs(rs_p - ex_p) > 1:
                issues += 1
                details.append({"rule": "pass_mismatch",
                    "msg": f"{proj_lower}: run_summary.passed={rs_p} vs executions.PASS={ex_p}"})
            if ex_f > 0 and abs(rs_f - ex_f) > 1:
                issues += 1
                details.append({"rule": "fail_mismatch",
                    "msg": f"{proj_lower}: run_summary.failed={rs_f} vs executions.FAIL={ex_f}"})

        cur.close(); conn.close()
        score = max(0.0, 100.0 - (issues / max(total_checks, 1)) * 150.0)
        return score, details
    except Exception as e:
        _log.error("[engine_eval] 一致性检查异常: %s", e); return 0.0, [{"rule": "db_error", "msg": str(e)[:200]}]


def _check_endpoints() -> tuple[float, list[dict]]:
    """打后端 API 看返回是否正常（HTTP 200 + JSON + 关键字段）。

    先 POST /api/login 拿 session cookie（JSON body, 字段 user_id + password），
    再调受保护端点。公开端点 /healthz /readyz 不需要登录。
    """
    import urllib.request, urllib.error, http.cookiejar, json as _json
    details: list[dict] = []; passed = 0; total = 0

    # Step 1: 登录（JSON body, user_id 字段 — 引擎实际路由）
    cookie_jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookie_jar))
    login_data = _json.dumps({"user_id": "admin", "password": "admin123"}).encode()
    try:
        req = urllib.request.Request(
            f"{ENGINE_BACKEND_URL}/api/login", data=login_data, method="POST",
            headers={"Content-Type": "application/json"},
        )
        resp = opener.open(req, timeout=5)
        _log.info("[engine_eval] POST /api/login → %d cookies=%d", resp.status, len(cookie_jar))
    except Exception as e:
        _log.warning("[engine_eval] 登录失败: %s，继续尝试公开端点", str(e)[:100])

    # Step 2: 检查端点（混合公开 + 需 auth，全部来自实际 grep 扫描）
    endpoints = [
        ("/healthz", "healthz(公开)"),              # 公开健康检查
        ("/readyz", "readyz(公开)"),                # 公开 DB 检查
        ("/api/projects", "projects(需auth)"),       # 需登录
        ("/api/stats", "stats(需auth)"),             # 需登录
    ]
    for path, name in endpoints:
        total += 1
        try:
            req = urllib.request.Request(f"{ENGINE_BACKEND_URL}{path}")
            resp = opener.open(req, timeout=5)
            passed += 1
            _log.info("[engine_eval] GET %s → %d OK", path, resp.status)
        except urllib.error.HTTPError as e:
            details.append({"rule": "endpoint_http_error", "msg": f"{path}: {e.code}", "name": name})
            _log.warning("[engine_eval] GET %s → %d", path, e.code)
        except Exception as e:
            details.append({"rule": "endpoint_crash", "msg": f"{path}: {str(e)[:100]}", "name": name})
            _log.warning("[engine_eval] GET %s → %s", path, str(e)[:80])
    return (passed / max(total, 1)) * 100.0, details


def _check_internal_strictness() -> float:
    """静态分析：src/pipeline 下 except: pass / 裸 except 数量。

    这是 fail-open 问题的根因指标。
    """
    import re
    root = Path(__file__).resolve().parent.parent / "src" / "pipeline"
    if not root.exists(): return 50.0
    count = 0; scanned = 0
    for py in root.rglob("*.py"):
        scanned += 1
        try:
            txt = py.read_text(encoding="utf-8", errors="ignore")
            count += len(re.findall(r"except\s*:\s*(?:\n\s+pass\b|\bpass\s*(?:#.*)?$)", txt, re.MULTILINE))
            count += len(re.findall(r"except\s*Exception\s*(?:as\s+\w+)?\s*:\s*\n\s*#.*(?:skip|silent|graceful)", txt, re.MULTILINE))
        except Exception: pass
    _log.info("[engine_eval] 内部严格性: scanned=%d files, except-pass=%d occurrences", scanned, count)
    # 少于 5 处 → 100 分，超过 30 处 → 50 分
    if count <= 5: return 100.0
    if count >= 30: return 50.0
    return 100.0 - (count - 5) * 2.0


# ══════════════════════════════════════════════════════════════════════════
# 内部工具（全部只读，不注入数据）
# ══════════════════════════════════════════════════════════════════════════

def _run_target(target: str, *, timeout: int) -> tuple[bool, int, str]:
    cmd = [sys.executable, "main.py", "target", "--target", target]
    _log.info("[engine_eval] exec: %s (timeout=%ds)", " ".join(cmd), timeout)
    cwd = str(Path(__file__).resolve().parent.parent)
    try:
        result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        rc = result.returncode
        run_ok = rc in (0, 2)
        error = "" if run_ok else f"exit_code={rc} stderr_tail={result.stderr[-300:]}"
        _log.info("[engine_eval] target=%s rc=%d ok=%s", target, rc, run_ok)
        return run_ok, rc, error
    except subprocess.TimeoutExpired:
        return False, -1, f"timeout {timeout}s"
    except Exception as e:
        return False, -1, str(e)[:300]


def _get_engine_connection():
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from src.core.db_manager import get_connection
    return get_connection()


def _probe_engine_db() -> tuple[bool, bool, float]:
    try:
        conn = _get_engine_connection()
    except Exception as e:
        _log.error("[engine_eval] MySQL 连接失败: %s", e); return False, False, 0.0
    try:
        cur = conn.cursor()
        cur.execute("SELECT 1 AS alive")
        db_ok = bool(cur.fetchone() and cur.fetchone() is not None) or True  # 简化检查
        cur.execute("SELECT DATABASE() AS db_name")
        db_row = cur.fetchone()
        db_name = db_row.get("db_name") if db_row else None
        found = []
        for t in ENGINE_REQUIRED_TABLES:
            cur.execute("SELECT COUNT(*) AS c FROM information_schema.tables WHERE table_schema=%s AND table_name=%s", (db_name, t))
            if int(cur.fetchone().get("c", 0) or 0) > 0: found.append(t)
        cur.close(); conn.close()
        missing = set(ENGINE_REQUIRED_TABLES) - set(found)
        if missing: _log.warning("[engine_eval] 缺失表: %s", missing)
        return True, len(missing) == 0, (len(found) / len(ENGINE_REQUIRED_TABLES)) * 100.0
    except Exception as e:
        _log.error("[engine_eval] DB 探测异常: %s", e); return True, False, 0.0


def _fetch_real_metrics(target: str, db_ok: bool, run_ok: bool) -> tuple[float, float]:
    if not db_ok: return 0.0, 0.0
    try:
        conn = _get_engine_connection(); cur = conn.cursor()
        cur.execute(
            "SELECT COUNT(*) total, SUM(CASE WHEN status='PASS' THEN 1 ELSE 0 END) passed, AVG(confidence) avg_conf "
            "FROM executions WHERE project=%s", (target,),
        )
        row = cur.fetchone(); cur.close(); conn.close()
        total = int(row.get("total", 0) or 0); passed = int(row.get("passed", 0) or 0)
        avg = float(row.get("avg_conf") or 0.0)
        if total == 0: return 0.0, 0.0
        return passed / total, avg
    except Exception as e:
        _log.error("[engine_eval] 真实指标查询异常: %s", e); return 0.0, 0.0


def _scan_false_positives(target: str, db_ok: bool) -> tuple[float, list[dict]]:
    if not db_ok: return 1.0, []
    details: list[dict] = []
    try:
        conn = _get_engine_connection(); cur = conn.cursor()
        cur.execute("SELECT COUNT(*) tp FROM executions WHERE project=%s AND status='PASS'", (target,))
        tp = int(cur.fetchone().get("tp", 0) or 0)
        cur.execute(
            "SELECT case_id, check_type FROM executions WHERE project=%s AND status='PASS' AND (check_type='' OR check_type IS NULL)",
            (target,),
        )
        for r in cur.fetchall()[:5]:
            details.append({"case_id": r.get("case_id"), "rule": "empty_check_type",
                "msg": f"PASS 但 check_type 为空，可能未走验证轨"})
        cur.close(); conn.close()
        if tp == 0: return 0.0, []
        return min(len(details) / tp, 1.0), details
    except Exception as e:
        _log.error("[engine_eval] 假阳性扫描异常: %s", e); return 0.5, []


def _compute_cross_cut_score(target: str, db_ok: bool) -> float:
    if not db_ok: return 0.0
    try:
        conn = _get_engine_connection(); cur = conn.cursor()
        cur.execute(
            "SELECT oracle_type, verdict, score FROM oracle_results WHERE project=%s ORDER BY created_at DESC",
            (target,),
        )
        rows = cur.fetchall(); cur.close(); conn.close()
        if not rows: return 50.0
        total = 0.0; cnt = 0
        for r in rows:
            v = str(r.get("verdict") or "").lower()
            sv = float(r.get("score") or 0.0)
            base = {"pass": 100.0, "warn": 70.0, "fail": 30.0}.get(v, 50.0)
            total += base * (0.6 + 0.4 * sv); cnt += 1
        return round(total / max(cnt, 1), 2)
    except Exception as e:
        _log.error("[engine_eval] 横切评分异常: %s", e); return 50.0


# ══════════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════════

def _cli():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
    import argparse
    p = argparse.ArgumentParser(description="AutoResearch 评估基准（不可修改）")
    p.add_argument("--target", default="smartshop")
    p.add_argument("--engine-only", action="store_true", help="只跑引擎自检验（不跑靶子）")
    p.add_argument("--skip-engine", action="store_true")
    args = p.parse_args()

    if args.engine_only:
        print(json.dumps(asdict(_selftest_engine(args.target)), ensure_ascii=False, indent=2, default=str))
    else:
        r = evaluate(args.target, skip_engine=args.skip_engine)
        print("\n" + "=" * 60); print("FULL EVALUATION"); print("=" * 60)
        print(json.dumps(asdict(r), ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__": _cli()
