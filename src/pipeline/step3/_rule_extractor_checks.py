# -*- coding: utf-8 -*-
from __future__ import annotations
from src.core.guarded import guard

"""_rule_extractor_checks — 从 _rule_extractor.py 拆分（D.1g 架构债清理 2026-07-25，§4.3 单文件≤800行）。

职责：checks 相关函数。
逻辑不变，仅结构重构。re-export 保持调用兼容。
"""
# ★ 注：Python 3.9 兼容的 `from __future__ import annotations` 已在文件头部（第2行）声明，
#   此处不得重复声明——重复的 future import 出现在模块 docstring 之后会触发
#   SyntaxError: "from __future__ imports must occur at the beginning of the file"。
import json
import logging
import re
from collections import Counter
from typing import Any

# ★ RC-AE(2026-09-25): 反馈条选择器统一从全引擎单一事实源导入（Step3/Step4/Agent 同源）。
from src.pipeline.utils.feedback_selectors import ERROR_SELECTOR_CSV as _ERROR_SELECTOR_CSV

_log = logging.getLogger(__name__)


def _shorten_expected_feedback(text: str, max_len: int = 20) -> str:
    """★ 根因修复(2026-08-08): 从 expected_feedback 长句提取短关键词（≤max_len字符）。

    expected_feedback 常为长句（如"商品管理、订单处理、供应商管理、库存管理等核心功能正常可用"），
    直接作为 text_contains_check 的 expected 搜索文本必然失败（页面不存在该完整句子）。

    策略（通用，零业务硬编码）：
      1. 若 text ≤ max_len 且不含中文顿号/逗号，直接返回
      2. 按中文顿号/逗号拆分，取前2个短词用逗号拼接
      3. 若仍超长，取第1个词
      4. 若第1个词也超长，取前max_len字

    通用性: 跨系统/跨行业/跨页面，纯文本处理，无业务术语依赖。
    """
    text = (text or "").strip()
    if not text:
        return ""
    if len(text) <= max_len and "、" not in text and "，" not in text:
        return text
    parts = [p.strip() for p in re.split(r"[、,，]", text) if p.strip()]
    if parts:
        short_parts = [p for p in parts if len(p) <= 15]
        if not short_parts:
            short_parts = parts[:1]
        result = ",".join(short_parts[:2])
        if len(result) <= max_len:
            return result
        if len(short_parts[0]) <= max_len:
            return short_parts[0]
    return text[:max_len]


def _build_ui_check_by_operation(
    operation: str,
    expected: str,
    matched_apis: list[dict],
    rag_data: dict,
    actions: list[dict] = None,
) -> list[dict]:
    """★ Fix18 (2026-07-07): 按 operation 类型生成具体 UI 断言。
    ★ Fix25 (2026-07-07): 断言注入用户期望（expected_feedback → check.expected）
    ★ Fix41 (2026-07-21): 查询类断言必须验证搜索结果与查询词一致性，
          原逻辑只检查表格存在，导致假PASS。覆盖所有查询/搜索/筛选类需求。

    原bug: 硬编码 "成功,保存成功,操作成功,提交成功,完成" 通用模板，
          跟具体操作无关，导致 Step4 的 text_contains 校验必然失败
          （如查询类操作不会出现"保存成功"提示）

    Fix18: 根据 operation 关键词映射到具体的成功提示文本和页面状态断言
    Fix25: 当需求 expected_feedback 非空时，把它注入 check.expected，
          让 Step4 实际校验用户期望文本，而非只校验硬编码模板。
          断言 = 用户期望（主）+ 通用提示词（兜底）
    Fix41: 查询类返回多个 checks：
          1) 结果列表存在
          2) 结果文本包含查询关键词（与 actions 中 fill value 一致）
          契约：§4.1 "一个需求→一个完整测试用例"，断言必须符合用户期望
                §13.3 Step3 checks 必须匹配 actions 语义
    """
    op_lower = operation.lower() if operation else ""
    actions = actions or []

    # ★ Fix25: 通用提示词（按 operation 类型兜底），当无用户期望时使用
    _HINTS_BY_OP = {
        "query": "查询,搜索,筛选,共,条,记录",
        "create": "新增成功,添加成功,创建成功,保存成功,创建,新增",
        "update": "修改成功,更新成功,保存成功,编辑成功",
        "delete": "删除成功,移除成功,删除,移除",
        "import": "导入成功,上传成功,导入完成,上传完成",
        "export": "导出成功,下载成功,导出完成,下载完成",
        "toggle": "操作成功,启用成功,停用成功,状态切换成功",
        "generic": "成功,完成,操作成功",
    }

    _op_class = _classify_operation_for_check(operation)
    _hint = _HINTS_BY_OP.get(_op_class, _HINTS_BY_OP["generic"])

    # ★ v4.6 修复 (2026-07-23, §13.3): 断言期望 = 通用提示词 + operation核心动词+成功
    #   根因(§10.9):
    #     1. 表层: text_contains:✗ — expected="成功,完成,操作成功" 不匹配系统"复制成功"提示
    #     2. 中层: _classify_operation_for_check("用户点击复制版本")→generic, 提示词无"复制成功"
    #     3. 根因: 静态_HINTS_BY_OP无法覆盖所有操作类型(复制/发布/审批/推送/同步...)
    #     4. 架构: 操作特定成功提示应从operation动态提取,而非穷举静态映射表
    #   修复: _check_expected = _hint + "," + 核心动词+"成功"
    #   通用性(§12.4): 跨系统/跨行业/跨操作 — 任何operation都能提取核心动词生成对应成功提示
    _op_core = _extract_op_core_verb(operation)
    if _op_core and (_op_core + "成功") not in _hint:
        _check_expected = _hint + "," + _op_core + "成功"
    else:
        _check_expected = _hint

    # ★ Fix25: expect 描述 = 用户期望（主）or operation 类型描述（兜底）
    _DESC_BY_OP = {
        "query": "列表加载完成并展示数据",
        "create": "新建成功并显示提示",
        "update": "编辑保存成功并显示提示",
        "delete": "删除成功并显示提示",
        "import": "导入成功并显示提示",
        "export": "导出成功并显示提示",
        "toggle": "状态切换成功并显示提示",
        "generic": "操作成功并显示提示",
    }
    _desc = expected or _DESC_BY_OP.get(_op_class, "操作成功并显示提示")

    # ★ Fix41: 查询类操作提取 fill value，断言结果与查询词一致
    _query_value = ""
    if _op_class == "query":
        for a in actions:
            if a.get("type") == "fill" and a.get("value"):
                _query_value = str(a.get("value"))
                break

    # ★ Fix25 / Fix41: 按操作类别选择合适的页面元素选择器（通用，无业务硬编码）
    if _op_class == "query":
        checks = [
            {
                "track": "ui",
                "precision": "fuzzy",
                "check": {
                    "type": "element_exists",
                    "selector": "[role='table'], .table, table, .ant-list, .el-table__body, [class*='list'], [class*='table']",
                },
                "expect": "搜索结果列表/表格存在",
                "assertion": "element_exists",
                "target_hint": "数据列表表格",
            },
        ]
        # 若知道查询词，追加结果文本包含查询词的断言
        if _query_value:
            checks.append(
                {
                    "track": "ui",
                    "precision": "fuzzy",
                    "check": {
                        "type": "text_contains_check",
                        "expected": _query_value,
                        "operator": "contains",
                        "selector": "[role='table'], .table, table, .ant-list, .el-table__body, [class*='list'], [class*='table']",
                    },
                    "expect": f"搜索结果包含查询关键词 '{_query_value}'",
                }
            )
        # 兜底：通用查询提示词校验
        checks.append(
            {
                "track": "ui",
                "precision": "fuzzy",
                "check": {
                    "type": "text_contains_check",
                    "expected": _check_expected,
                    "operator": "contains",
                    "selector": "[role='table'], .table, table, .ant-list, .el-table__body, [class*='list'], [class*='table']",
                },
                "expect": _desc,
            }
        )
        return checks
    else:
        # 其他类：断言操作成功提示（通用 ARIA + 语义选择器）
        return [
            {
                "track": "ui",
                "precision": "fuzzy",
                "check": {
                    "type": "text_contains_check",
                    "expected": _check_expected,
                    "operator": "contains",
                    "selector": "[role='status'], [role='alert'], [class*='message'], [class*='notice'], [class*='toast'], [class*='alert'], [class*='tip'], [class*='success'], [class*='feedback'], .success, .alert-success",
                },
                "expect": _desc,
            }
        ]


def _classify_operation_for_check(operation: str) -> str:
    """★ Fix32/Fix33 共用：按 operation 关键词分类（通用，无业务硬编码）。

    返回: query/create/update/delete/import/export/toggle/generic
    """
    op = operation or ""
    if any(k in op for k in ["查询", "搜索", "筛选", "列表", "getList"]):
        return "query"
    if any(k in op for k in ["新建", "新增", "添加", "创建", "create", "add"]):
        return "create"
    if any(k in op for k in ["编辑", "修改", "更新", "edit", "update", "modify"]):
        return "update"
    if any(k in op for k in ["删除", "移除", "del", "remove"]):
        return "delete"
    if any(k in op for k in ["导入", "上传", "import", "upload"]):
        return "import"
    if any(k in op for k in ["导出", "下载", "export", "download"]):
        return "export"
    if any(k in op for k in ["启用", "停用", "激活", "enable", "disable"]):
        return "toggle"
    return "generic"


def _extract_op_core_verb(operation: str) -> str:
    """★ v4.6 (2026-07-23): 从 operation 提取核心动词(2字)，用于生成操作特定成功提示。

    '用户点击复制版本' → '复制'
    '用户点击批量导入' → '导入'
    '用户点击删除'     → '删除'（已知类型也提取，增加提示词覆盖）

    通用性(§12.4): 跨系统/跨行业/跨操作 — 任何operation都能提取核心动词
    """
    import re as _re

    if not operation:
        return ""
    _text = operation.strip()
    # 1. 剥离开头主语
    _text = _re.sub(
        r"^(用户|系统|管理员|操作员|用户角色|前端|后台|客户端|平台)[之的]?", "", _text
    )
    # 2. 剥离动词前缀（含双动词组合）
    _text = _re.sub(
        r"^(点击|执行|查询|导入|加载|查看|操作|访问|进入|选择|输入|上传|下载|导出|"
        r"提交|保存|确认|取消|删除|编辑|修改|新建|新增|添加|创建|启用|停用|刷新|重置|"
        r"触发|调用|打开|关闭|展示|显示|返回|完成|支持|需要|允许|浏览|检索|筛选|查找)",
        "",
        _text,
    )
    # 3. 剥离修饰词
    _text = _re.sub(
        r"(批量|单个|快速|定时|自动|手动|本地|远程|在线|离线|"
        r"列表中的|页面中的|对应的|某条|某个|该条|这条|一条|当前|所有|全部)",
        "",
        _text,
    )
    _text = _text.strip()
    # 4. 取前2个中文字符作为核心动词
    _cn = _re.findall(r"[\u4e00-\u9fff]", _text)
    if len(_cn) < 2:
        return ""
    return "".join(_cn[:2])


def _select_primary_api(matched_apis: list[dict], operation: str) -> dict:
    """★ Fix32 (2026-07-07): 按 operation 类型智能选择主操作 API。

    通用策略（无业务硬编码，按 path 语义关键词匹配）：
    - 查询类（查询/搜索/筛选/列表/getList）→ 优先 path 含 list/getList/search/page 的 API
    - 新增类（新增/添加/创建/保存）→ 优先 path 含 create/add/insert/save 的 API
    - 编辑类（编辑/修改/更新）→ 优先 path 含 edit/update/modify 的 API
    - 删除类（删除/移除）→ 优先 path 含 delete/remove/del 的 API
    - 无匹配 → 返回第一个 API 兜底

    契约: §4.2 通用性 — 只用 path 语义关键词，不硬编码业务功能名
    """
    if not matched_apis:
        return {}
    if len(matched_apis) == 1:
        return matched_apis[0]

    # operation 类型 → path 关键词映射（通用，无业务硬编码）
    _OP_TO_PATH_KEYWORDS = {
        "query": ["getlist", "list", "search", "page", "query"],
        "create": ["create", "add", "insert", "save", "submit"],
        "update": ["edit", "update", "modify", "save"],
        "delete": ["delete", "remove", "del"],
        "import": ["upload", "import", "add"],
        "export": ["export", "download"],
    }

    op_class = _classify_operation_for_check(operation)
    if not op_class:
        return matched_apis[0]

    keywords = _OP_TO_PATH_KEYWORDS.get(op_class, [])
    if not keywords:
        return matched_apis[0]

    # 按 path 关键词匹配打分，选最高分的
    best_api = None
    best_score = 0
    for api in matched_apis:
        path = (api.get("path", "") or "").lower()
        score = 0
        for kw in keywords:
            if kw in path:
                score += 1
        if score > best_score:
            best_score = score
            best_api = api

    return best_api if best_api else matched_apis[0]


def _build_api_error_checks(matched_apis: list[dict], operation: str) -> list[dict]:
    """API-only 异常用例断言：无效参数应返回非 200 错误码。

    通用策略（无业务硬编码）：
    - 取主操作 API 端点
    - 断言：请求体含非法字符时，响应 code 不为 0/200（期望错误码）
    - 断言：响应 msg 包含错误提示（非成功提示）
    """
    api = _select_primary_api(matched_apis, operation)
    _api_path = api.get("path", "")
    _api_method = str(api.get("method", "") or "").upper()
    if not _api_path:
        return []
    return [
        {
            "track": "api",
            "precision": "exact",
            "check": {
                "type": "api_response_body_check",
                "url_pattern": _api_path,
                "method": _api_method,
                "expected_field": "code",
                "expected_value": "200,0",
                "operator": "not_in",
            },
            "expect": "异常输入时 code 不为 200/0（拒绝非法数据）",
        },
        {
            "track": "api",
            "precision": "fuzzy",
            "check": {
                "type": "api_response_body_check",
                "url_pattern": _api_path,
                "method": _api_method,
                "expected_field": "msg",
                "expected_value": "成功,操作成功,保存成功",
                "operator": "not_contains",
            },
            "expect": "异常输入时 msg 不包含成功提示",
        },
    ]


def _build_api_boundary_checks(matched_apis: list[dict], operation: str) -> list[dict]:
    """API-only 边界用例断言：超长/特殊字符等边界值应被正确处理。

    通用策略（无业务硬编码）：
    - 取主操作 API 端点
    - 断言：边界值输入时，响应 code 不崩溃（返回 200 或指定错误码）
    - 断言：响应 msg 有明确提示（非空）
    """
    api = _select_primary_api(matched_apis, operation)
    _api_path = api.get("path", "")
    _api_method = str(api.get("method", "") or "").upper()
    if not _api_path:
        return []
    return [
        {
            "track": "api",
            "precision": "exact",
            "check": {
                "type": "api_response_body_check",
                "url_pattern": _api_path,
                "method": _api_method,
                "expected_field": "code",
                "expected_value": "200,0,400,422",
                "operator": "in",
            },
            "expect": "边界值输入时 code 为 200/0（接受）或 400/422（拒绝但不崩溃）",
        },
        {
            "track": "api",
            "precision": "fuzzy",
            "check": {
                "type": "api_response_body_check",
                "url_pattern": _api_path,
                "method": _api_method,
                "expected_field": "msg",
                "expected_value": "",
                "operator": "not_empty",
            },
            "expect": "边界值输入时 msg 非空（有明确响应）",
        },
    ]


def _build_checks(
    req: dict,
    required_tracks: list[str],
    matched_apis: list[dict],
    rag_data: dict,
    selected_asserts: list[dict],
    actions: list[dict] = None,
    error_case: bool = False,
    boundary_case: bool = False,
    data_dict: dict | None = None,
) -> list[dict]:
    """构建 checks 列表（结合 RAG API response_fields 做精确断言）。

    通用策略：
    - track="ui" → 用 element_exists / no_error 断言页面元素
    - track="api" → 用 api_response_body_check 断言 API 响应字段（从 RAG 提取 response_fields）
    - track="db" → 用 db_field_value 断言 DB 字段（从 RAG 提取表字段名）

    ★ 根因修复(2026-08-09): 新增 data_dict 参数，用于 I01 类型用例生成字段级 text_contains 检查。
    """
    operation = str(req.get("operation", "") or "").strip()
    # ★ 根因修复(2026-08-08): expected_feedback 长句截断为短关键词
    #   expected_feedback 常为"商品管理、订单处理、供应商管理、库存管理等核心功能正常可用"这类长句，
    #   直接作为 text_contains_check 搜索文本必然失败。修复后提取短关键词（≤20字）。
    _expected_raw = str(req.get("expected_feedback", "") or "").strip()
    expected = _shorten_expected_feedback(_expected_raw)
    checks = []

    # 1. UI 断言（按 operation 类型生成具体断言）
    # ★ Fix (2026-07-14): UI 断言仅在 required_tracks 包含 "ui" 时添加。
    #   原逻辑硬编码 `or True` 导致 API 接口需求也生成 UI 断言，
    #   与 required_tracks=["api"] 的路由意图冲突。
    _assert_ids = {a["action_id"] for a in selected_asserts}
    if "ui" in required_tracks:
        if error_case:
            checks.append(
                {
                    "track": "ui",
                    "precision": "exact",
                    "check": {
                        "type": "element_exists",
                        # ★ RC-AE(2026-09-25): 改引全引擎单一事实源（原本地词表漏
                        #   `.flash-danger` / `[data-testid*='error']` 等框架无关形态
                        #   → 真实错误条被扫不到 → 恒 0 命中假 FAIL）。
                        "selector": _ERROR_SELECTOR_CSV,
                    },
                    "expect": "阻止非法数据提交并显示错误信息",
                }
            )
        else:
            # ★ Fix18 (2026-07-07): UI 断言按 operation 类型生成具体断言
            #   原bug: 硬编码 "成功,保存成功,操作成功,提交成功,完成" 通用模板，
            #         跟具体操作无关，导致 Step4 的 text_contains 校验必然失败
            #   修复: 根据 operation 关键词映射到具体的成功提示文本和页面状态断言
            # ★ Fix41 (2026-07-21): 传入 actions 让查询类断言关联 fill value
            _ui_checks = _build_ui_check_by_operation(
                operation, expected, matched_apis, rag_data, actions
            )
            checks.extend(_ui_checks)

    # 2. API 断言（从 RAG API response_fields 提取）
    if "api" in required_tracks and matched_apis:
        # ★ Fix32 (2026-07-07): 按 operation 类型智能选择主操作 API，而非简单取第一个
        #   原 bug: matched_apis[0] 总是取 trace_links 第一个 API，
        #          REQ-001 的 trace_links = [GET typeList, POST getList]，
        #          查询辅材信息应断言 POST getList（分页查询），却断言了 GET typeList（类型列表）
        #   修复: 按 operation 关键词匹配 API path，选最匹配的作为主操作 API
        #   契约: §4.1 "规则优先" — 有 RAG API 关联就应该选对 API，不烧 token 让 AI 纠正
        api = _select_primary_api(matched_apis, operation)
        res_fields = api.get("res_fields", "")
        _api_path = api.get("path", "")
        _api_method = str(api.get("method", "") or "").upper()
        # ★ Fix36 (2026-07-07): 异常用例跳过 API code/msg 断言
        #   原因: 异常用例（非法字符）期望 API 返回错误码/错误消息，
        #         但 code/msg 断言用的是正常期望（code=0/200, msg contains expected_feedback），
        #         会导致异常用例的 API 断言必然失败，误导用户以为是被测系统 bug
        #   修复: 异常用例只保留 UI error 断言（"阻止非法数据提交并显示错误信息"），
        #         不做 API 层精确断言（因为不知道具体错误码/错误消息是什么）
        #   契约: §4.2 "通用性" — 不硬编码错误码/错误消息关键词
        #         §5 "区分未配置(跳过)和配置但失败(阻断)" — 异常用例 API 断言无法精确配置 → 跳过
        if res_fields and not error_case:
            # 从 "code(number), msg(string), data(array)" 提取字段
            _fields = re.findall(r"(\w+)\s*\(([^)]*)\)", res_fields)
            if _fields:
                # 对 code 字段做精确断言
                code_field = next((f for f in _fields if f[0] == "code"), None)
                if code_field:
                    # ★ Fix35 (2026-07-07): 生成 Step4 能执行的标准 dict 格式
                    #   原 bug: check 字段是字符串描述（"API POST ... 响应码正确"），
                    #          Step4 _api.py 的字符串路径不校验（不含"200"/"成功"关键词），
                    #          dict 路径又被 isinstance(check, dict) 跳过 → 断言被静默忽略
                    #   修复: check 字段改为标准 dict（type/url_pattern/expected_field/expected_value/operator），
                    #          Step4 _api.py 路由到 _check_response_body 执行
                    #   契约: §4.4 "输出格式 = 下游输入格式" — Step3 输出 = Step4 输入，不插入转换层
                    checks.append(
                        {
                            "track": "api",
                            "precision": "exact",
                            "check": {
                                "type": "api_response_body_check",
                                "url_pattern": _api_path,
                                "method": _api_method,
                                "expected_field": "code",
                                "expected_value": "0,200",
                                "operator": "in",
                            },
                            "expect": "code=200 或 code=0（成功）",
                        }
                    )
                # 对 msg 字段做模糊断言
                msg_field = next((f for f in _fields if f[0] == "msg"), None)
                if msg_field:
                    checks.append(
                        {
                            "track": "api",
                            "precision": "fuzzy",
                            "check": {
                                "type": "api_response_body_check",
                                "url_pattern": _api_path,
                                "method": _api_method,
                                "expected_field": "msg",
                                "expected_value": expected or "成功",
                                "operator": "contains",
                            },
                            "expect": expected or "msg 包含成功信息",
                        }
                    )

        # ★ Fix33 (2026-07-07): 查询类操作增加 data 内容断言（常识校验）
        #   原因: 查询类操作只断言 code=200/msg 不够，code=200 但 data.total=0 说明查询无结果
        #   修复: 对查询类操作（operation 含查询/搜索/筛选/列表），从 res_fields 提取 data 字段，
        #         断言 data.total > 0 或 data.rows 非空（查询返回数据是常识性校验）
        # ★ Fix33b (2026-07-07): error_case 时跳过 data.total 断言
        #   原因: 异常用例（非法字符）期望查询失败或无结果，不断言 data.total > 0
        #   契约: §4.1 "规则优先" — 常识性校验应该规则生成，不烧 token 让 AI 补
        #         §4.2 "通用性" — 只看 res_fields 结构，不硬编码业务字段
        _op_class = _classify_operation_for_check(operation)
        if _op_class == "query" and res_fields and not error_case:
            # res_fields 含 total(number) → 断言 data.total > 0
            if "total" in res_fields and "number" in res_fields:
                checks.append(
                    {
                        "track": "api",
                        "precision": "exact",
                        "check": {
                            "type": "api_response_body_check",
                            "url_pattern": _api_path,
                            "expected_field": "data.total",
                            "expected_value": 0,
                            "operator": "gt",
                        },
                        "expect": "data.total > 0（查询结果非空）",
                    }
                )

    # 3. DB 断言（从 RAG db_table 提取字段名 + 数据流验证）
    _op_class = _classify_operation_for_check(operation)
    _is_write_op = _op_class in ("create", "update", "delete")
    if "db" in required_tracks and rag_data.get("db_tables"):
        db_table = rag_data["db_tables"][0]
        table_name = db_table.get("name", "")
        columns = db_table.get("columns", [])
        if table_name and columns:
            if _is_write_op:
                # ★ 数据流验证(2026-08-09): 写操作生成三阶段数据流 check
                #   跟踪数据从"创建→存储→展示"的完整链路
                #   阶段1: UI 操作成功提示验证
                checks.append({
                    "track": "ui",
                    "precision": "exact",
                    "check": {"type": "text_contains"},
                    "expect": "成功",
                    "description": "阶段1: UI 操作成功提示验证",
                    "data_flow_stage": "ui_feedback",
                })
                #   阶段2: DB 持久化验证
                checks.append({
                    "track": "db",
                    "precision": "exact",
                    "check": {
                        "type": "db_record_exists_check",
                        "table": table_name,
                    },
                    "expect": "数据已写入DB",
                    "description": "阶段2: DB 持久化验证",
                    "data_flow_stage": "db_persistence",
                })
                #   阶段3: UI 回读展示验证（从 actions 提取 fill 值作为回读验证依据）
                _query_value = ""
                if actions:
                    for a in actions:
                        if a.get("type") == "fill" and a.get("value"):
                            _query_value = str(a.get("value"))
                            break
                if not _query_value:
                    _query_value = "新创建的数据"
                checks.append({
                    "track": "ui",
                    "precision": "exact",
                    "check": {"type": "text_contains"},
                    "expect": _query_value,
                    "description": "阶段3: UI 回读展示验证",
                    "data_flow_stage": "ui_readback",
                })
                _log.info(
                    "[DataFlow] 规则提取器生成数据流验证: op=%s table=%s => 3 阶段 check",
                    operation[:40] if operation else "?",
                    table_name[:30],
                )
            else:
                # 非写操作：保持原有简单 DB 断言
                first_col = columns[0]
                checks.append(
                    {
                        "track": "db",
                        "precision": "exact",
                        "check": f"DB 表 {table_name} 数据正确写入",
                        "expect": f"{first_col['name']} 字段有值",
                        "assertion": "db_field_value",
                        "target_hint": f"{table_name}.{first_col['name']}",
                    }
                )

    # ★ 根因修复(2026-08-09): I01 分支 — 字段级 text_contains 检查
    #   从 data_dict 提取字段名，替代通用 text_contains 检查
    _case_id = str(req.get("case_id", "") or req.get("req_id", "") or "")
    _operation = operation or ""
    _is_i01 = "I01" in _case_id or "字段" in _operation or "字段展示" in _operation
    if _is_i01 and data_dict:
        with guard("_build_checks: from ._rule_extractor_utils import (", log=_log, level=logging.WARNING):
            from ._rule_extractor_utils import (
                _extract_field_names_from_data_dict,
                _build_field_checks_for_i01,
            )
            _field_names = _extract_field_names_from_data_dict(data_dict)
            if _field_names:
                checks = _build_field_checks_for_i01(_field_names, checks)
                _log.info(
                    "[RuleExtract] ★ I01字段级检查(_build_checks): "
                    "case=%s fields=%d -> %s",
                    _case_id, len(_field_names), _field_names[:5],
                )

    # 兜底：如果没有任何 checks，添加通用断言
    if not checks:
        checks.append(
            {
                "track": "ui",
                "precision": "fuzzy",
                "check": (
                    f"执行{operation}后页面正常响应" if operation else "页面正常响应"
                ),
                "expect": expected or "功能正常响应",
                "assertion": "no_error",
                "target_hint": "页面无错误",
            }
        )

    return checks
