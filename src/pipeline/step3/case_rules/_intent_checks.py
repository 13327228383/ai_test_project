# -*- coding: utf-8 -*-
from __future__ import annotations

"""intent 用例的 checks 来源决策（W1-2）。

从 `base.py` 拆出，保证单文件 ≤800 行（AGENTS.md §4.3）。

背景（根因 §10.9）：
  `_build_intent_case` 原先无条件写死 ``[element_exists(整页 body), no_error]``，
  注释声称"AgentExplorer 探索结果会追加真实验证"，但全仓 grep 无任何 checks 追加
  写回点 —— 该承诺从未兑现。结果 intent 类用例只验证"页面能打开"，属 §12.4
  明令禁止的假 PASS 温床。

本模块提供 `IntentChecksMixin`，按 §12.3「断言来源=设计+需求」重排优先级：
  上游 checks → verification_plan.ui → expected_feedback → 最小占位(打标)
"""

import logging
import re
from typing import Any

_log = logging.getLogger(__name__)

# ★ 性能型期望判据: 性能是页面快照不可观察的量，
#   语义断言用它必假 FAIL；命中即改发 performance_lcp_check 真实测量。
#   通用性: 时间/秒/毫秒/响应/加载/渲染 语汇跨系统/行业，零业务硬编码。
_PERF_EXPECTATION_KW = (
    "响应时间", "加载时间", "渲染时间", "首屏", "耗时", "毫秒内", "秒内",
    "不超过", "小于 2秒", "小于2秒", "秒以内", "< 2秒", "<2秒", "≤", "≤ 2秒",
    "response time", "load time", "LCP", "FCP",
)
_PERF_THRESHOLD_RE = re.compile(r"(\d{1,4})\s*(?:毫秒|ms|秒|s)")
_PERF_MS_RE = re.compile(r"(\d{1,4})\s*毫秒|(\d+)\s*ms", re.IGNORECASE)
_PERF_SEC_RE = re.compile(r"(\d{1,2})\s*秒|(\d+)\s*s\b", re.IGNORECASE)


def _is_performance_expectation(texts: str) -> bool:
    """需求期望是否为性能型（时间/秒级约束）。"""
    _t = (texts or "").lower()
    return any(_kw in _t for _kw in _PERF_EXPECTATION_KW)


def _extract_perf_threshold_ms(texts: str, default_ms: int = 3000) -> int:
    """从需求文本提取性能阈值（毫秒），无则返回 default_ms。"""
    _m = _PERF_MS_RE.search(texts)
    if _m:
        return int(_m.group(1) or _m.group(2) or default_ms)
    _m = _PERF_SEC_RE.search(texts)
    if _m:
        return int(_m.group(1) or _m.group(2) or 0) * 1000
    _m = _PERF_THRESHOLD_RE.search(texts)
    if _m:
        _v = int(_m.group(1))
        if "秒" in _m.group(0).strip() or "s" in _m.group(0).strip().lower():
            return _v * 1000
        return _v
    return default_ms

# 占位断言使用的通用主内容区 selector（跨 React/Vue/原生 HTML 通用）
_MAIN_SELECTOR = "main, [role='main'], .ant-layout-content, .el-main, body"

# assertion / expected 文本入库前的统一截断长度
_TEXT_LIMIT = 120

# ★ 根因修复(2026-09-24): 「行为描述 vs 页面文案」判别（needle 质量单一事实源）。
#   根因: 需求 expected_feedback 绝大多数是"系统做什么"的行为描述
#     （"系统接受大于0的数字价格"/"列表行数减一"/"客户端拦截不提交并显示错误提示"
#      /"系统弹出浏览器原生确认对话框"/"系统按每页5条分页显示,当前页高亮"），
#     不是页面上会字面出现的文案。原 _extract_short_keywords 只要文本 ≤20 字就
#     当作字面 needle 返回 → 下游 text_contains/semantic 断言拿行为描述去比对页面，
#     恒不匹配 → 系统性假 FAIL（实测 47 条断言、REQ-004/005/006/007/008/009/010/014/
#     015/017/018/019/020/026/030/034/035/038 的 E01/I01 全灭）。
#   通用判据（跨系统/行业/页面，零业务硬编码）:
#     ① 行为主体: 出现"谁在做"（系统/页面/服务端/客户端/前端/后端/应用/平台/浏览器）
#        → 该串在陈述行为，不可能整体出现在页面上；
#     ② 行为结果短语: 行数增减/保持不变/每页/写库/落库/拦截/重定向/跳转/不提交/
#        弹出/分页/高亮/无致命错误 —— 同样是行为描述而非文案。
#   反例（必须保留为字面 needle，页面真实渲染）: "无匹配数据"、"名称已存在"、
#     "创建成功"、"保存成功"、"用户名或密码错误"、"维护中的资产不允许删除"。
#   ⚠ 判"行为描述"时返回 ""，调用方退化为语义验证（交 Agent/AI 判定），
#     遵循"机械断言宁缺勿假"——绝不允许用行为描述当字面 needle。
_BEHAVIOR_SUBJECT_RE = re.compile(
    r"系统|页面|服务端|客户端|前端|后端|应用|平台|浏览器"
)
_BEHAVIOR_RESULT_RE = re.compile(
    r"行数|减一|加一|保持不变|每页|写库|落库|拦截|重定向|跳转|不提交|"
    r"弹出|分页|高亮|无致命错误|精确过滤|自动生成|自动向|自动写入|"
    # 反馈"类别"词（页面渲染的是具体文案，不是"错误提示"这四个字本身）
    r"错误提示|校验提示|提示错误|校验失败|错误信息|校验信息|错误条"
)


class IntentChecksMixin:
    """为 BaseCaseRule 提供 intent 用例 checks 决策能力。

    依赖宿主类提供 `_build_check(check_kind, target, expected, description, ...)`。
    """

    def _resolve_intent_checks(
        self,
        req: dict[str, Any],
        expected: str,
        operation: str,
        entity_tables: dict | None = None,
        is_i01: bool = False,
        module: str = "",
    ) -> tuple[list[dict[str, Any]], bool]:
        """决定 intent 用例的 checks 来源。

        优先级（§12.3 断言来源=设计+需求）：
          1. req 已带 checks[]              → 直接沿用（上游已产出真实断言）
          2. req 已带 verification_plan.ui  → 转成 checks 沿用
          3. entity_tables 非空且 I01 类型   → 生成字段级 text_contains 断言（★ 根因修复 2026-08-09）
          4. expected_feedback 非空          → 生成可证伪的 text_contains 断言
          5. 全缺失                          → 最小占位并置 needs_real_assertion

        禁止伪造边界值（如 0 / 999999999）—— 占位只表达"页面未崩"，
        不冒充业务断言，也不构造需求里不存在的输入。

        Args:
            req: 需求字典
            expected: 期望反馈文本
            operation: 操作描述
            entity_tables: 可选，实体表字典（含 columns 列名列表），用于 I01 字段级检查
            is_i01: 是否 I01 字段展示类型用例
            module: 需求模块名（如"学生管理"），用于匹配正确的 DB 表

        返回:
            (checks, needs_real_assertion)
        """
        # ★ 根因修复(2026-08-09): I01 优先级 — entity_tables 字段级检查
        # ★ v2(2026-08-09): case_id 含 I01 后缀（如 TC-REQ-002-I01），
        #   req_id 不含 I01（如 REQ-002），所以必须通过 is_i01 参数传入。
        # ★ v3(2026-08-09): 传入 module 参数，使 _checks_from_entity_tables 能匹配对应表，
        #   而非全局共用一个 seen 集或回退到第一张表（根因: 之前所有 I01 用例都得 Department 字段）。
        _is_i01 = is_i01 or "字段" in (operation or "")
        if _is_i01 and entity_tables:
            _log.info(
                "[intent_checks] ★ I01入口: entity_tables=%d 张表, keys=%s, module=%r",
                len(entity_tables),
                list(entity_tables.keys())[:10],
                module,
            )
            _field_checks = self._checks_from_entity_tables(entity_tables, module=module)
            if _field_checks:
                _log.info(
                    "[intent_checks] ★ I01字段级检查: checks=%d 条, "
                    "module=%r, 首条expect=%s",
                    len(_field_checks),
                    module,
                    _field_checks[0].get("expect", "") if _field_checks else "",
                )
                return _field_checks, False

        reused = self._reuse_upstream_checks(req)
        if reused is not None:
            return reused, False

        # ★ 根因修复(2026-09-26) FIX4/AJ(续): 行为型访问控制需求 → 行为断言
        _ac_checks = self._access_control_intent_checks(req, operation, expected)
        if _ac_checks is not None:
            return _ac_checks, False

        expected_text = (expected or "").strip()
        if expected_text:
            _extra = " ".join(
                str(req.get(k) or "")
                for k in ("operation", "description", "module")
            )
            return self._checks_from_expected(expected_text, _extra), False

        _log.debug(
            "[intent_checks] op=%r 无断言来源，生成最小占位", (operation or "")[:40]
        )
        return self._placeholder_checks(), True

    @staticmethod
    def _access_control_intent_checks(
        req: dict[str, Any], operation: str, expected: str
    ) -> list[dict[str, Any]] | None:
        """行为型访问控制需求的**行为断言**；非此类需求返回 ``None``（保持原路径）。

        ★ 根因修复(2026-09-26) FIX4/AJ(续): 访问控制需求（"viewer 只能查看资产列表
          与详情，不允许新增/编辑/删除，操作时返回 403 权限不足"）的 intent 用例
          （TC-*-I01 Agent探索验证）被生成
          `semantic_check('不同角色获得对应的数据访问与操作权限')` —— 该文本是需求里的
          **设计陈述**（规格语言），页面上根本不存在 → 恒 0 匹配 → 假 FAIL
          （实测 accept_sut_final TC-REQ-068-I01）。

        根因（5 层 why）:
          L1 现象: 访问控制型需求的 I01 用例恒 FAIL，且 FAIL 与产品行为无关。
          L2 直接: 断言针取自 `expected_feedback`（"不同角色获得对应的数据访问与操作权限"），
            它是**设计陈述**，不是页面可观察信号。
          L3 为何: 本决策点只有"把 expected_feedback 当页面文案"这一种断言形态，
            没有"行为型期望"的形态可发。
          L4 为何反复: 与规则兜底分支同一根因（`_fallback_cases._apply_access_control_template`）
            —— 两条生成路径各自解释"访问控制该验什么"，必然各自退化。
          L5 根本: 缺「期望性质 → 断言手段」的匹配规则：**呈现型**期望用文本匹配，
            **行为型**期望必须用行为断言；手段与期望性质错配即产生假 FAIL。

        修复: 期望性质为行为型（`_is_access_restriction_req`：权限语境词与限制/拒绝词
          共现，命中 403/权限不足/不允许/仅具有/越权/重定向登录…）时，
          发 `access_control_check`（低权身份执行受限操作 → 断言系统拒绝）。
          断言构造器与规则兜底分支**同源共用**（`_fallback_cases._access_control_checks`），
          杜绝两条生成路径口径漂移。

        通用性: 拒绝形态（403/无权限/重定向登录）与业务、框架无关，零硬编码。
        """
        try:
            from src.pipeline.step3.case_rules.base import _is_access_restriction_req
            from src.pipeline.step3._fallback_cases import (
                _access_control_checks,
                _restricted_actions,
            )
        except Exception as _e:  # noqa: BLE001
            _log.debug("[intent_checks] 访问控制分支导入失败，回落通用路径: %s", _e)
            return None
        _desc = str(req.get("description") or "")
        if not _is_access_restriction_req(operation or "", _desc, expected or ""):
            return None
        _verbs = _restricted_actions(operation or "", _desc, expected or "")
        _log.info(
            "[intent_checks] ★ 访问控制型需求 → 行为断言 access_control_check（受限操作=%s）",
            _verbs,
        )
        return _access_control_checks(_verbs)

    def _reuse_upstream_checks(
        self, req: dict[str, Any]
    ) -> list[dict[str, Any]] | None:
        """沿用上游已生成的 checks / verification_plan.ui，无可用时返回 None。"""
        existing = req.get("checks")
        if isinstance(existing, list):
            valid = [c for c in existing if isinstance(c, dict) and c]
            if valid:
                _log.info("[intent_checks] 沿用上游 checks: %d 条", len(valid))
                return list(valid)

        plan = req.get("verification_plan")
        if isinstance(plan, dict):
            converted = self._plan_ui_to_checks(plan)
            if converted:
                _log.info(
                    "[intent_checks] 沿用 verification_plan.ui: %d 条", len(converted)
                )
                return converted
        return None

    @staticmethod
    def _extract_short_keywords(text: str, max_len: int = 20) -> str:
        """★ 根因修复(2026-08-08): 从长句 expected_feedback 提取短关键词（≤max_len字符）。

        expected_feedback 常为长句（如"商品管理、订单处理、供应商管理、库存管理等核心功能正常可用"），
        直接作为 text_contains_check 的 expected 搜索文本必然失败（页面不存在该完整句子）。

        策略（通用，零业务硬编码）：
          0. ★ 引号段优先(2026-09-22 needle 污染根修): 需求描述 UI 原文时常以引号包裹
             （"系统提示「用户名或密码错误」"），needle 若带"系统提示"前缀+引号装饰，
             字面 contains 匹配必败（页面渲染的是引号内原文）。有引号段取引号内原文；
          0b. ★ 占位符守卫: 引号内含模板占位符（"共 N 条，第 X/Y 页"的孤立 N/X/Y）
             时机械字面匹配不可靠 → 返回 ""（调用方禁发 text_contains，语义验证
             交给 Agent 引导断言）——机械断言宁缺勿假；
          1. 若 text ≤ max_len 且不含中文顿号/逗号，直接返回
          2. 按中文顿号/逗号拆分，取前2个短词用逗号拼接
          3. 若仍超长，取第1个词
          4. 若第1个词也超长，取前max_len字

        返回 "": 表示无法产出可靠字面 needle（调用方必须跳过 text_contains，
        绝不允许用空串/散文当 needle——空串 contains 恒真是假 PASS，散文是假 FAIL）。

        通用性: 跨系统/跨行业/跨页面，纯文本处理，无业务术语依赖。
        """
        text = (text or "").strip()
        if not text:
            return ""
        # ★ 策略0: 引号段优先（「」『』“”""''，取最内层原文）
        _m = re.search(
            r"[「『“\"']([^「」『』“”\"']{2,})[」』”\"']", text
        )
        if _m:
            _quoted = _m.group(1).strip()
            # 占位符守卫：孤立大写单字母（N/X/Y 等）= 模板占位符，字面匹配不可靠
            if re.search(r"(?<![A-Za-z])[A-Z](?![A-Za-z])", _quoted):
                return ""
            return _quoted if len(_quoted) <= max_len else _quoted[:max_len]
        # ★ 策略0b: 无引号但含孤立大写占位符 → 不可靠
        if re.search(r"(?<![A-Za-z])[A-Z](?![A-Za-z])", text):
            return ""
        # ★ 策略0c: 行为描述守卫(2026-09-24) —— 无引号段的文本若在陈述"系统做什么"
        #   （行为主体 / 行为结果短语），就不是页面字面文案 → 返回 ""。
        #   置于"≤max_len 直接返回"之前，否则 12 字的"系统接受大于0的数字价格"
        #   会被当 needle 放行（假 FAIL 主因）。见模块头 _BEHAVIOR_*_RE 注释。
        if _BEHAVIOR_SUBJECT_RE.search(text) or _BEHAVIOR_RESULT_RE.search(text):
            _log.info(
                "[intent_checks] needle 守卫: 行为描述不作字面 needle → 交语义验证: %r",
                text[:40],
            )
            return ""
        # 剥离常见叙事前缀（"系统提示"/"提示"/"显示"——页面渲染的是其后的内容）
        text = re.sub(r"^(系统提示|提示信息|提示|显示|出现|页面显示|页面出现)[:：]?", "", text).strip()
        if _BEHAVIOR_SUBJECT_RE.search(text) or _BEHAVIOR_RESULT_RE.search(text):
            return ""
        if len(text) <= max_len and "、" not in text and "，" not in text:
            return text
        # 按中文顿号/逗号拆分
        parts = [p.strip() for p in re.split(r"[、,，]", text) if p.strip()]
        if parts:
            # 过滤掉过长的词（>15字）
            short_parts = [p for p in parts if len(p) <= 15]
            if not short_parts:
                short_parts = parts[:1]
            # 取前2个词
            result = ",".join(short_parts[:2])
            if len(result) <= max_len:
                return result
            # 取第1个词
            if len(short_parts[0]) <= max_len:
                return short_parts[0]
        return text[:max_len]

    def _checks_from_entity_tables(
        self, entity_tables: dict[str, Any], module: str = "",
    ) -> list[dict[str, Any]]:
        """★ 根因修复(2026-08-09): 从 entity_tables 提取字段名，生成字段级 text_contains 检查。

        策略：
          1. 根据 module 匹配对应的实体表（如 module="3.1 学生 (Student)" → 匹配 education_student 表）
          2. 优先使用 verbose_name（中文显示名，如"学号"），回退到列名
          3. 排除 ID、STATUS、CREATED_AT 等通用数据库列
          4. 每个字段名生成一条 text_contains 检查，precision="exact"
          5. 追加 no_error 检查

        Args:
            entity_tables: {"表名": {"columns": ["ID","NAME","CODE",...],
                                     "verbose_names": {"CODE": "科目编码", ...}}, ...}
            module: 需求模块名（如"3.1 学生 (Student)"），用于匹配正确的表

        Returns:
            字段级 text_contains checks 列表
        """
        # 通用字段黑名单（在各业务表中都出现，无需验证）
        _GENERIC_COLS = {"ID", "CODE", "STATUS", "REMARK", "REMARKS",
                         "CREATE_TIME", "UPDATE_TIME", "CREATED_AT", "UPDATED_AT",
                         "CREATED_BY", "UPDATED_BY", "CREATE_BY", "UPDATE_BY",
                         "IS_DELETED", "DELETED", "SORT", "SORT_ORDER", "SEQ",
                         "VERSION", "TENANT_ID", "ORG_ID", "COMPANY_ID",
                         "ROW_ID", "RECORD_ID", "UUID", "OBJECT_ID"}

        checks: list[dict[str, Any]] = []

        # 根据 module 匹配对应的表
        # module 格式如 "3.1 学生 (Student)" 或 "1.1 会计科目 (Account)"
        # 提取英文名用于匹配 entity_tables 表名
        _module_lower = (module or "").lower().strip()
        _module_en = ""
        if _module_lower:
            _m_en = re.search(r'\((\w+)\)', _module_lower)
            if _m_en:
                _module_en = _m_en.group(1).lower()
        _matched_tables = []
        if _module_en:
            # 优先用英文名匹配（如 "student" → "education_student"）
            for _tbl in entity_tables:
                _tbl_lower = _tbl.lower()
                if _module_en in _tbl_lower:
                    _matched_tables.append(_tbl)
        if not _matched_tables and _module_lower:
            # 回退：用完整 module 匹配
            for _tbl in entity_tables:
                _tbl_lower = _tbl.lower()
                if _module_lower in _tbl_lower or _tbl_lower in _module_lower:
                    _matched_tables.append(_tbl)
        if not _matched_tables:
            # 无法匹配时，取第一张非空表
            for _tbl in entity_tables:
                _info = entity_tables[_tbl] if isinstance(entity_tables, dict) else {}
                if isinstance(_info, dict) and _info.get("columns"):
                    _matched_tables.append(_tbl)
                    break
        _log.info(
            "[intent_checks] _checks_from_entity_tables: matched_tables=%s (module=%r)",
            _matched_tables, module,
        )

        for _table in _matched_tables:
            _info = entity_tables[_table] if isinstance(entity_tables, dict) else {}
            if not isinstance(_info, dict):
                continue
            _cols = _info.get("columns", [])
            if not isinstance(_cols, list):
                continue
            _verbose_names = _info.get("verbose_names", {})
            for _c in _cols:
                _raw = str(_c).strip() if isinstance(_c, str) else ""
                if not _raw:
                    continue
                # 跳过通用数据库列
                _upper = _raw.upper()
                if _upper in _GENERIC_COLS:
                    continue
                # 使用 verbose_name 如果可用，否则回退到列名
                if _verbose_names and _raw in _verbose_names:
                    _name = _verbose_names[_raw]
                else:
                    _name = _raw
                # 跳过已存在的字段（同名不重复添加）
                if any(c.get("check", {}).get("type") == "text_contains_check"
                       and c.get("expect") == _name for c in checks):
                    continue
                checks.append(
                    self._build_check(
                        "text_contains_check",
                        "body",
                        _name,
                        f"页面展示字段: {_name}",
                        check_expected=_name,
                        precision="exact",
                    )
                )

        # 追加 no_error 检查
        checks.append(
            self._build_check("no_error", "页面", "无错误", "系统无报错")
        )

        if not checks:
            return []

        _log.info(
            "[intent_checks] 从 entity_tables 生成 %d 个字段级 checks (module=%s, 表=%s)",
            len(checks) - 1, module, _matched_tables,
            len(checks),
        )
        return checks

    def _checks_from_expected(
        self, expected_text: str, extra_text: str = ""
    ) -> list[dict[str, Any]]:
        """用需求 expected_feedback 生成可证伪断言。

        ★ 根因修复(2026-08-08): expected_feedback 长句截断为短关键词，
          不再将完整长句作为 text_contains_check 的搜索文本。

        ★ 根因修复(2026-09-24) RC-A: 字段约束型需求改发 field_constraint_check。
          根因: "系统允许输入最多40字符的名称"/"系统接受非负整数库存值" 这类期望
            的性质是**控件 DOM 约束**（maxlength/min/step/type），页面上不存在
            描述这些规则的文字；原实现发语义断言 → AI 只能看页面文本 → 如实回答
            "页面无任何关于'非负整数'的验证说明" → 判 FAIL（实测
            TC-REQ-001-I01/004-I01/005-I01）——验证手段与期望性质错配，属引擎缺陷。
          修复: 期望（或需求 operation/description）可机械解析出控件约束时，
            发 field_constraint_check（Step4 读 DOM 属性判定），
            不再发"文本/语义"断言（用错工具）。
          通用性: 判据来自 parse_field_constraints 的通用语汇解析，零业务硬编码；
            同时消除"needle 守卫返回空 → 仅剩 no_error → 假 PASS"的新风险。
        """
        _blob = f"{expected_text} {extra_text}"
        from src.pipeline.step3._rule_extractor_keywords import (
            is_field_constraint_requirement,
            expects_error_feedback as _expects_error_feedback,
            expects_accept_feedback,
        )

        # ★ 根因修复(2026-09-28): 性能型需求（响应时间/加载时长/秒级约束）**不能
        #   发文本/语义断言**——耗时是页面快照里不可观察的量，AI 语义断言只能
        #   如实回答"页面无关于耗时的说明"→ 恒假 FAIL（实测 TC-REQ-011/023-I01：
        #   期望'页面从请求到内容渲染完成的时间 < 2秒'被判语义不符）。
        #   正确工具 = performance_lcp_check（FCP/LCP 真实测量），与 _fallback_cases
        #   PF01 模板同构；无则跳过该断言（性能由横切 perf 门禁承担，不造假 FAIL）。
        if _is_performance_expectation(_blob):
            _log.info(
                "[intent_checks] 性能型需求 → performance_lcp_check（替代语义断言）: %r",
                expected_text[:50],
            )
            return [
                {
                    "track": "ui",
                    "precision": "exact",
                    "check": {
                        "type": "performance_lcp_check",
                        "selector": "LCP",
                        "expected": _extract_perf_threshold_ms(_blob, 3000),
                        "operator": "less_than_equal",
                    },
                    "expect": "页面首屏渲染耗时 ≤ 阈值（LCP 真实测量）",
                    "description": f"性能型期望: {expected_text[:80]}",
                },
            ]

        # ★ 根因修复(2026-09-28): 拒绝/错误反馈型需求（"商品名称已存在并拒绝提交"/
        #   "必填字段缺失提示错误且不落库"）不走文本/AI 语义断言——文本断言拿
        #   "拒绝提交"当页面 needle 必 0 命中假 FAIL；AI 语义判定又不如 DOM 错误条
        #   可靠（实测 TC-REQ-001/002-I01：引导层错误条检测已 PASS，checks 层
        #   semantic_check 却判 FAIL，双判定口径矛盾）。改发 `field_error_visible_check`
        #   （DOM 活动错误条，跨系统/框架通用），与引导层用同一可观察证据。
        _reject_feedback = _expects_error_feedback(_blob)
        if _reject_feedback:
            _log.info(
                "[intent_checks] 拒绝/错误反馈型需求 → field_error_visible_check: %r",
                expected_text[:50],
            )
            # ★ 根因修复(2026-10-14) RC-AG: 选择器取**单一事实源**
            #   `feedback_selectors.ERROR_SELECTORS`。本处此前内联一份 9 项副本，
            #   缺 .ant-*/.el-*/[data-testid*='validation-error'] 等形态 →
            #   与验证端词表再次漂移（正是 feedback_selectors 文档禁止的做法）。
            from src.pipeline.utils.feedback_selectors import (
                ERROR_SELECTORS as _ERROR_SELECTORS,
                as_csv as _as_csv,
            )

            return [
                {
                    "track": "ui",
                    "precision": "exact",
                    "check": {
                        "type": "field_error_visible_check",
                        "selector": _as_csv(_ERROR_SELECTORS),
                        "expected": "非空错误提示文本",
                        "operator": "not_empty",
                    },
                    "expect": "页面出现错误/校验提示（DOM 反馈条，证明系统拒绝）",
                    "description": f"拒绝反馈型期望: {expected_text[:80]}",
                },
            ]

        if is_field_constraint_requirement(_blob):
            # ★ 根因修复(2026-10-24): 字段约束必须**逐字段**拆分，且只对「控件能力型」
            #   约束发 DOM 断言。根因（实测 TC-REQ-029-I01）: 多字段需求
            #   "必填：商品名称（1-50 字）、分类、价格（大于 0）、库存（非负整数）" 的
            #   四组约束被合并成一个 dict，再套到定位到的**任意**一个 label（实际套到
            #   "分类"的文本输入框按 positive/integer 判定 → 假 FAIL）；字段名还从散文
            #   "在表单字段模块中" 误提取出 "在表单"。判据与拆分见单一事实源
            #   `control_constraint_items`（按分句拆解 + 性质过滤）。
            #   取值域/必填类约束（min/positive/required）客户端与服务端都可承担，
            #   DOM 无属性≠违反需求 → 交由 validation 场景用例（填非法值→拒绝/提示）
            #   行为验证；此处不发 DOM 断言（否则用错工具产生虚假 FAIL）。
            from src.pipeline.step3._rule_extractor_keywords import (
                control_constraint_items as _control_items,
            )

            #   分句必须按**原文边界**切分（expected 与 extra 分别送入）：拼接后的
            #   空格会让"库存（非负整数） 必填：商品名称（1-50 字）"粘成一个分句，
            #   商品的 maxlength 又串到库存上。
            _items = _control_items(expected_text, extra_text)
            if _items:
                _log.info(
                    "[intent_checks] 控件能力型约束 → 逐字段 field_constraint_check: %s",
                    [(i["field"], i["constraints"]) for i in _items],
                )
                return [
                    {
                        "track": "ui",
                        "precision": "exact",
                        "check": {
                            "type": "field_constraint_check",
                            "expected": expected_text[:120],
                            "locate_text": (_it["clause"] or expected_text)[:400],
                            "field_hint": _it["field"],
                            "constraints": _it["constraints"],
                        },
                        "expect": expected_text[:60],
                        "description": "控件级约束校验: %s %s"
                        % (_it["field"] or "(按约束集合定位)", _it["constraints"]),
                    }
                    for _it in _items
                ]
            _log.info(
                "[intent_checks] 约束均为取值域/必填型（非控件能力）→ 不发 DOM 断言，"
                "行为验证由 validation 场景承担: %r",
                expected_text[:50],
            )

        snippet = self._extract_short_keywords(expected_text)
        # ★ 根因修复(2026-09-28): **无具体反馈的接受型期望**（"操作成功,成功"/
        #   "成功"等通用词，Step2 对分页/刷新/字段契约行为型需求硬写的默认反馈）
        #   不可观察——SUT 正常完成跳转/渲染但无 toast/成功条，按"成功文案"判定
        #   恒假 FAIL（实测 REQ-012/017/018-I01: 页面正常显示列表却判"无成功提示"）。
        #   通用规则: 期望只含接受通用词（成功/完成/操作成功）且**无业务实体**时，
        #   操作成功的可观察证据 = 页面主内容正常渲染 + 无致命错误；不追死"成功条"。
        #   业务实体（"新增商品成功"/"删除成功并跳回列表"）仍走原语义判定。
        _acc_blob = (expected_text or "")[:40]
        _accept_only = bool(
            (
                expects_accept_feedback(_blob)
                or expects_accept_feedback(expected_text)
            )
            and not any(
                _kw in _acc_blob
                for _kw in (
                    "商品", "名称", "价格", "库存", "分类", "字段", "数据", "记录",
                    "列表", "删除", "新增", "创建", "更新", "编辑", "提交", "保存",
                    "返回", "重定向", "刷新", "行数", "分页", "详情",
                )
            )
        )
        if _accept_only:
            _log.info(
                "[intent_checks] 无实体接受型期望 → 页面渲染+无错误断言: %r",
                expected_text[:50],
            )
            return [
                self._build_check(
                    "element_exists",
                    _MAIN_SELECTOR,
                    "存在",
                    "操作完成后页面主内容正常渲染（无崩溃）",
                ),
                self._build_check("no_error", "页面", "无错误", "系统无报错"),
            ]
        if not snippet:
            _log.info(
                "[intent_checks] 无有效字面 needle → semantic_check 语义判定: %r",
                expected_text[:60],
            )
            return [
                {
                    "track": "ui",
                    "precision": "fuzzy",
                    "check": {
                        "type": "semantic_check",
                        "expected": expected_text[:_TEXT_LIMIT],
                    },
                    "expect": expected_text[:60],
                    "description": f"语义判定需求期望: {expected_text[:60]}",
                },
            ]
        return [
            self._build_check(
                "text_contains_check",
                "body",
                snippet,
                f"页面出现需求期望反馈: {expected_text[:60]}",
                operator="contains",
                check_expected=snippet,
                precision="fuzzy",
            ),
            self._build_check("no_error", "页面", "无错误", "系统无报错"),
        ]

    def _placeholder_checks(self) -> list[dict[str, Any]]:
        """最小占位断言：仅表达页面可访问 + 无报错，显式标注待补。"""
        return [
            self._build_check(
                "element_exists",
                _MAIN_SELECTOR,
                "存在",
                "[占位待补]页面主内容区加载成功",
            ),
            self._build_check("no_error", "页面", "无错误", "系统无报错"),
        ]

    def _plan_ui_to_checks(self, plan: dict[str, Any]) -> list[dict[str, Any]]:
        """把 verification_plan 的 ui 轨断言转成 Step3 checks 格式。

        兼容两种落库形态：``{"ui": [assertion, ...]}`` 与 ``{"ui": {"checks": [...]}}``。

        ★ 根因修复(2026-08-08): verification_plan 断言文本也可能是长句，
          用 _extract_short_keywords 提取短关键词作为搜索文本。
        """
        raw = plan.get("ui")
        if isinstance(raw, dict):
            raw = raw.get("checks")
        if not isinstance(raw, list):
            return []

        checks: list[dict[str, Any]] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            assertion = str(item.get("assertion", "") or "").strip()
            if not assertion:
                continue
            snippet = self._extract_short_keywords(assertion)
            if not snippet:
                # ★ needle 不可靠(占位符模板) → 跳过该断言的机械 text_contains
                _log.info(
                    "[intent_checks] 断言文本无可靠字面 needle，跳过 text_contains: %r",
                    assertion[:60],
                )
                continue
            evidence = str(item.get("evidence", "") or "").strip().lower()
            if evidence == "no_error":
                checks.append(self._build_check("no_error", "页面", "无错误", snippet))
            else:
                checks.append(
                    self._build_check(
                        "text_contains_check",
                        "body",
                        snippet,
                        assertion[:60],
                        operator="contains",
                        check_expected=snippet,
                        precision="fuzzy",
                    )
                )
        return checks
