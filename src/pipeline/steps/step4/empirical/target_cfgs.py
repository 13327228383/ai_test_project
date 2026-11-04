# -*- coding: utf-8 -*-
from __future__ import annotations

"""[未复核-需 GLM/DeepSeek 把关] 多靶子配置注册表（target_cfg 契约）。

★ 职责：把"真实验证能力"从单靶子（hospital_sut）扩到由 ``target_cfg`` 驱动的
  通用能力。集中注册所有已知靶子（hospital_sut / api_demo / spa_demo / smartshop），
  提供 ``get_target_cfg(key)`` 做默认值合并与缺失补全。

★ target_cfg 契约（见 docs/system_design_expend.md §3.1）：
  - 必含 ``target_key`` / ``mode``（browser | api）。
  - browser 模式：url / port / selectors / pagination / auth(form_cookie)。
  - api 模式：url / port / auth(bearer_token) / launch(flask_script)。
  - 通用选择器键（跨靶子统一，见 §3.2）：
    list_items / btn_new / input_title / input_price / input_stock /
    btn_save / form / field_error / pagination_current。

★ 依赖方向：仅依赖标准库 + constants（避免循环依赖）。
"""

import logging
from pathlib import Path

from src.pipeline.steps.step4.empirical.constants import (
    API_MODE,
    BLOCKED_BY_BROWSER_NATIVE,
    BLOCKED_BY_CLIENT_VALIDATION,
    BLOCKED_BY_SERVER_VALIDATION,
)

_log = logging.getLogger(__name__)

# 项目根（target_cfgs.py 位于 <root>/src/.../empirical）。
# 鲁棒解析：向上找到同时含 "src" 与 "scripts" 的祖先目录。
def _resolve_project_root() -> str:
    cur = Path(__file__).resolve().parent
    for _ in range(10):
        if (cur / "src").is_dir() and (cur / "scripts").is_dir():
            return str(cur)
        if cur.parent == cur:
            break
        cur = cur.parent
    # 兜底：empirical 向上 5 级
    return str(Path(__file__).resolve().parent.parent.parent.parent.parent)


_PROJECT_ROOT: str = _resolve_project_root()

# hospital_sut 专属 browser selector（在 target_cfg 内自声明，供该靶子注入；
# 通用引擎已移除任何系统级默认 selector，选择器只随各靶子配置流转）。
_HOSPITAL_DEFAULT_SELECTORS: dict[str, str] = {
    "list_items": "table[data-testid='table-patients'] tbody tr",
    "list_text": "table[data-testid='table-patients']",
    "btn_new": "a[data-testid='btn-new-patient']",
    "input_title": "input[data-testid='input-name']",
    "input_price": "input[data-testid='input-mrn']",
    "input_stock": "input[data-testid='input-mrn']",
    "btn_save": "button[data-testid='btn-save']",
    "form": "form[data-testid='patient-form']",
    "field_error": ".flash-danger, .flash-warning",
    "pagination_current": "[data-testid='pagination-current']",
}

# spa_demo 原生 JS SPA 的 id/class 选择器（无 data-testid）。
_SPA_DEMO_SELECTORS: dict[str, str] = {
    "list_items": "#view li",          # 列表项（#view 容器内 li）
    "btn_new": "a[href='#/books/new']",  # 新增图书链接
    "input_title": "#f_title",
    "input_price": "#f_price",
    "input_stock": "#f_stock",
    "btn_save": "#f_submit",
    "form": "form",
    "field_error": ".error",
    # 列表文本容器：SPA 用 #view 渲染，无 table[data-testid]。
    "list_text": "#view",
    # SPA 前端全量渲染（localStorage），无分页。
    "pagination_current": "",
    # D01 删除：每行的删除按钮（li 内 class="btn danger" 且文本"删除"）。
    "delete_btn": "button.btn.danger",
}


# ── target_cfg 字段约束（供校验，docs §3.1）────────────────────────────
TARGET_CFG_SCHEMA: dict = {
    "title": "TargetConfig",
    "type": "object",
    "required": ["target_key", "mode"],
    "properties": {
        "target_key": {"type": "string"},
        "mode": {"type": "string", "enum": ["browser", "api"]},
        # ★ 用例绑定专项(2026-08): 被测系统版本（如 "1.0.0"）。
        #   用例上传时会校验"文件 version" 与 "当前靶子 version" 是否一致。
        "version": {"type": "string"},
        "url": {"type": "string"},
        "port": {"type": "integer"},
        "base_url": {"type": "string"},
        "auth": {
            "type": "object",
            "properties": {
                "type": {
                    "type": "string",
                    "enum": ["form_cookie", "bearer_token", "none"],
                },
                "login_url": {"type": "string"},
                "login_fields": {"type": "object"},
                "token_field": {"type": "string"},
            },
        },
        "launch": {
            "type": "object",
            "properties": {
                "kind": {
                    "type": "string",
                    "enum": ["flask_factory", "flask_script", "static_serve", "none"],
                },
                "cmd": {"type": "string"},
                "cwd": {"type": "string"},
                "factory": {"type": "string"},
                "env": {"type": "object"},
            },
        },
        "selectors": {"type": "object"},
        "pagination": {
            "type": "object",
            "properties": {
                # 注意：enum 必须与运行时支持对齐。smartshop 用 "browser"
                # （解析"第X/Y页"逐页累加），运行时 _count_total 已支持，此处补 "browser"。
                "type": {
                    "type": "string",
                    "enum": ["server_page_param", "none", "browser"],
                },
                "param": {"type": "string"},
                "current_sel": {"type": "string"},
            },
        },
        "db_env": {"type": "string"},
        "cases": {"type": "array"},
        "report_path": {"type": "string"},
        "default_blocked_by": {"type": "string"},
        # 字段填充策略（per-target，见 deep_assertion_runner._resolve_fill_value）。
        "fill": {
            "type": "object",
            "properties": {
                "positive": {"type": "object"},
                "negative": {"type": "object"},
            },
        },
        "authz": {
            "type": "object",
            "properties": {
                "permission_matrix": {"type": "object"},
                "probes": {"type": "array"},
            },
        },
        # 导航数据种子声明：file（相对项目根）+ fn（可调用名），随靶子存放。
        "nav_seed": {
            "type": "object",
            "properties": {"file": {"type": "string"}, "fn": {"type": "string"}},
        },
    },
}


# ── 4 靶子注册表 ────────────────────────────────────────────────────────
TARGET_CFGS: dict[str, dict] = {
    # (1) hospital_sut：既有 browser 靶子，真实字段校验（后端拒绝非法值）。
    "hospital_sut": {
        "target_key": "hospital_sut",
        "mode": "browser",
        "version": "1.0.0",  # ★ 用例绑定专项：被测系统版本（占位，靶子负责人按需修订）
        "url": "http://127.0.0.1:5081",
        "port": 5081,
        "base_url": "http://127.0.0.1:5081",
        "list_path": "/patients",   # 列表基地址（拼到 base_url 后为 http://127.0.0.1:5081/patients）
        "auth": {
            "type": "form_cookie",
            "login_url": "/login",
            "login_fields": {
                "username_field": "username",
                "password_field": "password",
                "username": "admin",
                "password": "admin123",
            },
        },
        # nav_seed：导航数据种子随靶子放（file 相对项目根），引擎按声明加载；
        # 未声明的靶子 seeded=False，不回落任何靶子的种子。
        "nav_seed": {
            "file": "target_systems/hospital_sut/nav_seed.py",
            "fn": "main",
        },
        "launch": {
            "kind": "flask_factory",
            # factory 表达式为自包含启动命令（含导入），SutHarness 仅按 launch.cwd
            # 注入 sys.path/os.chdir 后 exec，不再写死 from app import create_app。
            "factory": "from app import create_app; create_app().run(host='127.0.0.1', port=5081, threaded=True)",
            "cwd": str(Path(_PROJECT_ROOT) / "target_systems" / "hospital_sut"),
            "env": {"HOSPITAL_SUT_DB": "<writable_db>"},
            "seed_cmd": "python seed.py",
        },
        "selectors": dict(_HOSPITAL_DEFAULT_SELECTORS),
        "pagination": {"type": "server_page_param", "param": "page",
                        "current_sel": "[data-testid='pagination-current']"},
        "db_env": "HOSPITAL_SUT_DB",
        # ★ visual 期望基线（2026-09-04 补齐）：提供期望描述以启用视觉门禁，
        #   使 visual 横切维度不再因"无 expectation"诚实 NO_DATA。
        "visual": {
            "enabled": True,
            "source": "target_empirical",
            "expectation": "页面渲染为患者列表页：顶部有导航/标题，主体为一张含表头的"
                           "数据表格，表格内显示若干行患者记录，每行含姓名、病历号等字段",
        },
        # B01 空提交被表单原生 HTML5 required 拦截 → browser_native_validation
        # （作为 default_blocked_by 兜底，case 漏填 blocked_by 时也不误判）。
        "default_blocked_by": BLOCKED_BY_BROWSER_NATIVE,
        # 字段填充策略（per-target）：把「表单怎么填」从执行器下沉到靶子配置。
        #   positive：正向新增。title=<unique> 唯一值；price=<mrn> 生成唯一 MRN
        #     （input_price 映射 MRN 字段，服务端要求 ^[A-Za-z]+\d[\w-]{2,31}$ 且唯一，
        #     避免与累积数据 UNIQUE 冲突）；stock=5（input_stock 与 input_price 同
        #     映射 MRN，执行器按「同 selector 去重」跳过，不覆盖 MRN）。
        #   negative：非法提交。title=!@#$%（后端按姓名维度拒绝）；price/stock 跳填。
        #   token 语义见 deep_assertion_runner._resolve_fill_value。
        "fill": {
            "positive": {"title": "<unique>", "price": "<mrn>", "stock": "5"},
            "negative": {"title": "!@#$%", "price": None, "stock": None},
        },
        "cases": [
            {"case_id": "P01", "fill_invalid": False},
            {"case_id": "N01", "fill_invalid": True},
            {"case_id": "B01", "fill_invalid": False},
        ],
    },

    # (2) api_demo：纯 REST 无 UI，必须走 API 模式（requests，不打浏览器）。
    "api_demo": {
        # 探针套件随靶子走：引擎按文件路径动态加载（nav_seed 同款去靶子化模式）
        "probes": {
            "file": "examples/api_demo/empirical_probes.py",
            "cls": "ApiAssertionRunner",
        },
        "target_key": "api_demo",
        "mode": API_MODE,  # "api"
        "version": "1.0.0",  # ★ 用例绑定专项：被测系统版本（占位）
        "url": "http://127.0.0.1:8010",
        "port": 8010,
        "base_url": "http://127.0.0.1:8010",
        "auth": {
            "type": "bearer_token",
            "login_url": "/api/v1/auth/login",
            "login_fields": {
                "username_field": "username",
                "password_field": "password",
                "admin_username": "admin",
                "admin_password": "admin123",
                "user_username": "user",
                "user_password": "user123",
            },
            "token_field": "data.token",
            # AuthzMatrixVerifier 用的角色凭证（横切 authz 门禁驱动源）。
            "roles": {
                "admin": {"username": "admin", "password": "admin123"},
                "user": {"username": "user", "password": "user123"},
            },
        },
        # ★ 越权/访问控制横切门禁输入（2026-09-05）：api_demo 为 RBAC 靶子
        #   （DELETE 仅 admin），声明 authz 配置后，run_cross_cut_gates 自动
        #   以 run 级执行以下权限探针（正向/负向/边界全覆盖）。全部探针期望
        #   PASS（即权限控制正确）；若核对时改编靶子去掉 admin 校验，user DELETE
        #   实际返回 200 而期望 403 → 越权漏洞被捕（FAIL），验证判真能力。
        "authz": {
            "permission_matrix": {
                "admin": ["books_read", "books_write", "books_delete"],
                "user": ["books_read", "books_write"],
            },
            "state_reset": {"path": ""},  # api_demo 无显式复位，保持探针幂等
            "probes": [
                {"id": "AZ-01", "role": "user", "method": "GET",
                 "path": "/api/v1/books", "expected": "allow", "expected_code": 200},
                {"id": "AZ-02", "role": "admin", "method": "GET",
                 "path": "/api/v1/books", "expected": "allow", "expected_code": 200},
                {"id": "AZ-03", "role": "anonymous", "method": "GET",
                 "path": "/api/v1/books", "expected": "deny", "expected_code": 401},
                {"id": "AZ-04", "role": "user", "method": "DELETE",
                 "path": "/api/v1/books/1", "expected": "deny", "expected_code": 403},
                {"id": "AZ-05", "role": "admin", "method": "POST",
                 "path": "/api/v1/books", "expected": "allow", "expected_code": 201,
                 "body": {"title": "authz-probe", "author": "engine",
                          "price": 9.9, "stock": 1}},
            ],
        },
        "launch": {
            "kind": "flask_script",
            "cmd": "python examples/api_demo/app.py",
            "cwd": _PROJECT_ROOT,
        },
        "selectors": {},  # API 模式无 selector
        "pagination": {"type": "none"},
        "cases": [
            {"case_id": "P01", "fill_invalid": False},
            {"case_id": "N01", "fill_invalid": True},
            {"case_id": "B01", "fill_invalid": False},
            {"case_id": "A01", "fill_invalid": False},
        ],
    },

    # (3) spa_demo：纯前端 SPA（原生 JS + localStorage），id/class 选择器。
    #     T03 完整适配：hash 路由（list_path=/index.html#/books）、pagination=none
    #     （前端全量渲染）、B01 客户端 validate() 拦截 → blocked_by=client_validation。
    "spa_demo": {
        "target_key": "spa_demo",
        "mode": "browser",
        "version": "1.0.0",  # ★ 用例绑定专项：被测系统版本（占位）
        "url": "http://127.0.0.1:8012/index.html",
        "port": 8012,
        "base_url": "http://127.0.0.1:8012/index.html",
        "list_path": "#/books",   # hash 路由列表（拼到 base_url 后为 .../index.html#/books）
        "list_text_sel": "#view",            # 列表文本容器
        "auth": {"type": "none"},
        "launch": {
            "kind": "static_serve",
            "cmd": "python -m http.server 8012",
            "cwd": str(Path(_PROJECT_ROOT) / "examples" / "spa_demo"),
        },
        "selectors": dict(_SPA_DEMO_SELECTORS),
        "pagination": {"type": "none"},
        # ★ visual 期望基线（2026-09-04 补齐）：SPA 列表页，启用视觉门禁。
        "visual": {
            "enabled": True,
            "source": "target_empirical",
            "expectation": "页面渲染为图书列表视图（#view 容器）：包含标题/导航，"
                           "列表内显示若干行图书记录，每行展示书名及数量/价格等信息",
        },
        # B01/N01 客户端 validate() 拦截（title 必填 / price>0 / stock>=0）→
        # client_validation 兜底（case 漏填 blocked_by 时也不误判）。
        "default_blocked_by": BLOCKED_BY_CLIENT_VALIDATION,
        # 字段填充策略（per-target）。
        #   negative：非法提交。title 留空（不填 → validate 触发「标题不能为空」拦截）；
        #     price=99 / stock=5 填合法值，绕过 price/stock 校验，让拦截精准落在
        #     title 维度（不掩盖语义）。positive 复用引擎通用默认（见 runner）。
        "fill": {
            "negative": {"title": None, "price": "99", "stock": "5"},
        },
        "cases": [
            {"case_id": "P01", "fill_invalid": False},
            {"case_id": "N01", "fill_invalid": True},
            # B01：price<=0 被客户端 validate() 拦截 → 不写库 → blocked。
            {"case_id": "B01", "fill_invalid": False,
             "blocked_by": BLOCKED_BY_CLIENT_VALIDATION},
            # D01：删除第一条 → 列表行数 -1 且被删标题消失。
            {"case_id": "D01", "fill_invalid": False},
        ],
    },

    # (4) smartshop：Flask 商品管理靶子（带服务端分页的浏览器 CRUD）。
    #     新建 examples/smartshop/app.py（端口 8020，SQLite 自包含），补全真实可跑字段。
    #     分页类型 browser：列表页显示"第X/Y页"，执行器逐页累加得总数。
    "smartshop": {
        "target_key": "smartshop",
        "mode": "browser",
        "version": "1.0.0",  # ★ 用例绑定专项：被测系统版本（占位）
        "url": "http://127.0.0.1:8020",
        "port": 8020,
        "base_url": "http://127.0.0.1:8020",
        "list_path": "/products",   # 列表基地址（拼到 base_url 后为 http://127.0.0.1:8020/products）
        "list_text_sel": "#product-table",
        "auth": {"type": "none"},
        # 实时可写库：smartshop 默认库 examples/smartshop/smartshop.db 是跟踪文件，
        # 沙箱只读会写失败；统一经 sut_harness 的 <writable_db> 落到 .verify_dbs/。
        "db_env": "SMARTSHOP_DB",
        # 最小扩展：直接给 launch.cmd 拉起 examples/smartshop/app.py，
        # 不改造 sut_harness 的 target_systems 约束。
        "launch": {
            "kind": "flask_script",
            "cmd": "python examples/smartshop/app.py",
            "cwd": _PROJECT_ROOT,
            "env": {"SMARTSHOP_DB": "<writable_db>"},
        },
        "selectors": {
            "list_items": "#product-table tbody tr",   # 商品行
            "btn_new": "#btn-new",                      # 新增按钮
            "input_title": "#name",                     # 商品名称
            "input_price": "#price",                    # 价格
            "input_stock": "#stock",                    # 库存
            "input_category": "#category",              # 分类
            "btn_save": "#btn-save",                    # 保存
            "form": "#product-form",                    # 新增表单
            "field_error": ".field-error",              # 服务端校验错误位
            "list_text": "#product-table",
            # 分页"第X/Y页"文本（浏览器分页，逐页累加得总数）。
            "pagination_current": "#pager",
            # D01 删除：每行的删除按钮（表单内 submit）。
            "delete_btn": "form[action*='/delete'] button.delete-btn",
            # 行内编辑入口（GET /products/<id>/edit）。
            "edit_btn": "a[href*='/edit']",
        },
        "pagination": {
            "type": "browser",
            "current_sel": "#pager",
        },
        # ★ visual 期望基线（2026-09-04 补齐）：商品列表页，启用视觉门禁。
        "visual": {
            "enabled": True,
            "source": "target_empirical",
            "expectation": "页面渲染为商品列表页：顶部有标题/操作按钮，主体为一张含表头的"
                           "数据表格，表格内显示若干行商品记录，每行含名称、价格、库存等信息",
        },
        # 字段填充策略（per-target）。
        #   negative：非法提交。title=<unique> 唯一非重复；price=0（<=0 → 服务端
        #     「价格必须大于 0」拒绝）；stock=1 合法正整数，隔离到 price 维度暴露
        #     服务端校验。positive 复用引擎通用默认（见 runner）。
        "fill": {
            "negative": {"title": "<unique>", "price": "0", "stock": "1"},
        },
        "cases": [
            # P01：新增+1（name 用唯一值，避免 UNIQUE 冲突）。
            {"case_id": "P01", "fill_invalid": False},
            # N01：非法值（name 空或重复 / price<=0 / stock<0）→ 报错行数不变。
            {"case_id": "N01", "fill_invalid": True},
            # B01：边界被服务端校验拦截（price<=0 或 name 空）→ blocked_by 服务端校验。
            #   ★ 配置漏填兜底：即使 case 未显式声明 blocked_by，也由 target_cfg
            #     default_blocked_by 提供（smartshop 表单无 HTML5 required，
            #     不应误判 browser_native_validation）。
            {"case_id": "B01", "fill_invalid": False,
             "blocked_by": BLOCKED_BY_SERVER_VALIDATION},
            # D01：删除第一条 → 列表行数 -1。
            {"case_id": "D01", "fill_invalid": False},
        ],
        # §14 问题 6 修复：smartshop 表单无 HTML5 required，B01 边界必被服务端校验
        # 拦截；作为 default_blocked_by 兜底，case 漏填 blocked_by 时也不误判。
        "default_blocked_by": BLOCKED_BY_SERVER_VALIDATION,
    },

    # (5) fullcov_sut：全能力覆盖靶子（2026-09-22 新增，资产管理域，端口 8050）。
    #     六场景 view/create/edit/delete/search/security + 真实分页（5/页，
    #     23 条种子）+ 客户端 JS 校验 + 服务端双重校验 + 角色权限
    #     （admin 全权 / viewer 只读 403）。选择器全量 data-testid 契约。
    #     配套验收矩阵：docs/全覆盖靶子验收矩阵.md（44 指标族点亮验收）。
    "fullcov_sut": {
        "target_key": "fullcov_sut",
        "mode": "browser",
        "version": "1.0.0",
        "url": "http://127.0.0.1:8050",
        "port": 8050,
        "base_url": "http://127.0.0.1:8050",
        "list_path": "/assets",
        "list_text_sel": "[data-testid='table-assets']",
        "auth": {
            "type": "form_cookie",
            "login_url": "/login",
            "login_fields": {
                "username_field": "username",
                "password_field": "password",
                "username": "admin",
                "password": "admin123",
            },
            "roles": {
                "admin": {"username": "admin", "password": "admin123"},
                "viewer": {"username": "viewer", "password": "viewer123"},
            },
        },
        "launch": {
            "kind": "flask_script",
            "cmd": "python target_systems/fullcov_sut/app.py",
            "cwd": _PROJECT_ROOT,
        },
        "selectors": {
            "list_items": "table[data-testid='table-assets'] tbody tr",
            "list_text": "table[data-testid='table-assets']",
            "row_title_sel": "[data-testid='asset-name']",
            "btn_new": "a[data-testid='btn-new-asset']",
            "input_title": "input[data-testid='input-title']",
            "input_price": "input[data-testid='input-price']",
            "input_stock": "input[data-testid='input-stock']",
            "btn_save": "button[data-testid='btn-save']",
            "form": "form[data-testid='asset-form']",
            "field_error": ".flash-danger",
            "pagination_current": "[data-testid='pagination-current']",
            "delete_btn": "button[data-testid='btn-delete']",
            "input_confirm": "input[data-testid='input-confirm']",
        },
        "pagination": {"type": "server_page_param", "param": "page",
                        "current_sel": "[data-testid='list-summary']"},
        "visual": {
            "enabled": True,
            "source": "target_empirical",
            "expectation": "页面渲染为资产列表页：顶部导航含资产/审计链接，主体为"
                           "含表头的资产数据表格，每行含名称、分类、价格、库存、"
                           "状态与操作列，底部有分页控件",
        },
        # B01：客户端 JS 校验（价格>0/库存非负整数/勾选确认）先拦截。
        "default_blocked_by": BLOCKED_BY_CLIENT_VALIDATION,
        "fill": {
            # positive：唯一名称 + 合法价格/库存（客户端与服务端都放行）；
            # confirm 勾选「信息确认」checkbox（该靶子客户端校验的必勾项）。
            "positive": {"title": "<unique>", "price": "299.5", "stock": "7",
                         "confirm": ["input_confirm"]},
            # negative：价格为 0（客户端与服务端双重拦截，隔离到 price 维度）。
            "negative": {"title": "<unique>", "price": "0", "stock": "3",
                         "confirm": ["input_confirm"]},
        },
        "cases": [
            {"case_id": "P01", "fill_invalid": False},
            {"case_id": "N01", "fill_invalid": True},
            {"case_id": "B01", "fill_invalid": False,
             "blocked_by": BLOCKED_BY_CLIENT_VALIDATION},
            {"case_id": "D01", "fill_invalid": False},
        ],
    },
}


# ── 默认值合并 ──────────────────────────────────────────────────────────
_DEFAULTS: dict = {
    "mode": "browser",
    "port": 0,
    "url": "",
    "base_url": "",
    "auth": {"type": "none"},
    "launch": {"kind": "none"},
    "selectors": {},
    "pagination": {"type": "none"},
    "cases": [],
}


def get_target_cfg(key: str) -> dict:
    # [未复核-需 GLM/DeepSeek 把关] 核心路径：默认值合并 + 透传字段，影响所有验证用例。
    """按 key 取靶子配置，并做默认值合并（不修改注册表原对象）。

    Args:
        key: 靶子标识，如 hospital_sut / api_demo / spa_demo / smartshop。

    Returns:
        合并默认值后的靶子配置 dict。

    Raises:
        KeyError: key 不在 TARGET_CFGS 注册表中。
    """
    if key not in TARGET_CFGS:
        raise KeyError(
            f"未知靶子 key={key!r}；已注册靶子={list(TARGET_CFGS.keys())}"
        )
    base = TARGET_CFGS[key]
    merged: dict = {
        "target_key": key,
        "mode": base.get("mode", _DEFAULTS["mode"]),
        # ★ 用例绑定专项：透传 version（缺省占位 "1.0.0"，方便未配靶子也能校验）
        "version": base.get("version", "1.0.0"),
        "url": base.get("url", _DEFAULTS["url"]),
        "port": base.get("port", _DEFAULTS["port"]),
        "base_url": base.get("base_url", base.get("url", _DEFAULTS["base_url"])),
        "auth": {**_DEFAULTS["auth"], **(base.get("auth") or {})},
        "launch": {**_DEFAULTS["launch"], **(base.get("launch") or {})},
        "selectors": dict(base.get("selectors") or {}),
        # 探针套件声明（随靶子走）：原样透传，引擎按声明动态加载
        "probes": dict(base.get("probes") or {}),
        "pagination": {**_DEFAULTS["pagination"], **(base.get("pagination") or {})},
        "cases": list(base.get("cases") or []),
        "db_env": base.get("db_env", ""),
    }
    # 透传可选字段（browser 通用靶子导航/文本容器 + 配置兜底/权限验证）
    for opt in (
        "report_path", "skip", "skip_reason",
        "list_path", "list_text_sel", "default_blocked_by", "authz",
        "verification", "fill", "visual", "nav_seed",
    ):
        if opt in base:
            merged[opt] = base[opt]
    return merged
