# Data contract and replay steps

## Open the files

data/01_initial.json is the initial delivery. data/02_update.json is a later delivery for one of the same meetings. Do not add their record counts together. The full state after both contains 44 unique source records, not 45.

These JSON files wrap the original-shaped OXO call and meeting objects in a small assessment delivery envelope. No database is required. A JSON object is a set of named fields; an array is a list. Null means no supplied value, not zero or false.

## Envelope fields

| Field | Type | Meaning |
| --- | --- | --- |
| batch_id | string | Identifies a delivery, not a conversation. |
| revision | integer | Delivery version. Apply the higher revision to each matching source record. |
| exported_at | timestamp | Assessment delivery time. It is not the time the conversation occurred. |
| records | array | Source records supplied in this delivery. |
| records[].record_ref | string | Short reading reference such as M07 or C01. Convenience only, not an identity key or route label. |
| records[].source_type | string | call or meeting. |
| records[].source_id | string | Same as payload.id. Unique together with source_type. |
| records[].payload | object | Call or meeting metadata using the source field names and types. |
| records[].transcript_payload | object or null | Meeting transcript response. For calls it is null because text is in payload.transcript. |

## Import rules

1. Identity is the pair (source_type, source_id). Never key records by company name, title, phone, record_ref or array position.
2. Initial revision is 1. Update revision is 2. Store the last applied revision per record. Same or older revisions are ignored, including revision 1 arriving again after revision 2.
3. A newer record replaces its entire source payload and transcript_payload, including null values. It is not a partial field merge. Other records are unchanged. There are no deletion events in this exercise.
4. Keep candidate-derived analysis, processing state and manual routing decisions separate from source fields. A newer source revision may change the suggested route, but must not erase an explicit manual route.
5. When source text changes, refresh the analysis or mark it stale/pending review. Pre-generated analysis may be updated with human-assisted content. No automatic model call is required. Show whether an analysis reflects the current source revision.
6. Persist state across browser refresh or local application restart, according to how your app runs. Local storage or a local file is sufficient.

## Replay to demonstrate

Import 01_initial; import it again with the record order changed; manually change M07's route; import 02_update; import 01_initial again; refresh/restart. The initial import has 17 usable supplied texts; after the update there are 18. The unique record count stays 44 throughout. M07's newer text remains visible, its manual route remains in effect, and its analysis is either updated or visibly awaiting review. Other source records remain unchanged.

Your export should reflect the current state. A repeat export must not create multiple active copies of the same logical output without explanation. You may keep explicit versions if you identify which is current. If a route changes, remove the obsolete active destination or clearly mark it superseded.

## Export manifest

Use JSON or CSV. Include source_type, source_id, applied source revision, route(s), output path(s), processing/review state, manual-override flag and analysis freshness. Field names in your manifest are your choice if their meaning is clear. Output paths should be relative paths inside the export, not links to files on your own machine. Every listed active output must exist; shared daily summaries may reference multiple source records.

## Evidence references

Meeting passages: cite source_id and the 1-based turn number in the supplied turns array, or a readable record_ref plus that turn number. Timecodes may repeat and are not unique IDs. Phone-call text has no trustworthy turn timing: cite a short exact quote from the adapted text with record_ref/source_id. Do not invent speaker timecodes or talk ratios. In your interface make the supporting text findable.

## What was changed for the exercise

Identities, source IDs, emails, phone tokens, URLs, dates and commercially identifying details were transformed. Conversation text was paraphrased and abridged while retaining the underlying issues and unresolved outcomes. Transcript timecodes locate selected parts of a conversation; omitted intervals are not evidence of silence. Dates were shifted consistently by whole weeks. Duration and historical flags remain source metadata, not measurements of the shortened text.

Existing summaries and classification_reason bodies are deliberately withheld (null) so they do not provide ready-made answers. Their source processing-status fields remain as received. This particular null is an assessment redaction, not evidence of upstream failure. Other null fields retain their source meaning. Meeting redacted is set true to identify the adaptation. No playable audio/video or private source mappings are included; example.invalid URLs and PHONE tokens are non-operational.

One meeting's usable transcript is withheld from the initial delivery and supplied in the update. That delivery timing is a constructed test fixture. Other no-speech responses are preserved source conditions. Do not generalise the batch timing to how the real connector operates.
