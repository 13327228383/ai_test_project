"""全量源码索引生成器。

扫描仓库内的 Python 源文件，用 AST 提取每个模块的职责要素
（模块文档串、顶层类及其文档串、顶层函数及签名、对外依赖），
输出 JSON 供写作引用，并输出按目录聚合的 Markdown 概览。

用途：为《E2E 智能测试引擎 技术设计与实现》的附录"文件职责索引"提供
可复核的原始数据，避免人工列举造成遗漏。
"""

from __future__ import annotations

import ast
import json
import os
import sys
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
OUT_DIR = os.path.join(ROOT, "docs", "book", "_build")

SKIP_DIRS = {"__pycache__", ".git", "build", "dist", "node_modules", "site-packages"}


def first_line(text: str | None, limit: int = 160) -> str:
    """取文档串首段（到第一个空行为止），压缩空白。"""
    if not text:
        return ""
    out: list[str] = []
    for raw in text.strip().splitlines():
        line = raw.strip()
        if not line:
            if out:
                break
            continue
        out.append(line)
    joined = " ".join(out).strip()
    return joined[:limit]


def signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    args = node.args
    parts: list[str] = []
    pos = list(getattr(args, "posonlyargs", [])) + list(args.args)
    for i, a in enumerate(pos):
        name = a.arg
        if i < len(pos) - len(args.defaults):
            parts.append(name)
        else:
            parts.append(name + "=")
    if args.vararg:
        parts.append("*" + args.vararg.arg)
    elif args.kwonlyargs:
        parts.append("*")
    for a, d in zip(args.kwonlyargs, args.kw_defaults):
        parts.append(a.arg + ("=" if d is not None else ""))
    if args.kwarg:
        parts.append("**" + args.kwarg.arg)
    prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
    return f"{prefix} {node.name}({', '.join(parts)})"


def module_imports(tree: ast.Module) -> list[str]:
    """只收集 src.* 的内部依赖，用于绘制模块间引用。"""
    deps: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            for a in node.names:
                deps.add(a.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                if node.level:
                    deps.add("." * node.level + node.module)
                else:
                    deps.add(node.module)
    return sorted(d for d in deps if d.startswith("src."))


def parse(path: str) -> dict | None:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            src = fh.read()
    except (OSError, UnicodeDecodeError):
        return None
    try:
        tree = ast.parse(src)
    except SyntaxError as exc:
        return {"parse_error": f"{exc.__class__.__name__}: {exc.msg}", "lines": src.count("\n")}
    classes = []
    funcs = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            methods = [n.name for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
            classes.append({
                "name": node.name,
                "doc": first_line(ast.get_docstring(node), 120),
                "methods": len(methods),
                "bases": [b.id if isinstance(b, ast.Name) else (b.attr if isinstance(b, ast.Attribute) else "?") for b in node.bases],
            })
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            funcs.append({"sig": signature(node), "doc": first_line(ast.get_docstring(node), 120)})
    return {
        "lines": src.count("\n") + 1,
        "doc": first_line(ast.get_docstring(tree), 240),
        "classes": classes,
        "funcs": funcs,
        "deps": module_imports(tree),
    }


def main() -> int:
    records: dict[str, dict] = {}
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        # 只索引引擎源码树与入口脚本，跳过产物目录
        rel_dir = os.path.relpath(dirpath, ROOT).replace("\\", "/")
        if rel_dir.startswith(("marketing", "logs", "temp", "backup", "test_files", "deliverables")):
            dirnames[:] = []
            continue
        for fn in filenames:
            if not fn.endswith(".py"):
                continue
            path = os.path.join(dirpath, fn)
            rel = os.path.relpath(path, ROOT).replace("\\", "/")
            info = parse(path)
            if info:
                info["path"] = rel
                records[rel] = info

    os.makedirs(OUT_DIR, exist_ok=True)
    with open(os.path.join(OUT_DIR, "file_index.json"), "w", encoding="utf-8") as fh:
        json.dump(records, fh, ensure_ascii=False, indent=1)

    # 按二级目录聚合概览
    buckets: dict[str, list[str]] = defaultdict(list)
    for rel in records:
        parts = rel.split("/")
        key = "/".join(parts[:2]) if len(parts) > 2 else "/".join(parts[:1])
        buckets[key].append(rel)

    tot_lines = sum(r.get("lines", 0) for r in records.values())
    errors = [k for k, v in records.items() if "parse_error" in v]
    print(f"indexed files = {len(records)}  lines = {tot_lines}  parse_error = {len(errors)}")
    for k in sorted(buckets):
        n = len(buckets[k])
        ln = sum(records[r].get("lines", 0) for r in buckets[k])
        print(f"  {k:38s} {n:4d} files {ln:8d} lines")
    if errors:
        print("PARSE ERRORS:", errors[:10])
    print("out:", os.path.join(OUT_DIR, "file_index.json"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
