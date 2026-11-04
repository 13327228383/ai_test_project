# -*- coding: utf-8 -*-
"""项目级数据清空 —— 范围由 information_schema 派生，不做逐表维护。

为什么: 清空范围曾以逐表 ``DELETE FROM`` 与分类分支维护（``_clear.py`` 45 条），
新表上线即漏清，"从干净态验证流水线"不可达。现在范围 = 带 project 列的表 − KEEP 集，
删除在同一事务内完成（失败整体回滚）。

约定:
- KEEP 只收两类：项目注册表、依赖外部服务的输入态；派生数据不得进 KEEP。
- 清空期间关闭外键约束并在同一会话恢复：项目内子行随父行同删，不需要拓扑排序。
- UI 虚拟路径 ``mysql://table[/sub...]`` 的子键列按 ``SUB_KEY_COLUMNS`` 优先级解析。
"""

from __future__ import annotations

import logging

_log = logging.getLogger(__name__)

PROJECT_COLUMNS: tuple[str, ...] = ("project", "project_name")

# 保留：项目注册 / 身份 / 全局能力缓存 —— 删掉会破坏项目本身或跨项目资产
KEEP_REGISTRY_TABLES: frozenset[str] = frozenset(
    {
        "project_configs", "project_members", "project_meta", "project_synonym_overrides",
        "project_versions", "api_tokens", "user_credentials_v2", "role_projects",
        "capability_cache", "capability_cache_events", "capability_cache_policy",
    }
)

# 保留：输入态 —— 重新获取依赖 Confluence/YApi/Figma 等外部服务，清掉后无法离线复现 Step1
KEEP_INPUT_TABLES: frozenset[str] = frozenset(
    {
        "source_documents", "source_hashes", "source_breakdown_hashes",
        "source_confluence_hashes", "source_yapi_hashes", "document_versions",
        "figma_pages", "figma_nodes", "figma_components", "figma_images",
        "figma_node_hashes", "figma_mcp_cache",
    }
)

# source_documents 中由引擎写入的状态行（非输入内容）—— 保留输入表时仍要清掉
DERIVED_DOC_TYPES: tuple[str, ...] = (
    "step1_status", "test_case", "smoke_intent", "smoke", "smoke_excel",
)

# 无 project 列、经 scope_type/scope_id 归属到项目的表
SCOPE_KEYED_TABLES: dict[str, str] = {"rag_chunks": "scope_type", "qa_feedback": "scope_type"}

# 子键列候选（按优先级）——``mysql://table/sub`` 的 sub 落到第一个适用的列
SUB_KEY_COLUMNS: tuple[str, ...] = (
    "report_type", "doc_type", "snapshot_type", "req_id", "case_id",
    "chunk_id", "run_id", "version_label", "id",
)

# 历史虚拟表名 → 实际表名（旧 UI 链接里 mysql://screenshots/... 指向截图表）
TABLE_ALIASES: dict[str, str] = {"screenshots": "execution_screenshots"}


def _q(name: str) -> str:
    return "`%s`" % str(name).replace("`", "")


def project_scoped_tables(conn) -> dict[str, str]:
    """``{table: project_column}``，每次从 information_schema 实时派生。"""
    rows = conn.execute(
        "SELECT TABLE_NAME AS t, COLUMN_NAME AS c FROM information_schema.columns "
        "WHERE table_schema=DATABASE() AND COLUMN_NAME IN %s "
        "AND TABLE_NAME IN (SELECT TABLE_NAME FROM information_schema.tables "
        "                   WHERE table_schema=DATABASE() AND TABLE_TYPE='BASE TABLE')",
        (PROJECT_COLUMNS,),
    ).fetchall()
    out: dict[str, str] = {}
    for r in rows:
        if r["t"] not in out or r["c"] == "project":  # 同表两列都在时优先规范列
            out[r["t"]] = r["c"]
    return out


def table_columns(conn, table: str) -> set[str]:
    rows = conn.execute(
        "SELECT COLUMN_NAME AS c FROM information_schema.columns "
        "WHERE table_schema=DATABASE() AND table_name=%s",
        (table,),
    ).fetchall()
    return {r["c"] for r in rows}


def purge_plan(
    project: str, include_inputs: bool = False, keep_extra: tuple = ()
) -> list[dict]:
    """返回删除计划，每项 ``{table, where, args, label}``。

    ``include_inputs=True`` 时输入态也一并清空（源码变更后全量重取的语义）。
    """
    from src.core.db_manager import get_connection

    project = str(project or "").strip()
    if not project:
        raise ValueError("purge_plan: project 不能为空")

    conn = get_connection()
    try:
        scoped = project_scoped_tables(conn)
    finally:
        conn.close()

    keep = set(KEEP_REGISTRY_TABLES) | set(keep_extra or ())
    if not include_inputs:
        keep |= set(KEEP_INPUT_TABLES)

    plan = [
        {"table": t, "where": "%s=%%s" % _q(c), "args": (project,), "label": t}
        for t, c in scoped.items()
        if t not in keep
    ]
    plan.append(
        {  # 保留输入表时仍清掉其中的引擎状态行
            "table": "source_documents",
            "where": "project=%s AND doc_type IN (%s)"
            % ("%s", ",".join("%s" for _ in DERIVED_DOC_TYPES)),
            "args": (project,) + DERIVED_DOC_TYPES,
            "label": "source_documents[derived_doc_types]",
        }
    )
    plan += [
        {
            "table": t,
            "where": "%s='project' AND scope_id=%%s" % _q(c),
            "args": (project,),
            "label": t,
        }
        for t, c in SCOPE_KEYED_TABLES.items()
    ]
    return plan


def _execute(plan: list[dict], dry_run: bool) -> dict:
    """执行计划：``dry_run`` 只统计行数；否则同一事务内删除，任一失败整体回滚。"""
    from src.core.db_manager import get_connection

    counts: dict[str, int] = {}
    errors: list[str] = []
    conn = get_connection()
    try:
        conn.execute("SET FOREIGN_KEY_CHECKS=0")
        for item in plan:
            sql = "%s FROM %s WHERE %s" % (
                "SELECT COUNT(*) AS c" if dry_run else "DELETE",
                _q(item["table"]),
                item["where"],
            )
            try:
                cur = conn.execute(sql, item["args"])
                if dry_run:
                    counts[item["label"]] = int((cur.fetchone() or {}).get("c") or 0)
                else:
                    counts[item["label"]] = int(getattr(cur, "rowcount", 0) or 0)
            except Exception as e:  # noqa: BLE001
                errors.append("%s: %s" % (item["label"], e))
                _log.warning("[purge] %s 失败: %s", item["label"], str(e)[:160])
    except Exception as e:  # noqa: BLE001
        errors.append("清空失败: %s" % e)
    finally:
        try:
            conn.execute("SET FOREIGN_KEY_CHECKS=1")
        except Exception as e:  # noqa: BLE001
            _log.warning("[purge] 恢复外键约束失败: %s", e)
        if not dry_run:
            if errors:
                conn.rollback()
            else:
                conn.commit()
        conn.close()

    return {
        "tables": counts,
        "total": sum(counts.values()),
        "errors": errors,
        "dry_run": bool(dry_run),
    }


def purge_project(
    project: str, include_inputs: bool = False, dry_run: bool = False,
    keep_extra: tuple = (),
) -> dict:
    """清空一个项目的派生数据，返回 ``{project, tables, total, errors, dry_run}``。"""
    project = str(project or "").strip()
    if not project:
        raise ValueError("purge_project: project 不能为空")
    result = _execute(purge_plan(project, include_inputs, keep_extra), dry_run)
    result["project"] = project
    _log.info(
        "[purge] project=%s dry_run=%s 删除 %d 行 / %d 表, 失败 %d",
        project, dry_run, result["total"], len(result["tables"]), len(result["errors"]),
    )
    return result


def resolve_spec(conn, spec: str, version_label: str | None = None) -> dict | None:
    """把 ``mysql://table[/sub...]`` 解析成 ``{table, where, args, label}``（args 不含项目参数）。

    纯数字子键优先落主键 ``id``（``mysql://run_logs/<id>`` 与 ``mysql://run_logs/<run_id>``
    共用同一路径形态）；``version_label`` 只加在具备该列的表上，避免误删其它版本。
    """
    raw = str(spec or "").strip()
    if raw.startswith("mysql://"):
        raw = raw[len("mysql://"):]
    parts = [p for p in raw.split("/") if p]
    if not parts:
        return None
    table = TABLE_ALIASES.get(parts[0], parts[0])
    cols = table_columns(conn, table)
    if not cols:
        return None
    scoped = project_scoped_tables(conn)

    wheres: list[str] = []
    args: list = []
    if table in scoped:
        wheres.append("%s=%%s" % _q(scoped[table]))
    elif table in SCOPE_KEYED_TABLES:
        wheres += ["%s='project'" % _q(SCOPE_KEYED_TABLES[table]), "scope_id=%s"]
    else:
        return None

    key_cols = [c for c in SUB_KEY_COLUMNS if c in cols]
    for val in parts[1:]:
        if not key_cols:
            return None
        col = "id" if (val.isdigit() and "id" in key_cols) else key_cols[0]
        if col == "doc_type" and val == "upload":
            wheres.append("doc_type LIKE 'upload_%%'")
        else:
            wheres.append("%s=%%s" % _q(col))
            args.append(val)
        key_cols.remove(col)
    if version_label and "version_label" in cols:
        wheres.append("%s=%%s" % _q("version_label"))
        args.append(str(version_label))

    return {
        "table": table,
        "where": " AND ".join(wheres),
        "args": tuple(args),
        "label": "%s%s" % (table, "/" + "/".join(parts[1:]) if parts[1:] else ""),
    }


def purge_specs(
    project: str, specs, dry_run: bool = False, version_label: str | None = None
) -> dict:
    """按显式 ``mysql://table[/sub]`` 清单删除（UI 逐项清除路径），未识别目标记入 errors。"""
    from src.core.db_manager import get_connection

    project = str(project or "").strip()
    if not project:
        raise ValueError("purge_specs: project 不能为空")

    plan: list[dict] = []
    errors: list[str] = []
    conn = get_connection()
    try:
        for spec in specs or ():
            item = resolve_spec(conn, spec, version_label)
            if not item:
                errors.append("%s: 未识别的清理目标" % spec)
                _log.warning("[purge] 未识别的清理目标: %s", spec)
                continue
            item["args"] = (project,) + item["args"]
            plan.append(item)
    finally:
        conn.close()

    result = _execute(plan, dry_run)
    result["errors"] = errors + result["errors"]
    result["project"] = project
    return result
