# -*- coding: utf-8 -*-
"""检查项定稿守卫（单一咽喉点， 全量判定审计后的根因修复）。

根因（ponytail 式五层 why）：
  L1 现象: 大面积假 FAIL——`field_error_visible_check` / `db_table_accessed_check`
     在执行证据中判 "未知检查类型（无有效断言，结论不可用）"。
  L2 直接: Step3 各生成器（case_rules / _fallback_cases / _intent_checks）发射的
     check 类型与 Step4 执行注册表（_track_protocol + _UI_CHECK_DISPATCH）是两套词表。
  L3 为何 fail-closed: 未知类型无法执行 → "无有效断言" → FAIL（诚实化设计正确）。
  L4 为何反复复发: 历史上 no_error/loading_complete/list_order 曾同样漂移并逐个"补齐"，
     每次只修当下发现的类型——发射端始终可以随意造新类型，漂移必然再发生。
  L5 根本: 生成与执行之间没有单一事实源约束。

根治 = 在用例定稿的唯一咽喉点（_modeling_intents 落库前）按执行注册表校验：
  1. 不可执行类型 → 剔除 + ERROR 告警（新增类型必须先注册执行器，否则生成端自动剔除，
     词表从此不可能漂移）；
  2. 极性过滤：field_error_visible_check 仅允许负向语义用例（正向用例无错误提示可断言，
     注入即假 FAIL）；
  3. 轨道诚实化：按剔除后剩余 checks 重算 required_tracks；
  4. ★ 断言手段对齐(2026-09-23)：语义型 text_contains（正向、非精确字段值）
     → 改写为 semantic_check，由 Step4 走「机械优先 + AI 语义兜底」判定。
     语义期望不再用字面 contains 实现，语义对齐边界由 AI 承担。
  5. ★ 极性反转(2026-09-27)：需求期望本身即错误/拒绝提示时，no_error（"系统无报错"）
     与之极性矛盾——**反转**为 semantic_check（期望取需求 expected_feedback），
     而非删除。删除会丢失负向需求的判定能力，并把用例退化为"仅剩环境探针"→假 PASS。

通用性: 跨系统/行业/页面，零业务硬编码——只依赖类型注册表与极性语义。
"""

from __future__ import annotations
from src.core.guarded import guard

import logging
import re
from typing import Any

from src.pipeline.step3._rule_extractor_keywords import (
    expects_accept_feedback,
    expects_confirm_dialog,
    expects_error_feedback,
    forbidden_states_in_texts,
    has_opposite_outcome_disjunction,
    has_tolerant_outcome_marker,
    is_disjunctive_text,
    is_tolerant_outcome,
    strip_engine_template,
    strip_inferred_feedback_clause,
)
# 反馈条选择器（成功/错误）的**唯一事实源**（Step3 生成/Step4 校验/Agent 探索共用）
from src.pipeline.utils.feedback_selectors import (
    CONFIRM_SELECTOR_CSV,
    ERROR_SELECTOR_CSV,
    SUCCESS_SELECTOR_CSV,
)
# 控件语义类别表的**唯一事实源**（动词→类别），复用而不另造关键词表
#   —— 第三份关键词表本身就是"漂移"的来源，禁止复制。
from src.pipeline.step3._rule_extractor_buttons import _VERB_CATEGORY_SORTED
# 「操作后果型」断言的前置触发序列（唯一事实源，与 _fallback_cases 共享）
from src.pipeline.step3._trigger_actions import (
    INTERACTION_SCOPE_OBSERVE_ONLY,
    OBSERVE_ONLY_ACTION_TYPES,
    POST_ACTION_CHECK_TYPES,
    _is_submit_action,
    action_sequence_triggers_outcome,
    align_delete_flow_shape,
    build_field_validation_trigger,
    check_requires_interaction,
    complete_submit_preconditions,
    ensure_form_entry,
    field_rejection_scope,
    row_count_delta_in_text,
)

_log = logging.getLogger(__name__)


def _build_executable_check_types() -> frozenset:
    """从 Step4 执行侧注册表收集全部可执行 check 类型（单一事实源）。

    ★ 根因修复(2026-09-26)：原实现自带两份硬编码词表（api/db 各一段），
      与真正的执行注册表脱钩 —— 实测 `db_table_accessed_check` 已在
      `DbTrackVerifier` 注册（db 轨专用探针），却因不在本函数硬编码的 db 集合
      中被本守卫漏放过、又被 Step4 Agent 路径的硬编码集合当作 UI 类型 →
      "未知检查类型"假 FAIL（实测 TC-REQ-004-P01）。
      修复：改为投影执行注册表本身（`registered_check_types()`），
      生成/执行两端共用同一事实源，新增类型只需注册一次。

    import 失败时 fail-open（返回空集=跳过校验），绝不阻断建模主流程；
    但会打 ERROR 日志提醒（注册表不可读本身就是环境异常）。
    """
    types: set[str] = set()
    with guard("_build_executable_check_types: from src.pipeline.steps.step4.verifi", log=_log, level=logging.ERROR):
        from src.pipeline.steps.step4.verification._track_protocol import (
            registered_check_types,
        )

        for _track_set in registered_check_types().values():
            types |= set(_track_set)
    return frozenset(types)


_EXECUTABLE_CHECK_TYPES = _build_executable_check_types()

# 无交互（不改变页面状态）的动作类型：唯一定义处为 `_trigger_actions`
#   （`OBSERVE_ONLY_ACTION_TYPES`），此处只 import 复用，禁止再复制一份词表。


def _is_pure_verification_case(case: dict) -> bool:
    """纯验证用例 = 无交互动作 + 断言全部可确定性执行 → 无探索价值。

    根因: 此类用例（如"低权执行受限操作应被拒绝"：navigate + access_control_check）
      被标 `_exploratory=True` → 进 Agent 多轮探索；但既无交互可做、又无法自行提证
      → 空转耗尽预算 → 结构性假 ERROR（实测 REQ-016/017/018）。行为型断言已由
      Step4 确定性执行器（access_control_check/semantic_check/…）承载，无需探索。
    通用性: 判据只用"动作是否改变状态"+"断言是否可执行（投影执行注册表）"，
      零业务/模块硬编码。
    """
    actions = case.get("actions") or []
    if not actions:
        return False
    for a in actions:
        if not isinstance(a, dict):
            return False
        if str(a.get("type") or "") not in OBSERVE_ONLY_ACTION_TYPES:
            return False
    checks = case.get("checks") or []
    if not checks:
        return False
    types = {_check_type_of(c) for c in checks}
    return bool(types) and types <= _EXECUTABLE_CHECK_TYPES


def ensure_post_action_triggers(case: dict) -> bool:
    """为「操作后果型」断言补全前置触发交互（就地修改），返回是否发生注入。

    ★ 根因修复(2026-10-14) RC-AH: 「断言可执行」≠「断言可满足」。
      现象: TC-REQ-025/026/027/028/029/037/049/050-I01 的 actions 只有
        `navigate + screenshot`，却断言 `field_error_visible_check`
        （"页面出现错误/校验提示"）→ 从未提交非法输入 → 错误条永不出现
        → 结构性假 FAIL。
      根因: `_is_pure_verification_case` 只看"动作是否改变状态 + 断言是否在
        执行注册表内"。`field_error_visible_check` 虽在注册表内，但其执行器
        `_handle_field_error_visible` **只观察 DOM、不触发交互** —— 它所需的
        "先提交非法输入"必须由 actions 承载。intent 生成端统一只发
        navigate+screenshot（注释称"AgentExplorer 负责"），该假设对随后被
        判为纯验证而免探索的用例不成立。
      修复: 动作序列缺交互时，注入**同一事实源**（`_trigger_actions`）的触发
        序列，并把用例固定为确定性执行（触发已显式给出，无需 Agent 探索）。
      通用性: 只依赖"断言类型→是否需前置交互"的登记表（POST_ACTION_CHECK_TYPES）
        与动作类型集合，零业务/模块硬编码。
    """
    if not isinstance(case, dict):
        return False
    # 字段规格 SSOT 按「当前项目」取需求镜像（延迟导入避免与配置单例成环）
    from src.core.config_loader import config as _cfg

    _checks = [c for c in (case.get("checks") or []) if isinstance(c, dict)]
    # 断言的来源不止结构化 checks：`_explore_task.test_method.verifications` 也是
    # 用例声明的断言（期望同样观察"操作后果"）。只看 checks 会让"断言只在验证点里"
    # 的用例漏过守卫 → 声明动作仍只有 navigate+screenshot → 后果永不发生 → 假 FAIL。
    _verif_expects = [
        str(v.get("expect") or "")
        for v in (
            (case.get("_explore_task") or {}).get("test_method") or {}
        ).get("verifications") or []
        if isinstance(v, dict)
    ]
    if not _checks and not _verif_expects:
        return False
    _types = {_check_type_of(c) for c in _checks}
    # ★ 极性取**断言级 expected**（需求义务写在断言里），不是用例级模板文案。
    #   原实现读 case.expected_feedback/expected_result（恒为"系统无报错"模板）→
    #   对"期望被拒绝"的 semantic_check 完全失明（实测 TC-REQ-037-E01 断言
    #   "系统拒绝保存并返回唯一性校验错误"却只发 navigate+screenshot → 假 FAIL）。
    _blob = " ".join(
        [
            str(
                (c.get("check") or {}).get("expected")
                if isinstance(c.get("check"), dict)
                else c.get("expect") or ""
            )
            for c in _checks
            # 防御型 no_error 的文案自带"错误"二字（"无错误"/"系统无报错"），
            # 把它并入极性判据会把**正向**用例误判为"期望错误" → 触发值取非法值
            # → 正向用例被自己注入的非法激励判死。极性只由**肯定型断言**表达。
            if _check_type_of(c) != "no_error"
        ]
        + list(_verif_expects)
    )
    # ★ 判据唯一化(2026-10-15)：改由 `_trigger_actions.check_requires_interaction` 单点裁决
    #   —— 该函数同时覆盖「类型登记」与「断言文本观察交互后果」两类情形。
    #   根因: 原判据在本处**另行拼装**（POST_ACTION_CHECK_TYPES ∈ 表 或 _blob 含拒绝语义），
    #     与生成端 `_trigger_actions` 的认知**不同源** → 覆盖面窄 →
    #     实测 606 条中 219 条"断言要交互后果、动作却只有 navigate+screenshot"
    #     的用例漏过守卫 → 结构性假 FAIL（且被归因 PRODUCT_BUG）。
    #   通用性: 只问"断言观察的是交互后果还是既有状态"，零业务/模块硬编码。
    _requires_interaction = any(
        check_requires_interaction(_check_type_of(c), _blob) for c in _checks
    ) or any(check_requires_interaction("", _e) for _e in _verif_expects)
    if not _requires_interaction:
        return False
    _actions = [a for a in (case.get("actions") or []) if isinstance(a, dict)]
    if not _actions:
        return False
    _op = str(case.get("description") or case.get("title") or "")
    # ★ 删除型：前提是「点删除（+确认）」，不是「录入 + 提交」—— 其形态已由
    #   `complete_submit_flow` 归一为 trigger_confirm；此处若再用通用表单触发覆写，
    #   会把删除流程重新变成新增表单流程（删除永不发生）。两条规则不得互相打架。
    try:
        from src.pipeline.step3._rule_extractor_buttons import (
            mutation_flow_kind as _flow_kind,
        )

        if _flow_kind(_op, True) == "trigger_confirm":
            return False
    except Exception:  # noqa: BLE001 —— 形态判据不可用 → 走原路径
        pass
    _module = str(case.get("module") or "")
    _project = str(case.get("project") or "") or str(
        getattr(_cfg, "project_name", "") or ""
    )
    # 前置（输入 + 提交）已建立 → 不得**替换**既有动作序列（那是用例自己声明的真实操作）；
    #   其"完备性"（必填/必勾选 + 记录名唯一值形态）由 `complete_submit_flow` 单独承担。
    if action_sequence_triggers_outcome(_actions):
        return False
    _nav = next(
        (a for a in _actions if str(a.get("type") or "") in ("navigate", "goto")),
        None,
    )
    _tail = [
        a
        for a in _actions
        if str(a.get("type") or "") in ("screenshot", "wait", "wait_for")
    ]
    # 极性决定触发值：断言期望"错误/拒绝"（或本身就是字段错误断言）→ 非法值；
    # 期望"成功/接受的交互后果" → 合规值。二者共用同一构造器，不各写一套。
    _negate = "field_error_visible_check" in _types or expects_error_feedback(_blob)
    case["actions"] = (
        ([_nav] if _nav else [])
        + build_field_validation_trigger(
            "", _op, _blob, negate=_negate, module=_module,
            project=_project, unique_value=_case_unique_value(case),
        )
        + _tail
    )
    # 触发已显式给出 → 确定性执行（不探索，避免 Agent 空转耗尽预算 → 假 ERROR）。
    # 但若用例的断言**只在** `_explore_task` 的验证点里（无结构化 checks），则保留
    # `_explore_task`：注入触发反而会弹掉它 → 用例将失去全部断言。
    if _checks:
        case["_exploratory"] = False
        case.pop("_explore_task", None)
    _log.info(
        "[check_guard] 操作后果断言补全前置触发: %s",
        case.get("case_id") or case.get("title") or "?",
    )
    return True

# 负向语义：字段错误提示仅对这些用例有意义
_NEGATIVE_SEMANTIC_RE = re.compile(
    r"异常|错误|失败|无效|为空|非法|越权|拒绝|校验|提示|不存在|注入|xss|csrf",
    re.IGNORECASE,
)

# ★ needle 可靠性(2026-09-23 第三轮: 已被 semantic_check 通道取代)
#   历史上此处按 needle 形态（孤立占位符/引号装饰/超长散文）剔除 text_contains，
#   但"形态合法而来源是需求叙述"的 needle（如"用户名或密码错误"）仍必然 0 匹配。
#   根治改为**断言手段对齐期望性质**：语义期望 → semantic_check（AI 判定），
#   见下方 sanitize_case_checks。形态过滤不再需要，避免正则堆叠。


def _needle_of(entry: Any) -> str:
    if not isinstance(entry, dict):
        return ""
    ck = entry.get("check") or {}
    if not isinstance(ck, dict):
        return ""
    return str(ck.get("expected") or entry.get("expect") or "")


def _injected_values(case: dict) -> list:
    """本用例注入页面的输入值（声明动作 value + 探索计划验证点 value）。"""
    _vals: list[str] = []
    for _a in case.get("actions") or []:
        if isinstance(_a, dict):
            _vals.append(str(_a.get("value") or _a.get("text") or ""))
    _et = case.get("_explore_task") if isinstance(case.get("_explore_task"), dict) else {}
    _tm = _et.get("test_method") if isinstance(_et.get("test_method"), dict) else {}
    for _v in _tm.get("verifications") or []:
        if isinstance(_v, dict):
            _vals.append(str(_v.get("value") or ""))
    return _vals


def _is_value_level_expectation(case: dict, needle: str) -> bool:
    """期望字面值是否即为**本用例自己注入的输入值** —— 值级断言判据。

    why: 「列表中出现唯一值 X」「字段回显 X」这类期望的判定对象是**值本身**
      （要找到它），不是对句子的语义理解 —— 机械比较就是正确手段。把它转成语义
      判定会引入运行期 AI 依赖（架构约束「运行期零 AI」）→ 恒落非判定态。
    判据: 字面值出现在本用例的动作/验证点 value 中（用例自己把它填进了页面），
      判据自洽，不引任何词表。
    """
    _n = str(needle or "").strip()
    if len(_n) < 2:
        return False
    return any(str(_v or "").strip() == _n for _v in _injected_values(case))


def _check_type_of(entry: Any) -> str:
    """提取 check 的可执行类型（单一取法，与执行端 resolve_handler 一致）。

    ★ 健壮性修复(2026-09-27)：原实现 ``(entry.get("check") or {}).get("type")``
      假定 ``check`` 必为 dict——实测存量用例中存在 ``{"check": "自然语言", ...}``
      形态（``_combo_enhancer`` / ``_modeling_intents`` 的占位发射），
      该形态直接抛 ``AttributeError: 'str' object has no attribute 'get'``，
      使整个 Step3 落库咽喉点崩溃。字符串 ``check`` **不是类型**，
      必须按"无类型"处理（交由下游统一 fail-closed 剔除）。
    """
    if not isinstance(entry, dict):
        return ""
    _ck = entry.get("check")
    _t = _ck.get("type") if isinstance(_ck, dict) else None
    return str(_t or entry.get("type") or "")


# ★ 生成端"指令契约"第二半（2026-09-27）：check 之外，**action 也必须受契约约束**。
#   根因（与 check 词表漂移同源）：生成端发射了契约外 action（如 count_list_rows
#   "统计列表行数"基线动作），Step4 两条执行路径都没有消费分支
#   （不在 CANONICAL_ACTIONS 内）——既被静默略过，又让依赖它的深断言结构性死亡。
#   统一原则：**生成端只能发射执行端能执行的指令**；新增 action/check 必须先入契约。
_MUTATION_KEYWORDS = (
    "新增", "创建", "新建", "提交", "保存", "删除", "更新", "修改", "编辑",
    "注册", "登录", "导入", "上传", "发布", "下单", "支付", "绑定", "启用", "停用",
)
# 只读/检索型操作：字段级错误提示、列表行数增量断言对这类用例是**类别错误**
#   （正常行为不会弹出字段错误、也不会改变行数）→ 注入即结构性假 FAIL。
_READONLY_OP_KEYWORDS = (
    "查询", "检索", "搜索", "筛选", "查看", "展示", "浏览", "列表", "详情",
    "统计", "报表", "导出", "分页", "排序",
)


# 控件类别 → 操作性质（复用 _SEMANTIC_CATEGORIES 的类别名，非另造词表）
_WRITE_CATEGORIES = frozenset(
    {"create", "delete", "edit", "save", "import", "enable", "copy", "tag"}
)
_READONLY_CATEGORIES = frozenset({"query", "export"})

# (动词, 类别) 展开并按动词长度倒序 —— 最长优先，避免"新增"被单字"加"抢先匹配
#   ★ 去重(2026-09-27)：改用 `_rule_extractor_buttons._VERB_CATEGORY_SORTED`（同源
#     派生，禁止第三份词表）。两份实现曾各自排序，属同一 SSOT 的重复物化。
_VERB_CATEGORY: tuple = _VERB_CATEGORY_SORTED

# 参与"操作性质"判定的交互动作类型（点击/提交类才表达操作意图）
_INTENT_ACTION_TYPES = frozenset(
    {
        "click", "double_click", "right_click", "submit",
        "select_option", "check", "uncheck", "upload_file", "press_key",
    }
)


def _classify_control_category(text: str) -> str:
    """按语义类别表（`_SEMANTIC_CATEGORIES`，单一事实源）判定控件文本的类别。"""
    _s = str(text or "")
    for _v, _c in _VERB_CATEGORY:
        if _v and _v in _s:
            return _c
    return ""


def _action_role_signal(case: dict) -> str:
    """从**用例指令**判定操作性质：``"write"`` / ``"read"`` / ``""``（无法判定）。

    ★ 根因修复(2026-09-27 第三轮)：写入/只读的**唯一可靠信号是用例指令本身**
      —— 执行端正是按同一 ``target_hint`` 解析控件（"执行根据用例指令"）。
      文本叙述不可靠，实测证据（同一批 5 条用例）：

        | 用例 | click 目标 | 文本"写入"信号来源 | 正确性质 |
        |------|-----------|------------------|---------|
        | TC-REQ-001-B01（检索） | **查询** | "提交非法输入后…"（生成端**断言叙述**） | read |
        | TC-REQ-002-B01（新增） | **新增资产** | "…创建环节"（需求语义） | write |
        | TC-REQ-003-P01（修改） | **编辑** | "…更新数据库记录" | write |

      两者文本高度重叠（都含"提交/列表"），**按文本判定必错、按指令判定必对**。
      这正是"生成端只能发射执行端能执行的指令"的镜像：**判定也只能依据指令**。

    通用性：类别表来自 Step3 既有动词分类 SSOT，跨系统/行业/页面通用，零业务硬编码。
    """
    _acts = case.get("actions")
    if not isinstance(_acts, list):
        return ""
    _has_write = _has_read = False
    for _a in _acts:
        if not isinstance(_a, dict):
            continue
        if str(_a.get("type") or "") not in _INTENT_ACTION_TYPES:
            continue
        _cat = _classify_control_category(
            str(_a.get("target_hint") or _a.get("selector") or "")
        )
        if _cat in _WRITE_CATEGORIES:
            _has_write = True
        elif _cat in _READONLY_CATEGORIES:
            _has_read = True
    if _has_write:
        return "write"
    if _has_read:
        return "read"
    return ""


def _authored_signal(case: dict) -> str:
    """权威操作信号：``operation``（生成端由需求派生的操作意图）+ ``actions[].type``
    （可执行意图）。这两者才是"这条用例要做什么"的唯一权威表达。
    """
    _acts = case.get("actions")
    _act_types = (
        " ".join(str(a.get("type") or "") for a in _acts if isinstance(a, dict))
        if isinstance(_acts, list)
        else ""
    )
    return (
        strip_engine_template(str(case.get("operation") or "")) + " " + _act_types
    ).strip()


def _case_semantic_text(case: dict) -> str:
    """用例的「写入型/只读型」判定文本 —— **权威字段优先，断言叙述仅兜底**。

    ★ 根因修复(2026-09-27 第二轮)：判据必须收敛到**权威字段**（operation +
    actions[].type）。历史实现把 title/description/expected_* 一并纳入扫描，
    于是**断言叙述**被误当作**操作语义**：实测 TC-REQ-002-B01
    （操作=新增资产；断言叙述=「字段级校验提示可见且**列表**行数不变」）因
    "列表"命中 `_READONLY_OP_KEYWORDS` 被判**只读型** → 其唯一的字段级错误断言
    被 `field_error_visible_check` 语义过滤剔除 → 用例退化为仅剩 url_matches →
    Step4 无判定能力 → ERROR（同类误判同时命中 TC-REQ-002-P01 描述
    "断言**列表**出现新建记录"）。
    通用原则：**操作语义只能由操作字段推断；断言叙述不得反向定义操作类型。**

    第一轮的 `strip_engine_template` 仍必要（探针模板文案同样是"非需求派生文本"），
    但它只治"引擎自撰文案"这一种污染；本轮治的是更普遍的"断言叙述污染"。

    兜底：权威字段未命中任何写入/只读关键词时，才回落完整文本，兼容历史数据。
    """
    _sig = _authored_signal(case)
    if _sig and any(k in _sig for k in _MUTATION_KEYWORDS + _READONLY_OP_KEYWORDS):
        return _sig
    _explore = (
        case.get("_explore_task") if isinstance(case.get("_explore_task"), dict) else {}
    )
    return " ".join(
        strip_engine_template(str(case.get(k) or ""))
        for k in ("operation", "title", "description", "expected_result", "expected_feedback")
    ) + " " + str(_explore.get("operation") or "")


def _is_mutation_case(case: dict) -> bool:
    """该用例是否为"变更型"（新增/提交/删除…）——只有这类才可能产生字段级错误提示。

    ★ 判据优先级：**用例指令**（click 目标控件角色）> 文本关键词。
      指令是执行端真正解析的东西（"执行根据用例指令"），文本是叙述、不可靠。
    """
    _role = _action_role_signal(case)
    if _role:
        return _role == "write"
    return any(k in _case_semantic_text(case) for k in _MUTATION_KEYWORDS)


def _is_readonly_case(case: dict) -> bool:
    """该用例是否为"只读/检索型"（查询/展示/列表…）——与变更型互斥。

    同上：**用例指令**优先。实测 TC-REQ-001-B01（click 目标"查询"）判只读，
    TC-REQ-002-B01（click 目标"新增资产"）判写入——二者文本高度重叠，唯有
    指令能区分（见 `_action_role_signal` 的根因说明）。
    """
    _role = _action_role_signal(case)
    if _role:
        return _role == "read"
    return any(k in _case_semantic_text(case) for k in _READONLY_OP_KEYWORDS)


def _needs_baseline(entry: Any) -> bool:
    """该 check 的判定是否依赖**结构性不可得**的前置数据（基线或对比目标）。

    ★ 基线取得已下移到**执行期**（`capture_list_row_baseline` 在用例开始时采集 DOM
      事实）→ `expected_delta` 不再"结构性不可得"，不再剔除（否则"删除后行数减一"
      这类主张被静默丢掉）。
    仅剩一种：`list_row_count_check` **双缺**（既无 `expected_count` 也无
      `expected_delta`）→ 执行端无对比目标（实测 PF02 模板发 `operator=gte` 无对比值
      → 恒报"⊘无 expected_count/expected_delta" → 结构性 ERROR，与产品行为无关）。
    """
    if not isinstance(entry, dict):
        return False
    _ck = entry.get("check") if isinstance(entry.get("check"), dict) else {}
    if _ck.get("type") != "list_row_count_check":
        return False
    return (
        _ck.get("expected_count") is None
        and entry.get("expected_count") is None
        and _ck.get("expected_delta") is None
        and entry.get("expected_delta") is None
    )


def _expected_delta_of(entry: Any) -> Any:
    """check 声明的行数增量（``check.expected_delta`` 优先，兼容外层平铺形态）。"""
    if not isinstance(entry, dict):
        return None
    _inner = entry.get("check") if isinstance(entry.get("check"), dict) else {}
    _d = _inner.get("expected_delta")
    return _d if _d is not None else entry.get("expected_delta")


def sanitize_case_actions(case: dict) -> dict:
    """按统一 action 契约清洗用例动作（生成端唯一咽喉点，原地修改并返回）。

    契约外 action（classify_action → "UNKNOWN"）一律剔除 + ERROR 告警：
    契约里没有的指令就是执行端执行不了的指令，必须止步于生成端。
    通用性：只依赖 action 契约（CANONICAL_ACTIONS / TEMPLATE_BUNDLES /
      RESERVED_ACTIONS），零业务硬编码；新增 action 必须先入契约再发射。
    """
    acts = case.get("actions")
    if not isinstance(acts, list) or not acts:
        return case
    try:
        from src.pipeline.steps.step4.action_contract import classify_action
    except Exception as _e:  # noqa: BLE001
        _log.error("[check_guard] action 契约不可读: %r", _e)
        return case
    kept: list[Any] = []
    for a in acts:
        if not isinstance(a, dict):
            kept.append(a)
            continue
        _t = str(a.get("type") or a.get("action") or "").strip()
        if not _t:
            kept.append(a)
            continue
        if classify_action(_t) == "UNKNOWN":
            _log.error(
                "[check_guard] ★指令契约外 action★ 剔除 %r (case=%s) —— "
                "未登记在 CANONICAL_ACTIONS，Step4 无执行分支，留之必被静默略过",
                _t, case.get("case_id", "?"),
            )
            continue
        kept.append(a)
    if len(kept) != len(acts):
        case["actions"] = kept
    return case


def _case_unique_value(case: dict) -> str:
    """用例记录名的唯一值形态（生成端 SSOT；真值由运行期按 (run, case) 物化）。"""
    try:
        from src.pipeline.step3._fallback_cases import _gen_unique_value

        return _gen_unique_value(
            {"req_id": str(case.get("req_id") or "REQ")},
            "name",
            scope=str(case.get("case_id") or ""),
        )
    except Exception:  # noqa: BLE001 —— 生成端不可用 → 不归一（诚实降级）
        return ""


def _expectation_texts(case: dict) -> set:
    """用例全部断言的期望文本（判"动作值是否与断言同源"）。"""
    _out: set[str] = set()
    for c in case.get("checks") or []:
        if not isinstance(c, dict):
            continue
        _inner = c.get("check") if isinstance(c.get("check"), dict) else {}
        _e = str(_inner.get("expected") or c.get("expect") or "").strip()
        if _e:
            _out.add(_e)
    return _out


# ── 模块级「操作约束」语料（受禁止处理的状态行）──────────────────────────────
#   受限状态值是**需求语料**事实（同模块需求声明"某状态禁止被某操作处理"），
#   非业务词表。每批次读一次需求镜像并缓存；`sanitize_intents` 入口清空，
#   保证同批一致、不跨批陈旧。
_REQUIREMENTS_CACHE: dict[str, list] = {}
_REQ_TEXT_KEYS = ("description", "operation", "condition_expr",
                  "expected_feedback", "source_quote")


def clear_requirement_texts_cache() -> None:
    """清空模块约束语料缓存（批量守卫入口调用，保证同批一致）。"""
    _REQUIREMENTS_CACHE.clear()


def _project_requirements() -> list:
    """本工程全部需求行（同批缓存的**唯一装载点**，模块级/需求级读取共用）。"""
    from src.core.config_loader import config

    _proj = str(getattr(config, "project_name", "") or "")
    if _proj not in _REQUIREMENTS_CACHE:
        with guard("_project_requirements: requirement_store.list_requirements()", log=_log):
            from src.pipeline.utils.requirement_store import requirement_store

            _REQUIREMENTS_CACHE[_proj] = [
                _r for _r in (requirement_store.list_requirements() or [])
                if isinstance(_r, dict)
            ]
    return _REQUIREMENTS_CACHE.get(_proj, [])


def _requirement_row_text(_r: dict) -> str:
    """需求行的判定语料（字段口径见 `_REQ_TEXT_KEYS`，单一事实源）。"""
    return " ".join(str(_r.get(k) or "") for k in _REQ_TEXT_KEYS)


def module_requirement_texts(module: str) -> list[str]:
    """同模块全部需求的文本行（"某状态被禁止处理"的判定语料）。"""
    _mod = str(module or "")
    if not _mod:
        return []
    return [
        _requirement_row_text(_r)
        for _r in _project_requirements()
        if str(_r.get("module") or "") == _mod
    ]


def requirement_texts_of(req_id: str) -> list[str]:
    """指定需求自身的文本行（极性/期望强度判定语料）。"""
    _rid = str(req_id or "").strip()
    if not _rid:
        return []
    return [
        _requirement_row_text(_r)
        for _r in _project_requirements()
        if str(_r.get("req_id") or "").strip() == _rid
    ]


# ── 期望强度 ≤ 需求声明（落库前唯一咽喉）────────────────────────────────────
#   异常模板的 `expected` 是引擎自撰的通用文案，与需求无关；它要求"必须出现错误/
#   拒绝反馈"时，主张的强度由需求原文授予，不能由模板自撰。此前该门控只挂在单个
#   生成器上 → AI 生成/其它模板仍产出"需求从未声明错误极性"的异常用例，执行端逐
#   分支取证必然全不成立 → 结构性假 FAIL。
#   作用面刻意限定「异常用例」（结构化字段 `scenario_type`）：边界/容错模板的期望
#   允许"正确处理"分支成立，不构成超强主张。
#   判据全部复用既有 SSOT：`expects_error_feedback`（极性）+ `expects_noop_invariant`
#   （"操作不得生效"同属拒绝语义）+ `_REQ_TEXT_KEYS`（需求语料字段），不另立词表。
_ERROR_PATH_SCENARIO = "error_path"
_CASE_EXPECTATION_KEYS = ("expected_result",)
_CHECK_EXPECTATION_KEYS = ("expected", "expect")


def _case_expectation_text(case: dict) -> str:
    """用例自身主张的预期文本（结构化字段 + checks 期望，不读标题/描述）。"""
    _parts: list[str] = []
    for _k in _CASE_EXPECTATION_KEYS:
        _v = case.get(_k)
        if isinstance(_v, str) and _v.strip():
            _parts.append(_v)
    for _c in case.get("checks") or []:
        if not isinstance(_c, dict):
            continue
        _inner = _c.get("check") if isinstance(_c.get("check"), dict) else _c
        for _k in _CHECK_EXPECTATION_KEYS:
            _v = _inner.get(_k)
            if isinstance(_v, str) and _v.strip():
                _parts.append(_v)
    return " ".join(_parts)


def _requirement_declares_rejection(texts: list[str]) -> bool:
    """需求原文是否声明"被拒绝/错误反馈/操作不得生效"（极性同层判据）。"""
    from src.pipeline.step3._rule_extractor_keywords import expects_noop_invariant

    for _t in texts:
        _clean = strip_inferred_feedback_clause(str(_t or ""))
        if expects_error_feedback(_clean) or expects_noop_invariant(_clean):
            return True
    return False


def _requirement_declares_validation(texts: list[str]) -> bool:
    """需求原文是否声明字段级约束（必填/非空/规格书写范式）。

    复用字段规格 SSOT（`_REQUIRED_KEYS` + `_iter_specs_from_text`），不另立词表：
    需求声明了字段约束 ⇒ "空/非法输入应被拦下"就是需求本身的主张，异常用例不是超强。
    """
    from src.pipeline.step3._rule_extractor_fields import (
        _REQUIRED_KEYS,
        _iter_specs_from_text,
    )

    for _t in texts:
        _clean = strip_inferred_feedback_clause(str(_t or ""))
        if not _clean:
            continue
        if _iter_specs_from_text(_clean):
            return True
        _low = _clean.lower()
        if any(_k in _low for _k in _REQUIRED_KEYS):
            return True
    return False


def requirement_declares_negative_obligation(case: dict) -> "bool | None":
    """需求原文是否声明「拒绝 / 字段约束」义务（None = 语料不可得，不可判）。

    「用例可以主张错误/拒绝反馈」的唯一前提：期望强度门控
    （`overstated_expectation_reason`）与极性反转（`sanitize_case_checks`）
    共用本判据，下游不得各自从用例文案重推需求极性。
    """
    _texts = _requirement_texts_for(case)
    if not _texts:
        return None
    return _requirement_declares_rejection(_texts) or _requirement_declares_validation(_texts)


def overstated_expectation_reason(case: dict) -> str:
    """用例期望强度是否超出需求声明 —— 超出时返回归因（空串 = 未超出/不可判）。

    仅作用于「异常用例」（`scenario_type == error_path`）：容错/边界模板的期望
    允许"正确处理"分支成立，不是超强主张。

    fail-open 语义：需求原文不可得（条目缺失/多版本混读为空）时不判——无判据即
    不删用例，避免把"读不到需求"误当"需求未声明"。
    """
    if str(case.get("scenario_type") or "").strip() != _ERROR_PATH_SCENARIO:
        return ""
    _expect = _case_expectation_text(case)
    if not _expect or not expects_error_feedback(_expect):
        return ""
    if requirement_declares_negative_obligation(case) in (None, True):
        return ""
    return "异常用例主张错误/拒绝反馈，需求原文未声明该极性：%r" % _expect[:40]


def complete_submit_flow(case: dict, expects_error: bool = False) -> bool:
    """提交前置完备化（咽喉点独立步骤，**不依赖**"断言需要交互"）。

    why: 只要用例声明了提交流程，其前置（必填/必勾选 + 记录名唯一值形态）就必须完整 ——
      否则真实表单的前置校验会拦截提交（提交在任何一帧都未发生），断言要求的成功
      反馈结构性不出现。该完备性此前被挂在"断言需要交互"的判据之后 → 断言只观察
      列表既有状态的用例（如"提交后有成功反馈 + 列表出现唯一值"）漏过完备化。

    `expects_error=True`（期望被拒绝）时**不做完备化**：对这类用例，"前置不完整"
      正是被测激励（非法输入 → 应被拒绝）；填入合规必填值会把激励瓦解，令 SUT
      正确接受输入，断言"应被拒绝"反被判失败（实测 N01/I0x 族）。
    判据与录入段构造同源：`_trigger_actions.complete_submit_preconditions`（字段规格 SSOT）。
    """
    # ★ 删除型形态归一（**与极性无关**：删除的激励就是"点删除"，不是填表单；
    #   正/负向删除用例共用同一形态 —— 负向的"被拒绝"由服务端返回，不需要录入段）
    # 只读观察面用例（生成端一次性声明）不参与形态归一：其断言只看页面既有状态，
    #   补变更激励会让同一 SUT 缺陷在只读模板里被重复计数。
    from src.pipeline.step3._trigger_actions import (
        DATA_PRECONDITION_EXISTING_ROW,
        INTERACTION_SCOPE_OBSERVE_ONLY,
    )

    if str(case.get("interaction_scope") or "") == INTERACTION_SCOPE_OBSERVE_ONLY:
        return False
    _op = str(case.get("description") or case.get("title") or "")
    # 前置数据需求（生成端一次性声明，下游只读）：删除/编辑行的激励对象是"已有记录"，
    #   记录不存在时"未命中"既不是产品缺陷也不是用例期望 —— 执行端据此先造行。
    #   负向删除（期望被拒绝）不声明：其激励恰是"目标不可删除/不存在"。
    from src.pipeline.step3._rule_extractor_buttons import mutation_flow_kind

    if not expects_error and mutation_flow_kind(_op, True) == "trigger_confirm":
        case["data_precondition"] = DATA_PRECONDITION_EXISTING_ROW
    _acts0 = [a for a in (case.get("actions") or []) if isinstance(a, dict)]
    # ★ 删除触发须限定到正确状态行（同模块需求声明"某状态禁止删除"）：
    #   正向删除排除该受限状态行（否则被服务端拒绝 → 行数不减 → 假 FAIL）；
    #   负向删除（期望被拒绝）反之须命中该受限状态行（否则只是"碰巧"点到，数据一变即失效）。
    #   受限状态值来自需求语料（非业务词表）。
    from src.pipeline.step3._fallback_cases import _RESTRICTED_ACTION_TEXTS

    _del_tokens = tuple(
        t for t in str(_RESTRICTED_ACTION_TEXTS.get("删除") or "").split(",") if t
    )
    _restricted = forbidden_states_in_texts(
        module_requirement_texts(str(case.get("module") or "")), _del_tokens
    )
    _exclude = [] if expects_error else _restricted
    _include = _restricted if expects_error else []
    _del_acts, _del_fixed = align_delete_flow_shape(
        _acts0, _op, exclude_states=_exclude, include_states=_include
    )
    if _del_fixed:
        case["actions"] = _del_acts
        _log.info(
            "[check_guard] 删除型形态归一（trigger_confirm）: %s",
            case.get("case_id") or case.get("title") or "?",
        )
        return True
    if expects_error:
        return False
    from src.core.config_loader import config as _cfg2

    _module = str(case.get("module") or "")
    _project = str(case.get("project") or "") or str(
        getattr(_cfg2, "project_name", "") or ""
    )
    _acts = [a for a in (case.get("actions") or []) if isinstance(a, dict)]
    # 提交流程的入口必须是录入入口（形态串位会让表单从未打开）
    _acts, _entry_fixed = ensure_form_entry(_acts)
    _completed, _added = complete_submit_preconditions(
        _acts,
        _op,
        module=_module,
        project=_project,
        unique_value=_case_unique_value(case),
        expectation_texts=_expectation_texts(case),
    )
    if _completed == case.get("actions"):
        return False
    case["actions"] = _completed
    _log.info(
        "[check_guard] 提交前置完备化: %s (+%d 项；入口归一=%s；含记录名唯一值归一)",
        case.get("case_id") or case.get("title") or "?",
        len(_added),
        _entry_fixed,
    )
    return True


def _align_feedback_assertions(case: dict, expects_error: bool = False) -> list:
    """把断言手段对齐到期望性质（反馈条极性 → 确定性 check；值级期望 → 机械比较）。

    契约: 运行期零 AI（架构约束）⇒ 语义断言的 AI 兜底不可用。判据只有一条：
      **该期望的判定对象是什么**——
        · 反馈条极性（"系统应给出错误/成功反馈"）→ DOM 事实，落确定性 check：
          错误极性 → `field_error_visible_check`（既有执行器：DOM 错误条取证 +
            广义类名形态判据 + "页面无表单则不适用"诚实分支）；
          接受极性 → `element_exists`（共享成功条选择器 SSOT）。
        · 值级期望（期望字面值即本用例注入的输入值）→ 值本身，落机械
          `text_contains_check`（要"找到这个值"，不是"理解这句话"）。
      极性判据复用 `expects_error_feedback` / `expects_accept_feedback`，选择器复用
      `feedback_selectors`，值级判据复用 `_is_value_level_expectation` —— 本函数不持有词表。
    通用性: 反馈条形态/值级比较与业务、框架无关，跨系统/行业/页面成立。
    """
    out: list = []
    # 既有「行数差分」断言的期望值集合 —— 幂等保护：重复定稿不得叠加同一条差分断言。
    _existing_deltas: set = set()
    for _c0 in case.get("checks") or []:
        if isinstance(_c0, dict) and _check_type_of(_c0) in (
            "list_row_count_check",
            "list_row_count",
        ):
            _d0 = _expected_delta_of(_c0)
            if _d0 is not None:
                _existing_deltas.add(_d0)
    for c in case.get("checks") or []:
        if not isinstance(c, dict):
            out.append(c)
            continue
        _exp_any = str(
            (c.get("check") or {}).get("expected")
            if isinstance(c.get("check"), dict)
            else c.get("expect") or ""
        )
        # ①.5 「行数差分」期望 → 追加**确定性**行数差分断言（基线在用例开始时由执行期
        #   采集，见 `capture_list_row_baseline`）。语义断言保留为审计痕迹：它常同时含
        #   DB 类主张（如"审计日志记录一条"），本引擎无 DB 直连时不可判；差分断言承担
        #   其中**可确定性取证**的那一部分（同一条期望的两种判定手段，非替换）。
        _row_delta = row_count_delta_in_text(_exp_any)
        if (
            _row_delta is not None
            and _row_delta not in _existing_deltas
            and _check_type_of(c) not in (
                "list_row_count_check",
                "list_row_count",
            )
            # 只读/检索型不产生数据变更 → 行数 Δ 不是该需求的判定对象（与生成端过滤同源）
            and not (_is_readonly_case(case) and not _is_mutation_case(case))
        ):
            out.append(
                {
                    "track": str(c.get("track") or "ui"),
                    "precision": "exact",
                    "check": {
                        "type": "list_row_count_check",
                        "expected_delta": _row_delta,
                        "operator": "eq",
                    },
                    "expect": "列表行数 Δ=%d（执行期基线差分）" % _row_delta,
                    "_aligned_from": "row_count_delta",
                }
            )
        # ⓿ 极性自相矛盾：需求声明"应被拒绝"（单源：需求自身期望反馈）时，同一用例
        #   不得再主张"应成功/接受" —— 两条断言不可能同时成立，成功类断言是生成
        #   模板的通用短语残留（实测 TC-REQ-061-P01：删除被拦截的需求却带
        #   "提交后系统应保存该数据并给出成功反馈"）。**任何 check 类型**都适用，
        #   故先于类型分支判定（已对齐过的确定性 check 同样会残留该矛盾）。
        if (
            expects_error
            and expects_accept_feedback(_exp_any)
            and not expects_error_feedback(_exp_any)
        ):
            _log.info(
                "[check_guard] 极性矛盾断言剔除: %s 期望=%r（需求声明应被拒绝）",
                case.get("case_id", "?"),
                _exp_any[:40],
            )
            continue
        if _check_type_of(c) != "semantic_check":
            out.append(c)
            continue
        _inner = c.get("check") if isinstance(c.get("check"), dict) else {}
        _exp = str(_inner.get("expected") or c.get("expect") or "")
        # ① 值级期望：恢复机械比较（不得因"曾转语义"而永久失去可确定判定）
        if _is_value_level_expectation(case, _exp):
            _restored = dict(c)
            _restored["check"] = dict(_inner) or {}
            _restored["check"]["type"] = "text_contains_check"
            if "type" in _restored:
                _restored["type"] = "text_contains_check"
            _restored.pop("_converted_from", None)
            _restored["_restored_from"] = "semantic_check"
            _log.info(
                "[check_guard] 断言手段对齐: semantic_check→text_contains_check "
                "(case=%s 期望=%r 即本用例注入值，值级断言可机械判定)",
                case.get("case_id", "?"),
                _exp[:40],
            )
            out.append(_restored)
            continue
        # ② 反馈条极性：落确定性 DOM 取证
        _new_type = ""
        _new_inner: dict = {}
        # field_error_visible_check 的语义前提是「存在变更型提交」，与下方清洗阶段的
        # 适用性门禁同一判据；此处是第二个创建入口，不套同一判据就会造出**结构不可达**
        # 的硬断言（只读/无提交用例永远不可能弹出字段级错误条 → 恒假 FAIL）。
        _error_bar_applicable = (
            expects_error and _is_mutation_case(case) and not _is_readonly_case(case)
        )
        if expects_error_feedback(_exp) and _error_bar_applicable:
            # 析取式期望（"显示错误提示或确认拦截"）= 任一分支成立即符合需求。
            #   收窄为单一机制断言会在 SUT 走另一分支时必假 FAIL，也把"断言强度"
            #   抬到了声明期望之上；故落 OR 语义，分支证据面取自同一选择器事实源。
            if is_disjunctive_text(_exp):
                _new_type = "any_of_check"
                # 分支集由期望自身极性派生（错误条 ∨ 确认弹窗 ∨ 成功条 ∨ 结果承载面），
                #   与族⑤共用同一构造器；不得在此另写一份分支表（漂移源）。
                _new_inner = outcome_surface_check(_exp)
            else:
                _new_type = "field_error_visible_check"
                _new_inner = {
                    "type": _new_type,
                    "expected": _exp,
                    "operator": str(_inner.get("operator") or "not_empty"),
                    "selector": ERROR_SELECTOR_CSV,
                }
        elif expects_confirm_dialog(_exp):
            # "须弹出确认框"的观察对象是**确认弹窗是否出现**，与文案语义无关。
            #   原生 confirm/alert 与框架模态框是同一需求的两种实现形态，
            #   故落 OR 语义（任一出现即满足）；两者都没出现 = 产品未要求确认。
            _new_type = "any_of_check"
            _new_inner = {
                "type": _new_type,
                "expected": _exp,
                "alternatives": [
                    {"type": "dialog_check", "expected": _exp},
                    {
                        "type": "element_exists",
                        "expected": _exp,
                        "selector": CONFIRM_SELECTOR_CSV,
                    },
                ],
            }
        elif expects_accept_feedback(_exp):
            _new_type = "element_exists"
            _new_inner = {
                "type": _new_type,
                "expected": _exp,
                "selector": SUCCESS_SELECTOR_CSV,
            }
        if not _new_type:
            out.append(c)
            continue
        c = dict(c)
        c["check"] = _new_inner
        if "type" in c:
            c["type"] = _new_type
        c["_aligned_from"] = "semantic_check"
        _log.info(
            "[check_guard] 断言手段对齐: semantic_check→%s (期望=%r，反馈条极性可确定性取证)",
            _new_type,
            _exp[:40],
        )
        out.append(c)
    return out


# ── 断言族：由需求声明的可观察事实派生（落库前唯一咽喉）─────────────────────
#   why: 模板通用期望（"系统不崩溃"/"页面正常渲染"）只能落 element_exists + no_error,
#     它只证明"页面加载且无 JS 异常"，不是需求声明的事实；判定对象必须由需求语义派生：
#     访问控制 → 拒绝行为；拒绝 → 错误反馈；变更 → 行数差分；列表呈现 → 行数规模。
#   判据全部复用既有 SSOT（极性/访问限制/变更语义/列表行选择器），本段不持有词表。
_WEAK_CHECK_TYPES = frozenset({"element_exists", "no_error", "no_fatal_error"})


def outcome_surface_check(expected: str) -> dict:
    """「容错/析取」期望的可观察结果面断言（**唯一构造器**，两条生成通道共用）。

    分支由期望自身的极性语汇派生（同一事实源 `_rule_extractor_keywords`），
    而不是按用例模板写死：
      - 错误提示面（声明错误/拒绝/校验）→ `element_exists(错误反馈选择器)`；
      - 确认弹窗面（声明需二次确认）→ `dialog_check ∨ element_exists(确认选择器)`；
      - 接受结果面（声明成功/接受语义，或显式中性结果语汇"正确处理/合理结果"）
        → `element_exists(成功反馈选择器)` + `element_exists(列表容器选择器)`；
    任一分支成立即符合析取式期望；**全不成立才 FAIL**（可证伪）。

    why 错误面不能用 `field_error_visible_check`：该检查的执行前提是"页面存在可编辑
      字段"。删除/导入/筛选等非表单流程上它恒返回 N/A（不适用）→ 析取式断言失去
      错误提示这一支 → 即便 SUT 真的渲染了错误反馈条也无法成立 → 假 FAIL
      （实测 TC-REQ-063-I03 停在正常列表页、三支全不可达而 FAIL）。
      通用性: 反馈条/弹窗/结果承载面全部取既有选择器 SSOT，零业务硬编码。
    """
    from src.pipeline.step3._fallback_cases import _LIST_CONTAINER_SELECTOR

    _alts: list[dict] = []
    if expects_error_feedback(expected):
        _alts.append(
            {"type": "element_exists", "expected": "存在", "selector": ERROR_SELECTOR_CSV}
        )
    if expects_confirm_dialog(expected):
        _alts.append({"type": "dialog_check", "expected": expected[:80]})
        _alts.append(
            {
                "type": "element_exists",
                "expected": "存在",
                "selector": CONFIRM_SELECTOR_CSV,
            }
        )
    if expects_accept_feedback(expected) or has_tolerant_outcome_marker(expected):
        _alts.append(
            {"type": "element_exists", "expected": "存在", "selector": SUCCESS_SELECTOR_CSV}
        )
        # "正确处理/合理结果"类的中性结局可观察为结果承载面（列表已渲染）；
        #   纯错误/确认期望**不得**挂此分支——否则页面只要还在就等于满足了"应被拒绝"。
        _alts.append(
            {
                "type": "element_exists",
                "expected": "存在",
                "selector": _LIST_CONTAINER_SELECTOR,
            }
        )
    # 一种极性都解析不出（异常输入的非标准措辞）→ 至少要求错误提示面（可证伪）。
    if not _alts:
        _alts = [
            {"type": "element_exists", "expected": "存在", "selector": ERROR_SELECTOR_CSV}
        ]
    return {"type": "any_of_check", "expected": expected[:80], "alternatives": _alts}


def declare_feedback_scope(case: dict) -> int:
    """为「错误反馈」断言声明**反馈面适用性**（唯一生产点，就地修改）。

    ★ 根因修复(2026-10-27)：执行器 `_handle_field_error_visible` 的 N/A 判据是
      "页面是否存在可编辑字段"。该判据对**字段校验**类期望成立，但对
      **操作级**拒绝期望（删除/导入/分类拦截…）不成立——这些流程的终态页就是
      列表页，恒无字段 → 断言一律判 N/A → 用例假 SKIP（实测 TC-REQ-039-E01/I01、
      041-E01/I01、063-I02 停在正常列表页而 SKIP，需求要求的错误提示从未被判定）。
      根因是"页面能力"与"用例激励形态"两个问题被同一个判据回答。
      修法：由生成端在**动作定稿后**按 `field_rejection_scope`（动作类型 SSOT）
      一次性写入 `check.feedback_scope`，执行端只读（`field`=字段面 / `operation`=操作面），
      不再自行用页面形态猜。
    通用性: 只依赖动作类型（跨系统/行业/页面通用），零业务硬编码。

    返回被标注的检查项数量。
    """
    if not isinstance(case, dict):
        return 0
    _scope = field_rejection_scope(case.get("actions") or [])
    _n = 0
    for c in case.get("checks") or []:
        if not isinstance(c, dict):
            continue
        _inner = c.get("check") if isinstance(c.get("check"), dict) else None
        if not _inner or str(_inner.get("type") or "") != "field_error_visible_check":
            continue
        if _inner.get("feedback_scope") == _scope:
            continue
        _inner["feedback_scope"] = _scope
        _n += 1
    return _n



def _requirement_texts_for(case: dict) -> list[str]:
    """用例所属需求的原文语料（req 自身优先，退化到同模块）。"""
    _texts = requirement_texts_of(str(case.get("req_id") or ""))
    if _texts:
        return _texts
    _module = str(case.get("module") or "")
    return module_requirement_texts(_module) if _module else []


def _only_weak_checks(checks: list) -> bool:
    """断言是否全为「页面加载/无异常」类弱断言（无需求事实的判定对象）。"""
    return bool(checks) and all(_check_type_of(c) in _WEAK_CHECK_TYPES for c in checks)


def _list_row_selector_hit(selector: str) -> bool:
    """该选择器是否指向**列表数据行**（判据取列表行选择器 SSOT 的任一项）。"""
    from src.pipeline.step3._fallback_cases import _LIST_ROW_SELECTOR

    _sel = str(selector or "")
    return any(
        _tok.strip() and _tok.strip() in _sel
        for _tok in str(_LIST_ROW_SELECTOR).split(",")
    )


def align_requirement_observable_assertions(case: dict, expects_error: bool = False) -> bool:
    """把弱断言升级为「需求声明的可观察事实」对应的断言族（就地修改）。

    只在现有断言**全为弱断言**时生效（强断言已表达需求事实，无需改写）。
    五个断言族（一族一条，取首个适用者）：
      ① 访问控制（`_is_access_restriction_req`）→ `access_control_check`（执行器
         自行驱动受限入口并取证拒绝）；无可驱动入口候选时**不发射**（空判据是假 PASS 温床）。
      ② 拒绝型变更（需求/用例声明拒绝 ∨ 需求声明字段约束）→ `field_error_visible_check`
         （触发动作由 `ensure_post_action_triggers` 依同一注册表补全）。
      ③ 变更型且动作已建立交互后果（或删除型）→ `list_row_count_check(expected_delta)`
         （删除 -1 / 新增·导入 +1，方向由操作语义 SSOT 派生）。
      ④ 断言对象是列表数据行 → `list_row_count_check(gte 1)`（"列表有数据行渲染"的行数形态）。
      ⑤ 容错/析取期望且动作已建立交互后果 → `any_of_check`（错误反馈条 ∨ 成功反馈条
         ∨ 结果承载面；三支全不成立才 FAIL）。
    通用性: 判据全部来自极性/访问限制/变更语义/列表行选择器 SSOT，零业务硬编码。
    """
    checks = [c for c in (case.get("checks") or []) if isinstance(c, dict)]
    if not _only_weak_checks(checks):
        return False
    _texts = _requirement_texts_for(case)
    _blob = " ".join(_texts)
    _op = str(case.get("operation") or case.get("description") or case.get("title") or "")

    # ⓪ 安全探针：用例自己注入了哪类攻击载荷，断言就必须观察**该类攻击是否被挡住**
    from src.pipeline.step3.case_rules.base import classify_security_payload

    _probe_vals = [v for v in _injected_values(case) if classify_security_payload(v)]
    if _probe_vals:
        _attack = classify_security_payload(_probe_vals[0])
        _payloads = [v for v in _probe_vals if classify_security_payload(v) == _attack]
        # 防御成功 = 该类攻击的**可观察痕迹不存在**（DB 错误泄露 / 载荷被原样反射为标记），
        #   而不是"页面仍能打开"；no_error 保留（崩溃是另一条独立的防御面）。
        case["checks"] = [
            {
                "track": "ui",
                "precision": "exact",
                "check": {
                    "type": "security_probe_check",
                    "attack": _attack,
                    "payloads": _payloads,
                },
                "expect": "注入后未见该类攻击的可利用痕迹（%s）" % _attack,
                "_aligned_from": "weak",
            }
        ] + [c for c in checks if _check_type_of(c) == "no_error"]
        _log.info(
            "[check_guard] 断言族派生: 弱断言→security_probe_check(attack=%s payloads=%d) (case=%s)",
            _attack, len(_payloads), case.get("case_id", "?"),
        )
        return True

    # ① 访问控制
    from src.pipeline.step3.case_rules.base import _is_access_restriction_req

    if _is_access_restriction_req(_blob):
        from src.pipeline.step3._fallback_cases import (
            _access_control_checks,
            _restricted_actions,
        )

        _verbs = _restricted_actions(
            _blob, _op, str(case.get("expected_feedback") or ""),
            _case_expectation_text(case),
        )
        if _verbs:
            case["checks"] = _access_control_checks(_verbs)
            _log.info(
                "[check_guard] 断言族派生: 弱断言→access_control_check (case=%s 受限操作=%s)",
                case.get("case_id", "?"), _verbs,
            )
            return True

    # ② 拒绝型变更 → 错误反馈
    from src.pipeline.step3._rule_extractor_keywords import (
        expects_error_feedback,
        is_disjunctive_text,
    )

    _exp_text = _case_expectation_text(case)
    _observe_only = str(case.get("interaction_scope") or "") == INTERACTION_SCOPE_OBSERVE_ONLY
    # 断言观察的错误反馈必须是**用例自己声明**的后果（需求极性只决定"能否"，不决定"要不要"）；
    #   析取式期望（"正确处理**或**提示"）允许不漏提示的结果，收窄为错误条断言即抬高强度。
    _declares_err = expects_error_feedback(_exp_text) and not is_disjunctive_text(_exp_text)
    if (
        _declares_err
        and _is_mutation_case(case)
        and not _is_readonly_case(case)
        # 只读观察面用例不得被注入变更激励：其断言只看页面既有状态，
        #   补交互会让同一 SUT 缺陷在只读模板里被重复计数。
        and not _observe_only
    ):
        _exp = _case_expectation_text(case) or _blob[:60]
        case["checks"] = checks + [
            {
                "track": "ui",
                "precision": "exact",
                "check": {
                    "type": "field_error_visible_check",
                    "expected": _exp,
                    "operator": "not_empty",
                    "selector": ERROR_SELECTOR_CSV,
                },
                "expect": "页面出现错误/拒绝反馈（需求声明应被拒绝）",
                "_aligned_from": "weak",
            }
        ]
        _log.info(
            "[check_guard] 断言族派生: 弱断言→field_error_visible_check (case=%s)",
            case.get("case_id", "?"),
        )
        return True

    # ③ 变更型（已建立交互后果）→ 行数差分
    from src.pipeline.step3._rule_extractor_buttons import classify_operation_category
    from src.pipeline.step3._rule_extractor_keywords import expects_accept_feedback

    _cat = classify_operation_category(_op)
    # 差分的判定对象是"操作把一条记录写进/移出列表"这一**已声明的接受后果**；
    #   防御/容错类期望（"不崩溃"/"正确处理或提示"）没有这条主张，不得臆造差分。
    _expects_accept = expects_accept_feedback(_case_expectation_text(case))
    if _is_mutation_case(case) and not _is_readonly_case(case) and _expects_accept:
        _delta = {"delete": -1, "create": 1, "import": 1}.get(_cat)
        if _delta is not None and (
            _cat == "delete" or action_sequence_triggers_outcome(case.get("actions") or [])
        ):
            case["checks"] = checks + [
                {
                    "track": "ui",
                    "precision": "exact",
                    "check": {
                        "type": "list_row_count_check",
                        "expected_delta": _delta,
                        "operator": "eq",
                    },
                    "expect": "列表行数 Δ=%d（执行期基线差分）" % _delta,
                    "_aligned_from": "weak",
                }
            ]
            _log.info(
                "[check_guard] 断言族派生: 弱断言→list_row_count_check(Δ=%d) (case=%s)",
                _delta, case.get("case_id", "?"),
            )
            return True

    # ④ 列表呈现 → 行数规模（替换指向列表行的裸存在性断言）
    _out = list(case.get("checks") or [])
    _changed = False
    for _i, _c in enumerate(_out):
        if not isinstance(_c, dict) or _check_type_of(_c) != "element_exists":
            continue
        _inner = _c.get("check") if isinstance(_c.get("check"), dict) else {}
        _sel = str(_inner.get("selector") or _c.get("selector") or "")
        if not _list_row_selector_hit(_sel):
            continue
        from src.pipeline.step3._fallback_cases import _LIST_ROW_SELECTOR

        _out[_i] = {
            "track": str(_c.get("track") or "ui"),
            "precision": "exact",
            "check": {
                "type": "list_row_count_check",
                "selector": _LIST_ROW_SELECTOR,
                "operator": "gte",
                "expected_count": 1,
            },
            "expect": "列表至少渲染 1 行数据：%s" % str(_inner.get("expect") or _c.get("expect") or "")[:40],
            "_aligned_from": "element_exists",
        }
        _changed = True
    if _changed:
        case["checks"] = _out
        _log.info(
            "[check_guard] 断言族派生: element_exists(列表行)→list_row_count_check(gte 1) (case=%s)",
            case.get("case_id", "?"),
        )
        return True

    # ⑤ 容错/析取期望（"不崩溃/正确处理/提示…"）→ 交互后的**合法结果面**
    #   根因(2026-10-27): 异常/边界模板的期望天然是**析取式**（"显示错误提示或确认拦截"、
    #     "系统不崩溃，显示无结果或提示"）——族 ② 因主动收窄单一极性而跳过、族 ③ 因无
    #     接受语义跳过、族 ④ 的判定对象不是列表行 ⇒ 用例只剩
    #     `element_exists(页面主体) + no_fatal_error`（弱断言：只证明"页面能打开"）。
    #     而激励（检索/筛选/导入/导出/删除点击）已由生成端真实执行 → 交互后果无人观察。
    #   修复: 用既有 `any_of_check` 执行器承载"交互后至少出现一个合法结果面"
    #     （错误反馈条 / 成功反馈条 / 结果承载面）——三个分支全不成立才 FAIL（可证伪），
    #     既不放宽到"页面能打开"，也不把析取期望臆造收窄为单一极性。
    #   边界(2026-10-27 二次根因): 判据必须是**容错型结果**（`is_tolerant_outcome`：
    #     析取式或显式结果面语汇），不能用"既非拒绝也非接受"的补集 —— 否则
    #     ① 布局前置条件（"表单容器已渲染…必填标记可见"）、② 防御探针（"系统无报错"）
    #     也会被改写；这两类在表单/详情页上三个分支必然全不成立 → 结构性假 FAIL。
    #   通用性: 判据只有"动作是否建立交互后果"与既有容错/极性事实源，零业务硬编码。
    _exp_tolerant = _case_expectation_text(case)
    _acts_now = [a for a in (case.get("actions") or []) if isinstance(a, dict)]
    _has_interaction = any(
        str(a.get("type") or "") not in OBSERVE_ONLY_ACTION_TYPES for a in _acts_now
    )
    if _has_interaction and is_tolerant_outcome(_exp_tolerant):
        case["checks"] = [
            {
                "track": "ui",
                "precision": "fuzzy",
                "check": outcome_surface_check(_exp_tolerant),
                "expect": "交互后页面至少呈现一个合法结果面（错误/成功反馈条、确认弹窗或结果承载面）",
                "_aligned_from": "weak",
            }
        ] + [
            c
            for c in checks
            if _check_type_of(c) in ("no_error", "no_fatal_error")
        ]
        _log.info(
            "[check_guard] 断言族派生: 弱断言→any_of_check(反馈条|结果承载面) (case=%s)",
            case.get("case_id", "?"),
        )
        return True
    return False


def align_detail_entry_flow(case: dict) -> bool:
    """断言观察「详情/查看」视图时，动作必须真的进入该视图（详情入口点击）。

    why: "查看类"需求（GET /assets/<id> 详情）的用例被套用了**创建提交流程** ——
      提交成功后被重定向回列表，而断言要求详情视图 → 结构性假 FAIL
      （实测 TC-REQ-035-E01：断言 [data-testid='detail-panel'] 计数=1，动作却在
       点新增→填表→提交）。断言与动作在**页面上**必须同源。
    详情入口的通用形态：**列表首行的记录链接**（复用 `_LIST_ROW_SELECTOR` 行选择器
      SSOT，取行内第一个 a —— 记录名链接是最通用的详情入口，跨系统/框架成立）。
    只改"断言指向详情/查看 + 动作含提交"的用例；其余一律不动。
    """
    _checks = [c for c in (case.get("checks") or []) if isinstance(c, dict)]
    if not _checks:
        return False
    from src.pipeline.step3._fallback_cases import (
        _LIST_ROW_SELECTOR,
        _RESTRICTED_ACTION_TEXTS,
    )

    _view_tokens = tuple(
        t for t in str(_RESTRICTED_ACTION_TEXTS.get("查看") or "").split(",") if t
    )
    _blob = " ".join(
        str(
            (c.get("check") or {}).get("expected")
            if isinstance(c.get("check"), dict)
            else c.get("expect") or ""
        )
        for c in _checks
    )
    if not any(t in _blob for t in _view_tokens):
        return False
    _acts = [a for a in (case.get("actions") or []) if isinstance(a, dict)]
    if not any(_is_submit_action(a) for a in _acts) or not any(
        _check_type_of(c) != "no_error" for c in _checks
    ):
        return False
    _nav = next(
        (a for a in _acts if str(a.get("type") or "") in ("navigate", "goto")), None
    )
    _tail = [
        a
        for a in _acts
        if str(a.get("type") or "") in ("screenshot", "wait", "wait_for")
    ]
    _entry = ", ".join(
        s.strip() + " a" for s in str(_LIST_ROW_SELECTOR).split(",") if s.strip()
    )
    case["actions"] = (
        ([_nav] if _nav else [])
        + [{"type": "click", "target_hint": "css=" + _entry}]
        + _tail
    )
    _log.info(
        "[check_guard] 详情视图断言 → 补详情入口点击（列表首行记录链接）: %s",
        case.get("case_id") or case.get("title") or "?",
    )
    return True


def align_nav_target_with_siblings(intents: list) -> int:
    """同一需求的用例必须导航到**同一页面**；以兄弟用例的 URL 型导航为权威证据覆写。

    why: 页面名型导航目标由需求文本的字符重叠派生，描述里的偶发提及会把它带偏 ——
      实测 TC-REQ-025-E01：「保存编辑后资产」的描述提到"…生成对应审计记录…" → 导航
      目标成了"审计"；而同需求另一条用例（TC-REQ-025-I01）已解析出**真实路径**
      `/assets/`。同一需求只有一个目标页 —— 路径证据（URL 型）优于页面名猜测。
    安全边界：**只以已存在的兄弟 URL 覆写页面名型导航**（绝不臆造页面），
      URL 型导航不动。
    通用性: 判据只用"同需求 → 同页面"这一不变式 + 已有路径证据，零业务词表。
    """
    _url_by_req: dict[str, str] = {}
    for it in intents or []:
        if not isinstance(it, dict):
            continue
        _rid = str(it.get("req_id") or "").strip()
        if not _rid:
            continue
        for _a in it.get("actions") or []:
            if not isinstance(_a, dict) or str(_a.get("type") or "") not in (
                "navigate",
                "goto",
            ):
                continue
            _u = str(_a.get("url") or "").strip()
            if not _u:
                _h = str(_a.get("target_hint") or "").strip()
                _u = _h if _h.startswith(("http://", "https://", "/")) else ""
            if _u.startswith(("http://", "https://", "/")):
                _url_by_req.setdefault(_rid, _u)
    _fixed = 0
    for it in intents or []:
        if not isinstance(it, dict):
            continue
        _rid = str(it.get("req_id") or "").strip()
        _u = _url_by_req.get(_rid)
        if not _u:
            continue
        for _a in it.get("actions") or []:
            if not isinstance(_a, dict) or str(_a.get("type") or "") not in (
                "navigate",
                "goto",
            ):
                continue
            _h = str(_a.get("target_hint") or "").strip()
            if str(_a.get("url") or "").strip() or _h.startswith(
                ("http://", "https://", "/")
            ):
                continue
            _a["url"] = _u
            _a["target_hint"] = _u
            # ★ §4.17-B: 已由同需求真实路径解析成功 → 清除「未解析」标注（否则 UI 谎报）
            from src.pipeline.step3._navigation_resolver import clear_nav_unresolved

            clear_nav_unresolved(it)
            _fixed += 1
            _log.info(
                "[check_guard] 导航目标族内对齐: %s %r → %r (同需求真实路径)",
                it.get("case_id") or "?",
                _h[:20],
                _u,
            )
    return _fixed


def sanitize_case_checks(case: dict) -> dict:
    """按执行注册表与极性语义清洗单条用例的 checks（原地修改并返回）。"""
    # ★ 指令契约第一半：动作词表（与 check 词表同源治理）
    sanitize_case_actions(case)
    # ★ 详情视图断言 → 动作必须进入详情视图（断言与动作在页面上同源）
    align_detail_entry_flow(case)
    checks = case.get("checks")
    if not isinstance(checks, list) or not checks:
        return case

    # ★ 极性判定文本(2026-09-24): 除标题/操作/描述外，必须并入「需求期望反馈」。
    #   根因: "期望被拒绝"的语义常只落在 expected_feedback / expected_result /
    #   _explore_task.expected_feedback（如"系统返回错误提示…且不写库"），
    #   只扫标题+操作+描述会漏判 → no_error 极性过滤失效 → 假 FAIL 复发。
    # ★ 极性文本必须**剔除引擎推断产物**(2026-10-15):
    #   根因: `expected_feedback` 若是引擎按 operation 关键词推断的（如 REQ-040
    #     "登录失败…" 因含"登录"被判成 `登录成功,欢迎`），它不是需求自述，
    #     却会被并入极性文本 → 负向需求同时含"成功"（接受族）与"错误"（拒绝族）
    #     → 析取式误判 → 极性反转被豁免 → no_error 存活 → 恒假 FAIL。
    #     与 `resolve_expected_feedback` 同源：消费端不得把"引擎猜的"当需求义务。
    #   判据复用唯一事实源 `strip_inferred_feedback_clause`（SSOT，非新词表）：
    #     它只移除「系统预期反馈：<引擎推断值>」槽位，需求自述的槽位原样保留。
    #   同时必须叠加 `strip_engine_template`（引擎探针模板的**枚举信号**里含
    #     "校验提示"=拒绝族词，会把析取判定污染成两侧命中——与
    #     `expects_error_feedback` 内部的剥离纪律保持一致，二者同源同层）。
    _explore_task = case.get("_explore_task") if isinstance(case.get("_explore_task"), dict) else {}
    # ★ 根因修复(2026-10-15) D55：极性判据**不得**并入 `expected_result`。
    #   根因: `expected_result` 是引擎**派生展示字段**（case_rules 按操作类型套
    #     `_OP_SCENARIO_TEMPLATES` 模板生成，与需求无关）——实测 REQ-039「备注字段
    #     接受任意文本」被套成 `expected_result="显示错误提示"`。
    #     本函数 471 行已声明纪律「极性文本必须剔除引擎推断产物」，但只剔了
    #     `strip_inferred_feedback_clause` 那一种形态，漏了 `expected_result` 这个
    #     纯派生字段 → 派生值与需求自述 `expected_feedback` 冲突时判据被污染。
    #   后果（实测 v9 全库）: 11 条用例的 `expected_feedback` 是**接受/正向**
    #     （"系统接受任意文本输入并存储" / "系统提示保存成功" / "系统从列表中移除"），
    #     却因 `expected_result="显示错误提示"` 被判 `_expects_error=True` →
    #     `no_error` 被反转为"应显示错误提示" → SUT 正确接受输入（无错误）→
    #     恒假 FAIL 且被归因 PRODUCT_BUG（假 Bug）。
    #     受影响: REQ-013/029/032/039/046/049/066 等 I02/I03 变体。
    #   修法: 极性只读**需求自身的结构化声明**（expected_feedback / _explore_task.
    #     expected_feedback），下游只读不推 —— 与 §6「语义上游一次性判定」一致。
    #   通用性: 不引任何新词表，只是把"引擎猜的"排除在"需求义务"之外，跨系统/行业成立。
    _probe_text = strip_engine_template(
        strip_inferred_feedback_clause(
            " ".join(
                str(case.get(k) or "")
                for k in (
                    "title", "operation", "description",
                    "expected_feedback",
                )
            )
            + " " + str(_explore_task.get("expected_feedback") or "")
        )
    )
    # ★ 需求期望本身即错误/拒绝提示 → 负向断言 no_error("系统无报错")与之矛盾，
    #   注入即假 FAIL（SUT 按需求正确渲染错误提示反被判失败）。
    _expects_error = expects_error_feedback(_probe_text)
    # ★ 提交前置完备化（极性门控）：只对"期望提交成功"的流程成立 ——
    #   期望被拒绝的流程，前置不完整正是被测激励，补齐会瓦解激励（见函数文档）。
    complete_submit_flow(case, expects_error=_expects_error)
    kept: list[Any] = []
    # ★ 生成缺陷轨道留存(2026-09-27)：因"非指令/未注册"被剔除的轨道**不参与**
    #   required_tracks 收敛——那是生成端没造出可执行指令（生成缺陷），
    #   而 required_tracks 表达的是**需求对该维度的主张**。保留主张后，
    #   Step4 verification_plan_builder 的 RAG/Schema 兜底（_build_db_checks 等）
    #   才能接手产出**真实可判定**的断言；否则该维度被静默丢失。
    #   极性/语义类剔除（no_error / field_error / list_row_count）不在此列：
    #   那类剔除是"断言与用例语义不匹配"，属该维度确实不适用，应收敛。
    _defect_tracks: set[str] = set()
    # 「必须出现错误/拒绝反馈」是**需求**义务，不是模板文案的义务：需求原文明确未声明
    #   负向/字段约束时，no_error（"系统无报错"）正是"正确处理"分支的观察面——
    #   反转会把期望抬到需求声明之上，SUT 正确处理反被判假 FAIL。语料不可得(None)时不判。
    _req_negative = requirement_declares_negative_obligation(case)
    for entry in checks:
        ctype = _check_type_of(entry)
        if _EXECUTABLE_CHECK_TYPES and ctype not in _EXECUTABLE_CHECK_TYPES:
            # 契约违约告警：生成端发射了执行端无法分派的 check。
            # 分两类——① ctype 为空 = **非指令**（check 未携带 type，执行端
            #   resolve_handler 必落 UNSUPPORTED_CHECK）；② ctype 非空但未注册 = 词表漂移。
            _track = str(entry.get("track") or "").lower() if isinstance(entry, dict) else ""
            if _track in ("ui", "api", "db"):
                _defect_tracks.add(_track)
            if ctype:
                _log.error(
                    "[check_guard] ★词表漂移★ 剔除不可执行 check 类型 %r (case=%s) "
                    "——该类型未在 Step4 执行注册表注册，留之必判假 FAIL",
                    ctype, case.get("case_id", "?"),
                )
            else:
                _log.error(
                    "[check_guard] ★非指令 check★ 剔除 (case=%s, track=%s) —— check 未携带 "
                    "可执行 type（如 {'check': '<自然语言>'}），执行端无分派入口；"
                    "已保留 %r 维度主张，交由 Step4 RAG/Schema 兜底产出可判定断言",
                    case.get("case_id", "?"), _track or "-", _track or "-",
                )
            continue
        # ★ 根因修复(2026-09-28): **析取式期望不反转 no_error**。
        #   根因: 容错断言对 [main, no_error] 里的 no_error（"系统无致命错误"）是
        #     析取式用例（"显示错误提示**或**确认拦截"）的一部分——整体期望含
        #     "错误提示"使 `_expects_error` 判真 → no_error 被反转为"期望错误"断言
        #     → 与容错设计自相矛盾：SUT 正确处理（无错误条）时反转断言判 FAIL
        #     （实测 REQ-015/027/028-I02：容错 main 已 PASS，反转 semantic 却 FAIL）。
        #     析取式（is_disjunctive_text）本就同时陈述正/负结果，不构成单一极性，
        #     与 is_disjunctive_text 的设计初衷一致：不因命中"错误提示"就反转。
        if (
            ctype == "no_error"
            and _expects_error
            and _req_negative is not False
            and not has_opposite_outcome_disjunction(_probe_text)
        ):
            # ★ 极性反转(2026-09-27 通用化) —— 由「剔除」改为「反转」，根治探针-only 假 PASS。
            #   根因（5层why）:
            #     L1 现象: 若干 -E01 类负向用例恒 PASS（实测 conf=0.98，expected/actual/details
            #       全空），假绿；换一个批次又可能整批 FAIL。
            #     L2 直接: 该分支原实现把 no_error **直接剔除**——因为"需求期望本身即错误
            #       提示"，与 no_error("系统无报错") 极性矛盾，留着会假 FAIL。
            #     L3 后果: 剔除后该用例**失去唯一可判定断言**；而生成端
            #       `_normalize_required_tracks` 为保住 db 轨道自动注入了"环境探针"
            #       (db_table_accessed_check) → 用例只剩探针 → 探针恒真 → 恒 PASS 假绿。
            #     L4 为何反复: 历史上把"极性矛盾"当成"该断言不适用"，一律删除；
            #       删除是**丢失判定能力**，需求对"应被拒绝"的主张从此无人验证。
            #     L5 根本: 极性矛盾的正确处置是**反转断言极性**，不是删除断言。
            #   修复: 把 no_error("系统无报错") 反转为 semantic_check，期望取需求自身的
            #     负向期望文案（优先"自身即表达拒绝语义"的那一个候选，保证断言语义
            #     与极性自洽——否则会出现"因需求期望被拒绝而反转、却把期望设成无报错"
            #     的语义矛盾），兜底为通用负向期望文案。
            #     执行端 `_handle_semantic_check` 对"期望错误反馈"
            #     有 DOM 反馈条确定性判定通道 → 错误提示出现=PASS、缺失=FAIL，
            #     负向需求的真实判定能力得以保留（既不假绿也不假 FAIL）。
            #   通用性: 只依赖"需求期望极性"，跨系统/行业/页面成立，零业务硬编码。
            # ★ 根因修复(2026-10-15) D55：期望文案只取**需求自身的结构化声明**，
            #   不再回退到 `expected_result`（引擎派生展示字段，与需求无关）。
            #   根因: 原回退链 `... or next(c for c in _cands if c) or "<硬编码负向文案>"`
            #     会在需求自述为**接受型**时，仍取到派生值 "显示错误提示" 硬塞进断言，
            #     制造"用例期望成功 vs 断言要求报错"的自相矛盾 → 恒假 FAIL。
            #   修法（与 D56 同层，都属"断言文案必须自洽"）:
            #     进入本分支的前提已是「需求自身期望 = 拒绝语义」（_expects_error 为 True、
            #     已剔除 expected_result 污染），故此处**必然**能在需求自述中取到拒绝文案；
            #     取不到即说明前提不成立 → 放弃反转，保留 no_error 原义（系统无报错）。
            #   通用性: 零新增词表，仅消除"派生值冒充需求义务"。
            _req_expect = next(
                (
                    str(c or "").strip()
                    for c in (
                        case.get("expected_feedback"),
                        _explore_task.get("expected_feedback"),
                    )
                    if str(c or "").strip()
                    and expects_error_feedback(str(c or "").strip())
                ),
                "",
            )
            if not _req_expect:
                # 需求未给出拒绝语义 → 极性反转前提不成立 → 保持 no_error（系统无报错）
                kept.append(entry)
                continue
            _inner = entry.get("check") if isinstance(entry.get("check"), dict) else {}
            if not _inner:
                _inner = {"type": "semantic_check"}
                entry["check"] = _inner
            _inner["type"] = "semantic_check"
            _inner["expected"] = _req_expect
            if "type" in entry:
                entry["type"] = "semantic_check"
            # ★ 根因修复: 同一语义的「期望」在本结构里有 3 个表达位
            #   （check.expected / expect / description），翻转必须**同步全部**——
            #   否则执行端同时收到「应显示错误提示」与「无错误」两个互斥信号，
            #   语义判定必然摇摆 → 恒假 FAIL（实测 TC-REQ-011-I04：靶子已正确渲染
            #   '用户名或密码错误'，仍被判 FAIL）。期望语义必须单点表达、全位同源。
            entry["expect"] = _req_expect
            entry.pop("description", None)
            entry["_polarity_flipped"] = "no_error"
            _log.info(
                "[check_guard] 极性反转: no_error→semantic_check (case=%s, 需求期望=%r) "
                "—— 负向断言反转，保住需求'应被拒绝'的判定能力（防探针-only 假 PASS）",
                case.get("case_id", "?"), _req_expect[:40],
            )
            kept.append(entry)
            continue
        if ctype == "no_error" and _req_negative is False:
            _log.info(
                "[check_guard] 极性反转抑制: 需求原文未声明负向/字段约束义务 → 保留 "
                "no_error（'正确处理'即符合期望）(case=%s, 期望=%r)",
                case.get("case_id", "?"), _probe_text[-40:],
            )
        # ★ 契约修复(2026-09-27) F8 —— 断言与操作语义必须匹配（不止极性）。
        #   根因: 原判据只按"负向用例"放行 field_error_visible_check，未校验该用例
        #     是否具**表单提交**语义。实测 REQ-001「检索资产」的 B01/N01（动作是
        #     fill 查询框 + click 查询）也被注入 field_error_visible_check ——
        #     检索场景正常行为是返回空/全列表，不会弹字段级错误 → 结构性假 FAIL
        #     且被错误归因 PRODUCT_BUG。
        #   通用判据: 该断言的语义前提是"存在变更型提交"（且非只读检索型）。
        if ctype == "field_error_visible_check" and not (
            _expects_error and _is_mutation_case(case) and not _is_readonly_case(case)
        ):
            _log.info(
                "[check_guard] 语义过滤: 剔除 field_error_visible_check "
                "(case=%s expects_error=%s mutation=%s readonly=%s；需负向语义+变更型提交)",
                case.get("case_id", "?"),
                _expects_error,
                _is_mutation_case(case),
                _is_readonly_case(case),
            )
            continue
        # ★ 契约修复: 只读/检索型用例不得携带「行数增量」断言。
        #   根因: 只读操作不产生数据变更，行数 Δ 不是该需求的判定对象（正常行为不可能
        #   改变行数），生成端把它当"深断言"注入即结构性假 FAIL。基线已改为执行期
        #   采集，故不再能用 `_needs_baseline` 的"结构性不可得"理由剔除，须按操作性质判定。
        if (
            ctype in ("list_row_count_check", "list_row_count")
            and _is_readonly_case(case)
            and not _is_mutation_case(case)
            and _expected_delta_of(entry) is not None
        ):
            _log.info(
                "[check_guard] 语义过滤: 剔除 list_row_count_check 行数增量断言 "
                "(case=%s 只读/检索型，行数 Δ 非需求判定对象)",
                case.get("case_id", "?"),
            )
            continue
        # ★ 契约修复(2026-09-27) F9 —— 结构性不可判定的深断言必须在生成端剔除。
        #   根因: list_row_count_check(expected_delta) 依赖"前置基线行数"，基线由
        #     count_list_rows action 提供——该 action 不在契约内、执行端无消费分支，
        #     基线永不回填 → 该断言恒走"跳过"分支（旧渲染还会把它显示成 [PASS] 假绿）。
        #   原则: 生成端只能发射"执行端能给出判定"的断言。等价的可见后置条件
        #     （"列表中出现唯一值记录"）已由同一用例的 text_contains_check 覆盖，
        #     故剔除不丢覆盖，只去掉结构性死断言。
        if ctype == "list_row_count_check" and _needs_baseline(entry):
            _log.info(
                "[check_guard] 结构性剔除: list_row_count_check 依赖不可得基线 "
                "(case=%s，前置 count_list_rows 执行端无消费分支)",
                case.get("case_id", "?"),
            )
            continue
        if ctype == "text_contains_check":
            _needle = _needle_of(entry)
            _inner = entry.get("check") if isinstance(entry.get("check"), dict) else {}
            _op = str(_inner.get("operator") or entry.get("operator") or "")
            _prec = str(entry.get("precision") or _inner.get("precision") or "fuzzy")
            _sel = str(_inner.get("selector") or entry.get("selector") or "")
            # ★ 根因修复(2026-09-23): 语义期望 → 语义判定通道（semantic_check）。
            #   根因: 生成端把「需语义理解的期望」翻译成字面 needle 做 contains，
            #   页面用别的措辞表达同一语义时必然 0 匹配 → 假 FAIL（17 条实测）。
            #   保留机械的三种例外：
            #     ① not_contains（"不应出现 X"——缺席可机械判定）；
            #     ② exact+selector（字段值精确比较——就是要字面相等）；
            #     ③ **值级断言**：期望字面值正是本用例自己注入的输入值
            #        （如"列表中出现唯一值 '自动化记录xxx'"）——它是"要找这个值"，
            #        不是"要理解这句话"；转语义判定会把可确定的机械判定降级为
            #        运行期 AI 依赖 → 恒落非判定态（实测 TC-REQ-035/053-UX02）。
            if _op == "not_contains":
                kept.append(entry)
                continue
            if _prec == "exact" and _sel:
                kept.append(entry)
                continue
            if _needle.strip() and _is_value_level_expectation(case, _needle):
                kept.append(entry)
                continue
            if not _needle.strip():
                _log.info(
                    "[check_guard] 空期望 text_contains_check 剔除 (case=%s)",
                    case.get("case_id", "?"),
                )
                continue
            # 转换为语义断言：机械文本优先，未命中才交 AI 判定（见 _handle_semantic_check）
            _inner["type"] = "semantic_check"
            if "type" in entry:
                entry["type"] = "semantic_check"
            entry["_converted_from"] = "text_contains_check"
            _log.info(
                "[check_guard] text_contains→semantic_check (case=%s expect=%r) "
                "—— 语义期望改由 AI 判定，杜绝字面匹配假 FAIL",
                case.get("case_id", "?"), _needle[:40],
            )
        kept.append(entry)

    # ★ 根因修复(2026-09-28): 去重「错误/拒绝反馈」同语义断言——上游一次判定，
    #   下游只读不重推。根因: 负向用例常同时携带 field_error_visible_check（DOM
    #   活动错误条，最强确定性判定）+ 多个 semantic_check（期望黄金文本"商品名称
    #   已存在"、期望"拒绝提交"），三者是对同一"错误反馈"语义的下游重复推导：
    #   字段级错误条是瞬态的（校验后页面跳转即消失），事后语义重查必然失效
    #   （实测 REQ-001/002/004-I01: 引导层错误条检测 PASS，checks 层 semantic_check
    #   在最终页重查判 FAIL——两套断言自相矛盾，引擎自造假 FAIL）。
    #   通用规则: 用例已发射 field_error_visible_check 时，剔除所有期望"错误/拒绝"
    #   语义的 semantic_check（该判定已由 DOM 断言唯一承担）；非错误语义的
    #   semantic_check（防御成功/页面加载）不受影响。
    _has_field_error = any(
        isinstance(c, dict)
        and isinstance(c.get("check"), dict)
        and c["check"].get("type") == "field_error_visible_check"
        for c in kept
    )
    if _has_field_error:
        # 判据: field_error_visible_check 已确定性承担"校验/错误反馈"判定主题；
        # 同用例中任何**非接受语义**的 semantic_check 都是该主题的下游重复推导
        # （含需求原文错误文案"商品名称已存在"——它不含通用拒绝词、事后重查必失效）。
        # 仅"接受/成功"语义的 semantic_check（罕见，如复合期望）保留。
        _kept2: list[Any] = []
        for _c in kept:
            if (
                isinstance(_c, dict)
                and isinstance(_c.get("check"), dict)
                and _c["check"].get("type") == "semantic_check"
                and not expects_accept_feedback(str(_c["check"].get("expected") or ""))
            ):
                _log.info(
                    "[check_guard] 去重错误反馈语义: field_error_visible_check 已承担，"
                    "剔除同主题 semantic_check (case=%s expected=%r)",
                    case.get("case_id", "?"),
                    str(_c["check"].get("expected") or "")[:40],
                )
                continue
            _kept2.append(_c)
        kept = _kept2

    if len(kept) != len(checks):
        case["checks"] = kept

    # ★ 断言手段与期望性质对齐（架构约束「AI 前移到生成侧、运行期零 AI」）：
    #   语义断言的兜底通道是运行期 AI，而运行期零 AI ⇒ AI 兜底不可用 ⇒ 语义断言
    #   恒落"非判定态" SKIP（实测 18 条：断言真实可判定，引擎却拒判）。
    #   若期望的判定对象本就是**可确定性取证的 DOM 事实**（反馈条极性），必须落为
    #   确定性 check；极性判据与反馈条选择器均为单一事实源，下游只读不推。
    _checks_now = case.get("checks") or []
    if _checks_now:
        case["checks"] = _align_feedback_assertions(case, expects_error=_expects_error)
        # ★ 断言族派生：弱断言（仅 element_exists/no_error）只能证明"页面加载且无异常"，
        #   须升级为需求声明的可观察事实对应的断言族（访问控制/错误反馈/行数差分/行数规模）。
        align_requirement_observable_assertions(case, expects_error=_expects_error)

    # 轨道诚实化：按剩余 checks 实际覆盖的轨道收敛 required_tracks；
    # 并并入"因生成缺陷（非指令/未注册）被剔除"的轨道主张，交由执行层 RAG 兜底。
    _present = {
        str(c.get("track")) for c in kept
        if isinstance(c, dict) and c.get("track") in ("ui", "api", "db")
    } | _defect_tracks
    if _present:
        rt = case.get("required_tracks")
        if isinstance(rt, list) and rt:
            case["required_tracks"] = [t for t in rt if t in _present]
    # 每个操作步骤都必须携带断言（步骤级预期）——单点收口，判据见 step_assertions，
    # 生成端不得在别处再各自拼"逐步预期"，否则评审页会退回"只在末尾写整体预期"。
    from src.pipeline.utils.step_assertions import ensure_step_assertions

    ensure_step_assertions(case, step_key="test_steps")
    # ★ 运行期零 AI ⇒ AI 驱动探索（`_explore_task` + Agent 路径）在运行期不可执行：
    #   动作与断言齐备（策略1 确定性执行）的用例**不得**再携带探索计划 —— 否则执行端
    #   以 `_explore_task` 为分流闸门走 Agent 路径 → 引导层按探索计划的验证点重跑一遍
    #   动作（hint 与声明动作不同源）→ 定位失败 → 结构性 ERROR（实测 TC-REQ-039-E01：
    #   声明动作登录成功，探索验证点用另套 hint 重放 → "填充'用户名,账号,登录名'失败"）。
    if (case.get("actions") and case.get("checks") and case.get("_explore_task")):
        case.pop("_explore_task", None)
        case["_exploratory"] = False
        _log.info(
            "[check_guard] 移除探索计划（动作+断言齐备 → 确定性执行）: %s",
            case.get("case_id") or case.get("title") or "?",
        )
    # 反馈面适用性声明（幂等）：本函数是**落库边界**的通用清洗，任何生产者
    #   （规则/兜底/Excel 回填/外部导入）都经此处 → 在此处标注可保证
    #   `feedback_scope` 与最终落库动作链一致，不依赖某条上游路径是否记得调用。
    declare_feedback_scope(case)
    return case


def sanitize_intents(intents: list) -> list:
    """批量守卫入口：对即将落库的全部意图用例做检查项定稿清洗。"""
    # 模块约束语料缓存同批一致（本批需求镜像读一次，跨批不陈旧）
    clear_requirement_texts_cache()
    # ★ 导航目标解析必须覆盖**全部**生成路径：生成流水线的 `resolve_intents_batch`
    #   在探针/规则兜底分支产出用例**之前**执行过 → 这些用例的导航目标从未被解析
    #   （实测 TC-REQ-025-E01 nav 停在"审计"而非"资产" → 动作/断言全落错页面）。
    #   守卫是落库前最后一道，故在此重跑同一解析器（SSOT 复用，非另造）。
    with guard("sanitize_intents: resolve_intents_batch", log=_log, level=logging.WARNING):
        from src.core.config_loader import config as _nav_cfg

        from src.pipeline.step3._navigation_resolver import resolve_intents_batch

        resolve_intents_batch(intents, str(getattr(_nav_cfg, "project_name", "") or ""))
    # ★ 导航目标族内一致（同需求 → 同页面）：以兄弟用例的真实路径覆写页面名型导航
    align_nav_target_with_siblings(intents)
    dropped = 0
    dropped_actions = 0
    scanned = 0
    converted = 0
    flipped = 0
    pure_verification = 0
    triggered = 0
    scoped = 0
    overstated = 0
    kept_intents: list = []
    for it in intents:
        if isinstance(it, dict):
            scanned += 1
            # ★ 期望强度 ≤ 需求声明（唯一判据见 overstated_expectation_reason）：
            #   主张"须出现错误/拒绝反馈"而需求原文未声明该极性的用例，断言的是
            #   不存在的需求 → 不落库（不生成比需求更强的期望）。
            _why = overstated_expectation_reason(it)
            if _why:
                overstated += 1
                _log.warning(
                    "[check_guard] 不落库：期望强度高于需求声明 (case=%s) —— %s",
                    it.get("case_id") or it.get("title") or "?",
                    _why,
                )
                continue
            _before = len(it.get("checks") or [])
            _before_a = len(it.get("actions") or [])
            sanitize_case_checks(it)
            dropped += _before - len(it.get("checks") or [])
            dropped_actions += _before_a - len(it.get("actions") or [])
            converted += sum(
                1 for c in (it.get("checks") or [])
                if isinstance(c, dict) and c.get("_converted_from")
            )
            flipped += sum(
                1 for c in (it.get("checks") or [])
                if isinstance(c, dict) and c.get("_polarity_flipped")
            )
            # ★ 操作后果断言 → 补全前置触发（必须先于下方"纯验证免探索"判定：
            #   注入后动作含交互，`_is_pure_verification_case` 自然为假，用例被
            #   固定为确定性执行）。
            if ensure_post_action_triggers(it):
                triggered += 1
            # ★ 反馈面适用性声明：动作已定稿 → 按动作形态写入 `check.feedback_scope`
            #   （字段面/操作面）。执行端据此区分"页面无字段 → 诚实 N/A"与
            #   "操作已施加而反馈未出现 → 诚实 FAIL"，不再用页面形态猜用例意图。
            scoped += declare_feedback_scope(it)
            # ★ 提交前必填补全策略：上游一次性判定并落结构化字段 `form_fill_policy`
            #   （生产点唯一 = utils.form_fill_policy.declare_form_fill_policy），
            #   执行端只读；不在此处让执行端用自己拼的（可能为空的）输入重推极性。
            with guard("sanitize_intents: form_fill_policy", log=_log):
                from src.pipeline.utils.form_fill_policy import (
                    declare_form_fill_policy as _declare_fill,
                )

                _declare_fill(it)
            # ★ 纯验证用例免探索：断言确定可执行且无交互动作 → 无探索价值，
            #   走确定性执行器（否则 Agent 空转耗尽预算 → 结构性假 ERROR）。
            #   执行端以 `_explore_task` 是否存在作为分流闸门（见 handle_agent_intent），
            #   故必须一并移除，否则标记翻转不生效。
            if it.get("_exploratory") and _is_pure_verification_case(it):
                it["_exploratory"] = False
                it.pop("_explore_task", None)
                pure_verification += 1
        kept_intents.append(it)
    if len(kept_intents) != len(intents):
        intents[:] = kept_intents
    # VER print 证据：引擎 stdout 直出（run_logs 不一定收 WARNING 级 logger）
    print(
        f"[check_guard] 扫描 {scanned} 条意图, 剔除 {dropped} 个不可执行/"
        f"结构性不可判定 check, 剔除 {dropped_actions} 个契约外 action, "
        f"语义化改写 {converted} 个 text_contains→semantic_check, "
        f"极性反转 {flipped} 个 no_error→semantic_check, "
        f"纯验证用例免探索 {pure_verification} 条, "
        f"操作后果断言补全触发 {triggered} 条, "
        f"反馈面适用性声明 {scoped} 条, "
        f"期望强度超需求未落库 {overstated} 条, "
        f"可执行词表大小={len(_EXECUTABLE_CHECK_TYPES)}",
        flush=True,
    )
    if dropped:
        _log.warning(
            "[check_guard] 本批次共剔除 %d 个不可执行/极性错位 check（详见上方告警）",
            dropped,
        )
    return intents
