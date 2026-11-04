"""Talks to the University Logbook (.docx) through python-docx."""

import sys
from copy import deepcopy
from datetime import date, datetime, timedelta

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Pt

from app import config

# Layout and style come from profiles/logbook.json
PROFILE = config.LOGBOOK
# Week 1's Monday. Every week's dates are computed from this,
# never read from the header text (headers can contain copy-paste mistakes).
WEEK1_MONDAY = date.fromisoformat(PROFILE["week1_monday"])
WEEK_LABEL = PROFILE["week_label"]         # the first cell of every week table starts with this
FIRST_DAY_ROW = PROFILE["first_day_row"]   # row 0 = "WEEK: ...", row 1 = "DATE / ACTIVITIES"
DAYS_PER_WEEK = PROFILE["days_per_week"]   # Monday to Friday
# The row after the day rows is the supervisor's verify/stamp/comment row: never written
FONT_NAME = PROFILE["font_name"]
FONT_SIZE = PROFILE["font_size"]
DATE_FORMAT = PROFILE["date_format"]       # full year, since the internship runs into 2027
EMPTY_LINES = PROFILE["empty_lines"]       # empty lines in a new Activities cell, as in the template
BULLET = PROFILE["bullet"]
BULLET_INDENT = Pt(PROFILE["bullet_indent_pt"])  # wrapped lines line up with the text, not the bullet


def week_dates(week):
    """Return the 5 dates (Mon-Fri) of a week, e.g. week_dates(1) -> 17/08 to 21/08."""
    monday = WEEK1_MONDAY + timedelta(weeks=week - 1)
    return [monday + timedelta(days=i) for i in range(DAYS_PER_WEEK)]


def open_logbook():
    """Open the logbook file set in LOGBOOK_PATH."""
    return Document(config.LOGBOOK_PATH)


def find_week_tables(doc):
    """Return the week tables in document order: the 1st is Week 1, the 2nd Week 2..."""
    return [t for t in doc.tables if t.cell(0, 0).text.strip().startswith(WEEK_LABEL)]


def cell_text(cell):
    """A cell's text, one line per paragraph, without the empty lines."""
    lines = [p.text.strip() for p in cell.paragraphs]
    return "\n".join(line for line in lines if line)


def read_logbook(doc=None):
    """Return one record (a dict) per day row, with its computed date."""
    doc = doc or open_logbook()
    records = []
    for week, table in enumerate(find_week_tables(doc), start=1):
        for offset, day in enumerate(week_dates(week)):
            row = table.rows[FIRST_DAY_ROW + offset]
            records.append({
                "week": week,
                "date": day,
                "date_text": cell_text(row.cells[0]),
                "activities": cell_text(row.cells[1]),
            })
    return records


def set_cell_text(cell, lines, bullets=False):
    """Replace a cell's text, one paragraph per line, in the logbook font.

    Reuses the cell's existing (empty) paragraphs so the row keeps its height
    and alignment; adds more paragraphs only if there are more lines.
    With bullets=True, each line becomes a left-aligned "• " point.
    """
    paragraphs = cell.paragraphs
    while len(paragraphs) < len(lines):
        paragraphs.append(cell.add_paragraph())
    for i, paragraph in enumerate(paragraphs):
        for run in paragraph.runs:
            run._r.getparent().remove(run._r)  # clear what was there
        if i >= len(lines):
            continue
        text = lines[i]
        if bullets:
            text = f"{BULLET} {text}"
            paragraph.alignment = WD_ALIGN_PARAGRAPH.LEFT
            paragraph.paragraph_format.left_indent = BULLET_INDENT
            paragraph.paragraph_format.first_line_indent = -BULLET_INDENT
        run = paragraph.add_run(text)
        run.font.name = FONT_NAME
        run.font.size = Pt(FONT_SIZE)


def day_row(doc, day):
    """Find the table row for a date by computing its week and weekday."""
    if day < WEEK1_MONDAY:
        raise ValueError(f"{day:%d/%m/%Y} is before Week 1")
    if day.weekday() >= DAYS_PER_WEEK:
        raise ValueError(f"{day:%d/%m/%Y} is a weekend; weekend rows come in Part 9")

    week = (day - WEEK1_MONDAY).days // 7 + 1
    tables = find_week_tables(doc)
    if week > len(tables):
        raise ValueError(f"Week {week} table does not exist yet; add it first")
    # Only the 5 day rows can be reached, so the supervisor row is never touched
    return tables[week - 1].rows[FIRST_DAY_ROW + day.weekday()]


def write_entry(day, activities, overwrite=False):
    """Write one day's Date and Activities cells, then save the file.

    activities is a list of tasks; each becomes one bullet point.
    Refuses if the row already has text, unless overwrite=True.
    """
    doc = open_logbook()
    row = day_row(doc, day)
    date_cell, activities_cell = row.cells[0], row.cells[1]

    if (cell_text(date_cell) or cell_text(activities_cell)) and not overwrite:
        raise ValueError(f"{day:%d/%m/%Y} already has an entry; not overwriting it")

    # Drop any bullet the text already has ("*", "-", "•"), so it is never doubled
    tasks = [task.strip().lstrip("*-•").strip() for task in activities]
    set_cell_text(date_cell, [f"{day:{DATE_FORMAT}}"])
    set_cell_text(activities_cell, [task for task in tasks if task], bullets=True)
    doc.save(config.LOGBOOK_PATH)  # fails if the file is open in Word


def week_header(week):
    """The header text, built from the computed dates, e.g. "WEEK: Week 5 (14/09 – 18/09)"."""
    days = week_dates(week)
    return f"{WEEK_LABEL} Week {week} ({days[0]:%d/%m} – {days[-1]:%d/%m})"


def clear_cell(cell, keep_lines=1):
    """Empty a cell but keep its look; keep at most keep_lines paragraphs."""
    for paragraph in cell.paragraphs[keep_lines:]:
        paragraph._p.getparent().remove(paragraph._p)
    for paragraph in cell.paragraphs:
        for run in paragraph.runs:
            run._r.getparent().remove(run._r)


def add_week_table():
    """Add the next week's table after the last one, then save the file.

    Copies the last week table (and the page break before it) so the layout
    matches, writes a header from the computed dates and empties the day rows.
    The supervisor row is copied as it is and never filled in.
    Returns the new week number.
    """
    doc = open_logbook()
    tables = find_week_tables(doc)
    week = len(tables) + 1
    last = tables[-1]._tbl

    # What sits between the last two week tables (empty line + page break)
    body = list(doc.element.body.iterchildren())
    before_last = tables[-2]._tbl if len(tables) > 1 else last
    separator = body[body.index(before_last) + 1 : body.index(last)]

    # Insert: last table, copied separator, new table
    anchor = last
    for element in separator + [last]:
        copy = deepcopy(element)
        anchor.addnext(copy)
        anchor = copy
    new_table = find_week_tables(doc)[week - 1]

    # Header: keep the first run's look (bold), replace the text
    header = new_table.cell(0, 0).paragraphs[0]
    header.runs[0].text = week_header(week)
    for run in header.runs[1:]:
        run._r.getparent().remove(run._r)

    # Empty the day rows
    for offset in range(DAYS_PER_WEEK):
        row = new_table.rows[FIRST_DAY_ROW + offset]
        clear_cell(row.cells[0])
        clear_cell(row.cells[1], keep_lines=EMPTY_LINES)

    doc.save(config.LOGBOOK_PATH)  # fails if the file is open in Word
    return week


if __name__ == "__main__":
    # Usage:
    #   python -m app.integrations.logbook_docx                              (print weeks)
    #   python -m app.integrations.logbook_docx write 17/08/2026 "task one" "task two"
    #   (add --overwrite at the end to replace an existing entry)
    #   python -m app.integrations.logbook_docx add-week                       (add the next week)
    if len(sys.argv) > 1 and sys.argv[1] == "add-week":
        print(f"Added Week {add_week_table()}")
        sys.exit()

    if len(sys.argv) > 1 and sys.argv[1] == "write":
        args = sys.argv[2:]
        overwrite = "--overwrite" in args
        args = [a for a in args if a != "--overwrite"]
        day = datetime.strptime(args[0], DATE_FORMAT).date()
        write_entry(day, args[1:], overwrite=overwrite)
        print(f"Wrote {day:{DATE_FORMAT}}")
        sys.exit()

    doc = open_logbook()
    for week, table in enumerate(find_week_tables(doc), start=1):
        days = week_dates(week)
        print(f"Week {week}: computed {days[0]:%d/%m} - {days[-1]:%d/%m}"
              f"   header says: {table.cell(0, 0).text.strip()!r}")

    records = read_logbook(doc)
    filled = [r for r in records if r["activities"]]
    print(f"\n{len(records)} day rows, {len(filled)} with activities")
    for r in filled:
        print(f"  {r['date']:%d/%m/%Y}  {r['activities'][:60]!r}")
