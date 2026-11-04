# -*- coding: utf-8 -*-
"""
browser_intent_parser._io — IO 分区相关函数
============================================
extract_intent_schema, has_write_keyword, partition_intents_by_io, intent_needs_login
"""

import json
import logging
from typing import Any

_log = logging.getLogger(__name__)

# 写入语义关键词
_WRITE_KEYWORDS = (
    "创建",
    "新增",
    "新建",
    "添加",
    "删除",
    "保存",
    "提交",
    "修改",
    "编辑",
    "更新",
    "导入",
    "上传",
)


def extract_intent_schema(
    intent: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """v9 精简：intent_schema 已移除，返回 intent 自身作为 schema + payload。"""
    payload = intent.get("payload") if isinstance(intent.get("payload"), dict) else {}
    return intent, payload


def has_write_keyword(intent: dict[str, Any]) -> bool:
    """检查测试用例是否含写入语义。v9：required_tracks 含 db → write。

    ★ P0-refactor(2026-07-27): 探索意图用例（AgentExplorer 实时探索）强制为读意图。
       根因(§10.9 3层why):
         L1: Step4 0个读意图/13个写意图 → 所有用例被判为写意图
         L2: 为什么判为写意图? → required_tracks 含"db" + title 含"导入/新增/编辑/删除"
         L3: 为什么探索用例会有 db track? → _build_intent_case → _infer_required_tracks
             根据 operation 语义推断，含"导入"等操作词 → 推断需要 db track
         但 AgentExplorer 实时探索是只读操作（navigate→snapshot→match→click→observe），
         不执行写操作，不应分类为写意图。
       全局观: 写意图按 location 分桶串行执行（避免数据冲突），AgentExplorer 探索不需要
       职责边界: _io.py 职责内（IO 分区），修复不越界
       通用性(§12.4): 基于 _exploratory 标记判断，跨系统/行业/页面通用
    """
    # ★ 探索意图用例（AgentExplorer 实时探索）强制为读意图
    if intent.get("_exploratory") or intent.get("_explore_task"):
        return False

    tracks = intent.get("required_tracks") or []
    if isinstance(tracks, str):
        try:
            tracks = json.loads(tracks)
        except Exception as _e:
            _log.debug("[_io] has_write_keyword 异常: %s", _e, exc_info=True)
            tracks = [tracks]
    if "db" in [str(t).lower().strip() for t in tracks]:
        return True
    # 回退：title + title 关键词
    text = " ".join(
        [
            str(intent.get("title", "")),
            str(intent.get("title", "")),
        ]
    )
    return any(kw in text for kw in _WRITE_KEYWORDS)


def partition_intents_by_io(
    candidates: list[dict[str, Any]],
) -> tuple[list[dict], dict[str, list[dict]]]:
    """将候选测试用例按读/写语义分桶。

    v9 简化：required_tracks 含 db → write；否则 read。
    返回 (read_intents, write_buckets_by_location)：同页面写意图串行，读意图全并行。
    """
    read_intents = []
    write_buckets = {}
    for intent in candidates:
        if has_write_keyword(intent):
            location = (
                intent.get("location") or intent.get("module") or "_default_"
            ).strip()
            write_buckets.setdefault(location, []).append(intent)
        else:
            read_intents.append(intent)
    return read_intents, write_buckets


def intent_needs_login(steps: list) -> bool:
    if not steps:
        return False
    login_action_patterns = [
        "打开登录",
        "进入登录",
        "访问登录",
        "跳转登录",
        "登录系统",
        "登录页面",
        "打开系统登录",
    ]
    for s in steps[:2]:
        desc = s.get("description", "") if isinstance(s, dict) else str(s)
        dl = desc.lower().strip()
        if any(p in dl for p in login_action_patterns):
            return True
        has_user = any(kw in dl for kw in ("用户名", "账号", "username"))
        has_pass = any(kw in dl for kw in ("密码", "password"))
        if has_user and has_pass:
            return True
    return False
