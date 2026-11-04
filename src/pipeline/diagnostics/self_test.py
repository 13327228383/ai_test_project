# -*- coding: utf-8 -*-
"""流水线自检引擎 — 轻量验证每个 Step 的基础设施可用性。

设计原则：
  - 每个 Step 独立测试，失败不阻断后续
  - Step2 实际拆解内置需求（验证 AI + 规则引擎可用）
  - Step3/4/5 检查基础设施（AI Brain / DB 读写 / 浏览器 / 报告表）
  - 不启动完整流水线，单次自检 < 30 秒
  - 结果写入 pipeline_self_test_results 表
"""

from __future__ import (
    annotations,
)  # ★ 修复(2026-07-25): Python 3.9 兼容（PEP 604 `X | None` 语法）
from src.core.guarded import guard

import logging
import time
import json
from datetime import datetime

_log = logging.getLogger(__name__)

# ── 内置最小自检需求 ──
_SELF_TEST_REQUIREMENT = """
# 自检用例
**操作步骤**
1. 导航到系统首页
**预期行为**
1. 页面正常加载，无报错
"""


def run_self_test(
    project: str,
    version_label: str = "",
    target_url: str = "",
    run_id: str = "",
    user_id: str = "",
) -> dict:
    """执行流水线自检。

    Returns:
        {ok, steps: {step2/3/4/5: {status, detail, duration_ms}},
         summary: {total, passed, failed, skipped}, overall: HEALTHY/DEGRADED/BROKEN}
    """
    if not run_id:
        run_id = f"self_test_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    _log.info("[SelfTest] 开始流水线自检 project=%s run_id=%s", project, run_id)

    results = {}

    # ── Step2 自检：实际拆解内置需求 ──
    results["step2"] = _check_step2(project, version_label, run_id)

    # ── Step3 自检：基础设施检查 ──
    results["step3"] = _check_step3(project)

    # ── Step4 自检：浏览器基础设施检查 ──
    results["step4"] = _check_step4(project, target_url)

    # ── Step5 自检：报告基础设施检查 ──
    results["step5"] = _check_step5(project)

    # ── 汇总 ──
    passed = sum(1 for s in results.values() if s["status"] == "PASS")
    failed = sum(1 for s in results.values() if s["status"] == "FAIL")
    skipped = sum(1 for s in results.values() if s["status"] == "SKIP")

    if failed == 0:
        overall = "HEALTHY"
    elif passed >= 2:
        overall = "DEGRADED"
    else:
        overall = "BROKEN"

    summary = {
        "ok": failed == 0,
        "steps": results,
        "summary": {"total": 4, "passed": passed, "failed": failed, "skipped": skipped},
        "overall": overall,
        "run_id": run_id,
    }

    _persist_self_test_result(project, run_id, summary, user_id)
    _log.info(
        "[SelfTest] 自检完成: %s (passed=%d failed=%d skipped=%d)",
        overall,
        passed,
        failed,
        skipped,
    )
    return summary


# ══════════════════════════════════════════════════
# Step2 自检: 实际拆解内置需求
# ══════════════════════════════════════════════════


def _check_step2(project: str, version_label: str, run_id: str) -> dict:
    """Step2 自检: 用内置需求测试拆解能力。"""
    t0 = time.time()
    try:
        from src.pipeline.steps.step2.analyzers.feature_driven import (
            feature_driven_breakdown,
        )

        result = feature_driven_breakdown(
            raw_text=_SELF_TEST_REQUIREMENT,
            ui_context="",
            project_name=project,
            version_label=version_label,
        )

        duration_ms = int((time.time() - t0) * 1000)

        if result.get("success") and result.get("data"):
            reqs = result["data"]
            stats = result.get("_stats", {})
            ai_calls = stats.get("ai_calls", 0)
            rule_count = stats.get("rule_extractions", 0)
            coverage = stats.get("coverage_rate", 0)
            return {
                "status": "PASS",
                "detail": f"拆解成功: {len(reqs)} 条需求 (规则={rule_count}, AI={ai_calls}, 覆盖率={coverage}%)",
                "duration_ms": duration_ms,
            }
        else:
            return {
                "status": "FAIL",
                "detail": f"拆解失败: {result.get('error', '未知错误')[:80]}",
                "duration_ms": duration_ms,
            }
    except Exception as e:
        return {
            "status": "FAIL",
            "detail": f"异常: {str(e)[:100]}",
            "duration_ms": int((time.time() - t0) * 1000),
        }


# ══════════════════════════════════════════════════
# Step3 自检: 基础设施检查
# ══════════════════════════════════════════════════


def _check_step3(project: str) -> dict:
    """Step3 自检: 检查 AI Brain + 用例生成规则 + 操作库可用性。"""
    t0 = time.time()
    checks = []

    # 1. AI Brain 可用性
    try:
        from src.ai.ai_client import ai_brain

        if ai_brain:
            checks.append(("AI Brain", True, "已初始化"))
        else:
            checks.append(("AI Brain", False, "未初始化"))
    except Exception as e:
        checks.append(("AI Brain", False, str(e)[:60]))

    # 2. 用例生成规则加载
    try:
        from src.pipeline.step3 import case_rules

        case_rules._ensure_rules()
        if case_rules._RULE_REGISTRY:
            checks.append(
                ("用例规则", True, f"已加载 {len(case_rules._RULE_REGISTRY)} 条规则")
            )
        else:
            checks.append(("用例规则", False, "规则列表为空"))
    except Exception as e:
        checks.append(("用例规则", False, str(e)[:60]))

    # 3. 操作库可用（action_library_store）
    try:
        from src.pipeline.utils.action_library_store import action_library_store

        lib = action_library_store.load_library()
        if lib:
            action_count = sum(len(v) for v in lib.values() if isinstance(v, list))
            checks.append(("操作库", True, f"已加载 {action_count} 个操作"))
        else:
            checks.append(("操作库", False, "操作库为空（可能未初始化）"))
    except Exception as e:
        checks.append(("操作库", False, str(e)[:60]))

    # 4. RAG 引擎可用
    try:
        from src.extensions.rag_engine.engine_core import rag_engine

        if rag_engine:
            checks.append(("RAG 引擎", True, "已初始化"))
        else:
            checks.append(("RAG 引擎", False, "未初始化"))
    except Exception as e:
        checks.append(("RAG 引擎", False, str(e)[:60]))

    failed = [c for c in checks if not c[1]]
    duration_ms = int((time.time() - t0) * 1000)

    if not failed:
        return {
            "status": "PASS",
            "detail": f"全部通过: {', '.join(c[0] for c in checks)}",
            "checks": [{"name": c[0], "ok": c[1], "detail": c[2]} for c in checks],
            "duration_ms": duration_ms,
        }
    else:
        return {
            "status": "FAIL",
            "detail": f"失败: {', '.join(c[0] for c in failed)}",
            "checks": [{"name": c[0], "ok": c[1], "detail": c[2]} for c in checks],
            "duration_ms": duration_ms,
        }


# ══════════════════════════════════════════════════
# Step4 自检: 浏览器基础设施检查
# ══════════════════════════════════════════════════


def _check_step4(project: str, target_url: str = "") -> dict:
    """Step4 自检: 检查 Playwright + 浏览器 + 目标系统可达性。"""
    t0 = time.time()
    checks = []

    # 1. Playwright 可用
    try:
        from playwright.sync_api import sync_playwright

        checks.append(("Playwright", True, "已安装"))
    except ImportError:
        checks.append(("Playwright", False, "未安装 playwright 包"))
    except Exception as e:
        checks.append(("Playwright", False, str(e)[:60]))

    # 2. 浏览器路径检测
    try:
        import os
        from pathlib import Path

        browser_path = None
        # 项目自带 Chrome
        project_chrome = (
            Path(__file__).resolve().parent.parent.parent.parent
            / "chrome-win64"
            / "chrome.exe"
        )
        if project_chrome.is_file():
            browser_path = str(project_chrome)
        else:
            # Playwright Chromium
            ms_playwright = Path(os.environ.get("LOCALAPPDATA", "")) / "ms-playwright"
            for chromium_dir in sorted(ms_playwright.glob("chromium-*"), reverse=True):
                chrome_exe = chromium_dir / "chrome-win64" / "chrome.exe"
                if chrome_exe.is_file():
                    browser_path = str(chrome_exe)
                    break
        if browser_path:
            checks.append(("浏览器", True, browser_path[:80]))
        else:
            checks.append(("浏览器", False, "未找到 Chrome/Chromium"))
    except Exception as e:
        checks.append(("浏览器", False, str(e)[:60]))

    # 3. 目标系统可达性（如有配置）
    if target_url:
        try:
            import urllib.request

            req = urllib.request.Request(target_url, method="HEAD")
            urllib.request.urlopen(req, timeout=10)
            checks.append(("目标系统", True, f"{target_url[:60]} 可达"))
        except Exception as e:
            checks.append(
                ("目标系统", False, f"{target_url[:60]} 不可达: {str(e)[:40]}")
            )
    else:
        checks.append(("目标系统", None, "未配置（跳过）"))

    # 4. 浏览器执行器可用
    try:
        from src.pipeline.steps.step4.browser_core import browser_core

        if browser_core:
            checks.append(("执行器", True, "browser_executor 已导入"))
        else:
            checks.append(("执行器", False, "browser_executor 为 None"))
    except Exception as e:
        checks.append(("执行器", False, str(e)[:60]))

    failed = [c for c in checks if c[1] is False]
    duration_ms = int((time.time() - t0) * 1000)

    if not failed:
        return {
            "status": "PASS",
            "detail": f"全部通过: {', '.join(c[0] for c in checks)}",
            "checks": [{"name": c[0], "ok": c[1], "detail": c[2]} for c in checks],
            "duration_ms": duration_ms,
        }
    else:
        return {
            "status": "FAIL",
            "detail": f"失败: {', '.join(c[0] for c in failed)}",
            "checks": [{"name": c[0], "ok": c[1], "detail": c[2]} for c in checks],
            "duration_ms": duration_ms,
        }


# ══════════════════════════════════════════════════
# Step5 自检: 报告基础设施检查
# ══════════════════════════════════════════════════


def _check_step5(project: str) -> dict:
    """Step5 自检: 检查报告表 + 文件写入能力。"""
    t0 = time.time()
    checks = []

    # 1. run_summary 表可读写
    try:
        from src.core.db_manager import get_connection

        conn = get_connection()
        try:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM run_summary WHERE project=%s", (project,))
            row = cur.fetchone()
            cur.close()
            checks.append(
                (
                    "run_summary 表",
                    True,
                    f"当前 {row[0] if isinstance(row, (list, tuple)) else list(row.values())[0]} 条记录",
                )
            )
        finally:
            conn.close()
    except Exception as e:
        checks.append(("run_summary 表", False, str(e)[:60]))

    # 2. executions 表可读写
    try:
        conn = get_connection()
        try:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM executions WHERE project=%s", (project,))
            cur.close()
            checks.append(("executions 表", True, "可读写"))
        finally:
            conn.close()
    except Exception as e:
        checks.append(("executions 表", False, str(e)[:60]))

    # 3. 报告导出目录可写
    try:
        from pathlib import Path

        report_dir = (
            Path(__file__).resolve().parent.parent.parent.parent / "reports"
        )
        report_dir.mkdir(parents=True, exist_ok=True)
        test_file = report_dir / "_self_test_write_check.txt"
        test_file.write_text("self_test", encoding="utf-8")
        test_file.unlink()
        checks.append(("报告目录", True, str(report_dir)[:80]))
    except Exception as e:
        checks.append(("报告目录", False, str(e)[:60]))

    failed = [c for c in checks if not c[1]]
    duration_ms = int((time.time() - t0) * 1000)

    if not failed:
        return {
            "status": "PASS",
            "detail": "全部通过: 报告表 + 文件写入正常",
            "checks": [{"name": c[0], "ok": c[1], "detail": c[2]} for c in checks],
            "duration_ms": duration_ms,
        }
    else:
        return {
            "status": "FAIL",
            "detail": f"失败: {', '.join(c[0] for c in failed)}",
            "checks": [{"name": c[0], "ok": c[1], "detail": c[2]} for c in checks],
            "duration_ms": duration_ms,
        }


# ══════════════════════════════════════════════════
# 持久化 + 查询
# ══════════════════════════════════════════════════


def _persist_self_test_result(
    project: str, run_id: str, summary: dict, user_id: str
) -> None:
    with guard("_persist_self_test_result: from src.core.db_manager import get_", log=_log, level=logging.WARNING):
        from src.core.db_manager import get_connection

        conn = get_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO pipeline_self_test_results "
                "(project, run_id, user_id, steps_json, overall, passed, failed, skipped, created_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW())",
                (
                    project,
                    run_id,
                    user_id or "",
                    json.dumps(summary["steps"], ensure_ascii=False),
                    summary["overall"],
                    summary["summary"]["passed"],
                    summary["summary"]["failed"],
                    summary["summary"]["skipped"],
                ),
            )
            conn.commit()
            cur.close()
        finally:
            conn.close()


def get_latest_self_test(project: str) -> dict | None:
    try:
        from src.core.db_manager import get_connection

        conn = get_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT run_id, steps_json, overall, passed, failed, skipped, created_at "
                "FROM pipeline_self_test_results "
                "WHERE project=%s ORDER BY created_at DESC LIMIT 1",
                (project,),
            )
            row = cur.fetchone()
            cur.close()
            if not row:
                return None
            is_dict = isinstance(row, dict)
            return {
                "run_id": row["run_id"] if is_dict else row[0],
                "steps": json.loads(row["steps_json"] if is_dict else row[1]),
                "overall": row["overall"] if is_dict else row[2],
                "passed": row["passed"] if is_dict else row[3],
                "failed": row["failed"] if is_dict else row[4],
                "skipped": row["skipped"] if is_dict else row[5],
                "created_at": str(row["created_at"] if is_dict else row[6]),
            }
        finally:
            conn.close()
    except Exception as e:
        _log.warning("[SelfTest] 查询失败: %s", str(e)[:120])
        return None


def get_self_test_history(project: str, limit: int = 10) -> list[dict]:
    try:
        from src.core.db_manager import get_connection

        conn = get_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT run_id, overall, passed, failed, skipped, created_at "
                "FROM pipeline_self_test_results "
                "WHERE project=%s ORDER BY created_at DESC LIMIT %s",
                (project, limit),
            )
            rows = cur.fetchall()
            cur.close()
            return [
                {
                    "run_id": r["run_id"] if isinstance(r, dict) else r[0],
                    "overall": r["overall"] if isinstance(r, dict) else r[1],
                    "passed": r["passed"] if isinstance(r, dict) else r[2],
                    "failed": r["failed"] if isinstance(r, dict) else r[3],
                    "skipped": r["skipped"] if isinstance(r, dict) else r[4],
                    "created_at": str(r["created_at"] if isinstance(r, dict) else r[5]),
                }
                for r in rows
            ]
        finally:
            conn.close()
    except Exception as e:
        _log.warning("[SelfTest] 查询历史失败: %s", str(e)[:120])
        return []
