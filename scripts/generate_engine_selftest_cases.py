# -*- coding: utf-8 -*-
"""引擎自检全链路测试用例集 生成器 v2
========================================
对象：把「智能测试引擎」本身当作被测系统，对引擎的
  ① 操作指令轨 ② 断言指令轨 ③ 前端控件轨 ④ 数据链路(指标/DB)轨
  ⑤ 功能链路(Step1~5)轨 ⑥ 横切门禁轨 ⑦ 测试方法论轨 ⑧ 审核闸门轨
逐一产出「具体到怎么执行、怎么校验」的可执行可校验用例。

规则化组装：数据源为引擎唯一权威枚举（catalog.py / _taxonomy.py /
cross_cut_gates.py / scheduler.py / fetchers / schema.sql），
变化点按域系统扩展，生成数千条真实用例。

每条用例字段：用例ID/功能域/子模块/用例标题/测试方法码/变化点/前置条件/
操作步骤/预期结果/关键操作指令/关键校验断言指令/目标功能点/优先级/风险等级/证据出处

用法：python scripts/generate_engine_selftest_cases.py
输出：docs/引擎端到端测试用例集.xlsx（全量用例 / 覆盖矩阵 / 生成说明）
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.testing_methods.catalog import (
    METHOD_ORDER, METHOD_CATALOG,
)

XLSX_PATH = ROOT / "docs" / "引擎端到端测试用例集.xlsx"

# ── 方法论 18 码（单一权威源）──────────────────────────────
METHOD_CN: dict[str, str] = {}
for code in METHOD_ORDER:
    if code in METHOD_CATALOG:
        METHOD_CN[code] = METHOD_CATALOG[code][0]
ALL_CODES = list(METHOD_ORDER)


def _m(code: str) -> str:
    """方法列出标准 18 码；非法值兜底 FD。"""
    code = str(code or "").upper()
    return code if code in METHOD_CN else "FD"


# ── 操作指令全集（_taxonomy.py ACTION_TAXONOMY/CANONICAL_ACTIONS）──
BROWSER_ACTIONS = {
    "click": "鼠标-点击", "double_click": "鼠标-双击", "right_click": "鼠标-右键",
    "hover": "鼠标-悬停", "drag": "鼠标-拖拽", "scroll": "鼠标-滚动",
    "middle_click": "鼠标-中键", "fill": "表单-填充", "select_option": "表单-下拉选择",
    "check": "表单-勾选", "uncheck": "表单-取消勾选", "upload_file": "表单-上传",
    "set_date": "表单-日期", "press_key": "键盘-按键", "type_text": "键盘-键入文本",
    "clear": "键盘-清空", "key_down": "键盘-按下", "key_up": "键盘-抬起",
    "wheel": "键盘-滚轮", "navigate": "导航-跳转", "wait": "导航-等待",
    "page_reload": "导航-刷新", "go_back": "导航-后退", "go_forward": "导航-前进",
    "close_tab": "导航-关标签页", "focus": "高级-聚焦", "blur": "高级-失焦",
    "long_press": "高级-长按", "switch_tab": "高级-切标签页", "switch_to_iframe": "高级-切iframe",
    "switch_to_default": "高级-回主文档", "download_file": "高级-下载", "drag_offset": "高级-偏移拖拽",
    "resize_window": "高级-改视窗", "swipe": "高级-滑动", "context_menu_click": "高级-右键菜单",
    "network_fault": "网络-注入故障", "identity_switch": "身份-切换身份", "service_mock": "Mock-服务替身",
}
API_METHODS = ["GET", "POST", "PUT", "DELETE", "PATCH"]

# ── 断言指令全集（CHECK_TAXONOMY + 易用/兼容）────────────────
ALL_CHECKS = ["element_exists", "text_contains", "url_matches", "no_error", "api_status",
              "style", "toast", "refresh", "position", "element_count", "download",
              "text_not_exists", "element_not_exists", "element_enabled", "element_checked",
              "select_value", "text_equals", "image_loaded", "loading_complete", "list_order",
              "element_visible", "element_attribute", "visual_check", "geometry_check",
              "console_error", "dialog", "text_regex", "storage", "network",
              "animation_complete", "pagination", "element_attribute_all", "time_range",
              "websocket", "page_load_time", "response_time",
              "api_response_body", "api_call_count", "api_header", "request_body", "api_contract",
              "db_record_count", "db_record_not_exists", "db_record_exists", "db_field_value",
              "db_query", "db_ui_consistency"]

REQ_TYPE_METHODS = {
    "form": ["EP", "BV", "CE", "EG"], "crud": ["FD", "EP", "ST", "EG"],
    "search": ["EP", "BV", "OT", "DT"], "workflow": ["ST", "FD", "PC", "BV"],
    "permission": ["DT", "FD", "EG", "AU"], "dashboard": ["FD", "EP", "PC"],
    "complex": ["FD", "ST", "CE", "DT", "OT", "PC", "AU"],
}
REQ_TYPE_CN = {"form": "表单", "crud": "增删改查", "search": "查询检索", "workflow": "流程编排",
               "permission": "权限控制", "dashboard": "看板展示", "complex": "复杂组合"}
SECURITY_RULES = ["SQL-001 SQL注入|SI", "XSS-001 跨站脚本|SX", "DATA-001 敏感信息泄露|AU",
                  "HEAD-001 安全头缺失|EG", "HEAD-004 CORS误配|CS", "CSRF-001/002 防伪|CS",
                  "PATH-001 路径遍历|EG", "CMDI-001 命令注入|EG", "IDOR-001/002 越权IDOR|AU",
                  "MTD-001 方法篡改|EG", "MASS-001 批量赋值|EG"]
A11Y_HINTS = ["对比度", "可聚焦", "语义标签", "aria-label", "键盘可达", "焦点顺序", "文案可读", "触达区域"]


class Case:
    def __init__(self, domain, sub, title, method, variant, pre, steps, expect,
                 actions, checks, target_pt, prio, risk, evidence, idx=0):
        self.domain = domain; self.sub = sub; self.title = title; self.method = method
        self.variant = variant; self.pre = pre; self.steps = steps; self.expect = expect
        self.actions = actions; self.checks = checks; self.target_pt = target_pt
        self.prio = prio; self.risk = risk; self.evidence = evidence; self.idx = idx

    def row(self):
        return [self.idx, self.domain, self.sub, self.title, self.method, self.variant,
                self.pre, self.steps, self.expect, self.actions, self.checks,
                self.target_pt, self.prio, self.risk, self.evidence]


CASES: list[Case] = []


def add(domain, sub, title, method, variant, pre, steps, expect, actions, checks,
        target_pt, prio, risk, evidence):
    CASES.append(Case(domain, sub, title, _m(method), variant, pre, steps, expect,
                      actions, checks, target_pt, prio, risk, evidence))


# ━━━━━━━━━━━━━ ① 操作指令轨 ━━━━━━━━━━━━━
def gen_operational():
    # 浏览器动作 × 20 变化（每种真实分支）
    op_variants = [
        ("正向基线", "目标元素已渲染且唯一", "动作下发正确、结果写 executions、判定 PASS",
         "方向正确+guard无拦截"),
        ("目标元素缺失", "目标元素不存在", "guard 拦截(missing_ai_id)→min_repro，不抛原生异常、不假PASS",
         "FAIL 定位到缺元素"),
        ("非法选择器/注入", "选择器含非法或注入字符", "引擎做选择器合法性校验，非法输入不落地为真实动作",
         "guard(element_action_mismatch 或拒绝执行)"),
        ("元素不可交互", "元素可见但 disabled/被遮挡", "前置 check 失败即停，微步内不 blindfire 点击",
         "跳过并记 FAIL/可复核原因"),
        ("等待超时", "元素在超时窗口内不出现", "等待超时→FAIL归因 element-not-found，记录 timeout",
         "PASS/FAIL 明确"),
        ("重复执行(幂等)", "元素已处于目标状态", "重复执行 no-op，不产生重复副作用/重复记录",
         "幂等：二次不重复"),
        ("并发快速触发", "同一目标被并发下发同动作", "原子化防抖/防重放生效，只落地一次",
         "避免重复副作用"),
        ("网络故障", "注入 network_fault 使网络异常", "执行失败路径按网络故障归因，重试策略生效",
         "不误归因业务缺陷"),
        ("兼容-移动端", "viewport 375×667 / 触屏", "动作按触屏语义适配，目标可命中",
         "移动端亦可执行"),
        ("跨会话恢复", "浏览器会话被重置后重放步骤", "会话状态重建后动作仍可定位到目标",
         "可重放不串会话"),
        ("权限受限环境", "当前身份对目标无权限", "目标不可及时依法拒绝/跳过，不误判",
         "权限边界正确"),
        ("大规模DOM", "页面 5000+ 节点/深层嵌套", "dom_sizer 裁剪后动作仍命中目标而不 OOM",
         "裁剪后仍可执行"),
        ("iframe 内目标", "目标位于 iframe 内", "switch_to_iframe 后动作命中 iframe 内元素、随后 switch_to_default",
         "跨文档定位正确"),
        ("动态延迟出现", "目标在交互后才异步渲染", "auto-wait 生效，元素出现后继续动作，不提前误判",
         "异步渲染已等待"),
        ("滚动联动命中", "目标需滚动才进入视口", "scroll 至目标后可交互，scroll 与 click 联动正确",
         "滚动后命中"),
        ("长按复合操作", "目标需长按触发", "long_press 时长/坐标正确、不误触",
         "长按语义正确"),
        ("键盘组合键", "动作需 Ctrl/Alt/Shift 组合", "key_down/组合->动作->key_up 序列正确",
         "组合键生效"),
        ("剪贴板/粘贴", "动作涉及文本粘贴", "粘贴内容正确、转义安全、不触发 XSS",
         "粘贴行为正确"),
        ("多值/大文本输入", "textarea/富文本输入大量文本", "type_text 大文本不丢字、滚动定位正确",
         "大文本完整"),
        ("焦点顺序/无障碍", "目标需按焦点顺序到达", "focus/blur/键盘轨迹符合无障碍顺序，不破坏可聚焦性",
         "焦点轨迹正确"),
    ]
    for act, cn in BROWSER_ACTIONS.items():
        tool = "browser_" + act
        for vn, vpre, vexp, vcheck in op_variants:
            add("操作指令轨", f"浏览器动作-{cn}", f"[{cn}] {vn}：执行 {tool}",
                "EP", vn, f"前端就绪；{vpre}。",
                f"Step4 执行器经 CANONICAL 白名单校验后通过 {tool} 对目标元素执行 {cn}。",
                f"{vexp}。", tool, f"element_exists_check/{('console_error_check' if vn!='正向基线' else 'text_contains_check')}",
                cn, "P0" if vn == "正向基线" else "P1", "低" if vn == "正向基线" else "中",
                f"_taxonomy.py::{act}")
    # API 方法 × 变化
    api_variants = [
        ("正向", "鉴权有效、端点可用", "返回 2xx、体符合 api_contract；verification.api_pass 落库", "P0", "低"),
        ("无鉴权", "缺 token", "401 拒绝且不被误标 PASS", "P1", "中"),
        ("非法/缺失字段", "载荷字段缺失或类型错", "4xx 参数校验失败判真", "P1", "中"),
        ("资源不存在", "目标 id 不存在", "404、业务边界判真", "P1", "中"),
        ("越权", "普通身份访问高权限资源", "403、权限边界判真", "P1", "高"),
        ("重复提交幂等", "同请求重复发送", "幂等语义守住、不重复创建脏数据", "P1", "中"),
        ("慢响应/超时", "端点响应超过超时阈值", "按超时归因非业务误判，timeout 记录", "P2", "中"),
        ("大规模载荷", "大量记录批量写入", "不超时/不回滚部分，结果与总数一致", "P2", "中"),
    ]
    for mth in API_METHODS:
        for vn, vpre, vexp, prio, risk in api_variants:
            add("操作指令轨", f"API-{mth}", f"[API {mth}] {vn}：{mth} 请求到目标端点",
                "FD", vn, vpre,
                f"Step4 API 轨对 {mth} 端点发起请求（{vpre and ('含对应前置回放' or '')}），经 api_prefix 路由。",
                f"{vexp}。", f"api_{mth.lower()}", "api_status_check/api_response_body_check", f"API-{mth}", prio, risk,
                "step4_executor.py api")


# ━━━━━━━━━━━━━ ② 断言指令轨 ━━━━━━━━━━━━━
def gen_assert():
    a_variants = [
        ("判真", "使断言成立", "断言 PASS、verification.{c} 落库、证据计入、不误判", "P0", "低"),
        ("判伪", "使断言不成立", "断言 FAIL、failure_reason 落到差异点、可根因归因、杜绝假PASS", "P1", "中"),
        ("目标缺失", "目标不存在", "按目标缺失判伪，不静默通过也不越界抛原生异常", "P1", "中"),
        ("并发/多断言", "同页多断言并发", "各自独立判真，结果不串扰不相互污染", "P2", "中"),
        ("大规模数据", "断言目标数据量巨大", "分页/限流下仍精确判真不 OOM 不误报", "P2", "中"),
        ("断言超时", "目标在超时窗口内未就绪", "按超时归因并记录，不误判为业务缺陷", "P2", "中"),
        ("断言幂等", "同一断言重复执行", "同状态下结果一致、不产生重复副作用", "P2", "低"),
        ("边界值", "数据处于上下界", "等于界值判 PASS、越界判 FAIL、临界判定正确", "P1", "中"),
        ("空值(null)", "目标值为空", "按语义判空，不误判为存在、显示 '—' 一致", "P1", "中"),
        ("大小写/编码", "文本大小写或编码差异", "按配置精确或忽略大小写判定、中文编码不错乱", "P2", "中"),
        ("特殊字符/XSS", "目标含特殊字符", "断言匹配转义正确、不触发 XSS、不误报", "P1", "高"),
        ("跨轨交叉(ui/db/api)", "三轨同时断言同一状态", "ui/api/db_ui_consistency 判定一致、冲突可暴露", "P1", "中"),
        ("断言器不可用", "依赖 handler/库未装载", "按不可评估降级(NO_DATA)诚实标记，不误判 PASS/FAIL", "P1", "中"),
        ("断言信息超长", "diff/actual 文本超大", "failure_reason/证据截断安全、不超 DB 列宽、可读不崩", "P2", "低"),
    ]
    for c in ALL_CHECKS:
        for vn, vpre, vexp, prio, risk in a_variants:
            add("断言指令轨", f"断言-{c}", f"[{c}] {vn}：执行 {c} 校验",
                "BV", vn, f"{vpre}（目标：{c}）。",
                f"Step4 引擎经 verification/_verify_*.py 对应 handler 执行 {c}，读取目标状态与预期比对。",
                f"{vexp}。", f"verify_{c}", f"{c}_check", c, prio, risk, "CHECK_TAXONOMY")


# ━━━━━━━━━━━━━ ③ 前端控件轨 ━━━━━━━━━━━━━
def gen_controls():
    controls = [
        ("登录页/用户名输入框", "browser_fill", "text_contains_check/no_error"), ("登录页/密码输入框", "browser_fill", "text_contains_check"),
        ("登录页/登录按钮", "browser_click", "url_check/toast"), ("Dashboard/KPI卡片", "browser_click", "text_contains_check"),
        ("Dashboard/刷新按钮", "browser_click", "loading_complete"), ("Dashboard/管道卡片", "browser_click", "element_exists"),
        ("Pipeline/一键执行", "browser_click", "no_error/loading_complete"), ("Pipeline/暂停按钮", "browser_click", "toast/dialog"),
        ("Pipeline/Step下拉", "browser_select", "select_value"), ("Pipeline/执行此步", "browser_click", "loading_complete"),
        ("Pipeline/导出details", "browser_click", "element_visible"), ("Pipeline/运行状态条", "browser_wait", "text_contains"),
        ("Review/需求列表", "browser_click", "element_count/list_order"), ("Review/状态筛选", "browser_select", "element_count"),
        ("Review/详情面板", "browser_click", "element_visible"), ("Review/描述textarea", "browser_fill", "element_attribute"),
        ("Review/通过/驳回/保存", "browser_click", "toast/api_status"), ("Review/自动放行Green", "browser_click", "api_status"),
        ("Intent-review/方法论筛选横幅", "browser_click", "text_contains"), ("Intent-review/刷新", "browser_click", "loading_complete"),
        ("Intent-review/澄清横幅", "browser_click", "element_visible"), ("Metrics/域卡片", "browser_click", "element_visible"),
        ("Metrics/KPI总览Tab", "browser_click", "element_enabled"), ("Metrics/原始数据Tab", "browser_click", "element_count"),
        ("Metrics/Token成本Tab", "browser_click", "text_contains"), ("Metrics/统一KPITab", "browser_click", "text_contains"),
        ("AI-Health/综合评分KPI", "browser_click", "text_contains"), ("AI-Health/守卫拦截KPI", "browser_click", "text_contains"),
        ("AI-Health/健康明细", "browser_click", "element_visible"), ("AI-Health/运行快照", "browser_click", "api_status"),
        ("侧边栏/手风琴展开互斥", "browser_click", "position/geometry_check"), ("Toast/操作提示", "browser_wait", "toast_appear"),
    ]
    c_variants = [
        ("正向交互", "控件正常可用", "动作正确、状态更新、无 console_error", "P0", "低"),
        ("控件缺失", "页面无该控件", "guard 拦截(missing_ai_id)、FAIL 定位缺失、不假PASS", "P1", "中"),
        ("防重放", "快速连点", "withBtnLoading 生效、不重复提交", "P1", "中"),
        ("无权限", "当前用户无对应权限", "控件隐藏/禁用，接口按权限拒绝", "P1", "高"),
        ("小分辨率", "viewport 375×667", "可渲染可点不溢出(viewport_overflow)", "P2", "中"),
        ("大列表分页", "列表 1000+ 行", "分页/虚拟滚动，计数/顺序断言精确", "P2", "低"),
        ("弱网", "网络延迟注入", "loading 提示可见、超时兜底不假PASS", "P2", "中"),
        ("后端未就绪", "指标后端不可达", "显示 '—' 未采集态而非误导性 0、无接口报错", "P1", "中"),
        ("长内容", "文本超长/多行", "面板/日志自适应不溢出、换行正常(不固定高压缩)", "P2", "低"),
        ("键盘可达", "按键到达/Enter 触发", "btn 可聚焦、Enter 生效、焦点轨迹符合无障碍", "P2", "中"),
        ("空态", "列表/面板无数据", "显示空态占位而非报错、不误渲染旧数据", "P2", "低"),
        ("多实例/多Tab", "同控件的多实体", "操作各自隔离、状态不串", "P2", "中"),
        ("加载骨架态", "异步数据就绪前渲染骨架", "骨架态不误判为内容、就绪后正常可交互", "P2", "低"),
        ("失败重试", "首次交互失败后重试", "错误 toast 可见、重试成功、状态一致不重复副作用", "P2", "中"),
    ]
    for ctl, act, chk in controls:
        pg = ctl.split("/")[0]
        for vn, vpre, vexp, prio, risk in c_variants:
            add("前端控件轨", ctl, f"[{ctl.split('/')[-1]}] {vn}：执行 {act} 并校验",
                "EP", vn, f"控制台打开 {pg}；{vpre}。",
                f"对「{ctl}」执行 {act}。",
                f"{vexp}。", act, chk, ctl, prio, risk, f"frontend/templates/{pg}")


# ━━━━━━━━━━━━━ ④ 数据链路轨 ━━━━━━━━━━━━━
def gen_datalink():
    metrics = [
        ("guard_reject", "guard_rejection", "守卫拦截计数", "ai_health_metrics", 4),
        ("action_attempt", "action_type", "守卫拦截分母(step级)", "ai_health_metrics", 4),
        ("guard_reject_rate", "guard_reject_rate", "拦截率", "ai_health_metrics", 4),
        ("verification", "ui_pass/api_pass/db_pass/disagreement", "三轨校验达成", "ai_health_metrics", 4),
        ("capability", "cap_total_calls/cap_hit/cap_miss", "智能能力命中", "ai_health_metrics", 3),
        ("dom_sizer", "limit_value", "DOM裁剪阈值", "ai_health_metrics", 3),
        ("tool_mode", "tool_mode_calls/ai_total_calls", "融合工具/AI比", "ai_health_metrics", 4),
        ("op_cache", "cache_hit/cache_miss/hit_rate", "操作缓存命中", "ai_health_metrics", 4),
        ("atomic_rollback", "atomic_rollback/total", "原子回滚", "ai_health_metrics", 4),
        ("browser_ai_round", "elapsed_sec/prompt_tokens", "AI回合成本", "ai_health_metrics", 4),
        ("ai_loop", "total_loops", "AI循环轮数", "ai_health_metrics", 4),
        ("harness_governance", "dangerous_intercepts/rejected", "危险动作拦截", "ai_health_metrics", 4),
        ("scorecard", "avg_score/dim_*", "P0治理评分", "ai_health_metrics", 4),
        ("token_budget", "truncations/budget_exceeded", "Token预算", "ai_health_metrics", 3),
        ("flaky_guard", "env_dns/timing_async/data_inconsistent", "flaky守卫", "ai_health_metrics", 4),
        ("cross_check", "api_mismatch/ui_element_missing/enum", "AI交叉校验", "ai_health_metrics", 3),
        ("coverage", "forward_coverage_rate/orphan_modules", "前向覆盖", "ai_health_metrics", 3),
        ("slice_coverage", "coverage_rate/uncovered", "切片覆盖", "ai_health_metrics", 3),
        ("control_type", "hit_rate/total_calls", "控制类型命中", "ai_health_metrics", 4),
        ("parallel_exec", "max_parallel/throughput/passed/failed", "并行执行", "ai_health_metrics", 4),
        ("hash_reuse", "reuse_rate", "哈希复用", "ai_health_metrics", 3),
        ("evidence", "evidence_coverage/calibrated_confidence", "证据覆盖/校准置信度", "case_meta", 3),
        ("case_dominance", "dominance_score/recipe_only_pass", "用例主导", "executions", 4),
        ("case_complexity", "step_count", "用例复杂度", "executions", 4),
        ("flaky", "flaky_count", "不稳定用例", "ai_health_metrics", 4),
    ]
    dl_variants = [
        ("写读闭环一致性", "当前 run/version 有效", "读侧=写侧、project/version/run 隔离、非空、无假0", "P0", "低"),
        ("口径失配暴露", "读侧 metric_name 与写侧不同", "查询为空/0 而非伪造、界面临界显示'—'、可定位根因", "P1", "中"),
        ("多run隔离", "存在多个历史 run", "仅读当前 active version，历史脏数据被过滤", "P1", "中"),
        ("并发写", "同一 metric 并发写入", "写入不丢/不覆盖、计数正确", "P2", "中"),
        ("读侧无数据", "该 run 未采集该 metric", "显示'—'(未采集)而非伪造 0%", "P1", "中"),
        ("多版本隔离", "存在多个 project_version", "读 active 版本、旧版本不串读", "P1", "中"),
        ("超大值/计数", "value 极大或达到上限", "累计/比值计算不溢出、显示正确", "P2", "低"),
        ("负值/异常值", "value 为负或非法", "防御校验、不污染聚合、dashboard 不崩", "P2", "中"),
        ("单位/精度", "百分比/小数精度差异", "guard_reject_rate 等计算精度正确、无除零", "P1", "中"),
        ("边界-分母为0", "action_attempt=0 而 guard 有值", "保护逻辑生效，避免除零/误导性 0%", "P1", "高"),
        ("血缘/来源标注", "指标来源(来源表/生成器)需可追溯", "每条指标可回溯到写入来源，不被误算进错指标", "P2", "中"),
        ("增量/累加趋势", "多次 run 累加变化", "相对基线/上轮对比趋势正确、可判定回归", "P2", "中"),
    ]
    for mt, mn, cn, tbl, step in metrics:
        for vn, vpre, vexp, prio, risk in dl_variants:
            add("数据链路轨", f"指标-{mt}", f"[{mt}] {cn}：{vn}",
                "FD", vn, f"{tbl} 表存在；{vpre}。",
                f"Step{step} metrics_collector 写 metric_type={mt}/name={mn}；fetcher 以同 metric_name 读回。",
                f"{vexp}。", "record_*", "db_query_check/db_field_value_check", f"{mt}.{mn}", prio, risk,
                f"health.py record_{mt}")
    # guard 5 触发条件
    guard_conds = [
        ("whitelist", "动作不在白名单", "engine 执行白名单外动作"), ("missing_ai_id", "AI未给元素id", "AI产出动作缺 ai_id"),
        ("ai_id_out_of_range", "元素id越界", "ai_id 超出元素探测范围"), ("element_action_mismatch", "元素-动作不匹配", "动作与元素类型不匹配"),
        ("missing_required_param", "缺必填参数", "动作缺必填参数"),
    ]
    for gc, gcn, gstep in guard_conds:
        add("数据链路轨", f"守卫-{gc}", f"guard_rejection[{gcn}]：拦截并正确落指标",
            "AU", "守卫拦截", "Step4 浏览器AI回路开启。",
            f"{gstep}，交 browser_ai_validator._validate 判定。",
            f"record_guard_rejection({gc}) 落 guard_reject；同时 step 级记 action_attempt 作分母，拦截率有效。",
            "browser_ai_round/ai_loop", "api_call_count_check", f"guard_{gc}", "P0", "低", "_validate.py")


# ━━━━━━━━━━━━━ ⑤ 功能链路轨 ━━━━━━━━━━━━━
def gen_pipeline():
    pl = [
        ("Step1", "需求拉取-Confluence", "fetch.py", "source_documents", "catalog"),
        ("Step1", "设计稿拉取-Figma", "figma.py", "figma_*", "catalog"),
        ("Step1", "接口拉取-YApi", "yapi.py", "source_yapi_hashes", "catalog"),
        ("Step1", "DB schema 拉取", "database.py", "source_hashes", "catalog"),
        ("Step1", "RAG 索引构建", "integration.py", "rag_chunks", "catalog"),
        ("Step1", "导航图构建", "_nav_map_builder.py", "nav_graph_*", "catalog"),
        ("Step2", "AI 需求拆解", "step2.py", "requirements", "catalog"),
        ("Step2", "追溯链路构建", "traceability_model.py", "trace_links", "catalog"),
        ("Step2", "版本哈希对比", "step2_hashes.py", "source_*_hashes", "catalog"),
        ("Step2", "需求覆盖矩阵", "step2_coverage.py", "coverage_*", "catalog"),
        ("Step2", "需求审核闸门", "review_gate.py", "review_lane", "catalog"),
        ("Step3", "AI 用例建模", "_modeling_engine.py", "test_cases", "catalog"),
        ("Step3", "方法论筛选(MethodRouter)", "method_router.py", "test_method", "catalog"),
        ("Step3", "18方法收敛(canonical)", "catalog.py", "test_method", "catalog"),
        ("Step3", "控制 DOM 解析", "_control_dom_utils.py", "payload_json", "catalog"),
        ("Step3", "用例审核闸门(放行Green)", "full_pipeline_runner.py", "review_lane", "catalog"),
        ("Step4", "Playwright/API 执行", "step4_executor.py", "executions", "catalog"),
        ("Step4", "意图读取器", "step4_case_reader.py", "test_cases", "catalog"),
        ("Step4", "Oracle 验证", "oracle/store.py", "oracle_results", "catalog"),
        ("Step4", "横切门禁跑批", "cross_cut_gates.py", "oracle_results", "catalog"),
        ("Step5", "Markdown 报告", "report_generator.py", "generated_reports", "catalog"),
        ("Step5", "HTML 可视化报告", "report_generator.py", "generated_reports", "catalog"),
        ("Step5", "覆盖率报告", "coverage_reporter.py", "coverage_summaries", "catalog"),
        ("Step5", "性能回归对比", "calibration/sampler.py", "visual_baselines", "catalog"),
        ("Step5", "安全区块5D", "report_generator.py", "generated_reports", "catalog"),
        ("Step5", "人工补测建议清单", "report_generator.py", "generated_reports", "catalog"),
    ]
    pl_variants = [
        ("正向", "前置源可用、配置正确", "产出表写入正确、checkpoint/版本正确、无 ERROR", "P0", "低"),
        ("外部源不可用", "Confluence/Figma/YApi/DB 无数据或连接失败", "不静默跳过、错误入 run_logs、可选源诚实降级、不伪造数据", "P1", "中"),
        ("幂等重跑", "同一 run 重跑", "idempotent 清理旧数据，不重复累积致聚合得分稀释", "P1", "中"),
        ("中断续跑", "中途杀进程后 resume", "checkpoint 续跑、paused_at_step 准、不重复已完步骤", "P1", "中"),
        ("大项目数据", "上千需求/用例", "不超时不炸内存，按批/并行推进且结果聚合正确", "P2", "中"),
        ("受限权限", "当前用户无某步权限", "步骤按权限拒绝并记录，不越权执行", "P1", "高"),
        ("无外部MCP(如figma)", "Figma 不可达被 skip", "skip_sources 生效，其余步骤照常、不卡死", "P2", "低"),
        ("空输入集", "该步无输入需求/用例", "按空集正确处理，不崩、产出空结果并提示", "P2", "中"),
        ("版本冲突", "并发/旧版本数据并存", "版本隔离正确、不把旧版数据算进当前", "P1", "中"),
        ("大文本/嵌入", "需求含超长正文或引用", "截断/分块不丢语义、RAG 检索不失效", "P2", "中"),
        ("重复数据", "源含重复需求/用例", "去重逻辑生效、不重复计数/落库", "P1", "中"),
        ("跨步数据一致性", "前步产物被后步消费", "步骤间数据契约一致、无断链少链", "P1", "中"),
    ]
    for step, fn, file, tbl, cat in pl:
        num = step[4:5]
        for vn, vpre, vexp, prio, risk in pl_variants:
            add("功能链路轨", f"{step}-{fn}", f"[{step} {fn}] {vn}：{file} 处理并落库",
                "FD", vn, vpre,
                f"经 scheduler/{file} 进入 {step}；执行 {fn}。",
                f"{vexp}。", "navigate/wait", "element_count_check/db_record_count_check", fn, prio, risk, file)


# ━━━━━━━━━━━━━ ⑥ 横切门禁轨 ━━━━━━━━━━━━━
def gen_gates():
    g_variants = [
        ("判真", "达标页面/API", "门禁 PASS、run 级 oracle 行(execution_id=0)经 LEFT JOIN 可读回、不误报", "P0", "低"),
        ("判伪", "缺陷页面/API", "门禁 FAIL、缺陷项+严重级别入库可复核、根因归因 SUT", "P1", "中"),
        ("源不可用", "依赖源(如VLM/基线)不可达", "诚实 NO_DATA 而非伪造 PASS/FAIL", "P1", "中"),
        ("旧数据残留", "存在上一版基线/历史 oracle", "读 active 版本过滤脏数据，判定用当前 run", "P1", "中"),
    ]
    gates = ["a11y(可访问性)", "security(安全)", "perf(性能)", "visual(视觉)", "authz(权限矩阵)", "usability(易用/兼容)"]
    for g in gates:
        gk = g.split("(")[0]
        for vn, vpre, vexp, prio, risk in g_variants:
            add("横切门禁轨", g, f"[{gk}] {vn}：门禁判定",
                "FD", vn, vpre,
                f"Step4 调 run_{gk}_gate；结果入 oracle_results(security/a11y 等)。",
                f"{vexp}。", f"run_{gk}_gate", "api_status_check/db_record_exists_check", gk, prio, risk, "cross_cut_gates.py")
    for rule in SECURITY_RULES:
        name, code = rule.split("|")
        add("横切门禁轨", "security-规则", f"[security] {name}：命中/不命中判定",
            code, "规则命中", "构造/避开该漏洞响应",
            f"SecurityScanner.scan_response 对响应做 {name} 判定。",
            f"命中记严重级并告警、干净响应 0 命中无伪报。", "run_security_gate", "api_header_check/api_contract_check",
            name, "P1", "高", "security_scanner/__init__.py")
    for h in A11Y_HINTS:
        add("横切门禁轨", "a11y-原则", f"[a11y] {h}：可解释规则判定",
            "ST", "规则判定", "页面含/缺对应可访问性特征",
            f"run_a11y_check({h}) 纯 DOM 判定。",
            f"缺失记 high defect、齐全 PASS。", "run_a11y_gate", "element_attribute_check", h, "P1", "中", "extensions/a11y/checker.py")


# ━━━━━━━━━━━━━ ⑦ 方法论轨 ━━━━━━━━━━━━━
def gen_methods():
    for rt, ms in REQ_TYPE_METHODS.items():
        for mo in ms:
            for vn, vpre, vexp in [
                ("方法应用", "收录一条该类型需求", "1 用例 test_method={mo}，白名单不阻断"),
                ("FD坍缩回归", "用例类型已明确但 explore 无关键词", "canonical(case_signal) 按语义落码，不坍缩 FD"),
                ("显式码优先", "用例已显式指定方法", "显式码保留、不被路由覆盖"),
                ("多需求同类型", "同 type 多条需求逐条建模", "每用例独立收敛到匹配码、不互相污染"),
                ("无匹配类型", "需求类型无既定方法映射", "按复合/安全规则合理兜底、不坍缩成无意义 FD"),
                ("安全模型映射", "安全类需求(SS/CS/AU等)", "canonical 归结到安全码而非 FD、白名单放行"),
            ]:
                add("方法论轨", f"需求-{REQ_TYPE_CN[rt]}", f"[{mo}] {REQ_TYPE_CN[rt]}类需求：{vn}",
                    mo, vn, vpre,
                    f"Step3 经 MethodRouter→canonical_method_code 收敛 test_method。",
                    f"{vexp}。", "navigate/fill", "text_equals_check", f"{mo}@{rt}", "P1", "低", "catalog.py/method_router.py")


# ━━━━━━━━━━━━━ ⑧ 审核闸门轨 ━━━━━━━━━━━━━
def _flows():
    return [
        ("Step2需求闸门", "approve 批量确认", "review.py", "全量 approved/modified→放行续跑"),
        ("Step2需求闸门", "reject 驳回", "review.py", "标记 rejected→paused_at_step=2 待人工"),
        ("Step3用例闸门", "自动放行 green", "full_pipeline_runner.py", "green approved 继续，yellow/red 保留待审"),
        ("Step3用例闸门", "pause 待审", "case_lanes", "有未审→paused_at_step 正确不停错"),
        ("审批续跑", "approve 后 resume", "_run_step", "清 paused、从暂停后一步续跑不重复"),
    ]


# ━━━━━━ ⑨ 认证与安全会话轨（SS/CS/AU 具体化）━━━━━━
def gen_auth():
    items = [
        ("登录-用户名密码正确", "ss", "SS", "登录成功跳转 Dashboard、session 建立、token 落库", "P0", "低"),
        ("登录-密码错误", "eg", "EG", "登录失败、错误提示、不建立会话", "P1", "中"),
        ("登录-用户名不存在", "eg", "EG", "拒绝并通用提示、不泄露账号存在性", "P1", "中"),
        ("登录-空字段", "bv", "BV", "前端拦截+后端校验，400", "P1", "低"),
        ("登录-连续失败锁定", "bv", "BV", "达到阈值锁定/延迟，防爆破", "P1", "高"),
        ("登出-会话销毁", "ss", "SS", "登出后 token/cookie 失效、不可复用", "P0", "高"),
        ("会话超时-过期", "ss", "SS", "超时后请求 401、前端回登录", "P1", "中"),
        ("多会话-互踢", "ss", "SS", "新会话策略生效、旧会话按规则失效", "P2", "中"),
        ("token-刷新", "ss", "SS", "refresh token 有效轮换、旧 token 失效", "P1", "高"),
        ("XSS-登录页可控输入", "sx", "SX", "注入 payload 不执行、输出转义", "P1", "高"),
        ("CSRF-状态变更接口", "cs", "CS", "无防伪令牌的跨站请求被拒", "P1", "高"),
        ("越权-跨资源访问", "au", "AU", "非本人资源访问 403/404", "P0", "高"),
        ("中间人-传输安全", "eg", "EG", "会话仅 HSTS/HTTPS、无明文口令泄漏", "P2", "高"),
        ("SESSION固定-预防", "ss", "SS", "登入后轮换会话id、防固定", "P2", "高"),
    ]
    for title, key, mc, exp, prio, risk in items:
        add("认证与会话轨", key, f"[{mc}] {title}",
            mc, "认证安全", "引擎控制台登录态可用、后端就绪。",
            f"对登录/会话相关接口或页面执行 {title} 操作。",
            f"{exp}；guard/安全门禁如实判定、不假PASS。",
            "browser_fill/api_post", "api_status_check/element_exists_check", title, prio, risk, "backend/routes/auth")


# ━━━━━━ ⑩ 追溯与覆盖轨（数据面补全）━━━━━━
def gen_trace():
    items = [
        ("需求-用例映射", "UP", "case_requirement_mapping 建立/移除/继承正确", "P0", "低"),
        ("追溯链路-源到用例", "trace", "requirements→test_cases→executions 链路可追溯、无断链", "P0", "低"),
        ("前向覆盖-计算", "fab", "forward_coverage 公式精确、无重复计数", "P1", "中"),
        ("维度级覆盖-精确公式", "dim", "coverage=(covered)/(covered+missing) 精确", "P1", "中"),
        ("孤儿模块-检出", "orphan", "未覆盖模块被列为 orphan_modules 并提示", "P2", "中"),
        ("反向覆盖-缺陷跳变", "back", "deployment_matcher 反查未覆盖变更点", "P2", "中"),
        ("源哈希-变更检测", "hash", "源变更→哈希变化→触发增量重生成", "P1", "低"),
        ("竞态-覆盖更新", "race", "并发写 coverage 不丢不串", "P2", "中"),
    ]
    for title, key, exp, prio, risk in items:
        add("追溯与覆盖轨", key, f"{title}",
            "FD", "数据链路", "存在带需求的真实运行数据。",
            f"运行覆盖/追溯相关 pipeline 子功能并读取 DB。",
            f"{exp}。", "run_full_pipeline", "db_record_count_check/db_query_check", title, prio, risk, "pipeline/utils/coverage_store")


# ━━━━━━ ⑪ 指标阈值与告警轨 ━━━━━━
def gen_thresholds():
    items = [
        ("阈值-命中报警", "threshold", "指标越阈触发告警而非静默", "P1", "中"),
        ("预算-超预算禁写", "budget", "token 超预算记录 truncations/budget_exceeded", "P1", "中"),
        ("告警-降级路径", "downgrade", "依赖源不可达时诚实降级并有日志", "P1", "中"),
        ("版本-隔离读阈值", "version", "仅读 active version 阈值配置", "P2", "中"),
    ]
    for title, key, exp, prio, risk in items:
        add("指标阈值轨", key, title, "FD", "边界", "metric_thresholds/预算配置已置。",
            "触发边界条件并观察指标采集与告警。",
            f"{exp}。", "record_token_budget", "db_field_value_check", title, prio, risk, "extensions/metrics_collector")


# ━━━━━━ ⑫ 兼容/性能/探索/规范 方法族补全（补齐 18 码）━━━━━━
def gen_method_families():
    fam = [
        ("CT", "兼容与响应式", [
            ("小分辨率渲染", "viewport 375×667，全页面端正", "P2", "中"),
            ("多浏览器机型", "Chrome/Firefox/WebKit 渲染一致", "P2", "中"),
            ("响应式断点", "断点切换布局正确无溢出", "P2", "中"),
            ("字体/缩放", "125% 缩放不破坏布局", "P2", "低"),
        ]),
        ("PF", "性能", [
            ("首屏加载", "page_load_time ≤ 阈值，lcp 达标", "P1", "中"),
            ("接口响应", "response_time 达标、无长尾", "P1", "中"),
            ("并发负载", "parallel_exec 高并发下通过/失败统计正确", "P1", "中"),
            ("性能回归对比", "相对基线回归被真实判定(校准模式回写)", "P0", "中"),
        ]),
        ("ET", "探索式", [
            ("异常输入模糊", "任意输入不崩溃、错误兜底", "P2", "中"),
            ("随机操作路径", "非预期操作顺序不破坏状态", "P2", "中"),
            ("flaky 守卫", "环境噪声被 flaky_guard 归因而非假FAIL", "P1", "中"),
        ]),
        ("SS", "会话管理补充", [
            ("会话保持", "请求间会话/thread-local 不串(多用户隔离)", "P0", "高"),
            ("登录后版本对齐", "读/写 version 一致不漂移", "P1", "中"),
        ]),
        ("SD", "规范导出", [
            ("需求规范验收", "由 spec 导出的规范 claim 与需求一一对应", "P1", "低"),
            ("溯源性-导出", "导出的用例可回溯到源需求", "P1", "低"),
        ]),
    ]
    for mc, cn, cases in fam:
        for title, pre, prio, risk in cases:
            add("方法论补全轨", f"{mc}-{cn}", f"[{mc}] {title}", mc, cn,
                pre, f"执行 {cn} 相关功能并校验。",
                "结果真实可复核、判定明确。", "navigate/wait/verify", "page_load_time_check/response_time_check",
                title, prio, risk, "catalog.py")


# ━━━━━━ ⑬ 控制类型模板轨（ACTION_TEMPLATES，覆盖所有生成模板）━━━━━━
CONTROL_TEMPLATES = ["input", "table_row", "select", "upload", "checkbox", "date", "combo_key",
                     "overlay", "tooltip", "form", "tree", "steps", "interactive", "tab_traverse",
                     "rich_text", "text_select", "slider", "iframe", "autocomplete", "color", "rating",
                     "scroll_hover"]
TEMPLATE_CN = {
    "input": "单行输入", "table_row": "表格行", "select": "下拉选择", "upload": "文件上传",
    "checkbox": "复选", "date": "日期选择", "combo_key": "组合键", "overlay": "浮层", "tooltip": "提示气泡",
    "form": "表单", "tree": "树形", "steps": "步骤条", "interactive": "交互组件", "tab_traverse": "Tab遍历",
    "rich_text": "富文本", "text_select": "文本选择", "slider": "滑块", "iframe": "内嵌框架",
    "autocomplete": "自动补全", "color": "取色器", "rating": "评分", "scroll_hover": "滚动悬停",
}


def gen_templates():
    t_variants = [
        ("正向", "模板占位符上下文齐全", "占位符解析正确、模板校验步骤按方法论规则追加、不跑飞", "P0", "低"),
        ("上下文缺失", "缺少 required 上下文(字段/控制类型)", "模板保守兜底或跳过、不硬造校验步骤", "P1", "中"),
        ("动作契约合规", "模板展开动作字段完整", "展开动作均落 CANONICAL_ACTIONS 白名单(除 focus_check 特例)", "P1", "中"),
        ("方法论叠加", "同 case 需叠加规则模板", "merge_verifications 去重、AI 结果优先不重复追加", "P1", "中"),
        ("类型作序", "模板 action 顺序符合用户操作流", "顺序(填→选→勾→提交)合理、无跨步骤冲突", "P2", "中"),
        ("多模板复用", "同一控制类型多模板叠加", "不冲突、不重复生成、去重正确", "P2", "中"),
        ("非法占位符", "模板含未知占位符", "解析保守拒绝/跳过并提示，不落地为真实动作", "P2", "中"),
        ("空模板", "模板无可用动作或为空", "生成空动作集按空正确处理、核验步骤不硬造", "P2", "低"),
        ("模板升级/版本", "模板规则升级后旧用例重生成", "白名单仍放行、不因升级误毁既有用例", "P2", "中"),
        ("模板嵌套扩展", "动作模板内嵌校验模板", "嵌套解析正确、校验与动作顺序贴合、不冲突", "P2", "低"),
    ]
    for tpl in CONTROL_TEMPLATES:
        for vn, vpre, vexp, prio, risk in t_variants:
            add("控制类型模板轨", f"模板-{tpl}", f"[{tpl} {TEMPLATE_CN.get(tpl,'')}] {vn}",
                "EP", vn, f"目标控件为 {tpl}；{vpre}。",
                f"生成 layer 应用 {tpl} 动作模板(apply_action_templates)。",
                f"{vexp}。", "apply_action_templates", "element_attribute_check",
                tpl, prio, risk, "_action_templates.py")


# ━━━━━━━━━━━━━ 主流程 ━━━━━━━━━━━━━
def build():
    gen_operational(); gen_assert(); gen_controls(); gen_datalink()
    gen_pipeline(); gen_gates(); gen_methods(); gen_auth()
    gen_trace(); gen_thresholds(); gen_method_families(); gen_templates()
    rv_variants = [
        ("正向", "流水线到暂停点", "", "P0", "中"),
        ("非法请求", "未授权/畸形参数", "API 拒绝、闸门状态不被破坏", "P1", "中"),
        ("并发审批", "多人同时 approve", "幂等、状态一致不竞态", "P2", "中"),
        ("重复审批", "对已审批再审", "no-op、不产生重复记录", "P2", "低"),
    ]
    for dom, fn, file, exp in _flows():
        for vn, vpre, v_exp, prio, risk in rv_variants:
            add("审核闸门轨", dom, f"[{fn}] {vn}",
                "EP", vn, vpre,
                f"经控制台/{file} 触发 {fn}。",
                f"{exp}；{v_exp}。", "browser_click/api_post", "api_status_check/db_record_count_check",
                fn, prio, risk, file)
    for i, c in enumerate(CASES, start=1):
        c.idx = i


def _p0():
    return sum(1 for c in CASES if c.prio == "P0")


def write_xlsx(cases):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    headers = ["用例ID", "功能域", "子模块", "用例标题", "测试方法", "变化点", "前置条件",
               "操作步骤", "预期结果", "关键操作指令", "关键校验断言指令", "目标功能点",
               "优先级", "风险等级", "证据出处"]
    ws = wb.active; ws.title = "全量用例"; ws.append(headers)
    hfill = PatternFill("solid", fgColor="1F4E79")
    for col in range(1, len(headers) + 1):
        cell = ws.cell(row=1, column=col)
        cell.font = Font(bold=True, color="FFFFFF"); cell.fill = hfill
        cell.alignment = Alignment(horizontal="center", vertical="center")
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(headers))}{len(cases)+1}"
    for c in cases:
        ws.append(c.row())
    for i, w in enumerate([9, 12, 18, 34, 8, 12, 24, 40, 46, 20, 22, 16, 8, 8, 22], start=1):
        ws.column_dimensions[get_column_letter(i)].width = w

    ws2 = wb.create_sheet("覆盖矩阵")
    ws2.append(["计数维度", "数量"])
    ws2.append(["用例总数", len(cases)])
    ws2.append(["P0 用例数", _p0()])
    ws2.append(["功能域数", len({c.domain for c in CASES})])
    ws2.append(["测试方法码数", len({c.method for c in CASES})])
    ws2.append([""])
    ws2.append(["功能域", "用例数", "方法数", "变化点数", "P0数"])
    per = {}
    for c in cases:
        per.setdefault(c.domain, 0)
        per[c.domain] += 1
    dom_methods = {}; dom_var = {}; dom_p0 = {}
    for c in cases:
        dom_methods.setdefault(c.domain, set()).add(c.method)
        dom_var.setdefault(c.domain, set()).add(c.variant)
        if c.prio == "P0":
            dom_p0[c.domain] = dom_p0.get(c.domain, 0) + 1
    for d, _ in sorted(per.items(), key=lambda kv: -kv[1]):
        ws2.append([d, per[d], len(dom_methods[d]), len(dom_var[d]), dom_p0.get(d, 0)])
    ws2.append([""])
    ws2.append(["方法论码覆盖", "次数"])
    mc = Counter(c.method for c in cases)
    for mcode in ALL_CODES:
        ws2.append([f"{mcode}({METHOD_CN.get(mcode,'')})", mc.get(mcode, 0)])
    for col in range(1, 3):
        ws2.column_dimensions[get_column_letter(col)].width = 30

    ws3 = wb.create_sheet("生成说明")
    notes = [
        ["说明项", "内容"],
        ["对象与范围", "把「智能测试引擎」本身作为被测系统：操作指令轨/断言指令轨/前端控件轨/数据链路(指标DB)轨/功能链路(Step1~5)轨/横切门禁轨/方法论轨/审核闸门轨"],
        ["生成方式", "规则化组装：数据为引擎唯一权威枚举(_taxonomy.py/catalog.py/cross_cut_gates.py/scheduler.py/fetchers/schema.sql)，规则驱动+变化点组合，非AI编造"],
        ["测试方法", "18 码：EP/BV/CE/DT/ST/OT/EG/FD/SD/PC + SX/SI/CS/AU + SS/CT/PF/ET；本集覆盖其中实际应用者"],
        ["怎么执行", "「关键操作指令」列给出 browser_*/api_* 真实指令，Step4 执行器据此驱动浏览器或调 API"],
        ["怎么校验", "「关键校验断言指令」列给出 verification check：element_exists/text_contains/api_*/db_* 等，判定 PASS/FAIL 并可落库留证据"],
        ["变化点", "覆盖：正向/目标缺失/非法/边界/超长/XSS/SQL注入/幂等/并发/超时/越权/跨会话/回滚/大规模/兼容/网络故障/权限/多版本隔离/后端未就绪 等"],
        ["覆盖矩阵", "Sheet2 汇总各功能域×方法×变化点，可据以查缺"],
        ["规模", "数千条规则化用例，以实际生成为准"],
    ]
    for r in notes:
        ws3.append(r)
    ws3.column_dimensions["A"].width = 14
    ws3.column_dimensions["B"].width = 92

    wb.save(XLSX_PATH)
    return XLSX_PATH


if __name__ == "__main__":
    build()
    path = write_xlsx(CASES)
    print(f"[DONE] 用例数={len(CASES)}  P0={_p0()}")
    print(f"[METHOD] 覆盖码数={len({c.method for c in CASES})} 码={sorted({c.method for c in CASES})}")
    from collections import Counter as _C
    d = _C(c.domain for c in CASES)
    for k, v in d.most_common():
        print(f"   {k}: {v}")
    print(f"[FILE] {path}")