# Five-slide outline

## 1. The manager problem and findings

- A selected extract can look more certain than it is: 44 unique records, 18 usable texts after update, with no representative whole-team denominator.
- C10 says: “No. I said I will contact you if there is anything to discuss.” The source outcome says `follow_up`; this drives a Sales coaching route, Needs human review, and a quality flag in the record detail.
- C02 says: “Her voicemail would be fine, thank you.” The source says `is_conversation=true`; the dashboard flags the mismatch and the detail view directs review instead of counting a buyer discussion.
- M04 turn 11: “moving from prepaid to credit is the main issue for us.” M04 turn 21 leaves accounting feasibility open; this creates separate retention, product/support and human-review destinations.

## 2. Interface choices and omissions

- A local-time daily workspace leads with distinct records, transcript coverage, source-reported connection, review and routing.
- Person filters label call reps and meeting organisers separately. Activity is shown as counts, never as performance rankings.
- Record detail keeps source fields, transcript, evidence, analysis freshness, automatic routes and manual routes visible together.
- A plain-language assistant starts the workflow, links answers to verified source evidence, and can optionally use a localhost model to phrase a draft. It does not make route decisions.
- The manager home separates calls from meetings, groups source-backed potential risk and review needs, and keeps an alert history when an input revision changes.
- No forecast, pipeline currency, inferred account matching, talk ratio, audio player or public CRM sync: the supplied fields cannot support them.

## 3. Routing and folder design

- Exact phrase rules save an explicit reason and the source passage that triggered each destination. Meetings cite 1-based turns; calls cite exact quotes.
- A multi-route record gets an analysis/evidence copy in every active route folder, plus one canonical source folder and relative link.
- M04 is the hard decision: prepaid is not credit; the bank feed is settlement-delayed, available credit is not shown, and accounting feasibility is unresolved. It routes to Customer success / retention risk, Product or support issue and Needs human.
- Re-export replaces `exports/current/`; the manifest contains every current path and marks its route source automatic or manual.

## 4. How a key number and conclusion were checked

- Coverage is 18 / 44 = 40.9% after revision 2: 10 calls / 30 and 8 meetings / 14. Usable means nonblank call transcript or at least one nonblank meeting turn.
- The import test applies 44 initial identities, reverses and reimports the same batch, applies the one-record update, reapplies revision 1, and reopens SQLite. Final log: 44 new, 1 replaced, 87 ignored-same, 1 ignored-older.
- M04's conclusion is checked against turn 11 for the credit requirement and turn 21 for unresolved accounting feasibility. The source itself, not a title, supports the multiple routes.

## 5. Trade-offs, AI, limitations and next steps

- Analyses are pre-generated and all citations are programmatically checked; retrieval and calculations are local. Optional Ollama wording runs on localhost, uses no API key, and falls back to an evidence guide when unavailable. No training is done on the 18 unlabelled texts.
- Rejected: trusting stored labels/titles as routes. This is cheaper, but misroutes C10's explicit refusal, C02's receptionist transfer and internal/vendor activity. The trade-off is more Needs human review when literal evidence is insufficient.
- Risks: abridged text, no call timing, no staff directory, no account/opportunity table, one fixed activity date.
- The file watcher and alerts require an always-on process; the in-app alert feed does not send email or SMS. No training set is needed for 18 unlabelled conversations.
- Next: record the narrated walkthrough; in a production setting, add authenticated reviewer identities, audit retention policy, and a human workflow for approving revised analyses.