import { create } from "zustand";
import { persist, createJSONStorage } from "zustand/middleware";
import type {
  LogEntry,
  LogLevel,
  TabKey,
  TimeRangeKey,
  LevelFilterOption,
} from "@/types/log";
import {
  fetchLogHistory,
  fetchRunLogHistory,
  fetchLogRuns,
  parseDeepLink,
} from "@/api/logs";
import type { RunInfo } from "@/api/logs";
import { parseLog } from "@/lib/logParser";

const PAGE_SIZE = 50;
const ALL_LEVELS: LogLevel[] = ["INFO", "WARN", "ERROR", "SUCCESS", "DEBUG"];

// 是否使用 mock 数据源（开发期无后端时启用）
// 通过 Vite 环境变量 VITE_USE_MOCK=true 控制，默认 false（对接真实后端）
const USE_MOCK =
  (import.meta.env.VITE_USE_MOCK as string | undefined)?.toLowerCase() ===
  "true";

// ───────────────────────────────────────────────────────────────
// P0-4：开发期 mock 剧本的隔离加载
//
// 剧本已迁至 log-panel-ui/mock-dev/scenario.ts（src/ 之外，不在 tsconfig.include 内）。
// 这里刻意使用「变量说明符 + @vite-ignore」的动态 import：
//   · 变量说明符 → Rollup 无法静态解析，不会为其生成任何 chunk；
//   · import.meta.env.DEV 在生产构建被静态替换为 false → 整段被 esbuild 判为死代码剔除。
// 双保险确保生产 bundle 中不出现 mock / scenario / 任何内网域名。
// ───────────────────────────────────────────────────────────────
const MOCK_SCENARIO_PATH = "../../mock-dev/scenario.ts";

interface MockScenarioModule {
  generatePipelineRun: (base: Date) => LogEntry[];
  generateLoopBatch: (base: Date) => LogEntry[];
}

let mockScenarioModule: MockScenarioModule | null = null;

/** 按需加载开发期 mock 剧本；生产环境或未开启 mock 时恒返回 null。 */
async function loadMockScenario(): Promise<MockScenarioModule | null> {
  if (!import.meta.env.DEV || !USE_MOCK) return null;
  if (mockScenarioModule) return mockScenarioModule;
  try {
    const mod = (await import(
      /* @vite-ignore */ MOCK_SCENARIO_PATH
    )) as MockScenarioModule;
    mockScenarioModule = mod;
    return mod;
  } catch (err) {
    // 不吞异常：明确告知开发者 mock 不可用，并降级为空数据源而非静默假装成功
    console.warn(
      "[logStore] 开发期 mock 剧本加载失败，已降级为空数据源：",
      err,
    );
    return null;
  }
}

interface LogState {
  // 数据
  logs: LogEntry[];
  lastUpdated: string;
  // 数据源模式
  useMock: boolean;
  historyLoaded: boolean;
  // 按 run 查看
  currentRunId: string | null;
  availableRuns: RunInfo[];
  runsLoaded: boolean;
  // 筛选
  activeTab: TabKey;
  enabledLevels: LogLevel[]; // 快切启用的级别，空数组表示全部启用
  keyword: string;
  timeRange: TimeRangeKey;
  topbarLevel: LevelFilterOption; // 顶部下拉
  // 视图
  autoScroll: boolean;
  page: number;
  pageSize: number;
  // 详情面板
  selectedLogId: string | null;
  detailPanelOpen: boolean;
  detailPanelWidth: number;
  // 步骤重放（T04/P1-3）
  replayOpen: boolean;
  replayPlaying: boolean;
  replayIndex: number; // -1 表示未开始
  replayActiveId: string | null; // 当前高亮日志 id（供 LogEntryRow 廉价比较）
  replaySpeed: number;
  // 深链（T04/P1-3，契约见 diagnostics_api.py:365）
  deepLinkRunId: string | null;
  deepLinkCaseId: string | null;
  deepLinkApplied: boolean;
  deepLinkLocated: boolean;
  // 持久化偏好
  sidebarCollapsed: boolean;
  mobileSidebarOpen: boolean;
  // 动作
  appendLogs: (entries: LogEntry[]) => void;
  appendRealLog: (entry: LogEntry) => void; // SSE 单条追加
  loadHistory: () => Promise<void>; // 从后端拉取历史日志
  loadRunHistory: (runId: string) => Promise<void>; // 按 run 拉取历史日志
  loadRuns: () => Promise<void>; // 拉取 run 列表
  setCurrentRunId: (runId: string | null) => void; // 切换当前 run
  clearLogs: () => void;
  setTab: (tab: TabKey) => void;
  toggleLevel: (level: LogLevel) => void;
  setKeyword: (kw: string) => void;
  setTimeRange: (r: TimeRangeKey) => void;
  setTopbarLevel: (l: LevelFilterOption) => void;
  toggleAutoScroll: () => void;
  setPage: (p: number) => void;
  goPrevPage: () => void;
  goNextPage: () => void;
  setSidebarCollapsed: (v: boolean) => void;
  setMobileSidebarOpen: (v: boolean) => void;
  selectLog: (id: string | null) => void;
  setDetailPanelOpen: (v: boolean) => void;
  setDetailPanelWidth: (w: number) => void;
  setReplayOpen: (v: boolean) => void;
  setReplayPlaying: (v: boolean) => void;
  setReplayIndex: (idx: number, id: string | null) => void;
  setReplaySpeed: (v: number) => void;
  applyDeepLink: () => void; // 解析 URL 并切到目标 run
  locateDeepLinkTarget: () => void; // 历史加载后定位到目标 case
  bootstrap: () => void; // mock 模式下加载剧本
}

// 重放步骤：由可见日志派生，携带相对首条的时间偏移
export interface ReplayStep {
  id: string;
  index: number;
  timestamp: string;
  offsetMs: number;
  level: LogLevel;
  source: string;
  step?: 1 | 2 | 3 | 4 | 5;
  text: string;
  isError: boolean;
}

/** 解析 "HH:mm:ss.SSS" 为当天 0 点起的毫秒数；解析失败返回 0。 */
function tsToMs(ts: string): number {
  if (!ts) return 0;
  const parts = String(ts).split(":");
  if (parts.length < 2) return 0;
  const h = parseInt(parts[0], 10) || 0;
  const m = parseInt(parts[1], 10) || 0;
  const s = parts.length > 2 ? parseFloat(parts[2]) || 0 : 0;
  return (h * 3600 + m * 60 + s) * 1000;
}

// 初始时间基准
function makeBaseTime(): Date {
  const d = new Date();
  d.setHours(15, 23, 45, 0);
  return d;
}

function filterByTab(logs: LogEntry[], tab: TabKey): LogEntry[] {
  switch (tab) {
    case "all":
      return logs;
    case "step1":
      return logs.filter((l) => l.step === 1);
    case "step2":
      return logs.filter((l) => l.step === 2);
    case "step3":
      return logs.filter((l) => l.step === 3);
    case "step4":
      return logs.filter((l) => l.step === 4);
    case "step5":
      return logs.filter((l) => l.step === 5);
    case "system":
      return logs.filter((l) => l.source === "系统" || l.source === "Token");
    case "error":
      return logs.filter((l) => l.isError || l.level === "ERROR");
    default:
      return logs;
  }
}

function filterByLevel(logs: LogEntry[], enabled: LogLevel[]): LogEntry[] {
  if (enabled.length === 0) return logs;
  return logs.filter((l) => enabled.includes(l.level));
}

function filterByKeyword(logs: LogEntry[], kw: string): LogEntry[] {
  const k = kw.trim().toLowerCase();
  if (!k) return logs;
  return logs.filter((l) => {
    const text = l.segments.map((s) => s.text).join("");
    return (
      text.toLowerCase().includes(k) ||
      l.source.toLowerCase().includes(k) ||
      l.level.toLowerCase().includes(k) ||
      l.timestamp.toLowerCase().includes(k)
    );
  });
}

export const useLogStore = create<LogState>()(
  persist(
    (set, get) => ({
      logs: [],
      lastUpdated: "--:--:--",
      useMock: USE_MOCK,
      historyLoaded: false,
      currentRunId: null,
      availableRuns: [],
      runsLoaded: false,
      activeTab: "all",
      enabledLevels: [],
      keyword: "",
      timeRange: "all",
      topbarLevel: "ALL",
      autoScroll: true,
      page: 1,
      pageSize: PAGE_SIZE,
      sidebarCollapsed: false,
      mobileSidebarOpen: false,
      selectedLogId: null,
      detailPanelOpen: false,
      detailPanelWidth: 420,
      replayOpen: false,
      replayPlaying: false,
      replayIndex: -1,
      replayActiveId: null,
      replaySpeed: 1,
      deepLinkRunId: null,
      deepLinkCaseId: null,
      deepLinkApplied: false,
      deepLinkLocated: false,

      appendLogs: (entries) => {
        if (entries.length === 0) return;
        set((s) => {
          const logs = [...s.logs, ...entries];
          const last = entries[entries.length - 1];
          return {
            logs,
            lastUpdated: last.timestamp,
          };
        });
      },

      // SSE 单条追加（实时日志）
      appendRealLog: (entry) => {
        set((s) => {
          // 防止日志无限增长（保留最近 1000 条）
          const logs = [...s.logs, entry];
          if (logs.length > 1000) {
            logs.splice(0, logs.length - 1000);
          }
          return { logs, lastUpdated: entry.timestamp };
        });
      },

      // 从后端拉取历史日志（当前 run 优先，否则全局）
      loadHistory: async () => {
        if (USE_MOCK) return;
        if (get().historyLoaded) return;
        const runId = get().currentRunId;
        if (runId) {
          await get().loadRunHistory(runId);
          return;
        }
        try {
          const raw = await fetchLogHistory();
          if (raw.length === 0) {
            set({ historyLoaded: true });
            return;
          }
          const entries: LogEntry[] = [];
          for (const r of raw) {
            const e = parseLog({ text: r.text, ts: r.ts, step: r.step, level: r.level });
            if (e) entries.push(e);
          }
          set((s) => {
            const logs = entries.length > 0 ? entries : s.logs;
            return {
              logs,
              lastUpdated:
                entries.length > 0
                  ? entries[entries.length - 1].timestamp
                  : s.lastUpdated,
              historyLoaded: true,
            };
          });
        } catch (e) {
          console.warn("[store] loadHistory 失败:", e);
          set({ historyLoaded: true });
        }
      },

      // 按 run 拉取完整历史日志（分页一次拉完，避免 Step4 日志淹没前面步骤）
      loadRunHistory: async (runId) => {
        if (USE_MOCK) return;
        try {
          const all: LogEntry[] = [];
          let offset = 0;
          const limit = 2000;
          let hasMore = true;
          while (hasMore) {
            const res = await fetchRunLogHistory(runId, offset, limit, "asc");
            const entries: LogEntry[] = [];
            for (const r of res.logs) {
              const e = parseLog({ text: r.text, ts: r.ts, step: r.step, level: r.level });
              if (e) entries.push(e);
            }
            all.push(...entries);
            hasMore = res.has_more;
            offset += res.logs.length;
            if (res.logs.length === 0) break;
          }
          set((s) => ({
            logs: all,
            lastUpdated:
              all.length > 0 ? all[all.length - 1].timestamp : s.lastUpdated,
            historyLoaded: true,
          }));
        } catch (e) {
          console.warn("[store] loadRunHistory 失败:", e);
          set({ historyLoaded: true });
        }
      },

      // 拉取当前项目最近的 run 列表
      loadRuns: async () => {
        if (USE_MOCK) return;
        try {
          const res = await fetchLogRuns();
          set({
            availableRuns: res.runs,
            runsLoaded: true,
          });
        } catch (e) {
          console.warn("[store] loadRuns 失败:", e);
          set({ runsLoaded: true });
        }
      },

      // 切换当前 run：清空日志、重置历史加载状态。
      // 实际拉取由 useLogStream 的 currentRunId effect 触发，避免重复加载。
      setCurrentRunId: (runId) => {
        if (runId === get().currentRunId) return;
        set({
          currentRunId: runId,
          logs: [],
          historyLoaded: false,
          page: 1,
          selectedLogId: null,
          detailPanelOpen: false,
          // 数据源已换，重放进度必须归零，否则索引会指向旧 run 的条目
          replayPlaying: false,
          replayIndex: -1,
          replayActiveId: null,
        });
      },

      clearLogs: () => {
        set({ logs: [], lastUpdated: "--:--:--", page: 1 });
      },

      setTab: (tab) => set({ activeTab: tab, page: 1 }),

      toggleLevel: (level) =>
        set((s) => {
          const exists = s.enabledLevels.includes(level);
          const next = exists
            ? s.enabledLevels.filter((l) => l !== level)
            : [...s.enabledLevels, level];
          return { enabledLevels: next, page: 1 };
        }),

      setKeyword: (kw) => set({ keyword: kw, page: 1 }),

      setTimeRange: (r) => set({ timeRange: r, page: 1 }),

      setTopbarLevel: (l) => {
        // 顶部下拉同步到快切
        if (l === "ALL") {
          set({ topbarLevel: "ALL", enabledLevels: [], page: 1 });
        } else {
          set({ topbarLevel: l, enabledLevels: [l], page: 1 });
        }
      },

      toggleAutoScroll: () => set((s) => ({ autoScroll: !s.autoScroll })),

      setPage: (p) => set({ page: Math.max(1, p) }),
      goPrevPage: () => set((s) => ({ page: Math.max(1, s.page - 1) })),
      goNextPage: () => set((s) => ({ page: s.page + 1 })),

      setSidebarCollapsed: (v) => set({ sidebarCollapsed: v }),
      setMobileSidebarOpen: (v) => set({ mobileSidebarOpen: v }),

      selectLog: (id) => set({ selectedLogId: id, detailPanelOpen: id !== null }),
      setDetailPanelOpen: (v) => set({ detailPanelOpen: v }),
      setDetailPanelWidth: (w) => set({ detailPanelWidth: w }),

      // ───── 步骤重放（T04/P1-3）─────
      // 关闭重放时一并停播并清高亮，避免残留定时器与幽灵高亮行
      setReplayOpen: (v) =>
        set(
          v
            ? { replayOpen: true }
            : {
                replayOpen: false,
                replayPlaying: false,
                replayIndex: -1,
                replayActiveId: null,
              },
        ),
      setReplayPlaying: (v) => set({ replayPlaying: v }),
      setReplayIndex: (idx, id) =>
        set({ replayIndex: idx, replayActiveId: idx < 0 ? null : id }),
      setReplaySpeed: (v) => set({ replaySpeed: v > 0 ? v : 1 }),

      // ───── 深链（T04/P1-3）─────
      // 只解析一次；run_id 直接切换数据源，case_id 暂存待 locateDeepLinkTarget 定位
      applyDeepLink: () => {
        if (get().deepLinkApplied) return;
        const { runId, caseId } = parseDeepLink();
        set({
          deepLinkRunId: runId,
          deepLinkCaseId: caseId,
          deepLinkApplied: true,
        });
        if (runId) get().setCurrentRunId(runId);
      },

      // 历史日志到位后，定位到首条提及该 case_id 的日志并展开详情。
      // 找不到时不改动筛选条件（避免把面板筛空造成「假定位」）。
      locateDeepLinkTarget: () => {
        const s = get();
        const caseId = s.deepLinkCaseId;
        if (!caseId || s.deepLinkLocated) return;
        if (s.logs.length === 0) return;
        const hit = s.logs.find((l) =>
          l.segments.some((seg) => seg.text.includes(caseId)),
        );
        if (!hit) {
          set({ deepLinkLocated: true });
          return;
        }
        const idx = s.logs.indexOf(hit);
        set({
          deepLinkLocated: true,
          keyword: "",
          activeTab: "all",
          enabledLevels: [],
          page: Math.floor(idx / s.pageSize) + 1,
          autoScroll: false,
          selectedLogId: hit.id,
          detailPanelOpen: true,
        });
      },

      bootstrap: () => {
        // 仅在 mock 模式下加载剧本；真实模式由 loadHistory() 负责
        if (!USE_MOCK) return;
        if (get().logs.length > 0) return;
        // 剧本改为异步按需加载（P0-4），对外签名保持同步 void，调用方无需改动
        void loadMockScenario().then((mod) => {
          if (!mod) return;
          // 异步窗口内可能已被真实数据填充，二次确认避免覆盖
          if (get().logs.length > 0) return;
          const initial = mod.generatePipelineRun(makeBaseTime());
          set({
            logs: initial,
            lastUpdated: initial[initial.length - 1]?.timestamp ?? "--:--:--",
          });
        });
      },
    }),
    {
      name: "log-panel-prefs",
      storage: createJSONStorage(() => localStorage),
      partialize: (s) => ({
        activeTab: s.activeTab,
        enabledLevels: s.enabledLevels,
        timeRange: s.timeRange,
        autoScroll: s.autoScroll,
        sidebarCollapsed: s.sidebarCollapsed,
        topbarLevel: s.topbarLevel,
      }),
    },
  ),
);

// 派生选择器：根据当前筛选条件计算可见日志
export function selectFilteredLogs(s: LogState): LogEntry[] {
  let out = s.logs;
  out = filterByTab(out, s.activeTab);
  out = filterByLevel(out, s.enabledLevels);
  out = filterByKeyword(out, s.keyword);
  return out;
}

// 派生选择器：重放步骤序列（T04/P1-3）
//
// 基于「当前可见日志」而非全量 logs —— 高亮目标与 LogStream 渲染出的行
// 严格一一对应，不会出现「进度走了但页面没有对应行」的错位。
//
// ⚠ buildReplaySteps 每次返回全新对象数组，直接配 useShallow 订阅会导致
//   「快照恒不相等 → 无限重渲染」。组件侧务必先用 useShallow 订阅
//   selectFilteredLogs（元素引用稳定），再用 useMemo 调 buildReplaySteps。
export function buildReplaySteps(visible: LogEntry[]): ReplayStep[] {
  if (visible.length === 0) return [];
  const baseMs = tsToMs(visible[0].timestamp);
  return visible.map((l, i) => {
    const offset = tsToMs(l.timestamp) - baseMs;
    return {
      id: l.id,
      index: i,
      timestamp: l.timestamp,
      offsetMs: offset > 0 ? offset : 0,
      level: l.level,
      source: l.source,
      step: l.step,
      text: l.segments.map((seg) => seg.text).join(""),
      isError: Boolean(l.isError) || l.level === "ERROR",
    };
  });
}

/** 整状态版派生选择器（供非渲染路径 / 单元测试直接取值）。 */
export function selectReplaySteps(s: LogState): ReplayStep[] {
  return buildReplaySteps(selectFilteredLogs(s));
}

// 标签计数
export function selectTabCounts(s: LogState): Record<TabKey, number> {
  const all = s.logs;
  return {
    all: all.length,
    step1: all.filter((l) => l.step === 1).length,
    step2: all.filter((l) => l.step === 2).length,
    step3: all.filter((l) => l.step === 3).length,
    step4: all.filter((l) => l.step === 4).length,
    step5: all.filter((l) => l.step === 5).length,
    system: all.filter((l) => l.source === "系统" || l.source === "Token").length,
    error: all.filter((l) => l.isError || l.level === "ERROR").length,
  };
}

// 推送循环批次（用于 hook 调用）
// 签名保持同步 LogEntry[]，useLogStream.ts 零改动；
// 剧本尚未就绪时先触发一次异步加载并返回空批次，下一个 tick 自然生效。
export function makeLoopBatch(baseTime: Date): LogEntry[] {
  if (!mockScenarioModule) {
    void loadMockScenario();
    return [];
  }
  return mockScenarioModule.generateLoopBatch(baseTime);
}

export { ALL_LEVELS };
