# -*- coding: utf-8 -*-
"""逐用例证据的落库契约（SSOT）：归属切分 + 体积上限。

根因（2026-10-28，清空态全量跑实证）：
  现象——483 条用例执行完毕，executions 仅落 371 行；118 条用例整行记录丢失
  （分布 SKIP 6 而非 97），引擎日志却照常打印「Step n/483 结果」。
  直接——`(1406, "Data too long for column 'details' at row 1")`：details 是
  TEXT(65535 字节)，C5 证据接线把用例结束时缓冲里的 console/network 条目原样
  拼进 details；单条用例装载期即可产生上百条 CSP 报错 → 序列化后超列容量。
  根因——证据写入没有**体积契约**，且把「页面级事实」（同一页面任何用例都会
  看到的装载期报错）逐用例各存一份，存储量随用例数线性放大。
  修复——本模块是逐用例证据落库的唯一入口：
    1) 归属切分：复用 `js_load_phase.is_case_attributable`（唯一判据）——
       页面级事实压成「计数 + 签名摘要」，不再逐用例复制；
    2) 体积上限：可归因条目按类型限额保留、单条文本截断，超限只记丢弃条数。
  通用性：不含任何业务词表，只对「条目是否可归因于本用例」与「存储上限」
  两件事实负责，跨系统/行业/页面成立。
"""
from __future__ import annotations

import json
from collections import Counter

from src.pipeline.steps.step4.playwright_engine.js_load_phase import (
    is_case_attributable,
)

# 单条用例落库的证据上限（容量契约，非业务参数）：
#   - console 条目 40 条（含 error 与非 error，按捕获顺序）
#   - network 条目 60 条
#   - 单条目序列化文本 300 字符（method/url/status 等关键字段完整可读）
CASE_CONSOLE_MAX = 40
CASE_NETWORK_MAX = 60
ENTRY_TEXT_MAX = 300
# 页面级事实只保留最具代表性的签名数量（可诊断性 vs 体积的平衡点）
PAGE_FACT_SIGNATURE_MAX = 5


def _entry_type_text(entry) -> tuple[str, str]:
    """提取 (类型, 文本)；兼容 dict 与纯字符串两种证据条目形态。

    类型键按生产者契约回退：error 缓冲用 `type`（console.error/pageerror），
    网络缓冲用 `event`，全量 console 缓冲用 `level`（log/warning/error）。
    三者同源不同名，缺省为 "?" 仅作兜底，不承载任何业务语义。
    """
    if isinstance(entry, dict):
        _type = str(
            entry.get("type") or entry.get("event") or entry.get("level") or "?"
        )
        _text = str(entry.get("text") or entry.get("url") or "")
        return _type, _text
    return "?", str(entry)


def _trim_entry(entry) -> dict | str:
    """把单条目压到 ENTRY_TEXT_MAX 字符以内（嵌套结构按 JSON 文本截断）。"""
    if not isinstance(entry, dict):
        return str(entry)[:ENTRY_TEXT_MAX]
    _out: dict = {}
    _used = 0
    for _k, _v in entry.items():
        _room = ENTRY_TEXT_MAX - _used
        if _room <= 0:
            break
        if _v is None:
            _s = ""
        elif isinstance(_v, (dict, list)):
            _s = json.dumps(_v, ensure_ascii=False)
        else:
            _s = str(_v)
        _s = _s[:_room]
        _out[_k] = _s
        _used += len(_s)
    return _out


def pack_case_evidence(console, network) -> dict:
    """把引擎缓冲切成「用例可归因证据」+「页面级事实摘要」，并施加体积上限。

    返回可直接并入 details 的 dict（空输入返回空 dict，调用方据此决定是否写入）。
    """
    _console = list(console or [])
    _network = list(network or [])

    _case_level = [e for e in _console if is_case_attributable(e)]
    _page_level = [e for e in _console if not is_case_attributable(e)]

    _packed: dict = {}
    if _case_level:
        _kept = _case_level[:CASE_CONSOLE_MAX]
        _packed["console_logs"] = [_trim_entry(e) for e in _kept]
        if len(_case_level) > len(_kept):
            _packed["console_dropped"] = len(_case_level) - len(_kept)
    if _page_level:
        # 页面级事实是「同一页面任何用例都会看到」的存量，压成签名摘要即可诊断；
        # 完整原文由引擎日志承载（GAP-01 已单独打印）。
        _sig: Counter = Counter()
        for _e in _page_level:
            _t, _x = _entry_type_text(_e)
            _sig[(_t, _x[:ENTRY_TEXT_MAX])] += 1
        _packed["page_level_console"] = {
            "total": len(_page_level),
            "signatures": [
                {"type": _t, "text": _x, "count": _n}
                for (_t, _x), _n in _sig.most_common(PAGE_FACT_SIGNATURE_MAX)
            ],
        }
    if _network:
        _kept_net = _network[:CASE_NETWORK_MAX]
        _packed["network_logs"] = [_trim_entry(e) for e in _kept_net]
        if len(_network) > len(_kept_net):
            _packed["network_dropped"] = len(_network) - len(_kept_net)
    return _packed
