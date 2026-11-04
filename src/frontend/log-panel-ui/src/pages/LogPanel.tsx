import { useLogStream } from "@/hooks/useLogStream";
import Sidebar from "@/components/Sidebar";
import TopBar from "@/components/TopBar";
import FilterTabs from "@/components/FilterTabs";
import SearchBar from "@/components/SearchBar";
import LogStream from "@/components/LogStream";
import StatusBar from "@/components/StatusBar";
import LogDetail from "@/components/LogDetail";
import StepReplay from "@/components/StepReplay";

export default function LogPanel() {
  // 启动日志流（mock 模式加载剧本，真实模式拉取历史 + SSE 订阅）
  const { sseStatus } = useLogStream();

  return (
    <div className="flex h-screen overflow-hidden">
      <Sidebar />
      <main className="flex-1 flex flex-col overflow-hidden" style={{ background: "var(--bg-base)" }}>
        <TopBar />
        <FilterTabs />
        <SearchBar />
        <div className="flex-1 flex overflow-hidden">
          <LogStream />
          <LogDetail />
        </div>
        {/* T04/P1-3：步骤重放播放器，未开启时不渲染任何 DOM */}
        <StepReplay />
        <StatusBar sseStatus={sseStatus} />
      </main>
    </div>
  );
}
