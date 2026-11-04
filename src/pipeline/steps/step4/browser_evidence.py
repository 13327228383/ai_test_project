# -*- coding: utf-8 -*-
"""browser_evidence.py — 测试用例证据记录与截图方法。从 browser_core.py 拆分。"""

import json
from src.core.guarded import guard
import logging
from datetime import datetime
from typing import Any

_log = logging.getLogger(__name__)

from src.core.config_loader import config


def _parse_checks(case_meta: dict[str, Any]) -> list[dict[str, Any]]:
    """从 case_meta 提取 checks[]（兼容 JSON 字符串）。"""
    checks = (case_meta or {}).get("checks") or []
    if isinstance(checks, str):
        try:
            checks = json.loads(checks)
        except (json.JSONDecodeError, ValueError):
            checks = []
    return checks if isinstance(checks, list) else []


def _parse_actions(case_meta: dict[str, Any]) -> list[dict[str, Any]]:
    """从 case_meta 提取 actions[]（兼容 JSON 字符串）。"""
    actions = (case_meta or {}).get("actions") or []
    if isinstance(actions, str):
        try:
            actions = json.loads(actions)
        except (json.JSONDecodeError, ValueError):
            actions = []
    return actions if isinstance(actions, list) else []


def _extract_criteria(case_meta: dict[str, Any]) -> list[str]:
    """v3.0: 从 title + checks[] 提取验收标准。

    checks[{check, expect}] → 每条 check+expect 作为一个验收条件；
    title 作为基准条件置顶。
    """
    criteria = []
    what = str((case_meta or {}).get("title") or "").strip()
    if what:
        criteria.append(what)

    checks = _parse_checks(case_meta)
    for chk in checks:
        if not isinstance(chk, dict):
            continue
        # check 字段可能是 dict（如 {method, path}）或 str
        check_data = chk.get("check", "")
        if isinstance(check_data, dict):
            criteria.append(json.dumps(check_data, ensure_ascii=False))
        elif check_data and str(check_data).strip():
            criteria.append(str(check_data).strip())
        # expect 期望值也作为验收条件
        expect = chk.get("expect", "")
        if expect and str(expect).strip():
            criteria.append(str(expect).strip())
    return criteria


async def record_intent_evidence(
    engine,
    case_id: str,
    case_meta: dict[str, Any],
    passed: bool,
    step_results: list[dict[str, Any]],
    extract_keywords_fn=None,
):
    """把测试用例执行证据写回 test_cases.payload_json，作为 Step5 和追溯链路的数据源。
    v3.0: 证据来源于 title + checks[]。
    """
    if extract_keywords_fn is None:
        from src.pipeline.steps.step4.browser_verification import extract_keywords

        extract_keywords_fn = extract_keywords

    criteria = _extract_criteria(case_meta)
    page_url = ""
    page_title = ""
    page_text = ""
    password_count = 0
    try:
        if engine and engine._page:
            page_url = engine._page.url or ""
            page_title = await engine._page.title() or ""
            page_text = (
                await engine._page.evaluate("() => document.body.innerText || ''") or ""
            )
            try:
                password_count = await engine._page.locator(
                    'input[type="password"]'
                ).count()
            except Exception as _e:
                _log.debug(
                    "[browser_evidence] record_intent_evidence 异常: %s",
                    _e,
                    exc_info=True,
                )
                password_count = 0
    except Exception as _pass_e:
        _log.warning("browser_evidence.py:85 异常被吞: %s", _pass_e, exc_info=True)
    text_l = ("%s\n%s\n%s" % (page_text, page_title, page_url)).lower()
    evidence_items = [
        {
            "type": "url_state",
            "value": page_url,
            "matched": bool(page_url and page_url != "about:blank"),
        },
        {"type": "page_title", "value": page_title, "matched": bool(page_title)},
    ]
    for c in criteria[:8]:
        cs = str(c or "").strip()
        if not cs:
            continue
        kws = extract_keywords_fn(cs)
        matched = any(w.lower() in text_l for w in kws)
        if (
            not matched
            and "登录表单" in cs
            and ("消失" in cs or "未显示" in cs or "未发现" in cs)
        ):
            matched = password_count == 0
        if not matched and ("受保护首页" in cs or "首页" in cs):
            # ★ P0-01: 业务端口走 config.target_success_url_suffix 配置（项目级可覆盖）
            try:
                success_suffix = str(
                    getattr(config, "target_success_url_suffix", ":5001")
                )
            except Exception as _e:
                _log.debug("[browser_evidence] 非关键异常: %s", _e, exc_info=True)
                success_suffix = ":5001"
            title_keywords = []
            try:
                title_keywords = list(
                    getattr(config, "target_protected_title_keywords", [])
                    or ["管理系统"]
                )
            except Exception as _e:
                _log.debug("[browser_evidence] 非关键异常: %s", _e, exc_info=True)
                title_keywords = ["管理系统"]
            matched = bool(
                page_url and page_url.rstrip("/").endswith(success_suffix)
            ) or (page_title and any(kw in page_title for kw in title_keywords))
        evidence_items.append(
            {"type": "acceptance_criterion", "value": cs, "matched": matched}
        )
    hard_verified = any(
        isinstance(r, dict) and r.get("_hard_verified") for r in step_results
    )
    # v3.0: case_meta IS the case（不再嵌套 intent_schema 子对象）
    meta = case_meta or {}
    verdict = {
        "event": meta.get("title") or meta.get("title", ""),
        "actor": meta.get("type", ""),
        "location": meta.get("module") or meta.get("location", ""),
        "passed": bool(passed and hard_verified),
        "status": "PASS" if passed and hard_verified else "MANUAL_REVIEW",
        "evidence_items": evidence_items,
        "hard_verified": bool(hard_verified),
        "recorded_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    with guard("record_intent_evidence: from src.pipeline.utils.case_store i", log=_log, level=logging.WARNING):
        from src.pipeline.utils.case_store import case_store

        current = case_store.get_intent(case_id) or {}
        payload = dict(current.get("payload") or {})
        payload["last_case_verdict"] = verdict
        # v3.0: 直接存储 case_meta 作为 payload（不再包 intent_schema 子对象）
        payload["case_meta"] = meta
        case_store.update_intent(
            case_id,
            {"payload_json": json.dumps(payload, ensure_ascii=False, default=str)},
        )
        _log.info(
            "[Evidence] %s verdict=%s evidence=%d",
            case_id,
            verdict["status"],
            len(evidence_items),
        )
        if verdict["status"] == "PASS" and meta:
            with guard("record_intent_evidence: from src.pipeline.steps.step4.capabi", log=_log, level=logging.WARNING):
                from src.pipeline.steps.step4.capability_cache import capability_cache

                impl = {
                    "type": "step4_runtime_success",
                    "case_id": case_id,
                    "page_url": page_url,
                    "page_title": page_title,
                    "hard_verified": bool(hard_verified),
                    "note": "动作是运行时实现，测试资产以 case_meta 和 evidence 为准。",
                }
                capability_cache.record_success(meta, verdict, implementation=impl)


def record_intent_dominance_metrics(
    case_id: str,
    case_meta: dict[str, Any],
    step_results: list[dict[str, Any]],
    cfg=None,
) -> None:
    """记录 Step4 用例主导度（v3.0: checks[] + actions[] 替代旧 verification_plan 维度）。

    指标目的不是惩罚 recipe/cache，而是识别"只有动作成功、没有证据裁决"的滑坡风险。
    """
    _cfg = cfg or config
    with guard("record_intent_dominance_metrics: from src.backend.state import _threa", log=_log):
        from src.backend.state import _thread_run_id
        from src.extensions.metrics_collector import metrics_collector

        run_id = getattr(_thread_run_id, "value", "") or ""
        project = (
            getattr(_cfg, "project_name", "") or getattr(_cfg, "project", "") or ""
        )
        if not run_id or not project:
            return

        # v3.0: 用 checks[] 替代 verification_plan，用 actions[] 替代 intent_schema
        checks = _parse_checks(case_meta)
        actions = _parse_actions(case_meta)
        has_checks = len(checks) > 0
        has_actions = len(actions) > 0

        total_steps = len(step_results or [])
        recipe_hits = sum(
            1
            for r in (step_results or [])
            if isinstance(r, dict) and r.get("_tool_recipe_hit")
        )
        recipe_verified = sum(
            1
            for r in (step_results or [])
            if isinstance(r, dict) and r.get("_recipe_intent_verified")
        )
        hard_verified = sum(
            1
            for r in (step_results or [])
            if isinstance(r, dict) and r.get("_hard_verified")
        )
        recipe_only_pass = max(0, recipe_hits - recipe_verified)
        verdict_recorded = 1 if hard_verified > 0 else 0
        score = 0.0
        score += 30.0 if has_actions else 0.0
        score += 30.0 if has_checks else 0.0
        score += 30.0 if verdict_recorded else 0.0
        if recipe_only_pass:
            score -= min(30.0, recipe_only_pass * 15.0)
        if total_steps and hard_verified == total_steps:
            score += 10.0
        score = max(0.0, min(100.0, round(score, 1)))

        metrics_collector.record_intent_dominance(
            project=project,
            run_id=run_id,
            case_id=case_id,
            score=score,
            has_intent_schema=has_actions,  # v3.0 兼容：内部映射为 actions
            has_verification_plan=has_checks,  # v3.0 兼容：内部映射为 checks
            verdict_recorded=bool(verdict_recorded),
            recipe_hits=recipe_hits,
            recipe_only_pass=recipe_only_pass,
            total_steps=total_steps,
        )
        _log.info(
            "[CaseDominance] %s score=%.1f recipe_only=%d/%d",
            case_id,
            score,
            recipe_only_pass,
            max(recipe_hits, 1),
        )


async def save_fail_screenshot(engine, case_id, step_num, cfg=None):
    """截图直接写入 MySQL execution_screenshots 表（MEDIUMBLOB），不再落盘 PNG。"""
    _cfg = cfg or config
    with guard("save_fail_screenshot: screenshot = await engine.get_screen", log=_log, level=logging.ERROR):
        screenshot = await engine.get_screenshot()
        if not screenshot:
            return
        from src.backend.state import _thread_run_id
        from src.core.db_manager import get_connection

        rid = getattr(_thread_run_id, "value", "") or ""
        project_name = getattr(_cfg, "project_name", "") or ""
        conn = get_connection()
        try:
            conn.execute(
                "INSERT INTO execution_screenshots "
                "(project, case_id, run_id, step_num, image_data, file_size, created_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, NOW())",
                (
                    project_name,
                    str(case_id),
                    str(rid),
                    int(step_num),
                    screenshot,
                    len(screenshot),
                ),
            )
            conn.commit()
        finally:
            try:
                conn.close()
            except Exception as _pass_e:
                _log.warning(
                    "browser_evidence.py:234 异常被吞: %s", _pass_e, exc_info=True
                )
        _log.info(
            "[SS] 截图已写入 MySQL: case=%s step=%s size=%d",
            case_id,
            step_num,
            len(screenshot),
        )


# ★ 每个用例终态截图：复用既有 execution_screenshots 表（schema.sql 已定义，
#   browser_evidence.save_fail_screenshot / cleanup_manager / routes 均依赖其
#   project, case_id, run_id, step_num, image_data, file_size, created_at 列）。
#   因此不新建冲突表，而是确保「表存在 + 含任务要求的 status/version_id 列」。
_SCREENSHOT_TABLE_ENSURED = False


def ensure_screenshot_table():
    """确保 execution_screenshots 表存在且含 status/version_id 列（幂等）。

    - 表不存在 → 按兼容 schema 建表（含 status/version_id）。
    - 表已存在 → 先查 information_schema.columns，缺失列才 ALTER 增补。
    """
    global _SCREENSHOT_TABLE_ENSURED
    if _SCREENSHOT_TABLE_ENSURED:
        return
    with guard("ensure_screenshot_table: from src.core.db_manager import get_", log=_log, level=logging.WARNING):
        from src.core.db_manager import get_connection

        conn = get_connection()
        try:
            _rows = conn.execute(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = DATABASE() AND table_name = 'execution_screenshots' "
                "LIMIT 1"
            ).fetchall()
            if not _rows:
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS `execution_screenshots` ("
                    "  `id` BIGINT AUTO_INCREMENT PRIMARY KEY,"
                    "  `project` VARCHAR(128) NOT NULL DEFAULT '',"
                    "  `case_id` VARCHAR(64) NOT NULL DEFAULT '',"
                    "  `run_id` VARCHAR(64) NOT NULL DEFAULT '',"
                    "  `step_num` INT NOT NULL DEFAULT 0,"
                    "  `image_data` MEDIUMBLOB,"
                    "  `file_size` INT NOT NULL DEFAULT 0,"
                    "  `status` VARCHAR(16) DEFAULT NULL,"
                    "  `version_id` INT DEFAULT NULL,"
                    "  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP"
                    ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"
                )
                conn.commit()
                _SCREENSHOT_TABLE_ENSURED = True
                return
            _cols = {
                r["COLUMN_NAME"]
                for r in conn.execute(
                    "SELECT COLUMN_NAME FROM information_schema.columns "
                    "WHERE table_schema = DATABASE() AND table_name = 'execution_screenshots'"
                ).fetchall()
            }
            _alter = []
            if "status" not in _cols:
                _alter.append("ADD COLUMN `status` VARCHAR(16) DEFAULT NULL")
            if "version_id" not in _cols:
                _alter.append("ADD COLUMN `version_id` INT DEFAULT NULL")
            if _alter:
                conn.execute(
                    "ALTER TABLE execution_screenshots " + ", ".join(_alter)
                )
                conn.commit()
            _SCREENSHOT_TABLE_ENSURED = True
        finally:
            with guard("ensure_screenshot_table: conn.close()", log=_log):
                conn.close()


async def save_case_screenshot(
    engine,
    case_id,
    status,
    project=None,
    run_id=None,
    version_id=None,
    step_num=0,
):
    """每个用例结束都落一张终态截图到 execution_screenshots（复用既有表，记录 status/version_id）。

    在用例 PASS/BLOCKED/FAIL 判定之后、浏览器 page 关闭之前调用：
    - 调用 engine.get_screenshot() 取 PNG 字节；
    - 幂等确保表/列存在后写入 BLOB。
    """
    with guard("save_case_screenshot: screenshot = await engine.get_screen", log=_log, level=logging.ERROR):
        screenshot = await engine.get_screenshot()
        if not screenshot:
            _log.warning(
                "[SS] 用例终态截图为空，跳过写入: case=%s status=%s page=%s",
                case_id,
                status,
                bool(getattr(engine, "_page", None)),
            )
            return
        ensure_screenshot_table()
        from src.backend.state import _thread_run_id
        from src.core.db_manager import get_connection
        from src.users.audit import _active_version_id

        rid = run_id or (getattr(_thread_run_id, "value", "") or "")
        project_name = project or ""
        vid = version_id
        if vid is None and project_name:
            try:
                vid = _active_version_id(project_name)
            except Exception:
                vid = None
        conn = get_connection()
        try:
            conn.execute(
                "INSERT INTO execution_screenshots "
                "(project, case_id, run_id, step_num, image_data, file_size, status, version_id, created_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW())",
                (
                    project_name,
                    str(case_id),
                    str(rid),
                    int(step_num or 0),
                    screenshot,
                    len(screenshot),
                    str(status or "UNKNOWN"),
                    int(vid) if vid is not None else None,
                ),
            )
            conn.commit()
        finally:
            with guard("save_case_screenshot: conn.close()", log=_log):
                conn.close()
        _log.info(
            "[SS] 用例终态截图已写入: case=%s status=%s size=%d",
            case_id,
            status,
            len(screenshot),
        )
