"""命令行、范围选择和并发执行。"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path

from .cache import Cache
from .artwork import ArtworkSources, known_aliases
from .fyms import Fyms, metadata_patch
from .graphis import MODEL_ID
from .http import Http, RunCancelled, ScrapeError
from .paths import DataPaths
from .sources import AVLeague, normalize, search_spellings


def parser():
    p = argparse.ArgumentParser(description="快速获取 JAV 演员档案；默认预览，--apply 回写 FYMS。")
    scope = p.add_argument_group("演员范围")
    scope.add_argument("--name", action="append", default=[], help="演员名，可重复；支持简体/繁体/日文汉字转换")
    scope.add_argument("--names-file", type=Path, help="UTF-8 文本，每行一位演员，# 开头为注释")
    scope.add_argument("--all", action="store_true", help="处理 FYMS 当前全部演员，必须指定服务器")
    scope.add_argument("--limit", type=int, help="最多处理多少位演员，适合试跑")
    scope.add_argument("--mapping", type=Path, help='人工确认的姓名到 AV-LEAGUE ID 的 JSON，如 {"水野优香":"8553"}')
    scope.add_argument("--graphis-mapping", type=Path, help='指定写真版本的 JSON，如 {"三上悠亚":"200110_yua-mikami4"}')
    server = p.add_argument_group("FYMS")
    server.add_argument("--server", default=os.environ.get("FYMS_URL", ""), help="FYMS 地址，默认 FYMS_URL")
    server.add_argument("--user-id", default=os.environ.get("FYMS_USER_ID", ""), help="用户 ID，默认 FYMS_USER_ID")
    server.add_argument("--apply", action="store_true", help="实际回写演员元数据并补头像")
    server.add_argument("--overwrite", action="store_true", help="覆盖本次来源有值的资料字段；默认仅补缺")
    server.add_argument("--replace-avatar", action="store_true", help="更新已有头像；默认只补缺失头像")
    server.add_argument("--replace-backdrop", action="store_true", help="更新已有背景；默认只补缺失背景")
    server.add_argument("--images-only", action="store_true", help="仅回写图片，保留 MDCNG 已有文字资料；仍可获取档案确认别名")
    server.add_argument("--with-metadata", dest="images_only", action="store_false", help="补全文字资料与图片，覆盖资料仍需 --overwrite")
    speed = p.add_argument_group("性能与缓存")
    speed.add_argument("--data-dir", type=Path, help="数据目录，默认项目 data 或 ACTOR_DATA_DIR")
    speed.add_argument("--workers", type=int, default=4, help="并发演员数，默认 4")
    speed.add_argument("--rps", type=float, default=2, help="每个外部主机每秒请求上限，默认 2")
    speed.add_argument("--timeout", type=float, default=10, help="单次网络操作超时秒数，默认 10")
    speed.add_argument("--retries", type=int, default=1, help="GET 失败额外重试次数，默认 1；403/429 不重试")
    speed.add_argument("--cache-dir", type=Path, default=Path(os.environ.get("ACTOR_DATA_DIR") or Path(__file__).resolve().parent.parent / "data") / "cache")
    speed.add_argument("--ttl-days", type=float, default=30, help="档案缓存天数，默认 30")
    speed.add_argument("--miss-hours", type=float, default=24, help="未命中缓存小时数，默认 24")
    speed.add_argument("--refresh", action="store_true", help="忽略档案缓存；已知 ID 直接请求详情页")
    speed.add_argument("--proxy", help="外部资料/头像代理，如 http://127.0.0.1:7890；不作用于 FYMS")
    speed.add_argument("--avatar-source", choices=["auto", "graphis", "profile", "gfriends", "none"], default="auto", help="默认 GRAPHIS 主图+背景；头像回退 Gfriends 优选和档案页。none 关闭所有图片")
    speed.add_argument("--graphis-selection", choices=["classic", "latest"], default="classic", help="默认经典普通写真；latest 选择最新公开写真")
    speed.add_argument("--no-backdrop", action="store_true", help="只处理头像，不获取背景")
    speed.add_argument("--download-images", action="store_true", help="预览时也下载所选图片到本地缓存，便于检查；不会因此写 FYMS")
    p.add_argument("--output", type=Path, help="手动导出 JSONL；相对路径自动放入 data/exports")
    p.add_argument("--stdout", action="store_true", help="将完整 JSONL 结果输出到终端；默认只存任务数据库")
    return p


def select_people(names: list[str], people: list[dict]):
    selected = []
    for name in names:
        exact = [p for p in people if p["Name"] == name]
        matches = exact or [p for p in people if normalize(p["Name"]) == normalize(name)]
        if len(matches) != 1:
            raise ScrapeError(f"FYMS 中 {name} {'未找到' if not matches else '匹配多个演员'}，请使用精确姓名")
        selected.append(matches[0])
    return list({p["Id"]: p for p in selected}.values())


def resolve_inputs(args):
    names = list(args.name)
    if args.names_file:
        names += [line.strip() for line in args.names_file.read_text(encoding="utf-8-sig").splitlines() if line.strip() and not line.lstrip().startswith("#")]
    mappings = getattr(args, "actor_mapping", {})
    graphis_mappings = getattr(args, "graphis_ids", {})
    if args.mapping:
        mappings = json.loads(args.mapping.read_text(encoding="utf-8-sig"))
        if not isinstance(mappings, dict) or any(not str(value).isdigit() for value in mappings.values()):
            raise ScrapeError("--mapping 应为姓名到数字 ID 的 JSON 对象")
    if args.graphis_mapping:
        graphis_mappings = json.loads(args.graphis_mapping.read_text(encoding="utf-8-sig"))
        if not isinstance(graphis_mappings, dict) or any(not isinstance(value, str) or not MODEL_ID.fullmatch(value) for value in graphis_mappings.values()):
            raise ScrapeError("--graphis-mapping 应为姓名到写真 ID 的 JSON 对象")
    return list(dict.fromkeys(names)), {normalize(name): str(value) for name, value in mappings.items()}, {normalize(name): value for name, value in graphis_mappings.items()}


def execute(args, output, *, api_key=None, cancel_event=None, on_scope=None, on_progress=None, on_summary=None, on_log=None, targets=None):
    def log(message):
        if on_log:
            on_log(message)
        else:
            print(message, file=sys.stderr)

    started = time.perf_counter()
    cache = Cache(args.cache_dir)
    external = Http(timeout=args.timeout, rps=args.rps, retries=args.retries, proxy=args.proxy, cancel_event=cancel_event)
    server_http = Http(timeout=args.timeout, rps=0, retries=args.retries, token=api_key if api_key is not None else os.environ.get("FYMS_API_KEY"), cancel_event=cancel_event)
    try:
        fyms = Fyms(args.server, server_http, args.user_id) if args.server else None
        names, mappings, graphis_mappings = resolve_inputs(args)
        if targets is not None:
            targets = list(targets)
        elif fyms:
            if args.all:
                people = fyms.persons()
            else:
                # 少量指定演员直接搜索，避免先拉取整个线上演员库。
                queries = list(dict.fromkeys(query for name in names for query in search_spellings(name)))
                with ThreadPoolExecutor(max_workers=args.workers) as pool:
                    pages = list(pool.map(fyms.persons, queries))
                people = list({p["Id"]: p for page in pages for p in page}.values())
            targets = people if args.all else select_people(names, people)
        else:
            targets = [{"Name": name, "Id": ""} for name in names]
        if args.limit is not None:
            targets = targets[:args.limit]
        if on_scope:
            on_scope(targets)
        log(f"范围 {len(targets)} 位；{args.workers} 并发，外部 {args.rps:g} 请求/秒/主机；{'回写' if args.apply else '预览'}")
        artwork = ArtworkSources(external, cache, args)
        scraper = AVLeague(external, cache, ttl_days=args.ttl_days, miss_hours=args.miss_hours, refresh=args.refresh)

        def process(target):
            stamp = time.perf_counter()
            report = {"name": target["Name"], "person_id": target["Id"], "status": "error", "cached": False, "metadata_written": False, "image_written": False, "backdrop_written": False}
            try:
                # 列表已带 ProviderIds；先取资料，未命中时省掉 FYMS 详情请求。
                current = target if fyms else {}
                mapping = mappings.get(normalize(target["Name"]))
                source_id = mapping or next((str(v) for k, v in (current.get("ProviderIds") or {}).items() if k.lower() == "avleague"), "")
                profile, cached = scraper.get(target["Name"], source_id, confirmed_id=bool(mapping), aliases=known_aliases(target))
                report.update(profile=profile, cached=cached)
                if profile["status"] == "not_found":
                    # 档案源未命中时，仍可按已知姓名从图片源补主图/背景。
                    profile = {**profile, "name": target["Name"], "aliases": known_aliases(target), "image_url": ""}
                # 资料获取后再读取现有字段，合并时保留用户最新的手工编辑。
                if fyms:
                    current = fyms.detail(target["Id"])
                    if current.get("Name") != target["Name"]:
                        raise ScrapeError("FYMS 演员姓名已变化，本次跳过，请重新运行")
                patch = metadata_patch(current, profile, args.overwrite) if fyms and profile["status"] == "found" and not args.images_only else {}
                plan = artwork.plan(target, profile, graphis_mappings.get(normalize(target["Name"]), ""))
                report.update(patch=patch, artwork=plan)
                if args.apply and patch:
                    fyms.apply(target["Id"], patch)
                    report["metadata_written"] = True
                # 两种图分别下载/上传，某张失败不会阻止另一张；文字回写也单独计数。
                errors = artwork.apply(plan, current, fyms, target["Id"], report)
                if errors:
                    report["status"] = "partial" if any(report[k] for k in ["metadata_written", "image_written", "backdrop_written"]) else "error"
                    report["error"] = "; ".join(errors)
                elif not (plan["primary"] or plan["backdrop"]) and (profile["status"] == "not_found" or (args.images_only and args.avatar_source != "none")):
                    report["status"] = "not_found"
                elif not args.apply:
                    report["status"] = "preview"
                else:
                    report["status"] = "updated" if any(report[k] for k in ["metadata_written", "image_written", "backdrop_written"]) else "unchanged"
            except RunCancelled:
                report["status"] = "cancelled"
            except ScrapeError as exc:
                report["error"] = str(exc)
            except Exception as exc:
                report["error"] = f"未预期错误: {type(exc).__name__}"
            finally:
                report["seconds"] = round(time.perf_counter() - stamp, 3)
            return report

        counts = Counter()
        cache_hits = 0
        pool = ThreadPoolExecutor(max_workers=args.workers)
        try:
            remaining = iter(targets)
            pending = {pool.submit(process, target) for target in list(targets[:args.workers])}
            for _ in range(min(args.workers, len(targets))):
                next(remaining)
            completed = 0
            while pending:
                finished, _ = wait(pending, return_when=FIRST_COMPLETED)
                for future in finished:
                    pending.remove(future)
                    report = future.result()
                    completed += 1
                    counts[report["status"]] += 1
                    cache_hits += int(report["cached"])
                    output.write(json.dumps(report, ensure_ascii=False) + "\n")
                    output.flush()
                    log(f"[{completed}/{len(targets)}] {report['name']}: {report['status']} ({report['seconds']:.3f}s){' 缓存' if report['cached'] else ''}")
                    if on_progress:
                        on_progress(completed, len(targets), dict(counts))
                    if not cancel_event or not cancel_event.is_set():
                        target = next(remaining, None)
                        if target is not None:
                            pending.add(pool.submit(process, target))
        except KeyboardInterrupt:
            if cancel_event:
                cancel_event.set()
            raise
        finally:
            # 中断时取消未开始的任务，已完成资料仍留在缓存。
            pool.shutdown(wait=True, cancel_futures=True)
        summary = {"total": len(targets), "statuses": dict(counts), "cache_hits": cache_hits, "external_requests": external.requests, "fyms_requests": server_http.requests, "seconds": round(time.perf_counter() - started, 3)}
        log("统计 " + json.dumps(summary, ensure_ascii=False))
        if on_summary:
            on_summary(summary)
        if cancel_event and cancel_event.is_set():
            return 130
        return 1 if counts["error"] or counts["partial"] else 0
    finally:
        external.close()
        server_http.close()
        cache.close()


def main(argv=None):
    from .history import run_cli
    from .store import Store

    p = parser()
    preliminary, _ = p.parse_known_args(argv)
    paths = DataPaths.create(preliminary.data_dir)
    store = Store(paths)
    settings = store.settings()
    defaults = settings.model_dump(exclude={"api_key", "retention_days", "actor_mapping", "graphis_mapping"})
    defaults.update(server=os.environ.get("FYMS_URL", settings.server), user_id=os.environ.get("FYMS_USER_ID", settings.user_id), cache_dir=paths.cache)
    defaults["proxy"] = settings.proxy or None
    p.set_defaults(**defaults)
    args = p.parse_args(argv)
    args.actor_mapping = settings.actor_mapping
    args.graphis_ids = settings.graphis_mapping
    api_key = os.environ.get("FYMS_API_KEY", settings.api_key)
    if not args.all and not args.name and not args.names_file:
        p.error("指定 --name、--names-file 或 --all 来确定范围")
    if args.all and (args.name or args.names_file):
        p.error("--all 与按姓名选择不能同时使用")
    if (args.all or args.apply) and not args.server:
        p.error("--all / --apply 必须提供 --server 或 FYMS_URL")
    if args.server and not api_key:
        p.error("连接 FYMS 需要设置环境变量 FYMS_API_KEY")
    if not 1 <= args.workers <= 32 or args.rps <= 0 or args.timeout <= 0 or args.retries < 0:
        p.error("workers 应为 1~32，rps/timeout 应为正数，retries 应非负")
    if args.ttl_days < 0 or args.miss_hours < 0 or (args.limit is not None and args.limit < 1):
        p.error("缓存期限应非负，limit 应为正整数")
    try:
        return run_cli(args, paths, store, api_key)
    except (ScrapeError, OSError, ValueError) as exc:
        # JSON/文件格式错误只给简短原因，不输出包含配置的完整堆栈。
        print(f"失败：{exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("已中断；已获取档案保留在缓存，下次可继续。", file=sys.stderr)
        return 130
