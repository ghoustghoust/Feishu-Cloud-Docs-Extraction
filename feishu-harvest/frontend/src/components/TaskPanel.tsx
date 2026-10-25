import { useState } from "react";
import { api } from "../api";
import { PHASE_LABELS, ProgressRing, Spinner, Stat } from "./ui";
import { useFeed } from "../store";
import type { RunState, Stats } from "../types";

/** 任务卡片: URL 输入 + 启停 + 阶段流 + 进度环 + 统计格 + 单次运行选项 */
export function TaskPanel({ onNeedTree }: { onNeedTree: () => void }) {
  const [url, setUrl] = useState("");
  const [fresh, setFresh] = useState(false);
  const [busyLocal, setBusyLocal] = useState(false);
  const run = useFeed((s) => s.run);
  const stats = useFeed((s) => s.stats);
  const connected = useFeed((s) => s.connected);
  const running = run.state === "running";

  const start = async () => {
    if (!url.trim()) return;
    setBusyLocal(true);
    try {
      await api.crawlStart({ url: url.trim(), fresh });
      onNeedTree();
    } finally {
      setBusyLocal(false);
    }
  };
  const stop = async () => api.crawlStop();

  return (
    <div className="card p-5 space-y-5">
      <div className="flex items-center gap-3">
        <ProgressRing value={progressOf(run, stats)} />
        <div className="flex-1 min-w-0">
          <PhaseFlow run={run} />
          <div className="mt-3 flex gap-2">
            <input className="input font-mono !text-xs"
              placeholder="粘贴飞书知识库/文档链接 · https://xxx.feishu.cn/wiki/…"
              value={url} onChange={(e) => setUrl(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && !running && start()} />
            {running ? (
              <button className="btn-danger shrink-0" onClick={stop}>停止</button>
            ) : (
              <button className="btn-primary shrink-0" disabled={!url.trim() || !connected || busyLocal}
                onClick={start}>
                {busyLocal ? <Spinner /> : "▶"} 开始采撷
              </button>
            )}
          </div>
          <div className="mt-2 flex items-center gap-4 text-xs text-slate-500">
            <label className="flex items-center gap-1.5 cursor-pointer select-none">
              <input type="checkbox" className="accent-lark size-3.5"
                checked={fresh} onChange={(e) => setFresh(e.target.checked)} />
              全量重爬 (忽略缓存, 默认增量续爬)
            </label>
            {run.state === "running" && (
              <span className="text-lark-soft">运行中 · 可随时停止, 进度已实时落盘</span>
            )}
          </div>
          {run.state === "need_login" && (
            <div className="mt-3 rounded-xl bg-amber-50 border border-amber-200 px-4 py-2.5
              text-sm text-amber-700 flex items-center justify-between">
              登录态已失效, 请在弹出的浏览器中重新扫码
              <button className="btn-ghost !py-1 !px-2.5 !text-xs"
                onClick={() => api.loginStart()}>重新登录</button>
            </div>
          )}
          {run.state === "error" && (
            <div className="mt-3 rounded-xl bg-rose-50 border border-rose-200
              px-4 py-2.5 text-sm text-rose-600">任务错误: {run.error}</div>
          )}
          {run.state === "done" && (
            <div className="mt-3 rounded-xl bg-emerald-50 border border-emerald-200
              px-4 py-2.5 text-sm text-emerald-700">
              完成 · {run.elapsed_sec}s
              {run.output && <span className="ml-2 text-emerald-600/70 font-mono text-xs">{run.output}</span>}
            </div>
          )}
        </div>
      </div>
      {stats && <StatsGrid stats={stats} total={run.total ?? stats.total} />}
    </div>
  );
}

function progressOf(run: RunState, stats: Stats | null): number {
  if (run.state === "done") return 1;
  if (!stats || !stats.total) return 0;
  const fin = stats.done + stats.failed + stats.skipped;
  return fin / Math.max(stats.total, 1);
}

const PHASES = ["prepare", "discover", "extract", "export"] as const;

function PhaseFlow({ run }: { run: RunState }) {
  const order: Record<string, number> = { prepare: 0, discover: 1, discover_done: 1, extract: 2, export: 3, pdf: 3 };
  const cur = run.state === "done" ? 4 : run.state === "running" ? (order[run.phase ?? "prepare"] ?? 0) : -1;
  return (
    <div className="flex items-center gap-1.5 text-xs mb-3">
      {PHASES.map((p, i) => (
        <div key={p} className="flex items-center gap-1.5">
          <div className={`flex items-center gap-1.5 rounded-full px-2.5 py-1 border
            ${i < cur ? "border-emerald-200 text-emerald-600 bg-emerald-50"
              : i === cur ? "border-lark/50 text-lark-soft bg-lark/5"
                : "border-ink-700 text-slate-400 bg-ink-850"}`}>
            {i === cur && run.state === "running" && <Spinner className="size-3" />}
            {i < cur && <span>✓</span>}
            {PHASE_LABELS[p]}
          </div>
          {i < PHASES.length - 1 && <span className="w-3 h-px bg-ink-600" />}
        </div>
      ))}
      <span className="ml-auto text-slate-400 font-mono text-[11px] truncate max-w-[180px]">
        {run.output}
      </span>
    </div>
  );
}

function StatsGrid({ stats, total }: { stats: Stats; total: number }) {
  const fin = stats.done + stats.failed + stats.skipped;
  return (
    <div className="grid grid-cols-3 md:grid-cols-6 gap-2.5">
      <Stat label="已完成" value={stats.done} sub={`共 ${total || stats.total} 节点`} accent />
      <Stat label="缓存跳过" value={stats.cached ?? 0} sub="增量同步" />
      <Stat label="失败" value={stats.failed} sub={stats.skipped ? `跳过 ${stats.skipped}` : undefined} />
      <Stat label="字符量" value={fmtNum(stats.chars)} />
      <Stat label="速率" value={stats.rate_per_min ? `${stats.rate_per_min.toFixed(1)}/min` : "—"} />
      <Stat label="预计剩余"
        value={stats.eta_sec != null ? fmtEta(stats.eta_sec) : "—"}
        sub={`${fin}/${stats.total || "?"} 已处理`} />
    </div>
  );
}

function fmtNum(n: number): string {
  return n >= 10000 ? `${(n / 10000).toFixed(1)} 万` : n.toLocaleString();
}
function fmtEta(s: number): string {
  if (s < 90) return `${s}s`;
  if (s < 5400) return `${Math.round(s / 60)} 分钟`;
  return `${(s / 3600).toFixed(1)} 小时`;
}
