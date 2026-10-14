"""通过 FYMS 的 Emby 兼容接口做局部更新，并读回确认。"""

from __future__ import annotations

import io
import tempfile
from datetime import date
from urllib.parse import urlsplit

from PIL import Image

from .cache import Cache
from .http import Http, ScrapeError


def tag_key(tag: str) -> str:
    return tag.replace("：", ":").split(":", 1)[0].strip()


def metadata_patch(current: dict, profile: dict, overwrite=False) -> dict:
    """默认补缺；覆盖模式也只更新已获取到的字段及同名资料标签。"""
    if current.get("LockData"):
        return {}
    locked = {s.lower() for s in current.get("LockedFields", [])}
    patch = {}

    def set_field(field, value):
        if value and field.lower() not in locked:
            old = current.get(field)
            if (overwrite or not old) and old != value:
                patch[field] = value

    birthday = profile.get("birthday", "")
    # Emby 返回时间戳，FYMS 管理端可能返回仅日期；避免相同生日重复回写。
    existing_birthday = str(current.get("PremiereDate") or "")[:10]
    if birthday != existing_birthday:
        set_field("PremiereDate", birthday)
    chosen_birthday = patch.get("PremiereDate") or existing_birthday
    if chosen_birthday:
        set_field("ProductionYear", int(chosen_birthday[:4]))
    set_field("ProductionLocations", [profile["birthplace"]] if profile.get("birthplace") else [])
    if profile.get("aliases"):
        set_field("Overview", "别名：" + "、".join(profile["aliases"]))

    labels = {"cup": "罩杯", "height": "身高", "measurements": "三围", "blood_type": "血型", "debut_date": "出道日期"}
    incoming = {label: str(profile[key]) for key, label in labels.items() if profile.get(key)}
    if chosen_birthday:
        incoming["出生日期"] = chosen_birthday
        try:
            born, today = date.fromisoformat(chosen_birthday), date.today()
            age = today.year - born.year - ((today.month, today.day) < (born.month, born.day))
            if age >= 0:
                incoming["年龄"] = str(age)
        except ValueError:
            pass
    old_tags = current.get("Tags") or []
    tags = list(old_tags)
    for label, value in incoming.items():
        matching = [i for i, tag in enumerate(tags) if tag_key(tag) == label]
        # 年龄是生日的派生值，每次运行按保留/更新后的生日计算。
        if not matching:
            tags.append(f"{label}: {value}")
        elif overwrite or label == "年龄":
            tags[matching[0]] = f"{label}: {value}"
    if tags != old_tags and "tags" not in locked:
        patch["Tags"] = tags

    old_ids = current.get("ProviderIds") or {}
    ids = dict(old_ids)
    for key, value in {"AVLEAGUE": profile["source_id"], **profile.get("social", {})}.items():
        existing_key = next((k for k in ids if k.lower() == key.lower()), key)
        if overwrite or not ids.get(existing_key):
            ids[existing_key] = value
    if ids != old_ids and "providerids" not in locked:
        patch["ProviderIds"] = ids
    return patch


class Fyms:
    def __init__(self, base_url: str, http: Http, user_id=""):
        parsed = urlsplit(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.query or parsed.fragment:
            raise ScrapeError("--server 应为 http(s) 地址，可带 /emby 或反代路径；认证请用 FYMS_API_KEY")
        self.base = base_url.rstrip("/")
        self.http = http
        self.user_params = {"UserId": user_id} if user_id else {}

    def persons(self, search=""):
        people, seen = [], set()
        offset = 0
        while True:
            params = {"StartIndex": offset, "Limit": 500, **self.user_params}
            if search:
                params["SearchTerm"] = search
            data = self.http.json("GET", self.base + "/Persons", params=params)
            if not isinstance(data, dict) or not isinstance(data.get("Items"), list):
                raise ScrapeError("FYMS /Persons 返回格式异常")
            page = data["Items"]
            if not page:
                break
            fresh = [p for p in page if p.get("Id") and p["Id"] not in seen]
            if not fresh:
                raise ScrapeError("FYMS /Persons 分页没有前进")
            for person in fresh:
                if not person.get("Name"):
                    raise ScrapeError("FYMS 演员条目缺少 Name")
                seen.add(person["Id"])
                people.append(person)
            offset += len(page)
            if offset >= data.get("TotalRecordCount", offset + 1):
                break
        return people

    def detail(self, person_id: str):
        data = self.http.json("GET", self.base + f"/Items/{person_id}", params=self.user_params)
        if not isinstance(data, dict) or data.get("Type") != "Person" or data.get("Id") != person_id:
            raise ScrapeError("FYMS 返回的条目不是请求的演员")
        return data

    def apply(self, person_id: str, patch: dict, image=None, *, image_type="Primary"):
        if image_type not in {"Primary", "Backdrop"}:
            raise ScrapeError("仅支持 Primary / Backdrop 图片类型")
        if patch:
            self.http.request("POST", self.base + f"/Items/{person_id}", json=patch)
        if image:
            data, mime = image
            self.http.request("POST", self.base + f"/Items/{person_id}/Images/{image_type}", content=data, headers={"Content-Type": mime})
        if not patch and not image:
            return
        current = self.detail(person_id)
        for key, value in patch.items():
            actual = current.get(key)
            if key == "PremiereDate":
                actual = str(actual or "")[:10]
            if actual != value:
                raise ScrapeError(f"FYMS 回写后 {key} 读回不一致，更新可能已部分完成")
        has_image = current.get("ImageTags", {}).get("Primary") if image_type == "Primary" else current.get("BackdropImageTags")
        if image and not has_image:
            raise ScrapeError(f"FYMS 上传后未返回 {image_type} 图片标记，更新可能已部分完成")


def load_image(url: str, http: Http, cache: Cache, *, refresh=False, referer=""):
    if urlsplit(url).scheme not in {"http", "https"}:
        raise ScrapeError("头像来源没有有效的 HTTP 地址")
    path = cache.image_path(url)
    if path.exists() and not refresh:
        content = path.read_bytes()
    else:
        response = http.request("GET", url, headers={"Referer": referer} if referer else {})
        content = response.content
    try:
        with Image.open(io.BytesIO(content)) as image:
            mime = Image.MIME.get(image.format)
            image.verify()
        if mime not in {"image/jpeg", "image/png", "image/webp"}:
            raise ScrapeError("头像格式必须是 JPEG / PNG / WebP")
    except (OSError, ValueError) as exc:
        if path.exists():
            path.unlink()
        raise ScrapeError("头像返回了无效图片，未上传") from exc
    if not path.exists() or refresh:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
            temporary = stream.name
            stream.write(content)
        from pathlib import Path
        try:
            Path(temporary).replace(path)
        finally:
            Path(temporary).unlink(missing_ok=True)
    return content, mime
