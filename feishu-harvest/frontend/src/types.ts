// ---------- 后端事件模型 ----------
export type Ev = Record<string, any> & { type: string; ts?: number };

export interface RunState {
  state: "idle" | "running" | "done" | "error" | "need_login" | "stopped";
  phase?: "prepare" | "discover" | "discover_done" | "extract" | "export" | "pdf";
  url?: string;
  output?: string;
  format?: string;
  total?: number;
  error?: string;
  elapsed_sec?: number;
}

export interface Stats {
  done: number;
  failed: number;
  skipped: number;
  chars: number;
  total: number;
  cached: number;
  rate_per_min?: number;
  eta_sec?: number | null;
}

export interface PageStatus {
  token: string;
  title: string;
  status: "start" | "saved" | "section" | "failed" | "skipped" | "cached" | "not_logged_in" | "file";
  chars?: number;
  file?: string | null;
  error?: string;
}

// ---------- 树 ----------
export interface TreeNode {
  token: string;
  title: string;
  parent: string;
  level: number;
  status?: string;
  children: TreeNode[];
}

export interface Config {
  output_dir: string;
  export_format: "md" | "pdf";
  tabs: number;
  min_interval: number;
  ceiling: number;
  adaptive: boolean;
  embed_images: boolean;
  download_attachments: boolean;
  pdf_image_width: number;
  unsupported_policy: "placeholder" | "skip";
  bitable_all_views: boolean;
  video_as_link: boolean;
  nav_timeout: number;
  render_timeout: number;
  extract_retries: number;
  image_concurrency: number;
  clean: boolean;
}
