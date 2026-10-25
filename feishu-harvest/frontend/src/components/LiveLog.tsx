import { useEffect, useRef } from "react";
import { useFeed } from "../store";

const TONE: Record<string, string> = {
  info: "text-slate-500",
  warn: "text-amber-600",
  error: "text-rose-600",
};

/** 实时日志: SSE 事件流的结构化呈现 */
export function LiveLog() {
  const logs = useFeed((s) => s.logs);
  const connected = useFeed((s) => s.connected);
  const boxRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    boxRef.current?.scrollTo({ top: boxRef.current.scrollHeight });
  }, [logs.length]);

  return (
    <div className="card flex flex-col min-h-0">
      <div className="px-4 py-2.5 border-b border-ink-700/50 flex items-center gap-2">
        <h2 className="text-sm font-semibold text-slate-800">实时事件流</h2>
        <span className={`text-[10px] font-mono ${connected ? "text-emerald-600" : "text-rose-500"}`}>
          {connected ? "SSE ●" : "SSE ○"}
        </span>
        <span className="ml-auto text-xs text-slate-400 font-mono">{logs.length} 条</span>
      </div>
      <div ref={boxRef} className="flex-1 overflow-auto px-4 py-2 font-mono text-[11px]
        leading-relaxed min-h-[120px]">
        {logs.length === 0 && (
          <div className="text-slate-400 py-6 text-center">等待事件…</div>
        )}
        {logs.map((l, i) => (
          <div key={i} className="flex gap-2.5 py-0.5">
            <span className="text-slate-400 shrink-0">
              {new Date(l.ts).toLocaleTimeString("zh-CN", { hour12: false })}
            </span>
            <span className={TONE[l.level]}>{l.text}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

/** 最近页面: 每页结果的卡片流 */
export function RecentPages() {
  const recent = useFeed((s) => s.recent);
  if (!recent.length) return null;
  const tone: Record<string, string> = {
    saved: "border-emerald-200 text-emerald-600",
    section: "border-lark/30 text-lark-soft",
    cached: "border-ink-600 text-slate-500",
    failed: "border-rose-200 text-rose-600",
    skipped: "border-ink-600 text-slate-500",
    not_logged_in: "border-amber-200 text-amber-600",
  };
  return (
    <div className="space-y-1.5">
      {recent.slice(0, 8).map((p) => (
        <div key={p.token + p.status}
          className={`flex items-center gap-2 rounded-lg border bg-ink-850 px-3 py-1.5
            ${tone[p.status] ?? "border-ink-700"}`}>
          <span className="text-[10px] font-mono shrink-0 uppercase">{p.status}</span>
          <span className="text-xs text-slate-700 truncate flex-1">{p.title}</span>
          {p.chars != null && p.chars > 0 && (
            <span className="text-[10px] text-slate-400 font-mono shrink-0">
              {p.chars.toLocaleString()} 字
            </span>
          )}
        </div>
      ))}
    </div>
  );
}
