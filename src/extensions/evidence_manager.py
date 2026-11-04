# -*- coding: utf-8 -*-
"""C5 执行证据留存 — ExecutionEvidenceManager
=============================================

确保执行过程中 **控制台日志 (console)** 与 **网络日志 (request/response)**
默认被留存，可检索 / 可关联 execution；**证据缺失时明确标记**（而非静默丢弃）。

设计边界（对齐项目红线）：
- 本模块为**纯内存收集 + 结构化报告**（含缺失标记），不写 DB、不依赖浏览器，
  可直接单测；
- engine 层「默认捕获」改造见 ``lifecycle.py``（[未复核-需 GLM/DeepSeek 把关]）；
- Google-style 注释 + 类型注解；单文件 < 800 行。

证据关联：以 ``(run_id, case_id)`` 为键存储，produce 的报告可序列化、可被
execution 记录引用。当某执行未挂监听 / 捕获为空时，报告显式标记
``retained=False, missing=True, missing_reason=...``，绝不静默丢证据。
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any, Optional

_log = logging.getLogger(__name__)


def _now_ts() -> str:
    """统一时间戳（秒级 ISO）。"""
    return datetime.now().isoformat(timespec="seconds")


class ExecutionEvidenceManager:
    """执行证据收集与留存管理器（纯内存，线程不安全——单执行器内顺序调用）。"""

    def __init__(self) -> None:
        # (run_id, case_id) -> {"console": [...], "network": [...]}
        self._store: dict[tuple[str, str], dict[str, list[dict[str, Any]]]] = {}

    # ── 键归一 ──

    @staticmethod
    def _key(run_id: str, case_id: str) -> tuple[str, str]:
        return (str(run_id or ""), str(case_id or ""))

    # ── 收集入口 ──

    def collect_console(
        self,
        run_id: str,
        case_id: str,
        level: str,
        text: str,
        url: str = "",
        ts: Optional[str] = None,
    ) -> None:
        """收集一条控制台日志。

        参数:
            run_id/case_id: 证据归属（可关联 execution）
            level:         "error" / "warn" / "info" / "debug" / "log"
            text:          日志内容
            url:           来源 URL（pageerror/console 的位置）
            ts:            可选时间戳，缺省取当前
        """
        slot = self._store.setdefault(self._key(run_id, case_id), {"console": [], "network": []})
        slot["console"].append({
            "ts": ts or _now_ts(),
            "level": str(level or "log"),
            "text": str(text or ""),
            "url": str(url or ""),
        })

    def collect_network(
        self,
        run_id: str,
        case_id: str,
        method: str,
        url: str,
        status: Optional[int] = None,
        elapsed_ms: Optional[float] = None,
        request: Optional[Any] = None,
        response: Optional[Any] = None,
        ts: Optional[str] = None,
    ) -> None:
        """收集一条网络日志（request/response 配对）。

        参数:
            method/url/status: HTTP 方法 / URL / 状态码
            elapsed_ms:        响应耗时（性能基线数据源）
            request/response:  可选的请求体 / 响应体（序列化后留存）
        """
        slot = self._store.setdefault(self._key(run_id, case_id), {"console": [], "network": []})
        entry: dict[str, Any] = {
            "ts": ts or _now_ts(),
            "method": str(method or "").upper(),
            "url": str(url or ""),
            "status": int(status) if status is not None else None,
            "elapsed_ms": round(float(elapsed_ms), 2) if elapsed_ms is not None else None,
        }
        if request is not None:
            entry["request"] = request if isinstance(request, (dict, list, str)) else str(request)
        if response is not None:
            entry["response"] = response if isinstance(response, (dict, list, str)) else str(response)
        slot["network"].append(entry)

    # ── 读取 / 报告 ──

    def get_evidence(self, run_id: str, case_id: str) -> dict[str, list[dict[str, Any]]]:
        """读取原始证据缓冲（无则返回空结构）。"""
        return self._store.get(self._key(run_id, case_id), {"console": [], "network": []})

    def build_report(
        self,
        run_id: str,
        case_id: str,
        execution_record: Optional[dict[str, Any]] = None,
        max_entries: int = 50,
    ) -> dict[str, Any]:
        """构建结构化执行证据报告（含缺失标记）。

        关键契约：**证据缺失必须显式标记**，绝不静默丢弃。
        缺失原因包括：
          - 未挂监听（engine 未捕获）
          - 该执行确实无 console / 网络活动（需与「监听故障」区分，故 reason 明确）

        返回:
            报告 dict：console / network 各自 retained、count、entries、missing、missing_reason；
            顶层含 run_id / case_id / execution_status / generated_at / all_present。
        """
        slot = self.get_evidence(run_id, case_id)
        console = slot.get("console", [])
        network = slot.get("network", [])

        console_block = self._evidence_block(
            console, max_entries,
            "no console listener attached or execution produced no console output",
        )
        network_block = self._evidence_block(
            network, max_entries,
            "no network listener attached or execution triggered no HTTP traffic",
        )

        # execution 状态回填（用于关联）
        status = ""
        if isinstance(execution_record, dict):
            status = str(execution_record.get("status", "") or "")

        all_present = console_block["retained"] and network_block["retained"]
        missing_types = [
            t for t, b in (("console", console_block), ("network", network_block))
            if not b["retained"]
        ]

        return {
            "generated_at": _now_ts(),
            "run_id": str(run_id or ""),
            "case_id": str(case_id or ""),
            "execution_status": status,
            "console": console_block,
            "network": network_block,
            "all_present": all_present,
            "missing_types": missing_types,
            # 关联标识：便于被 execution 记录引用
            "association_key": f"{run_id}::{case_id}",
        }

    @staticmethod
    def _evidence_block(
        entries: list[dict[str, Any]],
        max_entries: int,
        missing_reason: str,
    ) -> dict[str, Any]:
        """构造单一证据类型（console / network）的留存块。"""
        if entries:
            return {
                "retained": True,
                "missing": False,
                "missing_reason": "",
                "count": len(entries),
                "entries": entries[:max_entries],
                "truncated": len(entries) > max_entries,
            }
        return {
            "retained": False,
            "missing": True,
            "missing_reason": missing_reason,
            "count": 0,
            "entries": [],
            "truncated": False,
        }

    # ── 从 engine 证据缓冲消费（关联 execution）──

    def consume_engine_evidence(
        self,
        run_id: str,
        case_id: str,
        engine: Any = None,
        execution_record: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """从 engine 默认捕获缓冲（见 lifecycle.py）读取并构建报告。

        若 engine 未提供 ``_console_logs`` / ``_network_logs`` 缓冲，则显式标记缺失。
        """
        console_logs: list[dict[str, Any]] = []
        network_logs: list[dict[str, Any]] = []
        if engine is not None:
            raw_console = getattr(engine, "_console_logs", None)
            if isinstance(raw_console, list):
                for item in raw_console:
                    if isinstance(item, dict):
                        console_logs.append(item)
                    elif isinstance(item, str):
                        console_logs.append({"level": "log", "text": item, "ts": _now_ts()})
            raw_net = getattr(engine, "_network_logs", None)
            if isinstance(raw_net, list):
                network_logs.extend([i for i in raw_net if isinstance(i, dict)])

        # 写入本管理器（以便后续 retrieve / 关联）
        slot = self._store.setdefault(self._key(run_id, case_id), {"console": [], "network": []})
        slot["console"].extend(console_logs)
        slot["network"].extend(network_logs)

        return self.build_report(run_id, case_id, execution_record=execution_record)

    # ── 清理 ──

    def clear(self, run_id: Optional[str] = None, case_id: Optional[str] = None) -> None:
        """清理证据缓冲（指定键或全量）。"""
        if run_id is None and case_id is None:
            self._store.clear()
            return
        for k in list(self._store.keys()):
            if (run_id is None or k[0] == run_id) and (case_id is None or k[1] == case_id):
                del self._store[k]


# ═══════════════════════════════════════════════════════════════
# Markdown 渲染
# ═══════════════════════════════════════════════════════════════


def render_evidence_markdown(report: dict[str, Any]) -> str:
    """将执行证据报告渲染为 Markdown（不 raise）。"""
    if not isinstance(report, dict) or not report:
        return ""
    try:
        lines = [
            "## 执行证据留存（C5）",
            "",
            "> run_id：`%s` ｜ case_id：`%s` ｜ 执行状态：`%s` ｜ 生成：%s"
            % (
                report.get("run_id", "-"), report.get("case_id", "-"),
                report.get("execution_status", "-"), report.get("generated_at", "-"),
            ),
            "",
        ]
        for etype in ("console", "network"):
            blk = report.get(etype, {})
            if blk.get("retained"):
                lines.append(
                    "- ✅ **%s 证据已留存**：%s 条%s"
                    % (etype, blk.get("count", 0),
                       "（已截断）" if blk.get("truncated") else "")
                )
            else:
                lines.append(
                    "- ⚠️ **%s 证据缺失**：%s"
                    % (etype, blk.get("missing_reason", "未说明原因"))
                )
        if report.get("all_present"):
            lines.append("")
            lines.append("**结论**：console + network 证据均已留存，可关联 execution。")
        else:
            lines.append("")
            lines.append(
                "**结论**：存在证据缺口（%s），已显式标记，未静默丢弃。"
                % ", ".join(report.get("missing_types", []))
            )
        return "\n".join(lines)
    except Exception as exc:  # noqa: BLE001
        _log.warning("[evidence_manager] Markdown 渲染失败: %s", str(exc)[:160])
        return ""


__all__ = [
    "ExecutionEvidenceManager",
    "render_evidence_markdown",
]
