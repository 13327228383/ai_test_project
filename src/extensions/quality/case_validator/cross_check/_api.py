# -*- coding: utf-8 -*-
"""
Intent Validator - P0 防幻觉：API 路径确定性校验 (v3.0)
========================================================
从 checks[{track=api}].check 和 actions[{type=navigate}].target
提取 API 路径，与 YApi 文档真实路径比对。

零 AI 调用 — 纯字符串匹配 + MySQL 查询。
"""

import json
import logging

from ._data import load_cross_check_data

_log = logging.getLogger(__name__)


def cross_check_apis(cases: list[dict]) -> list[dict]:
    """
    v3.0 确定性校验：从 checks[track=api] 和 actions[type=navigate] 提取
    API 路径/URL，与 YApi 真实接口列表比对。

    验证逻辑:
      1. checks[{track=api, precision=exact}].check 中的 method+path → 必须在 YApi 中存在
      2. checks[{track=api, precision=fuzzy}].check → 降级为 AI 语义（不硬校验）
      3. actions[{type=navigate}].target 若为 URL 路径 → 必须在 YApi 或 Figma nav_map 中存在

    返回: [{"case_id": "...", "reason": "...", "severity": "error|warning", "cross_check": "api"}]
    """
    data = load_cross_check_data()
    yapi_paths = data["yapi_paths"]
    figma_elements = data.get("figma_elements", set())

    if not yapi_paths:
        return [
            {
                "case_id": "SYSTEM",
                "reason": "⚠️ YApi API路径基准未加载，API确定性校验已跳过。建议执行Step1加载YApi数据源。",
                "severity": "warning",
                "cross_check": "baseline",
            }
        ]

    errors = []

    for c in cases:
        if not isinstance(c, dict):
            continue
        cid = c.get("case_id", "?")

        # ── 1. 从 checks[track=api] 提取 API 路径 ──
        checks = c.get("checks") or []
        if isinstance(checks, str):
            try:
                checks = json.loads(checks)
            except Exception:
                checks = []

        for ci, chk in enumerate(checks if isinstance(checks, list) else []):
            if not isinstance(chk, dict):
                continue
            if str(chk.get("track", "")).strip().lower() != "api":
                continue

            precision = str(chk.get("precision", "fuzzy")).strip()
            check_data = chk.get("check", "")

            # exact: check 必须是 {method, path} dict → 硬校验
            if precision == "exact":
                if isinstance(check_data, dict):
                    method = str(check_data.get("method", "")).upper().strip()
                    path = str(check_data.get("path", "")).strip()
                    if method and path:
                        full = f"{method} {path}"
                        # 先尝试精确匹配，再尝试纯路径匹配
                        found = full in yapi_paths or path in yapi_paths
                        # 宽松匹配：忽略路径参数差异 (如 /api/user/123 → /api/user/{id})
                        if not found and path:
                            import re

                            path_normalized = re.sub(r"/\d+", "/{id}", path)
                            found = path_normalized in yapi_paths or any(
                                path_normalized == re.sub(r"/\d+", "/{id}", p)
                                for p in yapi_paths
                            )
                        if not found:
                            # ★ 找最相似的 YApi 路径建议修正
                            suggestion = _find_closest_path(full, yapi_paths)
                            errors.append(
                                {
                                    "case_id": cid,
                                    "reason": (
                                        f"checks[{ci}]引用的API「{full}」未在YApi文档中找到。"
                                        f"{'建议: ' + suggestion if suggestion else '请确认接口路径是否正确。'}"
                                    ),
                                    "severity": "error",  # ★ 编造API路径 → 阻断级
                                    "cross_check": "api",
                                    "fix_hint": suggestion,
                                }
                            )
            # fuzzy: 不硬校验路径，但检查是否完全缺失 API 信息
            else:
                check_str = (
                    str(check_data)
                    if not isinstance(check_data, dict)
                    else json.dumps(check_data)
                )
                if not check_str or check_str == "{}":
                    errors.append(
                        {
                            "case_id": cid,
                            "reason": f"checks[{ci}]标记为fuzzy但check为空，无法验证。",
                            "severity": "warning",
                            "cross_check": "api",
                        }
                    )

        # ── 2. 从 actions[type=navigate] 提取页面路径 ──
        actions = c.get("actions") or []
        if isinstance(actions, str):
            try:
                actions = json.loads(actions)
            except Exception:
                actions = []

        for ai, act in enumerate(actions if isinstance(actions, list) else []):
            if not isinstance(act, dict):
                continue
            if str(act.get("type", "")).strip().lower() != "navigate":
                continue
            target = str(act.get("target_hint", "")).strip()
            if not target:
                continue
            # 只检查看起来像路径的 target（以 / 或 http 开头）
            if target.startswith("/") or target.startswith("http"):
                # 路径在 YApi 中有吗？
                path_only = target.split("?")[0].rstrip("/")
                yapi_has = path_only in yapi_paths or any(
                    path_only in str(p) or str(p).endswith(path_only)
                    for p in yapi_paths
                )
                # Figma 有吗？
                figma_has = figma_elements and any(
                    path_only.strip("/").split("/")[-1] in str(fe)
                    for fe in figma_elements
                )

                if not yapi_has and not figma_has:
                    errors.append(
                        {
                            "case_id": cid,
                            "reason": (
                                f"actions[{ai}]导航目标「{target}」未在YApi或Figma中找到对应页面。"
                                f"请确认页面路径是否正确。"
                            ),
                            "severity": "error",
                            "cross_check": "api",
                        }
                    )

    return errors


def _find_closest_path(target: str, candidates: set) -> str:
    """在候选集中找最接近的路径，用于自动修正建议。"""
    if not candidates:
        return ""
    target_lower = target.lower()
    # 优先找路径片段匹配
    segments = [s for s in target_lower.replace("/", " ").split() if len(s) > 2]
    best = ""
    best_score = 0
    for cand in candidates:
        cand_lower = str(cand).lower()
        score = sum(1 for seg in segments if seg in cand_lower)
        if score > best_score:
            best_score = score
            best = str(cand)
    if best_score >= 2:
        return best
    return ""
