# -*- coding: utf-8 -*-
"""
业务辅助函数模块
================
从 app.py 提取：_real_stats, _load_case_asset_files, _load_browsers,
_get_available_priorities, _confirm_figma_fetch, _user_can_access_project,
_validate_local_ollama, _validate_key, _create_config_template,
_build_vision_model_entry。
"""

import json
from src.core.guarded import guard
import ssl
import urllib.request
import urllib.error
import logging
from collections import Counter
from contextlib import contextmanager

_log = logging.getLogger(__name__)

from src.backend.state import _current_project


def _user_can_access_project(user_id: str, project_name: str) -> bool:
    """校验用户是否被授权访问指定项目。

    ★ admin 可访问所有项目（含已归档，供恢复/删除等管理操作）—— 通过 is_admin 短路放行，
      不走 get_user_projects；归档项目的管理由 _archive.py 路由 + _project.py:160-161 单独查询负责。
      非 admin 仅可访问 get_user_projects 返回的 active 项目。
    """
    if not user_id or not project_name:
        return False
    try:
        from src.users.manager import is_admin

        if is_admin(user_id):
            return True
        from src.users.manager import get_user_projects

        return project_name in set(get_user_projects(user_id))
    except Exception as e:
        _log.warning("[WARN] _user_can_access_project 失败: %s", str(e)[:120])
        return False


def _resolve_request_project(req_project: str):
    """解析下载请求的目标项目，admin 可指定任意 active 项目，非 admin 只能指定有权限的项目。

    返回 (project_name, error)：
      - 成功：(project_name, None)
      - 失败：(None, (error_message, status_code))

    用于 /api/docs/smoke-intents、/api/docs/xmind-design 等下载接口，
    让系统管理员无需先切换当前项目即可下载"能看见的项目"。
    """
    from src.core.config_loader import get_current_user

    uid = get_current_user()

    if req_project:
        if not _user_can_access_project(uid, req_project):
            return None, ("无权访问项目 %s" % req_project, 403)
        from src.users.manager import _conn

        _c = _conn()
        try:
            exists = _c.execute(
                "SELECT 1 FROM projects WHERE name=%s AND status='active'",
                (req_project,),
            ).fetchone()
        finally:
            _c.close()
        if not exists:
            return None, ("项目 %s 不存在或已归档" % req_project, 404)
        return req_project, None

    # 未传 project 参数，回退到当前项目
    project_name = _current_project() or ""
    if not project_name:
        return None, ("未选择项目", 400)
    return project_name, None


@contextmanager
def _temporary_project(project_name: str):
    """临时切换全局项目上下文，用完自动恢复。

    用于下载接口：当目标 project 与 _current_project() 不同时，
    临时切换 config + excel_handler，确保 generate_smoke_excel / generate_xmind_design
    内部依赖全局 excel_handler 的逻辑能读到目标项目的数据。

    ⚠️ 线程安全：本系统为单用户测试工具，下载是同步操作，可接受全局切换。
       若未来支持多用户并发，需改为 project 参数透传至生成函数。
    """
    from src.backend.state import _current_project, _set_current_project
    from src.core.config_loader import config as _cfg
    from src.core.excel_handler import excel_handler

    orig = _current_project()
    if not project_name or project_name == orig:
        # 无需切换
        yield project_name or orig
        return

    _set_current_project(project_name)
    try:
        _cfg.reload(project_name)
        excel_handler.reload_paths()
        yield project_name
    finally:
        # 恢复原项目；恢复失败不影响主流程（中间件会在下个请求自动恢复）
        _set_current_project(orig)
        if orig:
            with guard("_temporary_project: _cfg.reload(orig)", log=_log, level=logging.WARNING):
                _cfg.reload(orig)
                excel_handler.reload_paths()


def _real_stats():
    """读取实时统计数据（★ 结构化事实从 MySQL 读取；只统计 Validation_Intent，冒烟测试用例不纳入 KPI）。"""
    if not _current_project():
        return 0, 0, 0, 0, 0, 0, 0.0, {}, 0, 0, 0, {"ui": 0, "api": 0, "db": 0}, {}
    try:
        # ★ 防御性同步：确保 config.project_name 与 _current_project() 一致
        # 避免线程池复用导致 list_requirements/list_intents_with_meta 读到错误项目的数据
        from src.core.config_loader import config as _cfg

        if _cfg.project_name != _current_project():
            _cfg.reload(_current_project())
            from src.core.excel_handler import excel_handler

            excel_handler.reload_paths()

        from src.pipeline.utils.requirement_store import requirement_store
        from src.pipeline.utils.case_store import case_store

        reqs = requirement_store.list_requirements()
        ungen = sum(1 for r in reqs if not r.get("is_generated", False))

        # ★ 只统计正式 Validation_Intent（冒烟测试用例独立于 KPI，不纳入总体指标）
        raw = case_store.list_intents_with_meta()
        all_intents = []
        seen_files = set()
        for c in raw:
            if not c.get("case_id"):
                continue
            cf = str(c.get("case_asset_file") or "").strip().lower()
            ct = str(c.get("case_type") or "").strip().lower()
            if cf.startswith("smoke") or "smoke" in ct:
                # 明确是冒烟测试用例 -> 跳过
                continue
            if cf.startswith("test_case"):
                seen_files.add(str(c.get("case_asset_file")))
            all_intents.append(
                {
                    "case_id": str(c["case_id"]).strip(),
                    "case_type": str(c.get("case_type") or "未知"),
                    "required_tracks": [
                        t for t in (c.get("required_tracks") or [])
                        if t in ("ui", "api", "db")
                    ],
                }
            )
        c_total = len(all_intents)

        # ★ 2026-09-16 合并循环：一次遍历同时统计全局轨道 + 类型分布 + 类型×轨道分组
        track_counts = {"ui": 0, "api": 0, "db": 0}
        types = Counter()
        type_track_breakdown: dict[str, dict[str, int]] = {}
        for c in all_intents:
            ct = c.get("case_type", "未知")
            types[ct] += 1
            # 初始化该 type 的轨道槽
            if ct not in type_track_breakdown:
                type_track_breakdown[ct] = {"count": 0, "ui": 0, "api": 0, "db": 0}
            type_track_breakdown[ct]["count"] += 1
            for t in set(c.get("required_tracks", [])):
                if t in track_counts:
                    track_counts[t] += 1
                    type_track_breakdown[ct][t] += 1

        # ★ 口径一致性修复(2026-10-14)：执行结果以**实时 executions** 为准，
        #   run_summary 仅作兜底。根因：原实现**优先**读 run_summary，而 run_summary
        #   是 Step5 在某一时刻写入的快照 —— 同一 run 的 executions 之后仍会继续增长
        #   （实测 fullcov_sut：run_summary id=11 写于 10-12 17:06、记 396 例/275 通过
        #   /114 失败，同一 run 的 executions 却一直写到 23:00、最终 460 例/327 通过）
        #   → 面板长期显示陈旧快照（70.70%），与数据库真相（71.1%）背离。
        #   兄弟实现 `routes/kpi/_aggregator.py:87-93` 早已按"executions 覆盖陈旧
        #   run_summary"修好 —— 本处补齐，消除同一 KPI 的两份实现。
        passed, failed = 0, 0
        try:
            from src.users.manager import get_execution_status_map

            all_cids = [c["case_id"] for c in all_intents]
            status_map = get_execution_status_map(all_cids, _current_project())
            passed = sum(1 for s in status_map.values() if s == "PASS")
            failed = sum(1 for s in status_map.values() if s == "FAIL")
        except Exception:
            _log.debug("[KPI] executions 实时聚合失败", exc_info=True)

        if passed + failed == 0:
            # 兜底：executions 无可用行（如 case_id 未对齐）时回退 Step5 写入的权威合计，
            # 避免"流水线完成但 KPI=0"（保留原设计意图，仅降级为次优先）。
            try:
                from src.core.versioning import current_version_id
                from src.core.db_manager import get_connection, release_connection

                active_version_id = current_version_id(_current_project())
                conn = None
                try:
                    conn = get_connection()
                    row = conn.execute(
                        "SELECT total_passed, total_failed FROM run_summary "
                        "WHERE project=%s AND version_id=%s "
                        "ORDER BY created_at DESC LIMIT 1",
                        (_current_project(), str(active_version_id)),
                    ).fetchone()
                    if row:
                        passed = int(row["total_passed"] or 0)
                        failed = int(row["total_failed"] or 0)
                finally:
                    if conn:
                        release_connection(conn)
            except Exception:
                _log.debug("[KPI] run_summary 兜底查询失败", exc_info=True)
        unexec = c_total - passed - failed
        e = passed + failed
        rate = (passed / max(e, 1) * 100) if e > 0 else 0

        # ★ 测试用例版本数（只统计 Validation_Intent，与 KPI 口径一致）
        intent_versions = len(seen_files)

        # ★ RAG 索引统计
        rag_chunks = 0
        rag_current_version_chunks = 0
        try:
            from src.extensions.rag_engine import rag_engine

            rag_stats = rag_engine._get_chunk_stats_by_type()
            rag_chunks = rag_stats.get("total", 0)
            rag_current_version_chunks = rag_stats.get("current_version_chunks", 0)
        except Exception:
            _log.debug("[KPI] RAG chunk 查询失败", exc_info=True)

        return (
            len(reqs),
            c_total,
            passed,
            failed,
            unexec,
            ungen,
            rate,
            types,
            intent_versions,
            rag_chunks,
            rag_current_version_chunks,
            track_counts,
            type_track_breakdown,
        )
    except Exception as e:
        _log.warning("[WARN] _real_stats 失败: %s", e)
        return 0, 0, 0, 0, 0, 0, 0.0, {}, 0, 0, 0, {"ui": 0, "api": 0, "db": 0}, {}


def _load_case_asset_files():
    """返回测试用例文件列表（★ MySQL test_cases 表，不再扫磁盘）。"""
    try:
        from src.core.excel_handler import excel_handler

        return excel_handler.list_case_asset_files()
    except Exception as e:
        _log.warning("[WARN] _load_case_asset_files 失败: %s", e)
        return []


def _load_browsers():
    """检测本地安装的浏览器列表。"""
    try:
        from src.ai.browser_detector import get_installed_browsers

        return get_installed_browsers()
    except Exception as e:
        _log.warning("[WARN] _load_browsers 失败: %s", e)
        return []


def _get_available_priorities():
    """读取测试用例等级集合。"""
    try:
        from src.core.excel_handler import excel_handler

        intents = excel_handler.read_test_cases()
        return sorted(
            set(
                str(c.get("risk_level", "")).strip()
                for c in intents
                if str(c.get("risk_level", "")).strip()
            )
        )
    except Exception as e:
        _log.warning("[WARN] _get_available_priorities 失败: %s", e)
        return []


def _confirm_figma_fetch() -> bool:
    """Figma API 拉取前检查配额。配额警告已移至前端弹窗。"""
    try:
        from src.pipeline.steps.step1.mcp.figma_mcp import quota_tracker

        quota_tracker.should_warn_before_fetch()
        return True
    except Exception as e:
        _log.warning("[WARN] _confirm_figma_fetch 失败: %s", e)
        return True


def _validate_local_ollama() -> tuple:
    """校验本地 Ollama 服务是否可用。返回 (ok, error_msg)。"""
    # ★ 从 config 读取 Ollama 地址（支持远程，兜底走 constants.OLLAMA_DEFAULT_API_BASE）
    from src.core.constants import OLLAMA_DEFAULT_API_BASE

    try:
        from src.core.config_loader import config as _cfg

        _ollama_base = str(_cfg.ollama_api_base).rstrip("/")
    except Exception as _e:
        _log.debug("[KPI] ollama_api_base 读取失败，用默认值: %s", _e, exc_info=True)
        _ollama_base = OLLAMA_DEFAULT_API_BASE
    try:
        req = urllib.request.Request(f"{_ollama_base}/api/tags", method="GET")
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        models = data.get("models", [])
        if not models:
            return (
                False,
                f"本地 Ollama 已运行但未安装任何模型，请先执行: ollama pull qwen2.5:7b",
            )
        return True, ""
    except urllib.error.URLError as e:
        return (
            False,
            f"本地 Ollama 服务未运行（{_ollama_base} 无法连接），请先启动 Ollama",
        )
    except Exception as e:
        return False, f"本地 Ollama 检测异常: {str(e)[:60]}"


def _validate_key(key: str, provider: str, base_url: str = "") -> tuple:
    """验证 API Key 是否有效，并在成功时缓存模型列表。返回 (ok, error_msg)。
    base_url 可选：传入时使用自定义端点验证（支持 token 平台代理等场景）。"""
    base_url = (base_url or "").strip().strip("`").strip()
    # ★ 安全修复：SSRF 防护 — 禁止内网地址（黑名单 + IP 类型双重判断走 constants）
    from src.core.constants import is_internal_host

    if base_url:
        from urllib.parse import urlparse

        parsed = urlparse(base_url)
        host = parsed.hostname or ""
        if is_internal_host(host):
            return False, "不允许使用内网地址验证 Key"
        # 使用用户配置的 base_url 验证。
        # 支持两种常见写法：
        #   https://host            -> https://host/v1/models
        #   https://host/v1         -> https://host/v1/models
        # 避免把用户已填写的 /v1 拼成 /v1/v1/models。
        normalized = base_url.rstrip("/")
        url = (
            normalized + "/models"
            if normalized.endswith("/v1")
            else normalized + "/v1/models"
        )
    else:
        urls = {
            "deepseek": "https://api.deepseek.com/v1/models",
            "openai": "https://api.openai.com/v1/models",
            "anthropic": "https://api.anthropic.com/v1/models",
            "nvidia": "https://integrate.api.nvidia.com/v1/models",
        }
        url = urls.get(provider, urls["deepseek"])
    headers = {}
    if provider == "anthropic":
        headers["x-api-key"] = key
        headers["anthropic-version"] = "2023-06-01"
    else:
        headers["Authorization"] = "Bearer " + key
    try:
        req = urllib.request.Request(url, headers=headers, method="GET")
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(req, timeout=8, context=ctx) as resp:
            with guard("_validate_key: body = resp.read().decode('utf-8')", log=_log, level=logging.WARNING):
                body = resp.read().decode("utf-8")
                data = json.loads(body)
                # ★ 缓存已砍：不再写入全局缓存
                _ = data.get("data", [])
        return True, ""
    except urllib.error.HTTPError as e:
        if e.code == 401 or e.code == 403:
            return False, "Key 无效（认证失败）"
        return False, f"API 返回错误 {e.code}"
    except UnicodeEncodeError:
        return (
            False,
            "Key 包含非法字符（仅支持英文、数字和符号），请检查是否多复制了空格或 emoji",
        )
    except Exception as e:
        return False, f"网络错误: {str(e)[:60]}"


def _build_vision_model_entry(name: str, info: dict, selected: bool = False) -> dict:
    """组装单个视觉模型条目。"""
    return {
        "name": name,
        "input_price": info.get("input", 0),
        "output_price": info.get("output", 0),
        "desc": info.get("desc", ""),
        "note": info.get("note", ""),
        "supports_vision": True,
        "selected": selected,
        "est_per_click": round(
            (85 / 1_000_000) * info.get("input", 0)
            + (30 / 1_000_000) * info.get("output", 0),
            6,
        ),
    }


# _bg_dir 已删除（背景图已迁 MySQL 图库，此死函数无调用方）
