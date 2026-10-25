# -*- coding: utf-8 -*-
"""transport.browser — Playwright 生命周期与多 Tab 页面池。

transport 层是唯一了解 Playwright 的地方:
- BrowserService: 持久化 context (feishu_profile/), 登录态三态检测, 清除登录
- PagePool:      一个 context 开 N 个 Tab, acquire/release; Tab 共享登录 Cookie

未来接入飞书 Open API 时, 新增 transport/openapi.py 暴露同构接口即可整体替换。
"""
from __future__ import annotations

import asyncio
import shutil
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent  # backend/
PROFILE_DIR = BASE_DIR / "feishu_profile"

# Chromium 启动参数 (静态常量, 无外部输入拼接)
CHROMIUM_ARGS: list[str] = ["--disable-blink-features=AutomationControlled"]


def ensure_win_loop_policy() -> None:
    """Windows 下 ProactorEventLoop 才支持 asyncio subprocess (Playwright 启动依赖)。"""
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())


_LOGIN_JS = """() => {
    const t = document.title || '';
    if (/登录|扫码|Sign\\s*in/i.test(t) && t.length < 40) return false;
    const avatar = document.querySelector('[class*="user-avatar"],[class*="sdk-user"],[class*="user-menu"]');
    // 登录表单特征仅在无头像时判 False — 文件预览等短内容页可能含 qrcode 分享控件,
    // 不能单凭 renderLen<50 或 qrcode 误杀 (avatar 存在 = 明确已登录)
    const pwd = document.querySelector('input[type="password"]');
    if (pwd && !avatar) return false;
    if (document.querySelector('[class*="qrcode"]') && !avatar) return false;
    const u = document.querySelector('[class*="render-unit"]');
    if (u && u.innerText.length > 50) return true;
    if (avatar) return true;
    return null;
}"""


async def check_login_state(page) -> bool | None:
    """三态登录检测: False=明确未登录 / True=明确已登录 / None=不确定(按已登录处理)。"""
    url = page.url
    if "passport" in url or "login" in url or "accounts." in url:
        return False
    try:
        r = await page.evaluate(_LOGIN_JS)
        return r if r is not None else None
    except Exception:
        return None


class BrowserService:
    """进程级单例: 启动 Playwright → 持久化 context → 主页面。"""

    def __init__(self) -> None:
        self._pw = None
        self._context = None
        self._page = None
        self._lock = asyncio.Lock()

    @property
    def busy(self) -> bool:
        return getattr(self, "_busy", False)

    @busy.setter
    def busy(self, v: bool) -> None:
        self._busy = v

    @property
    def page(self):
        return self._page

    @property
    def context(self):
        return self._context

    async def ensure_context(self):
        """惰性启动浏览器; 已启动则复用。"""
        async with self._lock:
            if self._context and self._page:
                return self._context
            if self._pw is None:
                from playwright.async_api import async_playwright
                self._pw = await async_playwright().start()
            PROFILE_DIR.mkdir(parents=True, exist_ok=True)
            self._context = await self._pw.chromium.launch_persistent_context(
                str(PROFILE_DIR), headless=False,
                viewport={"width": 1400, "height": 900},
                args=CHROMIUM_ARGS)
            if not self._context.pages:
                self._page = await self._context.new_page()
            else:
                self._page = self._context.pages[0]
            return self._context

    async def new_page(self):
        """从同一 context 开新 Tab (共享登录态)。"""
        if not self._context:
            await self.ensure_context()
        return await self._context.new_page()

    async def check_login(self) -> bool | None:
        await self.ensure_context()
        return await check_login_state(self._page)

    async def clear_login(self) -> None:
        """关闭浏览器并删除持久化 profile (切换账号)。"""
        await self.close()
        if PROFILE_DIR.exists():
            shutil.rmtree(PROFILE_DIR, ignore_errors=True)

    async def close(self) -> None:
        for obj in (self._context, self._pw):
            try:
                if obj:
                    await obj.close()
            except Exception:
                pass
        self._context = self._page = self._pw = None


service = BrowserService()


class PagePool:
    """N 个 Tab 的页面池: acquire() 取页, release() 归还。

    首个 Tab 复用 BrowserService 主页面, 其余按需 new_page —
    全部共享同一持久化 context (同一登录态, 与重度用户多 Tab 浏览一致)。
    """

    def __init__(self, svc: BrowserService, size: int = 3) -> None:
        self._svc = svc
        self._size = max(1, size)
        self._free: list = []
        self._all: list = []
        self._sem = asyncio.Semaphore(self._size)
        self._lock = asyncio.Lock()

    async def acquire(self):
        await self._sem.acquire()
        async with self._lock:
            if self._free:
                return self._free.pop()
            if self._all:            # 第 2..N 个 Tab
                page = await self._svc.new_page()
            else:                    # 第 1 个 Tab 复用主页面
                await self._svc.ensure_context()
                page = self._svc.page
            self._all.append(page)
            return page

    def release(self, page) -> None:
        self._free.append(page)
        self._sem.release()

    async def replace(self, dead) -> object:
        """Tab 崩溃 (TargetClosedError) 后换新页, 避免带尸体继续产生连锁失败。"""
        try:
            if dead in self._all:
                self._all[self._all.index(dead)] = None
            try:
                await dead.close()
            except Exception:
                pass
            new = await self._svc.new_page()
            if None in self._all:
                self._all[self._all.index(None)] = new
            else:
                self._all.append(new)
            return new
        except Exception:
            return dead  # 换新失败则原样归还, 由上层按普通失败处理

    async def close_all(self) -> None:
        for p in self._all[1:]:  # 主页面归 BrowserService 管
            try:
                await p.close()
            except Exception:
                pass
        self._all = []
        self._free = []
        while self._sem._value < self._size:  # type: ignore[attr-defined]
            self._sem.release()
