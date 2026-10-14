"""管理 API 与静态前端，复用 CLI 刮削核心。"""

import json
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image
from pydantic import BaseModel, Field, ValidationError

from .auth import Auth
from .config import JobRequest, Settings
from .fyms import Fyms
from .http import Http, ScrapeError
from .jobs import Jobs
from .paths import DataPaths
from .store import Store


class LoginRequest(BaseModel):
    password: str = Field(min_length=1, max_length=4096)


def create_app(data_dir=None, *, password=None, runner=None):
    paths = DataPaths.create(data_dir)
    store = Store(paths)
    auth = Auth(paths, password)
    jobs = Jobs(store, **({"runner": runner} if runner else {}))

    @asynccontextmanager
    async def lifespan(app):
        store.recover()
        store.prune(store.settings().retention_days)
        yield
        jobs.shutdown()

    app = FastAPI(title="Actor Scraper", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.store, app.state.jobs, app.state.auth = store, jobs, auth

    @app.middleware("http")
    async def origin_check(request, call_next):
        if request.url.path.startswith("/api/") and request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            if origin and urlsplit(origin).netloc != request.headers.get("host"):
                return JSONResponse({"detail": "请求来源不匹配"}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        return JSONResponse({"detail": "；".join(error["msg"] for error in exc.errors())}, status_code=422)

    def require(request: Request):
        auth.require(request)

    def job_or_404(job_id):
        job = store.get_job(job_id, include_scope=False)
        if not job:
            raise HTTPException(404, "任务不存在")
        return job

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.post("/api/login")
    def login(body: LoginRequest, request: Request):
        token = auth.login(body.password, request.client.host if request.client else "local")
        response = JSONResponse({"ok": True})
        response.set_cookie("actor_session", token, httponly=True, samesite="strict", secure=request.url.scheme == "https", max_age=86400)
        return response

    @app.get("/api/session", dependencies=[Depends(require)])
    def session():
        return {"authenticated": True}

    @app.post("/api/logout", dependencies=[Depends(require)])
    def logout(request: Request):
        auth.logout(request.cookies.get("actor_session", ""))
        response = JSONResponse({"ok": True})
        response.delete_cookie("actor_session")
        return response

    @app.get("/api/settings", dependencies=[Depends(require)])
    def get_settings():
        return store.settings().public()

    @app.put("/api/settings", dependencies=[Depends(require)])
    def save_settings(body: dict):
        values = store.settings().model_dump()
        changes = dict(body)
        changes.pop("api_key_configured", None)
        clear_key = changes.pop("clear_api_key", False)
        if not clear_key and not changes.get("api_key"):
            changes.pop("api_key", None)
        if clear_key:
            changes["api_key"] = ""
        try:
            settings = Settings.model_validate({**values, **changes})
        except ValidationError as exc:
            raise HTTPException(422, "；".join(error["msg"] for error in exc.errors())) from exc
        store.save_settings(settings)
        return settings.public()

    @app.post("/api/connection/test", dependencies=[Depends(require)])
    def test_connection():
        settings = store.settings()
        if not settings.server or not settings.api_key:
            raise HTTPException(400, "请保存 FYMS 地址和 API 密钥")
        client = Http(timeout=settings.timeout, rps=0, token=settings.api_key, retries=0)
        try:
            server = Fyms(settings.server, client, settings.user_id)
            data = client.json("GET", server.base + "/Persons", params={"Limit": 1, **server.user_params})
            if not isinstance(data.get("Items"), list):
                raise ScrapeError("服务器未返回兼容的演员列表")
            return {"ok": True, "actors": data.get("TotalRecordCount", len(data["Items"]))}
        except ScrapeError as exc:
            raise HTTPException(502, str(exc)) from exc
        finally:
            client.close()

    @app.get("/api/jobs", dependencies=[Depends(require)])
    def list_jobs():
        return {"items": store.list_jobs()}

    @app.post("/api/jobs", dependencies=[Depends(require)], status_code=201)
    def start_job(body: JobRequest):
        try:
            return jobs.start(body)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/api/jobs/{job_id}", dependencies=[Depends(require)])
    def get_job(job_id: int):
        return job_or_404(job_id)

    @app.post("/api/jobs/{job_id}/cancel", dependencies=[Depends(require)])
    def cancel(job_id: int):
        job_or_404(job_id)
        try:
            jobs.cancel(job_id)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"ok": True}

    @app.post("/api/jobs/{job_id}/retry", dependencies=[Depends(require)], status_code=201)
    def retry(job_id: int):
        job_or_404(job_id)
        try:
            return jobs.retry(job_id)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/api/jobs/{job_id}/results", dependencies=[Depends(require)])
    def results(job_id: int, offset: int = 0, limit: int = 50):
        if offset < 0 or not 1 <= limit <= 200:
            raise HTTPException(400, "分页参数不正确")
        job_or_404(job_id)
        rows = store.results(job_id, offset, limit)
        for row in rows:
            for kind, key in [("primary", "image_path"), ("backdrop", "backdrop_path")]:
                path = row.pop(key, "")
                if path:
                    row[kind + "_preview"] = f"/api/jobs/{job_id}/results/{row['result_id']}/image?kind={kind}"
        return {"items": rows}

    @app.get("/api/jobs/{job_id}/results/{result_id}/image", dependencies=[Depends(require)])
    def image(job_id: int, result_id: int, kind: str = "primary"):
        if kind not in {"primary", "backdrop"}:
            raise HTTPException(400, "图片类型不正确")
        report = store.result(job_id, result_id)
        key = "image_path" if kind == "primary" else "backdrop_path"
        path = Path(report.get(key) or "").resolve() if report else None
        if not path or not path.is_relative_to((paths.cache / "images").resolve()) or not path.is_file():
            raise HTTPException(404, "图片尚未下载或缓存已不存在")
        with Image.open(path) as picture:
            mime = Image.MIME.get(picture.format, "application/octet-stream")
        return FileResponse(path, media_type=mime)

    @app.get("/api/jobs/{job_id}/export", dependencies=[Depends(require)])
    def export(job_id: int):
        job_or_404(job_id)
        destination = paths.export(f"job-{job_id}.jsonl")
        offset = 0
        with destination.open("w", encoding="utf-8", newline="\n") as stream:
            while rows := store.results(job_id, offset, 500):
                for row in rows:
                    row.pop("result_id", None)
                    stream.write(json.dumps(row, ensure_ascii=False) + "\n")
                offset += len(rows)
        return FileResponse(destination, media_type="application/x-ndjson", filename=destination.name)

    web = Path(__file__).resolve().parent / "web"
    app.mount("/assets", StaticFiles(directory=web), name="assets")

    @app.get("/")
    def index():
        return FileResponse(web / "index.html")

    return app
