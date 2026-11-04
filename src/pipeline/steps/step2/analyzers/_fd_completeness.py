# -*- coding: utf-8 -*-
"""需求完整性检查（纯算法）。提取自 feature_driven.py。"""

import json
from src.core.guarded import guard
import logging
import re
from pathlib import Path
from typing import Any, Optional

from src.pipeline.steps.step2.breakdown_rules.predicate import (
    BEHAVIOR_VERBS,
    MIN_ATOMIC_ACTION_LEN,
    MODAL_CONSTRAINT,
    has_executable_predicate,
    is_behavior_predicate,
)

_log = logging.getLogger(__name__)

# ══════════════════════════════════════════════════
# 6. 需求完整性检查（纯算法，不阻断拆解，生成建议供需求方审核）
# ══════════════════════════════════════════════════

# ★ 改进7: 完整性检查关键词（从配置文件加载）
_VAGUE_FEEDBACK_KEYWORDS = []
_BROAD_DESCRIPTION_KEYWORDS = []
_VAGUE_OPERATION_KEYWORDS = []
_PERMISSION_KEYWORDS = []
_UNTESTABLE_FEEDBACK_KEYWORDS = []
_COMPLETENESS_KEYWORDS_LOADED = False


def _load_completeness_keywords():
    """从配置文件加载完整性检查关键词。"""
    global _VAGUE_FEEDBACK_KEYWORDS, _BROAD_DESCRIPTION_KEYWORDS
    global _VAGUE_OPERATION_KEYWORDS, _PERMISSION_KEYWORDS, _UNTESTABLE_FEEDBACK_KEYWORDS
    global _COMPLETENESS_KEYWORDS_LOADED

    if _COMPLETENESS_KEYWORDS_LOADED:
        return

    with guard("_load_completeness_keywords: config_path = str( Path(__file__).re", log=_log, level=logging.ERROR):
        config_path = str(
            Path(__file__).resolve().parent.parent.parent.parent.parent
            / "config"
            / "operation_verbs.json"
        )

        if Path(config_path).exists():
            with open(config_path, "r", encoding="utf-8") as f:
                dict_config = json.load(f)

            completeness_check = dict_config.get("completeness_check", {})

            # 合并所有语言的关键词
            for lang in ["zh", "en"]:
                if lang in completeness_check.get("vague_feedback", {}):
                    _VAGUE_FEEDBACK_KEYWORDS.extend(
                        completeness_check["vague_feedback"][lang]
                    )
                if lang in completeness_check.get("broad_description", {}):
                    _BROAD_DESCRIPTION_KEYWORDS.extend(
                        completeness_check["broad_description"][lang]
                    )
                if lang in completeness_check.get("vague_operation", {}):
                    _VAGUE_OPERATION_KEYWORDS.extend(
                        completeness_check["vague_operation"][lang]
                    )
                if lang in completeness_check.get("permission_keywords", {}):
                    _PERMISSION_KEYWORDS.extend(
                        completeness_check["permission_keywords"][lang]
                    )
                if lang in completeness_check.get("untestable_feedback", {}):
                    _UNTESTABLE_FEEDBACK_KEYWORDS.extend(
                        completeness_check["untestable_feedback"][lang]
                    )

            _log.info(
                "[feature_driven] 从配置文件加载完整性检查关键词: vague_feedback=%d, broad_description=%d, vague_operation=%d, permission_keywords=%d, untestable_feedback=%d",
                len(_VAGUE_FEEDBACK_KEYWORDS),
                len(_BROAD_DESCRIPTION_KEYWORDS),
                len(_VAGUE_OPERATION_KEYWORDS),
                len(_PERMISSION_KEYWORDS),
                len(_UNTESTABLE_FEEDBACK_KEYWORDS),
            )
        else:
            _log.warning(
                "[feature_driven] 配置文件不存在: %s，使用默认关键词", config_path
            )

    _COMPLETENESS_KEYWORDS_LOADED = True


# 操作动词关键词（用于检测 operation 字段是否含具体动作）
# 判据唯一实现在 breakdown_rules.predicate（交互动词/系统行为/情态约束/接口四类）


# 笼统反馈词（expected_feedback 过于抽象）
# ★ 改进7: 从配置文件动态加载
def _build_vague_feedback_pattern():
    """从配置文件构建笼统反馈词正则。"""
    _load_completeness_keywords()
    if _VAGUE_FEEDBACK_KEYWORDS:
        keywords_pattern = "|".join(re.escape(w) for w in _VAGUE_FEEDBACK_KEYWORDS)
        return re.compile(rf"^(?:{keywords_pattern})$", re.IGNORECASE)
    # 兜底默认
    return re.compile(
        r"^(成功|正常|完成|生效|可用|没问题|可以|好了|OK|ok|"
        r"完成操作|操作完成|操作成功|处理完成|处理成功|系统正常|功能正常)$",
        re.IGNORECASE,
    )


_REQ_VAGUE_FEEDBACK = _build_vague_feedback_pattern()


# 宽泛描述词（description/operation 过于笼统）
# ★ 改进7: 从配置文件动态加载
def _build_broad_desc_pattern():
    """从配置文件构建宽泛描述词正则。"""
    _load_completeness_keywords()
    if _BROAD_DESCRIPTION_KEYWORDS:
        keywords_pattern = "|".join(re.escape(w) for w in _BROAD_DESCRIPTION_KEYWORDS)
        return re.compile(rf"(?:{keywords_pattern})(?=[，。！？\s]|$)", re.IGNORECASE)
    # 兜底默认
    return re.compile(
        r"管理正常(?=[，。！？\s]|$)|"
        r"运行正常(?=[，。！？\s]|$)|"
        r"功能正常(?=[，。！？\s]|$)|"
        r"系统正常(?=[，。！？\s]|$)|"
        r"功能完善(?=[，。！？\s]|$)|"
        r"正常工作(?=[，。！？\s]|$)|"
        r"正常运作(?=[，。！？\s]|$)|"
        r"工作正常(?=[，。！？\s]|$)",
        re.IGNORECASE,
    )


_REQ_BROAD_DESC = _build_broad_desc_pattern()


# 笼统操作词
# ★ 改进7: 从配置文件动态加载
def _build_vague_op_pattern():
    """从配置文件构建笼统操作词正则。"""
    _load_completeness_keywords()
    if _VAGUE_OPERATION_KEYWORDS:
        keywords_pattern = "|".join(re.escape(w) for w in _VAGUE_OPERATION_KEYWORDS)
        return re.compile(rf"(?:{keywords_pattern})", re.IGNORECASE)
    # 兜底默认
    return re.compile(
        r"操作系统|管理系统|处理功能|操作功能|管理功能|使用系统|"
        r"操作系统功能|处理系统",
        re.IGNORECASE,
    )


_REQ_VAGUE_OP = _build_vague_op_pattern()


# 权限/角色关键词（需要前置条件）
# ★ 改进7: 从配置文件动态加载
def _build_permission_kw_pattern():
    """从配置文件构建权限关键词正则。"""
    _load_completeness_keywords()
    if _PERMISSION_KEYWORDS:
        keywords_pattern = "|".join(re.escape(w) for w in _PERMISSION_KEYWORDS)
        return re.compile(rf"(?:{keywords_pattern})", re.IGNORECASE)
    # 兜底默认
    return re.compile(
        r"权限|角色|管理员|超管|审批|负责人|" r"授权(?![按钮])|授予", re.IGNORECASE
    )


_REQ_PERMISSION_KW = _build_permission_kw_pattern()

# 角色词位于句首并紧跟限定语/行为谓词 → 执行主体已写在 operation 里
_IDENTITY_SCOPED = re.compile(
    rf"^(?:{_REQ_PERMISSION_KW.pattern})[^，,。；;]{{0,6}}"
    rf"(?:可(?![能])|{MODAL_CONSTRAINT.pattern}|{BEHAVIOR_VERBS.pattern})"
)


def _check_requirement_completeness(req: dict[str, Any]) -> list[dict[str, Any]]:
    """检查单条需求的完整性（纯算法，不调 AI）。

    ★ 设计原则：
      - 不阻断拆解流程，仅附加 issues 到 req
      - 不臆造业务规则，仅基于结构性规则检测
      - 生成"建议"供需求方参考，需求方可同意/修改/驳回
      - 防幻觉红线：不生成文档没写的具体业务内容

    5 类检查规则：
      1. missing_steps:      operation 为空/过短/未表达可执行或可观察的系统行为
      2. missing_expected:   expected_feedback 为空或过于笼统
      3. too_broad_desc:     description/operation 含"XX管理正常"等宽泛描述
      4. vague_operation:    operation 含"操作系统"/"管理系统"等笼统词
      5. missing_condition:  涉及权限/角色但 condition_expr 为空

    Args:
        req: 需求字典（含 operation, expected_feedback, condition_expr, description 等）

    Returns:
        issues 列表 [{"type", "severity", "message", "suggestion"}, ...]
        severity: "warning"（需关注） / "info"（建议优化）
    """
    issues: list[dict[str, Any]] = []

    operation = (req.get("operation") or "").strip()
    expected = (req.get("expected_feedback") or "").strip()
    condition = (req.get("condition_expr") or "").strip()
    description = (req.get("description") or "").strip()
    source_quote = (req.get("source_quote") or "").strip()

    # ── 规则1: 缺少可执行的动作 ──
    # operation 为空 / 不足以表达「动作+对象」/ 未陈述任何可执行或可观察行为
    if not operation:
        issues.append(
            {
                "type": "missing_steps",
                "severity": "warning",
                "message": "操作步骤为空，无法确定具体动作",
                "suggestion": "请补充具体操作步骤（如'点击保存按钮'、'输入用户名和密码'）",
            }
        )
    elif len(operation) < MIN_ATOMIC_ACTION_LEN and not is_behavior_predicate(operation):
        issues.append(
            {
                "type": "missing_steps",
                "severity": "warning",
                "message": f"操作步骤过短（{len(operation)}字），无法确定具体动作与对象：{operation}",
                "suggestion": "请用「动作+对象」描述（如'点击XX按钮'、'返回XX列表'、'记录XX日志'）",
            }
        )
    elif not has_executable_predicate(operation):
        issues.append(
            {
                "type": "missing_steps",
                "severity": "warning",
                "message": f"操作步骤未表达可执行或可观察的系统行为：{operation[:30]}",
                "suggestion": (
                    "请用「动作+对象」或系统行为描述，如'点击XX按钮'、'输入XX内容'、"
                    "'列表展示XX列'、'接口返回XX字段'"
                ),
            }
        )

    # ── 规则2: 缺少预期行为 ──
    # expected_feedback 为空 / 只含"成功"/"正常"等笼统词
    # ★ 修复(07-05d): 去掉 len(expected) < 15 限制（"权限配置保存成功"9字也合法）
    #   改为完全匹配 _REQ_VAGUE_FEEDBACK 正则
    if not expected or (
        len(expected) < 3 and not is_behavior_predicate(operation)
    ):
        issues.append(
            {
                "type": "missing_expected",
                "severity": "warning",
                "message": "预期行为为空或过短，无法确定可观察反馈",
                "suggestion": "请补充可观察的反馈（如'显示XX提示'、'跳转到XX页面'、'列表刷新显示新数据'）",
            }
        )
    elif _REQ_VAGUE_FEEDBACK.match(expected):
        issues.append(
            {
                "type": "missing_expected",
                "severity": "info",
                "message": f"预期行为过于笼统：{expected[:30]}",
                "suggestion": "请细化可观察反馈（如'显示保存成功提示'、'页面跳转到详情页'）",
            }
        )

    # ── 规则3: 描述过于宽泛 ──
    # description 或 operation 含"管理正常"/"功能正常"等无具体测试点
    check_text = f"{description} {operation}"
    if _REQ_BROAD_DESC.search(check_text):
        broad_match = _REQ_BROAD_DESC.search(check_text)
        issues.append(
            {
                "type": "too_broad_desc",
                "severity": "warning",
                "message": f"需求描述过于宽泛：'{broad_match.group(0)}'，无法直接测试",
                "suggestion": (
                    "请细化具体功能点，例如拆分为：\n"
                    "  - 新增XX（新增操作 + 保存成功反馈）\n"
                    "  - 查询XX（查询操作 + 列表显示反馈）\n"
                    "  - 修改XX（编辑操作 + 更新成功反馈）\n"
                    "  - 删除XX（删除操作 + 二次确认反馈）"
                ),
            }
        )

    # ── 规则4: 操作过于笼统 ──
    # operation 含"操作系统"/"管理系统"等无具体动作
    if _REQ_VAGUE_OP.search(operation) and len(operation) < 20:
        issues.append(
            {
                "type": "vague_operation",
                "severity": "warning",
                "message": f"操作描述过于笼统：{operation[:40]}",
                "suggestion": "请列出具体操作步骤（如'1.进入XX页面 2.点击XX按钮 3.填写XX字段'）",
            }
        )

    # ── 规则5: 缺少前置条件 ──
    # 只判需求本体 operation：description 是引擎模板（含模块名），会把「权限模块」
    # 误当成需求涉及权限；operation 以角色词开头并紧跟限定语时，执行主体已内嵌。
    perm_in_op = bool(_REQ_PERMISSION_KW.search(operation))
    if perm_in_op and not condition and not _IDENTITY_SCOPED.search(operation):
        issues.append(
            {
                "type": "missing_condition",
                "severity": "info",
                "message": "涉及权限/角色操作，但未指定前置条件",
                "suggestion": "请补充前置条件（如'需具备XX权限'、'需登录管理员账号'、'需先创建XX角色'）",
            }
        )

    # ── 规则6: 不可测试的预期反馈 ──
    # expected_feedback 包含主观/相对/不可量化词，无法在自动化E2E测试中做精确断言
    if _REQ_UNTESTABLE_FEEDBACK.search(expected):
        untestable_match = _REQ_UNTESTABLE_FEEDBACK.search(expected)
        issues.append(
            {
                "type": "untestable_feedback",
                "severity": "warning",
                "message": f"预期行为包含不可自动化测试的表述：'{untestable_match.group(0)}'",
                "suggestion": (
                    "请改为可量化的可观察反馈，例如：\n"
                    "  - '响应很快' → '页面在3秒内完成加载'\n"
                    "  - '体验良好' → '操作完成后显示成功提示'\n"
                    "  - '界面美观' → '所有元素对齐且间距一致'"
                ),
            }
        )

    # ── 规则7: operation 与 expected_feedback 操作类型不匹配 ──
    # 如 operation="点击删除按钮" 但 expected_feedback="数据被保存"（删除 vs 保存）
    # 规则6（untestable）和自矛盾检测（detect_contradictions）已覆盖
    # 此处仅做结构性检查：operation 含动作词但 expected_feedback 不含对应结果词
    _op_action = _extract_action_type(operation)
    if _op_action and expected:
        _fb_match = _check_action_feedback_consistency(_op_action, expected)
        if _fb_match is False:
            issues.append(
                {
                    "type": "action_feedback_mismatch",
                    "severity": "info",
                    "message": f"操作类型'{_op_action}'与预期反馈'{expected[:30]}'可能不匹配",
                    "suggestion": "请确认操作与预期反馈是否一致（如删除操作→删除成功的反馈，而非保存成功的反馈）",
                }
            )

    return issues


# ── 不可测试词（主观/相对/不可量化，无法自动化断言）──
# 唯一来源：operation_verbs.json → completeness_check.untestable_feedback


def _build_untestable_feedback_pattern():
    """构建不可测试反馈词正则；配置为空时该规则不启用。"""
    _load_completeness_keywords()
    if not _UNTESTABLE_FEEDBACK_KEYWORDS:
        # 空 pattern 会匹配一切，此处用永不匹配的正则表达「规则未启用」
        return re.compile(r"(?!)")
    keywords_pattern = "|".join(re.escape(w) for w in _UNTESTABLE_FEEDBACK_KEYWORDS)
    return re.compile(rf"(?:{keywords_pattern})", re.IGNORECASE)


_REQ_UNTESTABLE_FEEDBACK = _build_untestable_feedback_pattern()


# ── 操作类型提取（用于 action_feedback_mismatch 检测）──
_ACTION_TYPE_MAP: dict[str, list[str]] = {
    "delete": ["删除", "移除", "清除", "销毁", "delete", "remove"],
    "save": ["保存", "存储", "提交", "save", "submit", "commit"],
    "create": ["新增", "添加", "创建", "新建", "add", "create", "new"],
    "edit": ["修改", "编辑", "更新", "变更", "edit", "update", "modify"],
    "query": ["查询", "搜索", "检索", "查看", "search", "query", "view", "find"],
    "export": ["导出", "下载", "export", "download"],
    "import": ["导入", "上传", "import", "upload"],
    "approve": ["审批", "审核", "批准", "通过", "approve", "review", "accept"],
    "reject": ["驳回", "拒绝", "退回", "reject", "deny", "refuse"],
    "login": ["登录", "登入", "login", "sign in"],
    "logout": ["登出", "退出", "logout", "sign out"],
    "enable": ["启用", "激活", "开启", "enable", "activate"],
    "disable": ["禁用", "停用", "关闭", "disable", "deactivate"],
}


def _extract_action_type(operation: str) -> Optional[str]:
    """从 operation 中提取操作类型。"""
    op_lower = operation.lower()
    for action_type, keywords in _ACTION_TYPE_MAP.items():
        for kw in keywords:
            if kw.lower() in op_lower:
                return action_type
    return None


def _check_action_feedback_consistency(
    action_type: str, expected_feedback: str
) -> Optional[bool]:
    """检查操作类型与预期反馈是否一致。

    返回 True=一致, False=可能不一致, None=无法判断。
    """
    fb_lower = expected_feedback.lower()
    # 同一操作类型的关键词出现在反馈中 → 一致
    _own_keywords = _ACTION_TYPE_MAP.get(action_type, [])
    for kw in _own_keywords:
        if kw.lower() in fb_lower:
            return True

    # 检查是否与其他操作类型的关键词冲突
    _conflict_types = {
        "delete": ["save", "create"],
        "save": ["delete"],
        "create": ["delete"],
        "enable": ["disable"],
        "disable": ["enable"],
        "approve": ["reject"],
        "reject": ["approve"],
        "login": ["logout"],
        "logout": ["login"],
    }
    conflicts = _conflict_types.get(action_type, [])
    for conflict_type in conflicts:
        for kw in _ACTION_TYPE_MAP.get(conflict_type, []):
            if kw.lower() in fb_lower:
                return False
    return None
