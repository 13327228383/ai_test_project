import { Search } from "lucide-react";
import { useLogStore, ALL_LEVELS } from "@/store/logStore";
import type { LogLevel, TimeRangeDef } from "@/types/log";

const LEVEL_TOGGLE: { level: LogLevel; cls: string }[] = [
  { level: "INFO", cls: "info" },
  { level: "WARN", cls: "warn" },
  { level: "ERROR", cls: "error" },
  { level: "DEBUG", cls: "debug" },
];

const TIME_RANGES: TimeRangeDef[] = [
  { key: "10m", label: "最近 10 分钟", minutes: 10 },
  { key: "30m", label: "最近 30 分钟", minutes: 30 },
  { key: "1h", label: "最近 1 小时", minutes: 60 },
  { key: "run", label: "本次执行" },
  { key: "all", label: "全部" },
];

const LEVEL_TOGGLE_STYLES: Record<string, { active: React.CSSProperties; inactive: React.CSSProperties }> = {
  info: {
    active: { color: "var(--state-info-text)", background: "var(--state-info-soft)", border: "1px solid var(--state-info-border)" },
    inactive: { color: "var(--text-tertiary)", background: "transparent", border: "1px solid var(--border-default)" },
  },
  warn: {
    active: { color: "var(--state-warning-text)", background: "var(--state-warning-soft)", border: "1px solid var(--state-warning-border)" },
    inactive: { color: "var(--text-tertiary)", background: "transparent", border: "1px solid var(--border-default)" },
  },
  error: {
    active: { color: "var(--state-error-text)", background: "var(--state-error-soft)", border: "1px solid var(--state-error-border)" },
    inactive: { color: "var(--text-tertiary)", background: "transparent", border: "1px solid var(--border-default)" },
  },
  debug: {
    active: { color: "var(--text-tertiary)", background: "rgba(100, 116, 139, 0.12)", border: "1px solid rgba(100, 116, 139, 0.3)" },
    inactive: { color: "var(--text-tertiary)", background: "transparent", border: "1px solid var(--border-default)" },
  },
};

export default function SearchBar() {
  const keyword = useLogStore((s) => s.keyword);
  const setKeyword = useLogStore((s) => s.setKeyword);
  const enabledLevels = useLogStore((s) => s.enabledLevels);
  const toggleLevel = useLogStore((s) => s.toggleLevel);
  const timeRange = useLogStore((s) => s.timeRange);
  const setTimeRange = useLogStore((s) => s.setTimeRange);
  const autoScroll = useLogStore((s) => s.autoScroll);
  const toggleAutoScroll = useLogStore((s) => s.toggleAutoScroll);

  // 启用集合为空时表示全部启用，此时所有徽标点亮
  const allOn = enabledLevels.length === 0;

  return (
    <div
      className="flex items-center gap-3 px-5 py-3 shrink-0 flex-wrap"
      style={{
        borderBottom: "1px solid var(--border-default)",
        background: "var(--bg-surface)",
      }}
    >
      {/* 搜索输入 */}
      <div className="relative flex-1" style={{ maxWidth: 360, minWidth: 180 }}>
        <Search
          size={12}
          className="absolute left-3 top-1/2 -translate-y-1/2 text-text-tertiary pointer-events-none"
        />
        <input
          type="text"
          value={keyword}
          onChange={(e) => setKeyword(e.target.value)}
          placeholder="搜索日志..."
          className="w-full outline-none transition-all font-mono text-xs text-text-primary"
          style={{
            height: "var(--control-height)",
            background: "var(--bg-base)",
            border: "1px solid var(--border-default)",
            borderRadius: "var(--radius-sm)",
            padding: "0 12px 0 28px",
          }}
        />
      </div>

      {/* 级别快切 */}
      <div className="flex items-center gap-1">
        {LEVEL_TOGGLE.map(({ level, cls }) => {
          const active = allOn || enabledLevels.includes(level);
          const styleKey = cls;
          const st = active
            ? LEVEL_TOGGLE_STYLES[styleKey].active
            : LEVEL_TOGGLE_STYLES[styleKey].inactive;
          return (
            <span
              key={level}
              onClick={() => toggleLevel(level)}
              className="font-mono font-medium cursor-pointer select-none transition-all"
              style={{
                fontSize: "var(--fs-micro)",
                padding: "2px 8px",
                borderRadius: "var(--radius-sm)",
                border: active ? "1px solid transparent" : "1px solid var(--border-default)",
                ...st,
              }}
            >
              {level}
            </span>
          );
        })}
      </div>

      <div className="flex-1" />

      {/* 时间范围 */}
      <select
        value={timeRange}
        onChange={(e) => setTimeRange(e.target.value as typeof timeRange)}
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
        {TIME_RANGES.map((t) => (
          <option key={t.key} value={t.key}>
            {t.label}
          </option>
        ))}
      </select>

      {/* 自动滚动开关 */}
      <div
        className="flex items-center gap-1 text-xs text-text-secondary cursor-pointer select-none"
        onClick={toggleAutoScroll}
      >
        <div
          className="relative transition-all"
          style={{
            width: 32,
            height: 18,
            background: autoScroll ? "var(--color-primary-soft)" : "var(--bg-elevated)",
            border: `1px solid ${autoScroll ? "var(--color-primary-border)" : "var(--border-default)"}`,
            borderRadius: "var(--radius-full)",
          }}
        >
          <div
            className="absolute top-0.5 transition-all"
            style={{
              width: 12,
              height: 12,
              left: autoScroll ? 16 : 2,
              borderRadius: "var(--radius-full)",
              background: autoScroll ? "var(--color-primary)" : "var(--text-tertiary)",
            }}
          />
        </div>
        <span className="hidden sm:inline">自动滚动</span>
      </div>
    </div>
  );
}
