# -*- coding: utf-8 -*-
"""运行配置 — dataclass + JSON 持久化, 线程安全的读写。

只描述"用户可调的偏好", 不承载任何任务状态 (任务状态在 store/)。
"""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass, asdict, field
from pathlib import Path

CONFIG_FILE = Path(__file__).resolve().parent.parent / "data" / "config.json"


@dataclass
class CrawlConfig:
    """爬取参数。默认值即 STRATEGY.md 推荐的保守安全档。"""

    output_dir: str = ""            # 空 = backend/data/outputs
    export_format: str = "md"       # md | pdf
    tabs: int = 3                   # 并发 Tab 数 (1-6)
    min_interval: float = 1.2       # 同 Tab 导航最小间隔(秒), 会叠加随机抖动
    ceiling: float = 30.0           # AIMD 自适应间隔上限(秒), 被限流时最大退避到这
    adaptive: bool = True           # AIMD 自适应节流开关
    embed_images: bool = False      # 自包含导出: 在 selfcontained/ 生成 base64 内嵌副本(原件不动)
    download_attachments: bool = True
    pdf_image_width: int = 1400     # PDF 图片降采样目标宽度(像素), 越小体积越小
    unsupported_policy: str = "placeholder"  # 无法提取的类型(sheet/幻灯等): placeholder=占位说明 | skip=跳过
    bitable_all_views: bool = False  # 多维表格导出全部视图 (默认仅当前视图)
    video_as_link: bool = True      # 页面内视频: 输出地址文本 (False 则忽略)
    nav_timeout: int = 40           # 单页导航超时(秒)
    render_timeout: int = 25        # 内容渲染条件等待上限(秒)
    extract_retries: int = 2        # 提取失败重试次数
    image_concurrency: int = 4      # 单页图片并行下载数 (1-8)
    clean: bool = False             # 任务启动前清空输出目录 (默认增量保留)

    # ---- 内部派生 ----
    def output_path(self, base: Path) -> Path:
        return Path(self.output_dir) if self.output_dir else base / "data" / "outputs"

    def to_dict(self) -> dict:
        return asdict(self)

    def merged(self, **over) -> "CrawlConfig":
        """返回应用了覆盖项的副本 (API 请求局部覆盖默认配置)。"""
        d = self.to_dict()
        for k, v in over.items():
            if v is not None and k in d:
                d[k] = v
        return CrawlConfig(**d)


_lock = threading.Lock()
_current = CrawlConfig()


def _load() -> CrawlConfig:
    try:
        raw = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        data = {k: v for k, v in raw.items() if k in CrawlConfig.__dataclass_fields__}
        return CrawlConfig(**data)
    except Exception:
        return CrawlConfig()


def get() -> CrawlConfig:
    with _lock:
        return _current


def update(**over) -> CrawlConfig:
    global _current
    with _lock:
        _current = _current.merged(**over)
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(
            json.dumps(_current.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        return _current


# 进程启动时恢复持久化配置
_current = _load()
