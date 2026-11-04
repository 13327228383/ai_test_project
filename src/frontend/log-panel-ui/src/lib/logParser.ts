// 日志解析器：把后端纯文本日志解析为结构化 LogEntry
//
// 后端 SSE 消息格式：
//   { "log": "<纯文本>", "run_id": "...", "priority": "high|normal|low" }
//   { "heartbeat": true }
//   { "health_summary": {...} }
//
// 后端历史接口 /api/log-history 返回：
//   [{ "ts": "HH:MM:SS", "text": "<纯文本>" }, ...]
//
// 本解析器职责：
//   1. 剥离 ANSI 终端颜色码
//   2. 识别 level（INFO/WARN/ERROR/SUCCESS/DEBUG）
//   3. 识别 source（Step1-5/系统/API/AI/Token/RAG/DB/Playwright）
//   4. 把消息切分为带 type 的 segments（highlight/value/metric/dim）
//   5. 过滤 Werkzeug HTTP 访问日志噪音

import type { LogEntry, LogLevel, LogSegment, SegmentType } from "@/types/log";

// ── ANSI 转义码正则（终端颜色码，如 \x1b[38;5;148m）──
const ANSI_RE = /\x1b\[[0-9;]*[a-zA-Z]/g;

export function stripAnsi(text: string): string {
  return text.replace(ANSI_RE, "");
}

// ── Werkzeug HTTP 访问日志正则（过滤噪音）──
const WERKZEUG_RE =
  /^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\s+-\s+-\s+\[.*\]\s+"(GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS)\s+\/\S*\s+HTTP\/[\d.]+"\s+\d{3}/;

export function isWerkzeugAccessLog(text: string): boolean {
  return WERKZEUG_RE.test(text.trim());
}

// ── Level 识别（按优先级，参考 app.js colorForLine）──
function detectLevel(text: string): { level: LogLevel; isError: boolean } {
  const lo = text.toLowerCase();
  // ERROR 优先
  if (
    lo.includes("traceback") ||
    lo.includes("✗") ||
    /\berror\b/.test(lo) ||
    lo.includes("异常") ||
    lo.includes("失败") ||
    lo.includes("fail") ||
    lo.includes("critical")
  ) {
    return { level: "ERROR", isError: true };
  }
  // SUCCESS
  if (
    lo.includes("✓") ||
    /\bpass(ed)?\b/.test(lo) ||
    /\bok\b/.test(lo) ||
    lo.includes("成功") ||
    lo.includes("通过") ||
    lo.includes("完成")
  ) {
    return { level: "SUCCESS", isError: false };
  }
  // WARN
  if (
    lo.includes("warn") ||
    lo.includes("警告") ||
    lo.includes("跳过") ||
    lo.includes("⚠")
  ) {
    return { level: "WARN", isError: false };
  }
  // DEBUG
  if (lo.includes("[debug]") || lo.includes("debug:") || /\bdebug\b/.test(lo)) {
    return { level: "DEBUG", isError: false };
  }
  return { level: "INFO", isError: false };
}

// ── Source + step 识别 ──
// 匹配 [Step1]-[Step5] 或 [系统]/[API]/[AI]/[Token]/[RAG]/[DB]/[Playwright] 前缀
const SOURCE_BRACKET_RE = /^\s*\[(Step([1-5])|系统|API|AI|Token|RAG|DB|Playwright|Confluence|Figma|YApi|MySQL)\]\s*/;

interface SourceInfo {
  source: string;
  step?: 1 | 2 | 3 | 4 | 5;
  message: string; // 去掉 source 前缀后的消息
}

function detectSource(rawMessage: string): SourceInfo {
  const m = rawMessage.match(SOURCE_BRACKET_RE);
  if (m) {
    const tag = m[1];
    const stepNum = m[2] ? (Number(m[2]) as 1 | 2 | 3 | 4 | 5) : undefined;
    return {
      source: tag,
      step: stepNum,
      message: rawMessage.slice(m[0].length),
    };
  }
  // 从内容推断
  const lo = rawMessage.toLowerCase();
  if (/step\s*1|数据拉取|fetcher|confluence|figma|yapi/.test(lo)) {
    return { source: "Step1", step: 1, message: rawMessage };
  }
  if (/step\s*2|需求拆解|防幻觉|置信度|cove/.test(lo)) {
    return { source: "Step2", step: 2, message: rawMessage };
  }
  if (/step\s*3|用例建模|建模|case_gen|json解析/.test(lo)) {
    return { source: "Step3", step: 3, message: rawMessage };
  }
  if (/step\s*4|检查执行|playwright|check-/.test(lo)) {
    return { source: "Step4", step: 4, message: rawMessage };
  }
  if (/step\s*5|报告生成|报告完成/.test(lo)) {
    return { source: "Step5", step: 5, message: rawMessage };
  }
  if (/token|预算|cost=/.test(lo)) {
    return { source: "Token", message: rawMessage };
  }
  if (/\bmodel=|ai推理|ai调用/.test(lo)) {
    return { source: "AI", message: rawMessage };
  }
  if (/mysql|db schema|sql/.test(lo)) {
    return { source: "DB", message: rawMessage };
  }
  if (/^get\s+http|^post\s+http|→\s*\d{3}/.test(lo)) {
    return { source: "API", message: rawMessage };
  }
  return { source: "系统", message: rawMessage };
}

// ── Segment 切分 ──
// 按以下模式依次匹配，命中即标注 type，其余为 normal
// 顺序很重要：先匹配长的、具体的模式
interface TokenPattern {
  type: SegmentType;
  re: RegExp;
}

const TOKEN_PATTERNS: TokenPattern[] = [
  // REQ-XXX 需求编号 → value
  { type: "value", re: /REQ-\d+/g },
  // CHECK-XXX 检查点编号 → value
  { type: "value", re: /CHECK-\d+/g },
  // HTTP 状态码（→ 200 / → 404）→ value
  { type: "value", re: /→\s*\d{3}\b/g },
  // 货币/成本（0.2482V, 1.1050V）→ metric
  { type: "metric", re: /\d+\.\d+V\b/g },
  // 百分比（94.4%, 85%/100%）→ metric
  { type: "metric", re: /\d+(\.\d+)?%/g },
  // 耗时（238ms, 1.7s, 2m 05s）→ metric
  { type: "metric", re: /\d+(\.\d+)?(ms|s)\b|\d+m\s+\d+s\b/g },
  // 模型名（deepseek-v4-pro, gpt-4 等）→ value
  {
    type: "value",
    re: /\b(deepseek-[a-z0-9-]+|gpt-[a-z0-9-]+|claude-[a-z0-9-]+|qwen[a-z0-9-]*)\b/gi,
  },
  // 带千分位的数字（12,847 / 220,903）→ value
  { type: "value", re: /\b\d{1,3}(,\d{3})+\b/g },
  // 纯数字 → value
  { type: "value", re: /\b\d+\b/g },
  // 括号注记 (238ms) / (1.7s) / (142ms) → dim
  { type: "dim", re: /\([^)]*\)/g },
  // 引号字符串 "submit" / 'expected '}' → highlight
  { type: "highlight", re: /"[^"]*"|'[^']*'/g },
  // 关键错误标记 FAIL / ERROR / expected '}' / invalid escape → highlight
  {
    type: "highlight",
    re: /\bFAIL\b|\bERROR\b|expected\s+['"][^'"]*['"]|invalid\s+escape\s+sequence[^,]*/gi,
  },
];

// 把纯文本切分为带 type 的 segments
export function tokenizeMessage(text: string): LogSegment[] {
  if (!text) return [];
  // 收集所有匹配区间
  type Range = { start: number; end: number; type: SegmentType };
  const ranges: Range[] = [];

  for (const { type, re } of TOKEN_PATTERNS) {
    re.lastIndex = 0;
    let m: RegExpExecArray | null;
    while ((m = re.exec(text)) !== null) {
      const start = m.index;
      const end = start + m[0].length;
      // 跳过已被更高优先级覆盖的区间
      if (ranges.some((r) => start >= r.start && end <= r.end)) continue;
      ranges.push({ start, end, type });
    }
  }
  // 按起始位置排序
  ranges.sort((a, b) => a.start - b.start);

  // 合并相邻同类型区间 + 填充 normal
  const segs: LogSegment[] = [];
  let cursor = 0;
  for (const r of ranges) {
    // 跳过重叠（保留先出现的）
    if (r.start < cursor) continue;
    if (r.start > cursor) {
      segs.push({ text: text.slice(cursor, r.start) });
    }
    segs.push({ text: text.slice(r.start, r.end), type: r.type });
    cursor = r.end;
  }
  if (cursor < text.length) {
    segs.push({ text: text.slice(cursor) });
  }
  return segs;
}

// ── 主入口：解析单条日志 ──
let _idCounter = 0;
function nextId(): string {
  _idCounter += 1;
  return `real-${_idCounter}`;
}

export interface ParseInput {
  text: string; // 纯文本日志（可能是 SSE 的 log 字段，或历史的 text 字段）
  ts?: string; // 时间戳 HH:MM:SS 或 HH:MM:SS.SSS，缺失则取当前时间
  runId?: string;
  step?: string; // 后端已解析的步骤，如 "step1" / "system"
  level?: string; // 后端已解析的级别，如 "INFO" / "WARN" / "ERROR"
}

function formatTs(d: Date): string {
  const pad = (n: number, l = 2) => String(n).padStart(l, "0");
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}.${pad(
    d.getMilliseconds(),
    3,
  )}`;
}

export function parseLog(input: ParseInput): LogEntry | null {
  const raw = input.text ?? "";
  // 1. 剥离 ANSI
  const cleaned = stripAnsi(raw).replace(/\r/g, "");
  if (!cleaned.trim()) return null;
  // 2. 过滤 Werkzeug 噪音
  if (isWerkzeugAccessLog(cleaned)) return null;

  // 3. 时间戳
  let timestamp = input.ts ?? "";
  if (!timestamp) {
    timestamp = formatTs(new Date());
  } else if (/^\d{2}:\d{2}:\d{2}$/.test(timestamp)) {
    // 后端只到秒，补上毫秒
    timestamp += ".000";
  }

  // 4. level：优先用后端已解析的级别，否则从文本推断
  let level: LogLevel;
  let isError = false;
  const providedLevel = (input.level ?? "").toUpperCase();
  if (["INFO", "WARN", "ERROR", "SUCCESS", "DEBUG"].includes(providedLevel)) {
    level = providedLevel as LogLevel;
    isError = level === "ERROR";
  } else {
    const detected = detectLevel(cleaned);
    level = detected.level;
    isError = detected.isError;
  }

  // 5. source + step：优先用后端已解析的步骤，否则从文本推断
  let source: string;
  let step: 1 | 2 | 3 | 4 | 5 | undefined;
  let message: string;
  const providedStep = (input.step ?? "").toLowerCase();
  const stepNumMatch = providedStep.match(/^step([1-5])$/);
  if (stepNumMatch) {
    step = Number(stepNumMatch[1]) as 1 | 2 | 3 | 4 | 5;
    source = `Step${step}`;
    message = cleaned;
  } else {
    const detected = detectSource(cleaned);
    source = detected.source;
    step = detected.step;
    message = detected.message;
  }

  // 6. segments
  const segments = tokenizeMessage(message);

  return {
    id: nextId(),
    timestamp,
    level,
    source,
    step,
    isError,
    segments,
  };
}

// 批量解析（用于历史日志）
export function parseLogs(inputs: ParseInput[]): LogEntry[] {
  const out: LogEntry[] = [];
  for (const i of inputs) {
    const e = parseLog(i);
    if (e) out.push(e);
  }
  return out;
}
