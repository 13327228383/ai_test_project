# -*- coding: utf-8 -*-
"""hint 工具模块 — 从 locator.py 拆分（§4.3 单文件≤800行）。

职责：hint 前缀剥离、核心关键词提取、前缀→选择器构建器映射。
被 _basic_locator.py / _fallback_locator.py 依赖。
"""
import logging
import re

from src.pipeline.steps.step4.playwright_engine.selectors import _escape_css_str

_log = logging.getLogger(__name__)


# ── hint 前缀剥离（自愈后的 hint 可能带 text=/placeholder=/ai_id= 等前缀）──
_HINT_PREFIXES = ("ai_id=", "text=", "placeholder=", "label=", "name=", "role=", "css=")


# ── 候选 hint 分隔符（**单一事实源**）────────────────────────────────────
#   生成端会产出"保存,提交,确定,确认"这类**候选组** hint（任一命中即可）。
#   定位器（_basic_locator）按本表拆分后逐个尝试；任何**其它**消费该 hint 的
#   判定（如 action_hooks 的可点击性校验）必须复用本函数，否则会把整串当字面文本
#   → `"保存".includes("保存,提交,确定,确认")` 恒假 → 误判"元素不可点击"
#   → 误触发 self-healing → 结构化路径中止（实测 TC-REQ-024/025-UX02 假 ERROR）。
HINT_CANDIDATE_SEPS = (",", "，", "、", "|")


def split_candidate_hint(hint: str) -> list[str]:
    """把候选组 hint 拆成候选词列表；非候选组 → 原串单元素列表。

    通用性: 分隔符是跨系统的通用书写约定，零业务硬编码。
    """
    text = str(hint or "").strip()
    if not text:
        return []
    for sep in HINT_CANDIDATE_SEPS:
        if sep in text:
            parts = [p.strip() for p in text.split(sep)]
            return [p for p in parts if p] or [text]
    return [text]


def _normalize_hint(hint: str) -> str:
    """从 hint 中提取纯文本关键词，剥离已知前缀与尾部括号修饰。

    尾部括号（如 "名称输入框(name=name)"）是 AI/生成端补的限定说明，
    不是元素名——留着会让 label/placeholder 文本匹配 0 命中（实测 fullcov_sut 新增页）。
    """
    for prefix in _HINT_PREFIXES:
        if hint.startswith(prefix):
            result = hint[len(prefix) :].strip("\"'")
            hint = result if result else hint
            break
    stripped = re.sub(r"[（(][^）)]*[）)]\s*$", "", hint).strip()
    return stripped or hint


# ── 核心关键词提取（剥离类型后缀，如"保存按钮"→"保存"）──
_TYPE_SUFFIXES = (
    "按钮",
    "输入框",
    "文本框",
    "下拉框",
    "选择框",
    "复选框",
    "链接",
    "选项",
    "标签",
    "区域",
    "表单",
    "字段",
    "菜单",
    "图标",
    "控件",
    "框",
    "项",
    "区",
    "条",
    "块",
    "栏",
)

# 通用动作关键词（用于从长 hint 末尾截取核心动作词）
_CORE_ACTION_WORDS = (
    "编辑",
    "新增",
    "删除",
    "查询",
    "搜索",
    "保存",
    "提交",
    "取消",
    "确认",
    "重置",
    "导出",
    "导入",
    "关闭",
    "返回",
    "添加",
    "修改",
    "查看",
    "下载",
    "上传",
    "登录",
    "退出",
    "复制",
    "粘贴",
    "筛选",
    "排序",
    "展开",
    "折叠",
)


def _extract_core_keyword(hint: str) -> str:
    """从 hint 中提取核心文本关键词，剥离类型后缀和操作描述。

    例如: "保存按钮" → "保存", "基准值参数输入框" → "基准值参数"
         "记录编号输入框，填写 REC001" → "记录编号"
         "某条记录的编辑按钮" → "编辑"
         "侧边栏菜单项：数据管理" → "数据管理"
    """
    text = _normalize_hint(hint)
    # ★ 通用修复 (2026-07-17): 区分冒号和逗号语义
    #   冒号 (:, ：): 格式为 "类型描述：目标文本" → 取冒号后的部分
    #   逗号 (,, ，): 格式为 "元素名，操作描述" → 取逗号前的部分
    #   处理顺序：先冒号（取后半部），再逗号（取前半部）
    for sep in ("：", ":"):
        if sep in text:
            parts = text.rsplit(sep, 1)  # rsplit: 取最后一个冒号后面
            text = parts[-1].strip()
            break
    for sep in ("，", ","):
        if sep in text:
            text = text.split(sep, 1)[0].strip()
            break
    # ★ 剥离类型后缀（保护：剥离后至少保留 2 个字符，防止短后缀误匹配如"某条"→"某"）
    _stripped = None
    for suffix in _TYPE_SUFFIXES:
        if text.endswith(suffix) and len(text) > len(suffix):
            _candidate = text[: -len(suffix)]
            if len(_candidate) >= 2:
                _stripped = _candidate
                break
    if _stripped is not None:
        text = _stripped
    # ★ 如果剥离后仍然很长（>6字符），尝试取末尾命中通用动作词的部分
    if len(text) > 6:
        for i in range(len(text) - 4, len(text)):
            if text[i:] in _CORE_ACTION_WORDS:
                return text[i:]
    return text


# ── 结构化（引用式）hint 形态判定 —— 单一事实源 ────────────────────────────
#   生成端按 DOM 契约产出 `name=<key>` / `id=` / `css=` 等**引用式** target_hint
#   （见 step3/_rule_extractor_fields：刻意用 name= 而非 label 文本，因 label
#   包裹会让 fill 命中 label 本身）。自然语言 hint（"按名称搜索"、"新增,新建"）
#   才走语义匹配。
#   根因: 执行端若不分派形态，会把 `name=price` 整串当自然语言做文本/语义匹配
#     → 恒 0 命中 → 声明动作落不了地 → 假 ERROR
#     （实测 fullcov_sut TC-REQ-012-I02：`input[name='price']` 真实存在却
#      "未找到输入框"）。
#   纪律: 任何消费 target_hint 的路径（含 Agent 语义路径）必须先经本函数分派，
#     不得各自 startswith 重推——形态判据全局唯一。
_STRUCTURED_HINT_PREFIXES = (
    "name=",
    "id=",
    "css=",
    "ai_id=",
    "role=",
    "label=",
    "placeholder=",
    "text=",
)


def is_structured_hint(hint: str) -> bool:
    """``hint`` 是否为**引用式**（DOM 属性 / CSS），而非自然语言描述。"""
    text = str(hint or "").strip()
    if not text:
        return False
    if text.startswith(_STRUCTURED_HINT_PREFIXES):
        return True
    return text[0] in "#.["


# ── 前缀→选择器构建器映射（_locate_by_hint 用）──
def _build_text_selector(page, query):
    return page.get_by_text(query, exact=False).first


def _build_placeholder_selector(page, query):
    return page.get_by_placeholder(query, exact=False).first


def _build_label_selector(page, query):
    return page.get_by_label(query, exact=False).first


def _build_id_selector(page, query):
    return page.locator("#" + query).first


def _build_name_selector(page, query):
    return page.locator('[name="' + _escape_css_str(query) + '"]').first


def _build_ai_id_selector(page, query):
    return page.locator('[data-ai-id="' + _escape_css_str(query) + '"]').first


def _build_role_selector(page, query):
    # 解析 role|name=yyy 格式
    parts = query.split("|")
    role_val = parts[0].strip()
    name_val = next(
        (p[5:].strip() for p in parts[1:] if p.strip().startswith("name=")), ""
    )
    if name_val:
        return page.get_by_role(role_val, name=name_val).first
    return page.get_by_role(role_val).first


def _build_css_selector(page, query):
    return page.locator(query).first


async def _build_tag_selector(page, query):
    loc = page.locator(query).first
    if await loc.count() > 0:
        return loc
    return None


_PREFIX_HANDLERS = [
    ("text=", _build_text_selector),
    ("placeholder=", _build_placeholder_selector),
    ("label=", _build_label_selector),
    ("id=", _build_id_selector),
    ("name=", _build_name_selector),
    ("ai_id=", _build_ai_id_selector),
    ("role=", _build_role_selector),
    ("css=", _build_css_selector),
    ("tag=", _build_tag_selector),
]
