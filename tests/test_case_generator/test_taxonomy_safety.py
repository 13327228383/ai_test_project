# -*- coding: utf-8 -*-
"""回归测试：TaxonomyFilter 三层减量的「准确性安全属性」（2026-08-30 通用性加固）。

锁定的不变式（防止将来"为省 token 而误删关键能力"的回退）：
  1. 保底性：任何方法过滤后必须保留 navigate/wait 结构保底操作；
  2. 方法能力：任何方法的 required_actions ⊆ 过滤后 actions（方法自身声明的操作绝不被删）；
  3. 方法断言：任何方法的 required_assertions ⊆ 过滤后 assertions；
  4. 诚实性：page_structure 缺失/为空 → page_structure_missing=True，
     调用方可感知"减量建立在保守默认集上"，而非误认为结构已知且无元素。

通用性：这些不变式不依赖任何具体页面/需求/系统，纯分类学语义。
"""
from src.testing_methods.taxonomy_filter import TaxonomyFilter, _STRUCTURAL_FLOOR

_ALL_METHODS = [
    "equivalence_partitioning",
    "boundary_value",
    "cause_effect_graph",
    "decision_table",
    "orthogonal_array",
    "state_transition",
    "scenario_method",
    "error_guessing",
    "statement_coverage",
    "branch_coverage",
    "condition_coverage",
    "path_coverage",
]

# 两种极端输入：无页面结构 + 有代表性页面结构
_NO_STRUCTURE = None
_RICH_STRUCTURE = {
    "elements": [
        {"tag": "button"}, {"tag": "a"}, {"tag": "input", "type": "text"},
        {"tag": "input", "type": "checkbox"}, {"tag": "input", "type": "date"},
        {"tag": "select"}, {"tag": "table"}, {"tag": "textarea"},
    ],
    "has_iframe": True,
    "has_file_download": True,
}
_REQ = {
    "title": "用户新增并删除书籍",
    "description": "在表单中填写书名与价格，保存后列表可见，可删除",
    "acceptance_criteria": ["保存后提示成功", "删除后列表中不存在"],
}


def test_structural_floor_always_present():
    """任何方法 + 任何页面结构（含缺失），navigate/wait 恒保留。"""
    tf = TaxonomyFilter()
    for page in (_NO_STRUCTURE, _RICH_STRUCTURE):
        for method in _ALL_METHODS:
            f = tf.filter(page_structure=page, requirement=_REQ, method_name=method)
            assert _STRUCTURAL_FLOOR.issubset(set(f.actions)), \
                f"保底操作被删: method={method} page_missing={f.page_structure_missing}"


def test_method_required_operations_never_trimmed():
    """方法自身声明的必需操作/断言绝不被三层漏斗裁剪。"""
    tf = TaxonomyFilter()
    for method in _ALL_METHODS:
        cfg = tf._get_method_config(method)
        for page in (_NO_STRUCTURE, _RICH_STRUCTURE):
            f = tf.filter(page_structure=page, requirement=_REQ, method_name=method)
            missing_actions = set(cfg.get("required_actions", [])) - set(f.actions)
            assert not missing_actions, \
                f"method={method} 必需操作被删: {sorted(missing_actions)}"
            missing_asserts = set(cfg.get("required_assertions", [])) - set(f.assertions)
            assert not missing_asserts, \
                f"method={method} 必需断言被删: {sorted(missing_asserts)}"


def test_reduction_still_happens_but_preserves_quality():
    """减量应明显（token 节省目标达成），但最终集 = 需求语义 ∪ 方法必需，不减关键能力。"""
    tf = TaxonomyFilter()
    total = len(tf.all_actions) + len(tf.all_assertions)
    for page in (_NO_STRUCTURE, _RICH_STRUCTURE):
        f = tf.filter(page_structure=page, requirement=_REQ, method_name="boundary_value")
        kept = len(f.actions) + len(f.assertions)
        assert kept < total, "应产生减量（token 节省）"
        # 边界值方法的语义操作必须齐备
        for needed in {"fill", "click", "clear", "navigate", "wait"}:
            assert needed in f.actions


def test_page_structure_missing_is_honest():
    """结构缺失 → 诚实标记；结构完整 → 不误标。"""
    tf = TaxonomyFilter()
    assert tf.filter(page_structure=None, requirement=_REQ, method_name="click").page_structure_missing is True
    assert tf.filter(page_structure={}, requirement=_REQ, method_name="click").page_structure_missing is True
    assert tf.filter(
        page_structure=_RICH_STRUCTURE, requirement=_REQ, method_name="click"
    ).page_structure_missing is False