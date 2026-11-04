# -*- coding: utf-8 -*-
"""轨道需求推断器（高度解耦，单一职责）。

判定一个测试用例需要校验哪几条轨道（ui/api/db）。
- 优先消费 Step3 AI 显式声明的 verification_plan.required_tracks；
- 缺失时从各轨 assertion 的 strictness/source 兜底推断（解决存量意图）。

设计原则：本模块纯函数、无副作用、不依赖运行时引擎，可独立单测。
"""

import json
from typing import Any

_ALL_TRACKS = ["ui", "api", "db"]


def _parse_vp(vp: Any) -> dict[str, Any]:
    """verification_plan 兼容解析：str→json.loads，非 dict→{}。"""
    if isinstance(vp, str):
        try:
            vp = json.loads(vp)
        except (json.JSONDecodeError, ValueError):
            return {}
    if not isinstance(vp, dict):
        return {}
    return vp


def _track_assertions(vp: dict[str, Any], track: str) -> list[dict[str, Any]]:
    """取某轨的 assertion 列表（元素归一为 dict，纯 str 也包成 {"assertion": s}）。"""
    items = vp.get(track) or []
    if not isinstance(items, list):
        return []
    out = []
    for it in items:
        if isinstance(it, dict):
            out.append(it)
        elif isinstance(it, str) and it.strip():
            out.append({"assertion": it.strip()})
    return out


def _track_needed(assertions: list[dict[str, Any]]) -> bool:
    """判断某轨是否需要参与校验。

    规则（宁可错杀不可放过，尊重 AI 声明）：
    - 有任意非空 assertion → 需要（AI 明确写了断言就校验，不论 strictness，偏错杀方向）
    - 无非空 assertion → 不需要（纯展示/该轨确实不适用，跳过不拖累）

    注：不按 strictness 区分——AI 写了 optional 断言也认为该校验，避免漏报。
    """
    has_nonempty = any(str(a.get("assertion", "")).strip() for a in assertions)
    if not has_nonempty:
        return False
    return True


def infer_required_tracks(vp: Any, intent_schema: Any = None) -> list[str]:
    """推断所需轨道列表。

    返回 list[str]，元素为 "ui"/"api"/"db" 的子集，至少含 "ui"（UI 永远需要校验）。

    优先级：
    1. vp.required_tracks（AI 显式声明，规范为小写后校验合法性）
    2. 兜底：从各轨 assertion 非空性推断；UI 恒在；空时默认全三轨（向后兼容）
    """
    vp = _parse_vp(vp)

    # 1. AI 显式声明
    declared = vp.get("required_tracks")
    if isinstance(declared, list) and declared:
        norm = [str(t).strip().lower() for t in declared if str(t).strip()]
        norm = [t for t in norm if t in _ALL_TRACKS]
        # dedup 保序（AI 可能输出重复 track，如 ["ui","ui","db"]，去重避免 summary 虚高计数）
        _seen = set()
        norm = [t for t in norm if not (t in _seen or _seen.add(t))]
        if norm:
            # UI 恒需（防止 AI 漏写导致 UI 不校验）
            if "ui" not in norm:
                norm.insert(0, "ui")
            return norm

    # 2. 兜底推断
    tracks = []
    for t in _ALL_TRACKS:
        if _track_needed(_track_assertions(vp, t)):
            tracks.append(t)
    if not tracks:
        # 完全无断言 → 默认全三轨（保持原 AND 行为，向后兼容存量意图）
        tracks = list(_ALL_TRACKS)
    if "ui" not in tracks:
        tracks.insert(0, "ui")
    return tracks
