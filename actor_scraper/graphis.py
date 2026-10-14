"""GRAPHIS 公开演员封面：同一组写真提供 prof.jpg 主图和 model.jpg 背景。"""

from __future__ import annotations

import re
import threading
import time
from urllib.parse import parse_qs, urljoin, urlsplit

from selectolax.lexbor import LexborHTMLParser

from .cache import Cache
from .http import Http, ScrapeError
from .sources import japanese_name, normalize

BASE = "https://graphis.ne.jp/"
MODEL_ID = re.compile(r"\d{6}_[A-Za-z0-9_-]+\Z")


def public_images(model_id: str, matched_name: str) -> dict:
    root = f"https://data.graphis.ne.jp/free/model/{model_id}/"
    return {
        "status": "found", "source": "GRAPHIS", "model_id": model_id, "matched_name": matched_name,
        "source_url": BASE + "monthly/model.php?ID=" + model_id,
        "primary_url": root + "prof.jpg", "backdrop_url": root + "model.jpg",
    }


def parse_models(text: str, expected_name: str):
    doc = LexborHTMLParser(text)
    if not doc.css_first('form input[name="K"]'):
        raise ScrapeError("GRAPHIS 返回的不是公开模型搜索页，未缓存为空结果")
    hits = {}
    for link in doc.css('a[href*="model.php?"]'):
        image = link.css_first("img[alt]")
        if not image:
            continue
        name = re.sub(r"[（(]DVD[）)]$", "", image.attributes["alt"]).strip()
        if normalize(name) != normalize(expected_name):
            continue
        parsed = urlsplit(urljoin(BASE, link.attributes["href"]))
        if parsed.hostname not in {"graphis.ne.jp", "www.graphis.ne.jp"}:
            continue
        model_id = parse_qs(parsed.query).get("ID", [""])[0]
        if MODEL_ID.fullmatch(model_id):
            hits[model_id] = name
    return hits


class Graphis:
    def __init__(self, http: Http, cache: Cache, *, ttl_days=30, miss_hours=24, refresh=False):
        self.http, self.cache = http, cache
        self.ttl, self.miss_ttl, self.refresh = ttl_days * 86400, miss_hours * 3600, refresh
        self.lock = threading.Lock()
        self.name_locks = {}

    def get(self, names: list[str], *, selection="classic", model_id=""):
        names = list(dict.fromkeys(japanese_name(name) for name in names if name))
        key = normalize(names[0]) + "|" + selection + "|" + model_id
        with self.lock:
            name_lock = self.name_locks.setdefault(key, threading.Lock())
        with name_lock:
            cached = self.cache.artwork(key)
            if cached and not self.refresh:
                data, checked = cached
                ttl = self.ttl if data["status"] == "found" else self.miss_ttl
                if time.time() - checked < ttl and (data["status"] == "found" or data.get("lookup_names") == names):
                    return data, True
            if model_id:
                if not MODEL_ID.fullmatch(model_id):
                    raise ScrapeError("GRAPHIS 映射 ID 格式应为 200110_yua-mikami4")
                response = self.http.request("GET", BASE + "monthly/model.php", params={"ID": model_id})
                doc = LexborHTMLParser(response.text)
                # 人工映射仍验证实际页面有对应的公开封面，拦截页不能成为成功记录。
                expected = f"/free/model/{model_id}/model.jpg"
                if not any(expected in image.attributes.get("src", "") for image in doc.css("img")):
                    raise ScrapeError("GRAPHIS 指定写真页未提供公开背景封面")
                result = public_images(model_id, names[0])
            else:
                result = None
                for name in names:
                    response = self.http.request("GET", BASE + "monthly/", params={"K": name})
                    hits = parse_models(response.text, name)
                    if not hits:
                        continue
                    # 经典优先采用最早公开的普通写真封面，排在 LIMITED / 合集之前。
                    standard = [mid for mid in hits if not any(s in mid for s in ["_ltd", "_sp", "_daily"])]
                    chosen = min(standard or list(hits)) if selection == "classic" else max(hits)
                    result = public_images(chosen, hits[chosen])
                    break
                if result is None:
                    result = {"status": "not_found", "source": "GRAPHIS", "lookup_names": names}
            self.cache.save_artwork(key, result)
            return result, False
