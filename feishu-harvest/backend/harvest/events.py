# -*- coding: utf-8 -*-
"""EventBus — 进程内发布/订阅中心。

- publish(): 管线各层发布结构化事件 (dict, 带 type/stage/…)
- stream():  供 SSE 的异步生成器, 多订阅者互不影响
- recent():  环形缓冲 (最近 500 条), 前端断线重连后回放
"""
from __future__ import annotations

import asyncio
import collections
import time
from typing import AsyncIterator

_RECENT_MAX = 500


class EventBus:
    def __init__(self) -> None:
        self._recent: collections.deque = collections.deque(maxlen=_RECENT_MAX)
        self._queues: set[asyncio.Queue] = set()

    def publish(self, event: dict) -> None:
        event = dict(event)
        event.setdefault("ts", round(time.time(), 3))
        self._recent.append(event)
        for q in list(self._queues):
            try:
                q.put_nowait(event)
            except Exception:
                pass  # 单个订阅者满/关闭不影响他人

    async def stream(self) -> AsyncIterator[dict]:
        q: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._queues.add(q)
        try:
            # 先回放缓冲, 让前端断线重连后恢复状态
            for ev in list(self._recent):
                yield ev
            while True:
                ev = await q.get()
                if ev is None:
                    break
                yield ev
        finally:
            self._queues.discard(q)

    def close(self) -> None:
        for q in list(self._queues):
            try:
                q.put_nowait(None)
            except Exception:
                pass


bus = EventBus()
