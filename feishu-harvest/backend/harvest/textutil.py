# -*- coding: utf-8 -*-
"""文本/文件名工具 — 自 v22 移植 (行为完全一致)。"""
from __future__ import annotations

import re

# 飞书正文混入的不可见运算符水印与双向控制字符
_INVIS_RE = r"[\u00ad\u180e\u200b-\u200d\u202a-\u202e\u2060-\u2064\u2066-\u206f\ufeff\ufe00-\ufe0f]"


def strip_zws(text: str) -> str:
    return re.sub(_INVIS_RE, "", text or "")


def sfn(name: str, ml: int = 80) -> str:
    """文件名规范化: 去水印 → 替换非法字符 → 去首尾点空 → 截断。"""
    name = re.sub(_INVIS_RE, "", name or "")
    name = re.sub(r'[\\/*?:"<>|]', "_", name).strip(". \t\n")
    return (name[:ml] if len(name) > ml else name) or "untitled"


_PAGE_PATH_RE = re.compile(
    r"^(?:https?://[^/]*?(?:feishu\.cn|larksuite\.com))?"
    r"(/(?:wiki|docx|doc|base|sheet|file|mindnotes|slides|minutes|wiki_v2)/[^\s]*)",
    re.I)


def is_page_link(url: str) -> bool:
    """飞书页面链接 (wiki/docx/base/…): 导出时降级为纯文本 —
    本地点开需要登录态, 多数环境直接报错; 附件下载/预览等非页面链接不受影响。"""
    if not url:
        return False
    return bool(_PAGE_PATH_RE.match(url.strip()))
