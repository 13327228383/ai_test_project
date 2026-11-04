# -*- coding: utf-8 -*-
"""「操作后果型」断言的前置触发动作 —— 全引擎**单一事实源**。

契约（为什么必须有本模块）:
  某些断言观察的对象**只可能在一次真实交互之后**出现：
    - `field_error_visible_check`：提交非法输入后，错误/校验反馈条才可见；
    - 期望"拒绝/错误反馈"的 `semantic_check` 同理。
  若用例的 actions 只有 `navigate` + `screenshot`（无任何交互），该断言
  **结构性不可满足** → 必然假 FAIL。实测 TC-REQ-025/026/027/028/029/037/049/050-I01：
  actions=[navigate,screenshot] 却断言"页面出现错误/校验提示"。

  根因不是某一条用例写错，而是"触发动作"这一语义被多处各自拼装（`_fallback_cases`
  探针分支内联一份、intent 生成端不发），补一处漏一处。故此处一次性收口：
  **唯一的触发序列构造器**，生成端（`_fallback_cases`）与定稿守卫（`_check_guard`）
  都只能 import 取用，不得再各写一套。

通用性: 入口/提交控件取**控件动词类别的通用候选**（跨系统/行业，非业务词）；
  非法值复用 `value_generator` 单一事实源；零业务硬编码。
"""

from __future__ import annotations

import logging
import re

_log = logging.getLogger(__name__)

# 录入入口 / 提交控件的通用候选（动词类别，跨系统/行业通用；非业务名词）
ENTRY_HINT_CREATE = "新增,新建,添加,创建"
ENTRY_HINT_EDIT = "编辑,修改,更新"
SUBMIT_HINT = "保存,提交,确定,确认"

# 断言类型 → 需要"前置触发交互"（观察操作后果，而非当前页面既有状态）。
#   新增类型时在此登记一次，生成端/守卫端同时生效。
POST_ACTION_CHECK_TYPES = frozenset({"field_error_visible_check"})

# 用例观察面（生成端一次性声明，下游只读）：observe_only = 断言只看页面既有状态，
#   下游不得注入数据变更类激励 —— 否则只读模板被塞入变更动作，同一 SUT 缺陷被重复计数。
INTERACTION_SCOPE_OBSERVE_ONLY = "observe_only"

# 用例前置数据需求（生成端一次性声明，下游只读）：existing_row = 该用例的激励对象
#   是"列表里已有的一条记录"（如删除/编辑行），执行前须保证至少存在一行。
DATA_PRECONDITION_EXISTING_ROW = "existing_row"

# ── 「行数差分」语义 → ±N（唯一实现）──────────────────────────────
# 差分断言的期望值只能从期望文本自身派生 —— 依据是**语言事实**（中文数词 + 增减方向），
# 不是业务词表；行数基线由执行期采集（`capture_list_row_baseline`），生成端不持有。
_ROW_DELTA_RE = re.compile(r"行数\s*(减|减少|少|加|增加|多)\s*([0-9]+|[一二两三四五六七八九十])")
_CN_NUM = {
    "一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
}


def row_count_delta_in_text(text: str) -> int | None:
    """期望文本是否表达「列表行数差分」→ ±N；无则 ``None``。"""
    _m = _ROW_DELTA_RE.search(str(text or ""))
    if not _m:
        return None
    _raw = _m.group(2)
    _n = int(_raw) if _raw.isdigit() else _CN_NUM.get(_raw, 0)
    if not _n:
        return None
    return -_n if _m.group(1) in ("减", "减少", "少") else _n


# 建立"已输入"前提的动作类型（跨系统/行业通用，非业务词）
_INPUT_ACTION_TYPES = frozenset(
    {"fill", "select_option", "check", "uncheck", "type_text", "set_date",
     "upload_file", "clear", "press_key", "paste"}
)

# 不改变页面状态的动作类型（仅观察/定位）：用例没有"可探索的内容"，
#   也不会产生任何"操作后果"。全引擎唯一定义处（守卫/触发两侧共用）。
OBSERVE_ONLY_ACTION_TYPES = frozenset(
    {"navigate", "goto", "screenshot", "wait", "wait_for", "assert", "verify"}
)

# 「往**可编辑字段**里录入值」的动作类型 —— 字段级校验反馈的产生前提。
#   从 `_INPUT_ACTION_TYPES` 派生（不另立词表），仅排除两类**不是字段录入**的动作：
#     · `upload_file`：文件校验反馈是**页面级**提示，其控件也不是可编辑文本字段
#        （执行器定位可编辑字段时明确排除 `type=file`）→ 若把它算作字段录入，
#        "上传非法文件"类用例会重新落回"页面无字段 → N/A"的假 SKIP；
#     · `press_key`：`_INPUT_ACTION_TYPES` 收录它是为了让"回车提交"建立触发前提，
#        它本身是**落定**动作而非取值动作。
_FIELD_ENTRY_ACTION_TYPES = _INPUT_ACTION_TYPES - {"upload_file", "press_key"}
_SUBMIT_HINT_TOKENS = tuple(x for x in SUBMIT_HINT.split(",") if x)


def field_rejection_scope(actions: list) -> str:
    """「期望被拒绝」的反馈面适用性 —— 由**最终动作链**决定的唯一判据。

    返回:
      - ``"field"``：用例的激励是往可编辑字段录入值 → 反馈面是**字段级校验提示**，
        页面无可编辑字段时该断言确实无从适用（N/A 诚实跳过）；
      - ``"operation"``：用例的激励是删除/上传/提交等**操作**（无字段录入）→
        反馈面是**页面级**提示条/弹窗，与"页面有无可编辑字段"无关，
        未出现即真实的"需求要求的反馈未出现"（FAIL，可证伪）。

    why 必须带此声明：执行器原判据只看"页面是否存在可编辑字段"——删除/导入/分类
      拦截等非表单流程终态页是列表页，恒无字段 → 断言一律 N/A → 用例被假 SKIP
      （实测 TC-REQ-039-E01/I01、041-E01/I01、063-I02 停在正常列表页而 SKIP）。
      "页面有无字段"回答的是**页面能力**，而上述用例的期望反馈是**操作后果**，
      两者是不同的问题；适用性必须由生成端在动作定稿后一次性声明，执行端只读。
    通用性: 只依赖动作类型（跨系统/行业/页面通用），零业务硬编码。
    """
    _acts = [a for a in (actions or []) if isinstance(a, dict)]
    if any(
        str(a.get("type") or a.get("action") or "").strip().lower()
        in _FIELD_ENTRY_ACTION_TYPES
        for a in _acts
    ):
        return "field"
    if any(
        str(a.get("type") or a.get("action") or "").strip().lower()
        not in OBSERVE_ONLY_ACTION_TYPES
        for a in _acts
    ):
        return "operation"
    # 无任何交互：无从施加激励，保持字段面（原"页面能力"判据在此仍成立）
    return "field"


def _is_submit_click(hint: str) -> bool:
    """点击目标是否为「提交/落定」控件 —— 唯一判据。

    save 族（保存/提交/确定/确认）出自 `SUBMIT_HINT`；**登录族**（登录/登陆/凭证/鉴权…）
    复用 `is_login_operation`（同一 SSOT）。此前只认 save 族 → 声明动作正确的登录用例
    被判"前提未建立"→ 守卫把动作序列**替换**成通用表单流程 → 用例的真实操作被销毁
    （实测 TC-REQ-039-E01：正确凭证登录被换成"新增资产→保存"→ 断言落到错误页面）。
    通用性: 「落定动作」= 提交/保存/登录等终止型动作，与业务无关。
    """
    _h = str(hint or "")
    if any(tok in _h for tok in _SUBMIT_HINT_TOKENS):
        return True
    try:
        from src.pipeline.step3._rule_extractor_keywords import is_login_operation

        return bool(is_login_operation(_h))
    except Exception:  # noqa: BLE001 —— 判据不可用时按非提交处理（fail-open 到原行为）
        return False


def action_sequence_triggers_outcome(actions: list) -> bool:
    """动作序列是否已建立「交互后果可发生」的前提 —— 全引擎唯一判据。

    前提 = **输入 + 提交**两步都发生（只有提交无输入 = 空提交；只有点击无提交 =
    从未施加激励）。判据唯一，生成端（`ensure_post_action_triggers`）据此决定是否
    补前置触发，执行端据此判断后果型断言是否可满足，杜绝两处各自判"有没有交互"。
    """
    _has_input = False
    for _a in actions or []:
        if not isinstance(_a, dict):
            continue
        _t = str(_a.get("type") or _a.get("action") or "").strip().lower()
        if _t in _INPUT_ACTION_TYPES:
            _has_input = True
        elif _t in ("submit", "press_key"):
            return _has_input
        elif _t == "click":
            _h = str(
                _a.get("target_hint") or _a.get("target") or _a.get("selector") or ""
            )
            if _is_submit_click(_h):
                return _has_input
    return False


# ── 「提交前置完整性」——正向提交必须满足表单必填/必勾选（单一事实源）──────
# 根因: 「输入 + 提交」这一**存在性**判据不足以支撑"操作后果型"断言 —— 用例的录入段
#   常有缺口（必填价格/库存、必勾选确认未被录入），真实表单的客户端/服务端校验会
#   拦截提交，于是提交在任何一帧都**未发生**，断言要求的成功反馈/落地记录结构性
#   不可能出现（实测 025-E01/I01、035/053/065/068-UX02：录入段缺必勾选 → 提交被拦截
#   → 期望"保存成功/列表出现唯一值"的断言恒不满足）。
#   完备性判据与录入段构造同源收口于本模块：录入段一律由字段规格 SSOT 产出/补齐，
#   调用方不得自拼（自拼就是"同一语义在多处各写一套"的漂移来源）。


def _is_submit_action(a: dict) -> bool:
    """该动作是否为「提交」（点击提交/落定控件 / press_key 回车提交）—— 判据同源。"""
    _t = str(a.get("type") or a.get("action") or "").strip().lower()
    if _t in ("submit", "press_key"):
        return True
    if _t != "click":
        return False
    return _is_submit_click(
        str(a.get("target_hint") or a.get("target") or a.get("selector") or "")
    )


def _key_in_hint(hint: str, key: str) -> bool:
    """hint 是否**以字段 key 定位**该字段（`name=<key>` / `[name='<key>']` / `#<key>`）。

    why: 用裸子串判 `key in hint` 会把 `name=price` 误判为覆盖了 key=`name`
      （因为 hint 里恒含 "name="）→ 归一/补齐错位（实测：价格字段被写成本用例的
      记录名唯一值）。必须按**键定位形态**判，不是子串。
    """
    _k = str(key or "")
    if not _k:
        return False
    return any(
        _p in hint
        for _p in (f"name={_k}", f"[name='{_k}']", f'[name="{_k}"]', f"#{_k}")
    )


def _field_covered(actions: list, field: dict) -> bool:
    """既有录入动作是否已覆盖该字段（按**标签或 key 的定位形态**判定）。

    既有动作的 target_hint 可能是 label 形态（"名称/姓名输入框"），也可能由本模块
    产出为 `name=<key>` 形态 —— 两种都要认，否则会把已录入字段重复录入一遍。
    """
    _label = str(field.get("_field_label") or "")
    _key = str(field.get("_field_key") or "")
    for a in actions or []:
        if not isinstance(a, dict) or str(a.get("type") or "") not in _INPUT_ACTION_TYPES:
            continue
        _h = str(a.get("target_hint") or a.get("target") or a.get("selector") or "")
        if _label and _label in _h:
            return True
        if _key_in_hint(_h, _key):
            return True
    return False


def _row_scoped_control_selector(
    control_texts: str, exclude_states: list = None, include_states: list = None
) -> str:
    """删除触发控件的**行作用域**选择器：限定命中行的状态。

    why: 正向删除用例若点到处于"禁止删除"状态的行（该状态由同模块需求声明），服务端
      会拒绝 → 行数不减 → 正向主张结构性落空（假 FAIL，实测 TC-REQ-028-I01）；
      负向删除用例（期望被拒绝）反之须**点到该受限状态行**才算施加了正确激励
      （否则只是"碰巧"点到受限行，SUT 数据一变即失效）。
    行选择器复用 `_LIST_ROW_SELECTOR`（唯一事实源）、控件动词复用 `control_texts`
      （`_RESTRICTED_ACTION_TEXTS` 动词→控件候选 SSOT）；受限状态值来自需求原文。
    通用性: 行/控件/状态均是跨系统/框架的通用形态，零业务名词硬编码。
    """
    from src.pipeline.step3._fallback_cases import _LIST_ROW_SELECTOR
    from src.pipeline.steps.step4.playwright_engine.selectors import _escape_css_str

    _excl = "".join(
        ':not(:has-text("%s"))' % _escape_css_str(str(_s))
        for _s in (exclude_states or [])
        if str(_s).strip()
    )
    _incl = "".join(
        ':has-text("%s")' % _escape_css_str(str(_s))
        for _s in (include_states or [])
        if str(_s).strip()
    )
    _ctrl = (
        ':is(button, a, [role="button"], input[type="submit"], input[type="button"])'
    )
    _parts: list[str] = []
    for _row in [s.strip() for s in str(_LIST_ROW_SELECTOR).split(",") if s.strip()]:
        for _verb in [s.strip() for s in str(control_texts).split(",") if s.strip()]:
            _parts.append(
                '%s%s%s %s:has-text("%s")'
                % (_row, _excl, _incl, _ctrl, _escape_css_str(_verb))
            )
    return ", ".join(_parts)


def align_delete_flow_shape(
    actions: list,
    operation: str = "",
    exclude_states: list | None = None,
    include_states: list | None = None,
) -> tuple[list, bool]:
    """删除型需求的动作形态 == `trigger_confirm`（SSOT `mutation_flow_kind`）。

    why: 删除型需求被套用了"新增表单"的录入段 + 保存提交 → **删除从未发生**，
      于是"列表行数减一 / 服务端拒绝提示"这两类后果断言全部落空
      （实测 TC-REQ-028-I01 用创建流程冒充删除、TC-REQ-061-P01 删除点击后接录入段）。
      删除操作不产生录入界面（确认框即可）——形态由**操作类型**决定（SSOT），
      不由模板习惯决定。
    保留：导航、删除触发点击、确认点击（in_dialog）、尾部截图；
      **剔除**：录入段（fill/check/select_option…）与非删除操作的提交点击。
    删除触发控件候选复用 `_RESTRICTED_ACTION_TEXTS["删除"]`（动词→控件映射 SSOT）。
    `exclude_states`: 正向删除时须排除的**受限状态行**（值来自同模块需求原文，
      见 `forbidden_states_in_texts`）—— 附到触发点击的 `selector` 上限定命中行；
    `include_states`: 负向删除（期望被拒绝）须命中的受限状态行（同一取值来源）。
    """
    _acts = [a for a in (actions or []) if isinstance(a, dict)]
    try:
        from src.pipeline.step3._rule_extractor_buttons import (
            mutation_flow_kind as _flow_kind,
        )

        if _flow_kind(operation, True) != "trigger_confirm":
            return _acts, False
    except Exception:  # noqa: BLE001 —— 形态判据不可用 → 不动（保守）
        return _acts, False
    from src.pipeline.step3._fallback_cases import _RESTRICTED_ACTION_TEXTS

    _del_hint = _RESTRICTED_ACTION_TEXTS.get("删除") or "删除"
    _del_tokens = tuple(t for t in str(_del_hint).split(",") if t)
    _sel = (
        _row_scoped_control_selector(
            _del_hint, exclude_states=exclude_states, include_states=include_states
        )
        if (exclude_states or include_states)
        else ""
    )
    # 形态归一（剔除录入段/提交）只在"删除型需求被套用创建提交流程"（确有提交段）时进行；
    # 行作用域（`selector`）则对**任何**删除触发点击都生效（含形态已正确者）。
    _masquerade = any(_is_submit_action(a) for a in _acts)
    _out: list = []
    _has_trigger = False
    for a in _acts:
        _t = str(a.get("type") or "").strip().lower()
        if _t in ("navigate", "goto"):
            _out.append(a)
            continue
        if _t in ("screenshot", "wait", "wait_for"):
            continue
        if _t == "click":
            _h = str(a.get("target_hint") or a.get("target") or "")
            if any(t in _h for t in _del_tokens) and not _has_trigger:
                _out.append({**a, "selector": _sel} if _sel else a)
                _has_trigger = True
                continue
            if a.get("in_dialog") and _has_trigger:
                _out.append(a)  # 触发之后的确认框内点击（提交前不得残留其它点击）
                continue
            if _masquerade:
                continue  # 冒充删除的入口/提交点击 → 剔除
            _out.append(a)
            continue
        if _masquerade:
            continue  # 录入段/其它提交（编辑型冒充）→ 删除操作不需要
        _out.append(a)
    if not _has_trigger:
        # 形态由**操作类型**决定，不由"用例里已有什么"决定：删除型需求的激励就是
        # "点删除"。原实现只在用例已带提交段（masquerade）时重建动作，纯探针/探索型
        # 用例（仅 navigate+screenshot）因此完全没有交互 → 删除从未发生，后果断言
        # 结构性落空（实测 TC-REQ-061-E01/I01）。此处用**同一编排实现**
        # （build_interaction_actions）补出交互序列，而非就地手写点击。
        from src.pipeline.step3._rule_extractor_buttons import (
            build_interaction_actions as _build_inter,
        )

        for _a in _build_inter(operation, None, _del_hint):
            if _sel and "selector" not in _a:
                _a["selector"] = _sel
            _out.append(_a)
    _out.extend(
        a
        for a in _acts
        if str(a.get("type") or "") in ("screenshot", "wait", "wait_for")
    )
    if _out == _acts:
        return _acts, False
    _log.info(
        "[trigger] 删除型形态归一（trigger_confirm）: 剔除录入段/提交，保留删除触发"
        "（排除受限状态=%s；命中受限状态=%s）",
        exclude_states or "-",
        include_states or "-",
    )
    return _out, True


def ensure_form_entry(actions: list, operation: str = "") -> tuple[list, bool]:
    """提交流程的入口控件必须是**录入入口**（触发型控件）—— 否则以创建入口候选覆写。

    why: 动作形态串位 —— 用例同时声明了录入段与提交，却把"打开录入界面"的那一次点击
      落在非录入控件上（实测 TC-REQ-065/068-UX02：入口是"搜索"）→ 录入控件在列表页
      不存在（定位到搜索框）→ 提交从未发生 → 期望的成功反馈/落地记录结构性不出现。
    判据（全部复用 SSOT）: 流程确有录入段 + 提交，且录入段之前的最近一次点击的操作
      类别 ∉ 触发型类别（`_TRIGGER_CATEGORIES`）→ 覆写为创建入口候选（`ENTRY_HINT_CREATE`）。
    """
    _acts = [a for a in (actions or []) if isinstance(a, dict)]
    _first_input = next(
        (
            i
            for i, a in enumerate(_acts)
            if str(a.get("type") or "") in _INPUT_ACTION_TYPES
        ),
        None,
    )
    if _first_input is None or not any(_is_submit_action(a) for a in _acts):
        return list(actions or []), False
    _entry_idx = next(
        (
            i
            for i in range(_first_input - 1, -1, -1)
            if str(_acts[i].get("type") or "") == "click"
        ),
        None,
    )
    if _entry_idx is None:
        return list(actions or []), False
    try:
        from src.pipeline.step3._rule_extractor_buttons import (
            _TRIGGER_CATEGORIES,
            classify_operation_category as _category,
        )

        _cat = _category(
            str(_acts[_entry_idx].get("target_hint") or _acts[_entry_idx].get("target") or "")
        )
    except Exception:  # noqa: BLE001 —— 判据不可用 → 不动（保守）
        return list(actions or []), False
    if _cat in _TRIGGER_CATEGORIES:
        return list(actions or []), False
    _out = list(actions or [])
    _out[_entry_idx] = {
        **_acts[_entry_idx],
        "target_hint": ENTRY_HINT_CREATE,
        "in_dialog": False,
    }
    _log.info(
        "[trigger] 提交流程入口归一为录入入口: %r → %r (原类别=%r)",
        str(_acts[_entry_idx].get("target_hint") or "")[:20],
        ENTRY_HINT_CREATE,
        _cat,
    )
    return _out, True


def complete_submit_preconditions(
    actions: list,
    operation: str = "",
    *,
    module: str = "",
    project: str = "",
    unique_value: str = "",
    expectation_texts=(),
) -> tuple[list, list]:
    """补齐「提交」前缺失的动作。返回 ``(新动作序列, 实际补齐的动作)``。

    补齐两类（均为"提交前置"的必要条件）：
      ① 缺失的必填/必勾选录入（字段规格 SSOT 产出；如价格/库存/信息确认勾选）；
      ② 记录名类字段的**唯一值形态归一** —— 唯一值真值的唯一生产者是运行期
         （`step4_executor._materialize_run_unique_values` 按 (run, case, field) 物化），
         生成端只产 token 形态。静态字面值跨 run 复用会撞 SUT 唯一约束（如"名称已存在"）
         → 提交被拒 → 期望反馈结构性不出现。仅归一"值未被任何断言期望引用"的字段
         （被引用即与断言同源，动它会制造 fill/check 分裂）。
    只补**缺失**项（已按标签/key 覆盖的字段不重复录入），插入到提交动作之前；
    需求未声明字段规格 → 原样返回（诚实降级，不臆造字段）。
    """
    _acts = [a for a in (actions or []) if isinstance(a, dict)]
    # 用例自己声明的录入控件 hint（表单归属的硬证据）—— 字段规格关联的第二判据
    _hints = tuple(
        str(a.get("target_hint") or a.get("target") or a.get("selector") or "")
        for a in _acts
        if str(a.get("type") or "") in _INPUT_ACTION_TYPES
    )
    try:
        from src.pipeline.step3._rule_extractor_fields import (
            build_form_data_actions as _build_form_data,
        )

        _fills = _build_form_data(
            operation,
            module=module,
            unique_value=unique_value,
            project=project,
            hints=_hints,
        )
    except Exception:  # noqa: BLE001 —— 规格不可读 → 原样返回，不阻断
        return _acts, []
    _missing = [f for f in _fills if not _field_covered(_acts, f)]
    # ★ 归属门槛：提交前置只针对**已经在本表单录入过**的流程 —— 既有录入段已覆盖该
    #   规格集中的至少一个字段。否则字段规格与用例表单是两张表单（需求镜像的
    #   对象词关联本就宽松，如登录需求会关联到资产表单的"必勾选确认"），补齐会把
    #   别的表单的字段塞进当前流程（实测 TC-REQ-005-E01 登录流程被塞入
    #   `check name=confirmed`）→ 定位失败 → 引擎自造动作缺口。
    if not any(_field_covered(_acts, f) for f in _fills):
        return _acts, []
    _norm = _normalize_name_fills(_acts, _fills, unique_value, expectation_texts)
    if not _missing and _norm == _acts:
        return _acts, []
    _out: list = []
    _inserted = False
    for a in _norm:
        if not _inserted and _is_submit_action(a):
            _out.extend(_missing)
            _inserted = True
        _out.append(a)
    if not _inserted:
        # 无提交动作 → 不是"提交前置"场景，不注入（避免把只读探针变成提交流程）
        return _acts, []
    return _out, _missing


def _normalize_name_fills(
    actions: list, fills: list, unique_value: str, expectation_texts=()
) -> list:
    """把记录名类字段的既有静态录入值归一为**唯一值形态**（未与断言同源的才动）。

    `fills` 中值等于 `unique_value` 的条目即字段规格 SSOT 判定的"名称类字段"
    （`build_form_data_actions` 仅对名称类字段使用 unique_value）——判据同源，不另立词表。
    """
    if not unique_value:
        return list(actions)
    _name_fills = [f for f in fills if str(f.get("value") or "") == unique_value]
    if not _name_fills:
        return list(actions)
    _expect = {
        str(t or "").strip() for t in (expectation_texts or []) if str(t or "").strip()
    }
    _out: list = []
    for a in actions:
        if not isinstance(a, dict):
            _out.append(a)
            continue
        _v = str(a.get("value") or "")
        if (
            _v
            and _v not in _expect
            and str(a.get("type") or "") in _INPUT_ACTION_TYPES
            and any(_field_covered([a], f) for f in _name_fills)
        ):
            _out.append({**a, "value": unique_value, "text": unique_value})
            continue
        _out.append(a)
    return _out


# 根因（2026-10-15 实测）：守卫原先只按上方 POST_ACTION_CHECK_TYPES + "断言含拒绝语义"
#   两个条件决定是否补前置触发 → 覆盖面过窄。实测 606 条中 **219 条**用例断言要求
#   "登出后会话清除 / 搜索后分页联动 / 切换页码 / 权限受限"等**交互后果**，
#   而 actions 只有 navigate+screenshot → 前提从未建立 → 结构性不可满足 → 必然假 FAIL，
#   且被 AI 归因 PRODUCT_BUG（假 Bug）。
# 修法：不再扩充"类型白名单"（那是补丁：新增断言类型就得再补一次），
#   改为按**断言自身的性质**判定——它观察的是"交互后果"还是"页面既有状态"。
#   下两张表即该性质的唯一定义处，生成端与守卫端共同 import 取用。

# ① 天然只读：这些断言观察的就是"到达该页后的即时状态"，**零交互才正确**。
#   若给它们补交互反而是错误（例：LCP 量的是首屏加载，点击后测量值失真）。
_NATURALLY_READONLY_CHECK_TYPES = frozenset(
    {
        "performance_lcp_check",
        "performance_fcp_check",
        "page_load_time",
        "viewport_overflow_check",
        "loading_complete_check",
        "a11y_check",
        "accessibility_check",
        "security_check",
        "visual_check",
        "visual_review",
        "console_error",
        "no_console_error",
        "no_error",
        "dialog",
        # 访问控制断言的可达前提是「身份 + 入口导航」，不是表单交互；其文本常含
        # "重定向/拒绝"，会被下方交互后果词误命中而补出无关的表单提交动作。
        "access_control_check",
    }
)

# ② 交互后果词：断言文本命中即说明它观察的对象**只可能在一次交互之后**出现。
#   取自需求书写范式的通用动词/结果词，跨系统/行业通用，非业务名词。
#   —— 与 _check_guard 的 `expects_error_feedback`（拒绝语义）互补：
#      此处覆盖的是**正向交互后果**（登出/搜索/分页/切换/提交成功…），
#      那一个覆盖**负向拒绝后果**。两者同为"后果"，合并判真。
_POST_INTERACTION_OUTCOME_KEYWORDS = (
    "登出",
    "退出登录",
    "登入成功",
    "登录后",
    "搜索后",
    "筛选后",
    "查询后",
    "分页",
    "翻页",
    "下一页",
    "上一页",
    "页码",
    "切换",
    "已删除",
    "删除后",
    "删除成功",
    "保存后",
    "保存成功",
    "提交后",
    "提交成功",
    "新增后",
    "创建后",
    "编辑后",
    "修改后",
    "更新后",
    "上传后",
    "导入后",
    "导出后",
    "行数减一",
    "行数加一",
    "列表中出现",
    "列表中不再",
    "状态变为",
    "弹出",
    "弹窗",
    "提示框",
    "跳转",
    "重定向",
    "刷新后",
    "排序后",
    "勾选后",
)


def check_requires_interaction(check_type: str, expected_text: str = "") -> bool:
    """判定「该断言是否需要一次前置交互才可能被满足」——全引擎唯一判据。

    :param check_type: 归一后的 check 类型（可由 `_check_type_of` 得到）
    :param expected_text: 该断言的期望文本（`check.expected` / `expect`）

    返回 True = 该断言观察交互后果，若 actions 无交互则结构性不可满足，需补前置触发。
    """
    _t = str(check_type or "").strip()
    if _t in _NATURALLY_READONLY_CHECK_TYPES:
        return False
    if _t in POST_ACTION_CHECK_TYPES:
        return True
    _txt = str(expected_text or "")
    if any(k in _txt for k in _POST_INTERACTION_OUTCOME_KEYWORDS):
        return True
    # 拒绝语义（复用同源判据，不另造词表）
    try:
        from src.pipeline.step3._rule_extractor_keywords import expects_error_feedback

        if expects_error_feedback(_txt):
            return True
    except Exception:  # noqa: BLE001 —— 判据不可用时不阻断生成（fail-open）
        pass
    return False


def build_field_validation_trigger(
    entry_hint: str = "",
    operation: str = "",
    expected: str = "",
    *,
    negate: bool = True,
    module: str = "",
    project: str = "",
    unique_value: str = "",
) -> list[dict]:
    """构造「字段校验 / 拒绝反馈」场景的前置触发动作（**不含** navigate/screenshot）。

    序列:  click 录入入口 → 录入字段 → click 提交
    语义:  使系统对该输入产生可观察后果，让 `field_error_visible_check` / 期望
           错误反馈 / 期望成功的断言**真实可发生**。
    `negate=True` 填非法值（触发校验拒绝）；`negate=False` 填合规值（正向提交）。

    ★ 录入字段来自**字段规格 SSOT**（`_rule_extractor_fields.build_form_data_actions`）：
      根因: 本函数原实现硬编码单条 `fill 名称/姓名输入框`，而真实表单的必填项
        （价格/库存/必勾选确认…）从未被满足 → 客户端校验拦截 → 提交在任何一帧
        都未发生 → 断言要求的"成功/拒绝反馈"结构性不可能出现 → 假 FAIL / 无结论。
      规格不可得（需求未声明字段规格）时降级为单字段 fill：诚实降级，不臆造字段。
    入口按需求操作语义取（编辑需求不得点"新增"），判据复用 `is_edit_operation`
      —— 与场景分类同源，不在本函数另推一次。
    调用方职责: 在首部补 navigate、尾部补 screenshot（本函数只产出"中间交互"）。
    """
    _blob = f"{operation}{expected}"
    # 录入入口：显式传入优先；否则按需求操作语义（编辑 vs 新增）派生
    _entry = entry_hint
    if not _entry:
        try:
            from src.pipeline.step3._rule_extractor_keywords import (
                is_edit_operation as _is_edit,
            )

            _entry = (
                ENTRY_HINT_EDIT
                if _is_edit(operation, operation, expected)
                else ENTRY_HINT_CREATE
            )
        except Exception:  # noqa: BLE001 —— 判据不可用时用通用创建入口，不阻断生成
            _entry = ENTRY_HINT_CREATE
    _entry_action = {"type": "click", "target_hint": _entry, "in_dialog": False}
    _submit_action = {"type": "click", "target_hint": SUBMIT_HINT, "in_dialog": True}

    if not negate:
        # 正向提交：录入段必须**完整**（必填/必勾选），否则提交被前置校验拦截
        #   → 后果结构性不发生 → 断言恒不满足。完备性由本模块单点实现补齐。
        _completed, _added = complete_submit_preconditions(
            [_entry_action, _submit_action],
            operation,
            module=module,
            project=project,
            unique_value=unique_value,
        )
        if _added:
            return _completed

    # 负向（非法值触发校验拦截）/ 字段规格不可得（降级）：单字段录入即可
    _ftype = (
        "integer"
        if any(k in _blob for k in ("数量", "金额", "价格", "库存"))
        else "string"
    )
    try:
        from src.pipeline.step3.value_generator import (
            generate_invalid_value_from_type as _gen_bad,
            generate_value_from_type as _gen_ok,
        )

        _val = _gen_bad(_ftype, operation) if negate else _gen_ok(_ftype, "名称")
    except Exception:  # noqa: BLE001 —— 取值失败退化为通用值，不阻断生成
        _val = "!@#" if negate else "测试名称"
    return [
        _entry_action,
        {"type": "fill", "target_hint": "名称/姓名输入框", "value": _val},
        _submit_action,
    ]
