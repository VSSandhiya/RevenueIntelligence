const state = {
  overview: null,
  filters: { person: "", source_type: "" },
  records: [],
  knownNotificationIds: new Set(),
  notificationsLoaded: false,
};

const $ = (selector) => document.querySelector(selector);
const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (character) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
}[character]));

function displayState(value) {
  const labels = {
    has_text_analysed: "Text available and checked",
    voicemail_or_unanswered_no_text: "No transcript available",
    recorded_but_silent: "Recording, no one spoke",
    not_recorded: "No recording was made",
    awaiting_late_transcript: "Waiting for transcript",
    needs_human_review: "Needs a person to check",
    not_applicable: "No transcript to assess",
    current: "Up to date",
    stale: "Needs review",
    pending: "Not checked yet",
  };
  return labels[value] || String(value || "unknown").replaceAll("_", " ");
}

function percent(value, total) {
  return total ? Math.round((value / total) * 100) : 0;
}

function updateWorkspaceDate() {
  const now = new Date();
  const dateText = new Intl.DateTimeFormat("en-IN", {
    weekday: "long",
    day: "numeric",
    month: "long",
    year: "numeric",
    timeZone: "Asia/Kolkata",
  }).format(now);
  const dateParts = new Intl.DateTimeFormat("en-CA", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    timeZone: "Asia/Kolkata",
  }).formatToParts(now);
  const dateTime = Object.fromEntries(dateParts.map((part) => [part.type, part.value]));
  const isoDate = `${dateTime.year}-${dateTime.month}-${dateTime.day}`;
  document.querySelectorAll("time[data-current-date]").forEach((element) => {
    element.textContent = dateText;
    element.dateTime = isoDate;
  });
}

function notify(message, error = false) {
  const notice = $("#notice");
  notice.hidden = !message;
  notice.textContent = message || "";
  notice.style.background = error ? "var(--coral-pale)" : "var(--green-pale)";
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  if (!response.ok) {
    let message = `Request failed (${response.status})`;
    try { message = (await response.json()).detail || message; } catch {}
    throw new Error(message);
  }
  return response.json();
}

function metricCard(label, value, denominator, tooltip) {
  return `<article class="metric-card" title="${esc(tooltip)}">
    <div class="metric-label">${esc(label)}</div>
    <div class="metric-value">${esc(value)}</div>
    <div class="metric-denom">${esc(denominator)}</div>
  </article>`;
}

function renderMetrics(metrics) {
  const coverage = metrics.usable_text_coverage;
  const connection = metrics.source_reported_connection_rate;
  const routing = metrics.routing;
  const byType = coverage.by_type;
  const reviewCount = Object.entries(metrics.review_by_intake_state)
    .filter(([key]) => key !== "has_text_analysed")
    .reduce((sum, [, value]) => sum + value, 0);
  const percentValue = coverage.selected ? `${percent(coverage.usable, coverage.selected)}%` : "—";
  const connectionValue = connection.rate === null ? "—" : `${Math.round(connection.rate * 100)}%`;
  $("#metric-grid").innerHTML = [
    metricCard("Selected records", `${metrics.selected_records.value} / ${metrics.selected_records.denominator}`, "Distinct selected identities", "Count of selected (source_type, source_id) pairs after revision rules."),
    metricCard("Usable text", percentValue, `${coverage.usable} / ${coverage.selected} selected · Calls ${byType.call.usable}/${byType.call.selected} · Meetings ${byType.meeting.usable}/${byType.meeting.selected}`, "A nonblank call transcript or at least one nonblank meeting turn. Type-specific denominators are shown."),
    metricCard("Source-reported connection", connectionValue, `${connection.connected} / ${connection.known} known · ${connection.unknown} unknown / ${connection.selected_calls} selected calls`, "True divided by calls with non-null is_connected. This is a stored source label, not transcript truth."),
    metricCard("Needs review", `${reviewCount} / ${metrics.selected_records.value}`, `${metrics.stale_analyses} / ${metrics.selected_records.value} stale · ${metrics.manual_overrides} / ${metrics.selected_records.value} manual`, "All selected records outside has_text_analysed; includes no-text, silent, late and human-review states."),
    metricCard("Route assignments", `${routing.route_assignments} / ${metrics.selected_records.value}`, `${routing.unique_records} unique · +${routing.difference} extra assignments`, "One record can have several route assignments; unique records count each identity once."),
  ].join("");
}

function renderRiskBreakdown(metrics) {
  const summary = metrics.risk_summary;
  const activity = metrics.activity_volume;
  const categories = [
    ["potential_risk", "Potential risk", "risk"],
    ["client_attention", "Needs follow-up or review", "attention"],
    ["no_risk_found", "No risk found in reviewed text", "clear"],
    ["not_enough_text", "Not enough transcript", "unknown"],
  ];
  const types = [
    ["call", "Calls", activity.calls],
    ["meeting", "Meetings", activity.meetings],
  ];
  $("#risk-breakdown-denominator").textContent = `${summary.selected} selected records · each bar is count / calls or meetings in this selection`;
  $("#risk-breakdown-chart").innerHTML = categories.map(([key, label, tone]) => `
    <div class="risk-chart-row ${tone}">
      <div class="risk-chart-category"><span class="risk-chart-dot"></span><strong>${esc(label)}</strong><span>${summary.counts[key] || 0} / ${summary.selected}</span></div>
      ${types.map(([type, typeLabel, total]) => {
        const count = summary.by_type[type][key] || 0;
        const share = total ? (count / total) * 100 : 0;
        return `<div class="risk-chart-type"><span class="risk-chart-type-name">${typeLabel}</span><span class="risk-chart-track"><i style="width:${share}%"></i></span><strong>${count} / ${total}</strong></div>`;
      }).join("")}
    </div>`).join("");
}

function renderPeople(rows, selectedTotal) {
  const max = Math.max(1, ...rows.map((row) => row.calls + row.meetings));
  $("#people-chart").innerHTML = rows.length ? rows.map((row) => {
    const total = row.calls + row.meetings;
    const callWidth = (row.calls / Math.max(1, selectedTotal)) * 100;
    const meetingWidth = (row.meetings / Math.max(1, selectedTotal)) * 100;
    return `<button class="person-row person-filter-button" data-person-filter="${esc(row.filter_value)}" aria-label="Filter selected records to ${esc(row.label)}">
      <span class="person-name">${esc(row.label)}</span>
      <span class="person-bars" title="${row.calls} calls · ${row.meetings} meetings"><i style="width:${callWidth}%"></i><i style="width:${meetingWidth}%"></i></span>
      <span class="person-count">${total} / ${selectedTotal}</span>
    </button>`;
  }).join("") : `<div class="empty-state">No people in this filter.</div>`;
  $("#people-denominator").textContent = `${selectedTotal} selected records · rep names and organizer emails are separate`;
  $("#people-chart").querySelectorAll("[data-person-filter]").forEach((button) => {
    button.addEventListener("click", () => {
      state.filters.person = button.dataset.personFilter;
      $("#person-filter").value = state.filters.person;
      loadOverview().catch((error) => notify(error.message, true));
    });
  });
}

function renderRoutes(metrics) {
  const rows = Object.entries(metrics.routing.by_route).sort((a, b) => b[1] - a[1]);
  const selected = metrics.selected_records.value;
  const items = rows.map(([route, count]) => `<div class="route-stat-line"><span class="route-name">${esc(route)}</span><span class="route-count">${count} / ${selected}</span></div>`).join("");
  const intake = Object.entries(metrics.review_by_intake_state).map(([name, count]) => `<div class="route-stat-line"><span class="route-name">${esc(displayState(name))}</span><span class="route-count">${count} / ${selected}</span></div>`).join("");
  $("#route-summary").innerHTML = `<div class="route-total"><span>Unique records</span><span>${metrics.routing.unique_records} / ${selected}</span></div>
    <div class="route-total"><span>Route assignments</span><span>${metrics.routing.route_assignments} / ${selected}</span></div>
    <div class="route-total"><span>Additional destinations</span><span>+${metrics.routing.difference} beyond ${metrics.routing.unique_records} / ${selected}</span></div>
    <div class="review-subheading">Intake states · count / ${selected} selected</div>${intake || `<div class="empty-state">No review states.</div>`}
    <div class="review-subheading">Route assignments · count / ${selected} selected</div>${items || `<div class="empty-state">No route assignments.</div>`}`;
}

function openRecord(sourceType, sourceId) {
  showRecord(sourceType, sourceId).catch((error) => notify(error.message, true));
}

function renderQuality(flags) {
  const rows = flags.flatMap((item) => item.flags.map((flag) => ({ ...item, flag })));
  $("#quality-summary").innerHTML = rows.length ? `<div class="quality-list"><div class="inbox-meta">${rows.length} flags across ${new Set(rows.map((item) => item.record_ref)).size} / ${state.overview?.selected_records || 0} selected records</div>${rows.slice(0, 7).map((item) => `<div class="quality-item"><i class="quality-bullet"></i><button data-record-type="${esc(item.source_type)}" data-record-id="${esc(item.record_ref)}">${esc(item.record_ref)}</button><span>${esc(item.flag)}</span></div>`).join("")}${rows.length > 7 ? `<div class="inbox-meta">+${rows.length - 7} more flags in record detail</div>` : ""}</div>` : `<div class="empty-state">No flags in this selection.</div>`;
  $("#quality-summary").querySelectorAll("button[data-record-id]").forEach((button) => {
    button.addEventListener("click", () => {
      const record = state.records.find((item) => item.record_ref === button.dataset.recordId);
      if (record) openRecord(record.source_type, record.source_id);
    });
  });
}

function recordRow(record) {
  const routes = record.routes.map((route) => `<span class="route-chip ${record.manual_override_flag ? "manual-chip" : ""}">${esc(route)}</span>`).join("") || "—";
  const risk = record.risk_classification || { category: "not_enough_text", label: "Not enough transcript" };
  return `<tr>
    <td><button class="record-link" data-record-type="${esc(record.source_type)}" data-record-id="${esc(record.source_id)}">${esc(record.record_ref)}</button><span class="record-title">${esc(record.title)}</span></td>
    <td>${esc(record.person || (record.source_type === "meeting" ? "Organizer not supplied" : "Rep not supplied"))}<span class="record-title">${record.source_type === "call" ? "Call rep" : "Meeting organizer"}</span></td>
    <td><span class="risk-pill risk-${esc(risk.category)}">${esc(risk.label)}</span><span class="record-title">${esc(risk.reason)}</span></td>
    <td><span class="state-pill state-${esc(record.intake_state)}">${esc(displayState(record.intake_state))}</span></td>
    <td>${routes}</td>
    <td><span class="fresh-pill fresh-${esc(record.analysis_freshness)}">${esc(displayState(record.analysis_freshness))}</span></td>
  </tr>`;
}

function renderRecords(records) {
  state.records = records;
  $("#record-table").innerHTML = records.length
    ? records.slice(0, 14).map(recordRow).join("")
    : `<tr><td colspan="6"><div class="empty-state">No records match these filters.</div></td></tr>`;
  $("#record-table").querySelectorAll("button[data-record-id]").forEach((button) => {
    button.addEventListener("click", () => openRecord(button.dataset.recordType, button.dataset.recordId));
  });
  $("#inbox-list").innerHTML = records.length ? records.map((record) => `
    <div class="inbox-row" tabindex="0" role="button" data-record-type="${esc(record.source_type)}" data-record-id="${esc(record.source_id)}">
      <span class="inbox-meta">${esc(record.record_ref)}<br>${esc(record.source_type)}</span>
      <span class="inbox-main">${esc(record.title)}<small class="record-title">${esc(displayState(record.intake_state))}${record.source_reason ? ` · ${esc(record.source_reason)}` : ""}</small></span>
      <span class="inbox-person">${esc(record.person || "Person not supplied")}<small class="record-title">${record.source_type === "call" ? "Call rep" : "Meeting organizer"}</small></span>
      <span class="inbox-routes">${record.routes.map((route) => `<i class="route-chip ${record.manual_override_flag ? "manual-chip" : ""}">${esc(route)}</i>`).join("")}</span>
      <span class="inbox-risk"><i class="risk-pill risk-${esc(record.risk_classification?.category || "not_enough_text")}">${esc(record.risk_classification?.label || "Not enough transcript")}</i></span>
      <span class="fresh-pill fresh-${esc(record.analysis_freshness)} inbox-fresh">${esc(displayState(record.analysis_freshness))}</span>
    </div>`).join("") : `<div class="empty-state">There are no records in this selection.</div>`;
  $("#inbox-list").querySelectorAll(".inbox-row").forEach((row) => {
    row.addEventListener("click", () => openRecord(row.dataset.recordType, row.dataset.recordId));
    row.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") { event.preventDefault(); openRecord(row.dataset.recordType, row.dataset.recordId); }
    });
  });
}

function renderAssistantSummary(metrics) {
  const coverage = metrics.usable_text_coverage;
  const selected = coverage.selected;
  const ratio = selected ? Math.round((coverage.usable / selected) * 100) : 0;
  const ring = $("#coverage-ring");
  ring.style.setProperty("--coverage", `${ratio}%`);
  ring.setAttribute("aria-label", `${coverage.usable} of ${selected} selected records have usable words`);
  $("#coverage-number").textContent = `${ratio}%`;
  $("#coverage-title").textContent = selected ? `${coverage.usable} of ${selected} have usable words` : "No records selected";
  $("#coverage-caption").textContent = "A transcript or at least one meeting turn with words.";
  $("#coverage-fraction").textContent = `${coverage.usable} / ${selected} records`;
  $("#coverage-bar").style.width = `${ratio}%`;

  const activity = metrics.activity_volume;
  $("#glance-volume").innerHTML = `
    <div class="volume-item"><strong>${activity.calls}</strong><span>Calls / ${selected} selected</span></div>
    <div class="volume-item"><strong>${activity.meetings}</strong><span>Meetings / ${selected} selected</span></div>`;

  const riskLabels = [
    ["potential_risk", "Potential risk", "risk"],
    ["client_attention", "Needs follow-up or review", "attention"],
    ["no_risk_found", "No risk found in reviewed text", "clear"],
    ["not_enough_text", "Not enough transcript", "unknown"],
  ];
  const risk = metrics.risk_summary;
  $("#glance-risk").innerHTML = riskLabels.map(([key, label, tone]) => {
    const count = risk.counts[key] || 0;
    const callCount = risk.by_type.call[key] || 0;
    const meetingCount = risk.by_type.meeting[key] || 0;
    const width = selected ? (count / selected) * 100 : 0;
    return `<div class="risk-summary-row ${tone}"><div class="risk-label-line"><span>${esc(label)}</span><strong>${count} / ${selected}</strong></div><div class="risk-summary-bar"><span style="width:${width}%"></span></div><small>Calls ${callCount} / ${activity.calls} · Meetings ${meetingCount} / ${activity.meetings}</small></div>`;
  }).join("");

  const stateLabels = [
    ["has_text_analysed", "Text checked and ready", "ready"],
    ["needs_human_review", "Needs a person to check", "review"],
    ["awaiting_late_transcript", "Waiting for a transcript", "waiting"],
    ["recorded_but_silent", "Recording, but no one spoke", "quiet"],
    ["not_recorded", "No recording was made", "quiet"],
    ["voicemail_or_unanswered_no_text", "No words to review", "quiet"],
  ];
  const states = metrics.review_by_intake_state;
  $("#glance-states").innerHTML = stateLabels.map(([key, label, tone]) => `
    <div class="glance-state"><span class="glance-state-dot ${tone}"></span><span>${esc(label)}</span><strong>${states[key] || 0} / ${selected}</strong></div>`).join("");
}

async function loadNotifications() {
  const result = await api("/api/notifications");
  const unseen = result.items.filter((item) => !state.knownNotificationIds.has(item.notification_id) && item.read_at === null);
  if (state.notificationsLoaded && "Notification" in window && Notification.permission === "granted") {
    unseen.forEach((item) => {
      const notice = new Notification(item.title, {
        body: item.record_ref ? `${item.record_ref} changed in revision ${item.revision}. Open the inbox to review it.` : item.message,
        tag: `revenue-inbox-${item.notification_id}`,
      });
      notice.onclick = () => {
        window.focus();
        if (item.source_type && item.source_id) openRecord(item.source_type, item.source_id);
      };
    });
  }
  state.knownNotificationIds = new Set(result.items.map((item) => item.notification_id));
  state.notificationsLoaded = true;
  $("#notification-count").textContent = `${result.unread_count} new`;
  $("#mark-notifications-read").hidden = result.unread_count === 0;
  $("#notification-feed").innerHTML = result.items.length ? result.items.map((item) => `
    <article class="notification-item ${item.read_at ? "is-read" : "is-unread"}">
      <div class="notification-title">${esc(item.title)}</div>
      <p>${esc(item.message)}</p>
      ${item.risk_classification ? `<span class="notification-risk">${esc(item.risk_classification.label)} · revision ${esc(item.applied_revision)}</span>` : `<span class="notification-risk">Batch ${esc(item.batch_id)} · revision ${esc(item.revision)}</span>`}
      ${item.record_ref ? `<button class="notification-link" data-source-type="${esc(item.source_type)}" data-source-id="${esc(item.source_id)}">Open ${esc(item.record_ref)} <span aria-hidden="true">→</span></button>` : ""}
    </article>`).join("") : `<div class="notification-empty">No input changes detected yet.</div>`;
  $("#notification-feed").querySelectorAll("button[data-source-id]").forEach((button) => {
    button.addEventListener("click", () => openRecord(button.dataset.sourceType, button.dataset.sourceId));
  });
}

function appendAssistantMessage(role, content, citations = [], mode = "", toolsUsed = []) {
  const message = document.createElement("article");
  message.className = `assistant-message ${role === "user" ? "from-user" : "from-assistant"}`;
  const body = document.createElement("p");
  body.textContent = content;
  message.append(body);
  if (mode) {
    const modeLabel = document.createElement("span");
    modeLabel.className = "assistant-answer-mode";
    modeLabel.textContent = mode;
    message.append(modeLabel);
  }
  if (toolsUsed.length) {
    const steps = document.createElement("span");
    steps.className = "assistant-answer-steps";
    steps.textContent = `What I checked: ${toolsUsed.join(" · ")}`;
    message.append(steps);
  }
  if (citations.length) {
    const list = document.createElement("div");
    list.className = "assistant-citations";
    citations.forEach((citation) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "assistant-citation";
      button.dataset.recordType = citation.source_type;
      button.dataset.recordId = citation.source_id;
      button.innerHTML = `<strong>${esc(citation.record_ref)} · ${esc(citation.title)}</strong>${citation.quote ? `<span>“${esc(citation.quote)}”</span>` : "<span>Open the source record to review its details.</span>"}<small>${citation.freshness === "stale" ? "Old analysis · check the new transcript" : "Open source and analysis"}</small>`;
      button.addEventListener("click", () => openRecord(citation.source_type, citation.source_id));
      list.append(button);
    });
    message.append(list);
  }
  const messages = $("#assistant-messages");
  messages.append(message);
  messages.scrollTo({ top: messages.scrollHeight, behavior: "smooth" });
}

async function askAssistant(question, includeQuestion = true) {
  const cleanQuestion = question.trim();
  if (!cleanQuestion) return;
  if (includeQuestion) appendAssistantMessage("user", cleanQuestion);
  const send = $("#assistant-send");
  const previousText = send.innerHTML;
  send.disabled = true;
  send.innerHTML = "<span class='spinner' aria-hidden='true'></span><span>Checking</span>";
  $("#assistant-mode").textContent = "Checking selected conversations and source evidence…";
  try {
    const result = await api("/api/assistant", {
      method: "POST",
      body: JSON.stringify({
        question: cleanQuestion,
        person: state.filters.person || null,
        source_type: state.filters.source_type || null,
      }),
    });
    const model = result.model ? `Local model: ${result.model}` : "Evidence checked · no hosted AI used";
    $("#assistant-mode").textContent = `${result.record_count} matching ${result.record_count === 1 ? "record" : "records"} · ${model}`;
    appendAssistantMessage("assistant", result.answer, result.citations, result.mode, result.tools_used);
  } catch (error) {
    $("#assistant-mode").textContent = "Assistant unavailable";
    appendAssistantMessage("assistant", `I couldn't check those records just now: ${error.message}`);
  } finally {
    send.disabled = false;
    send.innerHTML = previousText;
  }
}

async function loadOverview() {
  const query = new URLSearchParams();
  if (state.filters.person) query.set("person", state.filters.person);
  if (state.filters.source_type) query.set("source_type", state.filters.source_type);
  const data = await api(`/api/overview?${query}`);
  state.overview = data;
  state.records = data.records;
  renderMetrics(data.metrics);
  renderRiskBreakdown(data.metrics);
  renderPeople(data.metrics.activity_by_person, data.selected_records);
  renderRoutes(data.metrics);
  renderQuality(data.metrics.data_quality_flags);
  renderRecords(data.records);
  renderAssistantSummary(data.metrics);
  $("#filter-count").textContent = `${data.selected_records} selected records · activity date 21 Aug 2026`;
  const select = $("#person-filter");
  const selected = select.value;
  select.innerHTML = `<option value="">All people</option>${data.people.map((person) => `<option value="${esc(person.value)}">${esc(person.label)}</option>`).join("")}`;
  select.value = selected;
}

function evidenceReferences(record) {
  const analysis = record.analysis || {};
  return [...(analysis.evidence || []), ...(analysis.route_rules || [])];
}

function highlight(text, references) {
  let result = esc(text);
  const quotes = [...new Set(references.map((item) => item.quote || item.trigger).filter(Boolean))]
    .sort((left, right) => right.length - left.length);
  for (const quote of quotes) {
    const safeQuote = esc(quote);
    if (result.includes(safeQuote)) result = result.replace(safeQuote, `<mark>${safeQuote}</mark>`);
  }
  return result;
}

function fieldGrid(payload) {
  return Object.entries(payload).map(([key, value]) => {
    if (key === "transcript") return "";
    const formatted = value === null ? "Unknown / not supplied" : typeof value === "object" ? JSON.stringify(value) : String(value);
    return `<div class="detail-field"><div class="detail-label">${esc(key.replaceAll("_", " "))}</div><div class="detail-value">${esc(formatted)}</div></div>`;
  }).join("");
}

function renderAnalysis(record) {
  const analysis = record.analysis;
  if (!analysis) return `<div class="analysis-copy"><strong>No analysis attached</strong>${esc(record.source_reason || "No evidence-verified analysis is available for this record.")}</div>`;
  const freshness = record.analysis_freshness;
  const sections = [
    ["What happened", analysis.what_happened],
    ["Why it matters", analysis.why_it_matters],
    ["What the evidence does not establish", analysis.what_evidence_does_not_establish],
    ["Next step", analysis.next_step],
    ["Owner, only if stated", analysis.owner || "No individual owner was stated."],
  ];
  return `<span class="fresh-pill fresh-${esc(freshness)}">${esc(displayState(freshness))} · analysis source revision ${esc(analysis.source_revision)}</span>
    ${freshness === "stale" ? `<div class="analysis-copy"><strong>Review required</strong>This pre-generated analysis does not reflect the applied source revision.</div>` : ""}
    ${sections.map(([heading, copy]) => `<div class="analysis-copy"><strong>${esc(heading)}</strong>${esc(copy || "Not stated.")}</div>`).join("")}`;
}

function renderTranscript(record, references) {
  if (record.source_type === "call") {
    const text = record.payload.transcript;
    if (!text || !text.trim()) return `<div class="empty-state">No usable call transcript was supplied.</div>`;
    return `<div class="transcript"><div class="transcript-turn ${references.length ? "evidence-hit" : ""}">${highlight(text, references)}</div></div>`;
  }
  const turns = (record.transcript_payload || {}).turns || [];
  if (!turns.length) return `<div class="empty-state">${esc(record.source_reason || "No usable meeting turns were supplied.")}</div>`;
  return `<div class="transcript">${turns.map((turn, index) => {
    const number = index + 1;
    const turnEvidence = references.filter((item) => item.turn_number === number);
    return `<div class="transcript-turn ${turnEvidence.length ? "evidence-hit" : ""}"><span class="transcript-meta">Turn ${number} · ${esc(turn.timecode || "time unavailable")} · ${esc(turn.speaker || "Unknown speaker")}</span>${highlight(turn.text, turnEvidence)}</div>`;
  }).join("")}</div>`;
}

function renderRouteEditor(record) {
  const routes = state.overview?.routes || [];
  const selected = record.manual_override_flag ? record.manual_routes : record.suggested_routes;
  return `<div class="route-editor" id="route-editor">
    <div class="inbox-meta">Automatic suggestion: ${record.suggested_routes.map((route) => esc(route)).join(" · ") || "None"}</div>
    <div class="inbox-meta">Current decision: ${record.manual_override_flag ? "Manual" : "Automatic"}</div>
    <div class="route-options">${routes.map((route) => `<label class="route-option"><input type="checkbox" name="route-choice" value="${esc(route)}" ${selected.includes(route) ? "checked" : ""}><span>${esc(route)}</span></label>`).join("")}</div>
    <label for="route-note">Decision note</label><textarea id="route-note" placeholder="Why is this route appropriate?">${esc(record.manual_note || "")}</textarea>
    <label for="route-who">Reviewed by</label><input id="route-who" value="${esc(record.manual_who || "Reviewer")}">
    <div class="route-actions"><button class="button button-primary" id="save-routes">Save manual decision</button>${record.manual_override_flag ? `<button class="button button-quiet" id="reset-route">Reset to automatic</button>` : ""}</div>
  </div>`;
}

async function showRecord(sourceType, sourceId) {
  const record = await api(`/api/record/${encodeURIComponent(sourceType)}/${encodeURIComponent(sourceId)}`);
  const references = evidenceReferences(record);
  $("#drawer-kicker").textContent = `${record.record_ref} · ${record.source_type} · revision ${record.applied_revision}`;
  $("#drawer-title").textContent = record.payload.title || `${record.source_type === "call" ? "Call" : "Meeting"} ${record.record_ref}`;
  const sourceMeta = { ...record.payload, summary: record.payload.summary === null ? "Body withheld in supplied pack" : record.payload.summary, applied_revision: record.applied_revision };
  const evidence = (record.analysis?.evidence || []).map((item) => `<div class="evidence-item"><div class="evidence-ref">${record.source_type === "meeting" ? `Turn ${item.turn_number}` : record.record_ref}</div><div class="evidence-quote">“${esc(item.quote)}”</div><div class="evidence-point">${esc(item.point)}</div></div>`).join("");
  const reasonRules = (record.route_reasons || []).map((item) => `<div class="analysis-copy"><strong>${esc(item.route)}</strong><span>${esc(item.reason)}</span><br><q>${esc(item.trigger)}</q></div>`).join("");
  const flags = record.quality_flags.length ? `<ul>${record.quality_flags.map((flag) => `<li>${esc(flag)}</li>`).join("")}</ul>` : `<p class="inbox-meta">No data-quality flags for this record.</p>`;
  const revisions = record.revision_history.map((item) => `<div class="revision-row"><span>${esc(item.batch_id)}</span><span>r${item.revision}</span><strong>${esc(item.action)}</strong></div>`).join("") || `<div class="empty-state">No import history is available.</div>`;
  $("#drawer-content").innerHTML = `
    <section><div class="inbox-meta">${esc(displayState(record.intake_state))}${record.source_reason ? ` · ${esc(record.source_reason)}` : ""}</div><div class="detail-grid">${fieldGrid(sourceMeta)}</div><p class="chart-denominator">Summary status is the source processing label; the assessment pack withholds the summary body.</p></section>
    <section class="drawer-section"><h3>Transcript</h3>${renderTranscript(record, references)}</section>
    <section class="drawer-section"><h3>Analysis</h3>${renderAnalysis(record)}</section>
    <section class="drawer-section"><h3>Evidence links</h3><div class="evidence-list">${evidence || `<div class="empty-state">No substantive evidence is attached.</div>`}</div></section>
    <section class="drawer-section"><h3>Route decision</h3><div class="route-chip-row">${record.suggested_routes.map((route) => `<span class="route-chip">Automatic · ${esc(route)}</span>`).join("")}</div>${record.manual_override_flag ? `<div class="route-chip-row">${record.manual_routes.map((route) => `<span class="route-chip manual-chip">Manual · ${esc(route)}</span>`).join("")}</div><div class="analysis-copy"><strong>Manual note</strong>${esc(record.manual_note || "No note supplied.")}</div>` : ""}${reasonRules}${renderRouteEditor(record)}</section>
    <section class="drawer-section"><h3>Data-quality flags</h3>${flags}</section>
    <section class="drawer-section"><h3>Revision history</h3>${revisions}</section>`;

  $("#drawer-backdrop").hidden = false;
  $("#record-drawer").hidden = false;
  document.body.style.overflow = "hidden";
  $("#close-drawer").focus();
  $("#save-routes").addEventListener("click", async () => {
    const chosen = [...document.querySelectorAll("input[name='route-choice']:checked")].map((input) => input.value);
    try {
      await api(`/api/record/${sourceType}/${sourceId}/routes`, {
        method: "POST",
        body: JSON.stringify({ routes: chosen, note: $("#route-note").value, who: $("#route-who").value }),
      });
      notify("Manual route saved. It will stay in place through later source updates.");
      await loadOverview();
      await showRecord(sourceType, sourceId);
    } catch (error) { notify(error.message, true); }
  });
  const resetRoute = $("#reset-route");
  if (resetRoute) resetRoute.addEventListener("click", async () => {
    try {
      await api(`/api/record/${sourceType}/${sourceId}/routes/reset`, { method: "POST" });
      notify("Manual route reset to the current automatic suggestion.");
      await loadOverview();
      await showRecord(sourceType, sourceId);
    } catch (error) { notify(error.message, true); }
  });
}

function closeDrawer() {
  $("#record-drawer").hidden = true;
  $("#drawer-backdrop").hidden = true;
  document.body.style.overflow = "";
}

function setView(name) {
  document.querySelectorAll(".view-panel").forEach((view) => view.classList.toggle("is-visible", view.id === `${name}-view`));
  document.querySelectorAll(".nav-item").forEach((item) => item.classList.toggle("is-active", item.dataset.view === name));
  const titles = { assistant: "Your conversation assistant", overview: "Numbers and charts", inbox: "All conversations", brief: "Daily manager note" };
  $("#page-title").textContent = titles[name] || titles.overview;
  if (name === "brief") loadBrief().catch((error) => notify(error.message, true));
}

async function loadBrief() {
  const result = await api("/api/manager-brief");
  let markdown = esc(result.markdown);
  markdown = markdown.replace(/^# (.+)$/gm, "<h3>$1</h3>")
    .replace(/^## (.+)$/gm, "<h3>$1</h3>")
    .replace(/^- \*\*(.+?):\*\* (.+)$/gm, "<li><strong>$1:</strong> $2</li>")
    .replace(/^\d+\. (.+)$/gm, "<li>$1</li>")
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\[([^\]]+)\]\(\.\.\/records\/(\w+)\/source\.json\)/g, (_, label, reference) => `<button class="brief-evidence" data-record-ref="${reference}">${label}</button>`)
    .split(/\n\s*\n/).map((block) => block.startsWith("<h3>") || block.startsWith("<li>") ? block : block.trim() ? `<p>${block.replaceAll("\n", " ")}</p>` : "").join("");
  $("#brief-content").innerHTML = markdown;
  $("#brief-content").querySelectorAll("button[data-record-ref]").forEach((button) => {
    button.addEventListener("click", () => {
      const record = state.records.find((item) => item.record_ref === button.dataset.recordRef);
      const full = state.overview?.records.find((item) => item.record_ref === button.dataset.recordRef) || record;
      if (full) openRecord(full.source_type, full.source_id);
    });
  });
}

async function runAction(button, endpoint, successMessage) {
  button.disabled = true;
  try {
    const result = await api(endpoint, { method: "POST" });
    await loadOverview();
    await loadNotifications();
    notify(successMessage(result));
  } catch (error) { notify(error.message, true); }
  finally { button.disabled = false; }
}

document.querySelectorAll("[data-view]").forEach((button) => button.addEventListener("click", () => setView(button.dataset.view)));
const refreshSelectedRecords = () => loadOverview().then(() => {
  if ($("#assistant-view").classList.contains("is-visible")) askAssistant("What needs my attention?", false);
}).catch((error) => notify(error.message, true));
$("#person-filter").addEventListener("change", (event) => { state.filters.person = event.target.value; refreshSelectedRecords(); });
$("#type-filter").addEventListener("change", (event) => { state.filters.source_type = event.target.value; refreshSelectedRecords(); });
$("#clear-filters").addEventListener("click", () => {
  state.filters = { person: "", source_type: "" };
  $("#person-filter").value = "";
  $("#type-filter").value = "";
  refreshSelectedRecords();
});
$("#assistant-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const field = $("#assistant-question");
  const question = field.value;
  field.value = "";
  askAssistant(question);
});
document.querySelectorAll("[data-question]").forEach((button) => button.addEventListener("click", () => askAssistant(button.dataset.question)));
$("#add-data-button").addEventListener("click", () => $("#add-data-input").click());
$("#add-data-input").addEventListener("change", async (event) => {
  const file = event.currentTarget.files?.[0];
  if (!file) return;
  const button = $("#add-data-button");
  button.disabled = true;
  try {
    const delivery = JSON.parse(await file.text());
    if (!delivery || typeof delivery !== "object" || !Array.isArray(delivery.records)) {
      throw new Error("Choose a delivery JSON with a records list.");
    }
    const result = await api("/api/import", {
      method: "POST",
      body: JSON.stringify(delivery),
    });
    await loadOverview();
    await loadNotifications();
    const counts = result.actions;
    notify(`${file.name}: ${counts.new} new, ${counts.replaced} updated, ${counts["ignored-same"] + counts["ignored-older"]} unchanged. ${result.record_count} records total.`);
  } catch (error) {
    notify(error instanceof SyntaxError ? "That file is not valid JSON." : error.message, true);
  } finally {
    button.disabled = false;
    event.currentTarget.value = "";
  }
});
$("#mark-notifications-read").addEventListener("click", async () => {
  try {
    await api("/api/notifications/read", { method: "POST" });
    await loadNotifications();
  } catch (error) { notify(error.message, true); }
});
$("#enable-desktop-alerts").addEventListener("click", async () => {
  if (!("Notification" in window)) {
    notify("This browser does not support desktop notifications.", true);
    return;
  }
  const permission = await Notification.requestPermission();
  $("#enable-desktop-alerts").textContent = permission === "granted" ? "Desktop alerts enabled" : "Desktop alerts not enabled";
  notify(permission === "granted" ? "New input changes can now appear as desktop alerts." : "Browser permission was not granted.", permission !== "granted");
});
$("#demo-button").addEventListener("click", (event) => runAction(event.currentTarget, "/api/demo", (result) => `Both batches loaded. ${result.record_count} distinct records remain in view.`));
$("#export-button").addEventListener("click", (event) => runAction(event.currentTarget, "/api/export", () => "Export rebuilt from current state in exports/current/."));
$("#import-initial-button").addEventListener("click", (event) => runAction(event.currentTarget, "/api/import/initial", (result) => {
  const counts = Object.entries(result.actions).filter(([, count]) => count).map(([action, count]) => `${action}: ${count}`).join(" · ");
  $("#replay-result").textContent = `Initial batch · ${counts || "no rows"}`;
  return `Initial batch applied. ${$("#replay-result").textContent}`;
}));
$("#import-update-button").addEventListener("click", (event) => runAction(event.currentTarget, "/api/import/update", (result) => {
  const counts = Object.entries(result.actions).filter(([, count]) => count).map(([action, count]) => `${action}: ${count}`).join(" · ");
  $("#replay-result").textContent = `Update batch · ${counts || "no rows"}`;
  return `Update batch applied. ${$("#replay-result").textContent}`;
}));
$("#reset-button").addEventListener("click", async () => {
  if (!window.confirm("Reset the local database and active export? The next app start restores the post-update demo.")) return;
  try {
    await api("/api/reset", { method: "POST" });
    await loadOverview();
    notify("Local data reset. Use Load demo to restore both batches.");
  } catch (error) { notify(error.message, true); }
});
$("#close-drawer").addEventListener("click", closeDrawer);
$("#drawer-backdrop").addEventListener("click", closeDrawer);
document.addEventListener("keydown", (event) => { if (event.key === "Escape") closeDrawer(); });

loadOverview().then(async () => {
  await loadNotifications();
  await askAssistant("What needs my attention?", false);
}).catch((error) => {
  $("#error-state").hidden = false;
  $("#error-state").textContent = `The inbox could not load: ${error.message}. Check that the app is running and the candidate pack is available.`;
});
window.setInterval(() => loadNotifications().catch(() => {}), 30000);
updateWorkspaceDate();
window.setInterval(updateWorkspaceDate, 60000);