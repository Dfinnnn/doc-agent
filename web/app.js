// Document assistant page. It only calls the /api endpoints; the server's code
// (the same as the terminal's) does all checking and writing.

const $ = (id) => document.getElementById(id);
const page = { busy: false, waitingRun: null };

// ---- Talking to the server ----

async function api(path, body) {
  const options = body === undefined ? {} : {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  };
  const response = await fetch(path, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    // FastAPI puts our error text in "detail"
    const detail = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail);
    throw new Error(detail || `${response.status} ${response.statusText}`);
  }
  return data;
}

// ---- Formatting the agent's answers ----
// A small subset of markdown: headings, bullets, numbered lists, ---, **bold**, `code`.
// It builds page elements directly (never innerHTML), so answer text can't inject HTML.

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function addInline(parent, text) {
  // Split on **bold** and `code`; everything else stays plain text
  for (const part of text.split(/(\*\*[^*]+\*\*|`[^`]+`)/)) {
    if (part.startsWith("**") && part.endsWith("**") && part.length > 4) {
      parent.append(el("strong", null, part.slice(2, -2)));
    } else if (part.startsWith("`") && part.endsWith("`") && part.length > 2) {
      parent.append(el("code", null, part.slice(1, -1)));
    } else if (part) {
      parent.append(document.createTextNode(part));
    }
  }
}

function formatAnswer(text) {
  const box = el("div", "answer");
  let list = null;       // the <ul>/<ol> being filled
  let paragraph = null;  // the <p> being filled
  for (const line of text.split("\n")) {
    const heading = line.match(/^#{1,6}\s+(.*)/);
    const bullet = line.match(/^(\s*)[*•-]\s+(.*)/);
    const numbered = line.match(/^\s*\d+[.)]\s+(.*)/);
    if (!line.trim()) { paragraph = null; continue; }  // a blank line inside a list keeps the list
    if (/^\s*(-{3,}|\*{3,})\s*$/.test(line)) { box.append(el("hr")); list = paragraph = null; continue; }
    if (heading) {
      const h = el("h3"); addInline(h, heading[1]); box.append(h);
      list = paragraph = null;
    } else if (bullet || numbered) {
      const tag = bullet ? "ul" : "ol";
      const indent = bullet ? bullet[1].length : 0;
      if (!list || list.tagName.toLowerCase() !== tag) {
        list = el(tag); box.append(list);
        list.dataset.indent = indent;  // the first bullet's indent is the list's own level
      }
      const nested = indent >= Number(list.dataset.indent) + 2;
      const item = el("li", nested ? "nested" : null);
      addInline(item, bullet ? bullet[2] : numbered[1]);
      list.append(item);
      paragraph = null;
    } else {
      if (!paragraph) { paragraph = el("p"); box.append(paragraph); }
      else paragraph.append(el("br"));
      addInline(paragraph, line.trim());
      list = null;
    }
  }
  return box;
}

// ---- Chat ----

const clockTime = () => new Date().toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });

// One journal entry: the time in the margin, then the text
function addMessage(who, content) {
  const item = el("li", `msg ${who}`);
  const body = el("div", "body");
  if (who === "agent" || who === "working") {
    body.append(el("div", "who", "Assistant"));
    body.append(typeof content === "string" ? formatAnswer(content) : content);
  } else {
    body.textContent = content;  // your words and app notes stay plain text
  }
  item.append(el("span", "time", clockTime()), body);
  $("messages").append(item);
  item.scrollIntoView({ block: "end", behavior: "smooth" });
  return item;
}

function updateComposer() {
  const input = $("chat-input");
  const locked = page.busy || page.waitingRun !== null;
  input.disabled = locked;
  $("send").disabled = locked;
  input.placeholder = page.busy ? "Working…"
    : page.waitingRun !== null ? "Decide on the preview first"
    : "Write an order, e.g. log update 05/11: …";
  $("status").textContent = page.busy ? "working" : "ready";
  $("status").className = page.busy ? "busy" : "";
}

// Names of the agent's tools, as shown in the live steps
const STEP_NAMES = {
  read_sheet: "Read the Sheet", read_logbook: "Read the logbook",
  search_records: "Search the records", audit: "Run the audit",
  log_update: "Draft the entries", sync: "Prepare the sync",
  refine_entry: "Rewrite the entries", summarize: "Write the summary",
  change_setting: "Prepare the setting change",
  write_sheet_rows: "Prepare Sheet rows", write_logbook_entries: "Prepare logbook entries",
  create_month_tab: "Prepare a month tab", add_week_table: "Prepare week tables",
};

// The "working" bubble: bouncing dots, the steps so far, and a seconds counter
function startWorking(runBefore) {
  const box = el("div", "working-box");
  const dots = el("div", "dots");
  dots.append(el("span"), el("span"), el("span"));
  const steps = el("ul", "steps");
  const clock = el("div", "clock", "0s");
  box.append(dots, steps, clock);
  const bubble = addMessage("working", box);
  bubble.setAttribute("aria-busy", "true");

  const started = Date.now();
  const tick = setInterval(() => {
    clock.textContent = `${Math.round((Date.now() - started) / 1000)}s`;
  }, 1000);

  // Ask the server what the run has done so far, once a second
  const poll = setInterval(async () => {
    try {
      const now = await api("/api/progress");
      if (now.run_id === null || now.run_id === runBefore) return;  // not started yet
      showSteps(steps, now.steps);
    } catch { /* the answer itself will report real errors */ }
  }, 1000);

  return () => { clearInterval(tick); clearInterval(poll); bubble.remove(); };
}

function showSteps(list, events) {
  const rows = [];
  for (const event of events) {
    if (event.kind === "tool_call") {
      rows.push({ text: STEP_NAMES[event.name] || event.name, done: false });
    } else if (event.kind === "tool_result") {
      const open = rows.find((row) => !row.done);  // results come back in call order
      if (open) open.done = true;
    } else if (event.kind === "error") {
      rows.push({ text: "Stopped by an error", done: true, error: true });
    }
  }
  // Update rows in place and add only new ones, so old rows don't re-animate each poll
  rows.forEach((row, i) => {
    const item = list.children[i] || list.appendChild(el("li"));
    item.className = row.error ? "error" : row.done ? "done" : "now";
    item.textContent = row.text;
  });
}

async function send(text) {
  addMessage("you", text);
  page.busy = true;
  updateComposer();
  let runBefore = null;
  try { runBefore = (await api("/api/progress")).run_id; } catch { /* steps just won't show */ }
  const stopWorking = startWorking(runBefore);
  try {
    const reply = await api("/api/chat", { text });
    stopWorking();
    if (reply.kind === "answer") addMessage("agent", reply.text);
    else addMessage(reply.kind === "error" ? "app error" : "app", reply.text);
    showProposals(reply.run_id, reply.proposals);
  } catch (error) {
    stopWorking();
    addMessage("app error", error.message);
  } finally {
    page.busy = false;
    updateComposer();
    refreshActivity();
    if (!$("chat-input").disabled) $("chat-input").focus();
  }
}

$("chat-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const text = $("chat-input").value.trim();
  if (!text || page.busy) return;
  $("chat-input").value = "";
  growInput();
  send(text);
});

// Enter sends, Shift+Enter makes a new line; the box grows with the text
const input = $("chat-input");
function growInput() {
  input.style.height = "auto";
  input.style.height = `${Math.min(input.scrollHeight, 160)}px`;
}
input.addEventListener("input", growInput);
input.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    $("chat-form").requestSubmit();
  }
});

// Example orders fill the box; you finish and send them yourself
for (const chip of document.querySelectorAll(".chip")) {
  chip.addEventListener("click", () => {
    input.value = chip.dataset.text;
    growInput();
    input.focus();
    input.setSelectionRange(input.value.length, input.value.length);
  });
}

// ---- Preview: one card per proposed change; the server decides and writes ----

const preview = {
  ops: [],              // the waiting changes, as /api sends them
  selected: new Set(),  // ids ticked to write
  choices: {},          // id -> "overwrite" / "append" / "skip" / "future" / "all"
  refines: {},          // id -> {instruction, before, now, message} of the latest refine
  refining: null,       // id of the card whose refine box is open, or "all"
  acting: false,        // a refine or write is running
  error: "",
};

const KIND_NAMES = {
  sheet_row: "Work log", logbook_entry: "Logbook", setting: "Setting",
  week_table: "New week table", month_tab: "New month tab",
};
const FIELD_NAMES = {
  clock_in: "Clock-in", clock_out: "Clock-out", break_time: "Break", mode: "Mode",
  tasks: "Tasks", challenges: "Challenges", learnings: "Learnings", next_steps: "Next steps",
  activities: "Activities",
};
const CHOICE_NAMES = {
  conflict: [["overwrite", "Overwrite"], ["append", "Append"], ["skip", "Skip"]],
  scope: [["future", "New entries only"], ["all", "Also reformat existing"]],
};

const blocked = (op) => op.issues.some((issue) => issue.severity === "block");

function showProposals(runId, proposals) {
  const before = new Set(preview.ops.map((op) => op.id));
  page.waitingRun = proposals.length ? runId : null;
  preview.ops = proposals;
  for (const op of proposals) {
    if (before.has(op.id)) continue;  // keep your ticks and choices across a refine
    if (!blocked(op)) preview.selected.add(op.id);
    if (op.needs === "scope") preview.choices[op.id] = op.apply_to;
  }
  if (!proposals.length) {
    preview.selected.clear(); preview.choices = {}; preview.refines = {};
    preview.refining = null; preview.error = "";
  }
  $("tab-preview").textContent = proposals.length ? `Preview (${proposals.length})` : "Preview";
  renderPreview();
  if (proposals.length) selectTab("preview");
  updateComposer();
}

function renderPreview() {
  const panel = $("panel-preview");
  if (!preview.ops.length) {
    panel.replaceChildren(el("p", "panel-empty", "Proposed changes show up here."));
    return;
  }
  const count = [...preview.selected].length;
  const top = el("div", "preview-top");
  top.append(el("span", "muted", `${count} of ${preview.ops.length} selected · nothing written yet`));
  const links = el("span", "muted");
  links.append(linkButton("Select all", () => {
    for (const op of preview.ops) if (!blocked(op)) preview.selected.add(op.id);
    renderPreview();
  }), " · ", linkButton("Select none", () => { preview.selected.clear(); renderPreview(); }));
  top.append(links);

  const list = el("div", "cards");
  for (const op of preview.ops) list.append(card(op));

  panel.replaceChildren(top, list, actionBar(count));
}

function linkButton(text, onClick) {
  const button = el("button", "link", text);
  button.type = "button";
  button.disabled = preview.acting;
  button.addEventListener("click", onClick);
  return button;
}

// The left margin of a preview row: the day large, like a logbook date
function dateMargin(target) {
  const margin = el("div", "margin");
  const match = target.match(/^(\d{2})\/(\d{2})\/(\d{4})$/);
  if (!match) { margin.append(el("span", "margin-text", target)); return margin; }
  const day = new Date(Number(match[3]), Number(match[2]) - 1, Number(match[1]));
  const month = day.toLocaleDateString("en-GB", { month: "short" });
  const weekday = day.toLocaleDateString("en-GB", { weekday: "short" });
  margin.append(el("b", null, match[1]), el("span", null, `${month} · ${weekday}`));
  return margin;
}

function card(op) {
  const box = el("article", blocked(op) ? "card blocked" : "card");
  box.id = `card-${op.id}`;
  box.append(dateMargin(op.target));
  const content = el("div", "card-body");
  box.append(content);

  // Header: tick box, which document, number, Refine
  const head = el("div", "card-head");
  const pick = el("input");
  pick.type = "checkbox";
  pick.id = `pick-${op.id}`;
  pick.checked = preview.selected.has(op.id);
  pick.disabled = blocked(op) || preview.acting;
  pick.addEventListener("change", () => {
    if (pick.checked) preview.selected.add(op.id); else preview.selected.delete(op.id);
    renderPreview();
  });
  const title = el("label", "card-title", KIND_NAMES[op.kind] || op.kind);
  title.htmlFor = pick.id;
  head.append(pick, title, el("span", "muted", `#${op.id}`));
  if (op.kind === "sheet_row" || op.kind === "logbook_entry") {
    const refine = el("button", "link", "Refine");
    refine.type = "button";
    refine.disabled = preview.acting;
    refine.addEventListener("click", () => {
      preview.refining = preview.refining === op.id ? null : op.id;
      renderPreview();
      document.querySelector(`#card-${op.id} .refine-box input`)?.focus();
    });
    head.append(el("span", "grow"), refine);
  }
  content.append(head);

  // Old -> new, per field
  for (const field of op.fields) {
    const row = el("div", "field");
    if (op.fields.length > 1 || op.kind === "sheet_row" || op.kind === "setting") {
      row.append(el("span", "field-name", FIELD_NAMES[field.name] || field.name));
    }
    const values = el("div", "values");
    values.append(field.old ? el("div", "old", field.old) : el("div", "old empty", "(empty)"),
                  el("div", "new", field.new || "(empty)"));
    row.append(values);
    content.append(row);
  }
  if (op.note) content.append(el("p", "muted note", op.note));
  if (op.kept.length) {
    content.append(el("p", "muted note", `Kept: ${op.kept.map((f) => (FIELD_NAMES[f] || f).toLowerCase()).join(", ")}`));
  }

  // Issues as one short line, then the choice this change needs
  const issues = op.issues.filter((issue) => issue.severity !== "conflict");
  if (issues.length) {
    const flags = el("p", "flags");
    issues.forEach((issue, i) => {
      if (i) flags.append(" · ");
      const text = issue.severity === "block" ? `Can't be approved: ${issue.text}` : issue.text;
      flags.append(el("span", issue.severity, text));
    });
    content.append(flags);
  }
  if (op.needs) content.append(choiceGroup(op));

  if (preview.refines[op.id]) content.append(refineChanges(preview.refines[op.id]));
  if (preview.refining === op.id) content.append(refineBox([op.id], "Rewrite"));
  return box;
}

// ---- What a refine changed: the previous draft vs the new one, word by word ----

function wordDiff(before, now) {
  // Words and the spaces between them, so line breaks survive
  const a = before.split(/(\s+)/).filter(Boolean);
  const b = now.split(/(\s+)/).filter(Boolean);
  if (a.length * b.length > 250000) return null;  // too long to compare quickly
  // Longest common subsequence table, filled from the end
  const same = Array.from({ length: a.length + 1 }, () => new Uint16Array(b.length + 1));
  for (let i = a.length - 1; i >= 0; i--) {
    for (let j = b.length - 1; j >= 0; j--) {
      same[i][j] = a[i] === b[j] ? same[i + 1][j + 1] + 1 : Math.max(same[i + 1][j], same[i][j + 1]);
    }
  }
  // Walk the table: kept words stay plain, removed ones are <del>, added ones <ins>
  const parts = [];
  const push = (kind, text) => {
    const last = parts[parts.length - 1];
    if (last && last.kind === kind) last.text += text; else parts.push({ kind, text });
  };
  let i = 0, j = 0;
  while (i < a.length && j < b.length) {
    if (a[i] === b[j]) { push("same", a[i]); i++; j++; }
    else if (same[i + 1][j] >= same[i][j + 1]) push("del", a[i++]);
    else push("ins", b[j++]);
  }
  while (i < a.length) push("del", a[i++]);
  while (j < b.length) push("ins", b[j++]);
  return parts;
}

function refineChanges(refine) {
  const box = el("div", "changes");
  const head = el("div", "changes-head");
  head.append(el("strong", null, `Refined: "${refine.instruction}"`), el("span", "muted", "vs previous draft"));
  box.append(head);
  const parts = refine.before && refine.now ? wordDiff(refine.before, refine.now) : null;
  const text = el("div", "changes-text");
  if (parts && parts.some((part) => part.kind !== "same")) {
    for (const part of parts) {
      text.append(part.kind === "same" ? document.createTextNode(part.text)
                                       : el(part.kind, null, part.text));
    }
    box.append(text);
  } else if (refine.before && refine.before === refine.now) {
    box.append(el("p", "muted", "No change to the draft."));
  } else if (refine.before) {  // too long for a word-by-word view
    text.append(el("del", null, refine.before), document.createTextNode("\n"), el("ins", null, refine.now));
    box.append(text);
  }
  box.append(el("p", "muted changes-note", refine.message));
  return box;
}

function choiceGroup(op) {
  const group = el("div", "choice");
  group.setAttribute("role", "radiogroup");
  if (op.needs === "conflict") group.append(el("span", "flags conflict", "Already has content"));
  const segments = el("div", "segments");
  for (const [value, text] of CHOICE_NAMES[op.needs]) {
    const label = el("label", "segment");
    const radio = el("input");
    radio.type = "radio";
    radio.name = `choice-${op.id}`;
    radio.value = value;
    radio.checked = preview.choices[op.id] === value;
    radio.disabled = preview.acting;
    radio.addEventListener("change", () => {
      preview.choices[op.id] = value;
      preview.error = "";
      renderPreview();
      document.querySelector(`input[name="choice-${op.id}"][value="${value}"]`)?.focus();
    });
    label.append(radio, text);
    segments.append(label);
  }
  group.append(segments);
  return group;
}

function refineBox(ids, buttonText) {
  const form = el("form", "refine-box");
  const label = el("label", "sr-only", "What to change");
  const field = el("input");
  field.id = `refine-${ids.length > 1 ? "all" : ids[0]}`;
  label.htmlFor = field.id;
  field.placeholder = "What to change, e.g. make it shorter";
  field.disabled = preview.acting;
  const go = el("button", "small", preview.acting ? "Rewriting…" : buttonText);
  go.disabled = preview.acting;
  const cancel = el("button", "small", "Cancel");
  cancel.type = "button";
  cancel.disabled = preview.acting;
  cancel.addEventListener("click", () => { preview.refining = null; renderPreview(); });
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    if (field.value.trim()) refine(ids, field.value.trim());
    else field.focus();
  });
  field.addEventListener("keydown", (event) => {
    if (event.key === "Escape") { preview.refining = null; renderPreview(); }
  });
  form.append(label, field, go, cancel);
  return form;
}

function actionBar(count) {
  const bar = el("div", "actions");
  if (preview.refining === "all") {
    const drafts = preview.ops.filter((op) => op.kind === "sheet_row" || op.kind === "logbook_entry");
    bar.append(refineBox(drafts.map((op) => op.id), "Rewrite all"));
  }
  if (preview.error) bar.append(el("p", "action-error", preview.error));
  const row = el("div", "action-row");
  const refineAll = el("button", "link", "Refine all");
  refineAll.type = "button";
  refineAll.disabled = preview.acting;
  refineAll.addEventListener("click", () => {
    preview.refining = preview.refining === "all" ? null : "all";
    renderPreview();
    $("refine-all")?.focus();
  });
  const reject = el("button", null, "Reject all");
  reject.disabled = preview.acting;
  reject.addEventListener("click", () => decideNow([]));
  const write = el("button", "primary",
    preview.acting ? "Working…" : `Write ${count} change${count === 1 ? "" : "s"}`);
  write.disabled = preview.acting;
  write.addEventListener("click", writeSelected);
  const right = el("div", "action-right");
  right.append(reject, write);
  row.append(refineAll, right);
  bar.append(row);
  return bar;
}

function writeSelected() {
  const chosen = preview.ops.filter((op) => preview.selected.has(op.id));
  if (!chosen.length) {
    preview.error = "Tick at least one change, or use Reject all.";
    renderPreview();
    return;
  }
  const missing = chosen.find((op) => op.needs === "conflict" && !preview.choices[op.id]);
  if (missing) {
    preview.error = `#${missing.id} already has content: choose overwrite, append or skip.`;
    renderPreview();
    $(`card-${missing.id}`).scrollIntoView({ block: "center", behavior: "smooth" });
    document.querySelector(`input[name="choice-${missing.id}"]`)?.focus();
    return;
  }
  decideNow(chosen.map((op) => op.id));
}

async function decideNow(ids) {
  const choices = {};
  for (const id of ids) if (preview.choices[id]) choices[id] = preview.choices[id];
  preview.acting = true; preview.error = "";
  renderPreview();
  try {
    const result = await api(`/api/runs/${page.waitingRun}/decide`, { approved: ids, choices });
    addMessage("app", result.results.join("\n"));
    showProposals(null, []);
    if (result.next_waiting !== null) await showWaiting(result.next_waiting);
  } catch (error) {
    preview.error = error.message;  // nothing was written: decide checks before writing
  } finally {
    preview.acting = false;
    renderPreview();
    updateComposer();
    refreshActivity();
  }
}

async function refine(ids, instruction) {
  preview.acting = true; preview.error = "";
  renderPreview();
  try {
    const result = await api(`/api/runs/${page.waitingRun}/refine`, { ids, instruction });
    for (const item of result.refined) preview.refines[item.id] = { ...item, instruction };
    preview.refining = null;
    showProposals(page.waitingRun, result.proposals);
  } catch (error) {
    preview.error = error.message;
  } finally {
    preview.acting = false;
    renderPreview();
  }
}

// ---- Activity: recent runs, each run's timeline, and undo ----

const activity = { runs: [], open: null, detail: null, error: "" };

const STATUS_NAMES = { proposed: "waiting", written: "written", undone: "undone",
                       rejected: "rejected", failed: "failed", approved: "approved" };
const UNDOABLE = ["sheet_row", "logbook_entry", "setting"];

async function loadActivity() {
  try {
    activity.runs = (await api("/api/runs?limit=30")).runs;
    if (activity.open !== null) activity.detail = await api(`/api/runs/${activity.open}/events`);
    activity.error = "";
  } catch (error) {
    activity.error = `Can't load the activity: ${error.message}`;
  }
  renderActivity();
}

function renderActivity() {
  const panel = $("panel-activity");
  const top = el("div", "preview-top");
  top.append(el("span", "muted", "Newest first"));
  const buttons = el("span", "action-right");
  const undoLast = el("button", "small", "Undo last");
  undoLast.addEventListener("click", openUndoLast);
  const refresh = el("button", "small", "Refresh");
  refresh.addEventListener("click", loadActivity);
  buttons.append(undoLast, refresh);
  top.append(buttons);

  const list = el("ol", "runs");
  for (const run of activity.runs) list.append(runRow(run));
  const parts = [top];
  if (activity.error) parts.push(el("p", "action-error", activity.error));
  parts.push(list);
  panel.replaceChildren(...parts);
}

function runRow(run) {
  const item = el("li", "run-item");
  const row = el("button", "run");
  row.setAttribute("aria-expanded", activity.open === run.id);
  row.append(el("strong", null, `#${run.id}`), el("span", "muted", run.started_at.slice(11, 16)),
             el("span", "run-message", run.message));
  const pills = el("span", "pills");
  const statuses = Object.entries(run.statuses);
  if (!statuses.length) pills.append(el("span", "pill none", "no changes"));
  for (const [status, count] of statuses) {
    pills.append(el("span", `pill ${status}`, `${STATUS_NAMES[status] || status} ${count}`));
  }
  row.append(pills);
  row.addEventListener("click", async () => {
    activity.open = activity.open === run.id ? null : run.id;
    activity.detail = null;
    if (activity.open !== null) await loadActivity(); else renderActivity();
    document.querySelector(`.run-item:nth-child(${activity.runs.indexOf(run) + 1}) .run`)?.focus();
  });
  item.append(row);
  if (activity.open === run.id && activity.detail) item.append(runDetail(activity.detail));
  return item;
}

function runDetail(detail) {
  const box = el("div", "run-detail");
  const timeline = el("ol", "timeline");
  for (const event of detail.events) {
    const [label, text] = describeEvent(event);
    const line = el("li", `event ${event.kind}`);
    line.append(el("span", "muted", event.at.slice(11)), el("span", "event-kind", label), el("span", "event-text", text));
    timeline.append(line);
  }
  box.append(timeline);
  for (const op of detail.operations) {
    const row = el("div", "op-row");
    const name = el("span");
    name.append(`#${op.id} · ${KIND_NAMES[op.kind] || op.kind} · ${op.target} `,
                el("span", `pill ${op.status}`, STATUS_NAMES[op.status] || op.status));
    row.append(name);
    if (op.status === "written" && UNDOABLE.includes(op.kind)) {
      const undoButton = el("button", "small", "Undo");
      undoButton.addEventListener("click", () => openUndo([op]));
      row.append(undoButton);
    } else if (op.status === "written") {
      row.append(el("span", "muted", "remove by hand if needed"));
    }
    box.append(row);
  }
  return box;
}

// One activity-log event -> [label, text]
function describeEvent(event) {
  const d = event.data;
  const ids = (list) => list.map((id) => `#${id}`).join(", ");
  switch (event.kind) {
    case "user_message": return ["message", d.text];
    case "answer": return ["answer", d.text.length > 160 ? `${d.text.slice(0, 160)}…` : d.text];
    case "llm_call": {
      const tokens = d.tokens || {};
      const chose = d.tools_chosen.length ? d.tools_chosen.map((t) => STEP_NAMES[t] || t).join(", ") : "answer";
      return ["model", `${d.model} · ${d.seconds}s · ${tokens.input ?? "?"} in / ${tokens.output ?? "?"} out → ${chose}`];
    }
    case "tool_call": return ["tool", STEP_NAMES[d.name] || d.name];
    case "tool_result": return ["result", d.result.length > 120 ? `${d.result.slice(0, 120)}…` : d.result];
    case "preview": return ["preview", ids(d.operations)];
    case "refine": return ["refine", `#${d.operation} "${d.instruction}"`];
    case "decision": return ["decision", d.approved.length ? `approved ${ids(d.approved)}` : "none approved"];
    case "write": return ["write", `#${d.operation} (${d.choice})`];
    case "write_failed": return ["failed", `#${d.operation}: ${d.error}`];
    case "undo": return ["undo", `#${d.operation} back to its old value`];
    case "undo_failed": return ["failed", `undo of #${d.operation}: ${d.error || "result did not match"}`];
    case "error": return ["error", d.error];
    case "limit": return ["limit", `${d.rounds} tool rounds used`];
    default: return [event.kind, JSON.stringify(d).slice(0, 160)];
  }
}

// ---- Undo: list exactly what goes back, then ask ----

let undoIds = [];

function openUndo(ops) {
  undoIds = ops.map((op) => op.id);
  $("undo-title").textContent = `Undo ${ops.length} change${ops.length === 1 ? "" : "s"}?`;
  $("undo-list").replaceChildren(...ops.map((op) =>
    el("li", null, `#${op.id} · ${KIND_NAMES[op.kind] || op.kind} · ${op.target}`)));
  $("undo-error").hidden = true;
  $("undo-confirm").textContent = `Undo ${ops.length}`;
  $("undo-confirm").disabled = false;
  $("undo-dialog").showModal();
}

async function openUndoLast() {
  try {
    const { operations } = await api("/api/undo/preview");
    if (!operations.length) {
      activity.error = "There are no written changes to undo.";
      renderActivity();
      return;
    }
    openUndo(operations);
  } catch (error) {
    activity.error = error.message;
    renderActivity();
  }
}

$("undo-confirm").addEventListener("click", async () => {
  $("undo-confirm").disabled = true;
  $("undo-confirm").textContent = "Undoing…";
  try {
    const result = await api("/api/undo", { ids: undoIds });  // exactly the listed changes
    $("undo-dialog").close();
    addMessage("app", result.results.join("\n"));
    loadActivity();
  } catch (error) {
    $("undo-error").textContent = error.message;
    $("undo-error").hidden = false;
    $("undo-confirm").disabled = false;
    $("undo-confirm").textContent = "Try again";
  }
});

// ---- Tabs (click, or Left / Right arrow keys) ----

const tabs = ["preview", "activity"];

function selectTab(name) {
  for (const tab of tabs) {
    const selected = tab === name;
    $(`tab-${tab}`).setAttribute("aria-selected", selected);
    $(`tab-${tab}`).tabIndex = selected ? 0 : -1;
    $(`panel-${tab}`).hidden = !selected;
  }
  if (name === "activity") loadActivity();
}

// Keep the Activity tab current after anything that adds to the log
function refreshActivity() {
  if (!$("panel-activity").hidden) loadActivity();
}

for (const tab of tabs) {
  $(`tab-${tab}`).addEventListener("click", () => selectTab(tab));
  $(`tab-${tab}`).addEventListener("keydown", (event) => {
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
    const next = tabs[(tabs.indexOf(tab) + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length];
    selectTab(next);
    $(`tab-${next}`).focus();
  });
}

// Changes left waiting by an earlier order (a reload, a restart, the terminal)
async function showWaiting(runId) {
  const waiting = await api(`/api/runs/${runId}/proposals`);
  const run = (await api("/api/runs?limit=50")).runs.find((r) => r.id === runId);
  let order = run ? run.message.split("\n")[0] : "";
  if (order.length > 60) order = `${order.slice(0, 60)}…`;
  addMessage("app", `Run #${runId}${order ? ` ("${order}")` : ""} still has changes waiting. Decide on them in Preview.`);
  showProposals(waiting.run_id, waiting.proposals);
}

// ---- Start: model name, and any preview still waiting from before a reload ----

async function start() {
  try {
    $("today").textContent = new Date().toLocaleDateString("en-GB",
      { weekday: "long", day: "numeric", month: "long" });
    const health = await api("/api/health");
    $("model").textContent = health.model;
    $("logbook-file").textContent = health.logbook_file;
    renderPreview();
    const runs = await api("/api/runs?limit=1");
    if (runs.waiting_run !== null) await showWaiting(runs.waiting_run);
  } catch (error) {
    addMessage("app error", `Can't reach the server: ${error.message}`);
  }
  updateComposer();
}

start();
