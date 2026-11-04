# -*- coding: utf-8 -*-
from __future__ import annotations

"""
browser_intent_parser._steps — 步骤处理相关函数
=================================================
normalize_steps, steps_from_case, collapse_steps_to_case, format_steps
"""

import logging
import re
from typing import Any

_log = logging.getLogger(__name__)


def _try_capability_step(case: dict, config) -> dict | None:
    """尝试检测业务能力并生成确定性执行步骤。成功返回 step，失败返回 None。"""
    what = str(case.get("title") or case.get("case_id", "")).strip()
    if not what:
        return None
    try:
        from src.pipeline.steps.step4.plugins.business_capabilities import (
            business_capabilities as _bc,
        )

        _bc_result = _bc.detect_capability([], what, intent_schema={"event": what})
        if _bc_result and isinstance(_bc_result, tuple) and _bc_result[0]:
            hint = _bc_result[0]
            _capability_map = {
                "search_or_filter": "_try_search_or_filter",
                "delete_record": "_try_delete_record",
                "export_data": "_try_export_data",
                "create_business_record": "_try_create_business_record",
                "login_with_password": "_try_login_with_password",
                "form_fill_and_submit": "_try_form_fill_and_submit",
                "data_query": "_try_data_query",
                "approval_flow": "_try_approval_flow",
                "list_batch_action": "_try_list_batch_action",
                "navigate_menu": "_try_navigate_menu",
                "pagination_control": "_try_pagination_control",
                "table_export": "_try_table_export",
            }
            _cap_method = _capability_map.get(hint)
            if _cap_method:
                _log.info("[Capability] 确定性路径: %s → %s", hint, what[:60])
                return {
                    "description": f"确定性能力路径: {what}",
                    "expected": "确定性能力路径执行",
                    "_capability_method": _cap_method,
                    "_capability_goal": what,
                }
    except Exception:
        _log.debug("[Capability] 业务能力检测失败（不影响主流程）")
        pass
    return None


def normalize_steps(steps: list) -> list:
    """支持结构化意图：若 step 含 actions/assertions，Step4 走意图驱动路径；否则走 AI 翻译路径。"""
    if not steps:
        return []
    normalized = []
    for s in steps:
        if isinstance(s, dict):
            normalized.append(s)
        elif isinstance(s, str):
            normalized.append({"description": s})
        else:
            normalized.append({"description": str(s)})
    return normalized


def format_steps(steps: list[dict]) -> str:
    lines = []
    for i, s in enumerate(steps):
        if isinstance(s, str):
            s = {"description": s}
        desc = s.get("description", str(s))
        action = s.get("action", "")
        expected = s.get("expected", "")
        line = "步骤%d: %s" % (i + 1, desc)
        if action:
            line += " [操作: %s]" % action
        if expected:
            line += " [预期: %s]" % expected
        lines.append(line)
    return "\n".join(lines)


def _build_check_list(checks: list[dict]) -> str:
    """将结构化 checks 格式化为 AI 可读的成功标准清单。"""
    lines = []
    for i, c in enumerate(checks):
        if not isinstance(c, dict):
            continue
        track = str(c.get("track", "ui")).upper()
        precision = str(c.get("precision", "fuzzy"))
        check = c.get("check", "")
        expect = c.get("expect", "")
        if isinstance(check, dict):
            check = str(check)[:120]
        check_str = str(check)[:120]
        expect_str = str(expect)[:80]
        tag = "🔒精确" if precision == "exact" else "🤖AI判定"
        lines.append(f"  {i+1}. [{track}] {tag} {check_str} → 预期: {expect_str}")
    return "\n".join(lines)


def _table_from_sql(sql) -> str:
    """从 SQL 文本中提取表名（sql 分支 db check 无独立 table 字段时用）。"""
    if not sql:
        return ""
    m = re.search(r"FROM\s+[`\"\[]?(\w+)", str(sql), re.IGNORECASE)
    return m.group(1) if m else ""


def _bridge_plan_field_check(
    db_checks: list[Any], verification_plan: Any
) -> list[Any]:
    """[已复核-DeepSeek-V3 2026-08-16] ADI e2e 桥接：把 verification_plan.db[] 的 field_check
    透传到运行时 db_checks，使浏览器主路径触发值级比较（结构与 _expand_db_value_assertions 产出一致）。

    fail-open：
      - verification_plan 缺失/非 dict / 无 field_check → 原样返回（行为不变）
      - 运行时 db check 已自带 field_check            → 不覆盖
      - 按 (table, where) 精确优先，回退到 table 匹配；无匹配 → 原样返回
    """
    if not isinstance(verification_plan, dict):
        return db_checks
    plan_db = verification_plan.get("db") or []
    if not isinstance(plan_db, list):
        return db_checks
    _fc_by_tw: dict[tuple, Any] = {}
    _fc_by_table: dict[str, Any] = {}
    for e in plan_db:
        if not isinstance(e, dict):
            continue
        fc = e.get("field_check")
        if isinstance(fc, dict) and fc:
            _t = e.get("table")
            _w = e.get("where") or ""
            if _t:
                _fc_by_table.setdefault(_t, fc)
                if _w:
                    _fc_by_tw[(_t, _w)] = fc
    if not _fc_by_table:
        return db_checks
    out: list[Any] = []
    for dc in db_checks:
        if isinstance(dc, dict) and "field_check" not in dc:
            _t = dc.get("table") or _table_from_sql(dc.get("sql", ""))
            _w = dc.get("where") or ""
            _fc = _fc_by_tw.get((_t, _w)) or _fc_by_table.get(_t)
            if _fc:
                dc = dict(dc)
                dc["field_check"] = _fc
        out.append(dc)
    return out


def _target_to_url_pattern(target: str) -> str:
    """把 plan.api[].target（"GET /path" 或 "/path"）归一化为 url_pattern（去 method 前缀）。"""
    if not target:
        return ""
    parts = str(target).strip().split(None, 1)
    if len(parts) == 2 and parts[0].upper() in (
        "GET",
        "POST",
        "PUT",
        "DELETE",
        "PATCH",
    ):
        return parts[1].strip()
    return str(target).strip()


def _bridge_plan_api_field_check(
    api_checks: list[Any], verification_plan: Any
) -> list[Any]:
    """[已复核-DeepSeek-V3 2026-08-16] ADI tail 桥接：把 verification_plan.api[] 的 field_check
    透传到运行时 api_checks，转成 api_response_body_check 值级断言（与 _expand_db_value_assertions
    的 db 轨升级同构；与 _bridge_plan_field_check 的 db 轨桥接对称）。

    fail-open：
      - verification_plan 缺失/非 dict / 无 field_check → 原样返回（行为不变）
      - 运行时 api check 已含同 (url_pattern, expected_field) 的 api_response_body_check → 不追加
      - field_check 字段缺 expect → 跳过该字段
      - plan api 项无 target → 跳过该条 field_check（无 url_pattern 无法执行值比较）
      - 未知 operator → 回落 eq（与 _check_response_body 的 fail-open 语义一致）
    """
    if not isinstance(verification_plan, dict):
        return api_checks
    plan_api = verification_plan.get("api") or []
    if not isinstance(plan_api, list):
        return api_checks
    _existing: set[tuple] = set()
    for ac in api_checks:
        if isinstance(ac, dict) and ac.get("type") == "api_response_body_check":
            _existing.add(
                (str(ac.get("url_pattern", "") or ""), str(ac.get("expected_field", "") or ""))
            )
    out = list(api_checks)
    for e in plan_api:
        if not isinstance(e, dict):
            continue
        fc = e.get("field_check")
        if not isinstance(fc, dict) or not fc:
            continue
        url_pattern = _target_to_url_pattern(e.get("target", "") or "")
        if not url_pattern:
            continue
        for _field, _spec in fc.items():
            _expected = _spec.get("expect") if isinstance(_spec, dict) else _spec
            if _expected is None or _expected == "":
                continue
            if (url_pattern, _field) in _existing:
                continue
            _operator = "eq"
            _tol = 0.0
            if isinstance(_spec, dict):
                _operator = str(_spec.get("operator") or "eq").strip().lower()
                if _operator not in (
                    "eq", "neq", "gte", "lte", "gt", "lt", "in", "range", "contains",
                ):
                    _operator = "eq"
                try:
                    _tol = float(
                        _spec.get("tolerance", _spec.get("precision", 0)) or 0
                    )
                except (TypeError, ValueError):
                    _tol = 0.0
            out.append(
                {
                    "type": "api_response_body_check",
                    "url_pattern": url_pattern,
                    "expected_field": _field,
                    "expected_value": _expected,
                    "operator": _operator,
                    "tolerance": _tol,
                }
            )
            _existing.add((url_pattern, _field))
    return out


def _build_structured_checks(
    checks: list[dict], verification_plan: Any = None
) -> tuple:
    """将 checks 拆分为 api_checks / db_checks / ui_checks 三个列表。"""
    api_checks, db_checks, ui_checks = [], [], []
    for c in checks:
        if not isinstance(c, dict):
            continue
        track = str(c.get("track", "")).strip().lower()
        precision = str(c.get("precision", "fuzzy")).strip()
        check_data = c.get("check", "")
        expect = c.get("expect", "")
        if track == "api":
            if isinstance(check_data, dict):
                # ★ ADI tail [已复核-DeepSeek-V3 2026-08-16]: dict check 缺 type 但携带
                #   url_pattern/expected_field/expected_value（Step3 API 值比较期望的常见形态）
                #   → 补 type="api_response_body_check"，使期望值稳定路由进运行时值比较，
                #   避免落 UNSUPPORTED_CHECK（dict check 不能走字符串默认 handler）。
                if not check_data.get("type") and (
                    check_data.get("expected_field") or check_data.get("json_path")
                ):
                    check_data = dict(check_data)
                    check_data["type"] = "api_response_body_check"
                api_checks.append(check_data)
            else:
                api_checks.append(str(check_data))
        elif track == "db":
            if isinstance(check_data, dict) and check_data.get("sql"):
                db_checks.append(
                    {
                        "sql": check_data["sql"],
                        "expect": str(expect),
                        "precision": precision,
                    }
                )
            elif isinstance(check_data, dict):
                # ★ 多轨修复(2026-08-08): DB check dict 透传，不转字符串
                #   否则 verify_db_ext 读不到 table/expected_count/operator 字段
                db_checks.append(check_data)
            else:
                db_checks.append(str(check_data))
        elif track == "ui":
            # ★ Fix14b (2026-07-07): 传递 assertion/target_hint，避免 _verify_ui_objective 丢失断言类型
            #   原 bug: 只传 precision/check/expect，丢失了 assertion（如 element_exists）
            #   导致 _verify_ui_objective 把所有字符串 check 统一当 text_contains 处理
            ui_item = {"precision": precision, "check": check_data, "expect": expect}
            if c.get("assertion"):
                ui_item["assertion"] = c.get("assertion")
            if c.get("target_hint"):
                ui_item["target_hint"] = c.get("target_hint")
            ui_checks.append(ui_item)
        else:
            _log.warning(
                "[Checks] 非标准 track 值 '%s'，已忽略该 check: %s",
                track,
                str(check_data)[:80],
            )
    # [已复核-DeepSeek-V3 2026-08-16] ADI e2e 桥接：把 plan 的 field_check 透传进运行时 db_checks
    db_checks = _bridge_plan_field_check(db_checks, verification_plan)
    # ★ ADI tail [已复核-DeepSeek-V3 2026-08-16]: plan 的 api field_check 桥接进运行时 api_checks
    api_checks = _bridge_plan_api_field_check(api_checks, verification_plan)
    return api_checks, db_checks, ui_checks


def _build_verify_step(checks: list[dict], verification_plan: Any = None) -> dict:
    """从 checks 构建验证步骤（确定性路径和探索路径共用）。

    ★ S45-001: checks 为空时跳过三轨验证（策略1容忍矛盾）。
    当人工超驰策略1且 checks 为空时，只执行 actions 不做三轨验证。
    verification_plan: 可选，ADI e2e 桥接用（把 plan 的 field_check 透传进运行时 db_checks）。
    """
    # ★ S45-001: checks 为空 → 跳过三轨验证
    if not checks:
        return {
            "description": "无检查点，跳过三轨验证",
            "action": "verify",
            "expected": "动作执行完成即视为通过",
            "_checks": [],
            "skip_verification": True,  # ★ S45-001: 标记跳过三轨验证
            "required_tracks": [],
        }
    api_checks, db_checks, ui_checks = _build_structured_checks(
        checks, verification_plan
    )
    verify_step = {
        "description": f"验证 {len(checks)} 个检查点",
        "action": "verify",
        "expected": "所有 check 通过",
        "_checks": checks,
    }
    if api_checks:
        verify_step["api_checks"] = api_checks
    if db_checks:
        verify_step["db_checks"] = db_checks
    if ui_checks:
        verify_step["ui_checks"] = ui_checks
    tracks = list(set(c.get("track", "ui") for c in checks if isinstance(c, dict)))
    # ★ 严守门禁：tracks 从 checks 实际内容提取，不强制注入 "ui"。
    #   但如果结构化后三轨 checks 全为空（checks 格式不合法），标记验证失败而非放行。
    if not api_checks and not db_checks and not ui_checks:
        _log.warning(
            "[_build_verify_step] checks 非空但结构化后三轨全空，checks 格式不合法: %s",
            str(checks)[:200],
        )
        verify_step["required_tracks"] = ["ui"]
        verify_step["ui_checks"] = [
            {
                "precision": "exact",
                "check": "用例checks格式不合法",
                "expect": "FAIL",
                "target_hint": "invalid_checks",
            }
        ]
    else:
        verify_step["required_tracks"] = tracks or ["ui"]
    return verify_step


def _build_basic_verify_step() -> dict:
    """策略3（纯动作，无 checks）的基础 UI 验证步骤 —— 唯一定义，与策略1/2的
    verify_step 同形状，保证三策略汇入同一条验证管道。

    ★ 必须发结构化 check：自然语言句子会被执行端当字面 needle（text_contains），
      页面上永不出现 → 结构性假 FAIL。基础验证的真实语义 = 页面主体渲染 +
      无错误提示，两条都用可执行 check 表达。
    """
    return {
        "description": "页面基础验证",
        "action": "verify",
        "expected": "页面正常，无报错，关键元素可见",
        "required_tracks": ["ui"],
        "ui_checks": [
            {
                "precision": "fuzzy",
                "check": {
                    "type": "element_exists",
                    "selector": "main, [role='main'], .content, #content",
                },
                "expect": "存在",
                "description": "页面主体渲染可见",
            },
            {
                "precision": "exact",
                "check": {"type": "no_error"},
                "expect": "无错误",
                "description": "页面无报错提示",
            },
        ],
    }


def steps_from_case(case_schema: dict[str, Any], case: dict[str, Any], config) -> list:
    """从测试用例构造执行步骤。单管道三策略:

    策略1 — 确定性执行: actions + checks 都齐全 → 直接执行，零 AI
    策略2 — AI 自主探索: 只有 checks 没 actions → AI agent 拿 checks 当成功标准自主规划
    策略3 — 纯动作执行: 只有 actions 没 checks → 执行动作 + 基础 UI 验证
    都没有 → 跳过

    ★ B-4-1: 策略选择由 strategy_router._select_strategy() 统一决策，
       支持人工超驰 forced_strategy（case["forced_strategy"]）。
    """
    actions = case.get("actions") or []
    checks = case.get("checks") or []
    forced_strategy = case.get("forced_strategy")

    # ★ exploratory 用例跳过 no_error/fatal_error check（运行时源头过滤）。
    #   why: builder 层过滤写入 verification_plan 副本，但消费方（_build_structured_checks）
    #     读的是原始 checks[]（case.get("checks")）—— builder 过滤完全被绕过。
    #     过滤必须在 checks 被消费的第一站（这里）发生。
    _scenario = str(case.get("scenario_type") or "").strip().lower()
    if _scenario in ("exploratory", "explore", "explore_path"):
        _before = len(checks)
        checks = [
            c for c in checks
            if not (isinstance(c, dict)
                    and str((c.get("check") or {}).get("type") or "").strip().lower()
                    in ("no_error", "fatal_error"))
        ]
        if len(checks) < _before:
            _log.info("[CaseMode] %s exploratory 跳过 %d 个 no_error/fatal_error check",
                      case.get("case_id", "?"), _before - len(checks))

    # field_constraint_check 不得在此剔除：其执行器
    # （verification._db_ui._handle_field_constraint_check）正是读控件 DOM 属性
    # （required/min/type/maxlength/options）核对约束的 UI 判定；剔掉后该 check
    # 只剩 verification_plan 的纯文本 needle（"必填：商品名称（1-50 字）…"），
    # 页面上永不出现 → 结构性假 FAIL。

    from src.pipeline.steps.step4.strategy_router import (
        _select_strategy,
        STRATEGY_SKIP,
        STRATEGY_DETERMINISTIC,
        STRATEGY_AI_EXPLORATION,
    )

    selection = _select_strategy(actions, checks, forced_strategy)
    strategy = selection["strategy"]

    if strategy == STRATEGY_SKIP:
        _log.warning("[CaseMode] 跳过: %s", selection["reason"])
        return []

    _log.info("[CaseMode] %s", selection["reason"])

    # ── 共用数据 ──
    title = str(case.get("title") or case.get("case_id", ""))
    risk = str(case.get("risk_level", "P1"))

    # ═══════════════════════════════════════════════════
    # 策略1: 确定性执行 — actions + checks 都齐全
    # ★ S45-001: 人工超驰策略1且 checks 为空时，容忍矛盾，只执行 actions 不做三轨验证
    # ═══════════════════════════════════════════════════
    if strategy == STRATEGY_DETERMINISTIC:
        verify_step = _build_verify_step(
            checks, verification_plan=case.get("verification_plan")
        )
        step_entry = {
            "description": f"执行测试用例: {title}",
            "action": "execute",
            "expected": "所有检查点通过",
            "_case": case,
            "_strategy": 1,
            "actions": actions,
        }
        # ★ S45-001: checks 为空时标记跳过三轨验证
        if verify_step.get("skip_verification"):
            step_entry["skip_verification"] = True
            step_entry["_checks"] = []
            _log.info("[CaseMode] 策略1超驰: checks 为空，跳过三轨验证，仅执行 actions")
        else:
            # ★ 挂载 checks 到 step_entry，使 _verify_recipe_intent_result 能执行三轨验证
            for key in (
                "api_checks",
                "db_checks",
                "ui_checks",
                "required_tracks",
                "_checks",
            ):
                if key in verify_step:
                    step_entry[key] = verify_step[key]
        result = []
        cap_step = _try_capability_step(case, config)
        if cap_step:
            result.append(cap_step)
        result.append(step_entry)
        return result

    # ═══════════════════════════════════════════════════
    # 策略2: AI 自主探索 — 有 checks 但不知道怎么操作
    # ═══════════════════════════════════════════════════
    elif strategy == STRATEGY_AI_EXPLORATION:
        check_list = _build_check_list(checks)
        default_user = config.target_username
        default_pwd = config.target_password
        # ★ 已知步骤指引注入（2026-09-27）：生成端只产 test_steps 未产 actions 的
        #   用例（实测 002-UX02/008-UX02）只凭成功标准让 AI 自由探索，AI 在分页/表单
        #   页面反复空转 3 轮后触发早停 → 假 ERROR。test_steps 是生成端已知的操作路径，
        #   注入 prompt 后 AI 有路径指引（非强制），探索稳定且不依赖重新生成用例。
        _known_steps = str(case.get("test_steps") or "").strip()
        constraints = [
            "★★★ 探索执行模式：以下是成功标准，你自己想办法在浏览器里达成这些目标。",
            "不要机械执行固定步骤，根据页面实际情况灵活决策。",
            "只在必要时使用页面元素定位，不依赖固定 CSS/XPath 选择器。",
            f"测试账号: {default_user} / {default_pwd}",
            f"\n成功标准（全部达成才算通过）：\n{check_list}",
        ]
        if _known_steps:
            constraints.append(
                f"\n已知操作路径（参考指引，可灵活执行）：\n{_known_steps[:1500]}"
            )
        verify_step = _build_verify_step(
            checks, verification_plan=case.get("verification_plan")
        )
        exploration_step = {
            "description": f"探索执行: {title}",
            "action": "\n".join(constraints),
            "expected": "所有检查点通过",
            "_case": case,
            "_strategy": 2,
        }
        return [exploration_step, verify_step]

    # ═══════════════════════════════════════════════════
    # 策略3: 纯动作执行 — 有操作但无验证标准
    # ═══════════════════════════════════════════════════
    else:
        verify_step = _build_basic_verify_step()
        step_entry = {
            "description": f"执行测试用例: {title}",
            "action": "execute",
            "expected": "操作完成，页面状态正常",
            "_case": case,
            "_strategy": 3,
            "actions": actions,
        }
        # ★ 挂载 verify checks 到 step_entry
        for key in ("api_checks", "db_checks", "ui_checks", "required_tracks"):
            if key in verify_step:
                step_entry[key] = verify_step[key]
        return [step_entry]


def collapse_steps_to_case(steps: list, case_meta: dict[str, str], config) -> list:
    """将自然语言细步骤折叠为单个测试用例步骤。"""
    if not steps or len(steps) <= 1:
        return steps
    if any(isinstance(s, dict) and s.get("actions") for s in steps):
        return steps
    title = (case_meta or {}).get("title", "").strip()
    original = format_steps(steps)
    default_user = config.target_username
    default_pwd = config.target_password
    case_desc = "执行测试用例：%s" % (title or "当前用例")
    constraints = [
        "按业务目标自主规划操作，不要机械逐条翻译原始步骤。",
        "只在必要时使用页面元素定位，不输出或依赖固定 DOM 选择器。",
        "如果是登录成功场景，使用账号 %s 和密码 %s。" % (default_user, default_pwd),
        "如果是登录失败场景，使用账号 %s 和一个错误密码，并验证错误提示。"
        % default_user,
        "原始步骤只作为验收约束，不作为必须逐条执行的脚本：\n%s" % original,
    ]
    expected = "测试用例通过，所有检查点符合预期"
    _log.info("[CaseMode] 已将 %d 个细动作折叠: %s", len(steps), title or "未命名")
    return [
        {
            "description": case_desc,
            "action": "\n".join(constraints),
            "expected": expected,
            "_collapsed": True,
        }
    ]
