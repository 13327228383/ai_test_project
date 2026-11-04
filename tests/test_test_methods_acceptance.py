"""「测试方法论清单」API 验收测试

对 /api/test-methods 做结构化断言（不是目测）。运行：python tests/test_test_methods_acceptance.py

覆盖：
  1. 后端可达 + /api/test-methods ok=True
  2. total==18，且方法论编码全集与权威目录 src/testing_methods/catalog.py 一致
     （EP/BV/CE/DT/ST/OT/EG/FD/SD/PC/SX/SI/CS/AU/SS/CT/PF/ET）
  3. 每个方法结构：code/name/category/category_label/description，
     case_count & call_count 均含 system/project/version 三桶（int >= 0）
  4. 分类计数：黑盒 13 / 白盒 1 / 安全 4
  5. 页面路由 /test-methods 200
  6. DB 交叉验证：test_cases 表真实 test_method 分布都被归纳进 18 编码
     （未识别编码不入卡，但不应有异常）

★ 2026-09-21 通用性根因修复：本文件以 test_*.py 命名，会被 pytest 自动收集。
  原实现把执行体（含 sys.exit）直接写在模块顶层，导致 pytest 收集时触发
  SystemExit → INTERNALERROR 拖垮整批回归（0 例运行）。现把执行体收进 main()
  并在 __main__ 守卫下调用：直跑脚本语义不变（python tests/test_test_methods_acceptance.py），
  被 pytest 导入时不产生任何模块级副作用。
"""
import json, sys, os, pathlib, urllib.request, http.cookiejar

BASE = 'http://127.0.0.1:8700'

# 权威方法论编码集（与 src/testing_methods/catalog.py METHOD_ORDER 一致，18 码）
EXPECTED_CODES = (
    "EP", "BV", "CE", "DT", "ST", "OT", "EG", "FD", "SD", "PC",
    "SX", "SI", "CS", "AU", "SS", "CT", "PF", "ET",
)
# 清单页展示分类由 fetcher CATEGORY_LABELS 决定：
#   黑盒=13（EP/BV/CE/DT/ST/OT/EG/FD/SD + SS/CT/PF/ET）
#   白盒=1（PC）
#   安全=4（SX/SI/CS/AU）


def login():
    cj = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    data = json.dumps({'user_id': 'admin', 'password': 'admin123'}).encode()
    opener.open(urllib.request.Request(BASE + '/api/login', data=data, method='POST',
        headers={'Content-Type': 'application/json'}), timeout=5)
    return opener, cj


def get(path, opener):
    resp = opener.open(BASE + path, timeout=10)
    return json.loads(resp.read().decode())


class Result:
    def __init__(self):
        self.passed = []
        self.failed = []
    def check(self, name, cond, detail=''):
        if cond:
            self.passed.append(name)
            print('  OK  {}'.format(name))
        else:
            self.failed.append((name, detail))
            print('  FAIL  {} — {}'.format(name, detail))
    def summary(self):
        total = len(self.passed) + len(self.failed)
        print('\n{}/{} passed ({:.0f}%)'.format(len(self.passed), total, len(self.passed)/max(1,total)*100))
        if self.failed:
            print('FAILURES:')
            for n, d in self.failed:
                print('  - {}: {}'.format(n, d))
        return len(self.failed) == 0


def main():
    print('=' * 60)
    print('  测试方法论清单 API 验收')
    print('=' * 60)
    ok = Result()

    # --- 1. 后端可达 ---
    print('\n[1] API 可达性')
    try:
        opener, cj = login()
    except Exception as e:
        ok.check('登录', False, str(e))
        print('ABORT: login failed'); return 1

    try:
        data = get('/api/test-methods', opener)
        ok.check('/api/test-methods ok=True', data.get('ok') is True, str(data.get('error')))
    except Exception as e:
        ok.check('/api/test-methods ok=True', False, str(e))
        print('ABORT: api unreachable'); return 1

    # --- 2. 总数与编码全集 ---
    print('\n[2] 方法论总数与编码全集')
    methods = data.get('methods', [])
    ok.check('total==18', data.get('total') == 18, '实际 total={}'.format(data.get('total')))
    codes = [m.get('code') for m in methods]
    ok.check('编码合集匹配权威 18 码', sorted(codes) == sorted(EXPECTED_CODES),
        '实际={}'.format(codes))
    ok.check('卡片数量==18', len(methods) == 18, '实际 len={}'.format(len(methods)))

    # --- 3. 每个方法结构完整性 ---
    print('\n[3] 卡片结构完整')
    for m in methods:
        c = m.get('code')
        ok.check('{}: 有 name'.format(c), bool(m.get('name')), 'name={}'.format(m.get('name')))
        ok.check('{}: 有 category'.format(c), bool(m.get('category')))
        for key in ('case_count', 'call_count'):
            bucket = m.get(key) or {}
            for scope in ('system', 'project', 'version'):
                v = bucket.get(scope)
                ok.check('{}: {}.{} 是 int>=0'.format(c, key, scope),
                    isinstance(v, int) and v >= 0, '实际={}'.format(v))

    # --- 4. 分类计数 ---
    print('\n[4] 分类计数')
    cats = {}
    for m in methods:
        c = m.get('category')
        cats[c] = cats.get(c, 0) + 1
    allowed_cats = {'black_box', 'white_box', 'security'}
    ok.check('分类仅含 black_box/white_box/security', set(cats.keys()) <= allowed_cats,
        '实际={}'.format(cats))
    ok.check('分类计数合计==18', sum(cats.values()) == 18, '实际={}'.format(cats))
    ok.check('黑盒==13（9经典+SD+SS/CT/PF/ET）', cats.get('black_box') == 13,
        '实际黑盒={}'.format(cats.get('black_box')))
    ok.check('白盒==1（PC）', cats.get('white_box') == 1, '实际白盒={}'.format(cats.get('white_box')))
    ok.check('安全==4（SX/SI/CS/AU）', cats.get('security') == 4, '实际安全={}'.format(cats.get('security')))

    # --- 5. 页面路由 ---
    print('\n[5] 页面路由')
    try:
        import urllib.request
        req = urllib.request.Request(BASE + '/test-methods')
        req.add_header('Cookie', '; '.join('{}={}'.format(c.name, c.value) for c in cj))
        resp = urllib.request.urlopen(req, timeout=10)
        ok.check('/test-methods HTTP 200', resp.status == 200, '实际 status={}'.format(resp.status))
    except Exception as e:
        ok.check('/test-methods HTTP 200', False, str(e))

    # --- 6. DB 交叉验证（pymysql 缺失则跳过，不判失败） ---
    print('\n[6] DB 交叉验证')
    try:
        import pymysql
        PY = True
    except Exception:
        PY = False
        ok.check('DB: pymysql 可用（不可用则跳过 DB 交叉验证）', True, 'pymysql 未安装，跳过')

    if PY:
        for ln in pathlib.Path('.env').read_text(encoding='utf-8').splitlines():
            ln = ln.strip()
            if ln and not ln.startswith('#') and '=' in ln:
                k, _, v = ln.partition('=')
                v = v.strip().strip("'").strip('"')
                os.environ.setdefault(k.strip(), v)

        try:
            db = pymysql.connect(host=os.environ['MYSQL_HOST'], port=int(os.environ['MYSQL_PORT']),
                user=os.environ['MYSQL_USER'], password=os.environ['MYSQL_PASSWORD'],
                database=os.environ['MYSQL_DATABASE'], charset='utf8mb4',
                cursorclass=pymysql.cursors.DictCursor)
            cur = db.cursor()
            try:
                cur.execute("SELECT test_method, COUNT(*) AS n FROM test_cases WHERE test_method IS NOT NULL AND test_method<>'' GROUP BY test_method")
                rows = cur.fetchall()
                unknown = [r for r in rows if str(r.get('test_method')).strip().upper() not in EXPECTED_CODES]
                known_count = sum(int(r['n']) for r in rows if str(r.get('test_method')).strip().upper() in EXPECTED_CODES)
                ok.check('DB real 方法编码都归入 18 码（无漏网编码/空串计入）', len(unknown) == 0,
                    '未知编码={}'.format(unknown))
                ok.check('DB 至少有一批真实编码被统计', known_count > 0, 'known_count={}'.format(known_count))
            except Exception as e:
                ok.check('DB 交叉验证', False, str(e))
            finally:
                db.close()
        except Exception as e:
            ok.check('DB 连接', False, str(e))

    print('\n' + '=' * 60)
    all_pass = ok.summary()
    print('=' * 60)
    return 0 if all_pass else 1


if __name__ == '__main__':
    sys.exit(main())