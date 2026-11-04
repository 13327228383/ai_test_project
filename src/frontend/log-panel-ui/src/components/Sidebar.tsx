import { useState } from "react";
import {
  Play,
  ChevronDown,
  ChevronRight,
  Activity,
  BarChart3,
  FileText,
  Folder,
  User,
  Settings,
  LogOut,
  type LucideIcon,
} from "lucide-react";
import { useLogStore } from "@/store/logStore";

const STEPS = [
  { n: 1, label: "Step 1 文档拉取", color: "var(--step1-color)" },
  { n: 2, label: "Step 2 需求拆解", color: "var(--step2-color)" },
  { n: 3, label: "Step 3 用例建模", color: "var(--step3-color)" },
  { n: 4, label: "Step 4 检查执行", color: "var(--step4-color)" },
  { n: 5, label: "Step 5 报告生成", color: "var(--step5-color)" },
] as const;

interface NavItem {
  key: string;
  label: string;
  icon: LucideIcon;
  href: string;
  active?: boolean;
}

const EXEC_REPORT_ITEMS: NavItem[] = [
  { key: "pipeline", label: "流水线执行", icon: Activity, href: "/pipeline" },
  { key: "dashboard", label: "控制台", icon: BarChart3, href: "/dashboard" },
  { key: "visual", label: "可视化报告", icon: BarChart3, href: "/charts" },
  { key: "deep", label: "深度指标", icon: BarChart3, href: "/metrics-page" },
  { key: "docs", label: "文档中心", icon: FileText, href: "/docs" },
  { key: "logs", label: "日志面板", icon: FileText, href: "/log-panel", active: true },
];

function CollapsibleSection({
  title,
  icon: Icon,
  defaultOpen = false,
  children,
}: {
  title: string;
  icon: LucideIcon;
  defaultOpen?: boolean;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="pt-1">
      <button
        onClick={() => setOpen(!open)}
        className="flex items-center gap-2 w-full px-4 py-1.5 text-small text-text-secondary cursor-pointer transition-all hover:text-text-primary hover:bg-white/[0.03]"
      >
        <Icon size={14} style={{ opacity: 0.7 }} />
        <span className="flex-1 text-left">{title}</span>
        {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
      </button>
      {open && <div className="pb-1">{children}</div>}
    </div>
  );
}

function NavItemRow({ item }: { item: NavItem }) {
  const Icon = item.icon;
  return (
    <a
      href={item.href}
      className="flex items-center gap-2 px-4 py-1.5 pl-10 text-small cursor-pointer transition-all"
      style={{
        color: item.active ? "var(--color-primary)" : "var(--text-secondary)",
        background: item.active ? "var(--color-primary-soft)" : "transparent",
      }}
      title={item.label}
    >
      <Icon size={14} style={{ opacity: 0.7 }} />
      <span>{item.label}</span>
    </a>
  );
}

export default function Sidebar() {
  const collapsed = useLogStore((s) => s.sidebarCollapsed);
  const setCollapsed = useLogStore((s) => s.setSidebarCollapsed);

  const width = collapsed ? "var(--sidebar-collapsed)" : "var(--sidebar-width)";
  const showLabels = !collapsed;

  return (
    <>
      <aside
        className="sidebar flex flex-col h-screen overflow-y-auto overflow-x-hidden border-r shrink-0 transition-all duration-200"
        style={{
          width,
          background: "var(--sidebar-bg)",
          borderRight: "var(--sidebar-border)",
        }}
      >
        {/* 管理员标识 */}
        <div
          className="flex items-center gap-2 px-4 py-3"
          style={{ borderBottom: "var(--sidebar-border)" }}
        >
          <div
            className="flex items-center justify-center text-xs text-text-secondary shrink-0"
            style={{
              width: 28,
              height: 28,
              borderRadius: "var(--radius-full)",
              background: "var(--bg-elevated)",
              border: "1px solid var(--border-default)",
            }}
          >
            管
          </div>
          <span className="text-small font-medium text-text-primary">管理员</span>
          <span
            className="ml-auto text-micro"
            style={{
              color: "var(--state-success-text)",
              background: "var(--state-success-soft)",
              border: "1px solid var(--state-success-border)",
              padding: "0px 5px",
              borderRadius: "var(--radius-full)",
            }}
          >
            管理员
          </span>
        </div>

        {/* 当前环境 */}
        {showLabels && (
          <div className="px-4 py-3" style={{ borderBottom: "var(--sidebar-border)" }}>
            <div className="mb-1 text-micro font-medium uppercase tracking-wider text-text-tertiary">
              当前环境
            </div>
            <select
              className="custom-select w-full text-xs text-text-secondary outline-none cursor-pointer"
              style={{
                height: "var(--control-height-sm)",
                background: "var(--bg-elevated)",
                border: "1px solid var(--border-default)",
                borderRadius: "var(--radius-sm)",
                padding: "0 28px 0 8px",
                fontFamily: "var(--font-mono)",
              }}
              defaultValue="acceptance_demo"
            >
              <option>acceptance_demo</option>
              <option>TEST--V0.0.1</option>
              <option>DEV--V0.0.2</option>
            </select>
          </div>
        )}

        {/* 流水线步骤 */}
        <div className="pt-3 pb-1">
          {showLabels && (
            <div className="px-4 mb-1 text-micro font-medium uppercase tracking-wider text-text-tertiary">
              流水线步骤
            </div>
          )}
          {STEPS.map((s) => (
            <a
              key={s.n}
              href="#"
              onClick={(e) => e.preventDefault()}
              className="flex items-center gap-2 px-4 py-1 text-small text-text-secondary cursor-pointer transition-all hover:text-text-primary hover:bg-white/[0.03]"
              title={s.label}
            >
              <span
                className="rounded-full shrink-0"
                style={{ width: 7, height: 7, background: s.color }}
              />
              {showLabels && (
                <>
                  <span className="flex-1">{s.label}</span>
                  <span className="text-micro text-text-tertiary">等待</span>
                </>
              )}
            </a>
          ))}
        </div>

        {/* 一键执行 */}
        <div className="pt-2 pb-2 px-4">
          <button
            className="flex items-center justify-center gap-1.5 w-full text-small font-semibold text-text-inverse cursor-pointer transition-opacity hover:opacity-90"
            style={{
              padding: "8px",
              background: "linear-gradient(135deg, var(--color-primary), #00b894)",
              border: "none",
              borderRadius: "var(--radius-md)",
            }}
            title="一键执行"
          >
            <Play size={14} fill="currentColor" />
            {showLabels && "一键执行"}
          </button>
        </div>

        {/* 执行与报告 */}
        {showLabels && (
          <CollapsibleSection title="执行与报告" icon={Activity} defaultOpen={true}>
            {EXEC_REPORT_ITEMS.map((item) => (
              <NavItemRow key={item.key} item={item} />
            ))}
          </CollapsibleSection>
        )}

        {/* 项目 */}
        {showLabels && (
          <CollapsibleSection title="项目" icon={Folder}>
            <div className="px-4 py-2 text-micro text-text-tertiary">暂无项目</div>
          </CollapsibleSection>
        )}

        {/* 用户配置 */}
        {showLabels && (
          <CollapsibleSection title="用户配置" icon={User}>
            <div className="px-4 py-2 text-micro text-text-tertiary">用户设置</div>
          </CollapsibleSection>
        )}

        {/* 系统管理 */}
        {showLabels && (
          <CollapsibleSection title="系统管理" icon={Settings}>
            <div className="px-4 py-2 text-micro text-text-tertiary">系统设置</div>
          </CollapsibleSection>
        )}

        {/* 底部退出登录 */}
        <div
          className="mt-auto px-4 py-3"
          style={{ borderTop: "var(--sidebar-border)" }}
        >
          <button
            className="flex items-center gap-2 text-xs text-text-tertiary hover:text-red-400 cursor-pointer py-1 transition-colors w-full"
            title="退出登录"
          >
            <LogOut size={14} />
            {showLabels && <span>退出登录</span>}
          </button>
        </div>
      </aside>

      {/* 桌面端折叠按钮 */}
      <button
        onClick={() => setCollapsed(!collapsed)}
        className="hidden md:flex fixed top-1/2 -translate-y-1/2 z-50 items-center justify-center text-text-tertiary hover:text-primary transition-colors"
        style={{
          left: `calc(${width} - 10px)`,
          width: 20,
          height: 36,
          background: "var(--bg-elevated)",
          border: "1px solid var(--border-default)",
          borderRadius: "var(--radius-sm)",
        }}
        title={collapsed ? "展开侧边栏" : "折叠侧边栏"}
      >
        {collapsed ? <ChevronRight size={14} /> : <ChevronDown size={14} />}
      </button>
    </>
  );
}
