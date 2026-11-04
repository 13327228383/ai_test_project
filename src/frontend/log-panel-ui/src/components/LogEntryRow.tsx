import { useLogStore } from "@/store/logStore";
import type { LogEntry, LogLevel, SegmentType } from "@/types/log";

const LEVEL_BADGE_STYLES: Record<LogLevel, React.CSSProperties> = {
  INFO: { color: "var(--state-info-text)", background: "var(--state-info-soft)" },
  WARN: { color: "var(--state-warning-text)", background: "var(--state-warning-soft)" },
  ERROR: { color: "var(--state-error-text)", background: "var(--state-error-soft)" },
  SUCCESS: { color: "var(--state-success-text)", background: "var(--state-success-soft)" },
  DEBUG: { color: "var(--text-tertiary)", background: "rgba(100, 116, 139, 0.1)" },
};

function segmentColor(type: SegmentType | undefined): string {
  switch (type) {
    case "highlight":
      return "var(--text-primary)";
    case "value":
      return "var(--state-info-text)";
    case "metric":
      return "var(--state-warning-text)";
    case "dim":
      return "var(--text-tertiary)";
    default:
      return "inherit";
  }
}

export default function LogEntryRow({ entry, isNew }: { entry: LogEntry; isNew?: boolean }) {
  const isError = entry.isError || entry.level === "ERROR";
  const selectedLogId = useLogStore((s) => s.selectedLogId);
  const selectLog = useLogStore((s) => s.selectLog);
  const isSelected = selectedLogId === entry.id;
  // T04/P1-3：重放高亮。只订阅一个字符串，逐行比较开销可忽略
  const replayActiveId = useLogStore((s) => s.replayActiveId);
  const isReplayActive = replayActiveId === entry.id;

  // 消息基色
  let msgColor = "var(--text-secondary)";
  if (isError) msgColor = "var(--state-error-text)";
  else if (entry.level === "WARN") msgColor = "var(--state-warning-text)";
  else if (entry.level === "SUCCESS") msgColor = "var(--state-success-text)";
  else if (entry.level === "DEBUG") msgColor = "var(--text-tertiary)";

  return (
    <div
      data-log-id={entry.id}
      data-replay-active={isReplayActive ? "1" : undefined}
      className={`log-entry flex items-start font-mono leading-tight transition-colors cursor-pointer ${
        isSelected ? "bg-white/[0.06]" : "hover:bg-white/[0.03]"
      } ${isNew ? "log-enter" : ""}`}
      onClick={() => selectLog(isSelected ? null : entry.id)}
      style={{
        padding: "3px 20px",
        borderBottom: "1px solid rgba(148, 163, 184, 0.06)",
        fontSize: "var(--fs-xs)",
        // 重放高亮优先级最高：左侧主色描边 + 主色薄底，视觉上盖过选中态
        borderLeft: isReplayActive
          ? "2px solid var(--color-primary)"
          : isError
            ? "2px solid var(--state-error)"
            : isSelected
              ? "2px solid var(--color-primary)"
              : "none",
        paddingLeft: isError || isReplayActive ? "calc(20px - 2px)" : "20px",
        background: isReplayActive ? "var(--state-info-soft)" : undefined,
        boxShadow: isReplayActive ? "inset 0 0 0 1px var(--color-primary)" : undefined,
      }}
    >
      <span
        className="shrink-0 select-text"
        style={{ color: "var(--text-tertiary)", width: 110 }}
      >
        [{entry.timestamp}]
      </span>
      <span
        className="shrink-0 flex justify-end"
        style={{ width: 58, marginRight: "var(--space-2)" }}
      >
        <span
          className="font-mono font-medium"
          style={{
            fontSize: "var(--fs-micro)",
            padding: "0px 5px",
            borderRadius: "var(--radius-xs)",
            letterSpacing: "0.02em",
            ...LEVEL_BADGE_STYLES[entry.level],
          }}
        >
          {entry.level}
        </span>
      </span>
      <span
        className="shrink-0"
        style={{
          color: "var(--color-primary)",
          marginRight: "var(--space-2)",
          minWidth: 62,
        }}
      >
        [{entry.source}]
      </span>
      {/*
        ★ 根因(2026-08-31)：消息列是 flex 容器的最后一个子项，原先仅 break-all（只管换行、不撑宽），
          没有 flex:1 —— 三者固定列（时间/级别/来源）占据后，消息列按内容宽度自排，右侧留下约 25-30% 空白。
          flex-1 让消息列吸收剩余宽度自动填满；min-w-0 允许其收缩并配合 break-all 在窄容器内断行，
          避免内容把整行撑出容器。此为通用自适应，适用于任意长度日志。
      */}
      <span className="flex-1 min-w-0 break-all" style={{ color: msgColor }}>
        {entry.segments.map((seg, i) => (
          <span key={i} style={{ color: seg.type ? segmentColor(seg.type) : undefined }}>
            {seg.text}
          </span>
        ))}
      </span>
    </div>
  );
}
