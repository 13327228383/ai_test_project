# -*- coding: utf-8 -*-
"""变更影响→回归范围 知识库：Step5 报告区块。

对接 report_generator：build_* 取数 + render_* 输出 Markdown。
默认开箱即用（CHANGE_IMPACT_KB_ENABLED=True），知识来自 executions + project_configs，
变更信号优先取入参，其次取 project_configs.deployDoc / changed_modules（转测文档/变更清单）。
"""
from __future__ import annotations
from src.core.guarded import guard

import json
import logging
import re

from .kb import ChangeImpactKB

_log = logging.getLogger(__name__)


def _rc(key: str, default):
    """读取配置（pipeline_config 走路径 B，未配置回落 default）。"""
    try:
        from src.core.pipeline_config import _read_config_value as _rcv

        return _rcv(key, default)
    except Exception:  # noqa: BLE001
        return default


def _read_project_config_json(project: str) -> dict:
    """读取 project_configs.config_json（自带连接，避免复用已关闭连接）。"""
    with guard("_read_project_config_json: from src.core.db_manager import get_", log=_log):
        from src.core.db_manager import get_connection

        conn = get_connection()
        if conn is None:
            return {}
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT config_json FROM project_configs WHERE project=%s", (project,)
            )
            row = cur.fetchone()
            cur.close()
            if row and row.get("config_json"):
                raw = row["config_json"]
                return raw if isinstance(raw, dict) else json.loads(raw)
        finally:
            with guard("_read_project_config_json: conn.close()", log=_log):
                conn.close()
    return {}


def build_change_impact_section(
    project: str, run_id: str = "", changed_signals: list[str] | None = None
) -> dict:
    """构建'变更影响→回归范围'建议区块数据。"""
    enabled = bool(_rc("CHANGE_IMPACT_KB_ENABLED", True))
    if not enabled:
        return {"enabled": False, "skipped": "disabled"}

    cfg = {
        "top_k": int(_rc("CHANGE_IMPACT_KB_TOP_K", 20)),
        "min_similarity": float(_rc("CHANGE_IMPACT_KB_MIN_SIMILARITY", 0.3)),
    }

    kb = ChangeImpactKB(project=project, cfg=cfg)
    kb.build_from_engine(project)

    # 变更信号来源：入参 > project_configs.deployDoc / changed_modules
    signals = list(changed_signals or [])
    if not signals:
        pc = _read_project_config_json(project)
        doc = pc.get("deployDoc") or pc.get("changed_modules") or ""
        if isinstance(doc, str) and doc.strip():
            signals = [s.strip() for s in re.split(r"[\n,;，；]+", doc) if s.strip()]

    result = kb.recommend(signals)
    result["run_id"] = run_id
    return result


def render_change_impact_section(data: dict | None) -> str:
    """渲染 Markdown 区块；无数据/未启用返回空串（报告侧不追加空段）。"""
    if not data or not data.get("enabled"):
        return ""
    lines: list[str] = []
    lines.append("## 🔁 变更影响 → 回归范围建议")
    lines.append("")
    method = data.get("method")
    total = data.get("total_cases", 0)
    if method == "impact_driven":
        areas = data.get("impacted_areas") or []
        if areas:
            lines.append(f"- **识别到的变更影响域**：{', '.join(areas)}")
        lines.append(f"- **模式**：基于变更信号匹配历史用例（知识库共 {total} 条用例）")
    else:
        lines.append(
            f"- **模式**：未提供变更信号 → 按历史风险给出全局回归优先级（知识库共 {total} 条用例）"
        )
    lines.append("")
    cases = data.get("regression_cases") or []
    if not cases:
        lines.append("_暂无可推荐的回归用例（知识库为空或无匹配）。_")
    else:
        lines.append("| 优先级 | 用例 ID | 历史状态 | 置信度 | 功能域 |")
        lines.append("|---|---|---|---|---|")
        for i, c in enumerate(cases, 1):
            lines.append(
                f"| {i} | {c.get('case_id', '')} | {c.get('status', '')} | "
                f"{c.get('confidence', '')} | {c.get('area') or '-'} |"
            )
    for n in data.get("notes", []):
        lines.append("")
        lines.append(f"> 💡 {n}")
    return "\n".join(lines)
