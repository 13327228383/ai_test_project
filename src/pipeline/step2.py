# -*- coding: utf-8 -*-
"""Step 2: 功能清单驱动的需求拆解（v3）

流程：功能清单加载 → 三层匹配 → AI 提取三元组 → 去重 → 原文核对 → 逆向覆盖 → 置信度评分 → 门禁

★ MySQL 是唯一结构化事实源。
★ 功能清单是确定性的（来自 Step1 DOM 解析），AI 只负责从小片段提取三元组。
★ 零 AI 大纲识别，零单次全文兜底。
★ source_quote 强制锚定（5a 子串匹配）。
"""

import json as _json
from src.core.guarded import guard
import logging
import hashlib
from typing import Any

from src.core.pipeline_config import pipeline_config

_log = logging.getLogger(__name__)

# ── 命名常量 ──
_CHANGED_PAGE_THRESHOLD = pipeline_config.step2_changed_page_threshold
_QUICK_MODE_MAX_CHARS = 3000
_ERR_MSG_TRUNCATE_LEN = 80
# ★ Step2 进度阶段总数：feature_driven 内部 7 阶段 + 后续 5 阶段（编号+敏感→置信度→门禁→写库→追溯）
_STEP2_TOTAL_STAGES = 12

# ── 从拆分子模块 re-import 哈希工具 ──
from src.pipeline.step2_hashes import (
    compute_extraction_logic_fingerprint,
    compute_requirement_semantic_hash,
)

# ── 从拆分子模块 re-import 上下文恢复 ──
from src.pipeline.step1_context_loader import load_step1_contexts

# ── 从拆分子模块 re-import 版本管理工具 ──
from src.pipeline.step2_versioning import (
    _reset_downstream_for_requirement_change,
    _prepare_requirement_changes,
    _mark_removed_requirements,
    _detect_deletion_signals,
)

# ── 从拆分子模块 re-import 辅助函数（§4.3 单文件≤800行）──
from src.pipeline.step2_helpers import (
    _compute_upload_docs_hash,
    _detect_sensitive_data,
    _mark_sensitive_data,
    _query_multi_source_stats,
    index_requirements_to_rag,
)


def step2_breakdown(
    self, quick: bool = False, force_step2: bool = False, run_id: str = None
) -> dict[str, Any]:
    """
    AI 拆解需求 → 写入 MySQL requirements 表。

    ★ v3 功能清单驱动拆解（零 AI 大纲识别，确定性功能匹配）：
      1. 功能清单加载 — 从 system_feature_list 读取目标系统功能（零AI）
      2. 三层匹配 — 关键词 → 嵌入 → 标记未文档化
      3. AI 提取三元组 — 每个匹配功能+原文片段 → AI 提取 (operation, condition, expected_feedback)
      4. 去重合并 — 相同 (operation, expected_feedback) 合并 condition
      5. 原文核对 — source_quote 子串匹配（5a 零AI）
      6. 逆向覆盖 — rag_chunks 功能索引 vs 需求覆盖
      失败时自动复用历史需求兜底

    ★ force_step2=True: 忽略哈希检查，强制重新拆解；所有 unchanged 需求改为 modified。
    """
    if run_id:
        _log.info("  [run_id=%s] Step 2 开始: AI 需求拆解", run_id)

    # ★ 插件总线：发布 STEP2_START 事件（供 Step2AnalyzerPlugin 等订阅）
    with guard("step2_breakdown: from src.pipeline.plugins.pipeline_b", log=_log):
        from src.pipeline.plugins.pipeline_bus import pipeline_bus, PipelineEvent

        pipeline_bus.publish(
            PipelineEvent.STEP2_START,
            {
                "project": getattr(self, "_project_name", "") or "",
                "run_id": run_id,
                "quick": quick,
            },
        )

    # ★ 跨Step MySQL通信：从 source_documents 表读取 Step1 上下文（禁止内存变量跨Step）
    from src.pipeline.step1_context_loader import load_step1_contexts

    _step1_ctx = load_step1_contexts(self._project_name)
    _log.info("[DEBUG-step2] load_step1_contexts 完成")

    # ★ B-8-2: 数据完整性校验
    from src.pipeline.utils.data_integrity_checker import validate_step_input

    _raw_reqs = _step1_ctx.get("raw_requirements") or []
    _integrity = validate_step_input(
        "source_documents(raw_requirements)",
        _raw_reqs,
        required_fields=["content", "module"],
    )
    if not _integrity["valid"]:
        _log.error(
            "[Step2] Step1 产出数据完整性校验失败: %d/%d records 有问题",
            _integrity["failed"],
            _integrity["total"],
        )

    from src.pipeline.steps.step1.mcp.confluence_mcp import get_confluence_client
    from src.core.excel_handler import excel_handler
    from src.core.config_loader import config

    _log.info("[DEBUG-step2] 模块导入完成，开始读取 Confluence 缓存...")

    # ★ 哈希驱动：按页对比（MySQL source_confluence_hashes / source_breakdown_hashes 为唯一事实源）
    from src.pipeline.utils.source_hashes_store import (
        source_hashes_store as _step2_hash,
    )

    _version_label = getattr(config, "project_version", "") or ""
    _step2_cf = _step2_hash.load_confluence_hash(version_label=_version_label)
    current_page_hashes = {}
    current_cf_hash = (
        _step2_cf.get("overall", "")
        if isinstance(_step2_cf, dict) and _step2_cf
        else ""
    )
    if isinstance(_step2_cf, dict) and isinstance(_step2_cf.get("page_hashes"), dict):
        current_page_hashes = dict(_step2_cf["page_hashes"])
    # ★ MySQL-only：不再有磁盘 JSON；如需从旧 JSON 迁移，请显式调用
    #   source_hashes_store.import_confluence_from_json(path)

    # ★ 上传文档增量感知：计算上传文档组合哈希，纳入变更检测（按版本分区）
    _current_upload_hash = _compute_upload_docs_hash(
        getattr(config, "project_name", "") or "", _version_label
    )

    # ★ 版本感知：断点哈希按版本隔离，切换版本后自动触发重拆（MySQL source_breakdown_hashes）
    _ver_label = config.project_version
    last_page_hashes = {}
    last_cf_hash = ""
    # ★ MySQL 主路径：source_breakdown_hashes（替代磁盘 JSON 兜底）
    _bd = _step2_hash.load_breakdown_hash(version_label=_ver_label or "")
    if isinstance(_bd, dict):
        last_cf_hash = str(
            _bd.get("confluence_hash", "") or _bd.get("overall", "") or ""
        )
        if not last_cf_hash and isinstance(_bd.get("meta"), dict):
            last_cf_hash = str(_bd["meta"].get("confluence_hash", "") or "")
        last_semantic_hash = (
            str(_bd.get("meta", {}).get("semantic_hash", "") or "")
            if isinstance(_bd.get("meta"), dict)
            else ""
        )
        last_upload_hash = (
            str(_bd.get("meta", {}).get("upload_hash", "") or "")
            if isinstance(_bd.get("meta"), dict)
            else ""
        )
        if isinstance(_bd.get("page_hashes"), dict):
            last_page_hashes = {str(k): str(v) for k, v in _bd["page_hashes"].items()}
    else:
        last_semantic_hash = ""
        last_upload_hash = ""
    _downstream_reset_needed = False
    # ★ MySQL-only：不再有磁盘 JSON；如需从旧 JSON 迁移，请显式调用
    #   source_hashes_store.import_breakdown_from_json(path)

    # ★ MySQL-only：从 requirements 表读取，不再依赖 Excel 作为事实源
    from src.pipeline.utils.requirement_store import requirement_store

    try:
        existing = requirement_store.list_requirements()
    except Exception as e:
        # ★ B-3-4: 区分数据库连接问题(阻断)和其他异常(降级为空列表)
        _err_str = str(e).lower()
        _is_conn_err = any(
            kw in _err_str
            for kw in (
                "connect",
                "connection",
                "timeout",
                "refused",
                "unreachable",
                "can't connect",
                "lost connection",
                "gone away",
            )
        )
        if _is_conn_err:
            _log.error("[Step2] 读取已有需求列表失败(数据库连接): %s", e)
            return {"success": False, "error": "数据库连接失败: %s" % str(e)[:200]}
        _log.warning("[Step2] 读取已有需求列表异常(降级为空): %s", e)
        existing = []
    changed_page_ids = set()
    if existing and len(existing) > 0:
        # ★ 主路径：last_cf_hash / last_page_hashes 已经从 MySQL 或显式导入填充完毕
        # 不再读取磁盘 JSON 作为兜底
        # 完全未变更 → 跳过（★ 同时检查 Confluence 哈希 + 上传文档哈希）
        _source_unchanged = (
            last_cf_hash
            and current_cf_hash
            and last_cf_hash == current_cf_hash
            and last_upload_hash == _current_upload_hash
        )
        if _source_unchanged and not force_step2:
            _log.info(
                "已有 %d 条需求且源数据未变更 (哈希=%s)，跳过拆解",
                len(existing),
                current_cf_hash[:8],
            )
            return {"success": True, "count": len(existing), "skipped": True}
        if force_step2 and _source_unchanged:
            _log.info(
                "强制重拆解模式：源数据哈希未变更，但用户要求强制重拆解，跳过缓存"
            )
        _downstream_reset_needed = True
        # ★ 按页对比：找出具体哪些页变更了
        if last_page_hashes and current_page_hashes:
            all_page_ids = set(
                list(last_page_hashes.keys()) + list(current_page_hashes.keys())
            )
            for pid in all_page_ids:
                if last_page_hashes.get(pid) != current_page_hashes.get(pid):
                    changed_page_ids.add(pid)
            # 如果超过一半的页变更了，不如全量重来
            if len(changed_page_ids) > len(all_page_ids) * _CHANGED_PAGE_THRESHOLD:
                changed_page_ids = set()  # 清空 → 走全量
            if changed_page_ids:
                _log.info(
                    "部分页面变更: %d/%d 页 → 增量更新",
                    len(changed_page_ids),
                    len(all_page_ids),
                )
            else:
                _log.info(
                    "源数据已变更 (旧=%s 新=%s)，全量重新拆解",
                    last_cf_hash[:8] if last_cf_hash else "无",
                    current_cf_hash[:8] if current_cf_hash else "无",
                )
        else:
            _log.info(
                "源数据已变更 (旧=%s 新=%s)，重新拆解",
                last_cf_hash[:8] if last_cf_hash else "无",
                current_cf_hash[:8] if current_cf_hash else "无",
            )

    # ★ MySQL 化：优先从 source_documents(doc_type='confluence') 读取 Step1 产出
    raw_text = ""
    cf = {"success": False, "data": {"text": "", "pages": [], "titles": []}}
    cf_from_cache = False
    _try_project = getattr(config, "project_name", "") or ""
    # 单一事实源：canonical 需求行（Step1 已合并上传需求）——读取/剥头逻辑归 helpers
    from src.pipeline.helpers import (
        load_canonical_requirement_text as _load_canonical_req_text,
    )

    raw_text = _load_canonical_req_text(_try_project, _version_label)
    if raw_text:
        cf_from_cache = True
        cf["success"] = True
        cf["data"]["text"] = raw_text
        _log.info(
            "Confluence: 复用 Step1 MySQL 缓存 (%d 字符, version=%s)",
            len(raw_text),
            _version_label or "latest",
        )

    # ★ MySQL 已在上方读取（含无版本兜底 ORDER BY updated_at DESC），不再有磁盘兜底
    if not cf_from_cache and _version_label:
        _log.warning(
            "Confluence: 当前版本未命中 MySQL 缓存(version=%s)", _version_label
        )

    # ★ 上传需求不再单独拼接：canonical 行（Step1 写入）已合并全部
    #   category='requirement' 的上传文档，再叠加一次会让同一份正文被拆解两遍。

    if not cf_from_cache:
        cf_client = get_confluence_client()
        cf = cf_client.fetch_confluence_all()
        if cf["success"]:
            raw_text = cf["data"]["text"]
            _log.info("Confluence 原始文本长度: %d 字符", len(raw_text))
        else:
            _cf_url = getattr(config, "confluence_url", "") or ""
            _cf_configured = bool(
                _cf_url
                or getattr(config, "confluence_page_ids", [])
                or getattr(config, "confluence_base_url", "")
            )
            if _cf_configured:
                _log.warning("Confluence 拉取失败: %s", cf.get("error", "?"))
            else:
                _log.info("Confluence 未配置，跳过")

    # ★ 通用兜底：canonical 需求正文为空时，使用 YApi 接口文档作为需求输入
    if not raw_text:
        _yapi_conn = None
        try:
            from src.core.db_manager import get_connection

            _yapi_conn = get_connection()
            _yapi_rows = _yapi_conn.execute(
                "SELECT content FROM source_documents "
                "WHERE project=%s AND doc_type='yapi' "
                "ORDER BY updated_at DESC LIMIT 1",
                (self._project_name,),
            ).fetchall()
            if _yapi_rows:
                raw_text = _yapi_rows[0]["content"]
                _log.info(
                    "Confluence 和上传文档均无数据，使用 YApi 接口文档作为需求输入: %d 字符",
                    len(raw_text),
                )
        except Exception as e:
            _log.warning("[Step2] YApi 兜底读取失败: %s", e)
        finally:
            if _yapi_conn:
                try:
                    _yapi_conn.close()
                except Exception as _close_e:
                    _log.debug("连接关闭异常: %s", _close_e)

    # ★ DOM 爬取数据兜底：当 Confluence / 上传文档 / YApi 均无数据时，
    #   从导航库（nav_graph）读取页面摘要作为需求输入
    # ★ P0-1 读侧迁移(2026-08-05): page_dom_snapshots → nav_graph（read_dom_pages）。
    #   该表 0 行且全仓无写入方，兜底路径此前恒为空 → Step2 在无文档时直接失败。
    if not raw_text:
        try:
            from src.pipeline.utils.nav_graph_read import read_dom_pages

            _dom_rows = sorted(
                read_dom_pages(_try_project, _version_label or None),
                key=lambda r: str(r.get("page_path", "") or ""),
            )
            if _dom_rows:
                _dom_parts = []
                _dom_parts.append("## DOM 爬取页面摘要（共 %d 页）" % len(_dom_rows))
                _dom_parts.append("")
                for _dr in _dom_rows:
                    _title = _dr.get("page_title", "") or _dr.get("page_path", "")
                    _url = _dr.get("page_url", "")
                    _summary = _dr.get("dom_summary", "") or ""
                    _ec = _dr.get("element_count", 0)
                    _dom_parts.append("### %s" % _title)
                    if _url:
                        _dom_parts.append("URL: %s" % _url)
                    _dom_parts.append("元素数量: %d" % _ec)
                    _dom_parts.append("")
                    if _summary.strip():
                        _dom_parts.append(_summary.strip())
                    _dom_parts.append("")
                raw_text = "\n".join(_dom_parts)
                _log.info(
                    "DOM 爬取数据兜底: %d 页, %d 字符", len(_dom_rows), len(raw_text)
                )
            else:
                _log.info(
                    "DOM 爬取数据为空 (project=%s, version=%s)",
                    _try_project,
                    _version_label,
                )
        except Exception as _de:
            _log.error(
                "[Step2] DOM 爬取数据兜底读取失败 project=%s version=%s: %s",
                _try_project,
                _version_label,
                _de,
                exc_info=True,
            )

    if not raw_text:
        if existing and len(existing) > 0:
            _log.warning("无数据源可用，但已有 %d 条历史需求；复用", len(existing))
            return {
                "success": True,
                "count": len(existing),
                "skipped": True,
                "reused_existing_requirements": True,
                "warning": "source_unavailable_reused_existing",
            }
        _log.error("无法拉取 Confluence 数据，且无上传文档/历史需求")
        return {"success": False, "error": "无法拉取 Confluence 数据，且无上传文档可用"}

    current_semantic_hash = compute_requirement_semantic_hash(raw_text)
    # ★ 提取逻辑指纹：正文未变但**产出需求的逻辑变过**时，必须重拆（否则复用陈旧派生值）。
    _current_logic_fp = compute_extraction_logic_fingerprint()
    _last_logic_fp = (
        str(_bd.get("meta", {}).get("extraction_logic_fp", "") or "")
        if isinstance(_bd, dict) and isinstance(_bd.get("meta"), dict)
        else ""
    )
    _logic_changed = bool(_last_logic_fp) and _last_logic_fp != _current_logic_fp
    if (
        existing
        and last_semantic_hash
        and current_semantic_hash == last_semantic_hash
        and not _logic_changed
        and not force_step2
    ):
        _log.info(
            "已有 %d 条需求且语义哈希未变更，仅检测到格式/排版变化，跳过拆解",
            len(existing),
        )
        _conn_skip = None
        try:
            from src.core.db_manager import get_connection

            _conn_skip = get_connection()
            _ok_skip = _step2_hash.save_breakdown_hash(
                current_cf_hash,
                page_hashes=current_page_hashes,
                meta={
                    "confluence_hash": current_cf_hash,
                    "upload_hash": _current_upload_hash,
                    "semantic_hash": current_semantic_hash,
                    "extraction_logic_fp": _current_logic_fp,
                    "req_count": len(existing),
                    "skip_reason": "semantic_hash_unchanged",
                },
                version_label=_ver_label or "",
                conn=_conn_skip,
            )
            if not _ok_skip:
                _log.error(
                    "[Step2] 语义 skip breakdown 哈希写入失败（详见上游错误日志）"
                )
        except Exception as e:
            _log.warning("保存语义 skip breakdown 哈希失败: %s", e)
        finally:
            if _conn_skip:
                try:
                    _conn_skip.close()
                except Exception as e:
                    _log.debug("[step2] 关闭 _conn_skip 失败: %s", e)
        return {
            "success": True,
            "count": len(existing),
            "skipped": True,
            "semantic_skipped": True,
        }

    if quick:
        raw_text = raw_text[:_QUICK_MODE_MAX_CHARS]
        _log.info("快速模式: 截取前 %d 字符", _QUICK_MODE_MAX_CHARS)

    ui_ctx = _step1_ctx["ui_context"]
    if ui_ctx:
        _log.info("UI 上下文: 已注入 Figma 设计稿结构 (%d 字符)", len(ui_ctx))

    # ═══════════════════════════════════════════
    # ★ v3 功能清单驱动拆解（零 AI 大纲识别，确定性功能匹配）
    # ═══════════════════════════════════════════
    from src.pipeline.steps.step2.analyzers.feature_driven import (
        feature_driven_breakdown,
    )

    _project_name = getattr(config, "project_name", "") or ""
    _ver_label = getattr(config, "project_version", "") or ""

    phase_result = feature_driven_breakdown(
        raw_text=raw_text,
        ui_context=ui_ctx,
        project_name=_project_name,
        version_label=_ver_label,
        progress_callback=lambda done, total: (
            self._progress_callback(2, "AI 需求拆解", done, _STEP2_TOTAL_STAGES, 1, 5)
            if self._progress_callback
            else None
        ),
    )

    if not phase_result.get("success"):
        _log.error("AI 需求拆解失败: %s", phase_result.get("error", "?")[:80])
        # 兜底：如有历史需求，复用
        if existing and len(existing) > 0:
            _log.warning(
                "无法拆解，但已有 %d 条历史需求；复用既有需求继续", len(existing)
            )
            return {
                "success": True,
                "count": len(existing),
                "skipped": True,
                "reused_existing_requirements": True,
                "warning": "breakdown_failed_reused_existing",
            }
        return {"success": False, "error": "AI 需求拆解失败"}

    all_reqs = phase_result.get("data", [])
    if not isinstance(all_reqs, list) or len(all_reqs) == 0:
        _log.error("AI 未返回有效需求")
        return {"success": False, "error": "AI 未返回有效需求"}

    # ★ S2-017: 收集本次拆解的运行 stats（用于写入 ai_health_metrics）
    _breakdown_stats = phase_result.get("_stats") or {}

    _log.info("[Step2] 功能清单驱动拆解完成: %d 条需求", len(all_reqs))

    # ★ 进度上报：feature_driven 内部 7 阶段完成，后续 5 阶段（编号+敏感→置信度→门禁→写库→追溯）
    _step2_report = lambda d: (
        self._progress_callback(2, "AI 需求拆解", d, _STEP2_TOTAL_STAGES, 1, 5)
        if self._progress_callback
        else None
    )

    # ═══════════════════════════════════════════
    # 重新编号
    # ═══════════════════════════════════════════
    for i, r in enumerate(all_reqs):
        r["req_id"] = "REQ-%03d" % (i + 1)

    # ═══════════════════════════════════════════
    # ★ S2-018: 敏感数据检测 — 标记 contains_sensitive_data
    # 扫描身份证号/手机号/邮箱/IP，命中则标记 True，写入 requirements 表
    # ═══════════════════════════════════════════
    try:
        _sensitive_hits = _mark_sensitive_data(all_reqs)
        if _sensitive_hits > 0:
            _log.info(
                "[Step2] 敏感数据检测: %d/%d 条需求命中敏感模式（已标记 contains_sensitive_data）",
                _sensitive_hits,
                len(all_reqs),
            )
    except Exception as _se:
        _log.warning("[Step2] 敏感数据检测异常(不阻塞): %s", _se)

    _step2_report(8)  # 阶段8: 重新编号 + 敏感数据检测完成

    # ═══════════════════════════════════════════
    # ★ P0-3 3a: 三车道路由（替 conf>=80→approved 二值自动审核）
    #   修复前: conf>=80 → approved（63条全approved，实际_evidence_confidence仅0.65-0.70）
    #   修复后: green(≥85)→approved / yellow([60,85))→pending_review / red(<60)→blocked
    #   通用性: 阈值与 confidence_scorer.py 的 HIGH/MEDIUM 阈值一致，跨系统通用
    # ═══════════════════════════════════════════
    try:
        from src.pipeline.utils.confidence_scorer import (
            score_requirement,
            get_avg_confidence,
        )
        from src.pipeline.utils.review_lane_store import classify_lane

        _lane_counts = {"green": 0, "yellow": 0, "red": 0}
        # ★ S2-012: 收集置信度分数用于写入 ai_health_metrics
        _conf_scores = []
        for r in all_reqs:
            conf = score_requirement(r)
            conf_score = get_avg_confidence(conf)
            _conf_scores.append(conf_score)
            # 车道判定唯一实现（review_lane_store.classify_lane），此处只做
            # 需求级 review_status 的映射，不再自行推导 lane/reason。
            _lane, _lane_reason = classify_lane(r)
            if _lane == "green":
                r["review_status"] = "approved"
            elif _lane == "yellow":
                r["review_status"] = "pending_review"
            else:
                r["review_status"] = "blocked"
            r["_lane"] = _lane
            r["_lane_reason"] = _lane_reason
            _lane_counts[_lane] += 1
        _log.info(
            "[审核] Step2 三车道路由: green=%d yellow=%d red=%d (共%d条)",
            _lane_counts["green"],
            _lane_counts["yellow"],
            _lane_counts["red"],
            len(all_reqs),
        )

        # ★ S2-012: 置信度分布写入 ai_health_metrics 表（metric_type='step2_confidence'）
        try:
            from src.backend.state import _thread_run_id
            from src.extensions.metrics_collector import metrics_collector

            _mc_run_id = run_id or getattr(_thread_run_id, "value", "") or ""
            if _mc_run_id and _conf_scores:
                metrics_collector.record_step2_confidence(
                    project=getattr(config, "project_name", "") or "",
                    run_id=_mc_run_id,
                    scores=_conf_scores,
                )
                _log.info(
                    "[Step2] 置信度分布已写入 ai_health_metrics: count=%d, mean=%.2f",
                    len(_conf_scores),
                    sum(_conf_scores) / len(_conf_scores) if _conf_scores else 0.0,
                )
        except Exception as _mc_e:
            _log.warning(
                "[Step2] 置信度分布写入 ai_health_metrics 失败(不阻塞): %s", _mc_e
            )
    except Exception as _e:
        _log.debug("[审核] 自动审核跳过: %s", str(_e)[:80])

    _step2_report(9)  # 阶段9: 置信度评分完成

    # ══════════════════════════════════════════════════════════
    # ★ 入库前字段完整性门禁（最后一道防线）
    # 过滤掉 module/description 为空的需求，防止空字段入库
    # ══════════════════════════════════════════════════════════
    _pre_write_count = len(all_reqs)
    _filtered_reqs = []
    _removed_ids = []
    for _r in all_reqs:
        _mod = (_r.get("module") or "").strip()
        _desc = (_r.get("description") or "").strip()
        if not _mod or len(_mod) < 2 or not _desc or len(_desc) < 10:
            _removed_ids.append(_r.get("req_id", "?"))
            continue
        _filtered_reqs.append(_r)
    if _removed_ids:
        _log.warning(
            "[Step2] 入库门禁: 过滤 %d 条空字段需求: %s",
            len(_removed_ids),
            _removed_ids[:10],
        )
        all_reqs = _filtered_reqs
        # 重新编号
        for i, r in enumerate(all_reqs):
            r["req_id"] = "REQ-%03d" % (i + 1)
    if not all_reqs:
        _log.error("[Step2] 入库门禁: 所有需求均未通过字段完整性校验，拒绝入库")
        return {
            "success": False,
            "error": "所有需求字段不完整（module/description为空），拒绝入库",
        }
    _log.info(
        "[Step2] 入库门禁通过: %d 条需求 (过滤 %d 条)",
        len(all_reqs),
        _pre_write_count - len(all_reqs),
    )

    _step2_report(10)  # 阶段10: 入库门禁完成

    # 写入 MySQL + 版本管理
    # ═══════════════════════════════════════════
    _try_project = getattr(config, "project_name", "") or ""
    version_change_ctx = _prepare_requirement_changes(
        _try_project, all_reqs, force_step2=force_step2
    )
    if _downstream_reset_needed and not version_change_ctx.get("versioned"):
        _reset_downstream_for_requirement_change(
            _try_project, "step2_feature_driven_source_changed"
        )
    # ★ P0-3: 写入前清理当前版本的旧需求，避免 Step2 重跑时新旧并存导致计数混淆
    #   upsert 只覆盖同 req_id 的旧需求；若本次生成数量 < 上次，残留的旧需求会污染统计
    # ★ v3.5 增量保留：unchanged 需求不删除，避免 AI 不稳定导致旧需求丢失
    try:
        from src.pipeline.utils.requirement_store import requirement_store

        _active_vid = int(version_change_ctx.get("active_version_id") or 0)
        if _try_project and _active_vid:
            _unchanged_ids = [
                str(r.get("req_id", ""))
                for r in all_reqs
                if isinstance(r, dict) and r.get("change_type") == "unchanged"
            ]
            _unchanged_ids = [rid for rid in _unchanged_ids if rid]
            if _unchanged_ids:
                _log.info(
                    "[Step2] 增量保留: %d 条 unchanged 需求不删除", len(_unchanged_ids)
                )
                requirement_store.clear_version_requirements_except(
                    _try_project, _active_vid, _unchanged_ids
                )
            else:
                requirement_store.clear_version_requirements(_try_project, _active_vid)
    except Exception as _e:
        _log.warning("[Step2] 清理旧需求失败（不阻断）: %s", str(_e)[:80])
    count = excel_handler.write_requirements(all_reqs)

    # ★ P0-3 3a: 批量写入 review_lane 表（三车道路由持久化）
    #   需求级路由：case_id=req_id（需求是用例的前身，Step3 生成用例时继承 lane）
    try:
        from src.pipeline.utils.review_lane_store import write_review_lanes

        _rl_vid = int(version_change_ctx.get("active_version_id") or 0)
        _rl_proj = _try_project or getattr(config, "project_name", "") or ""
        if _rl_proj and _rl_vid:
            _rl_items = [
                {
                    "case_id": str(r.get("req_id", "") or ""),
                    "lane": r.get("_lane", "yellow"),
                    "reason": r.get("_lane_reason", ""),
                }
                for r in all_reqs
                if r.get("req_id")
            ]
            write_review_lanes(_rl_proj, _rl_vid, _rl_items)
    except Exception as _rl_e:
        _log.warning("[Step2] review_lane 写入失败(不阻断): %s", str(_rl_e)[:120])

    # ★ P2-11 接入: 需求索引到 multimodal_chunks（提取到 step2_helpers.index_requirements_to_rag）
    index_requirements_to_rag(_try_project or "", all_reqs)

    _mark_removed_requirements(
        _try_project,
        int(version_change_ctx.get("active_version_id") or 0),
        version_change_ctx.get("removed_ids") or [],
        version_change_ctx.get("removed_stable_keys") or [],
    )
    # ★ 删除信号识别
    _detect_deletion_signals(_try_project, all_reqs)

    # MySQL 原子写入 source_hashes
    _conn_h = None
    try:
        from src.core.db_manager import get_connection

        _conn_h = get_connection()
        _ver_label = getattr(config, "project_version", "") or ""
        _ok_bd = _step2_hash.save_breakdown_hash(
            current_cf_hash,
            page_hashes=current_page_hashes,
            meta={
                "confluence_hash": current_cf_hash,
                "upload_hash": _current_upload_hash,
                "semantic_hash": current_semantic_hash,
                "extraction_logic_fp": compute_extraction_logic_fingerprint(),
                "req_count": count,
            },
            version_label=_ver_label or "",
            conn=_conn_h,
        )
        if not _ok_bd:
            _log.error("[Step2] breakdown 细表哈希写入失败（详见上游错误日志）")
        _meta_str = _json.dumps(
            {
                "confluence_hash": current_cf_hash,
                "page_hashes": current_page_hashes,
                "req_count": count,
            },
            ensure_ascii=False,
        )
        _content_hash = hashlib.md5(_meta_str.encode()).hexdigest()
        _conn_h.execute(
            "INSERT INTO source_hashes "
            "(project, doc_type, version_label, content_hash, created_at, updated_at) "
            "VALUES (%s, 'breakdown', %s, %s, NOW(), NOW()) "
            "ON DUPLICATE KEY UPDATE content_hash = VALUES(content_hash), updated_at = NOW()",
            (_try_project, _ver_label or "", _content_hash),
        )
        _conn_h.commit()
        _log.info("[Step2] breakdown 哈希原子写入完成 (精简模式)")
    except Exception as _e:
        _log.warning("[Step2] breakdown hash 写入失败: %s", _e)
        if _conn_h:
            try:
                _conn_h.rollback()
            except (OSError, ConnectionError) as _rb_err:
                _log.warning("[Step2] rollback 失败: %s", _rb_err)
    finally:
        if _conn_h:
            try:
                _conn_h.close()
            except (OSError, ConnectionError) as _close_err:
                _log.warning("[Step2] conn.close 失败: %s", _close_err)
    _log.info("拆解完成: %d 条需求 (精简模式)", count)

    _step2_report(11)  # 阶段11: 写入MySQL完成

    # ═══════════════════════════════════════════
    # 追溯链路 + 快照
    # ═══════════════════════════════════════════
    if not quick:
        try:
            self._build_traceability_and_diff(all_reqs)
        except Exception as e:
            _log.warning("追溯链路/对比构建失败: %s", str(e)[:_ERR_MSG_TRUNCATE_LEN])

    _step2_report(_STEP2_TOTAL_STAGES)  # 阶段12: 全部完成

    # ═══════════════════════════════════════════
    # ★ v3 置信度评分（_verify_status 已在 feature_driven 内部设置）
    # ═══════════════════════════════════════════
    from src.pipeline.steps.step2.analyzers.confidence_scorer import (
        compute_confidence_scores,
    )

    # ★ 校准数据（冷启动判断）
    cal_data = None
    try:
        from src.core.config_loader import config as _step2_cfg
        from src.pipeline.evidence.calibration import get_calibration as _get_cal

        cal_data = _get_cal(getattr(_step2_cfg, "project_name", "") or "")
    except (ImportError, OSError, ValueError, KeyError) as _cal_err:
        _log.warning("[Step2] 校准数据加载失败(降级为None): %s", _cal_err)
    compute_confidence_scores(all_reqs, raw_text, set(), calibration_data=cal_data)

    # ★ 置信度评分结果回写 MySQL（修复：L799 已先写入需求，评分在此之后仅留内存
    #   → requirements._evidence_confidence 恒为 0.0，下游 Step3/Step5 门禁读到的全是空值）。
    #   此处补"评分→落库"最后一环：按 req_id 定点 UPDATE 置信度三列，不动 project/其他列
    #   （避免触发 upsert_requirements 中 config.project_name='' 的空串污染）。
    try:
        from src.core.db_manager import get_connection

        _conn = get_connection()
        _n_written = 0
        for _r in all_reqs:
            _rid = str(_r.get("req_id", "") or "").strip()
            if not _rid:
                continue
            # ★ requirements 表仅持久化 _evidence_confidence 单列（无 _confidence_level/_evidence_verdict 列）
            _conn.execute(
                "UPDATE requirements SET _evidence_confidence=%s, updated_at=NOW() WHERE req_id=%s",
                (float(_r.get("_evidence_confidence", 0) or 0), _rid),
            )
            _n_written += 1
        _conn.commit()
        _log.info("[Step2] 置信度评分结果已回写 %d 条需求", _n_written)
    except Exception as _ce:
        _log.error("[Step2] 置信度回写失败(不阻断): %s", _ce, exc_info=True)

    # ═══════════════════════════════════════════
    # ★ S2-017: 写入 ai_health_metrics（v3 功能清单驱动指标）
    # ═══════════════════════════════════════════
    try:
        from src.backend.state import _thread_run_id
        from src.extensions.metrics_collector import metrics_collector

        _mc_run_id = run_id or getattr(_thread_run_id, "value", "") or ""
        if _mc_run_id:
            # ★ #14 KPI 三方对齐：从 system_feature_list 查询多源功能统计
            _multi_source_stats = _query_multi_source_stats(
                getattr(config, "project_name", "") or "",
                _version_label or "",
            )
            metrics_collector.record_step2_breakdown_stats(
                project=getattr(config, "project_name", "") or "",
                run_id=_mc_run_id,
                feature_count=int(_breakdown_stats.get("feature_count", 0)),
                matched_count=int(_breakdown_stats.get("matched_count", 0)),
                undocumented_count=int(_breakdown_stats.get("undocumented_count", 0)),
                ai_calls=int(_breakdown_stats.get("ai_calls", 0)),
                rule_count=int(_breakdown_stats.get("rule_extractions", 0)),
                total_reqs=len(all_reqs),
                match_stats=_breakdown_stats.get("match_stats", {}),
                verify_stats=_breakdown_stats.get("verify_stats", {}),
                coverage_stats=_breakdown_stats.get("coverage_stats", {}),
                # ★ #14 三级信任分布（#9/#11）
                trust_l1_explicit=int(_breakdown_stats.get("trust_l1_explicit", 0)),
                trust_l2_implicit_common=int(
                    _breakdown_stats.get("trust_l2_implicit_common", 0)
                ),
                trust_l3_implicit_business=int(
                    _breakdown_stats.get("trust_l3_implicit_business", 0)
                ),
                # ★ #14 模式库补全统计（#10/#11）
                implicit_stats=_breakdown_stats.get("implicit_stats", {}),
                # ★ #14 多源功能提取统计（#6/#7/#8）
                multi_source_stats=_multi_source_stats,
            )
            _log.info(
                "[Step2] ai_health_metrics 已写入 (features=%d, matched=%d, undocumented=%d, ai_calls=%d, reqs=%d, L1=%d, L2=%d, L3=%d)",
                int(_breakdown_stats.get("feature_count", 0)),
                int(_breakdown_stats.get("matched_count", 0)),
                int(_breakdown_stats.get("undocumented_count", 0)),
                int(_breakdown_stats.get("ai_calls", 0)),
                len(all_reqs),
                int(_breakdown_stats.get("trust_l1_explicit", 0)),
                int(_breakdown_stats.get("trust_l2_implicit_common", 0)),
                int(_breakdown_stats.get("trust_l3_implicit_business", 0)),
            )
            # ★ v4.0 (2026-07-12): 双向覆盖指标（优先使用 feature_driven 内实时计算结果）
            _bidirectional = _breakdown_stats.get("bidirectional_coverage", {})
            if _bidirectional:
                _forward_rate = float(_bidirectional.get("forward_rate", 0) or 0)
                _uncovered = int(_bidirectional.get("uncovered_sections", 0) or 0)
                _orphan = int(_bidirectional.get("orphan_reqs", 0) or 0)
            else:
                # 兜底：无双向结果时使用切片覆盖率
                _forward_rate = float(_breakdown_stats.get("coverage_rate", 0) or 0)
                _uncovered = int(_breakdown_stats.get("uncovered_chunks", 0) or 0)
                _orphan = 0
            if _forward_rate > 0 or _uncovered > 0 or _orphan > 0:
                metrics_collector.record_coverage(
                    project=getattr(config, "project_name", "") or "",
                    run_id=_mc_run_id,
                    forward_rate=_forward_rate,
                    orphan_modules=_orphan,
                    uncovered_sections=_uncovered,
                )
    except Exception as _mc_e:
        _log.warning("[Step2] ai_health_metrics 写入失败(不阻塞): %s", _mc_e)

    if run_id:
        _log.info("  [run_id=%s] Step 2 完成: %d 条需求", run_id, count)

    # ★ 插件总线：发布 STEP2_COMPLETE 事件（供 Step2AnalyzerPlugin 等订阅）
    try:
        from src.pipeline.plugins.pipeline_bus import pipeline_bus, PipelineEvent

        pipeline_bus.publish(
            PipelineEvent.STEP2_COMPLETE,
            {
                "project": getattr(self, "_project_name", "") or "",
                "run_id": run_id,
                "requirement_count": count,
                "feature_count": int(_breakdown_stats.get("feature_count", 0)),
                "matched_count": int(_breakdown_stats.get("matched_count", 0)),
                "undocumented_count": int(
                    _breakdown_stats.get("undocumented_count", 0)
                ),
            },
        )
    except Exception as _bus_e:
        _log.debug("[Step2] pipeline_bus STEP2_COMPLETE 异常(不阻塞): %s", _bus_e)

    # ★ 需求审核闸门
    return self._apply_review_gate(count, "feature_driven")
