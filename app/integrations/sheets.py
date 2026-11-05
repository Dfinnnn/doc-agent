"""Talks to the Company Worksheet (Google Sheet) through the Sheets API."""

import calendar
import sys
from datetime import date, datetime

from google.oauth2 import service_account
from googleapiclient.discovery import build

from app import config

# Read and write access (the Sheet must also be shared with the robot as Editor)
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# Structure comes from profiles/worksheet.json
PROFILE = config.WORKSHEET
# Sheet header text -> field name in our records, in column order (A to K)
COLUMNS = PROFILE["columns"]
DATE_FORMAT = PROFILE["date_format"]
TOTAL_LABEL = PROFILE["total_label"]
# Same formulas the existing tabs use; {row} and {last} are filled in by the code
HOURS_FORMULA = PROFILE["hours_formula"]
TOTAL_FORMULA = PROFILE["total_formula"]
WEEKEND_MODE = PROFILE["weekend_mode"]


def get_service():
    """Sign in with the service-account key and return a Sheets API client."""
    creds = service_account.Credentials.from_service_account_file(
        config.SERVICE_ACCOUNT_FILE, scopes=SCOPES
    )
    return build("sheets", "v4", credentials=creds)


def read_range(tab, cells, render="FORMATTED_VALUE"):
    """Return the raw values of a range, e.g. read_range("October2026", "A1:Z5").

    render="FORMATTED_VALUE" gives what you see (e.g. "02/10/2026");
    "UNFORMATTED_VALUE" gives the number underneath (e.g. 46297).
    """
    result = (
        get_service()
        .spreadsheets()
        .values()
        .get(spreadsheetId=config.SHEET_ID, range=f"'{tab}'!{cells}",
             valueRenderOption=render)
        .execute()
    )
    # Empty rows at the end are left out by the API, so default to []
    return result.get("values", [])


def tab_name(year, month):
    """Every month has its own tab named like "October2026"."""
    return PROFILE["tab_name"].format(month_name=calendar.month_name[month], year=year)


def to_serial(day):
    """Sheets stores a date as a number of days since 30/12/1899."""
    return (day - date(1899, 12, 30)).days


def to_day_fraction(time_text):
    """Sheets stores a time as a fraction of a day: "18:00" -> 0.75.

    Accepts "HH:MM" or "HH:MM:SS" (the format read_month returns).
    """
    hours, minutes, *seconds = time_text.split(":")
    total = int(hours) * 3600 + int(minutes) * 60 + (int(seconds[0]) if seconds else 0)
    return total / (24 * 3600)


def read_month(year, month):
    """Return one clean record (a dict) per day from that month's tab."""
    rows = read_range(tab_name(year, month), "A1:K")
    header, days = rows[0], rows[1:]

    # Stop early if the columns are not what we expect, instead of mixing up fields
    if header != list(COLUMNS):
        raise ValueError(f"Unexpected headers in {tab_name(year, month)}: {header}")

    records = []
    for row in days:
        # The last row of each tab is the month's total, not a day
        if row and row[0] == TOTAL_LABEL:
            break
        # The API drops empty cells at the end of a row, so pad it to full width
        row = row + [""] * (len(COLUMNS) - len(row))
        record = dict(zip(COLUMNS.values(), row))
        record["date"] = datetime.strptime(record["date"], DATE_FORMAT).date()
        records.append(record)
    return records


def write_day(day, clock_in, clock_out, break_time, mode,
              tasks="", challenges="", learnings="", next_steps="", overwrite=False):
    """Fill in one day's row. Times are "HH:MM" text, e.g. "08:30".

    A value of None means "keep what is there now" (e.g. log update writes the
    text fields but keeps the clock times the user already entered).
    Never writes Hours Worked (a formula) or Supervisor's Comment.
    Refuses if the row's date is not this day, or if the text cells already
    have something in them (unless overwrite=True).
    """
    tab = tab_name(day.year, day.month)
    row = day.day + 1  # row 1 is the header

    if tab not in [t["title"] for t in list_tabs()]:
        raise ValueError(f"There is no {tab} tab; create it first")

    # Safety check: is this really the row for this date? (catches broken tabs)
    current = read_range(tab, f"A{row}:J{row}", render="UNFORMATTED_VALUE")
    current = (current[0] if current else []) + [""] * len(COLUMNS)
    if current[0] != to_serial(day):
        raise ValueError(f"Row {row} of {tab} is not {day:%d/%m/%Y}; not writing")
    # Only the text cells (G-J) count; Mode may already say "Weekend" from the new tab
    if any(current[6:10]) and not overwrite:
        raise ValueError(f"{day:%d/%m/%Y} already has entries; not overwriting them")

    def keep_or(value, column, convert=lambda v: v):
        return current[column] if value is None else convert(value)

    get_service().spreadsheets().values().batchUpdate(
        spreadsheetId=config.SHEET_ID,
        body={
            "valueInputOption": "RAW",  # text stays text, even if it starts with "="
            "data": [
                # Columns B-D: times (E is skipped, it holds the Hours formula)
                {"range": f"'{tab}'!B{row}:D{row}",
                 "values": [[keep_or(clock_in, 1, to_day_fraction),
                             keep_or(clock_out, 2, to_day_fraction),
                             keep_or(break_time, 3, to_day_fraction)]]},
                # Columns F-J: Mode and the text fields (K is the supervisor's)
                {"range": f"'{tab}'!F{row}:J{row}",
                 "values": [[keep_or(mode, 5), keep_or(tasks, 6), keep_or(challenges, 7),
                             keep_or(learnings, 8), keep_or(next_steps, 9)]]},
            ],
        },
    ).execute()


def list_tabs():
    """Return each tab's id, title and position, in the order shown in the Sheet."""
    meta = (
        get_service()
        .spreadsheets()
        .get(spreadsheetId=config.SHEET_ID, fields="sheets.properties(sheetId,title,index)")
        .execute()
    )
    return [sheet["properties"] for sheet in meta["sheets"]]


def find_template(tabs):
    """Pick the newest tab with intact headers to copy the layout from.

    Returns the tab and how many day rows it has (the rows before the Total row).
    """
    for tab in reversed(tabs):
        rows = read_range(tab["title"], "A1:K")
        if not rows or rows[0] != list(COLUMNS):
            continue  # e.g. a half-finished tab with blank headers
        for number, row in enumerate(rows):
            if row and row[0] == TOTAL_LABEL:
                return tab, number - 1  # minus the header row
    raise ValueError("No tab with intact headers and a Total row to copy from")


def create_month_tab(year, month):
    """Create a new month tab with that month's dates and empty entries.

    Copies the layout (colours, fonts, dropdowns) from an existing tab, then
    rewrites every day row, so nothing from the old month is left behind.
    Refuses if the tab already exists.
    """
    title = tab_name(year, month)
    tabs = list_tabs()
    if title in [tab["title"] for tab in tabs]:
        raise ValueError(f"{title} already exists; not overwriting it")

    template, template_days = find_template(tabs)
    days = calendar.monthrange(year, month)[1]

    # Place the new tab right after the previous month's tab, if there is one
    prev_year, prev_month = (year, month - 1) if month > 1 else (year - 1, 12)
    position = len(tabs)
    for tab in tabs:
        if tab["title"] == tab_name(prev_year, prev_month):
            position = tab["index"] + 1

    sheets = get_service().spreadsheets()

    # 1. Duplicate the template tab under the new name
    reply = sheets.batchUpdate(
        spreadsheetId=config.SHEET_ID,
        body={"requests": [{"duplicateSheet": {
            "sourceSheetId": template["sheetId"],
            "insertSheetIndex": position,
            "newSheetName": title,
        }}]},
    ).execute()
    new_id = reply["replies"][0]["duplicateSheet"]["properties"]["sheetId"]

    # 2. Make the number of day rows match the new month (row indexes start at 0)
    requests = []
    if days > template_days:
        requests.append({"insertDimension": {
            "range": {"sheetId": new_id, "dimension": "ROWS",
                      "startIndex": template_days + 1, "endIndex": days + 1},
        }})
    elif days < template_days:
        requests.append({"deleteDimension": {
            "range": {"sheetId": new_id, "dimension": "ROWS",
                      "startIndex": days + 1, "endIndex": template_days + 1},
        }})
    # Give every day row the same look and Mode dropdown as the first day row
    first_row = {"sheetId": new_id, "startRowIndex": 1, "endRowIndex": 2,
                 "startColumnIndex": 0, "endColumnIndex": len(COLUMNS)}
    day_rows = {"sheetId": new_id, "startRowIndex": 1, "endRowIndex": days + 1,
                "startColumnIndex": 0, "endColumnIndex": len(COLUMNS)}
    for paste_type in ("PASTE_FORMAT", "PASTE_DATA_VALIDATION"):
        requests.append({"copyPaste": {
            "source": first_row, "destination": day_rows, "pasteType": paste_type,
        }})
    sheets.batchUpdate(spreadsheetId=config.SHEET_ID, body={"requests": requests}).execute()

    # 3. Write the new dates, zero times, Weekend marks and the formulas
    values = []
    for number in range(1, days + 1):
        day = date(year, month, number)
        row = number + 1  # row 1 is the header
        mode = WEEKEND_MODE if day.weekday() >= 5 else ""  # Saturday = 5, Sunday = 6
        values.append([to_serial(day), 0, 0, 0, HOURS_FORMULA.format(row=row), mode,
                       "", "", "", "", ""])
    values.append([TOTAL_LABEL, "", "", "", TOTAL_FORMULA.format(last=days + 1)])
    sheets.values().update(
        spreadsheetId=config.SHEET_ID,
        range=f"'{title}'!A2:K{days + 2}",
        valueInputOption="USER_ENTERED",  # so formulas are read as formulas
        body={"values": values},
    ).execute()

    return title


if __name__ == "__main__":
    # Usage:
    #   python -m app.integrations.sheets read 2026 10     (print a month)
    #   python -m app.integrations.sheets create 2026 12   (create a month tab)
    #   python -m app.integrations.sheets write 02/11/2026 08:30 18:00 01:00 WFO "tasks text"
    command = sys.argv[1]

    if command == "write":
        day = datetime.strptime(sys.argv[2], "%d/%m/%Y").date()
        write_day(day, *sys.argv[3:])
        print(f"Wrote {day:%d/%m/%Y}")
        sys.exit()

    year, month = int(sys.argv[2]), int(sys.argv[3])
    if command == "read":
        records = read_month(year, month)
        print(f"{tab_name(year, month)}: {len(records)} days\n")
        for r in records:
            print(f"{r['date']:%d/%m/%Y}  {r['mode']:<8} {r['hours']}  {r['tasks'][:50]!r}")
    elif command == "create":
        print(f"Created {create_month_tab(year, month)}")
