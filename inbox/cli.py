from __future__ import annotations

import json
import shutil
from pathlib import Path

from inbox.database import Database
from inbox.exporter import export_current
from inbox.service import ROOT, import_json_file


SOURCE_BATCH_DIR = ROOT / "data" / "source_batches"


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(prog="revenue-inbox")
    parser.add_argument("command", choices=["demo", "reset", "export"])
    args = parser.parse_args()
    database = Database(ROOT / "state" / "app.db")
    if args.command == "reset":
        database.clear()
        current = ROOT / "exports" / "current"
        if current.exists():
            shutil.rmtree(current)
        print("Reset local database and current export.")
        return
    if args.command == "demo":
        for name in ("01_initial.json", "02_update.json"):
            result = import_json_file(database, SOURCE_BATCH_DIR / name)
            print(json.dumps(result, ensure_ascii=False))
    result = export_current(database, ROOT / "exports")
    print(f"Exported {result['record_count']} records to {result['directory']}")


if __name__ == "__main__":
    main()