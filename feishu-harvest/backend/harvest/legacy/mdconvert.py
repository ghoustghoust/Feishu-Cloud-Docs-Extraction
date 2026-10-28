# -*- coding: utf-8 -*-
"""legacy.mdconvert — 飞书 DOM → Markdown 转换器。

自 v22 `feishu_crawler.py` 逐函数移植 (2026-08 实测验证过的选择器):
heading / code / image / quote / list / todo / table / callout / bookmark /
grid / file / formula / divider 全类型覆盖, 代码块恢复与导航壳剔除原样保留。

修复 (相对 v22): 原文件多处字符串过度转义 (`f"\\n```"` 输出字面 \\n 文本、
`r'\\s'` 永不匹配), 本版改为正确转义。其余行为逐行对齐。

本模块只依赖 BeautifulSoup, 不依赖 Playwright — 可单测。
"""
from __future__ import annotations

import re

from bs4 import NavigableString, Tag

from harvest.textutil import is_page_link, strip_zws

# =====================================================================
# 行内文本
# =====================================================================

_SKIP_TAGS = ("script", "style", "noscript", "svg", "path", "br", "wbr", "meta")


def convert_ace_line(el) -> str:
    """行内元素 → Markdown 行内语法 (加粗/斜体/删除线/行内代码/链接)。"""
    if isinstance(el, NavigableString):
        return strip_zws(str(el))
    if not isinstance(el, Tag):
        return ""
    tag = (el.name or "").lower()
    if tag in _SKIP_TAGS:
        return ""
    children = "".join(convert_ace_line(c) for c in el.children)
    style = (el.get("style") or "").lower()
    is_bold = tag in ("strong", "b") or "bold" in style or "font-weight:bold" in style \
        or "font-weight:700" in style or "700" in str(el.get("data-font-weight", ""))
    is_italic = tag in ("em", "i") or "italic" in style or "font-style:italic" in style
    if tag == "a" and el.get("href"):
        if is_page_link(el["href"]):  # 飞书页面链接 → 纯文本 (本地打开会报错, 不保留跳转)
            return children
        return f"[{children}]({el['href']})"
    if tag == "code":
        return f"`{children}`"
    if is_bold:
        return f"**{children}**"
    if is_italic:
        return f"*{children}*"
    if tag in ("s", "del", "strike"):
        return f"~~{children}~~"
    return children


def _ace_lines(block: Tag) -> list[str]:
    out = []
    for ace in block.find_all(class_=re.compile(r"ace-line")):
        text = strip_zws("".join(convert_ace_line(c) for c in ace.children))
        if text.strip():
            out.append(text)
    return out


# =====================================================================
# 块级转换器
# =====================================================================

def convert_text_block(block: Tag) -> str:
    lines = _ace_lines(block)
    return "\n".join(lines) if lines else strip_zws(block.get_text())


def convert_heading_block(block: Tag, level: int) -> str:
    content = block.find(class_=re.compile(r"heading-content"))
    if not content:
        content = block.find(class_=re.compile(r"ace-line"))
    text = strip_zws(content.get_text() if content else block.get_text())
    return f"\n{'#' * level} {text}\n" if text else ""


_CODE_LANG_SET = {
    "bash", "shell", "python", "go", "java", "javascript", "typescript", "json",
    "yaml", "html", "css", "sql", "rust", "c", "cpp", "c++", "plain text", "text",
    "ruby", "php", "swift", "kotlin", "scala", "makefile", "dockerfile", "xml",
    "toml", "ini", "markdown", "yml", "js", "ts", "py", "sh", "conf"}

_LANG_MAP = {
    "bash": "bash", "shell": "bash", "sh": "bash", "python": "python", "py": "python",
    "go": "go", "golang": "go", "java": "java", "javascript": "javascript", "js": "javascript",
    "typescript": "typescript", "ts": "typescript", "json": "json", "yaml": "yaml", "yml": "yaml",
    "plain text": "", "text": "", "plain": "", "html": "html", "css": "css", "sql": "sql",
    "rust": "rust", "c": "c", "cpp": "cpp", "c++": "cpp", "ruby": "ruby", "php": "php",
    "swift": "swift", "kotlin": "kotlin", "scala": "scala", "makefile": "makefile",
    "dockerfile": "dockerfile", "xml": "xml", "toml": "toml", "ini": "ini",
    "markdown": "markdown", "conf": ""}


def convert_code_block(block: Tag) -> str:
    """代码块 — v22 多策略行提取, 移植并修复转义。"""
    lang = ""
    raw_lang = ""
    for sel in ("language", "code-lang", r"^lang", "code-tag", "code-lang-label"):
        lang_el = block.find(class_=re.compile(sel, re.I))
        if lang_el:
            raw_lang = lang_el.get_text(strip=True).lower().strip(":：")
            if raw_lang:
                break
    if not raw_lang:
        for el in block.find_all(["span", "div", "button"]):
            text = el.get_text(strip=True).lower()
            if text in _CODE_LANG_SET:
                raw_lang = text
                break
    if raw_lang:
        lang = _LANG_MAP.get(raw_lang, raw_lang)

    lines: list[str] = []
    # 策略1: 行级元素 (飞书多种 class 名)
    for line_cls in ("ace-line", "code-line", "cm-line", "view-line", "line-number"):
        found = block.find_all(class_=re.compile(line_cls, re.I))
        if found:
            lines = [strip_zws(el.get_text()) for el in found]
            break
    # 策略2: 代码容器 get_text('\n')
    if not lines:
        container = None
        for sel in ("code", "pre", "CodeMirror", "code-content", "code-body",
                    "code-lines", "highlight", "code-block-content", "code-block", "code-wrap"):
            container = block.find(class_=re.compile(sel, re.I)) or container
            if container:
                break
        if container:
            lines = [s for l in container.get_text("\n").split("\n")
                     if (s := strip_zws(l)) and s != "复制"]
    # 策略3: 整块回退
    if not lines:
        lines = [s for l in block.get_text("\n").split("\n")
                 if (s := strip_zws(l)) and s != "复制"]

    if lines and lines[0].strip() in ("代码块", raw_lang):
        lines = lines[1:]
    code_text = "\n".join(lines)
    code_text = re.sub(r"^代码块\s*\n?", "", code_text)
    if raw_lang:
        code_text = re.sub(
            re.escape(raw_lang) + r"\s*[\n:]?\s*", "", code_text, count=1, flags=re.I)
    code_text = code_text.strip("\n").rstrip()
    return f"\n```{lang}\n{code_text}\n```\n" if code_text else ""


def convert_image_block(block: Tag) -> str:
    parts = []
    for img in block.find_all("img"):
        src = img.get("src") or img.get("data-src") or ""
        alt = strip_zws(img.get("alt") or "图片")
        if src and not src.startswith("data:"):
            parts.append(f"\n![{alt}]({src})\n")
    return "".join(parts)


def convert_quote_block(block: Tag) -> str:
    lines = [f"> {t}" for t in _ace_lines(block)]
    if not lines:
        lines = [f"> {l}" for l in strip_zws(block.get_text()).split("\n") if l.strip()]
    return "\n".join(lines)


def convert_list_block(block: Tag) -> str:
    is_ordered = bool(block.find(class_=re.compile(r"list-number|list-order|list-index|number|order")))
    if not is_ordered:
        if re.match(r"^\d+[.)]", block.get_text(strip=True)):
            is_ordered = True
    prefix = "1." if is_ordered else "-"
    lines = [f"{prefix} {t}" for t in _ace_lines(block)]
    if not lines and strip_zws(block.get_text()).strip():
        lines = [f"{prefix} {strip_zws(block.get_text()).strip()}"]
    return "\n".join(lines)


def convert_todo_block(block: Tag) -> str:
    checked = bool(block.find("input", {"checked": True}) or block.find(class_=re.compile(r"checked")))
    prefix = "- [x]" if checked else "- [ ]"
    lines = [f"{prefix} {t}" for t in _ace_lines(block)]
    if not lines and strip_zws(block.get_text()).strip():
        lines = [f"{prefix} {strip_zws(block.get_text()).strip()}"]
    return "\n".join(lines)


def convert_callout_block(block: Tag) -> str:
    inner = []
    for child in block.children:
        if isinstance(child, Tag):
            cls = " ".join(child.get("class") or [])
            if child.get("data-block-type") or "block" in cls:
                md = convert_block(child)
                if md:
                    inner.append(md)
    content = "\n".join(inner) if inner else strip_zws(block.get_text())
    if not content.strip():
        return ""
    return "\n" + "\n".join(
        f"> {l}" if not l.startswith(">") else l for l in content.strip().split("\n")) + "\n"


def convert_bookmark_block(block: Tag) -> str:
    parts = []
    for a in block.find_all("a", href=True):
        text = strip_zws(a.get_text()) or a["href"]
        if is_page_link(a["href"]):
            parts.append(f"- {text}")
        else:
            parts.append(f"- [{text}]({a['href']})")
    if not parts and strip_zws(block.get_text()).strip():
        parts.append(strip_zws(block.get_text()).strip())
    return "\n".join(parts)


convert_grid_block = convert_image_block  # 分栏/画廊: 取出其中的图片


def convert_file_block(block: Tag) -> str:
    parts = []
    for a in block.find_all("a", href=True):
        text = strip_zws(a.get_text()) or a["href"]
        if is_page_link(a["href"]):
            parts.append(f"- {text}")
        else:
            parts.append(f"- [{text}]({a['href']})")
    if not parts:
        name_el = block.find(class_=re.compile(r"file-name|file-title|name", re.I))
        name = strip_zws(name_el.get_text()) if name_el else ""
        text = strip_zws(block.get_text())
        if name:
            parts.append(f"**附件**: {name}")
        elif text.strip():
            parts.append(f"**附件**: {text.strip()[:100]}")
    return "\n".join(parts)


def table_to_md(table: Tag) -> str:
    rows = [
        [td.get_text(strip=True).replace("\n", " ").replace("|", "\\|")
         for td in tr.find_all(["td", "th"])]
        for tr in table.find_all("tr")]
    rows = [r for r in rows if r]
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    for r in rows:
        r.extend([""] * (width - len(r)))
    body = "\n".join("| " + " | ".join(r) + " |" for r in rows[1:])
    return ("\n| " + " | ".join(rows[0]) + " |\n| " + " | ".join(["---"] * width)
            + " |\n" + body + "\n")


# =====================================================================
# 块分发 + 递归
# =====================================================================

def convert_block(block) -> str:
    if not isinstance(block, Tag):
        return ""
    bt = (block.get("data-block-type") or "").lower()
    cls = " ".join(block.get("class") or [])
    if bt.startswith("heading") or "heading" in bt:
        m = re.search(r"heading(\d)", bt)
        return convert_heading_block(block, int(m.group(1)) if m else 2)
    if "heading" in cls:
        for i in range(6, 0, -1):
            if f"heading{i}" in cls or f"heading-{i}" in cls:
                return convert_heading_block(block, i)
    if bt == "code" or "code-block" in cls:
        return convert_code_block(block)
    if bt == "image" or "image-block" in cls:
        return convert_image_block(block)
    if bt == "quote" or "quote-block" in cls:
        return convert_quote_block(block)
    if bt == "divider" or "divider-block" in cls:
        return "\n---\n"
    if bt in ("list", "list-item", "bulleted", "bulleted_list", "numbered",
              "numbered_list", "ordered") or "list-block" in cls:
        return convert_list_block(block)
    if bt == "todo" or "todo-block" in cls:
        return convert_todo_block(block)
    if bt == "table" or "table-block" in cls:
        return table_to_md(block)
    if bt == "callout" or "callout-block" in cls:
        return convert_callout_block(block)
    if bt in ("bookmark", "link") or "bookmark-block" in cls:
        return convert_bookmark_block(block)
    if bt in ("grid", "gallery") or "grid-block" in cls:
        return convert_grid_block(block)
    if bt in ("bitable", "base_refer") or "bitable" in cls:
        return ""  # 多维表格块: UI 壳文本不入正文, 数据由捕获通道提取
    if bt in ("file", "attachment") or "file-block" in cls:
        return convert_file_block(block)
    if bt in ("formula", "equation", "math"):
        text = strip_zws(block.get_text())
        return f"\n$$\n{text}\n$$\n" if text.strip() else ""
    if bt in ("text", "paragraph", "p", "") or "text-block" in cls or "paragraph" in cls:
        text = convert_text_block(block)
        return f"\n{text}\n" if text.strip() else ""
    if "render-unit" in cls:
        return convert_children(block)
    return ""


def convert_children(parent: Tag) -> str:
    parts = []
    for child in parent.children:
        if not isinstance(child, Tag):
            continue
        cls = " ".join(child.get("class") or [])
        if "block" in cls or child.get("data-block-type"):
            md = convert_block(child)
        else:
            md = convert_children(child)
        if md:
            parts.append(md)
    return "\n".join(parts)


def extract_blocks(container: Tag):
    """顶层块收集 (v22 _extract_blocks_recursive)。"""
    for child in container.children:
        if not isinstance(child, Tag):
            continue
        cls = " ".join(child.get("class") or [])
        is_block = bool(child.get("data-block-type")) or (
            "block" in cls and "page-block-children" not in cls)
        if is_block:
            md = convert_block(child)
            if md:
                yield md
        else:
            yield from extract_blocks(child)


# =====================================================================
# 代码块恢复 (innerText 回退路径用)
# =====================================================================

def recover_code_blocks(text: str) -> str:
    """从 innerText 检测 "语言标签 + 复制按钮" 模式, 重新包装为 ``` 围栏。

    已存在的 ``` 区域原样保留; 修复 v22 中永不匹配的转义。
    """
    lines = text.split("\n")
    result: list[str] = []
    in_code = False
    code_lang = ""
    code_buf: list[str] = []
    in_fence = False

    def flush():
        nonlocal in_code, code_lang, code_buf
        if code_buf:
            result.append(f"```{code_lang}")
            result.extend(code_buf)
            result.append("```")
            result.append("")
            code_buf = []
        in_code = False
        code_lang = ""

    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith("```"):
            in_fence = not in_fence
            result.append(lines[i])
            i += 1
            continue
        if in_fence:
            result.append(lines[i])
            i += 1
            continue
        if line == "代码块":
            i += 1
            continue
        is_lang = line.lower() in _CODE_LANG_SET
        next_is_copy = i + 1 < len(lines) and lines[i + 1].strip() == "复制"
        if is_lang and next_is_copy:
            if in_code:
                flush()
            code_lang = _LANG_MAP.get(line.lower(), line.lower())
            in_code = True
            i += 2
            continue
        if in_code:
            if re.match(r"^ch\d+", line) and len(line) < 20:  # 章节标题 → 代码块结束
                flush()
                result.append(lines[i])
            else:
                code_buf.append(lines[i])
            i += 1
            continue
        result.append(lines[i])
        i += 1
    if in_code:
        flush()
    return "\n".join(result)


# =====================================================================
# 导航壳 (侧栏目录树文本混入正文)
# =====================================================================

def repair_fences(text: str) -> str:
    """围栏自愈: 保证每个 ``` 开栏都有合法闭合 (CommonMark 要求闭合行仅含围栏字符)。

    逐行扫描维护开/闭状态:
    - 行首 ``` 无尾随文字 → 合法开/闭, 翻转状态
    - 行首 ``` 带语言标签 ("```markdown") 而当前已开栏 → 上一块先补闭合行,
      本行作为新块开头 (飞书 innerText 恢复时相邻代码块的闭合/语言标签粘连场景)
    - 文本结束仍在栏内 → 末尾补闭合
    """
    lines = text.split("\n")
    out: list[str] = []
    open_len = 0  # 当前开栏围栏长度 (```, ````, …); 0 = 未开栏
    for line in lines:
        stripped = line.lstrip(" ")
        is_fence = stripped.startswith("```")
        if is_fence:
            info = stripped[3:].strip()
            if open_len and not info:
                out.append(line)  # 合法闭合
                open_len = 0
            elif open_len and info:
                out.append("```")  # 先闭合上一块
                out.append(line)   # 本行开新块
                open_len = 3
            else:
                out.append(line)   # 开栏 (语言标签可有可无)
                open_len = 3
        else:
            out.append(line)
    if open_len:
        out.append("```")
    return "\n".join(out)


_NAV_SHELL_MARKERS = ("问问知识库", "互联网公开", "目录", "创建", "最近更新")


def has_nav_shell(text: str) -> bool:
    return bool(text) and "飞书云文档" in text[:50] and "问问知识库" in text[:2000]


def strip_nav_shell(text: str) -> str:
    if not text:
        return text
    lines = text.split("\n")
    first = text.lstrip("\n\r \t").split("\n", 1)[0].strip() if lines else ""
    if first == "飞书云文档" and sum(1 for m in _NAV_SHELL_MARKERS if m in text[:2000]) >= 2:
        for i, line in enumerate(lines[:100]):
            s = line.strip()
            if len(s) > 20 or (s.startswith("#") and len(s) > 5) or s.startswith("!["):
                if len(s) <= 30 and not s.startswith(("#", "![")) \
                        and re.search(r"知识库|目录|飞书云文档", s):
                    continue
                return "\n".join(lines[i:])
        return ""
    return text
