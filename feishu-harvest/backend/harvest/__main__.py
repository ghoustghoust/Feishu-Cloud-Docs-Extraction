# -*- coding: utf-8 -*-
"""python -m harvest — 启动后端 (默认 127.0.0.1:19527)。"""
from __future__ import annotations

import argparse
import sys


def main() -> None:
    parser = argparse.ArgumentParser("harvest")
    parser.add_argument("--port", type=int, default=19527)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()

    print("=" * 56)
    print("  Feishu Harvest v2.0 — 飞书知识库采撷")
    print(f"  API + 控制台:  http://{args.host}:{args.port}")
    print("=" * 56, flush=True)

    import uvicorn
    from .api.app import app
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    sys.exit(main())
