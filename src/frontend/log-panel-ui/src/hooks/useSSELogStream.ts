// SSE 实时日志流订阅 hook
//
// 对接后端 /api/log-stream（EventSource）
// 后端消息格式：
//   data: {"log": "<纯文本>", "run_id": "...", "priority": "high|normal|low"}
//   data: {"heartbeat": true}
//   data: {"health_summary": {...}}
//
// 自动重连：浏览器原生 EventSource 自带重连，无需手动处理
// 但持续断开 30s 时会通过 onerror 回调通知调用方

import { useEffect, useRef, useState } from "react";
import { parseLog } from "@/lib/logParser";

export interface SSEStatus {
  connected: boolean;
  errorCount: number;
  lastError?: string;
  receivedCount: number;
}

interface UseSSELogStreamOptions {
  onLog: (entry: ReturnType<typeof parseLog>) => void;
  onHealthSummary?: (summary: unknown) => void;
  // 是否订阅，默认 true；可由调用方在挂载后控制
  enabled?: boolean;
  // 指定 run_id 时订阅 /api/log-stream/<run_id>，否则订阅全局日志流
  runId?: string | null;
}

export function useSSELogStream({
  onLog,
  onHealthSummary,
  enabled = true,
  runId,
}: UseSSELogStreamOptions): SSEStatus {
  const [status, setStatus] = useState<SSEStatus>({
    connected: false,
    errorCount: 0,
    receivedCount: 0,
  });
  const evtRef = useRef<EventSource | null>(null);
  const receivedRef = useRef(0);
  const errorTsRef = useRef<number | null>(null);
  const lastErrorShownRef = useRef(false);
  // 把回调存到 ref，避免 onLog 变化重建 EventSource
  const onLogRef = useRef(onLog);
  const onHealthRef = useRef(onHealthSummary);
  onLogRef.current = onLog;
  onHealthRef.current = onHealthSummary;

  useEffect(() => {
    if (!enabled) return;
    // 关闭旧连接
    if (evtRef.current) {
      evtRef.current.close();
      evtRef.current = null;
    }

    const url = runId
      ? `/api/log-stream?run_id=${encodeURIComponent(runId)}`
      : "/api/log-stream";
    const evt = new EventSource(url);
    evtRef.current = evt;

    evt.onopen = () => {
      errorTsRef.current = null;
      lastErrorShownRef.current = false;
      setStatus((s) => ({ ...s, connected: true, lastError: undefined }));
    };

    evt.onmessage = (e) => {
      try {
        const data = JSON.parse(e.data);
        // 心跳
        if (data.heartbeat === true) return;
        // 健康摘要
        if (data.health_summary) {
          onHealthRef.current?.(data.health_summary);
          return;
        }
        // 普通日志
        if (typeof data.log === "string") {
          const entry = parseLog({
            text: data.log,
            runId: data.run_id,
            step: data.step,
            level: data.level,
          });
          if (entry) {
            receivedRef.current += 1;
            onLogRef.current(entry);
            setStatus((s) => ({
              ...s,
              receivedCount: receivedRef.current,
            }));
          }
        }
      } catch (err) {
        // 非 JSON 消息，忽略
        console.warn("[SSE] 解析消息失败:", err);
      }
    };

    evt.onerror = () => {
      // 浏览器原生 EventSource 会自动重连
      // 持续断开 30s 才提示
      if (!errorTsRef.current) errorTsRef.current = Date.now();
      const sustained = Date.now() - errorTsRef.current > 30000;
      if (sustained && !lastErrorShownRef.current) {
        lastErrorShownRef.current = true;
        setStatus((s) => ({
          ...s,
          connected: false,
          errorCount: s.errorCount + 1,
          lastError: "日志流连接中断，正在重连...",
        }));
      } else {
        setStatus((s) => ({ ...s, connected: false }));
      }
    };

    return () => {
      evt.close();
      evtRef.current = null;
    };
  }, [enabled, runId]);

  return status;
}
