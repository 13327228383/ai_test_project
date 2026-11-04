# -*- coding: utf-8 -*-
"""断言判定共享谓词（单点事实源）—— 区分「产品不满足」与「引擎够不着/不可评估」。

★ 背景：
  ModSystem 闭环 round5 实测 **60 条 FAIL 全部被判 PRODUCT_BUG**，其中至少 19 条
  的失败项**全部是引擎自身不可评估**的检查 —— 这是批量制造假 Bug（假 FAIL）。

  三条独立证据（executions 表实取，非推测）：
    ① `TC-REQ-00X-SEC01`（9 条）：唯一失败轨 = API，
       evidence=`声明了API校验点但无API调用发生(预期调用未发生)`；
       同行 `details.evidence.network_logs` **只有 `GET /`、`GET /products` 两条文档请求、
       零 XHR** → 靶子是纯 SSR，引擎却给它声明了 API 断言 ⇒ 断言**不可评估**，非产品缺陷。
    ② `TC-REQ-006/007-V01`（2 条）：`element_exists:✗(查询异常: Locator.count:
       Unexpected token ":" while parsing css select)` —— 选择器含伪元素 `::before`
       （见 `step3/_fallback_cases.py` 必填标记选择器），`querySelectorAll` 不接受伪元素，
       **该检查在任何页面上都必然抛异常**，与产品行为无关。
    ③ `TC-REQ-006-B01/N01/PF01/PF02`（8 条）：`db_table_accessed_check:✗(未知检查类型…)`、
       `performance_lcp_check:✗(未知检查类型…)`、`DB: AI生成SQL失败: 本地模型 JSON 校验失败`
       —— 全是引擎侧无处理器 / 执行器异常。

★ 为什么此前会误判（根因层级，非表面）：
  D8 引入的判据第 1 条是 `"结构化校验失败" in error → 升格 PRODUCT_BUG`，
  这是**无差别升格**：结构化校验失败的原因既可能是"断言执行了、产品不满足"，
  也可能是"引擎根本没能评估这条断言"。前者才是产品缺陷，后者是引擎缺口。
  无差别升格 → round5 把 19 条引擎缺口一并写成产品缺陷。

★ 现判据（判定语义**读结构化结果**，零业务硬编码）：
  把每条**正在失败**的检查归入三类：
    - VERDICT（产品不满足）：`_track_protocol.is_verdict` 为真且 passed=False；
    - NON_VERDICT（非判定态）：`is_verdict` 为假（⊘ N/A 跳过 / skipped / 未验证）；
    - GAP（引擎不可评估）：含引擎不可评估标记（无处理器 / 执行器异常 / 断言不适用）。
  导出三个谓词：
    - `has_executed_assertion_verdict()`  —— 存在 VERDICT ⇒ 可判 PRODUCT_BUG/FAIL；
    - `has_only_engine_gap_verdict()`    —— 无 VERDICT 且存在 GAP ⇒ SKIP(ENGINE_COVERAGE_GAP)；
    - `has_no_negative_verdict()`        —— 无 VERDICT 但有 NON_VERDICT ⇒ SKIP(ENGINE_COVERAGE_GAP)，
      既不记 FAIL（防假 Bug）也不记 PASS（防假绿）。

★ 为什么：本模块原只看 evidence 文本里的类型名子串来重推判定，与 `_track_protocol`
  的结构化判定形成两套判据，会把引擎自述"不适用"的 ⊘ 判成"已执行且给出否定结论"
  → 假 PRODUCT_BUG。现判定一律以结构化结果为准，文本仅作兜底。
"""

import re

# 具名断言族（文本投影）——权威定义在 `_track_protocol.PRODUCT_DEFECT_CHECK_MARKERS`，
# 此处只引用不重定义：结构化谓词与文本兜底必须是同一条语义。
def _product_defect_markers() -> tuple:
    """产品缺陷可归因的具名断言族（单一事实源，仅 Agent 文本兜底路径使用）。"""
    from src.pipeline.steps.step4.verification._track_protocol import (
        PRODUCT_DEFECT_CHECK_MARKERS,
    )

    return PRODUCT_DEFECT_CHECK_MARKERS


# 动作/定位失败（引擎能力缺口）——不得据此判定产品缺陷
ACTION_GAP_MARKERS = (
    "未找到元素", "未找到按钮", "未找到输入框", "未找到字段", "点击失败",
    "填充失败", "字段名差异", "无法定位", "定位失败", "元素不可见",
)

# ★ D18：引擎"不可评估"标记 —— 命中即代表失败原因在引擎侧，不得升格为产品缺陷
#
# 来源逐条对照实测证据：
#   "未知检查类型"/"无有效断言"     → verification/_track_protocol.py:244 UNSUPPORTED_CHECK
#   "执行器异常"                    → 同文件 EXECUTOR_EXCEPTION
#   "查询异常"/"Unexpected token"   → verification/_db_ui.py 各 handler except 分支
#                                    （选择器非法/伪元素等，见 D21）
#   "声明了API校验点但无API调用"    → 已由 D20 拆分为两种：通配探针记
#                                    "API断言不适用"（下方标记）；声明了具体端点
#                                    却未调用仍是 FAIL（**刻意不列入**，防放过真漏调用）
#   "AI生成SQL失败"/"JSON 校验失败" → DB 轨 AI 生成 SQL 的解析失败（引擎侧）
#   "不可评估"/"结论不可用"         → 引擎自述无法给出结论
ENGINE_INCAPABILITY_MARKERS = (
    "未知检查类型",
    "无有效断言",
    "结论不可用",
    "UNSUPPORTED_CHECK",
    "执行器异常",
    "EXECUTOR_EXCEPTION",
    "查询异常",
    "Unexpected token",
    "while parsing css",
    # ★ D20 精确短语：只认"通配探针不可评估"，不认"声明了具体端点但没调用"。
    #   （若把后者也列入，SPA 里"该发请求没发"的真实缺陷会被静默吞掉。）
    "API断言不适用",
    "ASSERTION_NOT_APPLICABLE",
    "AI生成SQL失败",
    "JSON 校验失败",
    "不可评估",
    # ★ F3 根因修复(2026-09-26)：DB 深断言的表名/列名由**用例定义**（引擎生成）提供，
    #   与产品行为无关。实测 TC-REQ-002-P01：`DB[exists] 非法表名: 资产管理系统（fullc`
    #   —— 引擎从需求文本臆造出中文表名 → 被判 FAIL（同 run 的 AI 根因分析亦判
    #   ENVIRONMENT_ERROR）。凡"表名/列名非法或缺失"都是引擎侧配置错误，不是产品缺陷。
    "非法表名",
    "非法字段名",
    "缺少 table",
    "缺少 field",
)


def _split_segments(text: str) -> list:
    """把轨/步骤级 evidence 串拆成单条检查片段。

    轨级 evidence 由 `_track_protocol._aggregate` 以 `"; ".join` 拼接，
    形如 `"element_exists:✓(找到2个匹配元素); element_exists:✗(查询异常: ...)"`。
    拆开后才能逐条判"这一条到底是产品不满足还是引擎够不着"——整串判会串味。
    同时剥离引擎附加的**说明性标注**（非断言结论，见 `_strip_annotations`）。
    """
    if not text:
        return []
    return [
        _s for _s in (_strip_annotations(x).strip() for x in str(text).split(";"))
        if _s
    ]


# 引擎附加的说明性标注（不构成断言结论，必须剥离后再判，否则被误当具名断言命中）
#   实测 round5：UI 轨 evidence 恒以 `[a11y_violations:10]` 结尾（`a11y 检查发现 …`
#   的前缀摘要）。该 token 含 "a11y_violation" → 会命中 VERDICT_CHECK_MARKERS，
#   使**已通过**的 UI 轨被误判为"给出否定结论的断言"，进而把仅因 API 断言不适用
#   而失败的 TC-REQ-00X-SEC01 又拉回 PRODUCT_BUG（D18 回放验证实际抓到）。
_ANNOTATION_RE = re.compile(r"\[a11y_violations:\s*\d+\]", re.IGNORECASE)


def _strip_annotations(text: str) -> str:
    """剥离 `[a11y_violations:N]` 这类引擎说明性标注。"""
    if not text:
        return ""
    return _ANNOTATION_RE.sub("", str(text))


def _is_engine_gap(blob: str) -> bool:
    return any(m in blob for m in ENGINE_INCAPABILITY_MARKERS)


def _is_verdict(blob: str) -> bool:
    """是否"产品不满足"的证据：有具名断言标记 且 无引擎不可评估标记。"""
    if _is_engine_gap(blob):
        return False
    return any(k in blob for k in _product_defect_markers())


# 片段级"失败"标记 —— 用于从轨级 evidence 拆分出的多片段中，
# 过滤出真正失败的那条（否则同轨 PASS 的 VERDICT 断言会被误当成失败证据，
# 见 P0 bug：field_error_visible_check[PASS] 被 _is_verdict 命中 → 假 PRODUCT_BUG）。
#
# 为什么不直接靠 ✗：
#   - structured 路径有 [PASS]/[FAIL] 前缀（见 _agent_checks.py 各 handler）
#   - Agent 路径用 ✓/✗
#   - "失败"二字是中文兜底
# 三者取并集，覆盖所有已知输出格式。
_FRAGMENT_FAIL_MARKERS = ("✗", "[FAIL]", "失败")


def _is_failing_fragment(blob: str) -> bool:
    """单条检查片段是否处于失败态。"""
    return any(m in blob for m in _FRAGMENT_FAIL_MARKERS)


def _structured_verdict(result) -> "bool | None":
    """读取条检查的**结构化**判定标记。

    True = 可归因产品缺陷的负向判定；False = 非判定态（⊘ N/A 跳过/skipped/未验证，
    或负向但不可归因——如 element_exists 未命中属定位歧义）；None = 无结构化结论或属
    **引擎不可评估**（UNSUPPORTED_CHECK/EXECUTOR_EXCEPTION）——语义由 `_track_protocol` 定夺。
    """
    if not isinstance(result, dict) or "passed" not in result:
        return None
    try:
        from src.pipeline.steps.step4.verification._track_protocol import (
            _NON_VERDICT_REASONS as _ENGINE_GAP_REASONS,
            is_product_defect_verdict as _is_product_defect,
            is_verdict as _is_verdict_structured,
        )
    except Exception:  # noqa: BLE001 —— 权威不可读时退回文本判据，不阻断执行
        return None
    if _is_product_defect(result):
        return True
    if _is_verdict_structured(result):
        # 真实通过 → 非失败项（调用方已按 passed is False 过滤）；负向但不可归因
        # 产品缺陷（定位歧义型）→ 同样不作缺陷证据。
        return None if result.get("passed") else False
    if str(result.get("reason", "") or "") in _ENGINE_GAP_REASONS:
        return None  # 引擎不可评估 → 交引擎缺口判据（保持 SKIP/ERROR 既有语义）
    return False


def _collect_failing_items(step_results) -> list:
    """收集**正在失败**的检查项，返回 ``[(文本片段, 判定标记)]``。

    判定标记：``True``=结构化确认构成否定判定（产品缺陷证据）；
    ``False``=结构化确认为非判定态（⊘ 跳过/N/A/未验证，不作缺陷证据）；
    ``None``=无结构化信息，交由文本判据兜底。

    ★ 判定语义一律**读结构化结果**（`_track_protocol` 唯一权威），不得从渲染文本重推。
      仅当只有文本可用（Agent 路径）时才回退文本嗅探，并显式排除非判定态符号
      （⊘/⚠，与 render_check_tag 同源，不另立词表）。

    来源优先级（细→粗）：
      1) `_verify_result["checks"]` 单条结果 —— 结构化权威；
      2) `_verify_result["ui"|"api"|"db"]` 轨级结果 —— 优先读轨自带三值元数据
         （no_verdict_only / has_negative_verdict），无元数据才回退文本；
      3) 步骤级 `evidence` / `error` 文本（Agent 路径兜底）。
    """
    try:
        from src.pipeline.steps.step4.verification._track_protocol import (
            NON_VERDICT_RENDER_SYMBOLS as _NON_VERDICT_SYMS,
        )
    except Exception:  # noqa: BLE001
        _NON_VERDICT_SYMS = ()

    out = []
    for r in step_results or []:
        if not isinstance(r, dict):
            continue
        _vr = r.get("_verify_result")
        # ★ 兼容直接传入 verify_result 本体（含 ui/api/db 三轨键）——
        #   使 `_classify_checks_failure(verify_result)` 能复用同一谓词。
        if not isinstance(_vr, dict) and any(
            k in r and isinstance(r.get(k), dict) for k in ("ui", "api", "db")
        ):
            _vr = r
        if isinstance(_vr, dict):
            # 1) 单条 check：结构化定夺
            for _c in _vr.get("checks") or []:
                if not isinstance(_c, dict) or _c.get("passed") is not False:
                    continue
                _flag = _structured_verdict(_c)
                if _flag is True:
                    out.append((_blob_of_check(_c), True))
                elif _flag is False:
                    out.append((_blob_of_check(_c), False))
                else:
                    # 引擎不可评估 → 留下片段交缺口判据（保持 D18 既有 SKIP 语义）
                    out.append((_blob_of_check(_c), None))
            # 2) 轨级结果
            for _tk in ("ui", "api", "db"):
                _t = _vr.get(_tk)
                if not isinstance(_t, dict) or _t.get("passed") is not False:
                    continue
                _ev = str(_t.get("evidence") or "")
                if not _ev:
                    continue
                if _t.get("no_verdict_only"):
                    out.append((_ev, False))  # 全为非判定态 → 无缺陷证据
                    continue
                # 升格门槛只认「可归因产品缺陷」的失败：负向但不可归因（定位歧义型）
                # 一律作非判定态，防把引擎选择器不匹配写成产品缺陷。
                _pd = _t.get("has_product_defect_verdict")
                if _pd is not None:
                    out.append((_ev, bool(_pd)))
                    continue
                if _t.get("has_negative_verdict") is True:
                    out.append((_ev, True))  # 结构化确认存在真实负向判定
                    continue
                if _t.get("has_negative_verdict") is False:
                    out.append((_ev, False))
                    continue
                # 无结构化元数据（如 API/DB 轨 / 旧版 verify_step 产出）→ 文本兜底
                _all_segs = _split_segments(_ev)
                _segs = [
                    s
                    for s in _all_segs
                    if not any(_sym in s for _sym in _NON_VERDICT_SYMS)
                ]
                if not _segs:
                    # ★ 全部片段都带非判定态符号(⊘/⚠) ⇒ 本轨无判定证据。
                    #   必须登记为"非判定态"，**不得**把整条 evidence 交回文本重推
                    #   （否则 ⊘ 片段里的类型名会再次被当成否定结论 → 假 BUG）。
                    out.append((_ev, False))
                elif len(_segs) == 1:
                    out.append((_segs[0], None))
                else:
                    out.extend((s, None) for s in _segs if _is_failing_fragment(s))
        # 3) 步骤级文本兜底（Agent 路径无 _verify_result 时）
        for _key in ("evidence", "error"):
            for _s in _split_segments(str(r.get(_key) or "")):
                if any(_sym in _s for _sym in _NON_VERDICT_SYMS):
                    continue  # 引擎自述"无结论"（⊘/⚠）→ 不作产品缺陷证据
                if _is_engine_gap(_s):
                    out.append((_s, None))
                elif "✗" in _s or "失败" in _s:
                    out.append((_s, None))
    return out


def _blob_of_check(result: dict) -> str:
    """把单条 check 结构化结果渲染为可读片段（供文本判据兜底时复用）。"""
    return " ".join(
        str(result.get(k) or "") for k in ("type", "evidence", "reason", "status")
    )


def has_executed_assertion_verdict(step_results) -> bool:
    """step_results 中是否存在"已执行且给出否定结论的断言"（产品不满足）。

    参数 step_results：形如
      [{"description":..., "passed":bool, "error":str, "evidence":str,
        "_verify_result": {"ui":{...}, "api":{...}, "db":{...}, "checks":[...]}}, ...]

    返回 True 表示"产品行为不满足，应判 PRODUCT_BUG/FAIL"。
    **纯引擎不可评估的失败一律返回 False**（D18）——否则批量制造假 Bug。
    """
    try:
        for _blob, _flag in _collect_failing_items(step_results):
            if _flag is True:
                return True
            if _flag is None and _is_verdict(_blob):
                return True
        return False
    except Exception:  # noqa: BLE001 —— 覆盖失败不影响既有分类结果
        return False


def has_only_engine_gap_verdict(step_results) -> bool:
    """失败项**全部**是引擎不可评估的检查（无任何产品缺陷证据）。

    返回 True ⇒ 应判 SKIP(ENGINE_COVERAGE_GAP)：既无产品缺陷证据（不记 FAIL），
    也未真实通过（不记 PASS）。用于消除"引擎缺口被记成产品缺陷"的假 Bug。
    """
    try:
        _items = _collect_failing_items(step_results)
        if not _items:
            return False
        for _blob, _flag in _items:
            if _flag is True:
                return False
            if _flag is None and _is_verdict(_blob):
                return False
        return any(
            _flag is None and _is_engine_gap(_blob) for _blob, _flag in _items
        )
    except Exception:  # noqa: BLE001
        return False


def has_not_applicable_assertion(step_results) -> bool:
    """是否有**已声明但未适用**的断言（⊘ N/A 跳过，`ASSERTION_NOT_APPLICABLE`）。

    why: 断言以 N/A 跳过（⊘）表示"用例自己的场景没触及该断言的前提"——例如
      "提交后应出现字段级错误提示"落在没有可编辑字段的页面上。此时用例对**自身
      声明期望**什么都没判定；若同用例的结构性断言（"页面主体能渲染"）通过就记
      PASS，声明期望未经验证却借道假绿。故此类用例不得记 PASS（记 SKIP）。
    判据只读结构化 `reason`（引擎自述标记，见 `_db_ui._handle_field_error_visible`），
      不从渲染文本重推，也不扩大到"传感器暂不可用"（VLM/性能无样本）的跳过。
    """
    for r in step_results or []:
        if not isinstance(r, dict):
            continue
        _vr = r.get("_verify_result")
        if not isinstance(_vr, dict) and any(
            k in r and isinstance(r.get(k), dict) for k in ("ui", "api", "db")
        ):
            _vr = r
        if not isinstance(_vr, dict):
            continue
        for _c in _vr.get("checks") or []:
            if isinstance(_c, dict) and str(_c.get("reason") or "") == (
                "ASSERTION_NOT_APPLICABLE"
            ):
                return True
    return False


def declares_executable_assertion(case) -> bool:
    """用例是否声明了至少一条**可执行断言**（结构化 ``checks`` 非空）。

    why: PASS 的充分条件必须包含"用例声明了对需求的可观察义务"。仅靠引导层通用
      探针（page_loaded / 任意错误条）不承载需求义务——实测 checks 为空的用例以
      "页面已加载"或与需求无关的表单错误（``.field-error: 价格必须是数字`` 为
      "返回统一错误结构：code、message"背书）判 PASS，属结构性假绿。
      checks 为空时既不能记 PASS（无义务被验证），也不能记 FAIL（未对产品行为
      做出任何判定）→ 唯一诚实结论 SKIP(ENGINE_COVERAGE_GAP)。
    单一事实源: 结构化路径（``_base._execute_one_intent``）与 Agent 路径
      （``_agent_checks._try_agent_explorer_path``）共用本谓词，禁止在调用点各自拼装。
    通用性: 只读用例结构，跨系统/行业/页面成立，零业务词表。
    """
    if not isinstance(case, dict):
        return False
    return any(isinstance(c, dict) for c in (case.get("checks") or []))


def has_no_negative_verdict(step_results) -> bool:
    """结构化结果明确"有断言观察、但无任何真实负向判定" → 无产品缺陷证据（应 SKIP）。

    与 `has_only_engine_gap_verdict` 的分工：
      · 后者 = 失败项全为**引擎不可评估**（未知检查类型/执行器异常，文本可识别）；
      · 本函数 = 断言自身标注为**非判定态**（⊘ N/A 跳过、全 skipped/未验证），
        由结构化字段直接给出结论，无需文本嗅探。

    判据：至少观察到一条**非判定态**结果（`is_verdict` 为假），
    且不存在任何**真实负向判定**（`is_negative_verdict` 为真）。
    纯动作失败（无任何非判定态断言）返回 False —— 不得把引擎动作缺口转成 SKIP。
    "引擎不可评估"类非判定态（UNSUPPORTED_CHECK / EXECUTOR_EXCEPTION）**不计入**
    本判据：它们由 `has_only_engine_gap_verdict` / `_unsupported` 通道处理，
    保持既有的 HARNESS_ERROR(ERROR) 语义不变。

    复用 `_collect_failing_items` 的同一份判定标记（不另立判据）。
    """
    try:
        _items = _collect_failing_items(step_results)
    except Exception:  # noqa: BLE001
        return False
    if not _items:
        return False
    _saw_non_verdict = False
    for _blob, _flag in _items:
        if _flag is True:
            return False
        if _flag is False:
            _saw_non_verdict = True
            continue
        # _flag is None：无结构化信息 → 文本判据
        if _is_verdict(_blob):
            return False
        if _is_engine_gap(_blob):
            continue  # 交引擎缺口判据（保持 ERROR 语义）
        _saw_non_verdict = True
    return _saw_non_verdict
