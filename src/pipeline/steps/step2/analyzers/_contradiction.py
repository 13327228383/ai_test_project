"""需求矛盾检测（纯算法，零 AI 调用）。

检测策略（通用，不绑业务）：
  1. 同 feature_label 下，operation 语义相似但 expected_feedback 矛盾
     → 否定词检测：一方含否定词，另一方不含
     → 反义词对检测：两方 expected_feedback 分别命中反义词对的两端
  2. 自矛盾：operation 描述的"目标状态"与 expected_feedback 矛盾
  3. 跨模块元素级矛盾：operation 的目标对象相同但 expected_feedback 矛盾

每条需求的矛盾信息写入 _contradiction_with 字段（不阻断，供审核使用）。
"""

import itertools
from src.core.guarded import guard
import json
import logging
import re
from pathlib import Path
from typing import Any, Optional

_log = logging.getLogger(__name__)

# ── 矛盾检测配置：否定词 ──
# 从 operation_verbs.json 动态加载，失败时使用默认值
_CONTRADICTION_NEGATION_WORDS: list[str] = []
_CONTRADICTION_ANTONYM_PAIRS: list[tuple[str, str]] = []
_CONTRADICTION_LOADED = False


def _load_contradiction_config():
    """从配置文件加载矛盾检测的关键词和反义词对。"""
    global _CONTRADICTION_NEGATION_WORDS, _CONTRADICTION_ANTONYM_PAIRS, _CONTRADICTION_LOADED
    if _CONTRADICTION_LOADED:
        return

    # 默认值（通用，不绑业务）
    _CONTRADICTION_NEGATION_WORDS = [
        # 中文否定词
        "不",
        "禁止",
        "不能",
        "不允许",
        "无法",
        "不可",
        "不会",
        "不应",
        "不得",
        "切勿",
        "严禁",
        "拒绝",
        "阻止",
        "拦截",
        # 英文否定词
        "not",
        "cannot",
        "should not",
        "must not",
        "unable",
        "disabled",
        "forbidden",
        "denied",
        "blocked",
    ]
    _CONTRADICTION_ANTONYM_PAIRS = [
        # 显示/隐藏类
        ("显示", "隐藏"),
        ("可见", "不可见"),
        ("展示", "隐藏"),
        ("弹出", "关闭"),
        ("弹窗", "关闭"),
        ("打开", "关闭"),
        ("展开", "收起"),
        ("显示", "不显示"),
        ("出现", "消失"),
        # 启用/禁用类
        ("启用", "禁用"),
        ("激活", "停用"),
        ("开启", "关闭"),
        ("允许", "禁止"),
        ("放行", "拦截"),
        # 成功/失败类
        ("成功", "失败"),
        ("通过", "不通过"),
        ("通过", "拒绝"),
        ("正常", "异常"),
        ("正确", "错误"),
        # 存在/不存在类
        ("存在", "不存在"),
        ("包含", "不包含"),
        ("有", "没有"),
        # 新增/删除类
        ("新增", "删除"),
        ("添加", "移除"),
        ("创建", "销毁"),
        ("增加", "减少"),
        ("新建", "删除"),
        # 选中/取消类
        ("选中", "取消"),
        ("勾选", "取消勾选"),
        ("选择", "取消选择"),
        # 颜色/状态类
        ("红色", "绿色"),
        ("红色", "灰色"),
        ("灰色", "蓝色"),
        ("高亮", "置灰"),
        ("可用", "不可用"),
        # 保存/丢弃类
        ("保存", "丢弃"),
        ("保留", "清除"),
        ("保存", "不保存"),
    ]

    with guard("_load_contradiction_config: config_path = str( Path(__file__).re", log=_log):
        config_path = str(
            Path(__file__).resolve().parent.parent.parent.parent.parent
            / "config"
            / "operation_verbs.json"
        )
        if Path(config_path).exists():
            with open(config_path, "r", encoding="utf-8") as f:
                dict_config = json.load(f)
            cc = dict_config.get("contradiction_check", {})
            if cc:
                if cc.get("negation_words"):
                    _CONTRADICTION_NEGATION_WORDS = cc["negation_words"]
                if cc.get("antonym_pairs"):
                    _CONTRADICTION_ANTONYM_PAIRS = [
                        tuple(pair) for pair in cc["antonym_pairs"] if len(pair) == 2
                    ]

    _CONTRADICTION_LOADED = True


def _has_negation(text: str) -> bool:
    """检测文本是否包含否定词。"""
    _load_contradiction_config()
    text_lower = text.lower()
    return any(nw.lower() in text_lower for nw in _CONTRADICTION_NEGATION_WORDS)


def _contains_antonym(text: str, word: str) -> bool:
    """检测文本是否包含某反义词。"""
    return word.lower() in text.lower()


def detect_contradictions(
    requirements: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """检测需求之间的矛盾和自矛盾。

    返回 (requirements, stats)。
    每条需求的矛盾信息写入 _contradiction_with 字段（不阻断，供审核使用）。
    """
    _load_contradiction_config()
    if not _CONTRADICTION_NEGATION_WORDS and not _CONTRADICTION_ANTONYM_PAIRS:
        return requirements, {"contradiction_pairs": 0, "self_contradictions": 0}

    # ★ 清理旧矛盾标记（避免 payload_json 携带的 DB 历史数据累积）
    for req in requirements:
        if "_contradiction_with" in req:
            del req["_contradiction_with"]

    # ★ 构建否定词正则（短 ASCII 词加 \b 词边界，如 "not" 不匹配 "notification"；
    #    中文词不加 \b，因为 \b 在汉字间不生效，会导致"不允许"等词无法匹配）
    _negation_re = re.compile(
        "|".join(
            rf"\b{re.escape(w)}\b" if len(w) <= 3 and w.isascii() else re.escape(w)
            for w in _CONTRADICTION_NEGATION_WORDS
        ),
        re.IGNORECASE,
    )

    contradiction_pairs = 0
    self_contradictions = 0

    # ── 策略 1: 按 feature_label 分组，同组内检测矛盾 ──
    _by_label: dict[str, list[tuple[int, dict[str, Any]]]] = {}
    for i, req in enumerate(requirements):
        label = req.get("feature_label", "") or "_ungrouped"
        _by_label.setdefault(label, []).append((i, req))

    for label, items in _by_label.items():
        if len(items) < 2:
            continue
        for (i1, r1), (i2, r2) in itertools.combinations(items, 2):
            op1 = (r1.get("operation") or "").strip()
            op2 = (r2.get("operation") or "").strip()
            fb1 = (r1.get("expected_feedback") or "").strip()
            fb2 = (r2.get("expected_feedback") or "").strip()
            if not op1 or not op2 or not fb1 or not fb2:
                continue
            if op1 == op2:
                continue  # 同 operation 已经在去重阶段处理

            # 操作目标相同（operation 的核心动词+对象相似）才检测矛盾
            _op1_core = _extract_core_operation(op1)
            _op2_core = _extract_core_operation(op2)
            if _op1_core != _op2_core:
                continue

            # 检测 1a: 否定词矛盾（一方有否定词，另一方没有）
            _neg1 = bool(_negation_re.search(fb1))
            _neg2 = bool(_negation_re.search(fb2))
            if _neg1 != _neg2:
                _mark_contradiction(r1, r2, "negation", fb1, fb2)
                contradiction_pairs += 1
                continue

            # 检测 1b: 反义词对矛盾
            for a_word, b_word in _CONTRADICTION_ANTONYM_PAIRS:
                _a1 = _contains_antonym(fb1, a_word)
                _b1 = _contains_antonym(fb1, b_word)
                _a2 = _contains_antonym(fb2, a_word)
                _b2 = _contains_antonym(fb2, b_word)
                if (_a1 and _b2) or (_b1 and _a2):
                    _mark_contradiction(r1, r2, f"antonym:{a_word}/{b_word}", fb1, fb2)
                    contradiction_pairs += 1
                    break

    # ── 策略 2: 自矛盾检测（operation 与 expected_feedback 语义冲突）──
    for req in requirements:
        op = (req.get("operation") or "").strip()
        fb = (req.get("expected_feedback") or "").strip()
        if not op or not fb:
            continue
        _self_contra = _detect_self_contradiction(op, fb)
        if _self_contra:
            req.setdefault("_contradiction_with", []).append(
                {
                    "type": "self_contradiction",
                    "detail": _self_contra,
                }
            )
            self_contradictions += 1

    stats = {
        "contradiction_pairs": contradiction_pairs,
        "self_contradictions": self_contradictions,
    }
    if contradiction_pairs > 0 or self_contradictions > 0:
        _log.warning(
            "[contradiction] 矛盾检测: %d 对需求矛盾, %d 条自矛盾",
            contradiction_pairs,
            self_contradictions,
        )
    return requirements, stats


def _extract_core_operation(operation: str) -> str:
    """提取 operation 的核心动词+对象（去除修饰语），用于跨需求操作匹配。

    支持中英文：中文移除"在/从/通过/基于/根据/针对"等前缀及其修饰范围；
    英文移除 "on/from/via/using/based on/in/at" 等介词短语前缀。
    """
    core = operation.strip()
    # ── 中文修饰前缀：移除"在X中/上/下"等结构 ──
    _zh_modifiers = ["在", "从", "通过", "使用", "基于", "根据", "针对", "对于"]
    for mod in _zh_modifiers:
        if core.startswith(mod):
            idx = core.find("中") if "中" in core else -1
            idx2 = core.find("上") if "上" in core else -1
            idx3 = core.find("下") if "下" in core else -1
            cut = max(idx, idx2, idx3)
            if cut > 0:
                core = core[cut + 1 :].strip()
    # ── 英文修饰前缀：移除介词短语 ──
    _en_prefixes = [
        "on the ",
        "on ",
        "from the ",
        "from ",
        "via the ",
        "via ",
        "using the ",
        "using ",
        "based on the ",
        "based on ",
        "in the ",
        "in ",
        "at the ",
        "at ",
    ]
    core_lower = core.lower()
    for prefix in _en_prefixes:
        if core_lower.startswith(prefix):
            _rest = core[len(prefix) :]
            _comma = _rest.find(",")
            if _comma > 0:
                core = _rest[_comma + 1 :].strip()
            else:
                core = _rest.strip()
            break
    core = core.strip()
    # ★ 剥离后为空 → 返回原始 operation（避免假阳性匹配）
    if not core:
        return (
            operation.strip()[:15] if len(operation.strip()) > 15 else operation.strip()
        )
    return core[:15] if len(core) > 15 else core


def _mark_contradiction(
    r1: dict[str, Any],
    r2: dict[str, Any],
    contra_type: str,
    fb1: str,
    fb2: str,
):
    """标记两条需求为矛盾关系（幂等：同一 req_id 不重复追加）。"""
    _r2_id = r2.get("req_id", "")
    _r1_id = r1.get("req_id", "")
    # 幂等检查：仅当两个 req_id 都非空时进行（空 req_id 会互相匹配导致误跳过）
    if _r1_id and _r2_id:
        _r1_existing = [d.get("req_id") for d in r1.get("_contradiction_with", [])]
        if _r2_id in _r1_existing:
            return
    detail = {
        "type": contra_type,
        "expected_feedback_1": fb1[:80],
        "expected_feedback_2": fb2[:80],
    }
    r1.setdefault("_contradiction_with", []).append(
        {
            "req_id": _r2_id,
            **detail,
        }
    )
    r2.setdefault("_contradiction_with", []).append(
        {
            "req_id": _r1_id,
            **detail,
        }
    )


def _detect_self_contradiction(operation: str, expected_feedback: str) -> Optional[str]:
    """检测 operation 与 expected_feedback 是否自矛盾。

    例如：operation="点击删除按钮" 但 expected_feedback="数据被保存"
    → 删除 vs 保存 是矛盾操作。
    """
    _load_contradiction_config()
    op_lower = operation.lower()
    fb_lower = expected_feedback.lower()

    for a_word, b_word in _CONTRADICTION_ANTONYM_PAIRS:
        a_in_op = _contains_antonym(op_lower, a_word)
        b_in_op = _contains_antonym(op_lower, b_word)
        a_in_fb = _contains_antonym(fb_lower, a_word)
        b_in_fb = _contains_antonym(fb_lower, b_word)
        if (a_in_op and b_in_fb) or (b_in_op and a_in_fb):
            return (
                f"operation 含'{a_word if a_in_op else b_word}'"
                f"但 expected_feedback 含'{b_word if b_in_op else a_word}'（反义词矛盾）"
            )
    return None
