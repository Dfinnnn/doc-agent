"""Talks to the Company Worksheet (Google Sheet) through the Sheets API."""

import calendar
import sys
from datetime import datetime

from google.oauth2 import service_account
from googleapiclient.discovery import build

from app import config

# Read-only access for now; Part 2 changes this when we start writing
SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]

# Sheet header text -> field name in our records, in column order (A to K).
# Part 4 moves this into profiles/worksheet.json.
COLUMNS = {
    "Date": "date",
    "Clock-in": "clock_in",
    "Clock-out": "clock_out",
    "Break Time": "break_time",
    "Hours Worked": "hours",
    "Mode": "mode",
    "Tasks Completed": "tasks",
    "Challenges Faced": "challenges",
    "Learnings": "learnings",
    "Next Steps": "next_steps",
    "Supervisor's Comment": "supervisor_comment",
}


def get_service():
    """Sign in with the service-account key and return a Sheets API client."""
    creds = service_account.Credentials.from_service_account_file(
        config.SERVICE_ACCOUNT_FILE, scopes=SCOPES
    )
    return build("sheets", "v4", credentials=creds)


def read_range(tab, cells):
    """Return the raw values of a range, e.g. read_range("October2026", "A1:Z5")."""
    result = (
        get_service()
        .spreadsheets()
        .values()
        .get(spreadsheetId=config.SHEET_ID, range=f"'{tab}'!{cells}")
        .execute()
    )
    # Empty rows at the end are left out by the API, so default to []
    return result.get("values", [])


def tab_name(year, month):
    """Every month has its own tab named like "October2026"."""
    return f"{calendar.month_name[month]}{year}"


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
        if row and row[0] == "Total working hours":
            break
        # The API drops empty cells at the end of a row, so pad it to full width
        row = row + [""] * (len(COLUMNS) - len(row))
        record = dict(zip(COLUMNS.values(), row))
        record["date"] = datetime.strptime(record["date"], "%d/%m/%Y").date()
        records.append(record)
    return records


if __name__ == "__main__":
    # Usage: python -m app.integrations.sheets [year] [month]   (default: October 2026)
    year = int(sys.argv[1]) if len(sys.argv) > 1 else 2026
    month = int(sys.argv[2]) if len(sys.argv) > 2 else 10

    records = read_month(year, month)
    print(f"{tab_name(year, month)}: {len(records)} days\n")
    for r in records:
        print(f"{r['date']:%d/%m/%Y}  {r['mode']:<8} {r['hours']}  {r['tasks'][:50]!r}")

    print("\nOne full record:")
    print(next(r for r in records if r["tasks"]))
