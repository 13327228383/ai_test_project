import type { LogEntry } from "@/types/log";

// 把日志条目导出为可下载文件
function flattenEntry(e: LogEntry): string {
  const msg = e.segments.map((s) => s.text).join("");
  return `[${e.timestamp}] [${e.level}] [${e.source}] ${msg}`;
}

export function exportAsTxt(logs: LogEntry[], filename = "e2e-logs.txt") {
  const text = logs.map(flattenEntry).join("\n");
  download(text, filename, "text/plain;charset=utf-8");
}

export function exportAsJson(logs: LogEntry[], filename = "e2e-logs.json") {
  const json = JSON.stringify(
    logs.map((e) => ({
      timestamp: e.timestamp,
      level: e.level,
      source: e.source,
      step: e.step ?? null,
      message: e.segments.map((s) => s.text).join(""),
    })),
    null,
    2,
  );
  download(json, filename, "application/json;charset=utf-8");
}

function download(content: string, filename: string, mime: string) {
  const blob = new Blob([content], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}
