# -*- coding: utf-8 -*-
"""常驻静态门禁：禁止「同一常量/词表被重复定义」（禁止重复造轮子的机械判据）。

背景: 同一判据在多处各自定义后会各自漂移，而每次修复若只在症状层加分支，补丁无穷。
已发生实例: `_navigation_resolver.py` 内 `_DROP_EXPECT_KW` 定义两遍（生成端发断言、
清洗端删断言，两会话各持一份）；`_DENY_RE` 在 step3 两个模块各持一份。
本门禁把「重复定义」变成可拦截的红线，不再依赖人工记忆。

规则
----
A. 同一文件内，同名 `_` 前缀常量（tuple/list/set 字面量，≥2 元素）定义 ≥2 次。
B. 同一多元素字符串常量（≥3 元素）以 `_` 前缀名出现在 ≥2 个不同模块。

B 的存量（跨模块词汇表统一需逐个评审，不能盲目合并）以 **ratchet 基线**放行：
基线内允许存在，**新增即失败**；基线只减不增。
"""
from __future__ import annotations

import ast
import hashlib
import json
import os

ROOT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "src",
)

# ratchet 基线（存量，只减不增）。详见模块 docstring 与 AGENTS.md §6。
_BASELINE_A = frozenset()  # 2026-10-02 全部清零：4 项同文件重复已归并（见 AGENTS.md §6 演进）
_BASELINE_B = frozenset()  # 2026-10-02 全部清零：21 组跨模块词表已归一（权威源 import 引用）


def _canon(node: ast.AST, min_len: int):
    """tuple/list/set 字面量 → 规范化元素 repr 元组（不足 min_len 或非字面量 → None）。"""
    if not isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        return None
    if len(node.elts) < min_len:
        return None
    try:
        value = ast.literal_eval(node)
    except Exception:  # noqa: BLE001
        return None
    if isinstance(value, set):
        value = tuple(sorted(value))
    return tuple(sorted(repr(x) for x in value))


def _digest(canon: tuple) -> str:
    return hashlib.sha1(
        json.dumps(list(canon), ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:16]


def _scan():
    """返回 (rule_a 违规集合, rule_b 违规 {digest: (字面量, 模块集)})。"""
    rule_a: set = set()
    shared: dict = {}
    for dirpath, _dirs, files in os.walk(ROOT):
        for fn in files:
            if not fn.endswith(".py"):
                continue
            path = os.path.join(dirpath, fn)
            rel = os.path.relpath(path, ROOT).replace("\\", "/")
            try:
                tree = ast.parse(
                    open(path, encoding="utf-8-sig").read(), filename=path
                )
            except SyntaxError as e:
                raise AssertionError("语法错误，无法静态门禁: %s: %s" % (rel, e))
            seen: set = set()
            for node in ast.walk(tree):
                if not isinstance(node, ast.Assign):
                    continue
                for target in node.targets:
                    if not (
                        isinstance(target, ast.Name)
                        and target.id.startswith("_")
                        and target.id != "__all__"
                    ):
                        continue
                    canon = _canon(node.value, 2)
                    if not canon:
                        continue
                    key = (rel, target.id, canon)
                    if key in seen:
                        rule_a.add((rel, target.id))
                    else:
                        seen.add(key)
                    if len(canon) >= 3 and all(x.startswith("'") for x in canon):
                        shared.setdefault(canon, set()).add(rel)
    rule_b = {
        _digest(c): (c, mods) for c, mods in shared.items() if len(mods) >= 2
    }
    return rule_a, rule_b


def test_no_new_duplicate_constant_in_same_file():
    """规则 A：同一文件内同名常量不得重复定义（基线为 0 存量除外的历史项）。"""
    rule_a, _ = _scan()
    new = sorted(rule_a - _BASELINE_A)
    assert not new, (
        "同一文件内重复定义了同名常量（应提取为单一模块级常量，禁止两份各自漂移）:\n"
        + "\n".join("  %s :: %s" % (f, n) for f, n in new)
    )


def test_no_new_duplicate_vocabulary_across_modules():
    """规则 B：同一字符串词汇表不得在 ≥2 个模块各自定义（基线内允许，新增即失败）。"""
    _, rule_b = _scan()
    new = sorted(set(rule_b) - _BASELINE_B)
    if new:
        detail = []
        for d in new:
            canon, mods = rule_b[d]
            preview = ", ".join(repr(x)[:24] for x in canon[:6])
            detail.append("  [%s] 出现于 %s\n      元素: %s..." % (d, sorted(mods), preview))
        raise AssertionError(
            "同一词汇表在多个模块被重复定义（应提取到单一事实源后各处 import）:\n"
            + "\n".join(detail)
        )