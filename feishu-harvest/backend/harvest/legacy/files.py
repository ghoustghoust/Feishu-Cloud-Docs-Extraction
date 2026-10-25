# -*- coding: utf-8 -*-
"""legacy.files — 文件管理页 / 文件节点 / 附件与图片下载 (自 v22 移植)。

三级降级下载: download/all (页面内 fetch + CSRF) → page.request →
预览转码 PDF 流 (DLP 禁用下载时唯一可读形态)。
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import re
from pathlib import Path
from urllib.parse import unquote, urlparse

from harvest.textutil import sfn

BASE_DIR = Path(__file__).resolve().parent.parent.parent  # backend/

# Content-Type → 扩展名兜底
_CT_EXT = {
    "application/pdf": ".pdf",
    "application/msword": ".doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.ms-powerpoint": ".ppt",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    "application/vnd.ms-excel": ".xls",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "text/csv": ".csv",
    "application/zip": ".zip",
    "application/epub+zip": ".epub",
    "video/mp4": ".mp4",
    "image/png": ".png",
    "image/jpeg": ".jpg",
}

_IMG_EXT = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp")


async def download_img(page, url: str, images_dir: Path) -> str | None:
    """图片下载: URL→md5 命名去重, 命中缓存直接返回相对路径。"""
    try:
        if not url or url.startswith("data:") or len(url) < 10:
            return None
        h = hashlib.md5(url.encode()).hexdigest()[:12]
        ext = Path(urlparse(url).path).suffix or ".png"
        if ext not in _IMG_EXT:
            ext = ".png"
        name = f"{h}{ext}"
        target = images_dir / name
        if target.exists():
            return f"images/{name}"
        r = await page.request.get(url, timeout=15000)
        if r.ok:
            body = await r.body()
            if len(body) > 50:
                images_dir.mkdir(parents=True, exist_ok=True)
                target.write_bytes(body)
                return f"images/{name}"
        return None
    except Exception:
        return None


async def _page_fetch_json(page, url: str) -> dict | None:
    try:
        return await page.evaluate(
            "async (u) => { const r = await fetch(u, {credentials:'include'});"
            " return await r.json(); }", url)
    except Exception:
        return None


def _space_id_js() -> str:
    return """() => {
        for (const u of performance.getEntriesByType('resource').map(e => e.name)) {
            const m = u.match(/tree\\/get_node\\/.*?space_id=(\\d+)/);
            if (m) return m[1];
        }
        return null;
    }"""


async def _get_node(page, origin: str, wiki_token: str) -> dict | None:
    """wiki token → {space_id, obj_token, obj_type, title} (space_id 来自 resource timing)。"""
    try:
        space_id = await page.evaluate(_space_id_js())
        if not space_id:
            return None
        jn = await _page_fetch_json(
            page, f"{origin}/space/api/wiki/v2/tree/get_node/?wiki_token={wiki_token}"
                  f"&space_id={space_id}&expand_shortcut=true&with_deleted=true")
        if not jn or jn.get("code") != 0 or not jn.get("data"):
            return None
        d = jn["data"]
        return {"space_id": space_id, "obj_token": d.get("obj_token", ""),
                "obj_type": d.get("obj_type"), "title": d.get("title", "")}
    except Exception:
        return None


async def file_node_info(page, url: str) -> dict | None:
    """wiki URL → 文件节点信息 (仅 obj_type=12 返回); 非文件节点返回 None。"""
    try:
        origin = f"{urlparse(url).scheme}://{urlparse(url).netloc}"
        m = re.search(r"/wiki/([^/?#]+)", urlparse(url).path)
        if not m:
            return None
        node = await _get_node(page, origin, m.group(1))
        if not node or not node["obj_token"] or node["obj_type"] != 12:
            return None
        return node
    except Exception:
        return None


async def _fetch_file_bytes(page, url: str) -> tuple[bytes | None, str, str, str]:
    """页面内 fetch 下载文件 (box/stream/download 校验 CSRF, 必须在页面上下文发起)。
    返回 (body, content_type, content_disposition, 诊断信息)。"""
    r = await page.evaluate(r"""async (u) => {
        try {
            const m = document.cookie.match(/(?:^|; )bv_csrf_token=([^;]+)/);
            const headers = {};
            if (m) headers['x-csrf-token'] = decodeURIComponent(m[1]);
            const resp = await fetch(u, {credentials: 'include', headers});
            const ct = resp.headers.get('content-type') || '';
            const cd = resp.headers.get('content-disposition') || '';
            if (!resp.ok) return {err: 'HTTP ' + resp.status + ' ' + (await resp.text()).slice(0, 100)};
            if (/text\/html|application\/json/.test(ct)) return {err: 'ctype ' + ct};
            const b = await resp.blob();
            const data = await new Promise((res) => {
                const fr = new FileReader();
                fr.onloadend = () => res(fr.result);
                fr.readAsDataURL(b);
            });
            return {data: data, ct: ct, cd: cd};
        } catch (e) { return {err: String(e)}; }
    }""", url)
    if not isinstance(r, dict) or r.get("err") or not r.get("data"):
        return None, "", "", (r.get("err") if isinstance(r, dict) else str(r))
    d = r["data"]
    if "," not in d:
        return None, "", "", "bad data url"
    return base64.b64decode(d.split(",", 1)[1]), r.get("ct", ""), r.get("cd", ""), ""


async def _preview_pdf_url(page, origin: str, obj_token: str) -> str | None:
    """meta API 取版本号 → preview_type=9 转码 PDF 流 URL。
    version 是 19 位整数超 JS Number 精度, 必须对原始文本正则。"""
    try:
        ver = await page.evaluate(
            """async (u) => {
                const r = await fetch(u, {credentials: 'include'});
                const t = await r.text();
                const m = t.match(/"version"\\s*:\\s*"?([0-9]{15,})"?/);
                return m ? m[1] : null;
            }""",
            f"{origin}/space/api/meta/?token={obj_token}&type=12&need_extra_fields=3")
        if not ver:
            return None
        return (f"https://internal-api-drive-stream.feishu.cn/space/api/box/stream/download/"
                f"preview/{obj_token}?preview_type=9&version={ver}&mount_point=wiki")
    except Exception:
        return None


async def download_wiki_file(page, origin: str, obj_token: str, title: str,
                             dest_dir: Path) -> tuple[str | None, str]:
    """文件节点 → 带登录态下载原始文件; 兜底预览转码 PDF。
    返回 (保存路径, via='origin'|'preview'); 失败 (None, '')。"""
    try:
        if not obj_token:
            return None, ""
        url = f"{origin}/space/api/box/stream/download/all/{obj_token}?type=12"
        body, ctype, cd, diag = await _fetch_file_bytes(page, url)
        via = "origin"
        if body is None:
            try:  # 降级 1: ctx.request (无 CSRF 头, 部分租户可用)
                r = await page.request.get(url, timeout=120000, max_redirects=10)
                ct0 = (r.headers.get("content-type") or "").lower().split(";")[0].strip()
                if r.ok and ct0 not in ("text/html", "application/json"):
                    body, ctype, cd = await r.body(), ct0, r.headers.get("content-disposition", "")
            except Exception:
                pass
        if body is None:  # 降级 2: 预览转码 PDF 流
            purl = await _preview_pdf_url(page, origin, obj_token)
            if purl:
                body, ctype, cd, diag = await _fetch_file_bytes(page, purl)
                via = "preview"
        if not body or len(body) < 1024:
            return None, ""
        ctype = ctype.lower().split(";")[0].strip()
        name = sfn(title or "文件", ml=120)
        base, ext = Path(name).stem, Path(name).suffix
        if via == "preview" and "pdf" in ctype:
            ext = ".pdf"
        if not ext:
            m = re.search(r"filename\*?=(?:UTF-8''|\")?([^\";]+)", cd, re.I)
            if m:
                ext = Path(unquote(m.group(1))).suffix
        if not ext:
            ext = _CT_EXT.get(ctype, ".bin")
        dest = Path(dest_dir)
        dest.mkdir(parents=True, exist_ok=True)
        p = dest / f"{base}{ext}"
        i = 1
        while p.exists():
            p = dest / f"{base}_{i}{ext}"
            i += 1
        p.write_bytes(body)
        return str(p), via
    except Exception:
        return None, ""


async def folder_manager_to_md(page, url: str, download_dir: Path | None = None,
                               download_file=None) -> str:
    """文件管理页 (obj_type=22) → Markdown 附件列表。
    download_dir + download_file 提供时同时真实下载每个附件 (download_file 注入
    core 的并发下载器, 由管线层决定并发度 — 本模块保持串行默认)。"""
    try:
        origin = f"{urlparse(url).scheme}://{urlparse(url).netloc}"
        m = re.search(r"/wiki/([^/?#]+)", urlparse(url).path)
        if not m:
            return ""
        wiki_token = m.group(1)
        space_id = await page.evaluate(_space_id_js())
        if not space_id:
            return ""
        jn = await _page_fetch_json(
            page, f"{origin}/space/api/wiki/v2/tree/get_node/?wiki_token={wiki_token}"
                  f"&space_id={space_id}&expand_shortcut=true&with_deleted=true")
        if not jn or jn.get("code") != 0 or not jn.get("data"):
            return ""
        obj_token = jn["data"].get("obj_token", "")
        if not obj_token or jn["data"].get("obj_type") != 22:
            return ""

        items: list[dict] = []
        last_label = ""
        while True:  # 分页: has_more + last_label 游标
            cu = (f"{origin}/space/api/wiki/v2/tree/get_node_child_paging_v2/"
                  f"?length=100&rank=1&need_user=true&need_explorer_info=true"
                  f"&thumbnail_width=1028&thumbnail_height=1028&thumbnail_policy=4&asc=false"
                  f"&wiki_token={wiki_token}&obj_token={obj_token}&obj_type=22")
            if last_label:
                cu += f"&last_label={last_label}"
            r = await _page_fetch_json(page, cu)
            if not r or r.get("code") != 0 or not r.get("data"):
                break
            d = r["data"]
            nodes_map = d.get("nodes") or {}
            for k in d.get("node_list") or []:
                n = nodes_map.get(k) if isinstance(k, str) else k
                if isinstance(n, dict) and n.get("title"):
                    items.append(n)
            if not d.get("has_more") or not d.get("last_label"):
                break
            last_label = d["last_label"]
        if not items:
            return ""

        md = [f"<!-- 附件列表 {wiki_token} · {len(items)} 个文件 -->", ""]
        for n in items:
            title = (n.get("title") or "").strip()
            obj_t = n.get("obj_token", "")
            saved = None
            if download_dir and obj_t and download_file:
                saved, _ = await download_file(page, origin, obj_t, title, download_dir)
            dl = f"{origin}/space/api/box/stream/download/all/{obj_t}?type=12"
            if saved:
                md.append(f"- {title} (已下载: {Path(saved).name})")
            elif obj_t:
                md.append(f"- [{title}]({dl})")
            else:
                md.append(f"- {title}")
        return "\n".join(md)
    except Exception:
        return ""
