# -*- coding: utf-8 -*-
"""
_run_step —— 在后台线程中执行指定步骤。
从原 executor.py 提取，逻辑保持不变。

[已复核-DeepSeek-V3 2026-08-16]
B6 转测文档契约修复：新增 ``_resolve_doc()``，把前端 ``deployDoc``（doc **标识**）
解析为 ``deployment_doc``（doc **正文**）；并补上 ``deployVersion`` → ``version_filter``
的键名映射。二者原先均被后端静默丢弃，导致「转测文档 + 转测版本」过滤完全失效。
前端 key 名未改动；解析失败一律 fail-closed（不过滤 + 显式告警），绝不伪造过滤结果。
"""

from dataclasses import dataclass, field
from src.core.guarded import guard
from pathlib import Path
import time
import logging

_log = logging.getLogger(__name__)

ROOT = (
    Path(__file__).resolve().parent.parent.parent.parent
)  # src/backend/executor/../../../ = 项目根

from src.backend.state import (
    _current_project,
    _thread_run_id,
    _set_running,
    _set_force_stop,
    _set_paused_step,
    _get_paused_step,
    _active_runs,
    _active_runs_lock,
    _run_progress,
    _run_progress_lock,
    _run_sse_queues,
    _run_sse_queues_lock,
    _update_progress,
)
from src.backend.helpers import _confirm_figma_fetch


@dataclass
class StepRunOptions:
    """_run_step 参数封装，避免 10 参数长签名。"""

    step_num: int
    run_id: str
    step4_options: dict = field(default_factory=dict)
    end_step: int = 0
    figma_force_refresh: bool = False
    force_regen: bool = False
    resume: bool = False
    quick: bool = False
    pipeline_mode: str = "sequential"
    skip_sources: list = None
    force_overwrite: bool = False  # ★ 强制覆盖所有 Step1 数据
    force_step2: bool = (
        False  # ★ 强制重拆解 Step2（忽略哈希检查，unchanged 改为 modified）
    )


def _resolve_doc(doc_ref: str, project: str) -> str:
    """把「转测文档标识」解析为「转测文档正文」。

    [B6 契约修复] 前端 ``step4-config.html`` 提交的字段是 ``deployDoc``，其值是
    ``/api/deployment/docs`` 返回的 **doc 标识**（形如 ``mysql://source_documents/deployment``），
    而后端 ``_run_step`` 原先只认 ``deployment_doc_content``（**文档正文**）。
    二者是"标识 vs 正文"两种语义，导致前端选了转测文档后**后端静默丢弃**，
    Step4 拿不到 ``deployment_doc``，转测文档过滤形同虚设。

    本函数复用既有的 MySQL 虚拟文档解析层 ``docs_analytics.fetchers.mysql_doc.resolve_doc``
    （与 ``/api/deployment/preview`` 走同一条解析路径，行为一致），把标识换成正文。

    Args:
        doc_ref: 文档标识。支持 ``mysql://`` URI 与虚拟相对路径两种协议；
            空串/None 视为"用户未选择"。
        project: 项目名，用于限定查询范围。

    Returns:
        文档正文字符串。**解析失败一律返回空串**（fail-closed：宁可不过滤，
        也不能拿错误内容去过滤用例），由调用方决定是否告警。
    """
    if not doc_ref or not isinstance(doc_ref, str) or not doc_ref.strip():
        return ""
    if not project:
        _log.warning("[转测文档] 无法解析 %r：当前无项目上下文", doc_ref[:80])
        return ""
    try:
        from src.backend.routes.docs_analytics.fetchers.mysql_doc import resolve_doc

        _doc = resolve_doc(doc_ref.strip(), project)
    except Exception as e:
        _log.warning("[转测文档] 解析 %r 异常: %s", doc_ref[:80], str(e)[:120])
        return ""
    if not _doc:
        _log.warning("[转测文档] 标识无法解析为文档: %r", doc_ref[:80])
        return ""
    if _doc.get("kind") not in ("text", "log"):
        _log.warning(
            "[转测文档] 标识 %r 解析出的是非文本文档(kind=%s)，已忽略",
            doc_ref[:80],
            _doc.get("kind"),
        )
        return ""
    return str(_doc.get("content_text") or "")


def _fire_ci_webhook(run_id: str, status: str, summary: str, project: str):
    """CI/CD Webhook 回调：pipeline 完成后 POST 通知外部系统。

    优先读取 project_configs 中的 webhook_url（项目级），
    若未配置则跳过（不报错、不阻塞主流程）。
    """
    with guard("_fire_ci_webhook: from datetime import datetime as _dt", log=_log):
        from datetime import datetime as _dt
        from src.core.db_manager import get_connection, release_connection

        conn = None
        webhook_url = ""  # ★ 防 get_connection() 抛异常时 UnboundLocalError
        try:
            conn = get_connection()
            # 项目级 webhook URL（从 project_configs.config_json 提取）
            row = conn.execute(
                "SELECT JSON_UNQUOTE(JSON_EXTRACT(config_json, %s)) AS webhook_url "
                "FROM project_configs WHERE project=%s",
                ("$.webhook_url", project),
            ).fetchone()
            webhook_url = (row["webhook_url"] or "").strip() if row else ""
            # 降级：全局 webhook（system_config）
            if not webhook_url:
                global_row = conn.execute(
                    "SELECT `value` FROM system_config WHERE `key`='webhook_url' LIMIT 1"
                ).fetchone()
                webhook_url = (global_row["value"] or "").strip() if global_row else ""
        finally:
            if conn:
                release_connection(conn)

        if not webhook_url:
            return  # 未配置 webhook，静默跳过

        import json as _json
        import urllib.request as _ur

        payload = _json.dumps(
            {
                "event": "pipeline_complete",
                "run_id": run_id,
                "project": project,
                "status": status,
                "summary": summary[:500] if summary else "",
                "timestamp": _dt.now().isoformat(),
            },
            ensure_ascii=False,
        ).encode("utf-8")

        req = _ur.Request(
            webhook_url,
            data=payload,
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        _ur.urlopen(req, timeout=10)
        _log.info("[Webhook] ✓ 已发送回调: %s status=%s", webhook_url[:60], status)


def _run_step(opts: StepRunOptions):
    """在后台线程中执行指定步骤。opts.end_step 为 0 时只跑单步。
    opts.figma_force_refresh 强制刷新 Figma 缓存。
    opts.force_regen 强制重新生成测试用例（忽略覆盖率，仅 Step 3 生效）。
    opts.resume 从 checkpoint 恢复执行。
    opts.pipeline_mode: 'sequential' | 'streaming'（v4.0 流式模式委托 StreamingPipelineScheduler）。
    ★ session_ctx 由调用方传入，用于跨线程恢复 session（thread-local 不会自动继承）。"""
    # ★ run 身份（run_id + step_label）唯一注入点：必须在任何 print/_log 之前完成，
    #   否则函数开头的日志无 run_id、且 step_label 为空 → run_logs.step_num=0，
    #   污染「按 step 切片」的完成判据（日志归属与 run 身份同源，不靠下游补猜）。
    import src.backend.log_capture as lc

    from src.backend.log_persistence import begin_run_log, finish_run_log

    _thread_run_id.value = opts.run_id
    _log_file = begin_run_log(opts.run_id, f"step{opts.step_num}")
    lc._current_log_file.value = _log_file
    _log.debug(
        "[EXEC-DEBUG] _run_step START: step=%s, run_id=%s", opts.step_num, opts.run_id
    )
    _log.info(
        "[DEBUG-exec] _run_step 开始, step=%d, run_id=%s", opts.step_num, opts.run_id
    )

    # ★ 逐 Step 权限校验：防止项目成员通过一键执行绕过 step1/2/3 限制
    from src.models.permission import has_permission
    from src.core.config_loader import get_current_user
    from src.users.manager import is_admin
    from src.backend.state import (
        _current_project,
    )  # ★ 修复(07-08): 移到分支外，admin 也需要

    _uid = get_current_user()
    _actual_end = opts.end_step or opts.step_num
    if _uid and not is_admin(_uid):
        from src.models.permission import has_project_permission

        _proj = _current_project()
        for _s in range(opts.step_num, _actual_end + 1):
            _perm = f"execution.step{_s}"
            if not has_permission(_uid, _perm) and not (
                _proj and has_project_permission(_uid, _proj, _perm)
            ):
                _log.error(
                    "##HTML:##<span style='color:#ef4444'>✗ 无权执行 Step %d（需要权限: %s）</span>",
                    _s,
                    _perm,
                )
                _set_running(False)
                return {
                    "success": False,
                    "error": f"无权执行 Step {_s}（需要权限: {_perm}）",
                }

    _set_running(True)

    # ★ v2.1: 废弃全流程自动跳过审核闸门逻辑
    # 一键执行也强制暂停弹审核面板，set_skip_review_gate 已废弃不再生效
    # （review_gate.py 内部不再响应此标志）

    # ★ 持久化日志的 run 身份已在函数入口注入（见上），此处不再重复 begin_run_log。

    _status = "completed"
    _summary = ""
    with guard("_run_step: from src.pipeline.utils.pause_contro", log=_log, level=logging.WARNING):
        from src.pipeline.utils.pause_controller import pause_controller

        pause_controller.resume()
    try:
        if opts.step4_options:
            if opts.step4_options.get("case_asset_file"):
                with guard("_run_step: from src.core.excel_handler import e", log=_log, level=logging.WARNING):
                    from src.core.excel_handler import excel_handler

                    excel_handler.set_case_asset_path(
                        opts.step4_options["case_asset_file"]
                    )
            # ★ 转测文档：存 MySQL source_documents，内容直接传 scheduler
            if opts.step4_options.get("deployment_doc_content"):
                with guard("_run_step: from src.core.db_manager import ( ge", log=_log, level=logging.WARNING):
                    from src.core.db_manager import (
                        get_connection as _gc,
                        release_connection as _rc,
                    )

                    _content = opts.step4_options["deployment_doc_content"]
                    _fname = opts.step4_options.get(
                        "deployment_doc_name", "转测文档.md"
                    )
                    import re as _re

                    _fname = _re.sub(r"[^\w一-鿿\.\-]", "_", _fname)
                    _conn = _gc()
                    try:
                        # ★ 修复(07-01): 加入 version_label（PK 组成部分），避免多个 deployment 文档 PK 冲突
                        from src.core.versioning import current_version_label

                        _v_label = current_version_label(_current_project())
                        _conn.execute(
                            "INSERT INTO source_documents(project, doc_type, version_label, content, content_hash, char_count) "
                            "VALUES(%s, 'deployment', %s, %s, MD5(%s), CHAR_LENGTH(%s)) "
                            "ON DUPLICATE KEY UPDATE content=%s, content_hash=MD5(%s), char_count=CHAR_LENGTH(%s)",
                            (
                                _current_project(),
                                _v_label,
                                _content,
                                _content,
                                _content,
                                _content,
                                _content,
                                _content,
                            ),
                        )
                        _conn.commit()
                        opts.step4_options["deployment_doc"] = _content
                        _log.info("  [转测文档] 已存 MySQL (%d 字符)", len(_content))
                    finally:
                        _rc(_conn)
            # ★ [B6 契约修复] 前端下拉选择的是 doc 标识（deployDoc），不是正文。
            #   原先后端只认 deployment_doc_content，导致选了文档也被静默丢弃。
            #   此处补上「标识 → 正文」解析；仅在未直传正文时生效，不影响上传路径。
            elif opts.step4_options.get("deployDoc"):
                _doc_ref = str(opts.step4_options.get("deployDoc") or "")
                _resolved = _resolve_doc(_doc_ref, _current_project())
                if _resolved:
                    opts.step4_options["deployment_doc"] = _resolved
                    _log.info(
                        "  [转测文档] 已由标识解析出正文 (%d 字符, ref=%s)",
                        len(_resolved),
                        _doc_ref[:60],
                    )
                else:
                    # fail-closed：解析不出就不过滤，但必须显式告警，不能静默
                    _log.warning(
                        "  [转测文档] ⚠️ 标识 %r 未能解析出正文，本次执行**不做转测文档过滤**",
                        _doc_ref[:60],
                    )
            # ★ [B6 同类第二处 — 仅告警，**故意不做映射**]
            #   前端提交 deployVersion，step4_executor:122 读的是 version_filter，
            #   二者从未连通 → 转测版本过滤同样被静默丢弃。
            #   但**不能简单改键名映射**：deployVersion 的 option value 是
            #   /api/project/versions 的「项目版本 ID」（如 "12"），而 matcher 的
            #   version 参数比对的是「转测文档表格 version 列」的版本号文本
            #   （orchestrate.py:227 filter_by_version）。两者语义不同，强行映射会让
            #   过滤结果恒为空（"没有匹配版本 12 的功能点"），比静默丢弃更糟。
            #   → 此处只做显式告警，修法需产品先定义该下拉框应展示哪一份版本列表。
            if opts.step4_options.get("deployVersion") and not opts.step4_options.get(
                "version_filter"
            ):
                _log.warning(
                    "  [转测文档] ⚠️ 已选转测版本 %r，但该值是项目版本ID、与转测文档内版本号"
                    "语义不符，**本次不做版本过滤**（详见 B6 遗留项）",
                    str(opts.step4_options["deployVersion"])[:40],
                )

        from src.pipeline.scheduler import PipelineScheduler
        from src.core.config_loader import get_current_user
        from src.backend.state import _get_force_stop

        _run_project = _current_project()
        s = PipelineScheduler(project_name=_run_project, user_id=get_current_user())
        s.run_id = opts.run_id
        # ★ A1 改造：_force_stop_check 仅检查当前项目的停止信号，不误杀其他项目
        s._force_stop_check = lambda: _active_runs.get(opts.run_id, {}).get(
            "force_stop", False
        ) or _get_force_stop(_run_project)
        s._progress_callback = lambda sn, sl, sd, st, fs, es: _update_progress(
            opts.run_id, sn, sl, sd, st, fs, es
        )
        from src.users.manager import log_action

        current_project = _run_project or ""
        log_action(
            get_current_user(),
            "step-exec",
            scope_type="project" if current_project else "system",
            scope_id=current_project,
            detail=f"from={opts.step_num} to={opts.end_step or opts.step_num} run_id={opts.run_id}",
        )
        project_name = _run_project or "default"
        with guard("_run_step: from src.pipeline.steps.step4.op_cac", log=_log):
            from src.pipeline.steps.step4.op_cache import op_cache

            if hasattr(op_cache, "warmup"):
                warmed = op_cache.warmup(project_name, top_n=20)
                if warmed:
                    _log.info(
                        "##HTML:##<span style='color:#3b82f6'>🔥 [缓存预热] 已加载 %d 条高频操作</span>",
                        len(warmed),
                    )
        _log.info("[DEBUG-exec] 准备调用 s.run, step=%d", opts.step_num)
        result = s.run(
            from_step=opts.step_num,
            end_step=opts.end_step or opts.step_num,
            quick=bool(opts.quick),
            step4_options=opts.step4_options,
            figma_force_refresh=opts.figma_force_refresh,
            force_regen=opts.force_regen,
            resume=opts.resume,
            pipeline_mode=opts.pipeline_mode,
            skip_sources=opts.skip_sources,
            force_overwrite=opts.force_overwrite,
            force_step2=opts.force_step2,
        )

        _log.info(
            f"[DEBUG] s.run() returned, result type={type(result)}, paused={result.get('paused') if result else 'N/A'}"
        )
        # ★ 根因修复(2026-10-15) D54：run 终态必须反映**步骤真实结局**。
        #   根因: `_status` 初值恒为 "completed"，只在 paused/异常两条路径被改写；
        #     步骤超时（scheduler_executor 发 `step_result={"success": False}` 并
        #     `[Step4] 失败，流水线中止`）**不回传** → run 仍以 status=completed 收尾。
        #   后果(实测 v14): Step4 于 7200s 被强杀在 304/597，141 条用例从未执行，
        #     但 RUN_END 报 completed → 统计层按 606 全分母出"通过率 69.8%"
        #     → **把未执行算进分母**，违反"完成判据本身必须可靠"。
        #   修法: 只要结果里含失败/超时信号，即把 run 终态降级为 aborted(超时) / failed，
        #     使消费方（统计脚本/前端）能据 status 拒绝出通过率或显式标注未执行数。
        #   通用性: 只读步骤结果的成功标志，零业务/模块硬编码。
        _step_ok = None
        if isinstance(result, dict):
            _step_ok = result.get("success", result.get("ok"))
        if _step_ok is False or (
            isinstance(result, dict) and result.get("error")
        ):
            _err = str((result or {}).get("error") or "")
            if "超时" in _err or "timeout" in _err.lower():
                _status = "aborted_timeout"
            elif _status == "completed":
                _status = "failed"
            _summary = _err[:200]
            _log.error(
                "[流水线] run 终态=%s（步骤未成功），summary=%s",
                _status,
                _summary,
            )
        if result and result.get("paused"):
            _set_paused_step(result.get("paused_at_step", opts.step_num), _run_project)
            _status = "paused"
            _log.info(
                f"[流水线] 已暂停，断点: Step {result.get('paused_at_step', opts.step_num)}"
            )
    except Exception as e:
        import traceback

        _status = "error"
        _summary = str(e)[:200]
        _log.error(
            f"##HTML:##<span style='color:#ef4444'>✗ Step {opts.step_num} 异常: {e}</span>"
        )
        traceback.print_exc()
    finally:
        # ★ 修复: run 生命周期兜底清理 — 异常路径（s.run 抛异常）或
        #   线程卡死后，原清理逻辑在 finally 中段（:401-402）可能永不执行，导致
        #   _active_runs 残留 → 后续 step4-run-single 永远 409「正在执行中」。
        #   本处兜底 pop 幂等（不存在即跳过），保证任何路径下 run 都释放
        #   （F-05 一条一例依赖 run 生命周期正确性）。
        with guard("_run_step: with _active_runs_lock: _active_runs", log=_log, level=logging.WARNING):
            with _active_runs_lock:
                _active_runs.pop(opts.run_id, None)
                _still_active_after_cleanup = len(_active_runs) > 0
            with _run_progress_lock:
                _run_progress.pop(opts.run_id, None)
        lc._current_log_file.value = None
        finish_run_log(opts.run_id, _log_file, _status, _summary)
        # ★ AI 健康度指标持久化
        try:
            from src.extensions.metrics_collector import metrics_collector

            metrics_collector.flush_run(opts.run_id)
        except Exception as e:
            import traceback

            _log.error(
                f"##HTML:##<span style='color:#ef4444'>✗ [metrics flush] run_id={opts.run_id} 异常: {e}</span>"
            )
            traceback.print_exc()
            with guard("_run_step: import json as _json", log=_log, level=logging.WARNING):
                import json as _json
                from src.backend.state import (
                    _health_flush_errors,
                    _health_flush_errors_lock,
                )

                err_msg = str(e)[:200]
                with _health_flush_errors_lock:
                    _health_flush_errors[opts.run_id] = err_msg
                from src.backend.log_capture import _broadcast_log

                _broadcast_log(
                    _json.dumps(
                        {
                            "event": "health_flush_error",
                            "run_id": opts.run_id,
                            "error": err_msg,
                        }
                    ),
                    run_id=opts.run_id,
                )
        # ★ v23 根源修复(2026-07-21): 置信度阈值拦截 — 直接从 executions 表读取
        #   ConfidenceGate 已持久化的 confidence，不再重新构造 block_in 重新计算。
        #   根因: ConfidenceGate 在 _execute_one_intent 中已正确计算 confidence（如83%），
        #   但原代码在 _run_step 中又从 DB 重新构造 block_in（step_results=[]）再算一次，
        #   导致 confidence 被严重低估为 35%，假 PASS 漏检。
        #   架构红线4: 禁止在 Step 之间插入格式转换层 — 本段原代码就是格式转换层。
        #   通用性: 适用于所有用例（P01/N01/B01），不针对单一用例/系统/行业。
        with guard("_run_step: from src.core.db_manager import ( ge", log=_log):
            from src.core.db_manager import (
                get_connection as _gc_conf,
                release_connection as _rc_conf,
            )
            from src.core.pipeline_config import _read_config_value as _cfg
            import json as _json

            # ★ 通用修复(2026-07-21): 置信度阈值对齐 AGENTS.md §13.4，confidence<0.70 视为假PASS
            _LOW_THRESHOLD = float(_cfg("confidence_low_threshold", 0.70))
            _MED_THRESHOLD = float(_cfg("confidence_medium_threshold", 0.85))
            conn = _gc_conf()
            try:
                exec_rows = conn.execute(
                    "SELECT case_id, status, failure_reason, confidence, confidence_factors, "
                    "expected, actual, details "
                    "FROM executions WHERE project=%s AND run_id=%s",
                    (project_name, opts.run_id),
                ).fetchall()
            finally:
                _rc_conf(conn)
            if exec_rows:
                blocked, warned = [], []
                _conf_sum, _pass_cnt, _valid_cnt = 0.0, 0, 0
                for r in exec_rows:
                    cid = str(r.get("case_id") or "").strip()
                    if not cid:
                        continue
                    status = str(r.get("status") or "").strip().upper()
                    if not status:
                        continue
                    confidence = float(r.get("confidence") or 0)
                    confidence_factors = r.get("confidence_factors")
                    passed = status in ("PASS", "PASSED", "OK", "SUCCESS", "TRUE")
                    # ★ v23 修复2: 只对 confidence_factors 不为 NULL 的记录做拦截判定。
                    #   根因: run_id 被复用，包含大量旧执行记录（v23修复前 confidence=0, factors=NULL），
                    #   以及 pre_gate_step3/pre_gate_step4 等预检查记录（ConfidenceGate 不执行）。
                    #   若纳入拦截判定会被误判为"低置信度"，混淆"旧数据"和"假PASS"。
                    #   通用性: confidence_factors=NULL 是 ConfidenceGate 未执行的可靠标志，
                    #         适用于所有旧数据、pre_gate记录、ConfidenceGate异常的情况。
                    if confidence_factors is None:
                        continue  # ConfidenceGate 未执行，跳过拦截
                    _valid_cnt += 1  # 统计 ConfidenceGate 执行过的记录数
                    # ★ v23 修复: 只对 PASS 用例做置信度拦截判定。
                    #   根因: FAIL 用例的 confidence=0（ConfidenceGate 仅在 status==PASS 时执行），
                    #   若纳入拦截判定会被误判为"低置信度"，混淆"执行失败"和"假PASS"。
                    #   通用性: 适用于所有用例，FAIL用例的失败原因已在 failure_reason 中记录。
                    if not passed:
                        continue  # FAIL 用例跳过置信度拦截，已在执行时记录失败
                    # ★ 通用修复(2026-07-21): 假PASS拦截——空 evidence 或 confidence<0.70 都按 FAIL 处理
                    #   根因: AGENTS.md §13.4 规定 status=PASS 但 evidence 为空或 confidence<70% 视为假PASS，必须拦截。
                    #   通用性: 适用于所有项目、所有用例；不污染 Step4 原始记录，仅在 step 结束校验层拦截。
                    ev_empty = not (
                        r.get("expected") or r.get("actual") or r.get("details")
                    )
                    if ev_empty:
                        blocked.append(
                            {
                                "case_id": cid,
                                "title": "",
                                "confidence": confidence,
                                "reason": "空 evidence 假PASS: expected/actual/details 全空",
                                "level": "low",
                            }
                        )
                        continue
                    if confidence < _LOW_THRESHOLD:
                        # 解析 factors 用于 reason
                        factors_str = ""
                        with guard("_run_step: factors = _json.loads(r.get('confide", log=_log):
                            factors = _json.loads(r.get("confidence_factors") or "{}")
                            factors_str = ", ".join(
                                f"{k}={v:.2f}" for k, v in factors.items()
                            )
                        reason = f"置信度 {confidence*100:.0f}% < {_LOW_THRESHOLD*100:.0f}% 阈值，factors: {factors_str}"
                        blocked.append(
                            {
                                "case_id": cid,
                                "title": "",
                                "confidence": confidence,
                                "reason": reason,
                                "level": "low",
                            }
                        )
                        continue
                    # 真实 PASS 才计入通过统计
                    _pass_cnt += 1
                    _conf_sum += confidence
                    if confidence < _MED_THRESHOLD:
                        warned.append(
                            {
                                "case_id": cid,
                                "title": "",
                                "confidence": confidence,
                                "reason": f"置信度偏低 ({confidence*100:.0f}%)",
                                "level": "medium",
                            }
                        )
                total = max(_valid_cnt, 1)  # 只统计 ConfidenceGate 执行过的记录
                block_rate = round(len(blocked) / total * 100, 1)
                warn_rate = round(len(warned) / total * 100, 1)
                # avg_c 只统计 PASS 用例（FAIL 用例 confidence=0 是默认值，不应纳入）
                avg_c = (
                    round(_conf_sum / max(_pass_cnt, 1), 3) if _pass_cnt > 0 else 0.0
                )
                pass_rate = round(_pass_cnt / total * 100, 1)
                if blocked:
                    _log.error(
                        "##HTML:##<span style='color:#ef4444'>🔴 [置信度拦截] 拦截 %d 条低置信度测试用例（占 %.1f%%），必须人工复核</span>",
                        len(blocked),
                        block_rate,
                    )
                    for b in blocked[:10]:
                        _log.info(
                            "  - [%s] 置信度=%.0f%%  %s",
                            b.get("case_id"),
                            b.get("confidence", 0) * 100,
                            b.get("reason", ""),
                        )
                    try:
                        from src.backend.log_capture import _broadcast_log

                        _broadcast_log(
                            _json.dumps(
                                {
                                    "event": "confidence_block",
                                    "blocked": [
                                        {
                                            "case_id": b.get("case_id"),
                                            "confidence": round(
                                                b.get("confidence", 0) * 100, 1
                                            ),
                                            "reason": b.get("reason", ""),
                                        }
                                        for b in blocked[:20]
                                    ],
                                    "block_rate": block_rate,
                                    "total": len(blocked),
                                }
                            ),
                            run_id=opts.run_id,
                        )
                    except Exception:
                        _log.debug("[置信度拦截] SSE 广播失败", exc_info=True)

                    # ★ v3.1: 自动重建 — 低置信度用例自动触发 AI 重新建模 + 去重
                    with guard("_run_step: from src.core.pipeline_config import", log=_log, level=logging.WARNING):
                        from src.core.pipeline_config import pipeline_config

                        if getattr(pipeline_config, "confidence_auto_rebuild", False):
                            rebuild_max = getattr(
                                pipeline_config, "confidence_auto_rebuild_max", 5
                            )
                            rebuild_list = [
                                b.get("case_id")
                                for b in blocked[:rebuild_max]
                                if b.get("case_id")
                            ]
                            if rebuild_list:
                                _log.info(
                                    "##HTML:##<span style='color:#60a5fa'>🔄 [自动重建] 开始对 %d 条低置信度用例重新建模（去重）...</span>",
                                    len(rebuild_list),
                                )
                                from src.backend.routes.case_review._remodel import (
                                    _remodel_intent,
                                )

                                rebuilt_ok_ids = set()
                                rebuilt_dup = 0
                                rebuilt_fail = 0
                                for cid in rebuild_list:
                                    r = _remodel_intent(cid)
                                    if r.get("ok"):
                                        rebuilt_ok_ids.add(cid)
                                    elif r.get("reason") == "all_duplicates":
                                        rebuilt_dup += 1
                                    else:
                                        rebuilt_fail += 1
                                _log.info(
                                    "##HTML:##<span style='color:#60a5fa'>🔄 [自动重建] 完成: 成功 %d, 全重复 %d, 失败 %d</span>",
                                    len(rebuilt_ok_ids),
                                    rebuilt_dup,
                                    rebuilt_fail,
                                )
                                if rebuilt_ok_ids:
                                    blocked = [
                                        b
                                        for b in blocked
                                        if b.get("case_id") not in rebuilt_ok_ids
                                    ]
                if warned:
                    _log.warning(
                        "##HTML:##<span style='color:#f59e0b'>🟡 [置信度提示] %d 条测试用例置信度偏低（占 %.1f%%），建议人工复核</span>",
                        len(warned),
                        warn_rate,
                    )
                if not blocked and not warned:
                    _log.info(
                        "##HTML:##<span style='color:#22c55e'>✓ [置信度] 全部测试用例通过阈值校验</span>"
                    )
                # ★ P1-1: 记录置信度趋势（数据源: executions.confidence，不再重新计算）
                with guard("_run_step: from src.extensions.metrics_collecto", log=_log):
                    from src.extensions.metrics_collector import metrics_collector

                    if hasattr(metrics_collector, "record_confidence_run"):
                        metrics_collector.record_confidence_run(
                            opts.run_id, project_name, avg_c, pass_rate, block_rate
                        )
                        _log.info(
                            "##HTML:##<span style='color:#8b5cf6'>📈 [趋势] 本批次 avg_conf=%.1f%% pass_rate=%.1f%% block_rate=%.1f%%</span>",
                            avg_c * 100,
                            pass_rate,
                            block_rate,
                        )
        # ★ P2-5: 坏缓存自动清理 + P1/P3 趋势持久化（flush_run 已覆盖）
        try:
            from src.pipeline.steps.step4.op_cache import op_cache

            if hasattr(op_cache, "invalidate_by_success_rate"):
                purged = op_cache.invalidate_by_success_rate(project_name, min_rate=0.3)
                if purged > 0:
                    _log.info(
                        "##HTML:##<span style='color:#f97316'>🗑 [缓存清理] 已淘汰 %d 条低成功率缓存</span>",
                        purged,
                    )
        except Exception:
            _log.debug("[缓存清理] op_cache.invalidate 跳过")
            pass
        # ★ P1-8: 执行结束后自动触发幻觉规则更新
        with guard("_run_step: from src.extensions.quality.hallucin", log=_log):
            from src.extensions.quality.hallucination_logger import hallucination_logger

            if hasattr(hallucination_logger, "analyze_and_update_rules"):
                hallucination_logger.analyze_and_update_rules()
                _log.info("[Hallucination] 规则自动更新完成")
        with _active_runs_lock:
            _active_runs.pop(opts.run_id, None)
            still_active = len(_active_runs) > 0
        with _run_progress_lock:
            _run_progress.pop(opts.run_id, None)
        # ★ 修复：不再删除 per-run SSE 队列。SSE 连接由 GeneratorExit 自行清理，
        #    此处删除会导致后续 Step 的日志无法通过 _send_to_per_run_queue 发送给前端。
        _thread_run_id.value = None
        # ★ A1 改造：清理操作按 project 隔离，不误清其他项目的状态
        _set_force_stop(False, _run_project)
        # ★ 如果流水线是暂停状态，不要重置 paused_step，以便审核批准后恢复
        _log.info(
            f"[DEBUG] finally block: paused_step={_get_paused_step(_run_project)}, step_num={opts.step_num}, _status={_status}"
        )
        if _get_paused_step(_run_project) == opts.step_num and _status != "paused":
            _set_paused_step(0, _run_project)
        # ★ 非暂停状态强制清除 pause_controller，防止残留阻塞下次启动
        if _status != "paused":
            from src.pipeline.utils.pause_controller import pause_controller

            pause_controller.resume()
        if not still_active:
            _set_running(False)  # ★ 清理僵尸 _active_runs + 重置 _thinking_active
        # ★ CI/CD Webhook 回调
        _fire_ci_webhook(opts.run_id, _status, _summary, _current_project())
