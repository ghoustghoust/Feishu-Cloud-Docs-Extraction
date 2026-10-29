import { useEffect, useState } from "react";
import { Header } from "./components/Header";
import { TaskPanel } from "./components/TaskPanel";
import { TreePanel } from "./components/TreePanel";
import { LiveLog, RecentPages } from "./components/LiveLog";
import { SettingsDrawer } from "./components/SettingsDrawer";
import { api } from "./api";
import { useFeed } from "./store";

function rootOf(url: string | undefined): string {
  if (!url) return "";
  const m = url.match(/\/wiki\/([^/?#]+)/);
  return m ? m[1] : "";
}

export default function App() {
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [rootToken, setRootToken] = useState("");
  const [roots, setRoots] = useState<{ token: string; count: number }[]>([]);
  const [refreshKey, setRefreshKey] = useState(0);
  const runUrl = useFeed((s) => s.run.url);
  const runState = useFeed((s) => s.run.state);

  // 服务重启后 run 状态丢失 → 启动时从库中恢复知识库根 (修复树面板空白)
  useEffect(() => {
    api.treeRoots().then((r) => {
      const sorted = [...r.roots].sort((a, b) => b.count - a.count);
      setRoots(sorted);
      setRootToken((cur) => cur || sorted[0]?.token || "");
    }).catch(() => {});
  }, []);

  useEffect(() => {
    const r = rootOf(runUrl);
    if (r && r !== rootToken) setRootToken(r);
  }, [runUrl]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (runState === "done" || runState === "error") setRefreshKey((k) => k + 1);
  }, [runState]);

  return (
    <div className="h-full flex flex-col">
      <Header onSettings={() => setSettingsOpen(true)} />
      <main className="flex-1 flex flex-col xl:grid xl:grid-cols-[minmax(0,1fr)_380px] gap-4 p-4 min-h-0
        overflow-auto xl:overflow-hidden">
        {/* 左列: 任务 + 最近页面 + 日志 */}
        <div className="flex flex-col gap-4 shrink-0 xl:shrink xl:min-h-0 pr-0.5 xl:overflow-auto">
          <TaskPanel onNeedTree={() => setRefreshKey((k) => k + 1)} />
          <section>
            <h2 className="label mb-2 px-1">最近页面</h2>
            <RecentPages />
          </section>
          <section className="flex-1 flex flex-col min-h-[220px]">
            <LiveLog />
          </section>
        </div>
        {/* 右列: 知识库树 */}
        <div className="shrink-0 xl:shrink min-h-[420px] xl:min-h-0">
          <TreePanel rootToken={rootToken} refreshKey={refreshKey}
            roots={roots} onRootChange={setRootToken} />
        </div>
      </main>
      <SettingsDrawer open={settingsOpen} onClose={() => setSettingsOpen(false)} />
    </div>
  );
}
