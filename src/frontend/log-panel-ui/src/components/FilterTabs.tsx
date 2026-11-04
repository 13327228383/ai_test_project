import { useShallow } from "zustand/react/shallow";
import { useLogStore, selectTabCounts } from "@/store/logStore";
import type { TabDef } from "@/types/log";

const STEP_COLORS: Record<string, string> = {
  step1: "var(--step1-color)",
  step2: "var(--step2-color)",
  step3: "var(--step3-color)",
  step4: "var(--step4-color)",
  step5: "var(--step5-color)",
};

const STEP_SOFT: Record<string, string> = {
  step1: "var(--step1-soft)",
  step2: "var(--step2-soft)",
  step3: "var(--step3-soft)",
  step4: "var(--step4-soft)",
  step5: "var(--step5-soft)",
};

const TABS: TabDef[] = [
  { key: "all", label: "全部" },
  { key: "step1", label: "Step1" },
  { key: "step2", label: "Step2" },
  { key: "step3", label: "Step3" },
  { key: "step4", label: "Step4" },
  { key: "step5", label: "Step5" },
  { key: "system", label: "系统" },
  { key: "error", label: "错误" },
];

export default function FilterTabs() {
  const activeTab = useLogStore((s) => s.activeTab);
  const setTab = useLogStore((s) => s.setTab);
  const counts = useLogStore(useShallow(selectTabCounts));

  return (
    <div
      className="flex items-center gap-0 px-5 shrink-0 overflow-x-auto no-scrollbar"
      style={{
        borderBottom: "1px solid var(--border-default)",
        background: "var(--bg-surface)",
      }}
    >
      {TABS.map((t) => {
        const active = activeTab === t.key;
        const stepColor = STEP_COLORS[t.key];
        const stepSoft = STEP_SOFT[t.key];
        const badgeColor = stepColor || (active ? "var(--color-primary)" : "var(--text-tertiary)");
        const badgeBg = stepSoft || (active ? "var(--color-primary-soft)" : "rgba(148, 163, 184, 0.1)");
        return (
          <div
            key={t.key}
            onClick={() => setTab(t.key)}
            className="flex items-center gap-1 text-small cursor-pointer whitespace-nowrap select-none transition-all py-2 px-3"
            style={{
              color: active ? "var(--color-primary)" : "var(--text-tertiary)",
              borderBottom: active
                ? "2px solid var(--color-primary)"
                : "2px solid transparent",
            }}
          >
            <span>{t.label}</span>
            <span
              className="font-mono"
              style={{
                fontSize: "var(--fs-micro)",
                background: badgeBg,
                color: badgeColor,
                padding: "0 5px",
                borderRadius: "var(--radius-full)",
              }}
            >
              {counts[t.key]}
            </span>
          </div>
        );
      })}
    </div>
  );
}
