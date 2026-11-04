# -*- coding: utf-8 -*-
"""性能采集暂存 — step4 采集 → step5 消费的跨阶段传递通道。

设计要点：
  - **零 DDL**：不建表、不改库，进程内单例 + jsonl 兜底。
  - **run_id 隔离**：不同运行互不干扰，``drain`` 取走即清空。
  - **双写兜底**：``put`` 同时写内存与 ``data/perf_runtime/<run_id>.jsonl``；
    ``drain`` 内存优先，内存为空则回读 jsonl（覆盖 step4/step5 跨进程场景）。
  - **线程安全**：``threading.RLock`` 保护全部读写。
  - **对外不 raise**：任何 IO 异常内部吞掉并 warning。

[已复核-DeepSeek-V3 2026-08-16]
"""

from __future__ import annotations
from src.core.guarded import guard

import json
import logging
import os
import re
import threading

from src.extensions.perf_baseline.schemas import PerfSample

_log = logging.getLogger(__name__)

#: 仓库根目录（src/extensions/perf_baseline/context.py → 上溯 3 层）
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))

#: jsonl 兜底目录
DEFAULT_SPILL_DIR = os.path.join(_REPO_ROOT, "data", "perf_runtime")

#: 单个 run_id 内存中保留的样本上限（防长跑爆内存；超限丢弃最旧的）
MAX_SAMPLES_PER_RUN = 5000

_RE_UNSAFE = re.compile(r"[^0-9A-Za-z_\-.]")


def _safe_run_id(run_id: str) -> str:
    """run_id → 可安全用作文件名的串。空值回落 ``default``。"""
    cleaned = _RE_UNSAFE.sub("_", (run_id or "").strip())
    return cleaned or "default"


class PerfContext:
    """采集样本的进程内暂存（内存 + jsonl 双写，按 run_id 隔离）。"""

    def __init__(self, spill_dir: str | None = None, spill_enabled: bool = True) -> None:
        """
        参数：
            spill_dir: jsonl 兜底目录，缺省 ``data/perf_runtime``。
            spill_enabled: 是否开启 jsonl 兜底。同进程场景可关掉省 IO。
        """
        self._store: dict[str, list[PerfSample]] = {}
        self._lock = threading.RLock()
        self._spill_dir: str = spill_dir or DEFAULT_SPILL_DIR
        self._spill_enabled: bool = bool(spill_enabled)

    # ── 配置 ───────────────────────────────────────────────────────────
    def configure(self, spill_enabled: bool | None = None, spill_dir: str | None = None) -> None:
        """运行时调整兜底行为（供插件按 yaml 配置调用）。"""
        with self._lock:
            if spill_enabled is not None:
                self._spill_enabled = bool(spill_enabled)
            if spill_dir:
                self._spill_dir = spill_dir

    def _spill_path(self, run_id: str) -> str:
        return os.path.join(self._spill_dir, f"{_safe_run_id(run_id)}.jsonl")

    # ── 写入 ───────────────────────────────────────────────────────────
    def put(self, run_id: str, sample: PerfSample) -> None:
        """写入一个样本（内存 + jsonl 双写）。异常内部吞掉。"""
        if sample is None or not isinstance(sample, PerfSample):
            _log.warning("[perf_gate] PerfContext.put 入参非 PerfSample，已忽略")
            return
        key = _safe_run_id(run_id)
        with self._lock:
            bucket = self._store.setdefault(key, [])
            bucket.append(sample)
            if len(bucket) > MAX_SAMPLES_PER_RUN:
                dropped = len(bucket) - MAX_SAMPLES_PER_RUN
                del bucket[:dropped]
                _log.warning("[perf_gate] run=%s 样本超上限，丢弃最旧 %d 条", key, dropped)
        self._spill(key, sample)

    def put_many(self, run_id: str, samples: list[PerfSample]) -> None:
        """批量写入（api 采集一次产出多条时使用）。"""
        for sample in samples or []:
            self.put(run_id, sample)

    def _spill(self, run_id: str, sample: PerfSample) -> None:
        """追加落盘到 ``<spill_dir>/<run_id>.jsonl``。"""
        if not self._spill_enabled:
            return
        with guard("_spill: os.makedirs(self._spill_dir, exist_o", log=_log, level=logging.WARNING):
            os.makedirs(self._spill_dir, exist_ok=True)
            line = json.dumps(sample.to_dict(), ensure_ascii=False)
            with open(self._spill_path(run_id), "a", encoding="utf-8") as fp:
                fp.write(line + "\n")

    # ── 读取 ───────────────────────────────────────────────────────────
    def _load_spill(self, run_id: str) -> list[PerfSample]:
        """回读 jsonl 兜底文件。文件不存在或损坏时返回空列表。"""
        path = self._spill_path(run_id)
        if not os.path.isfile(path):
            return []
        samples: list[PerfSample] = []
        broken = 0
        last_error = ""
        with guard("_load_spill: with open(path, 'r', encoding='utf-8", log=_log, level=logging.WARNING):
            with open(path, "r", encoding="utf-8") as fp:
                for raw in fp:
                    line = raw.strip()
                    if not line:
                        continue
                    try:
                        samples.append(PerfSample.from_dict(json.loads(line)))
                    except Exception as exc:  # noqa: BLE001 — 单行损坏不影响其余
                        # 逐行 warning 会刷屏，改为累计后汇总上报一次
                        broken += 1
                        last_error = str(exc)[:100]
        if broken:
            _log.warning(
                "[perf_gate] 兜底文件 %s 有 %d 行损坏已跳过（末条错误: %s）",
                path,
                broken,
                last_error,
            )
        return samples

    def _remove_spill(self, run_id: str) -> None:
        """删除 jsonl 兜底文件（drain 后清理）。"""
        if not self._spill_enabled:
            return
        with guard("_remove_spill: path = self._spill_path(run_id)", log=_log, level=logging.WARNING):
            path = self._spill_path(run_id)
            if os.path.isfile(path):
                os.remove(path)

    def peek(self, run_id: str) -> list[PerfSample]:
        """只读查看（不清空）。内存优先，空则回读 jsonl。"""
        key = _safe_run_id(run_id)
        with self._lock:
            in_memory = list(self._store.get(key, []))
        if in_memory:
            return in_memory
        return self._load_spill(key)

    def drain(self, run_id: str) -> list[PerfSample]:
        """取走并清空指定 run 的全部样本（内存优先，空则回读 jsonl）。"""
        key = _safe_run_id(run_id)
        with self._lock:
            in_memory = self._store.pop(key, [])
        samples = list(in_memory) if in_memory else self._load_spill(key)
        self._remove_spill(key)
        _log.info("[perf_gate] drain run=%s → %d 条样本", key, len(samples))
        return samples

    def clear(self, run_id: str) -> None:
        """清空指定 run（内存 + jsonl），不返回数据。"""
        key = _safe_run_id(run_id)
        with self._lock:
            self._store.pop(key, None)
        self._remove_spill(key)

    def clear_all(self) -> None:
        """清空全部内存缓存（不动 jsonl）。主要供单测使用。"""
        with self._lock:
            self._store.clear()

    def stats(self) -> dict[str, int]:
        """当前各 run 的内存样本计数（诊断用）。"""
        with self._lock:
            return {run: len(items) for run, items in self._store.items()}


#: 模块级单例 —— step4 采集与 step5 消费共用同一实例
perf_context = PerfContext()

__all__ = ["PerfContext", "perf_context", "DEFAULT_SPILL_DIR", "MAX_SAMPLES_PER_RUN"]
