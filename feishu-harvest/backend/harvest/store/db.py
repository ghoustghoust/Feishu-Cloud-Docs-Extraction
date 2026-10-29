# -*- coding: utf-8 -*-
"""store.db — SQLite WAL 任务库: 断点续爬与增量同步的状态来源。

表设计 (见 docs/ARCHITECTURE.md §6.2):

  runs(id, root_url, output_dir, status, started_at, finished_at, stats)
  nodes(token PK, root_token, title, parent, level, obj_type, status,
        chars, content_hash, error, updated_at)
  assets(hash PK, url, path, bytes, created_at)   -- 下载去重

连接策略: 每操作独立连接 (check_same_thread=False + 每线程/协程新建),
WAL 模式下并发读写安全; 任务状态写入均为 upsert, 幂等。
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    root_url TEXT NOT NULL,
    output_dir TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'running',
    started_at REAL NOT NULL,
    finished_at REAL,
    stats TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS nodes (
    token TEXT PRIMARY KEY,
    root_token TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    parent TEXT NOT NULL DEFAULT '',
    level INTEGER NOT NULL DEFAULT 0,
    obj_type INTEGER,
    status TEXT NOT NULL DEFAULT 'pending',
    chars INTEGER NOT NULL DEFAULT 0,
    content_hash TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT '',
    file_path TEXT NOT NULL DEFAULT '',
    updated_at REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_nodes_parent ON nodes(parent);
CREATE INDEX IF NOT EXISTS idx_nodes_status ON nodes(status);
CREATE TABLE IF NOT EXISTS assets (
    hash TEXT PRIMARY KEY,
    url TEXT NOT NULL,
    path TEXT NOT NULL,
    bytes INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);
"""


class Store:
    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.executescript(_SCHEMA)
        # 旧库迁移: 无 file_path 列则补上
        cols = {r[1] for r in self._conn.execute("PRAGMA table_info(nodes)")}
        if "file_path" not in cols:
            self._conn.execute("ALTER TABLE nodes ADD COLUMN file_path TEXT NOT NULL DEFAULT ''")
        self._conn.commit()

    def close(self) -> None:
        try:
            self._conn.commit()
            self._conn.close()
        except Exception:
            pass

    # ---------- runs ----------
    def start_run(self, root_url: str, output_dir: str) -> int:
        cur = self._conn.execute(
            "INSERT INTO runs(root_url, output_dir, status, started_at) VALUES(?,?, 'running', ?)",
            (root_url, output_dir, time.time()))
        self._conn.commit()
        return int(cur.lastrowid)

    def finish_run(self, run_id: int, status: str, stats: dict) -> None:
        self._conn.execute(
            "UPDATE runs SET status=?, finished_at=?, stats=? WHERE id=?",
            (status, time.time(), json.dumps(stats, ensure_ascii=False), run_id))
        self._conn.commit()

    def interrupt_stale_runs(self) -> None:
        """进程崩溃/关机遗留的 running 运行标记为 interrupted (下次启动时回收)。"""
        self._conn.execute(
            "UPDATE runs SET status='interrupted', finished_at=? WHERE status='running'",
            (time.time(),))
        self._conn.commit()

    # ---------- nodes ----------
    def upsert_node(self, token: str, title: str, parent: str, level: int,
                    root_token: str = "", obj_type: int | None = None) -> None:
        self._conn.execute(
            """INSERT INTO nodes(token, root_token, title, parent, level, obj_type, status, updated_at)
               VALUES(?,?,?,?,?,?, COALESCE((SELECT status FROM nodes WHERE token=?),'pending'), ?)
               ON CONFLICT(token) DO UPDATE SET
                 title=excluded.title, parent=excluded.parent, level=excluded.level,
                 obj_type=excluded.obj_type, root_token=excluded.root_token""",
            (token, root_token, title, parent, level, obj_type, token, time.time()))
        self._conn.commit()

    def node(self, token: str) -> dict | None:
        row = self._conn.execute("SELECT * FROM nodes WHERE token=?", (token,)).fetchone()
        return dict(row) if row else None

    def nodes_by_root(self, root_token: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM nodes WHERE root_token=?", (root_token,)).fetchall()
        return [dict(r) for r in rows]

    def mark_running(self, token: str) -> None:
        self._conn.execute(
            "UPDATE nodes SET status='running', error='', updated_at=? WHERE token=?",
            (time.time(), token))
        self._conn.commit()

    def mark_done(self, token: str, chars: int, content_hash: str,
                  file_path: str = "") -> None:
        self._conn.execute(
            "UPDATE nodes SET status='done', chars=?, content_hash=?, error='', "
            "file_path=?, updated_at=? WHERE token=?",
            (chars, content_hash, file_path, time.time(), token))
        self._conn.commit()

    def mark_failed(self, token: str, error: str) -> None:
        self._conn.execute(
            "UPDATE nodes SET status='failed', error=?, updated_at=? WHERE token=?",
            (error[:500], time.time(), token))
        self._conn.commit()

    def reset_interrupted(self, root_token: str = "") -> int:
        """启动时调用: 上次异常退出的 running 节点回退为 pending (崩溃恢复)。"""
        cond = "root_token=? AND status='running'" if root_token else "status='running'"
        cur = self._conn.execute(f"UPDATE nodes SET status='pending' WHERE {cond}",
                                 (root_token,) if root_token else ())
        self._conn.commit()
        return cur.rowcount

    def counts(self, root_token: str = "") -> dict:
        cond = "WHERE root_token=?" if root_token else ""
        args = (root_token,) if root_token else ()
        rows = self._conn.execute(
            f"SELECT status, COUNT(*) n FROM nodes {cond} GROUP BY status", args).fetchall()
        out = {r["status"]: r["n"] for r in rows}
        return {"pending": out.get("pending", 0), "running": out.get("running", 0),
                "done": out.get("done", 0), "failed": out.get("failed", 0)}

    # ---------- assets ----------
    def asset_hit(self, h: str) -> str | None:
        row = self._conn.execute("SELECT path FROM assets WHERE hash=?", (h,)).fetchone()
        return row["path"] if row else None

    def asset_put(self, h: str, url: str, path: str, nbytes: int) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO assets(hash, url, path, bytes, created_at) VALUES(?,?,?,?,?)",
            (h, url, path, nbytes, time.time()))
        self._conn.commit()
