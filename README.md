# Revenue Intelligence Inbox

A private, local-first workspace for reviewing the supplied 44 call and meeting records. It combines a plain-language conversation assistant, evidence-linked record review, simple visual summaries and human-owned route decisions. SQLite stores source revisions, analysis, intake state, route suggestions and human decisions independently.

## Run locally

Requires Python 3.11 or newer. Runtime deliveries are kept in `data/source_batches/`; the supplied source data must stay private.

```powershell
python -m pip install -r requirements.txt
python -m uvicorn inbox.api:app --host 127.0.0.1 --port 8000
```

Open <http://127.0.0.1:8000>. On first startup the app loads both delivery batches and opens in the revision-2 state. Local development has no password unless `REQUIRE_AUTH=true` or `APP_PASSWORD` is set. To protect a local run too, set `APP_PASSWORD` and `REQUIRE_AUTH=true` in the process environment before starting Uvicorn. Do not put a password in source control.

To add more records, choose **Add data** and select a JSON delivery file. It must follow the supplied [data contract](DATA_CONTRACT.md): a `batch_id`, positive `revision`, optional `exported_at`, and a `records` array. Each record needs `record_ref`, `source_type`, `source_id`, `payload`, and `transcript_payload`; `payload.id` must equal `source_id`. Calls put text in `payload.transcript`; meetings put turns in `transcript_payload.turns`. The file is sent only to this app and is not retained as an uploaded file. Source rows are stored in SQLite and included in regenerated exports.

Use a new batch ID for each delivery. To update an existing identity, raise its revision; the newer row replaces its entire payload, while manual routing is preserved. Same or older revisions are safely ignored. New records without a matching, evidence-checked analysis are marked for human review; the app does not invent an analysis for them.

## Ask the assistant

The first screen gives a plain-language starting point, suggested questions, source-linked answers and a quick visual of transcript coverage and records needing review. Try “What needs my attention?”, “Which customers need follow-up?” or ask about a record such as M04. Each citation opens the source fields, transcript, analysis freshness and route decision.

The assistant is a small tool-using workflow: it searches only the selected records, checks analysis freshness, and attaches verified evidence. It never changes routes or sends messages. With no model installed, it gives a deterministic evidence guide. To enable local model wording, install Ollama, start its local service, and pull the current Qwen 3.8 model:

```powershell
ollama pull qwen3.8:27b
$env:OLLAMA_MODEL = "qwen3.8:27b"
python -m uvicorn inbox.api:app --host 127.0.0.1 --port 8000
```

Qwen 3.8 27B needs substantial memory and disk space. On a smaller computer, use `ollama pull qwen3.5:4b` and set `OLLAMA_MODEL` to `qwen3.5:4b`. Ollama requests are restricted to localhost; no hosted model API or API key is used. Model-written wording is labelled as a draft, while the attached citations come from the app's pre-validated source analysis. Hosted deployments that do not run a local Ollama model use the deterministic assistant instead.

## Ongoing monitoring and manager alerts

At every startup, the app first reconciles both input batches against SQLite, so a higher revision changed while the app was offline is not missed. While the app process is running, a local watcher checks the two supplied input files every five seconds (`INPUT_WATCH_SECONDS` changes the interval). When a valid file changes, it reapplies both batches through the normal revision rules, refreshes analysis freshness, routing and exports, and stores an alert for each genuinely replaced record. New records create one batch summary. Repeated or older deliveries do not create duplicate alerts. The browser refreshes the manager feed every 30 seconds; alerts are retained in SQLite until marked as seen. Alert text contains record IDs and revision information, not transcript quotes.

This is evidence-led triage, not a prediction model:

- **Potential risk:** current usable text has an evidence-backed customer-retention or product/support route.
- **Needs follow-up or review:** current routes call for prospect follow-up or a human check, or source-quality flags exist.
- **No risk found in reviewed text:** current usable text has no customer-risk route. This does not prove the customer has no risk outside this extract.
- **Not enough transcript:** no usable words were supplied. Silent, cancelled, not-scheduled and late-transcript reasons remain distinct in the status list.

Every category shows its total and call-versus-meeting counts. The 21 Aug sample contains 30 calls and 14 meetings; those counts describe this selected extract, not a whole team's day. Alerts are in-app and can optionally use the browser's desktop notification permission while the app is open; the browser alert contains only a record ID and revision. An always-on service and persistent disk are required for continuous monitoring; this Render configuration uses the paid Starter plan. A sleeping/free host or a powered-off local computer cannot watch files continuously. No email or third-party notification service is configured.

No training dataset is needed for this product. The sample has only 18 unlabelled conversations, so training would be misleading; the app uses pre-generated, source-checked analyses and deterministic routing. If the rules are evaluated later, collect company-owned conversations with explicit consent, redact personal/contact details, and have reviewers label a separate evaluation set. Do not upload these customer records to public dataset sites or train a model on them.

The requested Make targets are available when `make` is installed:

| Command | Action |
| --- | --- |
| `make run` | Start the local web app on `127.0.0.1:8000`. |
| `make demo` | Idempotently import both batches and rebuild exports. |
| `make reset` | Clear the local database and active export. The next app start seeds the post-update demo; while running, choose Load demo. |
| `make test` | Run the pytest suite. |
| `make export` | Rebuild the active export from SQLite. |
| `make replay` | Run the repeat/shuffle/manual-override/update/older-delivery/reopen replay in an isolated temporary database. |

Equivalent Python commands are `python -m inbox.cli demo`, `python -m inbox.cli reset`, `python -m inbox.cli export`, `python -m inbox.replay` and `python -m pytest -q`.

## Deploy privately

`Dockerfile` uses Python 3.11. `render.yaml` sets `REQUIRE_AUTH=true`, creates a persistent disk at `/app/state`, and leaves `APP_PASSWORD` unsynced so the deployer must enter it in the private Render dashboard. HTTP Basic authentication protects every API endpoint; the health endpoint remains available for platform checks. Keep the deployment private and share credentials only with reviewers. TLS is supplied by the hosting platform. No API keys are needed.

The included dataset is de-identified but still must not be published as a public app or repository. A password-protected link is the intended deploy mode. SQLite and exports are local files; the Render disk persists the database, while generated export files can be rebuilt at any time.

## Import and replay

Source identity is exactly `(source_type, source_id)`. Initial revision 1 and update revision 2 are applied per identity; equal revisions log `ignored-same`, lower revisions log `ignored-older`, and higher revisions replace both complete payloads, including nulls. Delivery order and record-array order do not define identity. The Add data control accepts additional contract-compatible JSON batches. Manual routes live in a separate table and survive source replacement until Reset to automatic is clicked.

`make replay` checks these steps:

1. Import `data/source_batches/01_initial.json`: 44 records, 17 usable texts.
2. Reverse the record order and import the same batch again: still 44 and 17.
3. Manually route M07 to Product or support issue and Needs human.
4. Import `data/source_batches/02_update.json`: still 44, now 18 usable texts; M07 has 27 turns and its analysis is current.
5. Import revision 1 again: M07 remains revision 2 and the manual route remains.
6. Reopen the SQLite file: records, transcript, analysis freshness and override remain intact.

The replay asserts the final import log totals: `new=44`, `replaced=1`, `ignored-same=87`, `ignored-older=1`. The app's Load demo button repeats both file imports without clearing human decisions. Reset is a separate explicit action.

## Metrics

- **Selected records:** distinct `(source_type, source_id)` identities after filters. Every screen says “selected records, not a whole-team day.”
- **Activity:** calls use `started_at`; meetings use `starts_at`; both are converted to `Asia/Kolkata`. The person filter distinguishes call `rep_name` from meeting `organizer_email`; an organiser is not assumed to be a seller or attendee.
- **Usable text coverage:** nonblank call transcript or at least one nonblank meeting turn, divided by selected records. The dashboard also reports call and meeting numerators and denominators separately.
- **Source-reported connection rate:** calls with `is_connected=true` divided by calls whose `is_connected` is not null. Unknown calls are shown separately. This is the source's stored classification, not transcript truth.
- **Routing:** unique records count each identity once; route assignments count each destination, so one record may contribute more than one assignment. A manual decision replaces the automatic route in the effective assignment count, while the automatic suggestion remains visible.
- **Review:** all 44 records have one intake state: `has_text_analysed`, `voicemail_or_unanswered_no_text`, `recorded_but_silent`, `not_recorded`, `awaiting_late_transcript`, or `needs_human_review`. Source reason text is retained and shown. Stale analysis, manual overrides and source-quality flags are counted separately.
- **Data-quality flags:** compare source values without rewriting them. Examples include talk time exceeding duration, a connected/unresponsive source combination, C02's conversation label versus a gatekeeper transfer, C06's spoken name versus `rep_name`, and C10's follow-up label versus an explicit refusal.

No talk ratios, rankings, pipeline totals, account links or staff roles are inferred.

## Analysis and exports

The 18 conversation analyses are pre-generated in `data/analysis/<source_type>_<source_id>.json`. Each includes a differentiated output, evidence, uncertainty, next step, and owner only when the source states one. Calls cite exact quotes; meetings cite 1-based turn numbers and exact quote text. The generator validates every citation against the final revision of the transcript. The app computes counts, coverage, intake, exact-phrase routing, evidence freshness, manual decisions, source-quality flags and exports locally. No model is trained. Optional assistant wording uses a model running on the same computer; the default experience needs no model download.

On a new revision the complete source payload and transcript payload replace the previous values. If an analysis file's `source_revision` differs from the current row, the UI labels it stale and does not present it as current. Newer source data can change suggested routes, but not a manual route.

`exports/current/` is rebuilt from current SQLite state:

- `manifest.json` and `manifest.csv` list source identity, revision, routes, route source, review state, freshness, override flag and relative paths.
- `records/<record_ref>/source.json` and `transcript.txt` hold the canonical source and transcript.
- Each route has `<route>/<record_ref>_<short_title>/analysis.md` and `evidence.json`, linking back to the canonical source folder.
- `daily/2026-08-21_manager_brief.md` and `activity.csv` hold the fixed-date brief and local-time activity.

Re-export replaces the active directory, so obsolete destinations are removed; every manifest output path is checked by tests.

## Findings and rejected choice

1. **C10 declines a follow-up call.** “No. I said I will contact you if there is anything to discuss.” This drives the Sales coaching and Needs human routes, the source-label conflict flag, and the record-detail callout. A stored `follow_up` label does not override the spoken boundary.
2. **C02 is a receptionist transfer, not a buyer conversation.** “Her voicemail would be fine, thank you.” The dashboard flags the source `is_conversation=true` claim, and the record detail routes it to human review rather than treating it as a prospect exchange.
3. **M04 has several connected customer risks, not one generic deal note.** Turn 11: “moving from prepaid to credit is the main issue for us”; turn 21 leaves accounting feasibility unconfirmed. The detail and export route it to Customer success / retention risk, Product or support issue, and Needs human.

The deliberately rejected choice is to trust stored outcomes or meeting titles as route labels. That would be simpler and more consistent-looking, but it would misroute C10, C02 and internal/vendor meetings. The accepted trade-off is that literal rules can miss implied intent; unsupported or stale cases go to Needs human.

## Limitations and unfinished work

- The app is a compact single-manager review tool, not a CRM, production call recorder, staff directory or opportunity system.
- The adapted transcript is abridged and has no reliable call-speaker timing. Evidence quotes prove presence in the supplied text, not completeness of the original conversation.
- The 18 analyses are human-assisted, pre-generated judgments; they are not independent verification of product claims or outcomes. The optional local model may phrase a draft incorrectly, so citations and transcript should be checked before decisions.
- No training dataset is needed or appropriate for these 18 unlabelled conversations. The assistant retrieves from source-checked analysis rather than training on customer text. For future evaluation, use only consenting, de-identified examples and keep them separate from training; do not upload customer conversations to public dataset portals.
- Continuous monitoring runs inside the app process and depends on the configured host staying awake. Manager alerts are persistent in-app notices only; email/SMS delivery is not implemented.
- This environment had Python 3.12 rather than 3.11; the container image is pinned to the requested 3.11 family. `make` and Docker hosting were not available in this local Windows session; tests and replay were run with Python commands.
- A spoken screen recording must be recorded by the submitter; `video_script.md` is the narration guide, not a recorded media file.

Approximate implementation effort: about 7 hours, including source inspection, app, evidence checks, exports and tests.