# -*- coding: utf-8 -*-
"""W3 九类质量治理能力 —— 只读采集器集合。

设计依据：docs/frontend-adaptation-design-2026-08-06.md §3.2 / §8.7 / §8.8 / §8.9

════════════════════════════════════════════════════════════════════════════
★★★ 只读红线（§8.9，改本文件前必读）★★★
════════════════════════════════════════════════════════════════════════════
1. **绝不触发执行**：本模块不得 import 任何 `step4_executor` / `run_*` / `enforce_*`。
   特别注意 `fake_pass_audit.enforce_fake_pass_block()` 会 UPDATE executions 行 ——
   本模块**只能**调用同文件的 `audit_fake_passes()`（纯 SELECT）。
2. **绝不返回 0 冒充**：开关关闭时只返回 {enabled:false, flag_key, reason}，
   不得返回任何 count / rate / distribution 数值字段。前端见 enabled:false 渲染引导态，
   若后端塞个 0 过去，用户会误以为"指标正常"，比不显示更危险。
3. **绝不报错致页面崩**：每个采集器由 `_safe()` 独立兜底，单点异常不影响其余八块。
4. **绝不泄漏凭证**：identity_pool 采用**白名单投影**（只取 key 名与 role），
   而非黑名单剔除 —— 白名单是唯一能对"未来新增字段"依然安全的做法。
5. **零 DDL / 零 DML**：全程 SELECT，不得出现 INSERT / UPDATE / DELETE / ALTER。
════════════════════════════════════════════════════════════════════════════
"""
from __future__ import annotations

import json
import logging
from typing import Any, Callable

from src.core.pipeline_config import _read_config_value as _cfg

_log = logging.getLogger(__name__)

# 能力名 → 开关 key。前端拿 flag_key 直接调 /api/config/toggle 开启，
# 因此这里必须与 config.py 的 TOGGLEABLE_FLAGS 保持一致（两处都改，勿只改一处）。
CAP_FLAGS: dict[str, str] = {
    "fake_pass": "FAKE_PASS_AUDIT_ENABLED",
    "engine_health": "ENGINE_HEALTH_ENABLED",
    "perf_baseline": "PERF_BASELINE_ENABLED",
    "adi": "ADI_GATE_ENABLED",
    "privilege_matrix": "PRIVILEGE_MATRIX_ENABLED",
    "identity_pool": "IDENTITY_POOL_ENABLED",
    "contract": "CONTRACT_ENABLED",
    "network_fault": "NETWORK_FAULT_ENABLED",
    "idempotency": "IDEMPOTENCY_ENABLED",
}

# 能力名 → 中文名，用于 reason 引导文案
CAP_LABELS: dict[str, str] = {
    "fake_pass": "假 PASS 审计",
    "engine_health": "引擎健康治理",
    "perf_baseline": "性能基线",
    "adi": "ADI 断言深度",
    "privilege_matrix": "越权矩阵",
    "identity_pool": "身份池",
    "contract": "契约断言",
    "network_fault": "网络故障注入",
    "idempotency": "幂等比对",
}

# ADI 单次扫描上限。test_cases 可能上万行，而 compute_adi 是纯 Python 计算，
# 不设上限会让一个"只读摘要"接口拖到十几秒。2000 条足以反映分布形态。
_ADI_SCAN_LIMIT = 2000

# 明细列表截断长度（避免摘要接口返回超大 payload）
_TOP_HITS_LIMIT = 20
_PATTERNS_LIMIT = 10

# 异常摘要统一长度（§8.10）
_ERR_MAX = 160


# ══════════════════════════════════════════════════════════════
# 通用构件
# ══════════════════════════════════════════════════════════════


def flag_enabled(flag_key: str) -> bool:
    """读取开关生效值。

    统一走 `_read_config_value(key, False)`：其内部 `_coerce` 已正确处理
    字符串 "false" → False（直接 bool("false") 会得到 True，是经典陷阱）。
    """
    try:
        return bool(_cfg(flag_key, False))
    except Exception as e:  # noqa: BLE001 —— 配置读取失败一律视为未开启（fail-closed）
        _log.warning(
            "[W3] 读取开关 %s 失败(按关闭处理): %s", flag_key, str(e)[:_ERR_MAX]
        )
        return False


def _disabled(flag_key: str, reason: str) -> dict[str, Any]:
    """产出「未开启」能力块。

    ★ 红线 2：这里**只有三个字段**，不带任何数值字段。
    """
    return {
        "enabled": False,
        "flag_key": flag_key,
        "reason": reason,
    }


def disabled_for(cap_name: str) -> dict[str, Any]:
    """按能力名生成标准「未开启」块（带中文引导文案）。"""
    flag_key = CAP_FLAGS.get(cap_name, "")
    label = CAP_LABELS.get(cap_name, cap_name)
    return _disabled(flag_key, "%s 未开启（开关 %s）" % (label, flag_key))


def _safe(fn: Callable[[], dict], cap_name: str) -> dict[str, Any]:
    """采集器兜底执行器。

    ★ 红线 3：任何异常都被吃掉并转成该能力块内的 `error` 字段，
      使整体响应恒为 HTTP 200 —— 一个采集器挂掉不该让整张治理面板白屏。

    仅在开关已开启时才会调用本函数，因此异常态的 enabled 恒为 True。
    """
    try:
        result = fn()
        if not isinstance(result, dict):
            raise TypeError("采集器返回值非 dict: %s" % type(result).__name__)
        return result
    except Exception as e:  # noqa: BLE001 —— 兜底是本函数的唯一职责
        _log.warning("[W3] 采集器 %s 异常(降级返回): %s", cap_name, e, exc_info=True)
        return {
            "enabled": True,
            "flag_key": CAP_FLAGS.get(cap_name, ""),
            "has_data": False,
            "error": ("%s: %s" % (type(e).__name__, e))[:_ERR_MAX],
        }


# ══════════════════════════════════════════════════════════════
# 1. 假 PASS 审计
# ══════════════════════════════════════════════════════════════


def collect_fake_pass(
    project: str, run_id: str = "", with_details: bool = False
) -> dict[str, Any]:
    """假 PASS 审计摘要。

    数据来源：step5/fake_pass_audit.py:44 `audit_fake_passes()`（纯 SELECT）。
    ★ 红线 1：绝不调用同文件的 `enforce_fake_pass_block()` —— 那个会 UPDATE executions。

    参数:
        project: 项目名。
        run_id: 指定 run，空串 = 全量。
        with_details: 是否附带 top_hits 明细（需 execution.run 权限）。
    """
    from src.pipeline.steps.step5.fake_pass_audit import audit_fake_passes

    report = audit_fake_passes(project, run_id) or {}
    total = int(report.get("total_passes", 0) or 0)

    block: dict[str, Any] = {
        "enabled": True,
        "flag_key": CAP_FLAGS["fake_pass"],
        "has_data": total > 0,
        "total_passes": total,
        "fake_passes": int(report.get("fake_passes", 0) or 0),
        "rate": round(float(report.get("rate", 0.0) or 0.0), 4),
        "threshold": float(report.get("threshold", 0.05) or 0.05),
        "alert": bool(report.get("alert", False)),
    }
    if with_details:
        hits = report.get("hits") or []
        block["top_hits"] = [
            {
                "exec_id": h.get("exec_id"),
                "case_id": str(h.get("case_id", "") or ""),
                "confidence": h.get("confidence"),
                "evidence_gaps": list(h.get("evidence_gaps") or []),
            }
            for h in hits[:_TOP_HITS_LIMIT]
        ]
    return block


# ══════════════════════════════════════════════════════════════
# 2. 引擎健康治理
# ══════════════════════════════════════════════════════════════


def collect_engine_health(
    project: str, run_id: str = "", with_details: bool = False
) -> dict[str, Any]:
    """引擎健康（HARNESS_ERROR 聚类）摘要。

    数据来源：step5/engine_health.py:60 `engine_health_report()`（纯 SELECT）。
    """
    from src.pipeline.steps.step5.engine_health import engine_health_report

    report = engine_health_report(project, run_id) or {}
    total = int(report.get("total", 0) or 0)

    block: dict[str, Any] = {
        "enabled": True,
        "flag_key": CAP_FLAGS["engine_health"],
        "has_data": total > 0,
        "total": total,
        "harness_error": int(report.get("harness_error", 0) or 0),
        "harness_rate": round(float(report.get("harness_rate", 0.0) or 0.0), 4),
        "threshold": float(report.get("threshold", 0.10) or 0.10),
        "verdict": str(report.get("verdict", "GREEN") or "GREEN"),
    }
    if with_details:
        patterns = report.get("patterns") or []
        block["patterns"] = [
            {
                "pattern": str(p.get("pattern", "") or ""),
                "count": int(p.get("count", 0) or 0),
                "likely_module": str(p.get("likely_module", "other") or "other"),
                "sample_case_ids": list(p.get("sample_case_ids") or []),
            }
            for p in patterns[:_PATTERNS_LIMIT]
        ]
    return block


# ══════════════════════════════════════════════════════════════
# 3. 性能基线
# ══════════════════════════════════════════════════════════════


def collect_perf_baseline(project: str, run_id: str = "") -> dict[str, Any]:
    """性能基线对比摘要。

    数据来源：step5/perf_baseline.py:103 `build_perf_baseline()`（纯 SELECT）。
    无当前 run 数据或无历史基线时上游返回 {} —— 此时 has_data=False，
    ★ 不返回 items:[]，避免前端把"没数据"画成"零退化"。
    """
    from src.pipeline.steps.step5.perf_baseline import build_perf_baseline

    window = _perf_window()
    report = build_perf_baseline(project, run_id or "", window) or {}
    items = report.get("items") or []

    block: dict[str, Any] = {
        "enabled": True,
        "flag_key": CAP_FLAGS["perf_baseline"],
        "has_data": bool(items),
        "window": int(report.get("window", window) or window),
    }
    if items:
        block["items"] = items
        block["degraded_count"] = sum(
            1 for it in items if str(it.get("verdict", "")) == "degraded"
        )
    return block


def _perf_window() -> int:
    """基线对比窗口（PERF_BASELINE_WINDOW，_taxonomy.py 权威默认值 = 1）。"""
    try:
        return max(1, int(_cfg("PERF_BASELINE_WINDOW", 1) or 1))
    except (TypeError, ValueError):
        return 1


# ══════════════════════════════════════════════════════════════
# 4. ADI 断言深度
# ══════════════════════════════════════════════════════════════


def collect_adi(project: str, version_id: int = 0) -> dict[str, Any]:
    """ADI 断言深度分布（实时重算）。

    ★ §8.8：ADI **没有数据库列** —— `guard.py:62` 只把 adi 写进内存中的 case dict，
      从不落库。因此唯一可靠的数据源是 `test_cases.payload_json`（完整用例 JSON），
      逐条走 `compute_adi()` 纯函数重算。这也保证了口径与 Step3 门禁完全一致。

    参数:
        project: 项目名。
        version_id: 项目版本 ID，0 = legacy 数据。

    返回:
        depth_distribution: [n0, n1, n2, n3] —— ADI 得分 0~3 各档用例数
        mean:               平均 ADI
        total_cases:        参与统计的用例数
        unresolved:         payload 解析失败 / compute_adi 返回 error 的用例数
    """
    from src.core.db_manager import get_connection
    from src.pipeline.steps.step3.oracle.adi import compute_adi

    distribution = [0, 0, 0, 0]
    total = 0
    unresolved = 0
    score_sum = 0

    conn = get_connection()
    try:
        rows = (
            conn.execute(
                "SELECT case_id, payload_json FROM test_cases "
                "WHERE project=%s AND version_id=%s AND valid_status='valid' "
                "LIMIT %s",
                (project, int(version_id), _ADI_SCAN_LIMIT),
            ).fetchall()
            or []
        )
    finally:
        conn.close()

    for row in rows:
        payload = row.get("payload_json") if isinstance(row, dict) else None
        case = _load_case_payload(payload)
        if case is None:
            unresolved += 1
            continue
        score, detail = compute_adi(case)
        if isinstance(detail, dict) and detail.get("error"):
            # compute_adi 内部 fail-closed 返回 (0, {"error":...})：
            # 计入 unresolved 而非当作真实的 0 分，否则会虚增"断言缺失"告警。
            unresolved += 1
            continue
        idx = min(max(int(score), 0), 3)
        distribution[idx] += 1
        score_sum += idx
        total += 1

    # ── P0-3 增量：深断言 fail 占比信号（默认关闭，仅度量）──
    #   ★ §14 [未复核-需 GLM/DeepSeek 把关]：接入 collect_adi 质量报告。
    #   静态代理：从有效用例 payload 抽取全部 check 类型（视为失败信号），
    #   用 DeepAssertionMetrics 计算深断言占比。禁用开关时 ratio=0.0，绝不报错。
    #   说明：collect_adi 仅持有用例定义、无执行结果，故此处为"静态代理占比"，
    #        真正的 run 级深断言 fail 占比由 step4_executor 在执行后算得。
    deep_ratio = 0.0
    try:
        if bool(_cfg("ADI_SIGNAL_GATE_ENABLED", False)):
            from src.pipeline.steps.step3.oracle.adi_signal import (
                DeepAssertionMetrics,
                extract_case_check_types,
            )

            _proxy: list[dict] = []
            for row in rows:
                payload = (
                    row.get("payload_json") if isinstance(row, dict) else None
                )
                case = _load_case_payload(payload)
                if case is None:
                    continue
                for t in extract_case_check_types(case):
                    _proxy.append({"type": t, "status": "FAIL"})
            deep_ratio = DeepAssertionMetrics.collect(_proxy).get("ratio", 0.0) or 0.0
    except Exception as _e:  # noqa: BLE001
        _log.warning("[collect_adi] 深断言占比计算异常(置0): %s", _e)
        deep_ratio = 0.0

    return {
        "enabled": True,
        "flag_key": CAP_FLAGS["adi"],
        "has_data": total > 0,
        "depth_distribution": distribution,
        "mean": round(score_sum / total, 2) if total else 0.0,
        "total_cases": total,
        "unresolved": unresolved,
        "deep_assertion_fail_ratio": round(float(deep_ratio), 4),
        "scan_limit": _ADI_SCAN_LIMIT,
        "truncated": len(rows) >= _ADI_SCAN_LIMIT,
    }


def _load_case_payload(payload: Any) -> dict | None:
    """payload_json（JSON 列，驱动可能返回 str 或已解析 dict）→ case dict。

    解析失败返回 None（调用方计入 unresolved），绝不抛异常中断整批统计。
    """
    if isinstance(payload, dict):
        return payload
    if not payload:
        return None
    try:
        parsed = json.loads(payload) if isinstance(payload, (str, bytes)) else None
    except (ValueError, TypeError):
        return None
    return parsed if isinstance(parsed, dict) else None


# ══════════════════════════════════════════════════════════════
# 5. 越权矩阵
# ══════════════════════════════════════════════════════════════


def collect_privilege_matrix(project: str) -> dict[str, Any]:
    """越权矩阵规模摘要。

    ★ 只返回组合**数量**，不返回组合明细 —— 明细里含身份键名与数据域，
      属于攻击面信息，不应出现在只读摘要接口里。

    ⚠️ `build_privilege_matrix()` 内部有 `PRIVILEGE_MATRIX_ENABLED` 门控
      （privilege_matrix.py:42），关闭时返回空矩阵。本采集器只在开关开启时被调用，
      因此不会出现"开关开着却恒返回 0"的矛盾。
    """
    from src.pipeline.utils.identity_pool import IdentityPool
    from src.pipeline.utils.privilege_matrix import (
        build_privilege_matrix,
        role_hierarchy,
    )

    # ★ IdentityPool.load 是 staticmethod，签名 load(project)（已核实源码，
    #   不是 IdentityPool(project).load()）
    pool = IdentityPool.load(project) or {}
    matrix = build_privilege_matrix(pool) or {}
    horizontal = matrix.get("horizontal") or []
    vertical = matrix.get("vertical") or []

    return {
        "enabled": True,
        "flag_key": CAP_FLAGS["privilege_matrix"],
        "has_data": bool(horizontal or vertical),
        "horizontal_count": len(horizontal),
        "vertical_count": len(vertical),
        "identities": len(pool),
        "role_hierarchy": list(role_hierarchy()),
    }


# ══════════════════════════════════════════════════════════════
# 6. 身份池（脱敏）
# ══════════════════════════════════════════════════════════════

# 即便走白名单投影，仍对 key 名本身做一次黑名单过滤：
# 身份池的 key 由用户自定义，出现 "admin_token" 这类命名并非不可能。
_SENSITIVE_TOKENS = (
    "password",
    "passwd",
    "token",
    "secret",
    "credential",
    "apikey",
    "api_key",
)


def collect_identity_pool(project: str) -> dict[str, Any]:
    """身份池摘要（严格脱敏）。

    ★ 红线 4：采用**白名单投影** —— 只读取 `role` 一个字段，
      其余字段（username / password / permissions / data_scope …）一律不触碰。
      白名单优于黑名单：将来身份池新增字段时，黑名单会漏，白名单天然安全。
    """
    from src.pipeline.utils.identity_pool import IdentityPool

    pool = IdentityPool.load(project) or {}

    identity_keys: list[str] = []
    roles: set[str] = set()
    for raw_key, attrs in pool.items():
        key = str(raw_key or "").strip()
        if not key or _is_sensitive_name(key):
            # 键名本身疑似凭证 → 连名字都不回显，只计入总数
            continue
        identity_keys.append(key)
        if isinstance(attrs, dict):
            role = str(attrs.get("role", "") or "").strip()
            if role:
                roles.add(role)

    default_identity = str(_cfg("IDENTITY_DEFAULT", "") or "").strip()
    if _is_sensitive_name(default_identity):
        default_identity = ""

    return {
        "enabled": True,
        "flag_key": CAP_FLAGS["identity_pool"],
        "has_data": bool(pool),
        "identity_count": len(pool),
        "identity_keys": sorted(identity_keys),
        "roles": sorted(roles),
        "default_identity": default_identity,
    }


def _is_sensitive_name(name: str) -> bool:
    """名称是否疑似凭证字段（大小写不敏感子串匹配）。"""
    low = str(name or "").lower()
    return any(tok in low for tok in _SENSITIVE_TOKENS)


# ══════════════════════════════════════════════════════════════
# 7~9. 契约 / 网络故障 / 幂等 —— 本期仅开关三态
# ══════════════════════════════════════════════════════════════


def collect_flag_only(flag_key: str, cap_name: str) -> dict[str, Any]:
    """仅返回开关状态的能力块（contract / network_fault / idempotency 共用）。

    ★ 已核实：`oracle_results.oracle_type` 全仓库只写入 'correctness' 与 'performance'
      两个值，**没有** contract / idempotency / network_fault 记录。
      因此本期这三块无任何可聚合的真实数据 —— 与其编造数值，不如老实给
      has_data:false + note 说明，等 oracle_results 落库后再接明细（设计 §5 Q3）。
    """
    return {
        "enabled": True,
        "flag_key": flag_key,
        "has_data": False,
        "note": "%s 已启用；结果明细待 oracle_results 落库后接入"
        % CAP_LABELS.get(cap_name, cap_name),
    }
