# -*- coding: utf-8 -*-
"""从 step_results 提取三轨 evidence（ui/api/db）— 从 _base.py 拆出（§4.3 单文件≤800行）。

职责：
- 从 step_results 的 _verify_result 提取三轨 evidence
- 当 _verify_result=None（verify 未调用/能力路径跳过）时从 step_results 兜底
- 保证 record_execution 前的 expected/actual/details 永不全空（§13.4 红线）

★ P0-fix(2026-07-24): 新增 None 兜底逻辑
  原bug: _verify_result=None（verify 未调用）时 evidence 全空 → 空 evidence FAIL（§13.4 违规）
  修复: 从 step_results 的动作执行证据兜底，保证三字段至少一个非空
  通用性: 适用于任何"verify 未调用/返回 None/能力路径跳过"场景
  合理性: 追根溯源到 evidence 数据流断裂（L5 根因），不依赖 verify 是否调用
  覆盖性: 覆盖所有 record_execution 调用路径
"""

import logging
from src.core.guarded import guard
import re

_log = logging.getLogger(__name__)

# 无效 evidence 文本（不记入 actual）
_INVALID_EVIDENCE_TEXTS = ("无API校验点", "无DB校验点", "未声明该轨，跳过(不计入AND)")

# 判定态词（verdict）前缀/标记 —— 报告语义(2026-09-02)：expected 必须是"预期行为"，
# 不得携带 [PASS]/[FAIL] 判定或"验证通过:"等观测结论。此词汇表通用、非靶子系统相关。
_VERDICT_TOKENS = ("[PASS]", "[FAIL]", "[SKIP]", "[BLOCK]", "[ERROR]", "[WARN]",
                   "[BLOCKED]", "[MANUAL_REVIEW]")
_VERDICT_PREFIXES = (
    "验证通过: ", "验证失败: ", "操作成功: ", "操作失败: ", "执行成功: ", "执行失败: ",
    "Agent(guided): ", "Agent(free): ", "Agent执行成功: ", "Agent执行失败: ",
    "Agent执行: ", "Agent 执行: ", "Agent 执行：",
    "PASS: ", "FAIL: ", "SKIP: ", "ERROR: ", "BLOCK: ", "FAIL(需求未实现): ",
)


def clean_expected(text) -> str:
    """从预期文本中剥除判定态前缀/标记，还原为可读的"预期行为"。

    - 循环剥除开头的判定前缀（如 "验证通过: "、"Agent(guided): "、"FAIL(需求未实现): "）
    - 移除任意位置的 [PASS]/[FAIL] 等方括号判定串
    通用性：不依赖具体靶子系统/页面，任何"带观测结论的预期/操作描述"均适用。
    修复目标（2026-09-02 报告阶段通用缺陷）：
      ① expected 空 —— 权威 FAIL 无 verify.summary 时 expected 恒空；
      ② expected 污染 —— verify.summary 携带 [FAIL] text_contains 等观测文本被写入 expected。
    """
    t = (text or "").strip()
    if not t:
        return ""
    _changed = True
    while _changed:
        _changed = False
        for _p in _VERDICT_PREFIXES:
            if t.startswith(_p):
                t = t[len(_p):].strip()
                _changed = True
                break
        for _tok in _VERDICT_TOKENS:
            if t.startswith(_tok):
                t = t[len(_tok):].strip()
                _changed = True
                break
    for _tok in _VERDICT_TOKENS:
        t = t.replace(_tok + ": ", " ").replace(_tok + " ", " ")
    return " ".join(t.split()).strip()


def _first_step_description(step_results) -> str:
    """取首个带文本的步骤 description/action 作为"预期操作"候选。

    ★ 根因修复（2026-09-23）D11：拒绝「用例标题模板」冒充预期行为。
      browser_intent_parser 会把步骤 description 写成 `执行测试用例: {title}`，
      而本函数原样返回 → 落库 executions.expected 变成**用例标题**
      （实测 "执行测试用例: 2.5 其他零碎的…-REQ-001-安全-XSS/注入payload不执行"）。
      后果：报告的「预期结果」是无意义的标题，且 expected↔actual 无法对读，
      假 PASS/假 FAIL 审计失去基准。此类"要做什么"的元数据不得当作"期望什么"。
      通用性：不依赖具体靶子/业务，只排除"执行测试用例: "这一引擎自身模板。
    """
    for _r in step_results:
        if isinstance(_r, dict):
            _d = (_r.get("description") or _r.get("action") or "").strip()
            if not _d:
                continue
            if _is_expectation_metadata(_d):
                continue
            return _d
    return ""


# 引擎自身生成的「用例标题模板」前缀（不是可验证的期望行为）
_TITLE_TEMPLATE_PREFIXES = ("执行测试用例:", "执行测试用例：", "执行用例:", "执行用例：")

# 纯需求/用例编号（如 REQ-006 / REQ-DOC-01 / TC-REQ-006-I01）：关联元数据，
# 不是可验证的期望行为。
_REQ_ID_SHAPE_RE = re.compile(r"^[A-Za-z0-9]+(?:[-_][A-Za-z0-9]+)*$")


def _is_expectation_metadata(text: str) -> bool:
    """文本是否为「元数据」（用例标题模板 / 纯需求编号），而非可验证的期望行为。

    expected 列的语义是"期望什么"（可验证行为），用例标题和需求编号都是
    "关联什么/要做什么"的元数据 —— 落进 expected 会让 expected↔actual 无法对读。
    """
    _t = str(text or "").strip()
    if not _t:
        return False
    if any(_t.startswith(_p) for _p in _TITLE_TEMPLATE_PREFIXES):
        return True
    # 编号形态：无空格，且字母/数字/分隔符齐备（排除 "404" 这类纯数字合法期望）
    return (
        " " not in _t
        and bool(re.search(r"[A-Za-z]", _t))
        and bool(re.search(r"\d", _t))
        and bool(_REQ_ID_SHAPE_RE.match(_t))
    )


def _first_declared_expectation(step_results) -> str:
    """取步骤携带的**真实期望**（D11 修复：优先于 description）。

    来源优先级：step.expected > _verify_result.checks[*].expect > verify.summary。
    与 _first_step_description 的分工：本函数只回"期望什么"，不回"做了什么"。
    """
    for _r in step_results:
        if not isinstance(_r, dict):
            continue
        _e = str(_r.get("expected", "") or "").strip()
        if _e and not _is_expectation_metadata(_e):
            return _e
    for _r in step_results:
        if not isinstance(_r, dict):
            continue
        _vr = _r.get("_verify_result")
        if not isinstance(_vr, dict):
            continue
        for _c in _vr.get("checks") or []:
            if not isinstance(_c, dict):
                continue
            _e = str(
                _c.get("expect", "")
                or _c.get("expected", "")
                or _c.get("text", "")
                or ""
            ).strip()
            if _e and not _is_expectation_metadata(_e):
                return _e
    return ""


def declared_expectation(intent, case_meta=None) -> str:
    """读取**用例声明的预期行为**（Step3 落入 `test_cases.expected_result`）。

    expected 列的语义是「应该发生什么」，唯一上游生产者是 Step3；Step4 只读不推。
    运行期 verify summary（"1/1 通过 (置信度: 0.95)"）是**观测结论**，不得替代预期。
    """
    _intent = intent if isinstance(intent, dict) else {}
    _cm = case_meta if isinstance(case_meta, dict) else (_intent.get("case_meta") or {})
    if not isinstance(_cm, dict):
        _cm = {}
    return str(
        _intent.get("expected_result") or _cm.get("expected_result") or ""
    ).strip()


def extract_evidence_from_step_results(step_results: list, declared_expected: str = "") -> tuple:
    """从 step_results 提取三轨 evidence。

    参数:
        declared_expected: 用例声明的预期行为（`declared_expectation` 的返回值）。
            它是 expected 的**第一优先级**来源 —— 其余来源都是步骤级细节或兜底。

    返回 (expected, actual, check_details)
    - expected: str — 用例声明的预期 > 步骤声明 expected > 步骤 description（均已去判定态）
    - actual: str — 来自三轨 evidence（过滤无效文本）
    - check_details: list[dict] — 三轨明细 + 失败步骤错误 + 兜底动作序列
    """
    _expected = ""
    _actual = ""
    _check_details = []

    with guard("extract_evidence_from_step_results: for r in step_results: _vr = r.get('", log=_log, level=logging.WARNING):
        # ── 1. 从 _verify_result 提取三轨 evidence ──
        for r in step_results:
            _vr = r.get("_verify_result")
            if not isinstance(_vr, dict):
                continue
            # 三轨 evidence 汇总到 details（无论PASS/FAIL都记录）
            for _track in ("ui", "api", "db"):
                _track_result = _vr.get(_track)
                if not isinstance(_track_result, dict):
                    continue
                _evidence = str(_track_result.get("evidence", "") or "")
                if _evidence:
                    _check_details.append(
                        {
                            "track": _track,
                            "passed": _track_result.get("passed", False),
                            "evidence": _evidence[:300],
                        }
                    )
            # actual 记录所有轨的 evidence（无论PASS/FAIL）
            for _track in ("ui", "api", "db"):
                _track_result = _vr.get(_track)
                if isinstance(_track_result, dict):
                    _ev = str(_track_result.get("evidence", "") or "")
                    if _ev and _ev not in _INVALID_EVIDENCE_TEXTS:
                        _actual = (_actual + "; " + _ev).strip("; ") if _actual else _ev

        # ── 2. 兼容原有失败步骤错误信息提取（无 _verify_result 的场景）──
        for r in step_results:
            if not r.get("passed") and r.get("error"):
                _err = (r.get("error") or "")[:300]
                _check_details.append(
                    {
                        "step": r.get("action") or r.get("description") or "",
                        "error": _err,
                    }
                )
                if not _actual:
                    _actual = _err

    # ── 2.5 报告语义：expected 定为"预期行为"，去判定态 ──
    #   来源优先级：用例声明（Step3 expected_result）> 步骤声明 expected > 步骤 description。
    #   运行期 verify.summary 是观测结论（"1/1 通过 (置信度: 0.95) [ui]"），
    #   用它冒充预期会让 expected↔actual 无法对读（实测 456/519 行被污染）。
    if not _expected:
        _expected = (
            clean_expected(declared_expected)
            or clean_expected(_first_declared_expectation(step_results))
            or clean_expected(_first_step_description(step_results))
        )

    # ── 3. P0-fix 兜底：当 expected/actual/details 全空时，从 step_results 构造最小 evidence ──
    #   触发场景：verify 未调用（_cap_done=True / step_expected="" / step_passed=False 且无 error）
    #   违规红线：§13.4 "空 evidence 的 PASS/FAIL 比FAIL更有害"
    if not _expected and not _actual and not _check_details:
        _fallback_actions = []
        _fallback_results = []
        for r in step_results:
            _action = r.get("action") or r.get("description") or ""
            _passed = r.get("passed", False)
            _err = (r.get("error") or "")[:200]
            if _action:
                _fallback_actions.append(f"{_action}({'PASS' if _passed else 'FAIL'})")
            if _err:
                _fallback_results.append(f"{_action}: {_err}")
        if _fallback_actions:
            _expected = "动作执行序列(兜底无verify)"
            _actual = (
                "; ".join(_fallback_results)
                if _fallback_results
                else "; ".join(_fallback_actions)
            )
            _check_details = [
                {
                    "evidence_source": "fallback_no_verify",
                    "actions": _fallback_actions[:20],
                    "note": "verify 未调用或返回 None，从 step_results 兜底构造（§13.4 红线）",
                }
            ]
            _log.debug(
                "[EvidenceExtractor] 触发兜底: %d 个动作", len(_fallback_actions)
            )

    # §13.4 红线：expected 必须非空。无任何声明时用中性占位，**不得**拿观测结论冒充预期。
    if not _expected:
        _expected = "用例未声明预期行为（仅记录实际执行证据）"

    return _expected, _actual, _check_details


# ── 超时分支兜底 evidence（§13.4 红线：超时也必须有 evidence）──
#   原 bug: serial.py/parallel.py 的 asyncio.TimeoutError 分支调 record_execution
#          时未传 expected/actual/details → 3 条 FAIL 用例 evidence 全空（§13.4 违规）
#   修复: 统一构造超时场景的最小 evidence，3 处断链点调用此函数
#   通用性: 适用于任何"用例执行超时"场景（读意图/写意图/worker 并行）
#   合理性: 追根溯源到"异常分支与正常分支 evidence 提取割裂"（§10.9 根因层4）
#   覆盖性: 覆盖 serial.py L223/L288 + parallel.py L262 三处断链点
def build_timeout_evidence(timeout_sec: int, scenario: str = "用例") -> tuple:
    """构造超时分支的最小 evidence（expected, actual, details_json_str）。

    参数:
        timeout_sec: 超时秒数
        scenario: 场景描述（如"用例"/"写意图"/"单用例"）

    返回:
        (expected, actual, details) — 可直接传入 record_execution 的同名参数
    """
    import json

    _expected = "%s在规定时间内完成执行" % scenario
    _actual = "%s执行超时(%ds)，未能完成所有步骤" % (scenario, timeout_sec)
    _details_obj = [
        {
            "failure_category": "HARNESS_ERROR",
            "timeout_sec": timeout_sec,
            "scenario": scenario,
            "note": "超时分支兜底evidence(§13.4红线：无论PASS/FAIL都必须有evidence)",
            "evidence_source": "timeout_fallback",
        }
    ]
    _details = json.dumps(_details_obj, ensure_ascii=False)
    _log.debug(
        "[EvidenceExtractor] 超时兜底: scenario=%s timeout=%ds", scenario, timeout_sec
    )
    return _expected, _actual, _details
