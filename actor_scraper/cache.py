"""演员档案的持久缓存，以及头像文件缓存。"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from pathlib import Path


class Cache:
    def __init__(self, directory: Path):
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(directory / "actors.sqlite", check_same_thread=False)
        self.lock = threading.Lock()
        self.db.execute("""CREATE TABLE IF NOT EXISTS actors (
            name TEXT PRIMARY KEY, source_id TEXT, status TEXT NOT NULL,
            data TEXT NOT NULL, checked_at REAL NOT NULL)""")
        self.db.execute("""CREATE TABLE IF NOT EXISTS artwork (
            cache_key TEXT PRIMARY KEY, data TEXT NOT NULL, checked_at REAL NOT NULL)""")
        self.db.commit()

    def close(self):
        self.db.close()

    def lookup(self, name: str):
        with self.lock:
            row = self.db.execute(
                "SELECT source_id, status, data, checked_at FROM actors WHERE name = ?", (name,),
            ).fetchone()
        if not row:
            return None
        return {"source_id": row[0], "status": row[1], "data": json.loads(row[2]), "checked_at": row[3]}

    def save(self, name: str, source_id: str, status: str, data: dict):
        with self.lock:
            self.db.execute(
                "INSERT OR REPLACE INTO actors VALUES (?, ?, ?, ?, ?)",
                (name, source_id, status, json.dumps(data, ensure_ascii=False), time.time()),
            )
            self.db.commit()

    def image_path(self, url: str) -> Path:
        folder = self.directory / "images"
        folder.mkdir(exist_ok=True)
        return folder / (hashlib.sha256(url.encode()).hexdigest() + ".image")

    def artwork(self, key: str):
        with self.lock:
            row = self.db.execute("SELECT data, checked_at FROM artwork WHERE cache_key = ?", (key,)).fetchone()
        return (json.loads(row[0]), row[1]) if row else None

    def save_artwork(self, key: str, data: dict):
        with self.lock:
            self.db.execute("INSERT OR REPLACE INTO artwork VALUES (?, ?, ?)", (key, json.dumps(data, ensure_ascii=False), time.time()))
            self.db.commit()
