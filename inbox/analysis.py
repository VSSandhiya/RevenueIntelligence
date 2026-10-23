from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from inbox.prebuilt import ANALYSES


def transcript_text(record: dict[str, Any]) -> str:
    payload = record["payload"]
    if record["source_type"] == "call":
        return payload.get("transcript") or ""
    transcript = record.get("transcript_payload") or {}
    return "\n".join(
        turn.get("text", "")
        for turn in transcript.get("turns", [])
        if isinstance(turn.get("text"), str) and turn["text"].strip()
    )


def evidence_turn(record: dict[str, Any], turn_number: int) -> str:
    turns = (record.get("transcript_payload") or {}).get("turns", [])
    if turn_number < 1 or turn_number > len(turns):
        raise ValueError(f"{record['record_ref']}: evidence turn {turn_number} does not exist")
    return turns[turn_number - 1].get("text", "")


def validate_analysis(record: dict[str, Any], analysis: dict[str, Any]) -> None:
    if not transcript_text(record).strip():
        raise ValueError(f"{record['record_ref']}: analysis cannot attach without usable text")
    evidence_items = list(analysis.get("evidence", []))
    evidence_items.extend(analysis.get("route_rules", []))
    for item in evidence_items:
        quote = item.get("quote") or item.get("trigger")
        if not isinstance(quote, str) or not quote.strip():
            raise ValueError(f"{record['record_ref']}: evidence quote is empty")
        if record["source_type"] == "call":
            source_text = record["payload"].get("transcript") or ""
        else:
            turn_number = item.get("turn_number")
            if not isinstance(turn_number, int):
                raise ValueError(f"{record['record_ref']}: meeting evidence needs a turn number")
            source_text = evidence_turn(record, turn_number)
        if quote not in source_text:
            raise ValueError(f"{record['record_ref']}: evidence quote is not present in its cited source")


def analysis_path(directory: Path, record: dict[str, Any]) -> Path:
    return directory / f"{record['source_type']}_{record['source_id']}.json"


def build_analysis_files(
    initial_path: Path,
    update_path: Path,
    output_dir: Path,
) -> list[Path]:
    initial = json.loads(initial_path.read_text(encoding="utf-8"))
    update = json.loads(update_path.read_text(encoding="utf-8"))
    final_records = {
        (item["source_type"], item["source_id"]): item for item in initial["records"]
    }
    for item in update["records"]:
        final_records[(item["source_type"], item["source_id"])] = item
    by_ref = {item["record_ref"]: item for item in final_records.values()}
    output_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for record_ref, content in ANALYSES.items():
        record = by_ref[record_ref]
        validate_analysis(record, content)
        document = {
            "source_type": record["source_type"],
            "source_id": record["source_id"],
            "record_ref": record_ref,
            "source_revision": update["revision"] if record_ref == "M07" else initial["revision"],
            **content,
        }
        path = analysis_path(output_dir, record)
        path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        written.append(path)
    return written


def load_analysis(directory: Path, record: dict[str, Any]) -> dict[str, Any] | None:
    path = analysis_path(directory, record)
    if not path.exists():
        return None
    analysis = json.loads(path.read_text(encoding="utf-8"))
    if analysis.get("source_revision") != record.get("applied_revision"):
        return None
    validate_analysis(record, analysis)
    return analysis


def classify_intake(record: dict[str, Any], analysis: dict[str, Any] | None) -> tuple[str, str | None, bool]:
    payload = record["payload"]
    if transcript_text(record).strip():
        if analysis is not None and analysis.get("review_required"):
            return "needs_human_review", "Transcript analysis includes an explicit source or decision uncertainty.", True
        return "has_text_analysed", None, True
    if record["source_type"] == "call":
        return "voicemail_or_unanswered_no_text", "No usable call transcript was supplied.", False
    transcript = record.get("transcript_payload") or {}
    reason = transcript.get("unavailable_reason")
    status = payload.get("recording_status")
    if status in {"cancelled", "not_scheduled"}:
        detail = payload.get("recording_detail") or f"Recording status: {status}."
        return "not_recorded", detail, False
    if record["record_ref"] == "M07" and record.get("applied_revision") == 1:
        return "awaiting_late_transcript", reason or "The transcript is not present in revision 1.", False
    return "recorded_but_silent", reason or "Recording exists, but no usable transcript turns were supplied.", False