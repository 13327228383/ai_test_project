# -*- coding: utf-8 -*-
"""链路生命周期测试（L2 链级保障）—— 用真编排器走完整链，只在网络边界外全真。

背景（2026-10-02 审计教训）：
  authz 横切从「互斥模式」改为「横切合并」时，_run_api_mode 的 finally 在功能性
  用例结束后就回收了 SUT，导致 authz 矩阵对已死 SUT 探针全部连接被拒 → 引擎
  制造假 FAIL。组件级单测全部通过（各自 monkeypatch，互不衔接），只有链级
  生命周期测试能抓住「顺序」这类缺陷。本文件就是那条看门狗。

链覆盖：
  C4b API 执行链（探活→拉起→登录→探针）
  C6  横切门禁链（功能性路径之后、同一存活 SUT 上跑 authz）
  C7  报告链（报告落盘 + security.authz 有产出）

运行条件：本机引擎 MySQL 可达 + api_demo 靶子依赖（requirements.txt 全量）。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.pipeline.steps.step4.empirical.target_cfgs import get_target_cfg
from src.pipeline.steps.step4.empirical.target_empirical import TargetEmpiricalVerifier

_ROOT = Path(__file__).resolve().parents[2]
_REPORT = _ROOT / "logs" / "reports" / "empirical_api_demo_report.json"


def _run_api_demo() -> dict:
    cfg = get_target_cfg("api_demo")
    assert cfg, "target_cfgs 未注册 api_demo"
    return TargetEmpiricalVerifier(cfg).run()


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
def test_c4b_c6_c7_api_demo_full_lifecycle():
    """C4b+C6+C7 全生命周期：功能探针与 authz 横切必须在同一存活 SUT 上先后完成。

    断言的是链级不变量，而非组件行为：
      ① 功能探针有产出且全过（C4b）；
      ② authz 横切探针打到「存活」SUT —— 若 SUT 被提前回收，这里表现为
         全部探针连接被拒（即被修复的假 FAIL 形态），本断言即红（C6）；
      ③ 声明了 authz 的靶子，报告 security.authz 必须有产出（声明即产出，
         防"门禁静默消失"型假绿）（C6/C7）；
      ④ 报告落盘且整体 all_pass（C7）。
    """
    report = _run_api_demo()

    # ① 功能链（报告 case 结构：{case, overall: PASS/FAIL, assertions[]}）
    cases = report.get("cases") or []
    assert cases, "功能探针无产出（C4b 断链）"
    failed_cases = [
        c.get("case") for c in cases
        if str(c.get("overall") or "").upper() != "PASS"
        and not all(a.get("pass") for a in (c.get("assertions") or []))
    ]
    assert not failed_cases, f"功能探针失败: {failed_cases}"

    # ②③ authz 横切（声明即产出 + 打活 SUT）
    authz = (report.get("security") or {}).get("authz") or {}
    assert authz, "声明了 authz 的靶子，报告 security.authz 无产出（门禁静默消失）"
    assert int(authz.get("total") or 0) > 0, "authz 矩阵 0 探针（横切未执行）"
    conn_refused = [
        p for p in (authz.get("probes") or [])
        if "Connection refused" in str(p.get("error") or "")
           or "无法连接" in str(p.get("error") or "")
    ]
    assert not conn_refused, (
        f"authz 探针 {len(conn_refused)} 条连接被拒 —— SUT 在 authz 前被回收"
        "（生命周期顺序缺陷回归）"
    )
    assert authz.get("all_pass") is True, f"authz 横切未全过: {authz.get('failed')} failed"

    # ④ 报告链
    summary = report.get("summary") or {}
    assert summary.get("all_pass") is True, "链级 all_pass=False"
    assert _REPORT.exists(), "实证报告未落盘"


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
def test_c7_report_file_is_wellformed_json():
    """C7 报告链：落盘报告必须是可解析 JSON 且与本次 run 的 summary 一致。"""
    assert _REPORT.exists(), "实证报告不存在（先跑上一条用例或手动执行 api_demo 链）"
    data = json.loads(_REPORT.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    assert "summary" in data and "cases" in data


# ── 金样本回归（L2）：链路产物结构漂移检测 ─────────────────────────────
import json as _json

_GOLDEN = Path(__file__).resolve().parent / "golden" / "api_demo_lane.json"


def _normalize(rep) -> dict:
    import re as _re

    out = {"target": rep.get("target_key"), "cases": [], "authz": None,
           "summary_all_pass": bool((rep.get("summary") or {}).get("all_pass"))}
    for c in rep.get("cases") or []:
        out["cases"].append({
            "case": c.get("case"),
            "overall": str(c.get("overall") or "").upper(),
            "assertions": [
                [_re.sub(r"\d+", "0", a.get("name") or ""), bool(a.get("pass"))]
                for a in (c.get("assertions") or [])
            ],
        })
    az = (rep.get("security") or {}).get("authz") or {}
    out["authz"] = {
        "total": az.get("total"), "passed": az.get("passed"), "failed": az.get("failed"),
        "probe_ids": [p.get("id") for p in (az.get("probes") or [])],
        "probe_pass": [bool(p.get("pass")) for p in (az.get("probes") or [])],
    }
    out["cases"].sort(key=lambda x: x["case"] or "")
    return out


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
def test_c4b_c6_c7_golden_lane_structure():
    """金样本回归：链路产出的 case 集/断言结构/横切探针集合不得漂移。

    与上一条用例互补：那条断言"对不对"，本条断言"形状没变"——
    任何重构（如探针搬迁、断言语义调整）导致结构漂移都会在此显式暴露，
    属预期改动时更新 golden 文件即可（基线只许有意变更）。
    """
    golden = _json.loads(_GOLDEN.read_text(encoding="utf-8"))
    actual = _normalize(_run_api_demo())
    assert actual == golden, (
        "链路产物结构与金样本漂移 —— 若为有意变更请更新 "
        "tests/test_chains/golden/api_demo_lane.json；否则回查本次改动"
    )


# ── 浏览器道哨兵（spa_demo）：回归网覆盖 browser 执行路径 ───────────────
_GOLDEN_SPA = Path(__file__).resolve().parent / "golden" / "spa_demo_lane.json"


def _normalize_spa(rep) -> dict:
    import re as _re

    out = {"target": rep.get("target_key"),
           "summary_all_pass": bool((rep.get("summary") or {}).get("all_pass")),
           "cases": []}
    for c in rep.get("cases") or []:
        out["cases"].append({
            "case": c.get("case"),
            "overall": str(c.get("overall") or "").upper(),
            "assertions": [[_re.sub(r"\d+", "0", a.get("name") or ""), bool(a.get("pass"))]
                           for a in (c.get("assertions") or [])],
        })
    out["cases"].sort(key=lambda x: x["case"] or "")
    out["cross_dims"] = sorted(k for k in ("a11y", "visual", "perf", "security") if k in rep)
    return out


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
def test_c4a_browser_lane_spa_demo_lifecycle_and_golden():
    """浏览器道（C4a）生命周期 + 金样本：SPA 靶子走真浏览器链路。

    断言：功能探针全过、all_pass、横切四维（a11y/visual/perf/security）齐活、
    产物结构与金样本一致。为浏览器侧重构（定位器收敛等）提供显式漂移暴露点。
    耗时约 2 分钟（真 Chromium），是本套件最慢用例。
    """
    golden = _json.loads(_GOLDEN_SPA.read_text(encoding="utf-8"))
    report = TargetEmpiricalVerifier(get_target_cfg("spa_demo")).run()
    actual = _normalize_spa(report)
    assert actual["summary_all_pass"] is True, "spa 浏览器道 all_pass=False"
    assert actual["cross_dims"] == ["a11y", "perf", "security", "visual"], (
        "横切四维缺一（声明即产出红线）"
    )
    assert actual == golden, (
        "浏览器道产物结构与金样本漂移 —— 有意变更请更新 "
        "golden/spa_demo_lane.json，否则回查本次改动"
    )
