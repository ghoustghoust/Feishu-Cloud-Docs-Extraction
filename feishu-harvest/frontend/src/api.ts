import type { Config, TreeNode } from "./types";

async function req<T = any>(url: string, init?: RequestInit): Promise<T> {
  const r = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  return r.json();
}

export const api = {
  health: () => req("/api/health"),
  status: () => req("/api/status"),
  config: () => req<Config>("/api/config"),
  setConfig: (patch: Partial<Config>) =>
    req("/api/config", { method: "POST", body: JSON.stringify(patch) }),
  loginStart: () => req("/api/login/start", { method: "POST", body: "{}" }),
  loginClear: () => req("/api/login/clear", { method: "POST", body: "{}" }),
  treeRoots: () => req<{ roots: { token: string; count: number }[] }>("/api/tree/roots"),
  tree: (root: string) =>
    req<{ tree: TreeNode[]; flat: TreeNode[]; total: number }>(
      `/api/tree?root=${encodeURIComponent(root)}`,
    ),
  crawlStart: (body: { url: string; tokens?: string[]; fresh?: boolean; tabs?: number; export_format?: string; output_dir?: string }) =>
    req("/api/crawl/start", { method: "POST", body: JSON.stringify(body) }),
  crawlStop: () => req("/api/crawl/stop", { method: "POST", body: "{}" }),
};
