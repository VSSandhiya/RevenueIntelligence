from inbox.database import Database


def record(source_type, source_id, record_ref, payload, transcript=None):
    return {
        "source_type": source_type,
        "source_id": source_id,
        "record_ref": record_ref,
        "payload": {"id": source_id, **payload},
        "transcript_payload": transcript,
    }


def delivery(revision, records):
    return {"batch_id": f"batch-{revision}", "revision": revision, "records": records}


def test_repeated_delivery_is_ignored_and_logged(tmp_path):
    database = Database(tmp_path / "app.db")
    batch = delivery(1, [record("call", "same-id", "C01", {"transcript": "hello"})])

    first = database.import_delivery(batch)
    second = database.import_delivery(batch)

    assert first["actions"]["new"] == 1
    assert second["actions"]["ignored-same"] == 1
    assert len(database.records()) == 1
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM import_log").fetchone()[0] == 2


def test_identity_is_the_source_type_and_source_id_pair(tmp_path):
    database = Database(tmp_path / "app.db")
    database.import_delivery(
        delivery(
            1,
            [
                record("call", "shared-id", "C01", {"name": "same"}),
                record("meeting", "shared-id", "M01", {"name": "same"}),
            ],
        )
    )

    assert len(database.records()) == 2


def test_newer_revision_replaces_whole_payload_including_nulls(tmp_path):
    database = Database(tmp_path / "app.db")
    database.import_delivery(
        delivery(1, [record("meeting", "m7", "M07", {"title": "old", "kept": True}, {"turns": []})])
    )
    result = database.import_delivery(
        delivery(2, [record("meeting", "m7", "M07", {"title": None}, {"turns": [{"text": "new"}]})])
    )

    assert result["actions"]["replaced"] == 1
    stored = database.records()[0]
    assert stored["applied_revision"] == 2
    assert stored["raw_payload_json"] == '{"id":"m7","title":null}'
    assert '"text":"new"' in stored["raw_transcript_json"]


def test_older_revision_cannot_overwrite_newer_data(tmp_path):
    database = Database(tmp_path / "app.db")
    database.import_delivery(delivery(2, [record("meeting", "m7", "M07", {"title": "new"})]))

    result = database.import_delivery(delivery(1, [record("meeting", "m7", "M07", {"title": "old"})]))

    assert result["actions"]["ignored-older"] == 1
    assert '"title":"new"' in database.records()[0]["raw_payload_json"]


def test_invalid_delivery_does_not_partially_import(tmp_path):
    database = Database(tmp_path / "app.db")
    valid = record("call", "c1", "C01", {"transcript": "ok"})
    invalid = {**record("call", "c2", "C02", {}), "payload": {"id": "wrong"}}

    try:
        database.import_delivery(delivery(1, [valid, invalid]))
    except ValueError as error:
        assert "payload.id" in str(error)
    else:
        raise AssertionError("invalid delivery was accepted")

    assert database.records() == []


def test_notifications_are_created_only_for_new_or_replaced_revisions(tmp_path):
    database = Database(tmp_path / "app.db")
    first = delivery(1, [record("meeting", "m7", "M07", {"title": "old"})])
    update = delivery(2, [record("meeting", "m7", "M07", {"title": "new"})])

    database.import_delivery(first)
    database.import_delivery(first)
    database.import_delivery(update)
    database.import_delivery(first)

    alerts = database.notifications()
    assert len(alerts) == 2
    assert alerts[0]["title"] == "Source data changed: M07"
    assert alerts[0]["revision"] == 2
    assert alerts[1]["title"] == "New records imported"
    database.mark_notifications_read()
    assert all(alert["read_at"] is not None for alert in database.notifications())