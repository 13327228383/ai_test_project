# -*- coding: utf-8 -*-
"""分词器：停用词 + 语义同义词组 + tokenize + 概念/字符 token 拆分 — 从 fuzzy_match_engine.py 拆出，零逻辑变更。"""

import logging
import re
from typing import Set

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 常用停用词（中英文）
# ---------------------------------------------------------------------------
_STOPWORDS: Set[str] = {
    # 中文高频停用词（够用即可，不追求全）
    "的",
    "了",
    "在",
    "是",
    "我",
    "有",
    "和",
    "就",
    "不",
    "人",
    "都",
    "一",
    "一个",
    "上",
    "也",
    "很",
    "到",
    "说",
    "要",
    "去",
    "你",
    "会",
    "着",
    "没有",
    "看",
    "好",
    "自己",
    "这",
    "那",
    "这个",
    "那个",
    "吗",
    "呢",
    "吧",
    "啊",
    "请",
    "需要",
    "进行",
    "操作",
    "应该",
    "可以",
    "能",
    "能够",
    "然后",
    "但是",
    "不过",
    "所以",
    "因此",
    # 英文
    "a",
    "an",
    "the",
    "and",
    "or",
    "of",
    "to",
    "in",
    "on",
    "for",
    "is",
    "are",
    "was",
    "were",
    "be",
    "been",
    "being",
    "it",
    "this",
    "that",
    "these",
    "those",
    "as",
    "at",
    "by",
    "with",
    "from",
    "into",
    "about",
    "than",
    "then",
    "so",
    "but",
    "if",
    "you",
    "we",
    "they",
    "he",
    "she",
    "me",
    "him",
    "her",
    "us",
    "them",
    "my",
    "your",
    "our",
    "their",
    "please",
    "click",
    "do",
    "does",
    "did",
    "has",
    "have",
    "had",
    "will",
    "would",
    "should",
    "could",
    "can",
    "may",
    "might",
}

# ---------------------------------------------------------------------------
# ★ 语义同义词组（概念 token）
# 核心创新: 把"创建/新增/添加/新建"这种人眼一看就懂的同义词映射到同一概念，
# 让 Jaccard 即使在完全无字符重叠时也能识别语义相似。
# 格式: (concept_token, [关键词列表...]) — 只要文本中命中任一关键词，就产出该概念 token
# ---------------------------------------------------------------------------
_SEMANTIC_GROUPS: list[tuple[str, list[str]]] = [
    # 1. 创建类
    (
        "concept_create",
        [
            "创建",
            "新增",
            "添加",
            "新建",
            "增加",
            "建立",
            "create",
            "add",
            "new",
            "insert",
        ],
    ),
    # 2. 删除类
    (
        "concept_delete",
        ["删除", "移除", "删掉", "清除", "delete", "remove", "destroy", "drop", "del"],
    ),
    # 3. 搜索/查询类
    (
        "concept_search",
        [
            "搜索",
            "查询",
            "查找",
            "筛选",
            "检索",
            "过滤",
            "search",
            "query",
            "find",
            "filter",
        ],
    ),
    # 4. 更新/编辑类
    (
        "concept_update",
        ["更新", "编辑", "修改", "改动", "update", "edit", "modify", "change"],
    ),
    # 5. 导出/下载类
    ("concept_export", ["导出", "下载", "导出为", "导出到", "export", "download"]),
    # 6. 审批/确认类
    (
        "concept_approve",
        [
            "审批",
            "审核",
            "同意",
            "批准",
            "通过",
            "驳回",
            "申请",
            "请求",
            "确认",
            "approve",
            "approval",
            "confirm",
            "reject",
            "request",
        ],
    ),
    # 7. 登录/认证类
    (
        "concept_login",
        [
            "登录",
            "登陆",
            "登入",
            "signin",
            "sign in",
            "login",
            "log in",
            "注册",
            "signup",
        ],
    ),
    # 8. 导航/跳转类
    (
        "concept_navigate",
        [
            "导航",
            "跳转",
            "进入",
            "打开",
            "点击进入",
            "navigate",
            "go to",
            "open",
            "jump to",
        ],
    ),
    # 9. 分页/翻页类
    (
        "concept_page",
        [
            "分页",
            "翻页",
            "下一页",
            "上一页",
            "页码",
            "第几页",
            "page",
            "pagination",
            "paging",
        ],
    ),
    # 10. 表单/填写类
    (
        "concept_form",
        ["填写", "录入", "表单", "填", "输入", "fill", "fill in", "submit", "form"],
    ),
    # 11. 数据/记录类
    ("concept_record", ["记录", "数据", "条记录", "行", "record", "entry", "item"]),
    # 12. 配置/设置/复杂流程
    (
        "concept_config",
        [
            "配置",
            "设置",
            "参数",
            "策略",
            "流程",
            "工作流",
            "config",
            "setting",
            "parameter",
            "workflow",
        ],
    ),
    # 13. 文件/表格
    (
        "concept_file",
        ["文件", "表格", "excel", "csv", "pdf", "下载文件", "file", "document"],
    ),
    # 14. 批量操作
    (
        "concept_batch",
        ["批量", "批量操作", "全选", "批量处理", "batch", "bulk", "select all"],
    ),
    # 15. 列表/表格
    ("concept_list", ["列表", "表格", "数据列表", "table", "list", "grid"]),
    # 16. 用户/账号
    ("concept_user", ["用户", "账号", "用户账号", "user", "account", "username"]),
    # 17. 信息/详情
    ("concept_info", ["信息", "详情", "详细信息", "detail", "info", "information"]),
]


def _semantic_tokens(text: str) -> Set[str]:
    """
    ★ 核心新增: 从文本中抽取概念级 token（语义同义词组）。
    只要文本命中一个关键词（子串匹配），就产生对应的概念 token。
    目的: 让"创建数据记录"和"新增数据"在概念层产生共同 token CONCEPT_CREATE，
    从而让 Jaccard 能识别语义相似（而不只是字符重叠）。
    """
    try:
        if not text:
            return set()
        t = text.lower()
        tokens: Set[str] = set()
        for concept, kws in _SEMANTIC_GROUPS:
            for kw in kws:
                if kw and kw.lower() in t:
                    tokens.add(concept)
                    break  # 一组命中一个即产一个概念 token，避免同一个概念被加多次
        return tokens
    except Exception as e:
        _log.warning("fuzzy_match_engine._semantic_tokens 异常 %s", e)
        return set()


def tokenize(text: str, use_semantic: bool = True) -> Set[str]:
    """
    把文本切分为 token 集合。策略:
      - 英文: 按 \\w+ 切词，转小写，过滤长度 < 2 或停用词
      - 中文: 连续中文字符做 2-gram（滑窗 2 字），过滤停用词
      - ★ 语义概念: 命中语义同义词组 → 产出概念 token（如"创建"/"新增"→concept_create）
    返回 set，便于 Jaccard 计算。
    """
    try:
        if not text:
            return set()
        text = str(text).lower().strip()
        tokens: Set[str] = set()

        # 1) 英文词
        for m in re.finditer(r"[a-zA-Z][a-zA-Z0-9_]{1,}", text):
            w = m.group(0).lower()
            if len(w) < 2 or w in _STOPWORDS:
                continue
            tokens.add(w)

        # 2) 数字 token（保留 3 位以上数字，便于匹配 order_no / id 等）
        for m in re.finditer(r"\d{3,}", text):
            tokens.add(m.group(0))

        # 3) 中文：按连续中文字符块处理，每个块做 2-gram
        for block in re.findall(r"[一-鿿]{2,}", text):
            for i in range(len(block) - 1):
                gram = block[i : i + 2]
                if gram in _STOPWORDS:
                    continue
                tokens.add(gram)
            # 同时保留整块 ≥ 2 字且非停用词的子串（如完整关键词）
            if 2 <= len(block) <= 6 and block not in _STOPWORDS:
                tokens.add(block)

        # 4) ★ 语义概念 token（核心：字符无重叠时也能识别语义相似）
        if use_semantic:
            sem = _semantic_tokens(text)
            if sem:
                tokens |= sem

        return tokens
    except Exception as e:
        _log.warning("fuzzy_match_engine.tokenize 异常 %s", e)
        return set()


def _concept_tokens_only(tokens: Set[str]) -> Set[str]:
    """从 token 集合中提取概念 token（以 concept_ 开头的）。"""
    try:
        return {t for t in tokens if t.startswith("concept_")}
    except Exception as _e:
        _log.debug("[tokenizer] _concept_tokens_only 异常: %s", _e, exc_info=True)
        return set()


def _char_tokens_only(tokens: Set[str]) -> Set[str]:
    """从 token 集合中提取字符/字面 token（排除 concept_ 开头的）。"""
    try:
        return {t for t in tokens if not t.startswith("concept_")}
    except Exception as _e:
        _log.debug("[tokenizer] _char_tokens_only 异常: %s", _e, exc_info=True)
        return set()
