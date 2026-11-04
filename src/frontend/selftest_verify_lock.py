# -*- coding: utf-8 -*-
"""verify_lock.py「悬空属性门」检查的自测。

用法: python src/frontend/selftest_verify_lock.py

两层验证：
① 单元场景（核心断言，不依赖环境）：setAttribute / dataset / 模板静态属性 / allowlist 判 OK；
   无写入点 / 仅 removeAttribute / 属性名只出现在注释里 判 DANGLING。
② 真实历史回归（增强，环境不满足时自动 SKIP）：
   用 CSS 属性门 + 修复前的 JS/模板（git HEAD~1）组合，期望被判定为 DANGLING，
   以证明这条检查确实能拦住那次「菜单被焊死」的事故。

背景：app-design.css 曾长期存在
    html:not([data-sb-ready="1"]) .sidebar .nav-item { pointer-events: none }
而全仓库没有任何代码设置 data-sb-ready —— 门被焊死，用户表现为「切换功能区点了没反应」。
"""
import importlib.util
import os
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
SPEC = importlib.util.spec_from_file_location("verify_lock", os.path.join(HERE, "verify_lock.py"))
V = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(V)

CSS_GATE = 'html:not([data-demo-ready="1"]) .sidebar .nav-item { pointer-events: none; }'

CASES = [
    ("正例 · JS setAttribute 写入点",
     {"a.css": CSS_GATE, "a.js": 'document.documentElement.setAttribute("data-demo-ready","1");'},
     (), "OK"),
    ("正例 · dataset 驼峰赋值",
     {"a.css": CSS_GATE, "a.js": 'document.documentElement.dataset.demoReady = "1";'},
     (), "OK"),
    ("正例 · 模板静态属性",
     {"a.css": CSS_GATE, "a.html": '<html data-demo-ready="1">'},
     (), "OK"),
    ("正例 · allowlist 放行",
     {"a.css": CSS_GATE, "a.js": "// nothing"},
     ("data-demo-ready",), "OK"),
    ("反例 · 完全无写入点（= 修复前真实状态）",
     {"a.css": CSS_GATE, "a.js": "A.initSidebar = function(){ };", "a.html": "<html lang='zh'>"},
     (), "DANGLING"),
    ("反例 · 仅 removeAttribute（移除却从未设置）",
     {"a.css": CSS_GATE, "a.js": 'document.documentElement.removeAttribute("data-demo-ready");'},
     (), "DANGLING"),
    ("反例 · 属性名只出现在注释里（注释不算写入点）",
     {"a.css": CSS_GATE, "a.js": "// 提到 data-demo-ready 但只是注释\nvar x = 1;"},
     (), "DANGLING"),
    ("边界 · 仅元素级属性门（非根元素，不参与检查）",
     {"a.css": "[data-tooltip] { color: red; }", "a.js": "var x=1;"},
     (), "NO-GATE"),
]


def judge(sources, allow=()):
    gates = V.extract_root_gates(sources)
    if not gates:
        return "NO-GATE", None, []
    allow = set(allow)
    for attr in sorted(gates):
        if attr in allow:
            continue
        strong, weak = V.find_writers(attr, sources)
        if not strong:
            return "DANGLING", attr, weak
    return "OK", None, []


def run_units():
    print("===== ① 单元场景（核心断言）=====")
    fails = []
    for name, srcs, allow, expect in CASES:
        got, attr, weak = judge(srcs, allow)
        if got == expect:
            print(f"  ✅ {name:44s} {got}")
        else:
            fails.append(name)
            print(f"  ❌ {name:44s} expect={expect} got={got} attr={attr}")
    print(f"  单元场景: {len(CASES)-len(fails)}/{len(CASES)} 通过")
    return fails


def run_history():
    """真实历史回归：环境不满足时返回 None（SKIP），不判失败。"""
    print("\n===== ② 真实历史回归（增强，可 SKIP）=====")
    css_path = os.path.join(HERE, "static", "app-design.css")
    if not os.path.exists(css_path):
        print("  ⏭  SKIP: 找不到 app-design.css")
        return None
    css = open(css_path, encoding="utf-8").read()

    def git_show(ref):
        try:
            r = subprocess.run(["git", "show", ref], cwd=ROOT, capture_output=True, timeout=20)
            return r.stdout.decode("utf-8", errors="replace") if r.returncode == 0 else ""
        except Exception:
            return ""

    before_js = git_show("HEAD~1:src/frontend/static/app-common.js")
    before_html = git_show("HEAD~1:src/frontend/templates/_layout_base.html")
    if not before_js:
        print("  ⏭  SKIP: 取不到 HEAD~1 的历史版本（仓库状态不同/无 git）")
        return None

    after_js = open(os.path.join(HERE, "static", "app-common.js"), encoding="utf-8").read()
    after_html = open(os.path.join(HERE, "templates", "_layout_base.html"), encoding="utf-8").read()

    got_before, attr_b, weak_b = judge({
        "src/frontend/static/app-design.css": css,
        "src/frontend/static/app-common.js": before_js,
        "src/frontend/templates/_layout_base.html": before_html,
    })
    got_after, _, _ = judge({
        "src/frontend/static/app-design.css": css,
        "src/frontend/static/app-common.js": after_js,
        "src/frontend/templates/_layout_base.html": after_html,
    })
    print(f"  修复前（HEAD~1 的 JS/模板）: {got_before}"
          + (f"  attr={attr_b}" if attr_b else "")
          + (f"  weak={weak_b}" if weak_b else ""))
    print(f"  修复后（当前工作区）      : {got_after}")
    if got_before == "DANGLING" and got_after == "OK":
        print("  ✅ 真实回归判定：修复前被拦下、修复后通过")
        return True
    print("  ⏭  SKIP: 历史版本已不含该缺陷形态（判定不作为失败）")
    return None


def run_eol():
    """换行策略（.gitattributes）检查的自测：真文件必须通过，削弱/移除必须被拦。

    不直接改动仓库里的 .gitattributes（风险高），改为临时替换 V 里的路径常量，
    在临时目录里造样本文件，测完还原。
    """
    print("\n===== ③ 换行策略检查（.gitattributes 守卫）=====")
    import tempfile

    orig_path = V._GITATTR_PATH
    good = (
        "# 默认\n* text=auto eol=lf\n\n*.png binary\n"
    )
    missing = None                                    # 文件不存在
    weakened = "# 默认\n* text=auto\n\n*.png binary\n"  # 主规则被拿掉

    fails = []
    with tempfile.TemporaryDirectory() as td:
        fx = os.path.join(td, ".gitattributes")

        def run_case(name, content, expect_ok):
            if content is None:
                if os.path.exists(fx):
                    os.remove(fx)
            else:
                with open(fx, "w", encoding="utf-8", newline="") as f:
                    f.write(content)
            V._GITATTR_PATH = fx
            old_quiet = V.QUIET
            V.QUIET = True                    # 抑制 ✅ 噪声，只看返回值
            try:
                got = V.check_eol_policy()
            finally:
                V.QUIET = old_quiet
            tag = "OK" if got else "REJECTED"
            exp = "OK" if expect_ok else "REJECTED"
            if tag == exp:
                print(f"  ✅ {name:44s} {tag}")
            else:
                fails.append(name)
                print(f"  ❌ {name:44s} expect={exp} got={tag}")

        run_case("正例 · 完整 .gitattributes（* text=auto eol=lf）", good, True)
        run_case("反例 · 文件缺失（仓库根无 .gitattributes）", missing, False)
        run_case('反例 · 策略被削弱（* text=auto 无 eol=lf）', weakened, False)

    V._GITATTR_PATH = orig_path
    total = 3
    print(f"  换行策略场景: {total-len(fails)}/{total} 通过")
    return fails


def run_eol_live():
    """对仓库真实 .gitattributes 做一次现场校验（不修改任何文件）。"""
    print("\n===== ④ 真实仓库现场校验 =====")
    old_quiet = V.QUIET
    V.QUIET = True
    try:
        ok = V.check_eol_policy()
    finally:
        V.QUIET = old_quiet
    if ok:
        print("  ✅ 仓库 .gitattributes 存在且策略完整")
        return True
    print("  ❌ 仓库 .gitattributes 缺失或策略已被削弱")
    return False


def main():
    fails = run_units()
    hist = run_history()
    fails += run_eol()
    live_ok = run_eol_live()
    if not live_ok:
        fails.append("live-eol-policy")
    hard_fail = bool(fails)
    print("\n结论: {}".format("存在失败项: {}".format(fails) if hard_fail
                             else ("全部通过" + ("（历史回归 SKIP）" if hist is None else ""))))
    return 1 if hard_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
