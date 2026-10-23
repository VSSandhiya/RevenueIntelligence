from __future__ import annotations

import copy
import json
import tempfile
from pathlib import Path

from inbox.database import Database
from inbox.service import records_view, save_manual_routes, sync_derived_state


ROOT = Path(__file__).resolve().parent.parent
SOURCE_BATCH_DIR = ROOT / "data" / "source_batches"


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _check(records: list[dict], usable: int, step: str) -> None:
    assert len(records) == 44, f"{step}: expected 44 distinct records, found {len(records)}"
    actual_usable = sum(item["usable_text"] for item in records)
    assert actual_usable == usable, f"{step}: expected {usable} usable texts, found {actual_usable}"
    print(f"PASS {step}: {len(records)} records, {actual_usable} usable texts")


def run_replay() -> list[str]:
    initial = _read(SOURCE_BATCH_DIR / "01_initial.json")
    update = _read(SOURCE_BATCH_DIR / "02_update.json")
    report: list[str] = []
    with tempfile.TemporaryDirectory(prefix="revenue-inbox-replay-") as temporary:
        db_path = Path(temporary) / "app.db"
        database = Database(db_path)

        first = database.import_delivery(initial)
        assert first["actions"]["new"] == 44
        sync_derived_state(database)
        _check(records_view(database), 17, "initial import")

        shuffled = copy.deepcopy(initial)
        shuffled["records"].reverse()
        repeated = database.import_delivery(shuffled)
        assert repeated["actions"]["ignored-same"] == 44
        sync_derived_state(database)
        _check(records_view(database), 17, "initial import shuffled and repeated")

        m07_before = next(item for item in records_view(database) if item["record_ref"] == "M07")
        assert m07_before["analysis_freshness"] == "stale"
        assert m07_before["intake_state"] == "awaiting_late_transcript"
        save_manual_routes(
            database,
            "meeting",
            m07_before["source_id"],
            ["Product or support issue", "Needs human"],
            "Keep unresolved support issues visible until verified.",
            "Replay reviewer",
        )
        print("PASS manual M07 routes saved before update")

        before = {
            (item["source_type"], item["source_id"]): (item["raw_payload_json"], item["raw_transcript_json"])
            for item in database.records()
        }
        updated = database.import_delivery(update)
        assert updated["actions"]["replaced"] == 1
        sync_derived_state(database)
        after_update = records_view(database)
        _check(after_update, 18, "revision 2 update")
        m07_after = next(item for item in after_update if item["record_ref"] == "M07")
        assert len(m07_after["transcript_payload"]["turns"]) == 27
        assert m07_after["analysis_freshness"] == "current"
        assert m07_after["manual_override_flag"]
        assert m07_after["effective_routes"] == ["Product or support issue", "Needs human"]

        old = database.import_delivery(initial)
        assert old["actions"]["ignored-older"] == 1
        assert old["actions"]["ignored-same"] == 43
        sync_derived_state(database)
        final_records = records_view(database)
        _check(final_records, 18, "older revision replay")
        for item in database.records():
            key = (item["source_type"], item["source_id"])
            if key != ("meeting", m07_before["source_id"]):
                assert (item["raw_payload_json"], item["raw_transcript_json"]) == before[key]

        # Reopen the same file to exercise durable state across a process restart.
        database = Database(db_path)
        sync_derived_state(database)
        restarted = records_view(database)
        _check(restarted, 18, "database reopened after restart")
        m07_restarted = next(item for item in restarted if item["record_ref"] == "M07")
        assert len(m07_restarted["transcript_payload"]["turns"]) == 27
        assert m07_restarted["analysis_freshness"] == "current"
        assert m07_restarted["manual_override_flag"]
        assert m07_restarted["effective_routes"] == ["Product or support issue", "Needs human"]

        with database.connect() as connection:
            actions = [row[0] for row in connection.execute("SELECT action FROM import_log")]
        assert actions.count("new") == 44
        assert actions.count("replaced") == 1
        assert actions.count("ignored-same") == 87
        assert actions.count("ignored-older") == 1
        report.append(
            f"Import log: new={actions.count('new')}, replaced={actions.count('replaced')}, "
            f"ignored-same={actions.count('ignored-same')}, ignored-older={actions.count('ignored-older')}"
        )
        print("PASS M07 transcript, analysis freshness, manual routes, and unaffected records")
        print("PASS import log: " + report[-1])
    return report


if __name__ == "__main__":
    run_replay()
    print("REPLAY RESULT: PASS")