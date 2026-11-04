# -*- coding: utf-8 -*-
"""方法论全链路集成测试 — 用例生成真实用上 18 码。

背景(根因 bug，2026-09-21)：制造商只把 methodology 用于"选模板"，落库元数据恒为 FD；
校验层白名单仅 {EP,BV,EG,FD}+旧扩展8码=12，新 6 码(AU/CS/SS/CT/PF/ET)被高危阻断。
本次根因修复后，两条链路均归一：
  1) canonical_method_code：FD/未决定 → 按 SDET 直觉升级；显式码保留；dict 形状归一。
  2) MethodologyValidator.TEST_METHOD_WHITELIST 从 catalog.py 派生出权威 18 码。
运行：python -m pytest tests/test_step3/test_methodology_full_flow.py -v
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("SOURCEDIR", ".")

from src.testing_methods.catalog import METHOD_CODES  # noqa: E402
from src.pipeline.steps.step3.case_generator._test_method_rules import (  # noqa: E402
    canonical_method_code,
    normalize_method_code,
)
from src.pipeline.step3._methodology_validator import (  # noqa: E402
    MethodologyValidator,
    TEST_METHOD_WHITELIST,
)

VALIDATOR = MethodologyValidator()


# ── 1. 权威目录与白名单收敛 ─────────────────────────────
def test_whitelist_is_full_18_catalog():
    assert set(TEST_METHOD_WHITELIST) == set(METHOD_CODES)
    assert len(TEST_METHOD_WHITELIST) == 18
    assert {"AU", "CS", "SS", "CT", "PF", "ET"} <= set(TEST_METHOD_WHITELIST)


# ── 2. normalize 支持两种形状 ───────────────────────────
def test_normalize_dict_and_string_shapes():
    assert normalize_method_code({"method": "AU"}) == "AU"
    assert normalize_method_code({"code": "SX"}) == "SX"
    assert normalize_method_code("CS") == "CS"
    assert normalize_method_code("") == "FD"
    assert normalize_method_code({}) == "FD"


# ── 3. canonical 语义：显式保留 / FD 直觉升级 / 兜底 ─────
def test_canonical_preserves_explicit_and_upgrades_fd():
    assert canonical_method_code("SX") == "SX"
    assert canonical_method_code("SI") == "SI"
    assert canonical_method_code({"method": "CS"}) == "CS"

    def _et(op, module="", exp=""):
        return {"operation": op, "module": module, "expected_feedback": exp}

    assert canonical_method_code("FD", "", _et("校验越权访问", "权限", "无权访问被拦截")) == "AU"
    assert canonical_method_code("FD", "", _et("用户登录", "会话", "登录成功")) == "SS"
    # "数量校验"属边界值判定（区间边界），按 SDET 直觉应路由为 BV
    assert canonical_method_code("FD", "", _et("新增商品", "商品", "数量校验", )) == "BV"
    assert canonical_method_code("", "", _et("发起CSRF攻击")) in ("CS", "FD")


# ── 3b. case_signal：用例自身语义（标题/类型）驱动确定性落码 ──
def test_canonical_case_signal_prevents_fd_collapse():
    """根因(2026-09-21)：性能/兼容/安全类型用例的 explore_task 无关键词，此前坍缩 FD；
    现在把标题/类型作为 case_signal 并入信号文本，按自身语义确定性落码。"""
    # 例：fallback 生成的 CP01/PF01/SEC01 样式标题
    assert canonical_method_code("FD", "", None, "数据模块-REQ-001-兼容-小分辨率可渲染") == "CT"
    assert canonical_method_code("FD", "", None, "数据模块-REQ-001-性能-页面加载≤3s") == "PF"
    assert canonical_method_code("FD", "", None, "数据模块-REQ-001-安全-XSS/注入payload不执行") == "SX"
    assert canonical_method_code("FD", "", None, "数据模块-REQ-001-安全-SQL注入恶意请求") == "SI"
    assert canonical_method_code("FD", "", None, "数据模块-REQ-001-越权访问拦截") == "AU"
    # 显式码优先：case_signal 不覆盖显式决策
    assert canonical_method_code("EP", "", None, "数据模块-REQ-001-性能-页面加载≤3s") == "EP"


# ── 4. 校验器接受全部 18 码（0 阻断）────────────────────
def test_validator_accepts_all_18_codes():
    cases = []
    for i, code in enumerate(METHOD_CODES):
        cases.append({
            "case_id": f"c{i}", "scenario_type": "happy_path",
            "test_method": code,
        })
    r = VALIDATOR.validate(cases)
    assert r["blocked_count"] == 0, f"blocked: {r.get('dispositions', {}).get('blocked')}"
    # 每个码被保持（keep）
    fixed_test_methods = [c["test_method"] for c in cases]
    assert set(fixed_test_methods) == set(METHOD_CODES)


# ── 5. 别名就近映射到新码 ──────────────────────────────
def test_validator_alias_maps_chinese_and_new_codes():
    for alias, expect in [
        ("越权", "AU"), ("会话管理", "SS"), ("会话", "SS"),
        ("性能", "PF"), ("兼容", "CT"), ("探索式", "ET"),
        ("越权与权限", "AU"), ("CSRF跨站请求伪造", "CS"),
    ]:
        c = {"case_id": "x", "scenario_type": "happy_path", "test_method": alias}
        VALIDATOR.validate([c])
        assert c["test_method"] == expect, f"{alias} -> {c['test_method']}, want {expect}"


# ── 6. 真实规则兜底批量经 canonical+validate 后多方法分布 ──
def test_real_fallback_batch_spreads_methods():
    """用真实规则兜底生成器(_build_rule_fallback_cases)产出用例，
    再经 canonical_method_code + MethodologyValidator 全链路，断言：
      - 0 阻断
      - 方法论码跨度 >= 3（不再全部坍缩为 FD）
    """
    from src.pipeline.step3._fallback_cases import _build_rule_fallback_cases

    reqs = [
        {"req_id": "R1", "title": "用户登录", "description": "输入用户名密码登录系统，会话保持", "operation": "登录"},
        {"req_id": "R2", "title": "商品搜索", "description": "支持按关键词和价格区间搜索商品", "operation": "搜索"},
        {"req_id": "R3", "title": "权限控制", "description": "不同角色权限不同，越权访问被拒绝", "operation": "权限校验"},
        {"req_id": "R4", "title": "新增商品库存", "description": "填写库存数量与价格，校验数量范围", "operation": "新增"},
    ]
    all_intents = []
    for req in reqs:
        all_intents.extend(_build_rule_fallback_cases(req, "测试保底层") or [])

    assert len(all_intents) > 0, "规则兜底应产出用例"

    # 复刻写库前统一解码（_modeling_engine 新增的逻辑路径）
    for ic in all_intents:
        if not isinstance(ic, dict):
            continue
        ic["test_method"] = canonical_method_code(
            ic.get("test_method"),
            str(ic.get("control_type") or "").strip(),
            ic.get("_explore_task"),
        )

    v = VALIDATOR.validate(all_intents)
    assert v["blocked_count"] == 0, f"blocked: {v.get('dispositions', {}).get('blocked')}"
    methods = {c.get("test_method") for c in all_intents}
    assert not methods <= {"FD"}, f"方法仍全部坍缩为 FD? methods={methods}"
    assert len(methods) >= 3, f"方法跨度过小: methods={sorted(methods)}"
    for m in methods:
        assert m in set(METHOD_CODES), f"兜底方法码不在权威目录: {m}"