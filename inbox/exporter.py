from __future__ import annotations

import csv
import json
import re
import shutil
from pathlib import Path
from typing import Any

from inbox.service import records_view, source_text


MANAGER_BRIEF = """# Manager brief | 21 August 2026

## Activity

The selected extract contains 44 distinct records: 30 calls and 14 meetings. Supplied usable text covers 18 of 44 records (10 calls, 8 meetings). This is a selected-record view, not a whole-team day or a representative performance sample. Call and meeting activity uses the source start timestamp converted to Asia/Kolkata; delivery timestamps are not activity times.

## Most important conversations

- **Cedarwave (M04):** the customer says the prepaid-to-credit gap is the main issue. The bank-card feed's eligibility and accounting path still need confirmation. [Turn 11: “moving from prepaid to credit is the main issue for us”](../records/M04/source.json).
- **Willowfield (M07):** three separate product behaviors were demonstrated; the upload workaround does not resolve the direct-path issue. [Turn 14: “It is still extra steps compared with doing it directly”](../records/M07/source.json).
- **Asterloom (M03):** evaluation is not approved; the customer wants to brief the CFO and decide internally before any next call. [Turn 36: “we will tell you if another call is needed”](../records/M03/source.json).
- **Harborpath (M01):** the partner needs a scalable referral link and written onboarding requirements before promoting the offer. [Turn 20: “This needs to scale when people discover the offer through us.”](../records/M01/source.json).

## Practical coaching

- Treat a request for a document as the agreed next step, not permission for an unrequested call. C10 explicitly declines a scheduled call; its stored `follow_up` outcome is flagged for review.
- Qualify the product gap before extending a pitch. C05 says its existing system already provides the described payments and approvals.
- Respect explicit contact boundaries and avoid inferring authority from a referral. C09 says not to use the referrer's name.
- Keep stored connection and conversation labels separate from transcript-based interpretation. C02's words show a receptionist transfer to voicemail, not a buyer discussion.

## Follow-up priorities

1. Confirm M04 bank-feed eligibility and accounting reconciliation with the programme team; clearly separate prepaid from bank-issued credit.
2. Track M07's description-refresh, direct attachment and stale payment-amount issues separately; confirm resolution before closing them.
3. Send M03's itemized proposal and wait for the CFO-led internal decision.
4. Send M01 the written partner terms, eligibility checklist and unique link.
5. Send M02 a volume template that distinguishes observed accounts-payable volumes from estimated accounts-receivable volumes.

Analyses in this brief were prepared before runtime and checked against the cited source text. The application computes current counts, coverage, route rules, freshness, manual overrides and exports locally. It does not call an external AI service.
"""


def _slug(value: str, max_length: int = 46) -> str:
    value = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return value[:max_length].rstrip("-") or "record"


def _transcript_file(record: dict[str, Any]) -> str:
    if record["source_type"] == "call":
        return source_text(record).strip() + "\n"
    turns = (record.get("transcript_payload") or {}).get("turns", [])
    if not turns:
        return "No usable meeting turns were supplied.\n"
    return "\n\n".join(
        f"[Turn {index} | {turn.get('timecode', 'time unavailable')} | {turn.get('speaker', 'Unknown speaker')}]\n"
        f"{turn.get('text', '')}"
        for index, turn in enumerate(turns, start=1)
    ) + "\n"


def _analysis_markdown(record: dict[str, Any], canonical_path: str) -> str:
    analysis = record.get("analysis")
    status = record.get("analysis_freshness", "pending").upper()
    lines = [
        f"# {record['record_ref']} | {record['payload'].get('title') or record['payload'].get('observed_outcome') or 'Conversation'}",
        "",
        f"**Analysis freshness:** {status}",
        f"**Applied source revision:** {record['applied_revision']}",
        f"**Canonical source:** [source.json](../../{canonical_path})",
        "",
    ]
    if analysis:
        for label, field in (
            ("What happened", "what_happened"),
            ("Why it matters", "why_it_matters"),
            ("What the evidence does not establish", "what_evidence_does_not_establish"),
            ("Next step", "next_step"),
            ("Owner stated in source", "owner"),
        ):
            lines.extend([f"## {label}", "", str(analysis.get(field) or "Not stated in the source."), ""])
        if record["analysis_freshness"] != "current":
            lines.extend(["This pre-generated analysis is not current for the applied source revision. Review before relying on it.", ""])
    else:
        lines.extend(["No substantive analysis is attached because usable source text is absent.", ""])
        lines.extend([f"**Intake state:** {record.get('intake_state')}", "", record.get("source_reason") or "", ""])
    return "\n".join(lines)


def _evidence_payload(record: dict[str, Any]) -> dict[str, Any]:
    analysis = record.get("analysis") or {}
    return {
        "record_ref": record["record_ref"],
        "source_type": record["source_type"],
        "source_id": record["source_id"],
        "source_revision": analysis.get("source_revision"),
        "analysis_freshness": record.get("analysis_freshness", "pending"),
        "evidence": analysis.get("evidence", []),
        "route_rules": analysis.get("route_rules", []),
    }


def export_current(database, output_dir: Path) -> dict[str, Any]:
    current_dir = output_dir / "current"
    if current_dir.exists():
        shutil.rmtree(current_dir)
    current_dir.mkdir(parents=True)

    records = records_view(database)
    manifest_records = []
    activity: dict[tuple[str, str, str], int] = {}
    for record in records:
        reference = record["record_ref"]
        canonical_folder = f"records/{reference}"
        source_path = f"{canonical_folder}/source.json"
        transcript_path = f"{canonical_folder}/transcript.txt"
        raw_source = {
            "source_type": record["source_type"],
            "source_id": record["source_id"],
            "record_ref": reference,
            "applied_revision": record["applied_revision"],
            "payload": record["payload"],
            "transcript_payload": record.get("transcript_payload"),
        }
        source_file = current_dir / source_path
        transcript_file = current_dir / transcript_path
        source_file.parent.mkdir(parents=True, exist_ok=True)
        source_file.write_text(json.dumps(raw_source, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        transcript_file.write_text(_transcript_file(record), encoding="utf-8")
        paths = [source_path, transcript_path]
        routes = record["effective_routes"]
        route_source = "manual" if record["manual_override_flag"] else "automatic"
        short_title = record["payload"].get("title") or record["payload"].get("observed_outcome") or "conversation"
        short_title = _slug(short_title)
        for route in routes:
            route_folder = f"{_slug(route)}/{reference}_{short_title}"
            route_path = current_dir / route_folder
            route_path.mkdir(parents=True, exist_ok=True)
            analysis_file = route_path / "analysis.md"
            evidence_file = route_path / "evidence.json"
            analysis_file.write_text(_analysis_markdown(record, source_path), encoding="utf-8")
            evidence_file.write_text(json.dumps(_evidence_payload(record), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            paths.extend([f"{route_folder}/analysis.md", f"{route_folder}/evidence.json"])

        manifest_records.append(
            {
                "source_type": record["source_type"],
                "source_id": record["source_id"],
                "record_ref": reference,
                "applied_revision": record["applied_revision"],
                "routes": routes,
                "route_source": route_source,
                "output_paths": paths,
                "review_state": record["intake_state"],
                "manual_override_flag": record["manual_override_flag"],
                "analysis_freshness": record["analysis_freshness"],
                "superseded": False,
            }
        )
        timestamp = record["payload"].get("started_at") if record["source_type"] == "call" else record["payload"].get("starts_at")
        hour = "unknown"
        if timestamp:
            try:
                from datetime import datetime
                from zoneinfo import ZoneInfo

                hour = str(datetime.fromisoformat(timestamp.replace("Z", "+00:00")).astimezone(ZoneInfo("Asia/Kolkata")).hour)
            except ValueError:
                hour = "unknown"
        person = record["payload"].get("rep_name") if record["source_type"] == "call" else record["payload"].get("organizer_email")
        key = (hour, record["source_type"], person or "Unknown")
        activity[key] = activity.get(key, 0) + 1

    manifest = {"generated_from": "current SQLite state", "record_count": len(records), "records": manifest_records}
    (current_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with (current_dir / "manifest.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=[
                "source_type", "source_id", "record_ref", "applied_revision", "routes", "route_source",
                "output_paths", "review_state", "manual_override_flag", "analysis_freshness", "superseded",
            ],
        )
        writer.writeheader()
        for item in manifest_records:
            writer.writerow({**item, "routes": ";".join(item["routes"]), "output_paths": ";".join(item["output_paths"])})

    brief_dir = current_dir / "daily"
    brief_dir.mkdir(parents=True, exist_ok=True)
    (brief_dir / "2026-08-21_manager_brief.md").write_text(MANAGER_BRIEF, encoding="utf-8")
    with (brief_dir / "activity.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(["local_date", "hour_asia_kolkata", "source_type", "person_label", "records"])
        for (hour, source_type, person), count in sorted(activity.items()):
            writer.writerow(["2026-08-21", hour, source_type, person, count])

    return {"record_count": len(records), "directory": str(current_dir), "manifest": str(current_dir / "manifest.json")}
