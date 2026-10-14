import json
import threading
import time

import pytest
from fastapi.testclient import TestClient

from actor_scraper.config import Settings
from actor_scraper.jobs import Jobs
from actor_scraper.paths import DataPaths
from actor_scraper.store import Store
from actor_scraper.webapp import create_app


def report(name="测试演员", status="preview", **kwargs):
    return {"name": name, "person_id": name, "status": status, "cached": True, "seconds": 0.01, "metadata_written": False, "image_written": False, "backdrop_written": False, **kwargs}


def fast_runner(args, output, **callbacks):
    targets = callbacks.get("targets") or [{"Id": name, "Name": name} for name in args.name]
    callbacks["on_scope"](targets)
    for index, target in enumerate(targets, 1):
        output.write(json.dumps(report(target["Name"]), ensure_ascii=False) + "\n")
        callbacks["on_progress"](index, len(targets), {"preview": index})
    callbacks["on_summary"]({"total": len(targets), "statuses": {"preview": len(targets)}, "cache_hits": len(targets), "external_requests": 0, "fyms_requests": 0, "seconds": 0.01})
    return 0


def wait_job(store, job_id):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        job = store.get_job(job_id)
        if job["state"] not in {"queued", "running", "cancelling"}:
            return job
        threading.Event().wait(0.01)
    pytest.fail("任务没有在三秒内结束")


def login(client):
    response = client.post("/api/login", json={"password": "test-password"})
    assert response.status_code == 200
    assert "HttpOnly" in response.headers["set-cookie"]
    assert "SameSite=strict" in response.headers["set-cookie"]


def test_login_and_api_protection(tmp_path):
    with TestClient(create_app(tmp_path, password="test-password", runner=fast_runner)) as client:
        assert client.get("/").status_code == 200
        assert client.get("/health").status_code == 200
        assert client.get("/api/settings").status_code == 401
        assert client.get("/api/jobs").status_code == 401
        assert client.post("/api/login", json={"password": "wrong"}).status_code == 401
        login(client)
        assert client.get("/api/settings").status_code == 200
        assert client.put("/api/settings", json={"workers": 8}, headers={"Origin": "https://evil.example"}).status_code == 403
        assert client.post("/api/logout").status_code == 200
        assert client.get("/api/jobs").status_code == 401


def test_settings_persist_but_secret_is_never_returned(tmp_path):
    with TestClient(create_app(tmp_path, password="test-password")) as client:
        login(client)
        response = client.put("/api/settings", json={"server": "https://fyms.example/emby", "api_key": "private-api-key", "workers": 7})
        assert response.status_code == 200
        assert response.json()["api_key_configured"]
        assert "private-api-key" not in response.text and "api_key" not in response.json()
        assert client.put("/api/settings", json={"api_key": "", "workers": 5}).status_code == 200
        assert client.app.state.store.settings().api_key == "private-api-key"
        invalid = client.put("/api/settings", json={"server": "https://user:secret@fyms.example"})
        assert invalid.status_code == 422 and "user:secret" not in invalid.text
    with TestClient(create_app(tmp_path, password="test-password")) as restarted:
        login(restarted)
        saved = restarted.get("/api/settings").json()
        assert saved["workers"] == 5 and saved["server"] == "https://fyms.example/emby"
        assert saved["api_key_configured"]


def test_results_database_and_manual_export(tmp_path):
    with TestClient(create_app(tmp_path, password="test-password", runner=fast_runner)) as client:
        login(client)
        created = client.post("/api/jobs", json={"names": ["甲", "乙"], "mode": "preview"})
        assert created.status_code == 201
        job_id = created.json()["id"]
        done = wait_job(client.app.state.store, job_id)
        assert done["done"] == 2 and done["total"] == 2 and done["state"] == "completed"
        assert not list(tmp_path.rglob("*.jsonl"))
        detail = client.get(f"/api/jobs/{job_id}").json()
        assert "scope" not in detail, "轮询不应传输全量冻结演员列表"
        assert len(client.get(f"/api/jobs/{job_id}/results").json()["items"]) == 2
        exported = client.get(f"/api/jobs/{job_id}/export")
        assert exported.status_code == 200
        assert [json.loads(line)["name"] for line in exported.text.splitlines()] == ["甲", "乙"]
        assert (tmp_path / "exports" / f"job-{job_id}.jsonl").exists()
        assert client.get(f"/api/jobs/{job_id}/results?offset=-1").status_code == 400


def test_history_filters_cover_older_jobs_and_frozen_all_actor_scope(tmp_path):
    with TestClient(create_app(tmp_path, password="test-password")) as client:
        login(client)
        store = client.app.state.store
        ids = []
        for index in range(65):
            job_id = store.create_job(
                {"names": ["早期演员" if index == 0 else f"演员{index}"], "all": False, "mode": "preview", "limit": None},
                Settings().public(),
            )
            store.update_job(job_id, state="completed")
            ids.append(job_id)
        assert client.get("/api/jobs", params={"q": "早期演员"}).json()["items"][0]["id"] == ids[0]
        page = client.get("/api/jobs", params={"limit": 25, "offset": 50}).json()
        assert page["total"] == 65 and len(page["items"]) == 15
        assert "scope" not in page["items"][0]
        assert client.get("/api/jobs", params={"sort": "oldest", "limit": 1}).json()["items"][0]["id"] == ids[0]
        assert client.get("/api/jobs", params={"q": f"#{ids[0]}"}).json()["total"] == 1

        all_id = store.create_job({"names": [], "all": True, "mode": "apply", "limit": None}, Settings().public())
        store.update_job(all_id, state="failed", scope=[{"Name": "冻结演员", "Id": "person-1"}])
        filtered = client.get("/api/jobs", params={"q": "冻结", "mode": "apply", "state": "failed"}).json()
        assert filtered["total"] == 1 and filtered["items"][0]["id"] == all_id
        assert client.get("/api/jobs", params={"q": "冻结", "mode": "preview"}).json()["total"] == 0
        # 搜索符号是字面文本，不应作为 SQL LIKE 通配符或 SQL 片段。
        assert client.get("/api/jobs", params={"q": "%"}).json()["total"] == 0
        assert client.get("/api/jobs", params={"q": "' OR 1=1 --"}).json()["total"] == 0
        store.update_job(all_id, state="cancelling")
        assert client.get("/api/jobs", params={"state": "active"}).json()["total"] == 1
        assert client.get("/api/jobs", params={"state": "running"}).json()["total"] == 0


def test_history_date_boundaries_and_query_validation(tmp_path):
    with TestClient(create_app(tmp_path, password="test-password")) as client:
        login(client)
        store = client.app.state.store
        ids = []
        for timestamp in [300, 100, 200]:
            job_id = store.create_job({"names": ["甲"], "all": False, "mode": "preview", "limit": None}, Settings().public())
            store.update_job(job_id, state="completed")
            with store.connect() as db:
                db.execute("UPDATE jobs SET created_at=? WHERE id=?", (timestamp, job_id))
            ids.append(job_id)
        data = client.get("/api/jobs", params={"created_after": 100, "created_before": 300}).json()
        assert data["total"] == 2 and [job["id"] for job in data["items"]] == [ids[2], ids[1]]
        assert client.get("/api/jobs", params={"sort": "oldest", "limit": 1}).json()["items"][0]["id"] == ids[1]
        for params in [{"offset": -1}, {"limit": 0}, {"limit": 201}, {"created_after": 300, "created_before": 100}]:
            assert client.get("/api/jobs", params=params).status_code == 400
        for params in [{"state": "unknown"}, {"mode": "unknown"}, {"sort": "id; DROP TABLE jobs"}, {"created_before": "nan"}]:
            assert client.get("/api/jobs", params=params).status_code == 422


def test_result_filtering_paginates_matches_and_keeps_export_complete(tmp_path):
    with TestClient(create_app(tmp_path, password="test-password")) as client:
        login(client)
        store = client.app.state.store
        job_id = store.create_job({"names": [], "all": True, "mode": "preview", "limit": None}, Settings().public())
        for index in range(75):
            store.add_result(job_id, report(f"演员{index}", "preview"))
        for name, status in [("目标演员", "error"), ("另一位", "not_found"), ("第三位", "partial")]:
            store.add_result(job_id, report(name, status, image_path="cached-image.jpg"))
        url = f"/api/jobs/{job_id}/results"
        data = client.get(url, params={"status": "issues", "limit": 2, "offset": 2}).json()
        assert data["total"] == 3 and len(data["items"]) == 1
        assert data["items"][0]["name"] == "第三位"
        assert "image_path" not in data["items"][0] and "primary_preview" in data["items"][0]
        target = client.get(url, params={"q": "目标", "status": "error"}).json()
        assert target["total"] == 1 and target["items"][0]["name"] == "目标演员"
        assert client.get(url, params={"q": "目标", "status": "preview"}).json()["total"] == 0
        assert client.get(url, params={"status": "unknown"}).status_code == 422
        assert len(client.get(f"/api/jobs/{job_id}/export").text.splitlines()) == 78


def test_image_preview_cannot_read_config_or_arbitrary_files(tmp_path):
    app = create_app(tmp_path, password="test-password", runner=fast_runner)
    with TestClient(app) as client:
        login(client)
        job_id = app.state.store.create_job({"names": ["甲"], "all": False, "mode": "preview", "limit": None}, Settings().public())
        secret = tmp_path / "secret-file"
        secret.write_text("secret", encoding="utf-8")
        app.state.store.add_result(job_id, report("甲", image_path=str(secret)))
        row = app.state.store.results(job_id)[0]
        assert client.get(f"/api/jobs/{job_id}/results/{row['result_id']}/image").status_code == 404
        assert client.get(f"/api/jobs/{job_id}/results/{row['result_id']}/image?kind=unknown").status_code == 400


def test_running_job_freezes_config_and_can_be_cancelled(tmp_path):
    entered, captured = threading.Event(), {}

    def running(args, output, **callbacks):
        captured.update(server=args.server, api_key=callbacks["api_key"], selection=args.graphis_selection)
        entered.set()
        assert callbacks["cancel_event"].wait(3)
        return 130

    with TestClient(create_app(tmp_path, password="test-password", runner=running)) as client:
        login(client)
        client.put("/api/settings", json={"server": "https://first.example", "api_key": "first-secret"})
        created = client.post("/api/jobs", json={"names": ["甲"]})
        job_id = created.json()["id"]
        assert entered.wait(1)
        assert client.post("/api/jobs", json={"names": ["乙"]}).status_code == 409
        client.put("/api/settings", json={"server": "https://second.example", "api_key": "second-secret", "graphis_selection": "latest"})
        assert captured == {"server": "https://first.example", "api_key": "first-secret", "selection": "classic"}
        assert "first-secret" not in client.get(f"/api/jobs/{job_id}").text
        assert client.post(f"/api/jobs/{job_id}/cancel").status_code == 200
        assert wait_job(client.app.state.store, job_id)["state"] == "cancelled"
        assert client.post(f"/api/jobs/{job_id}/cancel").status_code == 409


def test_retry_uses_frozen_targets_and_policy(tmp_path):
    store = Store(DataPaths.create(tmp_path))
    settings = Settings(server="https://fyms.example", api_key="old-key")
    store.save_settings(settings)
    old_id = store.create_job({"names": [], "all": True, "limit": None, "mode": "apply"}, settings.public())
    scope = [{"Id": "a", "Name": "甲"}, {"Id": "b", "Name": "乙"}, {"Id": "c", "Name": "丙"}]
    store.update_job(old_id, state="failed", scope=scope, total=3, done=2)
    store.add_result(old_id, report("甲", "updated", person_id="a"))
    store.add_result(old_id, report("乙", "error", person_id="b"))
    store.save_settings(Settings(server="https://fyms.example", api_key="rotated-key", graphis_selection="latest"))
    captured = {}

    def retry_runner(args, output, **callbacks):
        captured.update(targets=callbacks["targets"], selection=args.graphis_selection, api_key=callbacks["api_key"])
        return fast_runner(args, output, **callbacks)

    jobs = Jobs(store, retry_runner)
    retried = jobs.retry(old_id)
    wait_job(store, retried["id"])
    assert captured == {"targets": scope[1:], "selection": "classic", "api_key": "rotated-key"}
    store.save_settings(Settings(server="https://different.example", api_key="key"))
    with pytest.raises(ValueError, match="地址已变化"):
        jobs.retry(old_id)


def test_restart_preserves_results_and_marks_interruption(tmp_path):
    store = Store(DataPaths.create(tmp_path))
    job_id = store.create_job({"names": ["甲"], "mode": "preview"}, Settings().public())
    store.update_job(job_id, state="running", scope=[{"Name": "甲", "Id": "a"}])
    store.add_result(job_id, report("甲"))
    with TestClient(create_app(tmp_path, password="test-password")) as client:
        login(client)
        assert client.get(f"/api/jobs/{job_id}").json()["state"] == "interrupted"
        assert len(client.get(f"/api/jobs/{job_id}/results").json()["items"]) == 1


def test_relative_cli_export_does_not_pollute_working_directory(tmp_path, monkeypatch):
    from actor_scraper import cli

    work = tmp_path / "work"
    work.mkdir()
    data = tmp_path / "app-data"
    monkeypatch.chdir(work)
    monkeypatch.delenv("FYMS_URL", raising=False)
    monkeypatch.delenv("FYMS_API_KEY", raising=False)

    def fake(args, output, **callbacks):
        assert args.proxy is None
        return fast_runner(args, output, **callbacks)

    monkeypatch.setattr(cli, "execute", fake)
    assert cli.main(["--data-dir", str(data), "--name", "甲", "--output", "preview.jsonl"]) == 0
    assert not (work / "preview.jsonl").exists()
    assert (data / "exports" / "preview.jsonl").exists()
    assert len(Store(DataPaths.create(data)).list_jobs()) == 1
    with pytest.raises(ValueError):
        DataPaths.create(data).export("../outside.jsonl")


def test_data_only_run_has_no_jsonl_by_default(tmp_path, monkeypatch):
    from actor_scraper import cli

    monkeypatch.delenv("FYMS_URL", raising=False)
    monkeypatch.delenv("FYMS_API_KEY", raising=False)
    monkeypatch.setattr(cli, "execute", fast_runner)
    assert cli.main(["--data-dir", str(tmp_path), "--name", "甲"]) == 0
    assert not list(tmp_path.rglob("*.jsonl"))
    assert Store(DataPaths.create(tmp_path)).list_jobs()[0]["done"] == 1


def test_invalid_job_scope_does_not_create_a_task(tmp_path):
    with TestClient(create_app(tmp_path, password="test-password", runner=fast_runner)) as client:
        login(client)
        assert client.post("/api/jobs", json={"names": [], "all": False}).status_code == 422
        assert client.post("/api/jobs", json={"names": ["甲"], "all": True}).status_code == 422
        assert client.post("/api/jobs", json={"names": ["甲"], "mode": "apply"}).status_code == 409
        assert not client.get("/api/jobs").json()["items"]


def test_cli_retry_preserves_file_names_and_source_mappings(tmp_path, monkeypatch):
    from actor_scraper import cli

    monkeypatch.delenv("FYMS_URL", raising=False)
    monkeypatch.delenv("FYMS_API_KEY", raising=False)
    names = tmp_path / "names.txt"
    names.write_text("甲\n乙\n", encoding="utf-8")
    mapping = tmp_path / "mapping.json"
    mapping.write_text('{"甲": "123"}', encoding="utf-8")
    graphis = tmp_path / "graphis.json"
    graphis.write_text('{"甲": "200110_yua-mikami4"}', encoding="utf-8")

    def failed(args, output, **callbacks):
        callbacks["on_scope"]([{"Id": "", "Name": name} for name in args.name])
        output.write(json.dumps(report("甲", "error", person_id="")) + "\n")
        return 1

    monkeypatch.setattr(cli, "execute", failed)
    data = tmp_path / "data"
    assert cli.main(["--data-dir", str(data), "--names-file", str(names), "--mapping", str(mapping), "--graphis-mapping", str(graphis), "--with-metadata"]) == 1
    store = Store(DataPaths.create(data))
    old = store.list_jobs()[0]
    assert old["request"]["names"] == ["甲", "乙"]
    names.unlink()
    mapping.unlink()
    graphis.unlink()

    def retried(args, output, **callbacks):
        assert args.actor_mapping == {"甲": "123"}
        assert args.graphis_ids == {"甲": "200110_yua-mikami4"}
        assert args.images_only is False
        return fast_runner(args, output, **callbacks)

    jobs = Jobs(store, runner=retried)
    retry = jobs.retry(old["id"])
    assert wait_job(store, retry["id"])["state"] == "completed"
    jobs.shutdown()
