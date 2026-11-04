// 日志流统一 hook
//
// 根据数据源模式自动切换：
//   - mock 模式（VITE_USE_MOCK=true）：加载剧本 + 定时推送循环批次
//   - 真实模式（默认）：拉取 /api/log-history + 订阅 /api/log-stream SSE

import { useEffect, useRef } from "react";
import { useLogStore, makeLoopBatch } from "@/store/logStore";
import { useSSELogStream } from "@/hooks/useSSELogStream";

// 把 "HH:mm:ss.SSS" 解析为当天 Date
function parseTs(ts: string): Date {
  const [hms, ms = "0"] = ts.split(".");
  const [h, m, s] = hms.split(":").map(Number);
  const d = new Date();
  d.setHours(h, m, s, Number(ms.padEnd(3, "0").slice(0, 3)));
  return d;
}

export function useLogStream(
  mockIntervalMs = 4000,
  mockBatchGapMs = 1500,
) {
  const useMock = useLogStore((s) => s.useMock);
  const appendLogs = useLogStore((s) => s.appendLogs);
  const appendRealLog = useLogStore((s) => s.appendRealLog);
  const bootstrap = useLogStore((s) => s.bootstrap);
  const loadHistory = useLogStore((s) => s.loadHistory);
  const loadRuns = useLogStore((s) => s.loadRuns);
  const applyDeepLink = useLogStore((s) => s.applyDeepLink);
  const locateDeepLinkTarget = useLogStore((s) => s.locateDeepLinkTarget);
  const currentRunId = useLogStore((s) => s.currentRunId);
  const logs = useLogStore((s) => s.logs);
  const cursorRef = useRef<Date | null>(null);
  const startedRef = useRef(false);

  // ───── mock 模式：加载剧本 + 定时推送循环批次 ─────
  useEffect(() => {
    if (!useMock) return;
    bootstrap();
  }, [useMock, bootstrap]);

  useEffect(() => {
    if (!useMock) return;
    if (logs.length === 0) return;
    if (!cursorRef.current) {
      const last = logs[logs.length - 1];
      cursorRef.current = parseTs(last.timestamp);
    }
    const timer = setInterval(() => {
      if (!cursorRef.current) return;
      cursorRef.current = new Date(
        cursorRef.current.getTime() + mockBatchGapMs,
      );
      const batch = makeLoopBatch(cursorRef.current);
      appendLogs(batch);
      if (batch.length > 0) {
        cursorRef.current = parseTs(batch[batch.length - 1].timestamp);
      }
    }, mockIntervalMs);
    return () => clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [useMock, logs.length > 0]);

  // ───── 真实模式：SSE 订阅 ─────
  // 挂载时加载 run 列表和历史日志
  useEffect(() => {
    if (useMock) return;
    if (startedRef.current) return;
    startedRef.current = true;
    // ★ T04/P1-3：必须先解析深链再拉历史 —— applyDeepLink 可能把 currentRunId
    //   切到目标 run；若顺序颠倒，全局历史的异步结果会覆盖单 run 结果。
    applyDeepLink();
    loadRuns();
    loadHistory();
  }, [useMock, loadHistory, loadRuns, applyDeepLink]);

  // 历史日志到位后定位深链目标（内部有 deepLinkLocated 幂等保护）
  useEffect(() => {
    if (useMock) return;
    if (logs.length === 0) return;
    locateDeepLinkTarget();
  }, [useMock, logs.length, locateDeepLinkTarget]);

  // currentRunId 切换时重新加载历史（SSE 会因 runId 变化自动重连）
  useEffect(() => {
    if (useMock) return;
    if (!startedRef.current) return;
    loadHistory();
  }, [useMock, currentRunId, loadHistory]);

  // SSE 订阅（真实模式才启用，按 currentRunId 区分全局/单 run）
  const sseStatus = useSSELogStream({
    onLog: (entry) => {
      if (entry) appendRealLog(entry);
    },
    enabled: !useMock,
    runId: currentRunId,
  });

  return { useMock, sseStatus };
}
