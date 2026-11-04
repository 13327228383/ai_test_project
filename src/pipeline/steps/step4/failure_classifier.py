# -*- coding: utf-8 -*-
from __future__ import annotations

"""执行失败分类器 — 区分被测系统bug vs 执行系统bug vs 环境问题。

核心判定逻辑：
    1. 收集失败链路（策略执行序列 + 每步结果）
    2. 根据链路最终状态判定 failure_category
    3. 同时判定 status: FAIL(被测系统bug) vs ERROR(执行系统bug/环境问题)

分类标准:
    PRODUCT_BUG     — 被测系统的问题（元素不存在/位置错误/行为不符合需求）
                      status = FAIL
    HARNESS_ERROR   — 执行系统的问题（定位策略全部失败/浏览器异常/框架错误）
                      status = ERROR
    ENVIRONMENT_ERROR — 环境问题（网络超时/目标服务不可用）
                      status = ERROR
    DATA_ERROR      — 测试数据问题（前置数据缺失/数据冲突）
                      status = ERROR
"""

import json
import logging

_log = logging.getLogger(__name__)

# 失败分类常量
PRODUCT_BUG = "PRODUCT_BUG"
HARNESS_ERROR = "HARNESS_ERROR"
ENVIRONMENT_ERROR = "ENVIRONMENT_ERROR"
DATA_ERROR = "DATA_ERROR"

# 状态映射：PRODUCT_BUG → FAIL，其他 → ERROR
CATEGORY_TO_STATUS = {
    PRODUCT_BUG: "FAIL",
    HARNESS_ERROR: "ERROR",
    ENVIRONMENT_ERROR: "ERROR",
    DATA_ERROR: "ERROR",
    "": "FAIL",  # 默认兜底
}


# ─────────────────────────────────────────────────────────────
# F1 全局修复 [未复核-需 GLM/DeepSeek 把关]（2026-08-29）：
# 失败分类/根因分析接入 AI —— 规则判定后的 AI 增补（enrich，绝不覆盖判定）
# ─────────────────────────────────────────────────────────────

_AI_ROOT_CAUSE_SYSTEM = (
    "你是资深测试工程师，基于失败链路做根因分析。只输出 JSON，不要多余文本。"
)

# 注意: JSON 输出示例的外层花括号必须写成 {{ }}，供 .format() 安全替换。
_AI_ROOT_CAUSE_PROMPT_TMPL = """请分析以下测试执行失败并输出 JSON。
## 失败分类（规则层，仅供参考，可推翻）
failure_category={category}
status={status}
## 期望
{expected}
## 实际
{actual}
## 失败原因
{failure_reason}
## 失败链路（策略序列）
{failure_chain_json}

输出 JSON 字段：
{{
  "ai_root_cause": "PRODUCT_BUG|HARNESS_ERROR|ENVIRONMENT_ERROR|DATA_ERROR|OTHER",
  "ai_severity": "critical|high|medium|low",
  "ai_suggestion": "给测试/开发的修复建议，≤120字",
  "ai_confidence": 0.0~1.0,
  "ai_evidence": "判定依据，≤80字"
}}"""


def enrich_root_cause_ai(
    chain: FailureChain,
    expected: str = "",
    actual: str = "",
    failure_reason: str = "",
    category: str = "",
    status: str = "",
) -> dict:
    """规则判定后的 AI 根因分析增补（F1 全局修复）。

    在 ``classify_failure`` 规则判定之后调用，返回 AI 增补字段字典（并入
    executions.details JSON）。**绝不修改**调用方的 status/failure_category/
    failure_reason —— 本函数只返回独立的 ``ai_*`` 键，判定语义保持规则层。

    返回字段（对齐设计 1.2 / 任务书）:
        ai_root_cause / ai_severity / ai_suggestion / ai_confidence /
        ai_model / ai_source（另附 prompt 契约中的 ai_evidence）

    降级铁律（fail-open）:
        1) 开关 ``failure_ai_enrich_enabled`` 为 False → 直接返回 {}（一键回退）；
        2) router.route 失败/超时/云端兜底失败 → 返回 {}；
        3) 返回非 dict / 缺 ai_root_cause / JSON 解析失败 → 返回 {}；
        4) 函数整体 try/except BaseException → 任何异常返回 {}，
           绝不向执行路径抛异常。

    稳定性（对齐 F2）:
        temperature=0.0 + seed=ai_stability_seed（默认 42），同输入→同输出。
    """
    try:
        from src.core.pipeline_config import _read_config_value as _cfg

        if not bool(_cfg("failure_ai_enrich_enabled", True)):
            _log.info("[FailureClassifier] failure_ai_enrich_enabled=False，跳过 AI 根因增补")
            return {}

        from src.ai.router_module import router

        _seed = int(_cfg("ai_stability_seed", 42) or 42)
        _timeout = float(_cfg("ai_local_timeout", 60.0))
        _prompt = _AI_ROOT_CAUSE_PROMPT_TMPL.format(
            category=str(category or "")[:50],
            status=str(status or "")[:20],
            expected=str(expected or "")[:500],
            actual=str(actual or "")[:500],
            failure_reason=str(failure_reason or "")[:500],
            failure_chain_json=chain.to_json()[:2000] if chain else "[]",
        )
        r = router.route(
            _prompt,
            system_prompt=_AI_ROOT_CAUSE_SYSTEM,
            expect_json=True,
            task_type="general",  # 复用既有 task 映射，避免新增 task_type 缺模型映射
            temperature=0.0,  # 稳定性铁律
            seed=_seed,
            local_timeout=_timeout,
        )
        if not isinstance(r, dict) or not r.get("success"):
            _log.info(
                "[FailureClassifier] AI 根因调用失败(fail-open): %s",
                str(r.get("error", "?") if isinstance(r, dict) else r)[:100],
            )
            return {}
        data = r.get("data")
        if isinstance(data, str):
            import json as _json

            txt = data.strip().strip("`").strip()
            if txt.startswith("json"):
                txt = txt[4:].strip()
            data = _json.loads(txt)
        if not isinstance(data, dict) or not data.get("ai_root_cause"):
            _log.warning(
                "[FailureClassifier] AI 根因返回非预期结构(fail-open): %r",
                str(data)[:100],
            )
            return {}
        try:
            _conf = float(data.get("ai_confidence", 0.0) or 0.0)
        except (TypeError, ValueError):
            _conf = 0.0
        out = {
            "ai_root_cause": str(data.get("ai_root_cause", "OTHER"))[:40],
            "ai_severity": str(data.get("ai_severity", "medium"))[:20],
            "ai_suggestion": str(data.get("ai_suggestion", ""))[:200],
            "ai_confidence": round(max(0.0, min(1.0, _conf)), 4),
            "ai_evidence": str(data.get("ai_evidence", ""))[:120],
            "ai_model": str(r.get("model", "") or "")[:80],
            "ai_source": str(r.get("source", "") or "")[:20],
        }
        _log.info(
            "[FailureClassifier] AI 根因增补成功: source=%s model=%s",
            out["ai_source"],
            out["ai_model"],
        )
        return out
    except BaseException as e:  # noqa: BLE001 —— fail-open 铁律
        _log.warning("[FailureClassifier] AI 根因增补异常(fail-open): %s", e)
        return {}

# 网络/连接类错误标记（用于把导航阶段的网络错误信号传递给 classify_failure）
# ★ BUG-1(2026-08-05): _base.py / _agent_checks.py / lifecycle.py 共用同一判定，
#   避免各调用点各自写死 "net::err" 子串导致漏判（如 ERR_CONNECTION_TIMED_OUT）。
_NETWORK_ERROR_MARKERS = (
    "net::err",
    "connection refused",
    "connection timed out",
    "econnreset",
    "econnrefused",
    "err_connection_timed_out",
    "err_connection_refused",
    "err_connection_reset",
    "err_name_not_resolved",
    "err_internet_disconnected",
    "err_address_unreachable",
    "err_network_changed",
    "err_empty_response",
    "err_timed_out",
    "err_proxy_connection_failed",
    "err_ssl_protocol_error",
    "err_tunnel_connection_failed",
)


def is_network_error_text(text: str) -> bool:
    """判断错误文本是否为网络/连接类错误。

    供 classify_failure 信号传播复用：lifecycle.goto 捕获 net::ERR_* 时置位
    engine._last_network_error；_base.py / _agent_checks.py 扫描 step_results
    错误文本时也走本函数，保证判定口径一致。
    """
    if not text:
        return False
    err = text.lower()
    return any(marker in err for marker in _NETWORK_ERROR_MARKERS)


class FailureChain:
    """失败链路记录器：记录策略执行序列和每步结果。"""

    def __init__(self):
        self._steps: list[dict] = []

    def add_step(
        self,
        strategy: str,
        success: bool,
        detail: str = "",
        element_found: bool = None,
        dom_has_element: bool = None,
    ):
        """记录一个策略步骤的执行结果。

        参数:
            strategy: 策略名称（如 smart_locator_high / scroll_search / visual_click）
            success: 该策略是否成功
            detail: 详细信息（错误原因等）
            element_found: 该策略是否找到了元素（None=不适用）
            dom_has_element: DOM中是否存在目标元素（None=未检查）
        """
        self._steps.append(
            {
                "strategy": strategy,
                "success": success,
                "detail": detail[:200] if detail else "",
                "element_found": element_found,
                "dom_has_element": dom_has_element,
            }
        )

    def to_json(self) -> str:
        """序列化为JSON字符串（存入 failure_chain 字段）。"""
        return json.dumps(self._steps, ensure_ascii=False)

    @property
    def steps(self) -> list[dict]:
        return self._steps

    def last_step(self) -> dict | None:
        return self._steps[-1] if self._steps else None


def classify_failure(
    chain: FailureChain,
    playwright_error: bool = False,
    timeout_error: bool = False,
    network_error: bool = False,
) -> tuple:
    """根据失败链路判定 failure_category 和 status。

    参数:
        chain: 失败链路记录
        playwright_error: 是否发生 Playwright 框架级异常
        timeout_error: 是否发生超时
        network_error: 是否发生网络错误

    返回:
        (failure_category, status)
    """
    # 1. 环境问题优先判定
    if network_error:
        _log.info("[FailureClassifier] → ENVIRONMENT_ERROR (网络错误)")
        return ENVIRONMENT_ERROR, "ERROR"

    if timeout_error and not chain.steps:
        _log.info("[FailureClassifier] → ENVIRONMENT_ERROR (超时且无策略执行)")
        return ENVIRONMENT_ERROR, "ERROR"

    # 2. Playwright 框架级异常 → 执行系统bug
    if playwright_error:
        _log.info("[FailureClassifier] → HARNESS_ERROR (Playwright框架异常)")
        return HARNESS_ERROR, "ERROR"

    # 3. 根据失败链路判定
    if not chain.steps:
        _log.info("[FailureClassifier] → HARNESS_ERROR (无策略执行记录)")
        return HARNESS_ERROR, "ERROR"

    # 检查最后一步的 DOM 状态
    last = chain.last_step()

    # 3a. 如果DOM中确实没有目标元素 → 被测系统bug（元素不存在）
    if last.get("dom_has_element") == False:
        _log.info("[FailureClassifier] → PRODUCT_BUG (DOM中不存在目标元素)")
        return PRODUCT_BUG, "FAIL"

    # 3b. 如果DOM中有元素但所有策略都失败 → 执行系统bug（定位能力不足）
    if last.get("dom_has_element") == True:
        _log.info("[FailureClassifier] → HARNESS_ERROR (DOM存在元素但定位失败)")
        return HARNESS_ERROR, "ERROR"

    # 3c. 检查是否有任何策略找到元素但执行失败 → 被测系统bug（行为不符合预期）
    any_found = any(s.get("element_found") for s in chain.steps)
    if any_found:
        _log.info("[FailureClassifier] → PRODUCT_BUG (元素找到但行为不符合预期)")
        return PRODUCT_BUG, "FAIL"

    # 3d. 默认：所有策略都未找到元素，且未确认DOM状态
    # ★ v11.4(2026-07-31): 区分"元素不存在"vs"定位能力不足"
    #   根因(§10.9): 原代码默认归HARNESS_ERROR，但Agent已充分探索(>=2策略)后
    #   仍找不到元素 → 更可能是元素不存在(PRODUCT_BUG)，而非引擎bug。
    #   通用性: 跨系统/行业 — 策略数量是Agent探索充分性的通用指标。
    _strategy_count = len(chain.steps)
    if _strategy_count >= 2:
        _log.info(
            "[FailureClassifier] → PRODUCT_BUG (Agent充分探索%d策略未找到元素)",
            _strategy_count,
        )
        return PRODUCT_BUG, "FAIL"
    _log.info(
        "[FailureClassifier] → HARNESS_ERROR (仅%d策略未找到元素，DOM状态未知)",
        _strategy_count,
    )
    return HARNESS_ERROR, "ERROR"


def build_failure_reason(chain: FailureChain) -> str:
    """从失败链路构建人类可读的失败原因。"""
    if not chain.steps:
        return "未知失败原因"

    parts = []
    for s in chain.steps:
        status = "✓" if s["success"] else "✗"
        detail = s.get("detail", "")
        parts.append(f"{status} {s['strategy']}" + (f": {detail}" if detail else ""))

    return " → ".join(parts)


# ── 词表唯一源（提升自 src/pipeline/steps/step4/diagnosis.py，下游 import 引用，勿重抄）──
_CREDENTIAL_HINTS = (
    "401",
    "403",
    "credential",
    "login failed",
    "login fail",
    "unauthorized",
    "forbidden",
    "authentication failed",
    "invalid credential",
    "用户名或密码",
    "密码错误",
    "凭据无效",
    "凭据错误",
)
