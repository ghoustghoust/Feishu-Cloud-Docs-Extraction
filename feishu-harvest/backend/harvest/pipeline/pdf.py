# -*- coding: utf-8 -*-
"""pipeline.pdf — Markdown 合并导出 PDF。

体积控制 (避免 v22 式 400MB 巨型 PDF):
- 图片先经 Pillow 降采样 (最长边 pdf_image_width) 并转 JPEG (q=82, 白底压 alpha)
  → _pdf_cache/ 内复用, PDF 内每图只嵌一份压缩结果;
- HTML 以临时文件写入输出根目录再 file:// 打开, 相对路径图片才能被 Chromium 解析
  (set_content 无基准 URL, 相对引用会全部落空 — 实测教训)。

注意: page.pdf() 仅在 headless 下可用, 因此单独启动一个无头 Chromium。
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

import markdown

_IMG_RE = re.compile(r"!\[([^\]]*)\]\(((?:\.\./)*images/[^)]+)\)")


def _compress_image(src: Path, cache_dir: Path, max_width: int) -> str | None:
    """图片 → 降采样 JPEG 缓存; 返回缓存文件名, 失败 None。"""
    try:
        from PIL import Image
        key = hashlib.md5(f"{src}|{max_width}".encode()).hexdigest()[:12]
        out = cache_dir / f"{key}.jpg"
        if out.exists():
            return out.name
        cache_dir.mkdir(parents=True, exist_ok=True)
        with Image.open(src) as im:
            if im.mode in ("RGBA", "P", "LA"):
                im = im.convert("RGBA")
                bg = Image.new("RGB", im.size, (255, 255, 255))
                bg.paste(im, mask=im.split()[-1])
                im = bg
            elif im.mode != "RGB":
                im = im.convert("RGB")
            if im.width > max_width:
                h = round(im.height * max_width / im.width)
                im = im.resize((max_width, h), Image.LANCZOS)
            im.save(out, "JPEG", quality=82, optimize=True)
        return out.name
    except Exception:
        return None


async def export_pdf(md_files: list[Path], output_path: Path,
                     title: str = "飞书文档", image_width: int = 1400) -> int:
    """合并 md → 单 PDF; 返回转换失败的文件数。md 文件本身不被修改。"""
    from playwright.async_api import async_playwright

    out_root = output_path.parent
    cache_dir = out_root / "images_cache"
    failed = 0
    all_body = ""
    toc_items: list[str] = []

    for i, f in enumerate(md_files, 1):
        try:
            c = f.read_text(encoding="utf-8")
            m = re.search(r"^# (.+)", c)
            page_title = m.group(1) if m else f.stem
            c = re.sub(r"^# .+\n", "", c, count=1)
            c = re.sub(r"> 来源:.*\n", "", c)
            c = re.sub(r"^---\s*\n", "", c, count=1, flags=re.MULTILINE)

            # 图片 → 压缩缓存 (按 md 所在目录解析相对路径, 与深度无关)
            def repl(mo):
                src = (f.parent / mo.group(2)).resolve()
                if not src.exists():
                    return mo.group(0)
                name = _compress_image(src, cache_dir, image_width)
                return f"![{mo.group(1)}](images_cache/{name})" if name else mo.group(0)

            c = _IMG_RE.sub(repl, c)
            body = markdown.markdown(c, extensions=["tables", "fenced_code", "toc"])
            toc_items.append(f'<li><a href="#page{i}">{page_title}</a></li>')
            all_body += f'<div class="page" id="page{i}"><h1>{page_title}</h1>{body}</div>\n'
        except Exception:
            failed += 1
    if not all_body:
        raise RuntimeError("没有可导出的内容 (所有 md 转换失败)")

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<style>
  @page {{ margin: 2cm 2.5cm; size: A4; }}
  body {{ font-family: "Microsoft YaHei", "SimSun", sans-serif; font-size: 11pt; line-height: 1.8; color: #333; }}
  h1 {{ font-size: 18pt; border-bottom: 2px solid #3370ff; padding-bottom: 8px; margin-top: 30px; }}
  h2 {{ font-size: 15pt; margin-top: 25px; color: #3370ff; }}
  h3 {{ font-size: 13pt; margin-top: 20px; }}
  h4 {{ font-size: 12pt; }}
  p {{ margin: 8px 0; }}
  img {{ max-width: 100%; height: auto; display: block; margin: 15px auto; }}
  table {{ border-collapse: collapse; width: 100%; margin: 15px 0; font-size: 10pt; }}
  th, td {{ border: 1px solid #ddd; padding: 8px 10px; text-align: left; }}
  th {{ background: #3370ff; color: white; }}
  tr:nth-child(even) {{ background: #f7f8fa; }}
  code {{ background: #f0f0f0; padding: 2px 5px; border-radius: 3px; font-size: 10pt; }}
  pre {{ background: #2d2d2d; color: #f8f8f2; padding: 15px; border-radius: 6px; overflow-x: auto; font-size: 9pt; line-height: 1.5; }}
  pre code {{ background: none; padding: 0; color: inherit; }}
  blockquote {{ border-left: 4px solid #3370ff; margin: 15px 0; padding: 8px 15px; background: #f0f4ff; }}
  strong {{ color: #1a1a1a; }}
  .page {{ page-break-after: always; }}
  .page:last-child {{ page-break-after: avoid; }}
  .toc {{ background: #f7f8fa; padding: 20px 30px; border-radius: 8px; margin-bottom: 30px; }}
  .toc h2 {{ border: none; margin-top: 0; }}
  .toc ul {{ padding-left: 20px; }}
  .toc li {{ margin: 6px 0; }}
</style>
</head>
<body>
<div class="toc">
<h2>目录</h2>
<ol>{''.join(toc_items)}</ol>
</div>
{all_body}
</body>
</html>"""

    # 临时 HTML 必须落在输出根目录 (相对引用 images_cache/ 才能被 file:// 解析)
    tmp_html = out_root / "_pdf_build.html"
    tmp_html.write_text(html, encoding="utf-8")
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            page = await browser.new_page()
            try:
                # 大 HTML 用 load 而非 networkidle — 长尾图片请求可能超时
                await page.goto(tmp_html.resolve().as_uri(), wait_until="load",
                                timeout=180000)
                await page.wait_for_timeout(2000)  # 留给 JPEG 解码
                await page.pdf(path=str(output_path), format="A4", print_background=True,
                               margin={"top": "2cm", "bottom": "2cm",
                                       "left": "2.5cm", "right": "2.5cm"})
            finally:
                await browser.close()
    finally:
        tmp_html.unlink(missing_ok=True)
    return failed
