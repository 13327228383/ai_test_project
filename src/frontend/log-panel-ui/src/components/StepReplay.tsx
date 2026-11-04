// 步骤重放播放器（T04/P1-3）
//
// 从 templates/logs.html（1466 行）的「按键重放播放器」移植而来，行为对齐：
//   · 时间轴基于日志时间戳的相对偏移，单步延迟 = 相邻偏移差 / 速度，钳制 [50ms, 3000ms]
//   · play / pause / 上一步 / 下一步 / 点击进度条 seek
//   · 逐步高亮当前日志行（React 侧改为 store.replayActiveId 驱动，不操作 class）
// React 侧增强：
//   · 重放序列基于「当前可见日志」，与 LogStream 渲染行严格一一对应
//   · 跨页自动翻页 + 滚动到可视区
//   · 截图快照：GET /api/docs/preview?path=mysql://screenshots/{case_id}（只读）

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useShallow } from "zustand/react/shallow";
import { Camera, Pause, Play, RotateCcw, SkipBack, SkipForward, X } from "lucide-react";
import {
  useLogStore,
  selectFilteredLogs,
  buildReplaySteps,
} from "@/store/logStore";
import { fetchCaseScreenshot } from "@/api/logs";

const SPEEDS: number[] = [0.5, 1, 2, 4];
const MIN_DELAY_MS = 50;
const MAX_DELAY_MS = 3000;

/** 毫秒转 "MM:SS"（与 logs.html msToTime 一致）。 */
function msToClock(ms: number): string {
  const safe = ms > 0 ? ms : 0;
  const totalSec = Math.floor(safe / 1000);
  const m = Math.floor(totalSec / 60);
  const s = totalSec % 60;
  return `${m < 10 ? "0" : ""}${m}:${s < 10 ? "0" : ""}${s}`;
}

interface ShotState {
  loading: boolean;
  dataUrl: string;
  error: string;
}

const SHOT_INIT: ShotState = { loading: false, dataUrl: "", error: "" };

export default function StepReplay() {
  const open = useLogStore((s) => s.replayOpen);
  const playing = useLogStore((s) => s.replayPlaying);
  const index = useLogStore((s) => s.replayIndex);
  const speed = useLogStore((s) => s.replaySpeed);
  const pageSize = useLogStore((s) => s.pageSize);
  const autoScroll = useLogStore((s) => s.autoScroll);
  const caseId = useLogStore((s) => s.deepLinkCaseId);
  const setReplayOpen = useLogStore((s) => s.setReplayOpen);
  const setReplayPlaying = useLogStore((s) => s.setReplayPlaying);
  const setReplayIndex = useLogStore((s) => s.setReplayIndex);
  const setReplaySpeed = useLogStore((s) => s.setReplaySpeed);
  const setPage = useLogStore((s) => s.setPage);
  const toggleAutoScroll = useLogStore((s) => s.toggleAutoScroll);

  // 元素引用稳定 → useShallow 安全；派生对象数组交给 useMemo，避免无限重渲染
  const filtered = useLogStore(useShallow(selectFilteredLogs));
  const steps = useMemo(() => buildReplaySteps(filtered), [filtered]);

  const total = steps.length;
  const totalMs = total > 0 ? steps[total - 1].offsetMs : 0;
  const current = index >= 0 && index < total ? steps[index] : null;

  const [shot, setShot] = useState<ShotState>(SHOT_INIT);
  const timerRef = useRef<number | null>(null);
  const progressRef = useRef<HTMLDivElement>(null);

  // steps 每次渲染都是新数组（buildReplaySteps 产出新对象）。若把它写进
  // useCallback / useEffect 的依赖，SSE 每推一条日志就会重建定时器，
  // 播放会被无限打断。这里用 ref 承载最新值，依赖只留稳定的原始类型。
  const stepsRef = useRef(steps);
  stepsRef.current = steps;

  // ───── 跳转到指定步（含边界钳制）─────
  const goTo = useCallback(
    (next: number) => {
      const list = stepsRef.current;
      if (list.length === 0) {
        setReplayIndex(-1, null);
        return;
      }
      const max = list.length - 1;
      const clamped = next < -1 ? -1 : next > max ? max : next;
      setReplayIndex(clamped, clamped >= 0 ? list[clamped].id : null);
      if (clamped >= 0) setPage(Math.floor(clamped / pageSize) + 1);
    },
    [pageSize, setReplayIndex, setPage],
  );

  // ───── 打开重放时关掉实时跟随，避免自动滚底与高亮定位互相打架 ─────
  useEffect(() => {
    if (open && autoScroll) toggleAutoScroll();
  }, [open, autoScroll, toggleAutoScroll]);

  // ───── 播放调度：延迟 = 相邻时间偏移差 / 速度，钳制 [50, 3000] ─────
  useEffect(() => {
    if (timerRef.current !== null) {
      window.clearTimeout(timerRef.current);
      timerRef.current = null;
    }
    if (!open || !playing || total === 0) return;
    if (index >= total - 1) {
      setReplayPlaying(false);
      return;
    }
    let delay = MIN_DELAY_MS;
    if (index >= 0) {
      const list = stepsRef.current;
      const gap = list[index + 1].offsetMs - list[index].offsetMs;
      delay = gap / (speed > 0 ? speed : 1);
    }
    if (delay < MIN_DELAY_MS) delay = MIN_DELAY_MS;
    if (delay > MAX_DELAY_MS) delay = MAX_DELAY_MS;
    timerRef.current = window.setTimeout(() => {
      timerRef.current = null;
      goTo(index + 1);
    }, delay);
    return () => {
      if (timerRef.current !== null) {
        window.clearTimeout(timerRef.current);
        timerRef.current = null;
      }
    };
  }, [open, playing, index, speed, total, goTo, setReplayPlaying]);

  // ───── 当前步滚动到可视区（等翻页渲染完成后再查 DOM）─────
  // 依赖用 currentId 这个字符串，避免 current 对象每渲染都换引用导致反复滚动
  const currentId = current ? current.id : "";
  useEffect(() => {
    if (!open || !currentId) return;
    const raf = window.requestAnimationFrame(() => {
      const el = document.querySelector(`[data-log-id="${CSS.escape(currentId)}"]`);
      if (el) el.scrollIntoView({ behavior: "smooth", block: "center" });
    });
    return () => window.cancelAnimationFrame(raf);
  }, [open, currentId]);

  // ───── 截图快照（只读 GET；无 case_id 时不发请求）─────
  const loadShot = useCallback(async () => {
    if (!caseId) {
      setShot({ loading: false, dataUrl: "", error: "" });
      return;
    }
    setShot({ loading: true, dataUrl: "", error: "" });
    const res = await fetchCaseScreenshot(caseId);
    setShot({
      loading: false,
      dataUrl: res.ok ? res.dataUrl : "",
      error: res.ok ? "" : res.error || "截图不可用",
    });
  }, [caseId]);

  useEffect(() => {
    if (!open) return;
    void loadShot();
  }, [open, loadShot]);

  if (!open) return null;

  const pct = totalMs > 0 && index >= 0 ? (steps[index].offsetMs / totalMs) * 100 : 0;

  const onSeek = (e: React.MouseEvent<HTMLDivElement>) => {
    if (total === 0) return;
    const box = progressRef.current;
    if (!box) return;
    const rect = box.getBoundingClientRect();
    if (rect.width <= 0) return;
    let ratio = (e.clientX - rect.left) / rect.width;
    if (ratio < 0) ratio = 0;
    if (ratio > 1) ratio = 1;
    setReplayPlaying(false);
    const targetMs = ratio * totalMs;
    let idx = 0;
    for (let i = 0; i < total; i++) {
      if (steps[i].offsetMs <= targetMs) idx = i;
      else break;
    }
    goTo(idx);
  };

  const onTogglePlay = () => {
    if (total === 0) return;
    if (playing) {
      setReplayPlaying(false);
      return;
    }
    if (index >= total - 1) goTo(-1); // 已到末尾 → 从头再播
    setReplayPlaying(true);
  };

  const onStep = (dir: number) => {
    setReplayPlaying(false);
    goTo(index + dir);
  };

  return (
    <div
      className="shrink-0 flex flex-col"
      style={{
        background: "var(--bg-surface)",
        borderTop: "1px solid var(--border-default)",
      }}
    >
      {/* ── 控制条 ── */}
      <div className="flex items-center gap-2 px-4 py-2">
        <span className="text-micro font-semibold text-text-primary shrink-0">步骤重放</span>

        <button
          onClick={() => onStep(-1)}
          disabled={index <= 0}
          className="p-1 rounded text-text-secondary hover:text-text-primary disabled:opacity-30 disabled:cursor-not-allowed cursor-pointer transition-colors"
          title="上一步"
        >
          <SkipBack size={14} />
        </button>
        <button
          onClick={onTogglePlay}
          disabled={total === 0}
          className="p-1.5 rounded disabled:opacity-30 disabled:cursor-not-allowed cursor-pointer transition-colors"
          style={{ background: "var(--color-primary)", color: "var(--bg-base)" }}
          title={playing ? "暂停" : "播放"}
        >
          {playing ? <Pause size={14} /> : <Play size={14} />}
        </button>
        <button
          onClick={() => onStep(1)}
          disabled={total === 0 || index >= total - 1}
          className="p-1 rounded text-text-secondary hover:text-text-primary disabled:opacity-30 disabled:cursor-not-allowed cursor-pointer transition-colors"
          title="下一步"
        >
          <SkipForward size={14} />
        </button>
        <button
          onClick={() => {
            setReplayPlaying(false);
            goTo(-1);
          }}
          disabled={index < 0}
          className="p-1 rounded text-text-secondary hover:text-text-primary disabled:opacity-30 disabled:cursor-not-allowed cursor-pointer transition-colors"
          title="回到开头"
        >
          <RotateCcw size={13} />
        </button>

        <span className="text-micro font-mono text-text-tertiary shrink-0" style={{ width: 42 }}>
          {msToClock(index >= 0 ? steps[index].offsetMs : 0)}
        </span>

        {/* 进度条 */}
        <div
          ref={progressRef}
          onClick={onSeek}
          className="flex-1 relative cursor-pointer"
          style={{ height: 6, borderRadius: 3, background: "var(--bg-base)", minWidth: 80 }}
          title="点击跳转"
        >
          <div
            style={{
              position: "absolute",
              inset: 0,
              width: `${Math.min(100, Math.max(0, pct))}%`,
              borderRadius: 3,
              background: "var(--color-primary)",
              transition: "width 120ms linear",
            }}
          />
        </div>

        <span className="text-micro font-mono text-text-tertiary shrink-0" style={{ width: 42 }}>
          {msToClock(totalMs)}
        </span>

        <select
          value={String(speed)}
          onChange={(e) => setReplaySpeed(parseFloat(e.target.value) || 1)}
          className="text-micro font-mono px-1 py-0.5 rounded cursor-pointer"
          style={{
            background: "var(--bg-base)",
            color: "var(--text-secondary)",
            border: "1px solid var(--border-default)",
          }}
          title="播放速度"
        >
          {SPEEDS.map((sp) => (
            <option key={sp} value={String(sp)}>
              {sp}x
            </option>
          ))}
        </select>

        <span className="text-micro font-mono text-text-tertiary shrink-0" style={{ minWidth: 74, textAlign: "right" }}>
          {total === 0 ? "无可重放日志" : `${index + 1} / ${total} 条`}
        </span>

        <button
          onClick={() => setReplayOpen(false)}
          className="p-1 text-text-tertiary hover:text-text-primary cursor-pointer transition-colors"
          title="关闭重放"
        >
          <X size={14} />
        </button>
      </div>

      {/* ── 当前步 + 快照 ── */}
      <div
        className="flex items-start gap-3 px-4 pb-2"
        style={{ borderTop: "1px solid var(--border-default)", paddingTop: 8 }}
      >
        <div className="flex-1 min-w-0">
          <div className="text-micro text-text-tertiary mb-1">当前步</div>
          {current ? (
            <div
              className="text-xs font-mono p-2 rounded break-all whitespace-pre-wrap"
              style={{
                background: "var(--bg-base)",
                border: "1px solid var(--border-default)",
                color: current.isError ? "var(--state-error-text)" : "var(--text-secondary)",
                maxHeight: 76,
                overflowY: "auto",
              }}
            >
              [{current.timestamp}] [{current.level}] [{current.source}] {current.text}
            </div>
          ) : (
            <div className="text-xs text-text-tertiary font-mono py-2">
              {total === 0 ? "当前筛选下没有可重放的日志" : "点击播放开始重放"}
            </div>
          )}
        </div>

        <div style={{ width: 200 }} className="shrink-0">
          <div className="flex items-center justify-between mb-1">
            <span className="text-micro text-text-tertiary flex items-center gap-1">
              <Camera size={11} /> 截图快照
            </span>
            {caseId && (
              <button
                onClick={() => void loadShot()}
                className="text-micro text-text-tertiary hover:text-text-primary cursor-pointer"
                title="重新加载截图"
              >
                刷新
              </button>
            )}
          </div>
          <div
            className="rounded flex items-center justify-center text-micro text-text-tertiary text-center px-2"
            style={{
              background: "var(--bg-base)",
              border: "1px solid var(--border-default)",
              height: 76,
              overflow: "hidden",
            }}
          >
            {!caseId ? (
              <span>无用例上下文<br />（深链需带 case_id）</span>
            ) : shot.loading ? (
              <span>加载中…</span>
            ) : shot.dataUrl ? (
              <img
                src={shot.dataUrl}
                alt={`用例 ${caseId} 执行截图`}
                style={{ maxHeight: "100%", maxWidth: "100%", objectFit: "contain" }}
              />
            ) : (
              <span>{shot.error || "该用例无截图"}</span>
            )}
          </div>
          <div className="text-micro text-text-tertiary mt-1 leading-tight">
            DOM 快照：数据源已于 2026-08-05 下线，暂不可用
          </div>
        </div>
      </div>
    </div>
  );
}
