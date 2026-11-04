# -*- coding: utf-8 -*-
"""[已复核 2026-09-04] 统一验证计划（VerificationPlan）与单一执行入口。

★ 为什么需要这一层（消除"两条平行执行链"）：
  此前引擎存在两套并行的执行入口——
    - 链 A（主 AI 流水线）：step4_executor → api_only_executor，吃主流水线的
      ``verification_plan``（ui/api/db 三轨）；
    - 链 B（实证套件 + 用例直传）：TargetEmpiricalVerifier(cfg) 吃靶子配置 +
      浏览器用例 / 权限探针。
  两者重复了"按 target_key 解析配置 + 覆盖 cases/authz"的逻辑，且输入形态不统一，
  久而久之会产生行为漂移（同一靶子两种跑法结果可能不一致）。

  本模块把"要验证什么"抽象为与靶子无关的 **VerificationPlan**，并提供 **execute(plan)**
  单一入口：无论计划来自 AI 生成、人类上传（case_upload）、还是从主流水线
  verification_plan 桥接，**都先归一化成同一种 VerificationPlan，再交给同一套执行器**。
  这是"代码 = SOP"在实证层的具体落地：一种计划、一个入口、统一报告。

★ 设计约束：
  - VerificationPlan 不依赖任何靶子业务逻辑；target 细节全部下沉到 target_cfgs 与
    各 runner（通用性保障）。
  - execute() 是门面（facade）：解析配置 + 套用覆盖 → 委托 TargetEmpiricalVerifier，
    不重复实现验证逻辑（最小变更、不破坏既有 35 个实证用例）。
  - 不假绿、不静默：任何执行异常由 TargetEmpiricalVerifier 如实返回
    all_pass=False（遵守 NO_ASSERTION_DOWNGRADE）。
"""
from __future__ import annotations
from src.core.guarded import guard

import logging
from dataclasses import dataclass, field
from typing import Any

_log = logging.getLogger(__name__)


# 主流水线 verification_plan 中，用于判定"拒绝类"断言的关键词。
_DENY_HINTS = ("拒绝", "无权", "403", "forbidden", "FORBIDDEN", "unauthorized")
# 浏览器用例中，用于判定"应填非法值"的关键词。
_INVALID_HINTS = ("非法", "无效", "错误", "失败", "为空", "缺失")


@dataclass
class VerificationPlan:
    """与靶子无关的验证计划（引擎对"要验证什么"的唯一标准表达）。

    Attributes:
        target_key: 靶子标识（必填），用于从 target_cfgs 注册表解析完整配置。
        mode: 执行模式 ``browser`` | ``api`` | ``authz``（缺省 browser）。
        cases: 实证浏览器/API 用例集（[{case_id, title, fill_invalid}, ...]）。
        authz_probes: RBAC 权限探针（[{id, role, method, path, expected}, ...]）。
        report_path: 报告落盘路径（可选）。
        cfg_overrides: 直接提供/覆盖的靶子配置字典（最高优先级；缺省则按
            target_key 从注册表解析）。便于高级用户显式注入 auth/launch/selectors。
        title: 人类可读标签（报告/CLI 展示用，可选）。
    """

    target_key: str
    mode: str = "browser"
    cases: list[dict] | None = None
    authz_probes: list[dict] | None = None
    report_path: str | None = None
    cfg_overrides: dict | None = None
    title: str | None = None
    meta: dict | None = None

    def implies_authz(self) -> bool:
        """该计划是否包含权限验证（探针非空，或覆盖配置里已声明 authz）。"""
        if self.authz_probes:
            return True
        return bool((self.cfg_overrides or {}).get("authz"))


def execute(plan: VerificationPlan) -> dict:
    """单一执行入口：验证计划 → 解析靶子配置 → 交给 TargetEmpiricalVerifier 如实验证。

    这是实证层所有执行路径的统一收口。AI 生成、人类上传、主流水线桥接，最终都走这里。

    Args:
        plan: 归一化后的 VerificationPlan。
    Returns:
        TargetEmpiricalVerifier.run() 的报告 dict（含 summary.all_pass 等）。
    """
    # 延迟导入，避免模块顶层硬依赖 target_cfgs / target_empirical（防止循环导入、
    # 也避免非必要时加载 playwright 相关子模块）。
    from src.pipeline.steps.step4.empirical.target_cfgs import get_target_cfg
    from src.pipeline.steps.step4.empirical.target_empirical import (
        TargetEmpiricalVerifier,
    )

    if plan.cfg_overrides:
        cfg: dict[str, Any] = dict(plan.cfg_overrides)
        # 部分覆盖兼容（2026-09-05）：cfg_overrides 语义为「覆盖」，本应支持增量。
        # 修复前只要传入 cfg_overrides 就整体替换 cfg → 仅传 {"perf": ...} 这类
        # 增量覆盖会丢失 target_key/mode/selectors 等必填键（如 --perf-recalibrate），
        # 既破坏通用性（新靶子无法受益），也违背 doistrument 文档"覆盖"定义。
        # 现：cfg_overrides 不含 target_key 时视为增量覆盖，从注册表解析基础配置后合并
        # （显式传入的键仍为最高优先级）；含 target_key 则仍视为完整配置，语义向后兼容。
        if not cfg.get("target_key"):
            _base = get_target_cfg(plan.target_key)
            cfg = {**_base, **cfg}
    else:
        cfg = get_target_cfg(plan.target_key)

    # 模式以计划为准（显式给出时覆盖注册表默认）。
    if plan.mode and plan.mode != "browser":
        cfg["mode"] = plan.mode

    if plan.cases:
        cfg["cases"] = plan.cases
    if plan.authz_probes:
        authz = dict(cfg.get("authz") or {})
        # 用户/上游提供的探针优先；permission_matrix 沿用靶子原配置（若有）。
        authz["probes"] = plan.authz_probes
        cfg["authz"] = authz
    if plan.report_path:
        cfg["report_path"] = plan.report_path

    _log.info(
        "[plan.execute] target=%s mode=%s cases=%d probes=%d",
        plan.target_key, cfg.get("mode", plan.mode),
        len(plan.cases or []), len(plan.authz_probes or []),
    )

    # ── 流水线强化专项 T6(2026-08-24): 双路径路由决策固化 ──
    #    保留 execute(plan) 唯一门面；按确定性规则（有 _explore_task → AgentExplorer 探索；
    #    无 → 经验/探针 step loop）记录路由决策日志与 meta.execution_path。
    #    纯记录，不改变执行语义、不重复执行（同一用例单次只走一条路径）。
    with guard("execute: from src.pipeline.steps.step4.execut", log=_log, level=logging.WARNING):
        from src.pipeline.steps.step4.execution_router import ExecutionRouter

        routing = ExecutionRouter.describe(plan)
        meta = dict(plan.meta or {})
        meta["execution_path"] = routing
        plan.meta = meta
        _log.info(
            "[plan.execute] T6 路由决策 total=%d agent_explorer=%d empirical_step_loop=%d",
            routing["total_cases"],
            routing["agent_explorer"],
            routing["empirical_step_loop"],
        )

    return TargetEmpiricalVerifier(cfg).run()


def from_upload_parsed(
    parsed: dict,
    target_key: str,
    *,
    report_path: str | None = None,
    cfg: dict | None = None,
) -> VerificationPlan:
    """把 case_upload.parse_cases_file 的解析结果包装成 VerificationPlan。

    Args:
        parsed: parse_cases_file 的返回值 {target_key, cases, authz_probes}。
        target_key: 最终靶子 key（可覆盖 parsed 内的取值）。
        report_path / cfg: 透传到计划。
    """
    cases = parsed.get("cases") or None
    probes = parsed.get("authz_probes") or None
    return VerificationPlan(
        target_key=target_key,
        # 只要有探针，execute() 实际会跑 authz 模式（cases 被忽略），
        # 故 mode 与真实行为对齐，避免沉默丢弃浏览器用例。
        mode="authz" if probes else "browser",
        cases=cases,
        authz_probes=probes,
        report_path=report_path,
        cfg_overrides=cfg,
        title="uploaded-cases",
    )


def from_verification_plan(
    vp: dict,
    target_key: str,
    *,
    report_path: str | None = None,
) -> VerificationPlan:
    """桥接器：把主流水线 verification_plan（ui/api/db 三轨）转为实证 VerificationPlan。

    ★ 映射规则（明确、最小化、可测；非无损桥接，db 轨如实标注跳过）：
      - vp["api"] 中带 ``target``(路径)+``method`` 的条目 → 权限探针：
          默认 role="user"，expected 由 assertion 推断（含拒绝类关键词→deny，否则 allow）。
      - vp["ui"] 条目 → 实证浏览器用例（case_id=UP-<n>，fill_invalid 由 assertion
          含非法/无效/错误/失败/为空 推断）。
      - vp["db"] 轨：实证层暂不支持 DB 断言，如实跳过（返回 plan 时该部分不进入
          cases/probes；调用方可经 meta 看到 skipped 计数）。

    Args:
        vp: 主流水线 verification_plan dict（{ui:[...], api:[...], db:[...]}）。
        target_key: 靶子标识（实证层据此解析 base_url / auth / launch）。
    Returns:
        VerificationPlan（含 meta 标注哪些轨被桥接/跳过）。
    """
    vp = vp or {}
    api = vp.get("api") or []
    ui = vp.get("ui") or []
    db = vp.get("db") or []

    probes: list[dict] = []
    for i, item in enumerate(api):
        if not isinstance(item, dict):
            continue
        path = str(item.get("target") or "").strip()
        if not path:
            continue
        method = str(item.get("method") or "GET").strip().upper()
        assertion = str(item.get("assertion") or "")
        expected = "deny" if any(k in assertion for k in _DENY_HINTS) else "allow"
        probes.append({
            "id": f"VP-A{i+1:02d}",
            "role": "user",
            "method": method,
            "path": path,
            "expected": expected,
        })

    cases: list[dict] = []
    for i, item in enumerate(ui):
        if not isinstance(item, dict):
            continue
        assertion = str(item.get("assertion") or item.get("text") or "")
        fill_invalid = any(k in assertion for k in _INVALID_HINTS)
        case_id = str(item.get("case_id") or f"UP-{i+1:02d}")
        cases.append({
            "case_id": case_id,
            "title": assertion[:60],
            "fill_invalid": fill_invalid,
        })

    meta = {
        "bridged_api": len(probes),
        "bridged_ui": len(cases),
        "skipped_db": len(db) if isinstance(db, list) else 0,
    }
    _log.info(
        "[plan.from_verification_plan] 桥接 api=%d ui=%d 跳过db=%d → target=%s",
        meta["bridged_api"], meta["bridged_ui"], meta["skipped_db"], target_key,
    )

    return VerificationPlan(
        target_key=target_key,
        # 只要有探针，execute() 实际跑 authz 模式（ui→cases 被忽略），mode 对齐真实行为。
        mode="authz" if probes else "browser",
        cases=cases or None,
        authz_probes=probes or None,
        report_path=report_path,
        title="from-verification-plan",
        meta=meta,
    )
