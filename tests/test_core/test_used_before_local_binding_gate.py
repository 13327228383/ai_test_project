# -*- coding: utf-8 -*-
"""静态门禁：函数内不得「名字在局部绑定之前被使用」（UnboundLocalError 缺陷类）。

背景（缺陷形状）
----------------
Python 中只要一个名字在函数体内**任意位置**被绑定，该名字在整个函数作用域内即为
局部变量；若某条执行路径在绑定**之前**读取它，运行时抛 ``UnboundLocalError``。

本仓库真实踩过（2026-10-16）：`_single_nav_helpers.py::_handle_browser_navigate`
顶部已用模块级 ``from src.core.page_ready import wait_page_ready``；
后文同一函数内又写了一次局部 ``from ... import wait_page_ready``。
于是该名字变局部 → 前置的 ``await wait_page_ready(page)`` 抛
``cannot access local variable 'wait_page_ready' where it is not associated with a value``
→ **每一次 navigate 动作都失败** → 结构化动作整体失败回落 AI 兜底 → AI 运行时禁用
→ 用例批量记 SKIP/ERROR。全仓 1172 文件仅此 1 处，却几乎废掉全部用例的导航步骤。

判据（纯静态，作用域正确）
--------------------------
对每个函数 F（不下探嵌套函数 / lambda / 推导式的作用域）：
  取 F 内该名字的**最早绑定行** b；若存在该名字的 Load **使用行** u 且 u < b → 违例。
  跳过：形参名、``global`` / ``nonlocal`` 声明名、内置名。

豁免
----
无。当前全仓违例数 = 0，故为硬门禁（不走 ratchet 基线）。
"""

from __future__ import annotations

import ast
import builtins
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"

_BUILTINS = set(dir(builtins)) | {
    "self",
    "cls",
    "__name__",
    "__file__",
    "__doc__",
    "__builtins__",
    "__class__",
}

# 独立作用域的节点：扫描外层函数时不下探
_NESTED_SCOPES = (
    ast.FunctionDef,
    ast.AsyncFunctionDef,
    ast.Lambda,
    ast.ListComp,
    ast.SetComp,
    ast.DictComp,
    ast.GeneratorExp,
)


def _target_names(t: ast.AST) -> list[str]:
    if isinstance(t, ast.Name):
        return [t.id]
    if isinstance(t, (ast.Tuple, ast.List)):
        out: list[str] = []
        for e in t.elts:
            out += _target_names(e)
        return out
    if isinstance(t, ast.Starred):
        return _target_names(t.value)
    return []


def _bind_from_stmt(node: ast.AST) -> list[str]:
    """单个语句节点直接引入的局部绑定名（不下探子节点）。"""
    out: list[str] = []
    if isinstance(node, ast.Assign):
        for t in node.targets:
            out += _target_names(t)
    elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
        out += _target_names(node.target)
    elif isinstance(node, ast.Import):
        for a in node.names:
            out.append(a.asname or a.name.split(".")[0])
    elif isinstance(node, ast.ImportFrom):
        for a in node.names:
            if a.name != "*":
                out.append(a.asname or a.name)
    elif isinstance(node, (ast.For, ast.AsyncFor)):
        out += _target_names(node.target)
    elif isinstance(node, (ast.With, ast.AsyncWith)):
        for it in node.items:
            if it.optional_vars is not None:
                out += _target_names(it.optional_vars)
    elif isinstance(node, ast.ExceptHandler):
        if node.name:
            out.append(node.name)
    elif isinstance(node, ast.Delete):
        for t in node.targets:
            out += _target_names(t)
    return [n for n in out if n]


def _collect_scope(body: list[ast.AST], params: set[str], nonlocals_: set[str]):
    """在单个函数作用域内收集 (name -> 最早绑定行) 与 (name -> [使用行])。"""
    bind: dict[str, int] = {}
    use: dict[str, list[int]] = {}
    stack: list[ast.AST] = list(body)
    seen: set[int] = set()
    while stack:
        n = stack.pop()
        if id(n) in seen:
            continue
        seen.add(id(n))
        ln = getattr(n, "lineno", None)
        if isinstance(n, _NESTED_SCOPES):
            # 嵌套作用域：只有 def/class 名对外层构成绑定，内部一律不下探
            nm = getattr(n, "name", None)
            if nm and nm not in params and nm not in nonlocals_ and nm not in _BUILTINS:
                bind[nm] = min(bind.get(nm, 10**9), getattr(n, "lineno", 10**9))
            continue
        if isinstance(n, ast.Name) and ln is not None:
            if isinstance(n.ctx, ast.Load):
                use.setdefault(n.id, []).append(ln)
            elif n.id not in params and n.id not in nonlocals_ and n.id not in _BUILTINS:
                bind[n.id] = min(bind.get(n.id, 10**9), ln)
            continue
        for nm in _bind_from_stmt(n):
            if nm not in params and nm not in nonlocals_ and nm not in _BUILTINS:
                bind[nm] = min(bind.get(nm, 10**9), getattr(n, "lineno", 10**9))
        for ch in ast.iter_child_nodes(n):
            stack.append(ch)
    return bind, use


def _scan_fn(fn: ast.AST, filename: str, out: list[str]) -> None:
    a = fn.args  # type: ignore[attr-defined]
    params = {x.arg for grp in (a.posonlyargs, a.args, a.kwonlyargs) for x in grp}
    if a.vararg:
        params.add(a.vararg.arg)
    if a.kwarg:
        params.add(a.kwarg.arg)
    nonlocals_: set[str] = set()
    for n in ast.walk(fn):
        if isinstance(n, (ast.Global, ast.Nonlocal)):
            nonlocals_.update(n.names)
    bind, use = _collect_scope(fn.body, params, nonlocals_)  # type: ignore[attr-defined]
    for nm, bl in bind.items():
        for ul in use.get(nm, []):
            if ul < bl:
                out.append(
                    f"{filename}:{ul} fn={fn.name} name={nm!r} used@{ul} < bound@{bl}"  # type: ignore[attr-defined]
                )
                break


def _walk(node: ast.AST, filename: str, out: list[str]) -> None:
    for ch in ast.iter_child_nodes(node):
        if isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef)):
            _scan_fn(ch, filename, out)
        _walk(ch, filename, out)


def _scan_repo() -> list[str]:
    out: list[str] = []
    for p in SRC.rglob("*.py"):
        if "__pycache__" in str(p) or ".bak" in p.name:
            continue
        try:
            tree = ast.parse(p.read_text(encoding="utf-8", errors="ignore"))
        except SyntaxError:
            continue
        _walk(tree, str(p.relative_to(ROOT)), out)
    return out


def test_no_used_before_local_binding():
    """全仓 src/ 不得存在「名字在局部绑定之前被使用」的函数。"""
    offenders = _scan_repo()
    assert not offenders, (
        "存在 UnboundLocalError 缺陷类（名字在局部绑定之前被使用）：\n  "
        + "\n  ".join(offenders)
    )


def test_gate_detects_the_known_pattern():
    """自证门禁有效：对已知缺陷形状必须报错（防止门禁退化为空转）。"""
    src = (
        "from pkg import wait_ready\n"
        "async def nav(page):\n"
        "    await wait_ready(page)\n"
        "    if page:\n"
        "        from pkg import wait_ready\n"
        "        await wait_ready(page)\n"
    )
    out: list[str] = []
    _walk(ast.parse(src), "inline", out)
    assert out and "wait_ready" in out[0], f"门禁未检出已知缺陷形状: {out}"
