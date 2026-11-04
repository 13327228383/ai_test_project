# -*- coding: utf-8 -*-
from __future__ import annotations

"""靶子实证验证 · 不降级断言硬纪律常量 + selector 命名约定。

★ 硬纪律（NO ASSERTION DOWNGRADE）：
    禁止为让报告"变绿"而降级断言。例如"找不到按钮"时退化为"页面加载"假 PASS
    属于违规。任何找不到/不可达必须**如实 FAIL** 或标记 **blocked**，
    绝不允许假成功。该纪律由模块级常量 `NO_ASSERTION_DOWNGRADE = True` 固化，
    供 T02/T03 的实证执行器在编写断言时作为契约检查锚点。

★ selector 命名约定：
    本文件**不再承载任何靶子专属 selector**。DOM 交互元素选择器一律由各靶子的
    ``target_cfg["selectors"]`` 自行注入（见 target_cfgs.py），深断言执行器仅按
    通用键约定读取，无系统级默认兜底——避免引擎被 hospital_sut 等单靶子硬编码绑定。
"""

# ── 不降级断言硬纪律 ──────────────────────────────────────────────────────
# True = 启用硬纪律：任何"找不到元素/按钮/断言对象"必须如实 FAIL 或标记 blocked，
# 禁止退化为"页面加载"等弱断言假 PASS。
NO_ASSERTION_DOWNGRADE: bool = True

# ── 实证模式标识 ────────────────────────────────────────────────────────
# browser：真浏览器打 DOM（hospital_sut / spa_demo）。
# api：纯 requests 打端点（api_demo，无浏览器、无 cookie、无 DOM）。
BROWSER_MODE: str = "browser"
API_MODE: str = "api"

# ── blocked_by 取值字典（见 docs/system_design_expend.md §6）─────────────
# 三者均表示"被正确拒绝、行数/总数不变"，overall 仍 PASS，但字段级错误维度
# 如实记录 pass=False（不掩盖）。
BLOCKED_BY_BROWSER_NATIVE = "browser_native_validation"  # hospital HTML5 required
BLOCKED_BY_CLIENT_VALIDATION = "client_validation"        # spa validate() 客户端拦截
BLOCKED_BY_API_PARAM = "api_param_validation"             # api 40001 后端字段校验
BLOCKED_BY_SERVER_VALIDATION = "server_validation"        # smartshop 服务端校验拦截
# api 靶子未声明 probes（探针随靶子走），引擎 fail-closed 阻断
BLOCKED_BY_MISSING_PROBES = "blocked_by_missing_probes"

# ── 通用 selector 键约定（跨靶子统一，见 docs/system_design_expend.md §3.2）──
# 深断言执行器按以下键从 target_cfg["selectors"] 读取，本文件不提供系统级默认值：
#   list_items / btn_new / input_title / input_price / input_stock /
#   btn_save / form / field_error / pagination_current / list_text / delete_btn
# 各靶子的实际 selector 值统一在 target_cfgs.py 的 TARGET_CFGS 中声明。
