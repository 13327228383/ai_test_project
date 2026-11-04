# -*- coding: utf-8 -*-
from __future__ import annotations
from src.core.guarded import guard

"""
docs_analytics.fetchers.mysql_doc — MySQL 虚拟文档解析层
=========================================================

统一处理「文档预览/保存/下载」三类操作的 MySQL 读取，彻底取代扫 data/{project}/ 磁盘。

支持两类 path 协议（前端零改动，双协议并存）：
  1. mysql:// URI（_file_tree.py 虚拟树叶子返回的标准形式）：
     - mysql://source_documents/{doc_type}   → source_documents 表内容
     - mysql://generated_reports/{report_type} → generated_reports 表内容（报告类）
     - mysql://requirements                  → requirements 表汇总（markdown）
     - mysql://test_cases            → test_cases 表汇总（xlsx 二进制）
     - mysql://screenshots/{case_id}       → execution_screenshots 二进制（PNG）
     - mysql://run_logs/{run_id}             → run_logs 表汇总（纯文本）
  2. 虚拟相对路径（前端旧调用点遗留，_infer_doc_type 推断 doc_type 后回查 source_documents / generated_reports）：
     - 过程产物/测试用例/{file}             → test_cases 表汇总
     - 过程产物/{name} / 智能分析/{name}    → _infer_doc_type 推断
     - 原始数据/接口文档/yapi_docs.json     → source_documents(doc_type='yapi')
     - 原始数据/数据库/system_data_catalog  → source_documents(doc_type='db_schema')

对外只暴露两个纯函数（无 Flask 依赖，高度解耦）：
  - resolve_doc(path, project) -> {ok, kind, ext, content_bytes, content_text, doc_type} 或 None
  - doc_type_from_mysql_uri(path) -> str|None   （仅 URI 形态判定，不查库）

设计原则：本模块只做「路径 → MySQL 数据」的解析，不做渲染（渲染由 routes 层负责）。
"""

import json as _json
import logging
from typing import Any

_log = logging.getLogger(__name__)

# source_documents.doc_type → 预览扩展名（决定 routes 层渲染分支）
_DOC_TYPE_EXT = {
    "yapi": ".json",
    "yapi_structured": ".json",  # ★ fix: 结构化版含 interfaces 数组，按 JSON 渲染以支持一接口一行
    "db_schema": ".json",
    "smoke_excel": ".xlsx",
    "test_data_excel": ".xlsx",
    "xmind": ".xmind",
    # confluence / requirement / figma / deployment → .md
    # report / html_report / coverage_report 已迁移到 generated_reports，不在此映射
}

# mysql:// 非表名（requirements/test_cases/screenshots/run_logs）→ 扩展名
_TABLE_EXT = {
    "requirements": ".md",  # 汇总为 markdown（可编辑）
    "test_cases": ".xlsx",  # 汇总为 xlsx（不可编辑）
    "screenshots": ".png",  # 二进制 png（不可编辑）
    "run_logs": ".log",  # 文本日志（只读）
}

# 二进制 doc_type（source_documents.content 存 base64，需解码）
_BINARY_DOC_TYPES = {"smoke_excel", "test_data_excel", "xmind"}


def doc_type_from_mysql_uri(path: str) -> str | None:
    """仅判定 mysql:// URI 形态，返回提取的 doc_type / report_type 或表名，不查库。
    用于 _save._infer_doc_type 的优先快速路径。"""
    if not path or not isinstance(path, str) or not path.startswith("mysql://"):
        return None
    rest = path[len("mysql://") :]
    parts = rest.split("/")
    if not parts:
        return None
    table = parts[
        0
    ]  # source_documents / generated_reports / requirements / test_cases / screenshots / run_logs
    if table in ("source_documents", "generated_reports"):
        # mysql://source_documents/{doc_type}
        # mysql://generated_reports/{report_type}
        return parts[1] if len(parts) > 1 else None
    return table  # requirements / test_cases / screenshots / run_logs


def _ext_for_doc_type(dt: str) -> str:
    """doc_type 或 mysql:// 表名 → 扩展名。先查表名映射，再查 doc_type 映射，默认 .md。"""
    return _TABLE_EXT.get(dt) or _DOC_TYPE_EXT.get(dt, ".md")


def _conn():
    from src.core.db_manager import get_connection

    return get_connection()


def _active_version_id(project: str) -> int:
    """当前项目 active version 的数字 ID；无活跃版本时返回 0（legacy）。

    统一版本隔离读入口：避免各分支反复 import + 重复转换逻辑。
    """
    try:
        from src.core.versioning import current_version_id

        return int(current_version_id(project) or 0)
    except Exception as _e:
        _log.debug("[mysql_doc] active_version_id 读取失败: %s", _e, exc_info=True)
        return 0


def resolve_doc(path: str, project: str) -> dict[str, Any] | None:
    """把 path 解析为 MySQL 数据。返回 dict 或 None（不存在的 path）。

    返回字段：
      ok: True
      kind: 'text' | 'binary' | 'xlsx' | 'png' | 'log'
      ext: 预览扩展名（.md/.json/.xlsx/.xmind/.png/.log）
      content_bytes: bytes（binary/xlsx/png）或 None
      content_text:  str（text/log）或 None
      doc_type: 原始 doc_type（source_documents 类）或表名（其他）
      meta: 额外元信息（case_id/run_id 等）
    """
    if not path or not project:
        return None
    try:
        if path.startswith("mysql://"):
            return _resolve_mysql_uri(path, project)
        return _resolve_virtual_relpath(path, project)
    except Exception as e:
        _log.warning("[mysql_doc] 解析 %r 失败: %s", path, str(e)[:120])
        return None


def _resolve_mysql_uri(path: str, project: str) -> dict[str, Any] | None:
    """解析 mysql:// URI。"""
    rest = path[len("mysql://") :]
    parts = rest.split("/")
    table = parts[0] if parts else ""
    conn = None
    try:
        conn = _conn()
        if table == "source_documents":
            dt = parts[1] if len(parts) > 1 else ""
            if not dt:
                return None
            # ★ 优先读 active version_label 行，回退最新行（防空 version_label 孤立行误读）
            try:
                from src.core.versioning import current_version_label

                _vlabel = current_version_label(project) or ""
            except Exception:
                _vlabel = ""
            row = None
            if _vlabel:
                row = conn.execute(
                    "SELECT content, file_format FROM source_documents "
                    "WHERE project=%s AND doc_type=%s AND version_label=%s LIMIT 1",
                    (project, dt, _vlabel),
                ).fetchone()
            if row is None:
                row = conn.execute(
                    "SELECT content, file_format FROM source_documents "
                    "WHERE project=%s AND doc_type=%s ORDER BY updated_at DESC LIMIT 1",
                    (project, dt),
                ).fetchone()
            if row is None:
                return None
            content = row["content"]
            ext = _ext_for_doc_type(dt)
            if dt in _BINARY_DOC_TYPES:
                # base64 解码为二进制
                import base64

                try:
                    raw = base64.b64decode(content)
                except Exception:
                    raw = content.encode("utf-8", errors="ignore")
                return {
                    "ok": True,
                    "kind": "binary",
                    "ext": ext,
                    "content_bytes": raw,
                    "content_text": None,
                    "doc_type": dt,
                    "meta": {},
                }
            # 文本类
            return {
                "ok": True,
                "kind": "text",
                "ext": ext,
                "content_bytes": None,
                "content_text": str(content),
                "doc_type": dt,
                "meta": {},
            }

        if table == "generated_reports":
            report_type = parts[1] if len(parts) > 1 else ""
            if not report_type:
                return None
            # ★ 优先读 active version_label 行，回退最新行
            try:
                from src.core.versioning import current_version_label

                _vlabel = current_version_label(project) or ""
            except Exception:
                _vlabel = ""
            row = None
            if _vlabel:
                row = conn.execute(
                    "SELECT content, report_type FROM generated_reports "
                    "WHERE project=%s AND report_type=%s AND version_label=%s "
                    "ORDER BY updated_at DESC LIMIT 1",
                    (project, report_type, _vlabel),
                ).fetchone()
            if row is None:
                row = conn.execute(
                    "SELECT content, report_type FROM generated_reports "
                    "WHERE project=%s AND report_type=%s ORDER BY updated_at DESC LIMIT 1",
                    (project, report_type),
                ).fetchone()
            if row is None:
                return None
            content = row["content"]
            # report / coverage_report → markdown; html_report → 原样 HTML 文本
            ext = ".html" if report_type == "html_report" else ".md"
            return {
                "ok": True,
                "kind": "text",
                "ext": ext,
                "content_bytes": None,
                "content_text": str(content),
                "doc_type": report_type,
                "meta": {},
            }

        if table == "requirements":
            # ★ 需求逐版本物理克隆（clone_version_baseline），必须按 active version 读取，
            #   否则会跨版本重复展示同一批 req_id。依赖( dependency_req_id )在同一克隆基线内解析。
            rows = conn.execute(
                "SELECT req_id, module, description, payload_json FROM requirements "
                "WHERE project=%s AND version_id=%s ORDER BY req_id LIMIT 2000",
                (project, _active_version_id(project)),
            ).fetchall()
            if not rows:
                return None

            # ★ v6: 从 payload_json 提取蓝红对抗指标
            consensus_stats = {
                "HIGHEST": 0,
                "HIGH": 0,
                "MEDIUM": 0,
                "LOW": 0,
                "SUPPLEMENTARY": 0,
            }
            verdict_stats = {
                "approved": 0,
                "revised": 0,
                "rejected": 0,
                "supplementary": 0,
            }
            lines = ["# 需求拆解（共 %d 条）\n" % len(rows)]

            for r in rows:
                req_id = r.get("req_id", "")
                module = r.get("module", "")
                desc = r.get("description", "")

                # 提取蓝红指标
                payload = {}
                with guard("_resolve_mysql_uri: import json as _json", log=_log):
                    import json as _json

                    payload_str = r.get("payload_json") or "{}"
                    payload = (
                        _json.loads(payload_str)
                        if isinstance(payload_str, str)
                        else (payload_str or {})
                    )

                consensus = payload.get("consensus_level", "")
                perspectives = payload.get("source_perspectives", [])
                verdict = payload.get("verdict", "")
                perspective_count = payload.get(
                    "perspective_count",
                    len(perspectives) if isinstance(perspectives, list) else 0,
                )

                # 统计
                if consensus in consensus_stats:
                    consensus_stats[consensus] += 1
                if verdict in verdict_stats:
                    verdict_stats[verdict] += 1

                # 标记
                badges = []
                if consensus:
                    icon = {
                        "HIGHEST": "★★★",
                        "HIGH": "★★",
                        "MEDIUM": "■",
                        "LOW": "○",
                        "SUPPLEMENTARY": "+",
                    }.get(consensus, "")
                    badges.append("%s%s" % (icon, consensus))
                if verdict:
                    v_icon = {
                        "approved": "✅",
                        "revised": "✏️",
                        "rejected": "❌",
                        "supplementary": "➕",
                    }.get(verdict, "")
                    badges.append("%s%s" % (v_icon, verdict))
                if perspective_count:
                    badges.append("%d视角" % perspective_count)

                badge_str = (" [`%s`]" % " | ".join(badges)) if badges else ""
                lines.append("## %s — %s%s\n%s\n" % (req_id, module, badge_str, desc))

            # 附加汇总段落
            lines.append("---\n")
            lines.append("## 🔵🔴 蓝红对抗共识摘要\n")
            lines.append("| 共识级别 | 数量 | 占比 |")
            lines.append("|---------|------|------|")
            total = len(rows)
            for level in ["HIGHEST", "HIGH", "MEDIUM", "LOW", "SUPPLEMENTARY"]:
                cnt = consensus_stats.get(level, 0)
                if cnt > 0:
                    lines.append(
                        "| %s | %d | %.1f%% |" % (level, cnt, cnt / max(total, 1) * 100)
                    )
            lines.append("")
            lines.append("| 仲裁判决 | 数量 |")
            lines.append("|---------|------|")
            for v in ["approved", "revised", "rejected", "supplementary"]:
                cnt = verdict_stats.get(v, 0)
                if cnt > 0:
                    lines.append("| %s | %d |" % (v, cnt))

            return {
                "ok": True,
                "kind": "text",
                "ext": ".md",
                "content_bytes": None,
                "content_text": "\n".join(lines),
                "doc_type": "requirements",
                "meta": {
                    "count": len(rows),
                    "consensus": consensus_stats,
                    "verdicts": verdict_stats,
                },
            }

        if table == "test_cases":
            # ★ 测试用例采用虚拟继承（copy-on-read）：新版本不物理复制旧版用例，
            #   直接按 version_id 过滤会丢掉继承自旧版本的用例。必须走规范读取
            #   case_store.list_intents()（版本作用域 + 继承展开），而非裸 SQL。
            try:
                from src.pipeline.utils.case_store import case_store

                # ★ 必须不带 limit 调用：list_intents 在 limit 非 None 时
                #   会跳过虚拟继承展开，导致丢弃继承自旧版本的用例（与版本有效用例口径割裂）。
                #   版本作用域 + 继承展开由 store 内部完成。
                rows = case_store.list_intents()
                if rows and len(rows) > 2000:
                    rows = rows[:2000]
            except Exception as _e:
                _log.warning(
                    "[mysql_doc] test_cases 规范读取失败, 回退空: %s", _e
                )
                rows = []
            if not rows:
                return None
            xlsx_bytes = _intents_to_xlsx_bytes(rows)
            return {
                "ok": True,
                "kind": "xlsx",
                "ext": ".xlsx",
                "content_bytes": xlsx_bytes,
                "content_text": None,
                "doc_type": "test_cases",
                "meta": {"count": len(rows)},
            }

        if table == "screenshots":
            case_id = parts[1] if len(parts) > 1 else ""
            if not case_id:
                return None
            row = conn.execute(
                "SELECT image_data, file_size FROM execution_screenshots "
                "WHERE project=%s AND version_id=%s AND case_id=%s ORDER BY created_at DESC LIMIT 1",
                (project, _active_version_id(project), case_id),
            ).fetchone()
            if not row or not row.get("image_data"):
                return None
            return {
                "ok": True,
                "kind": "png",
                "ext": ".png",
                "content_bytes": bytes(row["image_data"]),
                "content_text": None,
                "doc_type": "screenshots",
                "meta": {"case_id": case_id},
            }

        if table == "run_logs":
            run_id = parts[1] if len(parts) > 1 else ""
            if not run_id:
                return None
            rows = conn.execute(
                "SELECT step_num, log_level, message, created_at FROM run_logs "
                "WHERE project=%s AND version_id=%s AND run_id=%s ORDER BY id ASC",
                (project, _active_version_id(project), run_id),
            ).fetchall()
            lines = [
                "[%s] [%s] [step%s] %s"
                % (
                    r.get("created_at", ""),
                    r.get("log_level", ""),
                    r.get("step_num", ""),
                    r.get("message", ""),
                )
                for r in rows or []
            ]
            if not lines:
                return None
            return {
                "ok": True,
                "kind": "log",
                "ext": ".log",
                "content_bytes": None,
                "content_text": "\n".join(lines),
                "doc_type": "run_logs",
                "meta": {"run_id": run_id, "count": len(lines)},
            }

        if table == "figma_pages":
            # ★ fix: Figma 设计稿汇总预览 — 从 figma_pages 表读取并汇总为 markdown
            try:
                from src.core.versioning import current_version_label

                _vlabel = current_version_label(project) or ""
            except Exception:
                _vlabel = ""
            rows = conn.execute(
                "SELECT page_id, name, canvas, page_type, element_count, ui_structure, updated_at "
                "FROM figma_pages WHERE project=%s AND version_label=%s "
                "ORDER BY canvas, name LIMIT 500",
                (project, _vlabel),
            ).fetchall()
            if not rows:
                # 回退到最新版本
                rows = conn.execute(
                    "SELECT page_id, name, canvas, page_type, element_count, ui_structure, updated_at "
                    "FROM figma_pages WHERE project=%s ORDER BY updated_at DESC LIMIT 500",
                    (project,),
                ).fetchall()
            if not rows:
                return None
            lines = ["# Figma 设计稿（共 %d 页）\n" % len(rows)]
            for r in rows:
                name = r.get("name", "")
                canvas = r.get("canvas", "")
                ptype = r.get("page_type", "")
                elem = r.get("element_count", 0)
                ui = r.get("ui_structure", "") or ""
                lines.append("## %s" % name)
                meta_parts = []
                if canvas:
                    meta_parts.append("画布: %s" % canvas)
                if ptype:
                    meta_parts.append("类型: %s" % ptype)
                meta_parts.append("元素: %d" % elem)
                lines.append("> " + " · ".join(meta_parts) + "\n")
                if ui:
                    # ui_structure 可能较长，截断展示前 2000 字符
                    lines.append("```\n%s\n```" % ui[:2000])
                lines.append("")
            return {
                "ok": True,
                "kind": "text",
                "ext": ".md",
                "content_bytes": None,
                "content_text": "\n".join(lines),
                "doc_type": "figma_pages",
                "meta": {"count": len(rows)},
            }
        return None
    finally:
        if conn:
            with guard("_resolve_mysql_uri: conn.close()", log=_log):
                conn.close()


def _resolve_virtual_relpath(path: str, project: str) -> dict[str, Any] | None:
    """解析虚拟相对路径（前端旧调用点）。复用 _infer_doc_type 推断后回查 source_documents / generated_reports。"""
    from src.backend.routes.docs_analytics.fetchers.search import _infer_doc_type

    p = path.replace("\\", "/").lower()
    # 测试用例虚拟路径 → test_cases 汇总
    if "过程产物/测试用例" in p or "接口意图" in p and "intent" in p:
        return _resolve_mysql_uri("mysql://test_cases", project)
    # 测试设计 xmind → source_documents(doc_type='xmind')
    if "测试设计" in p or p.endswith(".xmind"):
        return _resolve_mysql_uri("mysql://source_documents/xmind", project)
    # 报告类虚拟路径 → generated_reports
    if "报告" in p or "report" in p:
        if "html" in p:
            return _resolve_mysql_uri("mysql://generated_reports/html_report", project)
        if "coverage" in p or "覆盖率" in p:
            return _resolve_mysql_uri(
                "mysql://generated_reports/coverage_report", project
            )
        return _resolve_mysql_uri("mysql://generated_reports/report", project)
    doc_type = _infer_doc_type(path)
    if not doc_type:
        return None
    # _infer_doc_type 的 fallback 是文件名 stem，对未知路径查不到 → 返回 None
    if doc_type == path.rsplit("/", 1)[-1].rsplit(".", 1)[0].lower():
        return None
    return _resolve_mysql_uri("mysql://source_documents/%s" % doc_type, project)


def _intents_to_xlsx_bytes(rows) -> bytes:
    """把 test_cases 行列表转成 xlsx 二进制（复用 openpyxl，纯内存 BytesIO）。"""
    import io
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "测试用例"
    ws.append(["case_id", "title", "case_type", "risk_level", "expected_outcome"])
    for r in rows:
        ws.append(
            [
                r.get("case_id", ""),
                r.get("title", ""),
                r.get("case_type", ""),
                r.get("risk_level", ""),
                r.get("expected_outcome", ""),
            ]
        )
    buf = io.BytesIO()
    wb.save(buf)
    wb.close()
    return buf.getvalue()
