# -*- coding: utf-8 -*-
"""Step3 方法论白名单后置校验器（MethodologyValidator）—— 流水线强化专项 T2。

★ 职责：Step3 产出用例后强制校验方法论标签（LLM 自觉不可靠，改为规则兜底）：
    - scenario_type ∈ {happy_path, error_path, boundary, security, exploratory}
    - test_method ∈ 权威目录 18 码（EP/BV/CE/DT/ST/OT/EG/FD/SD/PC/SX/SI/CS/AU/SS/CT/PF/ET，
      单一事实源 src/testing_methods/catalog.py，禁止本文件再硬编码方法论码）
    - 4 维配额（error_path 20-30% / boundary 15-20% / security 10-15%）
  处置策略（Q4 拍板）：
    - 低危（可映射别名 / 大小写差异 / 未决定标记）→ 自动修正 + 记录 fix_note
    - 高危（白名单外且不可映射）→ 标记 method_valid=0 + blocked（调用方读后决定是否中止）

★ 根因修复(2026-09-21)：白名单/别名从 catalog.py 动态派生，消除"校验层只认 12 码、
  新 6 码被高危阻断"的口径散布——方法论全集收敛为单一事实源，任何新增/调整只改 catalog。

★ 依赖方向：仅依赖标准库 + catalog；供 _modeling_engine / _intent_quality 复用，不反向依赖 backend。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

# ★ 单一权威来源（2026-09-21）：18 码全集从 catalog 派生，绝不在本文件硬编码方法论码
from src.testing_methods.catalog import METHOD_CODES as CATALOG_METHOD_CODES
from src.testing_methods.catalog import UNDECIDED_METHOD_MARKERS
from src.testing_methods.catalog import METHOD_CATALOG as CATALOG_METHOD_CATALOG

_log = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────
# T2 唯一事实源枚举（design §7.2）
# ─────────────────────────────────────────────────────────────────────
SCENARIO_TYPE_WHITELIST: frozenset[str] = frozenset(
    {"happy_path", "error_path", "boundary", "security", "exploratory"}
)
# ★ test_method 白名单 = 权威目录 18 码（此前硬编码 {EP,BV,EG,FD} 仅 4 码，
#   导致 AU/CS/SS/CT/PF/ET 六项新方法论在校验层被高危阻断、无法落库）
TEST_METHOD_WHITELIST: frozenset[str] = frozenset(CATALOG_METHOD_CODES)
# 别名（中文名 ← 目录中文名派生 + 常见短写补充）。目录中文名作为别名，
# 保证 case 里写了中文方法论名也能就近映射到规范码；短写表兜底语义词。
METHOD_ALIAS: dict[str, str] = {
    name: code for code, (name, _cat, _desc) in CATALOG_METHOD_CATALOG.items()
}
METHOD_ALIAS.update({
    "等价": "EP", "等价类": "EP",
    "边界": "BV", "边界值": "BV",
    "错误推测": "EG", "异常": "EG",
    "场景法": "FD", "场景": "FD",
    "越权": "AU", "越权访问": "AU", "权限不足": "AU",
    "csrf": "CS", "跨站请求": "CS", "防伪": "CS",
    "会话": "SS", "登录": "SS", "超时": "SS",
    "兼容": "CT", "响应式": "CT", "多端": "CT",
    "性能": "PF", "加载": "PF",
    "探索": "ET", "异常场景": "ET",
})
# 配额（占全部用例比例，对齐 _prompts.py 编写规则 1）
QUOTA: dict[str, tuple[float, float]] = {
    "error_path": (0.20, 0.30),
    "boundary": (0.15, 0.20),
    "security": (0.10, 0.15),
}
SCENARIO_ALIAS: dict[str, str] = {
    "正向": "happy_path",
    "正向流程": "happy_path",
    "happy": "happy_path",
    "happy-path": "happy_path",
    "异常": "error_path",
    "异常路径": "error_path",
    "错误路径": "error_path",
    "error": "error_path",
    "error-path": "error_path",
    "边界": "boundary",
    "边界值": "boundary",
    "boundary-value": "boundary",
    "安全": "security",
    "安全验证": "security",
    "security-check": "security",
}
# ★ 取消硬编码扩展码表（2026-09-21）：白名单已覆盖权威目录全部 18 码，
#   无需再区分"核心 4 码 + 扩展 8 码"；保留空集以兼容 _fix_label 分支结构。
PROMPT_EXTENDED_METHODS: frozenset[str] = frozenset()
# 未决定标记 → 契约归一为 FD（场景法兜底）。
# ★ 契约修复(2026-09-01): 与 case_generator/_test_method_rules.normalize_method_code
#   单一事实源对齐——'playwright'/''/None/未知 并非真实方法论编码，而是"未决定"标记，
#   统一就近映射为 FD，避免同一批数据在制造层被归一为 FD、在校验层却被判高危阻断。
UNDECIDED_METHOD_TO_FD: frozenset[str] = UNDECIDED_METHOD_MARKERS  # SSOT: catalog


@dataclass
class MethodologyFixRecord:
    """单条用例方法论标签校验/修正记录（可序列化）。"""

    case_id: str
    field: str
    original: str
    fixed: str
    risk: str = "low"
    action: str = "keep"
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        """序列化为 dict（供质量摘要 / 前端展示）。"""
        return {
            "case_id": self.case_id,
            "field": self.field,
            "original": self.original,
            "fixed": self.fixed,
            "risk": self.risk,
            "action": self.action,
            "note": self.note,
        }


class MethodologyValidator:
    """方法论白名单校验器：scenario_type/test_method 白名单 + 配额 + 处置策略。"""

    def __init__(
        self,
        scenario_whitelist: set[str] | frozenset[str] | None = None,
        method_whitelist: set[str] | frozenset[str] | None = None,
        quota: dict[str, tuple[float, float]] | None = None,
    ) -> None:
        """初始化（白名单/配额可覆盖，便于测试注入）。"""
        self.scenario_whitelist = frozenset(
            scenario_whitelist or SCENARIO_TYPE_WHITELIST
        )
        self.method_whitelist = frozenset(method_whitelist or TEST_METHOD_WHITELIST)
        self.quota = dict(quota or QUOTA)

    # ─────────────────────────────────────────────────────────────────
    # 公开 API
    # ─────────────────────────────────────────────────────────────────
    def validate(self, cases: list[dict[str, Any]]) -> dict[str, Any]:
        """对一批用例做方法论标签校验（就地修正/标记）。

        行为：
          - 低危 → 就地改写 case 的 scenario_type / test_method 为白名单规范值，
            并写入 case["method_fix_note"] / case["method_valid"]=1。
          - 高危 → 不改写，写入 case["method_valid"]=0 + case["method_fix_note"]，
            results 中 action="blocked"。

        Args:
            cases: Step3 产出的用例列表（每个含 scenario_type / test_method）。

        Returns:
            {
              "validated": int, "valid": int, "auto_fixed": int, "blocked_count": int,
              "blocked": bool, "results": [MethodologyFixRecord.to_dict(), ...],
              "quota_gaps": {scenario_type: {actual, min, max, gap, ok}},
              "dispositions": {"auto_fix": [case_id,...], "blocked": [case_id,...]},
            }
        """
        cases = cases or []
        results: list[dict[str, Any]] = []
        dispositions: dict[str, list[str]] = {"auto_fix": [], "blocked": []}
        valid_count = 0
        auto_fixed = 0
        blocked_count = 0

        for case in cases:
            case_id = str(case.get("case_id") or case.get("title") or "?")
            scenario = str(case.get("scenario_type") or "").strip()
            method = str(case.get("test_method") or "").strip()

            case["method_valid"] = 1
            case["method_fix_note"] = ""

            # ── scenario_type 校验 ──
            scen_rec = self._fix_label("scenario_type", scenario)
            if scen_rec.action == "auto_fix":
                case["scenario_type"] = scen_rec.fixed
                case["method_fix_note"] = self._append_note(
                    case.get("method_fix_note", ""), scen_rec.note
                )
                auto_fixed += 1
                dispositions["auto_fix"].append(case_id)
            elif scen_rec.action == "blocked":
                case["method_valid"] = 0
                case["method_fix_note"] = self._append_note(
                    case.get("method_fix_note", ""), scen_rec.note
                )
                blocked_count += 1
                dispositions["blocked"].append(case_id)
            else:
                valid_count += 1

            # ── test_method 校验 ──
            meth_rec = self._fix_label("test_method", method)
            if meth_rec.action == "auto_fix":
                case["test_method"] = meth_rec.fixed
                case["method_fix_note"] = self._append_note(
                    case.get("method_fix_note", ""), meth_rec.note
                )
                auto_fixed += 1
                if case_id not in dispositions["auto_fix"]:
                    dispositions["auto_fix"].append(case_id)
            elif meth_rec.action == "blocked":
                case["method_valid"] = 0
                case["method_fix_note"] = self._append_note(
                    case.get("method_fix_note", ""), meth_rec.note
                )
                blocked_count += 1
                if case_id not in dispositions["blocked"]:
                    dispositions["blocked"].append(case_id)

            results.append(
                MethodologyFixRecord(
                    case_id=case_id,
                    field="scenario_type/test_method",
                    original=f"{scenario}/{method}",
                    fixed=(
                        f"{case.get('scenario_type', '')}/{case.get('test_method', '')}"
                    ),
                    risk=("high" if case.get("method_valid") == 0 else "low"),
                    action=(
                        "blocked"
                        if case.get("method_valid") == 0
                        else ("auto_fix" if case.get("method_fix_note") else "keep")
                    ),
                    note=case.get("method_fix_note", ""),
                ).to_dict()
            )

        quota_gaps = self._check_quota(cases)
        return {
            "validated": len(cases),
            "valid": valid_count,
            "auto_fixed": auto_fixed,
            "blocked_count": blocked_count,
            "blocked": blocked_count > 0,
            "results": results,
            "quota_gaps": quota_gaps,
            "dispositions": dispositions,
        }

    # ─────────────────────────────────────────────────────────────────
    # 内部实现
    # ─────────────────────────────────────────────────────────────────
    def _fix_label(self, field: str, value: str) -> MethodologyFixRecord:
        """判定单个标签值并给出处置。

        Returns:
            MethodologyFixRecord（action: keep / auto_fix / blocked）。
        """
        value = (value or "").strip()
        if not value:
            return MethodologyFixRecord(
                case_id="",
                field=field,
                original="",
                fixed="",
                risk="high",
                action="blocked",
                note=f"{field} 缺失（空值），高危阻断",
            )

        if field == "scenario_type":
            if value in self.scenario_whitelist:
                return MethodologyFixRecord(
                    case_id="", field=field, original=value, fixed=value,
                    action="keep", note="",
                )
            lowered = value.lower().strip()
            if lowered in self.scenario_whitelist:
                return MethodologyFixRecord(
                    case_id="", field=field, original=value, fixed=lowered,
                    risk="low", action="auto_fix",
                    note=f"scenario_type 大小写差异 {value} → {lowered}，已自动修正",
                )
            if lowered in SCENARIO_ALIAS:
                fixed = SCENARIO_ALIAS[lowered]
                return MethodologyFixRecord(
                    case_id="", field=field, original=value, fixed=fixed,
                    risk="low", action="auto_fix",
                    note=f"scenario_type 别名 {value} → {fixed}，已就近映射",
                )
            return MethodologyFixRecord(
                case_id="", field=field, original=value, fixed="",
                risk="high", action="blocked",
                note=f"白名单外 scenario_type={value}（合法: "
                f"{sorted(self.scenario_whitelist)}），高危阻断",
            )

        # test_method
        upper = value.upper().strip()
        if upper in self.method_whitelist:
            if upper != value:
                return MethodologyFixRecord(
                    case_id="", field=field, original=value, fixed=upper,
                    risk="low", action="auto_fix",
                    note=f"test_method 大小写差异 {value} → {upper}，已自动修正",
                )
            return MethodologyFixRecord(
                case_id="", field=field, original=value, fixed=value,
                action="keep", note="",
            )
        if value in METHOD_ALIAS:
            fixed = METHOD_ALIAS[value]
            return MethodologyFixRecord(
                case_id="", field=field, original=value, fixed=fixed,
                risk="low", action="auto_fix",
                note=f"test_method 别名 {value} → {fixed}，已就近映射",
            )
        if upper in PROMPT_EXTENDED_METHODS:
            return MethodologyFixRecord(
                case_id="", field=field, original=value, fixed=upper,
                risk="low", action="auto_fix",
                note=(
                    f"test_method={value} 为提示词声明扩展编码（非核心白名单 "
                    f"{sorted(self.method_whitelist)}），保留原值并记录"
                ),
            )
        if upper in UNDECIDED_METHOD_TO_FD:
            return MethodologyFixRecord(
                case_id="", field=field, original=value, fixed="FD",
                risk="low", action="auto_fix",
                note=(
                    f"test_method={value} 为未决定标记（非真实方法论编码），"
                    f"按契约归一为 FD（场景法）"
                ),
            )
        return MethodologyFixRecord(
            case_id="", field=field, original=value, fixed="",
            risk="high", action="blocked",
            note=f"白名单外 test_method={value}（合法: "
            f"{sorted(self.method_whitelist)} 或扩展编码 {sorted(PROMPT_EXTENDED_METHODS)}），"
            f"高危阻断",
        )

    def _check_quota(self, cases: list[dict[str, Any]]) -> dict[str, Any]:
        """校验 4 维配额（按修正后的 scenario_type 统计）。"""
        total = len(cases or [])
        gaps: dict[str, Any] = {}
        if total == 0:
            for st in sorted(self.quota):
                lo, hi = self.quota[st]
                gaps[st] = {
                    "actual": 0,
                    "min": lo,
                    "max": hi,
                    "ok": False,
                    "gap": f"缺 {st} 场景（应占 {lo:.0%}-{hi:.0%}）",
                }
            return gaps

        counter: dict[str, int] = {}
        for case in cases or []:
            st = str(case.get("scenario_type") or "").strip().lower()
            counter[st] = counter.get(st, 0) + 1

        for st, (lo, hi) in sorted(self.quota.items()):
            actual = counter.get(st, 0)
            rate = actual / total
            ok = lo <= rate <= hi
            gap_msg = ""
            if not ok:
                if rate < lo:
                    gap_msg = f"缺 {st} 场景（实际 {actual} 条 {rate:.1%} < 下限 {lo:.0%}）"
                else:
                    gap_msg = f"{st} 场景超配额（实际 {actual} 条 {rate:.1%} > 上限 {hi:.0%}）"
            gaps[st] = {
                "actual": actual,
                "rate": round(rate, 4),
                "min": lo,
                "max": hi,
                "ok": ok,
                "gap": gap_msg,
            }
        return gaps

    @staticmethod
    def _append_note(existing: str, note: str) -> str:
        """拼接 fix_note（多个字段修正时用分号连接）。"""
        if not note:
            return existing
        return f"{existing}; {note}".strip("; ") if existing else note

    @staticmethod
    def apply_block_filter(
        all_intents: list[dict[str, Any]],
        validation_result: dict[str, Any],
        mode: str,
    ) -> tuple[list[dict[str, Any]], list[str]]:
        """按 STEP3_METHOD_BLOCK 策略处理被高危阻断的用例。

        - mode != "block"（默认 tag/软打标）→ 原样返回，不剔除。
        - mode == "block"（硬门禁）→ 从可执行集剔除白名单外的用例，
          返回 (保留列表, 被剔除的 case_id 列表)；被剔除用例打
          method_valid=0 + gen_status=method_blocked。

        Returns:
            (kept_intents, dropped_case_ids)
        """
        validation_result = dict(validation_result or {})
        if str(mode or "tag").strip() != "block":
            return list(all_intents or []), []

        blocked_ids = set(
            validation_result.get("dispositions", {}).get("blocked", [])
        )
        if not blocked_ids:
            return list(all_intents or []), []

        kept: list[dict[str, Any]] = []
        dropped: list[str] = []
        for intent in all_intents or []:
            cid = str(intent.get("case_id") or intent.get("title") or "")
            if cid in blocked_ids:
                intent["method_valid"] = 0
                intent["gen_status"] = "method_blocked"
                dropped.append(cid)
                continue
            kept.append(intent)
        return kept, dropped

    @staticmethod
    def merge_into_quality_summary(
        quality: dict[str, Any], validation: dict[str, Any]
    ) -> dict[str, Any]:
        """把校验结果并入质量摘要（只增键不删键，对齐 design §7.6）。"""
        quality = dict(quality or {})
        quality["methodology_validation"] = {
            "validated": validation.get("validated", 0),
            "valid": validation.get("valid", 0),
            "auto_fixed": validation.get("auto_fixed", 0),
            "blocked_count": validation.get("blocked_count", 0),
            "blocked": validation.get("blocked", False),
            "quota_gaps": validation.get("quota_gaps", {}),
            "dispositions": validation.get("dispositions", {}),
            "results": validation.get("results", []),
        }
        return quality
