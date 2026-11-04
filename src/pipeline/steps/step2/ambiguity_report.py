# -*- coding: utf-8 -*-
"""需求歧义主动上报（A1，L2→L3）—— 生成用例前主动产出结构化歧义报告。

背景（CAPABILITY_GAP_PRD）：
  - A1「需求理解与歧义识别」：高级工程师读需求时能指出「这里没写清楚」「这两条互相矛盾」，
    主动向产品/开发提问，而不是照着字面写用例。
  - 现状（§4 待核实 A1）：已有需求闸门 / Step2 需求拆解 / RAG / 幻觉日志，
    但「歧义主动上报」**未落地** —— 需求不清时仍会静默生成可能错的用例。
  - 与既有能力的边界：
      * `breakdown_rules/completeness.py`      → 只判「字段缺失/笼统」，产出 issue 供审核
      * `analyzers/_contradiction.py`          → 只判「同功能需求间语义矛盾」
      * 本模块                                 → 判「歧义」并**主动上报可澄清的问题清单**
        （歧义点 / 影响 / 建议澄清问题），且给出 should_block_generation 闸门信号

六类歧义规则（规则驱动、可解释、fail-open 不臆造业务内容）：
  1. conflicting_field_semantics : 字段含义冲突（必填 vs 选填、唯一 vs 可重复、允许 vs 禁止…）
  2. missing_precondition        : 缺少前置条件（涉及权限/角色/状态但无 condition）
  3. non_unique_expected         : 预期结果不唯一（「或」「视情况」「可能」等多分支表述）
  4. undefined_entity            : 涉及未定义实体（占位符 XX/某某、未定义字段引用）
  5. unquantified_constraint     : 约束未量化（「尽快」「大量」「若干」「适当」…）
  6. cross_req_conflict          : 跨需求冲突（同一操作对应互相矛盾的预期结果）

纯读分析：零 DB 写、零 DDL、零 AI 调用、零网络。全部数据来自调用方传入的需求三元组。
不阻断流水线：仅产出报告 + WARNING 级日志上报；是否拦截由调用方读 should_block_generation 决定。

[未复核-需 GLM/DeepSeek 把关]（新增模块，寇豆码 新增）
"""

from __future__ import annotations
from src.core.guarded import guard

import logging
import re
from datetime import datetime
from typing import Any, Optional

_log = logging.getLogger(__name__)

AMBIGUITY_SCHEMA_VERSION = "ambiguity-v1"

# severity → 是否阻断用例生成（high 阻断，medium/low 仅上报）
_BLOCKING_SEVERITIES: frozenset[str] = frozenset({"high"})

# ═══════════════════════════════════════════════════════════════════════════
# 规则 1：字段含义冲突（同一需求内出现互斥语义对）
# ═══════════════════════════════════════════════════════════════════════════
# 每项：(左侧词组, 右侧词组, 冲突描述)
_CONFLICT_PAIRS: tuple[tuple[tuple[str, ...], tuple[str, ...], str], ...] = (
    (("必填", "必须填写", "不能为空", "required"), ("选填", "可为空", "非必填", "optional"),
     "同一字段同时被描述为必填与选填"),
    (("唯一", "不可重复", "unique"), ("可重复", "允许重复", "duplicate"),
     "同一字段同时被描述为唯一与可重复"),
    (("允许", "可以", "支持"), ("禁止", "不允许", "不可以", "不支持"),
     "同一行为同时被描述为允许与禁止"),
    (("只读", "不可编辑", "readonly"), ("可编辑", "可修改", "editable"),
     "同一字段同时被描述为只读与可编辑"),
    (("自动", "系统自动"), ("手动", "人工填写", "手工"),
     "同一动作同时被描述为自动与手动"),
    (("同步", "实时"), ("异步", "延迟", "定时"),
     "同一处理同时被描述为同步与异步"),
)

# ═══════════════════════════════════════════════════════════════════════════
# 规则 2：缺少前置条件（涉及权限/角色/状态但 condition 为空）
# ═══════════════════════════════════════════════════════════════════════════
_PRECOND_TRIGGER_RE = re.compile(
    r"权限|角色|管理员|超管|审批|负责人|授权(?![按钮])|授予|"
    r"登录后|已登录|未登录|已审批|待审批|已提交|草稿|生效中|已作废|"
    r"仅.{0,6}可见|仅.{0,6}可操作",
    re.IGNORECASE,
)

# ═══════════════════════════════════════════════════════════════════════════
# 规则 3：预期结果不唯一（多分支/条件性表述）
# ═══════════════════════════════════════════════════════════════════════════
_NON_UNIQUE_RE = re.compile(
    r"或者|或(?![则者])|要么|视情况|根据情况|按需|酌情|"
    r"可能|也许|大概|应该会|一般会|通常|"
    r"等等|之类|以及其他|其他情况|若.{0,10}则.{0,10}否则|"
    r"either|or\s+may",
    re.IGNORECASE,
)

# ═══════════════════════════════════════════════════════════════════════════
# 规则 4：涉及未定义实体（占位符 / 泛指代词）
# ═══════════════════════════════════════════════════════════════════════════
_UNDEFINED_ENTITY_RE = re.compile(
    r"XX+|xx+|某某|某个(?!人)|相关(?:页面|字段|列表|模块|信息)|"
    r"对应(?:页面|字段|列表|模块|信息)|指定(?:页面|字段|列表|模块)|"
    r"若干(?:字段|项)|待定|TBD|tbd|/\*.*?\*/|\{\{.*?\}\}|<.*?待补.*?>",
)

# ═══════════════════════════════════════════════════════════════════════════
# 规则 5：约束未量化（缺少可断言的数值/阈值）
# ═══════════════════════════════════════════════════════════════════════════
_UNQUANTIFIED_RE = re.compile(
    r"尽快|快速|较快|及时|实时(?!同步)|大量|少量|若干|多个(?!选项)|"
    r"适当|合理|足够|充分|一定(?:时间|数量)|较多|较少|"
    r"性能良好|响应迅速|不卡顿|流畅",
    re.IGNORECASE,
)

# 类型元数据：type → (中文标签, 默认 severity)
_TYPE_META: dict[str, tuple[str, str]] = {
    "conflicting_field_semantics": ("字段含义冲突", "high"),
    "missing_precondition": ("缺少前置条件", "high"),
    "non_unique_expected": ("预期结果不唯一", "high"),
    "undefined_entity": ("涉及未定义实体", "medium"),
    "unquantified_constraint": ("约束未量化", "medium"),
    "cross_req_conflict": ("跨需求预期冲突", "high"),
}


# ═══════════════════════════════════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════════════════════════════════
def _req_text(req: dict[str, Any]) -> str:
    """把需求三元组拼为可检索文本（operation + expected_feedback + description）。"""
    parts = [
        req.get("operation"),
        req.get("expected_feedback"),
        req.get("description"),
        req.get("source_quote"),
    ]
    return " ".join(str(p) for p in parts if p)


def _req_condition(req: dict[str, Any]) -> str:
    """读取前置条件（兼容 condition / condition_expr 两种键名）。

    仓库内 ``standard_structure.py`` 产出 ``condition``，
    而 ``completeness.py`` 读取 ``condition_expr`` —— 此处两者都兼容。
    """
    for key in ("condition_expr", "condition", "precondition"):
        value = req.get(key)
        if value and str(value).strip():
            return str(value).strip()
    return ""


def _req_id(req: dict[str, Any], index: int) -> str:
    """获取需求标识（req_id 优先，回落索引）。"""
    return str(req.get("req_id") or "REQ-IDX-%03d" % (index + 1))


def _make_item(
    amb_type: str,
    ambiguity_point: str,
    impact: str,
    clarify_questions: list[str],
    req: dict[str, Any],
    req_index: int,
    field_name: str,
    quote: str,
    severity: Optional[str] = None,
) -> dict[str, Any]:
    """构造一条结构化歧义项。

    Args:
        amb_type: 歧义类型（_TYPE_META 的键）。
        ambiguity_point: 歧义点描述。
        impact: 对用例生成的影响。
        clarify_questions: 建议向产品/开发澄清的问题列表。
        req: 来源需求三元组。
        req_index: 需求在列表中的索引。
        field_name: 命中字段名。
        quote: 命中的原文片段（证据）。
        severity: 覆盖默认 severity。

    Returns:
        结构化歧义项 dict。
    """
    label, default_sev = _TYPE_META.get(amb_type, (amb_type, "medium"))
    sev = severity or default_sev
    return {
        "type": amb_type,
        "type_label": label,
        "severity": sev,
        "blocking": sev in _BLOCKING_SEVERITIES,
        # ── A1 要求的三个核心字段 ──
        "ambiguity_point": ambiguity_point,
        "impact": impact,
        "clarify_questions": list(clarify_questions),
        # ── 证据（可溯源，不臆造） ──
        "evidence": {
            "req_id": _req_id(req, req_index),
            "req_index": req_index,
            "field": field_name,
            "quote": (quote or "")[:200],
            "source_section": str(req.get("source_section") or ""),
            "source_quote": str(req.get("source_quote") or "")[:200],
        },
    }


# ═══════════════════════════════════════════════════════════════════════════
# 单条需求歧义检测
# ═══════════════════════════════════════════════════════════════════════════
def detect_ambiguities(req: dict[str, Any], req_index: int = 0) -> list[dict[str, Any]]:
    """检测单条需求的歧义（规则驱动，纯函数）。

    Args:
        req: 需求三元组（operation / expected_feedback / condition[_expr] /
            description / source_quote / source_section / req_id 均可缺失）。
        req_index: 需求在列表中的索引（写入证据便于溯源）。

    Returns:
        结构化歧义项列表；无歧义时返回空列表。
    """
    items: list[dict[str, Any]] = []
    operation = str(req.get("operation") or "").strip()
    expected = str(req.get("expected_feedback") or "").strip()
    condition = _req_condition(req)
    full_text = _req_text(req)

    # ── 规则 1：字段含义冲突（同一需求内出现互斥语义对） ──
    for left_words, right_words, desc in _CONFLICT_PAIRS:
        left_hit = next((w for w in left_words if w in full_text), "")
        right_hit = next((w for w in right_words if w in full_text), "")
        if left_hit and right_hit:
            items.append(
                _make_item(
                    "conflicting_field_semantics",
                    "%s：同一需求中同时出现「%s」与「%s」" % (desc, left_hit, right_hit),
                    "无法确定断言方向，按字面生成的用例有 50% 概率断言反向，"
                    "会产出假失败或假通过",
                    [
                        "该字段/行为最终应为「%s」还是「%s」？" % (left_hit, right_hit),
                        "两种表述是否分别对应不同角色或不同状态？若是，请补充触发条件。",
                    ],
                    req,
                    req_index,
                    "operation+expected_feedback",
                    full_text,
                )
            )

    # ── 规则 2：缺少前置条件（涉及权限/角色/状态但 condition 为空） ──
    trigger = _PRECOND_TRIGGER_RE.search(operation) or _PRECOND_TRIGGER_RE.search(expected)
    if trigger and not condition:
        hit = trigger.group(0)
        items.append(
            _make_item(
                "missing_precondition",
                "需求涉及「%s」但未声明前置条件（condition 为空）" % hit,
                "不知道以何种角色/状态进入，用例可能用错账号或错状态执行，"
                "导致 BLOCKED 或误判为产品缺陷",
                [
                    "执行该操作需要什么角色/权限（如管理员、审批人）？",
                    "执行前数据需处于什么状态（如待审批、已提交）？",
                    "无权限/错状态时的预期表现是什么（报错文案？按钮置灰？）",
                ],
                req,
                req_index,
                "operation" if _PRECOND_TRIGGER_RE.search(operation) else "expected_feedback",
                operation or expected,
            )
        )

    # ── 规则 3：预期结果不唯一（多分支/条件性表述） ──
    if expected:
        m = _NON_UNIQUE_RE.search(expected)
        if m:
            items.append(
                _make_item(
                    "non_unique_expected",
                    "预期结果含多分支/不确定表述「%s」，预期结果不唯一" % m.group(0),
                    "断言（Oracle）无法唯一确定，用例只能写成弱断言"
                    "（如「页面没报错」），失去值级校验能力",
                    [
                        "该操作的预期结果是否唯一？若有多个分支，请分别给出触发条件与对应结果。",
                        "「%s」具体对应哪几种可观察结果（提示文案 / 跳转页面 / 数据变化）？"
                        % m.group(0),
                    ],
                    req,
                    req_index,
                    "expected_feedback",
                    expected,
                )
            )

    # ── 规则 4：涉及未定义实体（占位符/泛指代词） ──
    m = _UNDEFINED_ENTITY_RE.search(full_text)
    if m:
        items.append(
            _make_item(
                "undefined_entity",
                "需求引用了未定义实体/占位符「%s」" % m.group(0),
                "实体未定义 → 无法定位页面元素或数据表，"
                "用例生成时只能猜测，是幻觉高发点",
                [
                    "「%s」具体指哪个页面/字段/实体？请给出准确名称。" % m.group(0),
                    "该实体在原型或接口文档中对应哪个位置？",
                ],
                req,
                req_index,
                "operation+expected_feedback",
                full_text,
            )
        )

    # ── 规则 5：约束未量化 ──
    m = _UNQUANTIFIED_RE.search(full_text)
    if m:
        items.append(
            _make_item(
                "unquantified_constraint",
                "需求含未量化约束「%s」，缺少可断言的阈值" % m.group(0),
                "无阈值 → 无法生成可判定的断言，只能跳过该校验点，形成覆盖盲区",
                [
                    "「%s」的量化标准是多少（如响应 ≤2s、条数 ≤100）？" % m.group(0),
                    "超出该标准时的预期表现是什么？",
                ],
                req,
                req_index,
                "operation+expected_feedback",
                full_text,
            )
        )

    return items


# ═══════════════════════════════════════════════════════════════════════════
# 跨需求歧义检测（同操作 → 互斥预期）
# ═══════════════════════════════════════════════════════════════════════════
_NEGATION_RE = re.compile(r"不|无法|未|禁止|失败|拒绝|驳回|报错|不能")


def detect_cross_requirement_ambiguities(
    reqs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """检测跨需求歧义：同一操作对应互相矛盾的预期结果。

    判定口径（保守，避免误报）：两条需求 operation 完全相同（去空白后），
    但 expected_feedback 不同，且其中恰有一条含否定词 —— 视为互斥预期。

    Args:
        reqs: 需求三元组列表。

    Returns:
        结构化歧义项列表。
    """
    items: list[dict[str, Any]] = []
    by_operation: dict[str, list[tuple[int, dict[str, Any]]]] = {}
    for idx, req in enumerate(reqs or []):
        key = re.sub(r"\s+", "", str(req.get("operation") or "")).strip()
        if key:
            by_operation.setdefault(key, []).append((idx, req))

    for op_key, group in by_operation.items():
        if len(group) < 2:
            continue
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                idx_a, req_a = group[i]
                idx_b, req_b = group[j]
                exp_a = str(req_a.get("expected_feedback") or "").strip()
                exp_b = str(req_b.get("expected_feedback") or "").strip()
                if not exp_a or not exp_b or exp_a == exp_b:
                    continue
                neg_a = bool(_NEGATION_RE.search(exp_a))
                neg_b = bool(_NEGATION_RE.search(exp_b))
                if neg_a == neg_b:
                    continue  # 同为肯定或同为否定 → 视为互补而非矛盾，不误报
                items.append(
                    _make_item(
                        "cross_req_conflict",
                        "需求 %s 与 %s 操作相同（%s）但预期互斥：「%s」 vs 「%s」"
                        % (
                            _req_id(req_a, idx_a),
                            _req_id(req_b, idx_b),
                            op_key[:40],
                            exp_a[:40],
                            exp_b[:40],
                        ),
                        "同操作两种互斥预期 → 无论生成哪条用例都必有一条失败，"
                        "会产生必然的假缺陷噪音",
                        [
                            "同一操作「%s」为何有两种互斥预期？是否漏写了区分条件"
                            "（角色/状态/入参）？" % op_key[:40],
                            "若两者都成立，请分别补充各自的前置条件。",
                        ],
                        req_a,
                        idx_a,
                        "expected_feedback",
                        "%s || %s" % (exp_a, exp_b),
                    )
                )
    return items


# ═══════════════════════════════════════════════════════════════════════════
# 报告装配 + 主动上报
# ═══════════════════════════════════════════════════════════════════════════
def build_ambiguity_report(
    reqs: list[dict[str, Any]],
    source: str = "",
    block_threshold: int = 1,
) -> dict[str, Any]:
    """对一批需求生成结构化歧义报告（A1 主动上报的核心产物）。

    Args:
        reqs: 需求三元组列表。
        source: 需求来源标识（项目名 / 文档名 / 版本号），写入报告便于溯源。
        block_threshold: 阻断阈值 —— blocking 级歧义数 ≥ 该值时
            ``should_block_generation=True``。默认 1（有一条高危歧义即建议先澄清）。

    Returns:
        {
          "schema_version": "ambiguity-v1",
          "generated_at": ISO 时间,
          "source": str,
          "requirement_total": int,
          "ambiguity_total": int,
          "blocking_total": int,
          "should_block_generation": bool,
          "by_type": {type: count},
          "by_severity": {"high"/"medium"/"low": count},
          "affected_requirements": [req_id, ...],
          "items": [歧义项, ...],
        }
    """
    reqs = reqs or []
    items: list[dict[str, Any]] = []
    for idx, req in enumerate(reqs):
        with guard("build_ambiguity_report: items.extend(detect_ambiguities(req,", log=_log):
            items.extend(detect_ambiguities(req, idx))
    with guard("build_ambiguity_report: items.extend(detect_cross_requiremen", log=_log):
        items.extend(detect_cross_requirement_ambiguities(reqs))

    # 编号 + 聚合
    by_type: dict[str, int] = {}
    by_severity: dict[str, int] = {"high": 0, "medium": 0, "low": 0}
    affected: list[str] = []
    blocking_total = 0
    for i, item in enumerate(items, 1):
        item["ambiguity_id"] = "AMB-%04d" % i
        by_type[item["type"]] = by_type.get(item["type"], 0) + 1
        sev = item.get("severity", "medium")
        by_severity[sev] = by_severity.get(sev, 0) + 1
        if item.get("blocking"):
            blocking_total += 1
        rid = item.get("evidence", {}).get("req_id", "")
        if rid and rid not in affected:
            affected.append(rid)

    return {
        "schema_version": AMBIGUITY_SCHEMA_VERSION,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source": source or "unknown",
        "requirement_total": len(reqs),
        "ambiguity_total": len(items),
        "blocking_total": blocking_total,
        "should_block_generation": blocking_total >= max(1, int(block_threshold)),
        "by_type": by_type,
        "by_severity": by_severity,
        "affected_requirements": affected,
        "items": items,
    }


def persist_clarification_sessions(
    report: dict[str, Any],
    project: str = "",
    version_id: str = "",
    run_id: str = "",
) -> list[dict[str, Any]]:
    """把歧义报告中的高危（blocking）项持久化为可交互澄清会话（流水线强化专项 T3）。

    ★ 纯增量、绝不阻断：DB 不可用/会话已存在/服务异常时静默跳过并返回空列表，
      不改变原歧义报告的任何字段 —— 歧义检测仍是纯读分析（零 DB 写）。

    Args:
        report: build_ambiguity_report / apply_ambiguity_report 的返回 dict。
        project / version_id / run_id: 流水线上下文。

    Returns:
        本次新建的会话 dict 列表（无新建时返回空列表）。
    """
    if not report or not report.get("items"):
        return []
    try:
        from src.pipeline.steps.step2.clarification_service import ClarificationService

        return ClarificationService().create_sessions(
            report, project=project, version_id=version_id, run_id=run_id
        )
    except Exception as _e:  # noqa: BLE001 —— 持久化失败不阻断拆解
        _log.warning("[ambiguity] 澄清会话持久化跳过(不阻断): %s", str(_e)[:120])
        return []


def apply_ambiguity_report(
    all_reqs: list[dict[str, Any]],
    source: str = "",
    block_threshold: int = 1,
) -> dict[str, Any]:
    """对所有需求应用歧义检测，附加 ``ambiguity_items`` 到每条需求并主动上报。

    与 ``_fd_labels._apply_completeness_check`` 同构（不阻断拆解流程，仅附加字段 +
    产出统计），差异是本函数额外输出可直接消费的**歧义报告**，并以 WARNING
    级日志「主动上报」高危歧义 —— 而非静默生成可能错的用例。

    Args:
        all_reqs: 需求三元组列表（原地附加 ``ambiguity_items`` 字段）。
        source: 需求来源标识。
        block_threshold: 阻断阈值（见 :func:`build_ambiguity_report`）。

    Returns:
        歧义报告 dict（同 :func:`build_ambiguity_report`）。
    """
    report = build_ambiguity_report(all_reqs, source=source, block_threshold=block_threshold)

    # 按需求索引回填到各条需求（便于写入 payload_json 溯源）
    by_index: dict[int, list[dict[str, Any]]] = {}
    for item in report.get("items") or []:
        idx = int(item.get("evidence", {}).get("req_index", -1))
        if idx >= 0:
            by_index.setdefault(idx, []).append(item)
    for idx, req in enumerate(all_reqs or []):
        req["ambiguity_items"] = by_index.get(idx, [])

    # ── 主动上报 ──
    if report["ambiguity_total"] > 0:
        _log.warning(
            "[ambiguity] 需求歧义主动上报: %d 条需求检出 %d 条歧义（高危阻断 %d 条）"
            "，建议先澄清再生成用例=%s；分布: %s",
            report["requirement_total"],
            report["ambiguity_total"],
            report["blocking_total"],
            report["should_block_generation"],
            ", ".join("%s=%d" % (k, v) for k, v in report["by_type"].items()),
        )
        for item in report["items"]:
            if item.get("blocking"):
                _log.warning(
                    "[ambiguity] %s [%s] %s → 建议澄清: %s",
                    item["ambiguity_id"],
                    item["type_label"],
                    item["ambiguity_point"],
                    " / ".join(item.get("clarify_questions") or []),
                )
    else:
        _log.info(
            "[ambiguity] 歧义检查通过: %d 条需求未检出歧义",
            report["requirement_total"],
        )
    return report
