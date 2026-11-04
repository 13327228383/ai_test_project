# -*- coding: utf-8 -*-
"""
AiGenerationLogsStore 写入 Mixin
=================================
提供 ``insert_log`` 等 AI 生成日志写入方法。
"""

import json
from src.core.guarded import guard
import logging
from datetime import datetime
from typing import Any

_log = logging.getLogger(__name__)


class AiGenerationLogsStoreWriteMixin:
    """AiGenerationLogsStore 写入能力。"""

    # -----------------------------------------------------------------
    # 版本兜底工具
    # -----------------------------------------------------------------
    @staticmethod
    def _active_version(project: str = "") -> int:
        """当前项目 active version_id（0=legacy）。"""
        from src.core.versioning import current_version_id

        return int(current_version_id(project) or 0)

    def _ensure_logs_version_column(self, c) -> None:
        """确保 ai_generation_logs 存在 version_id 列（存量库运行时 ALTER 兜底）。"""
        with guard("_ensure_logs_version_column: _cols = { r['COLUMN_NAME'] for r in ", log=_log, level=logging.WARNING):
            _cols = {
                r["COLUMN_NAME"]
                for r in c.execute(
                    "SELECT COLUMN_NAME FROM information_schema.columns "
                    "WHERE table_schema = DATABASE() AND table_name = 'ai_generation_logs'"
                ).fetchall()
            }
            if "version_id" not in _cols:
                c.execute(
                    "ALTER TABLE ai_generation_logs "
                    "ADD COLUMN version_id BIGINT NOT NULL DEFAULT 0"
                )
                c.commit()

    # -----------------------------------------------------------------
    # 写
    # -----------------------------------------------------------------
    def log_generation_attempt(
        self,
        requirement: dict[str, Any],
        result: dict[str, Any],
        source: str = "step3_ai_generation",
    ) -> bool:
        """记录一次 AI 生成尝试（成功/失败都落一行）。

        与 ``insert_log``（仅幻觉样本/失败分类路径）不同：本方法在每次 Step3
        AI 生成尝试后调用，确保 ai_generation_logs 有真实生成活动证据。

        参数：
            requirement: 需求字典（含 req_id / module / title / operation）
            result:      AI 生成结果字典（含 success / data / error）
            source:      日志来源标识（默认 step3_ai_generation）
        """
        c = None
        try:
            try:
                from src.core.config_loader import config

                project = getattr(config, "project_name", "") or ""
            except Exception as _e:
                _log.debug("[_write] log_generation_attempt 读取 project 异常: %s", _e)
                project = ""
            now = datetime.now()
            req_id = str(requirement.get("req_id", "") or "")
            module = str(
                requirement.get("module", requirement.get("module_name", "")) or ""
            )
            title = str(
                requirement.get("title", requirement.get("operation", "")) or ""
            )
            # 取生成结果中第一个用例的 case_id 作为代表
            intents = result.get("data", []) if isinstance(result, dict) else []
            if isinstance(intents, dict):
                intents = [intents]
            first_case_id = ""
            if intents:
                first_case_id = str(intents[0].get("case_id", "") or "")
            success = bool(result.get("success")) if isinstance(result, dict) else False
            error = (
                str(result.get("error", "") or "")
                if isinstance(result, dict)
                else ""
            )
            log_id = "%s_%s" % (
                first_case_id or req_id or "unknown",
                now.strftime("%Y%m%d_%H%M%S_%f"),
            )
            c = self._get_db()
            self._ensure_tables(c)
            self._ensure_logs_version_column(c)
            c.execute(
                """
                INSERT INTO ai_generation_logs(
                    project, version_id, log_id, case_id, req_id, module, title,
                    failure_reason, classification, raw_steps_json,
                    source, created_at
                ) VALUES(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    project,
                    self._active_version(project),
                    log_id,
                    first_case_id,
                    req_id,
                    module,
                    title[:500],
                    error[:500],
                    "generation_ok" if success else "generation_failed",
                    self._to_json(intents[:5]),
                    source,
                    now.strftime("%Y-%m-%d %H:%M:%S"),
                ),
            )
            c.commit()
            self._last_error = ""
            return True
        except Exception as e:
            self._last_error = str(e)
            _log.info(
                "  [AiGenLogsStore] ai_generation_logs 生成日志写入失败: %s",
                str(e)[:120],
            )
            return False
        finally:
            if c:
                try:
                    c.close()
                except Exception as _e:
                    _log.debug("[_write] 非关键异常: %s", _e, exc_info=True)

    def insert_log(
        self,
        intent: dict[str, Any],
        failure_info: dict[str, Any],
        source: str = "hallucination_logger",
    ) -> bool:
        """写入一条 AI 生成日志。

        参数：
            intent:       测试用例字典（含 case_id / req_id / module / title / case_actions_json）
            failure_info: 失败信息字典（含 reason / classification）
            source:       日志来源标识
        """
        if not intent:
            return False
        try:
            from src.core.config_loader import config

            project = getattr(config, "project_name", "") or ""
        except Exception as _e:
            _log.debug("[_write] insert_log 异常: %s", _e, exc_info=True)
            project = ""
        try:
            now = datetime.now()
            ts_iso = now.isoformat()
            log_id = "%s_%s" % (
                intent.get("case_id", "unknown") or "unknown",
                now.strftime("%Y%m%d_%H%M%S_%f"),
            )
            c = self._get_db()
            self._ensure_tables(c)
            self._ensure_logs_version_column(c)
            steps_raw = intent.get("steps") or intent.get("case_actions_json") or []
            c.execute(
                """
                REPLACE INTO ai_generation_logs(
                    project, version_id, log_id, case_id, req_id, module, title,
                    failure_reason, classification, raw_steps_json,
                    source, created_at
                ) VALUES(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    project,
                    self._active_version(project),
                    log_id,
                    str(intent.get("case_id", "") or ""),
                    str(intent.get("req_id", "") or ""),
                    str(intent.get("module", "") or ""),
                    str(intent.get("title", "") or ""),
                    str(failure_info.get("reason", "") or ""),
                    str(failure_info.get("classification", "unknown") or "unknown"),
                    self._to_json(steps_raw),
                    source,
                    ts_iso,
                ),
            )
            c.commit()
            # [FIX] c.close() moved to finally below
            self._last_error = ""
            return True
        except Exception as e:
            self._last_error = str(e)
            _log.info(
                "  [AiGenLogsStore] ai_generation_logs 写入失败: %s", str(e)[:120]
            )
            return False

        finally:
            if c:
                try:
                    c.close()
                except Exception as _e:
                    _log.debug("[_write] 非关键异常: %s", _e, exc_info=True)
