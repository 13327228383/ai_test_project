# -*- coding: utf-8 -*-
"""变更影响面 → 回归范围 知识库核心逻辑。

低成本设计：
- 无外部依赖、无 AI 调用：检索用轻量关键词/重叠度打分（中英文通用）。
- 知识来自引擎已有的 executions 历史用例 + project_configs 功能域，构建即读即弃。
- 全部可调参数由调用方通过 cfg 字典注入（默认取自 config.yaml，用户通常不改）。
"""
from __future__ import annotations

import json
import logging
import re
from collections import defaultdict

_log = logging.getLogger(__name__)

# 风险权重：历史失败/不稳定/低置信的用例在回归时优先级更高
_RISK_WEIGHT = {
    "FAIL": 3.0,
    "ERROR": 3.0,
    "PARTIAL": 2.0,
    "SKIP": 0.3,
    "BLOCKED": 1.2,
    "PASS": 1.0,
}


def _norm_tokens(text: str):
    """英文按词、中文按字归一化，便于跨语言重叠度计算。"""
    text = (text or "").lower()
    return re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]", text)


def _overlap_score(q_tokens, d_tokens) -> float:
    """Jaccard 重叠度；任一为空返回 0。"""
    if not q_tokens or not d_tokens:
        return 0.0
    qs, ds = set(q_tokens), set(d_tokens)
    inter = qs & ds
    if not inter:
        return 0.0
    return len(inter) / (len(qs | ds))


def _get_conn():
    """惰性获取 DB 连接；不可用时返回 None（知识库退化为空，不阻断报告）。"""
    try:
        from src.core.db_manager import get_connection

        return get_connection()
    except Exception as e:  # noqa: BLE001
        _log.debug("[ChangeImpactKB] DB 连接不可用: %s", e)
        return None


class ChangeImpactKB:
    """变更影响面知识库：从引擎已有数据构建，给出回归范围建议。"""

    def __init__(self, project: str | None = None, cfg: dict | None = None):
        self.project = project
        self.cfg = cfg or {}
        self.cases: list[dict] = []
        self.areas: list[str] = []
        self.built = False

    # ──────────────────────────────────────────────────────────
    # 构建（支持外部注入数据，便于单测；生产走 build_from_engine）
    # ──────────────────────────────────────────────────────────
    def add_case(
        self,
        case_id: str,
        status: str = "PASS",
        confidence: float = 1.0,
        text: str = "",
        area: str | None = None,
    ):
        self.cases.append(
            {
                "case_id": case_id,
                "status": (status or "PASS").upper(),
                "confidence": float(confidence or 0.0),
                "text": text or case_id,
                "area": area or self._infer_area(case_id, text),
                "tokens": _norm_tokens((case_id + " " + (text or ""))[:600]),
            }
        )
        return self

    def add_area(self, area_name: str | None):
        if area_name and area_name not in self.areas:
            self.areas.append(str(area_name))
        return self

    @staticmethod
    def _infer_area(case_id: str, text: str) -> str | None:
        """从用例 ID 前缀推断功能域，如 TC-PATIENT-001 -> patient。"""
        m = re.match(r"^(?:TC|CASE)[-_]([A-Za-z\u4e00-\u9fff]+)", case_id or "")
        if m:
            return m.group(1).lower()
        return None

    def build_from_engine(self, project: str, conn=None):
        """生产路径：从 executions 表 + project_configs 构建知识库。"""
        self.project = project
        conn = conn or _get_conn()
        if conn is None:
            self.built = True
            return self
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT case_id, status, confidence, expected, actual, details "
                "FROM executions WHERE project=%s ORDER BY executed_at DESC LIMIT 5000",
                (project,),
            )
            for row in cur.fetchall():
                cid = row.get("case_id") or ""
                status = row.get("status") or "PASS"
                conf = row.get("confidence") or 0.0
                text = " ".join(
                    str(row.get(k) or "")
                    for k in ("expected", "actual", "details", "case_id")
                )
                self.add_case(cid, status, conf, text)
            cur.close()
        except Exception as e:  # noqa: BLE001
            _log.debug("[ChangeImpactKB] 读取 executions 失败(降级为空知识库): %s", e)
        finally:
            try:
                conn.close()
            except Exception as _e:  # noqa: BLE001
                _log.debug("build_from_engine best-effort 容忍: %s", str(_e)[:120])
        self._load_areas_from_project_config(project)
        self.built = True
        return self

    def _load_areas_from_project_config(self, project: str):
        """从 project_configs 抽取已知功能域（页面/接口/模块），用于变更信号映射。"""
        conn = _get_conn()
        if conn is None:
            return
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT config_json FROM project_configs WHERE project=%s", (project,)
            )
            row = cur.fetchone()
            cur.close()
            if not row or not row.get("config_json"):
                return
            raw = row["config_json"]
            data = raw if isinstance(raw, dict) else json.loads(raw)
            for key in ("pages", "apis", "modules", "functional_modules", "nav", "endpoints"):
                v = data.get(key)
                if isinstance(v, list):
                    for item in v:
                        name = (
                            item.get("name")
                            or item.get("title")
                            or (item if isinstance(item, str) else None)
                        )
                        if name:
                            self.add_area(str(name))
                elif isinstance(v, dict):
                    for name in v.keys():
                        self.add_area(str(name))
        except Exception as e:  # noqa: BLE001
            _log.debug("[ChangeImpactKB] 读取项目功能域失败(跳过): %s", e)
        finally:
            try:
                conn.close()
            except Exception as _e:  # noqa: BLE001
                _log.debug("_load_areas_from_project_config best-effort 容忍: %s", str(_e)[:120])

    # ──────────────────────────────────────────────────────────
    # 检索
    # ──────────────────────────────────────────────────────────
    def recommend(self, changed_signals: list[str] | None = None, top_k: int | None = None):
        top_k = top_k or int(self.cfg.get("top_k", 20))
        min_sim = float(self.cfg.get("min_similarity", 0.3))
        changed_signals = changed_signals or []
        notes: list[str] = []

        if changed_signals:
            return self._recommend_impact_driven(changed_signals, top_k, min_sim, notes)
        return self._recommend_risk_priority(top_k, notes)

    def _recommend_impact_driven(self, changed_signals, top_k, min_sim, notes):
        impacted_areas: list[str] = []
        scored: list[tuple[float, dict]] = []
        for sig in changed_signals:
            q_tokens = _norm_tokens(sig)
            # 1) 已知功能域直接匹配
            for area in self.areas:
                if area and (area.lower() in sig.lower() or sig.lower() in area.lower()):
                    if area not in impacted_areas:
                        impacted_areas.append(area)
            # 2) 用例关键词/重叠度匹配
            for c in self.cases:
                s = _overlap_score(q_tokens, c["tokens"])
                if s >= min_sim:
                    risk = _RISK_WEIGHT.get(c["status"], 1.0)
                    boost = 1.0 + (1.0 - min(c["confidence"], 1.0))  # 低置信加分
                    scored.append((s * risk * boost, c))
        # 去重（保留最高分）
        best: dict[str, tuple[float, dict]] = {}
        for score, c in scored:
            prev = best.get(c["case_id"])
            if prev is None or score > prev[0]:
                best[c["case_id"]] = (score, c)
        ranked = sorted(best.values(), key=lambda x: x[0], reverse=True)[:top_k]
        regression = [self._case_view(c) for _, c in ranked]
        if not impacted_areas:
            notes.append(
                "未从变更信号中解析出已知功能域，已按关键词匹配历史用例。"
            )
        return {
            "enabled": True,
            "method": "impact_driven",
            "project": self.project,
            "impacted_areas": impacted_areas,
            "regression_cases": regression,
            "total_cases": len(self.cases),
            "notes": notes,
        }

    def _recommend_risk_priority(self, top_k, notes):
        def _prio(c):
            risk = _RISK_WEIGHT.get(c["status"], 1.0)
            return risk * (1.0 + (1.0 - min(c["confidence"], 1.0)))

        ranked = sorted(self.cases, key=_prio, reverse=True)[:top_k]
        regression = [self._case_view(c) for c in ranked]
        notes.append(
            "未提供变更信号，按历史风险（失败/不稳定/低置信）给出全局回归优先级。"
        )
        if not self.cases:
            notes.append("知识库为空（无 executions 历史），无法给出回归建议。")
        return {
            "enabled": True,
            "method": "risk_priority",
            "project": self.project,
            "impacted_areas": [],
            "regression_cases": regression,
            "total_cases": len(self.cases),
            "notes": notes,
        }

    @staticmethod
    def _case_view(c: dict) -> dict:
        return {
            "case_id": c["case_id"],
            "status": c["status"],
            "confidence": round(c["confidence"], 3),
            "area": c.get("area"),
        }
