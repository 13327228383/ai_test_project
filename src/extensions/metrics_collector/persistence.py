# -*- coding: utf-8 -*-
"""持久化（_insert_single / flush_run / flush_all / _insert_batch）+ 置信度趋势 — 从 metrics_collector.py 拆出。"""

import json
from src.core.guarded import guard
from datetime import datetime
from typing import Any
import logging

_log = logging.getLogger(__name__)

# ★ 2026-08-26 收敛：MySQL 不可用时，指标降级到文件持久化 SQLite（offline_store 的
#   offline_outbox），重启不丢；MySQL 恢复后由 _maybe_replay_metrics 机会性回放同步回去，
#   与 case_upload_history / clarification_service 同构（Outbox 模式）。


def _metric_unique_key(entry: dict) -> str:
    """确定性唯一键：对一条指标的业务内容做稳定哈希，相同内容 → 相同键（入队覆盖）。

    ★ metrics 是 append-only 时序数据、无天然业务唯一键，早期用 uuid 作 key 会导致
      同一 entry 重复入队时产生多行、回放后 MySQL 出现重复行；改为内容哈希后，
      相同 entry（含 created_at）重复入队即被 offline_outbox 的 (kind, unique_key)
      唯一约束覆盖为一行，从源头消除重复回放。
    """
    import hashlib

    canonical = json.dumps(
        {
            "project": entry.get("project", ""),
            "version_id": entry.get("version_id") or 0,
            "run_id": entry.get("run_id", ""),
            "step": entry.get("step", 0),
            "metric_type": entry.get("metric_type", ""),
            "metric_name": entry.get("metric_name", ""),
            "category": entry.get("category", ""),
            "value": entry.get("value", 0),
            "total": entry.get("total", 0),
            "details": entry.get("details", ""),
            "created_at": entry.get("created_at", ""),
        },
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    return hashlib.sha1(canonical.encode("utf-8")).hexdigest()


class _PersistenceMixin:
    """MySQL 写入 + 缓冲管理 + JSONL 置信度趋势。"""

    # ── 外键自愈缓存 ──
    _known_projects: set = set()
    _fk_checked: bool = False

    def _ensure_project_exists(self, conn, project: str) -> bool:
        """确保 project 在 projects 表中存在（外键自愈）。成功返回 True。"""
        if not project or not project.strip():
            return False
        if project in self._known_projects:
            return True
        try:
            row = conn.execute(
                "SELECT 1 FROM projects WHERE name=%s LIMIT 1", (project,)
            ).fetchone()
            if row:
                self._known_projects.add(project)
                return True
            # 尝试插入 project（name 可能来自 config，projects 表支持直接 INSERT）
            conn.execute("INSERT INTO projects (name) VALUES (%s)", (project,))
            conn.commit()
            self._known_projects.add(project)
            _log.info(
                "  [MetricsCollector] 自愈: 已创建 project=%s 到 projects 表", project
            )
            return True
        except Exception as e:
            _log.warning(
                "  [MetricsCollector] 外键自愈失败 project=%s: %s",
                project,
                str(e)[:120],
            )
            return False

    def _insert_single(self, entry: dict) -> bool:
        """单条写入 ai_health_metrics。成功返回 True，失败返回 False（由调用方降级）。"""
        conn = None
        try:
            from src.models.base import get_conn

            conn = get_conn()
            project = entry.get("project", "") or ""
            if project and project.strip():
                self._ensure_project_exists(conn, project)
            conn.execute(
                "INSERT INTO ai_health_metrics (project, version_id, run_id, step, metric_type, metric_name, category, value, total, details, created_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    entry["project"],
                    entry.get("version_id") or 0,
                    entry["run_id"],
                    entry["step"],
                    entry["metric_type"],
                    entry["metric_name"],
                    entry["category"],
                    entry["value"],
                    entry["total"],
                    entry["details"],
                    entry["created_at"],
                ),
            )
            conn.commit()
            return True
        except Exception as ex:
            _log.warning(
                "  [MetricsCollector] 即时写入失败（交由上层降级到离线 outbox）: %s", ex
            )
            return False
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception as _pass_e:
                    _log.warning("非关键异常: %s", _pass_e, exc_info=True)

    def flush_run(self, run_id: str):
        """将指定 run_id 的缓冲数据写入 MySQL。"""
        with self._buffer_lock:
            entries = self._buffer.pop(run_id, [])
        # ★ 防无限循环：跳过已尝试 flush 的条目，避免 re-entrant flush 重复处理
        entries = [e for e in entries if not e.get("_flush_attempted")]
        if entries:
            self._insert_batch(entries)
        # ★ 收敛：flush 结束机会性回放积压的离线指标（MySQL 恢复时自动同步）
        self._maybe_replay_metrics()

    def flush_all(self):
        """将所有缓冲数据写入 MySQL。"""
        with self._buffer_lock:
            all_entries = []
            for run_entries in self._buffer.values():
                # ★ 防无限循环：跳过已尝试 flush 的条目，避免 re-entrant flush 无限循环
                all_entries.extend(
                    e for e in run_entries if not e.get("_flush_attempted")
                )
            self._buffer.clear()
        if all_entries:
            self._insert_batch(all_entries)
        # ★ 收敛：flush 结束机会性回放积压的离线指标（MySQL 恢复时自动同步）
        self._maybe_replay_metrics()

    def _insert_batch(self, entries: list[dict]):
        """批量 INSERT 到 ai_health_metrics 表。

        批次失败时，逐条重试（隔离单条坏数据），
        彻底失败时记录完整详情到 WARNING 日志，不再静默丢弃。

        ★ 防无限循环：标记 _flush_attempted，防止 re-entrant flush 重复处理同一批条目。
        """
        if not entries:
            return
        # ★ 标记 _flush_attempted：防止 flush_run/flush_all 再次 pick 这些条目导致无限循环
        for e in entries:
            e["_flush_attempted"] = True
        try:
            from src.models.base import get_conn
        except Exception as _e:
            _log.warning(
                "无法获取数据库连接，%d 条指标数据未能持久化: %s",
                len(entries),
                str(_e)[:120],
            )
            # ★ 收敛：MySQL 不可用，降级到文件持久化 outbox（重启不丢，恢复后回放）
            self._enqueue_metrics_batch(entries)
            return

        conn = None
        try:
            conn = get_conn()
            # 先确保所有涉及的 project 存在
            projects_set = set(
                e.get("project", "") for e in entries if e.get("project", "").strip()
            )
            for p in projects_set:
                self._ensure_project_exists(conn, p)
        except Exception as _e:
            _log.warning(
                "[MetricsCollector] 批量写入前 project 检查失败: %s", str(_e)[:120]
            )
            if conn:
                try:
                    conn.close()
                except Exception as _close_e:
                    _log.debug("连接关闭异常: %s", _close_e)
            conn = None

        if conn is None:
            # 无法获取连接，回退到逐条 _insert_single（它会自己获取连接）
            _log.warning(
                "[MetricsCollector] 批量连接失败，降级逐条写入 %d 条", len(entries)
            )
            failed = 0
            failed_raw = []
            for e in entries:
                if not self._insert_single(e):
                    failed += 1
                    failed_raw.append(e)
            if failed > 0:
                _log.warning(
                    "[MetricsCollector] 逐条写入仍有 %d/%d 条失败", failed, len(entries)
                )
                # ★ 收敛：彻底失败条目登记到文件 outbox（重启不丢，恢复后回放）
                self._enqueue_metrics_batch(failed_raw)
            return

        sql = "INSERT INTO ai_health_metrics (project, version_id, run_id, step, metric_type, metric_name, category, value, total, details, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
        rows = [
            (
                e["project"],
                e.get("version_id") or 0,
                e["run_id"],
                e["step"],
                e["metric_type"],
                e["metric_name"],
                e["category"],
                e["value"],
                e["total"],
                e["details"],
                e["created_at"],
            )
            for e in entries
        ]
        cursor = None
        try:
            cursor = conn.cursor()
            cursor.executemany(sql, rows)
            conn.commit()
            _log.info("  [MetricsCollector] 已持久化 %d 条健康度指标", len(rows))
        except Exception as ex:
            _log.warning(
                "  [MetricsCollector] 批量写入失败: %s，降级逐条重试", str(ex)[:120]
            )
            # 逐条重试，隔离坏数据
            ok = 0
            failed_entries = []
            failed_raw_entries = []
            for i, e in enumerate(entries):
                try:
                    conn.execute(
                        "INSERT INTO ai_health_metrics (project, version_id, run_id, step, metric_type, metric_name, category, value, total, details, created_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        (
                            e["project"],
                            e.get("version_id") or 0,
                            e["run_id"],
                            e["step"],
                            e["metric_type"],
                            e["metric_name"],
                            e["category"],
                            e["value"],
                            e["total"],
                            e["details"],
                            e["created_at"],
                        ),
                    )
                    conn.commit()
                    ok += 1
                except Exception as single_ex:
                    failed_entries.append(
                        {
                            "metric_type": e.get("metric_type", "?"),
                            "metric_name": e.get("metric_name", "?"),
                            "project": e.get("project", "?"),
                            "run_id": e.get("run_id", "?"),
                            "error": str(single_ex)[:200],
                        }
                    )
                    failed_raw_entries.append(e)
            _log.info("  [MetricsCollector] 逐条重试: 成功 %d/%d", ok, len(entries))
            if failed_entries:
                _log.warning(
                    "  [MetricsCollector] 彻底失败的条目 (%d 条): %s",
                    len(failed_entries),
                    json.dumps(failed_entries, ensure_ascii=False, default=str),
                )
                # ★ 收敛：彻底失败条目登记到文件 outbox（重启不丢，恢复后回放）
                self._enqueue_metrics_batch(failed_raw_entries)
        finally:
            if cursor is not None:
                try:
                    cursor.close()
                except Exception as _pass_e:
                    _log.warning("非关键异常: %s", _pass_e, exc_info=True)
            if conn is not None:
                try:
                    conn.close()
                except Exception as _pass_e:
                    _log.warning("非关键异常: %s", _pass_e, exc_info=True)

    # ── 离线回放同步（Outbox 模式，与 case_upload_history / clarification_service 同构）──

    def _enqueue_metric(self, entry: dict) -> None:
        """MySQL 不可用时，把一条指标登记到离线 outbox（文件持久化，重启不丢）。

        ★ 唯一键用业务内容确定性哈希（非 uuid）：同一 entry 重复入队会被覆盖为一行，
          避免离线积压产生重复回放；区别于会话快照的 upsert 覆盖语义。
        """
        with guard("_enqueue_metric: from src.core import offline_store", log=_log, level=logging.WARNING):
            from src.core import offline_store

            offline_store.enqueue("ai_health_metrics", entry, _metric_unique_key(entry))

    def _enqueue_metrics_batch(self, entries) -> None:
        """批量把「最终未写走」的指标登记到离线 outbox。"""
        for e in entries:
            self._enqueue_metric(e)

    def _replay_metric(self, payload: dict) -> bool:
        """把一条离线指标用 MySQL 直写回填（不回走降级/enqueue，避免重入）。

        ★ 回放前按业务键查重：ai_health_metrics 无唯一键，若已存在同构行则视为已回放，
          避免 outbox 残留或历史重复入队导致 MySQL 重复行。
        """
        conn = None
        try:
            from src.models.base import get_conn

            conn = get_conn()
        except Exception:
            return False
        try:
            project = payload.get("project", "")
            version_id = payload.get("version_id") or 0
            run_id = payload.get("run_id", "")
            step = payload.get("step", 0)
            metric_type = payload.get("metric_type", "")
            metric_name = payload.get("metric_name", "")
            category = payload.get("category", "")
            created_at = payload.get("created_at", "")
            exists = conn.execute(
                "SELECT COUNT(*) AS c FROM ai_health_metrics "
                "WHERE project=%s AND version_id=%s AND run_id=%s AND step=%s "
                "AND metric_type=%s AND metric_name=%s AND category=%s AND created_at=%s",
                (
                    project,
                    version_id,
                    run_id,
                    step,
                    metric_type,
                    metric_name,
                    category,
                    created_at,
                ),
            ).fetchone()
            if exists and int(exists.get("c") or 0) > 0:
                return True
            conn.execute(
                "INSERT INTO ai_health_metrics (project, version_id, run_id, step, metric_type, metric_name, category, value, total, details, created_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    project,
                    version_id,
                    run_id,
                    step,
                    metric_type,
                    metric_name,
                    category,
                    payload.get("value", 0),
                    payload.get("total", 0),
                    payload.get("details", ""),
                    created_at,
                ),
            )
            conn.commit()
            return True
        except Exception as e:
            _log.warning("[MetricsCollector] 离线指标回放失败: %s", e)
            return False
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception as _e:
                    _log.debug("_replay_metric best-effort 容忍: %s", str(_e)[:120])

    def _maybe_replay_metrics(self) -> None:
        """MySQL 恢复时机会性回放积压的离线指标。"""
        with guard("_maybe_replay_metrics: from src.core import offline_store", log=_log, level=logging.WARNING):
            from src.core import offline_store

            n = offline_store.drain("ai_health_metrics", self._replay_metric)
            if n:
                _log.info("[MetricsCollector] 已回放 %d 条离线指标到 MySQL", n)

    def _load_confidence_history_if_needed(self):
        """★ 已 MySQL 化：历史趋势在 get_confidence_trend 时直接查 ai_health_metrics，
        不再读 JSONL 文件（原文件只读不写，跨 run 历史全丢）。保留签名供兼容，内部 no-op。
        _confidence_history 仅缓存当前进程内记录，作 MySQL 不可用时的降级。"""
        self._confidence_history_loaded = True

    def record_confidence_run(
        self,
        run_id: str,
        project_name: str,
        avg_confidence: float,
        pass_rate: float,
        block_rate: float,
    ):
        """记录单批次置信度汇总：写内存（同进程快速读）+ 写 MySQL ai_health_metrics（跨进程历史）。
        ★ 原 impl 只写内存 + 读死 JSONL（只读不写），跨 run 历史丢失。现接通 MySQL 为唯一事实源。"""
        # ★ 版本随项目当前 active version 动态注入，避免置信度趋势全部落在 version_id=0
        try:
            from src.core.versioning import current_version_id

            _entry_version_id = current_version_id(project_name)
        except Exception as _e:
            _entry_version_id = 0
            _log.debug("[MetricsCollector] 版本ID解析失败: %s", _e, exc_info=True)
        # ★ 2026-09-22 时区根治：与 record()/全库约定一致，改用服务器本地时间
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        entry = {
            "run_id": run_id,
            "project": project_name,
            "version_id": _entry_version_id,
            "ts": ts,
            "avg_confidence": (
                float(avg_confidence) if avg_confidence is not None else 0.0
            ),
            "pass_rate": float(pass_rate) if pass_rate is not None else 0.0,
            "block_rate": float(block_rate) if block_rate is not None else 0.0,
            "_persisted": False,
        }
        try:
            with self._confidence_lock:
                self._confidence_history.append(entry)
        except Exception as _pass_e:
            _log.warning("非关键异常: %s", _pass_e, exc_info=True)
        # ★ 持久化到 MySQL ai_health_metrics（跨进程/跨 run 历史趋势的唯一事实源）
        with guard("record_confidence_run: self._insert_single( { 'project': pr", log=_log):
            self._insert_single(
                {
                    "project": project_name or "",
                    "run_id": run_id or "",
                    "step": 0,
                    "metric_type": "confidence_trend",
                    "metric_name": "batch_summary",
                    "category": "",
                    "value": entry["avg_confidence"],
                    "total": 0,
                    "details": json.dumps(
                        {
                            "pass_rate": entry["pass_rate"],
                            "block_rate": entry["block_rate"],
                            "ts": ts,
                        },
                        ensure_ascii=False,
                    ),
                    "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                }
            )
            entry["_persisted"] = True

    def get_confidence_trend(
        self, project_name: str, limit: int = 10
    ) -> list[dict[str, Any]]:
        """返回最近 N 次批次的置信度数据（按时间倒序）。★ 已 MySQL 化：查 ai_health_metrics。"""
        # 1) MySQL 跨进程历史（唯一事实源）
        # ★ 版本隔离：仅读当前版本（同项目旧版本/legacy 不混入），与写入端 current_version_id 同源
        conn = None
        result: list[dict[str, Any]] = []
        try:
            from src.core.versioning import current_version_id
            from src.models.base import get_conn

            _read_version_id = current_version_id(project_name)
            conn = get_conn()
            rows = conn.execute(
                "SELECT run_id, value AS avg_confidence, details, created_at "
                "FROM ai_health_metrics "
                "WHERE project=%s AND version_id=%s AND metric_type='confidence_trend' "
                "ORDER BY created_at DESC LIMIT %s",
                (project_name or "", _read_version_id, int(limit)),
            ).fetchall()
            for r in rows:
                det = r["details"]
                if isinstance(det, str):
                    try:
                        det = json.loads(det)
                    except Exception:
                        det = {}
                elif det is None:
                    det = {}
                result.append(
                    {
                        "run_id": r["run_id"],
                        "ts": str(r["created_at"]) if r["created_at"] else "",
                        "avg_confidence": float(r["avg_confidence"] or 0.0),
                        "pass_rate": float((det or {}).get("pass_rate", 0.0)),
                        "block_rate": float((det or {}).get("block_rate", 0.0)),
                    }
                )
        except Exception as _e:
            _log.debug(
                "[MetricsCollector] 置信度趋势 MySQL 查询失败，降级内存: %s",
                str(_e)[:80],
            )
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception as _pass_e:
                    _log.warning("非关键异常: %s", _pass_e, exc_info=True)
        # 2) MySQL 不可用或无数据时，降级到同进程内存
        if not result:
            try:
                with self._confidence_lock:
                    result = [
                        {
                            "run_id": it.get("run_id"),
                            "ts": it.get("ts"),
                            "avg_confidence": it.get("avg_confidence", 0.0),
                            "pass_rate": it.get("pass_rate", 0.0),
                            "block_rate": it.get("block_rate", 0.0),
                        }
                        for it in self._confidence_history
                        if it.get("project") == project_name
                    ]
                result.sort(key=lambda x: x.get("ts") or "", reverse=True)
                result = result[: int(limit)]
            except Exception:
                result = []
        return result
