import copy
import json
from pathlib import Path

import pytest

from inbox.analysis import build_analysis_files, validate_analysis
from inbox.database import Database
from inbox.service import (
    dashboard_metrics,
    initialize_demo,
    records_view,
    save_manual_routes,
    sync_derived_state,
)


ROOT = Path(__file__).resolve().parents[1]
SOURCE_BATCH_DIR = ROOT / "data" / "source_batches"
INITIAL = SOURCE_BATCH_DIR / "01_initial.json"
UPDATE = SOURCE_BATCH_DIR / "02_update.json"


def load_delivery(path):
    return json.loads(path.read_text(encoding="utf-8"))


def create_database(path):
    database = Database(path)
    initialize_demo(database, INITIAL, UPDATE)
    return database


def test_analysis_catalog_covers_all_eighteen_text_records(tmp_path):
    output = tmp_path / "analysis"
    files = build_analysis_files(INITIAL, UPDATE, output)

    assert len(files) == 18
    assert len(list(output.glob("*.json"))) == 18
    assert all(json.loads(path.read_text(encoding="utf-8"))["source_revision"] in (1, 2) for path in files)


def test_quote_and_turn_validation_rejects_nonexistent_evidence():
    record = next(item for item in load_delivery(INITIAL)["records"] if item["record_ref"] == "C01")
    bad_analysis = {
        "evidence": [{"quote": "a phrase that is not in this transcript"}],
        "route_rules": [],
    }

    with pytest.raises(ValueError, match="not present"):
        validate_analysis(record, bad_analysis)


def test_initial_update_replay_keeps_44_records_and_manual_route(tmp_path):
    database = Database(tmp_path / "app.db")
    initial = load_delivery(INITIAL)
    update = load_delivery(UPDATE)
    database.import_delivery(initial)
    sync_derived_state(database)
    assert sum(item["usable_text"] for item in records_view(database)) == 17

    shuffled = copy.deepcopy(initial)
    shuffled["records"].reverse()
    database.import_delivery(shuffled)
    sync_derived_state(database)
    assert len(database.records()) == 44

    m07 = next(item for item in records_view(database) if item["record_ref"] == "M07")
    assert m07["analysis_freshness"] == "stale"
    assert m07["intake_state"] == "awaiting_late_transcript"
    assert save_manual_routes(database, "meeting", m07["source_id"], ["Internal (no sales analysis)"], "Keep in support review", "Reviewer")

    before = {
        (item["source_type"], item["source_id"]): (item["raw_payload_json"], item["raw_transcript_json"])
        for item in database.records()
    }
    database.import_delivery(update)
    sync_derived_state(database)
    after_update = records_view(database)
    assert len(after_update) == 44
    assert sum(item["usable_text"] for item in after_update) == 18
    updated_m07 = next(item for item in after_update if item["record_ref"] == "M07")
    assert len(updated_m07["transcript_payload"]["turns"]) == 27
    assert updated_m07["analysis_freshness"] == "current"
    assert updated_m07["manual_override_flag"] is True
    assert updated_m07["effective_routes"] == ["Internal (no sales analysis)"]

    database.import_delivery(initial)
    sync_derived_state(database)
    final = records_view(database)
    assert len(final) == 44
    final_m07 = next(item for item in final if item["record_ref"] == "M07")
    assert final_m07["applied_revision"] == 2
    assert final_m07["manual_override_flag"] is True
    assert len(final_m07["transcript_payload"]["turns"]) == 27
    for item in database.records():
        key = (item["source_type"], item["source_id"])
        if key != ("meeting", updated_m07["source_id"]):
            assert (item["raw_payload_json"], item["raw_transcript_json"]) == before[key]

    with database.connect() as connection:
        actions = [row[0] for row in connection.execute("SELECT action FROM import_log")]
    assert actions.count("new") == 44
    assert actions.count("replaced") == 1
    assert actions.count("ignored-same") == 87
    assert actions.count("ignored-older") == 1


def test_dashboard_metrics_keep_types_denominators_and_timezone(tmp_path):
    database = create_database(tmp_path / "app.db")
    records = records_view(database)
    metrics = dashboard_metrics(records)

    assert metrics["selected_records"]["value"] == 44
    assert metrics["usable_text_coverage"] == {
        "usable": 18,
        "selected": 44,
        "by_type": {"call": {"usable": 10, "selected": 30}, "meeting": {"usable": 8, "selected": 14}},
        "label": "usable text / selected records",
    }
    assert metrics["source_reported_connection_rate"]["known"] + metrics["source_reported_connection_rate"]["unknown"] == 30
    assert metrics["source_reported_connection_rate"]["selected_calls"] == 30
    assert all(0 <= row["hour"] <= 23 for row in metrics["activity_by_hour"])
    assert sum(row["calls"] + row["meetings"] for row in metrics["activity_by_hour"]) == 44
    people = metrics["activity_by_person"]
    assert all(row["label"].startswith(("Call rep · ", "Meeting organizer · ")) for row in people)
    assert all((row["calls"] == 0) ^ (row["meetings"] == 0) for row in people)

    calls_only = dashboard_metrics(records, source_type="call")
    assert calls_only["selected_records"]["value"] == 30
    assert calls_only["usable_text_coverage"]["usable"] == 10


def test_intake_states_and_source_claim_flags_are_visible(tmp_path):
    database = create_database(tmp_path / "app.db")
    records = records_view(database)
    states = {item["intake_state"] for item in records}
    assert {
        "has_text_analysed",
        "voicemail_or_unanswered_no_text",
        "recorded_but_silent",
        "not_recorded",
        "needs_human_review",
    } <= states

    c02 = next(item for item in records if item["record_ref"] == "C02")
    c06 = next(item for item in records if item["record_ref"] == "C06")
    c10 = next(item for item in records if item["record_ref"] == "C10")
    assert any("conversation" in flag.lower() for flag in c02["quality_flags"])
    assert any("ravi" in flag.lower() for flag in c06["quality_flags"])
    assert any("follow-up label" in flag.lower() for flag in c10["quality_flags"])


def test_manual_routes_survive_a_later_revision_without_fresh_analysis(tmp_path):
    database = create_database(tmp_path / "app.db")
    m07 = next(item for item in records_view(database) if item["record_ref"] == "M07")
    assert save_manual_routes(database, "meeting", m07["source_id"], ["Vendor/supplier note"], "Manual stays", "Reviewer")
    changed = copy.deepcopy(load_delivery(UPDATE))
    changed["revision"] = 3
    changed["batch_id"] = "update-003"
    changed["records"][0]["transcript_payload"]["turns"][0]["text"] += " New sentence."
    database.import_delivery(changed)
    sync_derived_state(database)

    updated = next(item for item in records_view(database) if item["record_ref"] == "M07")
    assert updated["analysis_freshness"] == "stale"
    assert updated["intake_state"] == "needs_human_review"
    assert updated["manual_override_flag"] is True
    assert updated["effective_routes"] == ["Vendor/supplier note"]


def test_update_before_initial_delivery_still_leaves_one_current_record(tmp_path):
    database = Database(tmp_path / "app.db")
    database.import_delivery(load_delivery(UPDATE))
    database.import_delivery(load_delivery(INITIAL))
    sync_derived_state(database)

    records = records_view(database)
    m07 = next(item for item in records if item["record_ref"] == "M07")
    assert len(records) == 44
    assert sum(item["usable_text"] for item in records) == 18
    assert m07["applied_revision"] == 2
    assert len(m07["transcript_payload"]["turns"]) == 27
