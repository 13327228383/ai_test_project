# -*- coding: utf-8 -*-
"""
Step 1 — 需求与上下文拉取
=========================

职责：
1. 数据源健康检查
2. 配置校验（所有数据源凭据是否齐全）
3. Confluence 需求文档拉取（缓存优先 + MySQL 化）
4. Figma UI 设计稿拉取（智能分层 + 增量模式 + 基线继承）
5. 数据库真实测试账号查询
6. YApi 接口文档拉取（缓存优先 + MySQL 化）
7. 需求稿持久化（source_documents MySQL 事实源）
8. 结果摘要

【2026-06-23 重构】
★ 四数据源并行化：Confluence / Figma / DB / YApi 使用 ThreadPoolExecutor
  同时启动，整体耗时从 Σ(各源) 降为 max(各源)。
★ 导航地图生成（_generate_nav_map）移至 Step 2，由需求拆解后的模块粒度
  驱动，prompt 更聚焦。
★ Confluence 内部多页并行（MCP 协议支持 request-id 路由）。
★ Figma Layer 2：全量保留，按需检索由 Step2/3 的 RAG 完成。
"""

import logging
from src.core.guarded import guard
from concurrent.futures import (
    ThreadPoolExecutor,
    as_completed,
    TimeoutError as FutureTimeoutError,
)

from src.core.config_loader import config
from src.extensions.metrics_collector import metrics_collector  # ★ v4.0 诊断指标

_log = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# Step1DataFetcher 门面类
# ═══════════════════════════════════════════════════════════════

from .fetchers.base import _Step1BaseMixin
from .fetchers.confluence import _Step1ConfluenceMixin
from .fetchers.figma import _Step1FigmaMixin
from .fetchers.database import _Step1DatabaseMixin
from .fetchers.yapi import _Step1YapiMixin
from .fetchers.persist import _Step1PersistMixin


class Step1DataFetcher(
    _Step1BaseMixin,
    _Step1ConfluenceMixin,
    _Step1FigmaMixin,
    _Step1DatabaseMixin,
    _Step1YapiMixin,
    _Step1PersistMixin,
):
    """
    Step1 需求与上下文拉取器（门面类）。

    使用方式：
        from src.pipeline.step1 import step1_fetch_data
        result = step1_fetch_data()
    """

    pass


# ★ 方案C 改造：_step1_fetcher 改为 PerProjectRegistry，按 project 隔离
#   原实现：模块级单例，拉取器内部状态跨项目混合
from src.core.utils.per_project_registry import PerProjectRegistry

_step1_fetcher = PerProjectRegistry(Step1DataFetcher)


# ═══════════════════════════════════════════════════════════════
# 线程上下文恢复工具 + 并行数据源包装器 + 数据清理
# ═══════════════════════════════════════════════════════════════
# ★ 提取到 _step1_helpers.py（单文件 ≤ 800 行约束）
from ._step1_helpers import (
    _capture_thread_ctx,
    _restore_thread_ctx,
    _fetch_confluence_in_thread,
    _fetch_figma_in_thread,
    _fetch_database_in_thread,
    _fetch_yapi_in_thread,
    _clear_step1_data,
)

# ★ 多源导航地图融合构建器（提取到 _nav_map_builder.py）
from ._nav_map_builder import build_nav_map_from_docs as _build_nav_map_from_docs

# ★ 阶段函数（提取到 _step1_phases.py，§4.3 单文件≤800行）
from ._step1_phases import (
    _run_prototype_recognition_phase,
    _build_rag_index_phase,
    check_step1_blocking_failures,
)


# ═══════════════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════════════


def step1_fetch_data(
    figma_force_refresh=False, run_id=None, skip_sources=None, force_overwrite=False
):
    """
    Step 1: 拉取 Confluence 需求 + Figma UI 设计 + YApi 接口 + DB 测试账号。

    ★ 四数据源并行启动（ThreadPoolExecutor），整体耗时 ≈ max(各源耗时)。
    ★ 导航地图生成已移至 Step 2。

    参数:
        figma_force_refresh: 是否强制刷新 Figma（忽略缓存）
        run_id: 当前运行 ID（用于日志标记）
        skip_sources: 需要跳过的数据源列表，如 ["confluence", "yapi"]（用户选择不拉取）
        force_overwrite: 是否强制覆盖（清除所有 Step1 数据后重新拉取，忽略所有缓存）

    返回:
        dict: success / req_text / ui_context / api_docs_context / test_accounts /
              sources_status / version_label；失败时返回 {"success": False, "error": ...}
    """
    skip_sources = skip_sources or []

    # ★ 强制覆盖：清除所有 Step1 数据（source_documents、hashes 等）后再拉取
    if force_overwrite:
        _log.info("=" * 50)
        _log.info("  🔄 强制覆盖模式：清除所有 Step1 缓存数据后重新拉取")
        _log.info("=" * 50)
        _clear_step1_data()
    fetcher = _step1_fetcher

    # ★ 插件总线：发布 STEP1_START 事件（供 Step1DatasourcePlugin 等订阅）
    with guard("step1_fetch_data: from src.pipeline.plugins.pipeline_b", log=_log):
        from src.pipeline.plugins.pipeline_bus import pipeline_bus, PipelineEvent

        pipeline_bus.publish(
            PipelineEvent.STEP1_START,
            {
                "project": getattr(config, "project_name", "") or "",
                "run_id": run_id,
                "datasources": {},
            },
        )

    # ═══════════════ Phase 1: 前置准备 ═══════════════
    # 1. 版本管理
    active_ver, prev_active_version_label, _version_label = fetcher._setup_version()

    # 1.5. _paths 已废除（全走 MySQL，不再注入 ProjectPaths / ensure_dirs）

    # 2. 健康检查
    health = fetcher._check_health()
    if any(
        v.get("status") == "failed"
        for v in (health.values() if isinstance(health, dict) else [])
    ):
        _log.error(
            "数据源健康检查失败: %s",
            ", ".join(
                f"{k}: {v.get('detail', '未知')}"
                for k, v in (health.items() if isinstance(health, dict) else [])
            ),
        )

    # 3. 配置校验
    missing = fetcher._validate_config()
    if missing:
        _log.warning("缺少以下数据源凭据（对应数据源将被跳过）:")
        for m in missing:
            _log.warning("  - %s", m)

    # 4. 初始化状态
    sources_status = fetcher._build_sources_status()

    # ═══════════════ Phase 2: 并行拉取四数据源 ═══════════════
    ctx = _capture_thread_ctx()

    _log.info("  ⏳ 并行拉取数据源: Confluence | Figma | DB | YApi ...")
    _log.info("=" * 50)

    cf_ok = False
    req_text = ""
    cf_result = {}
    cf_changed = True
    cf_from_cache = False
    old_req_text = ""
    image_urls = []  # ★ 2026-07-13: 原型图 URL 列表
    figma_ok = False
    ui_context = ""
    figma_result = {}
    test_accounts = ""
    db_status = {"status": "skipped", "detail": "未启用"}
    yapi_ok = False
    api_docs_context = ""
    yapi_summary_text = ""
    yapi_status = {"status": "skipped", "detail": "未配置"}
    # ★ 用户选择跳过的数据源
    _skip_cf = "confluence" in skip_sources
    _skip_figma = "figma" in skip_sources
    _skip_yapi = "yapi" in skip_sources
    _skip_db = "database" in skip_sources
    if _skip_cf:
        _log.info("  ⊘ Confluence: 用户选择跳过")
        sources_status["confluence"] = {"status": "skipped", "detail": "用户选择跳过"}
    if _skip_figma:
        _log.info("  ⊘ Figma: 用户选择跳过")
        sources_status["figma"] = {"status": "skipped", "detail": "用户选择跳过"}
    if _skip_yapi:
        _log.info("  ⊘ YApi: 用户选择跳过")
        sources_status["yapi"] = {"status": "skipped", "detail": "用户选择跳过"}
    if _skip_db:
        _log.info("  ⊘ 数据库: 用户选择跳过")
        sources_status["database"] = {"status": "skipped", "detail": "用户选择跳过"}

    # ★ 实时进度：计算 Step1 工作总量（并行数据源 + 后处理阶段）
    #   后处理 = 原型图识别 + RAG 构建 + 多源功能提取 + 需求-SUT 匹配
    _step1_total = (
        (0 if _skip_cf else 1)
        + (0 if _skip_figma else 1)
        + (0 if _skip_db else 1)
        + (0 if _skip_yapi else 1)
    )
    _step1_total += 3  # 原型图识别 + RAG 构建 + 多源功能提取
    _step1_done = 0
    # ★ 源名称 → sources_status key 映射
    _SOURCE_KEY_MAP = {
        "Confluence": "confluence",
        "Figma": "figma",
        "DB": "database",
        "YApi": "yapi",
    }

    def _step1_report_progress(
        done: int, total: int, source_name: str = None, source_status: dict = None
    ):
        """实时更新 Step1 进度：内存 _run_progress + DB sources_status。"""
        if run_id:
            with guard("_step1_report_progress: from src.backend.state import _updat", log=_log):
                from src.backend.state import _update_progress

                _update_progress(run_id, 1, "数据提取", done, total, 1, 5)
        # 增量持久化 sources_status（每个源完成时写入，前端轮询可见）
        if source_name and source_status:
            _key = _SOURCE_KEY_MAP.get(source_name, source_name.lower())
            sources_status[_key] = source_status
            fetcher._persist_sources_status(sources_status, _version_label)

    with ThreadPoolExecutor(max_workers=4) as executor:
        # 提交四个独立的数据源拉取任务（用户选择跳过的跳过）
        f_cf = None
        f_figma = None
        f_db = None
        f_yapi = None
        if not _skip_cf:
            f_cf = executor.submit(
                _fetch_confluence_in_thread,
                fetcher,
                active_ver,
                _version_label,
                figma_force_refresh,
                force_overwrite,
                run_id,
                ctx,
            )
        if not _skip_figma:
            f_figma = executor.submit(
                _fetch_figma_in_thread,
                fetcher,
                active_ver,
                _version_label,
                prev_active_version_label,
                figma_force_refresh,
                force_overwrite,
                ctx,
            )
        if not _skip_yapi:
            f_yapi = executor.submit(
                _fetch_yapi_in_thread,
                fetcher,
                _version_label,
                figma_force_refresh,
                force_overwrite,
                ctx,
            )
        if not _skip_db:
            f_db = executor.submit(
                _fetch_database_in_thread, fetcher, _version_label, ctx
            )

        # 收集结果（各源独立 timeout）
        _TIMEOUT_CF = 300
        _TIMEOUT_FIGMA = 60  # ★ 降为 60s，超时后走重试而非傻等
        _TIMEOUT_DB = 60
        _TIMEOUT_YAPI = 300
        _MAX_TIMEOUT = max(_TIMEOUT_CF, _TIMEOUT_FIGMA, _TIMEOUT_DB, _TIMEOUT_YAPI)

        futures = {}
        if f_cf:
            futures[f_cf] = ("Confluence", _TIMEOUT_CF)
        if f_figma:
            futures[f_figma] = ("Figma", _TIMEOUT_FIGMA)
        if f_db:
            futures[f_db] = ("DB", _TIMEOUT_DB)
        if f_yapi:
            futures[f_yapi] = ("YApi", _TIMEOUT_YAPI)

        try:
            for fut in as_completed(futures, timeout=_MAX_TIMEOUT):
                name, _to = futures[fut]
                _step1_done += 1
                try:
                    # ★ as_completed 只 yield 已完成的 future，result() 立即可取无需超时
                    if name == "Confluence":
                        (
                            cf_ok,
                            req_text,
                            cf_result,
                            cf_changed,
                            cf_from_cache,
                            old_req_text,
                            image_urls,
                        ) = fut.result(timeout=0)
                        if cf_ok:
                            st = {"status": "ok", "detail": "%d 字符" % len(req_text)}
                        elif not config.confluence_page_ids:
                            st = {"status": "skipped", "detail": "未配置"}
                        else:
                            err = (
                                cf_result.get("error", "未知")
                                if isinstance(cf_result, dict)
                                else "未知"
                            )
                            st = {"status": "failed", "detail": str(err)[:100]}
                        _step1_report_progress(_step1_done, _step1_total, name, st)
                    elif name == "Figma":
                        figma_ok, ui_context, figma_result = fut.result(timeout=0)
                        if figma_ok:
                            st = {"status": "ok", "detail": "%d 字符" % len(ui_context)}
                        elif not config.figma_url:
                            st = {"status": "skipped", "detail": "未配置"}
                        else:
                            st = {"status": "failed", "detail": "拉取失败"}
                        _step1_report_progress(_step1_done, _step1_total, name, st)
                    elif name == "DB":
                        test_accounts, db_status = fut.result(timeout=0)
                        _step1_report_progress(
                            _step1_done, _step1_total, name, db_status
                        )
                    elif name == "YApi":
                        yapi_ok, api_docs_context, yapi_summary_text, yapi_status = (
                            fut.result(timeout=0)
                        )
                        _step1_report_progress(
                            _step1_done, _step1_total, name, yapi_status
                        )
                except Exception as e:
                    _log.error("  ✗ %s 拉取异常: %s", name, str(e)[:120])
                    if name == "Confluence":
                        cf_result = {"error": str(e)}
                        st = {"status": "failed", "detail": str(e)[:100]}
                    elif name == "Figma":
                        figma_result = {"success": False, "error": str(e)}
                        st = {"status": "failed", "detail": str(e)[:100]}
                    elif name == "DB":
                        db_status = {"status": "failed", "detail": str(e)[:100]}
                        st = db_status
                    elif name == "YApi":
                        yapi_status = {"status": "failed", "detail": str(e)[:100]}
                        st = yapi_status
                    _step1_report_progress(_step1_done, _step1_total, name, st)
        except FutureTimeoutError:
            # ★ as_completed 超时：标记未完成的源
            for fut, (name, _to) in futures.items():
                if not fut.done():
                    _log.error("  ✗ %s 超时（>%ds），已跳过", name, _MAX_TIMEOUT)
                    _step1_done += 1
                    st = {"status": "failed", "detail": f"超时（>{_MAX_TIMEOUT:d}s）"}
                    _step1_report_progress(_step1_done, _step1_total, name, st)
                    if name == "Confluence":
                        cf_result = {"error": f"超时（>{_MAX_TIMEOUT:d}s）"}
                    elif name == "Figma":
                        figma_result = {
                            "success": False,
                            "error": f"超时（>{_MAX_TIMEOUT:d}s）",
                        }
                    elif name == "DB":
                        db_status = {
                            "status": "failed",
                            "detail": f"超时（>{_MAX_TIMEOUT:d}s）",
                        }
                    elif name == "YApi":
                        yapi_status = {
                            "status": "failed",
                            "detail": f"超时（>{_MAX_TIMEOUT:d}s）",
                        }

    # ═══════════════ Phase 2.5: Figma 重试 ═══════════════
    # ★ Figma 重试（最多 2 次，间隔 5s）
    _FIGMA_MAX_RETRIES = 2
    if not figma_ok and not _skip_figma and config.figma_url:
        for _retry in range(1, _FIGMA_MAX_RETRIES + 1):
            _log.info("  🔄 Figma 重试 %d/%d ...", _retry, _FIGMA_MAX_RETRIES)
            import time as _time

            _time.sleep(5)
            with guard("step1_fetch_data: _figma_executor = ThreadPoolExecutor", log=_log, level=logging.WARNING):
                _figma_executor = ThreadPoolExecutor(max_workers=1)
                _f_retry = _figma_executor.submit(
                    _fetch_figma_in_thread,
                    fetcher,
                    active_ver,
                    _version_label,
                    prev_active_version_label,
                    figma_force_refresh,
                    force_overwrite,
                    ctx,
                )
                try:
                    figma_ok, ui_context, figma_result = _f_retry.result(
                        timeout=_TIMEOUT_FIGMA
                    )
                    if figma_ok:
                        _log.info("  ✓ Figma 重试 %d 成功", _retry)
                        # ★ 重试成功后更新 sources_status
                        _step1_report_progress(
                            _step1_done,
                            _step1_total,
                            "Figma",
                            {"status": "ok", "detail": "%d 字符" % len(ui_context)},
                        )
                        break
                except FutureTimeoutError:
                    _log.warning("  ✗ Figma 重试 %d 超时", _retry)
                finally:
                    _figma_executor.shutdown(wait=False)

    # ═══════════════ Phase 3: 后处理 ═══════════════
    _log.info("=" * 50)
    _log.info("  数据源拉取完成，开始后处理...")

    # 更新 sources_status（跳过用户选择跳过的数据源，避免覆盖"用户选择跳过"状态）
    if not _skip_cf:
        if cf_ok:
            _cf_data = cf_result.get("data", {}) if isinstance(cf_result, dict) else {}
            _cf_pages = (
                _cf_data.get("pages")
                or _cf_data.get("titles")
                or _cf_data.get("page_ids")
                or []
            )
            sources_status["confluence"] = {
                "status": "ok",
                "detail": "%d 页 · %d 字符" % (len(_cf_pages), len(req_text)),
                "page_count": len(_cf_pages),
                "char_count": len(req_text),
            }
        elif not config.confluence_page_ids:
            sources_status["confluence"] = {"status": "skipped", "detail": "未配置"}
        else:
            err = (
                cf_result.get("error", "未知")
                if isinstance(cf_result, dict)
                else "未知"
            )
            sources_status["confluence"] = {
                "status": "failed",
                "detail": str(err)[:100],
            }

    # 合并上传文档
    req_text, upload_info = fetcher._merge_uploaded_docs(
        req_text, getattr(config, "project_name", "") or "", _version_label
    )
    if upload_info.get("count", 0) > 0:
        _fmt_list = upload_info.get("formats", [])
        _fmt_detail = ", ".join(_fmt_list) if _fmt_list else ""
        sources_status["upload"] = {
            "status": "ok",
            "detail": (
                "%d 个文件 (%s)" % (upload_info["count"], _fmt_detail)
                if _fmt_detail
                else "%d 个文件" % upload_info["count"]
            ),
        }
    else:
        sources_status["upload"] = {"status": "skipped", "detail": "无上传文件"}

    if not _skip_figma:
        if figma_ok and ui_context:
            _figma_pages = (
                (figma_result or {}).get("total_pages", 0)
                if isinstance(figma_result, dict)
                else 0
            )
            _figma_nodes = (
                (figma_result or {})
                .get("layers", {})
                .get("layer3", {})
                .get("fetched_nodes", 0)
                if isinstance(figma_result, dict)
                else 0
            )
            sources_status["figma"] = {
                "status": "ok",
                "detail": "%d 页面 · %d 节点 · %d 字符"
                % (_figma_pages, _figma_nodes, len(ui_context)),
                "page_count": _figma_pages,
                "node_count": _figma_nodes,
                "char_count": len(ui_context),
            }
        elif not config.figma_url:
            sources_status["figma"] = {"status": "skipped", "detail": "未配置"}
        else:
            sources_status["figma"] = {"status": "failed", "detail": "拉取失败"}

    if not _skip_db:
        sources_status["database"] = db_status
    if not _skip_yapi:
        sources_status["yapi"] = yapi_status

    # 元数据：version_id 供 pre_step_gate 等下游解析
    sources_status["_version_id"] = int(active_ver.get("id") or 0) if active_ver else 0
    # DOM 维度改由 Step4 前置侦察产出；Step1 如实标注跳过（前端按 7 源展示）
    sources_status["dom_crawl"] = {
        "status": "skipped",
        "detail": "静态 DOM 爬取已由 AgentExplorer 实时探索替代",
        "page_count": 0,
    }

    # ═══════════════ Phase 3.1: 原型图视觉识别 ═══════════════
    # ★ 阶段拆分(2026-07-25): Phase 3.1 已移至 _step1_phases._run_prototype_recognition_phase
    #   原型图识别结果(prototype_chunks) 供 Phase 3.7 RAG 构建使用
    prototype_chunks = _run_prototype_recognition_phase(image_urls, sources_status)
    # ★ 后处理进度上报（不传 source_name，避免覆盖 sources_status）
    _step1_done += 1
    _step1_report_progress(_step1_done, _step1_total)

    # 持久化
    fetcher._persist_requirements(cf_ok, req_text, cf_result, _version_label)
    fetcher._persist_sources_status(sources_status, _version_label)

    # 摘要
    fetcher._build_summary(
        cf_ok,
        cf_result,
        bool(config.confluence_page_ids),
        req_text,
        figma_ok,
        bool(config.figma_url),
        ui_context,
        cf_changed,
        figma_force_refresh,
        yapi_ok,
        api_docs_context,
        yapi_summary_text,
        test_accounts,
        config.db_enabled and not _skip_db,
        figma_result=figma_result,
        db_status=db_status,
    )

    # ═══════════════ Phase 3.5: 多源功能提取（Figma/需求/YApi → system_feature_list） ═══════════════
    # 提取只读已持久化的数据源事实，先于失败判定：单源失败不影响其余源的提取产物。
    _step1_proj = getattr(config, "project_name", "") or ""
    try:
        from src.pipeline.steps.step1.multi_source_features import (
            run_multi_source_feature_extraction,
        )

        _ms_version = _version_label or ""
        if _step1_proj and _ms_version:
            _ms_result = run_multi_source_feature_extraction(_step1_proj, _ms_version)
            sources_status["multi_source_features"] = {
                "status": "ok",
                "detail": "figma=%d 条（需求/YApi 走 RAG 检索，不进功能清单）"
                % _ms_result.get("figma", 0),
            }
            _log.info(
                "  ✓ 多源功能提取: %s",
                sources_status["multi_source_features"]["detail"],
            )
    except Exception as _ms_e:
        _log.warning("  ⚠ 多源功能提取异常（不影响主流程）: %s", str(_ms_e)[:120])
        sources_status["multi_source_features"] = {
            "status": "failed",
            "detail": str(_ms_e)[:80],
        }
    # ★ 后处理进度上报（不传 source_name，避免覆盖 sources_status）
    _step1_done += 1
    _step1_report_progress(_step1_done, _step1_total)

    # ═══════════════ Phase 3.6: 数据源失败判定 ═══════════════
    # 失败判定必须先于 RAG 构建：失败的运行不得写入索引，否则本版本会留下
    # 残缺切片，后续运行/下游无法区分"上次失败的部分结果"和"真实数据"。
    _block_err = check_step1_blocking_failures(sources_status, req_text)
    if _block_err:
        _log.error("  ❌ %s（阻断执行）", _block_err)
        fetcher._persist_sources_status(sources_status, _version_label)
        return {"success": False, "error": _block_err}

    # ═══════════════ Phase 3.7: RAG 增量构建（功能清单已就绪） ═══════════════
    _build_rag_index_phase(
        prototype_chunks, sources_status, _step1_proj, _version_label
    )
    _step1_done += 1
    _step1_report_progress(_step1_done, _step1_total)

    # ★ 上传需求正文必须完整入索引（Step1 硬契约）：不完整时下游只能基于残缺需求
    #   生成用例且毫无提示 —— 与数据源失败同级，直接阻断而不是仅记录告警
    _rag_cov = sources_status.get("rag_coverage") or {}
    if _rag_cov.get("status") == "failed":
        _log.error("  ❌ 上传文档切片覆盖不完整: %s（阻断执行）", _rag_cov.get("detail"))
        try:
            fetcher._persist_sources_status(sources_status, _version_label)
        except Exception as _persist_cov_e:
            _log.warning("[Step1] 覆盖审计状态持久化失败: %s", _persist_cov_e)
        return {
            "success": False,
            "error": "上传文档切片覆盖不完整: %s。请检查文档内容后重新上传或重跑 Step1"
            % (_rag_cov.get("detail") or ""),
        }

    # ★ 最终持久化：数据源状态 + 多源功能清单一起写入
    fetcher._persist_sources_status(sources_status, _version_label)

    # ★ v4.0 诊断指标：Step1 数据源状态（每个数据源一条记录 + 汇总）
    with guard("step1_fetch_data: _mc_project = getattr(config, 'proje", log=_log):
        _mc_project = getattr(config, "project_name", "") or ""
        metrics_collector.record_step1_source_status(
            _mc_project, run_id, sources_status
        )

    # ★ 插件总线：发布 STEP1_COMPLETE 事件（供 Step1DatasourcePlugin 等订阅）
    with guard("step1_fetch_data: from src.pipeline.plugins.pipeline_b", log=_log):
        from src.pipeline.plugins.pipeline_bus import pipeline_bus, PipelineEvent

        pipeline_bus.publish(
            PipelineEvent.STEP1_COMPLETE,
            {
                "project": getattr(config, "project_name", "") or "",
                "run_id": run_id,
                "results": sources_status,
            },
        )

    return {
        "success": True,
        "req_text": req_text,
        "ui_context": ui_context,
        "api_docs_context": api_docs_context,
        "test_accounts": test_accounts,
        "sources_status": sources_status,
        "version_label": _version_label,
        "cf_changed": cf_changed,
        "cf_from_cache": cf_from_cache,
    }
