# Three-minute walkthrough narration

**0:00–0:25 | Ask the assistant**

“This is the Revenue Intelligence Inbox for the selected 21 August extract, not a whole-team day. I’ll ask what needs attention first. The assistant checks the selected records, points to source evidence, and does not make routing decisions. The manager view separates 30 calls and 14 meetings, then shows which conversations may need risk review. Eighteen of 44 records have usable text.”

**0:25–0:55 | Read the simple numbers**

“The visual summary explains which records have words and which need review. On the Numbers screen, connection rate is explicitly source-reported: true calls divided by calls with a known connection value, with unknown values shown separately. It is not the same as a transcript-based conversation count.”

**0:55–1:35 | Inspect M04**

“Here is M04, Cedarwave. The key point is not just that they are discussing a card migration. In turn 11 the customer says moving from prepaid to credit is the main issue. In turn 21 the team still needs to confirm whether the bank-card path can reconcile into accounting. That is why this has a retention route, a product/support route and a human-review route. The transcript also says the replacement is still prepaid, so the two options stay separate.”

**1:35–2:20 | Repeat the initial delivery and correct M07**

“I’ll load both batches now; the action is idempotent. To show the replay more clearly, I’ll use the command after this recording: first import the initial file, then import it again with its row order reversed. The count stays at 44. Before the update, I manually set M07 to Product or support issue and Needs human, with a note that the issues are unresolved.”

**2:20–2:50 | Apply the update**

“The revision-2 delivery replaces M07’s full source payload and transcript, rather than adding another meeting. Its text now has 27 turns and the total usable coverage changes from 17 to 18. The analysis is current only because its source revision matches; if the text changes again, the analysis becomes stale until reviewed.”

**2:50–3:00 | Show alerts, persistence and exports**

“When a higher revision changes a source file, the always-on local watcher refreshes analysis state and adds a manager alert. Here I reapply revision 1; it cannot replace the newer transcript or my manual route, and it creates no duplicate alert. The import log records the ignored older revision and the export manifest points to the current route folders.”