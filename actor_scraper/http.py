"""连接复用、按站限速及有界 GET 重试。"""

from __future__ import annotations

import threading
import time
from urllib.parse import urljoin, urlsplit

import httpx


class ScrapeError(Exception):
    pass


class RunCancelled(ScrapeError):
    pass


class Http:
    def __init__(self, *, timeout=10.0, rps=2.0, retries=1, proxy=None, token=None, transport=None, cancel_event=None):
        self.client = httpx.Client(
            timeout=timeout, proxy=proxy, transport=transport, follow_redirects=False,
            headers={"User-Agent": "Mozilla/5.0 FastActorScraper/1.0"},
            limits=httpx.Limits(max_connections=32, max_keepalive_connections=16),
        )
        self.token = token
        self.rps = rps
        self.retries = retries
        self.lock = threading.Lock()
        self.next_request: dict[str, float] = {}
        self.cooldowns: dict[str, float] = {}
        self.requests = 0
        self.cancel_event = cancel_event

    def close(self):
        self.client.close()

    def _wait(self, host):
        if self.cancel_event and self.cancel_event.is_set():
            raise RunCancelled("任务已停止")
        with self.lock:
            now = time.monotonic()
            if self.cooldowns.get(host, 0) > now:
                raise ScrapeError(f"{host}: HTTP 429 冷却中，请稍后重跑或降低 --rps")
            at = max(now, self.next_request.get(host, now))
            self.next_request[host] = at + (1 / self.rps if self.rps else 0)
        delay = at - time.monotonic()
        if delay > 0:
            if self.cancel_event:
                if self.cancel_event.wait(delay):
                    raise RunCancelled("任务已停止")
            else:
                time.sleep(delay)
        with self.lock:
            if self.cooldowns.get(host, 0) > time.monotonic():
                raise ScrapeError(f"{host}: HTTP 429 冷却中")

    def request(self, method, url, *, _redirects=0, **kwargs):
        host = urlsplit(url).hostname or ""
        attempts = self.retries + 1 if method == "GET" else 1
        if self.token:
            kwargs["headers"] = {**kwargs.get("headers", {}), "X-Emby-Token": self.token}
        for attempt in range(attempts):
            self._wait(host)
            with self.lock:
                self.requests += 1
            try:
                response = self.client.request(method, url, **kwargs)
            except httpx.HTTPError as exc:
                if attempt + 1 < attempts:
                    continue
                # 不打印异常原文，避免代理认证信息进入日志。
                raise ScrapeError(f"{host}: {type(exc).__name__}") from exc
            # 图片/CDN 可正常重定向；携带 FYMS 密钥的请求不跟随登录页或外站。
            if method == "GET" and 300 <= response.status_code < 400 and not self.token:
                destination = urljoin(url, response.headers.get("Location", ""))
                if _redirects >= 3 or not response.headers.get("Location") or urlsplit(destination).scheme not in {"http", "https"}:
                    raise ScrapeError(f"{host}: 无效或过多重定向")
                return self.request(method, destination, _redirects=_redirects + 1, **kwargs)
            if response.status_code == 429:
                try:
                    seconds = max(30.0, float(response.headers.get("Retry-After", 30)))
                except ValueError:
                    seconds = 30.0
                with self.lock:
                    self.cooldowns[host] = time.monotonic() + seconds
            if response.status_code >= 500 and attempt + 1 < attempts:
                continue
            if not 200 <= response.status_code < 300:
                raise ScrapeError(f"{host}: HTTP {response.status_code} ({method})")
            return response
        raise ScrapeError(f"{host}: 请求失败")

    def json(self, method, url, **kwargs):
        response = self.request(method, url, **kwargs)
        try:
            return response.json()
        except ValueError as exc:
            raise ScrapeError(f"{urlsplit(url).hostname}: 返回内容不是 JSON") from exc
