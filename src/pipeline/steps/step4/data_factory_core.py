# -*- coding: utf-8 -*-
from __future__ import annotations
from src.core.guarded import guard

"""
数据工厂核心 (DataFactoryCore) — 模板下载、文件上传、工作流编排
===============================================================
数据生成逻辑已拆分至 data_gen.py。
"""

import re, asyncio
from pathlib import Path
from typing import Any
import logging

_log = logging.getLogger(__name__)

from src.core.config_loader import config
from src.core.pipeline_config import pipeline_config


class DataFactoryCore:
    """数据工厂核心：模板/上传/工作流。"""

    # ── 常量 / 配置 ──────────────────────────────────────────
    _TEMPLATE_BUTTON_TEXTS = [
        "下载模板",
        "下载模版",
        "模板下载",
        "模版下载",
        "下载导入模板",
        "下载导入模版",
        "导入模板下载",
        "download template",
        "template download",
        "下载Excel模板",
        "下载CSV模板",
    ]

    _FILE_INPUT_SELECTORS = [
        'input[type="file"]',
        'input[accept*=".xlsx"]',
        'input[accept*=".xls"]',
        'input[accept*=".csv"]',
        '[class*="upload"] input[type="file"]',
        '[class*="import"] input[type="file"]',
    ]

    _UPLOAD_BUTTON_TEXTS = [
        "确认上传",
        "开始导入",
        "上传",
        "导入",
        "提交",
        "确认导入",
        "批量导入",
        "确定",
        "upload",
        "import",
        "submit",
        "confirm",
    ]

    DOWNLOAD_TIMEOUT_MS: int = pipeline_config.data_factory_download_timeout_ms
    PAGE_TEXT_MAX_CHARS: int = pipeline_config.data_factory_page_text_max_chars
    AI_MAX_TOKENS: int = pipeline_config.data_factory_ai_max_tokens
    AI_TEMPERATURE: float = 0.0  # AI 定位元素时温度

    def __init__(self, download_dir=None, work_dir=None) -> None:
        self._page = None
        # ★ A2: 允许构造时显式注入下载/工作目录，便于 per-group 隔离
        # ★ 全 MySQL：默认不创建项目目录，由 attach_page 用 tempfile.mkdtemp() 兜底。
        #   改造3/4 后下载/生成均不落这些目录（下载→NamedTemporaryFile，生成→source_documents）。
        self._download_dir = Path(download_dir) if download_dir else None
        self._work_dir = Path(work_dir) if work_dir else None
        self._group_id = None
        self._ai_brain = None

    def attach_page(self, page) -> "DataFactoryCore":
        self._page = page
        # ★ 全 MySQL：未显式注入目录时用系统 tempfile.mkdtemp()，不污染 data/{project}/。
        if self._download_dir is None or self._work_dir is None:
            import tempfile as _tf

            if self._download_dir is None:
                self._download_dir = Path(_tf.mkdtemp(prefix="df_dl_"))
            if self._work_dir is None:
                self._work_dir = Path(_tf.mkdtemp(prefix="df_wd_"))
        return self

    def set_engine(self, engine) -> "DataFactoryCore":
        """★ 修复连环断链2：serial/parallel 在 engine.start() 前调用 set_engine(engine)，
        但该方法从未定义（git 证初始提交即无），致 AttributeError 直接断 browser 主链路。

        语义：存 engine 引用；若 engine._page 已就绪则同步 attach_page，
        否则仅存引用（execute_import_workflow 入口会按需自绑最新 page，避免 stale page）。
        """
        self._engine = engine
        page = getattr(engine, "_page", None)
        if page is not None:
            self.attach_page(page)
        return self

    def preprocess_steps(self, steps, case_meta=None):
        """★ 修复连环断链3：_base.py:105 调 data_factory.preprocess_steps(steps, case_meta)，
        但该方法从未定义（git 证初始提交即无），致 except 吞为 warning、预处理长期失效。

        语义对齐调用点：返回 None/falsy → _base.py 用原 steps 透传（preprocess 可选）。
        当前无既定预处理规则，返回 None 保留原行为；后续如需模板预取/字段标注在此扩展。
        """
        return None

    def _materialize_to_tempfile(self, mysql_ref: dict[str, Any]) -> Path | None:
        """★ 全 MySQL：从 source_documents(test_data_excel) 读 base64 → 写 tempfile → 返回路径。

        Playwright set_input_files 需 OS 路径，故从 MySQL 物化到系统 tempfile，上传后由调用方删。
        """
        import tempfile, base64, re
        from src.core.db_manager import get_connection, release_connection

        project = mysql_ref.get("project") or config.project_name or "default"
        version_label = mysql_ref.get("version_label")
        if not version_label:
            return None
        conn = None
        try:
            conn = get_connection()
            row = conn.execute(
                "SELECT content FROM source_documents "
                "WHERE project=%s AND doc_type='test_data_excel' AND version_label=%s LIMIT 1",
                (project, version_label),
            ).fetchone()
        except Exception as e:
            _log.warning("[DataFactory] 读取 test_data_excel 失败: %s", e)
            return None
        finally:
            release_connection(conn)
        if not row or not row.get("content"):
            return None
        m = re.search(r"```test-data-excel-b64\n(.+?)\n```", row["content"], re.DOTALL)
        if not m:
            return None
        try:
            excel_bytes = base64.b64decode(m.group(1))
        except Exception as e:
            _log.warning("[DataFactory] test_data_excel base64 解码失败: %s", e)
            return None
        suffix = "_" + (mysql_ref.get("filename") or "generated.xlsx")
        tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
        tmp.write(excel_bytes)
        tmp.close()
        return Path(tmp.name)

    async def upload_file(self, file_ref) -> dict[str, Any]:
        """★ 全 MySQL：file_ref 为 dict(MySQL 标识) 时物化 tempfile 上传后即删；
        为 Path/str 时兼容旧调用（外部文件路径，不删）。"""
        if not self._page:
            return {"success": False, "error": "未绑定 Page"}
        tmp_path = None
        is_temp = False
        if isinstance(file_ref, dict) and file_ref.get("source") == "mysql":
            tmp_path = self._materialize_to_tempfile(file_ref)
            if not tmp_path:
                return {"success": False, "error": "从 MySQL 取测试数据 Excel 失败"}
            is_temp = True
        elif isinstance(file_ref, (str, Path)):
            tmp_path = Path(file_ref)
        else:
            return {"success": False, "error": "无效的文件标识"}

        abs_path = str(tmp_path.resolve())
        used_sel = None
        for sel in self._FILE_INPUT_SELECTORS:
            try:
                locator = self._page.locator(sel).first
                if await locator.count() > 0:
                    await locator.set_input_files(abs_path)
                    used_sel = sel
                    _log.info("  [DataFactory] 文件已上传: %s → %s", tmp_path.name, sel)
                    break
            except Exception as e:
                _log.debug("[DataFactory] 上传选择器 %s 未命中: %s", sel, e)
                continue
        # 上传后删 tempfile（仅 MySQL 物化的临时文件）
        if is_temp:
            with guard("upload_file: tmp_path.unlink()", log=_log):
                tmp_path.unlink()
        if used_sel:
            return {"success": True, "selector_used": used_sel}
        return {"success": False, "error": "未找到文件上传控件"}

    def cleanup(self, keep_files=False) -> None:
        """★ 全 MySQL：_work_dir/_download_dir 均为 tempfile.mkdtemp() 系统目录，
        keep_files=False 时 rmtree 整个目录（改造3/4 后目录内本就无持久化文件）。"""
        import shutil

        for d in (self._work_dir, self._download_dir):
            if keep_files or not d or not d.is_dir():
                continue
            with guard("cleanup: shutil.rmtree(str(d), ignore_errors=", log=_log):
                shutil.rmtree(str(d), ignore_errors=True)


# ★ 修复连环断链1：serial.py/parallel.py 用 `from data_factory_core import DataFactory`，
#   但类名重构为 DataFactoryCore 后未更新调用方 import，致 ImportError 直接断 browser 主链路。
#   保留 DataFactory 别名向后兼容（git 证初始提交类名即 DataFactoryCore，DataFactory 为历史调用残留）。
DataFactory = DataFactoryCore

# ★ 方案C 改造：data_factory 改为 PerProjectRegistry，按 project 隔离
#   原实现：全局单例，_work_dir/_download_dir 跨项目共享导致文件冲突
#   新实现：每个 project 拥有独立的临时目录，彻底隔离文件操作
from src.core.utils.per_project_registry import PerProjectRegistry

data_factory = PerProjectRegistry(DataFactoryCore)
