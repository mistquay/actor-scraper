"""CLI 与 Web 共用任务结果存储。"""

import json
import sys
import threading
import time


class ResultSink:
    def __init__(self, store, job_id, extra=None):
        self.store, self.job_id, self.extra = store, job_id, extra

    def write(self, line):
        report = json.loads(line)
        self.store.add_result(self.job_id, report)
        if self.extra:
            self.extra.write(line)
        return len(line)

    def flush(self):
        if self.extra:
            self.extra.flush()


def run_cli(args, paths, store, api_key):
    from .cli import execute, resolve_inputs
    from .config import Settings

    args.name, args.actor_mapping, args.graphis_ids = resolve_inputs(args)
    args.names_file = args.mapping = args.graphis_mapping = None
    request = {"names": args.name, "all": args.all, "limit": args.limit, "mode": "apply" if args.apply else "preview"}
    values = store.settings().model_dump(exclude={"api_key"})
    values.update({key: value for key, value in vars(args).items() if key in Settings.model_fields and key != "api_key"})
    values.update(proxy=args.proxy or "", graphis_mapping=args.graphis_ids)
    options = Settings(**values).public()
    store.prune(options["retention_days"])
    job_id = store.create_job(request, options)
    store.update_job(job_id, state="running")
    summary = {}
    stream = None
    try:
        if args.output:
            destination = paths.export(args.output)
            destination.parent.mkdir(parents=True, exist_ok=True)
            stream = destination.open("w", encoding="utf-8", newline="\n")
        elif args.stdout:
            stream = sys.stdout
        sink = ResultSink(store, job_id, stream)
        code = execute(
            args, sink, api_key=api_key, cancel_event=threading.Event(),
            on_scope=lambda targets: store.update_job(job_id, scope=targets, total=len(targets)),
            on_progress=lambda done, total, counts: store.update_job(job_id, done=done, summary={"statuses": counts}),
            on_summary=lambda result: summary.update(result),
        )
        store.update_job(job_id, state="cancelled" if code == 130 else ("failed" if code else "completed"), summary=summary, finished_at=time.time())
        print(f"任务 #{job_id} 已保存到 {paths.database}" + (f"；导出：{paths.export(args.output)}" if args.output else ""), file=sys.stderr)
        return code
    except BaseException:
        store.update_job(job_id, state="interrupted", message="命令行运行中断，可在 Web 任务记录中重试", finished_at=time.time())
        raise
    finally:
        if stream and stream is not sys.stdout:
            stream.close()
