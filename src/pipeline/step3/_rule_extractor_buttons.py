# -*- coding: utf-8 -*-
"""_rule_extractor_buttons — 从 _rule_extractor.py 拆分（D.1g 架构债清理 2026-07-25，§4.3 单文件≤800行）。

职责：buttons 相关函数。
逻辑不变，仅结构重构。re-export 保持调用兼容。
"""
from __future__ import (
    annotations,
)  # ★ 修复(2026-07-25): Python 3.9 兼容（PROJECT_MEMORY.md 硬约束）
import json
import logging
import re
from collections import Counter
from typing import Any

_log = logging.getLogger(__name__)

# ★ D.1g 拆分遗留断链修复(2026-07-25): 以下 3 个常量原定义在 _rule_extractor.py
#   拆分到本模块后漏 import → NameError 被 except 兜住 → 按钮匹配功能沉默失效
#   修复: 常量就近定义在唯一消费方（主文件自身不使用），避免循环 import
#   通用性(§12.4): 纯确定性动词表/虚词表，跨系统/行业/页面通用
_SEMANTIC_CATEGORIES: dict[str, set[str]] = {
    "query": {"查询", "搜索", "筛选", "检索", "查找", "查", "找"},
    "create": {"新建", "新增", "添加", "创建", "增加", "加"},
    "delete": {"删除", "移除", "去掉", "清除", "删"},
    "edit": {"编辑", "修改", "更新", "变更", "改"},
    "save": {"保存", "提交", "确认", "确定", "存"},
    "export": {"导出", "下载", "输出"},
    "import": {"导入", "上传", "输入"},
    "cancel": {"取消", "关闭", "退出"},
    "enable": {"启用", "停用", "开关", "切换"},
    "copy": {"复制", "拷贝", "克隆"},
    "tag": {"打标", "标签", "标记"},
    "refresh": {"刷新", "重置", "重新"},
}

# ─────────────────────────────────────────────────────────────────────────────
# 变更型操作的「触发 → 录入 → 提交」状态机（SSOT， 根因修复）
#
# 根因（与 check 词表漂移同源）：同一操作（新增/编辑/删除…）在生成侧有**两套动作
#   编排实现**，口径不一致：
#     · `_rule_extractor_actions._build_actions`（主规则路径）
#       与 `_fallback_cases._build_base_actions`（兜底路径）→ 产出
#       `fill(...) → click(触发按钮)`：**弹窗还没打开就先往表单里填**，fill 目标
#       必错、click 又去开了个空弹窗 → 结构化执行必败。
#     · `_fallback_cases` 探针分支 → 产出正确的
#       `click(触发) → fill(弹窗内) → click(确认)`。
#   实测（accept_sut_0923 试点，同一系统同一操作语义）：
#     TC-REQ-002-UX02（探针路径）动作序正确；TC-REQ-002-P01/B01、TC-REQ-003-P01
#     （主规则路径）动作序颠倒 → Step4 结构化执行失败 → 降级 AI 自由探索 →
#     8 轮不收敛 → HARNESS_ERROR。
#
# 根治 = 编排收敛到本模块的**单一实现**（三条路径共用），并删除各处私自维护的
#   局部关键词表（`_build_actions` 的 `_is_dialog_op` 曾漏 "新增/添加/创建/编辑/更新"
#   ——"新增资产"永不补全开窗，就是它漏词的直接后果）。
#
# 通用性(§1)：只依赖「动词→类别」语义表（SSOT）与"本用例是否存在录入动作"这一
#   结构事实，跨系统/行业/框架成立，零业务硬编码。
# ─────────────────────────────────────────────────────────────────────────────
_VERB_CATEGORY_SORTED: tuple = tuple(
    sorted(
        ((_v, _c) for _c, _vs in _SEMANTIC_CATEGORIES.items() for _v in _vs),
        key=lambda _x: len(_x[0]),
        reverse=True,
    )
)

# 触发型类别：点击该控件后**才**出现录入界面/确认框（录入必须发生在其后）。
#   ★ 不含 enable/tag：开关/打标是就地生效，不产生录入界面，避免过度编排。
_TRIGGER_CATEGORIES: frozenset = frozenset(
    {"create", "edit", "delete", "import", "copy"}
)
# 提交型类别：操作本身就是"提交/保存"（录入后点击即提交，无需先开窗）。
_CONFIRM_CATEGORIES: frozenset = frozenset({"save"})

# 纯录入描述词：语义是"往字段里填值"，**本身不打开新界面**。
#   "输入" 之所以归在 import 类别，只因按钮匹配需要（"导入/上传"同族）；
#   用于"是否先开窗"判定时必须剔除，否则"输入超长名称"这类字段约束需求
#   会被误判为"先开窗再录入" → 凭空点击触发控件。
#   属语言学分类（数据录入描述），非业务词，跨系统/行业通用。
_ENTRY_ONLY_VERBS: frozenset = frozenset({"输入", "填写", "录入", "填入"})

# 动词集合自 SSOT 派生（禁止另造词表）；剔除单字动词（"存/改/删"易与业务词误撞，
#   如"库存"会命中"存"）与纯录入描述词。
TRIGGER_VERBS: frozenset = frozenset(
    _v
    for _c, _vs in _SEMANTIC_CATEGORIES.items()
    if _c in _TRIGGER_CATEGORIES
    for _v in _vs
    if len(_v) >= 2 and _v not in _ENTRY_ONLY_VERBS
)
_TRIGGER_VERBS_ORDERED: tuple = tuple(
    sorted(TRIGGER_VERBS, key=len, reverse=True)
)
CONFIRM_VERBS: frozenset = frozenset(
    _v
    for _c, _vs in _SEMANTIC_CATEGORIES.items()
    if _c in _CONFIRM_CATEGORIES
    for _v in _vs
    if len(_v) >= 2
)


def classify_operation_category(text: str) -> str:
    """按 `_SEMANTIC_CATEGORIES`（唯一事实源）判定文本的操作类别；无命中返回 ""。

    最长动词优先（"新增"先于单字"加"），避免单字抢先误撞。
    """
    _s = str(text or "")
    for _v, _c in _VERB_CATEGORY_SORTED:
        if _v and _v in _s:
            return _c
    return ""


def mutation_flow_kind(operation: str, has_data_entry: bool) -> str:
    """变更型操作的动作编排形态（**唯一决策点**）。

    - ``dialog_form``：需录入且操作是触发型（新增/编辑/导入…）
      → ``click(触发) → 录入(in_dialog) → click(确认,in_dialog)``
    - ``inline_form``：需录入但操作本身即提交型（提交/保存）、或只读检索型
      → ``录入 → click(操作控件)``
    - ``trigger_confirm``：无需录入且为删除型 → ``click(触发) → [click(确认,in_dialog)]``
    - ``plain``：其余 → ``click(操作控件)``
    """
    _op = str(operation or "")
    # ★ 删除型操作**不产生录入界面**（确认框即可）：即便用例带了录入段，那些字段也
    #   不是本次操作的字段 —— 实测 TC-REQ-028-I01/061-P01 用创建表单的录入段+保存提交
    #   冒充"删除" → 删除从未发生（列表行数未变、拒绝反馈不出现）。形态由**操作类型**
    #   决定，不由"模板习惯"决定。
    if classify_operation_category(_op) == "delete":
        return "trigger_confirm"
    # 触发判据用**动词集合**（已剔除纯录入描述词），而非类别：
    #   import 类别同时含"导入/上传"（真开窗）与"输入"（纯录入描述），
    #   只有前者才允许在录入之前插入一次点击。
    _is_trigger = any(_v in _op for _v in _TRIGGER_VERBS_ORDERED)
    if has_data_entry:
        return "dialog_form" if _is_trigger else "inline_form"
    return "plain"


def infer_confirm_hint(operation: str, dom_buttons: list | None = None) -> str:
    """推断**确认控件**文本（弹窗/对话框内的提交控件）。

    返回 "" 表示"页面上没有可采信的确认控件信号"（弹窗内控件不在静态 DOM 快照中，
    属预期）；调用方按形态决定是否使用通用确认语义兜底。
    """
    _btns = [str(b) for b in (dom_buttons or [])]
    # 1. DOM 中既有的提交型按钮（页面真实存在，最可信）
    for _b in _btns:
        if any(_v in _b for _v in CONFIRM_VERBS):
            return _b
    # 2. 操作自身即提交语义（如 operation="提交工单" → "提交"）
    for _v in sorted(CONFIRM_VERBS, key=len, reverse=True):
        if _v in str(operation or ""):
            return _v
    return ""


def build_interaction_actions(
    operation: str,
    data_action: dict | list | None,
    trigger_hint: str,
    confirm_hint: str = "",
) -> list[dict]:
    """按 `mutation_flow_kind` 组装「导航之后」的交互动作序列（**单一实现**）。

    :param operation: 操作意图文本（类别判定来源）
    :param data_action: 录入动作（fill / select_option / upload_file / check）；
        支持单个 dict 或多个 dict 的列表；无录入为 ``None``
    :param trigger_hint: 页面上的**触发/操作控件**文本（如"新增资产"/"编辑"/"查询"）
    :param confirm_hint: **确认控件**文本（`infer_confirm_hint` 结果，可空）

    ★ 根因修复(2026-09-27)：变更型表单通常有**多个必填字段**（名称/价格/库存/勾选…），
      原先只接受单个 data_action → 只录入一个字段 → 客户端校验拦截提交 → 结构化执行
      必败（实测 TC-REQ-002-P01）。现支持动作列表，一条用例可完整录入全部必填字段。
      通用性：是否多字段由调用方按需求文本决定，本函数只负责按形态展开编排。
    """
    _trigger = str(trigger_hint or "")
    _confirm = str(confirm_hint or "")
    # 归一化为动作列表（dict → [dict]；None → []）
    if isinstance(data_action, list):
        _data_actions = [a for a in data_action if isinstance(a, dict)]
    elif isinstance(data_action, dict):
        _data_actions = [data_action]
    else:
        _data_actions = []
    _has_entry = bool(_data_actions)
    _kind = mutation_flow_kind(operation, _has_entry)

    if _kind == "dialog_form" and _trigger:
        _acts = [{"type": "click", "target_hint": _trigger, "in_dialog": False}]
        _acts.extend({**a, "in_dialog": True} for a in _data_actions)
        _acts.append(
            {
                "type": "click",
                # 弹窗内控件不在静态快照 → 用通用确认语义（Step4 支持逗号候选）
                "target_hint": _confirm or "保存,提交,确定,确认",
                "in_dialog": True,
            }
        )
        return _acts

    if _kind == "trigger_confirm" and _trigger:
        _acts = [{"type": "click", "target_hint": _trigger, "in_dialog": False}]
        if _confirm:
            # 仅在页面上确有确认控件信号时才补确认步，否则维持单次点击（不臆造交互）
            _acts.append(
                {"type": "click", "target_hint": _confirm, "in_dialog": True}
            )
        return _acts

    if _has_entry:  # inline_form（含只读检索型）
        return list(_data_actions) + [
            {"type": "click", "target_hint": _trigger or "提交按钮"}
        ]

    return [{"type": "click", "target_hint": _trigger or "提交按钮"}]


def build_operation_entry_action(
    op_type: str,
    *,
    error_case: bool = False,
) -> dict | None:
    """按**操作类型**产出"该操作自身的最小录入动作"（唯一实现）。

    适用面: 录入面就是**单一控件**、不依赖字段规格的操作 ——
      ``search``    → 向查询输入框填检索词（异常路径给非法字符，边界给空串）
      ``filter_op`` → 选定下拉项（下拉项有限，异常/边界都用首个真实可选项）
      ``import_op`` → 选用待上传文件（异常路径给非法扩展名，边界给空文件）
    返回 None 的类型（``form``/``export_op``/``delete_op``/``view``/
    ``security_behavior``）不在此装录入动作：表单的完整字段录入由字段规格 SSOT
    （``build_form_data_actions``）承担；导出只是"点一次"；删除/查看/访问控制
    没有录入面。

    通用性(§1): 判据只有操作类型（动词→类别 SSOT 的上游分类）与通用控件语义，
      值由 ``value_generator`` 按类型生成，零业务硬编码，跨系统/行业成立。
    """
    from src.pipeline.step3.value_generator import generate_invalid_value_from_type

    if op_type == "search":
        return {
            "type": "fill",
            "target_hint": "查询输入框, 搜索框, 搜索",
            "value": (
                generate_invalid_value_from_type("string", "名称") if error_case else ""
            ),
        }
    if op_type == "filter_op":
        # 下拉项是**有限枚举**，"不存在的选项"无法被选中（选不中=动作失败，非被测激励）；
        # 筛选的激励是"应用一个筛选条件"，故异常/边界统一取首个真实可选项（执行端哨兵）。
        return {
            "type": "select_option",
            "target_hint": "下拉选择框, 筛选",
            "option_text": "第一项",
        }
    if op_type == "import_op":
        return {
            "type": "upload_file",
            "target_hint": "文件上传控件, 上传",
            "file": "invalid_format.exe" if error_case else "empty.csv",
        }
    return None


# 对象提取时剔除的虚词/修饰词（不含业务实体词，保持跨行业通用）
_OPERATION_NOISE_WORDS: frozenset[str] = frozenset(
    {
        "用户",
        "点击",
        "批量",
        "单个",
        "执行",
        "进行",
        "通过",
        "系统",
        "自动",
        "按钮",
        "操作",
        "页面",
        "功能",
        "当前",
        "支持",
        "需要",
        "可以",
        "本地",
        "数据",
        "信息",
        "列表",
        "记录",
        "结果",
        "管理",
        "模块",
        "流程",
        "模版",
        "模板",
        "相关",
        "所有",
        "查看",
        "入口",
        "方式",
        "规范",
    }
)

# 尾部语气/连接助词（对象词尾部剥离）
_OPERATION_TRAILING_PARTICLES: str = "的和与及或等把将被让向从在到"


def _extract_operation_objects(operation: str) -> list[str]:
    """从 operation 提取操作对象名词（确定性规则，零 AI）。

    策略：
    1. 剔除动作动词（_SEMANTIC_CATEGORIES 全集，长词优先）
    2. 取剩余连续中文段，剔除虚词/修饰词
    3. 剥离尾部助词；候选词 ≥2 字
    4. 对 ≥3 字候选追加其 2 字前缀（中文对象名词通常前置，如「辅材编号」→「辅材」）

    Returns:
        去重保序的对象词列表；无法提取时返回 []（调用方按"无法证伪→保守允许"处理）
    """
    if not operation:
        return []
    import re as _re

    text = str(operation)
    verbs = sorted(
        {v for kws in _SEMANTIC_CATEGORIES.values() for v in kws}, key=len, reverse=True
    )
    for v in verbs:
        text = text.replace(v, " ")
    candidates: list[str] = []
    for seg in _re.findall(r"[\u4e00-\u9fff]{2,}", text):
        for noise in _OPERATION_NOISE_WORDS:
            seg = seg.replace(noise, " ")
        for part in seg.split():
            part = part.strip(_OPERATION_TRAILING_PARTICLES)
            if len(part) >= 2 and part not in _OPERATION_NOISE_WORDS:
                candidates.append(part)
                if len(part) >= 3:
                    candidates.append(part[:2])
    seen: set[str] = set()
    out: list[str] = []
    for o in candidates:
        if o not in seen:
            seen.add(o)
            out.append(o)
    return out


def _operation_object_matches_page(operation: str, page_context: str) -> bool:
    """操作对象与页面语境的一致性校验（跨页面搜索的语义闸门）。

    判定（保守原则：无法证伪则允许，可证伪才拒绝）：
    - 对象词为空 → True（operation 无明确对象，无法证伪）
    - 页面语境为空 → True（无证据，无法证伪）
    - 任一对象词出现在页面语境 → True
    - 否则 → False（对象与页面语义冲突，禁止张冠李戴）
    """
    objects = _extract_operation_objects(operation)
    if not objects:
        return True
    if not page_context:
        return True
    return any(o in page_context for o in objects)


def _match_button_by_keywords(operation: str, dom_buttons: list[str]) -> str | None:
    """★ Fix47 + Fix52 (2026-07-09): 用操作关键词匹配 DOM 中的实际按钮文本。

    策略（通用，无业务硬编码）：
    1. 从 operation 中提取中文关键词（2-3字，排除通用虚词）
    2. 用关键词匹配 dom_buttons 中的按钮文本（部分匹配）
    3. 按匹配度排序：完全匹配 > 前缀匹配 > 包含匹配
    4. ★ Fix52: 增加动作动词反向匹配 —— 从按钮文本中提取动作动词，
       与 operation 的语义类别（查询/新建/删除/保存/导出等）匹配
    5. ★ Fix52: 搜索/查询类特殊处理 —— 如果 operation 是查询语义但按钮中没有
       "查询/搜索"类动作动词，返回 None（由调用方决定用回车或通用模板）
    6. 无匹配时返回 None（由调用方使用通用描述兜底）
    """
    if not dom_buttons or not operation:
        return None

    import re

    op_lower = operation.lower()

    # ★ Fix52: 先识别 operation 的语义类别
    #   通用语义分类（无业务硬编码）
    #   ★ Fix52c: 只取第一个出现的动作动词作为主语义类别，避免 operation 包含多动作时误匹配
    #     如"查询辅材信息，并进行批量导入、单个新建" → 主语义是 query，不是 import/create
    #   ★ v26: 提升至模块级 _SEMANTIC_CATEGORIES（供 _extract_operation_objects 复用，数据不变）
    _semantic_categories = _SEMANTIC_CATEGORIES
    # 收集所有动作关键词并按长度降序（长词优先匹配，避免"查"先于"查询"匹配）
    _all_action_keywords = []
    for cat, kws in _semantic_categories.items():
        for kw in kws:
            _all_action_keywords.append((kw, cat))
    _all_action_keywords.sort(key=lambda x: -len(x[0]))

    # 找到第一个出现的动作动词 → 主语义类别
    primary_category = None
    first_pos = len(op_lower)
    for kw, cat in _all_action_keywords:
        pos = op_lower.find(kw)
        if pos >= 0 and pos < first_pos:
            first_pos = pos
            primary_category = cat

    op_categories = {primary_category} if primary_category else set()

    # ★ Fix52: 动作动词映射表 —— 按钮文本前缀 → 语义类别
    #   通用，无业务硬编码
    _action_verb_to_category = {
        "新建": "create",
        "新增": "create",
        "添加": "create",
        "创建": "create",
        "删除": "delete",
        "移除": "delete",
        "编辑": "edit",
        "修改": "edit",
        "更新": "edit",
        "保存": "save",
        "提交": "save",
        "确认": "save",
        "确定": "save",
        "导出": "export",
        "下载": "export",
        "导入": "import",
        "上传": "import",
        "取消": "cancel",
        "关闭": "cancel",
        "启用": "enable",
        "停用": "enable",
        "复制": "copy",
        "打标": "tag",
        "标记": "tag",
        "刷新": "refresh",
        "重置": "refresh",
        "查询": "query",
        "搜索": "query",
        "筛选": "query",
        "查找": "query",
    }

    # 提取 operation 中的中文关键词
    cn_chars = re.findall(r"[\u4e00-\u9fff]", operation)
    cn_words = []
    for length in (2, 3):
        for i in range(len(cn_chars) - length + 1):
            cn_words.append("".join(cn_chars[i : i + length]))
    _stop_words = {
        "进行",
        "查看",
        "操作",
        "管理",
        "功能",
        "系统",
        "页面",
        "当前",
        "所有",
        "相关",
        "配方",
        "物料",
        "产品",
        "数据",
        "信息",
        "列表",
        "明细",
        "记录",
        "结果",
    }
    keywords = [w for w in cn_words if w not in _stop_words]

    if not keywords and not op_categories:
        return None

    # 动作关键词集合（用于权重计算）
    _action_keywords = set(_action_verb_to_category.keys())

    def _match_score(btn_text: str) -> int:
        score = 0
        has_action_match = False

        # 1) 正向关键词匹配（原有逻辑）
        for kw in keywords:
            if kw == btn_text:
                score += 10
                if kw in _action_keywords:
                    has_action_match = True
            elif btn_text.startswith(kw):
                score += 5
                if kw in _action_keywords:
                    has_action_match = True
            elif kw in btn_text:
                score += 3
                if kw in _action_keywords:
                    has_action_match = True

        # 2) ★ Fix52: 反向动作动词匹配
        #    从按钮文本中提取动作动词（不限前缀，包含即算），看是否匹配 operation 的主语义类别
        btn_lower = btn_text.lower()
        btn_verb_category = None
        btn_verb_len = 0
        for verb, cat in _action_verb_to_category.items():
            if verb in btn_lower and len(verb) > btn_verb_len:
                btn_verb_category = cat
                btn_verb_len = len(verb)

        if btn_verb_category and btn_verb_category in op_categories:
            score += 8  # 语义类别匹配权重高
            has_action_match = True

        # ★ Fix52c: 通用类别保护
        #   如果 operation 有明确的主语义类别（primary_category），
        #   则只接受主语义类别匹配的按钮，避免次要动作关键词误匹配
        if primary_category:
            # 按钮必须满足：要么动词类别匹配主语义，要么文本中直接包含主语义的动作词
            primary_verbs = _semantic_categories.get(primary_category, set())
            btn_has_primary_verb = any(v in btn_lower for v in primary_verbs)
            if not btn_has_primary_verb and btn_verb_category != primary_category:
                # 按钮与主语义不相关 → 拒绝（避免次要动作误匹配）
                return 0

        # 只有当至少有一个动作关键词匹配时，才认为有效
        if not has_action_match:
            return 0
        return score

    scored = [(btn, _match_score(btn)) for btn in dom_buttons]
    scored.sort(key=lambda x: x[1], reverse=True)

    if scored[0][1] > 0:
        _log.debug(
            "[RuleExtract] Fix52: 按钮匹配 '%s' → '%s' (score=%d, cats=%s, keywords=%s)",
            operation,
            scored[0][0],
            scored[0][1],
            op_categories,
            keywords,
        )
        return scored[0][0]

    return None


def _match_input_by_field_name(field_name: str, dom_inputs: list[str]) -> str | None:
    """★ Fix47: 用 RAG 字段名匹配 DOM 中的实际输入框 placeholder。

    策略（通用）：
    1. 用 field_name 匹配输入框 placeholder 中的关键词
    2. 英文 field_name 也尝试匹配中文 placeholder（通过单字拆分）
    3. 无匹配时返回 None（由调用方使用通用描述兜底）
    """
    if not dom_inputs or not field_name:
        return None

    field_lower = field_name.lower().strip()

    # 尝试精确匹配
    for inp in dom_inputs:
        if field_lower in inp.lower():
            return inp

    # 尝试中文单字匹配（如 "name" → 匹配 "名称" 所在的 placeholder）
    # 从 dom_inputs 反向找：如果 field_name 是英文且 dom_input 有中文，用通用匹配
    import re

    for inp in dom_inputs:
        cn_chars = re.findall(r"[\u4e00-\u9fff]", inp)
        if cn_chars:
            cn_text = "".join(cn_chars)
            if field_lower in cn_text.lower():
                return inp

    return None
