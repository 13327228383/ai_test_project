# -*- coding: utf-8 -*-
"""需求拆解规则模块 — 规则优先，AI 兜底。

按流水线分阶段：
  1. preprocess: 预处理（图片过滤）
  2. extract:    提取（标准结构、编号列表）
  3. check:      校验（完整性、宽泛检测、权限条件）
  4. clean:      清洗（去重合并）
  5. label:      标签（功能标签）

每个规则一个文件，注册表自动发现。
后端 API 可查询所有规则清单。
"""

import logging
from src.core.guarded import guard
from typing import Any, Optional

from .base import (
    BaseBreakdownRule,
    PreprocessRule,
    ExtractRule,
    CheckRule,
    CleanRule,
    LabelRule,
)
from .table_rules import get_table_rules

_log = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# 规则注册表（自动发现）
# ──────────────────────────────────────────────

_ALL_RULES: list[BaseBreakdownRule] = []
_RULES_BY_STAGE: dict[str, list[BaseBreakdownRule]] = {}
_RULE_MAP: dict[str, BaseBreakdownRule] = {}
_INITIALIZED = False


def _discover_rules() -> list[BaseBreakdownRule]:
    """自动发现 breakdown_rules 目录下所有规则类并实例化。"""
    import importlib
    import pkgutil
    import inspect

    rules = []
    for _, modname, _ in pkgutil.iter_modules(__path__, prefix=__name__ + "."):
        if modname.endswith(".base"):
            continue
        try:
            mod = importlib.import_module(modname)
        except Exception:
            _log.exception("加载规则模块失败: %s", modname)
            continue

        for _name, obj in inspect.getmembers(mod, inspect.isclass):
            if obj.__module__ != mod.__name__:
                continue
            if not issubclass(obj, BaseBreakdownRule) or obj is BaseBreakdownRule:
                continue
            # 跳过中间基类
            if obj in (PreprocessRule, ExtractRule, CheckRule, CleanRule, LabelRule):
                continue
            if getattr(obj, "rule_id", ""):
                with guard("_discover_rules: instance = obj()", log=_log, level=logging.ERROR):
                    instance = obj()
                    rules.append(instance)
                    _log.debug(
                        "注册规则: %s (%s) [%s]",
                        instance.rule_id,
                        instance.name,
                        instance.stage,
                    )

    rules.sort(key=lambda r: r.priority, reverse=True)
    return rules


def _ensure_rules():
    global _ALL_RULES, _RULES_BY_STAGE, _RULE_MAP, _INITIALIZED
    if _INITIALIZED:
        return
    _ALL_RULES = _discover_rules()
    _RULE_MAP = {r.rule_id: r for r in _ALL_RULES}
    _RULES_BY_STAGE = {}
    for r in _ALL_RULES:
        _RULES_BY_STAGE.setdefault(r.stage, []).append(r)
    _INITIALIZED = True
    _log.info(
        "需求拆解规则加载完成: %d 条 (%d stages)",
        len(_ALL_RULES),
        len(_RULES_BY_STAGE),
    )


# ──────────────────────────────────────────────
# 公共 API
# ──────────────────────────────────────────────


def get_all_rules() -> list[BaseBreakdownRule]:
    _ensure_rules()
    return list(_ALL_RULES)


def get_rules_by_stage(stage: str) -> list[BaseBreakdownRule]:
    _ensure_rules()
    return list(_RULES_BY_STAGE.get(stage, []))


def get_rule_meta_list() -> list[dict[str, Any]]:
    """获取所有规则的元数据列表（供 API 返回）。"""
    _ensure_rules()
    return [r.meta() for r in _ALL_RULES]


# ──────────────────────────────────────────────
# 流水线执行器
# ──────────────────────────────────────────────


def run_preprocess(text: str) -> str:
    """运行所有预处理规则。"""
    _ensure_rules()
    for rule in _RULES_BY_STAGE.get("preprocess", []):
        if isinstance(rule, PreprocessRule):
            text = rule.apply(text)
    return text


def run_extract(chunk_text: str, chunk_id: str) -> list[dict[str, Any]]:
    """运行所有提取规则，合并结果。"""
    _ensure_rules()
    all_triples = []
    for rule in _RULES_BY_STAGE.get("extract", []):
        if isinstance(rule, ExtractRule):
            with guard("run_extract: triples = rule.apply(chunk_text, chu", log=_log, level=logging.ERROR):
                triples = rule.apply(chunk_text, chunk_id)
                if triples:
                    _log.info("提取规则 [%s] 命中: %d 条", rule.rule_id, len(triples))
                    all_triples.extend(triples)
    return all_triples


def run_check(req: dict[str, Any]) -> list[dict[str, Any]]:
    """运行所有校验规则，返回问题列表。"""
    _ensure_rules()
    all_issues = []
    for rule in _RULES_BY_STAGE.get("check", []):
        if isinstance(rule, CheckRule):
            with guard("run_check: issues = rule.apply(req)", log=_log, level=logging.ERROR):
                issues = rule.apply(req)
                if issues:
                    _log.debug(
                        "校验规则 [%s] 发现问题: %d 条", rule.rule_id, len(issues)
                    )
                    all_issues.extend(issues)
    return all_issues


def run_clean(triples: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """运行所有清洗规则。"""
    _ensure_rules()
    for rule in _RULES_BY_STAGE.get("clean", []):
        if isinstance(rule, CleanRule):
            with guard("run_clean: triples = rule.apply(triples)", log=_log, level=logging.ERROR):
                triples = rule.apply(triples)
    return triples


def run_label(
    triples: list[dict[str, Any]], context: dict[str, Any] = None
) -> list[dict[str, Any]]:
    """运行所有标签规则。"""
    _ensure_rules()
    for rule in _RULES_BY_STAGE.get("label", []):
        if isinstance(rule, LabelRule):
            with guard("run_label: triples = rule.apply(triples, contex", log=_log, level=logging.ERROR):
                triples = rule.apply(triples, context)
    return triples


def run_all_pipeline(
    chunk_text: str,
    chunk_id: str,
    table_meta: Optional[dict[str, Any]] = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """运行完整流水线：表格规则 → 预处理 → 提取 → 校验 → 清洗 → 标签。

    Args:
        chunk_text: 切片文本内容。
        chunk_id: 切片唯一标识。
        table_meta: 表格元数据（headers/rows/title），来自 _fd_preprocess 的 table chunk。

    Returns:
        (triples, issues): 提取的三元组列表和校验问题列表
    """
    # 0. 表格规则优先（仅当 table_meta 存在时）
    if table_meta:
        for rule in get_table_rules():
            try:
                triples = rule(table_meta, chunk_id)
                if triples:
                    _log.debug(
                        "[breakdown_rules] 表格规则 %s 命中 chunk=%s",
                        rule.__name__,
                        chunk_id,
                    )
                    # 表格规则直接返回结果，不走后续流水线（表格数据已是结构化三元组）
                    return triples, []
            except Exception as e:
                _log.debug(
                    "[breakdown_rules] 表格规则 %s 异常: %s",
                    rule.__name__,
                    str(e)[:80],
                    exc_info=True,
                )

    # 1. 预处理
    cleaned = run_preprocess(chunk_text)

    # 2. 提取
    triples = run_extract(cleaned, chunk_id)

    # 3. 校验（每个三元组）
    all_issues = []
    for t in triples:
        issues = run_check(t)
        if issues:
            all_issues.extend(issues)

    # 4. 清洗
    triples = run_clean(triples)

    # 5. 标签
    triples = run_label(triples, {"chunk_id": chunk_id})

    return triples, all_issues
