# -*- coding: utf-8 -*-
"""api.routes — REST + SSE 路由 (薄适配层: 解析请求 → 调用管线 → 返回/推流)。"""
from __future__ import annotations

import asyncio
from pathlib import Path
from urllib.parse import urlparse

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from harvest import config
from harvest.events import bus
from harvest.pipeline.crawler import Crawler
from harvest.store.db import Store
from harvest.transport import browser

router = APIRouter(prefix="/api")

# 进程级单例 (uvicorn 单 worker 假设)
crawler = Crawler(config.get())


def _busy() -> str | None:
    if crawler.running:
        return "已有任务进行中"
    return None


import ipaddress

_ALLOWED_SUFFIXES = ("feishu.cn", "larksuite.com", "f.mioffice.cn")


def _valid_target_url(url: str) -> str | None:
    """校验目标 URL: 仅 http/https, host 必须为飞书/ Lark 域名 (拒绝 localhost/私网/保留地址)。"""
    try:
        u = urlparse(url)
    except Exception:
        return "URL 解析失败"
    if u.scheme not in ("http", "https"):
        return "仅支持 http/https"
    host = (u.hostname or "").lower()
    if not host:
        return "缺少 host"
    if not any(host == s or host.endswith("." + s) for s in _ALLOWED_SUFFIXES):
        return f"仅支持飞书域名 ({', '.join(_ALLOWED_SUFFIXES)})"
    try:  # 字面 IP 一律拒绝 (私有/环回/保留地址防护)
        ip = ipaddress.ip_address(host)
        if ip.is_loopback or ip.is_private or ip.is_reserved or ip.is_link_local:
            return "拒绝内网/环回地址"
    except ValueError:
        pass  # 域名 (已按白名单校验)
    return None


# ---------- 基础 ----------
@router.get("/health")
async def health():
    return {"status": "ok", "version": "2.0.0", "busy": crawler.running}


@router.get("/status")
async def status():
    store: Store = crawler.store
    counts = store.counts()
    last = None
    row = store._conn.execute(
        "SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()
    if row:
        last = {"id": row["id"], "root_url": row["root_url"], "status": row["status"],
                "started_at": row["started_at"], "finished_at": row["finished_at"]}
    return {"busy": crawler.running, "nodes": counts, "last_run": last,
            "last_result": crawler.last_result or None,
            "login": await _login_state_safe()}


async def _login_state_safe():
    try:
        if browser.service.page is not None:
            return await browser.check_login_state(browser.service.page)
    except Exception:
        pass
    return None


# ---------- 配置 ----------
@router.get("/config")
async def get_config():
    return config.get().to_dict()


class ConfigReq(BaseModel):
    output_dir: str | None = None
    export_format: str | None = None
    tabs: int | None = None
    min_interval: float | None = None
    ceiling: float | None = None
    adaptive: bool | None = None
    embed_images: bool | None = None
    download_attachments: bool | None = None
    pdf_image_width: int | None = None
    unsupported_policy: str | None = None
    bitable_all_views: bool | None = None
    video_as_link: bool | None = None
    clean: bool | None = None


_RANGE_CHECKS = {  # 字段 → (最小值, 最大值)
    "tabs": (1, 6), "min_interval": (0.3, 30), "ceiling": (5, 300),
    "nav_timeout": (10, 180), "render_timeout": (5, 60),
    "extract_retries": (0, 3), "image_concurrency": (1, 8),
    "pdf_image_width": (600, 3000),
}


@router.post("/config")
async def set_config(req: ConfigReq):
    over = {k: v for k, v in req.model_dump().items() if v is not None}
    if "export_format" in over and over["export_format"] not in ("md", "pdf"):
        return {"ok": False, "error": "export_format 仅支持 md / pdf"}
    for k, (lo, hi) in _RANGE_CHECKS.items():
        if k in over and not (lo <= over[k] <= hi):
            return {"ok": False, "error": f"{k} 取值范围 {lo}-{hi}"}
    if "unsupported_policy" in over and over["unsupported_policy"] not in ("placeholder", "skip"):
        return {"ok": False, "error": "unsupported_policy 仅支持 placeholder / skip"}
    cfg = config.update(**over)
    crawler.cfg = cfg
    from harvest.core.throttle import AdaptiveGate
    crawler.gate = type(crawler.gate)(cfg)  # 应用新节流参数
    return {"ok": True, "config": cfg.to_dict()}


# ---------- 登录 ----------
class LoginReq(BaseModel):
    timeout_sec: int = 600
    url: str | None = None  # 登录落地页: 传业务 URL 可借飞书重定向获得正确的登录参数


@router.post("/login/start")
async def login_start(req: LoginReq):
    if err := _busy():
        return {"ok": False, "error": err}
    if req.url and (err2 := _valid_target_url(req.url)):
        return {"ok": False, "error": err2}
    asyncio.create_task(_login_flow(req.timeout_sec, req.url))
    return {"ok": True, "detail": "已打开浏览器窗口, 请扫码登录"}


async def _login_flow(timeout_sec: int, land_url: str | None = None) -> None:
    bus.publish({"type": "login", "state": "waiting"})
    try:
        await browser.service.ensure_context()
        # goto 业务页 (或首页), 由飞书重定向到官方登录页 —
        # 硬编码 passport URL 会因参数失效报 4401, 让飞书自己生成最可靠
        await browser.service.page.goto(land_url or "https://feishu.cn", timeout=30000)
    except Exception:
        pass
    deadline = asyncio.get_event_loop().time() + timeout_sec
    while asyncio.get_event_loop().time() < deadline:
        page = browser.service.page
        url = page.url if page is not None else ""
        state = await _login_state_safe()
        # 成功判定: JS 特征明确已登录, 或页面已离开登录域 (扫码后 passport 会跳转)
        left_login = bool(url) and not any(
            k in url for k in ("accounts.", "passport", "login"))
        if state is True or (left_login and state is not False):
            bus.publish({"type": "login", "state": "ok"})
            return
        await asyncio.sleep(3)
    bus.publish({"type": "login", "state": "timeout"})


@router.post("/login/clear")
async def login_clear():
    if err := _busy():
        return {"ok": False, "error": err}
    await browser.service.clear_login()
    bus.publish({"type": "login", "state": "cleared"})
    return {"ok": True, "detail": "登录态已清除"}


@router.post("/browser/close")
async def browser_close():
    await browser.service.close()
    return {"ok": True}


# ---------- 树 ----------
@router.get("/tree/roots")
async def tree_roots():
    rows = crawler.store._conn.execute(
        "SELECT root_token, COUNT(*) n FROM nodes WHERE root_token != '' "
        "GROUP BY root_token").fetchall()
    return {"ok": True, "roots": [{"token": r["root_token"], "count": r["n"]}
                                  for r in rows]}


@router.get("/tree")
async def tree(root: str = ""):
    nodes = crawler.store.nodes_by_root(root)
    by_tok = {n["token"]: {**n, "children": []} for n in nodes}
    roots = []
    for n in nodes:
        parent = by_tok.get(n["parent"])
        if parent and n["token"] != n["parent"]:
            parent["children"].append(by_tok[n["token"]])
        else:
            roots.append(by_tok[n["token"]])

    def prune(t):
        t.pop("content_hash", None)
        for c in t["children"]:
            prune(c)
    for r in roots:
        prune(r)
    return {"ok": True, "tree": roots, "flat": nodes, "total": len(nodes)}


# ---------- 爬取 ----------
class CrawlReq(BaseModel):
    url: str
    tokens: list[str] | None = None
    fresh: bool = False
    # 局部覆盖 (不落盘, 仅本次任务)
    output_dir: str | None = None
    export_format: str | None = None
    tabs: int | None = None


@router.post("/crawl/start")
async def crawl_start(req: CrawlReq):
    if err := _busy():
        return {"ok": False, "error": err}
    if err2 := _valid_target_url(req.url):
        return {"ok": False, "error": err2}
    cfg = config.get().merged(**req.model_dump(
        include={"output_dir", "export_format", "tabs"}))
    crawler.cfg = cfg
    crawler.start(req.url, tokens=req.tokens, fresh=req.fresh)
    return {"ok": True, "detail": "任务已启动", "tabs": cfg.tabs}


@router.post("/crawl/stop")
async def crawl_stop():
    crawler.stop()
    return {"ok": True, "detail": "停止信号已发送, 任务将在当前页面完成后退出"}


@router.post("/tree/reorganize")
async def tree_reorganize(root: str = ""):
    """把已爬取的平铺输出按库中树重排进层级目录 (不重新爬取)。

    适用: 层级修复前爬出的历史输出; 依赖库中已有该 root 的节点父子关系。
    """
    if err := _busy():
        return {"ok": False, "error": err}
    nodes = crawler.store.nodes_by_root(root)
    if not nodes:
        return {"ok": False, "error": "库中无该知识库节点, 请先运行一次采撷"}
    from harvest.pipeline.crawler import BASE_DIR
    from harvest.pipeline.writers import reorganize_flat_output
    pages = [{"token": n["token"], "title": n["title"], "parent": n["parent"],
              "level": n["level"]} for n in nodes]
    out_dir = crawler.cfg.output_path(BASE_DIR)
    return reorganize_flat_output(out_dir, pages)


# ---------- 诊断 ----------
@router.get("/debug/recon")
async def debug_recon(url: str, sample_per_type: int = 2):
    if err := _valid_target_url(url):
        return {"ok": False, "error": err}
    """格式侦察: 采集导航树全量节点 → 逐节点查询 obj_type → 每类抽样访问收集块类型。

    用于评估目标知识库的格式构成 (bitable/sheet/file/folder 等), 不产生输出文件。
    """
    from harvest.legacy.files import _space_id_js
    from harvest.pipeline.discover import _scrape_nodes

    await browser.service.ensure_context()
    page = browser.service.page
    domain = urlparse(url).netloc
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
    except Exception:
        pass
    await asyncio.sleep(8)

    nodes = await _scrape_nodes(page, domain, deep=True)
    bus.publish({"type": "recon", "stage": "tree", "total": len(nodes)})
    space_id = await page.evaluate(_space_id_js())

    details: list[dict] = []
    type_counts: dict = {}
    if space_id:
        for n in nodes[:800]:
            try:
                jn = await page.evaluate(
                    "async (u) => { const r = await fetch(u, {credentials:'include'});"
                    " return await r.json(); }",
                    f"https://{domain}/space/api/wiki/v2/tree/get_node/"
                    f"?wiki_token={n['token']}&space_id={space_id}"
                    f"&expand_shortcut=true&with_deleted=true")
                d = (jn or {}).get("data") or {}
                ot = d.get("obj_type")
            except Exception:
                ot = None
            n["obj_type"] = ot
            n["obj_token"] = (d or {}).get("obj_token", "")
            type_counts[str(ot)] = type_counts.get(str(ot), 0) + 1
            details.append(n)
            await asyncio.sleep(0.05)
    bus.publish({"type": "recon", "stage": "typed", "types": type_counts})

    # 每类抽样访问 → 收集内容块类型
    by_type: dict = {}
    for n in details:
        by_type.setdefault(str(n.get("obj_type")), []).append(n)
    samples = []
    for ot, group in by_type.items():
        for n in group[:max(1, sample_per_type)]:
            try:
                await page.goto(n["url"], wait_until="domcontentloaded", timeout=30000)
            except Exception:
                pass
            await asyncio.sleep(4)
            try:
                info = await page.evaluate("""() => {
                    const bts = new Set();
                    document.querySelectorAll('[data-block-type]').forEach(
                        el => bts.add(el.getAttribute('data-block-type')));
                    return {
                        blocks: [...bts],
                        video: document.querySelectorAll('video').length,
                        iframe: document.querySelectorAll('iframe').length,
                        canvas: document.querySelectorAll('canvas').length,
                        renderLen: (document.querySelector('[class*="render-unit"]')||{innerText:''}).innerText.length,
                        url: location.href
                    };
                }""")
            except Exception as e:
                info = {"error": str(e)}
            samples.append({"obj_type": ot, "title": n["title"], **info})
            await asyncio.sleep(1)
    return {"ok": True, "space_id": space_id, "node_total": len(nodes),
            "type_counts": type_counts, "samples": samples}


class EvalReq(BaseModel):
    url: str
    js: str


@router.post("/debug/eval")
async def debug_eval(req: EvalReq):
    """诊断用: 打开页面后执行自定义 JS 并返回结果 (仅本地调试用)。"""
    if err := _valid_target_url(req.url):
        return {"ok": False, "error": err}
    await browser.service.ensure_context()
    page = browser.service.page
    try:
        await page.goto(req.url, wait_until="domcontentloaded", timeout=40000)
    except Exception:
        pass
    await asyncio.sleep(5)
    try:
        result = await page.evaluate(req.js)
    except Exception as e:
        result = {"error": str(e)}
    return {"ok": True, "result": result}


@router.get("/debug/embed_mine")
async def debug_embed_mine(url: str):
    """诊断: 硬加载页面后返回 init script 捕获的 docx blocks 片段 (挖内嵌表三元组)。"""
    if err := _valid_target_url(url):
        return {"ok": False, "error": err}
    await browser.service.ensure_context()
    page = browser.service.page
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=40000)
    except Exception:
        pass
    await asyncio.sleep(8)
    cap = await page.evaluate("() => window.__fh_cap || []")
    return {"ok": True, "n": len(cap), "cap": cap}


@router.get("/debug/bitable")
async def debug_bitable(url: str, page_url: str = ""):
    """诊断: 在当前页面对指定 bitable URL 直跑 to_markdown, 返回真实异常。"""
    import traceback
    from harvest.legacy import bitable as _bt
    await browser.service.ensure_context()
    page = browser.service.page
    if page_url:
        try:
            await page.goto(page_url, wait_until="domcontentloaded", timeout=40000)
            await asyncio.sleep(5)
        except Exception:
            pass
    try:
        md, blob = await _bt.to_markdown(page, url, all_views=False)
        return {"ok": True, "md_len": len(md), "head": md[:300]}
    except Exception:
        return {"ok": False, "traceback": traceback.format_exc()[-800:]}


@router.get("/debug/visit")
async def debug_visit(url: str):
    """打开页面并返回登录检测各分支的证据 (定位误判用)。"""
    if err := _valid_target_url(url):
        return {"ok": False, "error": err}
    await browser.service.ensure_context()
    page = browser.service.page
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=40000)
    except Exception:
        pass
    await asyncio.sleep(5)
    info = await page.evaluate("""() => {
        const ru = document.querySelector('[class*="render-unit"]');
        return {
            title: document.title,
            hasRenderUnit: !!ru,
            renderLen: ru ? ru.innerText.length : -1,
            hasPwd: !!document.querySelector('input[type="password"]'),
            hasQr: !!document.querySelector('[class*="qrcode"]'),
            hasAvatar: !!document.querySelector('[class*="user-avatar"],[class*="sdk-user"],[class*="user-menu"]'),
            bodyLen: document.body ? document.body.innerText.length : 0,
            bodyHead: (document.body ? document.body.innerText : '').slice(0, 150)
        };
    }""")
    state = await browser.check_login_state(page)
    return {"ok": True, "final_url": page.url, "state": state, "info": info}


# ---------- SSE ----------
@router.get("/events")
async def events():
    import json as _json

    async def gen():
        async for ev in bus.stream():
            yield f"data: {_json.dumps(ev, ensure_ascii=False)}\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})
