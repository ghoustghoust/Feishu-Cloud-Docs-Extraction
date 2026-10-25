import { useSyncExternalStore } from "react";
import type { Ev, PageStatus, RunState, Stats } from "./types";

/**
 * feed — SSE 事件 → 全局状态的外部 store。
 * useFeed(selector) 订阅任意切片; 断线自动重连 (EventSource 原生行为)。
 */
export interface FeedState {
  connected: boolean;
  run: RunState;
  stats: Stats | null;
  pages: Map<string, PageStatus>; // token → 最新状态
  recent: PageStatus[];           // 最近完成的页面 (倒序, 最多 12)
  logs: { ts: number; text: string; level: "info" | "warn" | "error" }[];
  loginState?: string;
}

const MAX_LOGS = 300;
const MAX_RECENT = 12;

class Feed {
  state: FeedState = {
    connected: false,
    run: { state: "idle" },
    stats: null,
    pages: new Map(),
    recent: [],
    logs: [],
  };
  private listeners = new Set<() => void>();
  private es: EventSource | null = null;

  subscribe = (fn: () => void) => {
    this.listeners.add(fn);
    this.connect();
    return () => this.listeners.delete(fn);
  };

  getSnapshot = () => this.state;

  private emit() {
    this.state = { ...this.state };
    this.listeners.forEach((fn) => fn());
  }

  private log(text: string, level: "info" | "warn" | "error" = "info") {
    this.state.logs = [...this.state.logs, { ts: Date.now(), text, level }].slice(-MAX_LOGS);
  }

  connect() {
    if (this.es) return;
    this.es = new EventSource("/api/events");
    this.es.onopen = () => {
      this.state.connected = true;
      this.emit();
    };
    this.es.onerror = () => {
      this.state.connected = false;
      this.emit();
    };
    this.es.onmessage = (e) => {
      try {
        this.handle(JSON.parse(e.data) as Ev);
      } catch {
        /* 忽略坏帧 */
      }
    };
  }

  private handle(ev: Ev) {
    const s = this.state;
    switch (ev.type) {
      case "run": {
        if (ev.state === "running") {
          s.run = { state: "running", phase: ev.phase, url: ev.url ?? s.run.url,
                    output: ev.output ?? s.run.output, format: ev.format ?? s.run.format,
                    total: ev.total ?? s.run.total };
          if (ev.phase === "prepare" || ev.phase === "discover") s.pages = new Map();
          this.log(`▶ 阶段: ${ev.phase}${ev.total ? ` · ${ev.total} 节点` : ""}`);
        } else if (ev.state === "done") {
          s.run = { ...s.run, state: "done", elapsed_sec: ev.elapsed_sec };
          this.log(`✔ 完成 · 保存 ${ev.pages_saved} 页 · ${ev.total_chars?.toLocaleString()} 字 · ${ev.elapsed_sec}s`);
        } else if (ev.state === "error") {
          s.run = { ...s.run, state: "error", error: ev.error };
          this.log(`✖ 任务错误: ${ev.error}`, "error");
        } else if (ev.state === "need_login") {
          s.run = { ...s.run, state: "need_login" };
          this.log("登录态已失效, 请重新扫码", "warn");
        }
        break;
      }
      case "discover": {
        if (ev.stage === "done") this.log(`发现完成: ${ev.total} 个节点`);
        else if (ev.stage === "seed") this.log(`增量发现: 继承 ${ev.total} 个已知节点`);
        else if (ev.stage === "round") this.log(`BFS 第 ${ev.round} 轮: +${ev.new} → 共 ${ev.total}`);
        else if (ev.stage === "resume") this.log(`续爬: 缓存 ${ev.cached} · 待爬 ${ev.todo}`);
        else if (ev.stage === "subset") this.log(`子集模式: ${ev.total} 个节点`);
        else if (ev.stage === "visit") {
          s.run = { ...s.run, total: ev.total };
        }
        break;
      }
      case "page": {
        const ps: PageStatus = {
          token: ev.token, title: ev.title, status: ev.status,
          chars: ev.chars, file: ev.file, error: ev.error,
        };
        const next = new Map(s.pages);
        next.set(ev.token, ps);
        s.pages = next;
        if (["saved", "section", "failed", "skipped", "cached"].includes(ev.status)) {
          s.recent = [ps, ...s.recent].slice(0, MAX_RECENT);
        }
        if (ev.status === "failed") this.log(`✖ ${ev.title}: ${ev.error ?? "失败"}`, "error");
        break;
      }
      case "stats": {
        s.stats = ev as unknown as Stats;
        break;
      }
      case "login": {
        s.loginState = ev.state;
        if (ev.state === "ok") this.log("登录成功");
        break;
      }
    }
    this.emit();
  }
}

export const feed = new Feed();

export function useFeed<T>(selector: (s: FeedState) => T): T {
  return useSyncExternalStore(feed.subscribe, () => selector(feed.getSnapshot()));
}
