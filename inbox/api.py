from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import secrets
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from inbox.assistant import answer_question
from inbox.database import Database
from inbox.exporter import export_current
from inbox.service import (
    ROOT,
    ROUTES,
    dashboard_metrics,
    filter_records,
    import_json_file,
    initialize_demo,
    record_detail,
    records_view,
    reset_manual_routes,
    save_manual_routes,
    sync_derived_state,
)


SOURCE_BATCH_DIR = ROOT / "data" / "source_batches"
INITIAL_FILE = SOURCE_BATCH_DIR / "01_initial.json"
UPDATE_FILE = SOURCE_BATCH_DIR / "02_update.json"
DATABASE_PATH = Path(os.environ.get("DB_PATH", ROOT / "state" / "app.db"))
DB = Database(DATABASE_PATH)
AUTH = HTTPBasic(auto_error=False)
REQUIRE_AUTH = os.environ.get("REQUIRE_AUTH", "false").lower() == "true"
WATCH_INTERVAL_SECONDS = max(2, int(os.environ.get("INPUT_WATCH_SECONDS", "5")))
LOGGER = logging.getLogger(__name__)


class RouteDecisionBody(BaseModel):
    routes: list[str] = Field(default_factory=list)
    note: str = ""
    who: str = "Reviewer"


class AssistantQuestion(BaseModel):
    question: str = Field(min_length=1, max_length=500)
    person: str | None = None
    source_type: str | None = None


class ImportDeliveryBody(BaseModel):
    batch_id: str = Field(min_length=1, max_length=128)
    revision: int = Field(ge=1)
    exported_at: str | None = None
    records: list[dict[str, Any]] = Field(max_length=5000)


def require_user(
    credentials: Annotated[HTTPBasicCredentials | None, Depends(AUTH)],
) -> str:
    password = os.environ.get("APP_PASSWORD")
    if not REQUIRE_AUTH and not password:
        return "Local reviewer"
    if not password:
        raise HTTPException(status_code=503, detail="APP_PASSWORD is not configured")
    if credentials is None or not secrets.compare_digest(credentials.password, password):
        raise HTTPException(
            status_code=401,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username


def input_signature() -> tuple[tuple[str, int, int, str], ...]:
    values = []
    for path in (INITIAL_FILE, UPDATE_FILE):
        try:
            stat = path.stat()
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            values.append((str(path), stat.st_mtime_ns, stat.st_size, digest))
        except OSError:
            values.append((str(path), -1, -1, "missing"))
    return tuple(values)


async def watch_input_batches() -> None:
    previous = input_signature()
    while True:
        await asyncio.sleep(WATCH_INTERVAL_SECONDS)
        current = input_signature()
        if current == previous:
            continue
        try:
            for path in (INITIAL_FILE, UPDATE_FILE):
                result = import_json_file(DB, path)
                if result["actions"]["new"] or result["actions"]["replaced"]:
                    LOGGER.info("Applied changed input batch %s: %s", path.name, result["actions"])
            export_current(DB, ROOT / "exports")
            previous = current
        except (OSError, ValueError, json.JSONDecodeError):
            LOGGER.exception("Changed input file could not be imported; will retry")


@asynccontextmanager
async def lifespan(application: FastAPI):
    initialize_demo(DB, INITIAL_FILE, UPDATE_FILE)
    export_current(DB, ROOT / "exports")
    watcher = asyncio.create_task(watch_input_batches())
    try:
        yield
    finally:
        watcher.cancel()
        with suppress(asyncio.CancelledError):
            await watcher


app = FastAPI(title="Revenue Intelligence Inbox", version="1.0.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


@app.get("/")
def home() -> FileResponse:
    return FileResponse(ROOT / "static" / "index.html")


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/assistant")
def assistant(
    body: AssistantQuestion,
    user: Annotated[str, Depends(require_user)],
) -> dict:
    records = filter_records(records_view(DB), body.person, body.source_type)
    return answer_question(records, body.question.strip())


@app.get("/api/notifications")
def notifications(user: Annotated[str, Depends(require_user)]) -> dict:
    items = DB.notifications(limit=50)
    current_records = {
        (record["source_type"], record["source_id"]): record for record in records_view(DB)
    }
    for item in items:
        record = current_records.get((item["source_type"], item["source_id"]))
        if record:
            item["risk_classification"] = record["risk_classification"]
            item["applied_revision"] = record["applied_revision"]
    return {"items": items, "unread_count": sum(item["read_at"] is None for item in items)}


@app.post("/api/notifications/read")
def read_notifications(user: Annotated[str, Depends(require_user)]) -> dict[str, str]:
    DB.mark_notifications_read()
    return {"status": "read"}


@app.get("/api/overview")
def overview(
    user: Annotated[str, Depends(require_user)],
    person: str | None = Query(default=None),
    source_type: str | None = Query(default=None),
) -> dict:
    records = records_view(DB)
    selected = filter_records(records, person, source_type)
    people = []
    seen = set()
    for record in records:
        if record["source_type"] == "call":
            name = record["payload"].get("rep_name") or "Unknown rep"
            label = f"Call rep · {name}"
            value = f"call:{name}"
        else:
            name = record["payload"].get("organizer_email") or "Unknown organiser"
            label = f"Meeting organizer · {name}"
            value = f"meeting:{name}"
        if value not in seen:
            seen.add(value)
            people.append({"value": value, "label": label})
    return {
        "metrics": dashboard_metrics(records, person, source_type),
        "people": people,
        "routes": ROUTES,
        "records": [
            {
                "source_type": item["source_type"],
                "source_id": item["source_id"],
                "record_ref": item["record_ref"],
                "title": item["payload"].get("title") or f"{'Call' if item['source_type'] == 'call' else 'Meeting'} {item['record_ref']}",
                "person": item["payload"].get("rep_name") if item["source_type"] == "call" else item["payload"].get("organizer_email"),
                "intake_state": item["intake_state"],
                "source_reason": item["source_reason"],
                "analysis_freshness": item["analysis_freshness"],
                "routes": item["effective_routes"],
                "manual_override_flag": item["manual_override_flag"],
                "quality_flags": item["quality_flags"],
                "risk_classification": item["risk_classification"],
                "usable_text": item["usable_text"],
            }
            for item in selected
        ],
        "selected_records": len(selected),
        "sample_label": "selected records, not a whole-team day",
        "pre_generated_notice": "Conversation analyses are pre-generated and evidence-checked. Counts, coverage, route rules, staleness, overrides and exports are computed locally.",
        "reviewer": user,
    }


@app.get("/api/record/{source_type}/{source_id}")
def get_record(
    source_type: str,
    source_id: str,
    user: Annotated[str, Depends(require_user)],
) -> dict:
    if source_type not in {"call", "meeting"}:
        raise HTTPException(status_code=404, detail="Record not found")
    record = record_detail(DB, source_type, source_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Record not found")
    return record


@app.post("/api/record/{source_type}/{source_id}/routes")
def update_routes(
    source_type: str,
    source_id: str,
    body: RouteDecisionBody,
    user: Annotated[str, Depends(require_user)],
) -> dict[str, str]:
    try:
        saved = save_manual_routes(DB, source_type, source_id, body.routes, body.note, body.who or user)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    if not saved:
        raise HTTPException(status_code=404, detail="Record not found")
    export_current(DB, ROOT / "exports")
    return {"status": "saved"}


@app.post("/api/record/{source_type}/{source_id}/routes/reset")
def reset_routes(
    source_type: str,
    source_id: str,
    user: Annotated[str, Depends(require_user)],
) -> dict[str, str]:
    if not reset_manual_routes(DB, source_type, source_id):
        raise HTTPException(status_code=404, detail="Record not found")
    export_current(DB, ROOT / "exports")
    return {"status": "reset"}


@app.post("/api/import/{batch_name}")
def import_batch(
    batch_name: str,
    user: Annotated[str, Depends(require_user)],
) -> dict:
    files = {"initial": INITIAL_FILE, "update": UPDATE_FILE}
    if batch_name not in files:
        raise HTTPException(status_code=404, detail="Batch must be initial or update")
    result = import_json_file(DB, files[batch_name])
    export_current(DB, ROOT / "exports")
    return result


@app.post("/api/import")
def import_delivery(
    body: ImportDeliveryBody,
    user: Annotated[str, Depends(require_user)],
) -> dict:
    try:
        result = DB.import_delivery(body.model_dump())
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    sync_derived_state(DB)
    export_current(DB, ROOT / "exports")
    return {**result, "record_count": len(DB.records())}


@app.post("/api/demo")
def load_demo(user: Annotated[str, Depends(require_user)]) -> dict:
    results = [import_json_file(DB, INITIAL_FILE), import_json_file(DB, UPDATE_FILE)]
    export_current(DB, ROOT / "exports")
    return {"status": "loaded", "imports": results, "record_count": len(DB.records())}


@app.post("/api/reset")
def reset_demo(user: Annotated[str, Depends(require_user)]) -> dict[str, str]:
    DB.clear()
    export_dir = ROOT / "exports" / "current"
    if export_dir.exists():
        import shutil

        shutil.rmtree(export_dir)
    return {"status": "reset"}


@app.post("/api/export")
def run_export(user: Annotated[str, Depends(require_user)]) -> dict:
    return export_current(DB, ROOT / "exports")


@app.get("/api/manager-brief")
def manager_brief(user: Annotated[str, Depends(require_user)]) -> dict[str, str]:
    path = ROOT / "exports" / "current" / "daily" / "2026-08-21_manager_brief.md"
    if not path.exists():
        export_current(DB, ROOT / "exports")
    return {"markdown": path.read_text(encoding="utf-8")}


@app.get("/api/import-log")
def import_log(user: Annotated[str, Depends(require_user)]) -> list[dict]:
    with DB.connect() as connection:
        rows = connection.execute(
            "SELECT b.batch_id, l.record_ref, l.revision, l.action, l.logged_at "
            "FROM import_log l JOIN import_batches b ON b.batch_pk = l.batch_pk "
            "ORDER BY l.log_id DESC LIMIT 200"
        ).fetchall()
    return [dict(row) for row in rows]
