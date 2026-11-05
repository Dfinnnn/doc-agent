"""Rule-based checks. Each check takes data in and returns findings out;
nothing here calls Google or opens files, so every rule can be unit-tested.

A finding is a dict:
    where     - tab/cell or week/row, e.g. "November2026!A2:A31"
    what      - what is wrong
    cause     - the likely cause (rule-based guess)
    severity  - "block" (fix before writing) or "warn"
    fix       - a suggested fix the user can approve
"""

import calendar
import re
from datetime import date, datetime, timedelta

from app import config
from app.integrations.logbook_docx import DATE_FORMAT as LOGBOOK_DATE_FORMAT, week_header

SHEET = config.WORKSHEET
COLUMNS = list(SHEET["columns"])
FIELDS = list(SHEET["columns"].values())
NON_WORKING_MODES = {"Weekend", "Public Holidays", "Leave", "Medical Leave"}
TEXT_FIELDS = ["tasks", "challenges", "learnings", "next_steps"]
FIRST_DAY = date.fromisoformat(SHEET["first_day"])  # days before the internship are not checked


def finding(where, what, cause, severity, fix):
    return {"where": where, "what": what, "cause": cause, "severity": severity, "fix": fix}


def from_serial(number):
    """Sheets date number -> date (the opposite of sheets.to_serial)."""
    return date(1899, 12, 30) + timedelta(days=int(number))


def is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def check_month_tab(title, year, month, rows, today):
    """Check one month tab.

    rows are the tab's cells as read with render="FORMULA": dates and times are
    numbers, formulas are text like "=C2-B2-D2", empty cells may be missing.
    """
    findings = []

    # 1. Headers
    header = (rows[0] if rows else []) + [""] * len(COLUMNS)
    wrong = [f"{chr(65 + i)}1 should be '{name}'" for i, name in enumerate(COLUMNS)
             if header[i] != name]
    if wrong:
        findings.append(finding(
            f"{title}!1:1", "Header row is not intact: " + ", ".join(wrong),
            "a header cell was cleared or renamed", "block",
            "restore the header text: " + ", ".join(wrong)))

    # 2. Total row
    total_at = next((i for i, row in enumerate(rows)
                     if row and row[0] == SHEET["total_label"]), None)
    if total_at is None:
        findings.append(finding(
            title, f"No '{SHEET['total_label']}' row", "the Total row was deleted",
            "block", "add the Total row back below the last day"))
        return findings

    # 3. One row per day, in this month, in order
    days_in_month = calendar.monthrange(year, month)[1]
    day_rows = [row + [""] * len(FIELDS) for row in rows[1:total_at]]
    if len(day_rows) != days_in_month:
        findings.append(finding(
            f"{title}!A2:A{total_at}",
            f"{len(day_rows)} day rows, but {calendar.month_name[month]} has {days_in_month} days",
            "the tab was copied from a month with a different length", "block",
            f"make it exactly {days_in_month} day rows"))

    wrong_month, out_of_order = [], []
    for i, row in enumerate(day_rows):
        sheet_row = i + 2
        if not is_number(row[0]):
            out_of_order.append(f"A{sheet_row}")
            continue
        day = from_serial(row[0])
        if (day.year, day.month) != (year, month):
            wrong_month.append((sheet_row, day))
        elif day.day != i + 1:
            out_of_order.append(f"A{sheet_row}")
    if wrong_month:
        first_row, first_day = wrong_month[0]
        findings.append(finding(
            f"{title}!A{first_row}:A{wrong_month[-1][0]}",
            f"{len(wrong_month)} dates are not in {calendar.month_name[month]} {year} "
            f"(e.g. A{first_row} is {first_day:%d/%m/%Y})",
            "the tab was copied from another month and the dates were not updated", "block",
            f"rewrite the dates as 01/{month:02d}/{year} onwards"))
    if out_of_order:
        findings.append(finding(
            f"{title}!" + ",".join(out_of_order),
            "Dates are missing, duplicated or out of order",
            "a row was inserted, deleted or edited by hand", "block",
            "rewrite the dates so row N holds day N-1 of the month"))

    # 4. Formulas
    for i, row in enumerate(day_rows):
        sheet_row = i + 2
        expected = SHEET["hours_formula"].format(row=sheet_row)
        if row[4] != expected:
            findings.append(finding(
                f"{title}!E{sheet_row}", f"Hours formula is '{row[4]}', expected '{expected}'",
                "the formula was typed over or copied from another row", "block",
                f"restore '{expected}'"))
    expected_total = SHEET["total_formula"].format(last=len(day_rows) + 1)
    total_formula = (rows[total_at] + [""] * 5)[4]
    if total_formula != expected_total:
        findings.append(finding(
            f"{title}!E{total_at + 1}",
            f"Total does not sum all day rows (E2:E{len(day_rows) + 1})",
            "the formula was copied from a shorter month", "warn",
            f"set the Total to sum E2:E{len(day_rows) + 1}"))

    # 5. Mode and content, row by row (only valid dates from the internship start)
    for i, row in enumerate(day_rows):
        if not is_number(row[0]) or from_serial(row[0]) < FIRST_DAY:
            continue
        findings += check_day_row(title, i + 2, from_serial(row[0]), dict(zip(FIELDS, row)), today)
    return findings


def check_day_row(title, sheet_row, day, record, today):
    """Mode-vs-content and time checks for one day."""
    findings = []
    where = f"{title}!{sheet_row}:{sheet_row}"
    mode = record["mode"]
    has_text = any(str(record[field]).strip() for field in TEXT_FIELDS)
    clock_in, clock_out, break_time = (
        record[f] if is_number(record[f]) else 0 for f in ("clock_in", "clock_out", "break_time"))

    if mode and mode not in SHEET["modes"]:
        findings.append(finding(
            f"{title}!F{sheet_row}", f"Mode '{mode}' is not one of {SHEET['modes']}",
            "typed by hand instead of picked from the dropdown", "block",
            "pick a Mode from the dropdown"))

    if day > today:
        # Future days: only entries count; pre-filled clock-out/break times are fine
        if has_text:
            findings.append(finding(
                where, f"{day:%d/%m/%Y} is in the future but already has entries",
                "entered on the wrong date", "warn", "move the entry to the right date"))
        return findings

    if mode == SHEET["weekend_mode"] and has_text:
        findings.append(finding(
            where, f"{day:%d/%m/%Y} has entries but Mode is Weekend",
            "weekend work without changing Mode", "warn", "set Mode to WFO or WFH"))
    elif mode in NON_WORKING_MODES and has_text:
        findings.append(finding(
            where, f"{day:%d/%m/%Y} has entries but Mode is {mode}",
            "Mode or entries on the wrong day", "warn",
            "clear the entries or change Mode"))
    elif mode in ("WFO", "WFH") and not record["tasks"]:
        findings.append(finding(
            where, f"{day:%d/%m/%Y} is a working day ({mode}) with no Tasks Completed",
            "the entry was not filled in yet", "warn", "add the day's tasks"))
    elif not mode:
        findings.append(finding(
            f"{title}!F{sheet_row}", f"{day:%d/%m/%Y} has no Mode",
            "the day was not filled in yet", "warn",
            "set Mode (WFO, WFH, Leave, Public Holidays...)"))

    if clock_out and not clock_in:
        findings.append(finding(
            f"{title}!B{sheet_row}", f"{day:%d/%m/%Y} has a clock-out but no clock-in",
            "clock-in was not filled in", "warn", "add the clock-in time"))
    elif clock_in and clock_out - clock_in - break_time < 0:
        findings.append(finding(
            f"{title}!B{sheet_row}:D{sheet_row}", f"{day:%d/%m/%Y} works out to negative hours",
            "clock-in and clock-out swapped, or break too long", "warn",
            "check the clock-in, clock-out and break times"))
    return findings


def check_logbook(headers, records, today):
    """Check the logbook.

    headers: the header text of each week table, in document order.
    records: read_logbook() output (week, computed date, date_text, activities).
    """
    findings = []

    # 1. Week headers must match the dates computed from Week 1
    for week, text in enumerate(headers, start=1):
        expected = week_header(week)
        if text != expected:
            # Same "(dd/mm – dd/mm)" part as the week before -> probably copy-pasted
            dates = text[text.find("("):]
            copied = week > 1 and dates == headers[week - 2][headers[week - 2].find("("):]
            findings.append(finding(
                f"Week {week} header", f"Header says '{text}', expected '{expected}'",
                f"copy-pasted from Week {week - 1}" if copied else "typed by hand",
                "warn", f"change the header to '{expected}'"))

    # 2. Each day row: the Date cell must match the computed date for that row
    for record in records:
        day, text = record["date"], record["date_text"]
        where = f"Week {record['week']}, {day:%A} row"
        if record.get("weekend_row"):
            # Extra rows are dated by their own text: it must fall in this week
            if (day - FIRST_DAY).days // 7 + 1 != record["week"]:
                findings.append(finding(
                    where, f"Weekend row {text} is in the Week {record['week']} table",
                    "the row was added to the wrong week", "block",
                    "move it to its own week's table"))
            continue
        expected = f"{day:{LOGBOOK_DATE_FORMAT}}"
        if text and text != expected:
            try:
                written = datetime.strptime(text, LOGBOOK_DATE_FORMAT).date()
                cause = f"the entry for {written:%d/%m/%Y} was put in the wrong row"
            except ValueError:
                cause = "the date is in a different format"
            findings.append(finding(
                where, f"Date cell says '{text}', but this row is {expected}",
                cause, "block", f"set the date to {expected}, or move the entry to its own row"))
        if record["activities"] and not text:
            findings.append(finding(
                where, f"{expected} has activities but no date",
                "the date was not filled in", "warn", f"set the date to {expected}"))
        if record["activities"] and day > today:
            findings.append(finding(
                where, f"{expected} is in the future but already has activities",
                "entered on the wrong date", "warn", "move the entry to the right date"))
    return findings


LEAVE_WORDING = {  # Sheet Mode -> what the logbook should say for that day
    "Leave": config.LOGBOOK["wording"]["leave"],
    "Medical Leave": config.LOGBOOK["wording"]["leave"],
    "Public Holidays": config.LOGBOOK["wording"]["public_holiday"],
}


def check_consistency(sheet_records, logbook_records, today):
    """Compare the Sheet (the master) with the logbook, weekday by weekday.

    sheet_records: read_month() records for the months to compare.
    logbook_records: read_logbook() records.
    Weekend days are compared only when the Sheet has work on them (an extra row).
    """
    findings = []
    logbook = {record["date"]: record for record in logbook_records}
    weeks_present = max((record["week"] for record in logbook_records), default=0)
    missing_weeks = set()

    for sheet in sheet_records:
        day = sheet["date"]
        weekend = day.weekday() >= 5
        if day < FIRST_DAY or day > today or (weekend and not sheet["tasks"]):
            continue
        week = (day - FIRST_DAY).days // 7 + 1
        entry = logbook.get(day)
        expected_text = None if weekend else LEAVE_WORDING.get(sheet["mode"])
        needs_entry = bool(sheet["tasks"]) or expected_text

        if entry is None:
            if needs_entry and week > weeks_present:
                missing_weeks.add(week)
            elif needs_entry:  # only weekend days have no row in an existing table
                findings.append(finding(
                    f"{day:%d/%m/%Y} (Sheet row / logbook Week {week})",
                    "Weekend work is in the Sheet but has no row in the logbook",
                    "the logbook was not updated after the Sheet", "warn",
                    "sync this day: an extra row is added to that week's table"))
            continue
        where = f"{day:%d/%m/%Y} (Sheet row / logbook Week {week})"
        if needs_entry and not entry["activities"]:
            what = (f"Sheet says {sheet['mode']} but the logbook is empty" if expected_text
                    else "Sheet has tasks but the logbook entry is empty")
            findings.append(finding(
                where, what, "the logbook was not updated after the Sheet", "warn",
                f"write '{expected_text}' in the logbook" if expected_text
                else "sync this day from the Sheet to the logbook"))
        elif entry["activities"] and not sheet["tasks"] and not expected_text:
            findings.append(finding(
                where, "The logbook has activities but the Sheet has no tasks",
                "the entry was written in the logbook only (the Sheet is the master)", "warn",
                "add the tasks to the Sheet, or clear the logbook entry"))

    if missing_weeks:
        first, last = min(missing_weeks), max(missing_weeks)
        span = f"Week {first}" if first == last else f"Weeks {first}-{last}"
        verb = "has" if first == last else "have"
        findings.append(finding(
            "Logbook", f"{span} {verb} Sheet entries but no week table in the logbook",
            "the logbook has not been synced up to today", "warn",
            f"add the week tables and sync {span} from the Sheet"))
    return findings


DRAFT_TEXT_FIELDS = ["tasks", "challenges", "learnings", "next_steps", "activities"]
BULLET_MARKS = ("-", "*", "•")


def check_draft_against_notes(notes, draft):
    """Rule checks on a draft: no numbers or names that are not in the notes,
    and no tasks dropped. These are "possible" problems: the user decides.
    """
    findings = []
    notes_lower = notes.lower()

    for field in DRAFT_TEXT_FIELDS:
        value = draft[field]
        # Split into sentences, also inside each list item, so a word that starts
        # a second sentence is not mistaken for a name
        text = "\n".join(value) if isinstance(value, list) else value
        sentences = re.split(r"[.\n]", text)
        added = []
        for sentence in sentences:
            words = re.findall(r"[A-Za-z][\w-]*|\d+(?:[.:]\d+)?", sentence)
            for position, word in enumerate(words):
                is_number = word[0].isdigit()
                # A capitalised word that does not start the sentence is likely a name
                is_name = word[0].isupper() and position > 0
                if (is_number or is_name) and word.lower() not in notes_lower:
                    added.append(word)
        if added:
            unique = list(dict.fromkeys(added))
            findings.append(finding(
                f"draft {field}", f"Possible added fact: {', '.join(unique)} not in the notes",
                "the model added a name or number", "warn",
                "remove it, or add it to the notes if it is true"))

    bullets = [line for line in notes.splitlines() if line.strip().startswith(BULLET_MARKS)]
    if len(bullets) > len(draft["activities"]):
        findings.append(finding(
            "draft activities", f"The notes have {len(bullets)} tasks but the draft has "
            f"{len(draft['activities'])}", "tasks were merged or dropped", "warn",
            "redraft with one entry per task"))
    return findings


def date_in_notes(day, notes, today):
    """True if the drafted date is really in the notes (as d/m or dd/mm), or the notes
    have no date / say "today" and the date is today. Catches swapped day and month."""
    if re.search(rf"(?<!\d)0?{day.day}/0?{day.month}(?!\d)", notes):
        return True
    has_dates = re.search(r"(?<!\d)\d{1,2}/\d{1,2}(?!\d)", notes)
    return day == today and (not has_dates or "today" in notes.lower())
