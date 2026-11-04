import { useEffect, useMemo, useRef } from "react";
import { useShallow } from "zustand/react/shallow";
import { useLogStore, selectFilteredLogs } from "@/store/logStore";
import LogEntryRow from "@/components/LogEntryRow";

export default function LogStream() {
  // ★ selectFilteredLogs 每次返回新数组，必须用 useShallow 浅比较，否则 React 18 无限循环
  const filteredLogs = useLogStore(useShallow(selectFilteredLogs));
  const page = useLogStore((s) => s.page);
  const pageSize = useLogStore((s) => s.pageSize);
  const setPage = useLogStore((s) => s.setPage);
  const autoScroll = useLogStore((s) => s.autoScroll);

  const total = filteredLogs.length;
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const safePage = Math.min(page, totalPages);
  const startIdx = (safePage - 1) * pageSize;
  const endIdx = Math.min(startIdx + pageSize, total);
  const pageLogs = useMemo(
    () => filteredLogs.slice(startIdx, endIdx),
    [filteredLogs, startIdx, endIdx],
  );

  const scrollRef = useRef<HTMLDivElement>(null);
  const prevTotalRef = useRef(total);
  const newIdSet = useMemo(() => {
    const prevTotal = prevTotalRef.current;
    if (total > prevTotal) {
      // 新增的日志 id（仅在最后一页时高亮）
      const newCount = total - prevTotal;
      const newIds = new Set<string>();
      for (let i = total - newCount; i < total; i++) {
        newIds.add(filteredLogs[i].id);
      }
      return newIds;
    }
    return new Set<string>();
  }, [total, filteredLogs]);

  // 自动滚动：日志增长时跳到最后一页并滚到底
  useEffect(() => {
    if (autoScroll && total > prevTotalRef.current) {
      setPage(totalPages);
    }
    prevTotalRef.current = total;
  }, [total, autoScroll, totalPages, setPage]);

  // 滚动到底部
  useEffect(() => {
    if (!autoScroll) return;
    const el = scrollRef.current;
    if (el) {
      el.scrollTop = el.scrollHeight;
    }
  }, [pageLogs, autoScroll, safePage]);

  if (total === 0) {
    return (
      <div
        ref={scrollRef}
        className="flex-1 flex items-center justify-center overflow-y-auto"
        style={{ background: "var(--bg-log)" }}
      >
        <div className="text-center text-text-tertiary font-mono text-xs">
          <div className="mb-2 text-2xl" style={{ color: "var(--color-primary)", opacity: 0.5 }}>
            ∅
          </div>
          <div>暂无日志</div>
          <div className="mt-1 text-micro">等待流水线推送<span className="blink-cursor" /></div>
        </div>
      </div>
    );
  }

  return (
    <div
      ref={scrollRef}
      className="flex-1 overflow-y-auto overflow-x-hidden"
      style={{ background: "var(--bg-log)" }}
    >
      {pageLogs.map((entry) => (
        <LogEntryRow key={entry.id} entry={entry} isNew={newIdSet.has(entry.id)} />
      ))}
      {autoScroll && safePage === totalPages && (
        <div
          className="px-5 py-1.5 font-mono text-micro text-text-tertiary flex items-center gap-2"
          style={{ background: "var(--bg-log)" }}
        >
          <span
            className="pulse-dot"
            style={{
              display: "inline-block",
              width: 6,
              height: 6,
              borderRadius: "9999px",
              background: "var(--color-primary)",
            }}
          />
          实时跟随中
        </div>
      )}
    </div>
  );
}
