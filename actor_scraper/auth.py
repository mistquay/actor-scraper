"""单用户管理登录；密码不进入配置接口或任务快照。"""

import hashlib
import hmac
import os
import secrets
import threading
import time

from fastapi import HTTPException, Request


class Auth:
    def __init__(self, paths, password=None):
        value = password or os.environ.get("ACTOR_ADMIN_PASSWORD")
        if not value:
            path = paths.root / "admin-password"
            if not path.exists():
                path.write_text(secrets.token_urlsafe(18) + "\n", encoding="utf-8")
                path.chmod(0o600)
            value = path.read_text(encoding="utf-8").strip()
        self.password_hash = hashlib.sha256(value.encode()).digest()
        self.sessions = {}
        self.failures = {}
        self.lock = threading.Lock()

    def login(self, password, peer):
        now = time.monotonic()
        with self.lock:
            failures = [at for at in self.failures.get(peer, []) if now - at < 60]
            if len(failures) >= 10:
                raise HTTPException(429, "登录尝试过多，请一分钟后重试")
            if not hmac.compare_digest(hashlib.sha256(password.encode()).digest(), self.password_hash):
                self.failures[peer] = [*failures, now]
                raise HTTPException(401, "密码不正确")
            self.failures.pop(peer, None)
            self.sessions = {token: expires for token, expires in self.sessions.items() if expires > now}
            token = secrets.token_urlsafe(32)
            self.sessions[token] = now + 86400
            return token

    def require(self, request: Request):
        token = request.cookies.get("actor_session", "")
        with self.lock:
            valid = self.sessions.get(token, 0) > time.monotonic()
        if not valid:
            raise HTTPException(401, "请先登录")

    def logout(self, token):
        with self.lock:
            self.sessions.pop(token, None)
