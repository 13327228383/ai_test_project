import { useState, useRef, useEffect } from "react";
import { useShallow } from "zustand/react/shallow";
import { FileText, Download, Trash2, ChevronDown, PlayCircle } from "lucide-react";
import { useLogStore, selectFilteredLogs } from "@/store/logStore";
import { exportAsTxt, exportAsJson } from "@/utils/export";
import type { LevelFilterOption } from "@/types/log";
import type { RunInfo } from "@/api/logs";

const LEVEL_OPTIONS: { value: LevelFilterOption; label: string }[] = [
  { value: "ALL", label: "全部级别" },
  { value: "INFO", label: "INFO" },
  { value: "WARN", label: "WARN" },
  { value: "ERROR", label: "ERROR" },
  { value: "DEBUG", label: "DEBUG" },
];

export default function TopBar() {
  const clearLogs = useLogStore((s) => s.clearLogs);
  const topbarLevel = useLogStore((s) => s.topbarLevel);
  const setTopbarLevel = useLogStore((s) => s.setTopbarLevel);
  const currentRunId = useLogStore((s) => s.currentRunId);
  const availableRuns = useLogStore((s) => s.availableRuns);
  const setCurrentRunId = useLogStore((s) => s.setCurrentRunId);
  // T04/P1-3：步骤重放顶层开关
  const replayOpen = useLogStore((s) => s.replayOpen);
  const setReplayOpen = useLogStore((s) => s.setReplayOpen);
  const [exportOpen, setExportOpen] = useState(false);
  const [runOpen, setRunOpen] = useState(false);
  const [confirmClear, setConfirmClear] = useState(false);
  const exportRef = useRef<HTMLDivElement>(null);
  const runRef = useRef<HTMLDivElement>(null);

  // 当前可见日志（用于导出）— useShallow 浅比较避免 React 18 无限循环
  const filteredLogs = useLogStore(useShallow(selectFilteredLogs));

  useEffect(() => {
    function onClick(e: MouseEvent) {
      if (exportRef.current && !exportRef.current.contains(e.target as Node)) {
        setExportOpen(false);
      }
      if (runRef.current && !runRef.current.contains(e.target as Node)) {
        setRunOpen(false);
      }
    }
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, []);

  const handleExport = (fmt: "txt" | "json") => {
    if (filteredLogs.length === 0) return;
    if (fmt === "txt") exportAsTxt(filteredLogs);
    else exportAsJson(filteredLogs);
    setExportOpen(false);
  };

  const handleClear = () => {
    if (!confirmClear) {
      setConfirmClear(true);
      setTimeout(() => setConfirmClear(false), 3000);
      return;
    }
    clearLogs();
    setConfirmClear(false);
  };

  const formatRunLabel = (r: RunInfo) => {
    const short = r.run_id.slice(0, 8);
    const time = r.last_ts ? r.last_ts.replace("T", " ").slice(0, 16) : "";
    return `${short}${time ? ` · ${time}` : ""} (${r.log_count})`;
  };

  return (
    <div
      className="flex items-center justify-between px-5 py-3 shrink-0"
      style={{
        borderBottom: "1px solid var(--border-default)",
        background: "var(--bg-surface)",
        minHeight: 48,
      }}
    >
      {/* 面包屑 + run 选择器 */}
      <div className="flex items-center gap-3 text-body font-medium text-text-primary">
        <FileText size={14} style={{ opacity: 0.5 }} />
        <span>日志面板</span>
        <div className="relative" ref={runRef}>
          <button
            onClick={() => setRunOpen(!runOpen)}
            className="flex items-center gap-1 text-xs cursor-pointer font-sans transition-all"
            style={{
              padding: "4px 10px",
              color: "var(--text-secondary)",
              background: "var(--bg-elevated)",
              border: "1px solid var(--border-default)",
              borderRadius: "var(--radius-sm)",
              maxWidth: 260,
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
            }}
            title={currentRunId || "全局日志（最近 200 条）"}
          >
            <span>
              {currentRunId
                ? formatRunLabel(
                    availableRuns.find((r) => r.run_id === currentRunId) ?? {
                        run_id: currentRunId,
                        log_count: 0,
                        first_ts: "",
                        last_ts: "",
                      }
                  )
                : "全局日志"}
            </span>
            <ChevronDown size={10} />
          </button>
          {runOpen && (
            <div
              className="absolute left-0 top-full mt-1 z-50 min-w-[260px] max-h-[360px] overflow-y-auto py-1"
              style={{
                background: "var(--bg-elevated)",
                border: "1px solid var(--border-default)",
                borderRadius: "var(--radius-sm)",
                boxShadow: "var(--surface-shadow-float)",
              }}
            >
              <button
                onClick={() => {
                  setCurrentRunId(null);
                  setRunOpen(false);
                }}
                className={`block w-full text-left px-3 py-1.5 text-xs cursor-pointer hover:bg-white/[0.04] ${
                  currentRunId === null
                    ? "text-primary bg-white/[0.06]"
                    : "text-text-secondary"
                }`}
              >
                全局日志（最近 200 条）
              </button>
              {availableRuns.length === 0 && (
                <div className="px-3 py-1.5 text-xs text-text-tertiary">
                  暂无运行记录
                </div>
              )}
              {availableRuns.map((r) => (
                <button
                  key={r.run_id}
                  onClick={() => {
                    setCurrentRunId(r.run_id);
                    setRunOpen(false);
                  }}
                  className={`block w-full text-left px-3 py-1.5 text-xs cursor-pointer hover:bg-white/[0.04] ${
                    currentRunId === r.run_id
                      ? "text-primary bg-white/[0.06]"
                      : "text-text-secondary"
                  }`}
                >
                  {formatRunLabel(r)}
                </button>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* 操作区 */}
      <div className="flex items-center gap-2">
        {/* T04/P1-3：步骤重放开关（顶层入口，无需先选中日志） */}
        <button
          onClick={() => setReplayOpen(!replayOpen)}
          className="flex items-center gap-1 text-xs cursor-pointer font-sans transition-all"
          style={{
            padding: "4px 12px",
            color: replayOpen ? "var(--text-inverse)" : "var(--text-secondary)",
            background: replayOpen ? "var(--color-primary)" : "transparent",
            border: `1px solid ${replayOpen ? "var(--color-primary)" : "var(--border-default)"}`,
            borderRadius: "var(--radius-sm)",
          }}
          title="打开/关闭步骤重放播放器"
        >
          <PlayCircle size={12} />
          <span className="hidden sm:inline">步骤重放</span>
        </button>

        <button
          onClick={handleClear}
          className="flex items-center gap-1 text-xs cursor-pointer font-sans transition-all"
          style={{
            padding: "4px 12px",
            color: confirmClear ? "var(--text-inverse)" : "var(--state-error-text)",
            background: confirmClear ? "var(--state-error)" : "transparent",
            border: `1px solid ${confirmClear ? "var(--state-error)" : "var(--state-error-border)"}`,
            borderRadius: "var(--radius-sm)",
          }}
        >
          <Trash2 size={12} />
          {confirmClear ? "确认清空?" : "清空日志"}
        </button>

        {/* 导出下拉 */}
        <div className="relative" ref={exportRef}>
          <button
            onClick={() => setExportOpen(!exportOpen)}
            className="flex items-center gap-1 text-xs cursor-pointer font-sans transition-all"
            style={{
              padding: "4px 12px",
              color: "var(--text-secondary)",
              background: "transparent",
              border: "1px solid var(--border-default)",
              borderRadius: "var(--radius-sm)",
            }}
          >
            <Download size={12} />
            <span className="hidden sm:inline">导出</span>
            <ChevronDown size={10} />
          </button>
          {exportOpen && (
            <div
              className="absolute right-0 top-full mt-1 z-50 min-w-[140px] py-1"
              style={{
                background: "var(--bg-elevated)",
                border: "1px solid var(--border-default)",
                borderRadius: "var(--radius-sm)",
                boxShadow: "var(--surface-shadow-float)",
              }}
            >
              <button
                onClick={() => handleExport("txt")}
                className="block w-full text-left px-3 py-1.5 text-xs text-text-secondary hover:text-primary hover:bg-white/[0.04] cursor-pointer"
              >
                导出为 TXT
              </button>
              <button
                onClick={() => handleExport("json")}
                className="block w-full text-left px-3 py-1.5 text-xs text-text-secondary hover:text-primary hover:bg-white/[0.04] cursor-pointer"
              >
                导出为 JSON
              </button>
            </div>
          )}
        </div>

        {/* 级别下拉 */}
        <select
          value={topbarLevel}
          onChange={(e) => setTopbarLevel(e.target.value as LevelFilterOption)}
          className="custom-select cursor-pointer outline-none text-xs font-sans"
          style={{
            height: "var(--control-height-sm)",
            background: "var(--bg-elevated)",
            color: "var(--text-secondary)",
            border: "1px solid var(--border-default)",
            borderRadius: "var(--radius-sm)",
            padding: "0 24px 0 8px",
          }}
        >
          {LEVEL_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
      </div>
    </div>
  );
}
