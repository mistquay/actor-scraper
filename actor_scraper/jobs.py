"""单个后台任务、取消与冻结范围的失败重试。"""

import argparse
import logging
import threading
import time

from .cli import execute
from .config import JobRequest, Settings
from .history import ResultSink
from .http import RunCancelled


def options(settings, request, paths):
    values = settings.model_dump(exclude={"api_key", "retention_days", "graphis_mapping"})
    values.update(
        name=request.names, all=request.all, limit=request.limit, apply=request.mode == "apply",
        cache_dir=paths.cache, names_file=None, mapping=None, graphis_mapping=None,
        graphis_ids=settings.graphis_mapping, proxy=settings.proxy or None,
    )
    return argparse.Namespace(**values)


class Jobs:
    def __init__(self, store, runner=execute):
        self.store, self.runner = store, runner
        self.lock = threading.Lock()
        self.events = {}
        self.threads = {}
        self.logger = logging.getLogger("actor_scraper.jobs")

    def start(self, request: JobRequest, *, settings=None, targets=None):
        settings = settings or self.store.settings()
        if (request.all or request.mode == "apply") and not settings.server:
            raise ValueError("请先配置 FYMS 地址")
        if settings.server and not settings.api_key:
            raise ValueError("请先配置 FYMS API 密钥")
        self.store.prune(settings.retention_days)
        job_id = self.store.create_job(request.model_dump(), settings.public())
        event = threading.Event()
        thread = threading.Thread(target=self._run, args=(job_id, request, settings, event, targets), daemon=True, name=f"actor-job-{job_id}")
        with self.lock:
            self.events[job_id] = event
            self.threads[job_id] = thread
        thread.start()
        return self.store.get_job(job_id, include_scope=False)

    def _run(self, job_id, request, settings, event, targets):
        summary = {}
        try:
            self.store.update_job(job_id, state="running", message="正在读取并冻结本次演员范围")
            code = self.runner(
                options(settings, request, self.store.paths), ResultSink(self.store, job_id),
                api_key=settings.api_key, cancel_event=event, targets=targets,
                on_scope=lambda people: self.store.update_job(job_id, scope=people, total=len(people)),
                on_progress=lambda done, total, counts: self.store.update_job(job_id, done=done, summary={"statuses": counts}),
                on_summary=lambda result: summary.update(result),
                on_log=lambda message: self._log(job_id, message),
            )
            state = "cancelled" if code == 130 or event.is_set() else ("failed" if code else "completed")
            self.store.update_job(job_id, state=state, summary=summary, finished_at=time.time())
        except RunCancelled:
            self.store.update_job(job_id, state="cancelled", message="已停止", finished_at=time.time())
        except Exception as exc:
            # 配置/请求异常只输出简短消息，避免 traceback 泄漏配置快照。
            message = str(exc) if isinstance(exc, ValueError) else f"任务异常：{type(exc).__name__}"
            self._log(job_id, message)
            self.store.update_job(job_id, state="failed", message=message, finished_at=time.time())
        finally:
            with self.lock:
                self.events.pop(job_id, None)
                self.threads.pop(job_id, None)

    def _log(self, job_id, message):
        self.logger.info("任务 #%s %s", job_id, message)
        self.store.update_job(job_id, message=message)

    def cancel(self, job_id):
        with self.lock:
            event = self.events.get(job_id)
            if event is None:
                raise ValueError("该任务未由当前 Web 进程运行，请在原命令行停止")
            self.store.request_cancel(job_id)
            event.set()

    def retry(self, job_id):
        old = self.store.get_job(job_id)
        if not old or old["state"] in {"queued", "running", "cancelling"}:
            raise ValueError("只能重试已结束的任务")
        current = self.store.settings()
        if current.server != old["options"]["server"]:
            raise ValueError("FYMS 地址已变化，请重新选择演员创建任务")
        saved = {k: v for k, v in old["options"].items() if k in Settings.model_fields and k != "api_key"}
        settings = Settings(**saved, api_key=current.api_key)
        if not old["scope"]:
            return self.start(JobRequest.model_validate(old["request"]), settings=settings)
        completed = set()
        offset = 0
        while rows := self.store.results(job_id, offset, 500):
            for row in rows:
                if row["status"] in {"preview", "updated", "unchanged", "not_found"}:
                    completed.add(row.get("person_id") or row["name"])
            offset += len(rows)
        targets = [target for target in old["scope"] if (target.get("Id") or target["Name"]) not in completed]
        if not targets:
            raise ValueError("该任务没有失败或未完成的演员")
        request = JobRequest(names=[t["Name"] for t in targets], mode=old["request"]["mode"])
        return self.start(request, settings=settings, targets=targets)

    def shutdown(self):
        with self.lock:
            for event in self.events.values():
                event.set()
            threads = list(self.threads.values())
        deadline = time.monotonic() + 5
        for thread in threads:
            thread.join(max(0, deadline - time.monotonic()))
