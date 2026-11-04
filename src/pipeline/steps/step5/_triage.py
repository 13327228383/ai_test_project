# -*- coding: utf-8 -*-
"""失败三分类 + 根因下钻（P0-6 / P1-5 有界增强，规则驱动、fail-open）。

★ [已复核-DeepSeek-V3 2026-08-16]（verdict=APPROVED，见 scripts/_tmp/strong_model_review_triage.json）寇豆码 2026-08-16

背景（AGENTS.md 红线）：
  - §3 交付标准：报告必须包含根因分析（引擎缺陷 vs 靶子系统问题）
  - §14 强模型终验：根因链 ≥3 层
  - CAPABILITY_GAP_PRD P0-6：失败分类闭环（产品缺陷 / 用例缺陷 / 环境问题）
  - CAPABILITY_GAP_PRD P1-5：根因下钻，输出结构化根因链

本模块为 Step5 Bug 报告提供两个**纯增量**结构化字段（不改流水线契约、不新增采集）：
  failure_triage.category          : ENGINE 引擎/断言缺陷 | SUT 被测系统缺陷 | ENV 环境/数据问题 | UNKNOWN
  failure_triage.root_cause_chain  : ≥3 层根因链（表层现象 → 中间层（断言/响应差异） → 深层（根因归属）），
                                     每层带证据引用（evidence_ref 指向 executions 已落库字段）。

证据来源（全部复用 executions 已落库字段，不新增采集）：
  - failure_reason / failure_category / failure_chain / details / check_type
  - expected / actual / status / case_id / title
  - 复用 step4 既有判定（只读 import，不改动）：
      step4.diagnosis.classify_diagnosis      （SUT_DB_NOT_MIGRATED / SUT_UNREACHABLE / CREDENTIAL_INVALID）
      step4.failure_classifier.is_network_error_text（net::ERR_* 等网络判定）
      step4.failure_classifier 的 PRODUCT_BUG / HARNESS_ERROR / ENVIRONMENT_ERROR / DATA_ERROR

fail-open：无样本/证据不足 → category=UNKNOWN（confidence=0.0），不硬造根因。
"""

from __future__ import annotations
from src.core.guarded import guard

import json
import logging
import re
from typing import Any, Optional
from src.pipeline.steps.step4.failure_classifier import _CREDENTIAL_HINTS as _ENV_CREDENTIAL_HINTS  # noqa: F401  词表唯一源

_log = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════
# 三分类常量（与 step4/diagnosis.py DIAG_TO_TRIAGE 字符串值严格对齐）
# ═══════════════════════════════════════════════════════════════════════════
TRIAGE_ENGINE = "ENGINE"   # 引擎/断言缺陷：断言定义错误、定位能力不足、引擎内部异常
TRIAGE_SUT = "SUT"         # 被测系统缺陷：业务行为不符合预期、元素缺失、服务端逻辑错误
TRIAGE_ENV = "ENV"         # 环境/数据问题：网络不可达、依赖缺失、DB 未迁移、凭据/前置数据
TRIAGE_UNKNOWN = "UNKNOWN"  # fail-open：证据不足，不硬造

TRIAGE_VERSION = "triage-v1"

# ═══════════════════════════════════════════════════════════════════════════
# 证据关键词表（命中即触发对应分类；每条带可解释依据 + 置信度）
# ═══════════════════════════════════════════════════════════════════════════
# 环境/数据问题（ENV）：非网络类关键词
_ENV_DB_MIGRATE_HINTS = (
    "no such table", "auth_user", "programmingerror", "operationalerror",
    "doesn't exist", "does not exist", "unknown column", "relation ",
    "migrat", "django",
)
_ENV_DATA_HINTS = (
    "前置数据缺失", "前置条件未满足", "测试数据不存在", "fixture", "seed data",
    "no data found", "数据未构造", "缺少测试数据",
)
_ENV_DRIVER_HINTS = (
    "executable doesn't exist", "ms-playwright", "browser executable",
    "driver not found", "chromium not found", "浏览器驱动", "playwright not installed",
)
# 连接/不可达类（不含裸 timeout —— 裸 timeout 需结合「无策略步骤」才判 ENV，见信号 2.5）
_ENV_CONNECT_HINTS = (
    "err_connection", "econnrefused", "econnreset", "connection refused",
    "connection reset", "connection timed out", "unreachable", "failed to connect",
    "cannot connect", "network is unreachable", "name or service not known",
    "err_name_not_resolved", "err_address_unreachable", "err_internet_disconnected",
    "err_empty_response", "err_timed_out", "err_ssl_protocol_error",
    "err_tunnel_connection_failed", "err_proxy_connection_failed", "dns",
    "port 80", "服务未启动", "端口不可达", "连接被拒",
)
_ENV_TIMEOUT_HINTS = ("timed out", "timeout")

# 引擎/断言缺陷（ENGINE）
_ENGINE_HARNESS_HINTS = (
    "playwright", "strict mode violation", "locator", "selector", "timeout waiting for",
    "waiting for selector", "harness", "引擎内部异常", "断言定义", "check_definition",
    "assertion error", "precision", "operator",
)
_ENGINE_PLACEHOLDER_RE = re.compile(r"\{\{.*?\}\}|\$\{.*?\}|<placeholder>|待替换", re.S)

# 被测系统缺陷（SUT）
_SUT_BUSINESS_HINTS = (
    "业务逻辑", "状态流转", "商品状态", "订单状态", "库存", "余额", "计算错误",
    "返回错误", "接口返回", "数据库写入", "js console", "console error",
    "javascript error", "500 internal", "internal server error", "traceback",
    "product bug", "行为不符合", "元素不存在", "未找到元素", "element not found",
)

# 新增：API/DB 断言失配（精准增强，让真实注入缺陷可归 SUT 而非 UNKNOWN）
_API_MISMATCH_RE = re.compile(r"断言失败|status=\d+.*期望|code=\d+.*期望|响应状态码.*期望|接口返回.*期望", re.IGNORECASE)
_DB_STATE_RE = re.compile(r"数据库.*(不一致|不匹配|仍存在|不存在)|状态.*(不符|不一致)|记录.*(仍存在|不存在)|交叉验证.*(失败|未通过)|cross.?validation", re.IGNORECASE)

# check_type 中属于「值级断言」的类型（用于空期望 → ENGINE 判定）
_VALUE_CHECK_TYPES = (
    "text_equals", "value_equals", "db_field_value", "api_value",
    "field_value_check", "hard_verify", "text_contains",
)


# ═══════════════════════════════════════════════════════════════════════════
# 证据文本组装
# ═══════════════════════════════════════════════════════════════════════════
def _normalize_text(value: Any) -> str:
    """把任意值转成可检索的小写文本（None → 空串）。"""
    if value is None:
        return ""
    return str(value).lower()


def _chain_text(failure_chain: Any) -> str:
    """把 failure_chain（可能是 JSON 字符串/列表/文本）拍平成可检索文本。"""
    if not failure_chain:
        return ""
    if isinstance(failure_chain, (list, dict)):
        try:
            return json.dumps(failure_chain, ensure_ascii=False)
        except Exception:  # noqa: BLE001
            return str(failure_chain)
    if isinstance(failure_chain, str):
        return failure_chain
    return str(failure_chain)


def _chain_steps(failure_chain: Any) -> list[dict]:
    """尝试把 failure_chain 解析为策略步骤列表（失败返回空列表）。"""
    if isinstance(failure_chain, list):
        return [s for s in failure_chain if isinstance(s, dict)]
    if isinstance(failure_chain, str) and failure_chain.strip():
        try:
            parsed = json.loads(failure_chain)
        except Exception:  # noqa: BLE001
            return []
        if isinstance(parsed, list):
            return [s for s in parsed if isinstance(s, dict)]
    return []


def _build_haystack(failure: dict) -> str:
    """把所有证据字段合并为检索文本（小写）。"""
    parts = [
        failure.get("failure_reason"),
        failure.get("failure_category"),
        failure.get("details"),
        failure.get("check_type"),
        failure.get("expected"),
        failure.get("actual"),
        failure.get("title"),
        _chain_text(failure.get("failure_chain")),
    ]
    return " ".join(_normalize_text(p) for p in parts)


def _signal(evidence_ref: str, matched: str, rule: str, confidence: float) -> dict:
    """构造单条分类依据（哪个证据触发的）。"""
    return {
        "signal": rule,
        "evidence_ref": evidence_ref,
        "matched": matched,
        "confidence": confidence,
    }


# ═══════════════════════════════════════════════════════════════════════════
# 三分类主判定
# ═══════════════════════════════════════════════════════════════════════════
def classify_triage(failure: dict) -> dict:
    """规则驱动失败三分类（可解释：每个判定都带证据引用）。

    Args:
        failure: executions 单行失败证据字典，键：
            status / check_type / expected / actual / details / failure_reason /
            failure_category / failure_chain / case_id / title（均允许缺失）。

    Returns:
        {"category": ENGINE|SUT|ENV|UNKNOWN,
         "confidence": float 0.0~1.0,
         "category_basis": [{"signal","evidence_ref","matched","confidence"}, ...]}
    """
    hay = _build_haystack(failure)
    basis: list[dict] = []

    # ── 信号 0：step4 failure_category（结构化信号，优先级最高） ──
    fcat = _normalize_text(failure.get("failure_category"))
    _FCAT_MAP = {
        "product_bug": (TRIAGE_SUT, 0.9, "failure_category=PRODUCT_BUG（step4 判定被测系统 bug）"),
        "harness_error": (TRIAGE_ENGINE, 0.9, "failure_category=HARNESS_ERROR（step4 判定执行系统 bug）"),
        "environment_error": (TRIAGE_ENV, 0.9, "failure_category=ENVIRONMENT_ERROR（step4 判定环境问题）"),
        "data_error": (TRIAGE_ENV, 0.9, "failure_category=DATA_ERROR（step4 判定数据问题）"),
    }
    if fcat in _FCAT_MAP:
        _cat, _conf, _rule = _FCAT_MAP[fcat]
        basis.append(_signal("failure_category", fcat, _rule, _conf))
        return {
            "category": _cat,
            "confidence": _conf,
            "category_basis": basis,
        }

    # ── 信号 0.5：API 断言失配（status/code 不符） → SUT ──
    #   须置于 diagnosis/ENV 凭据启发式之前：否则「期望 status=401」中的裸 401 会被
    #   classify_diagnosis / _ENV_CREDENTIAL_HINTS 误判为 ENV。显式断言失配文本
    #   （含「断言失败/期望/接口返回」）是 SUT 行为的最强直接证据。
    _api_m = _API_MISMATCH_RE.search(hay)
    if _api_m:
        basis.append(
            _signal(
                "failure_reason+details",
                _api_m.group(0),
                "API 响应与预期不符（status/code 断言失配）→ 被测系统接口行为不符预期",
                0.8,
            )
        )
        return {"category": TRIAGE_SUT, "confidence": 0.8, "category_basis": basis}

    # ── 信号 0.6：DB 轨状态失配（写库/事务/缓存一致性） → SUT ──
    _db_m = _DB_STATE_RE.search(hay)
    if _db_m:
        basis.append(
            _signal(
                "failure_reason+details",
                _db_m.group(0),
                "数据库状态与预期不一致（写库/事务/缓存一致性）→ 被测系统数据缺陷",
                0.85,
            )
        )
        return {"category": TRIAGE_SUT, "confidence": 0.85, "category_basis": basis}

    # ── 信号 1：step4 diagnosis 枚举（DB 未迁移/不可达/凭据 → ENV） ──
    with guard("classify_triage: from src.pipeline.steps.step4.diagno", log=_log):
        from src.pipeline.steps.step4.diagnosis import classify_diagnosis, diagnosis_to_triage

        _diag = classify_diagnosis(hay)
        _diag_triage = diagnosis_to_triage(_diag)
        if _diag_triage == TRIAGE_ENV:
            basis.append(
                _signal(
                    "failure_reason+failure_chain",
                    _diag,
                    "classify_diagnosis → %s（DB 迁移/不可达/凭据类）" % _diag,
                    0.9,
                )
            )
            return {"category": TRIAGE_ENV, "confidence": 0.9, "category_basis": basis}

    # ── 信号 2：网络错误（复用 step4 failure_classifier 共享判定） ──
    try:
        from src.pipeline.steps.step4.failure_classifier import is_network_error_text

        _net = is_network_error_text(hay)
    except Exception:  # noqa: BLE001
        _net = any(h in hay for h in _ENV_CONNECT_HINTS)
    if _net:
        _matched = next(
            (m for m in _ENV_CONNECT_HINTS if m in hay), "net::err/connection 类错误"
        )
        basis.append(
            _signal(
                "failure_reason+failure_chain",
                _matched,
                "网络/连接类错误 → 靶子系统不可达（环境问题）",
                0.9,
            )
        )
        return {"category": TRIAGE_ENV, "confidence": 0.9, "category_basis": basis}

    # ── 信号 2.5：裸超时且无策略步骤（镜像 failure_classifier:
    #   timeout_error and not chain.steps → ENVIRONMENT_ERROR） ──
    #   避免把「Timeout waiting for selector」（定位超时，属引擎/被测系统）误判为 ENV
    if any(h in hay for h in _ENV_TIMEOUT_HINTS) and not _chain_steps(failure.get("failure_chain")):
        basis.append(
            _signal(
                "failure_reason+failure_chain",
                "timeout/timed out（无策略执行步骤）",
                "裸超时且无策略执行 → 靶子系统无响应（环境问题）",
                0.8,
            )
        )
        return {"category": TRIAGE_ENV, "confidence": 0.8, "category_basis": basis}

    # ── 信号 3：ENV 关键词（DB 迁移 / 凭据 / 前置数据 / 驱动缺失 / 连接类） ──
    for _hints, _rule, _ref in (
        (_ENV_DB_MIGRATE_HINTS, "DB 未迁移/缺表", "failure_reason+failure_chain"),
        (_ENV_CREDENTIAL_HINTS, "凭据无效", "failure_reason+details"),
        (_ENV_DATA_HINTS, "前置测试数据缺失", "failure_reason+details"),
        (_ENV_DRIVER_HINTS, "浏览器驱动/依赖缺失", "failure_reason+details"),
        (_ENV_CONNECT_HINTS, "连接/不可达", "failure_reason+details"),
    ):
        _hit = next((h for h in _hints if h in hay), "")
        if _hit:
            basis.append(_signal(_ref, _hit, _rule, 0.9))
            return {"category": TRIAGE_ENV, "confidence": 0.9, "category_basis": basis}

    # ── 信号 4：ENGINE —— 断言定义缺陷（期望含未解析占位符 / 空期望） ──
    _exp_raw = failure.get("expected")
    _exp_text = _normalize_text(_exp_raw)
    _check_type = _normalize_text(failure.get("check_type"))
    _ph = _ENGINE_PLACEHOLDER_RE.search(str(_exp_raw or ""))
    if _ph:
        basis.append(
            _signal(
                "expected",
                _ph.group(0),
                "期望值含未解析占位符 → 断言定义缺陷（引擎生成错误）",
                0.9,
            )
        )
        return {"category": TRIAGE_ENGINE, "confidence": 0.9, "category_basis": basis}
    if (
        _check_type in _VALUE_CHECK_TYPES
        and not _exp_text
        and _normalize_text(failure.get("actual"))
    ):
        basis.append(
            _signal(
                "expected",
                "(空)",
                "值级断言期望为空 → 断言定义不完整（引擎缺陷）",
                0.8,
            )
        )
        return {"category": TRIAGE_ENGINE, "confidence": 0.8, "category_basis": basis}

    # ── 信号 5：ENGINE —— 执行系统/定位能力关键词 ──
    _hit = next((h for h in _ENGINE_HARNESS_HINTS if h in hay), "")
    if _hit:
        # 排除：超时类若命中 ENV 服务器提示则已在上方返回；此处仅剩 harness/定位类
        basis.append(
            _signal(
                "failure_reason+failure_chain",
                _hit,
                "执行系统/定位能力关键词 → 引擎缺陷",
                0.7,
            )
        )
        return {"category": TRIAGE_ENGINE, "confidence": 0.7, "category_basis": basis}

    # ── 信号 6：SUT —— DOM 元素缺失（策略链充分探索后仍缺） ──
    _steps = _chain_steps(failure.get("failure_chain"))
    _dom_missing = any(
        s.get("dom_has_element") is False for s in _steps
    )
    if _dom_missing:
        basis.append(
            _signal(
                "failure_chain",
                "dom_has_element=false",
                "DOM 中不存在目标元素（被测系统缺元素/状态未渲染）",
                0.9,
            )
        )
        return {"category": TRIAGE_SUT, "confidence": 0.9, "category_basis": basis}

    # ── 信号 7：SUT —— 期望 vs 实际具体值不一致（业务行为不符） ──
    if _exp_text and _normalize_text(failure.get("actual")):
        basis.append(
            _signal(
                "expected+actual",
                "期望「%s」≠ 实际「%s」" % (str(_exp_raw)[:40], str(failure.get("actual"))[:40]),
                "期望/实际具体值不一致 → 被测系统行为不符合预期",
                0.7,
            )
        )
        return {"category": TRIAGE_SUT, "confidence": 0.7, "category_basis": basis}

    # ── 信号 8：SUT —— 业务/服务端错误关键词 ──
    _hit = next((h for h in _SUT_BUSINESS_HINTS if h in hay), "")
    if _hit:
        basis.append(
            _signal(
                "failure_reason+details",
                _hit,
                "业务/服务端错误关键词 → 被测系统缺陷",
                0.6,
            )
        )
        return {"category": TRIAGE_SUT, "confidence": 0.6, "category_basis": basis}

    # ── fail-open：证据不足 → UNKNOWN，不硬造 ──
    basis.append(
        _signal(
            "all",
            "(无命中)",
            "无足够证据支撑三分类 → fail-open 置 UNKNOWN，不硬造根因",
            0.0,
        )
    )
    return {"category": TRIAGE_UNKNOWN, "confidence": 0.0, "category_basis": basis}


# ═══════════════════════════════════════════════════════════════════════════
# 根因下钻（≥3 层，每层带证据）
# ═══════════════════════════════════════════════════════════════════════════
def build_root_cause_chain(failure: dict, triage: dict) -> list[dict]:
    """生成 ≥3 层根因链：表层现象 → 中间层（断言/响应差异） → 深层（根因归属）。

    每层 dict: {"layer": int, "type": str, "label": str, "detail": str, "evidence_ref": str}
    若 failure_chain 可解析出策略步骤，在中间层与深层之间追加「证据链」层（仍 ≥3）。
    """
    chain: list[dict] = []
    status = failure.get("status") or "FAIL"
    reason = str(failure.get("failure_reason") or "").strip()
    check_type = failure.get("check_type") or "-"
    expected = failure.get("expected")
    actual = failure.get("actual")
    details = str(failure.get("details") or "").strip()

    # ── 层 1：表层现象（status + failure_reason 直出） ──
    _sym_detail = "executions.status=%s" % status
    if reason:
        _sym_detail += "；failure_reason='%s'" % reason[:120]
    else:
        _sym_detail += "；failure_reason 为空（仅 status 标记失败）"
    chain.append(
        {
            "layer": 1,
            "type": "SYMPTOM",
            "label": "表层现象",
            "detail": _sym_detail,
            "evidence_ref": "status + failure_reason",
        }
    )

    # ── 层 2：中间层（断言/响应差异） ──
    if expected is not None or actual is not None:
        _gap_detail = "断言(%s)：期望「%s」 vs 实际「%s」" % (
            check_type,
            str(expected)[:80] if expected is not None else "(无)",
            str(actual)[:80] if actual is not None else "(无)",
        )
        _gap_ref = "expected + actual"
    elif details:
        _gap_detail = "check_type=%s；details='%s'" % (check_type, details[:120])
        _gap_ref = "check_type + details"
    else:
        _gap_detail = "check_type=%s；无期望/实际/详情（失败发生在更早阶段）" % check_type
        _gap_ref = "check_type"
    chain.append(
        {
            "layer": 2,
            "type": "ASSERT_GAP",
            "label": "中间层·断言/响应差异",
            "detail": _gap_detail,
            "evidence_ref": _gap_ref,
        }
    )

    # ── 层 3（可选）：证据链（failure_chain 策略步骤 / diagnosis 枚举） ──
    _steps = _chain_steps(failure.get("failure_chain"))
    _ev_details: list[str] = []
    if _steps:
        _step_texts = []
        for _s in _steps[:4]:
            _flag = "✓" if _s.get("success") else "✗"
            _st = "%s %s" % (_flag, str(_s.get("strategy") or "?")[:40])
            _d = str(_s.get("detail") or "").strip()
            if _d:
                _st += ": %s" % _d[:60]
            _step_texts.append(_st)
        _ev_details.append("策略链：%s" % " → ".join(_step_texts))
    _chain_raw = str(failure.get("failure_chain") or "").strip()
    if _chain_raw and not _steps:
        _ev_details.append("failure_chain='%s'" % _chain_raw[:120])
    if _ev_details:
        chain.append(
            {
                "layer": 3,
                "type": "EVIDENCE",
                "label": "证据链",
                "detail": "；".join(_ev_details),
                "evidence_ref": "failure_chain",
            }
        )

    # ── 末层：深层根因归属（category + 依据） ──
    _cat = triage["category"]
    _basis_text = "; ".join(
        "%s(%s)" % (b["signal"], b["matched"]) for b in triage.get("category_basis", [])
    )
    if _cat == TRIAGE_ENV:
        _root_detail = (
            "ENV 环境/数据问题：靶子系统不可达/依赖缺失/DB 未迁移/凭据或前置数据异常，"
            "非引擎与被测系统逻辑缺陷。依据：%s" % _basis_text
        )
    elif _cat == TRIAGE_SUT:
        _root_detail = (
            "SUT 被测系统缺陷：页面/接口/数据库呈现的业务行为不符合预期，"
            "属靶子系统实现问题。依据：%s" % _basis_text
        )
    elif _cat == TRIAGE_ENGINE:
        _root_detail = (
            "ENGINE 引擎/断言缺陷：断言定义/定位策略/期望生成存在缺陷，"
            "需修复引擎侧而非被测系统。依据：%s" % _basis_text
        )
    else:
        _root_detail = (
            "UNKNOWN 证据不足：当前 executions 字段无足够证据支撑三分类归属，"
            "fail-open 不硬造根因（需补充控制台/网络/DB 快照证据）。"
        )
    chain.append(
        {
            "layer": len(chain) + 1,
            "type": "ROOT_CAUSE",
            "label": "深层·根因归属",
            "detail": _root_detail,
            "evidence_ref": "failure_reason + failure_chain + category_basis",
        }
    )
    return chain


# ═══════════════════════════════════════════════════════════════════════════
# 对外主入口：组装 failure_triage 结构
# ═══════════════════════════════════════════════════════════════════════════
def build_failure_triage(failure: dict) -> dict:
    """为单个 FAIL/ERROR 生成 failure_triage 结构（纯函数，不依赖 DB）。

    Args:
        failure: executions 单行失败证据字典（键同 classify_triage）。

    Returns:
        {
          "category": ENGINE|SUT|ENV|UNKNOWN,
          "confidence": float,
          "category_basis": [...],
          "root_cause_chain": [≥3 层，每层带 evidence_ref],
          "evidence_refs": [触发分类的证据引用列表],
          "triage_version": "triage-v1",
        }
    """
    triage = classify_triage(failure)
    chain = build_root_cause_chain(failure, triage)
    refs: list[str] = []
    for b in triage.get("category_basis", []):
        _r = str(b.get("evidence_ref") or "")
        _m = str(b.get("matched") or "")
        _entry = _r + (": " + _m if _m and _m != "(无命中)" else "")
        if _entry and _entry not in refs:
            refs.append(_entry)
    if not refs:
        refs = ["no_evidence"]
    return {
        "category": triage["category"],
        "confidence": triage["confidence"],
        "category_basis": triage.get("category_basis", []),
        "root_cause_chain": chain,
        "evidence_refs": refs,
        "triage_version": TRIAGE_VERSION,
    }


# ═══════════════════════════════════════════════════════════════════════════
# 汇总（报告级，纯增量）
# ═══════════════════════════════════════════════════════════════════════════
def summarize_triage(bugs: list[dict]) -> dict:
    """按三分类聚合 bugs 列表（供报告顶层展示，纯增量不破坏既有字段）。"""
    counts = {TRIAGE_ENGINE: 0, TRIAGE_SUT: 0, TRIAGE_ENV: 0, TRIAGE_UNKNOWN: 0}
    for b in bugs:
        _ft = b.get("failure_triage") if isinstance(b.get("failure_triage"), dict) else {}
        _cat = _ft.get("category") or TRIAGE_UNKNOWN
        counts[_cat] = counts.get(_cat, 0) + 1
    return {
        "engine": counts[TRIAGE_ENGINE],
        "sut": counts[TRIAGE_SUT],
        "env": counts[TRIAGE_ENV],
        "unknown": counts[TRIAGE_UNKNOWN],
        "total": len(bugs),
    }
