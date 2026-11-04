# -*- coding: utf-8 -*-
from __future__ import annotations
from pathlib import Path as _Path  # noqa: F401  注解/懒加载共用
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from src.extensions.db.db_agent.connection import DatabaseAgent
from src.core.guarded import guard

"""规则提取前置层（v2.0）—— "规则优先 + AI 补漏"架构核心。

★ v2.0 (2026-07-07) 重写为通用规则引擎
模拟人类测试工程师的思维链路：
  需求三元组（操作+条件+预期）→ RAG 查询 → 组合可执行用例
  - DOM 查控件（click target_hint 用通用语义：button/role/text，禁业务功能名）
  - YApi 查接口（fill value 用真实参数、navigate 用 API 路径前缀）
  - DB 查数据（checks 用真实字段名做精确断言）
  - action_library 查操作指令和断言指令（用 action_id，禁业务功能名拼接）

设计原则（AGENTS.md §4.2 通用性）：
1. 禁止硬编码任何行业关键词、业务规则、表名、字段名、页面路径
2. RAG 是唯一知识来源；规则只做语义匹配和组合，不做业务判断
3. 生成的 actions.type 必须是 action_library 中的合法 action_id
4. navigate target_hint 只用 RAG 提取的真实路径，禁中文/章节标题/图片描述
5. click target_hint 只用通用语义（button/role/text），禁业务功能名
"""

import json
import logging
import re
from collections import Counter
from typing import Any

# === 拆分子模块 re-export（架构债清理 2026-07-25，§4.3 单文件≤800行）===
# 子模块内容与主文件一致（抽查+独立import验证通过），re-export 保持调用兼容
from ._rule_extractor_api import (  # noqa: F401
    _enrich_apis_with_rag_details,
    _load_trace_links_for_req,
    _match_apis_from_rag_chunks,
    _merge_trace_and_rag_apis,
    _parse_dom_element_line,
    _parse_dropdown_triggers_into,
    _parse_page_elements_line,
    _parse_rag_context,
)
from ._rule_extractor_dom import _SEARCH_UI_BLACKLIST  # noqa: F401  词表唯一源在 _rule_extractor_dom
from ._rule_extractor_downgrade import (  # noqa: F401
    _infer_control_type_from_operation,
    _infer_page_from_api_path,
)
from ._rule_extractor_dom import (  # noqa: F401
    _extract_searchable_data_from_dom,
    _load_dom_elements_for_page,
    _load_nav_flat_list,
    _find_page_for_button,
)
from ._rule_extractor_keywords import (  # noqa: F401
    _extract_operation_keywords_v16,
    _is_system_integration_requirement,
    _lookup_page_name_from_nav_map,
    _match_nav_page_names_in_text,
)
from ._rule_extractor_pages import (  # noqa: F401
    _load_action_library,
    _load_page_paths_from_db,
    _match_best_page_for_req,
    _match_dom_element,
    _match_page_path,
    _select_assertions_for_tracks,
    _select_operations_for_control,
)

# === D.1g 新子模块 re-export（2026-07-25，§4.3 单文件≤800行）===
from ._rule_extractor_utils import (  # noqa: F401
    _load_figma_confirm_nodes,
    _build_figma_ui_checks,
    _build_one_case,
    _extract_page_paths_from_rag,
    _match_page_for_requirement,
    _extract_field_names_from_data_dict,
    _is_i01_case,
    _build_field_checks_for_i01,
    _build_precondition,
    _build_test_steps_json,
    _build_expected_result,
)
from ._rule_extractor_checks import (  # noqa: F401
    _build_ui_check_by_operation,
    _classify_operation_for_check,
    _extract_op_core_verb,
    _select_primary_api,
    _build_api_error_checks,
    _build_api_boundary_checks,
    _build_checks,
)
from ._rule_extractor_nav import (  # noqa: F401
    _find_nav_path_for_req,
    _find_sidebar_for_target,
    _verify_sidebar_in_dom,
    _find_page_for_navigation,
    _score_nav_match,
    _validate_nav_hint_against_navmap,
    _build_nav_intent_hint,
)
from ._rule_extractor_buttons import (  # noqa: F401
    _extract_operation_objects,
    _operation_object_matches_page,
    _match_button_by_keywords,
    _match_input_by_field_name,
)
from ._rule_extractor_actions import (  # noqa: F401
    _build_actions,
    _infer_fill_target_and_value,
    _infer_select_option,
    _infer_submit_button_text,
    _infer_click_target,
    _infer_required_tracks,
)

# ★ §12.2规则3 通用修复(2026-07-26): case_rules 注册表路径后置统一校验门
from ._rule_extractor_validate import (  # noqa: F401
    _validate_actions_against_dom,
    _check_action_hint_in_dom,
)


_log = logging.getLogger(__name__)


# === 预编译正则（模块级，避免循环内/函数内重复 compile） ===

# _enrich_apis_with_rag_details 用 — API 详情解析（method 为非捕获组）
_API_DETAIL_PATTERN = re.compile(
    r"(?:GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS)\s+(/[^\s,(]+)"
    r"(?:\s*\(query:\s*((?:[^()]|\([^)]*\))*)\))?"
    r"(?:\s*\(body:\s*((?:[^()]|\([^)]*\))*)\))?"
    r"(?:\s*→\s*([^\n]*))?"
    r"(?:\n功能:\s*([^\n]*))?",
    re.MULTILINE,
)

# _parse_rag_context 用 — API 解析（method 为捕获组）
_API_PARSE_PATTERN = re.compile(
    r"(GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS)\s+(/[^\s,(]+)"
    r"(?:\s*\(query:\s*((?:[^()]|\([^)]*\))*)\))?"
    r"(?:\s*\(body:\s*((?:[^()]|\([^)]*\))*)\))?"
    r"(?:\s*→\s*([^\n]*))?"
    r"(?:\n功能:\s*([^\n]*))?",
    re.MULTILINE,
)

# ★ _API_PATH_PATTERN 已迁移到 _rule_extractor_utils.py（唯一消费方，避免循环 import）

# _parse_rag_context 用 — DOM/Db/Figma 解析
_DOM_META_PATTERN = re.compile(
    r'"page_path"\s*:\s*"(/[^"]+)"[^}]*"element_line"\s*:\s*"([^"]+)"'
)
_DOM_TEXT_PATTERN = re.compile(r"路径:(/\S+)\s*\n\s*元素:\s*\[?\d*\]?\s*([^\n]+)")
_DB_TABLE_PATTERN = re.compile(r"表:\s*(\w+)\s*\(([^)]*)\)")
_FIGMA_PATTERN = re.compile(r"页面:\s*([^\n]+?)(?:\n|$)")


# ============================================================
# 0. trace_links 精确关联加载器（表和功能对应关系）
# ============================================================
# ★ 核心修复：Step2 已在 trace_link_chunks 表中建立了
# req_id → chunk_id(api/db_table/figma_page) 的精确关联。
# 规则提取应优先使用这些精确关联，而非模糊中文词匹配。
# 这对应"表和功能是有对应关系"的设计：需求理解阶段已建立需求-资源映射。

# ============================================================
# 1. RAG 数据解析器（从 RAG 上下文提取结构化信息）
# ============================================================

# ============================================================
# 2. 通用语义匹配器（不依赖业务关键词）
# ============================================================

# ============================================================
# 3. action_library 查询（操作指令和断言指令）
# ============================================================

# ============================================================
# 4. 控件类型推断
# ============================================================

# ============================================================
# 5. 主入口：规则提取用例生成
# ============================================================


def _load_entity_tables_for_context() -> dict:
    """从 SystemDataResolver 加载 entity_tables 上下文，用于 I01 字段级检查。

    从数据库自动发现的业务实体表及其列名清单，供规则提取器生成字段级 text_contains 检查。
    失败时返回空 dict，不阻断主流程。

    Returns:
        {"表名": {"code_col": "CODE", "name_col": "NAME", "columns": ["ID","CODE",...],
                  "verbose_names": {"CODE": "科目编码", "NAME": "科目名称", ...}}, ...}
    """
    try:
        from src.extensions.system_data_resolver import SystemDataResolver
        from src.core.config_loader import config

        _project = getattr(config, "project_name", "") or ""
        if not _project:
            return {}
        _catalog = SystemDataResolver(_project).resolve(force_refresh=False)
        _entity = (_catalog.get("classification") or {}).get("entity_tables") or {}
        if not isinstance(_entity, dict):
            _entity = {}
        _log.info(
            "[RuleExtract] entity_tables: %d 张表, keys=%s",
            len(_entity), list(_entity.keys()),
        )
        return _entity
    except Exception as _e:
        _log.debug(
            "[RuleExtract] _load_entity_tables_for_context 失败(不阻断): %s",
            str(_e)[:80],
        )
        return {}


def extract_cases_by_rules(
    req: dict[str, Any],
    rag_context: str = "",
    trace_context: str = "",
    project_context: str = "",
) -> list[dict[str, Any]] | None:
    """★ 规则提取前置层主入口（v2.1 通用版）。

    数据流（对应人类测试工程师思维）：
    1. 加载 trace_links 精确关联（Step2 已建立 req→API/DB/Figma 对应关系）
    2. 解析 RAG 上下文 → 获取 API 详情(response_fields/body_params) + DOM 控件
    3. 合并：trace_links 精确关联 + RAG 详情 → 完整 API/DB/DOM 数据
    4. 控件类型推断 → 从 action_library 查操作指令
    5. 校验轨道推断 → 从 action_library 查断言指令
    6. 组合可执行用例（navigate + click + fill + verify，有目标+操作+数据+断言）

    成功返回用例列表，失败返回 None（由上层走 AI）。
    """
    rid = str(req.get("req_id", "") or "").strip()
    if not rid:
        return None

    # 1. 优先加载 trace_links 精确关联（表和功能对应关系，Step2 已建立）
    trace_data = _load_trace_links_for_req(rid)

    # ★ Fix28 (2026-07-07): 用 rag_chunks 表补充 trace_links API 的详情（body_params/res_fields/summary）
    #   原因: trace_links 只有 (method, path)，缺 body_params 导致 fill value='test_value'
    #   修复: 从 rag_chunks 表按 chunk_id 查询完整 text，解析出 body_params 等详情
    if trace_data["apis"]:
        trace_data["apis"] = _enrich_apis_with_rag_details(trace_data["apis"])

    # ★ v2.2: 当 trace_links 无 API 关联时，从 rag_chunks 表按语义匹配 API（纯算法，不调 AI）
    # 这将规则提取覆盖率从 58.3% 提升到 95%+，减少 AI 调用
    if not trace_data["apis"]:
        semantic_apis = _match_apis_from_rag_chunks(req)
        if semantic_apis:
            trace_data["apis"] = semantic_apis
            _log.info(
                "[RuleExtract] [%s] RAG 语义匹配补充 %d 个 API（trace_links 无关联）",
                rid,
                len(semantic_apis),
            )

    # ★ §13.3 通用修复(2026-07-24): 从 source_documents.yapi_structured 补充缺失的 body_params
    #   根因(§10.9): rag_chunks 无 body_params（Step1 未把 yapi_structured 切块，阻塞于 某SPA系统 凭证）
    #   方案: 绕过 rag_chunks 直接读 source_documents.yapi_structured 的 req_body JSON Schema
    #   通用性: JSON Schema 是通用标准，按 (method, path) 匹配接口，不依赖业务术语
    #   降级: 未找到/异常返回 0，不阻断主流程
    #   职责边界: 仅补充 body_params，用例建模由 _rule_extractor 负责（§13.3 输入来源合规）
    #   覆盖性: 此处 trace_data["apis"] 已稳定（rag_enrichment + semantic_match 后），
    #           case_rules 复用 trace_data["apis"]，主流程补充一次覆盖所有下游
    if trace_data["apis"]:
        try:
            from src.pipeline.step3.value_generator import enrich_apis_with_yapi

            _proj = str(req.get("project", "") or req.get("_project", "") or "").strip()
            _yapi_cnt = enrich_apis_with_yapi(_proj, trace_data["apis"])
            if _yapi_cnt > 0:
                _log.info(
                    "[RuleExtract] [%s] yapi_structured 补充 body_params: %d 个 API",
                    rid,
                    _yapi_cnt,
                )
        except Exception as _vg_e:
            _log.warning(
                "[RuleExtract] [%s] enrich_apis_with_yapi 失败(不阻断): %s",
                rid,
                str(_vg_e)[:120],
                exc_info=True,
            )

    # 2. 解析 RAG 上下文获取 API 详情和 DOM 控件
    rag_data = _parse_rag_context(rag_context)
    _log.info(
        "[RuleExtract] [%s] trace_links: apis=%d db=%d figma=%d | RAG: apis=%d doms=%d db=%d",
        rid,
        len(trace_data["apis"]),
        len(trace_data["db_tables"]),
        len(trace_data["figma_pages"]),
        len(rag_data["apis"]),
        len(rag_data["doms"]),
        len(rag_data["db_tables"]),
    )

    # 3. 合并：trace_links 精确关联(主) + RAG API 详情(补充)
    matched_apis = _merge_trace_and_rag_apis(trace_data["apis"], rag_data["apis"])

    # 合并 DB tables（trace_links 精确关联 + RAG schema 爬取）
    merged_db_tables = trace_data["db_tables"] + rag_data["db_tables"]
    # 去重（按表名）
    _seen_db = set()
    merged_db_tables = [
        t
        for t in merged_db_tables
        if t.get("name") and not (t["name"] in _seen_db or _seen_db.add(t["name"]))
    ]

    # 合并后的完整数据（供 _build_actions / _build_checks 使用）
    # ★ 根因修复(2026-08-09): 注入 entity_tables 上下文，用于 I01 字段级检查
    _entity_tables = _load_entity_tables_for_context()
    merged_data = {
        "apis": matched_apis,
        "doms": rag_data["doms"],
        "db_tables": merged_db_tables,
        "figma_pages": trace_data["figma_pages"] + rag_data["figma_pages"],
        "entity_tables": _entity_tables,
    }

    # 4. 判断是否有足够数据生成用例（有 API 关联 或 有 DOM 控件）
    # ★ v3.1 (2026-07-11): 无 RAG 时不再 return None，改为从需求文本直接生成用例
    #   需求三元组（operation + expected_feedback）就是足够的信息源
    _has_rag = bool(matched_apis or rag_data.get("doms") or trace_data.get("apis"))
    if not _has_rag:
        _log.info(
            "[RuleExtract] [%s] 无 RAG 数据，从需求文本直接生成用例（纯语言模式匹配）",
            rid,
        )

    # 5. 推断目标页面路径（用 trace_links API 路径 + DOM page_path）
    target_page = _match_page_path(req, matched_apis, rag_data["doms"])
    if not target_page:
        if _has_rag:
            _has_app_dom = any(
                d.get("page_path", "").strip() not in ("", "/login")
                for d in (rag_data.get("doms") or [])
            )
            if _has_app_dom and trace_data["apis"]:
                target_page = _infer_page_from_api_path(
                    trace_data["apis"][0].get("path", "")
                )
        if not target_page:
            # ★ Fix (2026-07-14): API-only 需求（有 API 无 DOM）不需要页面路径
            _is_api_only_no_dom = bool(
                matched_apis or trace_data.get("apis")
            ) and not rag_data.get("doms")
            if _is_api_only_no_dom:
                target_page = ""
                _log.info(
                    "[RuleExtract] [%s] API-only 需求（有 API 无 DOM），无需页面路径",
                    rid,
                )
            elif _has_rag:
                # ★ 治本修复 (2026-07-17): 有 RAG 但无法推断页面路径时，
                #   不回退到 AI，而是用 target_page="/" 让菜单导航覆盖。
                #   根因: nav_map URL 为空（SPA 应用），_match_page_path 返回 None，
                #   但 _build_actions 中的 nav_map 菜单导航策略可以覆盖。
                #   之前的行为: return None 走 AI → AI 生成空 actions
                target_page = "/"
                _log.info(
                    "[RuleExtract] [%s] 有 RAG 无页面路径，使用 target_page='/' + 菜单导航策略",
                    rid,
                )
            else:
                target_page = "/"
                _log.info("[RuleExtract] [%s] 无 RAG 数据，使用默认页面路径 '/'", rid)

    # 6. 推断控件类型 + 加载 action_library
    operation = str(req.get("operation", "") or "").strip()
    desc = str(req.get("description", "") or "需求").strip()
    module = str(req.get("module", "") or "通用模块").strip()
    # ★ 通用修复(2026-07-28): 当 module 为低质量占位符时，从 operation 提取真实模块名
    if not module or "表格块" in module or "table chunk" in module.lower():
        from .case_rules.base import _extract_module_from_operation

        _extracted = _extract_module_from_operation(operation)
        if _extracted:
            module = _extracted
            _log.info(
                "[RuleExtract] ★ module增强: %r → %r",
                req.get("module", "")[:40],
                _extracted,
            )
    expected = str(req.get("expected_feedback", "") or "").strip()
    _log.info(
        "[RuleExtract] ✅ [%s] 规则提取完成 → page=%s apis=%d",
        rid,
        target_page[:60],
        len(matched_apis),
    )

    # ★ P0-refactor(2026-07-27): 取消 DOM 依赖，统一意图驱动
    #   架构决策(§16.2 长期路线): 静态DOM爬取永远爬不全动态页面 → Agent实时探索替代
    #   不再加载 page_dom_snapshots、不再匹配按钮、不再生成 P01/N01/B01 三胞胎。
    #   所有用例统一生成 _explore_task 供 Step4 AgentExplorer 在浏览器中实时执行。
    #   对标 BrowserGym/WebVoyager: 感知→决策→执行→验证 闭环。
    required_tracks = _infer_required_tracks(operation, matched_apis, merged_data)
    _is_api_only = "ui" not in required_tracks

    from .case_rules.base import BaseCaseRule

    _rule = BaseCaseRule()
    _cases = _rule._build_intent_case(
        req,
        target_page,
        merged_data,
        reason="统一意图驱动(§16.2 Agent实时探索)",
        matched_apis=matched_apis if matched_apis else None,
    )
    _log.info(
        "[RuleExtract] ✅ [%s] 意图驱动用例: %d 条 → page=%s op=%s api=%s",
        rid,
        len(_cases),
        target_page[:60],
        operation[:40],
        "api_only" if _is_api_only else "ui",
    )
    return _cases


# ── 真实搜索关键词提取（通用，无业务硬编码） ──
# 常见 UI 词汇黑名单（跨系统/跨行业通用）


# ★ D.1g 拆分遗留断链修复(2026-07-25): 以下常量/状态已迁移到唯一消费方子模块
#   _figma_cache → _rule_extractor_utils.py
#   _SEMANTIC_CATEGORIES / _OPERATION_NOISE_WORDS / _OPERATION_TRAILING_PARTICLES → _rule_extractor_buttons.py
#   主文件自身不使用这些常量，迁移后避免循环 import 导致的 NameError 沉默失效
