# -*- coding: utf-8 -*-
"""非浏览器工具层指令的真实执行路由（轨路由）。

覆盖（均为引擎真实代码路径，非模拟）：
  - 数据链路轨  record_*        → metrics_collector 真实写入 + MySQL 读回（写读闭环判定）
  - 横切门禁轨  run_*_gate      → cross_cut_gates 真实执行（对当前真实页面）
  - 控制类型模板轨 apply_action_templates → Step3 生成侧真实调用
  - 功能链路轨  run_full_pipeline 等 → 如实标记暂不执行（重流水线，非单条可驱动）

判定纪律：
  - record_* 的 PASS 判据是「写入后能按 run_id 读回」——写读闭环真实成立，不是"函数没报错"
  - run_*_gate 的 PASS 判据是「真实执行无异常且返回结构化判定」；门禁自身的
    pass/fail 结论如实写入 content（干净页发现真实 a11y 问题属引擎正确行为，不冤枉）
"""
from __future__ import annotations

import inspect
import json
import os
import re
import sys
import time
from typing import Any

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

_RUN = {"id": None}


def _new_run_id() -> str:
    if _RUN["id"] is None:
        _RUN["id"] = "CASE_RUNNER_%d" % int(time.time() * 1000)
    return _RUN["id"]


def _mysql_pw():
    d = {}
    for line in open(os.path.join(_ROOT, ".env"), encoding="utf-8", errors="ignore"):
        m = re.match(r"\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)", line)
        if m:
            d[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    return d.get("STORAGE_MYSQL_PASSWORD") or d.get("MYSQL_PASSWORD") or ""


def _read_back(run_id: str) -> int:
    """从 ai_health_metrics 按 run_id 精确读回本次写入的行数。"""
    import pymysql

    c = pymysql.connect(
        host="127.0.0.1", port=3306, user="root", password=_mysql_pw(),
        database="ai_test_engine", charset="utf8mb4",
    )
    try:
        cur = c.cursor()
        cur.execute("SELECT COUNT(*) FROM ai_health_metrics WHERE run_id=%s", (run_id,))
        return int(cur.fetchone()[0])
    finally:
        c.close()


def _fill_args(fn, project: str, run_id: str) -> dict:
    """按函数签名自动填参：必填无默认的字符串/数值参数给探测值。

    通用规则（随引擎新增 record_* 自动可用），不按函数名硬编码。
    """
    out: dict[str, Any] = {}
    sig = inspect.signature(fn)
    for name, p in sig.parameters.items():
        if p.default is not inspect._empty:
            continue
        if name in ("self", "project"):
            continue
        if name == "run_id":
            out[name] = run_id
        elif name == "step":
            out[name] = 4  # 必填 step（如 record_token_budget）：探针记 Step4 面即可闭环
        elif name in ("metric_type", "metric_name"):
            out[name] = "case_runner_probe"  # 必填时给探针值（有默认值的方法不受影响）
        elif name in ("case_id", "action_type", "reason_category", "source"):
            out[name] = "case_runner_probe"
        elif name == "status" or name.endswith("_status"):
            out[name] = "pass"
        else:
            ann = str(p.annotation)
            if "int" in ann or name.endswith("_count"):
                out[name] = 1
            elif "float" in ann:
                out[name] = 0.5
            elif "dict" in ann or "Dict" in ann:
                out[name] = {}
            elif "list" in ann or "List" in ann:
                out[name] = []
            else:
                out[name] = "case_runner_probe"
    out["project"] = project
    return out


# ---------------------------------------------------------------------------
# record_* 写入口通用解析梯子（精确 → 双向前缀 → metric_type 语义扫描）
# ---------------------------------------------------------------------------
_MT_MAP_CACHE: dict[str, str] | None = None


def _metric_type_method_map() -> dict[str, str]:
    """扫描 metrics_collector 包源码：metric_type=「X」 → 所在 def 方法名。

    引擎写入口的权威语义是落库的 metric_type（读端按它聚合），而非文档名。
    """
    global _MT_MAP_CACHE
    if _MT_MAP_CACHE is not None:
        return _MT_MAP_CACHE
    import glob as _glob

    mapping: dict[str, str] = {}
    pkg_dir = os.path.join(_ROOT, "src", "extensions", "metrics_collector")
    cur_def = ""
    for f in _glob.glob(os.path.join(pkg_dir, "*.py")):
        cur_def = ""
        try:
            with open(f, encoding="utf-8", errors="ignore") as fh:
                for line in fh:
                    m_def = re.match(r"\s*def ([a-zA-Z_][a-zA-Z0-9_]*)\(", line)
                    if m_def:
                        cur_def = m_def.group(1)
                        continue
                    m_mt = re.search(r"metric_type\s*=\s*[\"']([a-zA-Z0-9_]+)[\"']", line)
                    if m_mt and cur_def:
                        mapping.setdefault(m_mt.group(1), cur_def)
        except Exception:
            continue
    _MT_MAP_CACHE = mapping
    return mapping


def _record_candidates(case: dict, instr: str) -> list[str]:
    """从用例语义生成候选写入口名（证据出处 > 指令名 > 目标功能点）。"""
    cands: list[str] = []
    ev = str(case.get("证据出处", "") or "")
    for tok in ev.replace("::", " ").split():
        if tok.startswith("record_"):
            cands.append(tok)
    s = str(instr or "").split("/")[0].strip()
    if s.startswith("record_") and s != "record_*":
        cands.append(s)
    base = str(case.get("目标功能点", "") or "").split(".")[0]
    if base and base != "*":
        cands.append("record_" + base)
    return cands


def _resolve_record_fn(inst, case: dict, instr: str):
    """候选 → 真实 record 方法。三级梯子，全部落空返回 None。"""
    cands = _record_candidates(case, instr)
    if not cands:
        return None
    methods = [
        n for n in dir(inst) if n.startswith("record_") and callable(getattr(inst, n))
    ]
    # 1) 精确
    for c in cands:
        if c in methods:
            return getattr(inst, c)
    # 2) 双向前缀（候选最长优先）
    for c in sorted(set(cands), key=len, reverse=True):
        for m in methods:
            if m.startswith(c) or c.startswith(m):
                return getattr(inst, m)
    # 3) metric_type 语义：候选名去 record_ 前缀后渐进尾词收缩，对齐落库 metric_type
    hits = _metric_type_method_map()
    for c in cands:
        cur = c[len("record_") :] if c.startswith("record_") else c
        while cur:
            if cur in hits and hits[cur] in methods:
                return getattr(inst, hits[cur])
            if "_" in cur:
                cur = cur.rsplit("_", 1)[0]
            else:
                cur = ""
    return None


def _exec_evidence_closure(run_id: str) -> dict:
    """证据面写读闭环（引擎 ExecutionEvidenceManager：collect_* → get_evidence）。

    该面为内存证据存储（非 MySQL），闭环判据同样是「写后可按 (run_id, case_id) 读回」。
    """
    try:
        from src.extensions.evidence_manager import ExecutionEvidenceManager
    except Exception as e:
        return {"ok": False, "content": f"evidence_manager 导入失败: {e}"}
    try:
        m = ExecutionEvidenceManager()
        cid = "CR_EVID_PROBE"
        m.collect_console(run_id, cid, "error", "case_runner 证据探针", url="case_runner://probe")
        m.collect_network(run_id, cid, "GET", "case_runner://probe", status=200, elapsed_ms=1.0)
        got = m.get_evidence(run_id, cid)
        ok = bool(got.get("console")) and bool(got.get("network"))
        return {
            "ok": ok,
            "content": (
                f"证据面写读闭环: console={len(got.get('console', []))}条 "
                f"network={len(got.get('network', []))}条 → {'闭环成立' if ok else '假写入(读不回)'}"
            ),
        }
    except Exception as e:
        return {"ok": False, "content": f"证据面闭环异常: {type(e).__name__}: {str(e)[:120]}"}


def _exec_record(case: dict) -> dict | None:
    """数据链路轨：真实写入 + 读回（写读闭环）。"""
    try:
        from src.extensions.metrics_collector import metrics_collector
    except Exception as e:
        return {"ok": False, "content": f"metrics_collector 导入失败: {e}", "track": "数据链路轨"}

    project = "case_runner"
    try:
        inst = metrics_collector.for_project(project)
    except Exception:
        inst = metrics_collector

    instr = str(case.get("关键操作指令", "") or "")
    fn = _resolve_record_fn(inst, case, instr)

    if fn is None:
        # 证据面（evidence）语义 → 走引擎证据存储的写读闭环
        cands = _record_candidates(case, instr)
        if any("evidence" in c for c in cands):
            r = _exec_evidence_closure(_new_run_id())
            r["track"] = "数据链路轨"
            return r
        return None  # 交回上层如实记 NO_HANDLER

    run_id = _new_run_id()
    try:
        args = _fill_args(fn, project, run_id)
        r = fn(**args)
        if inspect.iscoroutine(r):
            import asyncio

            asyncio.get_event_loop().run_until_complete(r)
    except Exception as e:
        return {"ok": False, "content": f"{fn.__name__} 写入异常: {type(e).__name__}: {str(e)[:120]}", "track": "数据链路轨"}

    try:
        n = _read_back(run_id)
    except Exception as e:
        return {"ok": False, "content": f"{fn.__name__} 写入后读回失败: {str(e)[:120]}", "track": "数据链路轨"}

    ok = n > 0
    return {
        "ok": ok,
        "content": f"{fn.__name__} 写读闭环: run_id={run_id} 读回 {n} 行 → {'闭环成立' if ok else '假写入(读不回)'}",
        "track": "数据链路轨",
    }


async def _exec_gate(first: str, case: dict, env) -> dict | None:
    """横切门禁轨：对当前真实页面执行引擎门禁函数。"""
    try:
        from src.pipeline.steps.step4.empirical import cross_cut_gates as ccg
    except Exception as e:
        return {"ok": False, "content": f"cross_cut_gates 导入失败: {e}", "track": "横切门禁轨"}

    alias = {"run_a11y_gate": "run_a11y_check", "run_usability_gate": "run_a11y_check"}
    fname = alias.get(first, first)
    fn = getattr(ccg, fname, None)
    if fn is None or not callable(fn):
        # ★ 通用规范化(2026-09-21): run_X_gate → run_X_check → run_X（去掉 _gate 后缀）。
        #   用例集用 *_gate 命名，引擎门禁实现统一 *_check 命名——按规则对齐，
        #   不逐个名字硬编码（引擎新增 X_check 门禁自动可路由）。
        for cand in (
            first.replace("_gate", "_check"),
            first[: -len("_gate")] if first.endswith("_gate") else "",
        ):
            if cand and cand != first:
                f2 = getattr(ccg, cand, None)
                if f2 is not None and callable(f2):
                    fname, fn = cand, f2
                    break
    if fn is None or not callable(fn):
        return None

    # ★ 异步执行栈优先用引擎的 async 门禁变体：同步门禁在异步 Page 上
    #   page.content() 拿到的是协程对象（引擎同步主路径无此问题）
    is_async = inspect.iscoroutinefunction(fn)
    if not is_async:
        afn = getattr(ccg, fname + "_async", None)
        if afn is not None and inspect.iscoroutinefunction(afn):
            fn, is_async = afn, True

    try:
        sig = inspect.signature(fn)
        kwargs: dict[str, Any] = {}
        for name, p in sig.parameters.items():
            if name == "page":
                kwargs[name] = env.page
            elif name == "api_calls":
                kwargs[name] = []
            elif name == "target_cfg":
                kwargs[name] = {"target_key": "hospital_sut", "url": "http://127.0.0.1:5080"}
            elif p.default is inspect._empty:
                kwargs[name] = None
        r = fn(**kwargs)
        if is_async or inspect.iscoroutine(r):
            r = await r
    except Exception as e:
        return {"ok": False, "content": f"{fname} 执行异常: {type(e).__name__}: {str(e)[:120]}", "track": "横切门禁轨"}

    if isinstance(r, dict):
        err = str(r.get("error") or "")
        if err:
            return {"ok": False, "content": f"{fname} 门禁报错(引擎fail-closed): {err[:150]}", "track": "横切门禁轨"}
        # ★ perf 类门禁：返回 {enabled, verdicts:[{name,passed}], summary} 结构
        verdicts = r.get("verdicts")
        if isinstance(verdicts, list) and verdicts:
            n_pass = sum(1 for v in verdicts if isinstance(v, dict) and v.get("passed"))
            n_fail = len(verdicts) - n_pass
            var = str(case.get("变化点", "") or "")
            if "判伪" in var or "问题页" in var:
                ok = n_fail > 0
                note = f"门禁如实报出 {n_fail} 项未过"
            elif "判真" in var or "达标" in var:
                if n_fail == 0:
                    ok, note = True, f"{len(verdicts)} 项 verdict 全过"
                else:
                    return {"ok": None, "content": f"{fname} 可执行；{n_fail}/{len(verdicts)} 项未过（真实发现/达标页未构造，不判门禁对错）", "track": "横切门禁轨"}
            else:
                ok, note = True, f"{n_pass}/{len(verdicts)} 项 verdict 通过"
            return {"ok": ok, "content": f"{fname} 真实执行 → {note} | {json.dumps(r.get('summary', {}), ensure_ascii=False, default=str)[:140]}", "track": "横切门禁轨"}
        if "ok" in r:
            return {"ok": bool(r.get("ok")), "content": f"{fname} 真实执行 → ok={r.get('ok')} | {json.dumps(r, ensure_ascii=False, default=str)[:160]}", "track": "横切门禁轨"}
        total = r.get("total")
        findings = r.get("findings")
        if total is not None or findings is not None:
            n = int(total if total is not None else len(findings or []))
            # 判定期望对齐用例语义：
            #   判伪（前置=含问题页）→ 门禁必须如实报出问题（n>0）才算通过；漏报=引擎缺陷
            #   判真（前置=达标页）→ 需真实达标页。当前靶子页实况 n>0（非达标），
            #     前置未构造，如实 UNDECIDED，不冒充 PASS 也不冤枉门禁
            var = str(case.get("变化点", "") or "")
            if "判伪" in var or "问题页" in var:
                ok = n > 0
                note = "门禁如实报出 %d 条问题" % n
            elif "判真" in var or "达标" in var:
                return {"ok": None, "content": f"{fname} 可执行；页面实况 {n} 条发现（非达标页，前置未构造，不判门禁对错）", "track": "横切门禁轨"}
            else:
                ok = True
                note = f"{fname} 执行成功，{n} 条发现"
            return {"ok": ok, "content": f"{fname} 真实执行 → {note} | {json.dumps(r, ensure_ascii=False, default=str)[:160]}", "track": "横切门禁轨"}
        status = str(r.get("status", r.get("gate_status", "")))
        return {"ok": bool(status), "content": f"{fname} 真实执行 → status={status or 'n/a'}", "track": "横切门禁轨"}
    return {"ok": r is not None, "content": f"{fname} → {str(r)[:200]}", "track": "横切门禁轨"}


def _exec_template(case: dict) -> dict | None:
    """控制类型模板轨：Step3 生成侧真实展开。"""
    try:
        from src.pipeline.steps.step3.case_generator._action_templates import (
            apply_action_templates,
        )
    except Exception as e:
        return {"ok": False, "content": f"apply_action_templates 导入失败: {e}", "track": "控制类型模板轨"}

    # 控件类型从用例「目标功能点」语义解析（通用词表，随引擎控件族扩展）
    sub = str(case.get("目标功能点", "")) + " " + str(case.get("子模块", "")) + " " + str(case.get("用例标题", ""))
    words = {
        "input": ("输入框", "表单", "文本"), "select": ("下拉", "选择"), "upload": ("上传",),
        "checkbox": ("勾选", "复选"), "date": ("日期",), "table": ("表格", "列表"),
        "button": ("按钮", "点击"), "dialog": ("弹窗", "对话框"), "tab": ("页签", "选项卡"),
        "search": ("查询", "搜索"), "pagination": ("分页",),
    }
    ct = "input"
    for k, kws in words.items():
        if any(kw in sub for kw in kws):
            ct = k
            break

    op = str(case.get("用例标题", "") or "case_runner 探测操作")
    try:
        tpl = apply_action_templates(ct, op, None)
    except Exception as e:
        return {"ok": False, "content": f"apply_action_templates({ct}) 异常: {type(e).__name__}: {str(e)[:120]}", "track": "控制类型模板轨"}

    ok = isinstance(tpl, list) and len(tpl) > 0
    return {
        "ok": ok,
        "content": f"apply_action_templates(control_type={ct}) 真实展开 → {len(tpl) if isinstance(tpl, list) else type(tpl).__name__} 条步骤",
        "track": "控制类型模板轨",
    }


def _exec_ai_round_metrics(case: dict) -> dict:
    """browser_ai_round/ai_loop 指令：AI 轮次指标面写读闭环。

    完整 AI 轮次（LLM 探索）非单条用例可独立驱动；该指令的可独立验证面是
    引擎的轮次指标写入路径：record_browser_ai_round / record_guard_rejection
    (whitelist) / record_action_attempt 全部真实写入后按 run_id 读回。
    """
    try:
        from src.extensions.metrics_collector import metrics_collector
    except Exception as e:
        return {"ok": False, "content": f"metrics_collector 导入失败: {e}", "track": "AI轮次指标面"}

    project = "case_runner"
    try:
        inst = metrics_collector.for_project(project)
    except Exception:
        inst = metrics_collector

    run_id = _new_run_id()
    done: list[str] = []
    failed: list[str] = []
    for fn_name in ("record_browser_ai_round", "record_guard_rejection", "record_action_attempt"):
        fn = getattr(inst, fn_name, None)
        if fn is None or not callable(fn):
            failed.append(f"{fn_name}(无此方法)")
            continue
        try:
            args = _fill_args(fn, project, run_id)
            if fn_name == "record_guard_rejection":
                args["reason_category"] = "whitelist"
            r = fn(**args)
            if inspect.iscoroutine(r):
                import asyncio

                asyncio.get_event_loop().run_until_complete(r)
            done.append(fn_name)
        except Exception as e:
            failed.append(f"{fn_name}({type(e).__name__}: {str(e)[:60]})")

    try:
        n = _read_back(run_id)
    except Exception as e:
        return {"ok": False, "content": f"AI轮次指标读回失败: {e}", "track": "AI轮次指标面"}

    ok = n > 0 and not failed
    note = f"已写入[{','.join(done)}] 读回 {n} 行"
    if failed:
        note += f"；未覆盖: {','.join(failed)}"
    return {
        "ok": ok,
        "content": f"AI轮次整体非单条可驱动；真实执行轮次指标写入+读回闭环: {note}",
        "track": "AI轮次指标面",
    }


async def exec_track_instruction(instr: str, case: dict, env) -> dict | None:
    """轨路由总入口。返回 None = 本模块也不支持（上层如实记 NO_HANDLER）。"""
    first = str(instr or "").split("/")[0].strip()
    if not first:
        return None
    if first.startswith("record_"):
        return _exec_record(case)
    if ("ai_round" in first) or ("ai_loop" in first):
        return _exec_ai_round_metrics(case)
    if first.startswith("run_") and ("gate" in first or "check" in first or "pipeline" in first):
        if "pipeline" in first:
            return {"ok": None, "content": "run_full_pipeline 为完整流水线入口，非单条用例可独立驱动（需项目上下文），如实不执行", "track": "功能链路轨"}
        return await _exec_gate(first, case, env)
    if first == "apply_action_templates":
        return _exec_template(case)
    return None
