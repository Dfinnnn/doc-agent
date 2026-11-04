"""The approval gate: the USER (never the LLM) approves proposed operations,
then the code writes them and saves before/after values.
"""

from datetime import datetime

from app.integrations import logbook_docx, sheets
from app.store.db import APPROVED, FAILED, PROPOSED, REJECTED, UNDONE, WRITTEN

DATE_FORMAT = "%d/%m/%Y"
# Structure first, so entries find their tab / week table when they are written
ORDER = {"month_tab": 0, "week_table": 1, "sheet_row": 2, "logbook_entry": 3}
SHEET_FIELDS = ["clock_in", "clock_out", "break_time", "mode",
                "tasks", "challenges", "learnings", "next_steps"]
SHEET_TEXT_FIELDS = ["tasks", "challenges", "learnings", "next_steps"]


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


def describe(op):
    """A short preview of one operation: before -> after and its issues."""
    lines = [f" #{op['id']}  {op['kind']}  {op['target']}"]
    proposal = op["proposal"]
    if op["kind"] == "sheet_row":
        try:
            before = current_value(op)
        except Exception as error:  # e.g. the tab does not exist yet
            before = {"(cannot read)": str(error)}
        for field in SHEET_FIELDS:
            old, new = before.get(field, ""), proposal.get(field, "")
            if old or new:
                lines.append(f"     {field}: {old or '(empty)'}  ->  {new or '(empty)'}")
    elif op["kind"] == "logbook_entry":
        try:
            before = current_value(op)["activities"].replace("\n", " | ") or "(empty)"
        except Exception:
            before = "(no row yet)"
        lines.append(f"     before: {before}")
        lines.append("     after:  " + " | ".join(f"• {a}" for a in proposal["activities"]))
    elif op["kind"] == "week_table":
        lines.append(f"     add table: {proposal['header']}")
    elif op["kind"] == "month_tab":
        lines.append(f"     add tab: {op['target']}")
    for found in op["issues"]:
        lines.append(f"     [{found['severity']}] {found['text']}")
    return "\n".join(lines)


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

    if op["kind"] == "week_table":
        added = logbook_docx.add_week_table()
        if added != proposal["week"]:
            raise ValueError(f"added Week {added}, expected Week {proposal['week']}")
        return None, {"header": proposal["header"]}

    before = current_value(op)
    day = day_of(op)
    if op["kind"] == "sheet_row":
        values = {field: proposal.get(field, "") for field in SHEET_FIELDS}
        if choice == "append":
            for field in SHEET_TEXT_FIELDS:
                values[field] = merge_text(before[field], values[field])
        sheets.write_day(day, **values, overwrite=choice in ("overwrite", "append"))
    else:  # logbook_entry
        activities = list(proposal["activities"])
        if choice == "append":
            old = [line.lstrip("•").strip() for line in before["activities"].splitlines()]
            activities = [a for a in old if a] + activities
        logbook_docx.write_entry(day, activities, overwrite=choice in ("overwrite", "append"))
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
    Re-asks if a number is not in the preview, so a typo never rejects silently.
    """
    ids = [op["id"] for op in ops]
    if len(ids) == 1:
        question = f"\nApprove #{ids[0]}? (y = yes, write it / n = no): "
    else:
        numbers = ", ".join(f"#{i}" for i in ids)
        question = (f"\nApprove which? y = all, n = none, or type the numbers to write "
                    f"only some (shown as {numbers}; e.g. {ids[0]},{ids[1]}): ")
    while True:
        answer = input_fn(question).strip().lower()
        if answer == "y":
            return set(ids), answer
        if answer == "n":
            return set(), answer
        parts = answer.replace("#", "").replace(" ", "").split(",")
        if parts and all(part.isdigit() and int(part) in ids for part in parts):
            return {int(part) for part in parts}, answer
        print(f"   Please type y, n, or numbers from this list: {', '.join(map(str, ids))}")


def review(store, run_id, record, input_fn=input):
    """Show this run's proposed operations, ask the user, write what they approve."""
    ops = store.operations(run_id, PROPOSED)
    if not ops:
        return
    ops.sort(key=lambda op: (ORDER[op["kind"]], op["id"]))
    print("\nProposed changes (nothing is written yet):")
    for op in ops:
        print(describe(op))
    record("preview", {"operations": [op["id"] for op in ops]})

    chosen, answer = ask_approval(ops, input_fn)
    record("decision", {"answer": answer, "approved": sorted(chosen)})

    for op in ops:
        if op["id"] not in chosen:
            store.update_operation(op["id"], REJECTED)
            print(f"   #{op['id']} rejected, not written")
            continue
        if any(i["severity"] == "block" for i in op["issues"]):
            store.update_operation(op["id"], REJECTED, error="blocked: cannot be approved")
            print(f"   #{op['id']} skipped: it has a block issue")
            continue
        choice = "write"
        if any(i["severity"] == "conflict" for i in op["issues"]):
            choice = ask_choice(op, input_fn)
            if choice == "skip":
                store.update_operation(op["id"], REJECTED, error="skipped by user")
                continue
        store.update_operation(op["id"], APPROVED)
        try:
            before, after = execute(op, choice)
        except Exception as error:
            message = f"{type(error).__name__}: {error}"
            store.update_operation(op["id"], FAILED, error=message)
            record("write_failed", {"operation": op["id"], "error": message})
            print(f"   #{op['id']} FAILED, nothing confirmed: {message}")
            continue
        store.update_operation(op["id"], WRITTEN, before=before, after=after)
        record("write", {"operation": op["id"], "choice": choice, "before": before, "after": after})
        print(f"   #{op['id']} written ({choice})")


def undo(store, op_id, record):
    """Put an operation's before value back. Returns a message for the user."""
    op = store.operation(op_id)
    if op is None:
        return f"There is no operation #{op_id}."
    if op["status"] != WRITTEN:
        return f"#{op_id} is '{op['status']}', not written, so there is nothing to undo."
    if op["kind"] in ("month_tab", "week_table"):
        return f"#{op_id} added structure ({op['target']}); remove it by hand if needed."

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
        message = f"{type(error).__name__}: {error}"
        record("undo_failed", {"operation": op_id, "error": message})
        return f"Undo of #{op_id} FAILED, nothing confirmed: {message}"
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
