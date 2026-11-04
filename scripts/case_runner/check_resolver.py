# -*- coding: utf-8 -*-
"""断言指令 → 校验器处理器的通用解析器。

设计原则（通用性，非补丁）：
  - 单一事实源是引擎的 CHECK_TAXONOMY（_taxonomy.py），不是本文件里的硬编码表。
  - 处理器通过遍历 verification 包子模块动态发现（新增 check 无需改本文件）。
  - 解析结果带「可解析 / 缺失」二态，缺失如实上报为引擎缺口，禁止静默 PASS。

对外：
  load_all_handlers()            -> {handler_name: callable}
  resolve_check_handler(name)    -> callable | None
  resolve_by_check_type(ctype)   -> callable | None   （按 CHECK_TAXONOMY 的 key/alias/tool_name）
  audit_check_coverage()         -> 覆盖审计（哪些 check 无处理器）
"""
from __future__ import annotations

import importlib
import importlib.util
import inspect
import os
import pkgutil
from typing import Any, Callable

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_VERIF_PKG = "src.pipeline.steps.step4.verification"
_VERIF_DIR = os.path.join(
    _ROOT, "src", "pipeline", "steps", "step4", "verification"
)

_HANDLERS: dict[str, Callable] | None = None


def _taxonomy_module():
    spec = importlib.util.spec_from_file_location(
        "_case_runner_taxonomy",
        os.path.join(
            _ROOT, "src", "pipeline", "steps", "step4", "browser_tools", "_taxonomy.py"
        ),
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_all_handlers(force: bool = False) -> dict[str, Callable]:
    """动态发现 verification 包内所有校验器处理器（async def）。"""
    global _HANDLERS
    if _HANDLERS is not None and not force:
        return _HANDLERS

    handlers: dict[str, Callable] = {}
    for fname in os.listdir(_VERIF_DIR):
        if not fname.endswith(".py") or fname.startswith("__"):
            continue
        mod_name = f"{_VERIF_PKG}.{fname[:-3]}"
        try:
            mod = importlib.import_module(mod_name)
        except Exception:
            # 子模块依赖缺失（如可选第三方库）时跳过，不污染整体解析
            continue
        for attr in dir(mod):
            if not attr.startswith("verify"):
                continue
            fn = getattr(mod, attr)
            # ★ 修正(2026-09-21)：处理器既有 async def 也有同步 def（如 verify_url），
            #   初版只收 coroutine function 会把同步处理器误判为「引擎缺失」——
            #   这是驱动器自身的漏检缺陷，不是引擎问题。统一收集，调用侧按类型 await。
            if callable(fn):
                handlers.setdefault(attr, fn)

    # 顶层 __init__ 已 re-export 的处理器也并入（含 api_verify 等非 verify_ 前缀）
    try:
        init = importlib.import_module(_VERIF_PKG)
        for attr in dir(init):
            if attr.startswith("verify") or attr in ("api_verify",):
                fn = getattr(init, attr)
                if callable(fn):
                    handlers.setdefault(attr, fn)
    except Exception:
        pass

    _HANDLERS = handlers
    return handlers


def resolve_check_handler(name: str) -> Callable | None:
    """按处理器函数名解析（如 verify_element_exists_check）。"""
    return load_all_handlers().get((name or "").strip())


def resolve_by_check_type(check_type: str) -> Callable | None:
    """按 CHECK_TAXONOMY 的 key / alias / tool_name 解析处理器。

    例：'element_exists_check'(alias) / 'element_exists'(key)
        / 'verify_element_exists_check'(tool_name) → 同一处理器
    """
    ct = (check_type or "").strip()
    if not ct:
        return None
    handlers = load_all_handlers()
    if ct in handlers:
        return handlers[ct]

    tax = _taxonomy_module().CHECK_TAXONOMY
    # 1) 直接命中 key
    if ct in tax:
        tn = (tax[ct].get("tool_name") or "").strip()
        if tn and tn in handlers:
            return handlers[tn]
        return None
    # 2) 命中的是 alias
    for key, meta in tax.items():
        aliases = meta.get("aliases") or []
        if ct in aliases or ct == key:
            tn = (meta.get("tool_name") or "").strip()
            if tn and tn in handlers:
                return handlers[tn]
            return None
    return None


def audit_check_coverage() -> dict[str, Any]:
    """审计：CHECK_TAXONOMY 声明的每个 check 是否都有可调用处理器。

    返回 {'total', 'resolved', 'missing':[{check_type, track, tool_name, reason}]}
    """
    tax = _taxonomy_module().CHECK_TAXONOMY
    handlers = load_all_handlers()
    missing: list[dict[str, Any]] = []
    resolved = 0
    for key, meta in tax.items():
        tn = (meta.get("tool_name") or "").strip()
        if not tn:
            missing.append(
                {
                    "check_type": key,
                    "track": meta.get("track"),
                    "tool_name": tn,
                    "reason": "CHECK_TAXONOMY 未声明 tool_name（注册表缺口）",
                }
            )
            continue
        if tn in handlers:
            resolved += 1
        else:
            missing.append(
                {
                    "check_type": key,
                    "track": meta.get("track"),
                    "tool_name": tn,
                    "reason": f"声明的处理器 {tn} 未在 verification 包中找到",
                }
            )
    return {"total": len(tax), "resolved": resolved, "missing": missing}
