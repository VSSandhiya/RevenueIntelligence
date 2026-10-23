from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from inbox.analysis import classify_intake, validate_analysis
from inbox.database import Database, json_text


ROOT = Path(__file__).resolve().parent.parent
ANALYSIS_DIR = ROOT / "data" / "analysis"
ROUTES = [
    "Prospect follow-up",
    "Sales coaching",
    "Partner/channel",
    "Customer success / retention risk",
    "Product or support issue",
    "Vendor/supplier note",
    "Internal (no sales analysis)",
    "Needs human",
    "No action (no text)",
]
LOCAL_ZONE = ZoneInfo("Asia/Kolkata")


def unpack_record(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_type": row["source_type"],
        "source_id": row["source_id"],
        "record_ref": row["record_ref"],
        "applied_revision": row["applied_revision"],
        "payload": json.loads(row["raw_payload_json"]),
        "transcript_payload": json.loads(row["raw_transcript_json"]),
    }


def source_text(record: dict[str, Any]) -> str:
    if record["source_type"] == "call":
        return record["payload"].get("transcript") or ""
    transcript = record.get("transcript_payload") or {}
    return "\n".join(
        turn.get("text", "") for turn in transcript.get("turns", []) if turn.get("text", "").strip()
    )


def stored_analysis(record: dict[str, Any], analysis_dir: Path = ANALYSIS_DIR) -> dict[str, Any] | None:
    path = analysis_dir / f"{record['source_type']}_{record['source_id']}.json"
    if not path.exists():
        return None
    analysis = json.loads(path.read_text(encoding="utf-8"))
    if analysis.get("source_revision") == record["applied_revision"]:
        try:
            validate_analysis(record, analysis)
        except ValueError:
            return None
    return analysis


def analysis_freshness(record: dict[str, Any], analysis: dict[str, Any] | None) -> str:
    if analysis is None:
        return "pending" if source_text(record).strip() else "not_applicable"
    return "current" if analysis.get("source_revision") == record["applied_revision"] else "stale"


def compute_route_suggestions(
    record: dict[str, Any],
    analysis: dict[str, Any] | None,
    freshness: str,
) -> tuple[list[str], list[dict[str, Any]]]:
    if not source_text(record).strip():
        return ["No action (no text)"], []
    if analysis is None or freshness != "current":
        return ["Needs human"], [{"route": "Needs human", "reason": "Text has no analysis verified against the applied revision."}]
    source = record["payload"].get("transcript") or "" if record["source_type"] == "call" else ""
    turns = (record.get("transcript_payload") or {}).get("turns", [])
    reasons = []
    for item in analysis.get("route_rules", []):
        trigger = item.get("trigger", "")
        if record["source_type"] == "call":
            matched = trigger in source
        else:
            turn_number = item.get("turn_number", 0)
            matched = (
                1 <= turn_number <= len(turns)
                and trigger in turns[turn_number - 1].get("text", "")
            )
        if matched:
            reasons.append(dict(item))
    route_names = list(dict.fromkeys(item["route"] for item in reasons))
    return route_names or ["Needs human"], reasons or [
        {"route": "Needs human", "reason": "No specific route rule matched this text."}
    ]


def sync_derived_state(database: Database, analysis_dir: Path = ANALYSIS_DIR) -> None:
    for row in database.records():
        record = unpack_record(row)
        analysis = stored_analysis(record, analysis_dir)
        freshness = analysis_freshness(record, analysis)
        intake_state, source_reason, usable_text = classify_intake(
            record,
            analysis if freshness == "current" else None,
        )
        if freshness == "stale" and usable_text:
            intake_state = "needs_human_review"
            source_reason = "Source revision changed after this analysis; review the new text before relying on it."
        elif freshness == "pending" and usable_text:
            intake_state = "needs_human_review"
            source_reason = "Text is available but has no analysis verified against this source revision."
        suggested_routes, reasons = compute_route_suggestions(record, analysis, freshness)
        with database.connect() as connection:
            with connection:
                if analysis is not None:
                    connection.execute(
                        "INSERT INTO analyses (source_type, source_id, source_revision, output_kind, analysis_json, status) "
                        "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(source_type, source_id) DO UPDATE SET "
                        "source_revision = excluded.source_revision, output_kind = excluded.output_kind, "
                        "analysis_json = excluded.analysis_json, status = excluded.status, updated_at = CURRENT_TIMESTAMP",
                        (
                            record["source_type"],
                            record["source_id"],
                            analysis.get("source_revision", 0),
                            analysis.get("output_kind", "untyped"),
                            json_text(analysis),
                            freshness,
                        ),
                    )
                else:
                    connection.execute(
                        "UPDATE analyses SET status = 'stale', updated_at = CURRENT_TIMESTAMP "
                        "WHERE source_type = ? AND source_id = ?",
                        (record["source_type"], record["source_id"]),
                    )
                connection.execute(
                    "INSERT INTO processing_states (source_type, source_id, intake_state, source_reason, usable_text) "
                    "VALUES (?, ?, ?, ?, ?) ON CONFLICT(source_type, source_id) DO UPDATE SET "
                    "intake_state = excluded.intake_state, source_reason = excluded.source_reason, "
                    "usable_text = excluded.usable_text, updated_at = CURRENT_TIMESTAMP",
                    (
                        record["source_type"],
                        record["source_id"],
                        intake_state,
                        source_reason,
                        int(usable_text),
                    ),
                )
                connection.execute(
                    "INSERT INTO route_decisions (source_type, source_id, suggested_routes_json, route_reasons_json) "
                    "VALUES (?, ?, ?, ?) ON CONFLICT(source_type, source_id) DO UPDATE SET "
                    "suggested_routes_json = excluded.suggested_routes_json, "
                    "route_reasons_json = excluded.route_reasons_json, updated_at = CURRENT_TIMESTAMP",
                    (
                        record["source_type"],
                        record["source_id"],
                        json_text(suggested_routes),
                        json_text(reasons),
                    ),
                )


def initialize_demo(database: Database, initial_path: Path, update_path: Path) -> None:
    for path in (initial_path, update_path):
        import_json_file(database, path)


def import_json_file(database: Database, path: Path) -> dict[str, Any]:
    delivery = json.loads(path.read_text(encoding="utf-8"))
    result = database.import_delivery(delivery)
    sync_derived_state(database)
    return result


def _person_key(record: dict[str, Any]) -> tuple[str, str]:
    if record["source_type"] == "call":
        return "call", record["payload"].get("rep_name") or "Unknown rep"
    return "meeting", record["payload"].get("organizer_email") or "Unknown organiser"


def filter_records(
    records: list[dict[str, Any]],
    person: str | None = None,
    source_type: str | None = None,
) -> list[dict[str, Any]]:
    selected = records
    if source_type in {"call", "meeting"}:
        selected = [record for record in selected if record["source_type"] == source_type]
    if person:
        selected = [record for record in selected if ":".join(_person_key(record)) == person]
    return selected


def _local_hour(record: dict[str, Any]) -> int | None:
    field = "started_at" if record["source_type"] == "call" else "starts_at"
    value = record["payload"].get(field)
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(LOCAL_ZONE).hour
    except ValueError:
        return None


def quality_flags(record: dict[str, Any], analysis: dict[str, Any] | None) -> list[str]:
    payload = record["payload"]
    flags: list[str] = []
    if record["source_type"] == "call":
        duration = payload.get("duration_seconds")
        talk_time = payload.get("talk_time_seconds")
        if duration is not None and talk_time is not None and talk_time > duration:
            flags.append("Source talk time exceeds source duration")
        if payload.get("is_connected") is True and payload.get("observed_outcome") == "unresponsive":
            flags.append("Source-reported connected conflicts with unresponsive outcome")
        spoken_name = re.search(r"\b([A-Z][a-z]+) here\.", source_text(record))
        source_name = payload.get("rep_name")
        if spoken_name and source_name and spoken_name.group(1).casefold() != source_name.casefold():
            flags.append(f"Spoken name {spoken_name.group(1)} differs from source rep name {source_name}")
        if payload.get("is_conversation") is True and analysis and record["record_ref"] == "C02":
            flags.append("Source says conversation; text only shows a receptionist-to-voicemail transfer")
        if (
            payload.get("observed_outcome") == "follow_up"
            and "No. I said I will contact you if there is anything to discuss." in source_text(record)
        ):
            flags.append("Source follow-up label conflicts with the contact declining a scheduled call")
    return flags


def classify_attention(record: dict[str, Any]) -> dict[str, str]:
    if not record["usable_text"]:
        return {
            "category": "not_enough_text",
            "label": "Not enough transcript",
            "reason": record.get("source_reason") or "No usable conversation text was supplied.",
        }
    if record["analysis_freshness"] != "current":
        return {
            "category": "client_attention",
            "label": "Needs attention",
            "reason": "The analysis is not verified against the newest available text.",
        }

    routes = set(record["effective_routes"])
    risk_routes = {"Customer success / retention risk", "Product or support issue"}
    matched_risk_routes = routes.intersection(risk_routes)
    if matched_risk_routes:
        if record["manual_override_flag"]:
            reason = record.get("manual_note") or "A reviewer selected a customer-risk route."
        else:
            reason = next(
                (
                    item.get("reason", item.get("trigger", ""))
                    for item in record["route_reasons"]
                    if item.get("route") in matched_risk_routes
                ),
                "The current analysis identified a customer or product issue route.",
            )
        return {"category": "potential_risk", "label": "Potential risk", "reason": reason}

    attention_routes = {"Prospect follow-up", "Needs human"}
    if routes.intersection(attention_routes) or record["quality_flags"]:
        reason = record["quality_flags"][0] if record["quality_flags"] else next(
            (
                item.get("reason", item.get("trigger", ""))
                for item in record["route_reasons"]
                if item.get("route") in attention_routes
            ),
            "A person should review the suggested next step.",
        )
        return {"category": "client_attention", "label": "Needs attention", "reason": reason}

    return {
        "category": "no_risk_found",
        "label": "No risk found in reviewed text",
        "reason": "The current, usable text has no customer-risk route. This does not prove there is no risk outside this extract.",
    }


def records_view(database: Database) -> list[dict[str, Any]]:
    rows = database.records()
    output = []
    with database.connect() as connection:
        for row in rows:
            record = unpack_record(row)
            analysis_row = connection.execute(
                "SELECT analysis_json, status FROM analyses WHERE source_type = ? AND source_id = ?",
                (record["source_type"], record["source_id"]),
            ).fetchone()
            state = connection.execute(
                "SELECT intake_state, source_reason, usable_text FROM processing_states "
                "WHERE source_type = ? AND source_id = ?",
                (record["source_type"], record["source_id"]),
            ).fetchone()
            route = connection.execute(
                "SELECT suggested_routes_json, route_reasons_json, manual_routes_json, manual_flag, note, who "
                "FROM route_decisions WHERE source_type = ? AND source_id = ?",
                (record["source_type"], record["source_id"]),
            ).fetchone()
            analysis = json.loads(analysis_row["analysis_json"]) if analysis_row else None
            freshness = analysis_row["status"] if analysis_row else "pending"
            record.update(
                {
                    "analysis": analysis,
                    "analysis_freshness": freshness,
                    "intake_state": state["intake_state"] if state else "needs_human_review",
                    "source_reason": state["source_reason"] if state else "Derived state has not been built.",
                    "usable_text": bool(state["usable_text"]) if state else bool(source_text(record).strip()),
                    "suggested_routes": json.loads(route["suggested_routes_json"]) if route else [],
                    "route_reasons": json.loads(route["route_reasons_json"]) if route else [],
                    "manual_routes": json.loads(route["manual_routes_json"]) if route else [],
                    "manual_override_flag": bool(route["manual_flag"]) if route else False,
                    "manual_note": route["note"] if route else None,
                    "manual_who": route["who"] if route else None,
                    "effective_routes": (
                        json.loads(route["manual_routes_json"])
                        if route and route["manual_flag"]
                        else json.loads(route["suggested_routes_json"]) if route else []
                    ),
                }
            )
            record["quality_flags"] = quality_flags(record, analysis)
            record["risk_classification"] = classify_attention(record)
            output.append(record)
    return output


def dashboard_metrics(
    all_records: list[dict[str, Any]],
    person: str | None = None,
    source_type: str | None = None,
) -> dict[str, Any]:
    selected = filter_records(all_records, person, source_type)
    total = len(selected)
    usable = sum(record["usable_text"] for record in selected)
    calls = [record for record in selected if record["source_type"] == "call"]
    meetings = [record for record in selected if record["source_type"] == "meeting"]
    known_connection = [record for record in calls if record["payload"].get("is_connected") is not None]
    connected = sum(record["payload"].get("is_connected") is True for record in known_connection)
    assignments = sum(len(record["effective_routes"]) for record in selected)
    by_type: dict[str, dict[str, int]] = {}
    for kind in ("call", "meeting"):
        typed = [record for record in selected if record["source_type"] == kind]
        typed_usable = sum(record["usable_text"] for record in typed)
        by_type[kind] = {"usable": typed_usable, "selected": len(typed)}

    hour_counts: dict[int, Counter[str]] = defaultdict(Counter)
    people_counts: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    intake_counts = Counter(record["intake_state"] for record in selected)
    risk_counts = Counter(record["risk_classification"]["category"] for record in selected)
    risk_by_type = {
        kind: dict(Counter(record["risk_classification"]["category"] for record in selected if record["source_type"] == kind))
        for kind in ("call", "meeting")
    }
    route_counts = Counter(route for record in selected for route in record["effective_routes"])
    flags = [
        {"record_ref": record["record_ref"], "source_type": record["source_type"], "flags": record["quality_flags"]}
        for record in selected
        if record["quality_flags"]
    ]
    for record in selected:
        hour = _local_hour(record)
        if hour is not None:
            hour_counts[hour][record["source_type"]] += 1
        kind, name = _person_key(record)
        people_counts[(kind, name)][kind] += 1

    return {
        "selected_records": {"value": total, "denominator": total, "label": "selected records, not a whole-team day"},
        "activity_volume": {"calls": len(calls), "meetings": len(meetings), "selected": total},
        "risk_summary": {"counts": dict(risk_counts), "by_type": risk_by_type, "selected": total},
        "activity_by_hour": [
            {"hour": hour, "calls": values["call"], "meetings": values["meeting"]}
            for hour, values in sorted(hour_counts.items())
        ],
        "activity_by_person": [
            {
                "person": name,
                "label": f"{'Call rep' if kind == 'call' else 'Meeting organizer'} · {name}",
                "filter_value": f"{kind}:{name}",
                "calls": values["call"],
                "meetings": values["meeting"],
            }
            for (kind, name), values in sorted(people_counts.items())
        ],
        "usable_text_coverage": {
            "usable": usable,
            "selected": total,
            "by_type": by_type,
            "label": "usable text / selected records",
        },
        "source_reported_connection_rate": {
            "connected": connected,
            "known": len(known_connection),
            "unknown": len(calls) - len(known_connection),
            "selected_calls": len(calls),
            "rate": connected / len(known_connection) if known_connection else None,
            "label": "source-reported connected calls / calls with non-null is_connected",
        },
        "routing": {
            "unique_records": sum(bool(record["effective_routes"]) for record in selected),
            "route_assignments": assignments,
            "difference": assignments - sum(bool(record["effective_routes"]) for record in selected),
            "by_route": dict(route_counts),
        },
        "review_by_intake_state": dict(intake_counts),
        "stale_analyses": sum(record["analysis_freshness"] == "stale" for record in selected),
        "manual_overrides": sum(record["manual_override_flag"] for record in selected),
        "data_quality_flags": flags,
    }


def record_detail(database: Database, source_type: str, source_id: str) -> dict[str, Any] | None:
    records = records_view(database)
    record = next(
        (item for item in records if item["source_type"] == source_type and item["source_id"] == source_id),
        None,
    )
    if record is None:
        return None
    with database.connect() as connection:
        history = connection.execute(
            "SELECT b.batch_id, l.revision, l.action, l.record_ref, l.logged_at "
            "FROM import_log l JOIN import_batches b ON b.batch_pk = l.batch_pk "
            "WHERE l.source_type = ? AND l.source_id = ? ORDER BY l.log_id",
            (source_type, source_id),
        ).fetchall()
    record["revision_history"] = [dict(item) for item in history]
    return record


def save_manual_routes(
    database: Database,
    source_type: str,
    source_id: str,
    routes: list[str],
    note: str,
    who: str,
) -> bool:
    if any(route not in ROUTES for route in routes):
        raise ValueError("One or more routes are not supported")
    with database.connect() as connection:
        with connection:
            exists = connection.execute(
                "SELECT 1 FROM source_records WHERE source_type = ? AND source_id = ?",
                (source_type, source_id),
            ).fetchone()
            if exists is None:
                return False
            connection.execute(
                "UPDATE route_decisions SET manual_routes_json = ?, manual_flag = 1, note = ?, who = ?, "
                "decided_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP "
                "WHERE source_type = ? AND source_id = ?",
                (json_text(list(dict.fromkeys(routes))), note.strip(), who.strip(), source_type, source_id),
            )
    return True


def reset_manual_routes(database: Database, source_type: str, source_id: str) -> bool:
    with database.connect() as connection:
        with connection:
            cursor = connection.execute(
                "UPDATE route_decisions SET manual_routes_json = '[]', manual_flag = 0, note = NULL, "
                "who = NULL, decided_at = NULL, updated_at = CURRENT_TIMESTAMP "
                "WHERE source_type = ? AND source_id = ?",
                (source_type, source_id),
            )
    return cursor.rowcount > 0
