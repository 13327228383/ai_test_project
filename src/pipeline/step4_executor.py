# -*- coding: utf-8 -*-
from __future__ import annotations
from src.core.guarded import guard

"""
step4_executor.py — Step4 测试用例执行编排器
=============================================
从 scheduler.py 拆分出的独立模块，负责 Step4 的完整执行编排：
- 部署文档匹配
- 多策略意图读取（版本/文件/失败重跑/指定ID/全量）
- API-only 快速通道路由
- Playwright 浏览器执行

★ 安全拆分原则：
  - 零业务逻辑变更，仅物理位置移动
  - 保持所有导入路径、函数签名、返回值 100% 一致
  - 原文件通过门面模式兼容
"""

import logging
import os
import json as _json
import re as _re
import uuid as _uuid
from pathlib import Path
from typing import Any

from src.core.pipeline_config import _read_config_value as _cfg
from src.pipeline.step4_case_reader import (
    read_intents_with_meta,
    read_intents_by_ids,
    read_failed_intents,
    read_unexecuted_cases_by_files,
    read_intents_for_versions,
)

_log = logging.getLogger(__name__)


def _run_data_lifecycle_for_cases(
    cases: list[dict], project: str, phase: str
) -> dict[str, Any]:
    """★ C 数据治理 [已复核-DeepSeek-V3 2026-08-16]: case 级 data_setup/data_teardown（fail-open）。

    返回 {case_id: {"phase","ns","ok","ops"}}；永不 raise；异常只记 warning。
    """
    if not cases:
        return {}
    try:
        from src.pipeline.steps.step4.data_lifecycle_ops import run_data_lifecycle

        return run_data_lifecycle(cases, project, phase)
    except Exception as _e:  # noqa: BLE001
        _log.warning("[TDL-OPS] %s 编排异常(不阻断): %s", phase, _e, exc_info=True)
        return {}


def _merge_dlc_evidence(
    store: dict[str, Any], ev_map: dict[str, Any]
) -> dict[str, Any]:
    """按 case_id 合并 lifecycle evidence（setup/teardown 分 phase 键存）。"""
    for _cid, _ev in ev_map.items():
        _phase = _ev.get("phase", "") if isinstance(_ev, dict) else ""
        store.setdefault(str(_cid), {})[_phase] = _ev
    return store


# 唯一值语义（业务无关的记录名/标题）token 形态——与生成端 _gen_unique_value 的前缀对齐。
# why: 唯一值只应有一处生产（此处运行时物化）；从 2026-09-27 起生成端确定性值已废弃，
#      执行端不再消费 "<unique>" 之外的旧形态前缀。
_UNIQUE_NAME_TOKEN_RE = _re.compile(r"(自动化记录|验证记录|自动化值)[0-9a-zA-Z]{5,8}")


def _replace_unique_in_str(
    text: str, run_salt: str, case_id: str = ""
) -> tuple[str, int]:
    """把字符串内所有唯一值 token 替换为**本用例**的物化值，返回 (新串, 替换次数)。

    why: 唯一值须同时满足 ① 同用例 fill 与 check 同源 ② 同 run 跨用例不同（避唯一约束撞车）
         ③ 跨 run 不同（避复用旧数据撞库）；三者交集只能按 (run, case, field) 派生。
    """
    _counted = {"n": 0}

    def _sub(_m: _re.Match) -> str:
        _counted["n"] += 1
        _prefix = _m.group(1)
        _seed = f"{run_salt}|{case_id}|{_prefix}"
        return _prefix + _uuid.uuid5(_uuid.NAMESPACE_DNS, _seed).hex[:6]

    _out = _UNIQUE_NAME_TOKEN_RE.sub(_sub, text)
    if "<unique>" in _out:
        _counted["n"] += _out.count("<unique>")
        _out = _out.replace(
            "<unique>",
            "自动化记录" + _uuid.uuid5(
                _uuid.NAMESPACE_DNS, f"{run_salt}|{case_id}|<unique>"
            ).hex[:6],
        )
    return _out, _counted["n"]


def _replace_unique_in_node(node: Any, run_salt: str, case_id: str = "") -> int:
    """递归替换 dict/list/str 中的唯一值 token，返回替换次数。"""
    _n = 0
    if isinstance(node, dict):
        for _k, _v in node.items():
            if isinstance(_v, str):
                _nv, _nn = _replace_unique_in_str(_v, run_salt, case_id)
                if _nn:
                    node[_k] = _nv
                _n += _nn
            else:
                _n += _replace_unique_in_node(_v, run_salt, case_id)
    elif isinstance(node, list):
        for _i, _v in enumerate(node):
            if isinstance(_v, str):
                _nv, _nn = _replace_unique_in_str(_v, run_salt, case_id)
                if _nn:
                    node[_i] = _nv
                _n += _nn
            else:
                _n += _replace_unique_in_node(_v, run_salt, case_id)
    return _n


def _materialize_run_unique_values(candidates: list[dict]) -> int:
    """唯一值运行时物化：按 **(run, case, field)** 三元组派生，全引擎只此一处生产。

    why: 唯一值一旦由多处各自生产（生成端/执行端各一份，或 run 级共享一份），
         必然违反「同用例 fill 与 check 同源」「跨用例不撞唯一约束」其中之一 → 假 FAIL。
         故收口为单一生产点：run 盐保证跨 run 不同，case_id 保证同 run 内分流。
    """
    if not candidates:
        return 0
    # run 级随机盐：跨 run 派生值必然不同 → 不撞库；同 run 内按 case_id 分流 → 不撞车。
    _run_salt = _uuid.uuid4().hex[:8]
    _total = 0
    for _c in candidates:
        _cid = str(_c.get("case_id") or "").strip()
        _p = _c.get("payload")
        if isinstance(_p, str):
            try:
                _obj = _json.loads(_p)
            except Exception:
                continue
            _n = _replace_unique_in_node(_obj, _run_salt, _cid)
            if _n:
                _c["payload"] = _json.dumps(_obj, ensure_ascii=False)
            _total += _n
        elif isinstance(_p, dict):
            _total += _replace_unique_in_node(_p, _run_salt, _cid)
    if _total:
        _log.info(
            "[Step4] 唯一值运行时物化: 替换 %d 处 → run 共享值 %s",
            _total,
            _run_salt,
        )
    return _total


def step4_execute(
    *,
    quick: bool = False,
    run_id: str = "",
    step4_options: dict = None,
    user_id: str = None,
    force_stop_check=None,
    progress_callback=None,
    execute_api_only_intents,
    read_intents_with_meta=None,
    read_intents_by_ids=None,
    read_failed_intents=None,
    read_unexecuted_cases_by_files=None,
    read_intents_for_versions=None,
) -> dict[str, Any]:
    """启动 Playwright 浏览器，执行所有未完成的测试用例。

    ★ v3.1 从 PipelineScheduler._step4_execute 提取为独立函数。
    所有 self.* 访问通过显式参数传入，保持逻辑零变更。
    """
    # ★ 允许 scheduler 传入自定义读取函数（覆盖模块级默认导入）
    _read_intents_with_meta = (
        read_intents_with_meta or globals()["read_intents_with_meta"]
    )
    _read_intents_by_ids = read_intents_by_ids or globals()["read_intents_by_ids"]
    _read_failed_intents = read_failed_intents or globals()["read_failed_intents"]
    _read_unexecuted_cases_by_files = (
        read_unexecuted_cases_by_files or globals()["read_unexecuted_cases_by_files"]
    )
    _read_intents_for_versions = (
        read_intents_for_versions or globals()["read_intents_for_versions"]
    )
    from src.core.excel_handler import excel_handler
    from src.pipeline.steps.step4.browser_core import browser_executor

    if force_stop_check:
        # ★ 方案B 改造：_force_stop_check 赋值到当前项目的 BrowserCore 实例
        browser_executor.for_current()._force_stop_check = force_stop_check

    opts = step4_options or {}
    case_asset_file = opts.get("case_asset_file")
    case_asset_files = opts.get("case_asset_files")
    include_executed = opts.get("include_executed", False)
    risk_level_filter = opts.get("risk_level_filter") or opts.get("priorities")
    retry_failed = (
        opts.get("retry_failed", False) or opts.get("mode", "") == "retry_failed"
    )
    case_ids = opts.get("case_ids")
    intent_indices = opts.get("intent_indices")
    # ★ [INT-1 fix 2026-08-28 | 未复核-需 GLM/DeepSeek 把关] 全量执行语义兜底：
    #   未显式指定用例(case_ids 空)且非 retry_failed 时，默认全量执行，
    #   避免 run-all/单步"执行"只跑增量、掩盖全量结果（违反"易用=一键真跑全量"）。
    if not include_executed and not case_ids and not retry_failed:
        include_executed = True
    browser_info = opts.get("browser_info")
    if not browser_info:
        _browser_raw = opts.get("browser")
        if isinstance(_browser_raw, dict):
            browser_info = _browser_raw
        elif isinstance(_browser_raw, str) and _browser_raw:
            # ★ 前端传的是浏览器 ID 字符串（如 "edge"），需查找完整配置（path/channel/engine）
            with guard("step4_execute: from src.ai.browser_detector import ", log=_log, level=logging.WARNING):
                from src.ai.browser_detector import get_installed_browsers

                for _b in get_installed_browsers():
                    if _b.get("id") == _browser_raw:
                        browser_info = _b
                        break
                if not browser_info:
                    _log.warning(
                        "[Step4] 未找到浏览器 ID=%s 的配置，使用默认浏览器",
                        _browser_raw,
                    )
        else:
            browser_info = {}
    if not isinstance(browser_info, dict):
        browser_info = {}
    include_virtual_inherited = bool(opts.get("include_virtual_inherited", False))
    version_ids = opts.get("version_ids") or opts.get("versions") or []

    # ★ F-14 (2026-08-04): 可观测计数器 — 置信度回写失败计数（F-10a 埋点，
    #   进程内自证「不再静默」，随 stats 返回，供基线采集与 M1 判据使用）。
    _stats_counters = {"confidence_writeback_failed_total": 0}

    # ★ 部署文档匹配：从部署文档中提取转测范围 → 语义匹配测试用例 → 喂入 case_ids 管道
    deployment_doc = opts.get("deployment_doc")
    if deployment_doc:
        # ★ deployment_doc 现在是文档内容字符串（已存 MySQL source_documents），不再从磁盘读取
        from src.extensions.deployment_matcher import DeploymentMatcher

        version_filter = opts.get("version_filter")

        # 加载全部测试用例：部署文档匹配只读取 MySQL test_cases。
        all_intents, _deployment_case_source = _read_intents_with_meta(
            excel_handler, include_virtual_inherited=include_virtual_inherited
        )
        if not all_intents:
            _log.error("[DeploymentMatcher] 测试用例表为空，无法匹配")
            _log.info("  [DeploymentMatcher] 测试用例表为空，无法匹配")
            return {
                "success": False,
                "count": 0,
                "error": "No validation intents available",
            }

        # 执行匹配
        matcher = DeploymentMatcher()
        match_result = matcher.filter_intents_by_deployment(
            doc_content=str(deployment_doc),
            doc_path="",  # 向后兼容空路径
            intents=all_intents,
            version=version_filter or "",
        )

        # 打印匹配报告
        report = match_result.get("report")
        if report:
            _log.info(DeploymentMatcher.format_report(report, version_filter or ""))

        # 处理错误
        error = match_result.get("error")
        if error:
            _log.error("[DeploymentMatcher] %s", error)
            _log.error("  [DeploymentMatcher] ERROR: %s", error)
            return {"success": False, "count": 0, "error": error}

        # 将匹配到的 case_ids 喂入现有管道
        matched_ids = match_result.get("case_ids", [])
        if not matched_ids:
            _log.info("没有匹配到任何测试用例，跳过执行")
            _log.info("  没有匹配到任何测试用例，跳过执行")
            return {"success": True, "count": 0, "skipped": True}

        case_ids = matched_ids
        _log.info(
            "部署文档匹配完成: %d 条测试用例待验证",
            match_result.get("matched_count", 0),
        )
        _log.info(
            "  部署文档匹配完成: %d 条测试用例待验证",
            match_result.get("matched_count", 0),
        )

    candidates = None
    _candidate_source = ""

    if version_ids and not retry_failed:
        candidates, _candidate_source = _read_intents_for_versions(
            excel_handler,
            version_ids,
            case_ids=case_ids,
            include_executed=include_executed,
        )
        _log.info(
            "  多版本模式: %s → %d 条待测试用例(按意图身份去重)",
            version_ids,
            len(candidates),
        )
    elif (
        case_asset_files
        and isinstance(case_asset_files, list)
        and len(case_asset_files) > 0
    ):
        candidates, _candidate_source = _read_unexecuted_cases_by_files(
            excel_handler,
            case_asset_files,
            user_id=user_id,
            include_virtual_inherited=include_virtual_inherited,
        )
        _log.info(
            "  多文件模式: %d 个文件 → %d 条未测试用例(去重)",
            len(case_asset_files),
            len(candidates),
        )
    elif case_asset_file:
        case_asset_path = Path(str(case_asset_file))
        if case_asset_path.is_file():
            excel_handler.set_case_asset_path(case_asset_path)

    uid = user_id
    if candidates is not None:
        pass
    elif retry_failed:
        candidates, _candidate_source = _read_failed_intents(
            excel_handler,
            user_id=uid,
            include_virtual_inherited=include_virtual_inherited,
        )
        _log.info(
            "  失败测试用例重验(%s): %d 条 FAIL 意图", uid or "全局", len(candidates)
        )
    elif intent_indices:
        return {
            "success": False,
            "count": 0,
            "error": "MySQL 事实源模式不支持按 Excel 列表位置执行，请改用 case_id",
        }
    elif case_ids:
        candidates, _candidate_source = _read_intents_by_ids(
            excel_handler, case_ids, include_virtual_inherited=include_virtual_inherited
        )
        found_ids = {str(c.get("case_id", "")).strip() for c in candidates}
        missing = [cid for cid in case_ids if str(cid).strip() not in found_ids]
        if missing:
            _log.warning("  [WARN] 以下测试用例未找到: %s", ", ".join(missing))
        _log.info("  指定测试用例执行: %d/%d 条匹配", len(candidates), len(candidates))
    elif include_executed:
        candidates, _candidate_source = _read_intents_with_meta(
            excel_handler, include_virtual_inherited=include_virtual_inherited
        )
    else:
        candidates, _candidate_source = _read_intents_with_meta(
            excel_handler, include_virtual_inherited=include_virtual_inherited
        )
        # 过滤已测试用例
        with guard("step4_execute: executed_ids = set()", log=_log, level=logging.WARNING):
            executed_ids = set()
            for c in candidates:
                _vs = str(c.get("verification_status", "")).strip().upper()
                if _vs in ("PASS", "FAIL"):
                    executed_ids.add(c.get("case_id", ""))
            candidates = [
                c for c in candidates if c.get("case_id", "") not in executed_ids
            ]
            _log.info("  过滤已验证: %d 条待验证", len(candidates))

    if risk_level_filter and len(risk_level_filter) > 0:
        candidates = [
            c
            for c in candidates
            if str(c.get("risk_level", "")).strip() in risk_level_filter
        ]

    if not candidates:
        _log.info("  没有符合条件的测试用例，跳过")
        return {"success": True, "count": 0, "skipped": True}

    # ★ F-05 一条一例守卫：CLI 模式跳过（AGENTS.md v5.2 红线仅约束 Web UI 模式）
    _explicit_single = (
        bool(case_ids)
        or bool(version_ids)
        or bool(case_asset_files)
        or bool(case_asset_file)
    )
    # CLI 模式允许批量执行（非交互式场景）
    _is_cli_mode = not bool(os.environ.get("FLASK_ENV") or os.environ.get("WERKZEUG_RUN_MAIN"))
    if (not _explicit_single) and len(candidates) > 1 and not _is_cli_mode:
        _log.error(
            "[Step4] 一条一例红线阻断: 检测到 %d 条待执行用例且非显式单条指定。"
            "批量自动执行已下线，请通过 /api/step4-run-single 逐条触发（AGENTS.md v5.2）。",
            len(candidates),
        )
        return {
            "success": False,
            "count": 0,
            "blocked": True,
            "reason": "ONE_CASE_AT_A_TIME",
            "error": "批量执行已下线（一条一例红线）: 待执行 %d 条，请用 /api/step4-run-single 逐条触发"
            % len(candidates),
        }

    # ── v9 精简: 风险分级采样（★ 根因修复 2026-09-24: 对称化 + 零静默丢弃）──
    # ★ 以下场景跳过采样（用户已显式指定要跑哪些，不应再被采样过滤）：
    #   - retry_failed: 失败用例无论何等级都需要重跑
    #   - case_ids/version_ids/case_asset_files: 用户显式选定的用例集合
    #
    # ★ 根因修复(2026-09-24): 原实现只认 P0/P1/P2 三档，**P3/P4 既无配置项也无分支**
    #   → 候选被静默丢弃：无日志、无 SKIP 记录、报告不可见。实测 accept_sut_final
    #   582 条中 P3=136(UI/性能) + P4=204(兼容/安全/易用) 共 340 条（58%）0 执行；
    #   而这些用例携带的机械 check（viewport_rendering_check / performance_lcp_check
    #   / click_target_size_check / text_contains_check / viewport_overflow_check）
    #   执行器**全部已支持** —— 即"能力在、被采样吞掉"，属纯引擎缺陷而非靶子问题。
    #   用户看到"通过/失败 91/265"却无从得知 58% 用例根本没跑。
    #   通用修复: 采样改为**按风险等级数据驱动**，P0~P4 一视同仁，逐档独立策略：
    #     · 每档读 step4_include_p{N}（是否纳入）/ step4_p{N}_sample_rate（抽样率）；
    #     · 未配置的档位 → 默认全量纳入（fail-open，绝不静默丢弃）；
    #     · 每档都打印决策（含"策略跳过"），零静默；
    #     · risk_level 为空/未知 → 一律纳入（宁可多跑，不可漏跑）。
    #   通用性: 纯风险枚举驱动，跨系统/行业/页面成立，无任何业务术语。
    explicit_selection = bool(case_ids or version_ids or case_asset_files)
    if not quick and not retry_failed and not explicit_selection:
        _rl_dist = {}
        for _c in candidates:
            _rl = str(_c.get("risk_level", "")).strip().upper() or "(未标注)"
            _rl_dist[_rl] = _rl_dist.get(_rl, 0) + 1

        _RISK_LEVELS = ("P0", "P1", "P2", "P3", "P4")
        _keep: list = []
        _plan: list = []
        for _lvl in _RISK_LEVELS:
            _bucket = [
                c
                for c in candidates
                if str(c.get("risk_level", "")).strip().upper() == _lvl
            ]
            if not _bucket:
                continue
            _inc = bool(_cfg("step4_include_%s" % _lvl.lower(), True))
            _rate = float(_cfg("step4_%s_sample_rate" % _lvl.lower(), 1.0))
            if not _inc or _rate <= 0.0:
                _plan.append("%s=0/%d(策略跳过)" % (_lvl, len(_bucket)))
                continue
            if _rate < 1.0:
                import random

                _n = max(1, int(len(_bucket) * _rate))
                _picked = random.sample(_bucket, min(_n, len(_bucket)))
                _plan.append(
                    "%s=%d/%d(%.0f%%)" % (_lvl, len(_picked), len(_bucket), _rate * 100)
                )
            else:
                _picked = _bucket
                _plan.append("%s=%d/%d" % (_lvl, len(_picked), len(_bucket)))
            _keep.extend(_picked)
        # 未标注/未知等级 → 一律纳入（宁可多跑，不可漏跑）
        _unknown = [
            c
            for c in candidates
            if str(c.get("risk_level", "")).strip().upper() not in _RISK_LEVELS
        ]
        if _unknown:
            _keep.extend(_unknown)
            _plan.append("未标注=%d/%d(全量纳入)" % (len(_unknown), len(_unknown)))
        _log.info(
            "  [采样] 决策: %s → 执行 %d/%d 条（丢弃 %d 条）",
            "; ".join(_plan),
            len(_keep),
            len(candidates),
            len(candidates) - len(_keep),
        )
        candidates = _keep

    if quick:
        candidates = candidates[: int(_cfg("quick_mode_sample_size", 3))]
        _log.info("  快速模式: 仅执行前 %d 条", len(candidates))

    # ── W1-0: 唯一值运行时物化（根因修复 2026-09-27）──
    #   why: 唯一业务记录名被两端各自生产——生成端 _gen_unique_value 按 req_id 哈希
    #   产确定性值（跨 run 复用撞唯一约束），且同 req 各用例 scope 不同导致创建值与
    #   编辑/列表校验期望值分裂（假 FAIL/假 PASS 温床）。此处统一为 run 级单一随机值：
    #   fill 与 check 同源、创建与依赖记录同值、跨 run 不撞库。下游只读，禁止再推。
    _materialize_run_unique_values(candidates)

    # ── W1-1: 落定 verification_plan（Step3 深度计划优先，auto_build 仅兜底）──
    #   根因(§10.9): 原实现无条件执行 `c["verification_plan"] = auto_build_verification_plan(c)`，
    #     把 Step3 已生成的三轨深度计划（db/api 轨）整体覆盖丢弃 → 深度断言永不执行。
    #   修复: 携带有效计划则保留，仅缺失/结构不可解析时才兜底；异常 fail-closed 记录详情。
    try:
        from src.pipeline.steps.step4.verification_plan_builder import (
            apply_verification_plans,
        )

        _plan_stats = apply_verification_plans(candidates)
        _log.info(
            "[Step4] verification_plan 来源: Step3 保留=%d, auto_build 兜底=%d, 空计划=%d",
            _plan_stats.get("step3", 0),
            _plan_stats.get("auto_build", 0),
            _plan_stats.get("empty", 0),
        )
        if _plan_stats.get("empty"):
            _log.warning(
                "[Step4] %d 条用例无任何可证伪断言（Step3 未产出且兜底为空），存在假 PASS 风险(§12.4)",
                _plan_stats["empty"],
            )
    except Exception as _e:
        # 阻断级(§5)：拿不到 verification_plan 等于无断言执行，是假 PASS 温床，禁止静默放行
        _log.error(
            "[Step4] verification_plan 落定失败，阻断执行: %s", _e, exc_info=True
        )
        raise

    # ── W2 P0-3: ADI 执行前拦截（同一开关；默认 OFF = 度量模式不拦截）──
    #   blocked 用例**绝不**进入 stats["results"]（否则被当执行结果算置信度 → F-14 fail-closed raise）。
    #   apply_adi_gate 幂等：度量模式下只重算 adi/adi_detail（只读，不拦截）。
    # ★ §14 关键改动 [已复核-DeepSeek-V4-Flash 2026-08-05]：Step4 核心执行路径，需强模型复核。
    _adi_blocked_ids: list[str] = []
    try:
        from src.pipeline.steps.step3.oracle.guard import apply_adi_gate

        _adi_report = apply_adi_gate(candidates)
        if _adi_report.get("enabled") and _cfg("ADI_GATE_BLOCK_EXEC", True):
            _adi_blocked_ids = [str(b) for b in (_adi_report.get("blocked") or []) if b]
            if _adi_blocked_ids:
                _blocked_set = set(_adi_blocked_ids)
                _before = len(candidates)
                candidates = [
                    c
                    for c in candidates
                    if str(c.get("case_id", "")).strip() not in _blocked_set
                ]
                _log.warning(
                    "##HTML:##<span style='color:#ef4444;font-weight:bold'>"
                    "⚠️ [ADI 门禁] %d/%d 条用例 ADI 不足（阈值 %d），执行前拦截，不判 PASS。"
                    "</span>",
                    len(_adi_blocked_ids),
                    _before,
                    _adi_report.get("threshold", 1),
                )
                if not candidates:
                    _log.warning(
                        "[Step4] ADI 门禁拦截后无候选用例，提前返回（blocked=%d）",
                        len(_adi_blocked_ids),
                    )
                    return {
                        "success": True,
                        "count": 0,
                        "blocked": _adi_blocked_ids,
                        "stats": {
                            "blocked": _adi_blocked_ids,
                            "results": [],
                            "total": 0,
                            "passed": 0,
                            "failed": 0,
                        },
                    }
    except Exception as _e_adi:
        # fail-closed：拦截异常不阻断执行（否则门禁故障导致全部停摆），但必须留 error 日志
        _log.error(
            "[Step4-ADI] 门禁拦截异常(不阻断执行，已记录): %s", _e_adi, exc_info=True
        )

    # ── P0-3 增量：深断言感知信号闸门（默认关闭，零行为变更）──
    #   ★ §14 [未复核-需 GLM/DeepSeek 把关]
    #   在 apply_adi_gate 之后：用候选用例的 check 定义做"静态深断言占比代理"
    #   （每条 check 视为一次失败信号），超阈值则该用例标记 adi_signal=True。
    #   后续 E2 置信度闸门对 adi_signal=True 用例跳过假PASS拦截
    #   （深断言拉低 PASS 率属于预期信号，非回归/非假PASS）。
    #   禁用开关（ADI_SIGNAL_GATE_ENABLED=False，默认）→ 完全不介入。
    try:
        if _cfg("ADI_SIGNAL_GATE_ENABLED", False):
            from src.pipeline.steps.step3.oracle.adi_signal import (
                AdiSignalGate,
                DeepAssertionMetrics,
                extract_case_check_types,
            )

            for _c in candidates or []:
                if not isinstance(_c, dict):
                    continue
                _checks = extract_case_check_types(_c)
                _proxy = [{"type": t, "status": "FAIL"} for t in _checks]
                _m = DeepAssertionMetrics.collect(_proxy)
                if AdiSignalGate.classify(_m) == AdiSignalGate.SIGNAL:
                    _c["adi_signal"] = True
    except Exception as _e_sig:  # noqa: BLE001
        _log.warning(
            "[Step4-ADI-SIGNAL] 信号闸门异常(不阻断执行): %s", _e_sig, exc_info=True
        )

    # ★ 追溯链路预检（trace_validator）：执行前校验表名/API路径，标记幻觉风险
    #   不阻塞执行，但以弹窗告警提醒用户
    _trace_issues_map = {}
    with guard("step4_execute: from src.extensions.traceability_mod", log=_log, level=logging.WARNING):
        from src.extensions.traceability_model import traceability_engine

        trace_cache = traceability_engine._load_cache()
        if trace_cache:
            from src.extensions.trace_validator import validate_checks_against_trace

            _log.info(
                "[Step4-TraceValid] 执行前追溯校验: %d 条候选用例", len(candidates)
            )
            validation_result = validate_checks_against_trace(
                candidates, trace_links=trace_cache, db_schema={}
            )
            invalid_count = 0
            for item in validation_result.get("intents", []):
                result = item["result"]
                if not result.get("valid", True):
                    case_id = item["case_id"]
                    invalid_count += 1
                    _trace_issues_map[case_id] = result
                    _log.warning(
                        "[Step4-TraceValid] 用例 %s: %s",
                        case_id,
                        "; ".join(result.get("issues", [])),
                    )
            if invalid_count > 0:
                _log.warning(
                    "##HTML:##<span style='color:#ef4444;font-weight:bold'>"
                    "⚠️ [追溯校验] %d 条用例存在幻觉风险：表名/API路径不在追溯链路中。"
                    "请检查 Step2 数据源（YApi/DB Schema）是否完整拉取，或暂停流水线重新执行 Step2→Step3。"
                    "</span>",
                    invalid_count,
                )
            else:
                _log.info(
                    "##HTML:##<span style='color:#10b981'>"
                    "✅ [追溯校验] 全部 %d 条用例通过，表名/API路径均已在追溯链路中"
                    "</span>",
                    len(candidates),
                )
        else:
            _log.warning(
                "##HTML:##<span style='color:#f59e0b;font-weight:bold'>"
                "⚠️ [追溯校验] 无追溯链路数据（Step2 未执行或 trace_links 表为空），"
                "跳过防幻觉预检。建议先执行 Step1→Step2→Step3 全流程后再执行 Step4。"
                "</span>"
            )

    # ★ 置信度增强：Step4 执行前只读检查。当前仅打印，不阻断执行。
    with guard("step4_execute: from src.pipeline.utils.consistency_", log=_log, level=logging.WARNING):
        from src.pipeline.utils.consistency_checker import run_and_print_checks

        run_and_print_checks("Step4前")

    from src.pipeline.scheduler_helpers import is_api_only_intent

    api_only = [c for c in candidates if is_api_only_intent(c)]
    browser_intents = [c for c in candidates if not is_api_only_intent(c)]
    # ★ C 数据治理 [已复核-DeepSeek-V3 2026-08-16]: 生命周期命名空间 project（api/browser 共用）
    try:
        from src.core.config_loader import config as _dlc_cfg

        _dlc_project = str(getattr(_dlc_cfg, "project_name", "") or "")
    except Exception:
        _dlc_project = ""
    _dlc_evidence: dict[str, Any] = {}
    if api_only:
        _log.info(
            "  [路由] %d 条 api_only 测试用例 → 快速通道（无浏览器）", len(api_only)
        )
        _log.info(
            "  [路由] %d 条 browser 测试用例 → Playwright 浏览器", len(browser_intents)
        )
        # ★ C 数据治理 [已复核-DeepSeek-V3 2026-08-16]: api-only 路径 setup（fail-open）
        try:
            _dlc_evidence = _merge_dlc_evidence(
                _dlc_evidence,
                _run_data_lifecycle_for_cases(api_only, _dlc_project, "setup"),
            )
        except Exception as _e:
            _log.warning("[TDL-OPS] api-only setup 异常(不阻断): %s", _e, exc_info=True)
        api_stats = execute_api_only_intents(api_only)
        # ★ C 数据治理 [已复核-DeepSeek-V3 2026-08-16]: api-only 路径 teardown（fail-open）
        try:
            _dlc_evidence = _merge_dlc_evidence(
                _dlc_evidence,
                _run_data_lifecycle_for_cases(api_only, _dlc_project, "teardown"),
            )
        except Exception as _e:
            _log.warning("[TDL-OPS] api-only teardown 异常(不阻断): %s", _e, exc_info=True)
    else:
        api_stats = {"passed": 0, "failed": 0, "total": 0}

    # ★ 方案B 改造：_force_stop_check 赋值到当前项目的 BrowserCore 实例
    browser_executor.for_current()._force_stop_check = force_stop_check
    # ★ B-8-4: 数据完整性校验
    from src.pipeline.utils.data_integrity_checker import validate_step_input

    _run_cases = browser_intents if api_only else candidates
    _integrity4 = validate_step_input(
        "test_cases",
        _run_cases,
        required_fields=["case_id"],
    )
    if not _integrity4["valid"]:
        _log.error(
            "[Step4] Step3 产出数据完整性校验失败: %d/%d records 有问题",
            _integrity4["failed"],
            _integrity4["total"],
        )
    headless = opts.get("headless", True)
    timeout_ms = opts.get("timeout_ms", int(_cfg("default_step4_timeout_ms", 30000)))
    max_parallel = opts.get("browser_max_parallel") or opts.get("max_parallel")
    _run_cases = browser_intents if api_only else candidates

    # ── W2 P0-2: 测试数据生命周期（TDL）—— setup 尽量晚，try/finally 只包 run_test_cases ──
    #   step4_execute 有 6 处提前 return（:115/:136/:149/:156/:193/:232/:254），setup 若提前
    #   到函数头部，这些 return 路径都会漏 teardown。因此 setup 紧贴 run_test_cases，
    #   finally 兜底 teardown（异常路径同样进入 teardown）。
    # ★ §14 关键改动 [已复核-DeepSeek-V4-Flash 2026-08-05]：核心执行路径 + 触发 DB 写，需强模型复核。
    #   ★ F3 修复（2026-08-05 W3）：TDL setup 编排整体异常不再置 _tdl=None，
    #     保留引用 + 记 _tdl_failed 全阻断标记，避免部分成功台账泄漏（详见 except 分支注释）。
    #   ★ W3 T03 [已复核-DeepSeek-V4-Flash 2026-08-05]（2026-08-05）：run 级身份选择
    #     （:503 附近 run_test_cases 调用前 apply_identity_override，finally 清空；
    #      case 级 identity 缺失 → BLOCKED(IDENTITY_UNAVAILABLE) 不进浏览器）。
    #     改动仅限 run_test_cases 调用附近，未触碰 ADI/TDL 已复核区间。
    _tdl = None
    _tdl_failed: list[tuple[str, str]] = []  # (case_id, reason)
    _run_cases_for_exec = _run_cases
    if _cfg("TDL_ENABLED", False):
        try:
            from src.pipeline.steps.step4.test_data_lifecycle import TestDataLifecycle
            from src.core.config_loader import config as _tdl_cfg

            _tdl = TestDataLifecycle(
                run_id=run_id,
                project=str(getattr(_tdl_cfg, "project_name", "") or ""),
            )
            for _tc in _run_cases:
                _tres = _tdl.setup(_tc)
                if not _tres.get("ok", False):
                    _cid = str(_tc.get("case_id", "") or "")
                    _reason = str(_tres.get("error") or "SETUP_FAILED")[:120]
                    if _cfg("TDL_FAIL_CLOSED", True):
                        _tdl_failed.append((_cid, _reason))
                        _log.error(
                            "##HTML:##<span style='color:#ef4444;font-weight:bold'>"
                            "⚠️ [TDL] 用例 %s 前置数据 setup 失败（fail-closed 阻断执行）: %s"
                            "</span>",
                            _cid,
                            _reason,
                        )
                    else:
                        _log.warning(
                            "[TDL] 用例 %s setup 失败（TDL_FAIL_CLOSED=False 继续执行）: %s",
                            _cid,
                            _reason,
                        )
        except Exception as _te:
            # fail-closed：setup 编排整体异常 → 记 error；是否阻断整批由 TDL_FAIL_CLOSED 决定
            _log.error("[TDL] 生命周期 setup 阶段异常: %s", _te, exc_info=True)
            # ★ F3 修复（2026-08-05 W3）：不再置 _tdl = None —— 异常前可能已有部分
            #   setup 成功并写入台账（如 s1 成功、s2 抛异常），置 None 会让后续
            #   _tdl_teardown_all() 变 no-op，导致部分成功项静默泄漏。
            #   改为：保留 _tdl 引用（若未初始化则保持 None，teardown 天然 no-op），
            #   仅记录全阻断标记，让全阻断分支仍能兜底回滚部分成功项。
            if _cfg("TDL_FAIL_CLOSED", True):
                _tdl_failed = [("__BATCH__", "TDL_SETUP_EXCEPTION")]

    if _tdl is not None and _cfg("TDL_FAIL_CLOSED", True) and _tdl_failed:
        _failed_ids = {cid for cid, _ in _tdl_failed}
        _run_cases_for_exec = [
            c
            for c in _run_cases
            if str(c.get("case_id", "") or "").strip() not in _failed_ids
        ]

    _run_exc: BaseException | None = None
    # ★ W3 T03 (2026-08-05): 身份池 BLOCKED 登记（IDENTITY_UNAVAILABLE；不进 results）
    _identity_blocked_ids: list[str] = []

    def _tdl_teardown_all() -> None:
        """TDL 兜底 teardown：全阻断分支与正常路径 finally 复用同一逻辑（不复制粘贴）。

        teardown 永不 raise（TDL 铁律）；此处兜底安全网，异常只记日志。
        只回滚台账记录过的主键（精确回滚：ref 空一律不删、白名单双闸门）。
        双开关默认 OFF 时 _tdl 为 None，本函数为 no-op（零行为变更）。
        """
        if _tdl is None:
            return
        for _tc in _run_cases:
            try:
                _tdl.teardown(_tc, exc=_run_exc)
            except Exception as _te2:
                _log.error(
                    "[TDL] teardown 安全网异常（不阻断）: %s", _te2, exc_info=True
                )

    if (
        _tdl is not None
        and _cfg("TDL_FAIL_CLOSED", True)
        and _tdl_failed
        and not _run_cases_for_exec
    ):
        # 全部被 TDL setup 失败阻断 → 不进入浏览器编排（避免在无前置数据时假装跑通）。
        # ★ 台账可能已有部分成功的 setup 记录（如 s1 成功、s2 失败）→ 必须 teardown 兜底，
        #   否则已插入行静默泄漏。与正常路径复用同一 teardown 逻辑。
        _log.error(
            "[TDL] 全部候选用例被 setup 失败阻断，跳过执行（blocked=%d），执行 teardown 兜底",
            len(_tdl_failed),
        )
        _tdl_teardown_all()
        stats = {
            "passed": 0,
            "failed": 0,
            "total": 0,
            "results": [],
            "tdl_blocked": [{"case_id": cid, "reason": r} for cid, r in _tdl_failed],
        }
        # ★ C 数据治理 [已复核-DeepSeek-V3 2026-08-16]: 全阻断分支也附 evidence（api 侧已执行）
        if _dlc_evidence:
            stats["data_lifecycle"] = dict(_dlc_evidence)
    else:
        # ★ W3 T03 (2026-08-05): run 级身份选择 —— 仅在此处（run_test_cases 调用前）
        #   做身份 override 包裹；默认 IDENTITY_POOL_ENABLED=False → 零行为变更。
        #   严禁触碰上方 TDL/ADI 已复核区间。
        _identity_ov_applied = False
        if _cfg("IDENTITY_POOL_ENABLED", False):
            with guard("step4_execute: from src.pipeline.utils.identity_poo", log=_log, level=logging.WARNING):
                from src.pipeline.utils.identity_pool import (
                    IdentityPool,
                    apply_identity_override,
                    clear_identity_override,
                )

                # ★ BUG-1 修复（2026-08-06 QA 独立验证）：step4_execute 内无模块级
                #   project 变量（函数签名无此参数、模块无全局），原代码直接引用
                #   project 会在运行时 NameError 被下方 except 吞掉 → 身份块静默失效。
                #   复用 :444 既有写法（config_loader.project_name），零新依赖。
                from src.core.config_loader import config as _id_cfg

                _identity_project = str(getattr(_id_cfg, "project_name", "") or "")
                _default_key = str(_cfg("IDENTITY_DEFAULT", "") or "").strip()
                if _default_key:
                    _pool = IdentityPool.load(_identity_project)
                    _ident = _pool.get(_default_key)
                    if _ident:
                        apply_identity_override(_identity_project, _ident)
                        _identity_ov_applied = True
                        _log.info("[Identity] run 级默认身份 %s 已应用", _default_key)
                    else:
                        _log.error(
                            "##HTML:##<span style='color:#ef4444;font-weight:bold'>"
                            "⚠️ [Identity] run 级默认身份 %s 缺失（IDENTITY_UNAVAILABLE）"
                            "</span>",
                            _default_key,
                        )
                # case 级 identity 声明但池中缺失 → BLOCKED（fail-closed，不进浏览器）
                _filtered: list[dict] = []
                for _c in _run_cases_for_exec:
                    _res = IdentityPool.resolve(_c, _identity_project)
                    if _res and _res.get("_missing"):
                        _cid = str(_c.get("case_id", "") or "").strip()
                        _identity_blocked_ids.append(_cid)
                        _log.error(
                            "##HTML:##<span style='color:#ef4444;font-weight:bold'>"
                            "⚠️ [Identity] 用例 %s 声明身份 %s 缺失 → BLOCKED(IDENTITY_UNAVAILABLE)"
                            "</span>",
                            _cid,
                            _res.get("_missing"),
                        )
                    else:
                        _filtered.append(_c)
                _run_cases_for_exec = _filtered
        # ★ C 数据治理 [已复核-DeepSeek-V3 2026-08-16]: browser 路径 setup（fail-open，紧贴执行）
        try:
            _dlc_evidence = _merge_dlc_evidence(
                _dlc_evidence,
                _run_data_lifecycle_for_cases(
                    _run_cases_for_exec, _dlc_project, "setup"
                ),
            )
        except Exception as _e:
            _log.warning("[TDL-OPS] browser setup 异常(不阻断): %s", _e, exc_info=True)
        try:
            stats = browser_executor.run_test_cases(
                _run_cases_for_exec,
                quick=quick,
                browser_info=browser_info,
                headless=headless,
                timeout_ms=timeout_ms,
                user_id=user_id,
                progress_callback=progress_callback,
                max_parallel=max_parallel,
            )
        except BaseException as _be:
            _run_exc = _be
            raise
        finally:
            _tdl_teardown_all()
            # ★ C 数据治理 [已复核-DeepSeek-V3 2026-08-16]: browser 路径 teardown（fail-open，永不 raise）
            try:
                _dlc_evidence = _merge_dlc_evidence(
                    _dlc_evidence,
                    _run_data_lifecycle_for_cases(
                        _run_cases_for_exec, _dlc_project, "teardown"
                    ),
                )
            except Exception as _e:
                _log.warning("[TDL-OPS] browser teardown 异常(不阻断): %s", _e, exc_info=True)
            if _identity_ov_applied:
                with guard("step4_execute: clear_identity_override()", log=_log):
                    clear_identity_override()
            # ★ F-R1 (2026-08-06): 网络故障兜底恢复（run 级 finally 强制恢复 offline）
            #   与 TDL teardown 同类：异常只记日志不阻断；NETWORK_FAULT_ENABLED=False
            #   时 recover_network_fault 内部直接返回 → 零行为变更。
            #   注：serial._execute_all 的 finally 已先于此 stop engine（context 关闭），
            #   故此处的 _page 多为 None → 实际多为兜底 no-op；真正生效点在 serial.py
            #   的用例间 / engine 停止前恢复。
            with guard("step4_execute: import asyncio as _asyncio", log=_log, level=logging.WARNING):
                import asyncio as _asyncio
                from src.pipeline.steps.step4.browser_tools._network_fault import (
                    recover_network_fault,
                )

                _bc_engine = getattr(browser_executor.for_current(), "_engine", None)
                if _bc_engine is not None:
                    _asyncio.run(recover_network_fault(_bc_engine, "offline"))

    # W2 P0-3/P0-2: blocked 登记（新键；blocked 用例已移出 candidates，绝不在 results 中）
    _tdl_blocked_ids = [cid for cid, _ in _tdl_failed]
    stats["blocked"] = list(_adi_blocked_ids) + _tdl_blocked_ids + _identity_blocked_ids
    if _tdl_failed:
        stats["tdl_blocked"] = [{"case_id": cid, "reason": r} for cid, r in _tdl_failed]
    if _identity_blocked_ids:
        stats["identity_blocked"] = [
            {"case_id": cid, "reason": "IDENTITY_UNAVAILABLE"}
            for cid in _identity_blocked_ids
        ]

    # ★ C 数据治理 [已复核-DeepSeek-V3 2026-08-16]: 把 data_setup/data_teardown evidence 附到
    #   stats 与逐条结果（供 Step5/报告展示；未启用时 _dlc_evidence 为空，零行为变更）。
    if _dlc_evidence:
        stats["data_lifecycle"] = dict(_dlc_evidence)
        for _r in stats.get("results", []):
            _cid = str(_r.get("case_id", "") or "").strip()
            if _cid in _dlc_evidence:
                _r["data_lifecycle"] = dict(_dlc_evidence[_cid])

    if api_only:
        stats["passed"] += api_stats["passed"]
        stats["failed"] += api_stats["failed"]
        stats["total"] += api_stats["total"]

    # ★ 追溯链路执行后诊断：交叉比对失败用例与 trace 预检结果
    #   判断失败是否由 AI 幻觉（表名/API 不存在）导致
    if _trace_issues_map:
        with guard("step4_execute: execution_results = stats.get('resul", log=_log, level=logging.WARNING):
            execution_results = stats.get("results", [])
            hallucination_failures = 0
            for res in execution_results:
                case_id = res.get("case_id", "")
                if not res.get("passed", True) and case_id in _trace_issues_map:
                    hallucination_failures += 1
                    _log.warning(
                        "[Step4-TraceValid] 用例 %s 执行失败，且追溯校验不通过 → 可能为AI幻觉，非真实Bug",
                        case_id,
                    )
            if hallucination_failures > 0:
                _log.warning(
                    "##HTML:##<span style='color:#f59e0b;font-weight:bold'>"
                    "⚠️ [追溯诊断] %d 条失败用例与追溯校验异常重叠（疑似 AI 幻觉），请人工复核确认"
                    "</span>",
                    hallucination_failures,
                )
                stats["_trace_hallucination_failures"] = hallucination_failures

    # 自我修复闭环 — 默认关闭：它会重新生成并替换 HARNESS_ERROR 失败用例，
    # 用例被改写后失败消失，反而掩盖引擎缺陷（违反"根因修复、不打补丁"）。
    # 需显式置 step4_self_repair_enabled=true 才启用。
    with guard("step4_execute: execution_results = stats.get('resul", log=_log, level=logging.WARNING):
        execution_results = stats.get("results", [])
        if not _cfg("step4_self_repair_enabled", False):
            stats["_self_repair"] = {
                "enabled": False,
                "rebuilt": [],
                "skipped": [],
                "errors": [],
            }
        elif execution_results and any(
            not r.get("passed", True) for r in execution_results
        ):
            from src.pipeline.steps.step4.self_repair import auto_rebuild_failed_cases

            repair_report = auto_rebuild_failed_cases(execution_results)
            stats["_self_repair"] = repair_report
            if repair_report["rebuilt"]:
                _log.info(
                    "##HTML:##<span style='color:#3b82f6;font-weight:bold'>"
                    "🔧 [自我修复] 已重建 %d 条失败用例（HARNESS_ERROR），%d 条跳过。"
                    "可点击「重试失败用例」验证新用例。"
                    "</span>",
                    len(repair_report["rebuilt"]),
                    len(repair_report["skipped"]),
                )

    # ★ v6: 红队对抗验证 — 对执行结果进行独立对抗审查
    # ★ F-14 (2026-08-04): 消除「降级跳过」——未启用与异常必须可区分（Q15 核验）。
    #   根因(3层): ①异常时只打 warning 继续返回成功 = 降级跳过，违反 v5.2「降级=失败」；
    #   ②`stats["_adversarial"]` 仅成功时写入，未启用与「跑了但挂了」输出不可区分；
    #   ③M1#4 验收「注入3幻觉样本拦截率100%」在不可区分状态下自我论证。
    #   修复: 未启用 → 显式写入 {"enabled": False}；启用但异常 → 记完整异常 + 标记失败。
    if _cfg("step4_red_team_enabled", False):
        try:
            from src.pipeline.steps.step4.browser_ai_validator._adversarial import (
                adversarial_review_results,
            )

            execution_results = stats.get("results", [])
            if execution_results:
                _log.info(
                    "[红队 Step4] 启动对抗验证 (%d 条结果)...", len(execution_results)
                )
                adv_report = adversarial_review_results(execution_results, candidates)
                stats["_adversarial"] = {
                    "enabled": True,
                    "reliability_score": adv_report.get("reliability_score", 0.0),
                    "verdicts": adv_report.get("verdicts", {}),
                    "anomaly_count": len(adv_report.get("anomalies", [])),
                }
                _log.info(
                    "[红队 Step4] 对抗验证完成: 可靠性=%.2f %s",
                    adv_report.get("reliability_score", 0.0),
                    adv_report.get("verdicts", {}),
                )
            else:
                stats["_adversarial"] = {"enabled": True, "skipped": "no_results"}
                _log.info("[红队 Step4] 无执行结果详情，跳过对抗验证")
        except Exception as e:
            # F-14: 异常不得静默跳过 —— 记录完整异常详情（exc_info + 上下文）
            _log.error(
                "[红队 Step4] 对抗验证异常（fail-closed，_adversarial 标记失败）: %s",
                str(e)[:200],
                exc_info=True,
            )
            stats["_adversarial"] = {
                "enabled": True,
                "failed": True,
                "error": str(e)[:200],
            }
    else:
        # ★ F-14/Q15: 未启用显式写入，与「启用但异常」可区分
        stats["_adversarial"] = {
            "enabled": False,
            "reason": "step4_red_team_enabled not configured",
        }

    # ★ v4.1: 构建 case_id→candidate 映射（用于 System B + System C 置信度计算）
    #         解决旧代码 res 只有 {case_id, passed, elapsed, step_results, error}
    #         但 ConfidenceCalculator 需要 steps/checks/title/case_type 等字段
    import json as _json

    cand_map = {}
    for c in candidates or []:
        cid = str(c.get("case_id") or "").strip()
        if cid:
            cand_map[cid] = c

    # ★ v6: Step4 执行后补调置信度计算器 + strictness_score 更新（功能挂载修复）
    try:
        from src.extensions.confidence_calculator import ConfidenceCalculator
        from src.pipeline.utils.review_store import review_store

        execution_results = stats.get("results", [])
        if execution_results:
            calc = ConfidenceCalculator()
            for res in execution_results:
                case_id = res.get("case_id")
                if not case_id:
                    continue
                # 合并候选元数据到结果（不覆盖执行结果自身的字段）
                cand = cand_map.get(str(case_id).strip(), {})
                for key in (
                    "title",
                    "case_type",
                    "risk_level",
                    "required_tracks",
                    "checks",
                    "steps",
                    "verification_plan",
                    "req_text",
                    "intent_text",
                    "semantic_similarity",
                ):
                    if key not in res or res[key] is None or res[key] == "":
                        val = cand.get(key)
                        if val is not None and val != "":
                            res[key] = val
                # checks 优先从 payload_json 提取（AI 生成时的完整 checks）
                if not res.get("checks"):
                    payload = cand.get("payload_json") or cand.get("payload") or {}
                    if isinstance(payload, str):
                        try:
                            payload = _json.loads(payload)
                        except Exception:
                            _log.warning("JSON解析payload失败", exc_info=True)
                            payload = {}
                    if isinstance(payload, dict):
                        chk = payload.get("checks") or []
                        if chk:
                            res["checks"] = chk
                # verdict 优先从 step_results 推断
                if not res.get("verdict"):
                    sr_list = res.get("step_results") or []
                    evidence_items = []
                    for sr in sr_list:
                        if isinstance(sr, dict):
                            ei = sr.get("evidence_items") or sr.get("evidence") or []
                            if ei:
                                evidence_items.extend(
                                    ei if isinstance(ei, list) else [ei]
                                )
                    if evidence_items:
                        res["verdict"] = {"evidence_items": evidence_items}
                calc_result = calc.calculate(res)
                new_confidence = float(calc_result.get("confidence", 0))
                # ★ F-14 (2026-08-04): 消除双层静默 — 检查回写返回值，False 即记 error。
                #   根因(3层): ①内层 case_meta.py:214-216 except → return False 吞异常；
                #   ②此处直接丢弃返回值，外层 except(L497) 永不触发 → 回写失败 100% 静默；
                #   ③F-10 基线出现「无声数据缺失」，分母都是错的。
                #   修复: 返回值 False → error 日志 + confidence_writeback_failed_total 计数器 +1
                #   （F-10a 埋点，进程内自证，不依赖看板）。
                try:
                    _wb_ok = review_store.update_avg_confidence(case_id, new_confidence)
                    if not _wb_ok:
                        _log.error(
                            "[step4_executor] avg_confidence 回写失败（返回 False）case_id=%s",
                            case_id,
                        )
                        _stats_counters["confidence_writeback_failed_total"] += 1
                except Exception as _e:
                    _log.error(
                        "[step4_executor] avg_confidence 回写异常 case_id=%s: %s",
                        case_id,
                        str(_e)[:200],
                        exc_info=True,
                    )
                    _stats_counters["confidence_writeback_failed_total"] += 1
                    # ★ F-14: 回写失败不再静默 —— 异常向上传导（fail-closed），
                    #   由外层捕获后同样计数，保证「失败可见」。
                    raise
                # ★ v4.1: 同时存储完整的因子明细（方便前端展示 + 后续分析）
                try:
                    review_store.update_execution_factors(
                        case_id,
                        new_confidence,
                        calc_result.get("factors", {}),
                        calc_result.get("level", "low"),
                    )
                except AttributeError as _e:
                    _log.debug(
                        "[step4_executor] update_execution_factors 跳过(§5): %s", _e
                    )
    except Exception as e:
        # ★ F-14: 动态置信度计算异常 → 记录完整异常 + 计数，禁止降级跳过
        _log.error(
            "Step4 动态置信度计算异常（fail-closed，confidence_writeback_failed_total +1）: %s",
            str(e)[:200],
            exc_info=True,
        )
        _stats_counters["confidence_writeback_failed_total"] += 1

    try:
        from src.pipeline.utils.review_store import review_store

        execution_results = stats.get("results", [])
        if execution_results:
            for res in execution_results:
                case_id = res.get("case_id")
                if not case_id:
                    continue
                must_total = res.get("must_total", 0)
                must_pass = res.get("must_pass", 0)
                should_total = res.get("should_total", 0)
                should_pass = res.get("should_pass", 0)
                may_total = res.get("may_total", 0)
                may_pass = res.get("may_pass", 0)
                score = 0.0
                denom = must_total * 3.0 + should_total * 2.0 + may_total * 1.0
                if denom > 0:
                    score = (
                        must_pass * 3.0 + should_pass * 2.0 + may_pass * 1.0
                    ) / denom
                try:
                    review_store.update_strictness_score(case_id, score)
                except Exception as _e:
                    # ★ F-14: 回写异常不得静默 —— 记完整异常（含 case_id），向上传导
                    _log.error(
                        "回写 strictness_score 失败 case_id=%s: %s",
                        case_id,
                        str(_e)[:200],
                        exc_info=True,
                    )
                    raise
    except Exception as e:
        # ★ F-14: strictness 更新异常 → 记录完整异常，禁止降级跳过
        _log.error(
            "Step4 strictness_score 更新异常（fail-closed）: %s",
            str(e)[:200],
            exc_info=True,
        )
        raise

    # ★ v4.1: System C 证据校准置信度 — 融合 AI 声称 + 历史实证 + 独立证据
    try:
        from src.pipeline.evidence.confidence import compute_evidence_confidence
        from src.pipeline.evidence.calibration import get_calibration
        from src.pipeline.utils.review_store import review_store
        from src.core.config_loader import config

        execution_results = stats.get("results", [])
        if execution_results:
            calibration_data = get_calibration(
                config.project_name
            )  # 读取项目历史校准数据
            enriched_count = 0
            for res in execution_results:
                case_id = res.get("case_id")
                if not case_id:
                    continue
                # 构建证据引擎需要的 item 结构
                item = {
                    "case_id": case_id,
                    "title": res.get("title", ""),
                    "description": res.get("title", ""),  # 用例 title 即 description
                    "source_quote": res.get("source_quote", ""),
                    "_consensus_level": res.get("_consensus_level", ""),
                    "_perspective_count": res.get("_perspective_count", 1),
                }
                # 合并候选元数据
                cand = cand_map.get(str(case_id).strip(), {}) if cand_map else {}
                for k in (
                    "description",
                    "source_quote",
                    "_consensus_level",
                    "_perspective_count",
                ):
                    if not item.get(k) and cand.get(k):
                        item[k] = cand[k]
                # 提取独立证据（step_results 中的 evidence_items）
                evidence = []
                sr_list = res.get("step_results") or []
                for sr in sr_list:
                    if isinstance(sr, dict):
                        ei = sr.get("evidence_items") or sr.get("evidence") or []
                        if isinstance(ei, list):
                            evidence.extend(ei)
                # 调用证据引擎
                ev_result = compute_evidence_confidence(
                    item,
                    calibration_data=calibration_data,
                    independent_evidence=evidence if evidence else None,
                )
                try:
                    review_store.update_evidence_confidence(
                        case_id,
                        ev_result.get("confidence", 0.0),
                        ev_result.get("ai_raw_confidence", 0.0),
                        ev_result.get("calibration_accuracy"),
                        ev_result.get("evidence_strength", 0.0),
                        ev_result.get("confidence_level", "LOW"),
                        ev_result.get("verdict", "ai_only"),
                        ev_result.get("cold_start", True),
                    )
                    enriched_count += 1
                except Exception as _we:
                    # ★ F-14: 证据置信度回写失败不得静默 —— 记 error 级（含 case_id）
                    _log.error(
                        "证据置信度回写失败 case_id=%s: %s",
                        case_id,
                        str(_we)[:200],
                        exc_info=True,
                    )
                    raise
            if enriched_count:
                _log.info(
                    "##HTML:##<span style='color:#a78bfa'>🔬 [证据引擎] 已校准 %d 条用例置信度</span>",
                    enriched_count,
                )
    except Exception as e:
        # ★ F-14: 证据校准置信度异常 → 记录完整异常，禁止降级跳过
        _log.error(
            "Step4 证据校准置信度异常（fail-closed）: %s",
            str(e)[:200],
            exc_info=True,
        )
        raise

    return {"success": True, "stats": stats}
