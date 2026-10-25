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

/** 知识库树: 懒展开 + 勾选子集 + 节点状态点 + 搜索过滤 + 根切换 */
export function TreePanel({ rootToken, refreshKey, roots, onRootChange }: {
  rootToken: string; refreshKey: number;
  roots: { token: string; count: number }[];
  onRootChange: (t: string) => void;
}) {
  const [tree, setTree] = useState<TreeNode[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [query, setQuery] = useState("");
  const [checked, setChecked] = useState<Set<string>>(new Set());
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  const load = useCallback(async () => {
    if (!rootToken) return;
    setLoading(true);
    try {
      const r = await api.tree(rootToken);
      setTree(r.tree);
      setTotal(r.total);
      // 默认展开第一层
      setExpanded(new Set(r.tree.map((n) => n.token)));
    } finally {
      setLoading(false);
    }
  }, [rootToken]);

  useEffect(() => { load(); }, [load, refreshKey]);

  const filtered = useMemo(
    () => (query.trim() ? filterTree(tree, query.trim().toLowerCase()) : tree),
    [tree, query]);

  const visibleTokens = useMemo(() => {
    const s = new Set<string>();
    walk(filtered, (n) => s.add(n.token));
    return s;
  }, [filtered]);

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
    if (!checked.size) return;
    // 所选 token + 后代 (后端也会递归展开, 这里只传选中的根)
    await api.crawlStart({
      url: `https://x.feishu.cn/wiki/${rootToken}`,
      tokens: [...checked],
    });
  };

  return (
    <div className="card flex flex-col h-full min-h-0">
      <div className="px-4 py-3 border-b border-ink-700/50 flex items-center gap-2">
        <h2 className="text-sm font-semibold text-slate-800">知识库树</h2>
        <span className="text-xs text-slate-500">{total} 节点</span>
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
          onClick={load} disabled={loading}>
          {loading ? <Spinner className="size-3" /> : "⟳"} 刷新
        </button>
      </div>
      <div className="px-4 py-2.5 border-b border-ink-700/50">
        <input className="input !py-1.5 !text-xs" placeholder="搜索节点标题…"
          value={query} onChange={(e) => setQuery(e.target.value)} />
      </div>
      <div className="flex-1 overflow-auto px-2 py-2 min-h-0">
        {!rootToken && <Empty text="先运行一次采撷, 树会自动建立" />}
        {!loading && rootToken && !filtered.length && <Empty text="无匹配节点" />}
        {filtered.map((n) => (
          <Row key={n.token} node={n} depth={0}
            expanded={expanded} checked={checked}
            onToggleExpand={toggleExpand} onToggleCheck={toggleCheck}
            showCheck={!!query.trim() || checked.size > 0} />
        ))}
      </div>
      {checked.size > 0 && (
        <div className="px-4 py-3 border-t border-ink-700/50 flex items-center gap-2">
          <span className="text-xs text-slate-600">已选 {checked.size} 节点 (含后代)</span>
          <button className="btn-primary !py-1.5 !text-xs ml-auto" onClick={crawlSelected}>
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
