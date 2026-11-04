"""前端布局锁校验脚本。

用法: python src/frontend/verify_lock.py
      python src/frontend/verify_lock.py --quiet   # 只输出结论与失败项

- 比对 FRONTEND_LOCK.json 里的 sha256 + section 名
- 悬空依赖检查：根元素(html / :root / body)上的 data-* 属性门必须有写入点
- 任何不匹配 → 输出差异 → exit code 1
- ratchet init 之后、任何 git 操作之后都可以跑这个
"""
import hashlib
import json
import os
import re
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
LOCK_PATH = os.path.join(ROOT, 'src', 'frontend', 'FRONTEND_LOCK.json')
CSS_DIR = os.path.join(ROOT, 'src', 'frontend', 'static')
TPL_DIR = os.path.join(ROOT, 'src', 'frontend', 'templates')

QUIET = '--quiet' in sys.argv

# ── 悬空依赖检查（2026-09-20 新增）────────────────────────────────────
# 教训：app-design.css 里长期存在
#     html:not([data-sb-ready="1"]) .sidebar .nav-item { pointer-events: none }
# 而全仓库没有任何代码去设置 data-sb-ready → 该规则恒成立 →
# 侧栏菜单项永久 pointer-events:none，表现为「切换功能区点了没反应」。
# 因为"门是焊死的"这件事在代码里没有任何显式痕迹，同一个问题被反复"修复"却始终复发。
# 故：根元素上的 data-* 属性门必须能查到写入点，否则本次校验直接失败。

_CSS_COMMENT = re.compile(r'/\*.*?\*/', re.DOTALL)
_HTML_COMMENT = re.compile(r'<!--.*?-->', re.DOTALL)
# 行注释：只允许行首出现空格/Tab（不能用 \s，否则会把换行一起吃掉 → 行号偏移）
_JS_LINE_COMMENT = re.compile(r'(?m)^[ \t]*//[^\n]*|(?<![:\w])//[^\n]*')
_ATTR_SEL = re.compile(r'\[\s*(data-[a-zA-Z0-9_-]+)\s*(?:[~^$*|]?=[^\]]*)?\]', re.DOTALL)
_ROOT_SEL = re.compile(r'(?:^|[\s,>+~(])(?:html|:root|body)(?=[\s,.:\[>+~]|$)')


def _strip_comments(text):
    """去掉注释，避免"注释里写了属性名"被误判为写入点。

    注意：多行注释必须保留其中的换行符，否则后续所有行号都会前移，
    导致报出的写入点位置指错行（曾出现 631 → 596 的偏移）。
    """
    def _keep_nl(m):
        return '\n' * m.group(0).count('\n')

    text = _HTML_COMMENT.sub(_keep_nl, text)
    text = _CSS_COMMENT.sub(_keep_nl, text)
    text = _JS_LINE_COMMENT.sub(' ', text)
    return text


def _read_sources():
    """返回 {相对路径: 去注释后的源码}（静态 JS/CSS + 模板）。"""
    out = {}
    for d in (CSS_DIR, TPL_DIR):
        if not os.path.isdir(d):
            continue
        for fn in sorted(os.listdir(d)):
            if fn.lower().endswith(('.js', '.html', '.htm', '.css')):
                p = os.path.join(d, fn)
                try:
                    with open(p, encoding='utf-8', errors='replace') as f:
                        out[os.path.relpath(p, ROOT)] = _strip_comments(f.read())
                except OSError:
                    pass
    return out


def extract_root_gates(sources):
    """从 CSS 里提取根元素级属性门。返回 {attr: {'selectors': [...], 'files': [...]}}。"""
    gates = {}
    for rel, text in sources.items():
        if not rel.lower().endswith('.css'):
            continue
        for m in re.finditer(r'([^{}]+)\{', text):
            sel = ' '.join(m.group(1).split())
            if not sel or sel.startswith('@') or not _ROOT_SEL.search(sel):
                continue
            for attr in _ATTR_SEL.findall(sel):
                g = gates.setdefault(attr, {'selectors': [], 'files': []})
                if sel not in g['selectors']:
                    g['selectors'].append(sel)
                if rel not in g['files']:
                    g['files'].append(rel)
    return gates


def _camel(attr):
    parts = attr.split('-')[1:]
    return parts[0] + ''.join(p.capitalize() for p in parts[1:]) if parts else attr


def find_writers(attr, sources):
    """反查属性写入点。返回 (strong, weak)：强写入点=设置该属性；弱=仅移除。"""
    strong, weak = [], []
    pats_strong = (
        r"""setAttribute\s*\(\s*['"]%s['"]""" % re.escape(attr),
        r"""toggleAttribute\s*\(\s*['"]%s['"]""" % re.escape(attr),
        r"""dataset\s*\.\s*%s\s*=""" % re.escape(_camel(attr)),
        r"""[\s'"`]%s\s*=\s*['"]""" % re.escape(attr),
    )
    pat_weak = r"""removeAttribute\s*\(\s*['"]%s['"]""" % re.escape(attr)
    for rel, text in sources.items():
        for pat in pats_strong:
            for m in re.finditer(pat, text):
                h = '%s:%d' % (rel.replace('\\', '/'), text.count('\n', 0, m.start()) + 1)
                if h not in strong:
                    strong.append(h)
        for m in re.finditer(pat_weak, text):
            h = '%s:%d' % (rel.replace('\\', '/'), text.count('\n', 0, m.start()) + 1)
            if h not in weak:
                weak.append(h)
    return sorted(strong), sorted(weak)


def check_attr_gates(lock):
    """执行悬空依赖检查，打印结果并返回是否通过。"""
    sources = _read_sources()
    gates = extract_root_gates(sources)
    allow = set(lock.get('attr_gate_allowlist') or [])
    ok = True

    if not gates:
        print('⚠️  ATTR GATES: 未在 CSS 中检出根元素级 data-* 属性门（如属预期可忽略）')
        return True

    for attr in sorted(gates):
        g = gates[attr]
        strong, weak = find_writers(attr, sources)
        if attr in allow:
            if not QUIET:
                print('✅ ATTR GATE OK (allowlist): {}'.format(attr))
            continue
        if strong:
            if not QUIET:
                print('✅ ATTR GATE OK: {}'.format(attr))
                for s in g['selectors'][:2]:
                    print('     selector: {}'.format(s[:120]))
                print('     writer:   {}'.format(', '.join(strong[:4])))
        else:
            ok = False
            print('❌ DANGLING ATTR GATE: {}  ← CSS 依赖它，但没有任何代码设置它'.format(attr))
            for s in g['selectors'][:3]:
                print('     selector: {}'.format(s[:120]))
            print('     defined:  {}'.format(', '.join(g['files'])))
            if weak:
                print('     weak:     仅有 removeAttribute（说明属性被移除却从未被设置）: {}'.format(
                    ', '.join(weak[:3])))
            print('     → 该 CSS 规则将恒成立（门被焊死）。请在 JS 中设置该属性，'
                  '或用多重兜底开闸；确属外部/第三方设置时，把它加入 FRONTEND_LOCK.json 的 '
                  'attr_gate_allowlist。')
    return ok


def _norm(data):
    """换行归一化：CRLF → LF。

    锁指纹按**归一化内容**计算（纵深防御）。历史上本仓库 core.autocrlf=true，
    Windows 工作区是 CRLF(107698B)，而 git 仓库里存的是 LF(104403B)，
    Linux CI checkout 得到的是 LF 版本 —— 若按字节级 sha256 比对，
    CI 上必然 HASH MISMATCH（本地却通过），导致校验形同虚设。
    2026-09-20 起已用 .gitattributes(`* text=auto eol=lf`) 把工作区也统一为 LF，
    归一化退居第二道防线：外部工具再写出 CRLF 时仍能得到正确判定，
    并由 check_eol_policy() 单独指出「是换行问题」而非「内容被改了」。
    """
    return data.replace(b'\r\n', b'\n')


# ── 换行策略检查（2026-09-20 新增）──────────────────────────────────
# 教训：Windows 工作区是 CRLF、仓库是 LF 时，`git status` 会为大量文件
# 报出全文件换行 diff。实测 1152 个「改动」里只有 48 个是真实内容改动，
# 其余全是噪声 —— 真实问题被淹没，同时锁指纹在两端不一致。
# .gitattributes 是唯一权威口径，这里检查它是否仍然存在且策略未被削弱。

_GITATTR_PATH = os.path.join(ROOT, '.gitattributes')
_REQUIRED_RULES = (
    ('* text=auto eol=lf', '默认统一 LF'),
)


def check_eol_policy():
    """校验 .gitattributes 存在且关键策略仍在。"""
    ok = True
    if not os.path.isfile(_GITATTR_PATH):
        print('❌ EOL POLICY MISSING: 仓库根缺少 .gitattributes')
        print('     → 没有它，Windows 工作区会退回 CRLF，')
        print('       锁指纹与 git diff 将再次随平台漂移。')
        return False

    with open(_GITATTR_PATH, encoding='utf-8') as f:
        text = f.read()

    for rule, why in _REQUIRED_RULES:
        if rule not in text:
            print('❌ EOL POLICY WEAKENED: .gitattributes 缺少规则 "{}"（{}）'.format(rule, why))
            ok = False

    if ok and not QUIET:
        print('✅ EOL POLICY OK: .gitattributes 存在，默认 eol=lf 生效')
    return ok


def _fingerprint(path):
    """返回 (归一化 sha256, 工作区原始字节数, 归一化字节数)。"""
    with open(path, 'rb') as f:
        raw = f.read()
    norm = _norm(raw)
    return hashlib.sha256(norm).hexdigest(), len(raw), len(norm)


def main():
    with open(LOCK_PATH, encoding='utf-8') as f:
        lock = json.load(f)

    ok = True

    # 1. 文件级 sha256 校验（归一化换行后比对，跨平台一致）
    mode = lock.get('hash_mode', 'normalized-lf')
    if mode != 'normalized-lf':
        print('❌ UNKNOWN hash_mode: {} (期望 normalized-lf)'.format(mode))
        ok = False
    for rel, info in lock.get('files', {}).items():
        path = os.path.join(ROOT, rel)
        if not os.path.exists(path):
            print('❌ FILE MISSING: {}'.format(rel))
            ok = False
            continue
        actual, raw_size, norm_size = _fingerprint(path)
        if actual != info['sha256']:
            print('❌ HASH MISMATCH: {}'.format(rel))
            print('     expected: {}'.format(info['sha256'][:32]))
            print('     actual:   {}'.format(actual[:32]))
            print('     → 如确认本次前端改动是预期的，请把 FRONTEND_LOCK.json 里该文件的')
            print('       sha256 更新为上面的 actual、size 更新为 {}（归一化字节数）。'.format(norm_size))
            ok = False
        elif not QUIET:
            crlf = raw_size != norm_size
            print('✅ HASH OK: {}  (归一化 {} B{})'.format(
                rel, norm_size, '，工作区为 CRLF 已归一化' if crlf else ''))

    # 2. Section 名语义校验（防止有人改了内容但 hash 还匹配？不可能，hash 过不了，但双保险）
    html_path = os.path.join(ROOT, 'src', 'frontend', 'templates', '_layout_base.html')
    with open(html_path, encoding='utf-8') as f:
        html = f.read()
    found = []
    for m in re.finditer(r'sidebar-section-title[^>]*>(.*?)</div>', html, re.DOTALL):
        title = re.sub(r'<[^>]+>', '', m.group(1)).strip()
        if title:
            found.append(title)
    expected = lock['expected_sections']
    if found != expected:
        print('❌ SECTION MISMATCH')
        print('     expected: {}'.format(' / '.join(expected)))
        print('     actual:   {}'.format(' / '.join(found)))
        ok = False
    elif not QUIET:
        print('✅ SECTIONS OK: {}'.format(' / '.join(found)))

    # 3. 悬空依赖检查（根元素属性门必须有写入点）
    if not check_attr_gates(lock):
        ok = False

    # 4. 换行策略检查（.gitattributes 是跨平台一致性的唯一权威口径）
    if not check_eol_policy():
        ok = False

    print()
    if ok:
        print('🔒 FRONTEND LOCK VERIFIED — all good')
        return 0
    else:
        print('⚠️  FRONTEND LOCK BROKEN — see above')
        return 1


if __name__ == '__main__':
    sys.exit(main())
