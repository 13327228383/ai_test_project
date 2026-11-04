# -*- coding: utf-8 -*-
"""多层分类学筛选 — 防止 AI token 爆炸，三级漏斗过滤操作/断言

L1 页面结构扫描 → L2 需求语义分析 → L3 方法适配裁剪
每层只保留与当前上下文相关的操作/断言，最终注入测试用例生成的 prompt 中。
"""

import re
import logging
from typing import Optional
from dataclasses import dataclass, field

_log = logging.getLogger(__name__)

# ── 从分类学中提取的权威定义（本地副本，避免循环导入）──
ACTION_TAXONOMY = {
    # mouse
    "click": {"category": "mouse", "tags": ["button", "a", "div", "li", "td", "tr", "span", "input"]},
    "double_click": {"category": "mouse", "tags": ["div", "tr", "td", "span", "img"]},
    "right_click": {"category": "mouse", "tags": ["div", "tr", "td", "span", "a", "button"]},
    "hover": {"category": "mouse", "tags": ["div", "span", "a", "li", "button", "tr", "td"]},
    "drag": {"category": "mouse", "tags": ["div", "img", "tr", "span"]},
    "scroll": {"category": "mouse", "tags": []},
    "middle_click": {"category": "mouse", "tags": ["a", "div", "span", "button"]},
    "multi_touch_pinch": {"category": "mouse", "tags": ["div", "img", "span"]},
    # form
    "fill": {"category": "form", "tags": ["input", "textarea"]},
    "select_option": {"category": "form", "tags": ["select"]},
    "check": {"category": "form", "tags": ["input"]},
    "uncheck": {"category": "form", "tags": ["input"]},
    "upload_file": {"category": "form", "tags": ["input"]},
    "set_date": {"category": "form", "tags": ["input"]},
    # keyboard
    "press_key": {"category": "keyboard", "tags": []},
    "type_text": {"category": "keyboard", "tags": ["input", "textarea"]},
    "clear": {"category": "keyboard", "tags": ["input", "textarea"]},
    "key_down": {"category": "keyboard", "tags": []},
    "key_up": {"category": "keyboard", "tags": []},
    "wheel": {"category": "keyboard", "tags": []},
    # navigation
    "navigate": {"category": "navigation", "tags": []},
    "wait": {"category": "navigation", "tags": []},
    "page_reload": {"category": "navigation", "tags": []},
    "go_back": {"category": "navigation", "tags": []},
    "go_forward": {"category": "navigation", "tags": []},
    "close_tab": {"category": "navigation", "tags": []},
    # advanced
    "focus": {"category": "advanced", "tags": ["input", "textarea", "select", "button"]},
    "blur": {"category": "advanced", "tags": ["input", "textarea", "select"]},
    "long_press": {"category": "advanced", "tags": ["div", "span", "button"]},
    "switch_tab": {"category": "advanced", "tags": []},
    "switch_to_iframe": {"category": "advanced", "tags": []},
    "switch_to_default": {"category": "advanced", "tags": []},
    "download_file": {"category": "advanced", "tags": ["a"]},
    "drag_offset": {"category": "advanced", "tags": ["div", "img"]},
    "resize_window": {"category": "advanced", "tags": []},
    "swipe": {"category": "advanced", "tags": ["div", "img"]},
    "context_menu_click": {"category": "advanced", "tags": ["div", "span", "a"]},
}

ASSERTION_TAXONOMY = {
    "ui": {
        "text_contains": {"keywords": ["显示", "展示", "包含", "出现", "可见", "查看", "看到"]},
        "text_not_contains": {"keywords": ["不显示", "隐藏", "不可见", "排除", "过滤", "没有"]},
        "text_equals": {"keywords": ["等于", "完全一致", "精确匹配"]},
        "text_regex": {"keywords": ["格式", "正则", "匹配模式"]},
        "element_exists": {"keywords": ["存在", "有", "包含元素"]},
        "element_not_exists": {"keywords": ["不存在", "没有元素", "删除后"]},
        "element_visible": {"keywords": ["可见", "显示"]},
        "element_enabled": {"keywords": ["可用", "启用", "可点击"]},
        "element_checked": {"keywords": ["选中", "勾选", "已选"]},
        "element_count": {"keywords": ["数量", "个数", "条", "行"]},
        "element_attribute": {"keywords": ["属性", "class", "href", "src"]},
        "url_matches": {"keywords": ["跳转", "导航", "URL", "地址", "页面"]},
        "select_value": {"keywords": ["选中值", "下拉值"]},
        "style": {"keywords": ["颜色", "字体", "样式", "CSS"]},
        "toast": {"keywords": ["提示", "弹窗", "通知", "消息", "toast"]},
        "dialog": {"keywords": ["对话框", "确认框", "弹窗"]},
        "loading_complete": {"keywords": ["加载完成", "加载完毕"]},
        "list_order": {"keywords": ["排序", "顺序", "排列"]},
        "pagination": {"keywords": ["分页", "翻页", "页码"]},
        "image_loaded": {"keywords": ["图片", "图像", "加载"]},
        "console_error": {"keywords": ["控制台", "JS错误"]},
        "page_load_time": {"keywords": ["加载时间", "响应速度", "性能"]},
        "animation_complete": {"keywords": ["动画", "过渡"]},
        "no_error": {"keywords": ["无错误", "正常", "成功"]},
    },
    "api": {
        "api_status": {"keywords": ["状态码", "HTTP", "响应码"]},
        "response_time": {"keywords": ["响应时间", "延迟", "API速度"]},
        "api_response_body": {"keywords": ["返回数据", "响应体", "JSON"]},
        "api_header": {"keywords": ["响应头", "Header"]},
        "api_contract": {"keywords": ["契约", "Schema", "接口定义"]},
    },
    "db": {
        "db_record_exists": {"keywords": ["保存", "写入", "入库", "创建", "新增"]},
        "db_record_not_exists": {"keywords": ["删除", "清除", "移除"]},
        "db_record_count": {"keywords": ["数据库数量", "记录数"]},
        "db_field_value": {"keywords": ["更新", "修改", "字段值", "变更"]},
        "db_ui_consistency": {"keywords": ["一致性", "同步", "数据一致"]},
    },
}


# ★ 结构保底操作 (2026-08-30 通用性加固): 无论 L1 页面结构数据是否完备，
#   这些操作对任何 Web 用例的"可达性"都不可或缺，恒不被三层漏斗裁剪。
#   核心目的: 页面结构缺失/抽取不全时，绝不因 L1 只返回基础集而丢掉导航/等待能力，
#   从而避免"可用操作被误删 → 用例缺关键前置步骤 → 假 PASS/假 FAIL"的准确性风险。
_STRUCTURAL_FLOOR: frozenset = frozenset({"navigate", "wait"})


@dataclass
class FilteredTaxonomy:
    """三层筛选后的精简分类学"""
    actions: list[str] = field(default_factory=list)
    assertions: list[str] = field(default_factory=list)
    layer1_hits: dict = field(default_factory=dict)  # L1 匹配详情
    layer2_hits: dict = field(default_factory=dict)  # L2 匹配详情
    layer3_hits: dict = field(default_factory=dict)  # L3 匹配详情
    stats: dict = field(default_factory=dict)
    # ★ 2026-08-30: 页面结构缺失/为空 → 置 True，向调用方如实暴露"L1 仅用了保守默认集"，
    #   避免把"结构未知"误当作"结构已知且无元素"。统计层面供准确率审计。
    page_structure_missing: bool = False


class TaxonomyFilter:
    """三层漏斗筛选器

    每一层都是减法：从全量 → L1保留 → L2保留 → L3保留
    最终注入 AI 的 token 量从 72 种（37+35）降到 10-20 种。
    """

    def __init__(self):
        self.all_actions = list(ACTION_TAXONOMY.keys())
        self.all_assertions = list(ASSERTION_TAXONOMY["ui"].keys()) + \
                              list(ASSERTION_TAXONOMY["api"].keys()) + \
                              list(ASSERTION_TAXONOMY["db"].keys())

    def filter(self, page_structure: Optional[dict] = None,
               requirement: Optional[dict] = None,
               method_name: str = "") -> FilteredTaxonomy:
        """执行三层筛选

        Args:
            page_structure: {elements: [{tag, type, id, class, ...}], has_iframe: bool, ...}
            requirement: {title, description, fields, ...}
            method_name: 选中的测试方法名（用于 L3 适配）
        """
        result = FilteredTaxonomy()

        # ── L1: 页面结构扫描 → 可用操作 ──
        l1_actions = self._layer1_page_scan(page_structure)
        result.layer1_hits = {"actions": l1_actions, "filtered_out": len(self.all_actions) - len(l1_actions)}
        # ★ 2026-08-30: 如实暴露"页面结构缺失/为空"——L1 此时用的是保守默认集，
        #   供调用方/审计方了解减量的根本依赖是否满足。通用性: 跨系统不掩埋结构数据质量。
        _ps = page_structure if isinstance(page_structure, dict) else None
        result.page_structure_missing = bool(
            not _ps or not (_ps.get("elements") or _ps.get("has_iframe") or _ps.get("has_file_download"))
        )

        # ── L2: 需求语义分析 → 相关断言 ──
        l2_assertions = self._layer2_requirement_scan(requirement)
        # L2 也可能影响操作（如需求提到"上传"→ upload_file）
        l2_actions = self._layer2_action_hints(requirement)
        result.layer2_hits = {"assertions": l2_assertions, "actions_hinted": l2_actions,
                              "filtered_out_assertions": len(self.all_assertions) - len(l2_assertions)}

        # ── L3: 方法适配裁剪 ──
        l3_actions, l3_assertions = self._layer3_method_adapt(
            l1_actions, l2_actions, l2_assertions, method_name
        )
        result.layer3_hits = {"actions": l3_actions, "assertions": l3_assertions}

        # 合并结果
        result.actions = l3_actions
        result.assertions = l3_assertions
        result.stats = {
            "total_taxonomy": len(self.all_actions) + len(self.all_assertions),
            "after_l1": len(l1_actions) + len(self.all_assertions),
            "after_l2": len(l1_actions) + len(l2_assertions),
            "after_l3": len(l3_actions) + len(l3_assertions),
            "reduction_ratio": f"{(1 - (len(l3_actions) + len(l3_assertions)) / (len(self.all_actions) + len(self.all_assertions))) * 100:.0f}%",
        }

        _log.info("TaxonomyFilter: %d actions + %d assertions (from %d total, %s reduction)",
                  len(l3_actions), len(l3_assertions),
                  len(self.all_actions) + len(self.all_assertions),
                  result.stats["reduction_ratio"])

        return result

    # ═══════════════════════════════════════════════
    # L1: 页面结构扫描
    # ═══════════════════════════════════════════════
    def _layer1_page_scan(self, page_structure: Optional[dict]) -> list[str]:
        """扫描页面 DOM 结构，确定哪些操作可用"""
        if not page_structure or not page_structure.get("elements"):
            # 无页面结构 → 返回基础操作集
            return ["navigate", "wait", "click", "fill", "page_reload", "go_back", "scroll"]

        elements = page_structure.get("elements", [])
        tag_counts = {}
        input_types = set()

        for el in elements:
            tag = el.get("tag", "").lower()
            tag_counts[tag] = tag_counts.get(tag, 0) + 1
            if tag == "input":
                input_types.add(el.get("type", "text").lower())

        available = set()
        # 基础导航（始终可用）
        available.update(["navigate", "wait", "page_reload", "go_back", "go_forward", "scroll"])

        # 根据页面元素推断可用操作
        for action_id, meta in ACTION_TAXONOMY.items():
            applicable_tags = meta.get("tags", [])
            if not applicable_tags:
                # 无标签限制的操作（如 press_key, scroll 等）→ 始终可用
                if action_id in ("scroll", "press_key", "key_down", "key_up", "wheel",
                                 "navigate", "wait", "page_reload", "go_back", "go_forward",
                                 "close_tab", "switch_tab", "switch_to_iframe", "switch_to_default",
                                 "resize_window"):
                    continue  # 已在基础集中或无需标签
                continue

            for tag in applicable_tags:
                if tag in tag_counts:
                    available.add(action_id)
                    break

        # 特殊处理：input type 决定可用操作
        if "input" in tag_counts:
            if "checkbox" in input_types or "radio" in input_types:
                available.update(["check", "uncheck"])
            if "file" in input_types:
                available.add("upload_file")
            if "date" in input_types or "datetime" in input_types:
                available.add("set_date")
            if "text" in input_types or "email" in input_types or "password" in input_types:
                available.add("fill")
                available.add("type_text")
                available.add("clear")

        if "select" in tag_counts:
            available.add("select_option")

        if "table" in tag_counts:
            available.add("scroll")

        if page_structure.get("has_iframe"):
            available.update(["switch_to_iframe", "switch_to_default"])

        if page_structure.get("has_file_download"):
            available.add("download_file")

        return sorted(available)

    # ═══════════════════════════════════════════════
    # L2: 需求语义分析
    # ═══════════════════════════════════════════════
    def _layer2_requirement_scan(self, requirement: Optional[dict]) -> list[str]:
        """根据需求文本语义，确定需要哪些断言类型"""
        if not requirement:
            # 无需求 → 返回基础断言
            return ["text_contains", "text_not_contains", "element_exists", "url_matches", "no_error"]

        text = " ".join([
            requirement.get("title", ""),
            requirement.get("description", ""),
            " ".join(requirement.get("acceptance_criteria", [])),
        ]).lower()

        needed = set()
        # 基础断言（几乎总是需要）
        needed.update(["text_contains", "element_exists", "no_error"])

        # UI 轨断言
        for assert_type, meta in ASSERTION_TAXONOMY["ui"].items():
            for kw in meta["keywords"]:
                if kw in text:
                    needed.add(assert_type)
                    break

        # API 轨断言
        if any(kw in text for kw in ["接口", "API", "请求", "响应", "HTTP"]):
            for assert_type in ASSERTION_TAXONOMY["api"]:
                for kw in ASSERTION_TAXONOMY["api"][assert_type]["keywords"]:
                    if kw in text:
                        needed.add(assert_type)
                        break

        # DB 轨断言
        if any(kw in text for kw in ["保存", "入库", "数据库", "持久化", "删除", "更新"]):
            for assert_type in ASSERTION_TAXONOMY["db"]:
                for kw in ASSERTION_TAXONOMY["db"][assert_type]["keywords"]:
                    if kw in text:
                        needed.add(assert_type)
                        break

        # 需求中的特殊信号
        if "权限" in text or "角色" in text or "登录" in text:
            needed.add("element_not_exists")
            needed.add("text_not_contains")

        if "表单" in text or "输入" in text or "填写" in text:
            needed.add("element_enabled")

        if "分页" in text or "列表" in text:
            needed.add("pagination")
            needed.add("element_count")

        if "排序" in text:
            needed.add("list_order")

        if "上传" in text:
            needed.add("image_loaded")

        if "弹窗" in text or "提示" in text or "通知" in text:
            needed.add("toast")

        if "性能" in text or "加载" in text:
            needed.add("page_load_time")
            needed.add("loading_complete")

        return sorted(needed)

    def _layer2_action_hints(self, requirement: Optional[dict]) -> list[str]:
        """需求文本中暗示的操作类型"""
        if not requirement:
            return []

        text = " ".join([
            requirement.get("title", ""),
            requirement.get("description", ""),
        ]).lower()

        hints = set()
        hint_map = {
            "上传": "upload_file",
            "下载": "download_file",
            "拖拽": "drag",
            "右键": "right_click",
            "双击": "double_click",
            "悬停": "hover",
            "勾选": "check",
            "取消勾选": "uncheck",
            "日期": "set_date",
            "键盘": "press_key",
            "快捷键": "press_key",
            "刷新": "page_reload",
            "返回": "go_back",
            "iframe": "switch_to_iframe",
            "文件": "upload_file",
            "附件": "upload_file",
            "滚动": "scroll",
            "下拉": "select_option",
            "选择": "select_option",
        }
        for kw, action in hint_map.items():
            if kw in text:
                hints.add(action)

        return sorted(hints)

    # ═══════════════════════════════════════════════
    # L3: 方法适配裁剪
    # ═══════════════════════════════════════════════
    def _layer3_method_adapt(self, l1_actions: list[str], l2_hints: list[str],
                              l2_assertions: list[str], method_name: str) -> tuple[list[str], list[str]]:
        """根据选中的测试方法，进一步裁剪操作/断言集"""
        method_config = self._get_method_config(method_name)

        # 操作：L1 ∩ L2_hints ∪ 方法必需操作
        actions = set(l1_actions)
        if l2_hints:
            actions = actions.intersection(set(l1_actions))  # L2 hints 必须在 L1 中存在
            actions.update(l2_hints)

        # 方法强相关的操作
        actions.update(method_config.get("required_actions", []))

        # ★ 2026-08-30 通用性加固: 结构保底 —— 恒保留导航/等待，
        #   无论 L1 页面结构是否完备/缺失，用例可达性不被裁剪。
        #   这是「减量不减关键能力」的硬保证，防准确性回退。
        actions.update(_STRUCTURAL_FLOOR)

        # 断言：L2 ∪ 方法必需断言
        assertions = set(l2_assertions)
        assertions.update(method_config.get("required_assertions", []))

        # 方法排除的操作（某些方法不需要某些操作）
        actions.difference_update(method_config.get("excluded_actions", []))

        return sorted(actions), sorted(assertions)

    def _get_method_config(self, method_name: str) -> dict:
        """各方法对操作/断言的偏好配置"""
        configs = {
            "equivalence_partitioning": {
                "required_actions": ["fill", "click", "select_option"],
                "required_assertions": ["text_contains", "text_not_contains", "element_exists"],
                "excluded_actions": [],
            },
            "boundary_value": {
                "required_actions": ["fill", "click", "clear", "type_text"],
                "required_assertions": ["text_contains", "text_not_contains", "toast", "no_error", "element_enabled"],
                "excluded_actions": [],
            },
            "cause_effect_graph": {
                "required_actions": ["fill", "click", "select_option", "check", "uncheck"],
                "required_assertions": ["text_contains", "text_not_contains", "element_exists", "element_enabled", "element_checked"],
                "excluded_actions": [],
            },
            "decision_table": {
                "required_actions": ["fill", "click", "select_option", "check"],
                "required_assertions": ["text_contains", "element_exists", "element_checked", "select_value"],
                "excluded_actions": [],
            },
            "orthogonal_array": {
                "required_actions": ["fill", "click", "select_option", "check", "uncheck"],
                "required_assertions": ["text_contains", "element_exists", "no_error"],
                "excluded_actions": [],
            },
            "state_transition": {
                "required_actions": ["click", "select_option", "wait"],
                "required_assertions": ["text_contains", "element_exists", "element_enabled", "select_value", "db_field_value"],
                "excluded_actions": [],
            },
            "scenario_method": {
                "required_actions": ["navigate", "click", "fill", "wait", "scroll"],
                "required_assertions": ["text_contains", "url_matches", "element_exists", "element_visible", "toast", "loading_complete"],
                "excluded_actions": [],
            },
            "error_guessing": {
                "required_actions": ["fill", "click", "clear", "type_text", "upload_file"],
                "required_assertions": ["text_contains", "text_not_contains", "toast", "no_error", "console_error", "element_enabled"],
                "excluded_actions": [],
            },
            "statement_coverage": {
                "required_actions": ["navigate", "click", "fill"],
                "required_assertions": ["text_contains", "element_exists", "url_matches", "no_error"],
                "excluded_actions": [],
            },
            "branch_coverage": {
                "required_actions": ["navigate", "click", "fill", "select_option", "check"],
                "required_assertions": ["text_contains", "text_not_contains", "element_exists", "element_not_exists", "url_matches"],
                "excluded_actions": [],
            },
            "condition_coverage": {
                "required_actions": ["fill", "click", "check", "uncheck", "select_option", "clear"],
                "required_assertions": ["text_contains", "text_not_contains", "element_enabled", "element_checked", "element_exists"],
                "excluded_actions": [],
            },
            "path_coverage": {
                "required_actions": ["navigate", "click", "fill", "select_option", "go_back", "wait"],
                "required_assertions": ["text_contains", "url_matches", "element_exists", "no_error", "loading_complete"],
                "excluded_actions": [],
            },
        }
        return configs.get(method_name, {"required_actions": [], "required_assertions": [], "excluded_actions": []})