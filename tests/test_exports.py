import json

from inbox.database import Database
from inbox.exporter import export_current
from inbox.service import initialize_demo, records_view, save_manual_routes

from test_source_contract import INITIAL, UPDATE


def test_every_manifest_path_exists_and_multiroute_records_have_each_destination(tmp_path):
    database = Database(tmp_path / "app.db")
    initialize_demo(database, INITIAL, UPDATE)
    result = export_current(database, tmp_path / "exports")
    export_root = tmp_path / "exports" / "current"
    manifest = json.loads((export_root / "manifest.json").read_text(encoding="utf-8"))

    assert result["record_count"] == 44
    for item in manifest["records"]:
        assert all((export_root / path).is_file() for path in item["output_paths"])
    m04 = next(item for item in manifest["records"] if item["record_ref"] == "M04")
    assert len(m04["routes"]) > 1
    assert sum(path.endswith("analysis.md") for path in m04["output_paths"]) == len(m04["routes"])
    assert (export_root / "daily" / "2026-08-21_manager_brief.md").is_file()
    assert (export_root / "daily" / "activity.csv").is_file()


def test_reexport_removes_obsolete_active_route_paths(tmp_path):
    database = Database(tmp_path / "app.db")
    initialize_demo(database, INITIAL, UPDATE)
    first = export_current(database, tmp_path / "exports")
    old_manifest = json.loads((tmp_path / "exports" / "current" / "manifest.json").read_text(encoding="utf-8"))
    old_m07 = next(item for item in old_manifest["records"] if item["record_ref"] == "M07")
    old_route_path = next(path for path in old_m07["output_paths"] if path.endswith("analysis.md"))

    m07 = next(item for item in records_view(database) if item["record_ref"] == "M07")
    save_manual_routes(database, "meeting", m07["source_id"], ["Vendor/supplier note"], "Manual route", "Reviewer")
    export_current(database, tmp_path / "exports")
    new_manifest = json.loads((tmp_path / "exports" / "current" / "manifest.json").read_text(encoding="utf-8"))
    new_m07 = next(item for item in new_manifest["records"] if item["record_ref"] == "M07")

    assert not (tmp_path / "exports" / "current" / old_route_path).exists()
    assert new_m07["route_source"] == "manual"
    assert all((tmp_path / "exports" / "current" / path).is_file() for path in new_m07["output_paths"])