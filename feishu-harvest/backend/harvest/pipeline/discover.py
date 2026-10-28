# -*- coding: utf-8 -*-
"""pipeline.discover — 知识库导航树发现 (增强 BFS, 自 feishu-kb-client 移植)。

相对 v22 的修复: 每轮访问**全部**未访问节点 (v22 只访问前 30 个却标记全部
已访问 → 子树丢失), 连续 2 轮零新增收敛, 硬上限 20 轮;
每轮开头执行"虚拟滚动深度展开通道", 覆盖视口外折叠子树。
"""
from __future__ import annotations

import asyncio
from urllib.parse import urlparse

from harvest.events import bus
from harvest.store.db import Store
from harvest.textutil import strip_zws

_EXPAND_JS = """()=>{document.querySelectorAll('.workspace-tree-view-node-expand-arrow--collapsed').forEach(el=>{try{if(el.closest('a,[target="_blank"]'))return;(el.querySelector('[role="button"]')||el).click()}catch(e){}})}"""

_SCRAPE_JS = """
    (domain) => {
        const result=[],seen=new Set();
        document.querySelectorAll('[data-node-uid]').forEach(el=>{
            const uid=el.getAttribute('data-node-uid');if(!uid)return;
            const m=uid.match(/wikiToken=([^&]+)/);const token=m?m[1]:'';
            if(!token||seen.has(token))return;seen.add(token);
            const fm=uid.match(/firstLevelWikiToken=([^&]+)/);
            const level=parseInt(el.getAttribute('data-node-level')||'0');let text='';
            const c=el.querySelector('[class*="content"],[class*="title"]');
            text=(c||el).textContent.trim().replace(/[\\u200b\\u200c\\u200d\\ufeff]/g,'').substring(0,120);
            const ar=el.querySelector('.workspace-tree-view-node-expand-arrow');
            const exp=!ar?'leaf':(ar.className.indexOf('--collapsed')>=0?'collapsed':(ar.className.indexOf('--expanded')>=0?'expanded':'leaf'));
            result.push({title:text||token,token:token,level:level,
                pos:el.getAttribute('data-node-pos')||'',
                flt:fm?fm[1]:'',exp:exp,
                url:'https://'+domain+'/wiki/'+token});
        });
        return result;
    }
"""

# 深度通道: 飞书侧栏是虚拟滚动列表, 视口外节点不进 DOM → 步进滚动强制渲染,
# 每步点击全部折叠节点, 多 pass 迭代至收敛
_SCROLL_EXPAND_JS = """
    async (domain) => {
        const sleep = ms => new Promise(r => setTimeout(r, ms));
        const collapsed = () => [...document.querySelectorAll('.workspace-tree-view-node-expand-arrow--collapsed')];
        const clickAll = () => {
            for (const el of collapsed()) {
                try {
                    const hot = el.querySelector('[role="button"]') || el;
                    hot.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true, view: window}));
                } catch(e) {}
            }
        };
        const bag = new Map();
        const collect = () => {
            document.querySelectorAll('[data-node-uid]').forEach(el => {
                const uid = el.getAttribute('data-node-uid'); if (!uid) return;
                const m = uid.match(/wikiToken=([^&]+)/); const token = m ? m[1] : '';
                if (!token) return;
                const fm = uid.match(/firstLevelWikiToken=([^&]+)/);
                const c = el.querySelector('[class*="content"],[class*="title"]');
                const text = ((c || el).textContent || '').trim()
                    .replace(/[\\u200b\\u200c\\u200d\\ufeff]/g, '').substring(0, 120);
                const ar = el.querySelector('.workspace-tree-view-node-expand-arrow');
                const exp = !ar ? 'leaf' : (ar.className.indexOf('--collapsed') >= 0 ? 'collapsed'
                    : (ar.className.indexOf('--expanded') >= 0 ? 'expanded' : 'leaf'));
                bag.set(token, {title: text || token, token: token,
                    level: parseInt(el.getAttribute('data-node-level') || '0'),
                    pos: el.getAttribute('data-node-pos') || '',
                    flt: fm ? fm[1] : '', exp: exp,
                    url: 'https://' + domain + '/wiki/' + token});
            });
        };
        let n = document.querySelector('[data-node-uid]');
        const cands = [];
        while (n && n !== document.documentElement) {
            const oy = getComputedStyle(n).overflowY;
            if (oy === 'auto' || oy === 'scroll' || oy === 'overlay')
                cands.push({el: n, h: n.scrollHeight});
            n = n.parentElement;
        }
        if (!cands.length) {
            clickAll(); collect(); await sleep(1200); collect();
            return {scrolled: false, nodes: [...bag.values()]};
        }
        cands.sort((a, b) => b.h - a.h);  // 虚拟滚动容器有占位撑高, scrollHeight 最大
        const sc = cands[0].el;
        for (let pass = 0; pass < 8; pass++) {
            const bagBefore = bag.size;
            let clicked = 0;
            sc.scrollTop = 0;
            await sleep(200);
            collect();
            let y = 0;
            while (y <= sc.scrollHeight) {
                const arr = collapsed();
                clicked += arr.length;
                for (const el of arr) {
                    try {
                        const hot = el.querySelector('[role="button"]') || el;
                        hot.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true, view: window}));
                    } catch(e) {}
                }
                sc.scrollTop = y;
                await sleep(150);
                collect();
                y += 300;
            }
            sc.scrollTop = sc.scrollHeight;
            await sleep(600);
            collect();
            if (clicked === 0 && bag.size === bagBefore) break;
            await sleep(800);
        }
        sc.scrollTop = 0;
        await sleep(200);
        let y2 = 0;
        while (y2 <= sc.scrollHeight) {
            sc.scrollTop = y2;
            await sleep(160);
            collect();
            y2 += 250;
        }
        return {scrolled: true, nodes: [...bag.values()]};
    }
"""


async def _scrape_nodes(page, domain: str, deep: bool = True) -> list[dict]:
    if deep:
        try:
            info = await page.evaluate(_SCROLL_EXPAND_JS, domain)
            return info.get("nodes") or []
        except Exception:
            pass
    for _ in range(5):
        await page.evaluate(_EXPAND_JS)
        await asyncio.sleep(0.3)
    return await page.evaluate(_SCRAPE_JS, domain)


def _merge_node(tokens: dict, n: dict) -> bool:
    tok = n["token"]
    if tok not in tokens:
        tokens[tok] = n
        return True
    old = tokens[tok]
    if not old.get("pos") and n.get("pos"):
        old["pos"] = n["pos"]
    if not old.get("flt") and n.get("flt"):
        old["flt"] = n["flt"]
    if n.get("exp"):
        old["exp"] = n["exp"]
    return False


def _rebuild_parents(tokens: dict) -> None:
    """data-node-pos 前缀 + firstLevelWikiToken 重建父子 (零 API 成本)。"""
    pos_map: dict = {}
    for tok, t in tokens.items():
        if t.get("pos"):
            pos_map.setdefault(t["pos"], tok)
    for t in tokens.values():
        pos = t.get("pos") or ""
        if "," in pos:
            parent = pos_map.get(pos.rsplit(",", 1)[0])
            if parent and parent in tokens:
                t["parent"] = parent
        if not t.get("parent") and t.get("flt") and t["flt"] in tokens:
            t["parent"] = t["flt"]


async def discover_tree(page, url: str, store: Store) -> list[dict]:
    """在已加载根页面的 page 上执行 BFS; 节点全部落库, 返回节点列表。"""
    domain = urlparse(url).netloc
    m = urlparse(url).path
    root_token = ""
    import re as _re
    rm = _re.search(r"/wiki/([^/?#]+)", m)
    if rm:
        root_token = rm.group(1)

    tokens: dict = {}
    # 增量种子: 库中该知识库已有节点直接继承 (不重复逐页 BFS)
    seeded = store.nodes_by_root(root_token)
    for n in seeded:
        t = {"token": n["token"], "title": n["title"], "parent": n["parent"],
             "level": n["level"], "url": f"https://{domain}/wiki/{n['token']}",
             "exp": "leaf"}
        tokens[n["token"]] = t
    if seeded:
        bus.publish({"type": "discover", "stage": "seed", "total": len(seeded)})

    for n in await _scrape_nodes(page, domain, deep=True):
        _merge_node(tokens, n)

    def persist():
        for t in tokens.values():
            store.upsert_node(t["token"], strip_zws(t.get("title", "")),
                              t.get("parent", ""), int(t.get("level") or 0),
                              root_token=root_token)

    persist()
    bus.publish({"type": "discover", "stage": "root", "total": len(tokens)})

    zero_rounds = 0
    for rnd in range(1, 21):
        for n in await _scrape_nodes(page, domain, deep=True):
            if _merge_node(tokens, n):
                persist()
        unvisited = [t for t in tokens.values() if not t.get("_visited")
                     and t.get("exp") != "leaf"]  # leaf 无需访问; 章节节点须访问切空间
        if not unvisited:
            break
        unvisited.sort(key=lambda t: 0 if "飞书云文档" in t.get("title", "") else 1)
        new_cnt = 0
        for i, p in enumerate(unvisited):
            p["_visited"] = True
            try:
                await page.goto(p["url"], wait_until="domcontentloaded", timeout=15000)
                await asyncio.sleep(2)
                for n in await _scrape_nodes(page, domain, deep=False):
                    if _merge_node(tokens, n):
                        new_cnt += 1
            except Exception:
                pass
            persist()
            bus.publish({"type": "discover", "stage": "visit", "round": rnd,
                         "visited": i + 1, "round_total": len(unvisited),
                         "new": new_cnt, "total": len(tokens),
                         "title": p.get("title", "")})
        zero_rounds = zero_rounds + 1 if new_cnt == 0 else 0
        bus.publish({"type": "discover", "stage": "round", "round": rnd,
                     "new": new_cnt, "total": len(tokens)})
        if zero_rounds >= 2:
            break

    pages = []
    _rebuild_parents(tokens)  # pos 前缀 + firstLevelWikiToken 重建父子 (缺此步则输出全平铺)
    for t in tokens.values():
        pages.append({"token": t["token"], "title": strip_zws(t.get("title", "")),
                      "parent": t.get("parent", ""), "level": int(t.get("level") or 0),
                      "url": t["url"]})
    persist()
    bus.publish({"type": "discover", "stage": "done", "total": len(pages)})
    return pages


def single_page_fallback(url: str) -> dict | None:
    """非 wiki 树链接回退为单页。"""
    import re as _re
    m = _re.search(r"/(?:wiki|docx|doc|base|sheet)/([^/?#]+)", url)
    if not m:
        return None
    token = m.group(1)
    return {"token": token, "title": f"单页_{token[:8]}", "parent": "", "level": 0, "url": url}
