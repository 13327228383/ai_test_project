# -*- coding: utf-8 -*-
"""
运行日志持久化模块（MySQL 版）
=================================
将每次 Step 执行的日志实时写入 MySQL `run_logs` 表，替代原先的磁盘 .log 文件。
保留函数签名（begin_run_log / append_run_log / finish_run_log），供调用方透明切换。

表结构参考（由 src/core/schema.sql 定义）：
    run_logs(id PK, run_id, project, version_id, step_num, log_level, message MEDIUMTEXT, created_at)
"""

import threading
from src.core.guarded import guard
import logging
import sys
from datetime import datetime

_log = logging.getLogger(__name__)

# 线程安全：避免多线程同时写 DB
_file_lock = threading.Lock()

# 递归保护：从 logging handler 中调用本模块时，失败不得再触发 logging，防止无限递归
_persisting_flag = threading.local()

# 单条日志最大长度（超过截断，避免 MEDIUMTEXT 撑爆）
_MAX_LOG_LINE = 64 * 1024

# 最终兜底：logging + stderr 都失败时的内存缓冲区（无 IO，不会失败）
_silent_errors: list = []

# ★ DDL 迁移标记（仅首次连接执行）
_run_logs_columns_ensured = False


def _get_project():
    """读取当前 project_name（统一来源，消除双重上下文不一致）。

    优先级：state._current_project()（与 dashboard 同源）→
            config.project_name（ConfigRegistry 回退）→
            空字符串。
    ★ 修复(2026-08-14): 原仅使用 config.project_name，与 dashboard 的
      _current_project() 可能不一致。
    """
    try:
        from src.backend.state import _current_project

        proj = _current_project()
        if proj:
            return proj
    except Exception as _e:
        _log.debug("[Log] _current_project 读取失败: %s", _e, exc_info=True)

    try:
        from src.core.config_loader import config

        return getattr(config, "project_name", "") or ""
    except Exception as _e:
        _log.debug("[Log] project_name 读取失败: %s", _e, exc_info=True)
        return ""


def _get_version_id():
    """读取当前 version_id（统一来源）。

    ★ 新增(2026-08-14): run_logs 补版本隔离。
    """
    try:
        from src.core.versioning import current_version_id

        project = _get_project()
        return current_version_id(project) if project else 0
    except Exception as _e:
        _log.debug("[Log] version_id 读取失败: %s", _e, exc_info=True)
        return 0


def _normalize_run_id(run_id: str, project: str, version_label: str = "") -> str:
    """纠正 run_id，使其归属当前项目，杜绝跨项目日志残留（★ 根因修复 2026-08-30）。

    背景：log_capture 在 SSE 路由持久化时，可能选中陈旧/跨项目的 run_id（例如上一个
    项目的订阅未清理），导致写入行形如 ``run_id=hospital_sut__v1_0, project=SmartShop``
    —— 两条字段自相矛盾，进而污染文档文件树的"执行日志"桶与日志列表（切换项目后出现
    其它项目的 run_id）。

    修复：run_id 标准格式为 ``{project}__{version_label}``。当传入 run_id 的项目前缀
    与当前 project 不一致时，将其修正为以当前 project 为前缀、保留版本后缀，把持久化
    边界统一到"run_id 的 project 前缀 == project 字段"这一不变量上。
    """
    if not project:
        return str(run_id or "")
    seg = str(run_id or "").split("__", 1)
    if seg and seg[0] == project:
        return str(run_id)
    suffix = seg[1] if (len(seg) == 2 and seg[1]) else (version_label or "unversioned")
    return "%s__%s" % (project, suffix)


def _ensure_run_logs_columns(conn) -> None:
    """确保 run_logs 表有 version_id 列（DDL 迁移）。"""
    global _run_logs_columns_ensured
    if _run_logs_columns_ensured:
        return
    with guard("_ensure_run_logs_columns: conn.execute( 'ALTER TABLE run_logs ", log=_log):
        conn.execute(
            "ALTER TABLE run_logs ADD COLUMN version_id INT NOT NULL DEFAULT 0 "
            "COMMENT '版本ID → project_versions.id' AFTER project"
        )
        _log.info("[Log] run_logs.version_id 列已添加")
    _run_logs_columns_ensured = True


def _parse_step_label(step_label):
    """从 'step1' / 'step_3' / 'Step5: xxx' 等标签中解析 step_num（整数）。"""
    try:
        import re

        m = re.search(r"(\d+)", str(step_label or ""))
        if m:
            return int(m.group(1))
    except Exception:
        _log.debug("[Log] _parse_step_label 失败: %s", step_label, exc_info=True)
    return 0


def begin_run_log(run_id: str, step_label: str) -> dict:
    """启动一个 run 的日志（MySQL run_logs 表）。
    project_root 参数已移除（全走 MySQL，不再依赖磁盘路径）。
    ★ fix: 返回 {"run_id": ..., "step_label": ...} dict，供 finish_run_log / append_run_log 使用。
    """
    conn = None
    run_id = str(run_id)  # 保证后续异常分支也能绑定
    try:
        from src.core.db_manager import get_connection

        project = _get_project()
        version_id = _get_version_id()
        step_num = _parse_step_label(step_label)
        # ★ 根因修复：run_id 必须归属当前项目，防止跨项目残留
        try:
            from src.core.versioning import current_version_label
        except Exception:
            current_version_label = None
        run_id = _normalize_run_id(
            str(run_id), project, current_version_label(project) if current_version_label else ""
        )
        conn = get_connection()
        _ensure_run_logs_columns(conn)
        conn.execute(
            "INSERT INTO run_logs (run_id, project, version_id, step_num, log_level, message, created_at) "
            "VALUES (%s, %s, %s, %s, 'INFO', %s, NOW())",
            (
                run_id,
                str(project),
                int(version_id),
                int(step_num),
                "[RUN_START] step=%s time=%s"
                % (step_label, datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
            ),
        )
        conn.commit()
        _log.info(
            "[Log] 日志 run 已在 MySQL 登记: run_id=%s step=%s", run_id, step_label
        )
    except Exception as e:
        _log.warning("[Log] begin_run_log MySQL 写入失败: %s", e)
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                _log.debug("[Log] begin_run_log close 失败", exc_info=True)
    return {"run_id": str(run_id), "step_label": step_label}


def append_run_log(file_path: dict, line_text: str):
    """追加一行日志到 MySQL run_logs 表。
    file_path: begin_run_log 返回的 dict，含 run_id 和 step_label。"""
    if not line_text:
        return

    # 递归保护：防止 logging handler 调用本函数失败后再触发 logging，形成死循环
    try:
        if getattr(_persisting_flag, "value", False):
            return
        _persisting_flag.value = True
    except Exception as _re:
        sys.stderr.write("[Log] 递归保护失败: %s\n" % str(_re)[:80])
        return

    try:
        run_id = str(file_path.get("run_id", ""))
        step_num = _parse_step_label(file_path.get("step_label", ""))
        project = _get_project()
        # ★ 根因修复：run_id 必须归属当前项目，并同步纠正调用方持有的 dict，
        #   保证同一 run 的后续行继续使用纠正后的 run_id，杜绝跨项目日志残留。
        try:
            from src.core.versioning import current_version_label
        except Exception:
            current_version_label = None
        run_id = _normalize_run_id(
            run_id,
            project,
            (current_version_label(project) if project and current_version_label else ""),
        )
        if file_path is not None and isinstance(file_path, dict):
            file_path["run_id"] = run_id

        # ★ 根因修复(2026-09-23): 工作线程（如 AI 抽取线程池）内 _get_project() 取不到
        #   当前项目（线程上下文/请求态缺失）→ project='' → run_logs 外键
        #   fk_run_logs_project(→ projects.name) 1452 失败 → 诊断日志静默丢失
        #   （实测：Step2 并发 AI 抽取期间大量 "append_run_log MySQL 写入失败"）。
        #   根治：run_id 已归一为 "{project}__{version}"，据此回填 project——
        #   日志与 run_id 自洽，不依赖线程上下文（自愈，零业务硬编码）。
        if not project or project == "None":
            _p_from_run = run_id.split("__", 1)[0] if "__" in run_id else ""
            if _p_from_run:
                project = _p_from_run

        # 限长保护
        if len(line_text) > _MAX_LOG_LINE:
            line_text = line_text[:_MAX_LOG_LINE] + "\n...[TRUNCATED]"

        conn = None
        with _file_lock:
            try:
                from src.core.db_manager import get_connection

                version_id = _get_version_id()
                conn = get_connection()
                _ensure_run_logs_columns(conn)
                conn.execute(
                    "INSERT INTO run_logs (run_id, project, version_id, step_num, log_level, message, created_at) "
                    "VALUES (%s, %s, %s, %s, 'INFO', %s, NOW())",
                    (run_id, project, int(version_id), int(step_num), line_text),
                )
                conn.commit()
            except Exception as e:
                # 日志层自身失败只在 stderr 提示，禁止调用 _log，避免从 logging handler 触发无限递归
                try:
                    sys.stderr.write(
                        f"[Log] append_run_log MySQL 写入失败: {str(e)[:120]}\n"
                    )
                except Exception as _fe:
                    # 最终兜底：stderr 也失败时记录到内存缓冲区（无 IO，不会失败）
                    _silent_errors.append("[Log] stderr 也失败: %s" % str(_fe)[:80])
            finally:
                if conn:
                    try:
                        conn.close()
                    except Exception as _close_e:
                        sys.stderr.write(
                            f"[Log] append_run_log close 失败: {str(_close_e)[:120]}\n"
                        )
    finally:
        try:
            _persisting_flag.value = False
        except Exception as _flag_e:
            sys.stderr.write(f"[Log] 递归保护标志重置失败: {str(_flag_e)[:120]}\n")


def finish_run_log(run_id: str, file_path: dict, status: str, summary: str):
    """运行结束：写入最后一条 run_logs。"""
    conn = None
    try:
        from src.core.db_manager import get_connection

        project = _get_project()
        version_id = _get_version_id()
        # ★ 根因修复：run_id 归属当前项目
        try:
            from src.core.versioning import current_version_label
        except Exception:
            current_version_label = None
        fin_run_id = _normalize_run_id(
            str(run_id),
            project,
            (current_version_label(project) if project and current_version_label else ""),
        )
        # ★ 空值保护：begin_run_log 失败时返回 None，file_path 可能为 None
        if not file_path or not isinstance(file_path, dict):
            _log.warning(
                "[Log] finish_run_log 跳过: file_path 为空（begin_run_log 可能失败）"
            )
            return
        step_num = _parse_step_label(file_path.get("step_label", ""))
        message = "[RUN_END] status=%s summary=%s time=%s" % (
            status,
            (summary or "")[:200],
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        )
        conn = get_connection()
        _ensure_run_logs_columns(conn)
        conn.execute(
            "INSERT INTO run_logs (run_id, project, version_id, step_num, log_level, message, created_at) "
            "VALUES (%s, %s, %s, %s, 'INFO', %s, NOW())",
            (fin_run_id, str(project), int(version_id), int(step_num), message),
        )
        conn.commit()
        _log.info("[Log] run 结束: run_id=%s status=%s", fin_run_id, status)
    except Exception as e:
        _log.warning("[Log] finish_run_log MySQL 写入失败: %s", e)
    finally:
        if conn:
            try:
                conn.close()
            except Exception as _pass_e:
                _log.warning("非关键异常: %s", _pass_e, exc_info=True)
    # 保留最新 20 个 run 的日志 + 清理 6 个月前的截图/日志
    from src.backend.cleanup_manager import cleanup_manager

    cleanup_manager.cleanup_all(keep_runs=20)
