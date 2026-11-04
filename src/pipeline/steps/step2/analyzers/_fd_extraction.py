# -*- coding: utf-8 -*-
"""三元组提取（规则优先 + AI 补漏）。提取自 feature_driven.py。"""

import json
from src.core.guarded import guard
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from .constants import (
    _ai_brain,
    config,
    pipeline_config,
    _EXTRACT_TEMPERATURE,
    _EXTRACT_MAX_TOKENS,
    _STABILITY_SEED,
)
from ._fd_preprocess import _resolve_module_for_chunk
from ._guard_injection import build_step2_guard_injection
from ._fd_verification import _parse_json_output
# 需求文档结构标记的唯一来源（切片器与提取器共用同一组正则）
from ._fd_chunking import _REQ_STEPS_MARKER, _REQ_EXPECTED_MARKER

_MIN_DESC_LEN = 20
# condition 已由提取规则剥掉触发词，个别路径仍会带尾缀，避免渲染出「当X后时」
_CONDITION_TAIL_PUNCT = re.compile(r"[，,。；;、：:\s]*(?:之后|以后|后|时|的时候)?[，,。；;、：:\s]*$")

_log = logging.getLogger(__name__)

# ── 操作动词/目标对象词典加载（从 JSON 配置文件，支持多语言）──
_OPERATION_VERBS_DICT = None
_TARGET_OBJECTS_DICT = None
_FUNCTION_PATTERNS_DICT = None


def _load_operation_dictionary():
    """加载操作动词、目标对象、功能模式词典（从 JSON 配置文件）。

    ★ 改进1 (L2): 删除中文硬编码，改为可配置词典
    - 配置文件: src/config/operation_verbs.json
    - 支持中英文双语（默认），可扩展其他语言
    - 运行时从配置加载，不硬编码在代码中
    """
    global _OPERATION_VERBS_DICT, _TARGET_OBJECTS_DICT, _FUNCTION_PATTERNS_DICT
    if _OPERATION_VERBS_DICT is not None:
        return  # 已加载，不重复加载

    try:
        config_path = str(
            Path(__file__).resolve().parent.parent.parent.parent.parent
            / "config"
            / "operation_verbs.json"
        )

        if Path(config_path).exists():
            with open(config_path, "r", encoding="utf-8") as f:
                dict_config = json.load(f)

            # 合并所有语言的词典
            verbs = []
            objects = []
            patterns = []

            for lang in ["zh", "en"]:
                if lang in dict_config.get("operation_verbs", {}):
                    verbs.extend(dict_config["operation_verbs"][lang])
                if lang in dict_config.get("target_objects", {}):
                    objects.extend(dict_config["target_objects"][lang])
                if lang in dict_config.get("function_patterns", {}):
                    patterns.extend(dict_config["function_patterns"][lang])

            _OPERATION_VERBS_DICT = verbs
            _TARGET_OBJECTS_DICT = objects
            _FUNCTION_PATTERNS_DICT = patterns

            _log.info(
                "[feature_driven] 操作词典加载成功: %d 个动词, %d 个对象, %d 个模式",
                len(verbs),
                len(objects),
                len(patterns),
            )
        else:
            _log.warning(
                "[feature_driven] 操作词典配置文件不存在: %s，使用默认词典", config_path
            )
            _use_default_dictionary()
    except Exception as e:
        _log.warning("[feature_driven] 操作词典加载失败: %s，使用默认词典", e)
        _use_default_dictionary()


def _use_default_dictionary():
    """使用默认词典（兜底）。"""
    global _OPERATION_VERBS_DICT, _TARGET_OBJECTS_DICT, _FUNCTION_PATTERNS_DICT
    _OPERATION_VERBS_DICT = [
        "点击",
        "输入",
        "选择",
        "提交",
        "删除",
        "修改",
        "保存",
        "查询",
        "搜索",
        "登录",
        "退出",
        "上传",
        "下载",
        "审核",
        "审批",
        "启用",
        "禁用",
        "新增",
        "编辑",
        "导出",
        "导入",
        "配置",
        "设置",
        "确认",
        "取消",
        "创建",
        "更新",
        "查看",
        "关闭",
        "展开",
        "收起",
        "复制",
        "粘贴",
        "打印",
        "发送",
        "接收",
        "分配",
        "激活",
        "注销",
        "注册",
        "绑定",
        "解绑",
        "审查",
        "批准",
        "驳回",
        "签署",
        "签订",
        "筛选",
        "勾选",
        "填写",
        "打开",
        "click",
        "submit",
        "enter",
        "select",
        "delete",
        "update",
        "save",
        "search",
        "login",
        "logout",
        "upload",
        "download",
        "create",
        "view",
        "close",
        "expand",
        "collapse",
        "copy",
        "paste",
        "print",
        "send",
        "receive",
        "assign",
        "config",
        "enable",
        "disable",
        "bind",
        "approve",
        "reject",
        "edit",
        "add",
        "open",
        "input",
        "query",
        "export",
        "import",
        "modify",
        "remove",
        "check",
        "uncheck",
    ]
    _TARGET_OBJECTS_DICT = [
        "按钮",
        "表单",
        "列表",
        "页面",
        "弹窗",
        "菜单",
        "输入框",
        "复选框",
        "下拉框",
        "开关",
        "链接",
        "图标",
        "用户",
        "角色",
        "权限",
        "数据",
        "记录",
        "信息",
        "文件",
        "图片",
        "文档",
        "报表",
        "日志",
        "配置",
        "参数",
        "订单",
        "商品",
        "账户",
        "密码",
        "金额",
        "数量",
        "日期",
        "时间",
        "状态",
        "类型",
        "button",
        "form",
        "list",
        "page",
        "dialog",
        "menu",
        "input",
        "checkbox",
        "select",
        "switch",
        "link",
        "icon",
        "user",
        "role",
        "permission",
        "data",
        "record",
        "info",
        "file",
        "image",
        "document",
        "report",
        "log",
        "config",
        "parameter",
        "order",
        "product",
        "account",
        "password",
        "amount",
        "quantity",
        "date",
        "time",
        "status",
        "type",
    ]
    _FUNCTION_PATTERNS_DICT = [
        "步骤",
        "预期",
        "条件",
        "规则",
        "限制",
        "约束",
        "校验",
        "验证",
        "反馈",
        "响应",
        "should",
        "must",
        "expected",
        "when",
        "then",
        "requirement",
        "step",
        "condition",
        "rule",
        "feedback",
    ]


# 模块加载时初始化词典
_load_operation_dictionary()

# ★ 性能修复(07-05): 切片驱动架构常量
#   原架构：功能清单匹配 → 关键词锚定截取≤500字 snippet → 喂 AI（锚点无决策价值）
#   新架构：需求文档原文智能切片 → 每片≤600字喂 AI → 切片覆盖率 + 两次机会
_CHUNK_MAX_CHARS = getattr(pipeline_config, "step2_chunk_max_chars", 600)
_CHUNK_RETRY_MAX = getattr(pipeline_config, "step2_chunk_retry_max", 2)
# ★ v3.0 (2026-07-10): 删除 _AI_BUDGET_PER_ROUND 死变量（截断逻辑已不存在）
# ★ v3.0 (2026-07-10): AI 并行度从 1 提升到 10（与 Step3 step3_parallel_workers 一致）
_AI_CONCURRENCY = getattr(pipeline_config, "step2_ai_concurrency", 10)


def _effective_ai_concurrency() -> int:
    """★  根因修复：AI 并行度必须匹配**模型服务端的真并发能力**。

    根因（modsystem 实测，2026-09-23）：
      并发 10 打在**单实例** Ollama 上 → 请求在同一模型队列里排队，
      单次调用耗时随队列长度递增：166s → 250s → 378s → 520s（雪崩），
      整步被拖长数倍，并触发 run-slot watchdog 误判挂起（已另行根治）。
    根治思路（通用，零业务硬编码）：本地单实例模型 → 收敛扇出（≤2）；
      云端（具备真并发）→ 保持配置值。
    """
    try:
        _cfg_val = int(getattr(pipeline_config, "step2_ai_concurrency", 10))
    except Exception:
        _cfg_val = 10
    with guard("_effective_ai_concurrency: from src.ai.router_module import rou", log=_log):
        from src.ai.router_module import router as _router

        if _router.local_enabled():
            _local_cap = int(getattr(pipeline_config, "step2_local_ai_concurrency", 2) or 2)
            if _cfg_val > _local_cap:
                print(
                    f"[feature_driven] 本地单实例模型 → AI 并发自适应收敛: "
                    f"{_cfg_val} → {_local_cap}（避免排队雪崩）",
                    flush=True,
                )
                return max(1, _local_cap)
    return _cfg_val



# ══════════════════════════════════════════════════
# 0.2 按切片喂 AI 提取原子需求
# ══════════════════════════════════════════════════

_CHUNK_EXTRACT_PROMPT = (
    "你是一名资深需求分析师。你的任务是从给定的需求文档片段中，提取所有原子需求。\n\n"
    "## ★ 全局上下文（必读）\n"
    "你会收到以下上下文信息，必须用于理解业务脉络，不能把当前片段当孤立片段处理：\n"
    "- 【全局目录】：整个需求文档的章节结构，帮助你理解当前片段所属的业务模块\n"
    "- 【当前切片位置】：当前片段在文档中的位置（第N/M片）\n"
    "- 【前文摘要】：前一片段的最后100字，帮助你理解业务上下文衔接\n"
    "- 【后文摘要】：后一片段的最前100字，帮助你理解后续动作\n"
    "提取需求时，operation/condition/expected_feedback 必须结合全局上下文理解，"
    "不能脱离业务脉络生成空洞的操作描述。\n\n"
    "## 原子需求三元组\n"
    "每条需求必须包含以下要素：\n"
    "- operation: ★ 原子操作（必须是「动词+对象」格式，≤15字，禁止主语/描述句/时态从句）\n"
    '    ✓ 正确："批量导入对象A"、"按更新时间排序"、"新建对象B"、"保存表单"、"删除记录"\n'
    '    ✗ 错误："用户点击保存按钮"（含主语"用户"）、"系统展示列表时按更新时间排序"（描述句）、"用户执行新建"（含前缀）\n'
    "    规则：① 以动词开头（点击/输入/选择/提交/删除/修改/保存/查询/搜索/上传/下载/新增/编辑/导出/导入/创建/查看/打开/关闭/审核/启用/禁用/配置/确认/取消/筛选/填写等）\n"
    "         ② 紧接具体业务对象（对象A/对象B/用户/订单/表单/记录等）\n"
    '         ③ 禁止含主语（用户/系统/管理员）、禁止描述句、禁止含"时/后/则"等时态词\n'
    "- condition: 在什么条件下触发（前置条件，可为空字符串）\n"
    '- expected_feedback: 系统如何响应（可观察的反馈，必须含具体业务反馈，如"系统提示辅材导入成功"而非"操作成功"）\n'
    "- description: ★ 业务描述（≥20字，用自然语言描述这条需求要做什么、为什么、期望结果，"
    "结合全局上下文写明业务场景，供后续测试用例生成使用，不要用机械格式）\n\n"
    "## 三级信任模型（is_implicit 字段）\n"
    "- is_implicit=0（显式需求）：文档片段原文明确描述的需求，source_quote 必填且必须从该片段中逐字摘录\n"
    "- is_implicit=1（通用隐性需求）：基于通用软件业务常识推断的需求（如必填项未填提交应失败、"
    "查询无结果应显示空态、删除前应二次确认、表单校验失败应保留输入、列表应支持分页等），"
    "source_quote 可为空字符串\n"
    "- is_implicit=2（业务隐性需求）：基于具体业务场景推断的潜在需求，source_quote 可为空字符串\n\n"
    "## 关键规则\n"
    "1. ★ 原子性：一个操作触发多个独立反馈 → 拆为多条需求\n"
    "2. ★ source_quote 必须从下方【需求文档片段】中逐字摘录，禁止改写或臆造。"
    "若摘录的原文含 double quote（\"）或内嵌 JSON 片段（如 `{\"status\": \"ok\"}`、请求/响应示例、"
    "接口路径带参数），必须把这些双引号按 JSON 规则转义为 \\\"，整体仍是一个合法 JSON 字符串。"
    "宁可省略含引号的原文也不要让未转义引号破坏整段 JSON 的合法性\n"
    "   对于叙事型文档摘录10-60字；对于表格型功能列表，可摘录功能名称所在整行或单元格内容\n"
    "3. 通用隐性需求只推断软件行业普遍适用的常识行为，不臆造业务规则\n"
    "4. ★ 格式-语义匹配（严禁混淆三类表格/列表）：\n"
    "   - A) **功能矩阵表格**（含功能名+状态列如 ✓/✗ 或「- 功能A → operation」格式）→ 每行是一条操作路径，"
    '     is_implicit=1（表格是能力清单，操作从功能名推断）\n'
    "   - B) **接口/Endpoint 清单**（格式：`METHOD /path（描述）` 或 `endpoint - 描述`）→ 每个 endpoint 是独立显式需求，"
    "     从原文可直接摘抄 METHOD+路径+描述，is_implicit=0（source_quote 非空且从原文摘录）\n"
    "   - C) **数据字典/字段定义表格**（格式：`表名：字段1(约束1)、字段2(约束2)` 或 Markdown 字段列表）→ 每个"
    "     字段约束是独立显式需求，如 `price（数值）`、`username（唯一）`、`id（自增主键）`，is_implicit=0\n"
    "   - D) **约束规则清单**（格式：`规则名：条件→结果` 或 `- 约束X：...`）→ 每条约束是显式需求，is_implicit=0\n"
    "   — 判定关键：source_quote 能否从原文**逐字摘录**？能 = 0；不能（必须推断 operation）= 1\n"
    "   ★ 禁止把 B/C/D 类格式错误套用上「表格型 is_implicit=1」的规则——它们与 A 类格式语义完全不同\n"
    '   ★ 功能矩阵表格 A 示例："按更新时间排序"→ operation="按更新时间排序"（非"系统展示列表时按更新时间排序"）\n'
    '   ★ 接口清单 B 示例："GET /assets（列表）"→ operation="请求GET /assets" source_quote="GET /assets" is_implicit=0\n'
    '   ★ 数据字典 C 示例："price（数值）"→ operation="验证price字段为数值类型" source_quote="price（数值）" is_implicit=0\n'
    "5. ★ 需求类型：不只有用户动作，凡能用**可观察断言**验证的契约/约束都应提取为独立需求：\n"
    "   - **字段校验/约束**（如字段必填、长度、范围、取值规则）→ operation=「校验字段<字段名>」或「提交含非法<字段名>的请求」，"
    "condition=非法取值场景，expected_feedback=「系统拒绝并返回错误码<code>」\n"
    "   - **错误码/响应契约**（如 code 40001 ↔ HTTP 400）→ operation=「触发错误码<code>」，condition=对应失败场景，"
    "expected_feedback=「返回 HTTP <状态> 且响应 code=<code>」\n"
    "   - **健康/探针接口**（如 GET /health）→ operation=「请求<接口路径>」，condition=触发条件，"
    "expected_feedback=「返回 <状态码>，响应含 <关键字段/值>」\n"
    "   - **非功能约束**（性能响应时间/并发、数据格式、安全不泄露内部信息）→ operation=「验证<约束对象>」，"
    "condition=约束适用场景，expected_feedback=「<可量化阈值或断言规则>」\n"
    "   → 上述一律映射为同一三元组结构（operation/condition/expected_feedback/description），"
    "description 写明所属契约类型及断言依据\n"
    "6. 如果片段没有描述任何可验证需求，返回空数组 []\n"
    "7. 输出纯 JSON 数组，不要任何解释文字\n"
    '8. ★ 忽略内容仅限**纯叙述性、无法形成可观察断言的文本**：系统概述、项目目标、技术选型、'
    '环境/启动说明、图片路径、URL链接、纯文本路径说明（如"图片如下"、"详见截图"）。'
    '勿因内容属于「接口定义 / 表格 / 数据字典 / 非功能」就整体丢弃——字段校验、错误码契约、'
    '健康检查/探针接口、非功能约束等**可验证内容**必须按【需求类型】提取\n'
    "9. ★ description 字段必须写业务描述，禁止写 [op] 条件: x → 反馈: y 这种机械格式\n\n"
    "## 输出格式\n"
    "[\n"
    "  {\n"
    '    "req_id": "REQ-001",\n'
    '    "operation": "保存表单数据",\n'
    '    "condition": "表单已填写完整",\n'
    '    "expected_feedback": "系统提示保存成功",\n'
    '    "description": "在表单填写完整的情况下，用户点击保存按钮提交表单数据，系统校验通过后提示保存成功，确保数据持久化",\n'
    '    "source_quote": "点击保存按钮后，系统校验通过则保存",\n'
    '    "is_implicit": 0,\n'
    '    "dependency_req_id": ""\n'
    "  }\n"
    "]\n"
    "\n"
    "## 依赖说明（能填则填，别臆造）\n"
    "- 每条给 req_id 标号，格式 REQ-001、REQ-002…（本次输出内唯一即可）\n"
    "- 若本条需求的实现逻辑上依赖**本次输出内**的另一条需求先完成\n"
    "  （例如“删除记录”依赖“登录/鉴权”、“保存表单”依赖“填写表单”），\n"
    '  则 dependency_req_id 填那条被依赖的 req_id；否则填空字符串 ""\n'
    '- ★ 只描述同一段文档片段内部的真实前后置关系，不要跨本次输出臆造依赖\n'
)

# ★ 覆盖率重试专用 prompt：换方法，不换标准
# 首次提取可能因片段偏背景/说明而漏提，重试时聚焦"可测试功能点"，明确要求至少提取一条 operation+expected_feedback
_CHUNK_RETRY_EXTRACT_PROMPT = (
    "你是一名资深需求分析师。上一次提取未从该片段中发现原子需求，请换一种角度重新审视。\n\n"
    "## ★ 全局上下文（必读）\n"
    "你会收到以下上下文信息，必须用于理解业务脉络：\n"
    "- 【全局目录】：整个需求文档的章节结构\n"
    "- 【当前切片位置】：当前片段在文档中的位置（第N/M片）\n"
    "- 【前文摘要】：前一片段的最后100字\n"
    "- 【后文摘要】：后一片段的最前100字\n"
    "重新审视时，结合全局上下文判断当前片段是否包含可测试的功能行为。\n\n"
    "## 重新审视目标\n"
    "只关注【可测试的功能行为】：任何用户/系统能做的操作与系统随之产生的可观察反馈，"
    "并包括**可验证的契约/约束**：字段校验规则、错误码/响应契约、健康/探针接口、"
    "非功能约束（性能/安全/格式）。凡能用可观察断言验证的都必须提取。\n"
    "忽略纯叙述性且无法形成可观察断言的文本：系统概述、项目目标、技术选型、环境/启动说明、"
    "UI 样式描述、图片路径、URL链接、纯文本路径说明。\n"
    "勿因内容属于「接口定义 / 表格 / 数据字典 / 非功能」就整体丢弃——其中可验证的规则/契约/约束必须提取。\n\n"
    "## 原子需求三元组\n"
    "每条需求必须包含：\n"
    "- operation: ★ 原子操作（「动词+对象」格式，≤15字，禁止主语/描述句/时态从句）\n"
    '    ✓ 正确："批量导入辅材"、"按更新时间排序"、"新建辅材"、"保存表单"\n'
    '    ✗ 错误："用户点击保存按钮"、"系统展示列表时按更新时间排序"、"用户执行新建"\n'
    '    规则：① 以动词开头 ② 紧接业务对象 ③ 禁止主语（用户/系统）、描述句、"时/后/则"时态词\n'
    "- condition: 触发条件（可为空字符串）\n"
    "- expected_feedback: 系统可观察的响应（必须含具体业务反馈）\n"
    "- description: ★ 业务描述（≥20字，结合全局上下文写明业务场景，禁止机械格式）\n\n"
    "## 关键规则\n"
    "1. ★ 只要片段里存在任何可识别的功能或操作，就必须提取，不要返回空数组\n"
    "2. ★ 一个操作对应多个独立反馈 → 拆为多条需求\n"
    "3. ★ source_quote 必须从片段原文逐字摘录。若摘录原文含 double quote 或内嵌 JSON 片段，"
    "必须将这些双引号转义为 \\\"，保证整段 JSON 合法；宁可省略含引号的原文也不破坏 JSON 结构。"
    "叙事型文档摘录10-60字；表格型功能列表可摘录功能名称所在行或单元格\n"
    "4. ★ 表格型功能列表：如果片段是表格格式，将每个功能名称行视为一条需求，"
    'operation 为该功能的核心「动词+对象」（≤15字，禁止整行描述，禁止加"用户执行"等主语前缀），'
    "expected_feedback 必须结合全局目录推断具体业务反馈，"
    "source_quote 摘录该行原文，is_implicit 设为 1\n"
    '   ★ 示例：表格行"按更新时间排序"→ operation="按更新时间排序"；表格行"新建"→ operation="新建辅材"\n'
    "5. 若确实没有任何可测试行为，再返回空数组 []\n"
    "6. 输出纯 JSON 数组，不要任何解释文字\n"
    '7. ★ 不要把图片路径、URL、"图片如下"、"详见截图"等当成需求\n'
    "8. ★ description 字段必须写业务描述，禁止写 [op] 条件: x → 反馈: y 这种机械格式\n\n"
    "## 输出格式\n"
    "[\n"
    "  {\n"
    '    "req_id": "REQ-001",\n'
    '    "operation": "保存表单数据",\n'
    '    "condition": "表单已填写完整",\n'
    '    "expected_feedback": "系统提示保存成功",\n'
    '    "description": "在表单填写完整的情况下，用户点击保存按钮提交表单数据，系统校验通过后提示保存成功，确保数据持久化",\n'
    '    "source_quote": "点击保存按钮后，系统校验通过则保存",\n'
    '    "is_implicit": 0,\n'
    '    "dependency_req_id": ""\n'
    "  }\n"
    "]\n"
    "\n"
    "## 依赖说明（能填则填，别臆造）\n"
    "- 每条给 req_id 标号，格式 REQ-001、REQ-002…（本次输出内唯一即可）\n"
    "- 若本条需求的实现逻辑上依赖**本次输出内**的另一条需求先完成\n"
    "  （例如“删除记录”依赖“登录/鉴权”、“保存表单”依赖“填写表单”），\n"
    '  则 dependency_req_id 填那条被依赖的 req_id；否则填空字符串 ""\n'
    '- ★ 只描述同一段文档片段内部的真实前后置关系，不要跨本次输出臆造依赖\n'
)


# 规则提取的唯一实现在 .breakdown_rules（按 stage 注册，含标准结构/编号列表/
# 条件句式/陈述句/表格规则）；本模块只负责调度"规则优先、AI 补漏"。
# -- 闭环反馈 + 幻觉护栏 -> 已提取到 _guard_injection.py --
# 导入: from ._guard_injection import build_step2_guard_injection


def _triple_to_req(
    triple: dict[str, Any],
    chunk: dict[str, Any],
    chunks: list[dict[str, str]],
    chunk_index: int,
    retry_attempt: int,
    match_type: str,
) -> dict[str, Any]:
    """把一条三元组落成需求 dict —— 规则路径与 AI 路径共用的唯一实现。

    module/description/source_quote 契约等字段在此统一处理，避免两条路径
    各自拼装导致同一需求在两种来源下字段语义不一致。
    """
    chunk_text = chunk.get("text", "") or ""
    source_quote = triple.get("source_quote", "") or ""
    _sq_stripped = source_quote.strip()

    try:
        is_implicit = int(triple.get("is_implicit", 0) or 0)
    except (TypeError, ValueError):
        is_implicit = 0
    if is_implicit not in (0, 1, 2):
        is_implicit = 0
    # is_implicit ↔ source_quote 契约：非空且在原文存在 → 显式；空 → 只能是隐式
    if _sq_stripped:
        if _sq_stripped[:80] in chunk_text:
            is_implicit = 0
    elif is_implicit == 0:
        is_implicit = 1

    operation = triple.get("operation", "") or ""
    condition = triple.get("condition", "") or ""
    feedback = triple.get("expected_feedback", "") or ""
    module = _resolve_module_for_chunk(
        chunk,
        chunks,
        chunk_index,
        hint_text=" ".join(x for x in [operation, condition, source_quote] if x),
    )

    description = (triple.get("description") or "").strip()
    if not description or len(description) < _MIN_DESC_LEN:
        # 只做如实复述：不推断执行主体（同一 operation 可能是用户动作、系统反应或
        # 产品约束），否则会把「所有写操作需保证事务一致」渲染成用户动作病句。
        _desc_condition = _CONDITION_TAIL_PUNCT.sub("", condition).strip()
        _head = f"在{module}模块中"
        if _desc_condition:
            _head += f"，当{_desc_condition}时"
        description = f"{_head}，{operation}"
        if feedback and feedback != operation:
            description += f"，预期反馈：{feedback}"

    return {
        "feature_id": 0,
        "module": module,
        "description": description,
        "operation": operation,
        "condition_expr": condition,
        "expected_feedback": feedback,
        "source_quote": source_quote,
        "is_implicit": is_implicit,
        # AI 在切片内用的是局部 req_id，跨合并后由 _dep_req_hint 就近解析
        "dependency_req_id": str(triple.get("dependency_req_id") or "").strip(),
        "_dep_req_hint": triple.get("_dep_req_hint", ""),
        "review_status": "pending_review" if chunk.get("needs_review") else "pending",
        "_match_type": match_type,
        "slice_id": chunk.get("chunk_id", ""),
        "_retry_attempt": retry_attempt,
        "_needs_manual_review": bool(chunk.get("needs_review")),
    }


def _extract_from_chunks(
    chunks: list[dict[str, str]],
    ui_context: str = "",
    retry_attempt: int = 0,
) -> tuple[list[dict[str, Any]], set, int, int]:
    """对每个切片提取原子需求三元组（规则优先 + AI 补漏）。

    判定顺序（决定产出是否可复现）：
      1. 每片先跑 .breakdown_rules 规则流水线（纯算法、确定性）；
      2. 规则命中的切片即视为已覆盖，不再调用 AI；
      3. 仅规则空手而归的切片才送 LLM，由 AI 理解补充。
    这样同一份文档的规则产出恒定，AI 只在规则覆盖不到的空白处引入变量。

    ★ 性能：
      - AI 调用并行化（ThreadPoolExecutor，并发度按模型服务端能力自适应）
      - 全量处理（无预算截断，覆盖率优先）

    ★ Phase1 (2026-07-27): Figma 设计稿上下文注入
      - ui_context 包含 Figma 设计稿的页面结构和交互元素信息
      - 注入到每个切片的 AI prompt 中，让 AI 知道"设计上这个功能长什么样"
      - 需求文档是骨架（要测什么），Figma 是唯一依据（预期行为是什么）

    Args:
        chunks: 切片列表 [{"chunk_id": str, "text": str, "section": str}]
        ui_context: Figma 设计稿上下文（页面结构+交互元素，作为 AI 提取的'设计唯一依据'）
        retry_attempt: 重试轮次（0=首次，1/2=未覆盖切片重试）

    Returns:
        (requirements_list, covered_chunk_ids, ai_call_count, rule_count)
    """
    all_reqs = []
    covered_chunk_ids = set()
    ai_call_count = 0
    rule_count = 0

    # ★ v23 根源修复(2026-07-21): 预计算全局目录 + 切片位置索引
    #   根因A: 原 _call_ai_for_chunk 只把 chunk_text_safe 作为 user_msg，
    #          prev_context/next_context 计算了但完全没注入 AI prompt，
    #          导致 AI 把每个切片当孤立片段处理，丢失业务脉络理解。
    #   修复: 预计算 global_toc（从 chunks 的 section 字段聚合去重），
    #         构建 chunk_id → (prev_context, next_context, position) 映射，
    #         在 _call_ai_for_chunk 中注入到 user_msg。
    global_toc = []
    _seen_sections = set()
    for c in chunks:
        sec = (c.get("section") or "").strip()
        if sec and sec not in _seen_sections:
            global_toc.append(sec)
            _seen_sections.add(sec)
    _global_toc_str = " | ".join(global_toc) if global_toc else "(无章节信息)"
    _total_chunks = len(chunks)
    _chunk_index_map = {c["chunk_id"]: i for i, c in enumerate(chunks)}

    # ── 阶段 1: 规则提取（确定性；命中即覆盖，该片不再调用 AI）──
    with guard("_extract_from_chunks: from src.pipeline.steps.step2.breakd", log=_log):
        from src.pipeline.steps.step2.breakdown_rules import run_all_pipeline

        for chunk in chunks:
            chunk_id = chunk.get("chunk_id", "")
            text = chunk.get("text", "") or ""
            if not text.strip():
                continue
            triples, _issues = run_all_pipeline(
                text, chunk_id, table_meta=chunk.get("table_meta")
            )
            if not triples:
                continue
            for triple in triples:
                all_reqs.append(
                    _triple_to_req(
                        triple,
                        chunk,
                        chunks,
                        _chunk_index_map.get(chunk_id, -1),
                        retry_attempt,
                        match_type="rule",
                    )
                )
            covered_chunk_ids.add(chunk_id)
            rule_count += len(triples)

    if rule_count:
        _log.info(
            "[feature_driven] 规则提取: 覆盖 %d/%d 片, 产出 %d 条需求",
            len(covered_chunk_ids),
            len(chunks),
            rule_count,
        )

    # ── 阶段 2: AI 补漏（只处理规则未覆盖的切片）──
    ai_chunks = [c for c in chunks if c["chunk_id"] not in covered_chunk_ids]

    # ★ 优化(07-08): 功能需求预判过滤器 — 跳过纯背景/目录/概述切片，节省 AI token
    #   升级: 从"单一关键词匹配"改为"操作动词+具体对象"模式匹配，减少误判
    # ★ 改进1 (L2): 从可配置词典动态生成正则，删除硬编码
    _verb_pattern = "|".join(re.escape(w) for w in _OPERATION_VERBS_DICT)
    _OPERATION_VERBS = re.compile(rf"(?:{_verb_pattern})", re.IGNORECASE)
    _obj_pattern = "|".join(re.escape(w) for w in _TARGET_OBJECTS_DICT)
    _TARGET_OBJECTS = re.compile(rf"(?:{_obj_pattern})", re.IGNORECASE)
    _func_pattern = "|".join(re.escape(w) for w in _FUNCTION_PATTERNS_DICT)
    _FUNCTION_PATTERNS = re.compile(rf"(?:{_func_pattern})", re.IGNORECASE)
    _PURE_BACKGROUND_PATTERNS = re.compile(
        r"^(?:#{1,3}\s+[^\n]+\n)+(?:\s*\n)*$"  # 纯标题+空行
        r"|^(?:\d+\.\s+[^\n]+\n)+$",  # 纯目录
        re.MULTILINE,
    )

    def _has_function_context(text):
        """判断切片是否包含功能需求上下文。

        ★ v3.0 (2026-07-10): 放宽为 OR 逻辑（动词/对象/功能标记任一命中即保留）
        原 AND 逻辑（动词+对象同时命中）跳过 62% 切片，导致覆盖率仅 9.6%。
        纯背景/目录/极短文本已由 _PURE_BACKGROUND_PATTERNS 和 len<30 过滤，
        此处只需拦截不含任何功能词的描述性文本即可。
        """
        has_verb = bool(_OPERATION_VERBS.search(text))
        has_object = bool(_TARGET_OBJECTS.search(text))
        has_func_marker = bool(_FUNCTION_PATTERNS.search(text))
        has_steps_marker = bool(_REQ_STEPS_MARKER.search(text))
        has_expected_marker = bool(_REQ_EXPECTED_MARKER.search(text))
        if has_steps_marker or has_expected_marker:
            return True
        # ★ v3.0: OR 逻辑，任一命中即保留（交由 AI 判断是否有真实需求）
        if has_verb or has_object or has_func_marker:
            return True
        return False

    # ★ v5.16(2026-08-04): 保存过滤前原始切片（功能预判回退兜底用）
    ai_chunks_raw = list(ai_chunks)
    _filtered_chunks = []
    _skipped_background = 0
    for c in ai_chunks:
        text = c.get("text", "")
        # ★ 表格切片不做功能预判：表格包含结构化功能数据（如 YApi 接口列表），需 AI 判断
        if c.get("slice_type") == "table":
            _filtered_chunks.append(c)
            continue
        if len(text.strip()) < 30:
            _skipped_background += 1
            continue
        if _PURE_BACKGROUND_PATTERNS.match(text.strip()):
            _skipped_background += 1
            continue
        if not _has_function_context(text):
            _skipped_background += 1
            continue
        _filtered_chunks.append(c)

    if _skipped_background > 0:
        _log.info(
            "[feature_driven] 功能预判: 跳过 %d 个非功能切片（背景/目录/概述），剩余 %d 个待 AI 提取",
            _skipped_background,
            len(_filtered_chunks),
        )
    ai_chunks = _filtered_chunks

    # ★ v3.0 (2026-07-10): 按 chunk_id 稳定排序，确保确定性
    #   原 _function_density 排序基于关键词频率，文档微调即改变排序结果，
    #   导致每次运行选出不同切片顺序 → 需求条目数非确定性（10 vs 12）
    ai_chunks.sort(key=lambda c: c.get("chunk_id", ""))

    if not ai_chunks:
        # ★ 通用性修复：功能预判只是省 token 的优化，不是硬闸门。
        #   根因(3层): ① 预判依赖领域词典（操作动词/目标对象），未知领域文档
        #   （如某业务系统"叶组配方/批量打标"）可能被判"非功能切片"整片丢弃；
        #   ② 丢弃后规则提取也 0 条 → Step2 拆解 0 需求，流水线中止；
        #   ③ 跨系统通用性要求：预判失败应回退原始切片交 AI，不得丢真实需求。
        #   修复: 过滤后为空但存在原始切片 → 回退全部原始切片交 AI 兜底。
        _fallback = [c for c in ai_chunks_raw if c.get("text", "").strip()]
        if _fallback:
            _log.warning(
                "[feature_driven] 功能预判过滤后为空，回退 %d 个原始切片交 AI（通用性兜底）",
                len(_fallback),
            )
            ai_chunks = _fallback
        else:
            _log.info("[feature_driven] 无需 AI 提取的切片（全部由规则覆盖）")
            return all_reqs, covered_chunk_ids, ai_call_count, rule_count

    # ★ 性能修复(07-08): 准备 AI 调用任务（预处理截断）
    # ★ v23 根源修复(2026-07-21) 根因A: 注入全局上下文（全局目录+切片位置+前后文摘要）
    #   根因: 原 user_msg 只含 chunk_text_safe，AI 把每个切片当孤立片段处理，
    #         丢失业务脉络理解，导致通过率从"整文档喂AI"的高变"切片独立调用"的0%。
    #   修复: user_msg 增加【全局目录】【当前切片位置】【前文摘要】【后文摘要】段落，
    #         让 AI 理解当前片段在文档中的业务上下文。
    #   通用性: 上下文都是事实信息（章节标题+相邻切片原文），不引入新事实，不增加幻觉风险。
    _CHUNK_SAFE_LIMIT = _CHUNK_MAX_CHARS * 2
    ai_tasks = []  # (chunk, user_msg, system_prompt)
    for chunk in ai_chunks:
        chunk_id = chunk["chunk_id"]
        chunk_text = chunk["text"]
        if not chunk_text or not chunk_text.strip():
            continue

        # 安全兜底：异常超长切片智能截断
        chunk_text_safe = chunk_text
        if len(chunk_text_safe) > _CHUNK_SAFE_LIMIT:
            try:
                from src.extensions.rag_engine.chunking_mixin_mixins._segment import (
                    SmartSegmentMixin,
                )

                segments = SmartSegmentMixin._smart_segment(
                    chunk_text_safe, _CHUNK_SAFE_LIMIT
                )
                if segments:
                    chunk_text_safe = segments[0]
                    _log.warning(
                        "[feature_driven] chunk_text 异常超长，已智能截断: %s (%d→%d字)",
                        chunk_id,
                        len(chunk_text),
                        len(chunk_text_safe),
                    )
                else:
                    _log.warning(
                        "[feature_driven] chunk_text 智能分段失败，跳过: %s", chunk_id
                    )
                    continue
            except Exception as _seg_e:
                _log.warning(
                    "[feature_driven] 智能截断异常，跳过: %s | %s", chunk_id, _seg_e
                )
                continue

        # ★ v23 根因A修复: 注入全局上下文
        _chunk_idx = _chunk_index_map.get(chunk_id, -1)
        _chunk_pos = (
            f"第{_chunk_idx + 1}/{_total_chunks}片" if _chunk_idx >= 0 else "未知位置"
        )
        _prev_ctx = (chunk.get("prev_context") or "").strip()
        _next_ctx = (chunk.get("next_context") or "").strip()
        # ★ Phase1(07-27): 注入 Figma 设计稿上下文（设计唯一依据）
        _figma_block = ""
        if ui_context and ui_context.strip():
            _figma_block = (
                f"【UI设计稿上下文（唯一依据）】\n"
                f"以下是 Figma 设计稿中定义的页面结构和交互元素，是判断预期UI行为的唯一依据：\n"
                f"{ui_context.strip()}\n\n"
            )
        _context_block = (
            f"【全局目录】 {_global_toc_str}\n"
            f"【当前切片位置】 {_chunk_pos}\n"
            f"【前文摘要】 {_prev_ctx or '(无)'}\n"
            f"【后文摘要】 {_next_ctx or '(无)'}\n"
            f"{_figma_block}"
            f"【需求文档片段】\n{chunk_text_safe}"
        )

        if retry_attempt > 0:
            user_msg = f"[重试{retry_attempt + 1}] {_context_block}"
            system_prompt = _CHUNK_RETRY_EXTRACT_PROMPT
        else:
            user_msg = _context_block
            system_prompt = _CHUNK_EXTRACT_PROMPT

        ai_tasks.append((chunk, user_msg, system_prompt))

    from src.core.config_session import (
        get_session_key,
        get_current_user,
        set_session_key,
        set_current_user,
    )

    _parent_session_key = get_session_key()
    _parent_user = get_current_user()
    _parent_run_id = None
    try:
        from src.backend.state import _thread_run_id

        _parent_run_id = getattr(_thread_run_id, "value", None)
    except Exception as _e:
        _log.debug("读取 _thread_run_id 失败: %s", _e, exc_info=True)

    def _call_ai_for_chunk(task_tuple):
        """对单个切片调用 AI 提取三元组。返回 (chunk, triples_list, success)"""
        if _parent_user:
            set_current_user(_parent_user)
        if _parent_session_key and _parent_session_key != "local":
            set_session_key(_parent_session_key)
        if _parent_run_id:
            try:
                from src.backend.state import _thread_run_id as _rid

                _rid.value = _parent_run_id
            except Exception as _e:
                _log.debug("设置 _thread_run_id 失败: %s", _e, exc_info=True)

        chunk, user_msg, system_prompt = task_tuple
        chunk_id = chunk["chunk_id"]

        # ★ 注入闭环反馈 + 幻觉护栏规则（Step2 源头预防，不阻断 AI 调用）
        _injection = build_step2_guard_injection(chunk)
        if _injection:
            system_prompt = system_prompt + _injection

        try:
            result = _ai_brain._call_api(
                system_prompt=system_prompt,
                user_message=user_msg,
                temperature=_EXTRACT_TEMPERATURE,
                max_tokens=_EXTRACT_MAX_TOKENS,
                caller_name="feature_driven_extract",
                task_type="feature_driven_extract",
                seed=_STABILITY_SEED,
            )
            if not result.get("success"):
                _log.warning(
                    "[feature_driven] AI 调用失败 | chunk=%s | error=%s | result_keys=%s",
                    chunk_id,
                    str(result.get("error", ""))[:150],
                    list(result.keys())[:5],
                )
                return chunk, [], False

            data = result.get("data", {})
            content = ""
            if isinstance(data, dict):
                content = data.get("content", "") or data.get("text", "") or ""
                if not content:
                    _log.warning(
                        "[feature_driven] AI data(dict)无 content/text 字段 | chunk=%s | keys=%s | data[:100]=%s",
                        chunk_id,
                        list(data.keys())[:5],
                        str(data)[:100],
                    )
            elif isinstance(data, str):
                content = data
            elif isinstance(data, list):
                _log.info(
                    "[feature_driven] AI data 是 list, 直接作为 triples | chunk=%s | len=%d",
                    chunk_id,
                    len(data),
                )
                return (
                    chunk,
                    data if all(isinstance(item, dict) for item in data) else [],
                    bool(data),
                )
            else:
                _log.warning(
                    "[feature_driven] AI data 类型异常 | chunk=%s | type=%s | val[:100]=%s",
                    chunk_id,
                    type(data).__name__,
                    str(data)[:100],
                )

            if not content:
                _log.debug("[feature_driven] AI 返回空内容 | chunk=%s", chunk_id)
                return chunk, [], False

            triples = _parse_json_output(content)
            if not triples:
                _log.warning(
                    "[feature_driven] AI 返回内容无法解析为三元组 | chunk=%s | content[:200]=%s",
                    chunk_id,
                    content[:200],
                )
            return chunk, triples or [], bool(triples)

        except Exception as e:
            _log.warning(
                "[feature_driven] AI 调用异常: %s | chunk=%s", str(e)[:100], chunk_id
            )
            return chunk, [], False
        finally:
            # ★ 线程状态还原为父线程值，避免泄漏
            try:
                if _parent_user:
                    set_current_user(_parent_user)
                else:
                    set_current_user("")
            except Exception as _e:
                _log.debug("还原 current_user 失败: %s", _e, exc_info=True)
            try:
                if _parent_session_key and _parent_session_key != "local":
                    set_session_key(_parent_session_key)
                else:
                    set_session_key("")
            except Exception as _e:
                _log.debug("还原 session_key 失败: %s", _e, exc_info=True)
            try:
                from src.backend.state import _thread_run_id as _rid_cleanup

                _rid_cleanup.value = _parent_run_id
            except Exception as _e:
                _log.debug("还原 _thread_run_id 失败: %s", _e, exc_info=True)

    # ★ 性能修复(07-08): 并行执行 AI 调用
    # ★ 2026-09-23: 并发度按模型服务端真并发能力自适应（本地单实例 → 收敛）
    _ai_conc = _effective_ai_concurrency()
    _log.info(
        "[feature_driven] 开始并行 AI 提取: %d 个切片, 并发度=%d",
        len(ai_tasks),
        _ai_conc,
    )

    with ThreadPoolExecutor(max_workers=_ai_conc) as executor:
        futures = {executor.submit(_call_ai_for_chunk, task): task for task in ai_tasks}
        for future in as_completed(futures):
            ai_call_count += 1
            with guard("_extract_from_chunks: chunk, triples, success = future.res", log=_log, level=logging.WARNING):
                chunk, triples, success = future.result()
                chunk_id = chunk["chunk_id"]

                if not success or not triples:
                    continue

                covered_chunk_ids.add(chunk_id)
                _chunk_idx = _chunk_index_map.get(chunk_id, -1)

                # 依赖线索：同 chunk 内 AI 可引用它刚输出的局部 req_id；
                # 局部编号在全局重编号后失效，故把被依赖需求描述作为线索随需求存活。
                _local_desc = {
                    str(tr.get("req_id") or "").strip():
                    str(tr.get("description") or tr.get("operation") or "").strip()
                    for tr in triples
                    if (tr.get("req_id") or "").strip()
                }

                for triple in triples:
                    _dep_ref = str(triple.get("dependency_req_id") or "").strip()
                    triple["_dep_req_hint"] = (
                        _local_desc.get(_dep_ref, "") if _dep_ref else ""
                    )
                    all_reqs.append(
                        _triple_to_req(
                            triple,
                            chunk,
                            chunks,
                            _chunk_idx,
                            retry_attempt,
                            match_type="chunk_extract",
                        )
                    )

    _log.info(
        "[feature_driven] 切片提取完成: %d 片 → %d 条需求 (规则 %d 条 / AI 调用 %d 次, retry=%d, 并发=%d)",
        len(chunks),
        len(all_reqs),
        rule_count,
        ai_call_count,
        retry_attempt,
        _ai_conc,
    )
    return all_reqs, covered_chunk_ids, ai_call_count, rule_count
