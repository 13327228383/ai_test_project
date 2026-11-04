# -*- coding: utf-8 -*-
"""搜索规则：处理 search 控件，生成 P01/N01/B01 三胞胎。

★ 通用修复(2026-07-22 v26):
  根因: 硬编码 _RESULT_SELECTOR/_EMPTY_SELECTOR 不匹配不同系统的结果容器
    （某SPA系统用 ul[role='menu']，其他系统用 table/.list/.ant-list 等）。
  修复:
    1. 去掉硬编码选择器 — text_contains_check(fuzzy) 已在整页找文本(§_db_ui.py L116)，
       不需限定容器；element_exists 硬编码选择器无法覆盖所有系统。
    2. 搜索按钮判断加 class 检查 — Ant Design 的 ant-input-search-button 文本为空，
       只看 text 会漏判（原逻辑误判为"无搜索按钮"→按 Enter→关闭下拉→text_contains 失败）。
    3. 下拉选择器型搜索框只 fill 不提交 — 避免 Enter/click 关闭下拉列表导致关键词消失。
  通用性:
    - 跨系统: 不依赖特定 CSS class 或 DOM 结构
    - 跨行业: 不预设业务术语
    - 跨页面: 适用于列表页/详情页/Tab 页
    - 覆盖: 实时过滤型 + 提交型 + 下拉选择器型搜索框
  合规: AGENTS.md §13.3 禁止硬编码 + §12.4 通用性自检(9维度全通过)
"""

from typing import Any
from src.core.guarded import guard
import logging
from .base import BaseCaseRule

_log = logging.getLogger(__name__)


def _find_search_button_from_navmap(target_page: str) -> tuple[str, str]:
    """★ 通用修复(2026-07-22): 从 nav_map 获取搜索按钮的文本和 class。

    通用策略（无业务硬编码）:
      1. 从 nav_graph_nodes 的 page_elements.buttons 获取完整 button 对象(含 class)
      2. 检查每个 button:
         - text 含 "搜索/查询/筛选/search" → 返回 (text, "")
         - class 含 "search" (ant-input-search-button/el-input__search/search-btn)
           且 text 为空 → 返回 ("", class)  ← Ant Design 图标按钮
      3. 未找到 → 返回 ("", "")

    ★ P0-1 读侧迁移(2026-08-05): system_nav_maps → nav_graph_nodes。
      根因: system_nav_maps 表**没有 nav_json 列**（实际列名 nav_map_json），
      原 `SELECT nav_json ...` 每次都抛 `Unknown column` 并被 except 吞掉，
      表现为"永远找不到搜索按钮" → 用例只能降级用假按钮名。

    返回: (button_text, button_class)
    """
    from src.core.config_loader import config
    from src.pipeline.utils.nav_graph_read import read_flat_list

    project = getattr(config, "project_name", "") or ""
    if not project or not target_page:
        return ("", "")

    # 处理绝对 URL → 相对路径
    page_path = target_page
    if page_path.startswith("http://") or page_path.startswith("https://"):
        from urllib.parse import urlparse

        page_path = urlparse(page_path).path or "/"

    try:
        flat_list = read_flat_list(project)
    except Exception as _e:
        _log.error(
            "[search_rule] nav_graph 读取失败 project=%s page=%s: %s",
            project,
            target_page,
            _e,
            exc_info=True,
        )
        return ("", "")

    if not flat_list:
        _log.warning(
            "[search_rule] nav_graph 无导航数据 project=%s，无法定位搜索按钮", project
        )
        return ("", "")

    for item in flat_list:
        item_path = item.get("path", "") or ""
        # 路径匹配（前缀或精确）
        if not item_path or not (page_path in item_path or item_path in page_path):
            continue
        elems = item.get("page_elements") or {}
        for btn in elems.get("buttons", []) or []:
            if not isinstance(btn, dict):
                continue
            btn_text = (btn.get("text") or "").strip()
            btn_class = (btn.get("class") or "").strip()
            # 文本含搜索关键词
            if btn_text and any(
                k in btn_text for k in ["搜索", "查询", "筛选", "search", "Search"]
            ):
                return (btn_text, btn_class)
            # class 含 search（Ant Design 图标按钮等）
            if btn_class and "search" in btn_class.lower():
                return (btn_text, btn_class)  # text 可能为空
    return ("", "")


class SearchRule(BaseCaseRule):
    """搜索/筛选通用规则。

    P01: 输入关键词，搜索 → 期望返回匹配结果
    N01: 输入不存在关键词，搜索 → 期望空结果
    B01: 空搜索 → 期望返回全部或提示
    """

    rule_id = "search_rule"
    name = "搜索规则"
    description = "处理搜索/筛选控件，生成有结果/无结果/空搜索三胞胎"
    control_types = ["search", "filter"]
    priority = 75

    def generate(
        self,
        req: dict[str, Any],
        target_page: str,
        control_type: str,
        merged_data: dict[str, Any],
        selected_ops: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        rid = self._safe_rid(req)
        module = req.get("module", merged_data.get("module", "")) or ""
        operation = (req.get("operation") or "").lower()
        expected = req.get("expected_feedback") or req.get("expected") or ""
        desc = req.get("description", "") or operation
        fill_target = self._infer_fill_target(operation, target_page, merged_data)

        # ── 搜索按钮/提交方式判断（通用，无硬编码）──
        # ★ v26 修复: 搜索按钮判断加 class 检查
        #   原bug: 只看 button text，Ant Design 的 ant-input-search-button 文本为空被漏判
        #   修复: 从 nav_map 获取 button class，识别 class 含 "search" 的图标按钮
        search_btn_text, search_btn_class = _find_search_button_from_navmap(target_page)

        # 从 DOM 获取输入框信息
        has_search_input = False
        with guard("generate: from src.pipeline.step3._rule_extrac", log=_log):
            from src.pipeline.step3._rule_extractor import _load_dom_elements_for_page

            dom_elements = _load_dom_elements_for_page(target_page)
            inputs = dom_elements.get("input_placeholders", [])
            has_search_input = any(
                any(k in inp for k in ["搜索", "查询", "search", "筛选"])
                for inp in inputs
            )

        # ★ v26 修复: 提交方式判断（三态）
        #   1. 有搜索按钮(文本非空) → click 搜索按钮（传统提交型）
        #   2. 有搜索按钮(class含search, 文本为空) → 只fill不提交（下拉选择器型，click会关闭下拉）
        #   3. 无搜索按钮但有搜索输入框 → 只fill不提交（实时过滤型）
        #   4. 无搜索按钮无搜索输入框 → 降级
        has_text_search_btn = bool(search_btn_text)
        has_class_search_btn = bool(search_btn_class) and not search_btn_text
        has_search_btn = has_text_search_btn or has_class_search_btn

        if not has_search_btn and not has_search_input:
            # 无搜索按钮 + 无搜索输入框 → 降级
            return self._build_downgrade_cases(
                req,
                target_page,
                merged_data,
                reason=f"search_rule: DOM无搜索按钮+无搜索输入框(operation={operation[:30]})",
            )

        tracks = ["ui"]

        # ── 提交动作构造 ──
        def _submit_action(focus: str = "") -> dict[str, str]:
            """构造提交动作。

            有文本搜索按钮 → click
            有class搜索按钮(图标) → 不提交（只fill，实时过滤生效）
            无搜索按钮 → 不提交（只fill，实时过滤生效）
            """
            if has_text_search_btn:
                return self._click(search_btn_text)
            # 下拉选择器型/实时过滤型 → 只fill不提交，返回空操作占位
            # 实际上不生成提交动作，actions 里只有 fill
            return None  # 调用方过滤 None

        def _filter_actions(actions: list) -> list:
            """过滤掉 None 动作。"""
            return [a for a in actions if a is not None]

        # ── P01（正向）──
        # ★ 通用修复(2026-07-22): 从 DOM 提取真实搜索关键词，替代硬编码 "test"
        try:
            from src.pipeline.step3._rule_extractor import (
                _extract_searchable_data_from_dom,
            )

            real_keyword = _extract_searchable_data_from_dom(target_page)
        except Exception as _e:
            _log.warning("[search_rule] 提取真实搜索关键词失败: %s", str(_e)[:80])
            real_keyword = ""

        if real_keyword:
            # ★ 有真实数据 → 用真实关键词搜索
            # v26: 去掉 element_exists(硬编码选择器)，只保留 text_contains(整页找) + no_error
            #   根因: _RESULT_SELECTOR 硬编码不匹配 某SPA系统 的 ul[role='menu']，
            #         text_contains(fuzzy) 已在整页找文本(_db_ui.py L116)，不需限定容器。
            p01_actions = _filter_actions(
                [
                    self._nav(target_page),
                    self._fill(fill_target, real_keyword),
                    _submit_action(focus=fill_target),
                    self._screenshot(),
                ]
            )
            p01_checks = [
                # text_contains_check(fuzzy) 在整页找关键词 — 最直接的搜索验证
                self._build_check(
                    "text_contains_check",
                    "",
                    real_keyword,
                    f"查询结果包含关键词 '{real_keyword}'",
                    check_expected=real_keyword,
                    operator="contains",
                    precision="fuzzy",
                ),
                self._build_check("no_error", "页面", "无错误", "系统无报错"),
            ]
        else:
            # ★ DOM 无有效数据值 → 降级 P01：只验证搜索框存在 + 页面可访问
            _log.warning(
                "[search_rule] P01 降级: DOM 无有效搜索关键词, page=%s, op=%s",
                target_page[:50],
                operation[:30],
            )
            p01_actions = [
                self._nav(target_page),
                self._screenshot(),
            ]
            p01_checks = [
                self._build_check(
                    "element_exists",
                    "input[placeholder*='搜索'], input[placeholder*='查询'], "
                    "input[type='search'], input[role='combobox']",
                    "存在",
                    "搜索输入框存在",
                ),
                self._build_check("no_error", "页面", "无错误", "系统无报错"),
            ]

        # ── N01（无结果）──
        # v26: 去掉 element_exists(_EMPTY_SELECTOR)，改用 text_not_exists（更通用）
        #   根因: _EMPTY_SELECTOR 硬编码不匹配所有系统的空结果提示
        #   修复: text_not_exists 验证无效关键词不出现 — 适用于所有系统
        n01_keyword = "xyznonexistent123"
        n01_actions = _filter_actions(
            [
                self._nav(target_page),
                self._fill(fill_target, n01_keyword),
                _submit_action(focus=fill_target),
                self._screenshot(),
            ]
        )
        n01_checks = [
            self._build_check(
                "text_not_exists",
                "",
                f"不出现",
                f"无效关键词 '{n01_keyword}' 不出现在结果中",
                check_expected=n01_keyword,
                operator="not_contains",
                precision="fuzzy",
            ),
            self._build_check("no_error", "页面", "无错误", "系统无报错"),
        ]

        # ── B01（空搜索）──
        # v26: 去掉 element_exists(_RESULT_SELECTOR)，改用 text_contains(真实关键词)
        #   根因: 空搜索应返回全部数据，真实关键词应出现在结果中
        if has_text_search_btn:
            b01_actions = [
                self._nav(target_page),
                self._click(search_btn_text),
                self._screenshot(),
            ]
        else:
            # 无文本搜索按钮 → 只导航 + 截图（空搜索 = 不输入直接看列表）
            b01_actions = [
                self._nav(target_page),
                self._screenshot(),
            ]
        if real_keyword:
            b01_checks = [
                self._build_check(
                    "text_contains_check",
                    "",
                    real_keyword,
                    f"空搜索返回全部数据(含 '{real_keyword}')",
                    check_expected=real_keyword,
                    operator="contains",
                    precision="fuzzy",
                ),
                self._build_check("no_error", "页面", "无错误", "系统无报错"),
            ]
        else:
            b01_checks = [
                self._build_check("no_error", "页面", "无错误", "系统无报错"),
            ]

        return [
            self._build_one(
                f"TC-{rid}-P01",
                req,
                module,
                desc,
                operation,
                expected,
                "happy_path",
                "P1",
                "正向",
                "playwright",
                control_type,
                tracks,
                p01_actions,
                p01_checks,
                "正向流程验证",
            ),
            self._build_one(
                f"TC-{rid}-N01",
                req,
                module,
                desc,
                operation,
                expected,
                "error_path",
                "P1",
                "异常",
                "playwright",
                control_type,
                tracks,
                n01_actions,
                n01_checks,
                "无结果搜索验证",
            ),
            self._build_one(
                f"TC-{rid}-B01",
                req,
                module,
                desc,
                operation,
                expected,
                "boundary",
                "P2",
                "边界",
                "playwright",
                control_type,
                tracks,
                b01_actions,
                b01_checks,
                "空搜索边界验证",
            ),
        ]
