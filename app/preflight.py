"""Read-only check of the documents the settings point to. Writes nothing.

Run it after changing USE_REAL_FILES or the file settings in .env:
    python -m app.preflight
"""

import re
import sys

from app import config
from app.errors import explain

problems = []


def check(label, step):
    """Run one check; print OK with what it found, or the plain reason it failed."""
    try:
        print(f"  OK    {label}: {step()}")
    except Exception as error:
        problems.append(label)
        print(f"  FAIL  {label}: {explain(error)}")


def sheet_tabs():
    from app.integrations import sheets
    tabs = [t["title"] for t in sheets.list_tabs()]
    months = [t for t in tabs if re.fullmatch(r"[A-Z][a-z]+\d{4}", t)]
    if not months:
        raise ValueError(f"no month tabs like 'October2026' found (tabs: {', '.join(tabs[:6])})")
    return f"{len(months)} month tabs, from {months[0]} to {months[-1]}"


def sheet_headers():
    from app.integrations import sheets
    tabs = [t["title"] for t in sheets.list_tabs() if re.fullmatch(r"[A-Z][a-z]+\d{4}", t["title"])]
    for title in tabs:
        month_name, year = re.fullmatch(r"([A-Z][a-z]+)(\d{4})", title).groups()
        month = ["January", "February", "March", "April", "May", "June", "July", "August",
                 "September", "October", "November", "December"].index(month_name) + 1
        sheets.read_month(int(year), month)  # raises if the headers are not the expected ones
    return f"headers match the profile in all {len(tabs)} month tabs"


def logbook_weeks():
    from app.integrations import logbook_docx
    doc = logbook_docx.open_logbook()
    weeks = len(logbook_docx.find_week_tables(doc))
    if not weeks:
        raise ValueError("no week tables found (is this the BSD4812 logbook?)")
    filled = sum(1 for r in logbook_docx.read_logbook(doc) if r["activities"])
    return f"{weeks} week tables, {filled} days with entries"


def logbook_free():
    from app.integrations import logbook_docx
    logbook_docx.check_writable()
    return "not open in another program"


def main():
    mode = config.FILES_MODE.upper()
    print(f"\nPreflight ({mode} files) - reading only, nothing is written\n")
    print(f"  Sheet ID:  ...{config.SHEET_ID[-6:]}")
    print(f"  Logbook:   {config.LOGBOOK_PATH.name}")
    print(f"  Key file:  {config.SERVICE_ACCOUNT_FILE.name}")
    print(f"  History:   data/{config.DB_FILE.name}\n")
    check("Sheet tabs", sheet_tabs)
    check("Sheet headers", sheet_headers)
    check("Logbook week tables", logbook_weeks)
    check("Logbook can be saved", logbook_free)

    from app.integrations import logbook_docx
    print("\n  Logbook settings in profiles/logbook.json (shared by both modes; check them):")
    for name in logbook_docx.SETTINGS:
        print(f"    {name}: {logbook_docx.get_setting(name)}")
    print("\n  Edit access to the Sheet is only known at the first write: share it with the")
    print("  service-account email as Editor.")

    if problems:
        print(f"\n{len(problems)} problem(s): {', '.join(problems)}. Fix them before writing.")
        sys.exit(1)
    print(f"\nAll checks passed for the {mode} files.")


if __name__ == "__main__":
    main()
