# -*- coding: utf-8 -*-
"""
静态层核查 A：模板内 els 引用 vs els 注册 一致性 + $('id') 是否存在。

目标：抓出 `els.<key>.addEventListener` 引用了但 `els={...}` 未注册该 key 的
"sli有性 throw" 类 bug —— 这类 bug 无 console 错误却把该点之后的监听绑定全部断掉
（本次实测：intent-edit.html 漏注册 btnBack，导致返回/保存/刷新按钮同时失效）。
"""
from __future__ import annotations
import re, sys
from pathlib import Path

TPL = Path(r"d:\AI\AI_Test_Preject\src\frontend\templates")

# 收集模板内联 <script>（去掉 <script src>）
def inline_scripts(text: str) -> list[str]:
    blocks = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", text, re.S | re.I)
    return blocks

def find_els_map(js: str):
    """返回 (els变量名, keys集合) 或 None；支持 const els = { ... }; / var els = { ... };"""
    out = []
    # 匹配 `els = {` 到匹配的大括号
    for var in ("els", "ELS"):
        for m in re.finditer(r"\b%s\s*=\s*\{" % var, js):
            start = m.end() - 1  # 指向 {
            depth = 0
            i = start
            while i < len(js):
                if js[i] == "{":
                    depth += 1
                elif js[i] == "}":
                    depth -= 1
                    if depth == 0:
                        block = js[m.start():i + 1]
                        # 收集对象内所有顶层键（无论 RHS 用 $(id)/getElementById 等）
                        keys = re.findall(r"^\s*([A-Za-z_$][\w$]*)\s*:", block, re.M)
                        visible = list(dict.fromkeys(keys))
                        out.append({"var": var, "keys": visible, "ids": []})
                        break
                i += 1
    return out

def main():
    issues = []
    for html_path in sorted(TPL.glob("*.html")):
        text = html_path.read_text(encoding="utf-8", errors="replace")
        for si, js in enumerate(inline_scripts(text)):
            maps = find_els_map(js)
            if not maps:
                continue
            for mp in maps:
                keys = set(mp["keys"])
                # 所有 els.<key>.<method> 引用（含 return/分支里）
                refs = re.findall(r"%s\.([A-Za-z_$][\w$]*)\.(addEventListener|click|getAttribute|style|value|innerText|innerHTML|querySelector)" % mp["var"], js)
                ref_keys = set(r for r, _ in refs)
                missing = ref_keys - keys
                for mk in sorted(missing):
                    issues.append(
                        {"page": html_path.name, "var": mp["var"],
                         "key": mk, "type": "els-unregistered",
                         "detail": "被 addEventListener 等引用但 els 未注册"})
            # ── $('id') / getElementById('id') 引用的 id 是否在页面静态 HTML 存在 ──
            if si == 0:
                dom_ids = set(re.findall(r"id=\"([\w.:-]+)\"", text))
                ref_ids = set()
                for m in re.finditer(r"\$\s*\([\"']([\w.:-]+)[\"']\)", js):
                    ref_ids.add(m.group(1))
                for m in re.finditer(r"document\.getElementById\s*\([\"']([\w.:-]+)[\"']\)", js):
                    ref_ids.add(m.group(1))
                # 动态创建的 id（出现在 JS 字符串里的模板 id）不判 hard fail，仅提示
                for rid in sorted(ref_ids - dom_ids):
                    if rid.strip():
                        issues.append(
                            {"page": html_path.name, "var": "id",
                             "key": rid, "type": "id-not-in-static-html",
                             "detail": "$('...')/getElementById 引用的 id 未在静态 HTML 找到（可能动态创建，需确认）"})
    print(f"=== 静态 els 一致性检查：{len(issues)} 处问题（第2类为疑似动态，需人工确认）===")
    hard = [i for i in issues if i["type"] == "els-unregistered"]
    soft = [i for i in issues if i["type"] == "id-not-in-static-html"]
    for it in hard:
        print(f"  [HARD] {it['page']}: els.{it['key']} 未注册")
    for it in soft:
        print(f"  [soft] {it['page']}: id={it['key']} 未在静态HTML找到")
    print("done")
    return issues

if __name__ == "__main__":
    main()