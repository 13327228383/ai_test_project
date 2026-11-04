# -*- coding: utf-8 -*-
"""
Step3 控件存在性预验证
========================
通用机制：用 Step1 抓取的 DOM 快照校验 AI 生成的用例动作目标控件是否真实存在于目标页面。
- 若控件在当前页面缺失但在同基础路径的其他 tab 中存在，自动切换 navigate URL 到正确 tab。
- 若控件在所有 tab 均不存在，标记用例为 non_testable，避免 Step4 执行必然失败的用例。

设计约束：
- 禁止写死业务路径、tab 名、控件名；所有判断基于 DOM 快照文本匹配。
- 仅读取 nav_graph_nodes（经 nav_graph_read 适配），不操作浏览器；保持 Step3 轻量。
"""

from __future__ import annotations
from src.core.guarded import guard

import json
import logging
from typing import Any

from src.core.db_manager import get_connection
from src.pipeline.step3._rule_extractor import _operation_object_matches_page
from src.pipeline.utils.nav_graph_read import read_dom_pages

# ★ 架构债清理(2026-07-24): DOM 解析 + URL 工具函数拆分到 _control_dom_utils.py（§4.3 单文件≤800行）
from src.pipeline.step3._control_dom_utils import (
    _parse_dom_summary,
    _element_matches,
    _control_exists,
    _is_interactive,
    _is_form_control,
    _normalize_url,
    _sort_url_params_preserve_case,
    _split_base_and_tab,
)

_log = logging.getLogger(__name__)


def _load_dom_snapshots(project: str, version_label: str) -> list[dict[str, Any]]:
    """加载项目版本的页面 DOM 元素（来源：nav_graph_nodes.page_elements）。

    ★ P0-1 读侧迁移(2026-08-05): page_dom_snapshots → nav_graph（read_dom_pages）。
      根因(§10.9): page_dom_snapshots 已 0 行且**全仓无任何写入方**，原查询恒返回空 →
      snapshots=[] → 控件预验证整体放行 → non_testable 标记形同虚设，
      且失败被 `except: _log.warning` 静默吞掉，表现为"库里没数据"。
      现改读 nav_graph_nodes（Step1 真实爬取产物），dom_summary 由结构化
      page_elements 合成，格式与原 Step1 产物一致（_parse_dom_summary 契约不变）。

    返回空列表代表"该项目尚无导航数据"（未配置/未爬取），调用方按放行处理；
    真正的加载异常必须 error 级抛日志，不得与"无数据"混为一谈。
    """
    if not project:
        _log.warning(
            "[ControlValidation] project 为空，跳过控件预验证（无法定位导航数据）"
        )
        return []
    try:
        pages = read_dom_pages(project, version_label or None)
    except Exception as exc:
        _log.error(
            "[ControlValidation] 加载页面元素失败 project=%s version=%s，"
            "控件存在性预验证将整体放行: %s",
            project,
            version_label,
            exc,
            exc_info=True,
        )
        return []

    snapshots: list[dict[str, Any]] = []
    for page in pages:
        url = str(page.get("page_url", "") or "").strip()
        if not url:
            continue
        snapshots.append(
            {"page_url": url, "dom_summary": page.get("dom_summary", "") or ""}
        )
    _log.info(
        "[ControlValidation] 加载页面元素 %d 条 (project=%s, version=%s)",
        len(snapshots),
        project,
        version_label or "<latest>",
    )
    return snapshots


def _build_snapshot_index(
    snapshots: list[dict[str, Any]],
) -> tuple[
    dict[str, list[dict[str, Any]]],
    dict[str, list[tuple[str, str, str, list[dict[str, Any]]]]],
    dict[str, str],
]:
    """★ 通用修复(2026-07-21 v18): 构建 DOM 快照索引。

    Returns:
        url_index: normalized_url -> elements
        sibling_index: base_path -> [(tab, normalized_url, original_url, elements), ...]
        url_to_original: normalized_url -> original_url (★ v18 新增，用于 _find_alternative_page 返回带host的原始URL)
    """
    url_index: dict[str, list[dict[str, Any]]] = {}
    sibling_index: dict[str, list[tuple[str, str, str, list[dict[str, Any]]]]] = {}
    url_to_original: dict[str, str] = {}  # ★ v18 新增
    for snap in snapshots:
        url = str(snap.get("page_url", "")).strip()
        if not url:
            continue
        norm = _normalize_url(url)
        if norm in url_index:
            continue  # 去重，保留第一条（通常是最新的）
        elements = _parse_dom_summary(snap.get("dom_summary", ""))
        url_index[norm] = elements
        url_to_original[norm] = (
            url  # ★ v18: 保存原始URL（带host），供 _find_alternative_page 返回
        )
        base, tab = _split_base_and_tab(url)
        if base:
            sibling_index.setdefault(base, []).append((tab, norm, url, elements))
    return url_index, sibling_index, url_to_original


# 交互型动作（必须匹配）：click/dblclick/hover/press/focus/check/uncheck 等
# 数据输入型动作（可选匹配）：fill/select/type_text/select_option 等
# → 宽松模式下，只要交互动作能找到替代 tab，就切换；fill 动作让 Step4 运行时自适应
_INTERACTION_ACTION_TYPES = frozenset(
    {
        "click",
        "dblclick",
        "double_click",
        "hover",
        "press",
        "focus",
        "check",
        "uncheck",
        "right_click",
        "upload_file",
        "upload",
    }
)


def _elements_context_text(elements: list[dict[str, Any]], extra: str = "") -> str:
    """★ v26: 由 DOM 元素列表构建页面语境文本（text/placeholder/name/label + tab slug）。

    供"操作对象-页面语境"语义闸门使用，防止跨 tab/跨页面切换时张冠李戴。
    ★ v26b: 排除 role=tab/menu/menuitem 元素 —— Tab/菜单标签是"兄弟页面的名字"，
    不是本页业务对象（如叶组页 DOM 含「辅材配方」Tab 标签，混入会导致闸门失效）。
    """
    _EXCLUDE_ROLES = frozenset({"tab", "menu", "menuitem", "navigation"})
    parts: list[str] = [extra] if extra else []
    for el in elements:
        if not isinstance(el, dict):
            continue
        if str(el.get("role", "") or "").strip().lower() in _EXCLUDE_ROLES:
            continue
        for k in ("text", "placeholder", "name", "label"):
            v = el.get(k)
            if isinstance(v, str) and v.strip():
                parts.append(v.strip())
    return " ".join(parts)


def _find_alternative_tab(
    actions: list[dict[str, Any]],
    current_url: str,
    url_index: dict[str, list[dict[str, Any]]],
    sibling_index: dict[str, list[tuple[str, str, str, list[dict[str, Any]]]]],
    operation: str = "",
) -> str | None:
    """在当前 tab 缺失的控件能在另一个 tab 完整执行所有动作时，返回该 tab 的 URL。

    两阶段匹配：
    1. 严格模式：所有动作必须在替代 tab 中存在（当前行为）
    2. 宽松模式：仅交互动作（click/hover 等）必须在替代 tab 中存在，
       fill/select 等数据输入动作可选（让 Step4 运行时自适应）

    使用 _control_exists_for_action 而非单纯 target_hint 文本匹配，
    以正确处理 upload_file 等控件隐藏在 modal 中的场景。
    返回原始 URL（仅排序参数，保持大小写）。
    """
    base, current_tab = _split_base_and_tab(current_url)
    if not base or base not in sibling_index:
        return None
    siblings = sibling_index[base]

    # 过滤出需要检查的动作（排除 navigate/screenshot/wait/sleep）
    checkable = [
        a
        for a in actions
        if isinstance(a, dict)
        and str(a.get("type", "") or a.get("action", "")).strip()
        not in (
            "navigate",
            "screenshot",
            "wait",
            "sleep",
        )
    ]
    if not checkable:
        return None

    # 分离交互动作和数据输入动作
    interaction_actions = [
        a
        for a in checkable
        if str(a.get("type", "") or a.get("action", "")).strip()
        in _INTERACTION_ACTION_TYPES
    ]
    data_actions = [
        a
        for a in checkable
        if str(a.get("type", "") or a.get("action", "")).strip()
        not in _INTERACTION_ACTION_TYPES
    ]

    # ── 阶段 1：严格模式 ──
    for tab, _norm_url, original_url, elements in siblings:
        if tab == current_tab:
            continue
        # ★ v26: 语义闸门 — 对象与候选 tab 语境不一致则跳过（防止张冠李戴）
        if operation and not _operation_object_matches_page(
            operation, _elements_context_text(elements, extra=tab)
        ):
            _log.warning(
                "[ControlValidation] 语义闸门拦截(严格模式): operation=%r 与 tab=%r 语境不一致，跳过",
                operation[:30],
                tab,
            )
            continue
        all_supported = True
        for a in checkable:
            if not _control_exists_for_action(a, elements):
                all_supported = False
                break
        if all_supported:
            return _sort_url_params_preserve_case(original_url)

    # ── 阶段 2：宽松模式（仅当有交互动作时才尝试） ──
    if not interaction_actions:
        return None

    for tab, _norm_url, original_url, elements in siblings:
        if tab == current_tab:
            continue
        # ★ v26: 语义闸门 — 宽松模式同样校验对象一致性
        if operation and not _operation_object_matches_page(
            operation, _elements_context_text(elements, extra=tab)
        ):
            _log.warning(
                "[ControlValidation] 语义闸门拦截(宽松模式): operation=%r 与 tab=%r 语境不一致，跳过",
                operation[:30],
                tab,
            )
            continue
        all_interactive_supported = True
        for a in interaction_actions:
            if not _control_exists_for_action(a, elements):
                all_interactive_supported = False
                break
        if all_interactive_supported:
            _log.warning(
                "[ControlValidation] 宽松模式匹配: tab=%s（数据输入动作 %d 个将在 Step4 自适应）",
                tab,
                len(data_actions),
            )
            return _sort_url_params_preserve_case(original_url)

    return None


def _find_alternative_page(
    actions: list[dict[str, Any]],
    current_url: str,
    url_index: dict[str, list[dict[str, Any]]],
    sibling_index: dict[str, list[tuple[str, str, str, list[dict[str, Any]]]]],
    url_to_original: dict[str, str] = None,
    operation: str = "",
) -> tuple[str | None, str | None]:
    """★ 通用修复(2026-07-21 v13/v18): 跨页面搜索缺失的 click 控件。

    当 _find_alternative_tab（同 base path 的 tab 切换）失败后调用此函数。
    在所有页面（跨 base path）中搜索缺失的 click 控件。

    根因(3层追问):
      1. 表层: 需求文档说"辅料导入"在"配方管理"，但实际实现在"物料管理-辅料"
      2. 中层: _find_alternative_tab 只在同 base path 的 sibling tab 中搜索，
               不会跨页面（跨 base path）搜索
      3. 根因: 需求文档是大纲，实现以设计为准；需求文档滞后于实现是普遍现象

    通用性:
      - 跨系统通用: 纯 DOM 按钮文本匹配，不依赖业务逻辑
      - 跨行业通用: 适用于所有"需求位置≠实现位置"的场景
      - 跨页面通用: 在所有已爬取的页面中搜索
      - 兼容性: 找到则更新 navigate.url + 标记 location_mismatch；
                找不到则返回 (None, None)，由调用方生成 Agent 探索意图
      - ★ v18: 通过 url_to_original 反查原始URL，返回带host的绝对URL供navigate使用

    用户需求合规:
      "需求文档可能只是个大纲，实际按设计实现，导入实现在物料管理-辅料下，
       这种应该要能识别，但是置信度要扣掉与需求不一致的分数，能触发审核，
       但是不能阻断执行"
      → 本函数实现"识别+切换"，调用方实现"扣分+触发审核"（location_mismatch标记）

    Returns:
        (alternative_url, matched_button_text) 或 (None, None)
        ★ v18: alternative_url 优先返回原始URL（带host），无 url_to_original 时回退 norm_url
    """
    # 收集所有缺失的 click 控件 target_hint
    _CLICK_TYPES = frozenset({"click", "dblclick", "double_click", "right_click"})
    click_targets: list[str] = []
    for a in actions:
        if not isinstance(a, dict):
            continue
        atype = str(a.get("type", "") or a.get("action", "")).strip()
        target = str(a.get("target_hint", "") or "").strip()
        if atype in _CLICK_TYPES and target:
            click_targets.append(target)

    if not click_targets:
        return None, None

    # 当前页面的 base path，避免搜索同 base path（已由 _find_alternative_tab 处理）
    current_base, _ = _split_base_and_tab(current_url)

    # 遍历所有页面（跨 base path）搜索缺失的 click 控件
    # 按 base path 分组，每个 base path 只检查第一个匹配的页面
    searched_bases: set[str] = set()
    for norm_url, elements in url_index.items():
        # 解析这个 URL 的 base path
        # norm_url 是规范化后的 URL（v18后为相对URL，无host）
        _, norm_base = _extract_base_from_norm_url(norm_url)
        if not norm_base:
            continue
        # 跳过同 base path（已由 _find_alternative_tab 处理）
        if norm_base == current_base:
            continue
        # 每个 base path 只搜索一次
        if norm_base in searched_bases:
            continue
        searched_bases.add(norm_base)

        if not elements:
            continue

        # ★ v26: 语义闸门 — 对象与候选页面语境不一致则跳过（防止张冠李戴）
        if operation and not _operation_object_matches_page(
            operation, _elements_context_text(elements, extra=norm_base)
        ):
            _log.warning(
                "[ControlValidation] 语义闸门拦截(跨页面): operation=%r 与页面 %s 语境不一致，跳过",
                operation[:30],
                norm_base,
            )
            continue

        # 检查所有 click target 是否能在这个页面找到
        matched_buttons: list[str] = []
        for target in click_targets:
            for el in elements:
                if _element_matches(el, target):
                    matched_buttons.append(target)
                    break

        if matched_buttons:
            # ★ v18: 优先返回原始URL（带host），无 url_to_original 时回退 norm_url
            original_url = norm_url
            if url_to_original:
                original_url = url_to_original.get(norm_url, norm_url)
            _log.warning(
                "[ControlValidation] 跨页面搜索: 缺失控件 %s 在页面 %s 中找到（位置偏差）",
                click_targets[:3],
                original_url[:80],
            )
            return original_url, matched_buttons[0]

    return None, None


def _extract_base_from_norm_url(norm_url: str) -> tuple[str, str]:
    """从规范化 URL 中提取 base path（去除 query 参数）。

    用于 _find_alternative_page 中跳过同 base path 的搜索。
    """
    if not norm_url:
        return "", ""
    # 去除 query 参数
    base = norm_url.split("?")[0].split("#")[0]
    # 去除协议和域名，只保留路径
    if "://" in base:
        # http://host/path → /path
        try:
            base = "/" + base.split("/", 3)[3]
        except IndexError:
            base = "/"
    return base, base


def _is_upload_action(action: dict[str, Any]) -> bool:
    """判断是否为上传类动作；上传控件常隐藏在 modal 中，需要特殊判定。"""
    atype = str(action.get("type", "") or action.get("action", "")).strip()
    return atype in ("upload_file", "upload")


def _control_exists_for_action(
    action: dict[str, Any], elements: list[dict[str, Any]]
) -> bool:
    """针对动作类型做控件存在性判定。"""
    if not isinstance(action, dict):
        return False
    atype = str(action.get("type", "") or action.get("action", "")).strip()
    target = str(action.get("target_hint", "") or "").strip()

    if atype in ("navigate", "screenshot", "wait", "sleep"):
        return True

    # ★ 方案B (ADR-017, 2026-07-23): intent: 语义意图跳过预校验
    #   根因(§10.9 3层追问):
    #     1. 表层: intent:批量导入辅材 在 DOM 按钮列表中不存在 → 标记为 Agent 探索意图
    #     2. 中层: _control_exists_for_action 用纯文本匹配 DOM，intent: 是协议前缀不是按钮文本
    #     3. 根因: Plan B 的 intent: 是 Step3↔Step4 新契约，意为"Step4 实时找按钮"。
    #              Step3 之所以返回 intent:operation，正是因为预爬取 DOM 按钮没匹配上。
    #              对预爬取 DOM 再校验 intent: 是循环论证，必然失败。
    #   通用性(§12.4 9维度):
    #     1. 跨系统: intent: 协议与系统无关
    #     2. 跨行业: 语义意图适用于任何行业
    #     3. 跨页面: 列表/详情/表单/Tab 页都适用
    #     4. 跨Tab: Step4 实时感知处理 Tab
    #     5. 数据完整性: intent: 不依赖 nav_map 数据
    #     6. 根因溯源: 修协议契约，非打补丁
    #     7. 覆盖性: 影响所有带 intent: 前缀的 action（upload/input/button/search 全覆盖）
    #     8. 全局观: 与 Plan B 一致——intent: 用例决策权交给 Step4 实时感知
    #     9. 职责边界: §12.1 校验前置原则对 intent: 例外（Plan B 明确）
    #   自我反驳三次:
    #     1) 跳过校验会放过幻觉？→ 不会。intent: 是 case_rules 主动生成的语义意图，
    #        不是 AI 编造的按钮名；Step4 实时找不到会真实 FAIL
    #     2) 在 validation 里语义匹配 intent:operation？→ 否。case_rules 已试过
    #        _match_button_by_keywords 失败才返回 intent:，重复匹配浪费
    #     3) 删掉 _control_validation.py？→ 否。它仍校验非 intent: 的精确按钮文本
    #        （捕获 AI 幻觉），并做 tab 自动切换
    if target.startswith("intent:"):
        return True

    # ★ 通用修复(2026-07-22): dialog 内控件跳过校验
    #   根因(3层追问):
    #     1. 表层: upload_file 和弹窗内"确定"按钮被 _control_validation 误判为缺失
    #     2. 中层: DOM 快照只爬取页面初始状态，不包含点击触发按钮后弹出的 dialog 内控件
    #     3. 根因: dialog 内控件本质不在页面 DOM 中，校验它们没有意义
    #   通用性: 适用于所有"点击触发按钮→弹窗内操作"的 CRUD 场景（上传/编辑/详情等）
    #   覆盖性: 影响所有带 in_dialog=True 标记的 action
    #   合规: AGENTS.md §12.4 通用性自检 — 跨系统/跨行业/跨页面通用性
    if action.get("in_dialog") is True:
        return True

    if _is_upload_action(action):
        if target:
            # 有具体 target_hint → 必须精确匹配，不 fallback 到通用触发词
            return _control_exists(target, elements)
        # 无 target_hint → 检查页面是否有任何上传/导入触发控件
        upload_triggers = ["上传", "导入", "upload", "import", "选择文件"]
        for el in elements:
            for kw in upload_triggers:
                if _element_matches(el, kw):
                    return True
        return False

    if not target:
        return True  # 无 target 的动作无法校验，视为存在

    # click 类动作 → 仅匹配可交互元素（按钮/链接/输入框/tab），排除纯文本容器
    if atype in ("click", "dblclick", "hover", "focus", "press"):
        return _control_exists(target, elements, interactive_only=True)

    # ★ fill/select_option 动作 → 仅匹配表单控件（input/select/textarea）
    #   根因(§10.9 5层追问): TC-REQ-018-N01 fill target "【修改配置】 所属模块：模型 输入框"
    #   中的"模型"被 _element_matches 匹配到菜单项"模型管理" → 误判控件存在
    #   修复: 过滤非表单控件元素，避免跨控件类型污染
    #   通用性: 跨系统/行业/页面通用，所有表单控件标签/角色一致
    if atype in ("fill", "select_option", "select", "type", "clear"):
        form_elements = [el for el in elements if _is_form_control(el)]
        if not form_elements:
            # 页面无表单控件 → 标 unverified，Step4 自行探索
            return False
        return _control_exists(target, form_elements)

    return _control_exists(target, elements)


def validate_control_existence(
    all_intents: list[dict],
    project: str = "",
    version_label: str = "",
) -> list[dict]:
    """主入口：验证并修正用例中的控件存在性。

    Returns:
        修正后的意图列表（可能修改 navigate URL 或标记 non_testable）。
    """
    if not all_intents:
        return all_intents

    project = str(project or "").strip()
    version_label = str(version_label or "").strip()
    if not project:
        _log.debug("[ControlValidation] 未提供 project，跳过控件存在性校验")
        return all_intents

    snapshots = _load_dom_snapshots(project, version_label)
    if not snapshots:
        _log.warning("[ControlValidation] 未找到 DOM 快照，跳过控件存在性校验")
        return all_intents

    url_index, sibling_index, url_to_original = _build_snapshot_index(snapshots)
    if not url_index:
        return all_intents

    # ★ v26: 预加载 req_id → operation 映射（语义闸门的数据源，MySQL 唯一事实源）
    _ops_map: dict[str, str] = {}
    with guard("validate_control_existence: _conn = get_connection()", log=_log, level=logging.WARNING):
        _conn = get_connection()
        try:
            _rows = (
                _conn.execute(
                    "SELECT req_id, operation FROM requirements WHERE project=%s",
                    (project,),
                ).fetchall()
                or []
            )
            for _r in _rows:
                _rid = str(
                    (_r.get("req_id") if isinstance(_r, dict) else _r[0]) or ""
                ).strip()
                _op = str(
                    (_r.get("operation") if isinstance(_r, dict) else _r[1]) or ""
                ).strip()
                if _rid and _op:
                    _ops_map[_rid] = _op
        finally:
            with guard("validate_control_existence: _conn.close()", log=_log):
                _conn.close()

    fixed: list[dict] = []
    switched_count = 0
    unverified_count = 0

    for intent in all_intents:
        if not isinstance(intent, dict):
            fixed.append(intent)
            continue

        actions = intent.get("actions")
        if isinstance(actions, str):
            try:
                actions = json.loads(actions)
            except Exception:
                actions = []
        if not isinstance(actions, list):
            fixed.append(intent)
            continue

        # 找到 navigate URL
        nav_url = ""
        nav_idx = -1
        for idx, a in enumerate(actions):
            if (
                isinstance(a, dict)
                and str(a.get("type", "") or a.get("action", "")).strip() == "navigate"
            ):
                nav_url = str(
                    a.get("target_hint", "") or a.get("url", "") or ""
                ).strip()
                if nav_url:
                    nav_idx = idx
                    break

        if not nav_url:
            fixed.append(intent)
            continue

        norm_nav = _normalize_url(nav_url)
        current_elements = url_index.get(norm_nav, [])

        # ★ 检测 DOM 快照是否为导航骨架（SPA 页面未完全加载）
        #    导航骨架特征：交互元素极少（< 5），且大部分是 menuitem/link
        interactive_in_snapshot = sum(
            1 for el in current_elements if _is_interactive(el)
        )
        nav_only_snapshot = len(current_elements) > 0 and interactive_in_snapshot < 5

        missing_controls: list[str] = []
        for a in actions:
            if not isinstance(a, dict):
                continue
            if _control_exists_for_action(a, current_elements):
                continue
            target = str(a.get("target_hint", "") or "").strip()
            if target:
                missing_controls.append(target)

        if not missing_controls:
            fixed.append(intent)
            continue

        # ★ 导航骨架检测：DOM 快照只有导航菜单（SPA 未完全加载），标记 unverified
        if nav_only_snapshot:
            intent["_control_validation"] = {
                "unverified": True,
                "reason": (
                    f"DOM 快照仅包含导航骨架（{len(current_elements)} 个元素，"
                    f"交互元素 {interactive_in_snapshot} 个），SPA 页面未完全加载，"
                    "Step4 将自行探索"
                ),
                "missing_controls": missing_controls,
            }
            _log.info(
                "[ControlValidation] 用例 %s DOM 快照为导航骨架，标记 unverified",
                intent.get("case_id", ""),
            )
            fixed.append(intent)
            continue

        # ★ 区分「页面未爬取」vs「控件不存在」
        #   - 页面未爬取：URL 路径不在任何 DOM 快照中 → 标记 unverified，Step4 自己去探索
        #   - 控件不存在：页面已爬取但控件缺失 → 尝试同路径 tab 切换，失败则标记 non_testable
        page_was_crawled = bool(current_elements)  # URL 精确匹配到了 DOM 快照
        if not page_was_crawled:
            # 检查是否至少同 base path 有 sibling（页面可能爬了但 URL 参数不同）
            base, _ = _split_base_and_tab(nav_url)
            if base and base in sibling_index:
                page_was_crawled = True  # 同 base path 有快照，算已爬取
        if not page_was_crawled:
            intent["_control_validation"] = {
                "unverified": True,
                "reason": f"目标页面未在 DOM 快照中（URL: {nav_url[:100]}），Step4 将自行探索",
                "missing_controls": missing_controls,
            }
            _log.info(
                "[ControlValidation] 用例 %s 页面未爬取，标记 unverified（URL: %s）",
                intent.get("case_id", ""),
                nav_url[:80],
            )
            fixed.append(intent)
            continue

        # ★ 通用修复(2026-07-22): 跨页面解析标记 → 跳过 URL 覆盖
        #   根因: upload_rule._resolve_button_cross_page 已通过 DOM 按钮文本匹配
        #         找到了正确的页面（触发按钮在该页面上存在），_control_validation
        #         不应基于 dialog 内控件缺失覆盖这个正确决策
        #   通用性: 适用于所有规则通过跨页面搜索解析的正确 URL
        #   覆盖性: 影响所有带 _cross_page_resolved=True 标记的用例
        #   合规: AGENTS.md §10.9 职责边界 — _control_validation 不越权覆盖规则的决策
        if intent.get("_cross_page_resolved") is True:
            intent["_control_validation"] = {
                "cross_page_resolved": True,
                "reason": "规则已通过跨页面搜索解析正确页面，跳过 URL 覆盖",
                "missing_controls": missing_controls,
            }
            _log.info(
                "[ControlValidation] 用例 %s 已跨页面解析，跳过 URL 覆盖（缺失控件: %s）",
                intent.get("case_id", ""),
                missing_controls[:3],
            )
            fixed.append(intent)
            continue

        # 尝试在同基础路径的其他 tab 中寻找缺失控件
        # ★ v26: 传入 operation（按 req_id 从 requirements 取），驱动语义闸门
        _intent_operation = _ops_map.get(
            str(intent.get("req_id", "") or "").strip(), ""
        )
        alternative_url = _find_alternative_tab(
            actions, nav_url, url_index, sibling_index, operation=_intent_operation
        )
        if alternative_url:
            # 更新 navigate action
            if nav_idx >= 0:
                actions[nav_idx]["target_hint"] = alternative_url
                if "url" in actions[nav_idx]:
                    actions[nav_idx]["url"] = alternative_url
            intent["actions"] = actions
            intent["_control_validation"] = {
                "switched_tab": True,
                "from_url": nav_url,
                "to_url": alternative_url,
                "missing_controls": missing_controls,
            }
            switched_count += 1
            _log.warning(
                "[ControlValidation] 用例 %s 控件在目标页缺失，自动切换 tab: %s -> %s",
                intent.get("case_id", ""),
                nav_url,
                alternative_url,
            )
            fixed.append(intent)
            continue

        # ★ 通用修复(2026-07-21 v13/v18): 跨页面搜索缺失的 click 控件
        #   当 _find_alternative_tab（同 base path 的 tab 切换）失败后，
        #   在所有页面（跨 base path）中搜索缺失的 click 控件。
        #   根因: 需求文档是大纲，实现以设计为准；需求位置≠实现位置是普遍现象
        #   通用性: 纯 DOM 按钮文本匹配，不依赖业务逻辑
        #   效果: 找到则更新 navigate.url + 标记 location_mismatch（扣 semantic_align 分）
        #         找不到则标记为 Agent 探索意图（降级=失败）
        #   ★ v18: 传入 url_to_original，让 _find_alternative_page 返回带host的原始URL
        #   用户需求合规: "需求文档可能只是个大纲，实际按设计实现，导入实现在物料管理-辅料下，
        #                 这种应该要能识别，但是置信度要扣掉与需求不一致的分数，能触发审核，
        #                 但是不能阻断执行"
        #                 → 识别+切换在此处，扣分+触发审核通过 review_required + confidence_penalty 标记
        alt_page_url, matched_btn = _find_alternative_page(
            actions,
            nav_url,
            url_index,
            sibling_index,
            url_to_original,
            operation=_intent_operation,
        )
        if alt_page_url:
            # 更新 navigate action 到实际页面
            if nav_idx >= 0:
                actions[nav_idx]["target_hint"] = alt_page_url
                if "url" in actions[nav_idx]:
                    actions[nav_idx]["url"] = alt_page_url
            intent["actions"] = actions
            # ★ v18: 标记 location_mismatch + review_required + confidence_penalty
            #   用户要求: 置信度要扣掉与需求不一致的分数，能触发审核，但不阻断执行
            intent["_control_validation"] = {
                "location_mismatch": True,  # ★ 位置偏差标记（扣 semantic_align 分）
                "review_required": True,  # ★ v18 新增: 触发人工审核
                "confidence_penalty": 0.2,  # ★ v18 新增: 扣 20% 置信度
                "from_url": nav_url,  # 需求文档描述的位置
                "to_url": alt_page_url,  # 实际实现位置
                "matched_button": matched_btn,
                "missing_controls": missing_controls,
                "reason": "需求文档描述的位置与实际实现位置不一致，已自动切换到实际页面（扣 20% 置信度，触发审核）",
            }
            switched_count += 1
            _log.warning(
                "[ControlValidation] 用例 %s 跨页面搜索: 控件在 %s 中找到，自动切换页面: %s -> %s（位置偏差，扣 20%% 置信度，触发审核）",
                intent.get("case_id", ""),
                alt_page_url[:60],
                nav_url[:60],
                alt_page_url[:60],
            )
            fixed.append(intent)
            continue

        # 页面已爬取但控件缺失 → 检查是否有 click 动作缺失
        # ★ P0-downgrade-removal(2026-07-29): click 动作的 target_hint 缺失时标记为 Agent 探索意图（降级=失败）
        #   根因: 原代码只标记 unverified=True 让 Step4 自行探索，但 Step4 无法找到不存在的按钮
        #         导致 AI fallback 循环失败（如 REQ-021 的 "确定" 按钮在叶组配方页面不存在）
        #   通用性: 适用于所有系统/行业/页面，基于 DOM 快照校验，不依赖业务逻辑
        #   覆盖性: 覆盖所有经过 validate_control_existence 的用例（AI生成 + 规则生成 + fallback）
        #   合规: AGENTS.md §12.2 规则2 — click target_hint 必须在 DOM 按钮列表中存在
        _CLICK_TYPES = frozenset({"click", "dblclick", "double_click", "right_click"})
        click_missing_targets: list[str] = []
        for a in actions:
            if not isinstance(a, dict):
                continue
            atype = str(a.get("type", "") or a.get("action", "")).strip()
            target = str(a.get("target_hint", "") or "").strip()
            if atype in _CLICK_TYPES and target and target in missing_controls:
                click_missing_targets.append(target)

        if click_missing_targets:
            # ★ P0-exploratory(2026-07-27): 标记为 Agent 探索意图（替代 display 降级）
            #   架构决策(§16.2): 不降级为 display（仅 navigate+screenshot），
            #   而是标记 _exploratory=True，让 Step4 的 AgentExplorer 在浏览器中实时探索执行。
            #   通用性: 跨系统/跨行业/跨页面 — Agent 实时感知，不依赖静态 DOM 快照
            intent["_exploratory"] = True
            intent["control_type"] = "exploratory"
            intent["scenario_type"] = "exploratory"
            intent["low_confidence"] = True
            intent["downgrade_reason"] = (
                f"control_validation: click控件缺失({', '.join(click_missing_targets[:3])})"
                f"→Agent实时探索(§16.2)"
            )
            intent["_explore_task"] = {
                "operation": intent.get("operation", ""),
                "target_page": nav_url,
                "description": intent.get("description", ""),
                "expected_feedback": intent.get("expected_feedback", ""),
                "module": intent.get("module", ""),
                "missing_targets": click_missing_targets[:5],
                "test_method": {
                    "navigation": (
                        f"侧边栏→{intent.get('module', '')}"
                        if intent.get("module", "")
                        else "侧边栏菜单"
                    ),
                    "inputs": {},
                    "verifications": [
                        {
                            "step": 1,
                            "action": "verify",
                            "target": "页面内容",
                            "value": "",
                            "expect": "页面正常加载",
                        },
                        {
                            "step": 2,
                            "action": "verify",
                            "target": "操作结果",
                            "value": "",
                            "expect": intent.get("description", "")
                            or intent.get("operation", "")
                            or "操作成功",
                        },
                    ],
                },
            }
            intent["_control_validation"] = {
                "unverified": True,
                "exploratory": True,
                "reason": (
                    f"click控件缺失，标记为Agent探索意图: {', '.join(click_missing_targets[:5])}"
                    f"{'...' if len(click_missing_targets) > 5 else ''}"
                ),
                "missing_controls": missing_controls,
                "target_url": nav_url,
            }
            unverified_count = unverified_count + 1
            _log.warning(
                "[ControlValidation] ★Agent探索★ 用例 %s click控件缺失，标记为exploratory意图（URL: %s, 缺失: %s）",
                intent.get("case_id", ""),
                nav_url[:80],
                click_missing_targets[:3],
            )
            fixed.append(intent)
            continue

        # 非 click 动作缺失（fill/select 等）→ 保留 unverified，Step4 可自适应
        # （DOM 快照为静态抓取，可能遗漏动态控件/AJAX 加载内容/交互后出现的元素）
        intent["_control_validation"] = {
            "unverified": True,
            "reason": (
                f"目标页面已爬取但缺少所需控件: {', '.join(missing_controls[:5])}"
                f"{'...' if len(missing_controls) > 5 else ''}，Step4 将自行探索"
            ),
            "missing_controls": missing_controls,
            "target_url": nav_url,
        }
        unverified_count = unverified_count + 1
        _log.info(
            "[ControlValidation] 用例 %s 页面已爬取但控件缺失，标记 unverified（URL: %s, 缺失: %s）",
            intent.get("case_id", ""),
            nav_url,
            missing_controls[:3],
        )
        fixed.append(intent)

    if switched_count or unverified_count:
        _log.info(
            "[ControlValidation] 完成: 切换 tab %d 条，标记 unverified %d 条",
            switched_count,
            unverified_count,
        )
    return fixed
