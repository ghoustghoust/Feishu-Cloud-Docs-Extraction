# -*- coding: utf-8 -*-
"""core.throttle — 爬取节奏的纯机制层 (与飞书零耦合, 可复用于任何任务)。

三个组件:
- AdaptiveGate  AIMD 自适应导航间隔 (借鉴 Scrapy AutoThrottle / TCP 拥塞控制)
                + 随机抖动 (full jitter, 借鉴 AWS 指数退避结论)
- TokenBucket   通用令牌桶 (图片/附件下载限速)
- CircuitBreaker 三态熔断器 (借鉴 pybreaker), 连续失败自动冷却

所有 sleep 均可被 cancel, 优雅退出友好。
"""
from __future__ import annotations

import asyncio
import random
import time
from collections import deque
from secrets import SystemRandom

# 安全随机源: 抖动虽非密码学场景, 统一用 CSPRNG 消除可预测性
_rand = SystemRandom()


class AdaptiveGate:
    """AIMD 导航闸门: 成功→间隔缓慢下降(加性增), 异常→间隔放大(乘性减)。

    用法::

        gate = AdaptiveGate(min_interval=1.2)
        async with gate:
            await page.goto(url)
        gate.record(ok=True)   # 或 False / ("retry_after", 12.0)
    """

    def __init__(self, min_interval: float = 1.2, ceiling: float = 30.0,
                 floor: float | None = None, adaptive: bool = True,
                 warmup: int = 8) -> None:
        self.floor = floor if floor is not None else max(0.3, min_interval * 0.5)
        self.ceiling = ceiling
        self.adaptive = adaptive
        self._interval = min_interval * 2.0  # 慢启动: 初始取 2 倍保守间隔
        self._min_interval = min_interval
        self._warmup_left = warmup          # 前 N 次保持保守, 观察无异常再收紧
        self._lock = asyncio.Lock()
        self._latencies: deque = deque(maxlen=20)

    @property
    def interval(self) -> float:
        return self._interval

    async def __aenter__(self) -> "AdaptiveGate":
        await self._lock.acquire()
        jitter = _rand.uniform(0.7, 1.3)  # ±30% 抖动: 节奏像人, 不成脉冲
        await asyncio.sleep(self._interval * jitter)
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        self._lock.release()

    def record(self, ok: bool, latency: float | None = None,
               retry_after: float | None = None) -> None:
        """反馈一次请求结果; 429 时把服务端 Retry-After 传入 retry_after。"""
        if latency is not None:
            self._latencies.append(latency)
        if retry_after:
            self._interval = min(self.ceiling, max(self._interval, retry_after + 1.0))
            return
        if self._warmup_left > 0:
            self._warmup_left -= 1
            if not ok:
                self._interval = min(self.ceiling, self._interval * 2)
            return
        if not self.adaptive:
            return
        if ok:
            # 加性增: 每次成功让间隔向 floor 收一点
            self._interval = max(self.floor, self._interval * 0.95)
            # 延迟趋势变差(>2s)时不再收紧, 保护服务端
            if len(self._latencies) >= 10:
                tail = sum(list(self._latencies)[-5:]) / 5
                head = sum(list(self._latencies)[:-5]) / (len(self._latencies) - 5)
                if tail > head * 1.5 and tail > 2.0:
                    self._interval = min(self.ceiling, self._interval * 1.3)
        else:
            # 乘性减: 一旦异常立即翻倍
            self._interval = min(self.ceiling, max(self._interval * 2.0, self._min_interval))


class TokenBucket:
    """经典令牌桶: rate 个/秒, 容量 burst。"""

    def __init__(self, rate: float, burst: float = 1.0) -> None:
        self.rate = rate
        self.burst = burst
        self._tokens = burst
        self._ts = time.monotonic()
        self._lock = asyncio.Lock()

    async def take(self, n: float = 1.0) -> None:
        while True:
            async with self._lock:
                now = time.monotonic()
                self._tokens = min(self.burst, self._tokens + (now - self._ts) * self.rate)
                self._ts = now
                if self._tokens >= n:
                    self._tokens -= n
                    return
                need = (n - self._tokens) / self.rate
            await asyncio.sleep(need)


class CircuitBreaker:
    """CLOSED →(连续 fail_limit 次失败)→ OPEN →(冷却 cool 秒)→ HALF-OPEN →成功→ CLOSED。"""

    CLOSED, OPEN, HALF_OPEN = "closed", "open", "half_open"

    def __init__(self, fail_limit: int = 5, cool: float = 30.0) -> None:
        self.fail_limit = fail_limit
        self.cool = cool
        self.state = self.CLOSED
        self._fails = 0
        self._opened_at = 0.0

    def before(self) -> str:
        """请求前调用: 返回当前状态; OPEN 且冷却未满时抛 RuntimeError。"""
        if self.state == self.OPEN:
            if time.monotonic() - self._opened_at >= self.cool:
                self.state = self.HALF_OPEN
            else:
                raise RuntimeError("circuit open, cooling down")
        return self.state

    def record(self, ok: bool) -> None:
        if ok:
            self._fails = 0
            self.state = self.CLOSED
        else:
            self._fails += 1
            if self.state == self.HALF_OPEN or self._fails >= self.fail_limit:
                self.state = self.OPEN
                self._opened_at = time.monotonic()
