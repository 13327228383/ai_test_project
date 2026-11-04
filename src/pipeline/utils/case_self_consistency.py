# -*- coding: utf-8 -*-
"""用例自洽契约（单一事实源）—— 判定「用例的断言能否由其自身的动作达成」。

★ 实测背景（fullcov_sut / run `fullcov_sut__v20260928_154349`，414 条，非推测）：
  115 条用例的生成端自述 `scenario_type ∈ {error_path, boundary}`
  （引擎原文："输入非法值（空字段、超长文本、特殊字符），验证表单校验错误提示正确显示"），
  但 `case_actions_json` 只有 `navigate` + `screenshot` —— **零状态变更动作**。
  这 115 条的判定两个方向都站不住：
    · 80 条记 PASS（假 PASS）：`TC-REQ-020-I02`「viewer执行资产写操作-异常查看」
      从未尝试写操作，断言仅 `element_exists`(页面上找到 1 个任意元素) +
      `no_error`(页面上没出现报错) ⇒ 判 PASS、置信度 0.95。对一条"期望被拒绝"的
      用例，`no_error` 通过恰恰是**反向信号**，本不该计为通过。
    · 35 条记 FAIL（假 Bug）：`TC-REQ-040-I02`「登录失败需提示用户名或密码错误」
      从未提交登录，断言 `element_exists`(错误反馈条) 落空 ⇒ 记
      `failure_category=PRODUCT_BUG`，把引擎的生成缺口写成产品缺陷。
  同一根因的两种表现：**用例声明了一次操作，动作里却没有操作**。

★ 定位（本模块只判定，不修补；修补见调用方）：
  取 pytest 的「collection / setup 阶段」与 Playwright 的「actionability」两条成熟
  约定的最小交集 —— **测试只有真正执行了它所声明的前置操作后，其断言结论才可信；
  前置无法达成时，结论是「未测到」，既不是产品缺陷，也不是通过。**
  · pytest：无法完成 setup 的测试是 error/skip，从不因此记 failure；
  · Playwright：动作前先校验可操作性，不因"够不着"而断言被测系统有缺陷。

★ 判据（全部读**结构化字段**，零业务词表，不新增选择器/关键词）：
  · 动作是否施加状态变更 —— 由 `action_contract` 的动作白名单派生（同源，不另立词表）；
  · 用例是否自述「先操作、后观察」—— 任一成立即算：
      a) `scenario_type ∈ OPERATION_SCENARIOS`（生成端对"非法/边界输入后观察反应"的自述）
      b) checks 含「只有操作后才可能出现」的观察型断言（POST_OPERATION_CHECK_TYPES）
"""

from src.pipeline.step3._trigger_actions import INTERACTION_SCOPE_OBSERVE_ONLY
from src.pipeline.steps.step4.action_contract import CANONICAL_ACTIONS

# ── 只取景、不改变 SUT 状态的动作 ──
#   依据：它们不向被测系统提交新输入/新请求，效果是"把页面摆到镜头里"，
#   而不是"改变系统状态"。故不构成"用例声明的那次操作"。
OBSERVATION_ONLY_ACTIONS = frozenset(
    {
        "navigate",
        "wait",
        "verify",
        "focus_check",
        "screenshot",
        "scroll",
        "scroll_wheel",
        "wheel",
    }
)

# ── 只有"先发生一次操作"才可能观察到的断言 ──
#   ★ 刻意**不含**下列三类，纳入即误伤真用例（已用实测回放验证）：
#     · element_exists / text_contains_check / semantic_check
#       —— 在"页面静态事实"上同样合法（"访问未登录页应跳登录页"只需导航）。
#     · api_call_count_check
#       —— API 轨由执行器自身发起请求，不依赖用例动作（实测 8 条 api_verify PASS 会被误判）。
#     · access_control_check
#       —— 访问控制探针的执行器会自行尝试受限操作（实测 6 条 PASS / 2 条 SKIP 会被误判）。
POST_OPERATION_CHECK_TYPES = frozenset(
    {
        "field_error_visible_check",  # 提交被拒之后才出现的错误反馈条
        "list_row_count_check",       # 增删之后才变化的数据行数
        "db_field_value_check",       # 写操作之后才变化的库字段值
        "db_table_accessed_check",
        "toast_visible_check",
        "dialog_check",
        "download_check",
        "storage_check",
        "state_check",
        "refresh_check",
        "pagination_check",
    }
)

# ── 生成端自述"先施加操作、再观察反应"的场景类型（结构化字段，唯一事实源）──
OPERATION_SCENARIOS = frozenset({"error_path", "boundary"})

# 判定结果
SELF_CONSISTENT = "SELF_CONSISTENT"
ASSERTION_UNREACHABLE = "ASSERTION_UNREACHABLE"


def action_types(actions) -> list:
    """归一化动作列表为 action 名列表（兼容 dict / str / {"type"|"action"}）。"""
    out: list = []
    for a in actions or []:
        if isinstance(a, dict):
            t = a.get("type") or a.get("action")
            if t:
                out.append(str(t).strip())
        elif isinstance(a, str) and a.strip():
            out.append(a.strip())
    return out


def check_types(checks) -> list:
    """归一化 checks 为 check 类型名列表（兼容 {"check":{"type":..}} 与扁平行）。"""
    out: list = []
    for c in checks or []:
        if isinstance(c, dict):
            inner = c.get("check") if isinstance(c.get("check"), dict) else c
            t = inner.get("type") or c.get("type")
            if t:
                out.append(str(t).strip())
        elif isinstance(c, str) and c.strip():
            out.append(c.strip())
    return out


def performs_state_change(actions) -> bool:
    """用例动作里是否存在"向 SUT 施加操作"的动作（非仅取景）。"""
    return any(
        t in CANONICAL_ACTIONS and t not in OBSERVATION_ONLY_ACTIONS
        for t in action_types(actions)
    )


def declares_operation(scenario_type, checks, interaction_scope: str = "") -> bool:
    """用例的**断言**是否要求"先发生一次操作"才能成立。

    ★ 判据只看 checks，**不看 scenario_type**（2026-10-14 收窄）。
    根因：`scenario_type ∈ {error_path, boundary}` 是生成端的**场景叙述**，不是断言
      要求 —— 实测 418 条 fullcov_sut 用例中，107 条标了 boundary/error_path，
      但其断言（折取式模板"显示校验提示**或正确处理**"）落在静态页事实上：
      "主体渲染 + 无致命错误" 正是"正确处理"分支，**导航即可达成、且确为真实验证**。
      若用 scenario_type 触发，这 107 条会被判"断言不可达"→ 误记 SKIP，
      把真实通过抹成未测到（改判不是修复）。
    收窄后：只有当**断言本身**要求操作后状态时（如 field_error_visible_check /
      行数变化 / 库值变化 / toast / dialog / 分页…），才要求用例自带操作动作。
      这样既不放过"声明操作却零动作"的真缺陷，也不误伤静态断言的可达用例。

    ★ `interaction_scope` 优先于类型判据：生成端已一次性声明观察面时，下游只读该字段。
      仅按类型判会把同一类型的不同形态一律当"操作后果"—— `list_row_count_check`
      绝对值形（`expected_count`，"列表至少渲染 1 行"）是静态页事实，差分形
      （`expected_delta`）才是操作后果；只读模板一旦被误判，本应判定的用例被记 SKIP。
    """
    if str(interaction_scope or "").strip() == INTERACTION_SCOPE_OBSERVE_ONLY:
        return False
    return any(t in POST_OPERATION_CHECK_TYPES for t in check_types(checks))


def assess(actions, checks, scenario_type: str = "", interaction_scope: str = "") -> str:
    """判定用例是否自洽。

    返回 ``SELF_CONSISTENT`` 或 ``ASSERTION_UNREACHABLE``。
    仅当"用例自述要先做一次操作，动作里却没有任何状态变更动作"时判 UNREACHABLE
    —— 高精度优先：只要用例施加过任一操作，一律放行由既有判定链负责
    （绝不掩盖真实产品缺陷）。
    """
    if not declares_operation(scenario_type, checks, interaction_scope):
        return SELF_CONSISTENT
    if performs_state_change(actions):
        return SELF_CONSISTENT
    return ASSERTION_UNREACHABLE


def unreachable_reason(scenario_type: str = "", checks=None, actions=None) -> str:
    """给出 UNREACHABLE 的可读归因（写入 failure_reason，供审计与报告）。"""
    obs = sorted(
        {t for t in action_types(actions) if t in OBSERVATION_ONLY_ACTIONS}
    ) or ["(无动作)"]
    why = []
    if str(scenario_type or "").strip().lower() in OPERATION_SCENARIOS:
        why.append("场景自述=%s" % scenario_type)
    hit = [t for t in check_types(checks) if t in POST_OPERATION_CHECK_TYPES]
    if hit:
        why.append("观察型断言=%s" % ",".join(hit))
    return (
        "用例自述需先执行一次操作再观察结果（%s），但其动作只有 %s，"
        "不含任何状态变更动作 → 断言前置未满足，无法据此判定产品" % (
            "; ".join(why) or "未声明",
            "+".join(obs),
        )
    )
