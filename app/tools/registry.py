"""The agent's action set: each tool's schema (what the LLM reads) and the
Python function that really runs it.

Read tools run directly. Write tools only propose a change (added in the next step).
"""

import json
from datetime import date, datetime, timedelta

from app.integrations import logbook_docx, sheets
from app.validation import audit as audit_module
from app.validation.checks import FIRST_DAY

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


def save_proposal(kind, target, proposal, issues):
    """Save one proposed change as an operation; returns its id (None if no store)."""
    if CONTEXT["store"] is None:
        return None
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
            issues.append(issue("weekend rows are not supported yet (Part 9)", BLOCK))
        elif week > weeks:
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


DATE_PARAM = {"type": "string", "description": "Date as dd/mm/yyyy"}
TIME_PARAM = {"type": "string", "description": "Time as HH:MM (24h)"}

TOOLS = {
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
