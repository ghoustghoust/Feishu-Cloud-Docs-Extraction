#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
飞书文档爬取 v21 — 终端面板版
用法: python feishu_crawler.py [飞书URL] [输出目录]
"""

import asyncio, os, re, sys, hashlib, base64, time
from urllib.parse import urlparse
from pathlib import Path

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

from playwright.async_api import async_playwright
from bs4 import BeautifulSoup, Tag, NavigableString

# ========== 终端 UI ==========
W = 70  # 面板宽度

def bar(current, total, width=30, label=''):
    """ASCII 进度条"""
    pct = min(current / total, 1.0) if total > 0 else 1
    filled = int(width * pct)
    bar_str = '█' * filled + '░' * (width - filled)
    return f"  {bar_str} {pct*100:5.1f}%  [{current}/{total}] {label}"

def panel(title, lines, width=W):
    """画一个终端面板"""
    print(f"\n╭─ {title} {'─' * (width - len(title) - 6)}╮")
    for line in lines:
        print(f"│ {line:<{width-4}} │")
    print(f"╰{'─' * (width - 2)}╯")

def hr(title='', width=W):
    """分隔线"""
    if title:
        side = (width - len(title) - 2) // 2
        print(f"\n{'─' * side} {title} {'─' * side}")
    else:
        print('─' * width)

def table(headers, rows, col_widths=None):
    """简单表格"""
    if not col_widths:
        col_widths = [max(len(str(row[i])) for row in [headers] + rows) + 3 for i in range(len(headers))]
    header_line = ''.join(f"{h:<{w}}" for h, w in zip(headers, col_widths))
    print(f"  {header_line}")
    print(f"  {'─' * (sum(col_widths))}")
    for row in rows:
        print(f"  {''.join(f'{str(c):<{w}}' for c, w in zip(row, col_widths))}")

def spinner_task(label):
    """返回一个显示旋转动画的辅助类"""
    class Spinner:
        def __init__(self, label):
            self.label = label
            self.chars = '⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏'
            self.idx = 0
        def tick(self, extra=''):
            c = self.chars[self.idx % len(self.chars)]
            print(f"\r  {c} {self.label} {extra}", end='', flush=True)
            self.idx += 1
        def done(self, msg=''):
            print(f"\r  ✓ {self.label} {msg}" + ' ' * 20)
    return Spinner(label)

# ========== 菜单系统 ==========

class Config:
    def __init__(self):
        self.url = ""
        self.output_dir = Path("./feishu_output")
        self.export_format = "md"  # "md" 或 "pdf"

    def set_url(self, url):
        """设置URL — 默认输出目录保持 feishu_output"""
        self.url = url

config = Config()

def clear_screen():
    os.system('cls' if sys.platform == 'win32' else 'clear')

def menu_banner():
    clear_screen()
    print(f"""
╔══════════════════════════════════════════════════════════════════╗
║                   飞书文档爬取 v22 — 交互面板                       ║
╚══════════════════════════════════════════════════════════════════╝""")

def show_config():
    fmt = "Markdown (图片内嵌base64)" if config.export_format == "md" else "PDF (单个合并文件)"
    print(f"""
  ┌─ 当前设置 ─────────────────────────────────────────────────┐
  │  1. 目标URL   : {config.url[:52] if config.url else '(未设置)':<52} │
  │  2. 导出目录  : {str(config.output_dir):<52} │
  │  3. 导出格式  : {fmt:<52} │
  └────────────────────────────────────────────────────────────┘""")

def main_menu():
    print("""
  [1] 设置目标 URL
  [2] 设置导出目录
  [3] 导出格式设置
  [4] ▶ 开始爬取
  [5] 清除登录状态 (切换账号)
  [0] 退出
""")

def format_menu():
    clear_screen()
    menu_banner()
    print(f"""
  ┌─ 导出格式设置 ─────────────────────────────────────────────┐
  │                                                            │
  │  [1] Markdown  (.md 文件，每个页面一个文件，图片内嵌base64)   │
  │  [2] PDF       (.pdf 合并文件，所有页面整合，图片内嵌)        │
  │                                                            │
  │  [0] 返回上级                                               │
  └────────────────────────────────────────────────────────────┘""")

def sub_menu_format():
    while True:
        format_menu()
        show_config()
        c = input("  请选择 [0-2]: ").strip()
        if c == '1':
            config.export_format = "md"
            print("  OK: Markdown 模式")
            input("  按回车继续...")
            return
        elif c == '2':
            config.export_format = "pdf"
            print("  OK: PDF 模式")
            input("  按回车继续...")
            return
        elif c == '0':
            return

def sub_menu_url():
    clear_screen()
    menu_banner()
    print(f"""
  ┌─ 设置目标 URL ─────────────────────────────────────────────┐
  │                                                            │
  │  输入飞书知识库中任意页面的链接                                │
  │  示例: https://xxx.feishu.cn/wiki/ABC123                     │
  │                                                            │
  │  [0] 返回上级                                               │
  └────────────────────────────────────────────────────────────┘""")
    show_config()
    url = input("\n  请输入URL: ").strip()
    if url and url != '0':
        if 'feishu.cn' in url:
            config.set_url(url)
            print(f"  ✓ URL 已设置, 输出目录: {config.output_dir}")
        else:
            print("  ⚠ 请输入有效的飞书链接")
        input("  按回车继续...")

def sub_menu_dir():
    clear_screen()
    menu_banner()
    print(f"""
  ┌─ 设置导出目录 ─────────────────────────────────────────────┐
  │                                                            │
  │  输入导出目录路径（相对或绝对路径）                             │
  │  示例: ./my_docs  或  D:/文档/飞书                           │
  │                                                            │
  │  [0] 返回上级                                               │
  └────────────────────────────────────────────────────────────┘""")
    show_config()
    path = input("\n  请输入导出目录: ").strip()
    if path and path != '0':
        config.output_dir = Path(path)
        print(f"  ✓ 导出目录已设置为: {config.output_dir}")
        input("  按回车继续...")

# ========== 工具函数 ==========
def sfn(name, ml=80):
    name = re.sub(r'[\u200b\u200c\u200d\ufeff\u00ad]', '', name)
    name = re.sub(r'[\\/*?:"<>|]', '_', name).strip('. \t\n')
    return (name[:ml] if len(name) > ml else name) or "untitled"

def strip_zws(text):
    return re.sub(r'[\u200b\u200c\u200d\ufeff\u00ad]', '', text)

async def download_img(page, url, d):
    try:
        if not url or url.startswith('data:') or len(url) < 10: return None
        h = hashlib.md5(url.encode()).hexdigest()[:12]
        ext = Path(urlparse(url).path).suffix or '.png'
        if ext not in ('.png','.jpg','.jpeg','.gif','.svg','.webp'): ext = '.png'
        n = f"{h}{ext}"; p = d / n
        if p.exists(): return f"images/{n}"
        r = await page.request.get(url, timeout=15000)
        if r.ok:
            c = await r.body()
            if len(c) > 50: p.write_bytes(c); return f"images/{n}"
        return None
    except: return None

# ========== HTML → Markdown 转换 (核心不变) ==========
def convert_ace_line(el):
    if isinstance(el, NavigableString):
        return strip_zws(str(el))
    if not isinstance(el, Tag):
        return ""
    tag = el.name.lower()
    if tag in ('script', 'style', 'noscript', 'svg', 'path', 'br', 'wbr', 'meta'):
        return ""
    children = ''.join(convert_ace_line(c) for c in el.children)
    style = (el.get('style') or '').lower()
    is_bold = tag in ('strong', 'b') or 'bold' in style or 'font-weight:bold' in style or 'font-weight:700' in style or '700' in str(el.get('data-font-weight', ''))
    is_italic = tag in ('em', 'i') or 'italic' in style or 'font-style:italic' in style
    is_strikethrough = tag in ('s', 'del', 'strike')
    is_code = tag == 'code'
    is_link = tag == 'a' and el.get('href')
    if is_link: return f"[{children}]({el['href']})"
    if is_code: return f"`{children}`"
    if is_bold: return f"**{children}**"
    if is_italic: return f"*{children}*"
    if is_strikethrough: return f"~~{children}~~"
    return children

def convert_text_block(block):
    lines = []
    for ace in block.find_all(class_=re.compile(r'ace-line')):
        text = strip_zws(''.join(convert_ace_line(c) for c in ace.children))
        if text.strip(): lines.append(text)
    return '\n'.join(lines) if lines else strip_zws(block.get_text())

def convert_heading_block(block, level):
    content = block.find(class_=re.compile(r'heading-content'))
    if not content: content = block.find(class_=re.compile(r'ace-line'))
    text = strip_zws(content.get_text() if content else block.get_text())
    return f"\n{'#' * level} {text}\n" if text else ""

def convert_code_block(block):
    """转换代码块 - v22增强版: 多策略行提取"""
    # === 语言检测 ===
    lang = ''; raw_lang = ''
    for sel in [re.compile(r'language', re.I), re.compile(r'code-lang', re.I),
                re.compile(r'lang', re.I), re.compile(r'code-tag', re.I),
                re.compile(r'code-lang-label', re.I)]:
        lang_el = block.find(class_=sel)
        if lang_el:
            raw_lang = lang_el.get_text(strip=True).lower().rstrip(':：').lstrip(':：')
            if raw_lang: break
    if not raw_lang:
        for el in block.find_all(['span', 'div', 'button']):
            text = el.get_text(strip=True).lower()
            if text in ('bash','shell','python','go','java','javascript','typescript','json',
                        'yaml','html','css','sql','rust','c','cpp','c++','plain text','text',
                        'ruby','php','swift','kotlin','scala','makefile','dockerfile','xml',
                        'toml','ini','markdown','yml','js','ts','py','sh','conf'):
                raw_lang = text; break
    if raw_lang:
        lang_map = {'bash':'bash','shell':'bash','sh':'bash','python':'python','py':'python',
                    'go':'go','golang':'go','java':'java','javascript':'javascript','js':'javascript',
                    'typescript':'typescript','ts':'typescript','json':'json','yaml':'yaml','yml':'yaml',
                    'plain text':'','text':'','plain':'','html':'html','css':'css','sql':'sql',
                    'rust':'rust','c':'c','cpp':'cpp','c++':'cpp','ruby':'ruby','php':'php',
                    'swift':'swift','kotlin':'kotlin','scala':'scala','makefile':'makefile',
                    'dockerfile':'dockerfile','xml':'xml','toml':'toml','ini':'ini','markdown':'markdown',
                    'conf':''}
        lang = lang_map.get(raw_lang, raw_lang)

    # === 代码内容提取: 多策略 ===
    lines = []

    # 策略1: 查找所有行级元素（支持多种飞书class名）
    for line_cls in ['ace-line', 'code-line', 'cm-line', 'view-line', 'line-number']:
        found = block.find_all(class_=re.compile(line_cls, re.I))
        if found:
            for el in found:
                line_text = strip_zws(el.get_text())
                lines.append(line_text)
            break

    # 策略2: 找代码容器, get_text('\\n') 保留元素间换行
    if not lines:
        code_container = None
        for sel in ['code', 'pre', '[class*="CodeMirror"]', '[class*="code-content"]',
                    '[class*="code-body"]', '[class*="code-lines"]', '[class*="highlight"]',
                    '[class*="code-block-content"]', '[class*="code-block"]', '[class*="code-wrap"]']:
            if sel.startswith('['):
                code_container = block.find(class_=re.compile(sel[8:-2], re.I))
            else:
                code_container = block.find(sel)
            if code_container: break

        if code_container:
            text = code_container.get_text('\\n')
            for l in text.split('\\n'):
                s = strip_zws(l)
                if s and s != '复制':
                    lines.append(s)

    # 策略3: 整个block用 get_text('\\n') 回退
    if not lines:
        text = block.get_text('\\n')
        lines = [strip_zws(l) for l in text.split('\\n') if strip_zws(l) and strip_zws(l) != '复制']

    # 清理行首UI标签
    if lines and lines[0].strip() in ('代码块', raw_lang):
        lines = lines[1:]
    code_text = '\\n'.join(lines) if lines else ''

    # 移除开头的UI标签
    code_text = re.sub(r'^代码块\\s*\\n?', '', code_text)
    if raw_lang:
        code_text = re.sub(r'^' + re.escape(raw_lang) + r'\\s*[\\n:]?\\s*', '', code_text, count=1, flags=re.IGNORECASE)

    code_text = code_text.strip()
    if code_text:
        return f"\\n```{lang}\\n{code_text}\\n```\\n"
    return ""


def convert_image_block(block):
    imgs = block.find_all('img')
    parts = []
    for img in imgs:
        src = img.get('src', '') or img.get('data-src', '')
        alt = strip_zws(img.get('alt', '') or '图片')
        if src and not src.startswith('data:'): parts.append(f"\n![{alt}]({src})\n")
    return ''.join(parts)

def convert_quote_block(block):
    lines = []
    for ace in block.find_all(class_=re.compile(r'ace-line')):
        text = strip_zws(''.join(convert_ace_line(c) for c in ace.children))
        if text.strip(): lines.append(f"> {text}")
    if not lines:
        text = strip_zws(block.get_text())
        lines = [f"> {l}" for l in text.split('\n') if l.strip()]
    return '\n'.join(lines) if lines else ""

def convert_list_block(block):
    is_ordered = bool(block.find(class_=re.compile(r'list-number|list-order|list-index|number|order')))
    if not is_ordered:
        text = block.get_text(strip=True)
        if re.match(r'^\d+[\.\)]', text): is_ordered = True
    lines = []
    for ace in block.find_all(class_=re.compile(r'ace-line')):
        text = strip_zws(''.join(convert_ace_line(c) for c in ace.children))
        if text.strip():
            prefix = "1." if is_ordered else "-"
            lines.append(f"{prefix} {text}")
    if not lines:
        text = strip_zws(block.get_text())
        if text.strip():
            prefix = "1." if is_ordered else "-"
            lines.append(f"{prefix} {text}")
    return '\n'.join(lines) if lines else ""

def convert_todo_block(block):
    lines = []
    for ace in block.find_all(class_=re.compile(r'ace-line')):
        text = strip_zws(''.join(convert_ace_line(c) for c in ace.children))
        if text.strip():
            checked = block.find('input', {'checked': True}) or block.find(class_=re.compile(r'checked'))
            prefix = "[x]" if checked else "[ ]"
            lines.append(f"- {prefix} {text}")
    if not lines:
        text = strip_zws(block.get_text())
        if text.strip(): lines.append(f"- [ ] {text}")
    return '\n'.join(lines) if lines else ""

def convert_callout_block(block):
    inner_parts = []
    for child in block.children:
        if isinstance(child, Tag):
            inner_cls = ' '.join(child.get('class', []))
            if child.get('data-block-type') or 'block' in inner_cls:
                md = convert_block(child)
                if md: inner_parts.append(md)
    content = '\n'.join(inner_parts) if inner_parts else strip_zws(block.get_text())
    if not content.strip(): return ""
    lines = content.strip().split('\n')
    quoted = '\n'.join(f"> {line}" if not line.startswith('>') else line for line in lines)
    return f"\n{quoted}\n"

def convert_bookmark_block(block):
    links = block.find_all('a', href=True)
    parts = []
    for a in links:
        href = a['href']; text = strip_zws(a.get_text()) or href
        parts.append(f"- [{text}]({href})")
    if not parts:
        text = strip_zws(block.get_text())
        if text.strip(): parts.append(text)
    return '\n'.join(parts) if parts else ""

def convert_grid_block(block):
    parts = []
    for img in block.find_all('img'):
        src = img.get('src', '') or img.get('data-src', '')
        alt = strip_zws(img.get('alt', '') or '图片')
        if src and not src.startswith('data:'): parts.append(f"\n![{alt}]({src})\n")
    return ''.join(parts)

def convert_file_block(block):
    links = block.find_all('a', href=True)
    parts = []
    for a in links:
        href = a['href']; text = strip_zws(a.get_text()) or href
        parts.append(f"- [{text}]({href})")
    if not parts:
        name_el = block.find(class_=re.compile(r'file-name|file-title|name', re.I))
        name = strip_zws(name_el.get_text()) if name_el else ''
        text = strip_zws(block.get_text())
        if name: parts.append(f"**附件**: {name}")
        elif text.strip(): parts.append(f"**附件**: {text.strip()[:100]}")
    return '\n'.join(parts) if parts else ""

def convert_block(block):
    if not isinstance(block, Tag): return ""
    block_type = block.get('data-block-type', '')
    cls = ' '.join(block.get('class', []))
    bt = block_type.lower()
    if bt.startswith('heading') or 'heading' in bt:
        m = re.search(r'heading(\d)', bt)
        level = int(m.group(1)) if m else 2
        return convert_heading_block(block, level)
    if 'heading' in cls:
        for i in range(6, 0, -1):
            if f'heading{i}' in cls or f'heading-{i}' in cls:
                return convert_heading_block(block, i)
    if bt == 'code' or 'code-block' in cls: return convert_code_block(block)
    if bt == 'image' or 'image-block' in cls: return convert_image_block(block)
    if bt == 'quote' or 'quote-block' in cls: return convert_quote_block(block)
    if bt == 'divider' or 'divider-block' in cls: return "\n---\n"
    if bt in ('list', 'list-item', 'bulleted', 'bulleted_list', 'numbered', 'numbered_list', 'ordered'): return convert_list_block(block)
    if 'list-block' in cls: return convert_list_block(block)
    if bt == 'todo' or 'todo-block' in cls: return convert_todo_block(block)
    if bt == 'table' or 'table-block' in cls: return _table_to_md(block)
    if bt == 'callout' or 'callout-block' in cls: return convert_callout_block(block)
    if bt in ('bookmark', 'link') or 'bookmark-block' in cls: return convert_bookmark_block(block)
    if bt in ('grid', 'gallery') or 'grid-block' in cls: return convert_grid_block(block)
    if bt in ('file', 'attachment') or 'file-block' in cls: return convert_file_block(block)
    if bt in ('text', 'paragraph', 'p', ''):
        text = convert_text_block(block)
        if text.strip(): return f"\n{text}\n"
        return ""
    if bt in ('formula', 'equation', 'math'):
        text = strip_zws(block.get_text())
        return f"\n$$\n{text}\n$$\n" if text.strip() else ""
    if 'text-block' in cls or 'paragraph' in cls:
        text = convert_text_block(block)
        return f"\n{text}\n" if text.strip() else ""
    if 'render-unit' in cls: return convert_children(block)
    return ""

def convert_children(parent):
    parts = []
    for child in parent.children:
        if isinstance(child, Tag):
            cls = ' '.join(child.get('class', []))
            if 'block' in cls or child.get('data-block-type'):
                md = convert_block(child)
                if md: parts.append(md)
            else:
                md = convert_children(child)
                if md: parts.append(md)
    return '\n'.join(parts)

def _table_to_md(table):
    rows = [[td.get_text(strip=True).replace('\n',' ').replace('|','\\|') for td in tr.find_all(['td','th'])] for tr in table.find_all('tr')]
    rows = [r for r in rows if r]
    if not rows: return ""
    mc = max(len(r) for r in rows)
    for r in rows: r.extend(['']*(mc-len(r)))
    return "\n" + "\n".join(["| " + " | ".join(rows[0]) + " |", "| " + " | ".join(["---"]*mc) + " |"] + ["| " + " | ".join(r) + " |" for r in rows[1:]]) + "\n"

def _extract_blocks_recursive(container):
    for child in container.children:
        if not isinstance(child, Tag): continue
        cls = ' '.join(child.get('class', []))
        has_block_type = bool(child.get('data-block-type'))
        is_block = 'block' in cls and 'page-block-children' not in cls
        if has_block_type or is_block:
            md = convert_block(child)
            if md: yield md
        elif 'render-unit-container' in cls or 'render-unit' in cls:
            yield from _extract_blocks_recursive(child)
        else:
            yield from _extract_blocks_recursive(child)

# =====================================================================
# 代码块恢复: 当 HTML 解析器对某些飞书模板失效时, 从 innerText 中
# 检测 "语言标签 + 复制按钮 + 代码内容" 模式, 重新包装为 ``` 标记
# =====================================================================
def _recover_code_blocks(text):
    """检测并修复缺失的代码块标记
    飞书innerText中的代码块格式:
      Markdown      ← 语言标签(应剥离)
      复制           ← 复制按钮(应剥离)
      <代码内容>
    已存在的```块原样保留, 只对非fence区域做恢复"""

    # 已知语言标签
    lang_labels = {'bash','shell','python','go','java','javascript','typescript',
                   'json','yaml','html','css','sql','rust','c','cpp','c++',
                   'ruby','php','swift','kotlin','scala','makefile','dockerfile',
                   'xml','toml','ini','markdown','yml','js','ts','py','sh','conf',
                   'plain text','text'}

    lines = text.split('\n')
    result = []
    in_code = False
    code_lang = ''
    code_buf = []
    in_existing_fence = False  # 跳过已有的```区域

    i = 0
    while i < len(lines):
        line = lines[i].strip()

        # 跳过已有的```区域(HTML解析器已处理的代码块)
        if line.startswith('```'):
            in_existing_fence = not in_existing_fence
            result.append(lines[i])  # 原样保留```
            i += 1
            continue
        if in_existing_fence:
            result.append(lines[i])  # fence内的内容原样保留
            i += 1
            continue

        # 跳过飞书UI标签
        if line == '代码块':
            i += 1
            continue

        # 检测 "语言标签 + 复制" 模式 → 剥离UI, 开始新代码块
        is_lang = line.lower() in lang_labels
        next_is_copy = (i + 1 < len(lines) and lines[i + 1].strip() == '复制')

        if is_lang and next_is_copy:
            new_lang = line.lower()
            lang_map = {'plain text':'','text':''}
            new_lang = lang_map.get(new_lang, new_lang)

            if in_code:
                result.append(f"```{code_lang}")
                result.extend(code_buf)
                result.append("```")
                result.append("")
                code_buf = []

            code_lang = new_lang
            in_code = True
            i += 2  # 剥离语言标签和"复制"
            continue

        # 代码块结束: 章节标题
        if in_code:
            if re.match(r'^ch\d+', line) and len(line) < 20:
                result.append(f"```{code_lang}")
                result.extend(code_buf)
                result.append("```")
                result.append("")
                code_buf = []
                in_code = False
                code_lang = ''
                result.append(lines[i])
                i += 1
                continue
            else:
                code_buf.append(lines[i])
                i += 1
                continue

        # 普通文本
        result.append(lines[i])
        i += 1

    # 关闭最后的代码块
    if code_buf:
        result.append(f"```{code_lang}")
        result.extend(code_buf)
        result.append("```")

    return '\n'.join(result)

# ========== 页面提取 ==========
async def extract_page(page, IMAGES_DIR):
    blob_mapping = {}
    await page.evaluate("""
        () => {
            document.querySelectorAll('[aria-expanded="false"]').forEach(el => {try{el.click()}catch(e){}});
            document.querySelectorAll('details:not([open])').forEach(el => {try{el.setAttribute('open','')}catch(e){}});
            document.querySelectorAll('*').forEach(el => {
                try{el.style.setProperty('content-visibility','visible','important');el.style.setProperty('contain','none','important');el.style.setProperty('contain-intrinsic-size','none','important')}catch(e){}
            });
        }
    """)
    await asyncio.sleep(0.5)

    await page.evaluate("async()=>{const s=400;const m=Math.max(document.body.scrollHeight,30000);for(let y=0;y<m;y+=s){window.scrollTo(0,y);await new Promise(r=>setTimeout(r,60))}window.scrollTo(0,0);await new Promise(r=>setTimeout(r,300))}")
    await asyncio.sleep(1)

    await page.keyboard.press('End'); await asyncio.sleep(1.5)
    await page.keyboard.press('Home'); await asyncio.sleep(0.5)
    for _ in range(6):
        await page.keyboard.press('PageDown'); await asyncio.sleep(0.4)

    await page.evaluate("""
        () => {
            document.querySelectorAll('[aria-expanded="false"]').forEach(el => {try{el.click()}catch(e){}});
            document.querySelectorAll('*').forEach(el => {try{el.style.setProperty('content-visibility','visible','important');el.style.setProperty('contain','none','important')}catch(e){}});
        }
    """)
    await asyncio.sleep(0.5)

    orig = page.viewport_size
    await page.set_viewport_size({'width':1400,'height':15000})
    await asyncio.sleep(4)
    await page.evaluate("async()=>{const m=document.body.scrollHeight/2;window.scrollTo(0,m);await new Promise(r=>setTimeout(r,1000));window.scrollTo(0,0);await new Promise(r=>setTimeout(r,500))}")
    await asyncio.sleep(1)

    # 捕获blob图片
    blob_results = await page.evaluate("""
        async () => {
            const imgs=document.querySelectorAll('img[src^="blob:"]');const rs=[];
            for(const img of imgs){const s=img.getAttribute('src');if(!s)continue;let d=null;
                try{if(img.naturalWidth>0){const c=document.createElement('canvas');c.width=img.naturalWidth;c.height=img.naturalHeight;c.getContext('2d').drawImage(img,0,0);d=c.toDataURL('image/png')}}catch(e){}
                if(!d){try{const r=await fetch(s);const b=await r.blob();if(b.size>0)d=await new Promise(r=>{const fr=new FileReader();fr.onloadend=()=>r(fr.result);fr.readAsDataURL(b)})}catch(e){}}
                rs.push({url:s,data:d});
            }
            return rs;
        }
    """)
    for r in blob_results:
        if r['data'] and ',' in r['data']:
            h = hashlib.md5(r['url'].encode()).hexdigest()[:12]
            data = base64.b64decode(r['data'].split(',')[1])
            if len(data) > 100:
                (IMAGES_DIR / f"{h}.png").write_bytes(data)
                blob_mapping[r['url']] = f"images/{h}.png"

    html = await page.evaluate("""
        () => {
            for(const s of ['[class*="render-unit-wrapper"]','[class*="render-unit"]','.page-block-children','[class*="wiki-content"]','[class*="doc-content"]','article','main']){
                const e=document.querySelector(s);if(e&&e.innerHTML.length>200)return e.outerHTML;}
            return document.body.innerHTML;
        }
    """)

    full_text = await page.evaluate("""
        () => {
            for(const s of ['[class*="render-unit"]','.page-block-children','[class*="wiki-content"]','[class*="doc-content"]','article','main']){
                const e=document.querySelector(s);if(e&&e.innerText&&e.innerText.length>100)return e.innerText;}
            return document.body.innerText||'';
        }
    """)
    full_text = strip_zws(full_text) if full_text else ""

    await page.set_viewport_size(orig if orig else {'width':1400,'height':900})

    if not html or len(html) < 100:
        if full_text and len(full_text) > 50: return re.sub(r'\n{3,}','\n\n',full_text), blob_mapping
        return "", blob_mapping

    soup = BeautifulSoup(html, 'lxml')
    wrapper = (soup.find(class_=re.compile(r'render-unit-wrapper')) or soup.find(class_=re.compile(r'render-unit')) or soup.find(class_='page-block-children'))
    if not wrapper:
        if full_text and len(full_text) > 50: return re.sub(r'\n{3,}','\n\n',full_text), blob_mapping
        return "", blob_mapping

    parts = list(_extract_blocks_recursive(wrapper))
    result = '\n\n'.join(p.strip() for p in parts if p.strip())
    result = re.sub(r'\n{4,}','\n\n\n',result)
    result = strip_zws(result)

    # 完整性检查: 在两种情况下使用innerText
    # 保护: HTML有代码块(>=2) → 源码/提示词页，永久保护
    # 保护: HTML有表格+链接且>=3标题 → 理论学习页，永久保护
    # 情况1: HTML内容太少(<4000字)且innerText明显更大(>3x) → 实战演练页
    # 情况2: HTML质量差 + innerText更大(>1.5x) → 纯文本页
    heading_count = result.count('\n## ') + result.count('\n### ')
    has_table = '| ---' in result
    has_links = result.count('](') >= 3
    has_code = result.count('```') >= 4  # 至少2个代码块

    use_inner = False
    if full_text and not has_code:  # 代码页永不回退
        inner_ratio = len(full_text) / max(len(result), 1)
        if len(result) < 4000 and inner_ratio > 3.0:
            use_inner = True
        elif not (has_table and has_links) and inner_ratio > 1.5:
            use_inner = True

    if use_inner:
        # 提取HTML版本的图片引用注入到innerText
        html_images = re.findall(r'!\[([^\]]*)\]\(([^)]+)\)', result)
        result = re.sub(r'\n{3,}','\n\n', full_text)
        appended = []
        for alt, src in html_images:
            if src not in result and src not in str(appended):
                appended.append(f"![{alt}]({src})")
        if appended:
            result += "\n\n---\n\n" + "\n".join(appended)

    # 对所有页面运行代码块恢复 — 修复HTML解析器漏掉的代码块
    result = _recover_code_blocks(result)

    return result, blob_mapping

# ========== 页面发现 ==========
async def extract_pages_list(page, DOMAIN):
    tokens = {}

    async def scrape_nodes():
        for _ in range(5):
            await page.evaluate("()=>{document.querySelectorAll('[aria-expanded=\"false\"]').forEach(el=>{try{el.click()}catch(e){}})}")
            await asyncio.sleep(0.3)
        nodes = await page.evaluate("""
            (domain) => {
                const result=[],seen=new Set();
                document.querySelectorAll('[data-node-uid]').forEach(el=>{
                    const uid=el.getAttribute('data-node-uid');if(!uid)return;
                    const m=uid.match(/wikiToken=([^&]+)/);const token=m?m[1]:'';
                    if(!token||seen.has(token))return;seen.add(token);
                    const level=parseInt(el.getAttribute('data-node-level')||'0');let text='';
                    const c=el.querySelector('[class*="content"],[class*="title"]');
                    text=(c||el).textContent.trim().replace(/[\\u200b\\u200c\\u200d\\ufeff]/g,'').substring(0,120);
                    result.push({title:text||token,token:token,level:level,url:'https://'+domain+'/wiki/'+token});
                });
                return result;
            }
        """, DOMAIN)
        return nodes

    spin = spinner_task("发现页面..."); spin.tick()
    nodes = await scrape_nodes()
    for n in nodes:
        if n['token'] not in tokens: tokens[n['token']] = n
    spin.done(f"根页面: {len(tokens)} 个")

    for rnd in range(1, 6):
        unvisited = [t for t in tokens.values() if not t.get('_visited')]
        if not unvisited: break
        parents = [t for t in unvisited if '飞书云文档' in t.get('title', '')]
        if not parents: parents = unvisited[:30]
        new_cnt = 0
        batch = min(len(parents), 30)
        for i, p in enumerate(parents[:batch]):
            p['_visited'] = True
            try:
                await page.goto(p['url'], wait_until="domcontentloaded", timeout=15000)
                await asyncio.sleep(2)
                nodes = await scrape_nodes()
                for n in nodes:
                    if n['token'] not in tokens:
                        tokens[n['token']] = n; new_cnt += 1
            except: pass
        print(f"  第{rnd}轮: +{new_cnt} (总计{len(tokens)})")
        for t in unvisited: t['_visited'] = True
        if new_cnt == 0 and rnd >= 2: break

    for t in tokens.values(): t.pop('_visited', None)
    return list(tokens.values())

# ========== 后处理：清理 + 统计 ==========
def post_process(output_dir):
    """删除目录页 + 智能章节排序 + 图片内嵌 + 统计"""
    hr("后处理")
    deleted = 0; total_chars = 0

    # 1. 读取所有文件信息
    files_info = []
    for f in sorted(output_dir.glob("*.md")):
        if f.name == "README.md": continue
        c = f.read_text(encoding='utf-8')
        title = re.search(r'^# (.+)', c)
        title = title.group(1) if title else f.stem
        # 算纯文本字数
        text = re.sub(r'!\[.*?\]\(.*?\)', '', c)
        text = re.sub(r'```.*?```', '', text, flags=re.DOTALL)
        text = re.sub(r'[#>*\-\|\s]', '', text)
        char_count = len(text)

        # 删除目录页
        is_catalog = False
        if title == '飞书云文档' and char_count < 500:
            is_catalog = True
        elif char_count < 800 and re.search(r'(列表|显示设置|上传|新建|名称|所有者|修改时间)', c) and not re.search(r'## ', c):
            is_catalog = True

        if is_catalog:
            f.unlink(); deleted += 1
            continue

        files_info.append((f, title, char_count, c))

    # 2. 智能章节排序 — 从标题提取章节号，按课程结构排序
    def sort_key(item):
        f, title, _, _ = item
        t = title

        # 提取明确的章节号
        m = re.search(r'第\s*(\d+)\s*章', t)
        if m: return (int(m.group(1)), 0)

        # 已知关键词顺序
        keywords = [
            ('Mewcode Agent项目', 0), ('必读', 0.5),
            ('什么是 Coding Agent', 1), ('前置准备', 1.5),
            ('LLM API', 2), ('动手实现对话', 2.1), ('源码解析.*LLM', 2.2),
            ('Function Calling', 3), ('动手实现工具', 3.1), ('源码解析.*工具注册', 3.2),
            ('ReAct.*Agent Loop', 4), ('动手实现 Agent Loop', 4.1), ('源码解析.*Agent 主循环', 4.2),
            ('System Prompt', 5), ('动手设计提示词', 5.1), ('源码解析.*系统提示词', 5.2),
            ('五层', 6), ('动手实现权限', 6.1), ('源码解析.*五层权限', 6.2),
            ('MCP 协议', 7), ('动手接入 MCP', 7.1), ('源码解析.*MCP', 7.2),
            ('上下文压缩', 8), ('动手实现上下文', 8.1), ('源码解析.*上下文', 8.2),
            ('跨会话记忆', 9), ('动手实现记忆', 9.1), ('源码解析.*会话持久化', 9.2),
            ('Slash Command', 10), ('动手实现命令', 10.1), ('源码解析.*命令注册', 10.2),
            ('Skill.*可复用', 11), ('动手实现.*Skill', 11.1), ('源码解析.*技能加载', 11.2),
            ('Hook.*生命周期', 12), ('动手实现.*Hook', 12.1), ('源码解析.*事件钩子', 12.2),
            ('SubAgent', 13), ('动手实现子', 13.1), ('源码解析.*子 Agent', 13.2),
            ('Git Worktree', 14), ('动手实现 Worktree', 14.1), ('源码解析.*Git 工作树', 14.2),
            ('多 Agent.*团队', 15), ('动手实现 Agent Teams', 15.1), ('源码解析.*多 Agent', 15.2),
            ('回顾与展望', 16),
            ('面试亮点', 17), ('如何介绍', 18), ('简历模板', 19), ('面试题', 20),
            ('Claude Code', 21),
        ]
        for kw, order in keywords:
            if re.search(kw, t):
                # 子排序: 理论(0) < 实战(1) < Go(2) < Python(3) < Java(4)
                sub = 0
                if '实战' in t or '动手' in t: sub = 1
                elif 'Go源码' in t: sub = 2
                elif 'Python源码' in t: sub = 3
                elif 'Java源码' in t: sub = 4
                return (order, sub)

        return (99, 0)  # unknown pages at end

    files_info.sort(key=sort_key)

    # 3. 重命名 (按排序后的顺序)
    for i, (f, title, _, _) in enumerate(files_info, 1):
        new_name = f'{i:02d}_{sfn(title)}.md'
        new_path = f.parent / new_name
        if f != new_path:
            if new_path.exists(): new_path.unlink()
            f.rename(new_path)
        total_chars += files_info[i-1][2]

    # 4. 图片内嵌 base64
    embedded = 0
    for f in sorted(output_dir.glob("*.md")):
        if f.name == "README.md": continue
        c = f.read_text(encoding='utf-8')
        img_dir = output_dir / "images"

        def embed_img(m):
            nonlocal embedded
            alt, src = m.group(1), m.group(2)
            img = img_dir / Path(src).name
            if img.exists():
                b64 = base64.b64encode(img.read_bytes()).decode()
                embedded += 1
                return f'![{alt}](data:image/png;base64,{b64})'
            return m.group(0)

        c2 = re.sub(r'!\[([^\]]*)\]\((images/[^)]+)\)', embed_img, c)
        if c2 != c:
            f.write_text(c2, encoding='utf-8')

    # 5. 整理 sizes for report
    sizes = []
    for f in sorted(output_dir.glob("*.md")):
        if f.name == "README.md": continue
        c = f.read_text(encoding='utf-8')
        m = re.search(r'^# (.+)', c)
        title = m.group(1) if m else f.stem
        text = re.sub(r'!\[.*?\]\(.*?\)', '', c)
        text = re.sub(r'```.*?```', '', text, flags=re.DOTALL)
        text = re.sub(r'[#>*\-\|\s]', '', text)
        sizes.append((f.name, len(text), title))

    content_count = len(sizes)
    print(f"  删除目录页: {deleted} | 内嵌图片: {embedded} | 排序完成")
    return deleted, total_chars, content_count, 0, sizes

def generate_report(output_dir, start_time, page_count, deleted, total_chars, content_count, sizes):
    """输出统计报告"""
    elapsed = time.time() - start_time

    panel("爬取报告", [
        f"输出目录: {output_dir}",
        f"发现页面: {page_count} 个",
        f"删除目录页: {deleted} 个",
        f"最终文件: {content_count} 个",
        f"图片数量: {len(list((output_dir/'images').glob('*')))} 张",
        f"总字数: {total_chars:,}",
        f"耗时: {elapsed/60:.1f} 分钟",
    ])

    if sizes:
        print(f"\n  {'─'*60}")
        print(f"  {'文件':<36} {'字数':>8}")
        print(f"  {'─'*60}")
        for name, chars, title in sizes:
            short = title[:33] if len(title) > 33 else title
            print(f"  {short:<36} {chars:>8,}")
        print(f"  {'─'*60}")

    # 保存报告
    report_path = output_dir / "CRAWL_REPORT.txt"
    lines = [f"飞书文档爬取报告", f"{'='*50}", f"",
             f"爬取时间: {time.strftime('%Y-%m-%d %H:%M:%S')}",
             f"发现页面: {page_count}", f"删除目录页: {deleted}",
             f"最终文件: {content_count}", f"总字数: {total_chars:,}",
             f"耗时: {elapsed/60:.1f} 分钟", f"",
             f"文件列表:", f"{'-'*40}"]
    for name, chars, title in sorted(sizes):
        lines.append(f"  {name}  — {chars:,}字")
    report_path.write_text('\n'.join(lines), encoding='utf-8')

# ========== 核心爬取逻辑 ==========
async def do_crawl():
    """执行爬取 — 由菜单调用"""
    start_time = time.time()
    url = config.url
    output_dir = config.output_dir
    DOMAIN = urlparse(url).netloc
    IMAGES_DIR = output_dir / "images"

    panel("开始爬取", [
        f"目标URL: {url}",
        f"输出目录: {output_dir}",
        f"导出格式: {'PDF合并文件' if config.export_format == 'pdf' else 'Markdown + 图片内嵌'}",
    ])

    # 检查是否已有内容，询问是否清空 (n=不清空继续追加)
    existing = list(output_dir.glob("*.md"))
    if existing:
        print(f"\n  ⚠ 输出目录已有 {len(existing)} 个文件")
        c = input("  是否清空后重新爬取？[Y/n]: ").strip().lower()
        if c == 'n':
            print("  保留已有文件，继续追加...")
        else:
            for f in output_dir.glob("*.md"): f.unlink()
            for f in IMAGES_DIR.glob("*"): f.unlink()
    else:
        for f in output_dir.glob("*.md"): f.unlink()
        for f in IMAGES_DIR.glob("*"): f.unlink()
    output_dir.mkdir(parents=True, exist_ok=True)
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as p:
        USER_DATA = Path("./feishu_profile")
        USER_DATA.mkdir(parents=True, exist_ok=True)
        context = await p.chromium.launch_persistent_context(
            str(USER_DATA), headless=False,
            viewport={'width':1400,'height':900},
            args=['--disable-blink-features=AutomationControlled']
        )
        page = context.pages[0] if context.pages else await context.new_page()

        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        except: pass
        await asyncio.sleep(8)  # 加长等待确保渲染

        if 'passport' in page.url or 'login' in page.url:
            print("\n  🔐 需要登录！请在浏览器中扫码/登录飞书...")
            for i in range(600):
                await asyncio.sleep(1)
                try:
                    if '/wiki/' in page.url and 'passport' not in page.url:
                        print("  ✓ 登录成功！\n"); await asyncio.sleep(3); break
                except: pass
                if i % 30 == 0: print(f"    等待登录... ({i}秒)")

        # 发现页面
        hr("阶段 1/3: 发现页面")
        pages = await extract_pages_list(page, DOMAIN)
        total = len(pages)
        print(f"\n  ✓ 共发现 {total} 个页面\n")

        # 爬取
        hr("阶段 2/3: 爬取内容")
        counter = 0

        for idx, info in enumerate(pages):
            page_url = info['url']
            b = bar(idx, total, 30, f"准备...")
            print(f"\r{b}", end='', flush=True)

            try:
                await page.goto(page_url, wait_until="domcontentloaded", timeout=40000)
            except:
                try: await page.goto(page_url, wait_until="load", timeout=30000)
                except: pass
            await asyncio.sleep(3)

            if 'passport' in page.url or 'login' in page.url:
                b = bar(idx+1, total, 30, "SKIP(需登录)")
                print(f"\r{b}", end='', flush=True)
                continue

            # 标题
            try:
                title = await page.evaluate("""
                    () => {
                        for(const s of ['.wiki-title','.doc-title','h1','[class*="page-title"]','[class*="wiki-title"]']){
                            const e=document.querySelector(s);if(e){const t=e.textContent.trim().replace(/[\\u200b\\u200c\\u200d\\ufeff]/g,'');if(t&&t.length>2)return t;}}
                        const bc=document.querySelector('[class*="breadcrumb"]');
                        if(bc){const ss=bc.querySelectorAll('span,a');if(ss.length){const t=ss[ss.length-1].textContent.trim().replace(/[\\u200b\\u200c\\u200d\\ufeff]/g,'');if(t&&t.length>2&&!t.includes('飞书'))return t;}}
                        let dt=document.title.replace(/ - 飞书云文档/,'').replace(/ - 飞书知识库/,'').replace(/[\\u200b\\u200c\\u200d\\ufeff]/g,'');
                        return dt.length>2?dt:'未命名';
                    }
                """)
                title = strip_zws(title)
            except:
                title = info.get('title', '未命名')

            # 提取
            try:
                md, blob_map = await extract_page(page, IMAGES_DIR)
            except:
                b = bar(idx+1, total, 30, f"ERR(页面崩溃)")
                print(f"\r{b}", flush=True)
                continue
            if not md or len(md) < 30:
                b = bar(idx+1, total, 30, f"SKIP({len(md) if md else 0}字)")
                print(f"\r{b}", end='', flush=True)
                continue

            # 图片处理: 爬取阶段统一用外链，post_process阶段统一内嵌base64
            # 1. 替换blob图片 — 如果blob URL已在 ![]() 中，只替换URL，否则加完整语法
            for blob_url, local_path in blob_map.items():
                if blob_url in md:
                    # 检查blob URL是否已在 ![]() 包裹中
                    pattern = re.escape(blob_url)
                    if re.search(r'!\[[^\]]*\]\(' + pattern + r'\)', md):
                        md = md.replace(blob_url, local_path)  # 只替换URL
                    else:
                        md = md.replace(blob_url, f"![图片]({local_path})")  # 完整包装

            # 2. 下载正则图片
            for m in re.finditer(r'!\[([^\]]*)\]\(([^)]+)\)', md):
                src = m.group(2)
                if src.startswith('images/') or src.startswith('data:'):
                    continue
                local = await download_img(page, src, IMAGES_DIR)
                if not local:
                    try:
                        b64 = await page.evaluate("""async(u)=>{try{const r=await fetch(u);const b=await r.blob();return new Promise(r=>{const fr=new FileReader();fr.onloadend=()=>r(fr.result);fr.readAsDataURL(b)})}catch(e){return null}}""", src)
                        if b64 and ',' in b64:
                            h = hashlib.md5(src.encode()).hexdigest()[:12]
                            data = base64.b64decode(b64.split(',')[1])
                            if len(data) > 100:
                                (IMAGES_DIR / f"{h}.png").write_bytes(data)
                                local = f"images/{h}.png"
                    except: pass
                if local:
                    md = md.replace(src, local)

            counter += 1
            fn = f"{counter:02d}_{sfn(title)}.md"
            (output_dir / fn).write_text(f"# {title}\n\n> 来源: [{page_url}]({page_url})\n\n---\n\n{md}", encoding='utf-8')
            b = bar(idx+1, total, 30, f"{title[:30]} | {len(md)}字")
            print(f"\r{b}", flush=True)

        await context.close()

    print()

    # 后处理: 自动清理 + 排序 + 内嵌 + 报告
    hr("阶段 3/3: 清理与统计")
    deleted, total_chars, content_count, _, sizes = post_process(output_dir)

    # README
    files = sorted([f for f in output_dir.glob("*.md") if f.name != "README.md"])
    lines = ["# 飞书文档索引\n\n", f"爬取时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n",
             f"共 {len(files)} 个页面 | {total_chars:,} 字\n\n---\n\n"]
    for f in files:
        c = f.read_text(encoding='utf-8')
        m = re.search(r'^# (.+)', c)
        lines.append(f"- [{m.group(1) if m else f.stem}]({f.name})\n")
    (output_dir / "README.md").write_text(''.join(lines), encoding='utf-8')

    generate_report(output_dir, start_time, page_count=len(files) + deleted,
                    deleted=deleted, total_chars=total_chars, content_count=len(files),
                    sizes=sizes)

    # PDF 导出
    if config.export_format == 'pdf':
        hr("导出 PDF")
        md_files = sorted([f for f in output_dir.glob("*.md") if f.name != "README.md"])
        if md_files:
            pdf_path = output_dir / "feishu_docs.pdf"
            await export_pdf(md_files, pdf_path, title="飞书文档")
            # 可选: 删除md文件只保留PDF
            keep_md = input("  保留 Markdown 文件? (Y/n): ").strip().lower()
            if keep_md == 'n':
                for f in md_files:
                    f.unlink()
                print("  已删除 Markdown 文件，仅保留 PDF")
        else:
            print("  没有文件可导出")

    try:
        print("\n  爬取完成! 按回车返回主菜单...")
        input()
    except (EOFError, KeyboardInterrupt):
        pass

# ========== PDF 导出 ==========
def markdown_to_html(md_text):
    """将 Markdown 转为带样式的 HTML"""
    import markdown
    # 使用扩展: 表格, 代码高亮, fenced code
    md = markdown.Markdown(extensions=['tables', 'fenced_code', 'codehilite', 'toc'])
    body = md.convert(md_text)
    return body

async def export_pdf(md_files, output_path, title="飞书文档"):
    """将多个 md 文件合并为一个 PDF"""
    import markdown
    md = markdown.Markdown(extensions=['tables', 'fenced_code', 'toc'])

    # 构建完整 HTML
    all_body = ""
    toc_items = []
    for i, f in enumerate(md_files, 1):
        c = f.read_text(encoding='utf-8')
        m = re.search(r'^# (.+)', c)
        page_title = m.group(1) if m else f.stem
        # 去掉首行标题（我们用自己的模板）和来源行
        c = re.sub(r'^# .+\n', '', c, count=1)
        c = re.sub(r'> 来源:.*\n', '', c)
        c = re.sub(r'^---\s*\n', '', c, count=1, flags=re.MULTILINE)

        body = md.convert(c)
        toc_items.append(f'<li><a href="#page{i}">{page_title}</a></li>')
        all_body += f'<div class="page" id="page{i}"><h1>{page_title}</h1>{body}</div>\n'

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

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        await page.set_content(html, wait_until="networkidle")
        await page.pdf(
            path=str(output_path),
            format='A4',
            print_background=True,
            margin={'top': '2cm', 'bottom': '2cm', 'left': '2.5cm', 'right': '2.5cm'}
        )
        await browser.close()

    print(f"  PDF 已导出: {output_path} ({output_path.stat().st_size // 1024} KB)")

def clear_login():
    """清除浏览器登录状态，用于切换飞书账号"""
    import shutil
    profile = Path("./feishu_profile")
    if profile.exists():
        size_mb = sum(f.stat().st_size for f in profile.rglob('*') if f.is_file()) / 1024 / 1024
        print(f"\n  当前登录数据: {size_mb:.1f} MB")
        c = input("  确认清除登录状态？清除后下次需要重新扫码 [y/N]: ").strip().lower()
        if c == 'y':
            shutil.rmtree(profile)
            print("  已清除。下次爬取时将弹出登录页面。")
        else:
            print("  已取消。")
    else:
        print("\n  没有登录数据，无需清除。")
    input("  按回车返回菜单...")

# ========== 菜单入口 ==========
def run_menu():
    """主菜单循环"""
    # 命令行快捷入口
    if len(sys.argv) >= 2:
        config.set_url(sys.argv[1])
        if len(sys.argv) >= 3:
            config.output_dir = Path(sys.argv[2])  # 手动指定覆盖自动
        asyncio.run(do_crawl())
        return

    while True:
        menu_banner()
        show_config()
        main_menu()
        c = input("  请选择 [0-4]: ").strip()

        if c == '1':
            sub_menu_url()
        elif c == '2':
            sub_menu_dir()
        elif c == '3':
            sub_menu_format()
        elif c == '4':
            if not config.url or 'feishu.cn' not in config.url:
                print("\n  请先设置目标URL（选项1）")
                input("  按回车继续...")
                continue
            asyncio.run(do_crawl())
        elif c == '5':
            clear_login()
        elif c == '0':
            print("\n  再见！\n")
            break
        else:
            continue

if __name__ == "__main__":
    run_menu()
