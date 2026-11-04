# -*- coding: utf-8 -*-
"""书稿质量审计：引用可解析（防编造）/ 段落重复 / 数字口径矛盾。

用法（仓库根目录）：
    python docs/book/_build/audit_quality.py

三面检查：
  A. 引用校验 —— 抽取正文所有 `path:line` 引用，对照 file_index.json 的真实
     文件与行数，报「文件不存在 / 行号溢出 / 短名歧义」。
  B. 段落重复 —— 跨章逐字重复的段落（≥60 字符）清单。
  C. 数字口径 —— 同一量词（张表/个模板/章…）出现多个不同数字的清单。
"""
from __future__ import annotations

import ast
import os
import re
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]  # 仓库根
BOOK = ROOT / "docs" / "book"

CODE_EXT = (
    "py|sql|json|ya?ml|js|jsx|ts|tsx|html|htm|toml|bat|ps1|sh|css|txt|csv|spec"
    "|ini|cfg|conf|env|md"
)
# 引用一律写成行内代码（反引号）——只在反引号片段内判定，避免 Node.js / hashlib.sha256 这类误报
INLINE_RE = re.compile(r"`([^`\n]+)`")
CODE_SPAN_RE = re.compile(
    r"^([A-Za-z0-9_.][A-Za-z0-9_./\-]*\.(?:" + CODE_EXT + r"))"
    r"(?::(\d+)(?:[-–~](\d+))?)?$"
)
BARE_LINE_RE = re.compile(r"`:(\d+)`")
# `file.py:123`（`symbol`） —— 校验符号是否真的属于被引文件（抓"行号合法但文件串了"）
SYM_RE = re.compile(
    r"`([A-Za-z0-9_./\-]+\.(?:" + CODE_EXT + r")):(\d+)(?:[-–~]\d+)?`"
    r"\s*[（(]\s*[`\"]?([A-Za-z_][A-Za-z0-9_]{2,})"
)

# 排除：缓存、依赖、打包副本、生成产物、参考资料
SKIP_DIRS = {
    "__pycache__", "node_modules", ".git", ".venv", "venv",
    "dist", "build", "logs", "marketing", "temp", "_internal",
    "_refs", ".workbuddy", "e2e-test-report-2026-08-10",
}


def build_fs() -> tuple[set[str], dict[str, list[str]], dict[str, int]]:
    """扫描文件系统：返回 (全部相对路径集合, basename→路径列表, 路径→真实行数)。"""
    all_paths: set[str] = set()
    by_name: dict[str, list[str]] = defaultdict(list)
    for dp, dns, fns in os.walk(ROOT):
        dns[:] = [d for d in dns if d not in SKIP_DIRS]
        for fn in fns:
            if not re.search(r"\.(?:" + CODE_EXT + r")$", fn):
                continue
            rel = os.path.relpath(os.path.join(dp, fn), ROOT).replace("\\", "/")
            all_paths.add(rel)
            by_name[fn].append(rel)
    return all_paths, by_name, {}


def line_count(rel: str, cache: dict[str, int]) -> int:
    if rel not in cache:
        try:
            with open(ROOT / rel, "rb") as fh:
                cache[rel] = fh.read().count(b"\n") + 1
        except OSError:
            cache[rel] = 0
    return cache[rel]


def chapter_files() -> list[Path]:
    return sorted(p for p in BOOK.glob("*.md") if not p.name.startswith("_"))


def strip_fences(text: str) -> str:
    return re.sub(r"```.*?```", "", text, flags=re.S)


def resolve(cite: str, all_paths: set[str], by_name: dict[str, list[str]]):
    """返回 (状态, 候选路径列表)。

    带 `/` 的引用按路径严格解析（exact → suffix）；裸文件名按同名全集解析
    —— 裸名天生歧义（如 `package.json` 有根目录与 log-panel-ui 两份），不能用某一个断言。
    """
    cite = re.sub(r"^\./", "", cite)
    if "/" in cite:
        if cite in all_paths:
            return "exact", [cite]
        suffix = [p for p in all_paths if p.endswith("/" + cite)]
        if suffix:
            return "suffix", suffix
        return "missing", []
    cands = by_name.get(cite, [])
    return ("name", cands) if cands else ("missing", [])


def audit_citations(all_paths, by_name, cache):
    ok = 0
    file_missing: list[tuple[str, str]] = []
    line_overflow: list[tuple[str, str, int, int]] = []
    ambiguous_fail: list[tuple[str, str, int]] = []
    bare = 0
    total = 0

    for f in chapter_files():
        raw = f.read_text(encoding="utf-8")
        bare += len(BARE_LINE_RE.findall(raw))
        for span in INLINE_RE.findall(strip_fences(raw)):
            m = CODE_SPAN_RE.match(span.strip())
            if not m:
                continue
            cite, l1, l2 = m.group(1), m.group(2), m.group(3)
            total += 1
            status, cands = resolve(cite, all_paths, by_name)
            if status == "missing":
                file_missing.append((f.name, cite))
                continue
            end = (int(l2) if l2 else int(l1)) if l1 else None
            if end is None:
                ok += 1
                continue
            if any(end <= line_count(c, cache) for c in cands):
                ok += 1
            elif len(cands) == 1:
                line_overflow.append((f.name, cite, end, line_count(cands[0], cache)))
            else:
                ambiguous_fail.append((f.name, cite, len(cands)))
    return {
        "total": total,
        "ok": ok,
        "file_missing": file_missing,
        "line_overflow": line_overflow,
        "ambiguous_fail": ambiguous_fail,
        "bare_line_refs": bare,
    }


def audit_symbols(all_paths, by_name, cache):
    """检查 `file:line`（`symbol`） 里的 symbol 是否真的出现在被引文件中。"""
    bad: list[tuple[str, str, str]] = []
    checked = 0
    for f in chapter_files():
        text = strip_fences(f.read_text(encoding="utf-8"))
        for m in SYM_RE.finditer(text):
            cite, _line, sym = m.group(1), m.group(2), m.group(3)
            status, cands = resolve(cite, all_paths, by_name)
            if status == "missing":
                continue
            checked += 1
            if not any(_file_contains(c, sym) for c in cands):
                bad.append((f.name, cite, sym))
    return checked, bad


_TEXT_CACHE: dict[str, str] = {}


def _file_contains(rel: str, sym: str) -> bool:
    if rel not in _TEXT_CACHE:
        try:
            _TEXT_CACHE[rel] = (ROOT / rel).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            _TEXT_CACHE[rel] = ""
    return sym in _TEXT_CACHE[rel]


ID_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{1,}")


def collect_symbols() -> set[str]:
    """扫描全仓 .py，收集**所有标识符**（函数/类/常量/参数/局部变量皆算）。

    这是一个"弱但可靠"的存在性底座：只要书里点到的名字在全仓任何 .py 出现过
    就不算编造。抓的是真正凭空出现的名字。
    """
    names: set[str] = set()
    for dp, dns, fns in os.walk(ROOT):
        dns[:] = [d for d in dns if d not in SKIP_DIRS]
        for fn in fns:
            if not fn.endswith(".py"):
                continue
            try:
                text = (Path(dp) / fn).read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            names.update(ID_TOKEN_RE.findall(text))
    return names


SYMNAME_RE = re.compile(
    r"^([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?):\d+(?:[-–~]\d+)?$"
)


def audit_symbol_names(all_symbols: set[str]):
    """校验 `函数名/类名:行号` 型引用里的名字是否真在代码中定义。

    排除 `foo.py:12` 这类文件引用（含已知扩展名的一律不算符号引用）。
    """
    import warnings

    bad: list[tuple[str, str]] = []
    checked = 0
    ext_tail = re.compile(r"\.(?:" + CODE_EXT + r"):\d")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        for f in chapter_files():
            for span in INLINE_RE.findall(strip_fences(f.read_text(encoding="utf-8"))):
                span = span.strip()
                if ext_tail.search(span):
                    continue
                m = SYMNAME_RE.match(span)
                if not m:
                    continue
                # 无扩展名的真实文件（Dockerfile / Makefile …）不算符号引用
                if (ROOT / m.group(1)).exists():
                    continue
                checked += 1
                name = m.group(1).rsplit(".", 1)[-1]
                if name not in all_symbols:
                    bad.append((f.name, m.group(1)))
    return checked, bad


def ref_anchor(span: str, all_paths, by_name):
    """把行内片段判为「文件引用」：含已知扩展名的 `path[:line]`，或无扩展名的
    真实文件（`Dockerfile`、`Makefile`）+ `:line`。返回候选列表或 None。"""
    fm = CODE_SPAN_RE.match(span)
    if fm and not span.startswith(":"):
        st, cands = resolve(fm.group(1), all_paths, by_name)
        if st != "missing" and cands:
            return cands
        return None
    nm = re.match(r"^([A-Za-z0-9_.][A-Za-z0-9_./\-]*):\d+$", span)
    if nm and (ROOT / nm.group(1)).is_file():
        return [nm.group(1).replace("\\", "/")]
    return None


def audit_bare_refs(all_paths, by_name, cache):
    """校验裸行号引用 `:N` —— 归属到"此前最近、且能容纳该行号"的文件引用后再校验。

    归因池 = 本行内该裸行号**之前**出现的文件引用 ＋ 本章此前各行出现过的文件
    引用，由近及远取第一个容得下该行号的引用。理由：作者常在本章/本节开头点明
    主体文件，其后行内顺带提及的旁支文件（`config.yaml`、`golden/*.json`、
    `entry_main.py`…）不该劫持后续裸行号；章节标题不切断该上下文（同一文件常
    跨多个 `##` 小标题被引用）。

    仅当池中**所有**引用都容不下该行号时才判为溢出（真错）；池为空者不计。
    命中引用不在本行者记「跨行命中」；最近引用容不下、更早引用接住者单独列出
    供人工复核（这类是启发式推断，证据弱于行内命中）。
    """
    scope_cnt = {"line": 0, "context": 0, "unattr": 0}
    hard: list[tuple[str, str, int, int, str]] = []      # 锚定引用容不下 → 真错
    review: list[tuple[str, str, str, str]] = []          # 最近锚定引用容不下、由更早引用接住
    in_fence = False
    for f in chapter_files():
        prior_refs: list[tuple[str, list[str]]] = []
        for raw_line in f.read_text(encoding="utf-8").splitlines():
            if raw_line.lstrip().startswith("```"):
                in_fence = not in_fence
                continue
            if in_fence:
                continue
            if not raw_line.strip():
                continue
            line_refs: list[tuple[str, list[str]]] = []
            for span in (s.strip() for s in INLINE_RE.findall(raw_line)):
                cands = ref_anchor(span, all_paths, by_name)
                if cands is not None:
                    line_refs.append((span.split(":")[0], cands))
                    continue
                bm = re.match(r"^:(\d+)(?:[-–~](\d+))?$", span)
                if not bm:
                    continue
                end = int(bm.group(2)) if bm.group(2) else int(bm.group(1))
                if line_refs:
                    # 行内锚定（强证据）：本行此前最近的引用必须容下该行号
                    name, cands = line_refs[-1]
                    scope_cnt["line"] += 1
                    if any(end <= line_count(c, cache) for c in cands):
                        continue
                    earlier = any(
                        end <= line_count(c, cache)
                        for _n, cs in line_refs[:-1] for c in cs
                    )
                    if earlier:
                        review.append((f.name, span, "(同行更早引用)", name))
                    else:
                        hard.append(
                            (f.name, span, end,
                             max(line_count(c, cache) for c in cands), name)
                        )
                    continue
                pool = prior_refs
                if not pool:
                    scope_cnt["unattr"] += 1
                    continue
                hit = None
                for name, cands in reversed(pool):
                    if any(end <= line_count(c, cache) for c in cands):
                        hit = (name, cands)
                        break
                scope_cnt["context"] += 1
                if hit is None:
                    name, cands = pool[-1]
                    hard.append(
                        (f.name, span, end, max(line_count(c, cache) for c in cands), name)
                    )
                elif hit[0] != pool[-1][0]:
                    review.append((f.name, span, hit[0], pool[-1][0]))
            prior_refs.extend(line_refs)
    return scope_cnt, hard, review


def audit_symbol_lines(all_paths, by_name, cache, all_symbols: set[str]):
    """校验 `name:line` 型引用 —— 用 AST 定位符号定义文件，再校验行号落点。

    书稿约定：`name:line` 指「该符号在其**定义文件**中的位置」。定义文件容不下该
    行号即为真错（多半是把"调用点行号"错写成符号定义）。AST 里没有 `def`、但全仓
    确有该标识符的（模块级常量、形参等）不算符号引用，跳过校验。
    """
    import warnings

    sym_files: dict[str, set[str]] = defaultdict(set)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        for dp, dns, fns in os.walk(ROOT):
            dns[:] = [d for d in dns if d not in SKIP_DIRS]
            for fn in fns:
                if not fn.endswith(".py"):
                    continue
                rel = os.path.relpath(os.path.join(dp, fn), ROOT).replace("\\", "/")
                try:
                    tree = ast.parse((ROOT / rel).read_text(encoding="utf-8", errors="ignore"))
                except (SyntaxError, OSError):
                    continue
                for node in ast.walk(tree):
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                        sym_files[node.name].add(rel)

    ext_tail = re.compile(r"\.(?:" + CODE_EXT + r"):\d")
    checked = 0
    overflow: list[tuple[str, str, int, int]] = []
    unknown: list[tuple[str, str]] = []
    for f in chapter_files():
        for span in INLINE_RE.findall(strip_fences(f.read_text(encoding="utf-8"))):
            span = span.strip()
            if ext_tail.search(span) or (ROOT / span.split(":")[0]).exists():
                continue
            m = SYMNAME_RE.match(span)
            if not m:
                continue
            name = m.group(1).rsplit(".", 1)[-1]
            line = int(re.search(r":(\d+)", span).group(1))
            files = sym_files.get(name, set())
            if not files:
                if name not in all_symbols:
                    unknown.append((f.name, m.group(1)))
                continue
            checked += 1
            if not any(line <= line_count(c, cache) for c in files):
                overflow.append((f.name, span, line, max(line_count(c, cache) for c in files)))
    return checked, overflow, unknown


MULTI_RE = re.compile(r"^([A-Za-z0-9_./\-]+\.(?:" + CODE_EXT + r"))(:\d+(?:/\d+)+)$")


def audit_multi_refs(all_paths, by_name, cache):
    """校验 `file.ext:a/b/c`（同一文件多个行号）型引用 —— A1/A4 的语法盲区。

    A1 只认「单文件单行号」，A4 只认纯 `:N`；这种把一行号串写成 `:21/30/36/42`
    的形态两者都不解析，是引用校验的真空白。此处逐分量校验行号落点。
    """
    checked = 0
    bad: list[tuple[str, str, str, int]] = []
    missing: list[tuple[str, str]] = []
    for f in chapter_files():
        for span in INLINE_RE.findall(strip_fences(f.read_text(encoding="utf-8"))):
            m = MULTI_RE.match(span.strip())
            if not m:
                continue
            cite = m.group(1)
            nums = [int(x) for x in re.findall(r":(\d+)", m.group(2))]
            st, cands = resolve(cite, all_paths, by_name)
            if st == "missing":
                missing.append((f.name, cite))
                continue
            checked += 1
            top = max(nums)
            if not any(top <= line_count(c, cache) for c in cands):
                bad.append((f.name, span.strip(), cite, max(line_count(c, cache) for c in cands)))
    return checked, bad, missing


def audit_duplication():
    """跨章逐字重复的段落（规范化后 ≥60 字符）。"""
    seen: dict[str, list[str]] = defaultdict(list)
    for f in chapter_files():
        text = strip_fences(f.read_text(encoding="utf-8"))
        for para in re.split(r"\n\s*\n", text):
            # 去 markdown 记号与空白，便于比较
            norm = re.sub(r"[`*_#>\-\|\s]", "", para)
            if len(norm) < 60:
                continue
            seen[norm].append(f.name)
    dups = []
    for norm, files in seen.items():
        uniq = sorted(set(files))
        if len(uniq) > 1:
            dups.append((len(norm), uniq, norm[:70]))
    dups.sort(reverse=True)
    return dups


QUANT_RE = re.compile(r"(\d[\d,]*)\s*(张表|个模板|个注册点|个端点|个路由|个域|个章节|条规则|种数字)")
# 只对「全书级」量词做矛盾检查；逐包计数（个文件/个模块）天然多值，不在此列
GLOBAL_UNITS = {"张表", "个模板", "个注册点", "个端点", "个路由", "个域", "个章节", "条规则"}


def audit_numbers():
    buckets: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    for f in chapter_files():
        if f.name[0] in "ABC":  # 跳过机械附录（逐条目计数必然多值）
            continue
        text = f.read_text(encoding="utf-8")
        for m in QUANT_RE.finditer(text):
            num, unit = m.group(1), m.group(2)
            if unit not in GLOBAL_UNITS:
                continue
            buckets[unit][num].add(f.name)
    conflicts = {}
    for unit, nums in buckets.items():
        if len(nums) > 1:
            conflicts[unit] = {n: sorted(s) for n, s in sorted(nums.items())}
    return conflicts, buckets


CONST_FWD_RE = re.compile(
    r"`?(_?[A-Z][A-Z0-9_]{2,})`?[`（(\s]{0,4}"
    r"`?([A-Za-z0-9_./\-]+\.(?:" + CODE_EXT + r")):(\d+)(?:[-–~](\d+))?"
)
CONST_REV_RE = re.compile(
    r"`?([A-Za-z0-9_./\-]+\.(?:" + CODE_EXT + r")):(\d+)(?:[-–~](\d+))?`?"
    r"[`）)\s]{0,4}`?(_?[A-Z][A-Z0-9_]{2,})"
)


def _module_const_spans() -> dict[str, dict[str, list[tuple[int, int]]]]:
    """{常量名: {相对路径: [(起行, 止行)]}} —— 仅模块顶层 Assign / AnnAssign。"""
    out: dict[str, dict[str, list[tuple[int, int]]]] = defaultdict(lambda: defaultdict(list))
    for dp, dns, fns in os.walk(ROOT):
        dns[:] = [d for d in dns if d not in SKIP_DIRS]
        for fn in fns:
            if not fn.endswith(".py"):
                continue
            rel = os.path.relpath(os.path.join(dp, fn), ROOT).replace("\\", "/")
            try:
                tree = ast.parse((ROOT / rel).read_text(encoding="utf-8", errors="ignore"))
            except (SyntaxError, OSError):
                continue
            for node in tree.body:
                if isinstance(node, ast.Assign):
                    tg = node.targets
                elif isinstance(node, ast.AnnAssign):
                    tg = [node.target]
                else:
                    continue
                for t in tg:
                    if isinstance(t, ast.Name):
                        out[t.id][rel].append((node.lineno, node.end_lineno or node.lineno))
    return out


def audit_const_lines(all_paths, by_name, cache):
    """A6：`常量名`（`file:line`）—— 校验行号是否落在该常量的模块级赋值块内。

    A5 只覆盖带 `def` 的符号，**模块级常量是它的盲区**（曾被漏检：`ALL_TOOLS`
    引自 `_make_schema` 函数内部、`UNDECIDED_METHOD_MARKERS` 引自上方的注释行）。
    判定口径：名字须是**被引文件的模块级常量**才算常量引用（据此过滤正文里的
    SQL/PASS 等普通词）；名字是模块级常量但被引文件不含它 → 报「文件不符」。
    正反两种书写（`` `NAME`（`file:line`） `` 与 `file:line`（`` `NAME` ``））都扫。
    """
    consts = _module_const_spans()

    def pairs(f):
        text = strip_fences(f.read_text(encoding="utf-8"))
        for m in CONST_FWD_RE.finditer(text):
            yield m.group(1), m.group(2), int(m.group(3)), int(m.group(4) or m.group(3))
        for m in CONST_REV_RE.finditer(text):
            yield m.group(4), m.group(1), int(m.group(2)), int(m.group(3) or m.group(2))

    checked = 0
    skipped = 0
    overflow: list[tuple[str, str, int, tuple[int, int]]] = []
    for f in chapter_files():
        for name, fpath, s, end in pairs(f):
            files = consts.get(name)
            if not files:
                continue  # 不是模块级常量 → 非本项范围（正文词 / 局部名 / 导入名）
            _st, cands = resolve(fpath, all_paths, by_name)
            # 只在「被引文件本身即该常量的定义文件」时判定行号；跨文件引用常量多为
            # 「使用点」引用（如"登录失败归 ENVIRONMENT_ERROR"），语义非定义位置，不纳入。
            same = [c for c in cands if c in files]
            if not same:
                skipped += 1
                continue
            checked += 1
            spans = [sp for c in same for sp in files[c]]
            if not any(a <= end and s <= b for a, b in spans):
                lo = min(a for a, _ in spans)
                hi = max(b for _, b in spans)
                overflow.append((f.name, f"{name}@{fpath}:{s}", s, (lo, hi)))
    return checked, overflow, skipped


def main() -> None:
    all_paths, by_name, cache = build_fs()
    print(f"文件系统实体文件：{len(all_paths)}；章节数：{len(chapter_files())}\n")

    print("═══ A. 引用校验（对照真实文件系统）═══")
    r = audit_citations(all_paths, by_name, cache)
    print(f"共抽取 filename(±line) 引用：{r['total']}；可解析：{r['ok']}"
          f"；另有裸行号引用（`:N`，单独由 A4 归因校验）：{r['bare_line_refs']}")
    print(f"文件不存在：{len(r['file_missing'])}；行号溢出：{len(r['line_overflow'])}"
          f"；同名多文件且全部不满足：{len(r['ambiguous_fail'])}")
    for fn, c in r["file_missing"][:40]:
        print(f"  [缺文件] {fn}: {c}")
    for fn, c, end, real in r["line_overflow"][:40]:
        print(f"  [行号溢出] {fn}: {c} → 引用 {end}，实际 {real} 行")
    for fn, c, n in r["ambiguous_fail"][:40]:
        print(f"  [同名全不满足] {fn}: {c} → {n} 个同名文件均不足")
    print()

    print("═══ A2. 符号归属校验（`file:line`（`symbol`））═══")
    checked, bad = audit_symbols(all_paths, by_name, cache)
    print(f"可校验（带符号名）的引用：{checked}；符号不在被引文件内：{len(bad)}")
    for fn, c, sym in bad[:40]:
        print(f"  [符号错位] {fn}: {c} → 该文件不含 `{sym}`")
    print()

    print("═══ A3. 函数名/类名型引用（`name:line`）存在性 ═══")
    all_symbols = collect_symbols()
    checked3, bad3 = audit_symbol_names(all_symbols)
    print(f"全仓定义符号数：{len(all_symbols)}；可校验引用：{checked3}；"
          f"名字在代码中不存在：{len(bad3)}")
    for fn, nm in bad3[:40]:
        print(f"  [符号不存在] {fn}: `{nm}:行号`")
    print()

    print("═══ A4. 裸行号引用（`:N`）归因后校验 ═══")
    scope_cnt, hard4, review4 = audit_bare_refs(all_paths, by_name, cache)
    print(f"行内锚定（强证据）：{scope_cnt['line']}；章内上下文锚定：{scope_cnt['context']}；"
          f"无引用可依（不校验）：{scope_cnt['unattr']}")
    print(f"硬错（锚定引用容不下该行号）：{len(hard4)}；"
          f"需复核（最近锚定引用容不下、由更早引用接住）：{len(review4)}")
    for fn, sp, end, real, owner in hard4[:40]:
        print(f"  [硬错] {fn}: {sp} → 锚定 {owner} 最 {real} 行，引用 {end}")
    for fn, sp, hit, near in review4[:30]:
        print(f"  [复核] {fn}: {sp} → 最近 {near} 容不下，改归 {hit}")
    print()

    print("═══ A4b. 多行号串引用（`file.ext:a/b/c`，A1/A4 语法盲区）═══")
    ck4b, bad4b, miss4b = audit_multi_refs(all_paths, by_name, cache)
    print(f"可校验：{ck4b}；行号溢出：{len(bad4b)}；文件不存在：{len(miss4b)}")
    for fn, sp, cite, real in bad4b[:40]:
        print(f"  [溢出] {fn}: `{sp}` → {cite} 最 {real} 行")
    for fn, cite in miss4b[:40]:
        print(f"  [缺文件] {fn}: {cite}")
    print()

    print("═══ A5. 符号:行号 引用（`name:line`）落点校验 ═══")
    ck5, ov5, unk5 = audit_symbol_lines(all_paths, by_name, cache, all_symbols)
    print(f"符号可 AST 定位：{ck5}；行号溢出：{len(ov5)}；全仓无此标识符：{len(unk5)}")
    for fn, sp, line, real in ov5[:40]:
        print(f"  [溢出] {fn}: `{sp}` → 定义文件最长 {real} 行")
    for fn, nm in unk5[:40]:
        print(f"  [全仓无此名] {fn}: `{nm}`")
    print()

    print("═══ A6. 常量:行号 引用（`NAME`（`file:line`））落点校验 ═══")
    ck6, ov6, sk6 = audit_const_lines(all_paths, by_name, cache)
    print(f"可校验（被引文件即该模块级常量的定义文件）：{ck6}；行号不在赋值块：{len(ov6)}"
          f"；跨文件常量引用（使用点，不判定）：{sk6}")
    for fn, sp, line, span in ov6[:40]:
        print(f"  [错位] {fn}: `{sp}` → 该常量赋值块 {span[0]}-{span[1]}")
    print()

    print("═══ B. 跨章重复段落（≥60 字符，规范化后逐字相同）═══")
    dups = audit_duplication()
    print(f"重复段落组：{len(dups)}")
    for ln, files, head in dups[:25]:
        print(f"  [{len(files)}章/{ln}字] {head}… ← {', '.join(files)}")
    print()

    print("═══ C. 数字口径矛盾（同一量词出现多个数字）═══")
    conflicts, buckets = audit_numbers()
    for unit, nums in conflicts.items():
        print(f"  「{unit}」出现 {len(nums)} 种数字：")
        for n, files in nums.items():
            print(f"      {n} {unit} ← {', '.join(files)}")
    if not conflicts:
        print("  无。")
    print(f"\n  仅一种数字的量词（一致）："
          f"{', '.join(f'{u}={list(v)[0]}' for u, v in buckets.items() if len(v) == 1)}")


if __name__ == "__main__":
    main()
