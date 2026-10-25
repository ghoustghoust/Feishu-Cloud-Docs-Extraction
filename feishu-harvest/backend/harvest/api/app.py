# -*- coding: utf-8 -*-
"""api.app — FastAPI 工厂: 挂载路由 + 托管前端静态文件 (生产模式单端口)。"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from harvest.transport.browser import ensure_win_loop_policy

ensure_win_loop_policy()  # 必须在事件循环创建前设置 (Windows + Playwright)

from .routes import router  # noqa: E402  (依赖 loop policy)

FRONTEND_DIST = Path(__file__).resolve().parent.parent.parent.parent / "frontend" / "dist"


def create_app() -> FastAPI:
    app = FastAPI(title="Feishu Harvest", version="2.0.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?",
        allow_methods=["*"], allow_headers=["*"],
    )
    app.include_router(router)

    # 生产模式: 前端已构建 → 后端单端口托管 (SPA fallback 到 index.html)
    if FRONTEND_DIST.exists():
        app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

        @app.get("/{path:path}")
        async def spa(path: str):
            from fastapi.responses import FileResponse
            target = FRONTEND_DIST / path
            if path and target.is_file():
                return FileResponse(target)
            return FileResponse(FRONTEND_DIST / "index.html")
    return app


app = create_app()
