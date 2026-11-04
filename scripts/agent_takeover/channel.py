# -*- coding: utf-8 -*-
"""Agent 接管通道（生成侧 AI 由 Agent 承担，运行期零 AI）。

skill `e2e-test-engine-pw-pytest-rag-ai` §三 的落地实现，三个子命令：

  export  从库内导出「AI 输入契约」：需求 + 页面事实 + 待重写用例现状
  import  读入「AI 输出契约」（Agent 产出的用例 JSON），校验后**就地重写**目标用例
  verify  校验接管结果：来源可追溯、无退化动作、无平凡/同义反复断言

设计纪律（对齐反补丁三禁）：
  - 不新增第二套用例表/第二套动作语义；重写的是**同一 case_id** 的既有行。
  - 动作/断言参数名一律取自引擎自身契约（CHECK_TAXONOMY + 各 handler 实参），
    不在本文件重新定义任何判据。
  - 运行期不参与：本通道只在生成侧运行。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime

PROJECT_DIR = r"D:\AI\AI_Test_Preject"
sys.path.insert(0, PROJECT_DIR)

GEN_DIR = os.path.join(PROJECT_DIR, "temp", "agent_gen_input")
PRODUCER = "agent_takeover"


def _load_env(path: str) -> None:
    for line in open(path, encoding="utf-8", errors="ignore"):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _conn():
    _load_env(os.path.join(PROJECT_DIR, ".env"))
    import pymysql

    return pymysql.connect(
        host=os.getenv("STORAGE_MYSQL_HOST", "127.0.0.1"),
        port=int(os.getenv("STORAGE_MYSQL_PORT", "3306")),
        user=os.getenv("STORAGE_MYSQL_USER", "root"),
        password=os.getenv("STORAGE_MYSQL_PASSWORD", ""),
        database=os.getenv("STORAGE_MYSQL_DATABASE", "ai_test_engine"),
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
    )


def _as_obj(v):
    if isinstance(v, (dict, list)):
        return v
    if isinstance(v, (bytes, bytearray)):
        v = v.decode("utf-8", "ignore")
    if isinstance(v, str) and v.strip():
        try:
            return json.loads(v)
        except Exception:
            return None
    return None


def _check_inner(c: dict) -> dict:
    """取 check 元素的内层断言对象（引擎规范：type 嵌套在 check 子对象内）。"""
    if isinstance(c.get("check"), dict):
        return c["check"]
    return c


# ────────────────────────── export ──────────────────────────

def cmd_export(args) -> int:
    conn = _conn()
    cur = conn.cursor()

    cur.execute(
        "SELECT req_id,module,operation,description,condition_expr,expected_feedback,is_implicit "
        "FROM requirements WHERE project=%s AND version_id=%s ORDER BY req_id",
        (args.project, args.version_id),
    )
    reqs = cur.fetchall()

    cur.execute(
        "SELECT case_id,req_id,case_type,risk_level,title,case_source,valid_status,identity_hint "
        "FROM test_cases WHERE project=%s AND version_id=%s"
        if False
        else "SELECT * FROM test_cases WHERE project=%s AND version_id=%s",
        (args.project, args.version_id),
    )
    cases = cur.fetchall()

    cur.execute(
        "SELECT case_id,status,failure_reason,failure_category,expected,actual,actual AS a2 "
        "FROM executions WHERE project=%s",
        (args.project,),
    )
    execs = {r["case_id"]: r for r in cur.fetchall()}

    page_model_path = os.path.join(GEN_DIR, "page_model.json")
    page_model = None
    if os.path.exists(page_model_path):
        page_model = json.load(open(page_model_path, encoding="utf-8"))

    targets = []
    for c in cases:
        e = execs.get(c["case_id"]) or {}
        st = e.get("status") or "NEVER_EXECUTED"
        if st == args.only_status_filter or args.only_status_filter == "ALL":
            pj = _as_obj(c.get("payload_json")) or {}
            targets.append(
                {
                    "case_id": c["case_id"],
                    "req_id": c["req_id"],
                    "case_type": c["case_type"],
                    "risk_level": c["risk_level"],
                    "title": c["title"],
                    "valid_status": c["valid_status"],
                    "case_source": c["case_source"],
                    "exec_status": st,
                    "exec_reason": (e.get("failure_reason") or "")[:400],
                    "exec_category": e.get("failure_category") or "",
                    "identity": pj.get("identity"),
                    "payload_status": pj.get("status"),
                    "actions": _as_obj(c.get("case_actions_json")) or [],
                    "checks": pj.get("checks") or [],
                }
            )

    out = {
        "project": args.project,
        "version_id": args.version_id,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "page_model": page_model,
        "requirements": reqs,
        "target_cases": targets,
        "case_source_values": sorted({c["case_source"] for c in cases if c["case_source"]}),
    }
    os.makedirs(GEN_DIR, exist_ok=True)
    p = os.path.join(GEN_DIR, "input.json")
    json.dump(out, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"[export] requirements={len(reqs)} cases={len(cases)} targets={len(targets)} -> {p}")

    from collections import Counter

    print("[export] target exec_status:", dict(Counter(t["exec_status"] for t in targets)))
    print("[export] target case_type  :", dict(Counter(t["case_type"] for t in targets)))
    print("[export] case_source values:", out["case_source_values"])
    print("[export] valid_status values:", dict(Counter(c["valid_status"] for c in cases)))
    conn.close()
    return 0


# ────────────────────────── import ──────────────────────────

def _validate_case(case: dict) -> list[str]:
    errs = []
    need = ["case_id", "req_id", "title", "case_type", "identity", "actions", "checks"]
    for k in need:
        if not case.get(k):
            errs.append(f"缺字段 {k}")
    acts = case.get("actions") or []
    if acts:
        types = {a.get("type") for a in acts if isinstance(a, dict)}
        # 退化判据：仅截图/空动作 = 构造上不产生任何效果
        if not (types - {"screenshot"}):
            errs.append("动作退化：只有 screenshot")
        for a in acts:
            if not isinstance(a, dict) or not a.get("type"):
                errs.append("动作缺 type")
                continue
            if a["type"] == "navigate" and not (a.get("url") or a.get("target_hint")):
                errs.append("navigate 缺 url/target_hint")
            if a["type"] in ("fill", "select", "type") and not (a.get("selector") or a.get("target_hint")):
                errs.append(f"{a['type']} 缺 selector/target_hint")
            if a["type"] in ("fill", "select", "type") and a.get("value") is None and a.get("text") is None:
                errs.append(f"{a['type']} 缺 value")
    checks = case.get("checks") or []
    if not checks:
        errs.append("无断言")
    from src.pipeline.steps.step4.browser_tools._taxonomy import (
        CHECK_TAXONOMY,
        resolve_check_type,
    )

    for c in checks:
        inner = _check_inner(c)
        t = inner.get("type")
        if not t:
            errs.append("断言缺 type")
            continue
        if resolve_check_type(t) not in CHECK_TAXONOMY:
            errs.append(f"断言类型未登记: {t}")
        exp = str(inner.get("expected") or c.get("expect") or "")
        if not exp.strip():
            errs.append(f"{t} 缺 expected")
        # 同义反复判据：期望文本里回抄了用例标题/需求操作 → 永远不可判定
        if exp and case.get("title") and exp in case["title"] and len(exp) > 8:
            errs.append(f"{t} 期望文本=用例标题（同义反复）")
        if exp and ("确认系统行为符合预期" in exp or "的" + str(inner.get("type")) in exp):
            errs.append(f"{t} 期望文本自指（同义反复）")
    return errs


def cmd_import(args) -> int:
    src = args.file or os.path.join(GEN_DIR, "agent_cases.json")
    cases = json.load(open(src, encoding="utf-8"))
    if isinstance(cases, dict):
        cases = cases.get("cases", [])

    bad = []
    for c in cases:
        errs = _validate_case(c)
        if errs:
            bad.append((c.get("case_id"), errs))
    print(f"[import] 待写入 {len(cases)} 条；校验不通过 {len(bad)} 条")
    for cid, errs in bad[:20]:
        print(f"  ✗ {cid}: {errs}")
    if bad and not args.force:
        print("[import] 校验未通过 → 中止（不加 --force 不写库）")
        return 2

    conn = _conn()
    cur = conn.cursor()
    now = datetime.now()
    written = 0
    log_rows = []
    for c in cases:
        cid, rid = c["case_id"], c["req_id"]
        cur.execute(
            "SELECT payload_json, case_actions_json FROM test_cases "
            "WHERE project=%s AND version_id=%s AND case_id=%s",
            (args.project, args.version_id, cid),
        )
        row = cur.fetchone()
        if not row:
            print(f"  ✗ {cid} 不存在于 {args.project}/{args.version_id}（拒绝新建）")
            continue

        pj = _as_obj(row.get("payload_json")) or {}
        acts = c["actions"]
        checks = c["checks"]
        tracks = []
        for ck in checks:
            t = ck.get("track") or "ui"
            if t not in tracks:
                tracks.append(t)

        pj.update(
            {
                "case_id": cid,
                "req_id": rid,
                "type": c["case_type"],
                "title": c["title"],
                "module": pj.get("module") or c.get("module") or "",
                "identity": c["identity"],
                "risk_level": c.get("risk_level") or pj.get("risk_level"),
                "description": c.get("description") or pj.get("description") or "",
                "actions": acts,
                "checks": checks,
                "test_steps": json.dumps(acts, ensure_ascii=False, indent=2),
                "adi_detail": {
                    "ui": sum(1 for ck in checks if (ck.get("track") or "ui") == "ui"),
                    "api": sum(1 for ck in checks if ck.get("track") == "api"),
                    "db": sum(1 for ck in checks if ck.get("track") == "db"),
                    "error": "",
                    "unresolved": [],
                    "track_count": len(tracks),
                    "assert_count": len(checks),
                },
                "status": "pending",
                "_producer": PRODUCER,
                "_target": c.get("target") or {},
            }
        )
        cur.execute(
            "UPDATE test_cases SET payload_json=%s, case_actions_json=%s, title=%s, "
            "case_type=%s, risk_level=%s, precondition=%s, expected_result=%s, "
            "case_source=%s, updated_at=%s "
            "WHERE project=%s AND version_id=%s AND case_id=%s",
            (
                json.dumps(pj, ensure_ascii=False),
                json.dumps(acts, ensure_ascii=False),
                c["title"],
                c["case_type"],
                c.get("risk_level") or "P2",
                c.get("precondition") or "",
                c.get("expected_result") or "",
                PRODUCER,
                now,
                args.project,
                args.version_id,
                cid,
            ),
        )
        written += 1
        log_rows.append(
            (
                args.project,
                f"{PRODUCER}::{cid}",
                cid,
                rid,
                pj.get("module") or "",
                c["title"],
                f"agent 重写动作 {len(acts)} / 断言 {len(checks)}",
                "agent_generation",
                json.dumps(acts, ensure_ascii=False)[:60000],
                "agent_generation",
                args.version_id,
                now,
            )
        )

    if log_rows:
        # 幂等：log_id 是主键（{project}::{producer}::{case_id}）→ 重复导入更新而非报 1062
        cur.executemany(
            "INSERT INTO ai_generation_logs(project,log_id,case_id,req_id,module,title,"
            "failure_reason,classification,raw_steps_json,source,version_id,created_at) "
            "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
            "ON DUPLICATE KEY UPDATE title=VALUES(title), module=VALUES(module), "
            "raw_steps_json=VALUES(raw_steps_json), created_at=VALUES(created_at)",
            log_rows,
        )
    conn.commit()
    print(f"[import] 写入 {written} 条 + ai_generation_logs {len(log_rows)} 行")
    conn.close()
    return 0


# ────────────────────────── verify ──────────────────────────

TRIVIAL = {"element_exists", "no_error", "semantic_check", "semantic"}


def cmd_verify(args) -> int:
    conn = _conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT case_id,req_id,case_type,case_source,title,case_actions_json,payload_json "
        "FROM test_cases WHERE project=%s AND version_id=%s",
        (args.project, args.version_id),
    )
    rows = cur.fetchall()
    cur.execute(
        "SELECT COUNT(*) c FROM ai_generation_logs WHERE project=%s AND source='agent_generation'",
        (args.project,),
    )
    prov = cur.fetchone()["c"]

    nav_only = 0
    trivial_only = 0
    tautology = 0
    agent_rows = 0
    for r in rows:
        acts = _as_obj(r.get("case_actions_json")) or []
        pj = _as_obj(r.get("payload_json")) or {}
        types = {a.get("type") for a in acts if isinstance(a, dict)}
        if not (types & {"click", "fill", "select", "type", "press"}):
            nav_only += 1
        checks = pj.get("checks") or []
        ctypes = {(_check_inner(c).get("type") or "") for c in checks if isinstance(c, dict)}
        ctypes = {t for t in ctypes if t}
        if ctypes and ctypes <= TRIVIAL:
            trivial_only += 1
        for c in checks:
            if not isinstance(c, dict):
                continue
            exp = str(_check_inner(c).get("expected") or c.get("expect") or "")
            if exp and ("确认系统行为符合预期" in exp or r["case_id"].replace("TC-", "") in exp):
                tautology += 1
                break
        if pj.get("_producer") == PRODUCER:
            agent_rows += 1

    n = len(rows)
    print(f"[verify] 用例总数 {n}")
    print(f"  NAV_ONLY（无真实交互）      : {nav_only}  ({nav_only*100//max(n,1)}%)")
    print(f"  平凡断言独占（仅 element_exists/no_error/semantic）: {trivial_only}")
    print(f"  同义反复断言（期望回抄标题）: {tautology}")
    print(f"  Agent 接管行（payload._producer=agent_takeover）: {agent_rows}")
    print(f"  ai_generation_logs 溯源行   : {prov}")
    conn.close()
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Agent 接管通道")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("export")
    p.add_argument("--project", required=True)
    p.add_argument("--version-id", type=int, required=True)
    p.add_argument("--only-status-filter", default="ALL")
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("import")
    p.add_argument("--project", required=True)
    p.add_argument("--version-id", type=int, required=True)
    p.add_argument("--file", default="")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_import)

    p = sub.add_parser("verify")
    p.add_argument("--project", required=True)
    p.add_argument("--version-id", type=int, required=True)
    p.set_defaults(func=cmd_verify)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
