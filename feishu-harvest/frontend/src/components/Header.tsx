import { useEffect, useState } from "react";
import { Dot } from "./ui";
import { api } from "../api";

export function Header({ onSettings }: { onSettings: () => void }) {
  const [connected, setConnected] = useState(false);
  const [login, setLogin] = useState<boolean | null>(null);

  useEffect(() => {
    let alive = true;
    const tick = async () => {
      try {
        const s = await api.status();
        if (!alive) return;
        setConnected(true);
        setLogin(s.login);
      } catch {
        if (alive) setConnected(false);
      }
    };
    tick();
    const t = setInterval(tick, 5000);
    return () => { alive = false; clearInterval(t); };
  }, []);

  return (
    <header className="flex items-center justify-between px-6 py-4 border-b border-ink-700 bg-white/70 backdrop-blur">
      <div className="flex items-center gap-3">
        <div className="size-9 rounded-xl bg-gradient-to-br from-lark to-lark-soft
          grid place-items-center shadow-[0_6px_20px_-6px_rgba(51,112,255,0.5)]">
          <svg viewBox="0 0 24 24" className="size-5 text-white" fill="none"
            stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M12 3v18M5 10c2-1 5-1 7 1 2-2 5-2 7-1M5 15c2-1 5-1 7 1 2-2 5-2 7-1" />
          </svg>
        </div>
        <div>
          <h1 className="text-slate-900 font-semibold leading-tight">Feishu Harvest</h1>
          <p className="text-xs text-slate-500">飞书知识库采撷 · v2.0</p>
        </div>
      </div>
      <div className="flex items-center gap-2 text-xs">
        <Dot tone={connected ? "bg-emerald-500" : "bg-rose-500"} pulse={!connected} />
        <span className="text-slate-500">{connected ? "服务已连接" : "服务未连接"}</span>
        <span className="mx-2 h-4 w-px bg-ink-700" />
        <Dot tone={login === true ? "bg-emerald-500" : login === false ? "bg-amber-500" : "bg-slate-300"} />
        <span className="text-slate-500">
          {login === true ? "飞书已登录" : login === false ? "未登录" : "登录态未知"}
        </span>
        <button className="btn-ghost !px-3 !py-1.5 ml-3" onClick={onSettings}>
          <svg viewBox="0 0 24 24" className="size-4" fill="none" stroke="currentColor"
            strokeWidth="2"><circle cx="12" cy="12" r="3" />
            <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09a1.65 1.65 0 0 0-1-1.51 1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09a1.65 1.65 0 0 0 1.51-1 1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33h.01a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51h.01a1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82v.01a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
          </svg>
          设置
        </button>
      </div>
    </header>
  );
}
