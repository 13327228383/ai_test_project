# -*- coding: utf-8 -*-
from __future__ import annotations

"""
双 Excel 状态管理模块 (ExcelHandler)
=====================================
统一处理需求拆解表 (requirements_breakdown.xlsx) 和
测试用例表 (Validation_Intent-*.xlsx) 的读写、过滤及状态回写。

强制数据结构：
- requirements_breakdown.xlsx: req_id, module, description, is_generated, dependency_req_id
- Validation_Intent-*.xlsx:            case_id, title, case_actions_json, expected_outcome, verification_status 等

核心设计：
1. 使用 openpyxl 库操作 .xlsx 文件，支持创建新文件和追加写入。
2. 所有列名与强制规范严格一致，防止下游模块取值错误。
3. 提供按 is_generated / verification_status 状态过滤的便捷方法。
"""

import json, re
from pathlib import Path
from typing import Any
import logging

_log = logging.getLogger(__name__)

# ★ 安全修复：Excel 文件大小上限（50MB），超过则拒绝加载防止 OOM
_MAX_EXCEL_SIZE = 50 * 1024 * 1024


def _check_file_size(path: Path) -> None:
    """检查 Excel 文件大小，超限则抛异常。"""
    if path.is_file() and path.stat().st_size > _MAX_EXCEL_SIZE:
        raise MemoryError(
            "Excel 文件过大（%.1f MB > %d MB），拒绝加载"
            % (path.stat().st_size / 1024 / 1024, _MAX_EXCEL_SIZE / 1024 / 1024)
        )


from src.core.config_loader import config


class ExcelHandler:
    """Excel 处理器。"""

    REQ_COLUMNS = [
        ("req_id", "需求编号", 12),
        ("module", "功能模块", 20),
        ("description", "需求描述", 60),
        ("is_generated", "是否已生成测试用例", 16),
        ("dependency_req_id", "依赖需求编号", 16),
        ("review_status", "审核状态", 14),
    ]

    INTENT_COLUMNS = [
        ("catalog_level", "目录层级", 10),
        ("catalog_name", "目录名称", 20),
        ("case_id", "意图ID", 14),
        ("title", "意图标题", 30),
        ("precondition", "预置条件", 40),
        ("case_actions_json", "意图动作", 55),
        ("expected_outcome", "预期结果", 40),
        ("case_type", "意图类型", 12),
        ("risk_level", "风险等级", 10),
        ("auto_flag", "自动化标记", 12),
        ("threshold_flag", "门槛标记", 10),
        ("author", "意图建模作者", 12),
        ("created_date", "创建时间", 14),
        ("verification_status", "意图验证结果", 14),
        ("verification_time", "执行时间", 14),
        # 放在末尾，保证旧 Excel 按位置读取时不发生列位移。
        ("req_id", "需求编号", 14),
    ]

    def __init__(self) -> None:
        """【MySQL 化改造】初始化处理器：

        - 不再依赖 `ensure_dirs()` 预创建目录。
        - `_req_path` / `_case_asset_path` 仅用于：
          * 显式「Excel 导入」场景（由调用方传入路径）
          * case_asset_file 元数据标识（写入 MySQL 时记录文件来源）
        - 运行时读取需求拆解 / 测试用例一律走 MySQL。
        """
        root = config.project_root
        from src.core.paths import ProjectPaths

        _pp = ProjectPaths(root)
        req_rel = config.requirements_excel_path
        # 仅计算路径（不做任何文件操作），供显式导入/导出场景使用
        self._req_path = root / req_rel if req_rel else _pp.process_requirements_xlsx
        intent_rel = config.test_cases_excel_path
        # 默认测试用例路径：仅做元数据记录用，运行时列表一律查 MySQL test_cases
        if intent_rel:
            self._case_asset_path = root / intent_rel
        else:
            self._case_asset_path = None

    def set_case_asset_path(self, path: Path) -> None:
        """更新当前测试用例元数据的「来源文件」标识。

        仅在显式导入 Excel 测试用例时由调用方传入；不会再扫描磁盘或落盘。
        """
        self._case_asset_path = Path(path) if path else None
        _log.info(
            "[ExcelHandler] 测试用例元数据来源已切换: %s",
            self._case_asset_path.name if self._case_asset_path else "<MySQL only>",
        )

    def reload_paths(self) -> None:
        """项目切换后重新绑定路径（保留方法签名以兼容旧调用方）。

        运行时不再扫描磁盘；显式 Excel 路径仅做元数据标识。
        """
        root = config.project_root
        from src.core.paths import ProjectPaths

        _pp = ProjectPaths(root)
        req_rel = config.requirements_excel_path
        self._req_path = root / req_rel if req_rel else _pp.process_requirements_xlsx
        intent_rel = config.test_cases_excel_path
        if intent_rel:
            self._case_asset_path = root / intent_rel
        else:
            self._case_asset_path = None

    @staticmethod
    def list_case_asset_files(root: Path = None) -> list:
        """【MySQL 化】返回项目各版本的意图分组（供 Step4 配置弹窗按版本选择执行范围）。

        返回 [{version_label, version_id, intent_count, is_active, latest}, ...]
        按版本创建时间倒序。
        """
        try:
            from src.core.config_loader import config as _cfg

            project = getattr(_cfg, "project_name", "") or ""
            current_ver_label = str(getattr(_cfg, "project_version", "") or "").strip()
        except Exception:
            project = ""
            current_ver_label = ""

        if not project:
            return []

        conn = None
        try:
            from src.core.db_manager import get_connection

            conn = get_connection()
            # 按版本分组统计意图数（排除 retired）
            rows = conn.execute(
                "SELECT v.version_label, v.id AS version_id, "
                "COUNT(CASE WHEN vi.valid_status<>'retired' THEN 1 END) AS intent_count, "
                "COUNT(CASE WHEN vi.valid_status='stale' THEN 1 END) AS stale_count "
                "FROM versions v "
                "LEFT JOIN test_cases vi ON vi.project=%s AND vi.version_id=v.id "
                "WHERE v.project_name=%s "
                "GROUP BY v.id, v.version_label "
                "ORDER BY v.created_at DESC",
                (project, project),
            ).fetchall()
            result = []
            for r in rows or []:
                label = str(r.get("version_label", "") or "").strip()
                vid = int(r.get("version_id") or 0)
                count = int(r.get("intent_count") or 0)
                stale = int(r.get("stale_count") or 0)
                result.append(
                    {
                        "version_label": label or f"#{vid}",
                        "version_id": vid,
                        "intent_count": count,
                        "stale_count": stale,
                        "is_active": label == current_ver_label,
                        "latest": label == current_ver_label,
                    }
                )
            return result
        except Exception as e:
            _log.info(
                "[ExcelHandler] list_case_asset_files 版本分组查询失败: %s", str(e)[:80]
            )
            return []
        finally:
            if conn:
                try:
                    conn.close()
                except Exception as _e:
                    _log.debug("conn.close + close(): %s", _e, exc_info=True)

    @staticmethod
    def _default_validation_case_asset_path(root: Path) -> Path:
        """【保留但不再扫描磁盘】返回一个仅用于元数据显示的 Path。

        历史上此方法扫描 process_intents_dir 下的 Validation_Intent*.xlsx。
        当前 MySQL 化后一律查 test_cases 表，此方法仅保留签名以兼容调用方，
        返回指向默认目录的虚拟 Path（不创建文件）。
        """
        from src.core.paths import ProjectPaths

        return ProjectPaths(root).process_intents_dir / "Validation_Intent_MySQL.xlsx"

    # =========================================================================
    # Excel 导入辅助方法（仅在显式调用时使用，不参与主链路）
    # =========================================================================

    @staticmethod
    def _create_sheet(wb, sheet_name, columns, data):
        """仅在 Excel 导入/导出时使用。主链路运行时一律走 MySQL。"""
        from src.core import excel_utils as _u

        return _u._create_sheet(wb, sheet_name, columns, data)

    @staticmethod
    def _write_data_rows(ws, data, start_row, columns):
        from src.core import excel_utils as _u

        return _u._write_data_rows(ws, data, start_row, columns)

    @staticmethod
    def _apply_status_formatting(ws, columns, start_row, end_row):
        from src.core import excel_utils as _u

        return _u._apply_status_formatting(ws, columns, start_row, end_row)

    @staticmethod
    def _read_sheet(wb, sheet_name, columns):
        from src.core import excel_utils as _u

        return _u._read_sheet(wb, sheet_name, columns)

    @staticmethod
    def _ensure_columns(ws, columns):
        """兼容旧表：缺少新增列时追加表头到末尾，不重排列。

        仅在显式导入 Excel 时使用；运行时 MySQL 表结构由 schema.sql 管理。
        """
        from src.core.excel_io_utils import ensure_columns

        return ensure_columns(ws, columns)

    # =========================================================================
    # 归一化方法
    # =========================================================================

    def _lookup_module(self, ref_req_id: str) -> str:
        from src.core.excel_normalizer import lookup_module

        if not hasattr(self, "_module_cache") or self._module_cache is None:
            self._module_cache = {}
        return lookup_module(ref_req_id, self.read_requirements, self._module_cache)

    # 归一化方法（委托到 case_normalizer 模块）
    @staticmethod
    def _extract_fields_from_json(case_actions_json):
        from src.core import case_normalizer as _n

        return _n._extract_fields_from_json(case_actions_json)

    @staticmethod
    def _normalize_title(title: str) -> str:
        from src.core import case_normalizer as _n

        return _n._normalize_title(title)

    @staticmethod
    def _normalize_case_type(raw_type: str) -> str:
        from src.core import case_normalizer as _n

        return _n._normalize_case_type(raw_type)

    @staticmethod
    def _extract_feature(feature, title, module):
        from src.core import case_normalizer as _n

        return _n._extract_feature(feature, title, module)

    @staticmethod
    def _enforce_expected_outcome(expected, case_type, title, steps=""):
        from src.core import case_normalizer as _n

        return _n._enforce_expected_outcome(expected, case_type, title, steps)

    @staticmethod
    def _normalize_expected_numbering(expected, steps):
        from src.core import case_normalizer as _n

        return _n._normalize_expected_numbering(expected, steps)

    @staticmethod
    def _clean_steps(case_actions_json):
        from src.core import case_normalizer as _n

        return _n._clean_steps(case_actions_json)

    @staticmethod
    def _normalize_intent_for_write(intent_data):
        from src.core.excel_normalizer import normalize_intent_for_write

        return normalize_intent_for_write(intent_data)

    # =========================================================================
    # 需求拆解表 I/O
    # =========================================================================

    @staticmethod
    def write_requirements(requirements):
        from src.core.excel_requirements import write_requirements

        return write_requirements(requirements)

    def _read_requirements_from_excel(self):
        """【导入源专用】仅用于显式从 Excel 导入源读取，不作为运行时事实源。"""
        from src.core.excel_requirements import read_requirements_from_excel

        return read_requirements_from_excel(
            self._req_path, self._read_sheet, self.REQ_COLUMNS
        )

    @staticmethod
    def read_requirements():
        """★ MySQL-only：从 requirements 表读取。Excel 仅作导入源导出物，不再参与主运行链路兜底。"""
        from src.core.excel_requirements import read_requirements

        return read_requirements()

    def get_ungenerated_requirements(self):
        from src.core.excel_requirements import get_ungenerated_requirements

        return get_ungenerated_requirements(self.read_requirements)

    def reset_all_is_generated(self):
        """★ MySQL-only：重置当前项目全部需求的 is_generated，不再操作 Excel。"""
        from src.core.excel_requirements import reset_all_is_generated

        return reset_all_is_generated()

    def update_is_generated(self, req_id, value=True):
        """★ MySQL-only：更新单条需求的 is_generated，不再操作 Excel。"""
        from src.core.excel_requirements import update_is_generated

        return update_is_generated(req_id, value)

    def _req_col_index(self, attr_name: str) -> int:
        """根据属性名查找 REQ_COLUMNS 中的列索引。"""
        from src.core.excel_requirements import req_col_index

        return req_col_index(attr_name, self.REQ_COLUMNS)

    def mark_requirement_intents_modeled(self, req_id):
        """★ MySQL-only：标记需求已生成测试用例，不再操作 Excel。"""
        from src.core.excel_requirements import mark_requirement_intents_modeled

        return mark_requirement_intents_modeled(req_id)

    # ★ v5：需求审核闸门
    def update_review_status(self, req_id: str, status: str) -> bool:
        """★ MySQL-only：更新单条需求的审核状态，不再操作 Excel。"""
        from src.core.excel_requirements import update_review_status

        return update_review_status(req_id, status)

    def update_requirement(self, req_id: str, updates: dict) -> bool:
        """★ MySQL-only：修改需求条目的字段值，不再操作 Excel。"""
        from src.core.excel_requirements import update_requirement

        return update_requirement(req_id, updates)

    def delete_requirement(self, req_id: str) -> bool:
        """★ MySQL-only：从 requirements 表删除一条需求，不再操作 Excel。"""
        from src.core.excel_requirements import delete_requirement

        return delete_requirement(req_id)

    def add_requirement(self, data: dict) -> bool:
        """★ MySQL-only：向 requirements 表追加需求，不再操作 Excel。"""
        from src.core.excel_requirements import add_requirement

        return add_requirement(data)

    # =========================================================================
    # 测试用例表 I/O
    # =========================================================================

    def write_test_cases(self, test_cases):
        """★ MySQL-only：全量写入 test_cases 表，不再生成 Excel。"""
        from src.core.excel_cases import write_test_cases

        return write_test_cases(test_cases, self._normalize_intent_for_write)

    def init_test_cases_file(self):
        """★ MySQL-only：清空当前项目 test_cases/case_requirement_mapping，不再创建空 Excel。"""
        from src.core.excel_cases import init_test_cases_file

        return init_test_cases_file()

    def append_intents(self, intents):
        """★ MySQL-only：追加测试用例到 test_cases 表，不再操作 Excel。"""
        from src.core.excel_cases import append_intents

        return append_intents(intents, self._normalize_intent_for_write)

    def parse_and_write_intents(self, req_id, ai_text, ai_meta=None):
        """解析 AI 返回的 JSON 并写入 MySQL test_cases。Excel 仅通过导出命令生成。"""
        from src.core.excel_cases import parse_and_write_intents

        return parse_and_write_intents(
            req_id, ai_text, ai_meta, write_meta_fn=self._write_case_meta
        )

    def read_test_cases(self):
        """★ MySQL-only：从 test_cases 表读取，不再读 Excel。"""
        from src.core.excel_cases import read_test_cases

        return read_test_cases()

    # ── MySQL 元数据读写 ──

    def _get_case_db(self):
        from src.core.db_manager import get_connection

        return get_connection()

    def _write_case_meta(
        self,
        case_id: str,
        confidence: int,
        risk_level: str,
        case_metrics: dict[str, Any] | None = None,
        risk_embedding: list | None = None,
        method_valid: int | None = None,
        method_fix_note: str = "",
    ) -> bool:
        """通过 review_store 写入测试用例元数据（置信度/风险等级/意图指标）。

        ★ T2 流水线强化专项(2026-08-24): 透传 method_valid / method_fix_note
          （Step3 方法论白名单校验结果持久化到 case_meta 两列；缺省按通过处理）。
        """
        from src.core.excel_cases import write_case_meta

        return write_case_meta(
            case_id,
            confidence,
            risk_level,
            self._case_asset_path,
            case_metrics,
            risk_embedding=risk_embedding,
            method_valid=method_valid,
            method_fix_note=method_fix_note,
        )

    def delete_test_case(self, case_id: str) -> bool:
        """从 test_cases/case_requirement_mapping/case_meta 删除测试用例。"""
        from src.core.excel_cases import delete_test_case

        return delete_test_case(case_id)

    def update_test_case(self, case_id: str, updates: dict) -> bool:
        """★ MySQL-only：更新 test_cases 表字段 + 通过 review_store 标记已修改，不再 raw SQL。"""
        from src.core.excel_cases import update_test_case

        return update_test_case(case_id, updates)

    def read_test_cases_with_meta(self) -> list:
        """★ MySQL-only：从 MySQL 读测试用例+元数据（置信度/审核状态等），Excel 仅作导出物。"""
        from src.core.excel_cases import read_test_cases_with_meta

        return read_test_cases_with_meta()

    def get_intents_by_ids(self, case_ids: list):
        """按指定 case_id 列表获取测试用例。"""
        from src.core.excel_cases import get_intents_by_ids

        return get_intents_by_ids(case_ids, self.read_test_cases)

    def get_intents_by_indices(self, indices: list):
        """按数组下标获取测试用例（精确定位到行，不依赖 case_id）。
        前端 search 列表和此 read_test_cases() 的顺序一致。
        """
        from src.core.excel_cases import get_intents_by_indices

        return get_intents_by_indices(indices, self.read_test_cases)

    def _col_index(self, attr_name: str) -> int:
        """根据属性名查找 INTENT_COLUMNS 中的列索引（0-based），避免硬编码。"""
        from src.core.excel_cases import col_index

        return col_index(attr_name, self.INTENT_COLUMNS)

    def update_verification_status(
        self, case_id, status, screenshot_path="", failure_reason=""
    ):
        """★ MySQL-only：写入执行记录到 executions 表（按用户隔离），不再操作 Excel。"""
        from src.core.excel_cases import update_verification_status

        return update_verification_status(
            case_id,
            status,
            screenshot_path,
            failure_reason,
            self._case_asset_path,
            self._get_case_db,
        )

    @staticmethod
    def mark_flaky(case_id: str):
        """★ P1-4: 标记测试用例为 Flaky（写入 MySQL）— 委托到 failure_analyzer 模块。"""
        from src.core import failure_analyzer as _fa

        return _fa.mark_flaky(case_id)

    @staticmethod
    def _rag_failure_analysis(case_id, title, failure_detail=""):
        """★ v6: RAG + AI 智能失败原因分析 — 委托到 failure_analyzer 模块。"""
        from src.core import failure_analyzer as _fa

        return _fa._rag_failure_analysis(case_id, title, failure_detail)

    @staticmethod
    def _auto_failure_analysis(case_id, title, failure_detail=""):
        """失败原因自动分析入口 — 委托到 failure_analyzer 模块。"""
        from src.core import failure_analyzer as _fa

        return _fa._auto_failure_analysis(case_id, title, failure_detail)

    def _save_workbook_with_retry(self, wb, save_path=None, max_retries: int = 3):
        """仅用于显式 Excel 导出时的保存。主链路已不再生成 Excel。"""
        from src.core.excel_io_utils import save_workbook_with_retry

        if not save_path and not self._case_asset_path:
            _log.error(
                "[ExcelHandler] _save_workbook_with_retry: 未提供 save_path，跳过写入"
            )
            return
        target = save_path or self._case_asset_path
        return save_workbook_with_retry(wb, target, max_retries)

    def get_requirements_by_ids(self, req_ids):
        all_reqs = self.read_requirements()
        return [r for r in all_reqs if r.get("req_id") in req_ids]

    # =========================================================================
    # 用户级执行记录（MySQL executions 表，按用户隔离）
    # =========================================================================

    def get_unexecuted_cases(self, user_id=None):
        """【MySQL-only】获取当前用户尚未执行的测试用例。

        实现逻辑：查询 MySQL `executions` 表，按 (user_id, project, case_asset_file) 过滤，
        取最近一次执行的 status；若不存在或非成功状态则视为未执行。
        """
        all_intents = self.read_test_cases()
        # 过滤目录头行
        all_intents = [c for c in all_intents if c.get("case_id")]
        if not user_id:
            # 无用户上下文时回退到 verification_status 列（MySQL test_cases 表字段）
            return [
                c
                for c in all_intents
                if not c.get("verification_status")
                or str(c.get("verification_status", "")).strip() == ""
            ]
        from src.users.manager import get_executed_rows

        case_asset_file = self._case_asset_path.name if self._case_asset_path else ""
        proj = config.project_name
        executed = get_executed_rows(user_id, proj, case_asset_file)
        return [c for i, c in enumerate(all_intents) if (i + 2) not in executed]

    def get_failed_intents(self, user_id=None):
        """【MySQL-only】获取当前用户失败的测试用例。"""
        from src.pipeline.utils.case_store import case_store

        all_intents = case_store.list_intents(include_virtual_inherited=False)
        all_intents = [c for c in all_intents if c.get("case_id")]
        if not user_id:
            return [
                c
                for c in all_intents
                if str(c.get("verification_status", "")).strip().upper() == "FAIL"
            ]
        from src.users.manager import get_failed_rows

        case_asset_file = self._case_asset_path.name if self._case_asset_path else ""
        proj = config.project_name
        failed = get_failed_rows(user_id, proj, case_asset_file)
        return [c for i, c in enumerate(all_intents) if (i + 2) in failed]


class ExcelHandlerRegistry:
    """★ A3 改造：按 project 隔离的 ExcelHandler 注册表。

    原实现：excel_handler 是全局单例，_req_path / _case_asset_path 在 reload_paths()
    时被覆盖，项目 A 切换会影响项目 B。
    新实现：dict[project] → ExcelHandler，每个项目独立路径状态。
    兼容策略：保留所有实例方法签名，自动从 config.project_name 获取当前项目，
    委托给对应的 ExcelHandler 实例。静态方法直接转发。
    """

    def __init__(self):
        self._handlers: dict[str, ExcelHandler] = {}
        self._lock = __import__("threading").Lock()
        # 保留一个默认实例，用于无 project 上下文的兼容调用
        self._default = ExcelHandler()

    def for_project(self, project: str) -> ExcelHandler:
        """获取指定项目的 ExcelHandler（不存在则创建并 reload_paths）。"""
        if not project:
            return self._default
        with self._lock:
            if project not in self._handlers:
                h = ExcelHandler()
                self._handlers[project] = h
            return self._handlers[project]

    def remove_project(self, project: str):
        """移除指定项目的 ExcelHandler（任务结束后清理）。"""
        with self._lock:
            self._handlers.pop(project, None)

    def _current(self) -> ExcelHandler:
        """获取当前项目的 ExcelHandler（从 config.project_name 推断）。"""
        try:
            project = getattr(config, "project_name", "") or ""
        except Exception:
            project = ""
        return self.for_project(project)

    # ── 兼容方法：委托给当前项目的 ExcelHandler ──
    # 这些方法保持与旧单例完全一致的签名，旧调用方无需改动。

    def set_case_asset_path(self, path):
        self._current().set_case_asset_path(path)

    def reload_paths(self):
        self._current().reload_paths()

    def __getattr__(self, name):
        """★ 对未显式代理的方法/属性，委托给当前项目的 ExcelHandler。
        这确保 43 个引用点的所有方法调用都能正确路由到对应项目的实例。"""
        return getattr(self._current(), name)


# ★ A3 改造：excel_handler 现在是 ExcelHandlerRegistry 实例，
#   保持模块级单例入口不变，但内部按 project 隔离。
excel_handler = ExcelHandlerRegistry()
