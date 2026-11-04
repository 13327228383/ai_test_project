# -*- coding: utf-8 -*-
"""组合断言后处理增强 — 方案 C。

扫描 Step3 生成的用例 actions，识别业务场景并自动追加缺失的高价值断言。
纯算法实现，无 AI 调用，不影响 token 成本，始终启用。

场景识别规则：
  - 搜索/筛选场景：actions 含 fill + click 且 target_hint 含"搜索/筛选/查询"
    → 追加 loading_complete_check + list_order_check（若缺失）
  - 表单提交场景：actions 含 fill + click 且 target_hint 含"保存/提交/确认"
    → 追加 text_contains_check + semantic_check（若缺失）
  - 文件下载场景：actions 含 download_file
    → 追加 download_check（若缺失）
  - 批量删除场景：actions 含 click 且 target_hint 含"删除"
    → 追加 semantic_check 删除后置条件（若缺失）

设计原则：
  1. 只追加，不修改原有 checks
  2. 单条用例最多追加 combo_assertion_max_append 个
  3. 若 combo_assertion_require_multi_track=True，优先追加跨轨断言
  4. ★ 只发射**可执行指令**（check 必须携带 Step4 执行注册表内的 type）。
     规则层没有表名/字段上下文时，不得伪造 db 占位断言的字符串 check——
     那类"非指令"执行端无法分派，只会退化为无判定/假 PASS。
     表级断言交由 Step4 RAG/Schema 兜底按真实表产出。
"""

import logging
from typing import Any
from src.pipeline.step3.case_rules.base import _TRIGGER_KEYWORDS as _TRIGGER_KEYWORDS  # noqa: F401  词表唯一源

_log = logging.getLogger(__name__)


def _has_action(actions: list[dict], action_type: str) -> bool:
    """检查 actions 中是否包含指定类型。"""
    return any(a.get("type") == action_type for a in (actions or []))


def _has_check(checks: list[dict], check_type: str) -> bool:
    """检查 checks 中是否已存在指定类型断言（避免重复追加）。"""
    for c in checks or []:
        check_field = c.get("check", {})
        if isinstance(check_field, dict) and check_field.get("type") == check_type:
            return True
        # 兼容 track=ui 的简单格式
        if c.get("track") == check_type:
            return True
    return False


def _target_hints_contain(actions: list[dict], keywords: list[str]) -> bool:
    """检查 actions 的 target_hint 是否包含任一关键词。"""
    for a in actions or []:
        hint = str(a.get("target_hint", "") or "").lower()
        if any(k.lower() in hint for k in keywords):
            return True
    return False


# ★ Fix (2026-07-10): 负向测试错误断言关键词
#   当 checks 已含错误/异常相关断言时，不应再追加成功 toast 断言
_ERROR_CHECK_KEYWORDS = [
    "提示信息",
    "错误",
    "error",
    "失败",
    "fail",
    "异常",
    "invalid",
    "不能",
    "请输入",
    "请选择",
    "必填",
    "校验",
    "阻止",
    "拒绝",
]

# 提交按钮: 提交表单数据(保存/提交/确认/确定)
_SUBMIT_KEYWORDS = ("保存", "提交", "确认", "确定", "save", "submit")
# 搜索按钮: 查询/筛选数据
_SEARCH_KEYWORDS = ("搜索", "筛选", "查询", "search", "filter")


def _classify_click_scenario(actions: list[dict]) -> str:
    """★ 方案B (ADR-017, 2026-07-23): 基于click动作分类场景(主操作)。

    fill是数据输入前置步骤,click才是主操作。旧逻辑检查所有actions的target_hint,
    导致"fill搜索框 + click新建按钮"被误判为搜索场景 → 追加list_order_check → 假FAIL。

    Returns:
        "form_open"  — click是触发按钮(新建/编辑),打开表单,不涉及列表排序/成功提示
        "form_submit"— click是提交按钮(保存/提交),提交表单,验证成功提示
        "search"     — click是搜索按钮(查询/筛选),或仅有fill搜索框无click
        "delete"     — click含删除/移除
        "generic"    — 无法分类
    """
    actions = actions or []
    # 收集click动作的target_hint(主操作)
    click_hints = [
        str(a.get("target_hint", "") or "").lower()
        for a in actions
        if a.get("type") == "click"
    ]

    # 优先判断click是否为触发按钮(新建/编辑等,打开表单)
    for hint in click_hints:
        if any(k.lower() in hint for k in _TRIGGER_KEYWORDS):
            return "form_open"
    # 判断click是否为提交按钮(保存/提交等)
    for hint in click_hints:
        if any(k.lower() in hint for k in _SUBMIT_KEYWORDS):
            return "form_submit"
    # 判断click是否为搜索/查询按钮
    for hint in click_hints:
        if any(k.lower() in hint for k in _SEARCH_KEYWORDS):
            return "search"
    # 判断click是否为删除按钮
    for hint in click_hints:
        if any(k in hint for k in ("删除", "移除", "delete", "remove")):
            return "delete"
    # 无click或click无法分类 → 检查fill是否含搜索(纯搜索框+回车场景)
    for a in actions:
        if a.get("type") == "fill":
            hint = str(a.get("target_hint", "") or "").lower()
            if any(k.lower() in hint for k in _SEARCH_KEYWORDS):
                return "search"
    return "generic"


def _has_error_check(checks: list[dict]) -> bool:
    """★ Fix (2026-07-10): 检查 checks 中是否已有错误/异常相关断言（负向测试场景）。

    负向测试（异常输入校验）的 checks 会包含"提示信息/错误/校验"等断言，
    此时追加"成功,保存成功"toast 断言是自相矛盾的。
    """
    for c in checks or []:
        check_field = c.get("check", {})
        if isinstance(check_field, dict):
            expected = str(
                check_field.get("expected", "") or check_field.get("text", "")
            ).lower()
        else:
            expected = str(check_field).lower()
        expect = str(c.get("expect", "")).lower()
        combined = expected + " " + expect
        if any(kw.lower() in combined for kw in _ERROR_CHECK_KEYWORDS):
            return True
    return False


def _enhance_search_scenario(case: dict, max_append: int) -> int:
    """搜索/筛选场景：追加 loading_complete。

    ★ 方案B (ADR-017, 2026-07-23): 移除 list_order_check 追加
      根因(§10.9 3层追问):
        1. 表层: list_order_check失败 — "第0项无法转为数值: ''"
        2. 中层: combo_enhancer硬编码data_type="numeric"+value_selector="td:nth-child(1)",
                 但不同系统表格第1列可能是复选框/名称/序号/空,不一定是数值
        3. 根因: list_order_check假设过强(第1列是数值+升序排列),不通用
      通用性(§12.4):
        - 跨系统: 不同UI框架表格结构不同 ✓ (不假设第1列是数值)
        - 跨行业: 电商/SaaS/ERP表格列含义不同 ✓
        - 根因溯源: 移除过度断言,search核心是text_contains+loading_complete ✓
        - 职责边界: Step3 combo_enhancer内修复 ✓
      合规: AGENTS.md §4.2 "禁止写死特定行业的表名、字段名、页面路径"
    """
    appended = 0
    checks = case.setdefault("checks", [])

    if not _has_check(checks, "loading_complete_check") and appended < max_append:
        checks.append(
            {
                "track": "ui",
                "precision": "exact",
                "check": {
                    "type": "loading_complete_check",
                    "loading_selector": ".ant-spin, .loading",
                    "expected_state": "hidden",
                    "timeout_ms": 10000,
                },
                "expect": "搜索后加载完成",
            }
        )
        appended += 1

    # ★ 方案B: 不再追加 list_order_check
    #   原因: 硬编码data_type="numeric"+value_selector="td:nth-child(1)"不通用
    #   search场景核心断言是text_contains(验证搜索结果包含查询词),由_build_ui_check_by_operation生成

    return appended


def _enhance_form_submit_scenario(
    case: dict, max_append: int, require_multi_track: bool
) -> int:
    """表单提交场景：追加 text_contains + db_field_value（若有表名）。"""
    appended = 0
    checks = case.setdefault("checks", [])

    # UI 断言：Toast 提示包含"成功"
    if not _has_check(checks, "text_contains_check") and appended < max_append:
        checks.append(
            {
                "track": "ui",
                "precision": "exact",
                # ★ F3 全局修复 [未复核-需 GLM/DeepSeek 把关]（2026-08-29）：
                #   "成功后断言"显式标记 post_success=True，供 replay 场景
                #   （op_cache_v2 命中未真实执行业务动作）在执行层跳过
                #   （_is_replay_skippable_check），替代字符串关键字兜底。
                "post_success": True,
                "check": {
                    "type": "text_contains_check",
                    "selector": ".toast-message, .ant-message",
                    "expected": "成功,保存成功,操作成功",
                    "operator": "contains",
                },
                "expect": "Toast 提示包含成功",
            }
        )
        appended += 1

    # DB 断言：若已有 DB 轨道断言则跳过（避免冲突）
    has_db_track = any(c.get("track") == "db" for c in checks)
    # ★ 契约修复(2026-09-27)：原实现发射 {"check": "表单提交后 DB 应有对应记录"}——
    #   无 type 的**非指令**：Step4 resolve_handler 无分派入口（UNSUPPORTED_CHECK），
    #   plan builder 只能降维成 evidence="unverified" / table="" 的死断言。
    #   存量库实测 20 条同类 check 全部退化为"无判定"，旧聚合再把它渲染成假 PASS。
    #   根因：规则层没有表名/字段上下文（原注释写"由执行时 AI 兜底"，但该兜底并不存在）。
    #   修复：改发**可执行**的语义断言（semantic_check = Step4 机械优先 + AI 语义判定，
    #   是引擎对"语义期望"的唯一指定通道）；表级断言交由 Step4 RAG/Schema 兜底
    #   （verification_plan_builder._build_db_checks）产出，规则层不再伪造无法验证的指令。
    if (
        require_multi_track
        and not has_db_track
        and not _has_check(checks, "semantic_check")
        and appended < max_append
    ):
        checks.append(
            {
                "track": "ui",
                "precision": "fuzzy",
                "check": {
                    "type": "semantic_check",
                    "expected": "提交后系统应保存该数据并给出成功反馈",
                },
                "expect": "提交后数据被保存",
            }
        )
        appended += 1

    return appended


def _enhance_download_scenario(case: dict, max_append: int) -> int:
    """文件下载场景：追加 download_check。"""
    checks = case.setdefault("checks", [])
    if _has_check(checks, "download_check"):
        return 0
    checks.append(
        {
            "track": "ui",
            "precision": "exact",
            "check": {
                "type": "download_check",
                "expected_filename": "",
                "expected_min_size": 1,
            },
            "expect": "下载文件存在且非空",
        }
    )
    return 1


def _enhance_delete_scenario(
    case: dict, max_append: int, require_multi_track: bool
) -> int:
    """批量删除场景：追加**可执行**的删除后置条件语义断言。

    ★ 契约修复(2026-09-27)：原实现发射 {"check": "删除操作后 DB 记录数应减少"}——
      无 type 的**非指令**（Step4 无分派入口 → UNSUPPORTED_CHECK → 旧聚合渲染成假 PASS）。
      且"记录数减少"本身是**依赖基线的增量断言**，规则层既无表名也无基线上下文。
      修复：改为删除后**可观察后置条件**的语义断言（记录不再出现在列表中），
      由 Step4 semantic_check 通道（机械优先 + AI 语义判定）真实判定；
      表级计数断言交由 Step4 RAG/Schema 兜底按真实表产出。
    """
    checks = case.setdefault("checks", [])
    if _has_check(checks, "semantic_check"):
        return 0
    if not require_multi_track:
        return 0
    if max_append <= 0:
        return 0
    checks.append(
        {
            "track": "ui",
            "precision": "fuzzy",
            "check": {
                "type": "semantic_check",
                "expected": "被删除的记录不再出现在列表中",
            },
            "expect": "删除后记录消失",
        }
    )
    return 1


def enhance_combo_assertions(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """主入口：扫描用例 actions，自动追加缺失的高价值断言。

    纯算法实现，始终启用（无开关）。
    通过 combo_assertion_max_append / combo_assertion_require_multi_track 控制追加策略。
    返回增强后的 cases 列表（原列表会被修改）。
    """
    try:
        from src.core.pipeline_config import pipeline_config as cfg

        max_append = cfg.combo_assertion_max_append
        require_multi_track = cfg.combo_assertion_require_multi_track
    except Exception as e:
        _log.debug("[ComboEnhancer] 配置读取失败，使用默认值: %s", str(e)[:80])
        max_append = 3
        require_multi_track = True

    enhanced_count = 0
    for case in cases or []:
        if not isinstance(case, dict):
            continue
        actions = case.get("actions", [])
        if not actions:
            continue

        appended = 0
        # ★ 方案B (ADR-017, 2026-07-23): 基于click主操作分类场景,替代旧的全action扫描
        #   根因(§10.9): 旧逻辑检查所有actions的target_hint,fill含"搜索"→误判为搜索场景
        #   → 追加list_order_check → 对"新建"操作无意义 → 假FAIL
        #   修复: 用_classify_click_scenario基于click动作(主操作)分类
        #   通用性(§12.4):
        #     - 跨系统: 任何系统click都是主操作 ✓
        #     - 跨行业: 任何行业fill都是前置步骤 ✓
        #     - 跨页面: 表单页/列表页/详情页都适用 ✓
        #     - 覆盖性: 影响所有含fill+click的用例 ✓
        #     - 职责边界: Step3 combo_enhancer内修复 ✓
        scenario = _classify_click_scenario(actions)
        has_error_checks = _has_error_check(case.get("checks", []))

        # 搜索场景: 追加loading_complete + list_order(列表排序对搜索有意义)
        if scenario == "search":
            appended += _enhance_search_scenario(case, max_append - appended)

        # 表单提交场景(保存/提交): 追加成功toast(搜索/触发按钮/负向测试不追加)
        #   触发按钮(form_open)只打开表单不提交,不产生成功toast
        if scenario == "form_submit" and not has_error_checks:
            appended += _enhance_form_submit_scenario(
                case, max_append - appended, require_multi_track
            )

        # ★ 方案B: form_open场景(触发按钮:新建/编辑)不追加任何combo断言
        #   原因: 触发按钮只打开表单,不涉及列表排序/成功提示/DB入库
        #   checks由input_rule._build_ui_check_by_operation生成(element_exists验证表单打开)

        if _has_action(actions, "download_file"):
            appended += _enhance_download_scenario(case, max_append - appended)

        if scenario == "delete":
            appended += _enhance_delete_scenario(
                case, max_append - appended, require_multi_track
            )

        if appended > 0:
            enhanced_count += 1
            _log.info(
                "[ComboEnhancer] 用例 %s 追加了 %d 个断言",
                case.get("case_id", "?"),
                appended,
            )

    if enhanced_count > 0:
        _log.info("[ComboEnhancer] 共增强 %d 条用例的断言组合", enhanced_count)
    return cases
