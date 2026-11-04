# -*- coding: utf-8 -*-
from __future__ import annotations

"""_rule_extractor_utils — 从 _rule_extractor.py 拆分（D.1g 架构债清理 2026-07-25，§4.3 单文件≤800行）。

职责：utils 相关函数。
逻辑不变，仅结构重构。re-export 保持调用兼容。
"""
# ★ 注：Python 3.9 兼容的 `from __future__ import annotations` 已在文件头部（第2行）声明，
#   此处不得重复声明——重复的 future import 出现在模块 docstring 之后会触发
#   SyntaxError: "from __future__ imports must occur at the beginning of the file"。
import json
import logging
import re
from collections import Counter
from typing import Any

_log = logging.getLogger(__name__)

# ★ D.1g 拆分遗留断链修复(2026-07-25): 以下常量/状态原定义在 _rule_extractor.py
#   拆分到本模块后漏 import → NameError 被 except 兜住 → Figma 缓存/页面路径推断失效
#   修复: 就近定义在唯一消费方（主文件自身不使用），避免循环 import
# _extract_page_paths_from_rag 用 — API 路径提取
_API_PATH_PATTERN = re.compile(r"(?:GET|POST|PUT|DELETE|PATCH)\s+(/[^\s,(]+)")

# Figma 数据缓存（模块级，避免重复查询）
_figma_cache: dict[str, dict[str, Any]] = {}

from ._rule_extractor_downgrade import (  # noqa: F401
    _infer_control_type_from_operation,
    _infer_page_from_api_path,
)


def _load_figma_confirm_nodes(page_path: str) -> dict[str, Any]:
    """从 figma_nodes 表加载目标页面的 Figma 确认按钮和样式数据。

    通用策略（无业务硬编码）：
    1. 从 page_path 提取关键词（如 material_formula → 配方、辅材）
    2. 匹配 figma_pages.page_name（关键词模糊匹配）
    3. 加载匹配页面的 figma_nodes
    4. 提取两类节点：确认按钮（确定/确认/保存/提交/取消）和操作按钮（导出/删除/新建等）

    返回: {"confirm_buttons": [{"name": "确定", "text": "确定", "node_type": "INSTANCE"}, ...],
           "action_buttons": [{"name": "批量导出", "text": "批量导出", ...}, ...],
           "page_name": "辅材配方管理"}
    """
    result: dict[str, Any] = {
        "confirm_buttons": [],
        "action_buttons": [],
        "page_name": "",
    }
    if not page_path or page_path == "/login":
        return result

    cache_key = page_path
    if cache_key in _figma_cache:
        return _figma_cache[cache_key]

    try:
        from src.core.config_loader import config

        project = getattr(config, "project_name", "") or ""
        if not project:
            return result
        from src.core.db_manager import get_connection

        conn = get_connection()
        try:
            # 从 page_path 提取关键词用于匹配 figma page_name
            # 如 /dataManage/portraitData?tab=material_formula → material_formula
            import re

            _keywords = []
            # 提取 tab 参数
            _tab_m = re.search(r"[?&]tab=(\w+)", page_path)
            if _tab_m:
                _keywords.append(_tab_m.group(1))
            # 提取路径最后一段
            _path_parts = page_path.split("?")[0].rstrip("/").split("/")
            if _path_parts:
                _keywords.append(_path_parts[-1])

            # 查询 figma_pages，匹配 name（表字段为 name，非 page_name）
            figma_pages = (
                conn.execute(
                    "SELECT page_id, name, ui_structure FROM figma_pages "
                    "WHERE project=%s ORDER BY created_at DESC LIMIT 20",
                    (project,),
                ).fetchall()
                or []
            )

            if not figma_pages:
                return result

            # 关键词匹配最佳 page
            best_page = None
            best_score = 0
            for fp in figma_pages:
                page_name = str(fp.get("name", "") or "")
                score = 0
                for kw in _keywords:
                    # 中文关键词：配方 ↔ material_formula
                    # 用简单的字符重叠匹配（通用，不硬编码翻译表）
                    kw_lower = kw.lower()
                    if kw_lower in page_name.lower():
                        score += 10
                    # 也检查逆匹配
                    for ch in page_name:
                        if ch in kw:
                            score += 1
                if score > best_score:
                    best_score = score
                    best_page = fp

            if not best_page or best_score < 2:
                return result

            page_id = best_page["page_id"]
            result["page_name"] = str(best_page.get("name", "") or "")

            # 加载 figma_nodes
            nodes = (
                conn.execute(
                    "SELECT name, text_content, node_type, meta_json FROM figma_nodes "
                    "WHERE page_id=%s ORDER BY id",
                    (page_id,),
                ).fetchall()
                or []
            )

            # 确认按钮关键词（通用，跨语言适用）
            _confirm_patterns = [
                "确定",
                "确认",
                "提交",
                "保存",
                "完成",
                "取消",
                "ok",
                "confirm",
                "submit",
                "save",
                "cancel",
                "apply",
                "done",
                "yes",
                "no",
            ]
            # 操作按钮关键词（触发弹窗的操作）
            _action_patterns = [
                "导出",
                "导入",
                "删除",
                "新建",
                "添加",
                "创建",
                "编辑",
                "修改",
                "复制",
                "打标",
                "上传",
                "下载",
                "export",
                "import",
                "delete",
                "create",
                "add",
                "edit",
                "copy",
                "upload",
                "download",
            ]

            for node in nodes:
                name = str(node.get("name", "") or "").strip()
                text = str(node.get("text_content", "") or "").strip()
                node_type = str(node.get("node_type", "") or "").strip()
                # 合并 name 和 text 用于匹配
                combined = f"{name} {text}".lower()

                # 确认按钮
                for pat in _confirm_patterns:
                    if pat.lower() in combined:
                        result["confirm_buttons"].append(
                            {
                                "name": name,
                                "text": text,
                                "node_type": node_type,
                                "matched_pattern": pat,
                            }
                        )
                        break

                # 操作按钮
                for pat in _action_patterns:
                    if pat.lower() in combined:
                        result["action_buttons"].append(
                            {
                                "name": name,
                                "text": text,
                                "node_type": node_type,
                                "matched_pattern": pat,
                            }
                        )
                        break
        finally:
            conn.close()

        _figma_cache[cache_key] = result
        if result["confirm_buttons"] or result["action_buttons"]:
            _log.debug(
                "[RuleExtract] Figma: page=%s confirm=%d action=%d",
                result["page_name"],
                len(result["confirm_buttons"]),
                len(result["action_buttons"]),
            )
        return result
    except Exception as e:
        _log.debug("[RuleExtract] _load_figma_confirm_nodes 失败: %s", str(e)[:80])
        return result


def _build_figma_ui_checks(target_page: str, actions: list[dict]) -> list[dict]:
    """从 Figma/DOM 数据生成 UI 规范验证 checks（软约束，不阻断）。

    通用策略（无业务硬编码）：
    1. 对每个点击按钮，生成 verify_position check（检查是否在视口内，非 0,0）
    2. 对每个点击按钮，生成 verify_style check（检查是否可见）
    3. 这些 checks 标记为 severity="info"，不阻断功能通过，仅作为 UI 质量提示

    Step4 已有的 executor: verify_position, verify_style (在 _TOOL_PREFIX_MAP 中)
    """
    checks = []
    if not target_page or not actions:
        return checks

    # 收集所有 click 操作的 target_hint
    click_targets = []
    for a in actions:
        if isinstance(a, dict) and a.get("type") == "click":
            hint = str(a.get("target_hint", "")).strip()
            if hint and hint not in click_targets:
                click_targets.append(hint)

    if not click_targets:
        return checks

    # 为每个点击目标生成 UI 规范验证
    for i, target in enumerate(click_targets):
        # 生成 CSS selector（通用：用 button 文本匹配）
        _selector = f'button:has-text("{target}"), a:has-text("{target}"), [role="button"]:has-text("{target}")'

        # 1. 位置验证：检查元素在视口内，非 (0,0)
        checks.append(
            {
                "track": "ui",
                "precision": "fuzzy",
                "severity": "info",
                "check": {
                    "type": "position_check",
                    "selector": _selector,
                    "x": 0,
                    "y": 0,
                    "tolerance": 1,
                    "description": f"按钮'{target}'不应在 (0,0) 位置",
                },
                "expect": f"按钮'{target}'位置正常（非 0,0）",
            }
        )

        # 2. 可见性验证：检查按钮可见
        checks.append(
            {
                "track": "ui",
                "precision": "fuzzy",
                "severity": "info",
                "check": {
                    "type": "visibility_check",
                    "selector": _selector,
                    "expected": "visible",
                    "description": f"按钮'{target}'应可见",
                },
                "expect": f"按钮'{target}'可见",
            }
        )

    return checks


def _extract_field_names_from_data_dict(data_dict: dict | None) -> list[str]:
    """从 data_dict / entity_tables 中提取字段 verbose_name 列表。

    支持的 data_dict 格式：
      1. {"entity_tables": {"表名": {"columns": ["列1","列2",...]}}}
      2. {"db_tables": [{"name": "表名", "columns": [{"name": "列1"}, ...]}]}
      3. 直接传入 entity_tables dict

    Returns:
        字段名列表（去重，保留顺序），如 ["科目名称", "科目编码", "状态"]
    """
    field_names: list[str] = []
    seen: set[str] = set()

    if not data_dict:
        return field_names

    # 格式1: entity_tables 嵌套在 data_dict 中
    _entity = data_dict.get("entity_tables", {})
    if isinstance(_entity, dict):
        for _info in _entity.values():
            _cols = _info.get("columns", []) if isinstance(_info, dict) else []
            # ★ 根因修复(2026-08-09): 优先使用 verbose_name（中文），回退到列名
            _verbose_names = _info.get("verbose_names", {}) if isinstance(_info, dict) else {}
            for _c in _cols:
                _raw = str(_c).strip() if isinstance(_c, str) else ""
                if not _raw:
                    continue
                # 使用 verbose_name 如果可用
                if _verbose_names and _raw in _verbose_names:
                    _name = _verbose_names[_raw]
                else:
                    _name = _raw
                if _name not in seen:
                    seen.add(_name)
                    field_names.append(_name)

    # 格式2: db_tables 列表
    _db_tables = data_dict.get("db_tables", [])
    if isinstance(_db_tables, list):
        for _tbl in _db_tables:
            _cols = _tbl.get("columns", []) if isinstance(_tbl, dict) else []
            for _c in _cols:
                if isinstance(_c, dict):
                    _name = str(_c.get("name", "") or "").strip()
                elif isinstance(_c, str):
                    _name = _c.strip()
                else:
                    _name = ""
                if _name and _name not in seen:
                    seen.add(_name)
                    field_names.append(_name)

    # 格式3: 直接传入 entity_tables dict（无外层包装）
    if not field_names and isinstance(data_dict, dict):
        for _k, _v in data_dict.items():
            if isinstance(_v, dict) and "columns" in _v:
                _cols = _v.get("columns", [])
                # ★ 根因修复(2026-08-09): 优先使用 verbose_name（中文），回退到列名
                _verbose_names = _v.get("verbose_names", {})
                for _c in _cols:
                    _raw = str(_c).strip() if isinstance(_c, str) else ""
                    if not _raw:
                        continue
                    if _verbose_names and _raw in _verbose_names:
                        _name = _verbose_names[_raw]
                    else:
                        _name = _raw
                    if _name and _name not in seen:
                        seen.add(_name)
                        field_names.append(_name)

    return field_names


def _is_i01_case(case_id: str, title: str = "") -> bool:
    """判断是否为 I01 类型用例（字段展示验证）。"""
    return "I01" in (case_id or "") or "字段" in (title or "")


def _build_field_checks_for_i01(
    field_names: list[str],
    existing_checks: list[dict] | None = None,
) -> list[dict]:
    """为 I01 用例生成字段级 text_contains 检查。

    策略：
      1. 从已有 checks 中移除旧的通用 text_contains_check（如"操作成功,成功"、"页面显示"等）
      2. 用每个字段名生成精确 text_contains 检查

    Args:
        field_names: 字段名列表（如 ["科目名称", "科目编码", "状态"]）
        existing_checks: 已有的 checks 列表（可选，用于过滤通用检查）

    Returns:
        替换后的 checks 列表
    """
    # 通用检查关键词黑名单（应在 I01 中被替换的）
    _GENERIC_TEXT_KW = [
        "操作成功", "成功", "保存成功", "提交成功", "完成",
        "页面显示", "查询结果", "页面出现", "需求期望",
        "无错误", "无报错", "系统无报错",
    ]

    # 保留非 text_contains 的 check（如 element_exists, no_error 等）
    kept_checks = []
    if existing_checks:
        for c in existing_checks:
            _check = c.get("check", {})
            _type = ""
            if isinstance(_check, dict):
                _type = _check.get("type", "")
            elif isinstance(_check, str):
                _type = _check
            # 保留非 text_contains_check 的 check
            if "text_contains" not in _type:
                # 也保留 no_error 类型的 check
                if _type == "no_error" or c.get("assertion") == "no_error":
                    kept_checks.append(c)
                else:
                    kept_checks.append(c)

    # 用字段名生成精确 text_contains 检查
    field_checks = []
    for fname in field_names:
        field_checks.append({
            "track": "ui",
            "precision": "exact",
            "check": {
                "type": "text_contains",
                "selector": "body",
            },
            "expect": fname,
            "description": f"页面展示字段: {fname}",
        })

    # 合并：保留的已有 checks + 字段级 checks
    return kept_checks + field_checks


def _build_precondition(module: str, operation: str) -> str:
    """从 module 和 operation 生成 precondition 文本。"""
    _mod = (module or "通用").strip()
    return f"1.系统已登录 2.已进入{_mod}管理页面"


def _build_test_steps_json(actions: list[dict]) -> str:
    """将 actions 数组格式化为 JSON 字符串（test_steps 字段）。"""
    import json as _json
    try:
        return _json.dumps(actions, ensure_ascii=False, indent=2)
    except Exception:
        return str(actions)


def _build_expected_result(checks: list[dict]) -> str:
    """从第一个 check 的 expect 提取 expected_result 文本。"""
    if checks:
        _first = checks[0]
        _exp = _first.get("expect", "") or _first.get("expected", "")
        if _exp:
            return str(_exp)
    return "操作成功"


def _build_one_case(
    case_id: str,
    rid: str,
    module: str,
    desc: str,
    operation: str,
    expected: str,
    scenario_type: str,
    risk_level: str,
    case_type: str,
    test_method: str,
    control_type: str,
    required_tracks: list[str],
    actions: list[dict],
    checks: list[dict],
    scenario_label: str,
    data_dict: dict | None = None,
) -> dict:
    """构建一条用例。

    新增 data_dict 参数（可选）：包含 entity_tables 或 db_tables 上下文，
    用于 I01 类型用例生成字段级 text_contains 检查。
    """
    # ★ 根因修复(2026-08-09): 对 I01 类型用例生成字段级 text_contains 检查
    _title = f"{module}-{rid}-{scenario_label}"
    if _is_i01_case(case_id, _title):
        field_names = _extract_field_names_from_data_dict(data_dict)
        if field_names:
            checks = _build_field_checks_for_i01(field_names, checks)
            _log.info(
                "[RuleExtract] ★ I01字段级检查: case=%s fields=%d -> %s",
                case_id, len(field_names), field_names[:5],
            )

    # ★ 根因修复(2026-08-09): 添加 precondition / test_steps / expected_result 字段
    case = {
        "case_id": case_id,
        "req_id": rid,
        "title": _title,
        "description": f"执行{desc}的{scenario_label}，确认系统行为符合预期",
        "risk_level": risk_level,
        "type": case_type,
        "scenario_type": scenario_type,
        "test_method": test_method,
        "control_type": control_type,
        "required_tracks": required_tracks,
        "actions": actions,
        "checks": checks,
        "_generation_source": "rule_extract",
        "precondition": _build_precondition(module, operation),
        "test_steps": _build_test_steps_json(actions),
        "expected_result": _build_expected_result(checks),
    }
    return case


def _extract_page_paths_from_rag(
    rag_context: str, trace_context: str = ""
) -> list[str]:
    """从 RAG 文本和 trace_links 中提取可用页面路径清单。

    数据来源：
    1. DOM chunks 中的 page_path（最可靠，来自浏览器爬取的真实路由）
    2. API chunks 中的 path 前缀（取前两级，过滤 {占位符}）
    3. trace_links 中的 api_endpoints（同 API 处理逻辑）

    Returns:
        去重后的页面路径列表，如 ["/productTask", "/dataQuality/rule"]
    """
    paths = set()

    # 1. 从 RAG 文本提取 DOM page_path
    if rag_context:
        # JSON meta 格式: "page_path": "/xxx"
        for m in re.finditer(r'"page_path"\s*:\s*"(/[^"]+)"', rag_context):
            pp = m.group(1).strip()
            if pp and pp != "/login":
                paths.add(pp)
        # text 格式: 路径:/xxx
        for m in re.finditer(r"路径:(/\S+)", rag_context):
            pp = m.group(1).strip()
            if pp and pp != "/login":
                paths.add(pp)

    # 2. 从 RAG 文本提取 API path 前缀
    if rag_context:
        for m in _API_PATH_PATTERN.finditer(rag_context):
            api_path = m.group(1).strip()
            inferred = _infer_page_from_api_path(api_path)
            if inferred:
                paths.add(inferred)

    # 3. 从 trace_context 提取 API endpoints
    if trace_context:
        # trace_context 可能含 JSON 格式的 api_endpoints
        for m in re.finditer(
            r'"(?:path|api_endpoints?)"\s*:\s*"(/[^\s"]+)"', trace_context
        ):
            api_path = m.group(1).strip()
            inferred = _infer_page_from_api_path(api_path)
            if inferred:
                paths.add(inferred)
        # 也解析纯文本格式
        for m in re.finditer(
            r"(?:GET|POST|PUT|DELETE|PATCH)\s+(/[^\s,(]+)", trace_context
        ):
            api_path = m.group(1).strip()
            inferred = _infer_page_from_api_path(api_path)
            if inferred:
                paths.add(inferred)

    # 过滤掉含占位符的路径，按长度排序（短的优先，通常是页面根路径）
    result = sorted([p for p in paths if "{" not in p], key=len)
    return result


def _match_page_for_requirement(
    req: dict[str, Any], available_paths: list[str]
) -> str | None:
    """根据需求内容从可用页面路径清单中匹配最佳页面。

    匹配策略（通用，禁业务关键词）：
    1. 需求的 operation/expected 中包含路径关键词（如 productTask）→ 精确匹配
    2. 否则返回最短的路径（通常是首页/列表页，最通用）

    Returns:
        匹配的路径字符串，或 None
    """
    if not available_paths:
        return None

    # 从需求文本中提取可能的路由关键词
    req_text = " ".join(
        [
            str(req.get("operation", "") or ""),
            str(req.get("expected_feedback", "") or ""),
            str(req.get("description", "") or ""),
            str(req.get("module", "") or ""),
        ]
    ).lower()

    # 尝试精确匹配：路径的最后一段出现在需求文本中
    for path in available_paths:
        segments = [s for s in path.split("/") if s]
        if segments:
            last_seg = segments[-1].lower()
            if last_seg and len(last_seg) >= 3 and last_seg in req_text:
                return path

    # 兜底：返回最短的路径（最通用）
    return available_paths[0] if available_paths else None
