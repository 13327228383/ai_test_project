import { useShallow } from "zustand/react/shallow";
import { useLogStore, selectFilteredLogs } from "@/store/logStore";
import { ChevronLeft, ChevronRight } from "lucide-react";
import type { SSEStatus } from "@/hooks/useSSELogStream";

interface StatusBarProps {
  sseStatus?: SSEStatus;
}

function countByLevel(logs: ReturnType<typeof selectFilteredLogs>, level: string) {
  return logs.filter((l) => l.level === level).length;
}

export default function StatusBar({ sseStatus }: StatusBarProps) {
  const filteredLogs = useLogStore(useShallow(selectFilteredLogs));
  const page = useLogStore((s) => s.page);
  const pageSize = useLogStore((s) => s.pageSize);
  const goPrevPage = useLogStore((s) => s.goPrevPage);
  const goNextPage = useLogStore((s) => s.goNextPage);
  const lastUpdated = useLogStore((s) => s.lastUpdated);
  const useMock = useLogStore((s) => s.useMock);

  const total = filteredLogs.length;
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const safePage = Math.min(page, totalPages);
  const startIdx = total === 0 ? 0 : (safePage - 1) * pageSize + 1;
  const endIdx = Math.min(safePage * pageSize, total);
  const infoCount = countByLevel(filteredLogs, "INFO");
  const warnCount = countByLevel(filteredLogs, "WARN");
  const errorCount = countByLevel(filteredLogs, "ERROR");
  const debugCount = countByLevel(filteredLogs, "DEBUG");

  return (
    <div
      className="flex items-center justify-between px-5 py-2 shrink-0 text-xs text-text-tertiary flex-wrap gap-2"
      style={{
        borderTop: "1px solid var(--border-default)",
        background: "var(--bg-surface)",
        minHeight: 34,
      }}
    >
      <div className="flex items-center gap-4">
        <span>
          显示{" "}
          <span style={{ color: "var(--text-secondary)" }}>{startIdx}-{endIdx}</span>
          {" "}/ 共{" "}
          <span style={{ color: "var(--text-secondary)" }}>{total}</span> 条
        </span>
        <span>
          <span style={{ color: "var(--state-info-text)" }}>INFO</span>:{" "}
          <span style={{ color: "var(--text-secondary)" }}>{infoCount}</span>
        </span>
        <span>
          <span style={{ color: "var(--state-warning-text)" }}>WARN</span>:{" "}
          <span style={{ color: "var(--text-secondary)" }}>{warnCount}</span>
        </span>
        <span>
          <span style={{ color: "var(--state-error-text)" }}>ERROR</span>:{" "}
          <span style={{ color: "var(--text-secondary)" }}>{errorCount}</span>
        </span>
        <span>
          <span style={{ color: "var(--text-tertiary)" }}>DEBUG</span>:{" "}
          <span style={{ color: "var(--text-secondary)" }}>{debugCount}</span>
        </span>
      </div>

      <div className="flex items-center gap-4">
        {/* 分页翻页 */}
        <div className="flex items-center gap-2 font-mono">
          <button
            onClick={goPrevPage}
            disabled={safePage <= 1}
            className="flex items-center gap-1 cursor-pointer transition-all"
            style={{
              color: safePage <= 1 ? "var(--text-tertiary)" : "var(--text-secondary)",
              opacity: safePage <= 1 ? 0.5 : 1,
              pointerEvents: safePage <= 1 ? "none" : "auto",
              padding: "1px 6px",
              borderRadius: "var(--radius-xs)",
            }}
          >
            <ChevronLeft size={12} />
            上一页
          </button>
          <span style={{ color: "var(--color-primary)", fontWeight: 500 }}>{safePage}</span>
          <span>/</span>
          <span>{totalPages}</span>
          <button
            onClick={goNextPage}
            disabled={safePage >= totalPages}
            className="flex items-center gap-1 cursor-pointer transition-all"
            style={{
              color: safePage >= totalPages ? "var(--text-tertiary)" : "var(--text-secondary)",
              opacity: safePage >= totalPages ? 0.5 : 1,
              pointerEvents: safePage >= totalPages ? "none" : "auto",
              padding: "1px 6px",
              borderRadius: "var(--radius-xs)",
            }}
          >
            下一页
            <ChevronRight size={12} />
          </button>
        </div>

        <div className="font-mono text-micro hidden sm:block">
          最后更新: {lastUpdated}
        </div>
        {useMock ? (
          <span
            className="flex items-center gap-1 font-mono"
            style={{ color: "var(--text-tertiary)" }}
            title="当前使用内置 Mock 数据，未对接后端"
          >
            <span
              style={{
                width: 6,
                height: 6,
                borderRadius: "50%",
                background: "var(--text-tertiary)",
                display: "inline-block",
              }}
            />
            Mock
          </span>
        ) : sseStatus ? (
          <span
            className="flex items-center gap-1 font-mono"
            style={{
              color: sseStatus.connected
                ? "var(--state-success-text, #4ADE71)"
                : sseStatus.lastError
                  ? "var(--state-error-text, #EF4444)"
                  : "var(--state-warning-text, #F59E0B)",
            }}
            title={
              sseStatus.connected
                ? `SSE 已连接 · 已接收 ${sseStatus.receivedCount} 条`
                : sseStatus.lastError || "SSE 连接中..."
            }
          >
            <span
              style={{
                width: 6,
                height: 6,
                borderRadius: "50%",
                background: sseStatus.connected
                  ? "var(--state-success, #4ADE71)"
                  : sseStatus.lastError
                    ? "var(--state-error, #EF4444)"
                    : "var(--state-warning, #F59E0B)",
                display: "inline-block",
              }}
            />
            {sseStatus.connected
              ? `SSE 已连接 · ${sseStatus.receivedCount}`
              : sseStatus.lastError
                ? "SSE 中断"
                : "SSE 连接中"}
          </span>
        ) : null}
      </div>
    </div>
  );
}
