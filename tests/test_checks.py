"""Unit tests for the rule-based checks. Run with: python -m pytest"""

import calendar
from datetime import date

from app.integrations.logbook_docx import week_dates, week_header
from app.integrations.sheets import COLUMNS, HOURS_FORMULA, TOTAL_FORMULA, TOTAL_LABEL, to_serial
from app.validation.checks import (
    check_consistency, check_draft_against_notes, check_logbook, check_month_tab,
)

TODAY = date(2026, 11, 4)


def make_tab(year, month):
    """A correct month tab, as read with render="FORMULA" (like create_month_tab makes)."""
    days = calendar.monthrange(year, month)[1]
    rows = [list(COLUMNS)]
    for number in range(1, days + 1):
        day = date(year, month, number)
        mode = "Weekend" if day.weekday() >= 5 else ""
        rows.append([to_serial(day), 0, 0, 0, HOURS_FORMULA.format(row=number + 1), mode,
                     "", "", "", "", ""])
    rows.append([TOTAL_LABEL, "", "", "", TOTAL_FORMULA.format(last=days + 1)])
    return rows


def fill_working_days(rows, year, month):
    """Fill every weekday up to TODAY as a normal WFO day."""
    for number, row in enumerate(rows[1:-1], start=1):
        day = date(year, month, number)
        if day.weekday() < 5 and day <= TODAY:
            row[1:4] = [0.354, 0.75, 0.0417]  # 08:30, 18:00, 01:00
            row[5:7] = ["WFO", "Worked on the project."]
    return rows


def check(rows, year=2026, month=11):
    return check_month_tab(f"{calendar.month_name[month]}{year}", year, month, rows, TODAY)


def whats(findings):
    return " | ".join(f["what"] for f in findings)


def test_new_future_month_has_no_findings():
    assert check(make_tab(2026, 12), 2026, 12) == []


def test_filled_month_has_no_findings():
    assert check(fill_working_days(make_tab(2026, 10), 2026, 10), 2026, 10) == []


def test_unfilled_past_working_day_is_flagged():
    findings = check(make_tab(2026, 11))  # 02/11 and 03/11 are past weekdays with no Mode
    assert "02/11/2026 has no Mode" in whats(findings)
    assert all(f["severity"] == "warn" for f in findings)


def test_dates_copied_from_previous_month():
    rows = make_tab(2026, 11)
    october = make_tab(2026, 10)
    for row, old in zip(rows[1:-1], october[1:-1]):
        row[0] = old[0]
    findings = check(rows)
    assert "not in November 2026" in whats(findings)
    assert any(f["severity"] == "block" for f in findings)


def test_blank_header_cell():
    rows = make_tab(2026, 11)
    rows[0][5] = ""
    assert "F1 should be 'Mode'" in whats(check(rows))


def test_wrong_number_of_day_rows():
    rows = make_tab(2026, 11)
    rows.insert(-1, list(rows[-2]))  # an extra 31st row
    assert "31 day rows, but November has 30 days" in whats(check(rows))


def test_hours_formula_overwritten():
    rows = make_tab(2026, 11)
    rows[3][4] = "08:30:00"
    assert "Hours formula is '08:30:00'" in whats(check(rows))


def test_total_skips_the_last_day():
    rows = make_tab(2026, 10)
    rows[-1][4] = TOTAL_FORMULA.format(last=31)  # the old E2:E31 problem
    findings = check(rows, 2026, 10)
    assert "Total does not sum all day rows (E2:E32)" in whats(findings)


def test_leave_with_tasks():
    rows = make_tab(2026, 11)
    rows[2][5:7] = ["Leave", "Fixed a bug."]
    assert "has entries but Mode is Leave" in whats(check(rows))


def test_weekend_work_without_changing_mode():
    rows = make_tab(2026, 11)
    rows[1][6] = "Fixed a bug."  # 01/11 is a Sunday
    assert "has entries but Mode is Weekend" in whats(check(rows))


def test_invalid_mode():
    rows = make_tab(2026, 11)
    rows[2][5] = "Office"
    assert "Mode 'Office' is not one of" in whats(check(rows))


def test_clock_out_without_clock_in_on_a_past_day():
    rows = make_tab(2026, 11)
    rows[2][1:4] = [0, 0.75, 0.0417]
    assert "02/11/2026 has a clock-out but no clock-in" in whats(check(rows))


def test_prefilled_clock_out_on_a_future_day_is_fine():
    rows = make_tab(2026, 11)
    rows[10][1:4] = [0, 0.75, 0.0417]  # 10/11, after TODAY
    assert "10/11/2026" not in whats(check(rows))


def test_entry_on_a_future_date():
    rows = make_tab(2026, 11)
    rows[10][6] = "Fixed a bug."
    assert "10/11/2026 is in the future but already has entries" in whats(check(rows))


# ---- Logbook checks ----

def make_logbook(weeks):
    """Correct headers and empty day rows for the given number of weeks."""
    headers = [week_header(week) for week in range(1, weeks + 1)]
    records = [{"week": week, "date": day, "date_text": "", "activities": ""}
               for week in range(1, weeks + 1) for day in week_dates(week)]
    return headers, records


def test_correct_logbook_has_no_findings():
    headers, records = make_logbook(5)
    records[0].update(date_text="17/08/2026", activities="• Attended onboarding.")
    assert check_logbook(headers, records, TODAY) == []


def test_week_header_copied_from_the_week_before():
    headers, records = make_logbook(4)
    headers[3] = "WEEK: Week 4 (31/08 – 04/09)"  # the old Week 4 typo
    findings = check_logbook(headers, records, TODAY)
    assert "expected 'WEEK: Week 4 (07/09 – 11/09)'" in whats(findings)
    assert findings[0]["cause"] == "copy-pasted from Week 3"


def test_entry_in_the_wrong_row():
    headers, records = make_logbook(1)
    records[1].update(date_text="17/08/2026", activities="• Attended onboarding.")  # Tuesday row
    findings = check_logbook(headers, records, TODAY)
    assert "Date cell says '17/08/2026', but this row is 18/08/2026" in whats(findings)
    assert findings[0]["severity"] == "block"


def test_activities_without_a_date():
    headers, records = make_logbook(1)
    records[2]["activities"] = "• Fixed a bug."
    assert "19/08/2026 has activities but no date" in whats(check_logbook(headers, records, TODAY))


# ---- Sheet vs logbook ----


def sheet_day(day, mode="WFO", tasks="Worked on the project."):
    return {"date": day, "mode": mode, "tasks": tasks}


def test_consistent_documents_have_no_findings():
    headers, records = make_logbook(1)
    records[0].update(date_text="17/08/2026", activities="• Worked on the project.")
    sheet = [sheet_day(date(2026, 8, 17))]
    assert check_consistency(sheet, records, TODAY) == []


def test_sheet_entry_missing_in_logbook():
    _, records = make_logbook(1)
    findings = check_consistency([sheet_day(date(2026, 8, 18))], records, TODAY)
    assert "Sheet has tasks but the logbook entry is empty" in whats(findings)


def test_leave_day_needs_leave_wording_in_logbook():
    _, records = make_logbook(1)
    findings = check_consistency([sheet_day(date(2026, 8, 19), "Leave", "")], records, TODAY)
    assert "Sheet says Leave but the logbook is empty" in whats(findings)
    assert "write 'On leave'" in findings[0]["fix"]


def test_logbook_entry_missing_in_sheet():
    _, records = make_logbook(1)
    records[3]["activities"] = "• Fixed a bug."
    findings = check_consistency([sheet_day(date(2026, 8, 20), "WFO", "")], records, TODAY)
    assert "logbook has activities but the Sheet has no tasks" in whats(findings)


def test_missing_weeks_are_one_finding():
    _, records = make_logbook(5)
    sheet = [sheet_day(date(2026, 9, 21)), sheet_day(date(2026, 10, 26))]  # Weeks 6 and 11
    findings = check_consistency(sheet, records, TODAY)
    assert len(findings) == 1
    assert "Weeks 6-11 have Sheet entries but no week table" in findings[0]["what"]


# ---- Draft vs notes ----


def make_draft(tasks="", activities=()):
    return {"tasks": tasks, "challenges": "", "learnings": "", "next_steps": "",
            "activities": list(activities), "missing": []}


def test_faithful_draft_has_no_findings():
    notes = "- bumped the supabase pool to 20\n- tested the email module"
    draft = make_draft("Increased the Supabase connection pool size to 20.",
                       ["Increased the Supabase pool size to 20.", "Tested the email module."])
    assert check_draft_against_notes(notes, draft) == []


def test_added_number_and_name_are_flagged():
    notes = "- bumped the supabase pool"
    draft = make_draft("Increased the Supabase pool size to 50 for the Fundaztic client.",
                       ["Increased the pool size."])
    what = whats(check_draft_against_notes(notes, draft))
    assert "50" in what and "Fundaztic" in what


def test_dropped_task_is_flagged():
    notes = "- fixed the OCR bug\n- built the RAG pipeline\n- replied to the client"
    draft = make_draft("Fixed the OCR bug.", ["Fixed the OCR bug and built the RAG pipeline.",
                                              "Replied to the client."])
    assert "The notes have 3 tasks but the draft has 2" in whats(
        check_draft_against_notes(notes, draft))
