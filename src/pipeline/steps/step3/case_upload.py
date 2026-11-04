# -*- coding: utf-8 -*-
"""[未复核-需 GLM/DeepSeek 把关] 用例直传执行（从 step3 开始，跳过 step1/step2 生成）。

★ 目的：让用户直接上传用例并立即执行，无需先走需求分析 / AI 用例生成。
  引擎作为"通用、媲美高级测试工程师"的工具，既要能自动生成，也要能接受
  人工/外部系统下发的用例并如实验证。

★ 能力：
  1. 下载用例模版：write_template() 输出 CSV / JSON，含 2 条示例行
     （一条浏览器 CRUD 正向用例 + 一条 RBAC 权限探针），用户照葫芦画瓢。
  2. 解析上传文件：parse_cases_file() 支持 CSV / JSON，将行翻译为引擎用例
     （empirical cases 或 authz probes）。
  3. 直接执行：run_uploaded_cases() 解析 → 包装为 VerificationPlan → 经统一
     execute(plan) 入口交由 TargetEmpiricalVerifier 真实验证（浏览器 / API /
     权限矩阵），返回报告。与主流水线共用同一执行器，消除重复与漂移。

★ 用例格式（CSV 表头，字段可缺省）：
  case_id,title,target_key,fill_invalid,expected_result,role,method,path,expected_code,notes
    - 含 case_id 的行 → 实证用例（P01/N01/B01/D01/A01），fill_invalid=布尔。
    - 含 role+method+path 的行 → 权限探针，expected_result=allow|deny。
  两者可混排；target_key 取首个非空值（或 run_uploaded_cases 的 target 参数）。
"""
from __future__ import annotations

import csv
import json
import logging
from pathlib import Path

_log = logging.getLogger(__name__)

# ── 用例模版（含 3 条示例行：浏览器 CRUD + RBAC 权限探针 + 越权探针）──────
CASE_TEMPLATE_FIELDS: list[str] = [
    "case_id", "title", "target_key", "version", "fill_invalid",
    "expected_result", "role", "method", "path", "expected_code", "notes",
]

# ★ 用例绑定专项(2026-08): 当前被测系统 key（write_template / check_version_match 引用）。
#   通用默认：空串 = 未设置，不绑定任何具体靶子（历史曾默认 smartshop）。
#   必须由调用方显式 set_current_target() / 传 current_target 参数指定。
_CURRENT_TARGET_KEY: str = ""


def set_current_target(target_key: str) -> None:
    """设置"当前被测系统"标识（模板预填 / 匹配校验共用）。

    Args:
        target_key: 当前被测系统的 target_key（任一已注册靶子，如
            hospital_sut / api_demo / spa_demo / smartshop / authz_sut）。
        传空串则清空（回到"未设置"状态）。
    """
    global _CURRENT_TARGET_KEY
    _CURRENT_TARGET_KEY = (target_key or "").strip()


# 版本占位（无 target_cfg 时回退）
def _current_target_version() -> str:
    """读取当前被测系统的 version（来自 target_cfgs 注册表）。"""
    if not _CURRENT_TARGET_KEY:
        return ""
    try:
        from src.pipeline.steps.step4.empirical.target_cfgs import get_target_cfg
        cfg = get_target_cfg(_CURRENT_TARGET_KEY)
        return cfg.get("version", "1.0.0")
    except Exception:
        return "1.0.0"


CASE_TEMPLATE_ROWS: list[dict] = [
    {
        "case_id": "P01",
        "title": "新增商品-正向冒烟",
        "target_key": "",
        "version": "1.0.0",
        "fill_invalid": "false",
        "expected_result": "PASS",
        "role": "", "method": "", "path": "", "expected_code": "",
        "notes": "浏览器 CRUD 正向：新增后列表+1",
    },
    {
        "case_id": "",
        "title": "RBAC-用户查看商品(应允许)",
        "target_key": "",
        "version": "1.0.0",
        "fill_invalid": "",
        "expected_result": "allow",
        "role": "user", "method": "GET", "path": "/api/products",
        "expected_code": "200",
        "notes": "权限探针：user 拥有 product:view → 允许",
    },
    {
        "case_id": "",
        "title": "越权-用户访问他人资源(应拒绝)",
        "target_key": "",
        "version": "1.0.0",
        "fill_invalid": "",
        "expected_result": "allow_self_only",
        "role": "user", "method": "GET", "path": "/api/users/2",
        "expected_code": "403",
        "notes": "越权探针：低权用户访问他人资源应被拒（捕获水平越权）；"
                 "若靶子返回200→引擎如实FAIL(检出越权漏洞)",
    },
]


def write_template(dest_path: str, fmt: str = "csv", current_target: str | None = None) -> str:
    """写出用例上传模版（含 3 条示例行，预填 target_key + version）。

    ★ 用例绑定专项(2026-08): 模板中 target_key / version 列预填当前被测系统的
        真实值，避免用户手填错系统标识或版本。
    ★ 通用默认：不内置任何靶子默认。当前被测系统未设置（_CURRENT_TARGET_KEY 为空
        且未传 current_target）时如实报错，绝不静默生成空 target 模板。

    Args:
        dest_path: 落盘路径（.csv / .json）。
        fmt: "csv" | "json"。
        current_target: 当前被测系统 target_key；不传则使用 set_current_target 设定的值。
    Returns:
        实际写出的路径（与 dest_path 一致）。
    """
    if current_target:
        set_current_target(current_target)
    tk = _CURRENT_TARGET_KEY
    if not tk:
        raise ValueError(
            "未指定当前被测系统 target_key：无法生成模板。"
            "请先 set_current_target(<target_key>) 或传入 current_target 参数。"
        )
    ver = _current_target_version()
    # 预填：用当前被测系统覆盖示例行的 target_key / version（统一一处，避免错配）
    rows = []
    for row in CASE_TEMPLATE_ROWS:
        r = dict(row)
        r["target_key"] = tk
        r["version"] = ver
        rows.append(r)

    dest = Path(dest_path)
    if fmt == "json":
        payload = [
            {k: (row[k] if k in row else "") for k in CASE_TEMPLATE_FIELDS}
            for row in rows
        ]
        dest.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    else:
        with dest.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=CASE_TEMPLATE_FIELDS)
            writer.writeheader()
            for row in rows:
                writer.writerow({k: row.get(k, "") for k in CASE_TEMPLATE_FIELDS})
    _log.info("[case_upload] 模版已写出: %s (%s) target=%s version=%s", dest, fmt, tk, ver)
    return str(dest)


def _norm_bool(value: str) -> bool:
    return str(value).strip().lower() in ("1", "true", "yes", "y", "是")


def parse_cases_file(path: str) -> dict:
    """解析上传的用例文件（CSV 或 JSON）为引擎可执行结构。

    ★ 用例绑定专项(2026-08): 解析时采集文件内声明的 target_key 与 version
        （取首个非空值），供后续 check_version_match 做"被测系统+版本"匹配校验。

    Returns:
        {
          "target_key": str,            # 首个非空 target_key
          "version": str,               # 首个非空 version（缺失为空串）
          "cases": [ {...empirical case...} ],     # 含 case_id 的行
          "authz_probes": [ {...probe...} ],        # 含 role+method+path 的行
        }
    Raises:
        ValueError: 文件内声明的 target_key 与 version 任一缺失时（P0 严格拒绝）。
    """
    p = Path(path)
    raw_rows: list[dict] = []
    if p.suffix.lower() == ".json":
        data = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data = data.get("cases", [data])
        for item in data:
            raw_rows.append({k: str(item.get(k, "")) for k in CASE_TEMPLATE_FIELDS})
    else:
        with p.open(encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh)
            for r in reader:
                raw_rows.append({k: (r.get(k) or "") for k in CASE_TEMPLATE_FIELDS})

    target_key = ""
    version = ""
    cases: list[dict] = []
    authz_probes: list[dict] = []
    for i, row in enumerate(raw_rows):
        tk = (row.get("target_key") or "").strip()
        if tk and not target_key:
            target_key = tk
        ver = (row.get("version") or "").strip()
        if ver and not version:
            version = ver
        case_id = (row.get("case_id") or "").strip()
        role = (row.get("role") or "").strip()
        method = (row.get("method") or "").strip()
        path = (row.get("path") or "").strip()

        if case_id:
            cases.append({
                "case_id": case_id,
                "title": (row.get("title") or "").strip(),
                "fill_invalid": _norm_bool(row.get("fill_invalid") or ""),
            })
        elif role and method and path:
            raw_expected = (row.get("expected_result") or "deny").strip().lower()
            # 支持的期望语义（对齐 AuthzMatrixVerifier）：
            #   allow / deny / allow_self_only(水平越权防护) / deny_but_suspect(垂直越权防护)
            _ALLOWED = ("allow", "deny", "allow_self_only", "deny_but_suspect")
            expected = raw_expected if raw_expected in _ALLOWED else "deny"
            ec = (row.get("expected_code") or "").strip()
            probe = {
                "id": f"UP-{i+1:02d}",
                "role": role,
                "method": method.upper(),
                "path": path,
                "expected": expected,
            }
            if ec:
                try:
                    probe["expected_code"] = int(ec)
                except ValueError as _e:
                    _log.debug("parse_cases_file best-effort 容忍: %s", str(_e)[:120])
            authz_probes.append(probe)
        # 两者皆缺的行忽略（空行/注释行）

    # ★ P0 严格校验：target_key 或 version 缺失即拒绝（绝不静默执行）
    if not target_key:
        raise ValueError(
            "用例文件缺少 target_key 字段：无法确定目标被测系统，已拒绝。"
            "请使用模板（含 target_key 列）或从 /step3-case-upload 下载预填模板。"
        )
    if not version:
        raise ValueError(
            "用例文件缺少 version 字段：无法确定目标被测系统版本，已拒绝。"
            "请使用模板（含 version 列）或从 /step3-case-upload 下载预填模板。"
        )

    return {
        "target_key": target_key,
        "version": version,
        "cases": cases,
        "authz_probes": authz_probes,
    }


def run_uploaded_cases(
    cases_file: str,
    target_key: str | None = None,
    report_path: str | None = None,
    cfg: dict | None = None,
) -> dict:
    """解析上传用例文件并直接执行（从 step3 开始，跳过 step1/step2 生成）。

    ★ 统一入口：解析结果被包装为与靶子无关的 ``VerificationPlan``，再交给
       ``execute(plan)`` 单一入口执行——与 AI 生成、主流水线桥接共用同一套执行器，
       消除"两条平行执行链"的行为漂移。

    Args:
        cases_file: 上传的 CSV/JSON 用例文件。
        target_key: 覆盖文件内指定的靶子 key。
        report_path: 报告落盘路径（可选）。
        cfg: 直接提供靶子配置（优先级最高；缺省则按 target_key 从注册表解析）。

    Returns:
        execute(plan) 的报告 dict（含 summary.all_pass 等）。
    """
    from src.pipeline.steps.step4.empirical.plan import (
        execute,
        from_upload_parsed,
    )

    parsed = parse_cases_file(cases_file)
    tk = target_key or parsed.get("target_key")
    if not tk:
        raise ValueError("无法确定 target_key：请在文件或 --target 参数中指定靶子")

    plan = from_upload_parsed(parsed, tk, report_path=report_path, cfg=cfg)
    _log.info(
        "[case_upload] 执行上传用例 target=%s cases=%d probes=%d（经统一 execute 入口）",
        tk, len(parsed["cases"]), len(parsed["authz_probes"]),
    )
    report = execute(plan)

    # ── 流水线强化专项 T1: 短路链 ledger 核对钩子 ──
    #    执行完成后逐用例写 engine_expected + compare（真值缺失标 truth_unavailable），
    #    不一致自动经状态机置 OPEN（disposition=fix_target）；report 附加 ledger_check。
    #    纯挂载钩子：不触碰 target_cfgs.py 探针语义，也不改 parse_cases_file 解析逻辑。
    try:
        from src.verification.hooks import VerificationHook

        hook = VerificationHook()
        report = hook.attach_to_report(report, parsed, tk)
    except Exception as hook_e:
        _log.warning(
            "[case_upload] ledger 核对钩子执行异常(不阻断执行结果): %s",
            str(hook_e)[:200],
        )
        report.setdefault("ledger_check", {
            "checked": 0,
            "consistent_true": 0,
            "consistent_false": 0,
            "truth_unavailable": 0,
            "mismatches": [],
            "skipped_reason": "hook_error: %s" % str(hook_e)[:120],
        })
    return report


# ═══════════════════════════════════════════════════════════════════════════
# ★ 用例绑定专项(2026-08): 被测系统 + 版本匹配校验（P0 核心）
# ═══════════════════════════════════════════════════════════════════════════

# 版本不匹配处置策略（P1-1）
#   reject: 拒绝执行（默认，P0 仅支持）
#   warn:   告警但继续，执行结果标记"版本不符"
#   force:  强制覆盖（仅限授权角色，本期预留）
MISMATCH_POLICY_REJECT = "reject"
MISMATCH_POLICY_WARN = "warn"
MISMATCH_POLICY_FORCE = "force"
MISMATCH_POLICIES = (MISMATCH_POLICY_REJECT, MISMATCH_POLICY_WARN, MISMATCH_POLICY_FORCE)


class VersionMatchResult:
    """被测系统 + 版本匹配校验结果（前后端同构响应）。

    Attributes:
        file_target: 用例文件声明的 target_key。
        file_version: 用例文件声明的 version。
        current_target: 当前被测系统 target_key。
        current_version: 当前被测系统 version。
        matched: 是否匹配（True=放行，False=需按 policy 处置）。
        policy: 不匹配时的处置策略（reject/warn/force）。
        message: 人类可读的校验说明。
    """

    def __init__(
        self,
        file_target: str = "",
        file_version: str = "",
        current_target: str = "",
        current_version: str = "",
        matched: bool = False,
        policy: str = MISMATCH_POLICY_REJECT,
        message: str = "",
    ) -> None:
        self.file_target = file_target
        self.file_version = file_version
        self.current_target = current_target
        self.current_version = current_version
        self.matched = matched
        self.policy = policy if policy in MISMATCH_POLICIES else MISMATCH_POLICY_REJECT
        self.message = message

    def to_dict(self) -> dict:
        """序列化为前后端同构 dict。"""
        return {
            "file_target": self.file_target,
            "file_version": self.file_version,
            "current_target": self.current_target,
            "current_version": self.current_version,
            "matched": self.matched,
            "policy": self.policy,
            "message": self.message,
        }

    def __repr__(self) -> str:  # pragma: no cover - 调试便利
        return (
            f"VersionMatchResult(matched={self.matched}, policy={self.policy}, "
            f"file={self.file_target}@{self.file_version}, "
            f"current={self.current_target}@{self.current_version})"
        )


def check_version_match(
    file_target: str,
    file_version: str,
    policy: str = MISMATCH_POLICY_REJECT,
    current_target: str | None = None,
) -> VersionMatchResult:
    """校验"用例文件绑定"与"当前被测系统"的 target+version 是否一致。

    ★ 绝不静默执行：任何缺失/不一致都明确返回 matched 与原因。

    Args:
        file_target: 用例文件声明的 target_key。
        file_version: 用例文件声明的 version。
        policy: 不匹配时的处置策略（reject 默认 / warn / force）。
        current_target: 当前被测系统 target_key；不传则读全局 _CURRENT_TARGET_KEY。

    Returns:
        VersionMatchResult：matched/expected_version/message/policy 同构响应。
    """
    cur_tk = current_target or _CURRENT_TARGET_KEY
    try:
        from src.pipeline.steps.step4.empirical.target_cfgs import get_target_cfg
        cur_cfg = get_target_cfg(cur_tk)
        cur_ver = cur_cfg.get("version", "1.0.0")
    except Exception as e:
        # 找不到 target → 拒绝
        return VersionMatchResult(
            file_target=file_target,
            file_version=file_version,
            current_target=cur_tk,
            current_version="",
            matched=False,
            policy=policy,
            message=f"当前被测系统 '{cur_tk}' 不存在或未注册：{e}。已拒绝执行。",
        )

    # 缺 target 或 version → 拒绝
    if not file_target:
        return VersionMatchResult(
            file_target="", file_version=file_version,
            current_target=cur_tk, current_version=cur_ver,
            matched=False, policy=policy,
            message="用例文件缺少 target_key：无法确定目标被测系统，已拒绝。",
        )
    if not file_version:
        return VersionMatchResult(
            file_target=file_target, file_version="",
            current_target=cur_tk, current_version=cur_ver,
            matched=False, policy=policy,
            message="用例文件缺少 version：无法确定目标版本，已拒绝。",
        )

    # target 不一致 → 拒绝（跨系统，绝不静默）
    if file_target != cur_tk:
        return VersionMatchResult(
            file_target=file_target, file_version=file_version,
            current_target=cur_tk, current_version=cur_ver,
            matched=False, policy=policy,
            message=(
                f"被测系统不匹配：用例声明 target='{file_target}'，"
                f"当前被测系统为 '{cur_tk}'。已拒绝执行（严禁跨系统跑用例）。"
            ),
        )

    # version 不一致 → 按 policy 处置
    if file_version != cur_ver:
        if policy == MISMATCH_POLICY_WARN:
            return VersionMatchResult(
                file_target=file_target, file_version=file_version,
                current_target=cur_tk, current_version=cur_ver,
                matched=True, policy=policy,
                message=(
                    f"版本不一致（告警模式）：用例 version='{file_version}'，"
                    f"当前系统 version='{cur_ver}'。将按 warn 策略继续执行（结果标记版本不符）。"
                ),
            )
        # reject / force（force 本期等同拒绝，待授权角色扩展）
        return VersionMatchResult(
            file_target=file_target, file_version=file_version,
            current_target=cur_tk, current_version=cur_ver,
            matched=False, policy=policy,
            message=(
                f"版本不匹配：用例 version='{file_version}'，"
                f"当前系统 version='{cur_ver}'。已拒绝执行（reject 策略，严禁跨版本跑用例）。"
            ),
        )

    # 完全匹配
    return VersionMatchResult(
        file_target=file_target, file_version=file_version,
        current_target=cur_tk, current_version=cur_ver,
        matched=True, policy=policy,
        message=f"匹配通过：target='{cur_tk}' version='{cur_ver}'。",
    )


def validate_target_version(
    parsed: dict,
    policy: str = MISMATCH_POLICY_REJECT,
    current_target: str | None = None,
) -> VersionMatchResult:
    """基于 parse_cases_file 的解析结果做匹配校验（API 层便捷封装）。

    Args:
        parsed: parse_cases_file 的返回值（含 target_key / version）。
        policy: 不匹配处置策略。
        current_target: 当前被测系统 key（不传读全局）。
    Returns:
        VersionMatchResult。
    """
    return check_version_match(
        file_target=parsed.get("target_key", ""),
        file_version=parsed.get("version", ""),
        policy=policy,
        current_target=current_target,
    )
