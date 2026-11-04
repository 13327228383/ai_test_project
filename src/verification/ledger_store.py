# -*- coding: utf-8 -*-
"""验证台账存储层（LedgerStore）。

★ 职责：以 JSONL 格式落盘验证台账，路径 `ledger_dir/<target>.jsonl`，
  支持：append（追加写）+ 全量 reload；按 case_id upsert（同 target 同
  case_id 覆盖最新一条）；按 status / consistent=false / target 筛选查询。

★ 设计约束：
  - 仅用标准库（json + pathlib），无外部 DB 依赖（主理人决策 2）。
  - 每次 append 直接落盘一行，reload 时全量读取并重建内存索引。
  - upsert 语义：同 target+case_id 视为同一条记录的最新版本，覆盖而非新增。

★ 依赖方向：仅依赖标准库 + 本模块 schema。
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Iterator, Optional

from src.verification.schema import LedgerRecord, LedgerStatus, Consistency


class LedgerStore:
    """验证台账 JSONL 存储。

    典型用法::

        store = LedgerStore(Path("src/verification/ledger"))
        store.append(LedgerRecord(target="edu_sut", case_id="P01", ...))
        recs = store.load("edu_sut")
        for r in store.query("edu_sut", status=LedgerStatus.OPEN):
            ...
    """

    def __init__(self, ledger_dir: str | Path) -> None:
        """初始化。

        Args:
            ledger_dir: 台账根目录（JSONL 文件 `<target>.jsonl` 落于此）。
        """
        self.ledger_dir = Path(ledger_dir)
        self.ledger_dir.mkdir(parents=True, exist_ok=True)
        # 单实例写锁（append/upsert 串行化，避免并发写交错）。
        self._lock = threading.RLock()

    # ─────────────────────────────────────────────────────────────────
    # 路径工具
    # ─────────────────────────────────────────────────────────────────
    def _path(self, target: str) -> Path:
        """返回某靶子的 JSONL 文件路径。"""
        safe = target.replace("/", "_").replace("\\", "_")
        return self.ledger_dir / f"{safe}.jsonl"

    # ─────────────────────────────────────────────────────────────────
    # 写入
    # ─────────────────────────────────────────────────────────────────
    def append(self, rec: LedgerRecord) -> None:
        """追加一条记录（直接落盘一行 JSONL）。

        注意：append 不做 upsert 去重，若需「同 case_id 覆盖」请用 upsert。

        Args:
            rec: 待写入的 LedgerRecord。
        """
        with self._lock:
            line = json.dumps(rec.to_dict(), ensure_ascii=False)
            with self._path(rec.target).open("a", encoding="utf-8") as f:
                f.write(line + "\n")

    def upsert(self, rec: LedgerRecord) -> None:
        """按 (target, case_id) upsert 一条记录。

        语义：先全量 load 该 target，找到同 case_id 的最新一条替换之；
        若不存在则追加。全程持锁，保证原子性。

        Args:
            rec: 待写入/覆盖的 LedgerRecord。
        """
        with self._lock:
            recs = self._read_all(rec.target)
            replaced = False
            for i, existing in enumerate(recs):
                if existing.case_id == rec.case_id:
                    recs[i] = rec
                    replaced = True
                    break
            if not replaced:
                recs.append(rec)
            self._write_all(rec.target, recs)

    # ─────────────────────────────────────────────────────────────────
    # 读取
    # ─────────────────────────────────────────────────────────────────
    def load(self, target: str) -> list[LedgerRecord]:
        """全量 reload 某靶子的台账记录。

        Args:
            target: 靶子 key。

        Returns:
            该靶子全部 LedgerRecord 列表（按落盘顺序）。
            文件不存在时返回空列表。
        """
        with self._lock:
            return self._read_all(target)

    def query(
        self,
        target: str,
        status: Optional[LedgerStatus] = None,
        only_inconsistent: bool = False,
    ) -> list[LedgerRecord]:
        """按筛选条件查询。

        Args:
            target: 靶子 key（必填）。
            status: 可选，按状态枚举筛选（如 LedgerStatus.OPEN）。
            only_inconsistent: 为 True 时仅返回 consistent=="false" 的记录。

        Returns:
            满足条件的 LedgerRecord 列表。
        """
        recs = self.load(target)
        result = recs
        if status is not None:
            want = status.value if isinstance(status, LedgerStatus) else str(status)
            result = [r for r in result if r.status == want]
        if only_inconsistent:
            result = [
                r
                for r in result
                if r.consistent == Consistency.FALSE.value
            ]
        return result

    def get(self, target: str, case_id: str) -> Optional[LedgerRecord]:
        """按 (target, case_id) 取最新一条记录（取最后出现的，即最新）。"""
        recs = self.load(target)
        found: Optional[LedgerRecord] = None
        for r in recs:
            if r.case_id == case_id:
                found = r
        return found

    # ─────────────────────────────────────────────────────────────────
    # 内部读写（调用方需持锁）
    # ─────────────────────────────────────────────────────────────────
    def _read_all(self, target: str) -> list[LedgerRecord]:
        """从 JSONL 全量解析（容错：跳过损坏行并记录）。"""
        path = self._path(target)
        if not path.is_file():
            return []
        recs: list[LedgerRecord] = []
        with path.open("r", encoding="utf-8") as f:
            for lineno, raw in enumerate(f, 1):
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    obj = json.loads(raw)
                    recs.append(LedgerRecord.from_dict(obj))
                except (json.JSONDecodeError, ValueError) as e:
                    # 容错：损坏行不阻断整体读取，仅打印告警。
                    print(
                        f"[LedgerStore] 跳过损坏行 {path.name}:{lineno}: {e}"
                    )
        return recs

    def _write_all(self, target: str, recs: list[LedgerRecord]) -> None:
        """全量重写 JSONL（upsert 用）。"""
        path = self._path(target)
        tmp = path.with_suffix(path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            for r in recs:
                f.write(json.dumps(r.to_dict(), ensure_ascii=False) + "\n")
        tmp.replace(path)

    def __iter__(self) -> Iterator[LedgerRecord]:
        """默认迭代所有靶子的全部记录（便于导出）。"""
        for child in sorted(self.ledger_dir.glob("*.jsonl")):
            target = child.stem
            yield from self.load(target)
