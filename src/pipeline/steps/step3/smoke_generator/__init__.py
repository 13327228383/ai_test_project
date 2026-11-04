# -*- coding: utf-8 -*-
from __future__ import annotations

"""
冒烟用例 Excel 生成器（MySQL 版）— 包入口
================================================
按功能模块分组，每个模块生成 1 条端到端 Happy Path 冒烟用例，
生成带层级目录的 Excel 文件，base64 编码后写入 MySQL source_documents(doc_type='smoke_excel')。
支持缓存检查：有缓存直接返回，无缓存则 AI 生成。

【2026-06-18 重构】原 571 行单文件已按职责拆分为 4 个子文件：
  prompts.py        — _SMOKE_EXCEL_SYSTEM_PROMPT
  cache.py          — 5 个 MySQL 读写函数（write/get/get_latest/delete/list）
  excel_builder.py  — EXCEL_HEADERS + _sanitize_sheet_name + _build_smoke_excel
  __init__.py       — 主入口 generate_smoke_excel / clear_smoke_cache
外部接口 100% 兼容（5 个公共名 + 5 个私有但被引用的名都在 __init__ re-export）。
"""

import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

# ── re-export：保持调用方 from ...smoke_generator import X 全部能用 ──
from .prompts import _SMOKE_EXCEL_SYSTEM_PROMPT
from .cache import (
    _write_smoke_excel_to_mysql,
    _get_cached_smoke_excel,
    _get_latest_cached_smoke_excel,
    _delete_cached_smoke_excel,
    _load_docs_smoke_excel_files,
)
from .excel_builder import EXCEL_HEADERS, _sanitize_sheet_name, _build_smoke_excel

_log = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════
# 主入口：生成冒烟用例 Excel
# ══════════════════════════════════════════════════════════


def generate_smoke_excel(
    project_name: str = "",
    version_label: str = "",
    force: bool = False,
) -> tuple[bytes | None, str, int]:
    """
    生成冒烟用例 Excel。
    数据源：requirements 表三元组（operation + condition_expr + expected_feedback）。
    返回: (excel_bytes, version_label, case_count)
    - excel_bytes: Excel 二进制数据，失败时返回 None
    - version_label: 版本标签
    - case_count: 生成的用例数
    """
    from src.ai.ai_client import ai_brain
    from src.core.config_loader import config

    if not project_name:
        project_name = getattr(config, "project_name", "") or ""

    # ★ version_label 必须使用 project_versions 表中已注册的活跃版本
    if not version_label:
        from src.users.project_auth import get_active_version_label

        version_label = get_active_version_label(project_name)
        if not version_label:
            _ver = getattr(config, "project_version", None) or ""
            version_label = _ver or "v1"

    # 获取 version_id
    from src.core.versioning import current_version_id

    version_id = current_version_id(project_name)

    # 检查缓存
    if not force:
        cached = _get_cached_smoke_excel(project_name, version_label)
        if cached:
            _log.info(
                "  [冒烟] ✅ 命中缓存: project=%s version=%s (%d bytes)",
                project_name,
                version_label,
                len(cached),
            )
            return cached, version_label, -1  # -1 表示来自缓存

    # ★ 使用三元组作为数据源（原子需求，数量稳定）
    from src.pipeline.steps.step3.triple_reader import (
        read_triples,
        group_triples_by_module,
        format_triple_for_prompt,
    )

    triples = read_triples(project_name, version_id, exclude_implicit=True)
    if not triples:
        _log.info("  [冒烟] 暂无三元组数据，跳过")
        return None, version_label, 0

    # 按模块分组
    module_groups = group_triples_by_module(triples)
    sorted_modules = list(module_groups.items())
    total_modules = len(sorted_modules)

    _log.info(
        "  [冒烟] %d 条三元组 → %d 个功能模块，每个模块生成 1 条冒烟用例",
        len(triples),
        total_modules,
    )

    # ★  修复：捕获父线程 _thread_run_id，子线程恢复，
    # 否则 _gen_one_module 内的 _log.info 子线程 SSE 不带 run_id
    from src.backend.state import _thread_run_id

    _ctx_run_id = getattr(_thread_run_id, "value", None)

    # 并行生成
    module_cases: dict[str, list[dict[str, Any]]] = {}
    modules_order: list[str] = []
    _lock = threading.Lock()

    def _gen_one_module(idx, mod_name, mod_triples):
        try:
            if _ctx_run_id:
                _thread_run_id.value = _ctx_run_id
            _log.info(
                "  [冒烟 %d/%d] 模块: %s (%d条三元组)",
                idx,
                total_modules,
                mod_name,
                len(mod_triples),
            )

            # 构建三元组描述（主链路=正向操作+预期反馈）
            triples_text = "\n".join(
                format_triple_for_prompt(t, i + 1)
                for i, t in enumerate(mod_triples[:15])
            )

            user_msg = (
                "## 功能模块: %s\n\n"
                "## 原子需求点（三元组：操作 | 条件 | 预期 | 控件）\n"
                "%s\n\n"
                "请将以上三元组组装为 1 条端到端主链路冒烟用例（正向 Happy Path）。"
            ) % (mod_name, triples_text[:6000])

            last_error = ""
            for attempt in range(1, 4):
                try:
                    from src.ai.token_estimator import compute_max_tokens
                    from src.core.config_loader import config as _sg_cfg

                    max_tok, _ = compute_max_tokens(
                        _SMOKE_EXCEL_SYSTEM_PROMPT, user_msg, model=_sg_cfg.ai_model
                    )
                    max_tok = max(int(max_tok), 4096)
                except Exception as _e:
                    _log.debug("[__init__] _gen_one_module 异常: %s", _e, exc_info=True)
                    max_tok = 4096

                result = ai_brain._call_api(
                    system_prompt=_SMOKE_EXCEL_SYSTEM_PROMPT,
                    user_message=user_msg,
                    temperature=0.0,
                    max_tokens=max_tok,
                    caller_name="冒烟用例生成",
                    task_type="smoke_excel",
                    force_cloud=True,
                )
                if not result.get("success"):
                    last_error = "AI 调用失败: %s" % (
                        str(result.get("error", "未知"))[:120]
                    )
                    _log.error("    [WARN] 冒烟 %d/3: %s", attempt, last_error)
                    continue

                parsed = ai_brain._parse_json_response(result["data"])
                if parsed.get("success") and isinstance(parsed.get("data"), dict):
                    cases = parsed["data"].get("cases", [])
                    if isinstance(cases, list) and len(cases) > 0:
                        c = cases[0]
                        if isinstance(c, dict) and c.get("title"):
                            with _lock:
                                modules_order.append(mod_name)
                                module_cases[mod_name] = [
                                    {
                                        "title": c.get(
                                            "title", "%s-核心流程验证" % mod_name
                                        ),
                                        "precondition": c.get(
                                            "precondition",
                                            "1.环境部署成功 2.测试数据已准备",
                                        ),
                                        "steps": c.get(
                                            "steps",
                                            "1.进入%s页面 2.执行核心操作 3.验证结果"
                                            % mod_name,
                                        ),
                                        "expected": c.get(
                                            "expected",
                                            "1.页面正常加载 2.核心功能正常 3.关键数据展示正确",
                                        ),
                                        "risk_level": c.get("risk_level", "高"),
                                        "threshold_flag": c.get("threshold_flag", "是"),
                                        "case_type": "功能测试",
                                    }
                                ]
                            _log.info(
                                "    [冒烟 %d/%d] ✓ 模块=%s 用例=%s",
                                idx,
                                total_modules,
                                mod_name,
                                c.get("title", "")[:30],
                            )
                            return
                    last_error = "AI 返回格式不正确"
                else:
                    last_error = str(parsed.get("error", "JSON 解析失败"))[:120]
                _log.error("    [WARN] 冒烟 %d/3: %s", attempt, last_error)

            _log.error(
                "    [冒烟 %d/%d] ❌ 失败（已重试3次）: %s",
                idx,
                total_modules,
                last_error,
            )
            # 兜底用例
            with _lock:
                modules_order.append(mod_name)
                module_cases[mod_name] = [
                    {
                        "title": "%s-核心流程验证" % mod_name,
                        "precondition": "1.环境部署成功 2.测试数据已准备",
                        "steps": "1.进入%s页面 2.执行核心操作 3.验证结果" % mod_name,
                        "expected": "1.页面正常加载 2.核心功能正常 3.关键数据展示正确",
                        "risk_level": "高",
                        "threshold_flag": "是",
                        "case_type": "功能测试",
                        "_fallback": True,
                    }
                ]
        except Exception as _e:
            import traceback

            traceback.print_exc()
            _log.error(
                "    [冒烟 %d/%d] 💥 异常: %s", idx, total_modules, str(_e)[:120]
            )
            with _lock:
                modules_order.append(mod_name)
                module_cases[mod_name] = [
                    {
                        "title": "%s-核心流程验证" % mod_name,
                        "precondition": "1.环境部署成功 2.测试数据已准备",
                        "steps": "1.进入%s页面 2.执行核心操作 3.验证结果" % mod_name,
                        "expected": "1.页面正常加载 2.核心功能正常 3.关键数据展示正确",
                        "risk_level": "高",
                        "threshold_flag": "是",
                        "case_type": "功能测试",
                        "_fallback": True,
                    }
                ]
        finally:
            # ★ 防线程池复用串台
            try:
                _thread_run_id.value = None
            except Exception as _e:
                _log.debug("[__init__] 非关键异常: %s", _e, exc_info=True)

    # 并行执行 — ★ 用户级配置覆盖默认线程数
    from src.core.pipeline_config import _read_config_value as _cfg

    max_workers = min(total_modules, int(_cfg("step3_parallel_workers", 10)))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(_gen_one_module, i + 1, mod_name, mod_triples): mod_name
            for i, (mod_name, mod_triples) in enumerate(sorted_modules)
        }
        for future in as_completed(futures):
            try:
                future.result()
            except Exception as _fe:
                import traceback

                traceback.print_exc()
                _log.error("  [冒烟] 💥 线程异常: %s", str(_fe)[:200])

    total_cases = sum(len(v) for v in module_cases.values())

    if total_cases == 0:
        _log.info("  [冒烟] 未生成有效用例")
        return None, version_label, 0

    # 构建 Excel
    excel_bytes = _build_smoke_excel(modules_order, module_cases, project_name)

    if not excel_bytes:
        _log.error("  [冒烟] Excel 构建失败")
        return None, version_label, 0

    # 写入 MySQL 缓存
    _write_smoke_excel_to_mysql(project_name, version_label, excel_bytes)

    # ★ SUP-009: 自动清理旧冒烟文件，仅保留最近 3 个
    from src.pipeline.steps.step3.smoke_generator.cache import _cleanup_old_smoke_excel

    _cleanup_old_smoke_excel(project_name, keep_count=3)

    _log.info(
        "  [冒烟] ✅ 生成完成: %d 个模块 %d 条用例, Excel %d bytes",
        len(modules_order),
        total_cases,
        len(excel_bytes),
    )
    return excel_bytes, version_label, total_cases


# ══════════════════════════════════════════════════════════
# 清除缓存（供 API 调用）
# ══════════════════════════════════════════════════════════


def clear_smoke_cache() -> bool:
    """清除冒烟用例 Excel 缓存。"""
    try:
        from src.core.config_loader import config

        project_name = getattr(config, "project_name", "") or ""
        return _delete_cached_smoke_excel(project_name)
    except Exception as _e:
        _log.error("  [smoke] 清除缓存异常: %s", _e)
        return False
