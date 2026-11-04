# -*- coding: utf-8 -*-
"""性能基线存储 — 按靶子分文件、多指标聚合、原子写、可重置。

★ 本文件在 2026-08 「性能测试体系补全」中被**重写**：
  旧实现只面向「用例耗时 elapsed_ms」单指标、单文件 ``data/perf_baseline/baseline.json``、
  无靶子隔离；新实现按 ``baselines/<target>.json`` 分文件，支持任意指标集合、
  ``baseline_version`` 版本号与 ``reset()``。
  旧接口 ``get_case_baseline / get_all_baselines / get_threshold / compare``
  以兼容层形式保留（全仓库原本零调用方，保留仅为防御性向后兼容）。

文件 schema 见系统设计 §8.3。写入采用 ``tmp + os.replace`` 原子落盘，
避免进程被杀时留下半截 JSON。

对外方法全部**不 raise**：异常内部 catch → warning → 安全默认值。

[已复核-DeepSeek-V3 2026-08-16]
"""

from __future__ import annotations

import json
import logging
import os
import threading
from typing import Any, Optional

from src.extensions.perf_baseline.schemas import (
    KIND_WEB,
    PERCENTILE_ALGO,
    MetricStat,
    PerfSample,
    now_ts,
)

_log = logging.getLogger(__name__)

#: 默认基线存储目录（随仓库入库，便于跨机比对；CI 需干净工作区时自行 gitignore）
DEFAULT_BASELINE_DIR = os.path.join(os.path.dirname(__file__), "baselines")

#: 基线文件 schema 版本
SCHEMA_VERSION = 1

#: 进程内写锁（跨实例保护同一文件；os.replace 保证跨进程原子性）
_write_lock = threading.RLock()

_RE_UNSAFE_CHARS = ("/", "\\", ":", "*", "?", '"', "<", ">", "|", " ")


def _safe_target(target: str) -> str:
    """靶子名 → 安全文件名。空值回落 ``unknown``。"""
    name = (target or "").strip()
    for ch in _RE_UNSAFE_CHARS:
        name = name.replace(ch, "_")
    return name or "unknown"


class BaselineManager:
    """单个靶子的性能基线读写。

    用法::

        bm = BaselineManager("smartshop")
        entry = bm.get_entry("web::/product/{id}")     # None 表示首次见到
        bm.update(samples)                             # 聚合 + version+1 + 原子写
        bm.reset()                                     # 清空 entries（保留 target）
    """

    def __init__(self, target: str, storage_dir: Optional[str] = None) -> None:
        """
        参数：
            target: 靶子名（如 ``"smartshop"``）。
            storage_dir: 基线目录，缺省 ``src/extensions/perf_baseline/baselines/``。
        """
        self._target: str = _safe_target(target)
        self._dir: str = storage_dir or DEFAULT_BASELINE_DIR
        self._path: str = os.path.join(self._dir, f"{self._target}.json")
        self._data: dict[str, Any] = self.load()

    # ── 属性 ───────────────────────────────────────────────────────────
    @property
    def target(self) -> str:
        """当前靶子名。"""
        return self._target

    @property
    def path(self) -> str:
        """基线文件绝对路径。"""
        return os.path.abspath(self._path)

    def version(self) -> int:
        """当前基线版本号（每次成功 ``update`` 递增 1）。"""
        try:
            return int(self._data.get("baseline_version", 0))
        except (TypeError, ValueError):
            return 0

    def entries(self) -> dict[str, Any]:
        """全部基线条目（只读副本）。"""
        raw = self._data.get("entries")
        return dict(raw) if isinstance(raw, dict) else {}

    # ── 读写 ───────────────────────────────────────────────────────────
    def _empty(self) -> dict[str, Any]:
        """空基线骨架。"""
        stamp = now_ts()
        return {
            "schema_version": SCHEMA_VERSION,
            "target": self._target,
            "baseline_version": 0,
            "created_at": stamp,
            "updated_at": stamp,
            "runs_aggregated": 0,
            "last_run_id": "",
            # 整改 B2：记录 p50/p95 的算法版本。读取方据此判断该基线能否与
            # 其它算法版本产出的分位数同口径比较（历史朴素取整数据不可比）。
            "percentile_algo": PERCENTILE_ALGO,
            "entries": {},
        }

    def load(self) -> dict[str, Any]:
        """从磁盘加载基线。文件不存在 / 损坏时返回空骨架（不 raise）。"""
        if not os.path.isfile(self._path):
            self._data = self._empty()
            return self._data
        try:
            with open(self._path, "r", encoding="utf-8") as fp:
                parsed = json.load(fp)
            if not isinstance(parsed, dict) or not isinstance(parsed.get("entries"), dict):
                raise ValueError("基线文件结构非法")
            parsed.setdefault("schema_version", SCHEMA_VERSION)
            parsed.setdefault("baseline_version", 0)
            parsed.setdefault("runs_aggregated", 0)
            parsed.setdefault("last_run_id", "")
            # 整改 B2：本包的 p50/p95 一直由 schemas.percentile（线性插值）产出，
            # 历史文件只是没写这个字段，补默认值即为真实算法，不存在口径漂移。
            # （朴素取整口径只存在于 step5 的 MySQL 链路，不会写到这里。）
            parsed.setdefault("percentile_algo", PERCENTILE_ALGO)
            parsed["target"] = self._target
            self._data = parsed
        except Exception as exc:  # noqa: BLE001 — 损坏文件不得阻断流水线
            _log.warning(
                "[perf_gate] 基线文件损坏，按空基线处理 (%s): %s",
                self._path,
                str(exc)[:120],
            )
            self._data = self._empty()
        return self._data

    def save(self) -> bool:
        """原子写回磁盘（tmp + os.replace）。返回是否成功，不 raise。"""
        with _write_lock:
            tmp_path = f"{self._path}.tmp.{os.getpid()}"
            try:
                os.makedirs(self._dir, exist_ok=True)
                with open(tmp_path, "w", encoding="utf-8") as fp:
                    json.dump(self._data, fp, ensure_ascii=False, indent=2)
                    fp.flush()
                    os.fsync(fp.fileno())
                os.replace(tmp_path, self._path)
                return True
            except Exception as exc:  # noqa: BLE001
                _log.warning("[perf_gate] 基线落盘失败(不阻断): %s", str(exc)[:160])
                try:
                    if os.path.isfile(tmp_path):
                        os.remove(tmp_path)
                except OSError as clean_exc:
                    # 残留 tmp 文件不影响正确性（下次 save 会覆盖），仅记录
                    _log.warning(
                        "[perf_gate] 清理临时基线文件失败: %s (%s)",
                        tmp_path,
                        str(clean_exc)[:100],
                    )
                return False

    # ── 查询 ───────────────────────────────────────────────────────────
    def get_entry(self, key: str) -> Optional[dict]:
        """取指定 key 的基线条目。不存在返回 ``None``（→ BASELINE_ESTABLISHED）。"""
        if not key:
            return None
        entries = self._data.get("entries")
        if not isinstance(entries, dict):
            return None
        entry = entries.get(key)
        return entry if isinstance(entry, dict) and entry.get("metrics") else None

    def has_baseline(self, key: str) -> bool:
        """该 key 是否已有基线。"""
        return self.get_entry(key) is not None

    # ── 更新 ───────────────────────────────────────────────────────────
    def update(self, samples: list, run_id: str = "") -> None:
        """用本轮样本聚合更新基线，``baseline_version`` +1 并原子落盘。

        参数：
            samples: :class:`PerfSample` 列表（也兼容 dict 与旧版
                ``{"case_id","elapsed_ms"}`` 记录）。无效样本自动跳过。
            run_id: 本轮运行 id，仅记录到 ``last_run_id``，不参与聚合。

        说明：
            只有 ``is_valid()`` 的样本参与聚合 —— 采集失败的样本**绝不污染基线**。
        """
        normalized = [s for s in (_coerce_samples(samples)) if s.is_valid()]
        if not normalized:
            _log.info("[perf_gate] 无有效样本，基线未更新 (target=%s)", self._target)
            return

        stamp = now_ts()
        entries = self._data.setdefault("entries", {})
        if not isinstance(entries, dict):
            entries = {}
            self._data["entries"] = entries

        for sample in normalized:
            key = sample.key or ""
            if not key:
                continue
            entry = entries.get(key)
            if not isinstance(entry, dict):
                entry = {
                    "kind": sample.kind or KIND_WEB,
                    "samples": 0,
                    "first_seen": stamp,
                    "last_updated": stamp,
                    "metrics": {},
                }
                entries[key] = entry
            entry["kind"] = sample.kind or entry.get("kind") or KIND_WEB
            entry["last_updated"] = stamp
            entry["samples"] = int(entry.get("samples", 0) or 0) + 1

            metric_map = entry.get("metrics")
            if not isinstance(metric_map, dict):
                metric_map = {}
                entry["metrics"] = metric_map
            for metric_name, value in (sample.metrics or {}).items():
                stat = MetricStat.from_dict(metric_map.get(metric_name))
                stat.merge(value)
                metric_map[str(metric_name)] = stat.to_dict()

        self._data["baseline_version"] = self.version() + 1
        self._data["runs_aggregated"] = int(self._data.get("runs_aggregated", 0) or 0) + 1
        self._data["updated_at"] = stamp
        if run_id:
            self._data["last_run_id"] = str(run_id)
        self._data.setdefault("created_at", stamp)
        self._data["schema_version"] = SCHEMA_VERSION
        self._data["target"] = self._target

        self.save()
        _log.info(
            "[perf_gate] 基线已更新 target=%s version=%d keys=%d samples=%d",
            self._target,
            self.version(),
            len(entries),
            len(normalized),
        )

    def reset(self) -> None:
        """清空全部基线条目（保留 target 与创建时间），并落盘。"""
        created = self._data.get("created_at") or now_ts()
        self._data = self._empty()
        self._data["created_at"] = created
        self.save()
        _log.warning("[perf_gate] 基线已重置: target=%s", self._target)

    # ── 兼容层（旧接口，全仓库原本零调用方，保留以防外部脚本引用）──────
    def get_case_baseline(self, case_id: str) -> Optional[dict]:
        """[兼容] 按旧 case 语义查询，映射到 ``case::<case_id>`` key。"""
        return self.get_entry(f"case::{case_id}")

    def get_all_baselines(self) -> dict:
        """[兼容] 返回全部条目。"""
        return self.entries()

    def get_threshold(self, key: str, multiplier: float = 2.0) -> float:
        """[兼容] 返回 ``mean × multiplier``（取该 key 第一个指标）。无基线返回 0。"""
        entry = self.get_entry(key) or self.get_entry(f"case::{key}")
        if not entry:
            return 0.0
        metrics = entry.get("metrics") or {}
        for stat in metrics.values():
            if isinstance(stat, dict):
                try:
                    return round(float(stat.get("mean", 0.0)) * multiplier, 2)
                except (TypeError, ValueError):
                    continue
        return 0.0


def write_after_gate(
    manager: BaselineManager,
    samples: Any,
    calibration_cfg: Any = None,
    run_id: str = "",
) -> dict[str, Any]:
    """门禁判定后基线回写的**唯一策略入口**（靶子链路与流水线 step5 链路共用）。

    策略（参照系稳定优先）：
      - 基线尚未建立（version<=0，首轮）→ 自动建立；
      - 已建立 → 常规 run 只判定不回写，否则参照系随每次运行漂移；
      - 仅 ``calibration_cfg["update_baseline"]=true``（校准模式）才回写。

    返回 ``calibration`` 状态 dict（``mode`` / ``status``，必要时 ``before``/``after``/
    ``reason``），调用方据实报告，不得自行重推策略。**不 raise**。
    """
    cfg = calibration_cfg if isinstance(calibration_cfg, dict) else {}
    calibration: dict[str, Any] = {"mode": bool(cfg.get("update_baseline", False))}
    try:
        valid = [s for s in _coerce_samples(samples) if s.is_valid()]
        need_update = calibration["mode"]
        before = int(manager.version() or 0)
        if not need_update and before <= 0:
            need_update = True  # 首轮自动建立基线
        if not need_update:
            calibration["status"] = "skip_regular"
            return calibration
        if not valid:
            calibration["status"] = "skipped_no_samples"
            calibration["reason"] = "无有效样本，基线未更新（防止污染）"
            _log.warning("[perf_gate] 无有效样本，基线未更新 target=%s", manager._target)
            return calibration
        manager.update(valid, run_id=run_id or "")
        calibration["status"] = "updated"
        calibration["before"] = before
        calibration["after"] = int(manager.version() or 0)
    except Exception as exc:  # noqa: BLE001 — 基线回写失败不阻断主流程
        calibration["status"] = "error"
        calibration["reason"] = str(exc)[:120]
        _log.warning("[perf_gate] 基线回写失败(不阻断): %s", str(exc)[:120])
    return calibration


def _coerce_samples(samples: Any) -> list[PerfSample]:
    """把任意输入宽容转换为 :class:`PerfSample` 列表（不 raise）。

    支持三种形态：
      1. :class:`PerfSample` 实例
      2. 新 schema dict（含 ``key`` / ``metrics``）
      3. 旧 schema dict（``{"case_id": ..., "elapsed_ms": ...}``）→ 映射为
         ``key="case::<case_id>"``、``metrics={"elapsed_ms": ...}``
    """
    if not isinstance(samples, (list, tuple)):
        return []
    result: list[PerfSample] = []
    for item in samples:
        if isinstance(item, PerfSample):
            result.append(item)
            continue
        if not isinstance(item, dict):
            continue
        if "key" in item or "metrics" in item:
            result.append(PerfSample.from_dict(item))
            continue
        case_id = item.get("case_id")
        elapsed = item.get("elapsed_ms")
        if case_id is not None and elapsed is not None:
            result.append(
                PerfSample(
                    kind=KIND_WEB,
                    key=f"case::{case_id}",
                    case_id=str(case_id),
                    metrics={"elapsed_ms": float(elapsed)},
                )
            )
    return result


__all__ = ["BaselineManager", "DEFAULT_BASELINE_DIR", "SCHEMA_VERSION", "write_after_gate"]
