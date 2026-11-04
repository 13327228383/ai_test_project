# -*- coding: utf-8 -*-
from __future__ import annotations
from src.core.guarded import guard

"""用例生成规则基类。

每个规则文件继承 BaseCaseRule，实现 match() 和 generate()。
规则通过 RULE_META 声明元数据，后端 API 可查询所有规则清单。
"""

import logging
import re
from typing import Any

from src.pipeline.step3.case_rules._intent_checks import IntentChecksMixin
# ★ RC-AE(2026-09-25): 反馈条选择器统一从全引擎单一事实源导入（Step3/Step4/Agent 同源）。
from src.pipeline.utils.feedback_selectors import (
    ERROR_SELECTOR_CSV as _ERROR_SELECTOR_CSV,
    VALIDATION_SELECTOR_CSV as _VALIDATION_SELECTOR_CSV,
    SUCCESS_SELECTOR_CSV as _SUCCESS_SELECTOR_CSV,
)

_log = logging.getLogger(__name__)


# ── ★ 通用：存在性字面量识别（唯一实现，勿复制）──
# `element_exists`/`no_error` 类 check 的 `expect` 是**存在性字面量**（"存在"/"无错误"/
#   "页面存在"），它描述的是"某断言是否成立"，**不是**用例的预期结果语义。
#   任何把 check.expect 当"用例预期"用的地方（expected_result 反推、展示、导出、评分）
#   都必须先剔除这类字面量——否则产出"预期结果 = 存在"这种无意义残句
#   （实测 v9 全库 61 条）。
_PRESENCE_ONLY_FRAGMENTS = (
    "存在", "不存在", "无错误", "页面存在", "元素存在", "无", "有",
)


def _is_presence_only_fragment(text: str) -> bool:
    """文本是否为**纯存在性字面量**（无语义预期）。"""
    _t = str(text or "").strip().strip("。.！!，,")
    if not _t:
        return True
    return _t in _PRESENCE_ONLY_FRAGMENTS


# ── ★ 通用：需求否定感知（negation-aware) ──
# 需求可能明确裁定"取消/不提供某功能"（如"导出功能…最终裁定为取消，系统不应提供"）。
# 生成器若仍断言"X 控件可用"，会把"系统正确不提供"误判为失败。此处统一识别否定意图，
# 生成断言时翻转存在性。通用性：跨系统/行业，与业务知识无关。
_NEGATION_MARKERS = (
    "不应提供", "不提供", "不应存在", "不存在", "不能提供", "不得提供", "禁止提供",
    "已取消", "取消提供", "取消该功能", "功能已取消", "不再提供", "不实现",
    # ★ 根因修复(2026-09-28): 移除裸词"取消"——"用户取消删除/取消操作"是**操作动词**，
    #   不代表功能被撤销。仅保留"功能取消"复合语（取消提供/取消该功能/功能已取消/
    #   已取消），杜绝把"取消则保留商品"误判为删除功能不提供（实测 TC-REQ-004：生成
    #   否定断言"页面不应提供删除功能"，与 SUT 实际不符 → 假 FAIL）。
    "移除该功能", "下线",
    "should not provide", "not provide", "not implemented", "removed", "cancelled",
    "shall not",
)


# ── ★ 通用：行为型安全需求感知 ──
# 行为型需求断言的是"访问控制行为"（登录墙/重定向/403/仅授权角色可见/会话控制），
# 而非"页面显示某模块"的 UI 存在性。若对这类需求仍生成页面显示断言，
# Step4 会把不可观察的 UI 断言判为 FAIL/需求覆盖缺口（假缺陷）。
# 通用性：访问控制语义跨系统/跨行业，零业务硬编码。
_SECURITY_BEHAVIOR_KEYWORDS = (
    "未登录", "登录失败", "登录页", "登录墙", "强制登录", "会话超时", "会话过期",
    "仅管理员", "仅限管理员", "管理员可", "其他角色", "角色访问", "越权",
    "无权限", "没有权限", "权限校验", "权限控制", "鉴权", "未授权", "授权访问",
    "未认证", "认证用户", "匿名访问",
    "拒绝访问", "禁止访问", "访问被拒", "访问被拒绝", "重定向", "跳转到登录",
    "403", "401", "敏感信息", "注销",
    "unauthorized", "forbidden", "redirect", "access denied", "privilege",
)


def _is_security_behavior(*texts: str) -> bool:
    """需求语义是否为行为型访问控制（security_behavior 场景）。"""
    _t = " ".join(t for t in texts if t).lower()
    return any(_kw in _t for _kw in _SECURITY_BEHAVIOR_KEYWORDS)


# ── ★ 通用：访问控制**限制语义**精确判定 ──
#   与 `_is_security_behavior` 的分工（关键，防误判）:
#     `_is_security_behavior` = **宽召回**：只要需求处于"权限语境"就命中
#       （"管理员可以…"这类**正向**陈述也命中），用于场景标注/探索计划标注。
#     `is_access_restriction_req` = **精确判定**：必须"权限语境词"与"限制/拒绝词"
#       **共现**，才认定"低权执行受限操作应被拒绝"这一**行为型期望**成立。
#   判定实现已归位上游单一事实源 `src/core/requirement_semantics.py`（需求落库时
#   一次性判定为结构化字段 `access_control`）；此处仅保留同名别名供生成端调用，
#   禁止在本文件重新定义词表。
from src.core.requirement_semantics import (
    is_access_restriction_req as _is_access_restriction_req,
)


# ★ 2026-09-23(D23) 章节编号识别/剥离 —— 单一事实源，判定与剥离共用同一规则，
#   杜绝"判定吃多级、剥离只吃一级"这种两处不一致的漂移。
#   ① 多级点号编号：2.1 / 12.4.3（本身即章节号，不要求后随分隔符）
#   ② 单级编号：2. / 三、（必须后随显式分隔符，避免误伤 "2024 年度报告"）
_SECTION_PREFIX_RE = re.compile(
    r"^(?:\d+(?:\.\d+)+|(?:\d+|[一二三四五六七八九十]+)\s*[\.、．:：])"
)
_SECTION_STRIP_RE = re.compile(
    r"^(?:\d+(?:\.\d+)+|(?:\d+|[一二三四五六七八九十]+)\s*[\.、．:：])\s*"
)


def _is_doc_section_heading(module: str) -> bool:
    """模块名是否为需求文档章节标题（非 UI 菜单项）。

    章节标题特征（跨文档格式通用）：
      - 前导编号："2. 用户与权限"、"三、审计管理"
      - 尾部括号类别注释："用户与权限（安全需求）"、"登录（非功能）"
    这类文本是文档结构标记，不是可导航的 UI 模块；注入导航目标/显示断言必失败。
    """
    _m = (module or "").strip()
    if not _m:
        return False
    # ★  根因修复(D23)：编号必须吃完整多级形式。
    #   旧正则 `^(\d+|[...]+)\s*[\.、．:：]` 只吃一级，且贪婪回溯后会退化成
    #   "只吃第一个点号" —— 对两级编号 "2.1 商品管理" 剥完剩 "1 商品管理"
    #   （实测引擎日志逐字复现 module=1 商品管理 / module=5 其他零碎的），
    #   模块名残废 → 侧边栏匹配必失败 → 误判"需求未实现"。
    #
    #   新规则（两条互斥，避免误伤 "2024 年度报告" 这类裸数字开头的业务名）：
    #     ① 多级点号编号（2.1 / 12.4.3）—— 本身就是章节号，无需后随分隔符；
    #     ② 单级编号（2 / 三）—— 必须后随显式分隔符（.、．:：）才算章节号。
    #   通用性：任何两级及以上的编号体系（3.2 订单管理）均适用，零业务硬编码。
    if re.match(_SECTION_PREFIX_RE, _m):
        return True
    if re.search(r"[（(][^（）()]*(?:需求|非功能|安全|权限|说明|概述|场景)[^（）()]*[)）]\s*$", _m):
        return True
    # ★ 文档工件特征（2026-09-22 补充）：整份文档标题被 Step2 当作模块名
    #   （如"资产管理系统（fullcov sut）需求规格说明书 v1.0"）。含文档类关键词
    #   或以版本号结尾的"模块名"不可能是 UI 菜单项。
    if any(_kw in _m for _kw in ("规格说明书", "需求规格", "需求说明", "需求文档", "说明书")):
        return True
    if re.search(r"[vV]\d+(\.\d+)*\s*$", _m):
        return True
    return False


def _sanitize_doc_heading_module(module: str) -> str:
    """★ 章节标题 → 真实模块候选。

    Step2 常把需求文档章节标题当模块名（"2. 用户与权限（安全需求）"、"7. 删除资产
    （删除需求）"）。直接清空会让用例丢失全部导航线索；直接透传则导航必失败。
    正确做法：剥离前导编号与尾部类别注释，保留业务实体词（"用户与权限"/"删除资产"），
    交由 Step4 ModuleNav 的规则匹配→operation实体二次匹配→AI语义菜单映射三级链路定位。
    文档标题（规格说明书/带版本号）无业务实体可提取 → 返回 ''。
    """
    _m = (module or "").strip()
    if not _m:
        return ""
    if not _is_doc_section_heading(_m):
        return _m
    # ★ 2026-09-23(D23)：与 _is_doc_section_heading 共用同一编号规则（单一事实源），
    #   吃完整多级编号（2.1/3.2/2.5），否则会剥出 "1 商品管理"/"5 其他零碎的" 残废名。
    _m = _SECTION_STRIP_RE.sub("", _m)
    _m = re.sub(r"\s*[（(][^（）()]*[)）]\s*$", "", _m).strip()
    if not _m:
        return ""
    if any(_kw in _m for _kw in ("规格说明书", "需求规格", "需求说明", "需求文档", "说明书")):
        return ""
    if re.search(r"[vV]\d+(\.\d+)*\s*$", _m):
        return ""
    return _m


def _is_feature_negated(text: str) -> bool:
    """需求文本是否明确否定某功能的存在。"""
    if not text:
        return False
    _t = text.lower()
    return any(_mk in _t for _mk in _NEGATION_MARKERS)


def _apply_negation(expect: str, negated: bool, feature_kw: str = "") -> str:
    """存在性断言按否定翻转：期望"X 存在"→"X 应不存在"。

    仅生成层翻转文案，执行层（Step4 absence 验证）据"不应存在/不提供/不存在"
    语义在元素缺席时判定 PASS，避免把系统正确不提供的功能误判为失败。
    """
    if not negated:
        return expect
    _kw = feature_kw or expect
    return f"{_kw}：需求明确不提供，页面不应提供/不应存在该功能"


def _extract_module_from_operation(operation: str) -> str:
    """★ 通用修复(2026-07-28): 从 operation 文本中提取模块名。

    当 requirement 的 module 字段为低质量占位符（如"表格块"）时，
    从 operation 文本中提取真正的模块名（如"烟叶画像数据"、"辅材数据"）。
    """
    if not operation:
        return ""
    patterns = [
        r"所属模块[：:]\s*([^\s，,。.]{2,20})",
        r"所属功能[：:]\s*([^\s，,。.]{2,20})",
        r"模块[：:]\s*([^\s，,。.]{2,20})",
    ]
    for pat in patterns:
        m = re.search(pat, operation)
        if m:
            _mod = m.group(1).strip()
            _log.info(
                "[_extract_module] 从operation提取模块名: %r → %r", operation[:60], _mod
            )
            return _mod
    return ""


# ── ★ 通用操作类型分类（独立函数，供 _build_default_test_method 和 BaseCaseRule 共用）──
_OP_TYPE_KEYWORDS_MAP = {
    # ★ security_behavior 必须最优先：行为型访问控制语义（仅管理员/未登录/越权…）
    #   支配于普通操作类型——"仅管理员可查看"含"查看"不应被误分为 view，
    #   "分配权限"含"权限"但不含精确行为关键词，不受影响。
    #   ★ 单一事实源(2026-09-22): 关键词直接引用 _SECURITY_BEHAVIOR_KEYWORDS，
    #   分类时经 _is_security_behavior 用需求全语义判定（见 _classify_op_type）。
    "security_behavior": list(_SECURITY_BEHAVIOR_KEYWORDS),
    "form":      ["新建", "新增", "添加", "创建", "编辑", "修改", "更新", "输入", "填写",
                  "create", "add", "edit", "update", "modify", "保存", "提交", "save", "submit"],
    "search":    ["搜索", "查询", "查找", "检索", "search", "query", "find", "lookup"],
    "view":      ["查看", "详情", "展示", "浏览", "预览", "view", "detail", "display", "show", "preview", "read"],
    "import_op": ["导入", "上传", "import", "upload"],
    "export_op": ["导出", "下载", "export", "download"],
    "filter_op": ["筛选", "过滤", "filter"],
    "delete_op": ["删除", "移除", "清除", "delete", "remove", "clear"],
}


def _classify_op_type(operation: str, *extra_texts: str) -> str | None:
    """纯函数：从操作描述+需求全语义分类操作类型。返回 None 表示无法分类。

    ★ 全语义根修(2026-09-22 全量判定审计): 只扫 operation 会丢掉藏在
    description/expected_feedback 里的访问控制语义（如 REQ-004 "查看者角色仅
    具备只读权限…返回403" 的 operation 是"提交资产写操作"→ 被误分为 form，
    Step4 按 UI 表单流程执行 → 假 FAIL）。行为型安全语义以"需求整体"为单位
    判定，operation 单字段无权代表需求语义。
    兼容性: 保留原单参签名，额外文本经 *extra_texts 传入，旧调用方不受影响。
    """
    _op_lower = (operation or "").lower()
    # 行为型访问控制语义最优先：对需求全文本（操作+描述+期望）判定，
    # 防"仅管理员可查看"被 view 抢分类、"提交资产写操作"掩盖 403 语义。
    # ★ 分派必须用**精确判定**（权限语境词+限制词共现）——
    #   宽召回 `_is_security_behavior` 只可标注不可分派：正向需求
    #   "新增商品，保存后重定向回列表"含裸词"重定向"，若用宽召回分派
    #   会被整体误判为访问控制 → 正常保存判 FAIL（实测 SmartShop REQ-001）。
    if _is_access_restriction_req(operation, *extra_texts):
        return "security_behavior"
    # ★ 根因修复(2026-09-28): 删除+确认框语义优先于 form。根因（实测
    #   TC-REQ-005/018-I01）: op="执行删除前弹窗提交框"/"提交删除商品"含"提交"
    #   → form 词表先命中 → op_type=form → step-2 期望"浏览器弹出确认框"被
    #   当文本验证，SUT confirm 自动 accept 后无此文案 → 假 FAIL。删除确认的
    #   可观察证据是"确认后行数减少"，须走 delete_op 的 delete_done DOM 判定。
    #   判据: 删除动词 + 确认/弹窗语义共现（跨系统通用，零业务硬编码）。
    _blob_del = " ".join([_op_lower] + [str(t) for t in extra_texts if t]).lower()
    if (
        any(_k in _blob_del for _k in ("删除", "移除", "delete", "remove"))
        and any(_k in _blob_del for _k in ("确认", "弹窗", "对话框", "confirm", "二次确认"))
    ):
        return "delete_op"
    # ★ 校验/验证类语义（"验证名称唯一性"/"验证必填字段"）：operation 以
    #   "验证/校验"开头 + 期望含拒绝/提示语义 → form 校验拒绝路径。原逻辑
    #   返回 None → Step4 引导不补表单动作链 → Agent 空手断言"被拦截"必 FAIL
    #   （实测 SmartShop TC-REQ-005/006）。与 form:error_path 同构。
    _blob_all = " ".join([_op_lower] + [str(t) for t in extra_texts if t]).lower()
    if ("验证" in _blob_all or "校验" in _blob_all) and any(
        _kw in _blob_all for _kw in ("拒绝", "拦截", "错误提示", "不保存", "已存在",
                                     "唯一", "缺失", "不能为空", "非法", "必须")
    ):
        return "form"
    for _op_type, _keywords in _OP_TYPE_KEYWORDS_MAP.items():
        if _op_type == "security_behavior":
            # 已由 _is_security_behavior 用全语义判过，此处仅对 operation 本身兜底
            continue
        for _kw in _keywords:
            if _kw in _op_lower:
                return _op_type
    # operation 本身无法分类时，用全语义文本对非安全类做兜底分类
    if not _op_lower and extra_texts:
        _blob = " ".join(t for t in extra_texts if t).lower()
        for _op_type, _keywords in _OP_TYPE_KEYWORDS_MAP.items():
            if _op_type == "security_behavior":
                continue
            for _kw in _keywords:
                if _kw in _blob:
                    return _op_type
    return None


# ── ★ 场景→可观察行为期望（单一事实源， 根因修复）──
#   根因（5层why）:
#     L1 现象: 7 条 ERROR——Agent报"验证失败: 期望'登录失败提示-异常输入校验' 实际'资产 审计…'"
#     L2 直接: Agent 拿「用例标题」去页面找字面文本，必然找不到。
#     L3 为何: _build_default_test_method 的 expect 取了 `_desc`（= f"{operation[:20]}-异常输入校验"），
#        而全部 5 个调用点传入的 description 都是**用例标题**（_case_desc/_sqli_desc/_xss_desc…），
#        没有任何一个传入"期望结果"。
#     L4 为何反复: 该函数把 description 既当"分类依据"又当"断言文本"，语义过载；
#        调用点于是各自造标题、断言文本被标题污染。历史上只在执行端加启发式补丁。
#     L5 根本: 「用例标题」与「可观察期望」是两种数据，却共用同一个参数与字段。
#   根治 = 在共享函数内一次性分离：标题只用于分类/否定探测；expect 一律由
#    ①场景关键词（本表）→ ②需求期望反馈(negation_hint) → ③操作类型行为默认 三级推导，
#    永不取标题。全部调用点自动治愈，无需逐点打补丁。
#   通用性: 跨系统/行业/页面、零业务硬编码——只描述"页面可观察行为"。
_SCENARIO_EXPECTATIONS: dict[str, str] = {
    # 负向场景
    "异常输入校验": "非法输入被系统拒绝，页面出现校验或错误提示（页面无致命错误）",
    "异常搜索输入": "系统正常响应非法搜索输入：返回空结果或给出提示，页面不崩溃",
    "异常导入": "非法格式文件被拒绝，页面出现格式错误提示",
    "异常导出": "无数据或空选择时导出被正确处理，页面给出相应提示",
    "异常筛选": "无效筛选条件下系统正常响应：显示无结果或提示，页面不崩溃",
    "异常删除": "删除不存在/受保护数据被系统拦截，页面出现错误或确认提示",
    "异常查看": "无效或越权访问被系统拦截，页面给出错误提示或跳转",
    "异常提交": "非法数据提交被系统拒绝，页面出现校验或错误提示",
    # 边界场景
    "边界值验证": "边界输入被系统正确处理（接受并保存，或给出校验提示），页面无致命错误",
    "搜索边界值": "边界搜索条件被系统正确处理，页面给出合理结果",
    "导入边界值": "边界导入场景被系统正确处理，页面给出相应提示",
    "导出边界值": "边界导出场景（全量/跨页）被系统正确处理",
    "筛选边界值": "边界筛选条件被系统正确处理，页面正常响应",
    "删除边界值": "边界删除场景被系统正确处理，页面给出相应提示",
    "查看边界值": "边界查看场景被系统正确处理：页面正常渲染或显示空状态",
    # 安全场景
    "SQL注入防护": "系统防御 SQL 注入：页面不崩溃、不泄露数据库错误信息",
    "XSS防护": "系统防御 XSS：注入脚本被转义或过滤，页面不崩溃、未执行脚本",
    "CSRF防护": "系统防御 CSRF：越权伪造请求被拒绝，页面不崩溃",
    "注入防护": "系统正确防御注入攻击：不崩溃、不泄露内部信息",
    "安全测试": "系统安全防护生效：攻击输入被拒绝或转义，页面不崩溃、无敏感信息泄露",
}

# 操作类型 → 行为默认期望（无场景关键词时的兜底，仍是"可观察行为"而非标题）
_OP_TYPE_DEFAULT_EXPECTATION: dict[str, str] = {
    "view": "页面显示数据列表或详情信息（或明确的无数据提示）",
    "search": "页面给出搜索结果或明确的无匹配提示",
    "filter_op": "列表按筛选条件更新或给出无结果提示",
    "form": "表单提交完成：出现成功提示或校验/错误提示，页面无致命错误",
    "import_op": "导入完成并给出成功或格式错误提示",
    "export_op": "导出完成或在无数据时给出提示",
    "delete_op": "删除完成或出现确认/错误提示",
    "security_behavior": "访问控制行为符合需求：授权角色可正常使用该功能；未授权/低权限访问被拒绝（重定向到登录页或 403/401/无权限提示）",
}
_FALLBACK_EXPECTATION = "操作完成并出现结果反馈（成功提示或校验/错误提示），页面无致命错误"


def _scenario_expectation(*texts: str) -> str:
    """从用例标题/描述中命中场景关键词 → 返回可观察行为期望（无命中返回 ''）。"""
    _blob = " ".join(t for t in texts if t)
    if not _blob:
        return ""
    for _kw, _exp in _SCENARIO_EXPECTATIONS.items():
        if _kw in _blob:
            return _exp
    return ""


# ── ★ 通用：服务端数值校验拒绝语义 ──
#   数值约束校验是跨系统/行业/框架的通用需求语义（"价格<=0非法"/"库存<0"/"必须大于0"/
#   "不能为负"），此类需求被测路径 = 提交越界数值 → 服务端拒绝。判据在此**一次性判定**，
#   _build_default_test_method 内据此构造非法输入载荷落 inputs；执行层只读填充。
#   不会命中正向创建需求（"新增商品保存后跳回列表"无数值约束语汇）。
_NUMERIC_VALIDATION_SEMANTICS = (
    "数值校验", "非法值", "非法", "必须大于", "不能为负", "不得为负",
    "不允许为负", "非负", "必须为正", "大于 0", "大于0", "<=0", "<0", "≤0",
    "must be greater", "must be positive", "not be negative",
)
_NUMERIC_FIELD_PATTERNS = (
    # "对X字段…数值校验" / "X字段必须/不能/为"（"商品价格字段必须为正数"→"价格"）
    # ★ 根因修复(2026-09-28): 捕获组限 2-4 字纯字段名——原 `{1,6}` 在
    #   "服务端对价格字段" 处贪婪捕获"服务端对价格"（含 stopword），`_clean_numeric_field`
    #   剥不净（"端"非 stopword）→ 非法载荷注入到"对价格"（不存在的字段）→
    #   真实字段仍填合法值 → 负向用例假 PASS（实测 REQ-021-I01）。限 2-4 字后
    #   只能命中"价格字段"这种纯字段名，stopword 前缀无法进入捕获组。
    re.compile(r"对?([\u4e00-\u9fa5A-Za-z]{2,4})字段"),
)

# 数值约束词定位（"必须大于0"/"不能为负"/"<=0"）——字段 = 约束词**紧邻左侧**
# 的连续中文/英文末词。先 finditer 约束词再从词前 8 字取 2-4 字末词，
# "…显示价格必须大于"→"价格"（限长 4 + 动词 stopword，杜绝"绝并显示价格"残骸）。
_NUMERIC_CONSTRAINT_RE = re.compile(
    r"必须大于|严格大于|不能为负|不得为负|不允许为负|非负|必须为正|"
    r"大于0|大于 0|小于0|小于 0|<=\s*0|<0|≤0|小于等于|大于等于|小于或等于|大于或等于"
)
# 段内**独立词**收集（2026-09-28: 前后非汉字，杜绝"格必/须严"等滑动窗口残词；
#   字段名通常是与约束词相邻的完整名词，见 _extract_numeric_validation_field）。
_NUMERIC_SEGMENT_RE = re.compile(
    r"(?<![\u4e00-\u9fa5A-Za-z])[\u4e00-\u9fa5A-Za-z]{2,4}(?![\u4e00-\u9fa5A-Za-z])"
)


_NUMERIC_STOPWORDS = (
    "用户", "系统", "数据", "请求", "字段", "数值", "信息", "操作", "功能",
    "输入", "提交", "填写", "商品", "管理", "页面", "必须", "不能", "服务端",
    "显示", "返回", "拒绝", "提示", "出现", "展示", "浏览器", "错误", "报错",
    "校验", "场景", "检查", "验证", "服务", "响应", "结果", "代码", "实例",
    # ★ 根因修复(2026-09-28): "对X字段"结构中的介词"对"须剥除（"服务端对价格字段"
    #   的 stopword 前缀"服务端"剥净后可能残留"对价格"，配合字段模式限长双保险）。
    #   "非法"是数值语义词（"价格非法"），非字段名。
    "若", "当", "则", "如果", "时", "对", "非法", "相应",
)


# 通用数值字段名词白名单: 数值校验需求的字段必然是
#   "数值型业务字段"，取这些通用域名词保证非法载荷注入到真实字段。
#   仅收纯数值名词（金融/库存/度量/频次），零靶子业务名。
_NUMERIC_FIELD_WHITELIST = frozenset({
    # 中文章节
    "价格", "单价", "金额", "总额", "费用", "成本", "售价", "原价",
    "库存", "数量", "个数", "体积", "重量", "长度", "宽度", "高度",
    "面积", "容量", "次数", "频次", "频率", "比例", "百分比", "折扣",
    "时长", "秒数", "时间", "年龄", "温度", "数量", "人数", "总量",
    "积分", "余额", "额度", "上限", "下限", "步长", "阈值", "计数",
    # 英文段
    "price", "stock", "quantity", "amount", "count", "number", "total",
    "weight", "length", "width", "height", "area", "volume", "rate",
    "ratio", "percent", "score", "balance", "limit", "age", "time",
})


def _extract_numeric_validation_field(blob: str) -> str:
    """从需求全文提取数值校验字段名（价格/库存…），提取失败返回 ''。

    两条候选来源按**置信度排序**取优，不简单 min-len（平局会取序首致 mispick）：
      ① "X字段"结构模式（"商品价格字段必须为正数"→"价格"，desc 级最可信）；
      ② 数值约束词紧邻左侧（"…显示库存不能为负数"→"库存"）。
    两类候选都先剥离 stopword 前缀/后缀（"商品库存"→"库存"、"显示价格"→"价格"），
    剩余 2-4 字纯名词才可入选。同源再按长短取短。
    """
    _blob = (blob or "").strip()
    if not _blob:
        return ""
    _found = []  # (priority, field) —— 约束词紧邻路径(0) 优先于 "X字段" 结构(1)
    for _cm in _NUMERIC_CONSTRAINT_RE.finditer(_blob):
        _seg = _blob[max(0, _cm.start() - 12):_cm.start()]
        # ★ 根因修复(2026-09-28): 段内收集**独立词**，取**最靠近约束词的最后一个
        #   非空**——"…价格必须严格大于0" 段尾末词是 stopword"必须"，字段名"价格"
        #   在它前一位；原 tailword 只取段尾末词 → 提取空 → 回退字段路径误取
        #   "端价格"（实测 REQ-021-I01）。从右往左取第一个非 stopword 词。
        _cands: list[str] = []
        for _m2 in _NUMERIC_SEGMENT_RE.finditer(_seg):
            _f = _clean_numeric_field(_m2.group(0))
            if _f:
                _cands.append(_f)
        if _cands:
            _found.append((0, _cands[-1]))
    for _pat in _NUMERIC_FIELD_PATTERNS:
        for _m in _pat.finditer(_blob):
            _f = _clean_numeric_field(_m.group(1) or "")
            if _f:
                _found.append((1, _f))
    if not _found:
        return ""
    _best = min(_found, key=lambda x: (x[0], len(x[1])))
    return _best[1]


def _clean_numeric_field(raw: str) -> str:
    """剥离数值字段候选中的 stopword 前缀/后缀，返回 2-4 字纯名词；否则 ''。

    "商品库存"→"库存"、"显示价格"→"价格"、"绝并显示价格"→"价格"：
    循环剥除命中词后只剩字段词。剥离后须为 2-4 字中文/英文纯字段（价格/库存/数量）。

    ★ 根因修复(2026-09-28): 追加**通用数值字段白名单**过滤——仅"价格/库存/数量/
      金额/容量/长度/次数/比例…"这类**数值字段名词**才采纳。原实现只要 2-4 字
      非 stopword 就收，噪声词（"相应/非负/端价格"）污染注入字段名 → 非法载荷
      注入到真实字段之外的伪字段 → 负向用例假 PASS（实测 REQ-003-I01: 填充
      '相应'失败、'非负'被当字段）。白名单是数值域通用词（金融/库存/度量），
      跨系统/行业成立，零靶子业务硬编码。
    """
    _f = (raw or "").strip()
    if not _f:
        return ""
    _prev = ""
    while _prev != _f:
        _prev = _f
        for _sw in _NUMERIC_STOPWORDS:
            _f = _f.replace(_sw, "")
    _f = _f.strip()
    if not (2 <= len(_f) <= 4):
        return ""
    if _f not in _NUMERIC_FIELD_WHITELIST:
        return ""
    return _f


def _build_default_test_method(
    module: str,
    operation: str,
    description: str,
    op_type: str = "",
    negation_hint: str = "",
    expected_feedback: str = "",
    source_quote: str = "",
) -> dict:
    """★ 方案B(2026-07-29): 从module+operation自动构建默认测试方法。


    当AI未生成test_method时，为Agent提供结构化的执行指南。
    通用性: 跨系统/行业/页面 — 不写死业务术语。

    ★ v5.2(2026-08-09): 操作类型感知验证——filter/search 操作验证输入元素存在，
    而非期望操作描述文字出现在页面上（Django admin 列表页永远不会出现这些文字）。

    ★ 通用修复(2026-09-20): 需求否定感知。negation_hint（需求 expected_feedback 等）
    若明确"取消/不提供某功能"，生成器不得再断言"X 控件可用"（会把系统正确不提供
    误判为失败），改为断言"X 不应存在"。零业务知识、跨系统受益。
    """
    _raw_mod = (module or "").strip()
    # ★ 根因修复(2026-09-22): 文档章节标题不是 UI 模块——剥离编号/类别注释提取
    #   业务实体候选（"7. 删除资产（删除需求）"→"删除资产"）作导航线索；
    #   文档标题（规格说明书/版本号）无实体可提取 → ''。
    _mod = _sanitize_doc_heading_module(_raw_mod)
    # ★ 第四层修复: 模块名经章节清洗而来 = Step2 的模块归属本身不可信，
    #   其"页面显示X相关功能"断言不可观察（X 是章节词不是真实 UI 模块）。
    #   step-1 退回通用可观察断言，真实期望由需求 description（step-2）承载。
    _mod_from_heading = _raw_mod != _mod
    _op = (operation or "").strip()
    _desc = (description or "").strip()
    _ot = (
        op_type
        # ★ 全语义分类(2026-09-22): 操作+描述+期望反馈一起判定，
        #   访问控制语义常藏在 description/expected_feedback 而非 operation
        or _classify_op_type(_op, _desc, negation_hint or "")
        or ""
    )
    _neg = _is_feature_negated(negation_hint or _desc or _op)

    # ★ 根因修复(2026-09-23): expect 三级推导——场景行为 > 需求期望反馈 > 操作类型行为默认。
    #   绝不使用 _desc（用例标题）或 _op（操作名）当断言文本：
    #   标题是"元数据"，Agent 拿它去页面找字面文本必然失败（7 条 ERROR 的根因）。
    _expect_base = (
        _scenario_expectation(_desc, _op)
        or (negation_hint or "").strip()
        or _OP_TYPE_DEFAULT_EXPECTATION.get(_ot, "")
        or _FALLBACK_EXPECTATION
    )

    # ★ Fix A(2026-09-27): step-1「页面内容」断言必须与用例期望极性一致。
    #   根因(实测): 负向结果用例被注入「页面正常加载且核心业务内容可见」——如
    #     TC-REQ-008-I02（viewer 访问 @admin_required 页），SUT 按需求正确返回 403，
    #     断言与用例目的直接矛盾 → 系统性 ERROR（引擎把"正确拦截"判成"页面没加载"）。
    #   判据: 需求自身即「拒绝/错误」语义——必须并入 expected_feedback（
    #     "显示错误提示或跳转404" 等拒绝语义常只写在 expected_feedback），
    #     否则拒绝语义仅靠 operation 词表必然漏报。复用 Step3 极性单一事实源。
    from src.pipeline.step3._rule_extractor_keywords import (
        expects_error_feedback,
        is_disjunctive_text as _is_disj2,
    )

    _denial = _ot == "security_behavior" or expects_error_feedback(
        _op, negation_hint or "", expected_feedback or ""
    )


    # navigation: 从module构建侧边栏路径
    _nav = f"侧边栏→{_mod}" if _mod else "侧边栏菜单"

    # inputs: 从operation提取关键动作词
    _inputs = {}
    _action_keywords = {
        "搜索": "搜索关键词",
        "查询": "查询条件",
        "新增": "名称",
        "创建": "名称",
        "编辑": "名称",
        "修改": "名称",
        "导入": "文件",
        "导出": "导出条件",
        "录入": "数据值",
        "配置": "配置项",
        "设置": "设置项",
        "上传": "文件",
        "填报": "数据值",
        "登记": "数据值",
        "注册": "用户名",
    }
    for _kw, _field in _action_keywords.items():
        if _kw in _op:
            # ★ 根因修复(2026-09-24): 创建型需求的**标识字段**值改用 `<unique>` 占位符。
            #   根因: 原实现恒定写 "TEST_{动词}" 字面量，同 run 内/跨 run 重复提交同名
            #     → 靶子按业务正确返回"名称已存在" → 页面出现校验错误 →
            #     no_error 与"创建成功/跳回列表"类期望系统性假 FAIL（实测 REQ-001/007/011）。
            #   通用判据: 「创建/编辑动词 → 标识字段需唯一」是测试数据通用要求，与业务无关；
            #     查询类动词（搜索/查询）是**检索条件**，必须保留字面量（无匹配场景依赖它）。
            #   消费端: Step4 guided 填充解析 `<unique>`（与 empirical 路径同一约定）。
            _is_create_verb = _kw in (
                "新增", "创建", "新建", "添加", "录入", "登记", "注册", "填报",
                "编辑", "修改", "更新",
            )
            _inputs[_field] = {
                "value": "<unique>" if _is_create_verb else f"TEST_{_kw}",
                "source": "semantic_infer",
            }
            break

    # ★ 根因修复(2026-09-28): 服务端数值校验拒绝型需求（"价格<=0非法/库存<0非法/必须大于0"）
    #   必须构造**非法输入载荷**注入 inputs。根因: 此类用例 inputs 为空时，执行层 RC-W
    #   按"唯一性冲突"方向用 _harvest_existing_identifier 复用列表页**合法**值
    #   （价格=1.0）→ 校验通过提交成功 → "应被拒绝"断言系统性假 FAIL（实测 TC-REQ-003/004-I01）。
    #   通用判据: 「字段有数值约束、越界提交应被拒绝」跨系统/行业成立，零业务硬编码；
    #   非法载荷取 -1（同时违反 >0 与 >=0 两类约束）。字段名从需求文本"对X字段…数值校验/
    #   X<=0非法"结构提取；提取失败或非数值校验语义则不注入（维持原行为）。
    #   单一事实源: 非法载荷语义在本函数一次性判定并落 inputs，执行层只读填充，
    #   不得在步骤4再按文本重推（正是 RC-W 误推的根源）。
    _invalid_blob = " ".join(
        t for t in (_op, _desc, negation_hint or "", expected_feedback or "",
                    source_quote or "") if t
    )
    # ★ 根因修复(2026-09-28): 非法载荷**仅对拒绝/校验失败型**需求注入。
    #   根因（实测 TC-REQ-013-I01）: 正向"新增商品"desc/source_quote 含文档通用
    #   校验句"库存不能为负数"（归类到多个 REQ 的公共描述）→ 语义命中注入 库存=-1
    #   → 正向新增提交非法库存被拒 → submit_ok 假 FAIL。非法载荷是与"应被拒绝"
    #   配套的**负向测试数据**，正向成功型需求注入它自相矛盾。
    _denial_ctx = bool(_denial)  # expects_error_feedback / security_behavior 判定
    if (
        _denial_ctx and _invalid_blob
        and any(_s in _invalid_blob for _s in _NUMERIC_VALIDATION_SEMANTICS)
    ):
        # ★ 字段名优先取「需求短标签」（operation+expected_feedback）：标签聚焦单一
        #   被测字段（"验证商品库存合法性"/"显示库存不能为负数的错误信息"→"库存"）；
        #   desc/source_quote 常合并陈述多个约束（"价格必须大于0，库存不能为负"），
        #   同长候选 min() 取序首 → REQ-007 误取"价格"（实测）。短标签提取失败再回落
        #   全文（desc 直接写"X<=0"单约束时靠全文）。
        _num_field = (
            _extract_numeric_validation_field(
                " ".join(
                    t for t in (_op, negation_hint or "", expected_feedback or "") if t
                )
            )
            or _extract_numeric_validation_field(_invalid_blob)
        )
        if _num_field and _num_field not in _inputs:
            _inputs[_num_field] = {"value": "-1", "source": "invalid_value"}
            _log.info(
                "[InvalidValue] 数值校验拒绝需求 → 注入非法载荷: %s=-1", _num_field
            )

    # ★ v5.2: 操作类型感知验证——不同操作类型对应不同的验证断言
    # ★ 根因修复(2026-09-22): 行为型安全需求（security_behavior）断言的是访问控制
    #   行为，不是"页面显示某模块"的 UI 存在性。生成页面显示断言必然不可观察
    #   （模块是需求分类标签，非菜单项），Step4 会误判 FAIL/需求覆盖缺口。
    if _ot == "security_behavior":
        _verifications = [
            {
                "step": 1,
                "action": "verify",
                "target": "访问控制行为",
                "value": "",
                "expect": (
                    "访问控制行为符合需求：授权角色可正常使用该功能；"
                    "未授权/低权限访问应被拒绝（重定向到登录页或 403/401/无权限提示）"
                ),
            },
        ]
    else:
        # 负向用例落地页＝错误/拦截页；文案须 ≤40 字符，否则被导航解析器当长叙述剔除。
        # ★ 根因修复(2026-09-28): 校验拒绝 ≠ 访问拦截。V1 是**提交前**的页面内容断言——
        #   表单校验拒绝（名称重复/必填/非法值）时页面可正常进入，拒绝发生在提交后，
        #   若 V1 断言"被拦截"必 FAIL（实测 TC-REQ-005/006/007：列表页正常却判拦截）。
        #   仅「越权查看」类（view+denial，连页面都进不去）才断言拦截页。
        if _denial:
            # ★ 根因修复(2026-09-28): **析取式**期望（"显示错误提示或跳转404"/
            #   "删除不存在数据被拦截或正确处理"）不强制"拦截"——SUT 容错（正常
            #   列表/静默重定向）也符合期望，断言"被拦截"必假 FAIL（实测
            #   REQ-004-I02: Agent 访问正常列表却判拦截）。仅非析取式的越权查看
            #   （view/search/filter_op 且期望明确拒绝）才断言拦截页。
            # 拦截断言的唯一依据 = **访问限制语义**（权限语境 × 限制词共现，与
            # access_control_check 的发射判据同源）。仅"错误反馈"极性（如表单校验
            # 拒绝"展示 message 文本并保留已填内容"）不是访问拦截，断言拦截必假 FAIL。
            _expect1 = (
                "访问/操作被系统拦截，页面给出错误或拒绝提示"
                if _is_access_restriction_req(
                    _op, _desc, negation_hint or "", expected_feedback or "", source_quote or ""
                )
                and _ot in ("view", "search", "filter_op")
                and not _is_disj2(
                    " ".join(
                        t for t in (negation_hint or "", expected_feedback or "") if t
                    )
                )
                else "页面正常加载且核心业务内容可见"
            )
        elif _neg:
            _expect1 = "页面不显示该功能入口（功能已取消或不提供）"
        # 模块名来自章节清洗 → 不断言章节词显示；仅真实 UI 模块才断言其显示
        elif _mod and not _mod_from_heading:
            _expect1 = f"页面显示{_mod}相关功能"
        else:
            _expect1 = "页面正常加载且核心业务内容可见"
        # ★ 根因修复(2026-09-28): 加载类断言落结构化字段 `assert="page_loaded"`。
        #   根因: "页面正常加载且核心业务内容可见"是纯文本 expect，引导 verify 只能
        #     关键词/AI 匹配——全泛词必败，页面已渲染却误判失败（实测 TC-REQ-001/003）。
        #   "页面加载成功"的可观察证据 = 无 404 + 主内容区可见（引擎唯一实现
        #     _verify_page_content_loaded），语义在此一次性判定，下游读取执行。
        #   通用性: 加载断言语义与业务无关，任何系统页面加载都可如此验证。
        _is_page_loaded_assert = (
            not _denial
            and not _neg
            and ("加载" in _expect1 or "核心业务内容" in _expect1 or "相关功能" in _expect1)
        )
        _verifications = [
            {
                "step": 1,
                "action": "verify",
                "target": "页面内容",
                "value": "",
                "expect": _expect1,
                **({"assert": "page_loaded"} if _is_page_loaded_assert else {}),
            },
        ]

    # 步骤2: 操作类型感知断言（需求否定时翻转存在性断言）
    if _ot in ("filter_op", "search"):
        # filter/search: 验证筛选/搜索输入框存在，而非操作描述文字
        _verifications.append({
            "step": 2,
            "action": "verify",
            "target": "操作结果",
            "value": "",
            "expect": _apply_negation(
                "页面包含筛选/搜索输入框或列表数据", _neg, "筛选/搜索控件"
            ),
        })
    elif _ot == "form":
        # ★ 根因修复(2026-09-28): 正向提交成功的可观察证据 = **提交后重定向/刷新回
        #   列表（URL/主内容变化）且页面无错误条**，不是页面上的"新增成功"文案。
        #   根因: SUT 增改成功后只重定向回列表（无 toast/成功条），原期望落
        #   "提示新增成功，页面自动刷新回列表页，列表行数加一"纯文本 → 引导 verify
        #   文本/AI 语义判定，页面无成功文案 → 系统性假 FAIL（实测 TC-REQ-002/007/
        #   013/014-I01：重定向已发生、数据已更新，却判语义不符）。
        #   判据（通用）: form 正向成功 = 非拒绝（denial）非否定（neg），且期望携带
        #   提交/保存/创建/更新/重定向/刷新/返回列表语义；落结构化 `assert="submit_ok"`，
        #   执行层对"提交后页面回到业务视图 + 无错误条"做 DOM 判定。与加载断言同构。
        _is_form_success = (
            (not _denial and not _neg)
            and any(
                _s in _expect_base
                for _s in ("提交", "保存", "创建", "新增", "更新", "修改",
                           "重定向", "刷新", "返回列表", "列表页")
            )
        )
        _verifications.append({
            "step": 2,
            "action": "verify",
            "target": "操作结果",
            "value": "",
            # ★ 根因修复(2026-09-23): 原为 `_desc or _op`（标题当断言）→ 改为行为期望
            "expect": _apply_negation(_expect_base, _neg),
            **({"assert": "submit_ok"} if _is_form_success else {}),
        })
    elif _ot == "view":
        _verifications.append({
            "step": 2,
            "action": "verify",
            "target": "操作结果",
            "value": "",
            "expect": _apply_negation("页面显示数据列表或详情信息", _neg, "该功能"),
            # ★ 与加载断言同构(2026-09-28): "数据列表/详情"＝页面主内容渲染证据，
            #   落结构化标记由引导按 DOM 判定，不按全泛词文本猜。
            **({"assert": "page_loaded"} if not _neg else {}),
        })
    elif _ot == "delete_op":
        # ★ 删除期望有三态：析取式（确认/取消都可能）/ 明确"不生效"（取消则不删除、
        #   不执行删除）/ 明确"生效"（确认后删除）。断言证据必须随之取反，否则
        #   "取消则不删除"被断言成"行数减少"→ SUT 正确未删除反判 FAIL。
        #   极性判据读同层单一事实源（_rule_extractor_keywords），生成端不另立词表。
        from src.pipeline.step3._rule_extractor_keywords import (
            expects_noop_invariant as _noop_inv,
        )

        _del_disj = _is_disj2(
            " ".join(t for t in (negation_hint or "", expected_feedback or "") if t)
        )
        _del_noop = _neg or _noop_inv(
            _op, negation_hint or "", expected_feedback or "", source_quote or ""
        )
        if _del_disj:
            _del_expect, _del_assert = "页面正常加载且核心业务内容可见", {"assert": "page_loaded"}
        elif _del_noop:
            _del_expect = "取消/拒绝后不执行删除：目标记录仍在列表中，列表行数未减少"
            _del_assert = {"assert": "delete_aborted"}
        else:
            _del_expect = "删除操作完成：确认后列表中该行被移除（行数减少）"
            _del_assert = {"assert": "delete_done"}
        _verifications.append({
            "step": 2,
            "action": "verify",
            "target": "操作结果",
            "value": "",
            "expect": _del_expect,
            **_del_assert,
        })
    elif _ot in ("import_op", "export_op"):
        _verifications.append({
            "step": 2,
            "action": "verify",
            "target": "操作结果",
            "value": "",
            "expect": _apply_negation(
                "导入/导出页面或控件可用", _neg, "导入/导出功能"
            ),
        })
    elif _ot == "security_behavior":
        _verifications.append({
            "step": 2,
            "action": "verify",
            "target": "操作结果",
            "value": "",
            # ★ 根因修复(2026-09-23): 原为 `_desc or _op`（需求原文长句当断言，
            #   如"未认证用户直接访问业务页面时，系统必须将其重定向到登录页…"）
            #   → 改为场景/行为期望，交由 Agent 语义判定（含 LLM）而非字面匹配。
            "expect": _apply_negation(_expect_base, _neg),
        })
    else:
        # 无法分类的操作：行为默认期望（不得回退到标题/操作名）
        _verifications.append({
            "step": 2,
            "action": "verify",
            "target": "操作结果",
            "value": "",
            "expect": _apply_negation(_expect_base, _neg),
        })

    # ★ 根因修复(2026-09-28): 性能型需求（"响应时间≤2秒"/"加载时长<1秒"）的
    #   guided 验证点**不得**是耗时文本断言——耗时是页面快照里不可观察的量，
    #   Agent 只能如实回答"页面无耗时说明"→ 恒 ERROR（实测 TC-REQ-025-I01:
    #   期望'页面响应时间不超过2秒' 实际列表页）。真实耗时由 checks 层
    #   performance_lcp_check / 横切 perf 门禁测量；引导层只保留页面加载断言。
    try:
        from src.pipeline.step3.case_rules._intent_checks import (
            _is_performance_expectation as _is_perf_req,
        )
    except Exception:
        _is_perf_req = lambda *a, **k: False
    if _is_perf_req(
        " ".join(t for t in (_desc, _op, negation_hint or "", expected_feedback or "") if t)
    ):
        _verifications = [
            v for v in _verifications
            if v.get("step") == 1 or v.get("assert") == "page_loaded"
        ]

    return {
        "op_type": _ot,  # ★ 场景标记(2026-09-22): Step4 据此豁免行为型需求的导航判决
        "navigation": _nav,
        "inputs": _inputs,
        "verifications": _verifications,
    }


# ── 安全测试 Payload 库（通用，零业务硬编码；模块级唯一定义，类与下游断言族共用）──
SQL_INJECTION_PAYLOADS = [
    "' OR '1'='1",
    "' OR '1'='1' --",
    "'; DROP TABLE users; --",
    "1' UNION SELECT NULL--",
    "admin'--",
    "1' AND '1'='1",
    "' OR 1=1#",
    '1" OR "1"="1',
]

XSS_PAYLOADS = [
    '<script>alert(1)</script>',
    '<img src=x onerror=alert(1)>',
    '"><script>alert(1)</script>',
    '<svg onload=alert(1)>',
    'javascript:alert(1)',
    '<body onload=alert(1)>',
]


def classify_security_payload(value: str) -> str:
    """注入值属于哪一类安全攻击载荷（``sqli`` / ``xss`` / ``""``=非安全载荷）。

    why: 「本用例注入的是哪类攻击」这一事实只由**载荷库**定义。生成端发用例、
      落库前派生断言、执行端取证必须读同一判据，否则三处各猜一套词表。
    """
    _v = str(value or "").strip()
    if not _v:
        return ""
    if _v in XSS_PAYLOADS:
        return "xss"
    if _v in SQL_INJECTION_PAYLOADS:
        return "sqli"
    return ""


class BaseCaseRule(IntentChecksMixin):
    """用例生成规则基类。

    子类只需实现：
      - rule_id / name / description / control_types / priority（类属性）
      - match(req, control_type) → bool（默认按 control_types 匹配）
      - generate(req, ...) → list[dict]（生成 3 条用例：P01/N01/B01）
    """

    # ── 元数据（子类必须覆盖）──
    rule_id: str = ""  # 唯一标识，如 "input_rule"
    name: str = ""  # 中文名，如 "输入框规则"
    description: str = ""  # 描述
    control_types: list[str] = []  # 适用的控件类型，如 ["input", "textarea"]
    priority: int = 10  # 优先级（越大越先匹配，默认 10）
    version: str = "1.0"

    # ── 匹配 ──
    def match(self, req: dict[str, Any], control_type: str) -> bool:
        """是否匹配该需求。默认按 control_type ∈ control_types 匹配。"""
        return control_type in self.control_types

    # ── 生成（子类必须实现）──
    def generate(
        self,
        req: dict[str, Any],
        target_page: str,
        control_type: str,
        merged_data: dict[str, Any],
        selected_ops: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """生成 3 条用例（P01/N01/B01）。

        Returns:
            [case_p01, case_n01, case_b01] 或空列表（不适用）
        """
        raise NotImplementedError

    # ── 工具方法（子类可直接调用）──
    @staticmethod
    def _safe_rid(req: dict[str, Any]) -> str:
        rid = str(req.get("req_id", "") or "").strip()
        return rid.replace(" ", "_").replace("/", "_")

    @staticmethod
    def _op_ids(selected_ops: list[dict]) -> set:
        return {op["action_id"] for op in selected_ops}

    def _build_one(
        self,
        case_id: str,
        req: dict,
        module: str,
        desc: str,
        operation: str,
        expected: str,
        scenario_type: str,
        risk_level: str,
        case_type: str,
        test_method: str,
        control_type: str,
        required_tracks: list[str],
        actions: list[dict],
        checks: list[dict],
        scenario_label: str,
        data_dict: dict | None = None,
    ) -> dict[str, Any]:
        """构建一条用例（标准格式）。

        ★ 根因修复(2026-08-09): 新增 data_dict 参数（可选），
          用于 I01 类型用例生成字段级 text_contains 检查。
        """
        rid = self._safe_rid(req)
        _title = f"{module}-{rid}-{scenario_label}"

        # ★ 根因修复(2026-08-09): I01 字段级检查
        _is_i01 = "I01" in (case_id or "") or "字段" in _title
        if _is_i01 and data_dict:
            with guard("_build_one: from .._rule_extractor_utils import ", log=_log, level=logging.WARNING):
                from .._rule_extractor_utils import (
                    _extract_field_names_from_data_dict,
                    _build_field_checks_for_i01,
                )
                _field_names = _extract_field_names_from_data_dict(data_dict)
                if _field_names:
                    checks = _build_field_checks_for_i01(_field_names, checks)
                    _log.info(
                        "[_build_one] ★ I01字段级检查: case=%s fields=%d -> %s",
                        case_id, len(_field_names), _field_names[:5],
                    )

        # ★ 根因修复(2026-08-09): 添加 precondition / test_steps / expected_result 字段
        case = {
            "case_id": case_id,
            "req_id": rid,
            "title": _title,
            "description": f"执行{desc}的{scenario_label}，确认系统行为符合预期",
            "risk_level": risk_level,
            "type": case_type,
            "scenario_type": scenario_type,
            "test_method": test_method,
            "control_type": control_type,
            "required_tracks": required_tracks,
            "actions": actions,
            "checks": checks,
            "_generation_source": "rule_extract",
            "precondition": f"1.系统已登录 2.已进入{module}管理页面",
            "test_steps": self._build_test_steps_json(actions),
            "expected_result": self._build_expected_result(checks, req=req, declared=expected),
            # ★ 极性单一事实源(2026-10-14)：需求自身的期望反馈必须落到用例上。
            #   下游 _check_guard.sanitize_case_checks 的极性闸门（no_error 反转）只读
            #   case.expected_feedback；原实现从不写该字段 + expected_result 由 checks 反推
            #   （恒"系统无报错"）→ 闸门对"需求期望拒绝"失明 → SUT 按需求正确渲染拒绝提示
            #   反被判 FAIL（实测 REQ-002/003-P01）。
            # ★ 根因修复(2026-10-15)：极性必须取**该用例自身的期望**，不得无差别继承需求级。
            #   根因: 原实现无条件灌 `req.expected_feedback` —— 而同一需求下的用例变体
            #     （正向/异常/边界/安全/性能）期望**各不相同**。实测 TC-REQ-011-I04
            #     （SQL 注入防护，自身 expected="系统不崩溃，SQL注入被正确防御"）被灌入
            #     REQ-011 的"系统提示用户名或密码错误" → 下游极性闸门据"错误"字样把
            #     `no_error`（注入后无致命错误）**反转**成"应提示用户名或密码错误" →
            #     靶子正确防御（页面正常）反被判 FAIL 并归因 PRODUCT_BUG（假 Bug）。
            #   优先级与 _build_expected_result 同构：本用例声明 → 需求级兜底；
            #     同一条语义（期望反馈）在同一处按同一顺序推导，杜绝两套口径。
            "expected_feedback": (
                str(expected or "").strip()
                or str((req or {}).get("expected_feedback") or "")
            ),
        }
        return case

    @staticmethod
    def _build_test_steps_json(actions: list[dict]) -> str:
        """将 actions 数组格式化为 JSON 字符串（test_steps 字段）。"""
        import json as _json
        try:
            return _json.dumps(actions, ensure_ascii=False, indent=2)
        except Exception:
            return str(actions)

    @staticmethod
    def _build_expected_result(
        checks: list[dict], req: dict | None = None, declared: str = ""
    ) -> str:
        """用例预期结果：**调用方声明的预期**优先，需求预期次之，checks 反推兜底。

        根因(2026-10-15)：原实现无条件取「第一个 check 的 expect」，而
        `element_exists` 类 check 的 expect 是**存在性字面量**（"存在"/"无错误"），
        于是用例 expected_result 变成无意义残句——实测 v9 全库 **61 条**为 `"存在"`；
        同时 `_build_one` 的 `expected` 形参**被完全丢弃**（6 个调用点都传了有语义的值，
        却从未生效）——属"上游声明被下游忽略"的 SSOT 违背。
        该字段有真实下游：用例审核页展示、导出、置信度评分（`confidence_scorer.py`）。
        """
        # ① 调用方声明（各调用点传入的场景预期，最贴近该变体语义）
        _declared = str(declared or "").strip()
        if _declared and not _is_presence_only_fragment(_declared):
            return _declared
        # ② 需求自身预期
        _req_expect = str((req or {}).get("expected_feedback") or "").strip()
        if _req_expect and not _is_presence_only_fragment(_req_expect):
            return _req_expect
        # ③ checks 反推：跳过存在性字面量，取**首个有语义的** expect
        for _c in checks or []:
            _exp = str(_c.get("expect", "") or _c.get("expected", "") or "").strip()
            if _exp and not _is_presence_only_fragment(_exp):
                return _exp
        # ④ 兜底：声明/需求自述，但**仍不得返回纯存在性字面量**
        _fallback = _declared or _req_expect
        if _fallback and not _is_presence_only_fragment(_fallback):
            return _fallback
        return "操作成功"

    def _nav(self, target_page: str) -> dict[str, str]:
        return {"type": "navigate", "target_hint": target_page}

    def _click(self, target_hint: str, in_dialog: bool = False) -> dict[str, str]:
        # ★ 通用修复(2026-07-22): in_dialog 标记 dialog 内控件（如"确定"按钮）
        #   根因: dialog 内控件不在页面 DOM 快照中，_control_validation 会误判为缺失
        #   通用性: 适用于所有"点击触发按钮→弹窗内操作"的 CRUD 场景
        action: dict[str, Any] = {"type": "click", "target_hint": target_hint}
        if in_dialog:
            action["in_dialog"] = True
        return action

    def _fill(self, target_hint: str, value: str) -> dict[str, str]:
        return {"type": "fill", "target_hint": target_hint, "value": value}

    def _screenshot(self) -> dict[str, str]:
        return {"type": "screenshot", "target_hint": "操作后截图"}

    def _select(self, option_text: str) -> dict[str, str]:
        return {
            "type": "select_option",
            "target_hint": "下拉选择框",
            "option_text": option_text,
        }

    def _upload(self, file: str, in_dialog: bool = True) -> dict[str, str]:
        # ★ 通用修复(2026-07-22): upload 控件默认 in_dialog=True
        #   根因: 文件上传控件几乎总是在点击触发按钮后弹出的 dialog 内，
        #         DOM 快照只爬取页面初始状态，不可能包含 dialog 内控件
        #   通用性: 适用于所有 CRUD 系统的 upload 场景
        action: dict[str, Any] = {
            "type": "upload_file",
            "target_hint": "文件上传控件",
            "file": file,
        }
        if in_dialog:
            action["in_dialog"] = True
        return action

    def _press_key(self, key: str, focus_hint: str = "") -> dict[str, str]:
        """★ Fix52b: 生成按键操作（如 Enter 提交搜索）。
        focus_hint: 按键前需要聚焦的元素提示（可选）
        """
        action = {"type": "press_key", "target_hint": f"按{key}键", "key": key}
        if focus_hint:
            action["focus_hint"] = focus_hint
        return action

    # ── 按钮定位方法已拆分到 button_resolver.py（§4.3 单文件≤800行）──

    @staticmethod
    def _classify_button(submit_text: str, expected_feedback: str = "") -> str:
        """★ §13.3修复(2026-07-25): 分类按钮类型 — trigger / submit / other。

        checks 语义必须匹配 actions 语义（§13.3 + §12.5反模式）：
        - trigger（新建/编辑等）：actions 只打开表单不提交 → checks 验证表单/弹窗打开
        - submit（保存/提交等）：actions 提交表单 → checks 验证成功提示
        - other（执行/计算等）：actions 点击触发响应 → checks 验证页面有响应

        通用性(§12.4): 跨系统/跨行业/跨页面 — 任何按钮都能归入三类之一
        覆盖性: 供 button_rule/input_rule/form_rule 等所有规则复用
        """
        _is_trigger = any(k in (submit_text or "") for k in _TRIGGER_KEYWORDS) or any(
            k in (expected_feedback or "") for k in _TRIGGER_FEEDBACK_KEYWORDS
        )
        if _is_trigger:
            return "trigger"
        if any(k in (submit_text or "") for k in _SUBMIT_BUTTON_KEYWORDS):
            return "submit"
        return "other"

    @staticmethod
    def _trigger_check_selectors() -> str:
        """触发按钮 checks 的通用选择器（表单/弹窗打开验证）。"""
        return (
            "[role='dialog'], [role='form'], [role='alertdialog'], "
            ".ant-modal, .ant-drawer, .ant-form, "
            ".el-dialog, .el-drawer, .el-form, form"
        )

    def _build_submit_checks(
        self,
        submit_text: str,
        operation: str,
        expected: str,
        req: dict[str, Any],
        check_type: str = "success",
    ) -> list[dict[str, Any]]:
        """★ §13.3公共方法(2026-07-25): 根据按钮类型生成匹配的 checks。

        所有 case_rules 的 P01/N01/B01 checks 都应调用此方法，避免 §13.3 语义错配。

        参数:
            submit_text: 提交按钮文本（用于 _classify_button 判断）
            operation: 需求操作语义
            expected: 期望反馈
            req: 需求字典
            check_type: "success" | "error_hint" | "validation_hint"
                - success: 提交按钮→成功提示; 触发按钮→表单打开
                - error_hint: 提交按钮→校验提示; 触发按钮→表单打开
                - validation_hint: 提交按钮→超长校验; 触发按钮→表单打开

        通用性(§12.4): 跨系统/跨行业/跨页面 — 任何按钮+check_type 组合都能处理
        覆盖性: 供 button/input/form/checkbox/radio/select/switch/date/modal/table 规则复用
        合规: §13.3 "checks必须匹配actions语义" + §12.5反模式
        """
        _btn_type = self._classify_button(submit_text, expected)
        _FORM_SELECTOR = self._trigger_check_selectors()

        if _btn_type == "trigger":
            # 触发按钮：actions 只打开表单不提交 → checks 验证表单/弹窗打开
            return [
                self._build_check(
                    "element_exists_at", _FORM_SELECTOR, "存在", "表单/弹窗打开成功"
                ),
                self._build_check("no_error", "页面", "无错误", "系统无报错"),
            ]

        # 提交按钮/其他按钮：按 check_type 生成匹配的 checks
        if check_type == "success":
            return [
                self._build_success_check(operation, req=req),
                self._build_check("no_error", "页面", "无错误", "系统无报错"),
            ]
        elif check_type == "error_hint":
            return [
                self._build_check("error_hint", "页面", "提示信息", "系统显示校验提示"),
            ]
        elif check_type == "validation_hint":
            return [
                self._build_check(
                    "validation_hint", "页面", "校验提示", "系统显示超长校验提示"
                ),
            ]
        # 兜底：默认 success
        return [
            self._build_success_check(operation, req=req),
            self._build_check("no_error", "页面", "无错误", "系统无报错"),
        ]

    def _build_downgrade_cases(
        self,
        req: dict[str, Any],
        target_page: str,
        merged_data: dict[str, Any],
        reason: str = "",
    ) -> list[dict[str, Any]]:
        """★ P0-refactor(2026-07-27): 委托到 _build_intent_case（统一意图驱动）。

        架构决策（§16.2 长期路线）：
          取消降级机制。所有用例统一为意图驱动——Step3 只生成意图描述，
          Step4 AgentExplorer 在浏览器中实时探索执行。不存在"找不到按钮"的问题。
          此方法保留签名以兼容所有调用方，内部委托到 _build_intent_case。
        """
        return self._build_intent_case(req, target_page, merged_data, reason)

    def _build_exploratory_cases(
        self,
        req: dict[str, Any],
        target_page: str,
        merged_data: dict[str, Any],
        reason: str = "",
    ) -> list[dict[str, Any]]:
        """★ P0-refactor(2026-07-27): 委托到 _build_intent_case（统一意图驱动）。"""
        return self._build_intent_case(req, target_page, merged_data, reason)

    def _build_intent_case(
        self,
        req: dict[str, Any],
        target_page: str,
        merged_data: dict[str, Any],
        reason: str = "",
        matched_apis: list = None,
    ) -> list[dict[str, Any]]:
        """★ P0-refactor(2026-07-27): 统一意图驱动用例生成（唯一用例类型）。

        架构决策（§16.2 长期路线）：
          Step3 不再依赖静态 DOM 快照生成具体 actions。所有用例统一为意图驱动：
          - 生成 _explore_task（操作描述 + 目标页面 + 期望反馈）
          - Step4 AgentExplorer 在浏览器中实时感知→决策→执行→验证
          - 对标 BrowserGym/WebVoyager 的 Agent 驱动测试模式

        通用性（§12.4 全维度通过）：
          1. 跨系统：React/Vue/Angular/原生HTML 通用，Agent 实时感知不依赖框架
          2. 跨行业：电商/SaaS/ERP/配方系统通用，无业务术语硬编码
          3. 跨页面：列表页/详情页/表单页/Tab页通用，Agent 动态探索
          4. 数据完整性：不依赖静态 DOM 快照，Agent 在浏览器中实时获取
          5. 根因溯源：取消"先爬全再测试"的感知-执行分离模式
          6. 覆盖性：所有用例统一走此方法，零遗漏

        Args:
            req: 需求字典 {req_id, operation, description, expected_feedback, module}
            target_page: 目标页面 URL 或路径
            merged_data: 合并数据（APIs, DB tables, Figma pages）
            reason: 生成原因（调试用）
            matched_apis: 匹配的 API 列表（可选，用于注入 API checks）

        Returns:
            [case]: 单条意图驱动用例
        """
        rid = self._safe_rid(req)
        operation = req.get("operation") or ""
        module = req.get("module", merged_data.get("module", "")) or ""
        # ★ 通用修复(2026-07-28): 当 module 为低质量占位符时，从 operation 提取真实模块名
        if not module or "表格块" in module or "table chunk" in module.lower():
            _extracted = _extract_module_from_operation(operation)
            if _extracted:
                module = _extracted
        desc = req.get("description", "") or operation
        expected = req.get("expected_feedback") or ""

        _log.info(
            "[_build_intent_case] ★ 意图驱动用例★ %s: op=%r, module=%s, page=%s",
            rid,
            operation[:50],
            module,
            target_page[:60],
        )

        # 统一 actions：navigate + screenshot（AgentExplorer 负责具体操作）
        actions = [
            self._nav(target_page),
            self._screenshot(),
        ]

        # ── W1-2: checks 来源决策（沿用已有产物 > 需求断言 > 最小占位）──
        #   根因(§10.9): 原实现无条件写死 [element_exists(整页 body), no_error]，
        #     注释声称"AgentExplorer 会追加真实验证"但全仓无任何追加实现（grep 无写回点），
        #     导致 intent 类用例只验"页面能打开"→ 假 PASS 温床(§12.4)。
        #   修复: 已有 checks/verification_plan 一律沿用；否则用需求 expected_feedback
        #     生成可证伪断言(§12.3 断言来源=设计+需求)；两者皆无才给最小占位并打标。
        # ★ 根因修复(2026-08-09): 传入 entity_tables，用于 I01 字段级 text_contains 检查
        _entity_tables = (merged_data or {}).get("entity_tables", {})
        checks, needs_real_assertion = self._resolve_intent_checks(
            req, expected, operation, entity_tables=_entity_tables, is_i01=True,
            module=module,
        )

        # ★ 多轨验证(2026-08-08): 注入 API/DB checks + 动态 required_tracks
        # ★ 接口操作型需求（operation="请求GET /assets接口"）：以「导航触发 + 观测
        #   该接口被调用」承载，而非浏览器表单探索。
        #   根因: 此前一律生成 _explore_task（表单/点击探索）→ 接口类需求无对应
        #   表单页面 → Agent 空转耗尽预算 → 结构性假 ERROR（实测 REQ-007..018 共 70 条）。
        #   可执行性: 浏览器轨的 api_call_count_check 依据 api_tracker 观测到的真实
        #   请求计数（_verify_api_ext），导航到承载该接口的页面即触发调用 → 可证伪。
        #   通用性: HTTP method+path 判据单点见 is_api_operation，零业务硬编码。
        from src.pipeline.step3._rule_extractor_keywords import is_api_operation
        from src.pipeline.step3._rule_extractor_keywords import (
            parse_api_operation as _parse_api_op,
        )

        if is_api_operation(operation):
            _am, _ap = _parse_api_op(operation) or ("GET", "/")
            _api_checks = [
                self._build_check(
                    "api_call_count_check",
                    f"{_am} {_ap}",
                    "1",
                    f"接口 {_am} {_ap} 被调用至少 1 次",
                    track="api", precision="exact", operator=">=",
                ),
            ]
            _api_actions = [
                self._nav(target_page or "/"),
                self._screenshot(),
            ]
            _api_case = self._build_one(
                f"TC-{rid}-I01",
                req, module, desc, operation, expected,
                "api_verify", "P0", "功能", "api", "api_verify",
                ["api"],
                _api_actions,
                _api_checks,
                f"{operation[:30]}-接口验证",
                data_dict=merged_data,
            )
            _api_case["_exploratory"] = False
            _api_case["low_confidence"] = False
            return [_api_case]

        # 注入 API checks（如有匹配的 API）
        _has_api = False
        if matched_apis:
            for api in matched_apis[:2]:
                _path = api.get("path", "") or api.get("url", "")
                _method = api.get("method", "GET")
                if _path:
                    _has_api = True
                    checks.append(
                        self._build_check(
                            "api_call_count_check",
                            f"{_method} {_path}",
                            "1",  # expected_count=1（operator=">=" ⇒ 至少1次）
                            f"API {_method} {_path} 被调用至少1次",
                            track="api", precision="exact",
                            operator=">=",
                        )
                    )
                    # API 响应体校验
                    _res_fields = api.get("res_fields", "") or api.get("response_fields", "")
                    if _res_fields:
                        checks.append(
                            self._build_check(
                                "api_response_body_check",
                                f"{_method} {_path}",
                                f"响应包含字段: {_res_fields[:80]}",
                                f"API {_method} {_path} 响应体包含预期字段",
                                track="api", precision="fuzzy",
                                check_expected=_res_fields[:80],
                            )
                        )
                    # API 轨是可证伪断言，不再算"仅占位"
                    needs_real_assertion = False

        # 注入 DB checks（如有匹配的 DB 表）
        _has_db = False
        _db_tables = (merged_data or {}).get("db_tables", [])
        if _db_tables:
            for _db in _db_tables[:2]:
                _tbl = _db.get("name", "") or _db.get("table", "")
                if _tbl:
                    _has_db = True
                    checks.append(
                        self._build_check(
                            "db_record_exists_check",
                            _tbl,
                            f"表 {_tbl} 存在至少1条记录",
                            f"DB表 {_tbl} 有数据变更",
                            track="db", precision="fuzzy",
                        )
                    )

        # 动态计算 required_tracks（基于实际注入的 checks track 类型）
        _tracks = set()
        for c in checks:
            _t = c.get("track", "ui")
            if _t in ("ui", "api", "db"):
                _tracks.add(_t)
        if not _tracks:
            _tracks = {"ui"}
        required_tracks = sorted(_tracks)  # e.g. ["api", "db", "ui"]

        _op_brief = (operation or desc or "未知操作")[:30]
        # ★ 根因修复(2026-08-09): 传入 merged_data 作为 data_dict，用于 I01 字段级检查
        case = self._build_one(
            f"TC-{rid}-I01",
            req,
            module,
            desc,
            operation,
            expected,
            "exploratory",
            "P0",
            "功能",
            "playwright",
            "exploratory",
            required_tracks,
            actions,
            checks,
            f"{_op_brief}-Agent探索验证",
            data_dict=merged_data,
        )

        case["_exploratory"] = True
        case["low_confidence"] = False  # 意图驱动用例不是低置信度
        # W1-2: 沿用上游已生成的三轨计划（存在才写，避免造空壳覆盖 Step4 兜底）
        _existing_plan = req.get("verification_plan")
        if isinstance(_existing_plan, dict) and any(
            _existing_plan.get(t) for t in ("ui", "api", "db")
        ):
            case["verification_plan"] = _existing_plan
        # W1-2: 仅占位断言必须显式打标，供审核/报告识别"该用例尚无真实验证"
        if needs_real_assertion:
            case["needs_real_assertion"] = True
            _log.warning(
                "[_build_intent_case] %s 无可用断言来源（无 checks/verification_plan/"
                "expected_feedback/API），仅生成最小占位并标记 needs_real_assertion",
                rid,
            )
        # ★ 根因修复(2026-09-22): 文档章节标题不得进入执行导航链路（Step4 ModuleNav
        #   会拿它做侧边栏匹配→必失败→误判需求覆盖缺口）。展示字段保留原文。
        _nav_module = _sanitize_doc_heading_module(module)
        case["_explore_task"] = {
            "operation": operation,
            "target_page": target_page,
            "description": desc,
            "expected_feedback": expected,
            "module": _nav_module,
            "test_method": _build_default_test_method(
                module, operation, desc, negation_hint=expected, expected_feedback=expected,
                source_quote=str(req.get("source_quote") or ""),
            ),
        }

        # ★ 根因修复(2026-08-08): 恢复 P01/N01/B01 三胞胎生成
        #   P0-refactor(§16.2) 切断了 N01/B01 生成路径，导致引擎只有正向用例。
        #   高级测试工程师必须覆盖异常/边界场景，此处恢复 N01/B01 生成。
        all_cases = [case]

        # N01: 异常用例
        _n01 = self._build_error_boundary_case(
            req, rid, module, desc, operation, expected,
            target_page, merged_data, matched_apis,
            scenario_type="error_path",
            risk_level="P1",
            suffix="I02",
            description_suffix="-异常输入校验",
            expected_text="显示错误提示",
        )
        if _n01:
            all_cases.append(_n01)

        # B01: 边界用例
        _b01 = self._build_error_boundary_case(
            req, rid, module, desc, operation, expected,
            target_page, merged_data, matched_apis,
            scenario_type="boundary",
            risk_level="P2",
            suffix="I03",
            description_suffix="-边界值验证",
            expected_text="显示校验提示或正确处理",
        )
        if _b01:
            all_cases.append(_b01)

        # ★ 安全测试(2026-08-08): SQL注入/XSS/CSRF 安全用例
        #   高级测试工程师的必备覆盖维度，对可输入操作自动注入安全测试
        _sec_cases = self._build_security_cases(
            req, rid, module, desc, operation, expected,
            target_page, merged_data, matched_apis,
        )
        all_cases.extend(_sec_cases)

        return all_cases

    # ── 操作类型分类（通用，零业务硬编码）──
    _OP_TYPE_KEYWORDS = {
        "form": [
            "新建", "新增", "添加", "创建", "编辑", "修改", "更新",
            "输入", "填写", "create", "add", "edit", "update", "modify",
            "保存", "提交", "save", "submit",
        ],
        "search": [
            "搜索", "查询", "查找", "检索", "search", "query", "find", "lookup",
        ],
        "view": [
            "查看", "详情", "展示", "浏览", "预览", "view", "detail", "display",
            "show", "preview", "read",
        ],
        "import_op": [
            "导入", "上传", "import", "upload",
        ],
        "export_op": [
            "导出", "下载", "export", "download",
        ],
        "filter_op": [
            "筛选", "过滤", "filter",
        ],
        "delete_op": [
            "删除", "移除", "清除", "delete", "remove", "clear",
        ],
    }

    # ── 各操作类型的 N01/B01 场景描述（通用，跨系统通用）──
    _OP_SCENARIO_TEMPLATES = {
        "form": {
            "error_path": {
                "suffix": "-异常输入校验",
                "expected": "显示错误提示",
                "explore_desc": "输入非法值（空字段、超长文本、特殊字符），验证表单校验错误提示正确显示",
            },
            "boundary": {
                "suffix": "-边界值验证",
                "expected": "显示校验提示或正确处理",
                "explore_desc": "输入边界值（最小/最大长度、零值、负数），验证系统正确处理边界输入",
            },
        },
        "search": {
            "error_path": {
                "suffix": "-异常搜索输入",
                "expected": "系统不崩溃，显示无结果或提示",
                "explore_desc": "输入特殊字符（SQL注入、XSS、超长文本）进行搜索，验证系统不崩溃且正确处理",
            },
            "boundary": {
                "suffix": "-搜索边界值",
                "expected": "系统正常响应，显示合理结果",
                "explore_desc": "测试搜索边界值（空搜索、单字符、超长关键词），验证系统正确处理边界搜索",
            },
        },
        "view": {
            "error_path": {
                "suffix": "-异常查看",
                "expected": "显示错误提示或跳转404",
                "explore_desc": "尝试访问不存在的资源（无效ID、越权数据），验证系统正确拦截并提示",
            },
            "boundary": {
                "suffix": "-查看边界值",
                "expected": "页面正常渲染或显示空状态",
                "explore_desc": "测试查看边界场景（空数据列表、超大ID值），验证系统正确处理边界情况",
            },
        },
        "import_op": {
            "error_path": {
                "suffix": "-异常导入",
                "expected": "显示格式错误提示",
                "explore_desc": "上传无效格式文件（非支持格式、损坏文件），验证系统正确校验并提示错误",
            },
            "boundary": {
                "suffix": "-导入边界值",
                "expected": "显示校验提示或正确处理",
                "explore_desc": "测试导入边界场景（空文件、超大文件、重复数据），验证系统正确处理边界导入",
            },
        },
        "export_op": {
            "error_path": {
                "suffix": "-异常导出",
                "expected": "显示提示或无数据导出",
                "explore_desc": "在无数据或空选择时尝试导出，验证系统正确处理并提示",
            },
            "boundary": {
                "suffix": "-导出边界值",
                "expected": "导出成功或显示合理提示",
                "explore_desc": "测试导出边界场景（全量数据导出、跨页选择），验证系统正确处理大数据量导出",
            },
        },
        "filter_op": {
            "error_path": {
                "suffix": "-异常筛选",
                "expected": "系统不崩溃，显示无结果或提示",
                "explore_desc": "应用无效筛选条件（特殊字符、不存在的值），验证系统不崩溃且正确处理",
            },
            "boundary": {
                "suffix": "-筛选边界值",
                "expected": "系统正常响应",
                "explore_desc": "测试筛选边界场景（同时应用所有筛选条件、清空筛选），验证系统正确处理",
            },
        },
        "delete_op": {
            "error_path": {
                "suffix": "-异常删除",
                "expected": "显示错误提示或确认拦截",
                "explore_desc": "尝试删除不存在/已删除/受保护的数据，验证系统正确拦截并提示",
            },
            "boundary": {
                "suffix": "-删除边界值",
                "expected": "系统正确处理或提示",
                "explore_desc": "测试删除边界场景（批量删除、级联删除确认），验证系统正确处理边界删除",
            },
        },
    }

    @classmethod
    def _classify_operation_type(cls, operation: str) -> str | None:
        """★ 通用操作类型分类器（委托给独立函数 _classify_op_type）。"""
        return _classify_op_type(operation)

    def _build_error_boundary_case(
        self,
        req: dict,
        rid: str,
        module: str,
        desc: str,
        operation: str,
        expected: str,
        target_page: str,
        merged_data: dict,
        matched_apis: list,
        scenario_type: str,
        risk_level: str,
        suffix: str,
        description_suffix: str,
        expected_text: str,
    ) -> dict | None:
        """★ 根因修复 v2(2026-08-08): 生成一条异常/边界用例。

        为每个需求额外生成 N01(异常输入)和 B01(边界值)用例，
        恢复 P0-refactor(§16.2) 切断的三胞胎生成路径。

        v2 扩展: 覆盖 form/search/view/import/export/filter/delete 七类操作，
        不再局限于表单类操作。纯规则驱动，零业务硬编码，跨系统/跨行业通用。
        """
        # ★ v2: 分类操作类型，非表单类操作也生成 N01/B01
        _op_type = self._classify_operation_type(operation)
        if _op_type is None:
            return None

        # 获取该操作类型的场景模板
        _templates = self._OP_SCENARIO_TEMPLATES.get(_op_type, {})
        _scenario_key = "error_path" if scenario_type == "error_path" else "boundary"
        _tmpl = _templates.get(_scenario_key, {})

        _case_id = f"TC-{rid}-{suffix}"
        _op_brief = (operation or desc or "未知操作")[:20]
        _suffix_text = _tmpl.get("suffix", description_suffix)
        _case_desc = f"{_op_brief}{_suffix_text}"
        _expected = _tmpl.get("expected", expected_text)

        # 期望强度不得高于需求声明 —— 判据在落库前唯一咽喉
        #   （`_check_guard.overstated_expectation_reason`），所有生成器同受约束；
        #   生成端不得另推一次，否则同一语义会出现第二个判定入口。

        # 错误/边界检查：验证错误提示元素存在
        # ★ RC-AE(2026-09-25): 选择器改引全引擎单一事实源（原本地 7 项词表漏
        #   `.flash-danger`/`[data-testid*='error']` 等框架无关形态 → 恒 0 命中假 FAIL）。
        # ★ 根因修复(2026-09-28): **析取式**期望（"显示错误提示或正确处理"/
        #   "显示错误提示或跳转404"/"系统不崩溃，显示无结果或提示"）不要求错误提示
        #   必须出现——系统"正确处理"也是符合期望的结果。原实现对析取式模板仍断言
        #   element_exists(ERROR_SELECTOR)，SUT 容错（删除不存在数据→静默重定向、
        #   访问无效ID→正常列表）时无错误提示 → 恒假 FAIL（实测 REQ-005/015/012/007-I02、
        #   REQ-016/021-I03）。析取式 → 容错断言（主内容渲染 + 无致命错误）；
        #   非析取式（"显示错误提示"/"显示格式错误提示"）才断言错误提示存在。
        from src.pipeline.step3._rule_extractor_keywords import (
            expects_error_feedback as _expects_error,
            is_disjunctive_text as _is_disj,
        )

        # ★ 根因修复(2026-10-27)：异常/边界用例的**交互必须按操作类型装齐**。
        #   根因: 本函数此前只实现了"表单录入"一种交互 —— `build_form_data_actions`
        #     对检索/筛选/导出等操作返回空 → 用例动作恒为 [导航, 截图]，而断言却
        #     声明"系统不崩溃/正确处理"这类**交互后果** → 交互从未发生，"断言"只能
        #     证明页面能打开（弱断言假 PASS 温床）。
        #   修复: 录入段按**操作类型**取 —— 表单走字段规格 SSOT，检索/筛选/导入/导出
        #     走 `build_operation_entry_action`；随后「导航之后」的交互序列统一由
        #     `build_interaction_actions` 按 `mutation_flow_kind` 展开（与
        #     `_fallback_cases` / `_rule_extractor_actions` 同一实现，杜绝第三套编排）。
        #     删除/查看/访问控制不在此装交互：删除形态由 `complete_submit_flow`
        #     （`align_delete_flow_shape`）归一，查看/访问控制本就无录入面。
        #   通用性: 判据只有"操作类型"（动词→类别 SSOT）与"是否存在录入动作"，
        #     零业务硬编码。
        from src.pipeline.step3._rule_extractor_buttons import (
            build_interaction_actions,
            build_operation_entry_action,
            infer_confirm_hint,
            mutation_flow_kind,
        )

        _data_actions: list[dict] = []
        if _op_type == "form":
            try:
                from src.pipeline.step3._rule_extractor_fields import (
                    build_form_data_actions,
                )

                _data_actions = list(
                    build_form_data_actions(
                        operation,
                        module=module,
                        invalid=(scenario_type == "error_path"),
                        project=str(req.get("project") or ""),
                    )
                    or []
                )
            except Exception as _fe:  # noqa: BLE001
                _log.debug("[N01/B01] 字段规格组装失败(无录入动作): %s", str(_fe)[:80])
        else:
            _entry_action = build_operation_entry_action(
                _op_type, error_case=(scenario_type == "error_path")
            )
            if _entry_action:
                _data_actions.append(_entry_action)

        # 交互形态（SSOT `mutation_flow_kind`）：dialog_form 需先点开录入面再录入，
        #   其余形态直接录入并按操作控件提交。
        _flow_kind = mutation_flow_kind(operation, bool(_data_actions))
        if _flow_kind == "dialog_form":
            _entry_hint = (
                "编辑,修改,更新"
                if any(
                    _k in f"{operation} {_expected} {module}"
                    for _k in ("编辑", "修改", "更新")
                )
                else "新增,新建,添加,创建"
            )
            _confirm_hint = infer_confirm_hint(operation) or "保存,提交,确定,确认"
        else:
            _entry_hint = infer_confirm_hint(operation) or "保存,提交,确定,确认"
            _confirm_hint = ""
        # 非表单操作的"操作控件"由操作类型给出通用文本候选（与 `_fallback_cases`
        #   的 `submit_text` 同一语义层：控件文本候选，逗号并列多候选）。
        _OP_CONTROL_HINTS = {
            "search": "查询,搜索,检索",
            "filter_op": "筛选,过滤",
            "import_op": "导入,上传",
            "export_op": "导出,下载",
        }
        if _op_type in _OP_CONTROL_HINTS:
            _entry_hint = _OP_CONTROL_HINTS[_op_type]

        _interaction: list[dict] = []
        # form 且组装不出录入段 → 不臆造交互（"点保存"落在列表页只是空点击，
        #   属引擎动作缺口而非被测激励），与既有降级行为一致。
        if _op_type in ("search", "filter_op", "import_op", "export_op") or (
            _op_type == "form" and _data_actions
        ):
            _interaction = build_interaction_actions(
                operation, _data_actions or None, _entry_hint, _confirm_hint
            )

        # 错误条断言只有"用例自身完成一次**非法**录入并提交"才可达 ——
        #   以"能否组装出录入动作 + 期望是否单一负向"这一结构性事实决定，不靠关键词猜。
        # 错误条断言的可达性判据 = **期望自身声明了单一负向结果**（极性 SSOT）
        #   + 存在录入动作（非法值可注入）。
        #   ★ 根因修复(2026-10-27 二次)：原判据只用"是否组装出录入动作"——
        #     检索/筛选/导入拿到录入动作后，**容错型**期望（"系统正常响应/显示合理结果"）
        #     也被判成"必须出现错误提示" → 正常页面上恒无错误条 → 结构性假 SKIP/FAIL
        #     （实测边界值 TC-REQ-001/013/023/026/027/028/059/060-I03 由 PASS 退化）。
        #     期望极性只有 `_rule_extractor_keywords` 一个事实源，此处不得自造判据。
        _expect_error_bar = (
            _expects_error(_expected)
            and not _is_disj(_expected)
            and bool(_data_actions)
        )
        if _expect_error_bar:
            _error_checks = [
                self._build_check(
                    "element_exists",
                    _ERROR_SELECTOR_CSV,
                    "存在",
                    "错误/校验提示元素存在",
                    precision="fuzzy",
                ),
                self._build_check("no_fatal_error", "页面", "无错误", "系统无致命错误"),
            ]
        else:
            _error_checks = [
                self._build_check(
                    "element_exists",
                    "main, [role='main'], .content, #content",
                    "存在",
                    "异常/边界输入下页面主体仍正常渲染（容错不崩溃）",
                    precision="fuzzy",
                ),
                self._build_check("no_fatal_error", "页面", "无错误", "系统无致命错误"),
            ]

        # ★ 多轨验证(2026-08-08): N01/B01 也注入 API/DB checks
        _n01_apis = matched_apis or []
        if _n01_apis:
            _api = _n01_apis[0]
            _path = _api.get("path", "") or _api.get("url", "")
            _method = _api.get("method", "GET")
            if _path:
                _error_checks.append(
                    self._build_check(
                        "api_call_count_check",
                        f"{_method} {_path}",
                        "1",
                        f"API {_method} {_path} 被调用（异常场景）",
                        track="api", precision="exact",
                        operator=">=",
                    )
                )

        # 动态计算 required_tracks
        _n01_tracks = set()
        for c in _error_checks:
            _t = c.get("track", "ui")
            if _t in ("ui", "api", "db"):
                _n01_tracks.add(_t)
        if not _n01_tracks:
            _n01_tracks = {"ui"}
        _n01_required_tracks = sorted(_n01_tracks)

        # 异常/边界用例的动作 = 导航 + 该操作自身的交互序列 + 截图。
        #   交互已由上面的 SSOT 装齐；删除型与查看/访问控制不在此装交互
        #   （删除形态由 `complete_submit_flow` 归一，查看/访问控制无录入面）。
        _self_exec_actions: list[dict] = [self._nav(target_page)]
        _self_exec_actions.extend(_interaction)
        _self_exec_actions.append(self._screenshot())

        _case = self._build_one(
            _case_id,
            req,
            module,
            _case_desc,
            operation,
            _expected,
            scenario_type,
            risk_level,
            "功能",
            "playwright",
            "exploratory",
            _n01_required_tracks,
            _self_exec_actions,
            _error_checks,
            _case_desc,
        )
        _case["_exploratory"] = True
        _case["low_confidence"] = False
        _nav_module = _sanitize_doc_heading_module(module)
        _case["_explore_task"] = {
            "operation": f"{operation}（{_op_type}:{scenario_type}）",
            "target_page": target_page,
            "description": _tmpl.get("explore_desc", _case_desc),
            "expected_feedback": _expected,
            "module": _nav_module,
            "test_method": _build_default_test_method(
                module, operation, _case_desc, _op_type, expected_feedback=_expected,
                source_quote=str(req.get("source_quote") or ""),
            ),
        }
        return _case

    # ── 安全测试 Payload 库（通用，零业务硬编码；唯一定义在模块级，此处仅别名）──
    _SQL_INJECTION_PAYLOADS = SQL_INJECTION_PAYLOADS

    _XSS_PAYLOADS = XSS_PAYLOADS

    _SECURITY_CHECK_SELECTORS = [
        _ERROR_SELECTOR_CSV,
        'body',
    ]

    def _build_security_probe_actions(
        self, nav_target: str, payload_values: list[str], target_page: str = ""
    ) -> list[dict]:
        """构造安全探测的**真实注入动作序列**（SQLi/XSS/CSRF 共用单一实现）。

        根因: `_build_security_cases` 此前只发 `navigate + screenshot` —— payload
        仅存在于 `_explore_task`（探索引导），**静态动作层从未把 payload 打进任何
        输入框**。后果链（实测 TC-REQ-011/052-I04 全中）:
          ① 动作无交互 → `_check_guard._is_pure_verification_case` 判真
          ② → `pop("_explore_task")`，payload 引导随之丢失
          ③ → 用例退化为"只导航+截图"，注入从未发生
          ④ → `no_error` 断言因 `expected_feedback` 含"错误"被极性反转为
             "应提示 <别处需求> 的错误文案" → 页面正常（防御生效）反被判 FAIL
          ⑤ → AI 归因 PRODUCT_BUG，产出假 Bug 报告。
        修法: 注入动作必须在**动作层**真实发生——填 payload 到录入/搜索输入框 →
        提交 → 页面反应由 checks 判定。语义只在此处构造一次，三条安全用例复用。

        通用性: 输入点候选与提交按钮文案取自通用控件词表，零业务硬编码；
        payload 由调用方传入（与断言同源于本类的 payload 库）。
        """
        from src.pipeline.step3._trigger_actions import (
            build_field_validation_trigger as _bvt,
        )

        # 首选：复用"进入录入表单 → 填值 → 提交"的既有单一构造器（含控件候选），
        # 把第一个 fill 的值替换为注入 payload —— 避免另造一套入口/提交动作。
        _trigger = _bvt("", "输入", " ".join(payload_values))
        _pay = str(payload_values[0]) if payload_values else "' OR '1'='1"
        _injected: list[dict] = []
        _filled = False
        for _a in _trigger:
            if not isinstance(_a, dict):
                continue
            if not _filled and _a.get("type") == "fill":
                _injected.append({**_a, "value": _pay})
                _filled = True
                continue
            _injected.append(dict(_a))
        if not _filled:
            # 兜底：通用输入框候选（跨系统/行业通用控件词表）
            _injected = [
                {"type": "fill", "target_hint": "名称/姓名输入框, 搜索框, 查询", "value": _pay},
                {"type": "click", "target_hint": "保存,提交,确定,查询,搜索", "in_dialog": False},
            ]
        return (
            [{"type": "navigate", "target_hint": nav_target or target_page or "/"}]
            + _injected
            + [{"type": "screenshot", "target_hint": "注入后截图"}]
        )

    def _build_security_cases(
        self,
        req: dict,
        rid: str,
        module: str,
        desc: str,
        operation: str,
        expected: str,
        target_page: str,
        merged_data: dict,
        matched_apis: list,
    ) -> list[dict]:
        """★ 安全测试用例生成(2026-08-08): SQL注入/XSS/CSRF。

        高级测试工程师的必备覆盖维度。对可输入操作（form/search/filter）
        自动注入安全测试用例，验证系统对恶意输入的防护能力。

        通用性: 纯规则驱动，Payload 库为通用安全测试标准载荷，
        不依赖任何业务知识，跨系统/跨行业通用。
        """
        _op_type = self._classify_operation_type(operation)
        # 仅对可输入操作生成安全用例（form/search/filter）
        if _op_type not in ("form", "search", "filter_op"):
            return []

        _sec_cases = []
        _base_suffix = 4  # 从 I04 开始（I01=P01, I02=N01, I03=B01）
        _op_brief = (operation or desc or "未知操作")[:20]

        # ── S01: SQL 注入测试 ──
        _sqli_case_id = f"TC-{rid}-I{_base_suffix:02d}"
        _sqli_desc = f"{_op_brief}-SQL注入防护"
        # ★ 根因修复(2026-09-28): 安全防御成功的可观察证据 = **页面无错误提示**且
        #   仍正常渲染——不是"应看到错误提示元素"。原 checks 语义反了
        #   (element_exists(ERROR_SELECTOR)="注入后应出现错误条" → SUT 正确防御
        #   无错误提示 → 恒假 FAIL，实测 TC-REQ-002/003/007/013/014/017-I04)。
        #   正确断言组合: 页面主体仍渲染(element_exists body=1) + no_error(无致命错误/
        #   无数据库错误泄露)。与命题（防御生效）同向，跨系统/行业通用。
        _sqli_checks = [
            self._build_check(
                "element_exists",
                "body",
                "存在",
                "注入后页面主体仍正常渲染（未崩溃）",
                precision="fuzzy",
            ),
            self._build_check("no_fatal_error", "页面", "无错误", "系统无致命错误（SQL注入未导致崩溃/泄露）"),
        ]

        # 动态计算 required_tracks
        _sqli_tracks = set()
        for c in _sqli_checks:
            _t = c.get("track", "ui")
            if _t in ("ui", "api", "db"):
                _sqli_tracks.add(_t)
        if not _sqli_tracks:
            _sqli_tracks = {"ui"}

        _sqli_case = self._build_one(
            _sqli_case_id, req, module, _sqli_desc, operation,
            "系统不崩溃，SQL注入被正确防御",
            "security", "P1", "安全",
            "playwright", "exploratory",
            sorted(_sqli_tracks),
            self._build_security_probe_actions(
                target_page, self._SQL_INJECTION_PAYLOADS
            ),
            _sqli_checks, "SQL注入防护测试",
        )
        _sqli_case["_exploratory"] = True
        _sqli_case["low_confidence"] = False
        _sqli_case["_explore_task"] = {
            "operation": f"{operation}（SQL注入安全测试）",
            "target_page": target_page,
            "description": (
                f"在输入框中依次注入SQL注入Payload: {', '.join(self._SQL_INJECTION_PAYLOADS[:4])}，"
                f"验证系统正确防御SQL注入攻击，不崩溃、不泄露数据库错误信息"
            ),
            "expected_feedback": "系统不崩溃，SQL注入被正确防御，无数据库错误泄露",
            "module": _sanitize_doc_heading_module(module),
            "test_method": _build_default_test_method(module, operation, _sqli_desc, _op_type),
            "security_payloads": self._SQL_INJECTION_PAYLOADS[:],
        }
        _sec_cases.append(_sqli_case)

        # ── S02: XSS 跨站脚本测试 ──
        _xss_case_id = f"TC-{rid}-I{_base_suffix + 1:02d}"
        _xss_desc = f"{_op_brief}-XSS防护"
        # ★ 根因修复(2026-09-28): 与 SQLi 同根因——防御成功 = 无错误/未崩溃，
        #   不断言"错误提示存在"（见 S01 根因注释，同一语义一次性判定）。
        _xss_checks = [
            self._build_check(
                "element_exists",
                "body",
                "存在",
                "XSS注入后页面主体仍正常渲染（未崩溃）",
                precision="fuzzy",
            ),
            self._build_check(
                "no_fatal_error", "页面", "无错误", "系统无致命错误（XSS注入未导致崩溃）"
            ),
        ]

        _xss_tracks = set()
        for c in _xss_checks:
            _t = c.get("track", "ui")
            if _t in ("ui", "api", "db"):
                _xss_tracks.add(_t)
        if not _xss_tracks:
            _xss_tracks = {"ui"}

        _xss_case = self._build_one(
            _xss_case_id, req, module, _xss_desc, operation,
            "系统不崩溃，XSS脚本被正确转义",
            "security", "P1", "安全",
            "playwright", "exploratory",
            sorted(_xss_tracks),
            self._build_security_probe_actions(target_page, self._XSS_PAYLOADS),
            _xss_checks, "XSS防护测试",
        )
        _xss_case["_exploratory"] = True
        _xss_case["low_confidence"] = False
        _xss_case["_explore_task"] = {
            "operation": f"{operation}（XSS安全测试）",
            "target_page": target_page,
            "description": (
                f"在输入框中依次注入XSS Payload: {', '.join(self._XSS_PAYLOADS[:3])}，"
                f"验证系统正确转义HTML/JS代码，不执行恶意脚本"
            ),
            "expected_feedback": "系统不崩溃，XSS脚本被正确转义，无弹窗或脚本执行",
            "module": _sanitize_doc_heading_module(module),
            "test_method": _build_default_test_method(module, operation, _xss_desc, _op_type),
            "security_payloads": self._XSS_PAYLOADS[:],
            "check_no_alert": True,  # 验证没有 alert 弹窗
        }
        _sec_cases.append(_xss_case)

        # ── S03: CSRF 防护测试（仅表单类操作）──
        if _op_type == "form":
            _csrf_case_id = f"TC-{rid}-I{_base_suffix + 2:02d}"
            _csrf_desc = f"{_op_brief}-CSRF防护"
            # ★ 根因修复(2026-10-15): CSRF 用例断言方向错误 → 恒假 FAIL。
            #   根因: 原断言 `element_exists(CSRF token 选择器)` 主张"表单**必须**含
            #     CSRF token"——但该主张**不是需求派生的**（需求集里从无 CSRF 条目），
            #     且 CSRF token 是否存在于**页面契约**中，本就由需求决定而非通用规则。
            #     实测 fullcov_sut：58 条需求 0 条提 CSRF、模板/蓝图 0 处 csrf，
            #     而全部 9 条 -I06 都**只**挂在 element_exists，同时承载真实需求的
            #     semantic_check 一律 ✓ → 单条用例内部自相矛盾（需求已满足却判 FAIL）。
            #   为何是缺陷而非靶子问题: CSRF 缺席这一**安全事实**已由
            #     `security_scanner._check_csrf` 以 SecurityFinding(high/CSRF-001)
            #     正确捕获并进入 security 报告；此处再按"存在性"硬断言，等于把同一条
            #     安全观察**重复计分 9 次**到功能通过率，且用的是需求从未提出的义务。
            #   修复: 与同函数内 SQL 注入用例（S01，2026-09-28 同型修复）对齐——
            #     安全用例应断言**防御生效的可观察证据**（页面仍正常渲染 + 无致命错误），
            #     而非断言"防护机制的具体实现形态存在"。同向命题，跨系统/行业通用。
            #   CSRF 缺失仍被如实报告：由 security 门禁的 CSRF-001 findings 承担。
            _csrf_checks = [
                self._build_check(
                    "element_exists",
                    "body",
                    "存在",
                    "提交后页面主体仍正常渲染（未崩溃）",
                    precision="fuzzy",
                ),
                self._build_check(
                    "no_fatal_error", "页面", "无错误", "系统无致命错误（未因缺失防护而崩溃/泄露）"
                ),
            ]

            _csrf_tracks = set()
            for c in _csrf_checks:
                _t = c.get("track", "ui")
                if _t in ("ui", "api", "db"):
                    _csrf_tracks.add(_t)
            if not _csrf_tracks:
                _csrf_tracks = {"ui"}

            _csrf_case = self._build_one(
                _csrf_case_id, req, module, _csrf_desc, operation,
                "系统在缺少 CSRF token 的表单提交下不崩溃、无致命错误",
                "security", "P1", "安全",
                "playwright", "exploratory",
                sorted(_csrf_tracks),
                self._build_security_probe_actions(
                    target_page, self._SQL_INJECTION_PAYLOADS[:2]
                ),
                _csrf_checks, "CSRF防护测试",
            )
            _csrf_case["_exploratory"] = True
            _csrf_case["low_confidence"] = False
            _csrf_case["_explore_task"] = {
                "operation": f"{operation}（CSRF安全测试）",
                "target_page": target_page,
                "description": (
                    "提交表单并观察系统是否崩溃或泄露，验证系统在缺失 CSRF token 时"
                    "仍能安全处理请求（CSRF token 是否存在的实现细节由安全门禁另行报告）"
                ),
                "expected_feedback": "系统不崩溃、无致命错误",
                "module": _sanitize_doc_heading_module(module),
                "test_method": _build_default_test_method(module, operation, _csrf_desc, _op_type),
            }
            _sec_cases.append(_csrf_case)

        return _sec_cases

    @staticmethod
    def _infer_fill_target(
        operation: str, target_page: str = "", merged_data: dict = None
    ) -> str:
        """从 operation 推断 fill target_hint。

        优先级：DOM placeholder > RAG API 字段名 > operation 语义 > 通用兜底

        ★ Fix49 (2026-07-09): 增加 target_page + merged_data 参数，优先用 DOM/RAG 数据。
          契约: §4.1 规则优先 — 有 DOM/RAG 数据就应该用，不烧 token 问 AI。
        """
        import re

        op = (operation or "").lower()
        merged_data = merged_data or {}

        # ★ Fix49-1: 优先从 DOM 快照匹配实际 placeholder
        if target_page:
            try:
                from src.pipeline.step3._rule_extractor import (
                    _load_dom_elements_for_page,
                )

                dom_elements = _load_dom_elements_for_page(target_page)
                inputs = dom_elements.get("input_placeholders", [])
                if inputs:
                    # 查询类操作：优先匹配搜索类 placeholder
                    if any(k in op for k in ["查询", "搜索", "筛选"]):
                        for inp in inputs:
                            if any(k in inp for k in ["搜索", "查询", "search"]):
                                return inp
                    # 无关键词匹配，返回第一个 placeholder（最可能的主输入框）
                    return inputs[0]
            except Exception as _e:
                _log.debug("DOM placeholder 匹配失败: %s", _e, exc_info=True)

        # ★ Fix49-2: 从 RAG API body_params 字段名推断
        for api in merged_data.get("apis", []):
            body_params = api.get("body_params", "")
            if body_params:
                fields = re.findall(r"(\w+)\s*\([^)]*\)", body_params)
                if fields:
                    return f"{fields[0].lower()} 输入框"

        # 回退 operation 语义推断
        if any(k in op for k in ["查询", "搜索", "筛选"]):
            return "查询输入框"
        if any(k in op for k in ["名称", "名字", "标题"]):
            return "名称输入框"
        if any(k in op for k in ["编号", "代码", "code", "编码"]):
            return "编号输入框"
        if any(k in op for k in ["密码", "口令"]):
            return "密码输入框"
        if any(k in op for k in ["邮箱", "邮件", "email"]):
            return "邮箱输入框"
        if any(k in op for k in ["手机", "电话", "phone"]):
            return "手机号输入框"
        if any(k in op for k in ["日期", "时间", "date"]):
            return "日期输入框"
        if any(k in op for k in ["数量", "金额", "价格", "number"]):
            return "数量输入框"
        return "主输入框"

    @staticmethod
    def _infer_fill_value(operation: str, merged_data: dict = None) -> str:
        """从 RAG API body_params 提取正向测试值。

        ★ Fix49 (2026-07-09): 优先用 RAG API 字段名生成 value，回退原硬编码值。
        ★ §13.3 通用修复(2026-07-24): 用 generate_value_from_type 替代 test_ 硬编码前缀。
           根因: f"test_{field}" 违反 §13.3 禁止硬编码输入值。
           方案: 从 body_params 解析 (字段名, 类型)，用类型生成合规值。
           覆盖性: 与 _rule_extractor._infer_fill_target_and_value 同源修复。
        """
        import re

        merged_data = merged_data or {}
        for api in merged_data.get("apis", []):
            body_params = api.get("body_params", "")
            if body_params:
                # ★ 解析 "name(type)" 或 "name" 格式，兼容 yapi_structured 和 rag_chunks
                _pairs = re.findall(r"(\w+)\s*(?:\(([^)]*)\))?", body_params)
                if _pairs:
                    _fname = _pairs[0][0]
                    _ftype = _pairs[0][1] if len(_pairs[0]) > 1 else ""
                    try:
                        from src.pipeline.step3.value_generator import (
                            generate_value_from_type,
                        )

                        return generate_value_from_type(_ftype or "string", _fname)
                    except Exception as _ve:
                        _log.debug(
                            "[base] value_generator不可用(用兜底): %s", str(_ve)[:50]
                        )
                        return "测试数据"

        # ★ §13.3 通用修复(2026-07-24 续): 回退分支也走 value_generator，禁硬编码
        #   根因: 回退分支仍硬编码 Test@123456/test@example.com 等，违反 §13.3
        #   方案: 从 operation 语义推断 field_type，调 generate_value_from_type
        #   通用性: value_generator 内置密码/邮箱/手机/日期/金额等语义，跨系统/行业通用
        #   对称性: 与 _infer_error_value 回退分支、_rule_extractor._infer_fill_target_and_value 同源
        op = (operation or "").lower()
        _infer_type = "string"
        if any(k in op for k in ["数量", "金额", "价格", "number"]):
            _infer_type = "integer"
        elif any(k in op for k in ["是否", "启用", "禁用", "bool"]):
            _infer_type = "boolean"
        try:
            from src.pipeline.step3.value_generator import generate_value_from_type

            return generate_value_from_type(_infer_type, operation or "")
        except Exception:
            return "测试数据"

    @staticmethod
    def _infer_error_value(operation: str, merged_data: dict = None) -> str:
        """异常用例的通用非法值（类型驱动，走 value_generator 统一管道）。

        ★ §13.3 通用修复(2026-07-24 续): 与 _infer_fill_value 对称，禁硬编码。
          根因: 原实现硬编码 abc123/9999-99-99/!@#，与 value_generator 管道脱节。
          方案: 优先从 merged_data 解析 RAG body_params 类型；无 RAG 时用 operation
                语义推断 field_type；调 generate_invalid_value_from_type 生成非法值。
          通用性: operation→类型映射 + 类型→非法值映射，跨系统/行业通用。
          对称性: 与 _infer_fill_value、_rule_extractor._infer_fill_target_and_value 同源。
        """
        import re

        # 1. 优先从 RAG body_params 解析第一个字段的 (name, type)
        merged_data = merged_data or {}
        for api in merged_data.get("apis", []):
            body_params = api.get("body_params", "")
            if body_params:
                _pairs = re.findall(r"(\w+)\s*(?:\(([^)]*)\))?", body_params)
                if _pairs:
                    _fname = _pairs[0][0]
                    _ftype = _pairs[0][1] if len(_pairs[0]) > 1 else ""
                    try:
                        from src.pipeline.step3.value_generator import (
                            generate_invalid_value_from_type,
                        )

                        return generate_invalid_value_from_type(
                            _ftype or "string", _fname
                        )
                    except Exception as _ve3:
                        _log.debug(
                            "[base] value_generator不可用(break): %s", str(_ve3)[:50]
                        )
                        break
        # 2. 无 RAG：用 operation 语义推断 field_type，调 generate_invalid_value_from_type
        #    ★ 编号/代码是 string 标识符，不推断为 integer（非法值应为特殊字符非非数字）
        op = (operation or "").lower()
        _infer_type = "string"
        if any(k in op for k in ["数量", "金额", "价格", "number"]):
            _infer_type = "integer"
        elif any(k in op for k in ["是否", "启用", "禁用", "bool"]):
            _infer_type = "boolean"
        try:
            from src.pipeline.step3.value_generator import (
                generate_invalid_value_from_type,
            )

            return generate_invalid_value_from_type(_infer_type, operation or "")
        except Exception:
            return "!@#"

    @staticmethod
    def _infer_success_hint(operation: str) -> str:
        """★ v24 根因K-1 修复: 基于 operation 推断真实业务成功反馈（多候选逗号分隔）。

        替代原通用 element_exists + main selector 假断言。
        Step4 text_contains_check 支持逗号分隔多值匹配，任一命中即真 PASS。

        通用性:
          - 跨系统: 不同系统成功提示可能不同（"新增成功"vs"添加成功"vs"创建成功"），
                    用逗号分隔多候选，Step4 任一命中即 PASS。
          - 跨行业: 不预设业务术语，纯 operation 语义推断。
          - 跨页面: 适用于所有页面操作。

        设计原则:
          1. 保证返回值非空（避免 Step3 走 description 分支生成更差用例）
          2. 多候选覆盖常见变体（"新增成功,添加成功,创建成功,保存成功"）
          3. 查询类操作返回"页面显示查询结果"，调用方走 element_exists 验证结果列表

        合规: AGENTS.md §10.8 禁止治标不治本 — 真实业务反馈替代模板化文本。
        覆盖: 所有规则文件的 _build_checks 方法都调用此方法。
        """
        # ★ 重复定义清理(2026-10-14)：本函数原与 `step2/breakdown_rules/
        #   numbered_list.py::_infer_success_hint` 逐字相同（两份词表 = 漂移风险
        #   + 极性盲区：都会给"登录失败…"配上"登录成功,欢迎"）。
        #   现统一委托唯一实现 `_rule_extractor_keywords.infer_expected_feedback`。
        from src.pipeline.step3._rule_extractor_keywords import infer_expected_feedback

        return infer_expected_feedback(operation)

    def _build_success_check(
        self, operation: str, req: dict[str, Any] = None
    ) -> dict[str, Any]:
        """★ v25 根因Z1 修复(2026-07-21): 断言来自 req.expected_feedback，不再硬编码。

        v24 错误反思:
          v24 的 _infer_success_hint 是硬编码字典，按 operation 关键词返回固定文本。
          导致张冠李戴：点击"新建按钮"operation含"新建"→断言"新增成功"，
          但需求期望"系统进入辅材新建流程页面"。

        新策略（断言来自需求文档 §13.3）:
          1. 优先用 req.expected_feedback 作为断言依据
          2. 根据 expected_feedback 内容分类断言类型:
             - 含"成功/提示/完成" → text_contains_check + expected_feedback
             - 含"进入/页面/流程/跳转/打开/弹窗" → element_exists（验证新页面表单元素）
             - 含"只读/不可修改/不可切换/禁用" → element_exists（验证字段只读状态）
             - 含"显示/字段/展示" → element_exists（验证字段存在）
             - 默认 → text_contains_check + expected_feedback
          3. expected_feedback 为空时降级到 _infer_success_hint（兜底，向后兼容）
          4. 标记 evidence_source = "requirement"

        通用性: 适用于所有系统/行业/页面，断言来自需求文档而非硬编码。
        覆盖性: 影响所有21处调用 _build_success_check 的规则。
        合规: AGENTS.md §10.8 禁止治标不治本 + §13.3 断言来源=设计+需求。
        """
        expected_feedback = ""
        if req:
            expected_feedback = (
                req.get("expected_feedback") or req.get("expected") or ""
            ).strip()

        _SUCCESS_SELECTOR = '[role="status"], .success, .message, .toast, .notification, .ant-message-success, .ant-message-notice-success, .ant-notification-notice-success, .ant-alert-success, .el-message--success, .el-notification--success, .el-alert--success, .alert-success'
        _FORM_SELECTOR = "form, [role='form'], input:visible, textarea:visible, select:visible, .form-item, .ant-form-item"
        _READ_ONLY_SELECTOR = "[readonly], input[readonly], select[disabled], textarea[readonly], [aria-readonly='true']"

        # 如果 expected_feedback 为空，降级到 _infer_success_hint（兜底）
        if not expected_feedback:
            success_hints = self._infer_success_hint(operation)
            if success_hints == "页面显示查询结果":
                return self._build_check(
                    "element_exists_at",
                    "table, .table, .list, .result-list, [role='grid']",
                    "存在",
                    "页面显示查询结果列表",
                )
            return self._build_check(
                "text_contains_check",
                _SUCCESS_SELECTOR,
                f"页面显示成功提示({success_hints})",
                "验证业务操作成功反馈",
                check_expected=success_hints,
                operator="contains",
                precision="fuzzy",
            )

        # 根据 expected_feedback 内容分类断言（断言方向对齐需求文档）
        # 1. 含"进入/页面/流程/跳转/打开/弹窗" → 验证新页面表单元素存在
        if any(
            k in expected_feedback
            for k in ["进入", "页面", "流程", "跳转", "打开", "弹窗", "对话框", "模态"]
        ):
            check = self._build_check(
                "element_exists_at",
                _FORM_SELECTOR,
                "存在",
                f"验证页面跳转: {expected_feedback[:50]}",
                precision="fuzzy",
            )
            check["evidence_source"] = "requirement"
            return check

        # 2. 含"只读/不可修改/不可切换/禁用" → 验证字段只读状态
        if any(
            k in expected_feedback
            for k in [
                "只读",
                "不可修改",
                "不可切换",
                "不可编辑",
                "禁用",
                "disabled",
                "不可更改",
            ]
        ):
            check = self._build_check(
                "element_exists_at",
                _READ_ONLY_SELECTOR,
                "存在",
                f"验证字段只读: {expected_feedback[:50]}",
                precision="fuzzy",
            )
            check["evidence_source"] = "requirement"
            return check

        # 3. 含"显示/字段/展示/呈现" → 验证字段存在
        if any(
            k in expected_feedback for k in ["显示", "字段", "展示", "呈现", "包含"]
        ):
            check = self._build_check(
                "element_exists_at",
                _FORM_SELECTOR,
                "存在",
                f"验证字段显示: {expected_feedback[:50]}",
                precision="fuzzy",
            )
            check["evidence_source"] = "requirement"
            return check

        # 4. 默认 → text_contains_check + expected_feedback 短关键词（根因修复 2026-08-08）
        # ★ 根因修复: expected_feedback 常为长句（如"商品管理、订单处理、供应商管理、库存管理等核心功能正常可用"），
        #   直接作为 check_expected 搜索文本必然失败（页面不存在该完整句子）。
        #   修复: 用 _extract_short_keywords 提取短关键词作为搜索文本，expect 字段保留原文片段用于报告。
        _short_kw = self._extract_short_keywords(expected_feedback)
        _search_text = _short_kw or expected_feedback[:120]
        check = self._build_check(
            "text_contains_check",
            _SUCCESS_SELECTOR,
            _search_text,
            f"验证业务反馈: {expected_feedback[:60]}",
            check_expected=_search_text,
            operator="contains",
            precision="fuzzy",
        )
        check["evidence_source"] = "requirement"
        return check

    def _meta(self) -> dict[str, Any]:
        """返回规则元数据（供 API 查询）。"""
        return {
            "rule_id": self.rule_id,
            "name": self.name,
            "description": self.description,
            "control_types": self.control_types,
            "priority": self.priority,
            "version": self.version,
        }

    # ── check 构建（新格式，对齐 Step4 verification_plan_builder 期望）──
    # ★ 旧格式 {"check": "error_hint", "target": "页面", "expected": "...", "description": "..."}
    #   缺少 track 字段，Step4 _track_from_checks 按 track 筛选时会完全忽略，断言被静默跳过。
    # ★ 新格式 {"track": "ui", "precision": "exact", "check": {"type": "...", "selector": "..."}, "expect": "..."}
    #   Step4 能正确路由到对应执行器。
    # ★ v20 通用修复(2026-07-23): selector 覆盖 W3C ARIA + 主流 UI 框架（Ant Design/ElementUI/Bootstrap）+ 通用语义类
    #   原注释"禁止绑定具体 UI 框架"是对通用性的误解：UI 框架的 CSS 类名是技术通用的（跨行业），
    #   不是业务术语。真正的通用性 = 覆盖所有主流技术栈的并集，而非只用 W3C 标准然后期望系统遵守。
    #   原因: Ant Design 的 .ant-message-error 不会自动加 role="alert" 属性，
    #         ElementUI 的 .el-message--error 同理。只依赖 W3C ARIA 会漏掉大部分错误提示。
    #   原假设"Step4 text_contains fuzzy 路径兜底"不成立: element_exists 类型无 fuzzy 兜底，严格匹配 selector。
    #   通用性: 跨系统（React/Vue/Angular/Native HTML）、跨框架（Ant Design/ElementUI/Bootstrap）、跨行业、跨页面
    #   覆盖性: 影响 case_rules/base.py 的所有子类（input_rule/button_rule/form_rule 等）的 N01/B01/P01 checks
    #   全局观: 修复后 N01（异常）/B01（边界）/P01（成功）的 element_exists 断言能匹配到实际 UI 框架的提示组件
    #   职责边界: Step3 case_rules/base.py 内修复，不越界
    _CHECK_SELECTOR_MAP = {
        # 错误提示：W3C ARIA + Ant/ElementUI/Bootstrap + 通用语义类 + 框架无关错误条
        # ★ RC-AE(2026-09-25): 改引全引擎单一事实源，杜绝第三份词表漂移。
        "error_hint": _ERROR_SELECTOR_CSV,
        # 校验提示：W3C ARIA + 表单校验错误态（含 aria-invalid）
        "validation_hint": _VALIDATION_SELECTOR_CSV,
        # 成功提示：W3C ARIA + Ant/ElementUI/Bootstrap + 通用语义类 + 框架无关成功条
        "success_hint": _SUCCESS_SELECTOR_CSV,
    }

    # ★ 根因修复(2026-09-24) F7: CSS 选择器 vs 人类可读描述 的判别信号。
    #   强信号字符（`[ ] # > * =`）在自然语言描述里几乎不可能出现；
    #   弱信号字符（`. : ,`）需同时含 ASCII 字母才算选择器（防误伤 "2.1 节" 这类章节号）。
    #   通用性: 纯语法形态判据，零业务/行业/页面硬编码。
    _CSS_STRONG_CHARS = ("[", "]", "#", ">", "*", "=")
    _CSS_WEAK_CHARS = (".", ":", ",")

    @classmethod
    def _looks_like_css_selector(cls, text: str) -> bool:
        """target 形如 CSS 选择器？（用于 element_exists 的 selector 取舍）"""
        s = str(text or "").strip()
        if not s:
            return False
        if any(ch in s for ch in cls._CSS_STRONG_CHARS):
            return True
        if any(ch in s for ch in cls._CSS_WEAK_CHARS) and re.search(r"[A-Za-z]", s):
            return True
        return False

    def _build_check(
        self,
        check_kind: str,
        target: str,
        expected: str,
        description: str,
        track: str = "ui",
        precision: str = "exact",
        operator: str = "",
        check_expected: str = "",
    ) -> dict[str, Any]:
        """构建标准 check（新格式）。

        Args:
            check_kind: check 类型字符串（element_exists / no_error / error_hint /
                        validation_hint / success_hint / url_check / text_contains_check）
            target: 元素描述（用于 selector 推断或 description 补充）
            expected: 预期结果文本（用于 expect 字段）
            description: check 描述
            track: ui / api / db
            precision: exact / fuzzy
            operator: ★ v16新增 — check 内部 operator 字段（contains/not_contains/equals等）
            check_expected: ★ v16新增 — check 内部 expected 字段（用于 url_check/text_contains_check）
        """
        # 从 _CHECK_SELECTOR_MAP 获取 selector（error_hint/validation_hint/success_hint）
        if check_kind in self._CHECK_SELECTOR_MAP:
            selector = self._CHECK_SELECTOR_MAP[check_kind]
            check_type = "element_exists"
        elif check_kind == "element_exists":
            # ★ 根因修复(2026-09-24) F7: 尊重调用方传入的具体选择器。
            #   根因: 原实现无条件用通用 `main, [role='main'], .content, #content`
            #     覆盖 target → 调用方传入的具体断言选择器被丢弃，断言退化为
            #     "页面主内容区存在"，任意正常页面恒真 → **结构性假 PASS**
            #     （实测 5 处调用点全部被削弱: base.py N01/B01 错误提示、S02 XSS 提示、
            #      S03 CSRF token、search_rule 搜索输入框）。
            #   通用修复: target 形如 CSS 选择器 → 以其为准（真断言）；否则
            #     （人类描述如"页面内容区域"/"错误页面或提示"）→ 回退通用 main 选择器。
            #   通用性: 判据为选择器语法形态，跨系统/行业/页面成立。
            selector = (
                target
                if self._looks_like_css_selector(target)
                else "main, [role='main'], .content, #content"
            )
            check_type = "element_exists"
        elif check_kind == "element_exists_at":
            # ★ v24 根因K-1 修复: element_exists_at 的 selector 从 target 参数获取
            #   用于 _build_success_check 的查询类操作（验证结果列表存在）
            #   区别于 element_exists（通用 main selector），element_exists_at 用具体 selector
            selector = target
            check_type = "element_exists"
        elif check_kind == "no_error":
            selector = ""
            check_type = "no_error"
        elif check_kind == "no_fatal_error":
            # 防御型语义（注入/容错探针）：只判系统级崩溃/泄露，字段级业务
            # 校验提示属 SUT 正常反馈。结构化 mode 由执行端直接读取。
            selector = ""
            check_type = "no_error"
        elif check_kind == "url_check":
            # url_check: target 是 expected URL/path
            selector = ""
            check_type = "url_check"
        elif check_kind == "text_contains_check":
            # text_contains_check: target 是 selector, check_expected 是期望文本
            selector = target
            check_type = "text_contains_check"
        else:
            selector = target
            check_type = check_kind

        check_obj: dict[str, Any] = {"type": check_type}
        if check_kind == "no_fatal_error":
            check_obj["mode"] = "defense"

        # ★ 多轨修复(2026-08-08): API/DB check 使用正确的字段名
        #   API 轨: url_pattern + expected_count (非 generic "expected"/"selector")
        #   DB 轨:  table + expected_count + where (非 generic "expected"/"selector")
        #   否则 verify_api_ext / verify_db_ext 读不到数据 → 静默假通过

        # ── API 轨 check 类型 ──
        if check_type in ("api_call_count_check", "api_response_body_check",
                          "request_body_check"):
            if selector:
                check_obj["url_pattern"] = selector
            if check_type == "api_call_count_check":
                try:
                    check_obj["expected_count"] = int(expected) if expected else 1
                except (ValueError, TypeError):
                    check_obj["expected_count"] = 1
            elif check_type == "api_response_body_check":
                check_obj["expected_field"] = check_expected or expected or ""
            if operator:
                check_obj["operator"] = operator
            elif "operator" not in check_obj:
                check_obj["operator"] = ">=" if check_type == "api_call_count_check" else "eq"

        # ── DB 轨 check 类型 ──
        elif check_type in ("db_record_count_check", "db_record_exists_check",
                            "db_record_not_exists_check", "db_field_value_check",
                            "db_query_check"):
            if selector:
                check_obj["table"] = selector
            if check_type == "db_record_exists_check":
                check_obj["expected_count"] = 1
                if not operator:
                    check_obj["operator"] = "gte"
            elif check_type == "db_record_not_exists_check":
                check_obj["expected_count"] = 0
                if not operator:
                    check_obj["operator"] = "eq"
            if operator and "operator" not in check_obj:
                check_obj["operator"] = operator

        # ── UI 轨 check 类型（保持原有逻辑）──
        else:
            if selector:
                check_obj["selector"] = selector
            if check_type in ("url_check", "text_contains_check"):
                _check_expected = check_expected or expected or target
                if _check_expected:
                    check_obj["expected"] = _check_expected
                if operator:
                    check_obj["operator"] = operator
                elif "operator" not in check_obj:
                    check_obj["operator"] = "contains"

        return {
            "track": track,
            "precision": precision,
            "check": check_obj,
            "expect": expected or description,
            "description": description,
        }


# ── 从 button_resolver re-import 按钮定位方法（§4.3 单文件≤800行）──
# 零调用方改动：所有 self._method() / BaseCaseRule._method() 调用仍可用
from src.pipeline.step3.case_rules.button_resolver import (
    _infer_submit_text,
    _build_click_actions,
    _match_dropdown_trigger_by_semantic,
    _match_dropdown_item,
    _match_dropdown_item_by_semantic,
    _resolve_button_cross_page,
)

BaseCaseRule._infer_submit_text = staticmethod(_infer_submit_text)
BaseCaseRule._build_click_actions = staticmethod(_build_click_actions)
BaseCaseRule._match_dropdown_trigger_by_semantic = staticmethod(
    _match_dropdown_trigger_by_semantic
)
BaseCaseRule._match_dropdown_item = staticmethod(_match_dropdown_item)
BaseCaseRule._match_dropdown_item_by_semantic = staticmethod(
    _match_dropdown_item_by_semantic
)
BaseCaseRule._resolve_button_cross_page = staticmethod(_resolve_button_cross_page)


# ── 词表唯一源（提升自 src/pipeline/steps/step3/case_generator/_combo_enhancer.py，下游 import 引用，勿重抄）──
# ★ 方案B (ADR-017, 2026-07-23): 场景分类关键词 — 基于click主操作语义
#   根因(§10.9 3层追问):
#     1. 表层: list_order_check失败 — "第0项无法转为数值: ''"
#     2. 中层: combo_enhancer因fill含"搜索"误判为搜索场景,追加list_order_check
#     3. 根因: 场景分类基于所有actions的target_hint,而非click动作(主操作)
#            fill是数据输入前置步骤,click才是主操作
#   修复: 区分触发按钮(打开表单)/提交按钮(提交表单)/搜索按钮(查询数据)
#   通用性(§12.4): 跨系统/跨行业/跨页面 — 任何系统的click都是主操作
#   职责边界: Step3 combo_enhancer内修复,不越界(§13.3)
# 触发按钮: 打开表单/弹窗,不提交数据(新建/编辑/添加/创建/修改/更新)
_TRIGGER_KEYWORDS = ("新建", "新增", "添加", "创建", "编辑", "修改", "更新")


# ── 词表唯一源（提升自 src/pipeline/step3/case_rules/base.py，下游 import 引用，勿重抄）──
_SUBMIT_BUTTON_KEYWORDS = ("保存", "提交", "确定", "确认", "完成")


# ── 词表唯一源（提升自 src/pipeline/step3/case_rules/input_rule.py，下游 import 引用，勿重抄）──
_TRIGGER_FEEDBACK_KEYWORDS = (
    "流程",
    "唤起",
    "打开",
    "弹窗",
    "对话框",
    "模态",
    "抽屉",
)
