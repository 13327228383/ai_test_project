# -*- coding: utf-8 -*-
"""
JSON 响应解析与修复模块
========================
提供对 LLM 返回文本中 JSON 内容的健壮解析，处理常见格式问题：
- markdown 代码块包裹
- 尾逗号、注释、控制字符、非法转义
- 截断 JSON 补全
- 多个 JSON 对象/数组抽取

设计原则：纯函数，无外部依赖（除标准库 json/re/threading）。
"""

import json
from src.core.guarded import guard
import logging
import re
import threading
from typing import Any

_log = logging.getLogger(__name__)

# ★ KEEP-23: JSON 修复状态机统计
_JSON_REPAIR_STATS = {
    "total_parse_calls": 0,
    "success_count": 0,
    "failed_count": 0,
    "layer_1_extract_count": 0,  # markdown/正文中提取 JSON 主体
    "layer_2_normalize_count": 0,  # 尾逗号/注释/控制字符/非法转义等规范化
    "layer_3_truncate_fix_count": 0,  # 截断 JSON 补全
}
_JSON_REPAIR_LOCK = threading.Lock()


def _record_json_repair_stat(key: str, amount: int = 1) -> None:
    """记录 JSON 修复状态机计数，失败不影响主流程。"""
    with guard("_record_json_repair_stat: with _JSON_REPAIR_LOCK: _JSON_REPAIR", log=_log):
        with _JSON_REPAIR_LOCK:
            _JSON_REPAIR_STATS[key] = _JSON_REPAIR_STATS.get(key, 0) + amount


def parse_json_response(raw: str) -> dict[str, Any]:
    """
    解析 AI 返回的原始文本中的 JSON 内容。
    健壮处理 LLM 常见格式问题：markdown 包裹、尾逗号、注释、截断。

    返回：
        {"success": True, "data": <parsed_json>}
        或 {"success": False, "error": "..."}
    """
    _record_json_repair_stat("total_parse_calls")

    # 1. 去除 markdown 代码块包裹
    raw = raw.strip()
    original_raw = raw
    m = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", raw, re.DOTALL)
    if m:
        raw = m.group(1).strip()
        _record_json_repair_stat("layer_1_extract_count")

    def _try_load_candidate(s: str):
        before = s
        s = re.sub(r",\s*}", "}", s)
        s = re.sub(r",\s*]", "]", s)
        s = re.sub(r"//[^\n]*", "", s)
        s = re.sub(r"/\*.*?\*/", "", s, flags=re.DOTALL)
        s = escape_json_control_chars(s)
        s = fix_doubled_json_chars(s)
        s = fix_invalid_json_escapes(s)
        if s != before:
            _record_json_repair_stat("layer_2_normalize_count")
        try:
            return json.loads(s), s, None
        except json.JSONDecodeError as err:
            fixed = fix_truncated_json(s)
            if fixed != s:
                try:
                    _record_json_repair_stat("layer_3_truncate_fix_count")
                    return json.loads(fixed), fixed, None
                except json.JSONDecodeError as err2:
                    fixed = None
            # ★ 字符串内嵌未转义 JSON 片段恢复(2026-08-31)：
            #   模型常把「响应示例/请求体/接口返回」原样写进字符串值，内层双引号未转义
            #   导致整段 JSON 解析失败（如 expected_feedback="返回HTTP200，响应含{"status": "ok",..."}"）。
            #   fix_unescaped_embedded_json 把字符串值里内嵌 {…}/[…] 片段中的引号补转义，
            #   使这类「健康检查/错误码/字段契约」需求不被丢弃而回退劣质规则条。
            fixed2 = fix_unescaped_embedded_json(s)
            if fixed2 != s:
                try:
                    _record_json_repair_stat("layer_2_normalize_count")
                    return json.loads(fixed2), fixed2, None
                except json.JSONDecodeError as err3:
                    return None, s, err3
            return None, s, err

    def _extract_balanced_candidates(text: str):
        """抽取所有顶层 JSON 对象/数组，处理模型输出多个 JSON 或夹解释文本的情况。"""
        candidates = []
        pairs = {"{": "}", "[": "]"}
        for i, ch in enumerate(text):
            if ch not in pairs:
                continue
            stack = [pairs[ch]]
            in_str = False
            esc = False
            for j in range(i + 1, len(text)):
                cj = text[j]
                if in_str:
                    if esc:
                        esc = False
                    elif cj == "\\":
                        esc = True
                    elif cj == '"':
                        in_str = False
                    continue
                if cj == '"':
                    in_str = True
                elif cj in pairs:
                    stack.append(pairs[cj])
                elif stack and cj == stack[-1]:
                    stack.pop()
                    if not stack:
                        candidates.append(text[i : j + 1])
                        break
            if len(candidates) >= 8:
                break
        return candidates

    # 2. 定位 JSON 主体
    # 优先检查是否整体是数组 [{...},{...}]
    if raw.startswith("["):
        start, end = 0, raw.rfind("]")
        if end == -1:
            end = len(raw)  # 截断的数组，取全部
    else:
        # 对象：找到最外层 { 和 }
        start = raw.find("{")
        end = raw.rfind("}")
        if start == -1:
            _record_json_repair_stat("failed_count")
            return {
                "success": False,
                "error": "未找到 JSON 结构; 原始内容前300字: %s" % raw[:300],
            }
        if end == -1:
            # 截断：没有 }，取从 { 到末尾，交给 fix_truncated_json 补全
            end = len(raw)

    json_str = raw[start : end + 1] if end < len(raw) else raw[start:]
    if raw != original_raw or start > 0 or end < len(raw) - 1:
        _record_json_repair_stat("layer_1_extract_count")

    # 3. 尝试解析
    parsed, normalized, parse_err = _try_load_candidate(json_str)
    if parsed is not None:
        _record_json_repair_stat("success_count")
        return {"success": True, "data": parsed}

    # 4. 模型常见问题：输出多个对象、前后夹解释文本、数组后又重复输出对象。
    #    逐个抽取平衡 JSON，优先返回数组；多个对象时合并成数组，让 Step3 继续走意图资产校验。
    candidates = _extract_balanced_candidates(raw)
    parsed_items = []
    for cand in candidates:
        item, _norm, _err = _try_load_candidate(cand)
        if item is None:
            continue
        if isinstance(item, list):
            _record_json_repair_stat("layer_1_extract_count")
            _record_json_repair_stat("success_count")
            return {"success": True, "data": item}
        if isinstance(item, dict):
            parsed_items.append(item)
    if parsed_items:
        _record_json_repair_stat("layer_1_extract_count")
        _record_json_repair_stat("success_count")
        return {
            "success": True,
            "data": parsed_items if len(parsed_items) > 1 else parsed_items[0],
        }

    _record_json_repair_stat("failed_count")
    return {
        "success": False,
        "error": "JSON 解析失败: %s; 原始内容前300字: %s"
        % (parse_err, normalized[:300]),
    }


def escape_json_control_chars(json_str: str) -> str:
    """转义/移除 JSON 字符串中的原始控制字符（AI 偶尔生成未转义的字符）。"""
    result = []
    in_string = False
    escape_next = False
    for ch in json_str:
        if escape_next:
            result.append(ch)
            escape_next = False
            continue
        if ch == "\\":
            result.append(ch)
            escape_next = True
            continue
        if ch == '"':
            in_string = not in_string
            result.append(ch)
            continue
        if in_string:
            if ch == "\n":
                result.append("\\n")
            elif ch == "\r":
                result.append("\\r")
            elif ch == "\t":
                result.append("\\t")
            elif ord(ch) < 0x20:
                result.append("\\u%04x" % ord(ch))
            else:
                result.append(ch)
        else:
            # ★ 字符串外部：移除所有控制字符（AI 偶尔在 JSON 结构中插入不可见字符）
            if ord(ch) < 0x20 and ch not in ("\n", "\r", "\t"):
                continue  # 跳过控制字符
            result.append(ch)
    return "".join(result)


def fix_unescaped_embedded_json(json_str: str) -> str:
    """修复字符串值内未转义的内嵌 JSON 片段（补转义引号）。

    模型常把「响应示例/接口返回、错误码 JSON、请求体」原样写进字段字符串值，
    内层双引号未转义，导致整个 JSON 无法 json.loads，例如：
        "expected_feedback": "返回HTTP200，响应含{"status": "ok", "service": "api_demo", "books": N}"
    本函数仅在解析失败后兜底：扫描字符串内部，把处于内嵌 {…}/[…] 片段中的
    未转义双引号补为 \\"，外层字符串边界引号保持不变。结构层面（字符串外）的
    大括号/方括号不做任何改动。

    设计约束：
    - 纯字符串级修复，不触碰字符串外的 JSON 结构字符；
    - 已转义（\\"）的引号原样保留，避免双重转义；
    - 只对必须修复时才改动，未命中直接返回原串（供上层判断是否有变化）。
    """
    if '"' not in json_str:
        return json_str
    res: list[str] = []
    in_string = False
    esc = False
    depth = 0  # 字符串内部嵌入 JSON 的 {} / [] 嵌套深度
    changed = False
    for ch in json_str:
        if esc:
            res.append(ch)
            esc = False
            continue
        if ch == "\\":
            res.append(ch)
            esc = True
            continue
        if not in_string:
            if ch == '"':
                in_string = True
            res.append(ch)
            continue
        # —— 字符串内部 ——
        if ch == '"':
            if depth > 0:
                # 处于内嵌 {…}/[…] 片段内的引号 → 补转义
                res.append('\\"')
                changed = True
            else:
                # 普通字符串边界 → 关闭
                in_string = False
                res.append(ch)
            continue
        if ch in "{[":
            depth += 1
        elif ch in "}]":
            depth = max(0, depth - 1)
        res.append(ch)
    if not changed:
        return json_str
    return "".join(res)


def fix_doubled_json_chars(json_str: str) -> str:
    """
    修复 JSON 结构字符被意外双写的问题（LLM 幻觉或编码 bug 可能导致 {{ :: ,, }}）。
    只在 JSON 结构层面修复，不修改字符串内部内容。
    """
    # 检测是否有双字符问题（启发式：连续两个相同的 JSON 结构字符）
    if not re.search(r"[{},:]{2}", json_str):
        return json_str  # 没有双字符，跳过

    # 状态机：只在字符串外部替换
    result = []
    in_string = False
    escape_next = False
    i = 0
    while i < len(json_str):
        ch = json_str[i]
        if escape_next:
            result.append(ch)
            escape_next = False
            i += 1
            continue
        if ch == "\\":
            result.append(ch)
            escape_next = True
            i += 1
            continue
        if ch == '"':
            in_string = not in_string
            result.append(ch)
            i += 1
            continue
        if in_string:
            result.append(ch)
            i += 1
            continue

        # 字符串外部：检查双字符模式
        if (
            ch in ("{", "}", ",", ":")
            and i + 1 < len(json_str)
            and json_str[i + 1] == ch
        ):
            # 双写 → 只保留一个，跳过空格
            result.append(ch)
            i += 2
            # 跳过双字符后的空格
            while i < len(json_str) and json_str[i] == " ":
                result.append(" ")
                i += 1
        else:
            result.append(ch)
            i += 1

    return "".join(result)


def fix_invalid_json_escapes(json_str: str) -> str:
    """
    修复 JSON 字符串内部 Python json.loads 不认可的非法转义序列。
    AI 偶尔编造 \\e, \\s, \\d 等不存在于 JSON 规范中的转义符。
    策略：将非法转义符中的反斜杠双写，使其在解析后变为普通字面量。
    例如：\\e → \\\\e（JSON 解析后变成字面量 \\e）
    """
    # 合法的 JSON 转义字符集（RFC 8259 §7）
    VALID_ESCAPES = frozenset('"\\/bfnrtu')

    result = []
    in_string = False
    escape_next = False
    i = 0
    while i < len(json_str):
        ch = json_str[i]
        if escape_next:
            escape_next = False
            result.append(ch)
            i += 1
            continue
        if ch == "\\":
            if in_string and i + 1 < len(json_str):
                nxt = json_str[i + 1]
                if nxt not in VALID_ESCAPES:
                    # 非法转义：双写反斜杠使其变为字面量
                    result.append("\\\\")
                    i += 1
                    continue
            result.append(ch)
            escape_next = True
            i += 1
            continue
        if ch == '"':
            in_string = not in_string
        result.append(ch)
        i += 1
    return "".join(result)


def fix_truncated_json(s: str) -> str:
    """补全截断的 JSON：根据未闭合的括号栈逆序补全。"""
    stack = []
    in_string = False
    escape = False
    for ch in s:
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            stack.append("}")
        elif ch == "}":
            if stack and stack[-1] == "}":
                stack.pop()
        elif ch == "[":
            stack.append("]")
        elif ch == "]":
            if stack and stack[-1] == "]":
                stack.pop()
    # 逆序补全：最后打开的括号最先闭合
    closing = "".join(reversed(stack))
    # 如果截断在字符串中间，补一个引号
    if in_string:
        closing = '"' + closing
    return s + closing
