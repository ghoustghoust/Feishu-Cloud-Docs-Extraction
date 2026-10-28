# -*- coding: utf-8 -*-
"""pipeline.crawler — 三阶段爬取编排 (发现 → 提取 → 导出)。

核心设计:
- 提取阶段 PagePool(N Tab) worker 并发, 导航节奏由全局 AdaptiveGate 统一调节
  (AIMD 自适应: 429/超时 → 自动减速, 顺畅 → 缓慢提速)。
- 每页完成即落库 (SQLite) + 发布事件: 中断可续, 前端实时可见。
- 连续 3 页未登录 → 判定 Cookie 过期, 发布 need_login 并暂停任务。
"""
from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
import re
import time
from urllib.parse import urlparse

from harvest.config import CrawlConfig
from harvest.events import bus
from harvest.legacy import bitable, extract, files
from harvest.pipeline import pdf, writers
from harvest.pipeline.discover import discover_tree, single_page_fallback
from harvest.store.db import Store
from harvest.textutil import strip_zws
from harvest.transport.browser import PagePool, service, check_login_state

BASE_DIR = files.BASE_DIR  # backend/


class Crawler:
    def __init__(self, cfg: CrawlConfig) -> None:
        self.cfg = cfg
        self.gate = AdaptiveGateProxy(cfg)
        self.store = Store(BASE_DIR / "data" / "harvest.db")
        self.pool = PagePool(service, cfg.tabs)
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None
        self.last_result: dict = {}
        self._bt_hits: dict = {}  # page → {(base, tableId, viewId)} 网络层内嵌表格捕获

    # ---------- 生命周期 ----------
    def start(self, url: str, tokens: list[str] | None = None, fresh: bool = False) -> None:
        self._stop.clear()
        self._task = asyncio.create_task(self.run(url, tokens, fresh))
        _STRONG_REFS.add(self._task)  # 防 GC 提前回收长任务
        self._task.add_done_callback(_STRONG_REFS.discard)

    def stop(self) -> None:
        self._stop.set()

    @property
    def running(self) -> bool:
        return bool(self._task and not self._task.done())

    # ---------- 主流程 ----------
    async def run(self, url: str, tokens: list[str] | None = None,
                  fresh: bool = False) -> dict:
        started = time.time()
        out_dir = self.cfg.output_path(BASE_DIR)
        m = re.search(r"/wiki/([^/?#]+)", urlparse(url).path)
        if not self.cfg.output_dir and m:
            out_dir = out_dir / m.group(1)  # 按知识库分目录, 多库输出互不污染
        if self.cfg.clean:  # 可选: 清空输出目录 (默认增量保留)
            import shutil
            shutil.rmtree(out_dir, ignore_errors=True)
        run_id = self.store.start_run(url, str(out_dir))
        m2 = re.search(r"/wiki/([^/?#]+)", urlparse(url).path)
        self.store.reset_interrupted(m2.group(1) if m2 else "")  # 上次中断遗留 running → pending
        service.busy = True
        bus.publish({"type": "run", "state": "running", "phase": "prepare",
                     "url": url, "output": str(out_dir), "format": self.cfg.export_format})
        try:
            await service.ensure_context()
            page = service.page
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            except Exception:
                pass
            await asyncio.sleep(8)
            if await check_login_state(page) is False:
                bus.publish({"type": "run", "state": "need_login"})
                self.store.finish_run(run_id, "need_login", {})
                return {"ok": False, "need_login": True}

            # ---- 阶段 1: 发现 ----
            bus.publish({"type": "run", "state": "running", "phase": "discover"})
            pages = await self._discover(page, url, tokens, fresh=fresh)
            total = len(pages)
            if total == 0:
                raise RuntimeError("未发现任何页面")
            bus.publish({"type": "run", "state": "running", "phase": "discover_done",
                         "total": total})

            # ---- 阶段 2: 提取 (worker 池) ----
            bus.publish({"type": "run", "state": "running", "phase": "extract",
                         "total": total})
            # 写盘定位必须用全量树: 子集爬取时 subset 缺祖先链, 用子集建树会断链落顶层
            full_pages = self._store_pages(url) or pages
            stats = await self._extract_all(url, pages, full_pages, out_dir, fresh)

            # ---- 阶段 3: 导出 ----
            bus.publish({"type": "run", "state": "running", "phase": "export"})
            w = writers.TreeWriter(out_dir, pages)
            pdf_path = None
            if self.cfg.embed_images:  # 自包含副本 (原件保持轻量不动)
                sc = writers.export_selfcontained(out_dir)
                stats["selfcontained"] = sc
                bus.publish({"type": "run", "state": "running", "phase": "export",
                             "selfcontained": sc})
            md_files = sorted(f for f in out_dir.rglob("*.md")
                              if f.name != "README.md" and "selfcontained" not in f.parts)
            total_chars = sum(len(f.read_text(encoding="utf-8")) for f in md_files)
            w.write_index(len(md_files), total_chars)
            w.write_report({**stats, "耗时": f"{time.time() - started:.0f} 秒",
                            "总字符": f"{total_chars:,}", "输出目录": str(out_dir)})
            if self.cfg.export_format == "pdf" and md_files:
                bus.publish({"type": "run", "state": "running", "phase": "pdf"})
                pdf_target = out_dir / "feishu_docs.pdf"
                failed = await pdf.export_pdf(md_files, pdf_target,
                                              image_width=self.cfg.pdf_image_width)
                pdf_path = str(pdf_target)
                stats["pdf_failed"] = failed

            elapsed = time.time() - started
            result = {"ok": True, "pages_found": total, **stats,
                      "total_chars": total_chars, "output_dir": str(out_dir),
                      "pdf": pdf_path, "elapsed_sec": round(elapsed, 1)}
            self.last_result = result
            self.store.finish_run(run_id, "done", stats)
            bus.publish({"type": "run", "state": "done", **result})
            return result
        except Exception as e:
            self.store.finish_run(run_id, "error", {})
            bus.publish({"type": "run", "state": "error",
                         "error": f"{type(e).__name__}: {e}"})
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}
        finally:
            service.busy = False

    # ---------- 发现 ----------
    async def _discover(self, page, url: str, tokens: list[str] | None,
                        fresh: bool = False) -> list[dict]:
        if tokens:  # 子集爬取: 树来自库中已有发现结果
            pages = self._subset(url, tokens)
            if pages is not None:
                bus.publish({"type": "discover", "stage": "subset", "total": len(pages)})
                return pages
        # 增量默认: 库中已有该知识库的树 → 跳过全量 BFS (省 ~10 分钟);
        # fresh=True (界面"全量重爬") 才重新发现
        if not fresh:
            stored = self._store_pages(url)
            if stored:
                bus.publish({"type": "discover", "stage": "reuse_store",
                             "total": len(stored)})
                return stored
        pages = await discover_tree(page, url, self.store)
        if not pages:  # 单页回退
            single = single_page_fallback(url)
            if single:
                self.store.upsert_node(single["token"], single["title"], "", 0)
                pages = [single]
        return pages

    def _subset(self, url: str, tokens: list[str] | None = None) -> list[dict] | None:
        """选中 token 及其全部后代; 树为空返回 None 让调用方先跑发现。"""
        m = re.search(r"/wiki/([^/?#]+)", urlparse(url).path)
        root = m.group(1) if m else ""
        all_nodes = self.store.nodes_by_root(root)
        if not all_nodes:
            return None
        tok2 = {n["token"]: n for n in all_nodes}
        children: dict = {}
        for n in all_nodes:
            if n["parent"] in tok2:
                children.setdefault(n["parent"], []).append(n["token"])
        expanded: set = set()
        stack = [t for t in tokens if t in tok2]
        while stack:
            t = stack.pop()
            if t in expanded:
                continue
            expanded.add(t)
            stack.extend(children.get(t, []))
        return [{"token": n["token"], "title": n["title"], "parent": n["parent"],
                 "level": n["level"], "url": f"https://{urlparse(url).netloc}/wiki/{n['token']}"}
                for n in all_nodes if n["token"] in expanded]

    def _store_pages(self, url: str) -> list[dict] | None:
        """库中该知识库的全量节点 → 写盘用 pages (含未选入子集的祖先)。"""
        m = re.search(r"/wiki/([^/?#]+)", urlparse(url).path)
        root = m.group(1) if m else ""
        nodes = self.store.nodes_by_root(root) if root else []
        if not nodes:
            return None
        return [{"token": n["token"], "title": n["title"], "parent": n["parent"],
                 "level": n["level"],
                 "url": f"https://{urlparse(url).netloc}/wiki/{n['token']}"}
                for n in nodes]

    # ---------- 提取 ----------
    async def _extract_all(self, root_url: str, pages: list[dict],
                           writer_pages: list[dict], out_dir, fresh: bool) -> dict:
        total = len(pages)
        w = writers.TreeWriter(out_dir, writer_pages)
        queue: asyncio.Queue = asyncio.Queue()
        for p in pages:
            node = self.store.node(p["token"])
            if not fresh and node and node["status"] == "done" and not self._stop.is_set():
                bus.publish({"type": "page", "token": p["token"], "title": p["title"],
                             "status": "cached"})
                continue
            queue.put_nowait(p)
        todo = queue.qsize()
        bus.publish({"type": "discover", "stage": "resume", "cached": total - todo,
                     "todo": todo})

        self._st = {"done": 0, "failed": 0, "skipped": 0, "chars": 0,
                    "files": 0, "total": total, "cached": total - todo,
                    "t0": time.time(), "need_login": False}
        self._login_fail = 0

        async def worker():
            page = await self.pool.acquire()
            if not getattr(page, "_bt_hooked", False):  # 内嵌表格: 网络层捕获
                page.on("request", lambda req: self._bt_capture(page, req.url))
                page._bt_hooked = True
            try:
                while not self._stop.is_set():
                    try:
                        p = queue.get_nowait()
                    except asyncio.QueueEmpty:
                        return
                    page = await self._extract_one(page, p, w, queue.qsize() + 1, todo)
            finally:
                pass

        workers = [asyncio.create_task(worker()) for _ in range(self.cfg.tabs)]
        # 主页面也是 worker 的一员 (PagePool 第一个 acquire 复用主页面)
        await asyncio.gather(*workers)
        await self.pool.close_all()
        s = self._st
        return {"pages_saved": s["done"], "failed": s["failed"], "skipped": s["skipped"],
                "chars": s["chars"], "files_downloaded": s["files"],
                "cached": s["cached"], "stopped": self._stop.is_set()}

    def _bt_capture(self, page, url: str) -> None:
        """页面任意时刻发出的 bitable records 请求 → 内嵌表格三元组 (滚动触发懒加载也覆盖)。"""
        m = re.search(r"/space/api/v1/bitable/([^/?#]+)/records", url)
        if not m:
            return
        t = re.search(r"[?&]table[Ii][Dd]=([^&]+)", url)
        v = re.search(r"[?&]view[Ii][Dd]=([^&]+)", url)
        self._bt_hits.setdefault(page, set()).add(
            (m.group(1), t.group(1) if t else "", v.group(1) if v else ""))

    async def _extract_one(self, page, info: dict, w: writers.TreeWriter,
                           remaining: int, todo: int):
        """提取单页; Tab 崩溃时换新页并返回 (worker 回绑), 失败节点留给下轮增量重试。"""
        token, page_url = info["token"], info["url"]
        title = strip_zws(info.get("title", "")) or token[:8]
        self._bt_hits[page] = set()
        idx = todo - remaining + 1
        self.store.mark_running(token)
        bus.publish({"type": "page", "token": token, "title": title, "index": idx,
                     "total": todo, "status": "start"})

        # 导航 + 全局 AIMD 节流 (所有 Tab 共享一个闸门)
        t0 = time.monotonic()
        ok = True
        async with self.gate:
            try:
                try:
                    await page.goto(page_url, wait_until="domcontentloaded",
                                    timeout=self.cfg.nav_timeout * 1000)
                except Exception:
                    await page.goto(page_url, wait_until="load",
                                    timeout=self.cfg.nav_timeout * 1000)
            except Exception as e:
                ok = False
                if _is_target_closed(e):
                    page = await self.pool.replace(page)
        self.gate.record(ok, latency=time.monotonic() - t0 if ok else None)
        await asyncio.sleep(3)  # SPA 渲染起步

        # 登录三态: 连续 3 页未登录 → Cookie 过期, 暂停任务
        login = await check_login_state(page)
        if login is False:
            await asyncio.sleep(2)
            login = await check_login_state(page)
        if login is False:
            self._st["failed"] += 1
            self._login_fail += 1
            self.store.mark_failed(token, "not_logged_in")
            bus.publish({"type": "page", "token": token, "title": title,
                         "status": "not_logged_in"})
            if self._login_fail >= 3:
                self._st["need_login"] = True
                bus.publish({"type": "run", "state": "need_login"})
                self._stop.set()
            return page
        self._login_fail = 0

        try:
            md, blob_map, kind = "", {}, "doc"
            attempts = 1 + max(0, self.cfg.extract_retries)
            for attempt in range(attempts):
                try:
                    md, blob_map, kind = await self._route_extract(page, page_url, info, w)
                except Exception:
                    if attempt == attempts - 1:
                        raise
                    md, blob_map, kind = "", {}, "doc"
                if md and len(md) >= 30:
                    break
                if kind != "doc":
                    break  # bitable/folder/file 为空通常是真无内容, 重导航无意义
                if attempt < attempts - 1:  # 文档页过短 → 重新导航再试
                    await asyncio.sleep(2)
                    try:
                        await page.goto(page_url, wait_until="domcontentloaded",
                                        timeout=self.cfg.nav_timeout * 1000)
                    except Exception:
                        pass
                    await asyncio.sleep(4)
            if kind != "bitable":
                origin = f"{urlparse(page_url).scheme}://{urlparse(page_url).netloc}"
                for base, tid, vid in sorted(self._bt_hits.get(page, set()))[:5]:
                    if not (base and tid):
                        continue
                    u = f"{origin}/base/{base}?table={tid}" + (f"&view={vid}" if vid else "")
                    tmd, _ = await bitable.to_markdown(page, u,
                                                       all_views=self.cfg.bitable_all_views)
                    if tmd:
                        md = f"{md or ''}\n\n---\n\n{tmd}"
            # 内嵌记录视图(画册等)懒初始化不拉数据: 无表格产出时把 UI 壳替换为诚实占位
            # (检测在提取前完成 — 提取的整页滚动会把虚拟化块卸载, 事后检测会丢)
            if kind == "doc" and "| ---" not in (md or ""):
                if info.get("_has_embed") and not self._bt_hits.get(page):
                    md = ("<!-- 内嵌多维表格视图 (记录视图懒初始化, 未拉取到数据) -->\n\n"
                          "**本页为内嵌多维表格视图**，飞书仅在用户聚焦时加载其数据，"
                          "自动抓取未获取到表格内容。\n\n"
                          "如需查看表格，请打开原页面：上方「来源」。")
            if _is_browser_error_page(md or ""):
                self._st["failed"] += 1
                self.store.mark_failed(token, "browser_error_page")
                bus.publish({"type": "page", "token": token, "title": title,
                             "status": "failed", "error": "browser_error_page"})
                self._publish_stats()
                return page
            if not md or len(md) < 30:
                self._st["skipped"] += 1
                self.store.mark_failed(token, "empty")
                bus.publish({"type": "page", "token": token, "title": title,
                             "status": "skipped", "chars": len(md or "")})
                return page
            # blob 图片映射替换 + 外链图片并行下载 (受 image_concurrency 限速)
            for blob_url, local in (blob_map or {}).items():
                if blob_url in md:
                    md = md.replace(blob_url, local) if re.search(
                        r"!\[[^\]]*\]\(" + re.escape(blob_url) + r"\)", md) \
                        else md.replace(blob_url, f"![图片]({local})")
            ext_urls = {m.group(2) for m in re.finditer(r"!\[([^\]]*)\]\(([^)]+)\)", md)
                        if not m.group(2).startswith(("images/", "data:"))}
            if ext_urls:
                sem = asyncio.Semaphore(max(1, self.cfg.image_concurrency))

                async def _dl(src: str):
                    async with sem:
                        return src, await files.download_img(page, src, w.images)

                for src, local in await asyncio.gather(*(_dl(s) for s in ext_urls)):
                    if local:
                        md = md.replace(src, local)
            node0 = self.store.node(token) or {}
            prev = Path(node0["file_path"]) if node0.get("file_path") else None
            path_str = w.write_page(token, title, page_url, md, overwrite=prev)
            name = Path(path_str).name if path_str else None
            h = hashlib.md5(md.encode()).hexdigest()
            if kind == "file":
                self._st["files"] += 1
            self._st["done"] += 1
            self._st["chars"] += len(md)
            self.store.mark_done(token, len(md), h, file_path=path_str or "")
            bus.publish({"type": "page", "token": token, "title": title,
                         "status": "saved" if name else "section",
                         "chars": len(md), "file": name})
            self._publish_stats()
        except Exception as e:
            if _is_target_closed(e):
                page = await self.pool.replace(page)
            self._st["failed"] += 1
            self.store.mark_failed(token, f"{type(e).__name__}: {e}")
            bus.publish({"type": "page", "token": token, "title": title,
                         "status": "failed", "error": f"{type(e).__name__}: {e}"})
            self._publish_stats()
        return page

    async def _route_extract(self, page, live_url: str, info: dict,
                             w: writers.TreeWriter) -> tuple[str, dict, str]:
        """提取分发 (obj_type 驱动): bitable → file → folder_manager → DOM → 不支持占位。"""
        origin = f"{urlparse(live_url).scheme}://{urlparse(live_url).netloc}"
        wiki_token = info["token"]
        m = re.search(r"/wiki/([^/?#]+)", urlparse(live_url).path)
        if m:
            wiki_token = m.group(1)

        node = await files._get_node(page, origin, wiki_token)
        obj_type = (node or {}).get("obj_type")

        if "?table=" in live_url or obj_type == 8:  # 多维表格: API 直采
            md, blob = await bitable.to_markdown(page, live_url,
                                                 all_views=self.cfg.bitable_all_views)
            if md:
                return md, (blob if isinstance(blob, dict) else {}), "bitable"
            if obj_type == 8:  # 确认是 bitable 但 API 采集失败 → 占位而非静默丢失
                return self._unsupported_md(info, 8, live_url, origin), {}, "unsupported"

        if obj_type == 12 and node and node.get("obj_token"):  # 文件节点: 真实下载
            target_dir, _ = w.target_for(info["token"])
            saved, _via = await files.download_wiki_file(
                page, origin, node["obj_token"],
                node.get("title") or info.get("title", ""), target_dir)
            from pathlib import Path
            title = node.get("title") or info.get("title", "")
            if saved:
                md = (f"<!-- 文件已下载: {Path(saved).name} -->\n\n"
                      f"- 文件已保存至当前目录: **{Path(saved).name}**")
                return md, {}, "file"
            dl = f"{origin}/space/api/box/stream/download/all/{node['obj_token']}?type=12"
            preview = (f"{origin}/space/api/box/stream/download/preview_sub/"
                       f"{node['obj_token']}?preview_type=22&sub_id=txt_all.json")
            md = (f"<!-- 文件 {wiki_token} · {title} (下载失败, 降级为链接) -->\n\n"
                  f"- [⬇ 下载: {title}]({dl})\n- [在线预览: {title}]({preview})")
            return md, {}, "file"

        is_folder = False
        try:
            is_folder = await page.evaluate(
                "() => !!document.querySelector('[data-block-type=\"folder_manager\"]')")
        except Exception:
            pass
        if is_folder:
            target_dir, _ = w.target_for(info["token"])
            dl = files.download_wiki_file if self.cfg.download_attachments else None
            md = await files.folder_manager_to_md(page, live_url, download_dir=target_dir,
                                                  download_file=dl)
            if md:
                return md, {}, "folder"

        has_embed_early = False
        try:
            has_embed_early = await page.evaluate(
                "() => !!document.querySelector('[data-block-type=\"bitable\"],[data-block-type=\"base_refer\"]')")
        except Exception:
            pass
        md, blob = await extract.extract_page(page, w.images,
                                              render_timeout=self.cfg.render_timeout,
                                              video_links=self.cfg.video_as_link)
        info["_has_embed"] = has_embed_early
        # bitable SPA 晚绑定兜底: ?table= 在路由判定后才附加到地址栏
        # (obj_type=8 页的空间 ID 探测失败场景) — DOM 过短时按活页 URL 重试直采
        if len(md or "") < 400 and "?table=" in (page.url or ""):
            tmd, tblob = await bitable.to_markdown(page, page.url,
                                                   all_views=self.cfg.bitable_all_views)
            if tmd and len(tmd) > len(md or ""):
                return tmd, (tblob if isinstance(tblob, dict) else {}), "bitable"
        if (not md or len(md) < 30) and obj_type not in (None, 22):
            # DOM 无内容且非普通文档 → sheet/幻灯/画板等不支持的类型
            return self._unsupported_md(info, obj_type, live_url, origin), {}, "unsupported"
        return md, blob, "doc"

    def _unsupported_md(self, info: dict, obj_type, live_url: str, origin: str) -> str:
        """不支持类型的占位说明 (unsupported_policy=skip 时返回空串直接跳过)。"""
        if self.cfg.unsupported_policy == "skip":
            return ""
        names = {3: "表格 Sheet", 8: "多维表格", 11: "幻灯片", 15: "幻灯片",
                 9: "思维笔记", 10: "思维笔记", 12: "文件"}
        kind = names.get(obj_type, f"类型 {obj_type}")
        return (f"<!-- 不支持的飞书类型: obj_type={obj_type} -->\n\n"
                f"**{kind}**: {info.get('title', '')}\n\n"
                f"此内容为飞书专有格式 ({kind}), 无法转换为本地文档。\n"
                f"请通过飞书客户端查看: {live_url}\n")

    def _publish_stats(self) -> None:
        s = self._st
        elapsed = max(time.time() - s["t0"], 1)
        rate = s["done"] / (elapsed / 60)
        pending = s["total"] - s["done"] - s["failed"] - s["skipped"]
        eta = round(pending / rate * 60) if rate > 0.01 else None
        bus.publish({"type": "stats", "done": s["done"], "failed": s["failed"],
                     "skipped": s["skipped"], "chars": s["chars"],
                     "total": s["total"], "cached": s["cached"],
                     "rate_per_min": round(rate, 2), "eta_sec": eta})


class AdaptiveGateProxy:
    """包一层, 使 `async with crawler.gate` 与 record() 转发到 core.throttle。"""

    def __init__(self, cfg: CrawlConfig) -> None:
        from harvest.core.throttle import AdaptiveGate
        self._gate = AdaptiveGate(min_interval=cfg.min_interval, ceiling=cfg.ceiling,
                                  adaptive=cfg.adaptive)

    def __getattr__(self, name):
        return getattr(self._gate, name)

    async def __aenter__(self):
        return await self._gate.__aenter__()

    async def __aexit__(self, *a):
        return await self._gate.__aexit__(*a)

    def record(self, ok: bool, latency: float | None = None,
               retry_after: float | None = None) -> None:
        self._gate.record(ok, latency, retry_after)


def _is_target_closed(e: Exception) -> bool:
    name = type(e).__name__
    return "TargetClosed" in name or "Target page" in str(e) or "Target closed" in str(e)


_ERR_PAGE_SIG = re.compile(
    r"ERR_CONNECTION_CLOSED|ERR_INTERNET_DISCONNECTED|ERR_NAME_NOT_RESOLVED|"
    r"ERR_TIMED_OUT|ERR_CONNECTION_RESET|ERR_CONNECTION_REFUSED|无法访问此网站|意外终止了连接")


def _is_browser_error_page(md: str) -> bool:
    """Chromium 断网/断连错误页被当成正文提取 — 判失败待重爬 (终极测试教训)。"""
    return bool(md) and bool(_ERR_PAGE_SIG.search(md[:2000]))


_STRONG_REFS: set = set()
