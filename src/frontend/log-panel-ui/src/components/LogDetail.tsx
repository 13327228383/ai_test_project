import { useShallow } from "zustand/react/shallow";
import { useLogStore, selectFilteredLogs } from "@/store/logStore";
import { PlayCircle, X } from "lucide-react";
import type { LogLevel } from "@/types/log";

const LEVEL_COLORS: Record<LogLevel, string> = {
  INFO: "var(--state-info-text)",
  WARN: "var(--state-warning-text)",
  ERROR: "var(--state-error-text)",
  SUCCESS: "var(--state-success-text)",
  DEBUG: "var(--text-tertiary)",
};

function MetaRow({ label, value, valueColor }: { label: string; value: string; valueColor?: string }) {
  return (
    <div className="flex items-center justify-between py-1.5" style={{ borderBottom: "1px solid var(--border-default)" }}>
      <span className="text-xs text-text-tertiary">{label}</span>
      <span className="text-xs font-mono" style={{ color: valueColor || "var(--text-secondary)" }}>{value}</span>
    </div>
  );
}

export default function LogDetail() {
  const selectedLogId = useLogStore((s) => s.selectedLogId);
  const detailPanelOpen = useLogStore((s) => s.detailPanelOpen);
  const detailPanelWidth = useLogStore((s) => s.detailPanelWidth);
  const setDetailPanelOpen = useLogStore((s) => s.setDetailPanelOpen);
  const setDetailPanelWidth = useLogStore((s) => s.setDetailPanelWidth);
  const setSelectedLogId = useLogStore((s) => s.selectLog);
  // T04/P1-3：从详情面板直接进入步骤重放，并把重放游标对齐到当前这条日志
  const setReplayOpen = useLogStore((s) => s.setReplayOpen);
  const setReplayIndex = useLogStore((s) => s.setReplayIndex);

  const filteredLogs = useLogStore(useShallow(selectFilteredLogs));
  const entry = filteredLogs.find((l) => l.id === selectedLogId) || null;

  const onStartReplay = () => {
    if (!entry) return;
    const idx = filteredLogs.findIndex((l) => l.id === entry.id);
    setReplayOpen(true);
    if (idx >= 0) setReplayIndex(idx, entry.id);
  };

  if (!detailPanelOpen || !entry) return null;

  const fullText = entry.segments.map((s) => s.text).join("");

  // 拖拽调整宽度
  let dragStartX = 0;
  let dragStartW = 0;
  const onResizeDown = (e: React.MouseEvent) => {
    dragStartX = e.clientX;
    dragStartW = detailPanelWidth;
    const onMove = (ev: MouseEvent) => {
      const delta = dragStartX - ev.clientX;
      const newW = Math.max(240, Math.min(600, dragStartW + delta));
      setDetailPanelWidth(newW);
    };
    const onUp = () => {
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
    };
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
  };

  return (
    <div
      className="relative flex flex-col h-full shrink-0 border-l overflow-hidden"
      style={{
        width: detailPanelWidth,
        background: "var(--bg-surface)",
        borderLeft: "1px solid var(--border-default)",
      }}
    >
      {/* 拖拽条 */}
      <div
        className="absolute top-0 h-full cursor-col-resize hover:bg-white/5"
        style={{ left: -4, width: 8, zIndex: 10 }}
        onMouseDown={onResizeDown}
      />

      {/* 头部 */}
      <div
        className="flex items-center justify-between px-4 py-3 shrink-0"
        style={{ borderBottom: "1px solid var(--border-default)" }}
      >
        <div className="flex items-center gap-2">
          <span className="text-small font-semibold text-text-primary">日志详情</span>
          <span className="text-micro text-text-tertiary font-mono">{entry.timestamp}</span>
          <span
            className="text-micro font-mono px-1.5 py-0.5 rounded"
            style={{
              color: LEVEL_COLORS[entry.level],
              background: `${LEVEL_COLORS[entry.level]}22`,
            }}
          >
            {entry.level}
          </span>
          <span className="text-micro text-text-tertiary">·</span>
          <span className="text-micro text-text-tertiary">{entry.source}</span>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <button
            onClick={onStartReplay}
            className="flex items-center gap-1 px-1.5 py-0.5 rounded text-micro cursor-pointer transition-colors"
            style={{
              color: "var(--color-primary)",
              border: "1px solid var(--border-default)",
            }}
            title="从这条日志开始步骤重放"
          >
            <PlayCircle size={12} />
            步骤重放
          </button>
          <button
            onClick={() => {
              setDetailPanelOpen(false);
              setSelectedLogId(null);
            }}
            className="text-text-tertiary hover:text-text-primary cursor-pointer transition-colors"
            title="关闭详情"
          >
            <X size={14} />
          </button>
        </div>
      </div>

      {/* 内容区 */}
      <div className="flex-1 overflow-y-auto">
        {/* 完整消息 */}
        <div className="px-4 pt-3 pb-2">
          <div className="text-micro font-medium text-text-tertiary mb-2">完整消息</div>
          <div
            className="text-xs font-mono p-3 rounded leading-relaxed break-all whitespace-pre-wrap"
            style={{
              background: "var(--bg-base)",
              border: "1px solid var(--border-default)",
              color: "var(--text-secondary)",
            }}
          >
            {fullText}
          </div>
        </div>

        {/* 元数据 */}
        <div className="px-4 pt-3 pb-2">
          <div className="text-micro font-medium text-text-tertiary mb-2">元数据</div>
          <MetaRow label="时间戳" value={entry.timestamp} />
          <MetaRow label="级别" value={entry.level} valueColor={LEVEL_COLORS[entry.level]} />
          <MetaRow label="来源" value={entry.source} />
          <MetaRow label="步骤" value={entry.step ? `step${entry.step}` : "system"} />
          <MetaRow label="长度" value={`${fullText.length} 字符`} />
        </div>
      </div>
    </div>
  );
}
