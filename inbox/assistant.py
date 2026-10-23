from __future__ import annotations

import json
import os
import re
from typing import Any
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from inbox.service import source_text


DEFAULT_MODEL = "qwen3.8:27b"
STOP_WORDS = {
    "about", "after", "are", "can", "could", "does", "for", "from", "give",
    "help", "important", "need", "please", "record", "should", "show", "tell",
    "that", "the", "this", "what", "when", "where", "which", "who", "with",
}
PRIORITY_TERMS = ("attention", "urgent", "priority", "risk", "review", "today")
FOLLOW_UP_TERMS = ("follow-up", "follow up", "next step", "next steps", "pending")


class _BlockRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        return None


def local_model_url() -> str:
    value = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
    parsed = urlparse(value)
    if parsed.scheme != "http" or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("The model URL must use HTTP on this computer (localhost).")
    return value.rstrip("/")


def _record_text(record: dict[str, Any]) -> str:
    analysis = record.get("analysis") or {}
    parts = [
        record.get("payload", {}).get("title", ""),
        source_text(record),
        analysis.get("what_happened", ""),
        analysis.get("why_it_matters", ""),
        analysis.get("next_step", ""),
        " ".join(record.get("effective_routes", [])),
        " ".join(record.get("suggested_routes", [])),
    ]
    return " ".join(str(part) for part in parts if part).casefold()


def _priority(record: dict[str, Any]) -> tuple[int, int, str]:
    routes = set(record.get("effective_routes", []))
    urgency = 0
    if record.get("analysis_freshness") == "stale":
        urgency += 5
    if record.get("intake_state") == "needs_human_review":
        urgency += 4
    if "Needs human" in routes:
        urgency += 3
    if routes.intersection({"Prospect follow-up", "Customer success / retention risk", "Product or support issue"}):
        urgency += 2
    return (-urgency, 0 if record.get("usable_text") else 1, record.get("record_ref", ""))


def find_records(records: list[dict[str, Any]], question: str) -> list[dict[str, Any]]:
    text = question.strip()
    record_match = re.search(r"\b([CM]\d{2})\b", text, flags=re.IGNORECASE)
    if record_match:
        target = record_match.group(1).upper()
        return [record for record in records if record.get("record_ref", "").upper() == target]

    lowered = text.casefold()
    if any(term in lowered for term in FOLLOW_UP_TERMS):
        candidates = [
            record for record in records
            if set(record.get("effective_routes", [])).intersection(
                {"Prospect follow-up", "Customer success / retention risk"}
            )
        ]
        return sorted(candidates, key=_priority)[:5]

    if any(term in lowered for term in PRIORITY_TERMS):
        candidates = [
            record for record in records
            if record.get("analysis_freshness") == "stale"
            or record.get("intake_state") == "needs_human_review"
            or "Needs human" in record.get("effective_routes", [])
            or set(record.get("effective_routes", [])).intersection(
                {"Prospect follow-up", "Customer success / retention risk", "Product or support issue"}
            )
        ]
        return sorted(candidates, key=_priority)[:5]

    terms = [term for term in re.findall(r"[a-z0-9']+", lowered) if len(term) > 2 and term not in STOP_WORDS]
    scored = []
    for record in records:
        haystack = _record_text(record)
        score = sum(haystack.count(term) for term in terms)
        if score:
            scored.append((score, record))
    scored.sort(key=lambda item: (-item[0], *_priority(item[1])))
    return [record for _, record in scored[:5]]


def _citations(records: list[dict[str, Any]]) -> list[dict[str, str]]:
    citations = []
    for record in records:
        analysis = record.get("analysis") or {}
        evidence = analysis.get("evidence") or []
        first = evidence[0] if evidence else {}
        citations.append(
            {
                "record_ref": record.get("record_ref", ""),
                "source_type": record.get("source_type", ""),
                "source_id": record.get("source_id", ""),
                "title": record.get("payload", {}).get("title") or record.get("record_ref", "Record"),
                "quote": first.get("quote", ""),
                "turn_number": str(first.get("turn_number", "")),
                "freshness": record.get("analysis_freshness", "pending"),
            }
        )
    return citations


def _grounded_answer(records: list[dict[str, Any]], question: str) -> str:
    if not records:
        return "I couldn't find a matching conversation in the selected records. Try a record ID such as C10 or M04, or use a person/source filter."

    first = records[0]
    analysis = first.get("analysis") or {}
    if len(records) == 1:
        explanation = analysis.get("why_it_matters") or analysis.get("what_happened")
        next_step = analysis.get("next_step")
        pieces = [f"For {first['record_ref']}: {explanation or 'The source record is available for review.'}"]
        if next_step:
            pieces.append(f"Next step: {next_step}")
        if first.get("analysis_freshness") == "stale":
            pieces.append("This analysis is out of date for the current transcript, so review the new source first.")
        return " ".join(pieces)

    refs = ", ".join(record.get("record_ref", "") for record in records[:5])
    if any(term in question.casefold() for term in FOLLOW_UP_TERMS):
        lead = "These conversations have a follow-up or customer-retention route."
    else:
        lead = "These conversations are the best starting points for review based on current routing and analysis freshness."
    return f"{lead} Start with {refs}. Open each record to check the transcript and source evidence before acting."


def _model_context(records: list[dict[str, Any]]) -> str:
    context = []
    for record in records:
        analysis = record.get("analysis") or {}
        context.append(
            json.dumps(
                {
                    "record_ref": record.get("record_ref"),
                    "source_type": record.get("source_type"),
                    "revision": record.get("applied_revision"),
                    "freshness": record.get("analysis_freshness"),
                    "intake_state": record.get("intake_state"),
                    "source_reason": record.get("source_reason"),
                    "routes": record.get("effective_routes", []),
                    "analysis": analysis,
                    "evidence": analysis.get("evidence", []),
                },
                ensure_ascii=True,
            )
        )
    return "\n".join(context)


def _ask_local_model(question: str, records: list[dict[str, Any]]) -> str | None:
    try:
        url = f"{local_model_url()}/api/chat"
        model = os.environ.get("OLLAMA_MODEL", DEFAULT_MODEL)
        body = json.dumps(
            {
                "model": model,
                "stream": False,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "You are a careful revenue operations assistant. Answer in plain language using only the supplied records. "
                            "Do not invent owners, outcomes, account links, values, or next steps. Clearly call out stale analysis. "
                            "Do not change routes or take actions. Keep the answer under 120 words."
                        ),
                    },
                    {
                        "role": "user",
                        "content": f"Question: {question}\nCurrent evidence records (JSON):\n{_model_context(records)}",
                    },
                ],
                "options": {"temperature": 0.1},
            }
        ).encode("utf-8")
        request = Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
        opener = build_opener(_BlockRedirects())
        with opener.open(request, timeout=2.5) as response:
            result = json.loads(response.read().decode("utf-8"))
        message = result.get("message", {}).get("content", "").strip()
        return message or None
    except (OSError, URLError, TimeoutError, ValueError, json.JSONDecodeError):
        return None


def answer_question(records: list[dict[str, Any]], question: str) -> dict[str, Any]:
    matches = find_records(records, question)
    model_answer = _ask_local_model(question, matches) if matches else None
    citations = _citations(matches)
    return {
        "answer": model_answer or _grounded_answer(matches, question),
        "mode": "AI draft · verify linked evidence" if model_answer else "Evidence checked · no hosted AI used",
        "model": os.environ.get("OLLAMA_MODEL", DEFAULT_MODEL) if model_answer else None,
        "tools_used": ["Find matching conversations", "Check whether analysis matches newest text", "Link source passages"],
        "citations": citations,
        "record_count": len(matches),
        "notice": "This assistant can suggest what to review. It does not make route decisions or replace human judgment.",
    }