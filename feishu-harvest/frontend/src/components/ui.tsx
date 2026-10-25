import React from "react";

export function ProgressRing({ value, size = 84, stroke = 7 }: {
  value: number; size?: number; stroke?: number;
}) {
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;
  const pct = Math.max(0, Math.min(1, value));
  return (
    <svg width={size} height={size} className="-rotate-90">
      <circle cx={size / 2} cy={size / 2} r={r} fill="none"
        stroke="rgba(100,116,139,0.16)" strokeWidth={stroke} />
      <circle cx={size / 2} cy={size / 2} r={r} fill="none"
        stroke="url(#ring)" strokeWidth={stroke} strokeLinecap="round"
        strokeDasharray={c} strokeDashoffset={c * (1 - pct)}
        style={{ transition: "stroke-dashoffset .6s ease" }} />
      <defs>
        <linearGradient id="ring" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="#3370ff" />
          <stop offset="100%" stopColor="#6d9bff" />
        </linearGradient>
      </defs>
      <text x="50%" y="50%" dominantBaseline="central" textAnchor="middle"
        transform={`rotate(90 ${size / 2} ${size / 2})`}
        className="fill-slate-800 font-mono font-semibold" style={{ fontSize: size / 4 }}>
        {Math.round(pct * 100)}%
      </text>
    </svg>
  );
}

export function Dot({ tone, pulse }: { tone: string; pulse?: boolean }) {
  return (
    <span className={`inline-block size-2 rounded-full ${tone} ${pulse ? "animate-pulse-soft" : ""}`} />
  );
}

export function Stat({ label, value, sub, accent }: {
  label: string; value: React.ReactNode; sub?: string; accent?: boolean;
}) {
  return (
    <div className="rounded-xl bg-ink-850 border border-ink-700 px-4 py-3">
      <div className="label">{label}</div>
      <div className={`mt-1 font-mono text-xl ${accent ? "text-lark-soft" : "text-slate-900"}`}>
        {value}
      </div>
      {sub && <div className="text-xs text-slate-500 mt-0.5">{sub}</div>}
    </div>
  );
}

export function Spinner({ className = "size-4" }: { className?: string }) {
  return (
    <svg className={`animate-spin ${className}`} viewBox="0 0 24 24" fill="none">
      <circle cx="12" cy="12" r="10" stroke="currentColor" strokeOpacity=".25" strokeWidth="4" />
      <path d="M22 12a10 10 0 0 0-10-10" stroke="currentColor" strokeWidth="4" strokeLinecap="round" />
    </svg>
  );
}

export const PHASE_LABELS: Record<string, string> = {
  prepare: "准备",
  discover: "发现",
  discover_done: "发现完成",
  extract: "提取",
  export: "导出",
  pdf: "PDF",
};
