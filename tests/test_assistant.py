import pytest
from fastapi.testclient import TestClient

from inbox import assistant, api
from inbox.api import INITIAL_FILE, UPDATE_FILE
from inbox.database import Database
from inbox.service import dashboard_metrics, initialize_demo, records_view, source_text


def test_record_question_returns_verified_evidence(tmp_path, monkeypatch):
    database = Database(tmp_path / "assistant.db")
    initialize_demo(database, INITIAL_FILE, UPDATE_FILE)
    monkeypatch.setattr(assistant, "_ask_local_model", lambda question, matches: None)

    result = assistant.answer_question(records_view(database), "Why does M04 need attention?")
    m04 = next(row for row in records_view(database) if row["record_ref"] == "M04")

    assert result["record_count"] == 1
    assert result["citations"][0]["record_ref"] == "M04"
    assert result["citations"][0]["quote"] in source_text(m04)
    assert result["mode"] == "Evidence checked · no hosted AI used"
    assert "does not make route decisions" in result["notice"]


def test_follow_up_question_finds_routed_records(tmp_path, monkeypatch):
    database = Database(tmp_path / "assistant.db")
    initialize_demo(database, INITIAL_FILE, UPDATE_FILE)
    monkeypatch.setattr(assistant, "_ask_local_model", lambda question, matches: None)

    result = assistant.answer_question(records_view(database), "What follow-up is due?")

    assert result["record_count"] > 0
    assert all(item["record_ref"] for item in result["citations"])
    assert "follow-up" in result["answer"].casefold()


def test_model_url_must_be_loopback_only(monkeypatch):
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:11434")
    assert assistant.local_model_url() == "http://localhost:11434"

    monkeypatch.setenv("OLLAMA_BASE_URL", "https://example.com")
    with pytest.raises(ValueError, match="localhost"):
        assistant.local_model_url()


def test_assistant_endpoint_filters_by_selected_source(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "DB", Database(tmp_path / "api.db"))
    monkeypatch.setattr(assistant, "_ask_local_model", lambda question, matches: None)
    original_export = api.export_current
    monkeypatch.setattr(api, "export_current", lambda database, output_dir: original_export(database, tmp_path / "exports"))

    with TestClient(api.app) as client:
        response = client.post("/api/assistant", json={"question": "What needs attention?", "source_type": "meeting"})

    assert response.status_code == 200
    assert all(item["source_type"] == "meeting" for item in response.json()["citations"])


def test_dashboard_risk_categories_and_call_meeting_counts(tmp_path):
    database = Database(tmp_path / "dashboard.db")
    initialize_demo(database, INITIAL_FILE, UPDATE_FILE)
    records = records_view(database)

    metrics = dashboard_metrics(records)
    m04 = next(row for row in records if row["record_ref"] == "M04")

    assert metrics["selected_records"]["value"] == 44
    assert metrics["activity_volume"] == {"calls": 30, "meetings": 14, "selected": 44}
    assert metrics["risk_summary"]["counts"] == {
        "not_enough_text": 26,
        "client_attention": 13,
        "no_risk_found": 2,
        "potential_risk": 3,
    }
    assert m04["risk_classification"]["category"] == "potential_risk"
    assert "does not prove there is no risk outside this extract" in next(
        row["risk_classification"]["reason"]
        for row in records
        if row["risk_classification"]["category"] == "no_risk_found"
    )