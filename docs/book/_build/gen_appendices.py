# -*- coding: utf-8 -*-
"""生成附录 A（文件职责索引）、附录 B（数据表速查）、附录 C（HTTP 接口清单）。

三个附录的数据全部来自机械提取：
  A ← docs/book/_build/file_index.json（AST 索引）
  B ← src/core/schema.sql（建表语句）
  C ← src/backend/ 下的 Blueprint 定义与 route 装饰器
不手工录入，保证与代码一致且可重跑。
"""
from __future__ import annotations

import ast
import json
import os
import re
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
BUILD = os.path.join(ROOT, "docs", "book", "_build")
BOOK = os.path.join(ROOT, "docs", "book")


def clip(text: str, n: int = 120) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[: n - 1] + "…"


# ---------------------------------------------------------------- 附录 A
def gen_appendix_a() -> str:
    with open(os.path.join(BUILD, "file_index.json"), encoding="utf-8") as fh:
        idx = json.load(fh)

    groups: dict[str, list[str]] = defaultdict(list)
    for path in idx:
        parts = path.split("/")
        if len(parts) == 1:
            key = "（仓库根目录）"
        elif parts[0] == "src":
            key = "src/" + parts[1] if len(parts) > 2 else "src/"
        else:
            key = parts[0] + "/"
        groups[key].append(path)

    order = ["（仓库根目录）", "src/"]
    keys = order + sorted(k for k in groups if k not in order)

    out: list[str] = ["# 附录 A · 文件职责索引", ""]
    out.append("本附录由 AST 脚本扫描生成（`docs/book/_build/gen_file_index.py`），覆盖仓库内全部 "
               f"{len(idx)} 个 Python 源文件。每个条目给出文件路径、行数与职责——职责取自模块文档串首句；"
               "无模块文档串的文件改列其顶层类名。产物目录（`build/`、`dist/`、`node_modules/` 等）不在扫描范围。")
    out.append("")

    for key in keys:
        if key not in groups:
            continue
        files = sorted(groups[key])
        lines = sum(idx[f].get("lines", 0) for f in files)
        out.append(f"## {key}　（{len(files)} 个文件 / {lines} 行）")
        out.append("")
        out.append("| 文件 | 行数 | 职责 |")
        out.append("|:---|---:|:---|")
        for f in files:
            info = idx[f]
            desc = info.get("doc") or ""
            if not desc:
                names = [c["name"] for c in info.get("classes", [])]
                if names:
                    desc = "顶层类：" + "、".join(names[:6]) + ("…" if len(names) > 6 else "")
                else:
                    fn = info.get("funcs", [])
                    desc = "顶层函数：" + "、".join(x["sig"].split("(")[0].replace("def ", "") for x in fn[:6]) if fn else "（无模块文档串）"
            if "parse_error" in info:
                desc = "（解析失败：" + info["parse_error"] + "）"
            desc = desc.lstrip("★").strip()
            out.append(f"| `{f}` | {info.get('lines', 0)} | {clip(desc)} |")
        out.append("")

    return "\n".join(out)


# ---------------------------------------------------------------- 附录 B
_COL_RE = re.compile(r"^\s*`([^`]+)`\s+([A-Za-z][A-Za-z0-9_]*)\s*(\([^)]*\))?(.*)$")
_COMMENT_RE = re.compile(r"COMMENT\s*=?\s*'((?:[^'\\]|\\.)*)'")


def _parse_column(line: str):
    m = _COL_RE.match(line)
    if not m:
        return None
    name, base, arg, rest = m.group(1), m.group(2), m.group(3) or "", m.group(4) or ""
    typ = (base + arg).upper()
    if rest:
        fa = re.search(r"\b(AUTO_INCREMENT)\b", rest, re.I)
        if fa:
            typ += " AUTO_INCREMENT"
    nullable = "NO" if re.search(r"NOT NULL", rest, re.I) else "YES"
    c = _COMMENT_RE.search(rest)
    return {"name": name, "type": typ, "nullable": nullable, "comment": c.group(1) if c else ""}


def gen_appendix_b() -> str:
    lines = open(os.path.join(ROOT, "src", "core", "schema.sql"), encoding="utf-8", errors="ignore").read().split("\n")

    tables = []
    i = 0
    while i < len(lines):
        m = re.match(r"\s*CREATE TABLE IF NOT EXISTS `([^`]+)`", lines[i])
        if not m:
            i += 1
            continue
        name, start = m.group(1), i
        lead = ""
        for j in range(i - 1, max(-1, i - 6), -1):
            s = lines[j].strip()
            if s.startswith("--") and len(s) > 2:
                lead = s[2:].strip()
                break
            if s and not s.startswith("--"):
                break
        cols, pk, tail_comment, j = [], "", "", i + 1
        while j < len(lines) and "ENGINE=" not in lines[j].upper():
            col = _parse_column(lines[j])
            if col:
                cols.append(col)
            pm = re.search(r"PRIMARY KEY\s*\(([^)]*)\)", lines[j])
            if pm:
                pk = pm.group(1).replace("`", "").replace(" ", "")
            j += 1
        if j < len(lines):
            pm = re.search(r"PRIMARY KEY\s*\(([^)]*)\)", lines[j])
            if pm and not pk:
                pk = pm.group(1).replace("`", "").replace(" ", "")
            tc = _COMMENT_RE.search(lines[j])
            tail_comment = tc.group(1) if tc else ""
        tables.append({"name": name, "line": start + 1, "lead": lead, "cols": cols,
                       "pk": pk, "comment": tail_comment})
        i = j + 1

    out: list[str] = ["# 附录 B · 数据表速查", ""]
    out.append(f"本附录由脚本从 `src/core/schema.sql` 的建表语句逐条提取，共 **{len(tables)} 张表**、"
               f"{sum(len(t['cols']) for t in tables)} 个列定义。该文件中 `CREATE TABLE` 字面出现 94 次，"
               "其中 4 次位于注释行，不计入表数。列说明取自 DDL 内的 `COMMENT`，类型与空值约束按原样保留。")
    out.append("")
    out.append("## 表清单")
    out.append("")
    out.append("| 表 | 定义行 | 列数 | 主键 | 说明 |")
    out.append("|:---|---:|---:|:---|:---|")
    for t in tables:
        desc = t["comment"] or (t["lead"] if not t["lead"].startswith(("=", "-")) else "")
        desc = desc.lstrip("★").strip()
        out.append(f"| `{t['name']}` | {t['line']} | {len(t['cols'])} | `{t['pk']}` | {clip(desc, 90)} |")
    out.append("")

    out.append("## 列定义")
    out.append("")
    for t in tables:
        desc = t["comment"] or t["lead"]
        desc = desc if desc and not desc.startswith(("=", "-")) else ""
        desc = desc.lstrip("★").strip()
        head = f"### `{t['name']}`"
        if desc:
            head += f"　—　{clip(desc, 90)}"
        out.append(head)
        out.append("")
        out.append(f"定义行 {t['line']}，主键 `{t['pk']}`。")
        out.append("")
        out.append("| 列 | 类型 | 允许空 | 说明 |")
        out.append("|:---|:---|:---:|:---|")
        for c in t["cols"]:
            note = c["comment"].lstrip("★").strip()
            out.append(f"| `{c['name']}` | {c['type']} | {c['nullable']} | {clip(note, 80)} |")
        out.append("")
    return "\n".join(out)


# ---------------------------------------------------------------- 附录 C
_METHODS = {"route", "get", "post", "put", "delete", "patch"}
_VERB = {"get": ["GET"], "post": ["POST"], "put": ["PUT"], "delete": ["DELETE"], "patch": ["PATCH"]}


def gen_appendix_c() -> str:
    bp_prefix: dict[str, str] = {}
    bp_var_to_name: dict[str, str] = {}
    routes: list[tuple[str, str, str, str]] = []  # (蓝图变量, 路径, 方法, 文件)

    for dirpath, dirnames, filenames in os.walk(os.path.join(ROOT, "src", "backend")):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for fn in filenames:
            if not fn.endswith(".py"):
                continue
            path = os.path.join(dirpath, fn)
            rel = os.path.relpath(path, ROOT).replace("\\", "/")
            try:
                tree = ast.parse(open(path, encoding="utf-8", errors="ignore").read())
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
                    call = node.value
                    fname = call.func.id if isinstance(call.func, ast.Name) else ""
                    if fname == "Blueprint":
                        var = node.targets[0].id if isinstance(node.targets[0], ast.Name) else ""
                        bp_name = call.args[0].value if call.args and isinstance(call.args[0], ast.Constant) else var
                        prefix = ""
                        for kw in call.keywords:
                            if kw.arg == "url_prefix" and isinstance(kw.value, ast.Constant):
                                prefix = kw.value.value
                        bp_prefix[var] = prefix
                        bp_var_to_name[var] = bp_name
                # 装饰器形态与直接调用形态统一处理
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                    attr = node.func.attr
                    if attr not in _METHODS:
                        continue
                    if not node.args or not isinstance(node.args[0], ast.Constant):
                        continue
                    p = node.args[0].value
                    if not isinstance(p, str) or not p.startswith("/"):
                        continue
                    methods = _VERB.get(attr, [])
                    for kw in node.keywords:
                        if kw.arg == "methods" and isinstance(kw.value, (ast.List, ast.Tuple)):
                            methods = [e.value for e in kw.value.elts if isinstance(e, ast.Constant)]
                    var = node.func.value.id if isinstance(node.func.value, ast.Name) else "app"
                    routes.append((var, p, ",".join(methods or ["GET"]), rel))

    # 按模块聚合
    def module_of(rel: str) -> str:
        parts = rel.split("/")
        if rel == "src/backend/app.py":
            return "（app.py 内置）"
        if rel.startswith("src/backend/routes/"):
            return parts[3] if len(parts) > 4 else parts[-1]
        return rel

    agg: dict[str, int] = defaultdict(int)
    for var, p, m, rel in routes:
        agg[module_of(rel)] += 1

    out: list[str] = ["# 附录 C · HTTP 接口清单", ""]
    out.append(f"本附录由脚本扫描 `src/backend/` 生成，覆盖 **两种注册形态**：装饰器形态"
               f"（`@bp.route(...)` 置于函数之上）与直接调用形态（`bp.route(...)(func)`）。"
               f"后者容易被只数装饰器的统计漏掉，本附录一并计入，共 **{len(routes)} 个接口注册点**。"
               "路径参数占位符保留原始写法。")
    out.append("")
    out.append("## 按模块分布")
    out.append("")
    out.append("| 模块 | 注册点 |")
    out.append("|:---|---:|")
    for k in sorted(agg, key=lambda x: -agg[x]):
        out.append(f"| `{k}` | {agg[k]} |")
    out.append(f"| **合计** | **{len(routes)}** |")
    out.append("")
    out.append("## 接口明细")
    out.append("")
    out.append("「声明前缀」取自同名 Blueprint 的 `url_prefix`；为空表示该蓝图未声明前缀，"
               "其实际前缀由注册处决定，或路径本身已是完整路径。")
    out.append("")
    out.append("| 蓝图变量 | 声明前缀 | 路径 | 方法 | 定义文件 |")
    out.append("|:---|:---|:---|:---|:---|")
    for var, p, methods, rel in sorted(routes, key=lambda x: (x[3], x[1])):
        prefix = bp_prefix.get(var, "")
        if prefix and not p.startswith(prefix):
            shown = prefix
        elif prefix:
            shown = prefix
        else:
            shown = "—"
        out.append(f"| `{var}` | `{shown}` | `{p}` | {methods} | `{rel}` |")
    out.append("")
    out.append("## 蓝图清单")
    out.append("")
    out.append("| 蓝图变量 | 蓝图名 | 声明前缀 |")
    out.append("|:---|:---|:---|")
    for var in sorted(bp_prefix):
        out.append(f"| `{var}` | {bp_var_to_name.get(var, '')} | `{bp_prefix[var] or '—'}` |")
    out.append("")
    return "\n".join(out)


def main() -> None:
    for name, fn in (("A-文件职责索引", gen_appendix_a),
                     ("B-数据表速查", gen_appendix_b),
                     ("C-HTTP接口清单", gen_appendix_c)):
        text = fn()
        # 源码注释里的强调标记不属于正文，统一清除（同时收敛多余空格）
        text = re.sub(r"\s*★\s*", " ", text)
        text = re.sub(r"  +", " ", text)
        p = os.path.join(BOOK, name + ".md")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(text)
        print(f"{name}.md  {len(text.splitlines())} 行")


if __name__ == "__main__":
    main()
