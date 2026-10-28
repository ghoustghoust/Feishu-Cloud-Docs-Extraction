# -*- coding: utf-8 -*-
"""pipeline.writers — 层级写盘 / README 索引 / 报告 / 图片内嵌。

目录结构对应导航树: 章节节点 → 目录 (有正文存 _intro.md),
叶子节点 → 所在章节目录下 {标题}.md。
所有写入均为 临时文件 + os.replace 原子落盘 (中断不产生半截文件)。
"""
from __future__ import annotations

import base64
import os
import re
import time
from pathlib import Path

from harvest.textutil import sfn, strip_zws


def atomic_write(path: Path, data: str | bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp{os.getpid()}")
    mode = "wb" if isinstance(data, bytes) else "w"
    kw = {} if isinstance(data, bytes) else {"encoding": "utf-8"}
    with tmp.open(mode, **kw) as f:
        f.write(data)
    os.replace(tmp, path)  # 同盘原子重命名


class TreeWriter:
    """基于 token→节点映射的层级写盘器。"""

    def __init__(self, out_dir: Path, pages: list[dict]) -> None:
        self.out = Path(out_dir)
        self.images = self.out / "images"
        self.images.mkdir(parents=True, exist_ok=True)
        self.tok2 = {p["token"]: p for p in pages}
        self.parents: set = {p["parent"] for p in pages if p.get("parent") in self.tok2}

    def chain_dirs(self, token: str) -> list[str]:
        """祖先链目录名 (根 → … → 自身), 已规范化可安全作目录名。"""
        chain, cur, seen = [], token, set()
        while cur and cur in self.tok2 and cur not in seen:
            seen.add(cur)
            chain.append(cur)
            cur = self.tok2[cur].get("parent") or ""
        chain.reverse()
        return [sfn(strip_zws(self.tok2[c].get("title", "")) or c[:8]) for c in chain]

    def target_for(self, token: str) -> tuple[Path, bool]:
        """返回 (目标路径, 是否章节节点)。"""
        chain = self.chain_dirs(token)
        if token in self.parents:  # 章节/文件夹节点
            return (self.out.joinpath(*chain) if chain else self.out), True
        return (self.out.joinpath(*chain[:-1]) if len(chain) > 1 else self.out), False

    def write_page(self, token: str, title: str, url: str, md: str,
                   overwrite: Path | None = None) -> str | None:
        """写入一个页面; 章节节点有正文时写 _intro.md, 叶子返回文件路径。

        overwrite: 重爬时传入该节点上次的文件路径 → 确定性覆盖 (不再生成 _02 副本)。
        图片统一存根目录 images/, 写入子目录时按深度重写为 ../images/ 相对引用。
        """
        doc = f"# {title}\n\n> 来源: {url}\n\n---\n\n{md}"  # 来源仅存文字, 不做跳转链接
        target_dir, is_section = self.target_for(token)
        target_dir.mkdir(parents=True, exist_ok=True)
        depth = len(self.chain_dirs(token)) if is_section else max(len(self.chain_dirs(token)) - 1, 0)
        if depth and "](images/" in doc:
            doc = doc.replace("](images/", f"]({'../' * depth}images/")
        if is_section:
            intro = target_dir / "_intro.md"
            if len(md) >= 30:
                atomic_write(intro, doc)
            return str(intro)
        if overwrite and overwrite.parent == target_dir:
            target = overwrite  # 重爬: 覆盖同一位置
        else:
            base = sfn(title)
            target = target_dir / f"{base}.md"
            if target.exists():
                # 老节点无 file_path 记录: 用来源行 token 判定是否本页上次落盘
                same_page = False
                try:
                    head = target.read_text(encoding="utf-8", errors="replace")[:600]
                    m = re.search(r"> 来源: (\S+)", head)
                    same_page = bool(m and token in m.group(1))
                except Exception:
                    pass
                if not same_page:  # 真·重名 (不同页面) → 副本避让
                    k = 2
                    while target.exists():
                        target = target_dir / f"{base}_{k:02d}.md"
                        k += 1
        atomic_write(target, doc)
        return str(target)

    def write_index(self, pages_total: int, chars: int) -> None:
        from collections import defaultdict
        groups: dict = defaultdict(list)
        for f in sorted(self.out.rglob("*.md")):
            if f.name == "README.md" or "selfcontained" in f.parts:
                continue
            try:
                c = f.read_text(encoding="utf-8")
            except Exception:
                continue
            m = re.search(r"^# (.+)", c)
            title = m.group(1) if m else f.stem
            groups[f.parent.relative_to(self.out).as_posix() or "."].append((f, title))
        lines = [f"# 飞书文档索引\n\n", f"爬取时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n",
                 f"共 {pages_total} 个页面 | {chars:,} 字\n\n---\n\n"]
        for rel_dir in sorted(groups):
            if rel_dir != ".":
                lines.append(f"\n## {rel_dir}\n\n")
            for f, title in sorted(groups[rel_dir], key=lambda x: x[0].name):
                lines.append(f"- [{title}]({f.relative_to(self.out).as_posix()})\n")
        atomic_write(self.out / "README.md", "".join(lines))

    def write_report(self, stats: dict) -> None:
        lines = ["╭─ 采撷报告 " + "─" * 40, ""]
        for k, v in stats.items():
            lines.append(f"  {k}: {v}")
        lines.append("")
        atomic_write(self.out / "CRAWL_REPORT.txt", "\n".join(lines))


def export_selfcontained(out_dir: Path) -> dict:
    """自包含导出: 在 {out}/selfcontained/ 生成 base64 内嵌图片的副本。

    原始 md 保持轻量 (相对路径引用), 自包含副本可单文件迁移。
    返回 {files, bytes}。
    """
    import base64 as _b64
    import re as _re

    out_dir = Path(out_dir)
    img_dir = out_dir / "images"
    dest_root = out_dir / "selfcontained"
    count, total = 0, 0
    for f in out_dir.rglob("*.md"):
        rel = f.relative_to(out_dir)
        if rel.parts[0] == "selfcontained" or f.name == "README.md":
            continue
        text = f.read_text(encoding="utf-8")

        def embed(m):
            img = img_dir / Path(m.group(2)).name  # 所有图片统一存根 images/
            if img.exists():
                b64 = _b64.b64encode(img.read_bytes()).decode()
                return f"![{m.group(1)}](data:image/png;base64,{b64})"
            return m.group(0)

        text2 = _re.sub(r"!\[([^\]]*)\]\(((?:\.\./)*images/[^)]+)\)", embed, text)
        target = dest_root / rel
        atomic_write(target, text2)
        count += 1
        total += target.stat().st_size
    return {"files": count, "bytes": total}


def reorganize_flat_output(out_dir: Path, pages: list[dict]) -> dict:
    """把历史平铺输出按库中导航树重排进层级目录 (不重新爬取)。

    平铺文件名 = sfn(title).md: 标题 → token 反查 → 祖先链目录;
    章节节点 → {链}/_intro.md, 叶子 → {链[:-1]}/{原文件名};
    同时按目录深度重写 images/ 相对引用。返回移动统计。
    """
    from harvest.textutil import sfn

    w = TreeWriter(Path(out_dir), pages)
    by_name: dict = {}
    for p in pages:
        by_name.setdefault(sfn(strip_zws(p.get("title", ""))), p)

    moved = intros = 0
    leftover: list[str] = []
    for f in sorted(Path(out_dir).glob("*.md")):
        if f.name in ("README.md", "CRAWL_REPORT.txt"):
            continue
        node = by_name.get(f.stem)
        if not node:
            leftover.append(f.name)
            continue
        target_dir, is_section = w.target_for(node["token"])
        target_dir.mkdir(parents=True, exist_ok=True)
        depth = len(w.chain_dirs(node["token"])) if is_section \
            else max(len(w.chain_dirs(node["token"])) - 1, 0)
        text = f.read_text(encoding="utf-8")
        from harvest.legacy.mdconvert import repair_fences
        text = repair_fences(text)  # 顺带自愈历史文件中的悬空围栏
        if depth and "](images/" in text:
            text = text.replace("](images/", f"]({'../' * depth}images/")
        if is_section:
            atomic_write(target_dir / "_intro.md", text)
            intros += 1
        else:
            atomic_write(target_dir / f.name, text)
            moved += 1
        f.unlink()
    if moved or intros:
        w.write_index(moved + intros, 0)
    return {"ok": True, "moved": moved, "intros": intros, "leftover": leftover}
