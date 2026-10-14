"""独立工具的身份匹配、写入边界和增量缓存验证；与 hls2strm 测试隔离。"""

import io
import json

import httpx
import pytest
from PIL import Image

from actor_scraper import cli
from actor_scraper.cache import Cache
from actor_scraper.fyms import Fyms, metadata_patch
from actor_scraper.http import Http, ScrapeError
from actor_scraper.sources import AVLeague, Gfriends, normalize, parse_profile


PROFILE = """<div id="pan"><span>水野優香</span></div><div id="contents">
<div class="i-pic-box"><img src="https://pics.example/portrait.jpg"></div>
<table><tr><th>3サイズ</th><td>B:85（E） / W:57 / H:86</td></tr>
<tr><th>身長</th><td>158cm</td></tr><tr><th>生年月日</th><td>1978年1月30日（48歳）</td></tr>
<tr><th>血液型</th><td>不明</td></tr><tr><th>出身</th><td>不明</td></tr>
<tr><th>デビュー</th><td>2018年3月29日</td></tr><tr><th>Twitter</th><td>mizun0yuka</td></tr>
<tr><th>インスタ</th><td>なし</td></tr><tr><th></th><td>评论和广告不属于演员资料</td></tr></table></div>"""
SEARCH = '<div id="contents">検索結果（1人）<div class="l-name"><a href="/actress/8553.html">水野優香</a></div></div>'
PERSON = {"Id": "actor-1", "Name": "水野优香", "Type": "Person", "ProviderIds": {"Other": "keep"}, "ImageTags": {}, "Tags": ["手工标签"]}


def test_profile_fields_and_unknown_values():
    profile = parse_profile(PROFILE, "8553")
    assert profile["birthday"] == "1978-01-30"
    assert profile["debut_date"] == "2018-03-29"
    assert profile["measurements"] == "85/57/86"
    assert profile["height"] == 158 and profile["cup"] == "E"
    assert profile["social"] == {"Twitter": "mizun0yuka"}
    assert profile["birthplace"] == "" and profile["blood_type"] == ""
    assert "评论和广告" not in json.dumps(profile, ensure_ascii=False)
    assert normalize("水野优香") == normalize("水野優香")
    assert normalize("三上悠亚") == normalize("三上悠亜")


def test_gfriends_timestamp_is_query_not_filename(tmp_path):
    data = {"Content": {"7-Madonna": {"水野優香.jpg": "水野優香.jpg?t=1613619905"}}}
    (tmp_path / "gfriends.json").write_text(json.dumps(data), encoding="utf-8")
    http = Http(rps=0, transport=httpx.MockTransport(lambda r: pytest.fail("有效索引应直接命中本地缓存")))
    cache = Cache(tmp_path)
    try:
        url = Gfriends(http, cache).image(parse_profile(PROFILE, "8553"))
        assert url.endswith(".jpg?t=1613619905")
        assert "%3F" not in url and "%3D" not in url
        assert http.requests == 0
    finally:
        cache.close()
        http.close()


def test_precise_identity_and_cache_shortcut(tmp_path):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, text=SEARCH if "search.php" in request.url.path else PROFILE)

    http = Http(rps=0, transport=httpx.MockTransport(handler))
    cache = Cache(tmp_path)
    scraper = AVLeague(http, cache)
    try:
        first, hit = scraper.get("水野优香")
        assert not hit and first["source_id"] == "8553"
        assert len(calls) == 2
        second, hit = scraper.get("水野優香")
        assert hit and second == first and len(calls) == 2
        scraper.refresh = True
        scraper.get("水野優香")
        assert len(calls) == 3 and calls[-1].url.path == "/actress/8553.html"
    finally:
        cache.close()
        http.close()


def test_ambiguous_actor_is_not_first_result(tmp_path):
    ambiguous = SEARCH.replace("（1人）", "（2人）").replace('</div></div>', '</div><div class="l-name"><a href="/actress/9000.html">水野优香</a></div></div>')
    http = Http(rps=0, transport=httpx.MockTransport(lambda r: httpx.Response(200, text=ambiguous)))
    cache = Cache(tmp_path)
    try:
        with pytest.raises(ScrapeError, match="多个 ID"):
            AVLeague(http, cache).get("水野優香")
        assert http.requests == 1 and cache.lookup(normalize("水野優香")) is None
    finally:
        cache.close()
        http.close()


@pytest.mark.parametrize("response", [httpx.Response(403), httpx.Response(429), httpx.Response(200, text='<div id="contents">Challenge</div>')])
def test_failed_source_is_not_cached_as_missing(tmp_path, response):
    http = Http(rps=0, transport=httpx.MockTransport(lambda r: response))
    cache = Cache(tmp_path)
    try:
        with pytest.raises(ScrapeError):
            AVLeague(http, cache).get("水野優香")
        assert cache.lookup(normalize("水野優香")) is None
        assert http.requests == 1
    finally:
        cache.close()
        http.close()


def test_missing_actor_cache_is_distinct_from_failure(tmp_path):
    http = Http(rps=0, transport=httpx.MockTransport(lambda r: httpx.Response(200, text='<div id="contents">検索結果（0人）</div>')))
    cache = Cache(tmp_path)
    try:
        scraper = AVLeague(http, cache)
        assert scraper.get("不存在的演员")[0]["status"] == "not_found"
        assert scraper.get("不存在的演员")[1] is True
        assert http.requests == 1
    finally:
        cache.close()
        http.close()


def test_merge_preserves_existing_data_and_unknown_fields():
    profile = parse_profile(PROFILE, "8553")
    current = {**PERSON, "Overview": "人工简介", "ProductionLocations": ["日本"], "Tags": ["身高: 160", "生涯: 2018~", "手工标签"], "ProviderIds": {"Other": "keep", "Twitter": "manual"}}
    patch = metadata_patch(current, profile)
    assert "Overview" not in patch and "ProductionLocations" not in patch
    assert "身高: 160" in patch["Tags"] and "身高: 158" not in patch["Tags"]
    assert "生涯: 2018~" in patch["Tags"] and "手工标签" in patch["Tags"]
    assert patch["ProviderIds"] == {"Other": "keep", "Twitter": "manual", "AVLEAGUE": "8553"}
    updated = metadata_patch(current, profile, overwrite=True)
    assert "身高: 158" in updated["Tags"]
    assert updated["ProviderIds"]["Other"] == "keep"
    assert "ProductionLocations" not in updated
    assert metadata_patch({**current, "LockData": True}, profile, True) == {}
    assert "Tags" not in metadata_patch({**current, "LockedFields": ["Tags"]}, profile, True)


def test_server_pagination_and_readback_verification():
    state = {**PERSON}

    def handler(request):
        assert request.headers["X-Emby-Token"] == "secret"
        if request.url.path == "/emby/Persons":
            offset = int(request.url.params["StartIndex"])
            page = [{"Id": f"p{i}", "Name": str(i)} for i in range(offset, min(offset + 2, 3))]
            return httpx.Response(200, json={"Items": page, "TotalRecordCount": 3})
        if request.method == "POST":
            state.update(json.loads(request.content))
            return httpx.Response(204)
        return httpx.Response(200, json=state)

    http = Http(rps=0, token="secret", transport=httpx.MockTransport(handler))
    try:
        fyms = Fyms("https://fyms.example/emby", http)
        assert len(fyms.persons()) == 3
        fyms.apply("actor-1", {"Tags": ["手工标签", "罩杯: E"]})
        assert state["Tags"] == ["手工标签", "罩杯: E"]
    finally:
        http.close()


def test_success_status_without_persistence_is_detected():
    http = Http(rps=0, token="secret", transport=httpx.MockTransport(lambda r: httpx.Response(204) if r.method == "POST" else httpx.Response(200, json=PERSON)))
    try:
        with pytest.raises(ScrapeError, match="读回不一致"):
            Fyms("https://fyms.example", http).apply("actor-1", {"PremiereDate": "1978-01-30"})
    finally:
        http.close()


def test_api_token_does_not_follow_login_redirect():
    http = Http(rps=0, token="secret", transport=httpx.MockTransport(lambda r: httpx.Response(307, headers={"Location": "https://other.example/login"})))
    try:
        with pytest.raises(ScrapeError, match="307"):
            http.request("GET", "https://fyms.example/Persons")
        assert http.requests == 1
    finally:
        http.close()


@pytest.mark.parametrize("mode", ["preview", "partial", "image"])
def test_cli_preview_and_image_results(tmp_path, monkeypatch, mode):
    state = json.loads(json.dumps(PERSON))
    calls = []
    monkeypatch.setenv("FYMS_API_KEY", "secret")
    picture = io.BytesIO()
    Image.new("RGB", (16, 24), "blue").save(picture, format="JPEG")

    def handler(request):
        calls.append(request)
        if request.url.host == "www.av-league.com":
            assert "X-Emby-Token" not in request.headers
            return httpx.Response(200, text=SEARCH if "search.php" in request.url.path else PROFILE)
        if request.url.host == "pics.example":
            if mode == "image":
                return httpx.Response(200, content=picture.getvalue(), headers={"Content-Type": "image/jpeg"})
            return httpx.Response(200, text="HTML instead of image")
        assert request.headers["X-Emby-Token"] == "secret"
        if request.url.path == "/Persons":
            return httpx.Response(200, json={"Items": [state], "TotalRecordCount": 1})
        if request.method == "POST":
            if "Images/Primary" in request.url.path:
                assert request.content == picture.getvalue()
                assert request.headers["Content-Type"] == "image/jpeg"
                state["ImageTags"] = {"Primary": "new-tag"}
                return httpx.Response(200, json={"ok": True})
            state.update(json.loads(request.content))
            return httpx.Response(204)
        return httpx.Response(200, json=state)

    original_http = Http

    def make_http(**kwargs):
        kwargs.update(rps=0, transport=httpx.MockTransport(handler))
        return original_http(**kwargs)

    monkeypatch.setattr(cli, "Http", make_http)
    argv = ["--server", "https://fyms.example", "--name", "水野优香", "--avatar-source", "profile", "--no-backdrop", "--cache-dir", str(tmp_path)]
    if mode != "preview":
        argv.append("--apply")
    output = io.StringIO()
    result = cli.execute(cli.parser().parse_args(argv), output)
    report = json.loads(output.getvalue())
    if mode == "partial":
        assert result == 1 and report["status"] == "partial"
        assert report["metadata_written"] and not report["image_written"]
        assert state["PremiereDate"] == "1978-01-30"
        assert state["ProviderIds"]["Other"] == "keep"
        assert not any("Images/Primary" in request.url.path for request in calls)
    elif mode == "image":
        assert result == 0 and report["status"] == "updated"
        assert report["metadata_written"] and report["image_written"]
        assert state["ProviderIds"]["Twitter"] == "mizun0yuka"
        assert list((tmp_path / "images").iterdir())
    else:
        assert result == 0 and report["status"] == "preview"
        assert not any(request.method == "POST" for request in calls)
    assert "secret" not in output.getvalue()
