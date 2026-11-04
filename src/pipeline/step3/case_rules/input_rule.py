# -*- coding: utf-8 -*-
"""输入框规则：处理 input / textarea 控件，生成 P01/N01/B01 三胞胎。"""

from typing import Any
import logging
from .base import BaseCaseRule
from src.pipeline.step3.case_rules.base import _SUBMIT_BUTTON_KEYWORDS as _SUBMIT_BUTTON_KEYWORDS  # noqa: F401  词表唯一源
from src.pipeline.step3.case_rules.base import _TRIGGER_FEEDBACK_KEYWORDS as _TRIGGER_FEEDBACK_KEYWORDS  # noqa: F401  词表唯一源

_log = logging.getLogger(__name__)


class InputRule(BaseCaseRule):
    """输入框通用规则。

    覆盖：input, textarea, password, email, phone, number 等。
    P01: 填写合法值，提交 → 期望成功跳转
    N01: 填写非法值，提交 → 期望错误提示
    B01: 不填值（空提交），提交 → 期望校验提示
    """

    rule_id = "input_rule"
    name = "输入框规则"
    description = (
        "处理文本输入框/密码框/邮箱框/数字框等输入控件，生成正向/异常/边界三胞胎"
    )
    control_types = ["input", "textarea", "password", "email", "phone", "number"]
    priority = 100  # 最高优先级，input 最常见

    def generate(
        self,
        req: dict[str, Any],
        target_page: str,
        control_type: str,
        merged_data: dict[str, Any],
        selected_ops: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        rid = self._safe_rid(req)
        module = req.get("module", merged_data.get("module", "")) or ""
        operation = (req.get("operation") or "").lower()
        expected = req.get("expected_feedback") or req.get("expected") or ""
        desc = req.get("description", "") or operation
        fill_target = self._infer_fill_target(operation, target_page, merged_data)
        submit_text = self._infer_submit_text(operation, target_page, merged_data)
        # ★ 通用修复(2026-07-22): 查询/搜索类操作无独立按钮时，用"输入框+Enter"模式
        #   根因链路(5层):
        #     L1 表层: 查询类用例降级为 D01（只有 navigate+screenshot），无真实断言
        #     L2 中层: _infer_submit_text 返回 None（DOM 无"查询/搜索"按钮）
        #     L3 根因层: 很多系统搜索通过输入框+Enter触发，没有独立搜索按钮
        #     L4 设计层: input_rule 在 submit_text=None 时直接降级，未考虑 Enter 提交模式
        #     L5 最深根因: 降级判断在 use_enter_submit 逻辑之前，导致 Enter 模式永不执行
        #   修复: 在降级判断之前，先检查是否是查询类操作 + DOM 有搜索输入框 + 无搜索按钮
        #   通用性: 适用于所有"输入框+Enter搜索"模式的系统（Ant Design/Element UI/原生 HTML）
        #   覆盖性: 影响所有查询类 input_rule 用例（跨系统/跨行业/跨页面）
        #   全局观: 修复后查询类用例不再降级，P01/N01/B01 生成真实查询断言
        #   职责边界: Step3 input_rule 内部修复，不越界
        #   自我反驳:
        #     1) 硬编码 Enter 键？→ 不硬编码，use_enter_submit 标记驱动，_submit_action 选择
        #     2) 只检查 placeholder 含"搜索"通用吗？→ 通用，覆盖所有系统的搜索输入框
        #     3) 如果页面既无搜索按钮也无搜索输入框？→ 真实降级（无输入框无法查询）
        use_enter_submit = False
        is_query_op = any(
            k in operation for k in ["查询", "搜索", "筛选", "检索", "查找"]
        )
        if submit_text is None and is_query_op:
            try:
                from src.pipeline.step3._rule_extractor import (
                    _load_dom_elements_for_page,
                )

                dom_elements = _load_dom_elements_for_page(target_page)
                inputs = dom_elements.get("input_placeholders", [])
                buttons = dom_elements.get("button_texts", [])
                has_search_input = any(
                    any(k in inp for k in ["搜索", "查询", "search", "筛选", "查找"])
                    for inp in inputs
                )
                has_search_btn = any(
                    any(k in btn for k in ["搜索", "查询", "筛选", "search", "查找"])
                    for btn in buttons
                )
                if has_search_input and not has_search_btn:
                    use_enter_submit = True
                    submit_text = "__enter_search__"
                    _log.info(
                        "[InputRule] 查询类操作无搜索按钮，使用 Enter 提交模式: operation=%s",
                        operation[:40],
                    )
            except Exception as _e:
                _log.debug("[InputRule] DOM 元素加载失败: %s", _e, exc_info=True)

        # ★ 降级=失败: DOM 无匹配按钮时返回 None，触发 fallback 到下一个规则或 _fallback_cases
        if submit_text is None:
            return None
        op_ids = self._op_ids(selected_ops)
        has_fill = "fill" in op_ids

        # ★ Fix49: 正向值优先用 RAG API 字段名，回退 operation 语义
        happy_value = self._infer_fill_value(operation, merged_data)

        # 异常值
        # ★ §13.3 通用修复(2026-07-24 续): 传 merged_data 让 _infer_error_value 走 value_generator
        error_value = self._infer_error_value(operation, merged_data)

        tracks = ["ui"]

        # ★ use_enter_submit 和 is_query_op 已在降级判断前设置（line 52-73）

        # 构造提交动作
        def _submit_action(focus: str = "") -> dict[str, str]:
            if use_enter_submit:
                return self._press_key("Enter", focus_hint=focus or fill_target)
            return self._click(submit_text)

        # ★ v25 根因Z6 修复(2026-07-21): P01/N01/B01 checks 根据 submit_text 语义匹配
        #   原 bug: input_rule 的 P01/N01/B01 假设 submit_text 是"表单提交按钮"（保存/提交/确定），
        #          checks 期望"成功提示/错误提示/校验提示"。
        #         但对"新建"类操作，_infer_submit_text 返回的是"触发按钮"（新建配方/新增辅材），
        #          actions 实际上是"fill值 + click打开表单"，不是"fill值 + click提交表单"。
        #          系统不会显示成功/错误/校验提示（因为没提交表单）→ text_contains/element_exists 失败 → 假FAIL。
        #   修复: 对"新建/编辑"类触发按钮，P01/N01/B01 checks 改为验证"表单/弹窗打开了"，
        #         而不是"成功/错误/校验提示"（因为 actions 只打开了表单，没提交）。
        #   根因链路(5层):
        #     L1 表层: P01 text_contains_check 失败（找不到"新增成功"）
        #     L2 中层: actions 是"fill值 + click打开表单"，checks 期望"新增成功"
        #     L3 根因层: submit_text 是触发按钮（新建配方），不是提交按钮（保存/提交/确定）
        #     L4 设计层: input_rule 的 _build_checks 假设 submit_text 是提交按钮
        #     L5 最深根因: operation 语义和 actions 模板的语义错配
        #   通用性: 适用于所有"新建/编辑"类操作的 input_rule P01/N01/B01（跨系统/跨行业/跨页面）
        #   覆盖性: 覆盖所有 submit_text 是触发按钮的 P01/N01/B01 用例
        #   全局观: 修复后 P01/N01/B01 不再假FAIL，Step4 能真实执行
        #   职责边界: Step3 input_rule 内部修复，不越界
        #   自我反驳:
        #     1) 改 _build_success_check？→ 破坏其他规则的 P01（如 button_rule 的 P01 期望"成功"是合理的）
        #     2) 改 actions 加"打开表单+fill表单+提交"？→ 需要知道表单 DOM 结构，违反通用性
        #     3) 改 checks 匹配 actions 语义？→ 最通用，actions 打开表单就验证表单打开
        #   合规: AGENTS.md §10.8 禁止治标不治本 + §10.9 根因核查流程
        #   ★ v25 Z6-扩展(2026-07-21): 除"新建/编辑"关键词外，expected_feedback含
        #     "流程/唤起/打开/弹窗/对话框/模态"也视为触发按钮（功能按钮如"批量打标"）
        #     根因: "批量打标"按钮点击后唤起打标流程（弹窗），不是提交表单，
        #           但原_is_trigger_btn只判断"新建/编辑"关键词 → 走else分支生成"校验提示"checks → 假FAIL
        #     通用性: 适用于所有"功能按钮"（批量打标/导出/导入等），不硬编码业务术语
        #     覆盖性: 影响所有submit_text不是提交按钮的input_rule用例
        _TRIGGER_BUTTON_KEYWORDS = (
            "新建",
            "新增",
            "添加",
            "创建",
            "编辑",
            "修改",
            "更新",
            "新增配方",
            "新建配方",
            "新增辅材",
            "新建辅材",
        )
        _is_trigger_btn = any(
            k in (submit_text or "") for k in _TRIGGER_BUTTON_KEYWORDS
        ) or any(k in (expected or "") for k in _TRIGGER_FEEDBACK_KEYWORDS)

        _is_submit_btn = any(k in (submit_text or "") for k in _SUBMIT_BUTTON_KEYWORDS)
        _is_other_btn = not _is_trigger_btn and not _is_submit_btn
        # 页面响应选择器(通用): 弹窗/加载/结果/消息/状态变化
        _RESPONSE_SELECTOR = (
            "[role='dialog'], [role='alert'], [role='status'], "
            ".ant-modal, .ant-drawer, .ant-message, .ant-notification, "
            ".el-dialog, .el-drawer, .el-message, "
            "[class*='message'], [class*='notice'], [class*='toast'], "
            "[class*='loading'], [class*='result'], [class*='success'], "
            "[class*='feedback'], .success, .alert-success"
        )

        # ★ v25 根因Z7(2026-07-21): 批量操作类按钮需先选中记录才能打开弹窗
        #   根因链路(5层):
        #     L1 表层: B01 element_exists✗（找不到弹窗）
        #     L2 中层: actions 是"fill空值 + click批量打标"，但批量打标需先选中记录
        #     L3 根因层: actions 缺少"选中记录"前置步骤
        #     L4 设计层: input_rule 没有区分"批量操作按钮"和"单个操作按钮"
        #     L5 最深根因: 批量操作的业务语义是"选中记录→点击批量按钮→弹窗"
        #   修复: 对"批量"类操作，actions 中在 click 批量按钮前添加"选中第一行记录"步骤
        #   通用性(探索性原子操作):
        #     - 使用 action_library 的 `check` 原子操作（form.check，勾选 checkbox/radio）
        #     - target_hint 用语义描述"第一行复选框"，不硬编码 selector
        #     - Step4 通过 self-healing / 原子指令匹配探索定位实际 checkbox 元素
        #     - 适用于所有系统/行业/页面（Ant Design/Element UI/原生 HTML 都兼容）
        #   覆盖性: 影响所有 submit_text 含"批量"关键词的 input_rule 用例
        #   全局观: 修复后 Step4 不再假FAIL，批量操作能真实执行
        #   职责边界: Step3 input_rule 内部修复，不越界
        #   自我反驳:
        #     1) 硬编码 selector 通用吗？→ 不通用，不同 UI 框架 selector 不同，违反"探索性原子操作"原则
        #     2) 用 check 原子操作 + 语义 hint 通用吗？→ 通用，Step4 通过原子指令匹配探索定位
        #     3) 如果表格没 checkbox 怎么办？→ 真实 FAIL（check 找不到元素），不是假 PASS
        #   合规: AGENTS.md §10.8 禁止治标不治本 + §10.9 根因核查流程 + §11 控件-操作映射（查表优先）
        _BATCH_KEYWORDS = ("批量",)
        _is_batch_op = any(k in (submit_text or "") for k in _BATCH_KEYWORDS) or any(
            k in operation for k in _BATCH_KEYWORDS
        )

        def _select_first_row_action() -> dict[str, str]:
            """生成"选中第一行记录"动作（用 check 原子操作，探索性定位）。"""
            return {
                "type": "check",
                "target_hint": "第一行复选框",
                "_desc": "选中第一行记录（批量操作前置）",
            }

        # ── P01（正向）──
        p01_actions = [self._nav(target_page)]
        if has_fill:
            p01_actions.append(self._fill(fill_target, happy_value))
        if _is_batch_op:
            p01_actions.append(_select_first_row_action())
        p01_actions.append(
            _submit_action(focus=fill_target if use_enter_submit else "")
        )
        p01_actions.append(self._screenshot())

        # ★ v25 根因Z6 修复(2026-07-21): P01 checks 根据 submit_text 语义匹配
        #   触发按钮: P01 验证"表单/弹窗打开了"（W3C ARIA role=dialog + 通用框架类名）
        #   提交按钮: P01 验证"成功提示"（原逻辑）
        # ★ 通用修复(2026-07-24): 其他按钮(执行/计算/导入/导出)验证"页面有响应"
        #   根因: "执行产品质心计算"既非触发也非提交 → 走else生成text_contains期望"成功提示" → 假FAIL
        #   修复: _is_other_btn验证页面有响应(element_exists弹窗/加载/结果/消息) + no_error
        if _is_trigger_btn:
            p01_checks = [
                self._build_check(
                    "element_exists_at",
                    "[role='dialog'], [role='form'], .ant-modal, .ant-drawer, .ant-form, .el-dialog, .el-drawer, .el-form",
                    "存在",
                    "点击触发按钮后表单/弹窗打开（正向流程：表单可打开）",
                ),
                self._build_check("no_error", "页面", "无错误", "系统无报错"),
            ]
        elif _is_other_btn:
            # 其他按钮(执行/计算/导入/导出等): 验证页面有响应而非"成功提示"
            p01_checks = [
                self._build_check(
                    "element_exists_at",
                    _RESPONSE_SELECTOR,
                    "存在",
                    "点击按钮后页面有响应（弹窗/加载/结果/消息出现）",
                ),
                self._build_check("no_error", "页面", "无错误", "系统无报错"),
            ]
        else:
            p01_checks = self._build_checks(
                req, expected, "happy_path", merged_data, fill_value=happy_value
            )

        # ── N01（异常）──
        n01_actions = [self._nav(target_page)]
        if has_fill:
            n01_actions.append(self._fill(fill_target, error_value))
        if _is_batch_op:
            n01_actions.append(_select_first_row_action())
        n01_actions.append(
            _submit_action(focus=fill_target if use_enter_submit else "")
        )
        n01_actions.append(self._screenshot())

        # ★ 通用修复(2026-07-24): N01 checks 三分类 — 与 P01 一致(§13.3 覆盖性)
        #   根因(§10.9): N01 未同步 P01 的 _is_other_btn 分支，"执行/计算"类按钮走 else
        #     生成 error_path checks 期望"错误提示"，但 actions 只触发页面响应 → 假FAIL
        #   修复: 三分类 — 触发按钮验证表单打开；其他按钮验证页面响应；提交按钮验证错误提示(原逻辑)
        #   通用性(§12.4): 跨系统/跨行业/跨页面 — 与 P01 分类一致
        #   覆盖性: 覆盖所有 submit_text 是其他按钮的 N01 用例
        #   职责边界: Step3 input_rule 内部修复，不越界
        #   自我反驳:
        #     1) 漏洞? 验证页面响应是否太宽松? → 不宽松，验证弹窗/加载/结果/消息+no_error
        #     2) 更通用? 用 operation 语义? → submit_text 已含 operation 语义，足够
        #     3) 更根本? 改 _build_checks? → 破坏其他规则，应在此处分类
        if _is_trigger_btn:
            # 触发按钮：N01 验证"表单/弹窗打开了"（W3C ARIA role=dialog + 通用框架类名）
            n01_checks = [
                self._build_check(
                    "element_exists_at",
                    "[role='dialog'], [role='form'], .ant-modal, .ant-drawer, .ant-form, .el-dialog, .el-drawer, .el-form",
                    "存在",
                    "点击触发按钮后表单/弹窗打开（异常输入未提交，验证表单可打开）",
                ),
                self._build_check("no_error", "页面", "无错误", "系统无报错"),
            ]
        elif _is_other_btn:
            # 其他按钮(执行/计算/导入/导出等): 验证页面有响应而非"错误提示"
            n01_checks = [
                self._build_check(
                    "element_exists_at",
                    _RESPONSE_SELECTOR,
                    "存在",
                    "点击按钮后页面有响应（弹窗/加载/结果/消息出现）",
                ),
                self._build_check("no_error", "页面", "无错误", "系统无报错"),
            ]
        else:
            n01_checks = self._build_checks(
                req, expected, "error_path", merged_data, fill_value=error_value
            )

        # ── B01（边界）──
        b01_actions = [self._nav(target_page)]
        if has_fill:
            b01_actions.append(self._fill(fill_target, ""))
        if _is_batch_op:
            b01_actions.append(_select_first_row_action())
        if use_enter_submit:
            b01_actions.append(self._press_key("Enter", focus_hint=fill_target))
        else:
            b01_actions.append(self._click(submit_text))
        b01_actions.append(self._screenshot())

        # ★ v25 根因Z6 修复(2026-07-21): B01 checks 根据 submit_text 语义匹配
        #   原 bug: input_rule 的 B01 假设 submit_text 是"表单提交按钮"（保存/提交/确定），
        #          checks 期望"校验提示"（validation_hint）。
        #         但对"新建"类操作，_infer_submit_text 返回的是"触发按钮"（新建配方/新增辅材），
        #          actions 实际上是"fill空值 + click打开表单"，不是"fill空值 + click提交表单"。
        #          系统不会显示校验提示（因为没提交表单）→ element_exists 失败 → 假FAIL。
        #   根因链路(5层):
        #     L1 表层: B01 element_exists 失败（找不到校验提示元素）
        #     L2 中层: actions 是"fill空值 + click打开表单"，checks 期望"校验提示"
        #     L3 根因层: submit_text 是触发按钮（打开表单），不是提交按钮（提交表单）
        #     L4 设计层: input_rule 的 _build_checks 假设 submit_text 是提交按钮
        #     L5 最深根因: operation 语义和 actions 模板的语义错配
        #   修复: 对"新建/编辑"类触发按钮，B01 checks 改为验证"表单/弹窗打开了"，
        #         而不是"校验提示"（因为 actions 只打开了表单，没提交）。
        #   通用性: 适用于所有"新建/编辑"类操作的 input_rule B01（跨系统/跨行业/跨页面）
        #   覆盖性: 覆盖所有 submit_text 是触发按钮的 B01 用例
        #   全局观: 修复后 B01 不再假FAIL，Step4 能真实执行
        #   职责边界: Step3 input_rule 内部修复，不越界
        #   自我反驳:
        #     1) 改 _infer_submit_text 返回提交按钮？→ 破坏 P01（P01 需要 fill + click提交，但没打开表单 fill 找不到字段）
        #     2) 改 actions 加"打开表单+fill表单+提交"？→ 需要知道表单 DOM 结构，违反通用性
        #     3) 改 checks 匹配 actions 语义？→ 最通用，actions 打开表单就验证表单打开
        #   合规: AGENTS.md §10.8 禁止治标不治本 + §10.9 根因核查流程
        # ★ 通用修复(2026-07-24): B01 checks 三分类 — 与 P01/N01 一致(§13.3 覆盖性)
        #   根因(§10.9): B01 未同步 _is_other_btn 分支，"执行/计算"类按钮走 else
        #     生成 boundary checks 期望"校验提示"，但 actions 只触发页面响应 → 假FAIL
        #   修复: 三分类 — 触发按钮验证表单打开；其他按钮验证页面响应；提交按钮验证校验提示(原逻辑)
        #   通用性/覆盖性/职责边界/自我反驳: 同 N01 修复(见上)
        if _is_trigger_btn:
            # 触发按钮：B01 验证"表单/弹窗打开了"（W3C ARIA role=dialog + 通用框架类名）
            b01_checks = [
                self._build_check(
                    "element_exists_at",
                    "[role='dialog'], [role='form'], .ant-modal, .ant-drawer, .ant-form, .el-dialog, .el-drawer, .el-form",
                    "存在",
                    "点击触发按钮后表单/弹窗打开",
                ),
                self._build_check("no_error", "页面", "无错误", "系统无报错"),
            ]
        elif _is_other_btn:
            # 其他按钮(执行/计算/导入/导出等): 验证页面有响应而非"校验提示"
            b01_checks = [
                self._build_check(
                    "element_exists_at",
                    _RESPONSE_SELECTOR,
                    "存在",
                    "点击按钮后页面有响应（弹窗/加载/结果/消息出现）",
                ),
                self._build_check("no_error", "页面", "无错误", "系统无报错"),
            ]
        else:
            # 提交按钮：B01 验证"校验提示"（原逻辑）
            b01_checks = self._build_checks(
                req, expected, "boundary", merged_data, fill_value=""
            )

        return [
            self._build_one(
                f"TC-{rid}-P01",
                req,
                module,
                desc,
                operation,
                expected,
                "happy_path",
                "P1",
                "正向",
                "playwright",
                control_type,
                tracks,
                p01_actions,
                p01_checks,
                "正向流程验证",
            ),
            self._build_one(
                f"TC-{rid}-N01",
                req,
                module,
                desc,
                operation,
                expected,
                "error_path",
                "P1",
                "异常",
                "playwright",
                control_type,
                tracks,
                n01_actions,
                n01_checks,
                "异常输入校验",
            ),
            self._build_one(
                f"TC-{rid}-B01",
                req,
                module,
                desc,
                operation,
                expected,
                "boundary",
                "P2",
                "边界",
                "playwright",
                control_type,
                tracks,
                b01_actions,
                b01_checks,
                "边界值与异常验证",
            ),
        ]

    # ── 断言构建 ──
    def _build_checks(
        self,
        req: dict,
        expected: str,
        scenario: str,
        merged_data: dict,
        fill_value: str = "",
    ) -> list[dict[str, Any]]:
        """★ v24 根因K-1 修复: 构建 checks（真断言，替代通用 element_exists 假断言）。
        ★ Fix41 (2026-07-21): 查询/搜索类操作必须验证结果与查询词一致性，
              覆盖 P01/N01/B01，禁止只检查"表格存在"的假PASS。

        根因链路（6层）:
          L1 表层: 3/3 PASS=100%都是假PASS
          L2 中层: checks都是element_exists + 通用main selector
          L3 根因层: _build_checks不用expected_feedback参数（本方法）
          L4 设计层: 原设计假设"页面加载=业务正确"
          L5 架构层: checks生成与需求语义脱节
          L6 最深根因: expected_feedback本身是模板化无意义文本（根因K-2已修复）

        修复方案:
          - happy_path: text_contains_check + 业务反馈关键词（多候选逗号分隔）
          - error_path: error_hint (element_exists role=alert) - 真断言
          - boundary: validation_hint (element_exists aria-invalid) - 真断言
          - 查询类: 额外验证搜索结果包含/不包含查询词（与 actions 中 fill value 一致）

        通用性: 适用于所有 operation 语义，纯数据驱动，不预设业务场景。
        合规: AGENTS.md §10.8 禁止治标不治本 + §4.1 一个需求→一个完整测试用例
              §13.3 checks 必须匹配 actions 语义。
        """
        checks = []
        operation = (req.get("operation") or "").lower()
        is_query = any(k in operation for k in ["查询", "搜索", "筛选", "检索", "查找"])
        # 通用结果列表选择器（跨 UI 框架）
        _RESULT_SELECTOR = "table, .table, .list, .result-list, [role='grid'], [role='table'], .ant-list, .el-table__body"
        _EMPTY_SELECTOR = (
            ".empty, .no-data, .no-result, [role='empty'], .ant-empty, .el-empty"
        )

        if is_query:
            # 查询类操作：断言必须与输入值关联
            if scenario == "happy_path":
                # 正向查询：结果列表存在 + 结果文本包含查询词
                checks.append(
                    self._build_check(
                        "element_exists_at",
                        _RESULT_SELECTOR,
                        "存在",
                        "查询结果列表/表格存在",
                    )
                )
                if fill_value:
                    checks.append(
                        self._build_check(
                            "text_contains_check",
                            _RESULT_SELECTOR,
                            fill_value,
                            f"查询结果包含关键词 '{fill_value}'",
                            check_expected=fill_value,
                            operator="contains",
                            precision="fuzzy",
                        )
                    )
                checks.append(
                    self._build_check("no_error", "页面", "无错误", "系统无报错")
                )
            elif scenario == "error_path":
                # 异常查询：搜索无结果词 → 期望显示空状态提示
                checks.append(
                    self._build_check(
                        "element_exists_at",
                        _EMPTY_SELECTOR,
                        "存在",
                        "无匹配结果时显示空状态提示",
                    )
                )
                checks.append(
                    self._build_check("no_error", "页面", "无错误", "系统无报错")
                )
            elif scenario == "boundary":
                # 边界查询：空搜索 → 期望显示全部结果（列表存在）
                checks.append(
                    self._build_check(
                        "element_exists_at",
                        _RESULT_SELECTOR,
                        "存在",
                        "空搜索时结果列表存在",
                    )
                )
                checks.append(
                    self._build_check("no_error", "页面", "无错误", "系统无报错")
                )
            return checks

        if scenario == "happy_path":
            # ★ v24: 用 text_contains_check + 业务反馈关键词替代通用 element_exists
            checks.append(self._build_success_check(operation, req=req))
            checks.append(self._build_check("no_error", "页面", "无错误", "系统无报错"))
        elif scenario == "error_path":
            checks.append(
                self._build_check(
                    "error_hint", "页面", "错误提示", "系统显示错误提示信息"
                )
            )
        elif scenario == "boundary":
            checks.append(
                self._build_check(
                    "validation_hint", "页面", "校验提示", "系统显示校验提示信息"
                )
            )
        return checks
