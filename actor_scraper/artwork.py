"""图片来源编排、选图报告及两个独立图片槽位的应用。"""

from __future__ import annotations

import threading
from urllib.parse import unquote, urlsplit

from .fyms import load_image
from .graphis import Graphis
from .http import RunCancelled, ScrapeError
from .sources import Gfriends


def known_aliases(person: dict) -> list[str]:
    """MDCNG 的 minnano-av ID 携带已确认演员名；只作为同一人物的查询别名。"""
    names = []
    for key, value in (person.get("ProviderIds") or {}).items():
        if key.lower() != "minnano-av":
            continue
        name = unquote(urlsplit(str(value)).query).strip()
        if name and not any(s in name for s in ["=", "&", "/", "http"]):
            names.append(name)
    return names


class ArtworkSources:
    def __init__(self, http, cache, args):
        self.http, self.cache, self.args = http, cache, args
        self.graphis = Graphis(http, cache, ttl_days=args.ttl_days, miss_hours=args.miss_hours, refresh=args.refresh)
        self.gf = None
        self.gf_error = ""
        self.lock = threading.Lock()

    def gfriends(self):
        with self.lock:
            if self.gf is None and not self.gf_error:
                try:
                    self.gf = Gfriends(self.http, self.cache)
                except RunCancelled:
                    raise
                except ScrapeError as exc:
                    self.gf_error = str(exc)
            if self.gf_error:
                raise ScrapeError(self.gf_error)
            return self.gf

    def plan(self, target: dict, profile: dict, model_id=""):
        plan = {"primary": None, "backdrop": None, "warnings": [], "graphis_cached": False}
        if self.args.avatar_source == "none":
            return plan
        fallback = {"url": profile.get("image_url", ""), "source": "AVLEAGUE", "source_url": profile.get("source_url", "")}
        # 已经确认的原艺名优先，解决橋本有菜→新ありな→橋本ありなの链路。
        names = list(dict.fromkeys([*profile.get("aliases", []), profile["name"], *known_aliases(target), target["Name"]]))
        if self.args.avatar_source in {"auto", "graphis"} or not self.args.no_backdrop:
            try:
                result, cached = self.graphis.get(names, selection=self.args.graphis_selection, model_id=model_id)
                plan["graphis_cached"] = cached
                plan["graphis"] = result
                if result["status"] == "found":
                    base = {"source": "GRAPHIS", "source_url": result["source_url"], "model_id": result["model_id"]}
                    if self.args.avatar_source in {"auto", "graphis"}:
                        plan["primary"] = {**base, "url": result["primary_url"]}
                    if not self.args.no_backdrop:
                        plan["backdrop"] = {**base, "url": result["backdrop_url"]}
            except RunCancelled:
                raise
            except ScrapeError as exc:
                plan["warnings"].append(str(exc))
        if plan["primary"] is None and self.args.avatar_source in {"auto", "gfriends"}:
            try:
                gf_profile = {**profile, "aliases": names, "image_url": ""}
                url = self.gfriends().image(gf_profile)
                if url:
                    source = "Gfriends/GRAPHIS" if self.gfriends().image(gf_profile, graphis_only=True) == url else "Gfriends"
                    plan["primary"] = {"url": url, "source": source, "source_url": "https://github.com/gfriends/gfriends"}
            except RunCancelled:
                raise
            except ScrapeError as exc:
                plan["warnings"].append(str(exc))
        if plan["primary"] is None and fallback["url"] and self.args.avatar_source in {"auto", "profile", "gfriends"}:
            plan["primary"] = fallback
        return plan

    def apply(self, plan, current, fyms, person_id, report):
        errors = []
        locked = current.get("LockData") or "images" in {s.lower() for s in current.get("LockedFields", [])}
        for key, image_type, exists, replace in [
            ("primary", "Primary", bool(current.get("ImageTags", {}).get("Primary")), self.args.replace_avatar),
            ("backdrop", "Backdrop", bool(current.get("BackdropImageTags")), self.args.replace_backdrop),
        ]:
            candidate = plan[key]
            report_key = "image" if key == "primary" else "backdrop"
            needed = bool(candidate) and not locked and (replace or not exists)
            report[report_key + "_planned"] = needed
            report[report_key + "_url"] = candidate["url"] if candidate else ""
            if not candidate or not (self.args.download_images or (self.args.apply and needed)):
                continue
            try:
                image = load_image(candidate["url"], self.http, self.cache, refresh=self.args.refresh, referer=candidate.get("source_url", ""))
                report[report_key + "_path"] = str(self.cache.image_path(candidate["url"]).resolve())
                if self.args.apply and needed:
                    fyms.apply(person_id, {}, image, image_type=image_type)
                    report[report_key + "_written"] = True
            except RunCancelled:
                raise
            except ScrapeError as exc:
                errors.append(f"{image_type}: {exc}")
        return errors
