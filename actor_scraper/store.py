"""配置、任务和每位演员的结果存 SQLite，默认不生成 JSONL。"""

import json
import os
import sqlite3
import time
from contextlib import contextmanager

from .config import Settings


class Store:
    def __init__(self, paths):
        self.paths = paths
        with self.connect() as db:
            db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY CHECK(id=1), data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT, state TEXT NOT NULL, request TEXT NOT NULL,
                options TEXT NOT NULL, scope TEXT, summary TEXT NOT NULL DEFAULT '{}',
                total INTEGER NOT NULL DEFAULT 0, done INTEGER NOT NULL DEFAULT 0,
                message TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL, finished_at REAL);
            CREATE TABLE IF NOT EXISTS results (
                id INTEGER PRIMARY KEY AUTOINCREMENT, job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
                name TEXT NOT NULL, status TEXT NOT NULL, data TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS results_job_id ON results(job_id,id);
            """)
            if not db.execute("SELECT id FROM settings WHERE id=1").fetchone():
                defaults = Settings(server=os.environ.get("FYMS_URL", ""), user_id=os.environ.get("FYMS_USER_ID", ""), api_key=os.environ.get("FYMS_API_KEY", ""))
                db.execute("INSERT INTO settings VALUES (1,?)", (defaults.model_dump_json(),))
        try:
            self.paths.database.chmod(0o600)
        except OSError:
            pass

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.paths.database, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            yield db
            db.commit()
        finally:
            db.close()

    def settings(self):
        with self.connect() as db:
            value = db.execute("SELECT data FROM settings WHERE id=1").fetchone()[0]
        return Settings.model_validate_json(value)

    def save_settings(self, settings):
        with self.connect() as db:
            db.execute("UPDATE settings SET data=? WHERE id=1", (settings.model_dump_json(),))

    def create_job(self, request, options):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT id FROM jobs WHERE state IN ('queued','running','cancelling')").fetchone():
                raise ValueError("已有任务运行，请等待完成或停止后再创建")
            cursor = db.execute("INSERT INTO jobs(state,request,options,created_at) VALUES ('queued',?,?,?)", (json.dumps(request, ensure_ascii=False), json.dumps(options, ensure_ascii=False), time.time()))
            return cursor.lastrowid

    def update_job(self, job_id, **values):
        allowed = {"state", "scope", "summary", "total", "done", "message", "finished_at"}
        if not set(values).issubset(allowed):
            raise ValueError("未知任务字段")
        for key in ["scope", "summary"]:
            if key in values:
                values[key] = json.dumps(values[key], ensure_ascii=False)
        with self.connect() as db:
            assignments = ",".join(f"{key}=?" for key in values)
            db.execute(f"UPDATE jobs SET {assignments} WHERE id=?", (*values.values(), job_id))

    def add_result(self, job_id, report):
        with self.connect() as db:
            db.execute("INSERT INTO results(job_id,name,status,data) VALUES (?,?,?,?)", (job_id, report["name"], report["status"], json.dumps(report, ensure_ascii=False)))

    def get_job(self, job_id, include_scope=True):
        with self.connect() as db:
            columns = "*" if include_scope else "id,state,request,options,summary,total,done,message,created_at,finished_at"
            row = db.execute(f"SELECT {columns} FROM jobs WHERE id=?", (job_id,)).fetchone()
        return self._job(row) if row else None

    def list_jobs(self, limit=50):
        return self.query_jobs(limit=limit)["items"]

    def query_jobs(self, *, offset=0, limit=50, q="", state="", mode="", created_after=None, created_before=None, sort="newest"):
        clauses, params = [], []
        if q.strip():
            clauses.append("""(CAST(id AS TEXT)=? OR EXISTS (
                SELECT 1 FROM json_each(request, '$.names') WHERE instr(lower(value), lower(?))>0
            ) OR EXISTS (
                SELECT 1 FROM json_each(scope) WHERE instr(lower(json_extract(value, '$.Name')), lower(?))>0
            ))""")
            params.extend([q.strip().lstrip("#"), q.strip(), q.strip()])
        if state == "active":
            clauses.append("state IN ('queued','running','cancelling')")
        elif state:
            clauses.append("state=?")
            params.append(state)
        if mode:
            clauses.append("json_extract(request, '$.mode')=?")
            params.append(mode)
        for boundary, operator in [(created_after, ">="), (created_before, "<")]:
            if boundary is not None:
                clauses.append(f"created_at {operator} ?")
                params.append(boundary)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        order = "ASC" if sort == "oldest" else "DESC"
        with self.connect() as db:
            total = db.execute("SELECT COUNT(*) FROM jobs" + where, params).fetchone()[0]
            rows = db.execute(
                "SELECT id,state,request,options,summary,total,done,message,created_at,finished_at FROM jobs"
                + where + f" ORDER BY created_at {order}, id {order} LIMIT ? OFFSET ?", (*params, limit, offset),
            ).fetchall()
        return {"items": [self._job(row) for row in rows], "total": total}

    @staticmethod
    def _job(row):
        job = dict(row)
        for field in ["request", "options", "scope", "summary"]:
            if field in job:
                job[field] = json.loads(job[field]) if job[field] else None
        return job

    def results(self, job_id, offset=0, limit=50):
        return self.query_results(job_id, offset=offset, limit=limit)["items"]

    def query_results(self, job_id, *, offset=0, limit=50, q="", status=""):
        clauses, params = ["job_id=?"], [job_id]
        if q.strip():
            clauses.append("instr(lower(name), lower(?))>0")
            params.append(q.strip())
        if status == "issues":
            clauses.append("status IN ('error','partial','not_found')")
        elif status:
            clauses.append("status=?")
            params.append(status)
        where = " WHERE " + " AND ".join(clauses)
        with self.connect() as db:
            total = db.execute("SELECT COUNT(*) FROM results" + where, params).fetchone()[0]
            rows = db.execute("SELECT id,data FROM results" + where + " ORDER BY id LIMIT ? OFFSET ?", (*params, limit, offset)).fetchall()
        return {"items": [{"result_id": row["id"], **json.loads(row["data"])} for row in rows], "total": total}

    def result(self, job_id, result_id):
        with self.connect() as db:
            row = db.execute("SELECT data FROM results WHERE job_id=? AND id=?", (job_id, result_id)).fetchone()
        return json.loads(row[0]) if row else None

    def recover(self):
        with self.connect() as db:
            db.execute("UPDATE jobs SET state='interrupted',message='服务重启中断了任务，可重试未完成演员',finished_at=? WHERE state IN ('queued','running','cancelling')", (time.time(),))

    def request_cancel(self, job_id):
        with self.connect() as db:
            updated = db.execute("UPDATE jobs SET state='cancelling',message='正在停止，等待当前请求结束' WHERE id=? AND state IN ('queued','running','cancelling')", (job_id,)).rowcount
        if not updated:
            raise ValueError("该任务已经结束")

    def prune(self, days):
        with self.connect() as db:
            old = db.execute("SELECT id FROM jobs WHERE finished_at < ? AND state NOT IN ('queued','running','cancelling')", (time.time() - days * 86400,)).fetchall()
            db.execute("DELETE FROM jobs WHERE finished_at < ? AND state NOT IN ('queued','running','cancelling')", (time.time() - days * 86400,))
        # 只清理工具自己的历史导出，缓存图片继续复用。
        for row in old:
            (self.paths.root / "exports" / f"job-{row['id']}.jsonl").unlink(missing_ok=True)
