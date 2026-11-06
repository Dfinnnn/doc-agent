"""The approval gate: the USER (never the LLM) approves proposed operations,
then the code writes them and saves before/after values.
"""

import re
from datetime import datetime

from app.errors import explain, raw
from app.integrations import logbook_docx, sheets
from app.llm.draft import refine_text
from app.store.db import APPROVED, FAILED, PROPOSED, REJECTED, UNDONE, WRITTEN
from app.validation.checks import DRAFT_TEXT_FIELDS, check_draft_against_notes

DATE_FORMAT = "%d/%m/%Y"
# Structure first, so entries find their tab / week table when they are written
# Settings first (so entries in the same approval use them), then structure, then entries
ORDER = {"setting": -1, "month_tab": 0, "week_table": 1, "sheet_row": 2, "logbook_entry": 3}
SHEET_FIELDS = ["clock_in", "clock_out", "break_time", "mode",
                "tasks", "challenges", "learnings", "next_steps"]
SHEET_TEXT_FIELDS = ["tasks", "challenges", "learnings", "next_steps"]
# Issues made by check_draft_against_notes: dropped and re-checked after a refine
DRAFT_CHECK_PREFIXES = ("Possible added fact", "Possible added outcome", "First person",
                        "The notes have")


def day_of(op):
    return datetime.strptime(op["target"], DATE_FORMAT).date()


def current_value(op):
    """What is in the document now for this operation (None for new structure)."""
    if op["kind"] == "sheet_row":
        day = day_of(op)
        records = {r["date"]: r for r in sheets.read_month(day.year, day.month)}
        record = records.get(day, {})
        return {field: record.get(field, "") for field in SHEET_FIELDS}
    if op["kind"] == "logbook_entry":
        day = day_of(op)
        record = next((r for r in logbook_docx.read_logbook() if r["date"] == day), {})
        return {"date_text": record.get("date_text", ""), "activities": record.get("activities", "")}
    return None


def indent(text, width=13):
    """Line up multi-line text under the first line in the preview."""
    return str(text).replace("\n", "\n" + " " * width)


def preview_data(op):
    """What one operation would change, as data: the terminal prints it (describe),
    the web page draws it as a card.

    Returns {"fields": [{"name", "old", "new"}], "kept": [field names left as is],
             "note": extra text or None}.
    """
    proposal = op["proposal"]
    data = {"fields": [], "kept": [], "note": None}
    if op["kind"] == "sheet_row":
        try:
            before = current_value(op)
        except Exception as error:  # e.g. the tab does not exist yet
            before, data["note"] = {}, f"cannot read the current row: {explain(error)}"
        for field in SHEET_FIELDS:
            if field not in proposal or before.get(field, "") == proposal[field]:
                data["kept"].append(field)  # not changed by this operation
            else:
                data["fields"].append({"name": field, "old": before.get(field, ""),
                                       "new": proposal[field]})
    elif op["kind"] == "logbook_entry":
        try:
            old = current_value(op)["activities"]
        except Exception:
            old, data["note"] = "", "no row yet"
        mark = "• " if proposal.get("bullets", True) else ""
        data["fields"].append({"name": "activities", "old": old,
                               "new": "\n".join(f"{mark}{a}" for a in proposal["activities"])})
    elif op["kind"] == "week_table":
        data["note"] = f"add table: {proposal['header']}"
    elif op["kind"] == "month_tab":
        data["note"] = f"add tab: {op['target']}"
    elif op["kind"] == "setting":
        data["fields"].append({"name": proposal["setting"], "old": proposal["old"],
                               "new": proposal["new"]})
    return data


def describe(op):
    """A short preview of one operation for the terminal: before -> after and its issues."""
    lines = [f" #{op['id']}  {op['kind']}  {op['target']}"]
    data = preview_data(op)
    if op["kind"] == "sheet_row":
        if data["note"]:
            lines.append(f"     ({data['note']})")
        for field in data["fields"]:
            lines.append(f"     {field['name']}: {indent(field['old'] or '(empty)')}")
            lines.append(f"       ->  {indent(field['new'] or '(empty)')}")
        if data["kept"]:
            lines.append(f"     kept as is: {', '.join(data['kept'])}")
    elif op["kind"] == "logbook_entry":
        field = data["fields"][0]
        before = "(no row yet)" if data["note"] else field["old"].replace("\n", " | ") or "(empty)"
        lines.append(f"     before: {before}")
        lines.append("     after:  " + indent(field["new"]))
    elif op["kind"] in ("week_table", "month_tab"):
        lines.append(f"     {data['note']}")
    elif op["kind"] == "setting":
        field = data["fields"][0]
        scope = "new and existing entries" if op["proposal"]["apply_to"] == "all" else "new entries"
        lines.append(f"     {field['name']}: {field['old']}  ->  {field['new']}  ({scope})")
    for found in op["issues"]:
        lines.append(f"     [{found['severity']}] {found['text']}")
    return "\n".join(lines)


def ask_scope(op, input_fn):
    """Ask whether a format change is for new entries only or also existing ones.
    Returns "future" or "all"."""
    proposal = op["proposal"]
    current = "f" if proposal["apply_to"] == "future" else "a"
    while True:
        answer = input_fn(f"   #{op['id']} {proposal['setting']} -> {proposal['new']}: "
                          f"(f) new entries only / (a) also reformat existing entries "
                          f"[Enter = {current}]? ").strip().lower() or current
        if answer in ("f", "a"):
            return "future" if answer == "f" else "all"


def set_scope(store, op, apply_to):
    """Save the user's scope answer ("future" or "all") on a format change."""
    proposal = {**op["proposal"], "apply_to": apply_to}
    store.update_proposal(op["id"], proposal, op["issues"])
    return {**op, "proposal": proposal}


def apply_setting(setting, old, new, apply_to):
    """Change a logbook setting; with apply_to="all", also reformat existing entries.
    Returns (before, after)."""
    logbook_docx.set_setting(setting, new)
    reformatted = 0
    if apply_to == "all":
        wording = {old: new} if setting.endswith("_wording") else None
        reformatted = logbook_docx.reformat_entries(replace_wording=wording)
    return {"value": old}, {"value": new, "reformatted": reformatted}


def merge_text(old, new):
    """Append: keep the old text and add the new on a new line."""
    return f"{old}\n{new}" if old and new else (old or new)


def execute(op, choice):
    """Write one operation. choice is "write", "overwrite" or "append".
    Returns (before, after). Raises on failure."""
    proposal = op["proposal"]
    if op["kind"] == "month_tab":
        sheets.create_month_tab(proposal["year"], proposal["month"])
        return None, {"tab": op["target"]}

    if op["kind"] == "setting":
        return apply_setting(proposal["setting"], proposal["old"], proposal["new"],
                             proposal["apply_to"])

    if op["kind"] == "week_table":
        added = logbook_docx.add_week_table()
        if added != proposal["week"]:
            raise ValueError(f"added Week {added}, expected Week {proposal['week']}")
        return None, {"header": proposal["header"]}

    before = current_value(op)
    day = day_of(op)
    if op["kind"] == "sheet_row":
        # A field missing from the proposal is None: write_day keeps what is there
        values = {field: proposal.get(field) for field in SHEET_FIELDS}
        if choice == "append":
            for field in SHEET_TEXT_FIELDS:
                if values[field] is not None:
                    values[field] = merge_text(before[field], values[field])
        sheets.write_day(day, **values, overwrite=choice in ("overwrite", "append"))
    else:  # logbook_entry
        activities = list(proposal["activities"])
        if choice == "append":
            old = [line.lstrip("•").strip() for line in before["activities"].splitlines()]
            activities = [a for a in old if a] + activities
        logbook_docx.write_entry(day, activities, overwrite=choice in ("overwrite", "append"),
                                 bullets=proposal.get("bullets", True))
    return before, current_value(op)  # read back what is really there now


def ask_choice(op, input_fn):
    """For a conflict: overwrite, append or skip."""
    while True:
        answer = input_fn(f"   #{op['id']} {op['target']} already has content: "
                          f"(o)verwrite / (a)ppend / (s)kip? ").strip().lower()
        if answer in ("o", "a", "s"):
            return {"o": "overwrite", "a": "append", "s": "skip"}[answer]


def ask_approval(ops, input_fn):
    """Ask which operations to write. Returns (approved ids, the answer typed).

    y = all, n = none, or the #numbers shown in the preview (e.g. "1" or "1,3").
    "r 14 make it shorter" asks to refine #14 first: returns (None, (14, "make it shorter")).
    Re-asks if a number is not in the preview, so a typo never rejects silently.
    """
    ids = [op["id"] for op in ops]
    if len(ids) == 1:
        question = (f"\nApprove #{ids[0]}? (y = yes, write it / n = no / "
                    f"r = refine it first, e.g. 'r make it shorter'): ")
    else:
        numbers = ", ".join(f"#{i}" for i in ids)
        question = (f"\nApprove which? y = all, n = none, or type the numbers to write "
                    f"only some (shown as {numbers}; e.g. {ids[0]},{ids[1]}).\n"
                    f"To change a draft first: r <number> <what to change>, "
                    f"e.g. 'r {ids[-1]} make it shorter': ")
    while True:
        answer = input_fn(question).strip()
        lower = answer.lower()
        if lower == "y":
            return set(ids), answer
        if lower == "n":
            return set(), answer
        # "r all <change>", "r 50 <change>", "r 50, 51 <change>"
        refine = re.fullmatch(r"r\s+(all|#?\d+(?:\s*,\s*#?\d+)*)\s+(.+)", answer, re.IGNORECASE)
        if refine:
            which = refine.group(1).lower()
            targets = ids if which == "all" else [int(n) for n in re.findall(r"\d+", which)]
            if all(t in ids for t in targets):
                return None, (targets, refine.group(2))
        refine_one = re.fullmatch(r"r\s+(\D.*)", answer, re.IGNORECASE)
        if refine_one and len(ids) == 1:
            return None, (ids, refine_one.group(1))
        parts = lower.replace("#", "").replace(" ", "").split(",")
        if parts and all(part.isdigit() and int(part) in ids for part in parts):
            return {int(part) for part in parts}, answer
        print(f"   Please type y, n, numbers from this list: {', '.join(map(str, ids))}, "
              f"or r <number> <what to change>")


def draft_text(op):
    """The draft text of a proposal, as shown when refining (empty if it has none)."""
    proposal = op["proposal"]
    if op["kind"] == "logbook_entry":
        mark = "• " if proposal.get("bullets", True) else ""
        return "\n".join(f"{mark}{a}" for a in proposal["activities"])
    if op["kind"] == "sheet_row":
        return "\n".join(f"{f}: {proposal[f]}" for f in SHEET_TEXT_FIELDS if proposal.get(f))
    return ""


def refine_op(store, op, instruction):
    """Rewrite a proposed draft following the user's instruction. Nothing is written.
    Returns a message saying what changed."""
    proposal = dict(op["proposal"])
    if op["kind"] == "sheet_row":
        fields = {f: proposal[f] for f in SHEET_TEXT_FIELDS if f in proposal}
    elif op["kind"] == "logbook_entry" and proposal.get("bullets", True):
        fields = {"activities": proposal["activities"]}
    else:
        fields = {}
    if not fields:
        return f"#{op['id']} has no draft text to refine."
    facts = proposal.get("source", "")
    new, changes = refine_text(fields, facts, instruction)
    proposal.update(new)
    # Re-run the draft checks against the facts plus anything the instruction states
    draft = {f: "" for f in DRAFT_TEXT_FIELDS} | {"activities": []} | new
    found = check_draft_against_notes(f"{facts}\n{instruction}", draft)
    issues = [i for i in op["issues"]
              if not i["text"].startswith(DRAFT_CHECK_PREFIXES)]
    issues += [{"text": f["what"], "severity": "warn"} for f in found]
    store.update_proposal(op["id"], proposal, issues)
    return f"#{op['id']} refined: {changes or 'rewritten as asked'}"


def pending_ops(store, run_id):
    """This run's proposed operations, in the order they would be written."""
    return sorted(store.operations(run_id, PROPOSED), key=lambda o: (ORDER[o["kind"]], o["id"]))


def needs_choice(op):
    """The extra question an approved operation needs before it is written:
    "scope" (format change: new entries only, or also existing ones),
    "conflict" (the date already has content: overwrite / append / skip), or None."""
    if any(i["severity"] == "block" for i in op["issues"]):
        return None  # it will not be written anyway
    if op["kind"] == "setting" and op["target"] != "signed_up_to_week":
        return "scope"  # the scope says: always ask this for a format change
    # A refine of an existing entry is an intended overwrite: no need to ask again
    if not op["proposal"].get("overwrite") and any(i["severity"] == "conflict"
                                                   for i in op["issues"]):
        return "conflict"
    return None


CHOICES = {"scope": ("future", "all"), "conflict": ("overwrite", "append", "skip")}
# Changes that save the .docx (a setting may reformat existing entries)
LOGBOOK_KINDS = ("logbook_entry", "week_table", "setting")


def decide(store, run_id, chosen, choices, record, answer=None):
    """Write the approved operations of a run and reject the rest.

    chosen:  the ids to write.
    choices: {id: answer} for each approved operation that needs_choice():
             "future" / "all" for a format change (missing = keep what was proposed),
             "overwrite" / "append" / "skip" for a conflict (required).
    Everything is checked before anything is written, so a bad request writes nothing.
    Returns one line per operation saying what happened (empty if none were waiting).
    """
    ops = pending_ops(store, run_id)
    if not ops:
        return []
    ids = {op["id"] for op in ops}
    chosen = {int(i) for i in chosen}
    choices = {int(k): v for k, v in (choices or {}).items()}  # JSON keys arrive as text
    if not chosen <= ids:
        raise ValueError(f"not in this preview: {sorted(chosen - ids)}; waiting: {sorted(ids)}")
    for op in ops:
        question = needs_choice(op) if op["id"] in chosen else None
        given = choices.get(op["id"])
        if question == "conflict" and given is None:
            raise ValueError(f"#{op['id']} already has content: choose overwrite, append or skip")
        if question and given is not None and given not in CHOICES[question]:
            raise ValueError(f"#{op['id']}: '{given}' is not one of {', '.join(CHOICES[question])}")
    # The logbook open in Word would fail halfway (Sheet rows written, logbook not):
    # check before the first write, so a locked file means nothing is written at all
    if any(op["id"] in chosen and op["kind"] in LOGBOOK_KINDS for op in ops):
        try:
            logbook_docx.check_writable()
        except OSError as error:
            raise ValueError(f"Nothing was written. {explain(error)}") from error
    record("decision", {"answer": answer, "approved": sorted(chosen), "choices": choices})

    results = []
    for op in ops:
        if op["id"] not in chosen:
            store.update_operation(op["id"], REJECTED)
            results.append(f"#{op['id']} rejected, not written")
            continue
        if any(i["severity"] == "block" for i in op["issues"]):
            store.update_operation(op["id"], REJECTED, error="blocked: cannot be approved")
            results.append(f"#{op['id']} skipped: it has a block issue")
            continue
        question = needs_choice(op)
        if question == "scope":
            op = set_scope(store, op, choices.get(op["id"], op["proposal"]["apply_to"]))
        choice = "overwrite" if op["proposal"].get("overwrite") else "write"
        if question == "conflict":
            choice = choices[op["id"]]
            if choice == "skip":
                store.update_operation(op["id"], REJECTED, error="skipped by user")
                results.append(f"#{op['id']} skipped by you, not written")
                continue
        store.update_operation(op["id"], APPROVED)
        try:
            before, after = execute(op, choice)
        except Exception as error:
            store.update_operation(op["id"], FAILED, error=raw(error))
            record("write_failed", {"operation": op["id"], "error": raw(error)})
            results.append(f"#{op['id']} FAILED, nothing confirmed: {explain(error)}")
            continue
        store.update_operation(op["id"], WRITTEN, before=before, after=after)
        record("write", {"operation": op["id"], "choice": choice, "before": before, "after": after})
        results.append(f"#{op['id']} {op['kind']} {op['target']} written ({choice})")
    return results


def refine_ops(store, run_id, ids, instruction, record, show=None):
    """Rewrite waiting drafts of a run following the user's instruction. Nothing is written.

    Returns one item per draft: {"id", "message", "before", "now", "issues"}
    (before / now are the draft text). show(item), if given, is called as each
    draft is done, so a long "r all" shows progress.
    """
    waiting = {op["id"]: op for op in pending_ops(store, run_id)}
    ids = [int(i) for i in ids]
    if not ids or not set(ids) <= set(waiting):
        raise ValueError(f"not in this preview: {sorted(set(ids) - set(waiting)) or 'none given'}; "
                         f"waiting: {sorted(waiting)}")
    items = []
    for op_id in ids:
        op = waiting[op_id]
        try:
            message = refine_op(store, op, instruction)
        except Exception as error:
            message = f"#{op_id} could not be refined: {explain(error)}"
            record("error", {"operation": op_id, "error": raw(error)})
        record("refine", {"operation": op_id, "instruction": instruction, "result": message})
        new_op = store.operation(op_id)
        item = {"id": op_id, "message": message, "before": draft_text(op),
                "now": draft_text(new_op), "issues": new_op["issues"]}
        items.append(item)
        if show:
            show(item)
    return items


def print_refined(item):
    """Terminal view of one refined draft: old vs new, then its issues."""
    print(f"\n   {item['message']}")
    if item["before"]:
        print(f"     draft before:  {indent(item['before'], 20)}")
        print(f"     draft now:     {indent(item['now'], 20)}")
    for found in item["issues"]:
        print(f"     [{found['severity']}] {found['text']}")


def review(store, run_id, record, input_fn=input):
    """Terminal approval: show this run's proposed operations, ask the user,
    then let decide() write what they approve.
    Returns one line per operation saying what happened (empty if none)."""
    ops = pending_ops(store, run_id)
    if not ops:
        return []
    print("\nProposed changes (nothing is written yet):")
    for op in ops:
        print(describe(op))
    record("preview", {"operations": [op["id"] for op in ops]})

    while True:
        chosen, answer = ask_approval(ops, input_fn)
        if chosen is not None:
            break
        targets, instruction = answer  # refine drafts, show old vs new, then ask again
        refine_ops(store, run_id, targets, instruction, record, show=print_refined)
        ops = pending_ops(store, run_id)

    # Ask the extra questions first, then decide() checks everything and writes
    choices = {}
    for op in ops:
        question = needs_choice(op) if op["id"] in chosen else None
        if question == "scope":
            choices[op["id"]] = ask_scope(op, input_fn)
        elif question == "conflict":
            choices[op["id"]] = ask_choice(op, input_fn)
    results = decide(store, run_id, chosen, choices, record, answer)
    for line in results:
        print(f"   {line}")
    return results


def undo(store, op_id, record):
    """Put an operation's before value back. Returns a message for the user."""
    op = store.operation(op_id)
    if op is None:
        return f"There is no operation #{op_id}."
    if op["status"] != WRITTEN:
        return f"#{op_id} is '{op['status']}', not written, so there is nothing to undo."
    if op["kind"] in ("month_tab", "week_table"):
        return f"#{op_id} added structure ({op['target']}); remove it by hand if needed."
    if op["kind"] == "setting":
        p = op["proposal"]
        if logbook_docx.get_setting(p["setting"]) != p["new"]:
            return f"#{op_id} was changed again later, so it is not undone."
        try:
            apply_setting(p["setting"], p["new"], p["old"], p["apply_to"])
        except Exception as error:
            record("undo_failed", {"operation": op_id, "error": raw(error)})
            return f"Undo of #{op_id} FAILED, nothing confirmed: {explain(error)}"
        store.update_operation(op_id, UNDONE)
        record("undo", {"operation": op_id, "restored": p["old"]})
        return f"#{op_id} undone: {p['setting']} is back to {p['old']}."

    # Safety: only undo if the document still holds exactly what we wrote
    now = current_value(op)
    if now != op["after"]:
        return (f"#{op_id} was changed after it was written, so it is not undone "
                f"(it would overwrite the newer edit).")

    before, day = op["before"], day_of(op)
    try:
        if op["kind"] == "sheet_row":
            sheets.write_day(day, **before, overwrite=True)
        else:
            logbook_docx.restore_entry(day, before["date_text"], before["activities"])
        restored = current_value(op)
    except Exception as error:
        record("undo_failed", {"operation": op_id, "error": raw(error)})
        return f"Undo of #{op_id} FAILED, nothing confirmed: {explain(error)}"
    if restored != before:
        record("undo_failed", {"operation": op_id, "now": restored, "expected": before})
        return f"Undo of #{op_id} ran, but the result does not match the old value. Please check it."
    store.update_operation(op_id, UNDONE)
    record("undo", {"operation": op_id, "restored": before})
    return f"#{op_id} undone: {op['target']} is back to its old value."


def format_events(events):
    """The activity log of one run, one line per event."""
    lines = []
    for event in events:
        data = event["data"]
        time = event["at"][11:]
        kind = event["kind"]
        if kind == "llm_call":
            tokens = data.get("tokens") or {}
            chosen = ", ".join(data["tools_chosen"]) or "answer"
            detail = (f"{data['model']}, {data['seconds']}s, "
                      f"{tokens.get('input', '?')} in / {tokens.get('output', '?')} out -> {chosen}")
        elif kind == "tool_call":
            detail = f"{data['name']}({data['arguments']})"
        elif kind == "tool_result":
            detail = data["result"][:120]
        elif kind in ("user_message", "answer"):
            detail = data["text"][:120].replace("\n", " ")
        else:
            detail = str(data)[:160]
        lines.append(f"{time}  {kind:<12} {detail}")
    return "\n".join(lines)
