from fastapi.testclient import TestClient

from inbox.database import Database
from inbox import api


def client_for(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "DB", Database(tmp_path / "app.db"))
    original_export = api.export_current
    monkeypatch.setattr(
        api,
        "export_current",
        lambda database, output_dir: original_export(database, tmp_path / "exports"),
    )
    return TestClient(api.app)


def test_api_starts_on_post_update_state_and_serves_detail(tmp_path, monkeypatch):
    with client_for(tmp_path, monkeypatch) as client:
        overview = client.get("/api/overview")
        assert overview.status_code == 200
        data = overview.json()
        assert data["selected_records"] == 44
        assert data["metrics"]["usable_text_coverage"]["usable"] == 18

        c02 = next(row for row in data["records"] if row["record_ref"] == "C02")
        assert c02["title"] == "Call C02"

        m07_row = next(row for row in data["records"] if row["record_ref"] == "M07")
        detail = client.get(f"/api/record/meeting/{m07_row['source_id']}")
        assert detail.status_code == 200
        assert detail.json()["analysis_freshness"] == "current"
        assert len(detail.json()["transcript_payload"]["turns"]) == 27

        alerts = client.get("/api/notifications").json()
        changed_m07 = next(item for item in alerts["items"] if item["record_ref"] == "M07")
        assert alerts["unread_count"] == 2
        assert changed_m07["risk_classification"]["category"] in {
            "potential_risk", "client_attention", "no_risk_found", "not_enough_text"
        }
        assert changed_m07["applied_revision"] == 2
        assert client.post("/api/notifications/read").status_code == 200
        assert client.get("/api/notifications").json()["unread_count"] == 0


def test_manual_routes_and_demo_reload_survive_api_update(tmp_path, monkeypatch):
    with client_for(tmp_path, monkeypatch) as client:
        overview = client.get("/api/overview").json()
        m07 = next(row for row in overview["records"] if row["record_ref"] == "M07")
        route = client.post(
            f"/api/record/meeting/{m07['source_id']}/routes",
            json={"routes": ["Vendor/supplier note"], "note": "Reviewed", "who": "Manager"},
        )
        assert route.status_code == 200

        replay = client.post("/api/demo")
        assert replay.status_code == 200
        assert replay.json()["record_count"] == 44
        detail = client.get(f"/api/record/meeting/{m07['source_id']}").json()
        assert detail["manual_override_flag"] is True
        assert detail["effective_routes"] == ["Vendor/supplier note"]

        reset = client.post(f"/api/record/meeting/{m07['source_id']}/routes/reset")
        assert reset.status_code == 200
        detail = client.get(f"/api/record/meeting/{m07['source_id']}").json()
        assert detail["manual_override_flag"] is False


def test_authentication_challenge_when_enabled(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "REQUIRE_AUTH", True)
    monkeypatch.setenv("APP_PASSWORD", "local-test-password")
    with client_for(tmp_path, monkeypatch) as client:
        assert client.get("/api/overview").status_code == 401
        assert client.post(
            "/api/import",
            json={"batch_id": "private", "revision": 1, "records": []},
        ).status_code == 401
        response = client.get("/api/overview", auth=("manager", "local-test-password"))
        assert response.status_code == 200


def test_input_signature_changes_when_source_batch_changes(tmp_path, monkeypatch):
    initial = tmp_path / "initial.json"
    update = tmp_path / "update.json"
    initial.write_text("{}", encoding="utf-8")
    update.write_text("{\"revision\": 2}", encoding="utf-8")
    monkeypatch.setattr(api, "INITIAL_FILE", initial)
    monkeypatch.setattr(api, "UPDATE_FILE", update)

    before = api.input_signature()
    update.write_text("{\"revision\": 3}", encoding="utf-8")

    assert api.input_signature() != before


def test_background_watcher_applies_changed_batch_and_notifies(tmp_path, monkeypatch):
    import asyncio
    import json

    from inbox.service import sync_derived_state

    initial_data = json.loads(api.INITIAL_FILE.read_text(encoding="utf-8"))
    update_data = json.loads(api.UPDATE_FILE.read_text(encoding="utf-8"))
    initial_path = tmp_path / "01_initial.json"
    update_path = tmp_path / "02_update.json"
    initial_path.write_text(json.dumps(initial_data), encoding="utf-8")
    update_path.write_text(json.dumps(update_data), encoding="utf-8")
    database = Database(tmp_path / "watcher.db")
    database.import_delivery(initial_data)
    sync_derived_state(database)
    monkeypatch.setattr(api, "DB", database)
    monkeypatch.setattr(api, "INITIAL_FILE", initial_path)
    monkeypatch.setattr(api, "UPDATE_FILE", update_path)
    monkeypatch.setattr(api, "WATCH_INTERVAL_SECONDS", 0.05)
    monkeypatch.setattr(api, "ROOT", tmp_path)
    monkeypatch.setattr(api, "export_current", lambda database, output_dir: {})

    async def run_watcher():
        watcher = asyncio.create_task(api.watch_input_batches())
        await asyncio.sleep(0)
        update_path.write_text(json.dumps(update_data, indent=2), encoding="utf-8")
        await asyncio.sleep(0.15)
        watcher.cancel()
        try:
            await watcher
        except asyncio.CancelledError:
            pass

    asyncio.run(run_watcher())

    m07 = next(record for record in database.records() if record["record_ref"] == "M07")
    changed_alerts = [item for item in database.notifications() if item["record_ref"] == "M07"]
    assert m07["applied_revision"] == 2
    assert len(changed_alerts) == 1
    assert changed_alerts[0]["revision"] == 2


def test_startup_reconciles_revision_changed_while_app_was_offline(tmp_path):
    import json

    from inbox.service import initialize_demo, sync_derived_state

    initial_data = json.loads(api.INITIAL_FILE.read_text(encoding="utf-8"))
    update_data = json.loads(api.UPDATE_FILE.read_text(encoding="utf-8"))
    initial_path = tmp_path / "01_initial.json"
    update_path = tmp_path / "02_update.json"
    initial_path.write_text(json.dumps(initial_data), encoding="utf-8")
    update_path.write_text(json.dumps(update_data), encoding="utf-8")
    database = Database(tmp_path / "offline.db")
    database.import_delivery(initial_data)
    sync_derived_state(database)

    initialize_demo(database, initial_path, update_path)

    m07 = next(record for record in database.records() if record["record_ref"] == "M07")
    assert m07["applied_revision"] == 2
    assert any(item["record_ref"] == "M07" for item in database.notifications())


def test_custom_json_delivery_adds_records_and_rejects_invalid_identity(tmp_path, monkeypatch):
    with client_for(tmp_path, monkeypatch) as client:
        delivery = {
            "batch_id": "company-export-2026-08-22",
            "revision": 1,
            "exported_at": "2026-08-22T09:00:00Z",
            "records": [
                {
                    "record_ref": "C99",
                    "source_type": "call",
                    "source_id": "extra-call-001",
                    "payload": {
                        "id": "extra-call-001",
                        "title": "Customer asks about billing",
                        "transcript": "We need help understanding the invoice.",
                        "started_at": "2026-08-22T08:30:00Z",
                    },
                    "transcript_payload": None,
                }
            ],
        }

        first = client.post("/api/import", json=delivery)
        assert first.status_code == 200
        assert first.json()["actions"]["new"] == 1
        assert first.json()["record_count"] == 45

        repeated = client.post("/api/import", json=delivery)
        assert repeated.status_code == 200
        assert repeated.json()["actions"]["ignored-same"] == 1
        assert repeated.json()["record_count"] == 45

        invalid = {
            **delivery,
            "batch_id": "invalid-company-export",
            "revision": 2,
            "records": [{**delivery["records"][0], "payload": {"id": "wrong-id"}}],
        }
        rejected = client.post("/api/import", json=invalid)
        assert rejected.status_code == 422
        assert len(client.get("/api/overview").json()["records"]) == 45


def test_import_endpoints_log_same_and_older_revisions(tmp_path, monkeypatch):
    with client_for(tmp_path, monkeypatch) as client:
        initial_again = client.post("/api/import/initial")
        assert initial_again.status_code == 200
        assert initial_again.json()["actions"]["ignored-older"] == 1
        assert initial_again.json()["actions"]["ignored-same"] == 43
        log = client.get("/api/import-log")
        assert log.status_code == 200
        actions = [row["action"] for row in log.json()]
        assert "new" in actions and "replaced" in actions and "ignored-older" in actions
