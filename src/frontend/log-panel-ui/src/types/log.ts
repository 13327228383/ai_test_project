// 日志面板类型定义

export type LogLevel = "INFO" | "WARN" | "ERROR" | "SUCCESS" | "DEBUG";

export type SegmentType = "normal" | "highlight" | "value" | "metric" | "dim";

export interface LogSegment {
  text: string;
  type?: SegmentType;
}

export interface LogEntry {
  id: string;
  timestamp: string; // HH:mm:ss.SSS
  level: LogLevel;
  source: string; // [系统]/[Step1]/[API]...
  segments: LogSegment[];
  step?: 1 | 2 | 3 | 4 | 5; // 归属步骤
  isError?: boolean;
}

export type TabKey =
  | "all"
  | "step1"
  | "step2"
  | "step3"
  | "step4"
  | "step5"
  | "system"
  | "error";

export interface TabDef {
  key: TabKey;
  label: string;
}

export type TimeRangeKey =
  | "10m"
  | "30m"
  | "1h"
  | "run"
  | "all";

export interface TimeRangeDef {
  key: TimeRangeKey;
  label: string;
  minutes?: number; // undefined 表示无限制
}

// 顶部级别过滤下拉的选项
export type LevelFilterOption = "ALL" | LogLevel;
