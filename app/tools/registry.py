"""The agent's action set: each tool's schema (what the LLM reads) and the
Python function that really runs it.

Read tools run directly. Write tools only propose a change (added in the next step).
"""

import json
from datetime import date, datetime, timedelta

from app.integrations import logbook_docx, sheets
from app.llm.draft import draft_log_update, draft_sync, refine_text, summarize_days
from app.validation import audit as audit_module
from app.validation.checks import (
    FIRST_DAY, LEAVE_WORDING, check_draft_against_notes, date_in_notes,
)

DATE_FORMAT = "%d/%m/%Y"
MAX_DAYS = 62  # longest range one read may cover, to keep replies small


def parse_date(text):
    return datetime.strptime(text, DATE_FORMAT).date()


def parse_range(date_from, date_to=None):
    start = parse_date(date_from)
    end = parse_date(date_to) if date_to else start
    if end < start:
        raise ValueError("date_to is before date_from")
    if (end - start).days >= MAX_DAYS:
        raise ValueError(f"Range is longer than {MAX_DAYS} days; ask for a shorter range")
    return start, end


def months_between(start, end):
    """(year, month) for every month the range touches."""
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        yield year, month
        year, month = (year, month + 1) if month < 12 else (year + 1, 1)


def compact(record):
    """Drop empty fields and format the date, so replies stay short."""
    out = {"date": f"{record['date']:{DATE_FORMAT}}"}
    out.update({k: v for k, v in record.items() if k != "date" and v not in ("", None)})
    return out


# ---- Read tools ----

def read_sheet(date_from, date_to=None):
    start, end = parse_range(date_from, date_to)
    days, problems = [], []
    for year, month in months_between(start, end):
        try:
            records = sheets.read_month(year, month)
        except Exception as error:  # missing tab, broken headers...
            problems.append(f"{sheets.tab_name(year, month)}: {error}")
            continue
        days += [compact(r) for r in records if start <= r["date"] <= end]
    return {"days": days, "problems": problems}


def read_logbook(date_from, date_to=None):
    start, end = parse_range(date_from, date_to)
    records = [r for r in logbook_docx.read_logbook() if start <= r["date"] <= end]
    weeks = len(logbook_docx.find_week_tables(logbook_docx.open_logbook()))
    return {
        "days": [compact({"date": r["date"], "week": r["week"], "activities": r["activities"]})
                 for r in records],
        "note": f"The logbook has week tables up to Week {weeks}; later dates have no rows yet.",
    }


def search_records(text):
    needle = text.lower()
    today = date.today()
    matches = []
    for year, month in months_between(FIRST_DAY, today):
        try:
            records = sheets.read_month(year, month)
        except Exception:
            continue
        for r in records:
            fields = (r["tasks"], r["challenges"], r["learnings"], r["next_steps"])
            if any(needle in field.lower() for field in fields):
                matches.append(compact(r))
    return {"matches": matches[:20], "total": len(matches)}


def audit():
    today = date.today()
    sheet_findings, sheet_records = audit_module.audit_sheet(today)
    logbook_findings, logbook_records = audit_module.audit_logbook(today)
    findings = (sheet_findings + logbook_findings
                + audit_module.check_consistency(sheet_records, logbook_records, today))
    return {"findings": findings, "total": len(findings)}


# ---- Write tools: they only PROPOSE. Nothing is written until Part 8 adds approval. ----

NOT_WRITTEN = "not written: shown to the user for approval"
SHEET_TEXT_FIELDS = ["tasks", "challenges", "learnings", "next_steps"]

# Issue severities: "block" cannot be approved; "conflict" needs overwrite/append/skip;
# "warn" is shown but can be approved.
BLOCK, CONFLICT, WARN = "block", "conflict", "warn"

# Set by the agent loop for each user message, so proposals are saved to the store
CONTEXT = {"store": None, "run_id": None}


def set_context(store, run_id):
    CONTEXT.update(store=store, run_id=run_id)


def issue(text, severity):
    return {"text": text, "severity": severity}


SHEET_VALUE_FIELDS = ["clock_in", "clock_out", "break_time", "mode"] + SHEET_TEXT_FIELDS


def empty_change_issues(kind, proposal):
    """A change that would write nothing (or only a date) can never be approved."""
    if kind == "logbook_entry" and not any(str(a).strip() for a in proposal.get("activities", [])):
        return [issue("no activities to write", BLOCK)]
    if kind == "sheet_row" and not any(proposal.get(f) for f in SHEET_VALUE_FIELDS):
        return [issue("nothing to write: no tasks, Mode or times", BLOCK)]
    return []


def save_proposal(kind, target, proposal, issues):
    """Save one proposed change as an operation; returns its id (None if no store)."""
    if CONTEXT["store"] is None:
        return None
    issues = issues + empty_change_issues(kind, proposal)
    if kind == "logbook_entry" and logbook_docx.signed_week(parse_date(target)):
        issues = issues + [issue("this week is signed: the .docx will no longer match the "
                                 "signed PDF", WARN)]
    return CONTEXT["store"].add_operation(CONTEXT["run_id"], kind, target, proposal, issues)


def is_time(text):
    try:
        datetime.strptime(text, "%H:%M")
        return True
    except (TypeError, ValueError):
        return False


def write_sheet_rows(rows):
    tabs = {tab["title"] for tab in sheets.list_tabs()}
    current = {}  # existing records per (year, month), read once
    proposals = []
    for row in rows:
        issues = []
        day = parse_date(row["date"])
        tab = sheets.tab_name(day.year, day.month)
        if row.get("mode") not in sheets.PROFILE["modes"]:
            issues.append(issue(f"Mode must be one of {sheets.PROFILE['modes']}", BLOCK))
        for field in ("clock_in", "clock_out", "break_time"):
            if not is_time(row.get(field)):
                issues.append(issue(f"{field} must be HH:MM", BLOCK))
        if day > date.today():
            issues.append(issue("date is in the future", WARN))
        if tab not in tabs:
            issues.append(issue(f"tab {tab} does not exist yet; create_month_tab must be approved too", WARN))
        else:
            key = (day.year, day.month)
            if key not in current:
                current[key] = {r["date"]: r for r in sheets.read_month(*key)}
            existing = current[key].get(day, {})
            if any(existing.get(field) for field in SHEET_TEXT_FIELDS):
                issues.append(issue("this day already has entries", CONFLICT))
        op = save_proposal("sheet_row", row["date"], row, issues)
        proposals.append({"operation": op, "row": row, "tab": tab, "issues": issues})
    return {"proposed": "write_sheet_rows", "changes": proposals, "status": NOT_WRITTEN}


def create_month_tab(year, month):
    title = sheets.tab_name(year, month)
    exists = title in {tab["title"] for tab in sheets.list_tabs()}
    issues = [issue(f"{title} already exists", BLOCK)] if exists else []
    op = save_proposal("month_tab", title, {"year": year, "month": month}, issues)
    return {"proposed": "create_month_tab", "operation": op, "tab": title,
            "issues": issues, "status": NOT_WRITTEN}


def write_logbook_entries(entries):
    doc = logbook_docx.open_logbook()
    weeks = len(logbook_docx.find_week_tables(doc))
    existing = {r["date"]: r for r in logbook_docx.read_logbook(doc)}
    proposals = []
    for entry in entries:
        issues = []
        day = parse_date(entry["date"])
        week = (day - logbook_docx.WEEK1_MONDAY).days // 7 + 1
        if day.weekday() >= 5:
            issues.append(issue("weekend work: an extra row will be added to this week's table", WARN))
        if week > weeks:
            issues.append(issue(f"Week {week} table does not exist yet; add_week_table must be approved too", WARN))
        elif existing.get(day, {}).get("activities"):
            issues.append(issue("this day already has an entry", CONFLICT))
        if day > date.today():
            issues.append(issue("date is in the future", WARN))
        op = save_proposal("logbook_entry", entry["date"], entry, issues)
        proposals.append({"operation": op, "entry": entry, "week": week, "issues": issues})
    return {"proposed": "write_logbook_entries", "changes": proposals, "status": NOT_WRITTEN}


def add_week_table(up_to_week):
    weeks = len(logbook_docx.find_week_tables(logbook_docx.open_logbook()))
    if up_to_week <= weeks:
        return {"proposed": "add_week_table", "weeks": [],
                "issues": [issue(f"Week {up_to_week} already exists", BLOCK)], "status": NOT_WRITTEN}
    new = []
    for week in range(weeks + 1, up_to_week + 1):  # one operation per week, in order
        header = logbook_docx.week_header(week)
        op = save_proposal("week_table", f"Week {week}", {"week": week, "header": header}, [])
        new.append({"operation": op, "week": week, "header": header})
    return {"proposed": "add_week_table", "weeks": new, "issues": [], "status": NOT_WRITTEN}


# ---- Commands (Part 9): the code drafts and proposes; the LLM only passes the notes ----

def propose_week_tables(up_to_week):
    """Propose any missing week tables up to this week (once per run). Returns op ids."""
    weeks = len(logbook_docx.find_week_tables(logbook_docx.open_logbook()))
    already = {op["proposal"]["week"] for op in CONTEXT["store"].operations(CONTEXT["run_id"])
               if op["kind"] == "week_table"} if CONTEXT["store"] else set()
    ops = []
    for week in range(weeks + 1, up_to_week + 1):
        if week not in already:
            ops.append(save_proposal("week_table", f"Week {week}",
                                     {"week": week, "header": logbook_docx.week_header(week)}, []))
    return ops


def log_update(notes):
    today = date.today()
    drafts = draft_log_update(notes, today)
    logbook = {r["date"]: r for r in logbook_docx.read_logbook()}
    months = {}
    summary = []
    skipped = []
    for draft in drafts:
        day = parse_date(draft["date"])
        leave = LEAVE_WORDING.get(draft["mode"]) if day.weekday() < 5 else None
        has_text = draft["activities"] or any(draft[f] for f in SHEET_TEXT_FIELDS)
        has_values = any(draft[f] for f in ("mode", "clock_in", "clock_out", "break_time"))
        if not has_text and not has_values:
            # Code decides, not the LLM: no tasks, Mode or times means nothing to propose
            skipped.append({"date": draft["date"], "reason": "the notes give no tasks, Mode "
                            "or times for this date, so nothing was proposed"})
            continue
        found = check_draft_against_notes(draft["source_notes"] or notes, draft)
        drafted = [issue(f["what"], WARN) for f in found]
        if day > today:
            drafted.append(issue("date is in the future", WARN))
        if not date_in_notes(day, notes, today):
            drafted.append(issue(f"{draft['date']} is not a date in your notes (day and month "
                                 "swapped?): check the date", BLOCK))

        # --- Sheet row: text from the draft; times and Mode only if the notes gave them ---
        key = (day.year, day.month)
        if key not in months:
            try:
                months[key] = {r["date"]: r for r in sheets.read_month(*key)}
            except Exception as error:
                months[key] = error
        sheet_issues = list(drafted)
        source = draft["source_notes"] or notes
        row = {"date": draft["date"], "source": source}
        for field in ("tasks", "challenges", "learnings", "next_steps"):
            if draft[field]:
                row[field] = draft[field]
        for field in ("mode", "clock_in", "clock_out", "break_time"):
            if draft[field]:
                row[field] = draft[field]
        if isinstance(months[key], Exception):
            sheet_issues.append(issue(f"{sheets.tab_name(*key)} cannot be read: {months[key]}", BLOCK))
            existing = {}
        else:
            existing = months[key].get(day, {})
            if any(existing.get(f) for f in SHEET_TEXT_FIELDS):
                sheet_issues.append(issue("this day already has entries", CONFLICT))
            if "mode" not in row and existing.get("mode") in ("", sheets.WEEKEND_MODE):
                sheet_issues.append(issue(
                    f"Mode is '{existing.get('mode') or 'empty'}' and the notes do not say "
                    "WFO/WFH: tell me the Mode", WARN))
            if "clock_in" not in row and existing.get("clock_in") in ("", "00:00:00"):
                sheet_issues.append(issue("no clock-in time in the Sheet or the notes: tell me "
                                          "the times", WARN))
        if draft["missing"]:
            sheet_issues.append(issue("not in the notes, left as is: " + ", ".join(
                f for f in draft["missing"] if f in SHEET_TEXT_FIELDS + ["mode"]), WARN))
        sheet_op = save_proposal("sheet_row", draft["date"], row, sheet_issues)

        # --- Logbook entry (plus any missing week tables) ---
        if draft["activities"]:
            entry = {"date": draft["date"], "activities": draft["activities"], "source": source}
        elif leave:  # a leave / holiday day: the profile's plain wording, as sync writes it
            entry = {"date": draft["date"], "activities": [leave], "bullets": False, "source": source}
        else:
            entry = None  # e.g. only times or Mode WFO given: nothing to say in the logbook
        log_issues = list(drafted)
        week_ops, log_op = [], None
        if entry:
            week = (day - logbook_docx.WEEK1_MONDAY).days // 7 + 1
            week_ops = propose_week_tables(week)
            if day.weekday() >= 5:
                log_issues.append(issue("weekend work: an extra row will be added to this "
                                        "week's table", WARN))
            if logbook.get(day, {}).get("activities"):
                log_issues.append(issue("this day already has an entry", CONFLICT))
            log_op = save_proposal("logbook_entry", draft["date"], entry, log_issues)

        summary.append({"date": draft["date"], "sheet_operation": sheet_op,
                        "logbook_operation": log_op, "week_table_operations": week_ops,
                        "missing": draft["missing"],
                        "issues": [i["text"] for i in sheet_issues + log_issues]})
    result = {"proposed": "log_update", "days": summary, "status": NOT_WRITTEN}
    if skipped:
        result["not_proposed"] = skipped
    return result


SYNC_BATCH = 8  # days per LLM call when rewriting for the logbook


def sync(date_from=None, date_to=None):
    """Sheet (master) -> logbook: every day the Sheet has but the logbook does not."""
    today = date.today()
    start = parse_date(date_from) if date_from else FIRST_DAY
    end = min(parse_date(date_to), today) if date_to else today
    logbook = {r["date"]: r for r in logbook_docx.read_logbook()}

    to_rewrite, plain, skipped, not_synced = [], [], [], []
    for year, month in months_between(start, end):
        try:
            records = sheets.read_month(year, month)
        except Exception as error:
            skipped.append(f"{sheets.tab_name(year, month)}: {error}")
            continue
        for r in records:
            day = r["date"]
            if not (max(start, FIRST_DAY) <= day <= end):
                continue
            if logbook.get(day, {}).get("activities"):
                not_synced.append(f"{day:{DATE_FORMAT}}: already in the logbook")
                continue
            wording = LEAVE_WORDING.get(r["mode"]) if day.weekday() < 5 else None
            if wording:
                plain.append((day, wording))
            elif r["tasks"]:
                to_rewrite.append({"date": f"{day:{DATE_FORMAT}}", "tasks": r["tasks"]})
            elif day.weekday() < 5:
                not_synced.append(f"{day:{DATE_FORMAT}}: no tasks in the work log yet")

    rewritten = {}
    for i in range(0, len(to_rewrite), SYNC_BATCH):
        rewritten.update(draft_sync(to_rewrite[i:i + SYNC_BATCH]))

    proposed = []
    if to_rewrite or plain:
        last = max([parse_date(d["date"]) for d in to_rewrite] + [d for d, _ in plain])
        propose_week_tables(logbook_docx.week_of(last))
    for day, wording in plain:
        proposed.append(save_proposal("logbook_entry", f"{day:{DATE_FORMAT}}",
                                      {"date": f"{day:{DATE_FORMAT}}", "activities": [wording],
                                       "bullets": False}, []))
    for item in to_rewrite:
        activities = rewritten.get(item["date"])
        if not activities:
            skipped.append(f"{item['date']}: the rewrite failed, try syncing this day again")
            continue
        issues = []
        day = parse_date(item["date"])
        if day.weekday() >= 5:
            issues.append(issue("weekend work: an extra row will be added to this week's table", WARN))
        draft = {"tasks": "", "challenges": "", "learnings": "", "next_steps": "",
                 "activities": activities}
        issues += [issue(f["what"], WARN) for f in check_draft_against_notes(item["tasks"], draft)]
        task_lines = [line for line in item["tasks"].splitlines() if line.strip()]
        if len(activities) < len(task_lines):
            issues.append(issue(f"the Sheet has {len(task_lines)} tasks but the rewrite has "
                                f"{len(activities)}", WARN))
        proposed.append(save_proposal("logbook_entry", item["date"],
                                      {"date": item["date"], "activities": activities,
                                       "source": item["tasks"]}, issues))
    return {"proposed": "sync", "logbook_entries": len(proposed), "skipped": skipped,
            "not_synced": not_synced, "status": NOT_WRITTEN if proposed else "nothing to sync"}


def refine_entry(document, day, instruction, day_to=None):
    """Rewrite existing entries for a date or a range, following the user's instruction
    (old values kept for undo)."""
    if day_to:
        start, end = parse_range(day, day_to)
        results = [refine_one_entry(document, start + timedelta(days=i), instruction)
                   for i in range((end - start).days + 1)]
        done = [r for r in results if "operation" in r]
        return {"proposed": "refine", "operations": [r["operation"] for r in done],
                "not_refined": [r["error"] for r in results if "error" in r],
                "status": NOT_WRITTEN if done else "nothing to refine"}
    return refine_one_entry(document, parse_date(day), instruction)


def refine_one_entry(document, target, instruction):
    day = f"{target:{DATE_FORMAT}}"
    if document == "sheet":
        records = {r["date"]: r for r in sheets.read_month(target.year, target.month)}
        record = records.get(target, {})
        fields = {f: record[f] for f in SHEET_TEXT_FIELDS if record.get(f)}
        kind = "sheet_row"
    elif document == "logbook":
        record = next((r for r in logbook_docx.read_logbook() if r["date"] == target), {})
        lines = [line.lstrip(logbook_docx.BULLET).strip()
                 for line in record.get("activities", "").splitlines() if line.strip()]
        fields = {"activities": lines} if lines else {}
        kind = "logbook_entry"
    else:
        raise ValueError("document must be 'sheet' or 'logbook'")
    if not fields:
        return {"error": f"{day} has no {document} entry to refine"}

    facts = "\n".join(v if isinstance(v, str) else "\n".join(v) for v in fields.values())
    new, changes = refine_text(fields, facts, instruction)
    draft = {"tasks": "", "challenges": "", "learnings": "", "next_steps": "", "activities": []}
    found = check_draft_against_notes(f"{facts}\n{instruction}", {**draft, **new})
    issues = [issue("replaces the current entry; the old text is saved for undo", WARN)]
    issues += [issue(f["what"], WARN) for f in found]
    op = save_proposal(kind, f"{target:{DATE_FORMAT}}",
                       {"date": f"{target:{DATE_FORMAT}}", **new, "source": facts,
                        "overwrite": True}, issues)
    return {"proposed": "refine", "operation": op, "changes": changes, "status": NOT_WRITTEN}


SUMMARY_FIELDS = ["mode", "tasks", "challenges", "learnings", "next_steps"]


def summarize(scope, project=None, month=None, year=None, date_from=None, date_to=None):
    """Summarize the work log for a project, a month or a date range."""
    today = date.today()
    if scope == "project":
        if not project:
            raise ValueError("project is needed for a project summary")
        needle, days = project.lower(), []
        for y, m in months_between(FIRST_DAY, today):
            try:
                records = sheets.read_month(y, m)
            except Exception:
                continue
            days += [r for r in records
                     if any(needle in r[f].lower() for f in SHEET_TEXT_FIELDS)]
        label = f"project '{project}'"
    elif scope == "month":
        if not month:
            raise ValueError("month is needed for a month summary")
        year = year or (today.year if month <= today.month else today.year - 1)
        days = sheets.read_month(year, month)
        label = sheets.tab_name(year, month)
    elif scope == "range":
        start, end = parse_range(date_from, date_to)
        days = []
        for y, m in months_between(start, end):
            days += [r for r in sheets.read_month(y, m) if start <= r["date"] <= end]
        label = f"{date_from} to {date_to}"
    else:
        raise ValueError("scope must be 'project', 'month' or 'range'")

    days = [{"date": f"{r['date']:%d/%m}", **{f: r[f] for f in SUMMARY_FIELDS if r[f]}}
            for r in days if r["tasks"]]
    if not days:
        return {"scope": label, "summary": "", "days_used": 0,
                "note": "No work-log days with tasks match this."}
    return {"scope": label, "days_used": len(days), "summary": summarize_days(label, days)}


def change_setting(setting, value, apply_to="future"):
    """Propose changing a whitelisted logbook setting."""
    if setting not in logbook_docx.SETTINGS:
        return {"error": f"'{setting}' cannot be changed by chat. Allowed: "
                         f"{', '.join(logbook_docx.SETTINGS)}. Columns and tables cannot be changed."}
    new = logbook_docx.SETTINGS[setting][1](value)
    old = logbook_docx.get_setting(setting)
    if setting == "signed_up_to_week":
        apply_to = "future"  # a flag, nothing to reformat
    issues = []
    if new == old and apply_to != "all":
        issues.append(issue(f"{setting} is already {old}", BLOCK))
    elif new == old:
        issues.append(issue(f"{setting} is already {old}: this only reformats existing "
                             "entries to match it", WARN))
    if apply_to == "all":
        issues.append(issue("reformats every entry already written in the logbook", WARN))
        signed = logbook_docx.get_setting("signed_up_to_week")
        if signed:
            issues.append(issue(f"Weeks 1-{signed} are signed: the .docx will no longer match "
                                "the signed PDF", WARN))
    op = save_proposal("setting", setting,
                       {"setting": setting, "old": old, "new": new, "apply_to": apply_to}, issues)
    return {"proposed": "change_setting", "operation": op, "old": old, "new": new,
            "apply_to": apply_to, "status": NOT_WRITTEN}


DATE_PARAM = {"type": "string", "description": "Date as dd/mm/yyyy"}
TIME_PARAM = {"type": "string", "description": "Time as HH:MM (24h)"}

TOOLS = {
    "change_setting": {
        "function": change_setting,
        "description": "Propose changing a logbook format setting: font_name, font_size, "
                       "date_align (left/center/right), leave_wording, public_holiday_wording, "
                       "or signed_up_to_week (the last week the supervisor signed, e.g. 12). "
                       "apply_to: 'future' (new entries only) or 'all' (also reformat existing "
                       "entries). The app also asks the user to confirm this at approval.",
        "parameters": {
            "setting": {"type": "string", "enum": ["font_name", "font_size", "date_align",
                                                   "leave_wording", "public_holiday_wording",
                                                   "signed_up_to_week"]},
            "value": {"type": "string"},
            "apply_to": {"type": "string", "enum": ["future", "all"]},
        },
        "required": ["setting", "value"],
    },
    "summarize": {
        "function": summarize,
        "description": "Summarize the work log for a project (all days that mention it), a "
                       "month, or a date range. Returns a summary built only from the entries, "
                       "with the dates each point comes from. Show it to the user as it is.",
        "parameters": {
            "scope": {"type": "string", "enum": ["project", "month", "range"]},
            "project": {"type": "string", "description": "Project or client name (scope=project)"},
            "month": {"type": "integer", "description": "1-12 (scope=month)"},
            "year": {"type": "integer", "description": "Year (scope=month, optional)"},
            "date_from": {**DATE_PARAM, "description": "Start date (scope=range)"},
            "date_to": {**DATE_PARAM, "description": "End date (scope=range)"},
        },
        "required": ["scope"],
    },
    "refine_entry": {
        "function": refine_entry,
        "description": "Rewrite EXISTING entries in the work log ('sheet') or the logbook "
                       "('logbook') for one date or a date range, following the user's instruction (e.g. "
                       "'more detailed', 'shorter', 'mention the CIMB samples'). The app shows "
                       "old vs new for approval; the old text is kept for undo.",
        "parameters": {"document": {"type": "string", "enum": ["sheet", "logbook"]},
                       "day": DATE_PARAM,
                       "day_to": {**DATE_PARAM, "description": "End date, to refine every "
                                  "entry in a range (optional)"},
                       "instruction": {"type": "string", "description": "What to change"}},
        "required": ["document", "day", "instruction"],
    },
    "sync": {
        "function": sync,
        "description": "Copy work-log days into the logbook where the logbook has no entry: "
                       "tasks are rewritten in logbook style, Leave and Public Holiday days "
                       "get plain wording, missing week tables are added. Dates are optional "
                       "(default: from the internship start to today). The app shows "
                       "everything for approval.",
        "parameters": {"date_from": {**DATE_PARAM, "description": "Start date (optional)"},
                       "date_to": {**DATE_PARAM, "description": "End date (optional)"}},
        "required": [],
    },
    "log_update": {
        "function": log_update,
        "description": "Log the user's work notes into BOTH the work log and the logbook. "
                       "Pass the user's notes exactly as written, all dates included; do not "
                       "rewrite them. The app drafts the entries, checks them and shows them "
                       "for approval. Use this whenever the user asks to log or record their work.",
        "parameters": {"notes": {"type": "string", "description": "The user's notes, unchanged"}},
        "required": ["notes"],
    },
    "read_sheet": {
        "function": read_sheet,
        "description": "Read the company work log (Google Sheet) for a date or date range. "
                       "Returns each day's times, mode, tasks, challenges, learnings and next steps.",
        "parameters": {"date_from": DATE_PARAM,
                       "date_to": {**DATE_PARAM, "description": "End date (optional, inclusive)"}},
        "required": ["date_from"],
    },
    "read_logbook": {
        "function": read_logbook,
        "description": "Read the university logbook (.docx) entries for a date or date range.",
        "parameters": {"date_from": DATE_PARAM,
                       "date_to": {**DATE_PARAM, "description": "End date (optional, inclusive)"}},
        "required": ["date_from"],
    },
    "search_records": {
        "function": search_records,
        "description": "Find days in the work log whose tasks, challenges, learnings or next "
                       "steps mention a word or phrase (e.g. a project or client name).",
        "parameters": {"text": {"type": "string", "description": "Word or phrase to look for"}},
        "required": ["text"],
    },
    "audit": {
        "function": audit,
        "description": "Run all rule-based checks on both documents and return the findings.",
        "parameters": {},
        "required": [],
    },
    "write_sheet_rows": {
        "function": write_sheet_rows,
        "description": "Propose filling one or more days in the work log. Does not write: "
                       "returns the proposal and any issues for the user to approve.",
        "parameters": {"rows": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "date": DATE_PARAM, "clock_in": TIME_PARAM, "clock_out": TIME_PARAM,
                "break_time": TIME_PARAM,
                "mode": {"type": "string", "enum": sheets.PROFILE["modes"]},
                "tasks": {"type": "string"}, "challenges": {"type": "string"},
                "learnings": {"type": "string"}, "next_steps": {"type": "string"},
            },
            "required": ["date", "clock_in", "clock_out", "break_time", "mode", "tasks"],
        }}},
        "required": ["rows"],
    },
    "create_month_tab": {
        "function": create_month_tab,
        "description": "Propose creating a new month tab in the work log. Does not write.",
        "parameters": {"year": {"type": "integer"},
                       "month": {"type": "integer", "description": "1-12"}},
        "required": ["year", "month"],
    },
    "write_logbook_entries": {
        "function": write_logbook_entries,
        "description": "Propose writing logbook entries (one bullet per activity). Does not write.",
        "parameters": {"entries": {"type": "array", "items": {
            "type": "object",
            "properties": {"date": DATE_PARAM,
                           "activities": {"type": "array", "items": {"type": "string"}}},
            "required": ["date", "activities"],
        }}},
        "required": ["entries"],
    },
    "add_week_table": {
        "function": add_week_table,
        "description": "Propose adding logbook week tables up to the given week. Does not write.",
        "parameters": {"up_to_week": {"type": "integer"}},
        "required": ["up_to_week"],
    },
}


def tool_schemas():
    """The tools in the format the LLM expects."""
    return [{"type": "function", "function": {
        "name": name, "description": tool["description"],
        "parameters": {"type": "object", "properties": tool["parameters"],
                       "required": tool["required"]},
    }} for name, tool in TOOLS.items()]


def run_tool(name, arguments):
    """Run a tool and return its result as JSON text. Errors are returned, not raised,
    so the LLM can see what went wrong and the loop keeps going."""
    if name not in TOOLS:
        return json.dumps({"error": f"Unknown tool '{name}'"})
    try:
        result = TOOLS[name]["function"](**arguments)
    except Exception as error:
        result = {"error": f"{type(error).__name__}: {error}"}
    return json.dumps(result, ensure_ascii=False)
