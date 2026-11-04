# -*- coding: utf-8 -*-
"""常驻「指令契约」门禁：生成端 ↔ 执行端必须共用同一事实源。

背景（2026-09-27 根因修复，该类缺陷在本仓库已复发 ≥5 次：
no_error / loading_complete / list_order / db_table_accessed_check / count_list_rows）：

  五步流水线的稳定性前提是「生成端只能发射执行端能执行的指令」。历史上
  生成端与执行端各持一份词表且无强制约束，于是反复出现三类失真：
    1) 生成端发执行端不认的 check 类型 → "未知检查类型" 假 FAIL；
    2) 生成端发执行端无消费分支的 action（如 count_list_rows）→ 静默略过，
       依赖它的深断言（list_row_count_check 基线）结构性死亡；
    3) 结果语义各消费点自造 —— skipped/UNVERIFIED 被渲染/聚合为 PASS（假 PASS），
       unverified 被判 PRODUCT_BUG（假 Bug）。

本门禁把「这一整类」钉死，而不是逐个类型打补丁：
  A. 结果语义唯一权威（render_check_tag / aggregate_check_results）真值表；
  B. 生成端唯一咽喉点（_check_guard.sanitize_intents）的契约行为：
     契约外 action 剔除、结构性不可判定断言剔除、断言与操作语义匹配；
  C. 传输层无损透传（verification_plan_builder._preserve_registered_check）。
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.pipeline.steps.step4.verification._track_protocol import (  # noqa: E402
    aggregate_check_results,
    is_verdict,
    render_check_tag,
)


# ── A. 结果语义唯一权威 ──
def test_render_check_tag_truth_table():
    # 真判定
    assert render_check_tag({"passed": True}) == "[PASS]"
    assert render_check_tag({"passed": False}) == "[FAIL]"
    # 诚实跳过：passed=True 但 skipped —— 绝不渲染为 PASS（历史假 PASS 根因）
    assert render_check_tag({"passed": True, "skipped": True}) == "[SKIP]"
    assert (
        render_check_tag({"passed": False, "status": "UNVERIFIED"}) == "[SKIP]"
    )
    assert render_check_tag({"passed": True, "status": "NO_DATA"}) == "[SKIP]"
    # 引擎/环境侧：既非通过也非产品缺陷
    assert (
        render_check_tag({"passed": False, "reason": "UNSUPPORTED_CHECK"}) == "[ERROR]"
    )
    assert render_check_tag({"passed": False, "status": "ERROR"}) == "[ERROR]"


def test_aggregate_check_results_is_three_valued():
    # 有真实通过且无失败 → 通过
    _a = aggregate_check_results(
        [{"passed": True}, {"passed": True, "skipped": True}]
    )
    assert _a["passed"] is True and _a["has_positive"] is True

    # 有真实失败 → 不通过（产品行为不满足）
    _b = aggregate_check_results([{"passed": True}, {"passed": False}])
    assert _b["passed"] is False and _b["has_negative"] is True

    # 全为非判定态 → 不通过、且 no_verdict_only（下游据此判 SKIP，不假 PASS 不假 FAIL）
    _c = aggregate_check_results(
        [{"passed": True, "skipped": True}, {"passed": False, "status": "UNVERIFIED"}]
    )
    assert _c["passed"] is False
    assert _c["no_verdict_only"] is True
    assert _c["has_positive"] is False and _c["has_negative"] is False


# ── B. 生成端唯一咽喉点：指令契约 ──
def _readonly_case() -> dict:
    """检索型用例：动作是「填查询框 + 点查询」，语义为只读。"""
    return {
        "case_id": "TC-REQ-001-B01",
        "title": "检索资产-边界值",
        "operation": "检索资产",
        "actions": [
            {"type": "count_list_rows", "selector": "tbody tr"},
            {"type": "navigate"},
            {"type": "fill", "target_hint": "查询输入框"},
            {"type": "click", "target_hint": "查询"},
        ],
        "checks": [
            {"track": "ui", "check": {"type": "field_error_visible_check"}},
            {
                "track": "ui",
                "check": {
                    "type": "list_row_count_check",
                    "expected_delta": 0,
                    "baseline_action_index": 1,
                },
            },
            {"track": "ui", "check": {"type": "text_contains_check", "expected": "资产"}},
        ],
    }


def _mutation_case() -> dict:
    """变更型用例：新增资产（提交型），才允许字段级错误提示断言。"""
    return {
        "case_id": "TC-REQ-002-B01",
        "title": "新增资产-非法输入应提示字段错误",
        "operation": "新增资产",
        "expected_feedback": "系统返回字段级错误提示，且不写库",
        "actions": [
            {"type": "navigate"},
            {"type": "fill", "target_hint": "名称"},
            {"type": "click", "target_hint": "提交"},
        ],
        "checks": [
            {"track": "ui", "check": {"type": "field_error_visible_check"}},
        ],
    }


def test_generation_chokepoint_enforces_contract():
    from src.pipeline.step3._check_guard import sanitize_intents

    _cases = [_readonly_case(), _mutation_case()]
    sanitize_intents(_cases)

    # 1) 契约外 action（count_list_rows）必须在生成端剔除（执行端无消费分支）
    _acts = [a.get("type") for a in _cases[0]["actions"]]
    assert "count_list_rows" not in _acts, _acts
    assert {"navigate", "fill", "click"}.issubset(set(_acts)), _acts

    # 2) 检索型用例：字段级错误提示 + 行数增量断言均为类别错误 → 剔除
    _types0 = [
        (c.get("check") or {}).get("type") for c in _cases[0]["checks"]
    ]
    assert "field_error_visible_check" not in _types0, _types0
    assert "list_row_count_check" not in _types0, _types0
    # 语义期望被改写为 semantic_check（机械文本→AI 判定的既有契约）
    assert "semantic_check" in _types0, _types0

    # 3) 变更型用例：字段级错误提示保留（语义匹配成立）
    _types1 = [
        (c.get("check") or {}).get("type") for c in _cases[1]["checks"]
    ]
    assert "field_error_visible_check" in _types1, _types1

    # 4) 幂等：重复定稿不得让已剔除的断言复活或叠加新断言
    _n0 = len(_cases[0]["checks"])
    sanitize_intents(_cases)
    assert len(_cases[0]["checks"]) == _n0, _cases[0]["checks"]


# ── C. 传输层无损透传 ──
def test_plan_builder_preserves_registered_db_check():
    from src.pipeline.steps.step4.verification_plan_builder import (
        _preserve_registered_check,
    )

    _typed = _preserve_registered_check(
        {"type": "db_table_accessed_check", "expected": True}, "db", "探库", "medium"
    )
    assert _typed is not None
    assert _typed["type"] == "db_table_accessed_check"
    assert _typed["check"]["type"] == "db_table_accessed_check"

    # 未注册类型不伪装成已注册（交回既有兜底路径）
    assert (
        _preserve_registered_check({"type": "not_a_real_check"}, "db", "x", "low")
        is None
    )
    # 无类型的传统 {table, where} 形态不走透传
    assert _preserve_registered_check({"table": "assets"}, "db", "x", "low") is None


# ── D. 非指令 check（无 type）：剔除 + 轨道主张保留 ──
def test_untyped_check_rejected_but_track_claim_retained():
    """``{"check": "<自然语言>"}`` 形态不是可执行指令，必须在生成端剔除。

    根因（2026-09-27 实测存量库 20 条）：规则层占位发射的字符串 check
    （_combo_enhancer 提交/删除占位、_modeling_intents 跨需求兜底）在 Step4
    无分派入口（resolve_handler → UNSUPPORTED_CHECK）→ 旧聚合把 skipped/passed=True
    渲染成假 PASS。同时它还会"假装覆盖 db 轨"，抑制 plan builder 的 RAG/Schema 兜底。
    契约：生成端剔除该非指令，但保留**需求对该维度的主张**，交由执行层兜底产出
    真实可判定断言（verification_plan_builder._build_db_checks）。
    """
    from src.pipeline.step3._check_guard import sanitize_intents

    _case = {
        "case_id": "TC-REQ-030-B01",
        "title": "删除资产-边界值",
        "operation": "删除资产",
        "actions": [
            {"type": "navigate"},
            {"type": "click", "target_hint": "删除"},
        ],
        "required_tracks": ["ui", "db"],
        "checks": [
            {"track": "ui", "check": {"type": "element_exists", "selector": "#list"}},
            {
                "track": "db",
                "precision": "fuzzy",
                "check": "删除操作后 DB 记录数应减少",
                "expect": "记录数减少",
            },
        ],
    }
    sanitize_intents([_case])

    _types = [
        c["check"]["type"] for c in _case["checks"] if isinstance(c.get("check"), dict)
    ]
    assert _types == ["element_exists"], _types
    # 维度主张保留（否则 db 维度被静默丢失，RAG 兜底无从接手）
    assert "db" in _case["required_tracks"], _case["required_tracks"]
    assert "ui" in _case["required_tracks"], _case["required_tracks"]


# ── E. 落库边界强制：契约必须在唯一持久化咽喉点生效 ──
def test_contract_enforced_at_persistence_boundary():
    """指令契约必须落在 ``test_cases`` 的**唯一持久化咽喉点**上。

    根因（2026-09-27 通用化）：守卫原先只在 Step3 建模出口调用，属"某个调用点"，
    不是落库边界——自愈修复（consistency_helpers.repair 的 Excel→MySQL 回填）、
    外部导入、重试脚本等**任何其它生产者**直调 ``case_store.upsert_intents`` 即可
    绕过守卫，把执行端无法分派的指令（无 type 的 check / 契约外 action）写进库，
    落库后 Step4 必判 UNSUPPORTED_CHECK 或静默略过 → 假 FAIL / 假 PASS 复发。
    契约必须在边界强制，新增生产者才自动受约束（无需逐处补丁）。

    采用静态源检查：该边界是 DB 写入代码，行为级回归需真实 MySQL；此处锁定
    "调用存在且位于 upsert 写入逻辑内"这一结构不变量，防止被后续重构静默移除。
    """
    import os

    _p = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "src", "pipeline", "utils", "case_store", "crud", "write.py",
    )
    with open(_p, "r", encoding="utf-8") as f:
        _src = f.read()

    _idx_upsert = _src.find("def upsert_intents")
    assert _idx_upsert != -1, "未找到 upsert_intents 定义（落库边界已变更？）"
    # 取 upsert_intents 函数体范围（到下一个顶层 def / EOF）
    _body = _src[_idx_upsert:]
    _nxt = _body.find("\n    def ", 10)
    if _nxt != -1:
        _body = _body[:_nxt]

    assert "sanitize_case_checks" in _body, (
        "落库边界未强制指令契约：upsert_intents 必须调用 "
        "_check_guard.sanitize_case_checks，否则任何生产者都能写入不可执行指令"
    )
    # ★ §4.17-B(2026-10-28): 导航目标解析同属"不可伪造的事实"，必须在同一咽喉点强制。
    #   否则导入/自愈回填/重试脚本等生产者可写入占位导航（无 url 且 target="/"），
    #   评审页与执行端只能把它渲染/执行成"导航到 /"。
    assert "resolve_intents_batch" in _body, (
        "落库边界未强制导航目标解析：upsert_intents 必须调用 "
        "_navigation_resolver.resolve_intents_batch，否则占位导航可绕过解析直接落库"
    )


# ── F. 环境探针不是判定（修道假 PASS 与假 Bug）──
def test_probe_check_is_not_a_verdict():
    """探针（db_table_accessed_check）的结果**不构成判定**。

    根因（2026-09-27 实测 9 条 conf≈0.98 无证据假绿）：探针语义只是"目标库可访问 +
    有业务表可读"，其通过只证明**环境就绪**，与需求是否被满足无关。生成端曾用它
    "保 db 轨道"，于是用例唯一断言=探针 → 库能连上即恒 PASS（expected/actual/details
    全空）。反之探针失败（库不可达）是环境问题，也不得构成用例 FAIL（假 Bug）。
    纪律：探针通过/失败一律落**非判定态**（[SKIP]），由调用方按"无可判定断言"处理。
    """
    _probe_pass = {"type": "db_table_accessed_check", "passed": True}
    _probe_fail = {"type": "db_table_accessed_check", "passed": False}
    _probe_alias = {"type": "db_table_accessed", "passed": True}

    assert is_verdict(_probe_pass) is False
    assert is_verdict(_probe_fail) is False
    assert is_verdict(_probe_alias) is False
    assert render_check_tag(_probe_pass) == "[SKIP]"
    assert render_check_tag(_probe_fail) == "[SKIP]"
    assert render_check_tag(_probe_alias) == "[SKIP]"
    # 对照：真实断言仍正常构成判定
    assert is_verdict({"type": "text_contains", "passed": True}) is True
    assert render_check_tag({"type": "text_contains", "passed": True}) == "[PASS]"


# ── G. 极性反转：no_error 与"期望报错"矛盾时**反转**而非删除 ──
def test_polarity_conflict_is_inverted_not_dropped():
    """需求期望本身即错误提示时，no_error（"系统无报错"）必须极性反转，不得删除。

    根因（2026-09-27）：原实现直接**剔除**矛盾的 no_error —— 剔除本身避免了假 FAIL，
    但同时**丢失了负向需求的判定能力**；一旦生成端又为"保轨道"注入环境探针，
    用例就只剩探针 → 恒 PASS 假绿。正确处置是**反转断言极性**（改判"应出现错误提示"），
    使负向需求仍可被真实证伪。反转结果再按「运行期零 AI」契约对齐为
    `field_error_visible_check`（同一极性判定的确定性 DOM 取证）。
    """
    from src.pipeline.step3._check_guard import sanitize_intents

    _case = {
        "case_id": "TC-REQ-002-E01",
        "title": "新增资产-非法输入应被拒绝",
        "operation": "新增资产",
        "expected_feedback": "系统返回错误提示，且不写库",
        "actions": [{"type": "navigate"}, {"type": "click", "target_hint": "提交"}],
        "required_tracks": ["ui"],
        "checks": [
            {
                "track": "ui",
                "precision": "fuzzy",
                "check": {"type": "no_error", "selector": "", "expected": "无错误"},
                "expect": "系统无报错",
            }
        ],
    }
    sanitize_intents([_case])

    # 断言未被剔除（保住负向判定能力），而是被反转
    assert len(_case["checks"]) == 1, _case["checks"]
    _c0 = _case["checks"][0]
    assert _c0["_polarity_flipped"] == "no_error", _c0
    # 期望文本取自需求自身的 expected_feedback（而非原 no_error 的"无错误"）
    assert "错误提示" in _c0["check"]["expected"], _c0["check"]["expected"]
    # 反转后的语义断言再由「运行期零 AI」契约对齐为确定性 DOM 取证（非删除、非降级）
    assert _c0["check"]["type"] == "field_error_visible_check", _c0
    assert _c0.get("_aligned_from") == "semantic_check", _c0
    assert is_verdict({"type": "field_error_visible_check", "passed": True}) is True


# ── H. 生成端不得再发射"环境探针"来顶替需求断言 ──
def test_no_environment_probe_emitted_by_generator():
    """生成端不得再用环境探针"保轨道"（词表/探针漂移只准补执行器或剔除）。

    根因：探针占住轨道坑位会令 `verification_plan_builder` 认为该轨已覆盖，
    从而**抑制 RAG/Schema 兜底**产出真实断言（劣币驱逐良币），并制造无证据假绿。
    """
    from src.pipeline.step3._fallback_cases import (
        _gen_api_probe_check,
        _gen_db_probe_check,
    )

    assert _gen_api_probe_check() is None
    assert _gen_db_probe_check() is None


# ── I. 引擎自撰模板文案不得参与需求极性推断 ──
def test_engine_boilerplate_excluded_from_polarity():
    """探针用例 description 尾部的**引擎模板**不得影响极性判定。

    根因（2026-09-27 实测）：模板枚举"…（表单出现/校验提示/列表状态变化）"，
    其中"校验提示"命中 _REJECT_KEYWORDS → **所有探针用例**被判为"期望错误反馈"，
    连正向需求（TC-REQ-010-E01「资产列表需完整呈现核心业务字段」，期望反馈=
    "列表展示 ID、名称…"）也被误反转/误剔除。判据：剥离引擎模板后，正向需求
    不得判为 expects_error；真实负向需求（期望反馈即错误提示）仍应判 True。
    """
    from src.pipeline.step3._rule_extractor_keywords import (
        expects_error_feedback,
        strip_engine_template,
    )
    from src.pipeline.step3._check_guard import sanitize_intents

    _boiler = (
        "对资产列表需完整呈现核心业务字段及操作入口做真实功能探针：进入模块页"
        "→探索可交互控件→执行良性交互→断言具体业务信号（表单出现/校验提示/列表状态变化）"
    )
    # 剥离后只剩需求派生文本 → 模板词不再污染极性判据
    assert "校验提示" not in strip_engine_template(_boiler)
    assert expects_error_feedback(_boiler) is False
    # 对照：需求自身期望即错误提示时仍判 True（剥离不削弱真实负向语义）
    assert expects_error_feedback("提示“用户名或密码错误”，不区分具体错误项") is True

    _positive_case = {
        "case_id": "TC-REQ-010-E01",
        "title": "资产列表-功能探针验证（探索）",
        "description": _boiler,
        "expected_result": "系统无报错（Agent 引导断言承担语义验证）",
        "actions": [{"type": "navigate"}],
        "required_tracks": ["ui"],
        "checks": [
            {
                "track": "ui",
                "precision": "exact",
                "check": {"type": "no_error", "selector": "", "expected": "系统无报错"},
                "expect": "系统无报错",
            }
        ],
    }
    sanitize_intents([_positive_case])
    _c0 = _positive_case["checks"][0]
    # 正向用例的 no_error **不得**被误反转（模板词污染已消除）
    assert _c0["check"]["type"] == "no_error", _c0
    assert not _c0.get("_polarity_flipped"), _c0


# ── J. 断言叙述不得反向定义操作类型（写入型/只读型判定收敛到权威字段）──
def test_assertion_narration_does_not_define_operation_type():
    """写入型用例的**断言叙述**里出现"列表"不得让它被判为只读型。

    根因（2026-09-27 实测 TC-REQ-002-B01 / P01）：判据原先把 title/description/
    expected_* 一并扫描，于是「断言字段级校验提示可见且**列表**行数不变」
    中的"列表"命中 `_READONLY_OP_KEYWORDS` → 用例被判**只读型** →
    其唯一的 `field_error_visible_check` 被语义过滤剔除 → 用例退化为仅剩
    url_matches → Step4 无判定能力 → ERROR。修复：判定收敛到
    `operation` + `actions[].type`（权威操作信号），断言叙述不得反向定义操作类型；
    仅当权威字段无任何写入/只读信号时才回落完整文本（兼容历史数据）。
    """
    from src.pipeline.step3._check_guard import (
        _is_mutation_case,
        _is_readonly_case,
        sanitize_intents,
    )

    _write_case = {
        "case_id": "TC-REQ-002-B01",
        "title": "新增资产-输入边界值与异常数据",
        "operation": "新增资产",
        "description": "输入边界值与异常数据，断言字段级校验提示可见且列表行数不变",
        "expected_result": "对应字段级错误提示可见且文本非空",
        "expected_feedback": "系统返回字段级错误提示",
        "actions": [
            {"type": "navigate"},
            {"type": "fill", "value": "", "target_hint": "名称输入框"},
            {"type": "click", "target_hint": "保存,提交,确定"},
        ],
        "required_tracks": ["ui"],
        "checks": [
            {"track": "ui", "check": {"type": "field_error_visible_check"}},
        ],
    }
    # 权威信号判定：操作=新增资产 → 写入型，且**不是**只读型
    assert _is_mutation_case(_write_case) is True
    assert _is_readonly_case(_write_case) is False
    # 断言得以保留（不再因"列表"被误剔）
    sanitize_intents([_write_case])
    _types = [
        (c.get("check") or {}).get("type") for c in _write_case["checks"]
    ]
    assert "field_error_visible_check" in _types, _types

    # 对照：真正的检索型用例仍应保留"只读"判定（收敛不削弱反向识别）
    _read_case = {
        "case_id": "TC-REQ-001-B01",
        "operation": "检索资产",
        "description": "在列表中按名称检索资产",
        "actions": [
            {"type": "navigate"},
            {"type": "fill", "target_hint": "查询输入框"},
            {"type": "click", "target_hint": "查询"},
        ],
        "checks": [{"track": "ui", "check": {"type": "field_error_visible_check"}}],
    }
    assert _is_readonly_case(_read_case) is True
    assert _is_mutation_case(_read_case) is False


# ── K. 变更型操作动作序：单一实现（SSOT），不得双口径漂移 ──
def test_mutation_flow_orders_trigger_before_entry():
    """变更型操作必须产出「开窗 → 录入 → 提交」，且该序对全部调用方一致。

    根因（2026-09-27 实测 4 条 ERROR，最大簇）：同一操作语义（新增/编辑）在生成侧
    存在**两套动作编排实现**——主规则路径 `_rule_extractor_actions._build_actions`
    与兜底路径 `_fallback_cases._build_base_actions` 产出
    ``fill(先) → click(触发按钮)``（弹窗尚未打开就往表单里填），而 `_fallback_cases`
    的探针分支产出正确的 ``click(触发) → fill → click(确认)``。
    后者 PASS、前者 ERROR —— 同一系统、同一操作语义，两条实现口径不一致即根因。
    现收敛到 `_rule_extractor_buttons.build_interaction_actions` 单一实现，本测试
    锁定该序形态不因"用例类型不同"而变化（形态只由操作语义 + 是否存在录入决定）。
    """
    from src.pipeline.step3._rule_extractor_buttons import (
        build_interaction_actions,
        mutation_flow_kind,
    )

    def _shape(_acts):
        return [
            (a.get("type"), bool(a.get("in_dialog"))) for a in _acts
        ]

    _data = {"type": "fill", "target_hint": "名称输入框", "value": "自动化值"}

    # 1) 变更型：开窗(非弹窗) → 录入(弹窗内) → 提交(弹窗内)
    _expect = [("click", False), ("fill", True), ("click", True)]
    for _op in ("新增资产", "编辑资产"):
        _acts = build_interaction_actions(_op, _data, _op, "保存")
        assert _shape(_acts) == _expect, (_op, _acts)
    # 导入型：录入载体是上传控件，序形态与录入型一致（录制口不同、次序同一）
    _imp = build_interaction_actions(
        "导入资产台账", {"type": "upload_file", "file": "a.csv"}, "导入", "确定"
    )
    assert _shape(_imp) == [("click", False), ("upload_file", True), ("click", True)], _imp

    # 2) 形态与确认控件文本无关（同一操作 → 同一序；确认文本仅影响目标文本）
    _a = build_interaction_actions("新增资产", _data, "新增资产", "")
    _b = build_interaction_actions("新增资产", _data, "新增资产", "保存")
    assert _shape(_a) == _shape(_b) == _expect
    # 无确认信号时仍给出通用确认语义（Step4 支持逗号候选），不得退化为少一步
    assert _a[-1]["type"] == "click" and _a[-1]["target_hint"]

    # 3) 只读检索型：先录入再触发，绝不先点开窗
    _q = build_interaction_actions("检索资产", _data, "查询", "")
    assert [a.get("type") for a in _q] == ["fill", "click"], _q

    # 4) 纯录入描述词不得被判为"触发型"（否则"输入超长名称"会凭空点触发控件）
    assert mutation_flow_kind("输入超长资产名称", True) == "inline_form"
    assert mutation_flow_kind("导入资产台账", True) == "dialog_form"
    assert mutation_flow_kind("删除资产", False) == "trigger_confirm"


def test_both_generators_share_the_same_mutation_flow():
    """两条生成路径都必须调用同一 SSOT（静态锁定，防再次分叉）。

    这是"消除补丁"的结构性保障：一旦有人在任一调用点重建局部关键词表/动作序，
    门禁即失败——不再依赖人肉审查。
    """
    import os

    _root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    _files = {
        "主规则路径": os.path.join(
            _root, "src", "pipeline", "step3", "_rule_extractor_actions.py"
        ),
        "兜底路径": os.path.join(
            _root, "src", "pipeline", "step3", "_fallback_cases.py"
        ),
    }
    for _name, _p in _files.items():
        with open(_p, "r", encoding="utf-8") as f:
            _src = f.read()
        assert "build_interaction_actions" in _src, (
            f"{_name}未使用动作编排 SSOT（build_interaction_actions），动作序已分叉"
        )
    # 局部关键词表必须已删除（历史漂移实证：漏 "新增/添加/创建/编辑/更新"）
    with open(_files["主规则路径"], "r", encoding="utf-8") as f:
        _src_actions = f.read()
    assert "_is_dialog_op =" not in _src_actions, (
        "主规则路径仍在自行维护触发型关键词表（_is_dialog_op），属双口径漂移"
    )


# ── L. auth 类用例的导航目标必须可执行（禁发空导航指令）──
def test_auth_navigation_target_is_executable():
    """登录/认证类用例的导航目标**绝不为空**。

    根因（2026-09-27 实测 TC-REQ-005-E01/I01）：认证类需求因"无对应菜单"被清空
    导航目标（``url=""``）→ 生成端发射**不可执行指令** → Step4 结构化执行判
    "URL 解析为空" → 降级 AI 自由探索 8 轮不收敛 → HARNESS_ERROR。
    "无对应菜单"≠"无需导航"：登录入口本身是真实页面（config.target_login_path
    → 靶子注册表 auth.login_url → "/" 兜底）。
    """
    from src.pipeline.step3._navigation_resolver import _login_entry_url

    assert str(_login_entry_url()).strip() != ""
    assert _login_entry_url().startswith("/") or "://" in _login_entry_url()


def test_resolver_never_emits_empty_navigate():
    """静态锁定：解析器源码不得再出现空导航目标（``"url": ""``）。"""
    import os

    _p = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "src", "pipeline", "step3", "_navigation_resolver.py",
    )
    with open(_p, "r", encoding="utf-8") as f:
        _src = f.read()

    assert '"url": ""' not in _src, (
        "导航解析器重新发射了空导航目标——不可执行指令必然导致 Step4 执行失败"
    )
    assert "_login_entry_url()" in _src, "认证类导航未走统一登录入口解析"
    # 认证分支的动作/探索任务两个调用点都必须使用它（避免只修一处）
    assert _src.count("_login_entry_url()") >= 2, (
        "登录入口解析未在两个调用点（actions 覆写 / _explore_task 修正）同时生效"
    )