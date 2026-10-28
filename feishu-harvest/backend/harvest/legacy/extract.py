# -*- coding: utf-8 -*-
"""legacy.extract — 单页正文提取 (自 v22 extract_page 移植)。

流程: 条件等待渲染稳定 → 展开折叠/禁用 content-visibility → 滚动触发懒加载
→ 视口拉高 → blob 图片捕获 → HTML 提取 → 块转换 → 质量判定(innerText 回退)
→ 代码块恢复。
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import re
import time
from pathlib import Path

from bs4 import BeautifulSoup

from harvest.textutil import strip_zws
from . import mdconvert as mc
from . import bitable


async def _collect_embedded(page, video_links: bool = True) -> str:
    """收集 DOM 提取覆盖不到的嵌入内容: iframe / video / base_refer 内嵌表格。

    返回附加 markdown 段落 (可为空)。飞书页面链接一律纯文本 (本地不可打开)。
    """
    parts: list[str] = []
    try:
        media = await page.evaluate("""() => {
            const iframes = [...document.querySelectorAll('iframe')]
                .map(f => f.src || '').filter(s => s && !/session-tools|\\/refresh/.test(s));
            const videos = [...document.querySelectorAll('video')]
                .map(v => v.src || v.querySelector('source')?.src || '').filter(Boolean);
            const bases = [...document.querySelectorAll('[data-block-type="base_refer"]')]
                .map(el => ({
                    attrs: [...el.attributes].map(a => a.name + '=' + (a.value || '')),
                    text: el.innerText.slice(0, 100)
                }));
            return {iframes, videos, bases};
        }""")
    except Exception:
        media = {"iframes": [], "videos": [], "bases": []}

    if media.get("iframes"):
        parts.append("**嵌入页面**:")
        for s in dict.fromkeys(media["iframes"]):
            parts.append(f"- {s}")
        parts.append("")
    if media.get("videos") and video_links:
        parts.append("**视频**:")
        for s in dict.fromkeys(media["videos"]):
            parts.append(f"- 视频地址: {s}")
        parts.append("")

    # 内嵌多维表格 (base_refer): 属性不可靠, 改为资源时序发现 —
    # 页面加载内嵌表必发 /space/api/v1/bitable/{base}/records?tableId=..&viewId=.. 请求,
    # 每枚唯一三元组 = 一张表, 走 bitable API 直采拿到真实表格数据
    try:
        triples = await page.evaluate("""() => {
            const seen = new Map();
            for (const u of performance.getEntriesByType('resource').map(e => e.name)) {
                const m = u.match(/\\/space\\/api\\/v1\\/bitable\\/([^/?#]+)\\/records/);
                if (!m) continue;
                const base = m[1];
                const t = u.match(/[?&]table[Ii][Dd]=([^&]+)/);
                const v = u.match(/[?&]view[Ii][Dd]=([^&]+)/);
                const key = base + '|' + (t ? t[1] : '');
                if (!seen.has(key)) seen.set(key, {base, tableId: t ? t[1] : '', viewId: v ? v[1] : ''});
            }
            return [...seen.values()];
        }""")
    except Exception:
        triples = []
    if triples:
        from urllib.parse import urlparse as _up
        try:
            origin = f"{_up(page.url).scheme}://{_up(page.url).netloc}"
        except Exception:
            origin = ""
        for t in triples[:5]:  # 单页内嵌表上限, 防极端页面失控
            if not (origin and t.get("base") and t.get("tableId")):
                continue
            url = f"{origin}/base/{t['base']}?table={t['tableId']}"
            if t.get("viewId"):
                url += f"&view={t['viewId']}"
            md, _ = await bitable.to_markdown(page, url, all_views=False)
            if md:
                parts.append(md)
                parts.append("")
    return "\n".join(parts)


async def wait_content_ready(page, timeout: float = 25.0, settle_rounds: int = 3) -> int:
    """轮询内容区 innerText 长度直到连续 settle_rounds 次稳定; 返回最终长度 (-1=内容区未出现)。"""
    last_len, stable = -1, 0
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            cur = await page.evaluate(
                "() => { const el = document.querySelector('[class*=\"render-unit\"]');"
                " return el ? el.innerText.length : -1; }")
        except Exception:
            cur = -1
        if cur < 0:
            await asyncio.sleep(2)
            continue
        if cur == last_len:
            stable += 1
            if stable >= settle_rounds:
                return cur
        else:
            stable, last_len = 0, cur
        await asyncio.sleep(2)
    return last_len


async def _capture_blobs(page, images_dir: Path) -> dict:
    """blob: 图片 → canvas 绘制 / fetch 回退, 落盘并返回 {blob_url: 相对路径}。"""
    results = await page.evaluate("""
        async () => {
            const imgs = document.querySelectorAll('img[src^="blob:"]');
            const rs = [];
            for (const img of imgs) {
                const s = img.getAttribute('src');
                if (!s) continue;
                let d = null;
                try {
                    if (img.naturalWidth > 0) {
                        const c = document.createElement('canvas');
                        c.width = img.naturalWidth; c.height = img.naturalHeight;
                        c.getContext('2d').drawImage(img, 0, 0);
                        d = c.toDataURL('image/png');
                    }
                } catch (e) {}
                if (!d) {
                    try {
                        const r = await fetch(s);
                        const b = await r.blob();
                        if (b.size > 0) d = await new Promise(res => {
                            const fr = new FileReader();
                            fr.onloadend = () => res(fr.result);
                            fr.readAsDataURL(b);
                        });
                    } catch (e) {}
                }
                rs.push({url: s, data: d});
            }
            return rs;
        }""")
    mapping = {}
    for r in results or []:
        data = r.get("data") or ""
        if data and "," in data:
            raw = base64.b64decode(data.split(",", 1)[1])
            if len(raw) > 100:
                name = hashlib.md5(r["url"].encode()).hexdigest()[:12] + ".png"
                (images_dir / name).write_bytes(raw)
                mapping[r["url"]] = f"images/{name}"
    return mapping


async def extract_page(page, images_dir: Path, render_timeout: float = 25.0,
                       video_links: bool = True) -> tuple[str, dict]:
    """提取当前页面 → (markdown, blob_mapping)。质量判定/回退策略与 v22 一致。

    提取主链路之外附加收集 iframe / video / base_refer 内嵌表格 (DOM 块转换覆盖不到)。
    """
    images_dir.mkdir(parents=True, exist_ok=True)
    await wait_content_ready(page, timeout=render_timeout, settle_rounds=3)

    # 展开折叠 + 禁用 content-visibility (两轮)
    # 点击限定非链接元素: <a target=_blank> 被 click 会弹空白页 (终极测试教训)
    expand_js = """
        () => {
            const safeClick = (el) => {
                if (el.closest('a, [target="_blank"]')) return;
                try { el.click(); } catch (e) {}
            };
            document.querySelectorAll('[aria-expanded="false"]').forEach(safeClick);
            document.querySelectorAll('details:not([open])').forEach(el => {try{el.setAttribute('open','')}catch(e){}});
            document.querySelectorAll('*').forEach(el => {
                try{el.style.setProperty('content-visibility','visible','important');
                    el.style.setProperty('contain','none','important');
                    el.style.setProperty('contain-intrinsic-size','none','important')}catch(e){}
            });
        }"""
    await page.evaluate(expand_js)
    await asyncio.sleep(0.5)

    # 分段滚动触发懒加载
    await page.evaluate(
        "async()=>{const s=400;const m=Math.max(document.body.scrollHeight,30000);"
        "for(let y=0;y<m;y+=s){window.scrollTo(0,y);await new Promise(r=>setTimeout(r,60))}"
        "window.scrollTo(0,0);await new Promise(r=>setTimeout(r,300))}")
    await asyncio.sleep(1)

    await page.keyboard.press("End")
    await asyncio.sleep(1.5)
    await page.keyboard.press("Home")
    await asyncio.sleep(0.5)
    for _ in range(6):
        await page.keyboard.press("PageDown")
        await asyncio.sleep(0.4)

    await page.evaluate("""
        () => {
            document.querySelectorAll('[aria-expanded="false"]').forEach(el => {
                if (el.closest('a, [target="_blank"]')) return;
                try{el.click()}catch(e){}
            });
            document.querySelectorAll('*').forEach(el => {
                try{el.style.setProperty('content-visibility','visible','important');
                    el.style.setProperty('contain','none','important')}catch(e){}
            });
        }""")
    await asyncio.sleep(0.5)

    # 视口拉高避免 fixed 元素遮挡, 中段触发渲染后复位
    orig = page.viewport_size
    await page.set_viewport_size({"width": 1400, "height": 15000})
    await asyncio.sleep(4)
    await page.evaluate(
        "async()=>{const m=document.body.scrollHeight/2;window.scrollTo(0,m);"
        "await new Promise(r=>setTimeout(r,1000));window.scrollTo(0,0);"
        "await new Promise(r=>setTimeout(r,500))}")
    await asyncio.sleep(1)

    blob_map = await _capture_blobs(page, images_dir)

    html = await page.evaluate("""
        () => {
            for (const s of ['[class*="render-unit-wrapper"]','[class*="render-unit"]',
                             '.page-block-children','[class*="wiki-content"]',
                             '[class*="doc-content"]','article','main']) {
                const e = document.querySelector(s);
                if (e && e.innerHTML.length > 200) return e.outerHTML;
            }
            return document.body.innerHTML;
        }""")
    full_text = await page.evaluate("""
        () => {
            for (const s of ['[class*="render-unit"]','.page-block-children',
                             '[class*="wiki-content"]','[class*="doc-content"]',
                             'article','main']) {
                const e = document.querySelector(s);
                if (e && e.innerText && e.innerText.length > 100) return e.innerText;
            }
            return document.body.innerText || '';
        }""")
    full_text = mc.strip_nav_shell(strip_zws(full_text or ""))
    await page.set_viewport_size(orig or {"width": 1400, "height": 900})

    if not html or len(html) < 100:
        if full_text and len(full_text) > 50:
            return re.sub(r"\n{3,}", "\n\n", full_text), blob_map
        return "", blob_map

    soup = BeautifulSoup(html, "lxml")
    wrapper = (soup.find(class_=re.compile(r"render-unit-wrapper"))
               or soup.find(class_=re.compile(r"render-unit"))
               or soup.find(class_="page-block-children"))
    if not wrapper:
        if full_text and len(full_text) > 50:
            return re.sub(r"\n{3,}", "\n\n", full_text), blob_map
        return "", blob_map

    result = "\n\n".join(p.strip() for p in mc.extract_blocks(wrapper) if p.strip())
    result = mc.strip_zws(re.sub(r"\n{4,}", "\n\n\n", result))

    # 质量判定: 代码页/表格页永久走 HTML; 内嵌图片可能来自导航, 导航壳时不回退
    has_table = "| ---" in result
    has_links = result.count("](") >= 3
    has_code = result.count("```") >= 4
    use_inner = False
    if not mc.has_nav_shell(result) and full_text and not has_code:
        ratio = len(full_text) / max(len(result), 1)
        if len(result) < 4000 and ratio > 3.0:
            use_inner = True
        elif not (has_table and has_links) and ratio > 1.5:
            use_inner = True
    if use_inner:
        html_images = re.findall(r"!\[([^\]]*)\]\(([^)]+)\)", result)
        result = re.sub(r"\n{3,}", "\n\n", full_text)
        appended = [f"![{alt}]({src})" for alt, src in html_images
                    if src not in result]
        if appended:
            result += "\n\n---\n\n" + "\n".join(appended)

    result = mc.recover_code_blocks(result)
    result = mc.repair_fences(result)

    # 内嵌多维表格是懒加载 iframe: 逐块 scrollIntoView 停留, 强制触发其 records 请求
    # (网络层监听器在 crawler 侧捕获三元组 → 后续 to_markdown 直采)
    try:
        n_embeds = await page.evaluate("""async () => {
            const blocks = [...document.querySelectorAll('[data-block-type="base_refer"]')];
            for (const b of blocks.slice(0, 5)) {
                try { b.scrollIntoView({block: 'center'}); } catch (e) {}
                await new Promise(r => setTimeout(r, 2500));
            }
            return blocks.length;
        }""")
        if n_embeds:
            await asyncio.sleep(2)  # 最后停留, 让尾部 records 请求发出
    except Exception:
        pass

    extras = await _collect_embedded(page, video_links=video_links)
    if extras:
        result = f"{result}\n\n---\n\n{extras}"
    return result, blob_map
