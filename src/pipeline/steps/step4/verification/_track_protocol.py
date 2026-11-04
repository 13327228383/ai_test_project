# -*- coding: utf-8 -*-
"""单轨校验器协议 + 三轨收敛（T03, 2026-08-06）。

背景（架构师实测）：
- API 轨 _verify_api 对 api_header/network/websocket/page_load_time 静默跳过（fail-open 假通过）
- DB 轨 _verify_db_async 只 if/else 两分支，time_range_check 被误传
- UI 轨 8 个孤儿验证器落 UNSUPPORTED_CHECK
- _verify_perf 异常/无请求/未匹配 URL 均返回 passed=True（fail-open）

设计（主理人决策 #3/#5）：
- TrackVerifier 基类：register + register_alias + async verify(checks, ctx)
  - check 归一化（str→{"type":...}、外层 assertion/target_hint 合并，复用 _db_ui 逻辑）
  - dict 分发（原键 → 去 _check 后缀 → alias）
  - 未知类型 → UNSUPPORTED_CHECK（fail-closed 永久终态）
  - handler 异常 → EXECUTOR_EXCEPTION + passed=False
  - 全 skipped → skipped=True（passed 保持 True，不计 AND 但置信度扣分）
  - 聚合 evidence "type:✓/✗(ev)"
- 三轨适配：UiTrackVerifier / ApiTrackVerifier / DbTrackVerifier
- 对外 result["ui"/"api"/"db"] 字段名不变（前端/报告层零改动）

通用性：纯协议层，不依赖任何业务/系统/行业知识。
"""

from __future__ import annotations
from src.core.guarded import guard

import inspect
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Union

from .tracks import TrackStatus

_log = logging.getLogger(__name__)

# ★ Python 3.9 兼容(2026-08-25): `Awaitable[dict] | dict` 的 `|` 联合语法在
#   3.9 的 typing 泛型别名上不支持（_GenericAlias 无 __or__），模块级赋值在
#   import 时即抛 TypeError。改用 Union 显式联合，语义一致且 3.9 可用。
Handler = Callable[[dict, dict], Union[Awaitable[dict], dict]]


@dataclass
class TrackCheckResult:
    """单条 check 的校验结果。"""

    type: str = ""
    passed: bool = True
    evidence: str = ""
    status: str = ""
    reason: str = ""
    skipped: bool = False
    detail: Any = None

    def to_dict(self) -> dict:
        return {
            "type": self.type,
            "passed": self.passed,
            "evidence": self.evidence,
            "status": self.status,
            "reason": self.reason,
            "skipped": self.skipped,
            "detail": self.detail,
        }


@dataclass
class TrackResult:
    """整条轨的校验结果（字段名与 verify_step 消费方兼容）。"""

    passed: bool = True
    evidence: str = ""
    status: str = ""
    evidence_ref: str = ""
    skipped: bool = False
    skip_reason: str = ""
    details: list = field(default_factory=list)
    extra: dict = field(default_factory=dict)
    calls: list = field(default_factory=list)
    data: list = field(default_factory=list)

    def to_dict(self) -> dict:
        d = {
            "passed": self.passed,
            "evidence": self.evidence,
            "status": self.status,
            "skipped": self.skipped,
        }
        if self.evidence_ref:
            d["evidence_ref"] = self.evidence_ref
        if self.skip_reason:
            d["skip_reason"] = self.skip_reason
        if self.details:
            d["details"] = self.details
        if self.calls:
            d["calls"] = self.calls
        if self.data:
            d["data"] = self.data
        d.update(self.extra)
        return d


class TrackVerifier:
    """单轨校验器协议基类。

    用法：
        v = TrackVerifier("ui")
        v.register("element_exists", handler)          # handler(ctx, check) -> dict
        v.register_alias("element_exists_check", "element_exists")
        result = await v.verify(checks, ctx)           # -> TrackResult dict
    """

    def __init__(self, track_name: str = "track"):
        self.track_name = track_name
        self._handlers: dict[str, Handler] = {}
        self._aliases: dict[str, str] = {}
        self._default_handler: Handler | None = None

    # ── 注册 ──
    def register(self, check_type: str, handler: Handler) -> None:
        self._handlers[check_type] = handler

    def register_alias(self, alias: str, canonical: str) -> None:
        self._aliases[alias] = canonical

    def register_default(self, handler: Handler) -> None:
        """字符串 check（str → {"type": str}）的兜底 handler（API/DB 轨用）。"""
        self._default_handler = handler

    # ── check 归一化（复用 _db_ui 的 assertion/target_hint 合并逻辑）──
    @staticmethod
    def normalize_check(raw: Any) -> dict:
        """把 str / dict check 归一化为内部格式。

        返回 dict：{type, inner, precision, ...}（inner 为解析后的 check dict）。
        str → {"type": str, "inner": {}}
        dict → 合并外层 assertion/target_hint（与 _verify_ui_objective 同逻辑）
        """
        if isinstance(raw, str):
            return {"type": raw.strip(), "inner": {}}
        if not isinstance(raw, dict):
            return {"type": str(raw), "inner": {}}
        check = dict(raw)
        outer_assertion = str(check.get("assertion", "") or "").strip()
        outer_target_hint = str(check.get("target_hint", "") or "").strip()
        inner = check.get("check")
        if not isinstance(inner, dict):
            if isinstance(check.get("inner"), dict):
                # 已归一化（幂等）：复用之，避免二次合成把既有参数覆盖掉
                inner = check["inner"]
            elif outer_assertion:
                inner = {
                    "type": outer_assertion,
                    "expected": "" if inner is None else str(inner),
                }
                if (
                    outer_assertion in ("element_exists", "element_not_exists")
                    and outer_target_hint
                ):
                    inner["selector"] = outer_target_hint
            else:
                inner = {
                    "type": "text_contains",
                    "expected": "" if inner is None else str(inner),
                }
        else:
            inner = dict(inner)
            if not inner.get("type") and outer_assertion:
                inner["type"] = outer_assertion
                if (
                    outer_assertion in ("element_exists", "element_not_exists")
                    and outer_target_hint
                    and not inner.get("selector")
                ):
                    inner["selector"] = outer_target_hint
        check["inner"] = inner
        # ★ inner 非 type 参数提升顶层（单一归一化点）：DB/API 轨消费方从 check 顶层读
        #   table/where/field 等参数，参数只留在 inner 会在归一化后被判"缺少参数"。
        for _k, _v in (inner or {}).items():
            if _k != "type" and check.get(_k) is None:
                check[_k] = _v
        check.setdefault(
            "type", str(inner.get("type", "text_contains") or "text_contains").strip()
        )
        check.setdefault("precision", "fuzzy")
        return check

    # ── 分发：原键 → 去 _check 后缀 → alias → default ──
    def resolve_handler(self, check_type: str):
        _t = str(check_type or "").strip()
        if _t in self._handlers:
            return self._handlers[_t], _t
        if _t.endswith("_check"):
            _base = _t[: -len("_check")]
            if _base in self._handlers:
                return self._handlers[_base], _base
        if _t in self._aliases:
            _canonical = self._aliases[_t]
            return self._handlers.get(_canonical), _canonical
        return self._default_handler, _t

    # ── 执行 ──
    async def _run_handler(self, handler: Handler, ctx: dict, check: dict) -> dict:
        _r = handler(ctx, check)
        if inspect.isawaitable(_r):
            _r = await _r
        return (
            _r
            if isinstance(_r, dict)
            else {"passed": False, "evidence": "执行器返回非 dict"}
        )

    def _to_check_result(self, r: dict, check_type: str) -> TrackCheckResult:
        passed = bool(r.get("passed", False))
        skipped = bool(r.get("skipped", False))
        _reason = str(r.get("reason", "") or "")
        if _reason == "UNSUPPORTED_CHECK":
            _status = "ERROR"
        elif _reason == "EXECUTOR_EXCEPTION":
            _status = "ERROR"
        else:
            _status = str(r.get("status") or TrackStatus.from_bool(passed).value)
        return TrackCheckResult(
            type=str(r.get("type") or check_type),
            passed=passed,
            evidence=str(r.get("evidence", "") or ""),
            status=_status,
            reason=_reason,
            skipped=skipped,
            detail=r.get("detail"),
        )

    async def verify(self, checks: list, ctx: dict | None = None) -> dict:
        """执行整轨校验，返回 TrackResult dict（字段名与 verify_step 消费方兼容）。"""
        ctx = ctx or {}
        results: list[TrackCheckResult] = []
        for raw in checks or []:
            _was_str = isinstance(raw, str)
            check = self.normalize_check(raw)
            check_type = str(check.get("type", "") or "text_contains").strip()
            handler, _resolved = self.resolve_handler(check_type)
            # dict check 落到默认字符串 handler → 视为未知 dict → UNSUPPORTED_CHECK
            # （字符串 check 才允许走默认 handler，如 API "应调用 GET /api/xx 返回200"、
            #   DB SQL/自然语言 check）
            if handler is self._default_handler and not _was_str:
                handler = None
            if handler is None:
                results.append(
                    TrackCheckResult(
                        type=check_type,
                        passed=False,
                        status="ERROR",
                        reason="UNSUPPORTED_CHECK",
                        evidence="未知检查类型: %s（无有效断言，结论不可用）"
                        % check_type,
                    )
                )
                continue
            try:
                _r = await self._run_handler(handler, ctx, check)
                results.append(self._to_check_result(_r, check_type))
            except Exception as _e:
                _log.warning(
                    "[%s] check 执行器异常: %s", self.track_name, str(_e)[:120]
                )
                results.append(
                    TrackCheckResult(
                        type=check_type,
                        passed=False,
                        status="ERROR",
                        reason="EXECUTOR_EXCEPTION",
                        evidence="执行器异常: %s" % str(_e)[:80],
                    )
                )
        return self._aggregate(results, checks or [])

    def _aggregate(self, results: list[TrackCheckResult], checks: list) -> dict:
        if not results:
            return TrackResult(
                passed=True,
                evidence="无检查点",
                status=TrackStatus.UNKNOWN.value,
                skipped=False,
            ).to_dict()
        all_passed = all(r.passed for r in results)
        any_error = any(r.reason == "EXECUTOR_EXCEPTION" for r in results)
        any_unsupported = any(r.reason == "UNSUPPORTED_CHECK" for r in results)
        all_skipped = all(r.skipped for r in results)
        evidence = "; ".join(
            "%s:%s(%s)" % (r.type, "✓" if r.passed else "✗", r.evidence[:80])
            for r in results
        )
        if all_skipped:
            status = TrackStatus.UNKNOWN.value
            track_passed = (
                True  # skipped 轨不阻断 AND（与 _verify_db_async 现有语义一致）
            )
            skip_reason = "all_checks_skipped"
        elif any_error or any_unsupported:
            status = TrackStatus.FAIL.value
            track_passed = False
            skip_reason = ""
        else:
            status = TrackStatus.from_bool(all_passed).value
            track_passed = all_passed
            skip_reason = ""
        return TrackResult(
            passed=track_passed,
            evidence=evidence,
            status=status,
            skipped=all_skipped,
            skip_reason=skip_reason,
            details=[r.to_dict() for r in results],
        ).to_dict()


# ══════════════════════════════════════════════════════════════
# UI 轨
# ══════════════════════════════════════════════════════════════


def _merge_check_inner(check: dict, inner: dict, canonical_type: str) -> dict:
    """合并外层 check 与 inner，并把 type 归一化为目标验证器期望的后缀形式。"""
    merged: dict = {}
    if isinstance(check, dict):
        merged.update(check)
    if isinstance(inner, dict):
        for _k, _v in inner.items():
            if _k != "type":
                merged[_k] = _v
    merged["type"] = canonical_type
    return merged


class UiTrackVerifier(TrackVerifier):
    """UI 轨校验器：保留 _UI_CHECK_DISPATCH 全部键 + 补注册 8 个孤儿验证器。

    ⚠️ _UI_CHECK_DISPATCH 字典必须留在 _db_ui.py（test_f00_f01_dbui_dispatch
    用源码正则解析该字典），本类 import 并包装为协议 handler。
    """

    def __init__(self):
        super().__init__("ui")
        from ._db_ui import _UI_CHECK_DISPATCH

        for _k, _h in _UI_CHECK_DISPATCH.items():
            self.register(_k, self._wrap_dbui_handler(_h))
        # 孤儿验证器已在 _UI_CHECK_DISPATCH 注册（_db_ui.py T03 补齐），
        # 此处再显式注册 alias，确保裸名/后缀名均可达。
        for _alias, _canonical in {
            "element_visible": "element_visible",
            "element_attribute": "element_attribute",
            "text_regex": "text_regex",
            "console_error": "console_error",
            "dialog": "dialog",
            "animation_complete": "animation_complete",
            "pagination": "pagination",
            "storage": "storage",
        }.items():
            self.register_alias(_alias, _canonical)

    def _wrap_dbui_handler(self, handler: Handler) -> Handler:
        """把 _db_ui 的 (page, check, inner, check_type, precision) handler
        包装为协议 (ctx, check) handler。"""

        async def _wrapped(ctx: dict, check: dict) -> dict:
            page = ctx.get("page")
            inner = check.get("inner") or {}
            check_type = str(check.get("type", "") or "").strip()
            precision = str(check.get("precision", "fuzzy") or "fuzzy")
            return await handler(page, check, inner, check_type, precision)

        return _wrapped


# ══════════════════════════════════════════════════════════════
# API 轨
# ══════════════════════════════════════════════════════════════


class ApiTrackVerifier(TrackVerifier):
    """API 轨校验器：dict 分发 + 字符串 check 保留 _match_url_pattern。

    ctx: {api_tracker, start_time, page?, engine?}
    """

    def __init__(self):
        super().__init__("api")
        from ._verify_perf import verify_performance
        from ._verify_api_ext import verify_api_ext
        from ._verify_header import verify_api_header
        from ._verify_network import verify_network
        from ._verify_websocket import verify_websocket
        from ._verify_page_load import verify_page_load_time

        self.register("response_time_check", self._wrap2(verify_performance))
        self.register("response_header_check", self._wrap2(verify_performance))
        self.register("api_call_count_check", self._wrap_api_ext(verify_api_ext))
        self.register("request_body_check", self._wrap_api_ext(verify_api_ext))
        self.register("api_response_body_check", self._wrap_api_ext(verify_api_ext))
        # ★ W3 T04 (2026-08-05): 契约校验族 api_contract（fail-closed）
        try:
            from ._verify_contract import verify_api_contract

            self.register("api_contract_check", self._wrap(verify_api_contract))
            self.register_alias("api_contract", "api_contract_check")
        except Exception as _ce:
            _log = __import__("logging").getLogger(__name__)
            _log.warning("[TrackProtocol] api_contract 注册失败: %s", _ce)
        # ★ T03: 新增 4 个孤儿 API 验证器（原静默跳过 fail-open）
        self.register("api_header_check", self._wrap(verify_api_header))
        self.register("network_check", self._wrap(verify_network))
        self.register("websocket_check", self._wrap_engine(verify_websocket))
        self.register("page_load_time_check", self._wrap_page(verify_page_load_time))
        self.register_alias("api_header", "api_header_check")
        self.register_alias("network", "network_check")
        self.register_alias("websocket", "websocket_check")
        self.register_alias("page_load_time", "page_load_time_check")
        # 字符串 check 兜底（"应调用 GET /api/xxx 返回200"）
        self.register_default(self._string_handler)

    def _wrap(self, fn: Handler) -> Handler:
        async def _wrapped(ctx: dict, check: dict) -> dict:
            tracker = ctx.get("api_tracker")
            start_time = ctx.get("start_time", 0)
            return await fn(tracker, check, start_time)

        return _wrapped

    def _wrap2(self, fn: Handler) -> Handler:
        """verify_performance 签名 (api_tracker, check) —— 只传 2 参。"""

        async def _wrapped(ctx: dict, check: dict) -> dict:
            tracker = ctx.get("api_tracker")
            return await fn(tracker, check)

        return _wrapped

    def _wrap_api_ext(self, fn: Handler) -> Handler:
        async def _wrapped(ctx: dict, check: dict) -> dict:
            tracker = ctx.get("api_tracker")
            start_time = ctx.get("start_time", 0)
            return await fn(tracker, check, start_time)

        return _wrapped

    def _wrap_engine(self, fn: Handler) -> Handler:
        async def _wrapped(ctx: dict, check: dict) -> dict:
            engine = ctx.get("engine") or self._engine_from_ctx(ctx)
            if engine is None:
                return {
                    "passed": False,
                    "status": "FAIL",
                    "evidence": "websocket_check 无法执行：engine 不可达（ctx 无 engine/page._engine）",
                }
            return fn(engine, check)  # verify_websocket 为同步函数

        return _wrapped

    def _wrap_page(self, fn: Handler) -> Handler:
        async def _wrapped(ctx: dict, check: dict) -> dict:
            page = ctx.get("page")
            if page is None:
                return {
                    "passed": False,
                    "status": "FAIL",
                    "evidence": "page_load_time_check 无法执行：ctx 无 page",
                }
            return await fn(page, check)

        return _wrapped

    @staticmethod
    def _engine_from_ctx(ctx: dict):
        page = ctx.get("page")
        if page is not None:
            return getattr(page, "_engine", None) or getattr(page, "engine", None)
        return ctx.get("engine")

    async def _string_handler(self, ctx: dict, check: dict) -> dict:
        """字符串 check：保留 _match_url_pattern 语义（"应调用 GET /api/xx 返回200"）。"""
        from ._url_match import _match_url_pattern

        tracker = ctx.get("api_tracker")
        start_time = ctx.get("start_time", 0)
        calls = tracker.get_calls_since(start_time) if tracker else []
        check_lower = str(check.get("type", "") or "").lower()
        matched = False
        for call in calls:
            url = str(call.get("url", "") or "")
            method = str(call.get("method", "") or "")
            url_parts = [
                p
                for p in check_lower.split()
                if "/" in p or "*" in p or p.startswith("^")
            ]
            if method.lower() in check_lower and any(
                _match_url_pattern(p, url) for p in url_parts
            ):
                matched = True
                status = call.get("status", 0)
                if "200" in check_lower or "成功" in check_lower:
                    if status not in (200, 201, 204):
                        return {
                            "passed": False,
                            "evidence": "API %s→%d(预期200/201/204)" % (url, status),
                        }
                    return {"passed": True, "evidence": "API %s→%d ✓" % (url, status)}
                break
        if not matched and ("应调用" in check_lower or "应返回" in check_lower):
            return {"passed": False, "evidence": "未匹配: %s" % check.get("type", "")}
        return {
            "passed": True,
            "evidence": "API无匹配断言: %s" % check.get("type", "")[:60],
        }


# ══════════════════════════════════════════════════════════════
# DB 轨
# ══════════════════════════════════════════════════════════════


class DbTrackVerifier(TrackVerifier):
    """DB 轨校验器：dict 分派到 verify_db_ext / verify_time；字符串 → verify_db。

    ctx: {page?}（time_range_check 需要 page；无 page 时 UNKNOWN+skipped）
    """

    def __init__(self):
        super().__init__("db")
        from ._verify_db_ext import verify_db_ext

        for _t in (
            "db_record_count_check",
            "db_record_not_exists_check",
            "db_record_exists_check",
            "db_field_value_check",
            "db_query_check",
        ):
            self.register(_t, self._wrap_db_ext(verify_db_ext))
        self.register_alias("db_record_count", "db_record_count_check")
        self.register_alias("db_record_not_exists", "db_record_not_exists_check")
        self.register_alias("db_record_exists", "db_record_exists_check")
        self.register_alias("db_field_value", "db_field_value_check")
        self.register_alias("db_query", "db_query_check")
        # ★ T03: time_range_check 接入 verify_time（需要 page）
        self.register("time_range_check", self._time_range_handler)
        self.register_alias("time_range", "time_range_check")
        # ★ 2026-09-23 根治（db_table_accessed_check 词表漂移致 db 轨恒空）：
        #   生成端发射的通用 DB 探针类型在此之前未注册 → check_guard 剔除 → db 轨空转。
        #   执行器语义：连接 SUT 库并枚举业务表，至少 1 张可读表 → 持久层联动成立。
        self.register("db_table_accessed_check", self._table_accessed_handler)
        self.register_alias("db_table_accessed", "db_table_accessed_check")
        # 字符串 check（SQL / 自然语言）→ verify_db
        self.register_default(self._string_handler)

    @staticmethod
    def _wrap_db_ext(fn: Handler) -> Handler:
        async def _wrapped(ctx: dict, check: dict) -> dict:
            return await fn(check)

        return _wrapped

    async def _time_range_handler(self, ctx: dict, check: dict) -> dict:
        page = ctx.get("page")
        if page is None:
            return {
                "passed": True,
                "skipped": True,
                "status": "UNKNOWN",
                "skip_reason": "db_time_range_no_page",
                "evidence": "DB time_range_check 需要 page 上下文（当前不可用），跳过",
            }
        from ._verify_time import verify_time_range

        return await verify_time_range(page, check)

    async def _table_accessed_handler(self, ctx: dict, check: dict) -> dict:
        """★ 2026-09-23：目标库可访问探针（db_table_accessed_check 执行器）。

        判定：能连上 SUT 库 + 至少 1 张业务表可读（能取到行数） → passed。
        证据：库类型 / 业务表数 / 抽样表名与行数。
        不可达时**不判产品缺陷**（UNVERIFIED），避免"环境/配置问题"被记成假 FAIL。
        通用性：sqlite / mysql / postgresql / oracle 四类目标库。
        """
        _tables_sql = {
            "sqlite": "SELECT name FROM sqlite_master WHERE type='table'",
            "mysql": (
                "SELECT TABLE_NAME FROM information_schema.TABLES "
                "WHERE TABLE_SCHEMA=DATABASE()"
            ),
            "postgresql": (
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema='public'"
            ),
            "oracle": "SELECT TABLE_NAME FROM USER_TABLES",
            "yasdb": "SELECT TABLE_NAME FROM USER_TABLES",
        }
        try:
            from src.extensions.db.db_agent import DatabaseAgent

            agent = DatabaseAgent()
            agent.connect()
            db_type = str(getattr(agent, "_db_type", "") or "").strip().lower()
            sql = _tables_sql.get(db_type)
            if not sql:
                return {
                    "passed": False,
                    "status": "UNVERIFIED",
                    "skipped": True,
                    "skip_reason": "db_table_accessed_unsupported_db",
                    "evidence": "目标库类型不支持枚举表: %r" % db_type,
                }
            res = agent.query(sql)
            rows = (res or {}).get("data") or []
            names = []
            for r in rows:
                try:
                    _n = (
                        str(list(r.values())[0])
                        if isinstance(r, dict)
                        else str(r[0])
                    )
                except Exception:
                    continue
                if _n and not _n.startswith("sqlite_"):
                    names.append(_n)
            if not names:
                return {
                    "passed": False,
                    "status": "FAIL",
                    "reason": "DB_NO_BUSINESS_TABLE",
                    "evidence": "库 %s 内无业务表（表数=0）" % db_type,
                }
            _sample = names[0]
            _cnt = "n/a"
            with guard("_table_accessed_handler: _r2 = agent.query('SELECT COUNT(*) A", log=_log):
                _r2 = agent.query('SELECT COUNT(*) AS n FROM "%s"' % _sample)
                _d2 = (_r2 or {}).get("data") or []
                if _d2:
                    _first = _d2[0]
                    _cnt = str(
                        list(_first.values())[0]
                        if isinstance(_first, dict)
                        else _first[0]
                    )
            return {
                "passed": True,
                "evidence": "目标库(%s)可访问: 业务表 %d 张，抽样 %s 行数=%s"
                % (db_type, len(names), _sample, _cnt),
            }
        except Exception as _e:
            return {
                "passed": False,
                "status": "UNVERIFIED",
                "skipped": True,
                "skip_reason": "db_unreachable",
                "evidence": "目标库不可访问（环境/配置问题，非产品缺陷）: %s"
                % str(_e)[:120],
            }

    async def _string_handler(self, ctx: dict, check: dict) -> dict:
        """字符串 check（SQL / 自然语言）→ verify_db，恢复线程用户上下文。"""
        from src.pipeline.steps.step4.db_verifier import verify_db
        from src.core.config_session import get_current_user, set_current_user

        _captured_user = get_current_user()

        def _verify_with_context(check_desc):
            if _captured_user:
                set_current_user(_captured_user)
            return verify_db(check_desc)

        import asyncio as _asyncio

        _desc = str(check.get("type", "") or "")
        _r = await _asyncio.to_thread(_verify_with_context, _desc)
        if _r.get("skipped"):
            return {
                "passed": True,
                "skipped": True,
                "status": "UNKNOWN",
                "skip_reason": _r.get("skip_reason", "db_skipped"),
                "evidence": "DB: %s→⊘" % _r.get("evidence", ""),
            }
        return {
            "passed": bool(_r.get("success", False)),
            "evidence": "DB: %s→%s"
            % (_r.get("evidence", ""), "✓" if _r.get("success") else "✗"),
            "data": _r.get("data", []),
        }


# ══════════════════════════════════════════════════════════════
# 单一事实源：各轨已注册 check 类型（生成端定稿守卫 + 执行端轨分派共用）
# ══════════════════════════════════════════════════════════════

_TRACK_TYPES_CACHE: dict | None = None


def registered_check_types() -> dict:
    """返回 ``{ui/api/db: frozenset(check 类型)}``（含别名 + ``_check`` 后缀展开）。

    根因（词表漂移，历史已复发 4 次：no_error / loading_complete / list_order /
      db_table_accessed_check）：生成端（Step3 定稿守卫）与执行端（Agent 路径的
      UI/API/DB 轨分派）各自**硬编码类型子集**，与真正的执行注册表
      （UiTrackVerifier / ApiTrackVerifier / DbTrackVerifier）脱钩 →
      「生成端发了、执行端不认」→ 未知检查类型 → 假 FAIL / 假 SKIP。
    根治：两端都从这里取词表；新增类型只需在各 TrackVerifier.__init__ 注册一次。
    通用性: 跨系统/行业/页面 —— 纯注册表投影，零业务硬编码。
    """
    global _TRACK_TYPES_CACHE
    if _TRACK_TYPES_CACHE is not None:
        return _TRACK_TYPES_CACHE
    _out: dict = {}
    for _name, _cls in (
        ("ui", UiTrackVerifier),
        ("api", ApiTrackVerifier),
        ("db", DbTrackVerifier),
    ):
        try:
            _v = _cls()
            _types = set(_v._handlers) | set(_v._aliases)
        except Exception as _e:  # noqa: BLE001
            _log.warning("[TrackProtocol] %s 轨注册表不可读: %r", _name, _e)
            _types = set()
        # ``_check`` 后缀展开：生成端发射 xxx_check，执行端注册裸名（分发层归一）
        _types |= {t + "_check" for t in _types if not t.endswith("_check")}
        _out[_name] = frozenset(_types)
    _TRACK_TYPES_CACHE = _out
    return _TRACK_TYPES_CACHE


# ══════════════════════════════════════════════════════════════
# 单一事实源：check 结果语义（三值聚合 + 展示标签）
# ══════════════════════════════════════════════════════════════
# 根因：
#   1) 各消费点各自写 ``"PASS" if r.get("passed") else "FAIL"`` ——
#      `skipped`/`UNVERIFIED` 的 check（passed=True 仅为不阻断）被渲染成 ``[PASS]``，
#      如 list_row_count_check「基线不可得，诚实跳过」显示为通过 → 结构性假 PASS。
#   2) 各聚合点各自写 ``all(r["passed"] for r in results)`` —— 无可判定结论的 check
#      被算作通过 → 整轨 passed=True → 未验证却报绿。
#   修复：把"结果 → 判定"的语义收拢到本模块唯一定义，生成/执行/展示三层共用。
#   通用性: 只看结果字段，与业务/系统/行业无关。

# 非判定态：无有效结论（既非通过也非产品缺陷）
_NON_VERDICT_STATUS = frozenset({
    "UNVERIFIED", "UNKNOWN", "SKIPPED", "NOT_APPLICABLE",
    "NOT_AVAILABLE", "NO_DATA", "ERROR",
})
_NON_VERDICT_REASONS = frozenset({"UNSUPPORTED_CHECK", "EXECUTOR_EXCEPTION"})

# ★ 环境探针型 check（2026-09-27 通用化）==================================
# 根因（实测假 PASS，TC-REQ-005-E01 等 9 条）：
#   `db_table_accessed_check` 的语义是"目标库可访问 + 有业务表可读"——
#   它的通过条件**只证明环境就绪**，与"需求是否被满足/被执行"毫无关系。
#   而生成端曾用它来"保住 db 轨道声明"（_fallback_cases._normalize_required_tracks
#   自动注入探针），于是用例的唯一断言=环境探针 → 只要库能连上就恒 PASS
#   （实测 conf=0.98、expected/actual/details 全空 = 典型无证据绿）。
# 原则：**探针结果不是判定** ——
#   · 探针"通过"不得构成用例 PASS（防假 PASS）；
#   · 探针"失败"（如库不可达/无业务表）亦不得构成用例 FAIL（环境问题，防假 Bug）。
#   两者统一落入非判定态（[SKIP]），由调用方按"无可判定断言"处理。
# 通用性：跨系统/行业 —— 纯语义分类，新增探针类型只需登记于此。
_PROBE_CHECK_TYPES: frozenset[str] = frozenset({
    "db_table_accessed_check",
    "db_table_accessed",
})


def is_probe_check(check_type: Any) -> bool:
    """该 check 类型是否为**环境/能力探针**（其结果不构成需求判定）。"""
    return str(check_type or "").strip() in _PROBE_CHECK_TYPES


def is_verdict(result: Any) -> bool:
    """该 check 结果是否构成**有效判定**（真通过或真失败）。

    非判定态（skipped / UNVERIFIED / NO_DATA / ERROR / 未知类型 / **环境探针**）
    不构成判定：
      - 既不能算通过（防假 PASS），
      - 也不能算失败（防假 Bug，如环境不可达、引擎能力缺口）。
    """
    if not isinstance(result, dict):
        return False
    if is_probe_check(result.get("type")):
        return False
    if bool(result.get("skipped")):
        return False
    _status = str(result.get("status", "") or "").upper()
    if _status in _NON_VERDICT_STATUS:
        return False
    _reason = str(result.get("reason", "") or "")
    return _reason not in _NON_VERDICT_REASONS


def render_check_tag(result: Any) -> str:
    """把单条 check 结果渲染为 ``[PASS]/[FAIL]/[SKIP]/[ERROR]``（唯一语义权威）。

    · 非判定态且属引擎/环境侧（UNSUPPORTED_CHECK / ERROR）→ ``[ERROR]``
    · 环境探针（无论其 passed 真假）→ ``[SKIP]``（只说明环境状态，非判定）
    · 其余非判定态 → ``[SKIP]``（无结论，不记通过也不记缺陷）
    · 构成判定 → 按 passed 判 ``[PASS]/[FAIL]``
    """
    _r = result if isinstance(result, dict) else {}
    _reason = str(_r.get("reason", "") or "")
    _status = str(_r.get("status", "") or "").upper()
    if _reason in _NON_VERDICT_REASONS or _status == "ERROR":
        return "[ERROR]"
    if is_probe_check(_r.get("type")):
        return "[SKIP]"
    if bool(_r.get("skipped")) or _status in _NON_VERDICT_STATUS:
        return "[SKIP]"
    return "[PASS]" if _r.get("passed") else "[FAIL]"


# 判定标签 → 展示符号（唯一事实源：下游渲染/文本嗅探一律取此表，勿各自硬编码）
_CHECK_TAG_SYMBOLS = {
    "[PASS]": "✓",
    "[FAIL]": "✗",
    "[SKIP]": "⊘",
    "[ERROR]": "⚠",
}

# 非判定态展示符号（SKIP/ERROR）——文本兜底判据据此识别"无结论"片段，
# 与 render_check_tag 的语义严格一致（同一权威，不另立词表）。
NON_VERDICT_RENDER_SYMBOLS = (
    _CHECK_TAG_SYMBOLS["[SKIP]"],
    _CHECK_TAG_SYMBOLS["[ERROR]"],
)


def render_check_symbol(result: Any) -> str:
    """把单条 check 结果渲染为其展示符号（✓/✗/⊘/⚠）。"""
    return _CHECK_TAG_SYMBOLS.get(render_check_tag(result), "?")


def is_negative_verdict(result: Any) -> bool:
    """该 check 结果是否为**真实负向判定**（构成产品缺陷证据）。

    等价于 ``is_verdict(result) and not result["passed"]`` —— 供上游统一复用，
    避免各处再拼 ``passed is False and reason not in (...)`` 这类漂移判据。
    """
    return is_verdict(result) and not bool((result or {}).get("passed"))


# ── 单一事实源：「能否单独归因产品缺陷」──────────────────────────────
# 「负向判定」与「可归因产品缺陷」是两条语义：前者只说"断言给出否定结论"，
# 后者才允许升格 PRODUCT_BUG。定位歧义型 check 的否定结论既可能是产品缺控件，
# 也可能是引擎选择器不匹配 —— 不得凭它定罪产品（防假 Bug）。
_LOCATOR_AMBIGUOUS_CHECK_TYPES = frozenset({"element_exists", "url_matches"})

# 具名断言族（文本投影）——与上面的结构化谓词同源：本元组是"哪些 check 类型
# 可归因产品缺陷"的唯一文本定义，供只有渲染文本可用的 Agent 路径兜底。
# 刻意不含 `_LOCATOR_AMBIGUOUS_CHECK_TYPES`。
PRODUCT_DEFECT_CHECK_MARKERS = (
    "viewport_rendering_check", "click_target_size_check", "viewport_overflow_check",
    "text_contains", "text_present", "semantic_check", "no_error",
    "a11y_violation", "db_field_value_check", "api_call_count_check",
    "db_table_accessed_check", "field_error_visible_check",
    "access_control_check",
)


def _bare_check_type(check_type: Any) -> str:
    """归一 check 类型名（去掉生成端惯用的 ``_check`` 后缀）。"""
    _t = str(check_type or "").strip()
    return _t[: -len("_check")] if _t.endswith("_check") else _t


def is_product_defect_verdict(result: Any) -> bool:
    """该 check 结果能否**单独归因**为产品缺陷（可据此判 PRODUCT_BUG）。

    比 `is_negative_verdict` 更严：定位歧义型（element_exists / url_matches）的
    否定结论不作产品缺陷证据，避免把引擎选择器不匹配写成产品缺陷。
    """
    if not is_negative_verdict(result):
        return False
    return _bare_check_type((result or {}).get("type")) not in _LOCATOR_AMBIGUOUS_CHECK_TYPES


def aggregate_check_results(results: Any) -> dict:
    """三值聚合 check 结果列表（唯一聚合权威）。

    返回::

        {
          "passed": bool,             # 有正向判定且无负向判定
          "has_positive": bool,       # 至少 1 条真实通过
          "has_negative": bool,       # 至少 1 条真实失败
          "has_product_defect": bool, # 至少 1 条可归因产品缺陷的失败（升格门槛）
          "no_verdict_only": bool,    # 有结论项为零（全 skipped/未验证）→ 应 SKIP
          "verdict_count": int,
        }

    语义：
      · 有真实失败 → passed=False（产品行为不满足）
      · 有真实通过且无失败 → passed=True
      · 一条判定都没有（全 skipped/UNVERIFIED/ERROR）→ passed=False 且
        no_verdict_only=True（调用方据此判 SKIP：既不假 PASS 也不假 FAIL）
      · has_product_defect 只由 `is_product_defect_verdict` 产生，排除定位歧义型 ——
        下游升格 PRODUCT_BUG 只读它，不得再按裸 passed 重推。
    """
    _res = results if isinstance(results, list) else []
    _pos = _neg = _pd = 0
    for _r in _res:
        if not is_verdict(_r):
            continue
        if _r.get("passed"):
            _pos += 1
        else:
            _neg += 1
            if is_product_defect_verdict(_r):
                _pd += 1
    return {
        "passed": bool(_pos > 0 and _neg == 0),
        "has_positive": bool(_pos > 0),
        "has_negative": bool(_neg > 0),
        "has_product_defect": bool(_pd > 0),
        "no_verdict_only": bool(_pos == 0 and _neg == 0 and len(_res) > 0),
        "verdict_count": _pos + _neg,
    }
