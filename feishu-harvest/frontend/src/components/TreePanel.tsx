import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../api";
import type { TreeNode } from "../types";
import { Spinner } from "./ui";

const STATUS_TONE: Record<string, string> = {
  done: "bg-emerald-500",
  running: "bg-lark animate-pulse-soft",
  failed: "bg-rose-500",
  pending: "bg-slate-300",
};

/** 知识库树: 懒展开 + 勾选子集 + 节点状态点 + 搜索过滤 + 根切换 + 漏爬/待爬视图 + 查新页 */
export function TreePanel({ rootToken, refreshKey, roots, onRootChange }: {
  rootToken: string; refreshKey: number;
  roots: { token: string; count: number; url?: string }[];
  onRootChange: (t: string) => void;
}) {
  const [tree, setTree] = useState<TreeNode[]>([]);
  const [flat, setFlat] = useState<TreeNode[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [query, setQuery] = useState("");
  const [checked, setChecked] = useState<Set<string>>(new Set());
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [view, setView] = useState<"all" | "failed" | "pending">("all");
  const [scanning, setScanning] = useState(false);
  const [scanMsg, setScanMsg] = useState("");

  const rootUrl = roots.find((r) => r.token === rootToken)?.url || "";

  const load = useCallback(async () => {
    if (!rootToken) return;
    setLoading(true);
    try {
      const r = await api.tree(rootToken);
      setTree(r.tree);
      setFlat(r.flat);
      setTotal(r.total);
      // 默认展开第一层
      setExpanded(new Set(r.tree.map((n) => n.token)));
    } finally {
      setLoading(false);
    }
  }, [rootToken]);

  useEffect(() => { load(); }, [load, refreshKey]);
  useEffect(() => {
    if (!scanMsg) return;
    const t = setTimeout(() => setScanMsg(""), 8000);
    return () => clearTimeout(t);
  }, [scanMsg]);

  const failedNodes = useMemo(() => flat.filter((n) => n.status === "failed"), [flat]);
  const pendingNodes = useMemo(
    () => flat.filter((n) => n.status === "pending" || n.status === "running"), [flat]);

  const filtered = useMemo(
    () => (query.trim() ? filterTree(tree, query.trim().toLowerCase()) : tree),
    [tree, query]);

  const visibleTokens = useMemo(() => {
    const s = new Set<string>();
    if (view === "failed") failedNodes.forEach((n) => s.add(n.token));
    else if (view === "pending") pendingNodes.forEach((n) => s.add(n.token));
    else walk(filtered, (n) => s.add(n.token));
    return s;
  }, [filtered, view, failedNodes, pendingNodes]);

  const allVisibleChecked = visibleTokens.size > 0 &&
    [...visibleTokens].every((t) => checked.has(t));

  const toggleCheck = (n: TreeNode, on: boolean) => {
    setChecked((prev) => {
      const next = new Set(prev);
      walk([n], (x) => on ? next.add(x.token) : next.delete(x.token));
      return next;
    });
  };
  const toggleExpand = (t: string) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      next.has(t) ? next.delete(t) : next.add(t);
      return next;
    });

  const crawlSelected = async () => {
    if (!checked.size || !rootUrl) return;
    // 所选 token + 后代 (后端也会递归展开, 这里只传选中的根)
    await api.crawlStart({ url: rootUrl, tokens: [...checked] });
  };

  const rediscover = async () => {
    if (!rootUrl || scanning) return;
    setScanning(true);
    setScanMsg("");
    try {
      const r = await api.rediscover(rootUrl);
      setScanMsg(r.ok ? `查新页完成: 共 ${r.total} 节点, 新增 ${r.new}` :
        `查新页失败: ${r.error ?? "未知错误"}`);
      await load();
    } finally {
      setScanning(false);
    }
  };

  const listNodes = view === "failed" ? failedNodes : pendingNodes;

  return (
    <div className="card flex flex-col h-full min-h-0">
      <div className="px-4 py-3 border-b border-ink-700/50 flex items-center gap-2 flex-wrap">
        <h2 className="text-sm font-semibold text-slate-800">知识库树</h2>
        <span className="text-xs text-slate-500">{total} 节点</span>
        <button className={`text-[11px] rounded-full px-2 py-0.5 border
          ${view === "failed" ? "border-rose-300 bg-rose-50 text-rose-600"
            : "border-ink-600 text-slate-500 hover:text-rose-500"}`}
          onClick={() => setView(view === "failed" ? "all" : "failed")}
          title="提取失败/空内容的节点, 下次增量续爬会自动重试, 也可勾选立即补爬">
          漏爬 {failedNodes.length}
        </button>
        <button className={`text-[11px] rounded-full px-2 py-0.5 border
          ${view === "pending" ? "border-amber-300 bg-amber-50 text-amber-600"
            : "border-ink-600 text-slate-500 hover:text-amber-500"}`}
          onClick={() => setView(view === "pending" ? "all" : "pending")}
          title="已发现但尚未提取的节点 (查新页后发现的新页面)">
          待爬 {pendingNodes.length}
        </button>
        {roots.length > 1 && (
          <select className="ml-auto text-xs bg-white border border-ink-600 rounded-lg px-2 py-1
            text-slate-600 max-w-[150px] truncate" value={rootToken}
            onChange={(e) => onRootChange(e.target.value)}>
            {roots.map((r) => (
              <option key={r.token} value={r.token}>
                {r.token.slice(0, 6)}… ({r.count})
              </option>
            ))}
          </select>
        )}
        <button className={`btn-ghost !px-2 !py-1 !text-xs ${roots.length > 1 ? "" : "ml-auto"}`}
          onClick={rediscover} disabled={scanning || !rootUrl}
          title="重跑发现流程, 找出上游新增页面 (不提取内容)">
          {scanning ? <Spinner className="size-3" /> : "◎"} 查新页
        </button>
        <button className="btn-ghost !px-2 !py-1 !text-xs" onClick={load} disabled={loading}>
          {loading ? <Spinner className="size-3" /> : "⟳"} 刷新
        </button>
        {scanMsg && (
          <span className={`w-full text-[11px] ${scanMsg.startsWith("查新页失败")
            ? "text-rose-500" : "text-emerald-600"}`}>{scanMsg}</span>
        )}
      </div>
      <div className="px-4 py-2.5 border-b border-ink-700/50">
        <input className="input !py-1.5 !text-xs" placeholder="搜索节点标题…"
          value={query} onChange={(e) => setQuery(e.target.value)} />
      </div>
      <div className="flex-1 overflow-auto px-2 py-2 min-h-0">
        {!rootToken && <Empty text="先运行一次采撷, 树会自动建立" />}
        {!loading && rootToken && view === "all" && !filtered.length && <Empty text="无匹配节点" />}
        {!loading && rootToken && view !== "all" && !listNodes.length &&
          <Empty text={view === "failed" ? "没有漏爬节点" : "没有待爬节点"} />}
        {view === "all" && filtered.map((n) => (
          <Row key={n.token} node={n} depth={0}
            expanded={expanded} checked={checked}
            onToggleExpand={toggleExpand} onToggleCheck={toggleCheck}
            showCheck={!!query.trim() || checked.size > 0} />
        ))}
        {view !== "all" && listNodes.map((n) => (
          <div key={n.token} className="flex items-center gap-1.5 rounded-lg px-2 py-1.5
            hover:bg-ink-800">
            <span className={`size-1.5 rounded-full shrink-0
              ${view === "failed" ? "bg-rose-500" : "bg-amber-400"}`} />
            <input type="checkbox" className="accent-lark size-3.5 shrink-0"
              checked={checked.has(n.token)}
              onChange={(e) => toggleCheck(n, e.target.checked)} />
            <span className="text-xs text-slate-700 truncate" title={n.title}>{n.title}</span>
            {view === "failed" && (
              <span className="ml-auto text-[10px] text-rose-500 truncate max-w-[40%] shrink-0"
                title={n.error || ""}>{n.error || "failed"}</span>
            )}
          </div>
        ))}
      </div>
      {checked.size > 0 && (
        <div className="px-4 py-3 border-t border-ink-700/50 flex items-center gap-2">
          <span className="text-xs text-slate-600">已选 {checked.size} 节点 (含后代)</span>
          <button className="btn-primary !py-1.5 !text-xs ml-auto" onClick={crawlSelected}
            disabled={!rootUrl}>
            采撷所选
          </button>
          <button className="btn-ghost !py-1.5 !text-xs" onClick={() => setChecked(new Set())}>
            清除
          </button>
        </div>
      )}
      {checked.size === 0 && tree.length > 0 && (
        <div className="px-4 py-2.5 border-t border-ink-700/50 flex items-center gap-2 text-xs text-slate-500">
          <button className="text-lark-soft hover:underline"
            onClick={() => setChecked(new Set(visibleTokens))}>全选</button>
          · 勾选节点可仅采撷部分子树
          {failedNodes.length > 0 && (
            <button className="text-rose-500 hover:underline ml-auto"
              onClick={() => setChecked(new Set(failedNodes.map((n) => n.token)))}>
              选中漏爬
            </button>
          )}
        </div>
      )}
    </div>
  );
}

function Row({ node, depth, expanded, checked, onToggleExpand, onToggleCheck, showCheck }: {
  node: TreeNode; depth: number;
  expanded: Set<string>; checked: Set<string>;
  onToggleExpand: (t: string) => void;
  onToggleCheck: (n: TreeNode, on: boolean) => void;
  showCheck: boolean;
}) {
  const hasKids = node.children.length > 0;
  const open = expanded.has(node.token);
  const isChecked = checked.has(node.token);
  return (
    <div>
      <div className={`group flex items-center gap-1.5 rounded-lg px-2 py-1.5 hover:bg-ink-800
        ${isChecked ? "bg-lark/5" : ""}`} style={{ paddingLeft: depth * 14 + 8 }}>
        {hasKids ? (
          <button className={`size-4 shrink-0 grid place-items-center text-slate-400
            transition-transform ${open ? "rotate-90" : ""}`} onClick={() => onToggleExpand(node.token)}>
            ▸
          </button>
        ) : (
          <span className="size-4 shrink-0" />
        )}
        <span className={`size-1.5 rounded-full shrink-0 ${STATUS_TONE[node.status ?? "pending"]}`} />
        {(showCheck || isChecked) && (
          <input type="checkbox" className="accent-lark size-3.5 shrink-0"
            checked={isChecked}
            onChange={(e) => onToggleCheck(node, e.target.checked)} />
        )}
        <span className="text-xs text-slate-700 truncate" title={node.title}>{node.title}</span>
        {node.status === "done" && (
          <span className="ml-auto text-[10px] text-emerald-600 shrink-0">done</span>
        )}
        {node.status === "failed" && (
          <span className="ml-auto text-[10px] text-rose-500 shrink-0 truncate max-w-[35%]"
            title={node.error || ""}>{node.error || "failed"}</span>
        )}
      </div>
      {open && hasKids && node.children.map((c) => (
        <Row key={c.token} node={c} depth={depth + 1}
          expanded={expanded} checked={checked}
          onToggleExpand={onToggleExpand} onToggleCheck={onToggleCheck}
          showCheck={showCheck} />
      ))}
    </div>
  );
}

function walk(nodes: TreeNode[], fn: (n: TreeNode) => void) {
  for (const n of nodes) {
    fn(n);
    walk(n.children, fn);
  }
}

function filterTree(nodes: TreeNode[], q: string): TreeNode[] {
  const out: TreeNode[] = [];
  for (const n of nodes) {
    const kids = filterTree(n.children, q);
    if (n.title.toLowerCase().includes(q) || kids.length) {
      out.push({ ...n, children: kids });
    }
  }
  return out;
}

function Empty({ text }: { text: string }) {
  return (
    <div className="h-full grid place-items-center text-xs text-slate-400 py-12">{text}</div>
  );
}
