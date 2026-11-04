"""Audit: read both documents, run every check, print the findings.

Run with: python -m app.validation.audit
"""

import calendar
import re
from datetime import date

from app.integrations import logbook_docx, sheets
from app.validation.checks import (
    FIELDS, FIRST_DAY, check_consistency, check_logbook, check_month_tab, from_serial, is_number,
)

MONTHS = {name: number for number, name in enumerate(calendar.month_name) if name}


def parse_tab_title(title):
    """"October2026" -> (2026, 10); None for tabs that are not month tabs."""
    match = re.fullmatch(r"([A-Za-z]+)(\d{4})", title)
    if not match or match.group(1) not in MONTHS:
        return None
    return int(match.group(2)), MONTHS[match.group(1)]


def audit_sheet(today):
    """Check every month tab from the internship start. Returns (findings, day records)."""
    findings, records = [], []
    for tab in sheets.list_tabs():
        parsed = parse_tab_title(tab["title"])
        if parsed is None or parsed < (FIRST_DAY.year, FIRST_DAY.month):
            continue
        year, month = parsed
        rows = sheets.read_range(tab["title"], "A1:K", render="FORMULA")
        findings += check_month_tab(tab["title"], year, month, rows, today)
        # Day records for the Sheet-vs-logbook comparison
        for row in rows[1:]:
            if row and is_number(row[0]):
                record = dict(zip(FIELDS, row + [""] * len(FIELDS)))
                record["date"] = from_serial(row[0])
                records.append(record)
    return findings, records


def audit_logbook(today):
    """Check the logbook. Returns (findings, day records)."""
    doc = logbook_docx.open_logbook()
    headers = [table.cell(0, 0).text.strip() for table in logbook_docx.find_week_tables(doc)]
    records = logbook_docx.read_logbook(doc)
    return check_logbook(headers, records, today), records


def print_findings(title, findings):
    blocks = sum(f["severity"] == "block" for f in findings)
    print(f"\n=== {title}: {len(findings)} findings ({blocks} block) ===")
    for f in findings:
        print(f"[{f['severity']}] {f['where']}")
        print(f"    what:  {f['what']}")
        print(f"    cause: {f['cause']}")
        print(f"    fix:   {f['fix']}")


if __name__ == "__main__":
    today = date.today()
    sheet_findings, sheet_records = audit_sheet(today)
    logbook_findings, logbook_records = audit_logbook(today)
    print(f"Audit on {today:%d/%m/%Y}")
    print_findings("Company Worksheet", sheet_findings)
    print_findings("University Logbook", logbook_findings)
    print_findings("Sheet vs Logbook", check_consistency(sheet_records, logbook_records, today))
