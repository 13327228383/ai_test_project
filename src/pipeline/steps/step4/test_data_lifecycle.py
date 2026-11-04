# -*- coding: utf-8 -*-
"""测试数据生命周期（T04，W2 P0-2）—— TestDataLifecycle。

职责：
  setup(case)   —— 分配命名空间 → DataSandbox 快照 → 顺序执行 setup_steps → 记台账
  teardown(case, exc) —— 台账驱动精确回滚 + DataSandbox.post_exec 泄漏检测

铁律：
  - 全同步（接入点 step4_executor 是同步上下文，不侵入 serial/parallel 的 async 循环）。
  - teardown 全程 try/except：**永不 raise、永不修改用例结果**；
    异常仅 _log.error(exc_info=True) + 台账项标 status='leaked'。
  - DB 写走独立受控通道 _lifecycle_db（白名单双闸门 + 参数化 + 台账），
    严禁放宽/复用 db_verifier.safety._validate_sql_safe（那是只读安全阀）。
  - DataSandbox mode 白名单：none/detect/rollback（配置字符串做白名单校验）。

★ §14 关键改动 [已复核-DeepSeek-V4-Flash 2026-08-05]：
  DB 写 + 3 文件，W2 最高风险项；未经过强模型（GLM/DeepSeek）复核，请勿视为已审核。
"""
from __future__ import annotations

import logging
from typing import Any

from src.core.pipeline_config import _read_config_value as _cfg
from src.pipeline.steps.step4 import _lifecycle_db

_log = logging.getLogger(__name__)

_SNAPSHOT_MODES: tuple[str, ...] = ("none", "detect", "rollback")


class TestDataLifecycle:
    """测试数据生命周期编排器（命名空间 / setup / teardown / 台账）。"""

    def __init__(self, run_id: str = "", project: str = "") -> None:
        self._run_id: str = run_id or ""
        self._project: str = project or ""
        self._ledger: list[dict[str, Any]] = []  # 台账（内存；W2 不建表，见设计 A8）
        self._sandboxes: dict[str, Any] = (
            {}
        )  # case_id → DataSandbox 实例（None 表示初始化失败）
        self._ns_counter: int = 0

    # ── 配置 ──

    @property
    def enabled(self) -> bool:
        """TDL_ENABLED 总开关（默认 False = 零行为变更）。"""
        return bool(_cfg("TDL_ENABLED", False))

    @property
    def snapshot_mode(self) -> str:
        """TDL_SNAPSHOT_MODE 白名单校验（none/detect/rollback），非法值 fail-closed 回落 detect。"""
        mode = str(_cfg("TDL_SNAPSHOT_MODE", "detect") or "detect").strip().lower()
        return mode if mode in _SNAPSHOT_MODES else "detect"

    def _alloc_ns(self) -> str:
        prefix = str(_cfg("TDL_NAMESPACE_PREFIX", "e2e") or "e2e").strip() or "e2e"
        seq = self._ns_counter
        self._ns_counter += 1
        run_tag = self._run_id or "manual"
        return f"{prefix}_{run_tag}_{seq}"

    # ── setup ──

    def setup(self, case: dict[str, Any]) -> dict[str, Any]:
        """分配命名空间 → 快照(DataSandbox.pre_exec) → 顺序执行 setup_steps → 记台账。

        返回 {"ok","skipped","ns","fixtures","error"}；
        skipped=True 表示未启用或该用例无 setup_steps（正常放行，非失败）。
        """
        if not self.enabled:
            return {"ok": True, "skipped": True, "ns": "", "fixtures": [], "error": ""}
        if not isinstance(case, dict):
            return {
                "ok": False,
                "skipped": False,
                "ns": "",
                "fixtures": [],
                "error": "case 非 dict",
            }
        case_id = str(case.get("case_id", "") or "")
        setup_steps = case.get("setup_steps") or []
        if not isinstance(setup_steps, list) or not setup_steps:
            return {"ok": True, "skipped": True, "ns": "", "fixtures": [], "error": ""}

        # 命名空间（data_seeds.namespace 显式指定则复用，否则分配）
        seeds = case.get("data_seeds") or {}
        ns = (
            str(seeds.get("namespace") or "").strip() if isinstance(seeds, dict) else ""
        )
        if not ns:
            ns = self._alloc_ns()

        # 快照（per-case 实例，避免多用例间串台）
        try:
            from src.ai.data_sandbox import DataSandbox

            sandbox = DataSandbox()
            snap = sandbox.pre_exec(mode=self.snapshot_mode, case_id=case_id)
            if not snap.get("success", True):
                _log.warning(
                    "[TDL] case=%s 快照失败(继续): %s", case_id, snap.get("error", "?")
                )
            self._sandboxes[case_id] = sandbox
        except Exception as e:  # noqa: BLE001
            _log.error(
                "[TDL] case=%s DataSandbox 初始化异常(继续): %s",
                case_id,
                e,
                exc_info=True,
            )
            self._sandboxes[case_id] = None

        fixtures: list[dict[str, Any]] = []
        for step in setup_steps:
            if not isinstance(step, dict):
                continue
            sid = str(step.get("id", "") or f"setup_{len(self._ledger)}")
            channel = str(step.get("channel", "") or "").strip().lower()
            target = str(step.get("target", "") or "")
            payload = step.get("payload") or {}
            capture = step.get("capture") or {}
            entry: dict[str, Any] = {
                "case_id": case_id,
                "step_id": sid,
                "channel": channel,
                "target": target,
                "table": target,
                "ns": ns,  # ★ P0-2 增量：记录命名空间，供 reset(ns) 精确回滚
                "ref": {},
                "status": "pending",
                "error": "",
            }
            if channel == "db":
                if not _lifecycle_db.assert_table_allowed(target):
                    # 白名单未放行 → 跳过 + warning（配置默认 False 的预期行为，非失败）
                    entry["status"] = "skipped"
                    entry["error"] = "table_not_allowed"
                    self._ledger.append(entry)
                    _log.warning(
                        "[TDL] case=%s setup step=%s 表 %s 不在白名单，跳过"
                        "（TDL_DB_WRITE_ENABLED / TDL_DB_TABLE_WHITELIST 双闸门）",
                        case_id,
                        sid,
                        target,
                    )
                    continue
                if not isinstance(payload, dict) or not payload:
                    entry["status"] = "error"
                    entry["error"] = "payload_empty"
                    self._ledger.append(entry)
                    return {
                        "ok": False,
                        "skipped": False,
                        "ns": ns,
                        "fixtures": fixtures,
                        "error": f"setup step {sid}: payload 为空",
                    }
                res = _lifecycle_db.db_seed(target, payload, ns, capture=capture)
                if not res.get("ok"):
                    entry["status"] = "error"
                    entry["error"] = str(res.get("error") or "db_seed_failed")[:200]
                    self._ledger.append(entry)
                    return {
                        "ok": False,
                        "skipped": False,
                        "ns": ns,
                        "fixtures": fixtures,
                        "error": f"setup step {sid}: {entry['error']}",
                    }
                entry["ref"] = dict(res.get("ref") or {})
                entry["status"] = "ok"
                self._ledger.append(entry)
                fixtures.append(
                    {
                        "step_id": sid,
                        "channel": "db",
                        "target": target,
                        "ref": dict(entry["ref"]),
                        "ns": ns,
                    }
                )
            elif channel == "api":
                # W2 无 API 数据构造器（仅 DB 通道受控写）；api setup 步骤跳过 + warning（记录在台账）
                entry["status"] = "skipped"
                entry["error"] = "api_channel_unavailable_w2"
                self._ledger.append(entry)
                _log.warning(
                    "[TDL] case=%s setup step=%s channel=api 暂不支持（W2 仅 db 受控通道），跳过",
                    case_id,
                    sid,
                )
            else:
                entry["status"] = "skipped"
                entry["error"] = "unknown_channel"
                self._ledger.append(entry)
                _log.warning(
                    "[TDL] case=%s setup step=%s 未知 channel=%r，跳过",
                    case_id,
                    sid,
                    channel,
                )

        return {
            "ok": True,
            "skipped": False,
            "ns": ns,
            "fixtures": fixtures,
            "error": "",
        }

    # ── teardown ──

    def teardown(
        self, case: dict[str, Any], exc: BaseException | None = None
    ) -> dict[str, Any]:
        """台账驱动精确回滚 + DataSandbox.post_exec 泄漏检测。

        ★ 失败安全铁律：全程 try/except，**永不 raise、永不修改用例结果**；
          异常仅 _log.error(exc_info=True) 并将台账项标 status='leaked'。
        返回 {"ok","rolled_back","leaked","error"}
        """
        result: dict[str, Any] = {
            "ok": True,
            "rolled_back": 0,
            "leaked": 0,
            "error": "",
        }
        if not self.enabled:
            return result
        case_id = str(case.get("case_id", "") or "")
        try:
            # 1) 台账驱动精确回滚（只按 setup 记录的主键删；ref 空由 db_rollback 拒绝）
            for entry in self._ledger:
                if entry.get("case_id") != case_id:
                    continue
                if entry.get("status") != "ok" or entry.get("channel") != "db":
                    continue
                _table = str(entry.get("table") or entry.get("target") or "").strip()
                if not _table or not entry.get("ref"):
                    entry["status"] = "leaked"
                    result["leaked"] += 1
                    _log.error(
                        "[TDL] case=%s 台账项缺 table/ref，无法精确回滚，记 leaked: step=%s",
                        case_id,
                        entry.get("step_id"),
                    )
                    continue
                try:
                    rb = _lifecycle_db.db_rollback(_table, dict(entry["ref"]))
                    if rb.get("ok"):
                        entry["status"] = "rolled_back"
                        result["rolled_back"] += int(rb.get("deleted", 0) or 0)
                    else:
                        entry["status"] = "leaked"
                        result["leaked"] += 1
                        _log.error(
                            "[TDL] case=%s 回滚失败(记 leaked): table=%s error=%s",
                            case_id,
                            _table,
                            rb.get("error", "?"),
                        )
                except Exception as e:  # noqa: BLE001
                    entry["status"] = "leaked"
                    result["leaked"] += 1
                    _log.error(
                        "[TDL] case=%s 回滚异常(记 leaked): %s",
                        case_id,
                        e,
                        exc_info=True,
                    )

            # 2) DataSandbox.post_exec 泄漏检测（detect/rollback 模式）
            sandbox = self._sandboxes.get(case_id)
            if sandbox is not None:
                try:
                    snap_res = sandbox.post_exec()
                    changes = snap_res.get("changes") or []
                    if changes:
                        result["leaked"] += len(changes)
                        _log.error(
                            "[TDL] case=%s 快照检测到 %d 个表变更（泄漏/副作用，未自动回滚）: %s",
                            case_id,
                            len(changes),
                            ", ".join(
                                "%s(%s→%s)"
                                % (
                                    c.get("table"),
                                    c.get("old_count"),
                                    c.get("new_count"),
                                )
                                for c in changes[:10]
                            ),
                        )
                except Exception as e:  # noqa: BLE001
                    _log.error(
                        "[TDL] case=%s post_exec 检测异常(不 raise): %s",
                        case_id,
                        e,
                        exc_info=True,
                    )
        except Exception as e:  # noqa: BLE001
            result["ok"] = False
            result["error"] = f"{type(e).__name__}: {e}"[:200]
            _log.error(
                "[TDL] teardown 异常（永不 raise，结果不受影响）: %s", e, exc_info=True
            )
        return result

    def reset(self, ns: str, seeds: list[Any] | None = None) -> dict[str, Any]:
        """一键回到确定性种子态（可重复调用、幂等、单入口）。

        流程：
          1) teardown 该命名空间 ns 的全部台账（精确回滚 + 清理台账项）；
          2) 按 seeds 确定性重 seed（复用 _lifecycle_db.db_seed 受控写通道）。

        ★ 复用既有能力：db_rollback / db_seed / assert_table_allowed（受控写通道，
          白名单双闸门），**不新造 DB 写通道**（架构红线）。
        ★ 失败安全：全程 try/except 永不 raise；异常仅记日志 + 返回 ok=False。

        参数:
            ns:    命名空间（应与 setup 时一致，或显式指定）。
            seeds: 确定性种子列表，每项形如
                   {"table":..., "payload":{...}, "capture":{...}, "ns":可选覆盖}
                   缺省为空列表（仅 teardown，不 reseed）。

        返回:
            {"reset_ns", "reseeded":int, "rolled_back":int, "leaked":int, "ok":bool, "error":""}
        """
        result: dict[str, Any] = {
            "reset_ns": ns,
            "reseeded": 0,
            "rolled_back": 0,
            "leaked": 0,
            "ok": True,
            "error": "",
        }
        if not self.enabled:
            # 未启用：幂等 no-op（零行为变更），仍返回结构化结果
            return result
        ns = str(ns or "").strip()
        if not ns:
            return {**result, "ok": False, "error": "ns 为空"}
        seeds = seeds or []

        # 1) teardown 该命名空间（复用 teardown 的回滚逻辑，按 ns 过滤台账）
        try:
            for entry in list(self._ledger):
                if entry.get("ns") != ns:
                    continue
                # 非 db / 非成功项：直接清理台账，避免孤儿项污染后续复位
                if entry.get("status") != "ok" or entry.get("channel") != "db":
                    if entry.get("status") in ("skipped", "error", "leaked", "rolled_back"):
                        try:
                            self._ledger.remove(entry)
                        except ValueError as _e:  # pragma: no cover - 并发安全
                            _log.debug("reset best-effort 容忍: %s", str(_e)[:120])
                    continue
                _table = str(entry.get("table") or entry.get("target") or "").strip()
                if not _table or not entry.get("ref"):
                    entry["status"] = "leaked"
                    result["leaked"] += 1
                    _log.error(
                        "[TDL] reset(ns=%s) 台账项缺 table/ref，无法精确回滚，记 leaked: step=%s",
                        ns,
                        entry.get("step_id"),
                    )
                    continue
                try:
                    rb = _lifecycle_db.db_rollback(_table, dict(entry["ref"]))
                    if rb.get("ok"):
                        entry["status"] = "rolled_back"
                        result["rolled_back"] += int(rb.get("deleted", 0) or 0)
                    else:
                        entry["status"] = "leaked"
                        result["leaked"] += 1
                        _log.error(
                            "[TDL] reset(ns=%s) 回滚失败(记 leaked): table=%s error=%s",
                            ns,
                            _table,
                            rb.get("error", "?"),
                        )
                except Exception as e:  # noqa: BLE001
                    entry["status"] = "leaked"
                    result["leaked"] += 1
                    _log.error(
                        "[TDL] reset(ns=%s) 回滚异常(记 leaked): %s", ns, e, exc_info=True
                    )
            # 清理已回滚 / 非 db 台账项，避免重复复位
            self._ledger = [
                e
                for e in self._ledger
                if not (
                    e.get("ns") == ns
                    and e.get("status") in ("rolled_back", "skipped", "error")
                )
            ]
        except Exception as e:  # noqa: BLE001
            result["ok"] = False
            result["error"] = f"{type(e).__name__}: {e}"[:200]
            _log.error("[TDL] reset teardown 阶段异常(不 raise): %s", e, exc_info=True)

        # 2) 重新 seed 确定性基线（复用 db_seed，受控写通道 + 白名单双闸门）
        for seed in seeds or []:
            if not isinstance(seed, dict):
                continue
            _target = str(seed.get("table") or seed.get("target") or "").strip()
            _payload = seed.get("payload") or {}
            _capture = seed.get("capture") or {}
            _seed_ns = str(seed.get("ns") or ns).strip() or ns
            if not _target or not isinstance(_payload, dict) or not _payload:
                _log.warning("[TDL] reset seed 跳过(无效项): table=%s", _target)
                continue
            if not _lifecycle_db.assert_table_allowed(_target):
                _log.warning(
                    "[TDL] reset seed 表 %s 不在白名单，跳过（双闸门保护）", _target
                )
                continue
            try:
                res = _lifecycle_db.db_seed(_target, _payload, _seed_ns, capture=_capture)
                if res.get("ok"):
                    result["reseeded"] += 1
                    self._ledger.append(
                        {
                            "case_id": f"__reset__{ns}",
                            "step_id": f"reset_{result['reseeded']}",
                            "channel": "db",
                            "target": _target,
                            "table": _target,
                            "ns": ns,
                            "ref": dict(res.get("ref") or {}),
                            "status": "ok",
                            "error": "",
                        }
                    )
                else:
                    _log.error(
                        "[TDL] reset seed 失败(table=%s): %s",
                        _target,
                        res.get("error", "?"),
                    )
            except Exception as e:  # noqa: BLE001
                _log.error(
                    "[TDL] reset seed 异常(table=%s): %s", _target, e, exc_info=True
                )

        return result
