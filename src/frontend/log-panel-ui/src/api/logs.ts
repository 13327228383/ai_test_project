// 日志 API 客户端
//
// 对接后端：
//   GET /api/log-history           → 全局历史日志（最近 200 条）
//   GET /api/log-history/<run_id>  → 指定 run 的分页历史日志
//   GET /api/log-runs              → 当前项目最近的 run 列表
//   GET /api/log-stream            → SSE 实时日志流（在 useSSELogStream.ts 中用 EventSource 对接）
//   GET /api/log-stream/<run_id>   → 指定 run 的 SSE 实时日志流

export interface RawLogEntry {
  ts?: string;
  text: string;
  step?: string;
  level?: string;
}

export interface RunLogEntry extends RawLogEntry {
  run_id?: string;
}

export interface RunInfo {
  run_id: string;
  log_count: number;
  first_ts: string;
  last_ts: string;
}

export interface PaginatedRunLogs {
  run_id: string;
  total: number;
  offset: number;
  limit: number;
  has_more: boolean;
  logs: RunLogEntry[];
  error?: string;
}

export interface LogRunsResponse {
  runs: RunInfo[];
  total: number;
  error?: string;
}

// 取全局历史日志
export async function fetchLogHistory(): Promise<RawLogEntry[]> {
  try {
    const r = await fetch("/api/log-history", { credentials: "same-origin" });
    if (!r.ok) {
      console.warn("[logs] /api/log-history 返回非 200:", r.status);
      return [];
    }
    const data = await r.json();
    if (!Array.isArray(data)) return [];
    return data as RawLogEntry[];
  } catch (e) {
    console.warn("[logs] 拉取历史日志失败:", e);
    return [];
  }
}

// 取指定 run 的历史日志（分页）
export async function fetchRunLogHistory(
  runId: string,
  offset = 0,
  limit = 2000,
  order: "asc" | "desc" = "asc"
): Promise<PaginatedRunLogs> {
  try {
    const params = new URLSearchParams({
      offset: String(offset),
      limit: String(limit),
      order,
    });
    const r = await fetch(
      `/api/log-history/${encodeURIComponent(runId)}?${params.toString()}`,
      { credentials: "same-origin" }
    );
    if (!r.ok) {
      console.warn(`[logs] /api/log-history/${runId} 返回非 200:`, r.status);
      return { run_id: runId, total: 0, offset, limit, has_more: false, logs: [] };
    }
    const data = await r.json();
    if (Array.isArray(data)) {
      // 兼容旧格式 [{ts,text},...]
      return {
        run_id: runId,
        total: data.length,
        offset,
        limit,
        has_more: false,
        logs: data as RunLogEntry[],
      };
    }
    return {
      run_id: data.run_id || runId,
      total: data.total ?? 0,
      offset: data.offset ?? offset,
      limit: data.limit ?? limit,
      has_more: data.has_more ?? false,
      logs: Array.isArray(data.logs) ? (data.logs as RunLogEntry[]) : [],
      error: data.error,
    };
  } catch (e) {
    console.warn(`[logs] 拉取 run=${runId} 历史日志失败:`, e);
    return { run_id: runId, total: 0, offset, limit, has_more: false, logs: [] };
  }
}

// ───────────────────────────────────────────────────────────────
// T04/P1-3：深链参数 & 步骤重放上下文
//
// 深链契约来源：src/backend/routes/diagnostics/diagnostics_api.py:365
//   "log_ref": "/log-panel?run_id=%s&case_id=%s"
// 截图数据源：GET /api/docs/preview?path=mysql://screenshots/{case_id}
//   → {"type":"image","ext":".png","size":N,"data_url":"data:image/png;base64,..."}
//   （src/backend/routes/docs_analytics/routes/_preview.py:73-78）
// ───────────────────────────────────────────────────────────────

export interface DeepLinkParams {
  runId: string | null;
  caseId: string | null;
}

/** 从 URL query string 解析 `?run_id=&case_id=` 深链参数。 */
export function parseDeepLink(search?: string): DeepLinkParams {
  const raw =
    typeof search === "string"
      ? search
      : typeof window !== "undefined"
        ? window.location.search
        : "";
  try {
    const sp = new URLSearchParams(raw);
    const runId = (sp.get("run_id") || "").trim();
    const caseId = (sp.get("case_id") || "").trim();
    return { runId: runId || null, caseId: caseId || null };
  } catch (e) {
    console.warn("[logs] 深链参数解析失败:", e);
    return { runId: null, caseId: null };
  }
}

export interface ScreenshotResult {
  ok: boolean;
  dataUrl: string;
  size: number;
  error: string;
}

/**
 * 取指定用例的执行截图（只读 GET，绝不触发执行）。
 *
 * 后端仅提供「按 case_id 取最近一张」的能力，无按 step_num 分帧的公开接口，
 * 因此重放期间同一 case 的截图为同一张（详见交付报告「偏差说明」）。
 */
export async function fetchCaseScreenshot(
  caseId: string,
): Promise<ScreenshotResult> {
  const empty: ScreenshotResult = { ok: false, dataUrl: "", size: 0, error: "" };
  if (!caseId) return { ...empty, error: "缺少 case_id" };
  try {
    const path = `mysql://screenshots/${caseId}`;
    const r = await fetch(
      `/api/docs/preview?path=${encodeURIComponent(path)}`,
      { credentials: "same-origin" },
    );
    const data = await r.json().catch(() => null);
    if (!r.ok) {
      const msg =
        data && typeof data.error === "string" ? data.error : `HTTP ${r.status}`;
      return { ...empty, error: msg };
    }
    if (!data || data.type !== "image" || typeof data.data_url !== "string") {
      return { ...empty, error: "该用例无截图" };
    }
    return { ok: true, dataUrl: data.data_url, size: data.size ?? 0, error: "" };
  } catch (e) {
    console.warn(`[logs] 拉取 case=${caseId} 截图失败:`, e);
    return { ...empty, error: "截图请求失败" };
  }
}

// 取当前项目最近的 run 列表
export async function fetchLogRuns(): Promise<LogRunsResponse> {
  try {
    const r = await fetch("/api/log-runs", { credentials: "same-origin" });
    if (!r.ok) {
      console.warn("[logs] /api/log-runs 返回非 200:", r.status);
      return { runs: [], total: 0 };
    }
    const data = await r.json();
    return {
      runs: Array.isArray(data.runs) ? (data.runs as RunInfo[]) : [],
      total: data.total ?? 0,
      error: data.error,
    };
  } catch (e) {
    console.warn("[logs] 拉取 run 列表失败:", e);
    return { runs: [], total: 0 };
  }
}
