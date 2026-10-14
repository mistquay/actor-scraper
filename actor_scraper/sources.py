"""AV-LEAGUE 档案及可选 Gfriends 头像索引。只取搜索页和档案页。"""

from __future__ import annotations

import json
import re
import threading
import time
import unicodedata
from pathlib import PurePosixPath
from urllib.parse import parse_qsl, quote, urlencode, urljoin, urlsplit

from opencc import OpenCC
from selectolax.lexbor import LexborHTMLParser

from .cache import Cache
from .http import Http, ScrapeError

BASE = "https://www.av-league.com/"
GF_BASE = "https://raw.githubusercontent.com/gfriends/gfriends/master/"
_traditional = OpenCC("s2t")
_japanese = OpenCC("t2jp")
_from_japanese = OpenCC("jp2t")
_simplified = OpenCC("t2s")


def normalize(name: str) -> str:
    name = unicodedata.normalize("NFKC", name)
    name = _japanese.convert(_traditional.convert(name))
    return re.sub(r"\s+", "", name).casefold()


def japanese_name(name: str) -> str:
    return _japanese.convert(_traditional.convert(unicodedata.normalize("NFKC", name))).strip()


def search_spellings(name: str) -> list[str]:
    return list(dict.fromkeys([name, japanese_name(name), _simplified.convert(_from_japanese.convert(name))]))


def parse_date(value: str) -> str:
    match = re.search(r"(\d{4})\D+(\d{1,2})\D+(\d{1,2})", value)
    if not match:
        return ""
    from datetime import date
    try:
        return date(*(int(n) for n in match.groups())).isoformat()
    except ValueError:
        return ""


def parse_profile(text: str, source_id: str) -> dict:
    doc = LexborHTMLParser(text)
    name_node = doc.css_first("#pan span")
    if not name_node or not name_node.text(strip=True):
        raise ScrapeError("AV-LEAGUE 档案结构异常，可能返回了拦截页")
    # 不读评论表格，避免把用户评论混入演员档案。
    fields = {}
    for row in doc.css("#contents table tr"):
        label, value = row.css_first("th"), row.css_first("td")
        if label and value:
            label, value = label.text(strip=True), value.text(strip=True)
            if label and value not in {"", "不明", "なし", "-", "―"}:
                fields.setdefault(label, value)
    if not fields:
        raise ScrapeError("AV-LEAGUE 档案缺少资料表，未缓存为空档案")
    aliases = []
    for node in doc.css("#j-prof span"):
        if match := re.search(r"別名\s*[:：]\s*(.+)", node.text(strip=True)):
            aliases.extend(a.strip() for a in re.split(r"[、,]", match[1]) if a.strip())
    image = doc.css_first("#contents .i-pic-box img") or doc.css_first('meta[property="og:image"]')
    image_url = ""
    if image:
        image_url = urljoin(BASE, image.attributes.get("data-layzr") or image.attributes.get("src") or image.attributes.get("content") or "")
    profile = {
        "name": name_node.text(strip=True), "source": "AVLEAGUE", "source_id": source_id,
        "source_url": f"{BASE}actress/{source_id}.html", "aliases": list(dict.fromkeys(aliases)),
        "birthday": parse_date(fields.get("生年月日", "")),
        "debut_date": parse_date(fields.get("デビュー", "")),
        "birthplace": fields.get("出身", ""), "blood_type": fields.get("血液型", ""),
        "image_url": image_url, "social": {}, "raw_fields": fields,
    }
    if height := re.search(r"\d+", fields.get("身長", "")):
        profile["height"] = int(height[0])
    sizes = fields.get("3サイズ", "")
    measurements = [re.search(rf"{label}\s*[:：]\s*(\d+)", sizes) for label in "BWH"]
    if all(measurements):
        profile["measurements"] = "/".join(m[1] for m in measurements)
    if cup := re.search(r"[（(]\s*([A-Z]+)\s*[）)]", sizes):
        profile["cup"] = cup[1]
    for label, key, domains in [
        ("Twitter", "Twitter", {"twitter.com", "x.com"}),
        ("インスタ", "Instagram", {"instagram.com", "www.instagram.com"}),
    ]:
        value = fields.get(label, "")
        if value.startswith("http"):
            parsed = urlsplit(value)
            value = parsed.path.strip("/").split("/")[0] if parsed.hostname in domains else ""
        value = value.lstrip("@")
        if re.fullmatch(r"[A-Za-z0-9_.]+", value):
            profile["social"][key] = value
    return profile


class AVLeague:
    def __init__(self, http: Http, cache: Cache, *, ttl_days=30, miss_hours=24, refresh=False):
        self.http, self.cache = http, cache
        self.ttl, self.miss_ttl = ttl_days * 86400, miss_hours * 3600
        self.refresh = refresh
        self.lock = threading.Lock()
        self.name_locks: dict[str, threading.Lock] = {}

    def get(self, name: str, source_id="", *, confirmed_id=False, aliases=()):
        key = normalize(name)
        lookup_names = list(dict.fromkeys(japanese_name(n) for n in [*aliases, name] if n))
        with self.lock:
            name_lock = self.name_locks.setdefault(key, threading.Lock())
        with name_lock:
            cached = self.cache.lookup(key)
            if cached and not self.refresh and (not source_id or source_id == cached["source_id"]):
                ttl = self.ttl if cached["status"] == "found" else self.miss_ttl
                same_lookup = cached["status"] == "found" or not aliases or cached["data"].get("lookup_names") == lookup_names
                if same_lookup and time.time() - cached["checked_at"] < ttl:
                    return cached["data"], True
            if not source_id and cached and cached["status"] == "found":
                source_id = cached["source_id"]
            if cached and cached["status"] == "found" and source_id == cached["source_id"]:
                # 复用已通过精确匹配/人工映射确认的稳定来源 ID，允许后续艺名变化。
                confirmed_id = True
            if not source_id:
                url = BASE + "search/search.php"
                hits = {}
                for query in lookup_names:
                    response = self.http.request("GET", url, params={"k": query})
                    doc = LexborHTMLParser(response.text)
                    if not doc.css_first("#contents"):
                        raise ScrapeError("AV-LEAGUE 搜索结构异常，可能返回了拦截页")
                    for node in doc.css(".l-name a[href]"):
                        match = re.search(r"/actress/(\d+)\.html", node.attributes["href"])
                        if match and normalize(node.text(strip=True)) == normalize(query):
                            hits[match[1]] = node.text(strip=True)
                    if hits:
                        break
                    if "検索結果" not in doc.text(strip=True):
                        raise ScrapeError("AV-LEAGUE 空页面无法确认是搜索结果，未缓存为未命中")
                if not hits:
                    result = {"status": "not_found", "query": name, "lookup_names": lookup_names}
                    self.cache.save(key, "", "not_found", result)
                    return result, False
                if len(hits) > 1:
                    raise ScrapeError(f"同名演员对应多个 ID: {', '.join(hits)}，请使用 --mapping")
                source_id = next(iter(hits))
            if not re.fullmatch(r"\d+", source_id):
                raise ScrapeError("AV-LEAGUE ID 必须是数字")
            url = f"{BASE}actress/{source_id}.html"
            profile = parse_profile(self.http.request("GET", url).text, source_id)
            identities = {normalize(n) for n in [profile["name"], *profile["aliases"]]}
            if not confirmed_id and not identities.intersection(normalize(n) for n in [name, *aliases]):
                raise ScrapeError(f"ID {source_id} 的姓名与请求不匹配，请核对 --mapping")
            profile["status"] = "found"
            self.cache.save(key, source_id, "found", profile)
            return profile, False


class Gfriends:
    def __init__(self, http: Http, cache: Cache):
        path = cache.directory / "gfriends.json"
        data = None
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(data, dict) or not isinstance(data.get("Content"), dict):
                    data = None
            except (ValueError, OSError):
                pass
        if data is None or time.time() - path.stat().st_mtime > 86400:
            try:
                fresh = http.json("GET", GF_BASE + "Filetree.json")
                if not isinstance(fresh, dict) or not isinstance(fresh.get("Content"), dict):
                    raise ScrapeError("Gfriends 索引缺少 Content")
                temp = path.with_suffix(".tmp")
                temp.write_text(json.dumps(fresh, ensure_ascii=False), encoding="utf-8")
                temp.replace(path)
                data = fresh
            except ScrapeError:
                if data is None:
                    raise
        self.images = {}
        self.graphis_images = {}
        for folder, entries in data["Content"].items():
            for alias, filename in entries.items():
                key = normalize(PurePosixPath(alias).stem)
                # Filetree 的遍历顺序是仓库提供的选择次序；只下载所选头像。
                # Filetree 的文件名带 ?t=更新时间，它是查询参数，不是文件名的一部分。
                filename, _, query = filename.partition("?")
                url = GF_BASE + quote(f"Content/{folder}/{filename}", safe="/")
                if query:
                    url += "?" + urlencode(parse_qsl(query))
                prefix = re.match(r"(\d+)-", folder)
                rank = 0 if "graphis" in folder.lower() else (1 if "hand" in folder.lower() else (10 + int(prefix[1]) if prefix else 99))
                if key not in self.images or rank < self.images[key][0]:
                    self.images[key] = (rank, url)
                if "graphis" in folder.lower():
                    self.graphis_images[key] = url

    def image(self, profile: dict, *, graphis_only=False) -> str:
        matches = []
        for name in [profile["name"], *profile["aliases"]]:
            key = normalize(name)
            if graphis_only:
                if url := self.graphis_images.get(key):
                    return url
            elif match := self.images.get(key):
                matches.append(match)
        if matches:
            return min(matches, key=lambda item: item[0])[1]
        return "" if graphis_only else profile["image_url"]
