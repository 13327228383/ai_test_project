# -*- coding: utf-8 -*-
from __future__ import annotations
from typing import Dict
from src.core.guarded import guard

"""
Matcher Orchestrate Mixin: 匹配编排 + 主入口
"""

from pathlib import Path
from typing import Any
import logging

_log = logging.getLogger(__name__)

from src.extensions.deployment.parser import (
    DeploymentRow,
    MatchDetail,
    FeatureMatchResult,
    MatchReport,
    PHASE1_THRESHOLD,
    PHASE1_THRESHOLD_FALLBACK,
    PHASE2_AUTO,
    PHASE2_AUTO_FALLBACK,
    PHASE2_REVIEW,
    parse_markdown_sections,
    detect_transfer_scope_section,
    parse_table,
    filter_by_version,
    list_versions,
)


class _MatcherOrchestrateMixin:
    """提供匹配编排和主入口方法。"""

    @staticmethod
    def _load_doc_text(doc_path: str) -> str | None:
        """★ 全 MySQL：按 path 加载部署文档文本。
        - mysql:// URI 或虚拟相对路径 → resolve_doc 查 MySQL（source_documents.deployment 等）
        - 纯本地文件路径（用户显式传入）→ 磁盘读取兼容
        返回文本或 None（不存在）。
        """
        if not doc_path:
            return None
        # mysql:// URI 或虚拟路径：走 MySQL 解析
        if doc_path.startswith("mysql://") or "/" in doc_path:
            with guard("_load_doc_text: from src.core.config_loader import c", log=_log):
                from src.core.config_loader import config
                from src.backend.routes.docs_analytics.fetchers.mysql_doc import (
                    resolve_doc,
                )

                doc = resolve_doc(doc_path, getattr(config, "project_name", ""))
                if doc and doc.get("kind") in ("text", "log"):
                    return doc["content_text"] or ""
        # 纯本地文件兼容
        try:
            p = Path(doc_path)
            if p.is_file():
                return p.read_text(encoding="utf-8")
        except Exception as _e:
            _log.debug("resolve + read: %s", _e, exc_info=True)
        return None

    # ═══════════════════════════════════════════
    # 匹配编排
    # ═══════════════════════════════════════════

    def match_row_to_intents(
        self,
        row: DeploymentRow,
        intents: list[dict],
        phase1_threshold: float = None,
        phase2_auto: float = None,
    ) -> FeatureMatchResult:
        """
        将一个部署文档行匹配到测试用例集合。

        两阶段：
        1. Phase1：模块名模糊匹配 → 候选集
        2. Phase2：功能点语义匹配 → 精排
        综合分 = 0.3 × phase1 + 0.7 × phase2
        """
        if phase1_threshold is None:
            phase1_threshold = (
                PHASE1_THRESHOLD if self._use_embeddings else PHASE1_THRESHOLD_FALLBACK
            )
        if phase2_auto is None:
            phase2_auto = PHASE2_AUTO if self._use_embeddings else PHASE2_AUTO_FALLBACK

        result = FeatureMatchResult(deployment_row=row)

        has_catalog_info = any(
            str(c.get("catalog_name", "")).strip()
            for c in intents[: min(20, len(intents))]
        )

        candidates: list[tuple[float, Dict]] = []
        for intent in intents:
            if has_catalog_info:
                catalog = str(intent.get("catalog_name") or "")
                if not catalog:
                    catalog = self._extract_module_hint(intent)
                score = self._match_module(row.module, catalog)
                if score >= phase1_threshold:
                    candidates.append((score, intent))
            else:
                score = self._search_module_in_intent(row.module, intent)
                if score >= phase1_threshold:
                    candidates.append((score, intent))

        if not candidates:
            candidates = self._fallback_search(row.feature, intents)
            if not candidates:
                return result

        # Phase 2: 功能点语义匹配
        feature_text = f"{row.feature} {row.notes}".strip()

        matched = []
        low_conf = []
        for p1_score, intent in candidates:
            p2_score = self._compute_feature_similarity(feature_text, intent)
            combined = 0.3 * p1_score + 0.7 * p2_score

            detail = MatchDetail(
                case_id=str(intent.get("case_id", "")),
                title=str(intent.get("title", ""))[:60],
                phase1_score=round(p1_score, 3),
                phase2_score=round(p2_score, 3),
                combined_score=round(combined, 3),
                method="embedding" if self._use_embeddings else "keyword",
            )

            if combined >= phase2_auto:
                matched.append(detail)
            elif combined >= PHASE2_REVIEW:
                low_conf.append(detail)

        matched.sort(key=lambda x: x.combined_score, reverse=True)
        low_conf.sort(key=lambda x: x.combined_score, reverse=True)

        result.matched_intents = matched
        result.low_confidence = low_conf
        result.unmatched = len(matched) == 0 and len(low_conf) == 0
        return result

    # ═══════════════════════════════════════════
    # 主入口
    # ═══════════════════════════════════════════

    def filter_intents_by_deployment(
        self,
        doc_path: str = "",
        intents: list[dict] = None,
        version: str = "",
        doc_content: str = "",
    ) -> dict[str, Any]:
        """
        主入口：从部署文档中提取转测范围，匹配测试用例，返回 case_ids。

        优先使用 doc_content（内存字符串），否则回退读 doc_path 磁盘文件。
        """
        # 1. 读取文档
        if doc_content:
            doc_text = doc_content
        elif doc_path:
            # ★ 全 MySQL：mysql:// URI 或虚拟路径用 resolve_doc 解析；
            #   纯本地文件路径（用户显式传入）仍支持磁盘读取作为兼容。
            doc_text = self._load_doc_text(doc_path)
            if doc_text is None:
                return {
                    "case_ids": [],
                    "matched_count": 0,
                    "total_features": 0,
                    "error": f"部署文档不存在: {doc_path}",
                }
        else:
            return {
                "case_ids": [],
                "matched_count": 0,
                "total_features": 0,
                "error": "未提供部署文档内容或路径",
            }

        # 2. 解析章节
        sections = parse_markdown_sections(doc_text)
        if not sections:
            return {
                "case_ids": [],
                "matched_count": 0,
                "total_features": 0,
                "error": "文档无章节结构",
            }

        # 3. 检测转测范围章节
        scope_section = detect_transfer_scope_section(sections)
        if not scope_section:
            return {
                "case_ids": [],
                "matched_count": 0,
                "total_features": 0,
                "error": (
                    "未检测到转测范围章节。"
                    "支持的标题格式：'9. 转测范围'、'转测范围'、'范围' 等，"
                    "内容需包含模块/功能点/版本的表格"
                ),
            }

        section_heading = scope_section.get("heading", "(未知)")
        _log.info(f"  [DeploymentMatcher] 检测到章节: {section_heading}")

        # 4. 解析表格
        rows = parse_table(scope_section.get("body", ""))
        if not rows:
            return {
                "case_ids": [],
                "matched_count": 0,
                "total_features": 0,
                "error": "转测范围章节中未解析到表格数据，请检查表格格式",
            }

        _log.info(f"  [DeploymentMatcher] 解析到 {len(rows)} 行表格数据")

        # 5. 版本过滤
        if version:
            rows = filter_by_version(rows, version)
            if not rows:
                return {
                    "case_ids": [],
                    "matched_count": 0,
                    "total_features": 0,
                    "error": f"没有匹配版本 {version} 的功能点（可用版本: {list_versions(rows)}）",
                }
            _log.info(
                f"  [DeploymentMatcher] 版本过滤 ({version}): {len(rows)} 个功能点"
            )
        else:
            _log.info(
                f"  [DeploymentMatcher] 无版本过滤，全部 {len(rows)} 个功能点参与匹配"
            )

        # 6. 逐行匹配
        all_results = []
        matched_case_ids = set()

        for row_dict in rows:
            dep_row = DeploymentRow(
                module=row_dict.get("module", ""),
                feature=row_dict.get("feature", row_dict.get("notes", "")),
                version=row_dict.get("version", ""),
                prd_priority=row_dict.get("prd_priority", ""),
                notes=row_dict.get("notes", ""),
            )
            result = self.match_row_to_intents(dep_row, intents)
            all_results.append(result)
            for d in result.matched_intents:
                matched_case_ids.add(d.case_id)

        # 7. 构建报告
        unmatched_features = [r.deployment_row for r in all_results if r.unmatched]
        low_conf_matches = []
        for r in all_results:
            for ld in r.low_confidence:
                low_conf_matches.append((r.deployment_row, ld))

        report = MatchReport(
            total_features=len(rows),
            matched_features=len(rows) - len(unmatched_features),
            unmatched_features=unmatched_features,
            low_confidence_matches=low_conf_matches,
            results=all_results,
        )

        return {
            "case_ids": list(matched_case_ids),
            "matched_count": len(matched_case_ids),
            "total_features": len(rows),
            "report": report,
            "section_heading": section_heading,
        }
