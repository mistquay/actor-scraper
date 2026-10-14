"""GRAPHIS 源、艺名关联和主图/背景独立回写验证。"""

import io
import json

import httpx
import pytest
from PIL import Image

from actor_scraper import cli
from actor_scraper.artwork import known_aliases
from actor_scraper.cache import Cache
from actor_scraper.graphis import Graphis, parse_models
from actor_scraper.http import Http, ScrapeError
from actor_scraper.sources import AVLeague, Gfriends, normalize


SEARCH = '''<form><input name="K"></form>
<a href="/monthly/model.php?ID=200805_yua-mikami4_ltd2"><img alt="三上悠亜"></a>
<a href="/monthly/model.php?ID=200110_yua-mikami4"><img alt="三上悠亜"></a>
<a href="/monthly/model.php?ID=260101_another"><img alt="別の女優"></a>'''
PROFILE = '<div id="pan"><span>三上悠亜</span></div><div id="contents"><table><tr><th>生年月日</th><td>1993年8月16日</td></tr></table></div>'


def test_graphis_selects_same_shoot_for_both_images_and_caches(tmp_path):
    http = Http(rps=0, transport=httpx.MockTransport(lambda r: httpx.Response(200, text=SEARCH)))
    cache = Cache(tmp_path)
    try:
        source = Graphis(http, cache)
        pair, hit = source.get(["三上悠亚"])
        assert not hit and pair["model_id"] == "200110_yua-mikami4"
        assert pair["primary_url"].endswith("200110_yua-mikami4/prof.jpg")
        assert pair["backdrop_url"].endswith("200110_yua-mikami4/model.jpg")
        assert source.get(["三上悠亜"])[1] and http.requests == 1
        latest, _ = source.get(["三上悠亜"], selection="latest")
        assert latest["model_id"] == "200805_yua-mikami4_ltd2"
        assert "another" not in latest["backdrop_url"]
    finally:
        cache.close()
        http.close()


def test_graphis_challenge_is_not_missing(tmp_path):
    http = Http(rps=0, transport=httpx.MockTransport(lambda r: httpx.Response(200, text="Challenge")))
    cache = Cache(tmp_path)
    try:
        with pytest.raises(ScrapeError, match="搜索页"):
            Graphis(http, cache).get(["三上悠亜"])
        assert cache.db.execute("SELECT count(*) FROM artwork").fetchone()[0] == 0
        assert parse_models(SEARCH, "違う女優") == {}
    finally:
        cache.close()
        http.close()


def test_mdc_alias_resolves_new_and_old_stage_names(tmp_path):
    target = {"ProviderIds": {"minnano-av": "actress698091.html?新ありな"}}
    assert known_aliases(target) == ["新ありな"]
    profile_html = PROFILE.replace("三上悠亜", "新ありな") + '<h1 id="j-prof"><span>別名 : 橋本ありな</span></h1>'
    requests = []

    def handler(request):
        requests.append(request)
        if "search.php" in request.url.path:
            assert request.url.params["k"] == "新ありな"
            return httpx.Response(200, text='<div id="contents">検索結果<div class="l-name"><a href="/actress/35232.html">新ありな</a></div></div>')
        return httpx.Response(200, text=profile_html)

    http = Http(rps=0, transport=httpx.MockTransport(handler))
    cache = Cache(tmp_path)
    try:
        profile, _ = AVLeague(http, cache).get("桥本有菜", aliases=known_aliases(target))
        assert profile["name"] == "新ありな" and profile["aliases"] == ["橋本ありな"]
        assert cache.lookup(normalize("桥本有菜"))["source_id"] == "35232"
        assert len(requests) == 2
    finally:
        cache.close()
        http.close()


def test_gfriends_prefers_graphis_across_names_and_folder_order(tmp_path):
    data = {"Content": {
        "z-DMM": {"新ありな.jpg": "新ありな.jpg"},
        "8-GRAPHIS": {"橋本ありな.jpg": "橋本ありな.jpg?t=123"},
        "0-Hand-Storage": {"橋本ありな.jpg": "橋本ありな.jpg"},
        "7-Moodyz": {"橋本ありな.jpg": "橋本ありな.jpg"},
    }}
    (tmp_path / "gfriends.json").write_text(json.dumps(data), encoding="utf-8")
    http = Http(rps=0, transport=httpx.MockTransport(lambda r: pytest.fail("应命中本地索引")))
    cache = Cache(tmp_path)
    try:
        gf = Gfriends(http, cache)
        profile = {"name": "新ありな", "aliases": ["橋本ありな"], "image_url": "fallback"}
        assert "/8-GRAPHIS/" in gf.image(profile)
        assert gf.image(profile) == gf.image(profile, graphis_only=True)
    finally:
        cache.close()
        http.close()


def test_cli_missing_data_is_not_successful_preview(tmp_path, monkeypatch):
    http = Http(rps=0, transport=httpx.MockTransport(lambda r: httpx.Response(200, text='<div id="contents">検索結果（0人）</div>')))
    monkeypatch.setattr(cli, "Http", lambda **kwargs: http)
    monkeypatch.delenv("FYMS_URL", raising=False)
    args = cli.parser().parse_args(["--name", "未找到的演员", "--avatar-source", "none", "--cache-dir", str(tmp_path)])
    output = io.StringIO()
    assert cli.execute(args, output) == 0
    assert json.loads(output.getvalue())["status"] == "not_found"


@pytest.mark.parametrize("mode", ["preview", "both", "primary_fails", "backdrop_only", "wrong_backdrop_slot"])
def test_image_slots_and_read_only_preview(tmp_path, monkeypatch, mode):
    monkeypatch.setenv("FYMS_API_KEY", "secret")
    person = {"Id": "actor-1", "Name": "三上悠亚", "Type": "Person", "Tags": ["罩杯: H", "生涯: 2015~"], "ImageTags": {}, "BackdropImageTags": [], "ProviderIds": {}}
    if mode == "backdrop_only":
        person["ImageTags"] = {"Primary": "keep-avatar"}
    writes = []
    body = io.BytesIO()
    Image.new("RGB", (24, 16), "blue").save(body, "JPEG")

    def handler(request):
        if request.url.host != "fyms.example":
            assert "X-Emby-Token" not in request.headers
        if request.url.host == "www.av-league.com":
            if "search.php" in request.url.path:
                return httpx.Response(200, text='<div id="contents">検索結果<div class="l-name"><a href="/actress/8301.html">三上悠亜</a></div></div>')
            return httpx.Response(200, text=PROFILE)
        if request.url.host == "graphis.ne.jp":
            return httpx.Response(200, text=SEARCH)
        if request.url.host == "data.graphis.ne.jp":
            if mode == "primary_fails" and request.url.path.endswith("prof.jpg"):
                return httpx.Response(404)
            return httpx.Response(200, content=body.getvalue())
        assert request.headers["X-Emby-Token"] == "secret"
        if request.url.path == "/Persons":
            assert request.url.params["SearchTerm"]
            assert request.url.params["UserId"] == "test-user"
            return httpx.Response(200, json={"Items": [person], "TotalRecordCount": 1})
        if request.method == "POST":
            writes.append(request.url.path)
            assert "/Images/" in request.url.path, "images-only 不应回写文字"
            assert request.content == body.getvalue()
            if request.url.path.endswith("Backdrop") and mode != "wrong_backdrop_slot":
                person["BackdropImageTags"] = ["new-background"]
            else:
                person["ImageTags"] = {"Primary": "new-avatar"}
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(200, json=person)

    original_http = Http

    def make_http(**kwargs):
        kwargs.update(rps=0, transport=httpx.MockTransport(handler))
        return original_http(**kwargs)

    monkeypatch.setattr(cli, "Http", make_http)
    argv = ["--name", "三上悠亚", "--server", "https://fyms.example", "--user-id", "test-user", "--cache-dir", str(tmp_path), "--images-only"]
    argv += ["--download-images"] if mode == "preview" else ["--apply"]
    output = io.StringIO()
    code = cli.execute(cli.parser().parse_args(argv), output)
    report = json.loads(output.getvalue())
    assert not report["metadata_written"] and report["patch"] == {}
    assert person["Tags"] == ["罩杯: H", "生涯: 2015~"]
    if mode == "preview":
        assert code == 0 and not writes and report["status"] == "preview"
        assert report["image_path"] and report["backdrop_path"]
    elif mode == "both":
        assert code == 0 and report["image_written"] and report["backdrop_written"]
        assert writes == ["/Items/actor-1/Images/Primary", "/Items/actor-1/Images/Backdrop"]
    elif mode == "backdrop_only":
        assert code == 0 and not report["image_written"] and report["backdrop_written"]
        assert person["ImageTags"]["Primary"] == "keep-avatar"
        assert writes == ["/Items/actor-1/Images/Backdrop"]
    elif mode == "primary_fails":
        assert code == 1 and report["status"] == "partial" and report["backdrop_written"]
        assert not report["image_written"]
    else:
        assert code == 1 and report["status"] == "partial" and not report["backdrop_written"]
        assert "Backdrop" in report["error"]
    assert "secret" not in output.getvalue()
