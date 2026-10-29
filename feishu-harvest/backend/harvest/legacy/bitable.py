# -*- coding: utf-8 -*-
"""legacy.bitable — 多维表格 API 直采 (自 v22 移植)。

背景: bitable 页面用 canvas + 虚拟滚动渲染, DOM 无文本, 必须 API 直采。
数据编码: base64(gzip(json)) 双层; wiki token ≠ base token (clientvars 用
真实 obj_token, 从页面 resource timing 提取)。
"""
from __future__ import annotations

import base64
import gzip
import json
import re
from urllib.parse import urlparse

from harvest.textutil import is_page_link


def gunzip(s: str) -> str:
    """base64(gzip(json)) 解码; 非压缩时原样返回。"""
    if not s:
        return s
    try:
        if s.startswith("H4sI"):  # gzip magic 的 base64 前缀
            return gzip.decompress(base64.b64decode(s)).decode("utf-8", errors="replace")
        return s
    except Exception:
        return s


def parse_url(url: str) -> tuple[str | None, str | None, str | None]:
    """URL → (token, table_id, view_id)。"""
    q = urlparse(url)
    params = dict(kv.split("=", 1) for kv in q.query.split("&") if "=" in kv)
    m = re.search(r"/(?:wiki|base|bitable)/([^/?#]+)", q.path)
    return (m.group(1) if m else None), params.get("table"), params.get("view")


async def _request(page, url: str) -> dict | None:
    """页面内 fetch (自动带登录态与页面 headers — ctx.request 直连会被业务层拒)。"""
    try:
        return await page.evaluate(
            """async (u) => {
                const resp = await fetch(u, {credentials: 'include'});
                const ct = resp.headers.get('content-type') || '';
                if (ct.includes('json')) return await resp.json();
                return {code: -1, msg: 'non-json response'};
            }""", url)
    except Exception:
        return None


async def resolve_base(page) -> str | None:
    """从 resource timing 提取真实 base token (obj_token); 过滤 event 等路径杂质。"""
    try:
        bases = await page.evaluate(
            """() => {
                const bases = new Set();
                for (const u of performance.getEntriesByType('resource').map(e => e.name)) {
                    const m = u.match(/\\/space\\/api\\/v1\\/bitable\\/([^/?#]+)\\/clientvars/);
                    if (m && /^[A-Za-z0-9]{15,}$/.test(m[1])) bases.add(m[1]);
                }
                return [...bases];
            }""")
        return bases[0] if bases else None
    except Exception:
        return None


def _cell_text(value, depth: int = 0) -> str:
    """字段值 → 纯文本 (解包 {value, modifiedUser, modifiedTime} 包装, 递归展开)。"""
    if value is None or depth > 6:
        return ""
    if isinstance(value, dict) and set(value) <= {"value", "modifiedUser", "modifiedTime"}:
        return _cell_text(value.get("value"), depth + 1)
    if isinstance(value, str):
        return value.replace("\n", " ").replace("|", "\\|")
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, dict):
                if item.get("type") == "url" or item.get("link"):
                    t = item.get("text") or item.get("link") or ""
                    link = item.get("link") or item.get("url")
                    if link and is_page_link(link):  # 页面链接降级纯文本
                        return t
                    return f"[{t}]({link})" if link else t
                if "text" in item:
                    parts.append(str(item.get("text", "")))
                elif "name" in item:
                    parts.append(str(item.get("name", "")))
                else:
                    parts.append(json.dumps(item, ensure_ascii=False)[:200])
            else:
                parts.append(str(item))
        return ", ".join(parts).replace("\n", " ").replace("|", "\\|")
    if isinstance(value, dict):
        if "file_token" in value or "tmp_url" in value or "url" in value:
            name = value.get("name", "附件")
            direct = value.get("url") or value.get("tmp_url") or ""
            if direct:
                return f"[{name}]({direct})"
            ft = value.get("file_token", "")
            if ft:
                return f"[{name}](https://internal-api-drive-stream.feishu.cn/space/api/box/stream/download/all/{ft})"
        return json.dumps(value, ensure_ascii=False)[:300]
    return str(value)


async def to_markdown(page, url: str, all_views: bool = False) -> tuple[str, dict]:
    """bitable 页 → (markdown 表格, blob_mapping)。非 bitable/失败返回 ("", {})。

    all_views=True 时遍历全部视图, 每个视图一张表 (视图名作小标题)。
    """
    try:
        token, table_id, view_id = parse_url(url)
        if not token:
            return "", {}
        origin = f"{urlparse(url).scheme}://{urlparse(url).netloc}"
        # /base/ 路径的 token 即真实 base token (内嵌表格捕获场景); /wiki/ 需从资源时序解析
        if re.search(r"/base/", urlparse(url).path):
            base = token
        else:
            base = await resolve_base(page) or token

        cv_url = f"{origin}/space/api/v1/bitable/{base}/clientvars"
        cv_url += (f"?tableID={table_id}&viewID={view_id or ''}&recordLimit=200"
                   f"&ondemandLimit=200&needBase=true&viewLazyLoad=true"
                   if table_id else "?needBase=true")
        j = await _request(page, cv_url)
        if not j or j.get("code") != 0 or not j.get("data"):
            return "", {}
        try:
            table = json.loads(gunzip(j["data"].get("table", "")))
        except Exception:
            return "", {}
        if not table:
            return "", {}

        field_map = table.get("fieldMap") or {}
        if not isinstance(field_map, dict):  # 个别表结构异常, 不可解析
            return "", {}
        fields = [{"id": fid, "name": f.get("field_name") or f.get("name") or fid}
                  for fid, f in field_map.items()]
        if not fields:
            return "", {}
        view_map = table.get("viewMap") or {}
        views = [{"id": vid, "name": v.get("view_name") or v.get("name") or vid}
                 for vid, v in view_map.items() if isinstance(view_map, dict)]
        active_view = table.get("currentView") or view_id or (views[0]["id"] if views else "")
        total_num = (table.get("meta") or {}).get("recordsNum") or table.get("recordCount") or 0

        target_views = views if (all_views and views) else \
            [{"id": active_view, "name": next((v["name"] for v in views if v["id"] == active_view), active_view)}]
        if not target_views:
            target_views = [{"id": "", "name": ""}]

        md: list[str] = [f"<!-- 多维表格 {table_id or base} -->", ""]
        total_records = 0
        for vi, view in enumerate(target_views):
            vid = view["id"]
            all_records: list = []
            offset = 0
            while True:  # 分页拉全量 (offset 递增, limit 3000)
                rec_url = (f"{origin}/space/api/v1/bitable/{base}/records"
                           f"?tableId={table_id}&viewId={vid}&offset={offset}&limit=3000"
                           f"&tableID={table_id}&viewID={vid}&removeFmlExtra=true")
                jr = await _request(page, rec_url)
                if not jr or jr.get("code") != 0 or not jr.get("data"):
                    break
                try:
                    recs = json.loads(gunzip(jr["data"].get("records", "")))
                except Exception:
                    break
                if not recs or not recs.get("recordMap"):
                    break
                batch = list(recs["recordMap"].values())
                all_records.extend(batch)
                offset += len(batch)
                if not batch or offset >= (recs.get("tableRecordNum") or total_num or offset + 1):
                    break
            if not all_records and len(target_views) > 1:
                continue  # 全视图模式下跳过空视图
            total_records += len(all_records)
            if len(target_views) > 1:
                md.append(f"### 视图: {view['name'] or vid} ({len(all_records)} 条)")
                md.append("")
            else:
                md.append(f"<!-- 记录 {len(all_records)} 条 · 视图: {view['name'] or vid} -->")
            if len(views) > 1 and len(target_views) == 1:
                md.append(f"> 共 {len(views)} 个视图: " + " / ".join(v["name"] for v in views))
                md.append("")
            md.extend(_render_records(fields, all_records))
            md.append("")
        if total_records == 0:
            return "", {}
        return "\n".join(md), {}
    except Exception:
        return "", {}


def _render_records(fields: list[dict], records: list) -> list[str]:
    """窄表 → markdown 表格; 宽表/长文本 → 每记录一张卡片 (首字段作标题)。"""
    rows = [[_cell_text((r.get("fields") or r).get(f["id"])) for f in fields] for r in records]
    longest = max((len(c) for row in rows for c in row), default=0)
    if len(fields) <= 4 and longest <= 60:
        out = ["| " + " | ".join(h.replace("|", "\\|") for h in [f["name"] for f in fields]) + " |",
               "| " + " | ".join(["---"] * len(fields)) + " |"]
        out += ["| " + " | ".join(c.replace("|", "\\|") for c in row) + " |" for row in rows]
        return out
    out: list[str] = []
    for row in rows:
        vals = [(f["name"], c) for f, c in zip(fields, row) if c]
        if not vals:
            continue
        k = _title_idx(vals)
        title = vals[k][1]
        out.append(f"#### {title if len(title) <= 60 else title[:57] + '…'}")
        out.append("")
        for name, c in [v for i, v in enumerate(vals) if i != k]:
            out.append(f"- **{name}**: {c}")
        out.append("")
    return out


def _title_idx(vals: list[tuple[str, str]]) -> int:
    """卡片标题字段: 优先名称类字段; 否则跳过附件文件名/内部 ID/裸链接的首个实质值。"""
    for i, (name, _) in enumerate(vals):
        if any(k in name for k in ("书名", "名称", "标题", "名字", "文本")):
            return i
    for i, (_, v) in enumerate(vals):
        if len(v) < 6 or re.search(r"\.(png|jpe?g|gif|webp|svg)$", v, re.I) \
                or re.fullmatch(r"(opt|rec|tbl|vew|app|blk)[A-Za-z0-9]{6,}", v) \
                or v.startswith("["):
            continue
        return i
    return 0
