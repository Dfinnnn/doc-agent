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
DATE_ALIGN = getattr(WD_ALIGN_PARAGRAPH, PROFILE["date_align"].upper())  # e.g. "center"
BULLET_INDENT = Pt(PROFILE["bullet_indent_pt"])  # wrapped lines line up with the text, not the bullet


def refresh_style():
    """Re-read the style settings after the profile changed (font, size, date alignment)."""
    global FONT_NAME, FONT_SIZE, DATE_ALIGN
    FONT_NAME = PROFILE["font_name"]
    FONT_SIZE = PROFILE["font_size"]
    DATE_ALIGN = getattr(WD_ALIGN_PARAGRAPH, PROFILE["date_align"].upper())


# Settings the user may change by chat (structure such as columns or tables may not).
# name -> (path in the profile, how to check and convert the value)
def _font_size(value):
    size = int(value)
    if not 8 <= size <= 20:
        raise ValueError("font size must be between 8 and 20")
    return size


def _align(value):
    value = str(value).lower()
    if value not in ("left", "center", "right"):
        raise ValueError("alignment must be left, center or right")
    return value


def _week(value):
    week = int(value)
    if not 0 <= week <= 24:
        raise ValueError("signed week must be between 0 and 24")
    return week


def _text(value):
    value = str(value).strip()
    if not value:
        raise ValueError("the value cannot be empty")
    return value


SETTINGS = {
    "font_name": (["font_name"], _text),
    "font_size": (["font_size"], _font_size),
    "date_align": (["date_align"], _align),
    "leave_wording": (["wording", "leave"], _text),
    "public_holiday_wording": (["wording", "public_holiday"], _text),
    "signed_up_to_week": (["signed_up_to_week"], _week),
}


def get_setting(name):
    value = PROFILE
    for key in SETTINGS[name][0]:
        value = value.get(key) if isinstance(value, dict) else None
    return value


def set_setting(name, value):
    """Change one whitelisted setting in the profile, save the file, refresh the style."""
    path = SETTINGS[name][0]
    target = PROFILE
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    config.save_profile("logbook", PROFILE)
    refresh_style()


def signed_week(day):
    """True if this date is in a week the supervisor has already signed (on the PDF)."""
    return week_of(day) <= PROFILE.get("signed_up_to_week", 0)


def week_dates(week):
    """Return the 5 dates (Mon-Fri) of a week, e.g. week_dates(1) -> 17/08 to 21/08."""
    monday = WEEK1_MONDAY + timedelta(weeks=week - 1)
    return [monday + timedelta(days=i) for i in range(DAYS_PER_WEEK)]


def open_logbook():
    """Open the logbook file set in LOGBOOK_PATH."""
    return Document(config.LOGBOOK_PATH)


def check_writable():
    """Raise PermissionError if the logbook can't be saved (e.g. it is open in Word).
    Opens the file for writing and closes it at once; nothing in it changes."""
    with open(config.LOGBOOK_PATH, "r+b"):
        pass


def find_week_tables(doc):
    """Return the week tables in document order: the 1st is Week 1, the 2nd Week 2..."""
    return [t for t in doc.tables if t.cell(0, 0).text.strip().startswith(WEEK_LABEL)]


def cell_text(cell):
    """A cell's text, one line per paragraph, without the empty lines."""
    lines = [p.text.strip() for p in cell.paragraphs]
    return "\n".join(line for line in lines if line)


def week_of(day):
    return (day - WEEK1_MONDAY).days // 7 + 1


def weekend_rows(table):
    """Extra rows for weekend work: after Friday, before the supervisor row (the last row)."""
    return table.rows[FIRST_DAY_ROW + DAYS_PER_WEEK:-1]


def parse_day(text):
    try:
        return datetime.strptime(text.strip(), DATE_FORMAT).date()
    except ValueError:
        return None


def read_logbook(doc=None):
    """Return one record (a dict) per day row. Mon-Fri dates are computed from the
    row position; weekend rows (extra rows) are dated by the date written in them."""
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
        for row in weekend_rows(table):
            date_text = cell_text(row.cells[0])
            day = parse_day(date_text)
            if day is None:
                continue  # an extra row without a readable date: the audit reports it
            records.append({"week": week, "date": day, "date_text": date_text,
                            "activities": cell_text(row.cells[1]), "weekend_row": True})
    return records


def set_cell_text(cell, lines, bullets=False, align=None):
    """Replace a cell's text, one paragraph per line, in the logbook font.

    Reuses the cell's existing (empty) paragraphs so the row keeps its height;
    adds more paragraphs only if there are more lines.
    With bullets=True, each line becomes a left-aligned "• " point.
    align sets every line's alignment, so all rows look the same whatever the
    template's empty rows had.
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
        if align is not None:
            paragraph.alignment = align
        if bullets:
            text = f"{BULLET} {text}"
            paragraph.alignment = WD_ALIGN_PARAGRAPH.LEFT
            paragraph.paragraph_format.left_indent = BULLET_INDENT
            paragraph.paragraph_format.first_line_indent = -BULLET_INDENT
        run = paragraph.add_run(text)
        run.font.name = FONT_NAME
        run.font.size = Pt(FONT_SIZE)


def week_table(doc, day):
    if day < WEEK1_MONDAY:
        raise ValueError(f"{day:%d/%m/%Y} is before Week 1")
    week = week_of(day)
    tables = find_week_tables(doc)
    if week > len(tables):
        raise ValueError(f"Week {week} table does not exist yet; add it first")
    return tables[week - 1]


def day_row(doc, day):
    """Find the table row for a date. Mon-Fri: computed from the date.
    Saturday/Sunday: the extra row with that date written in it, or None."""
    table = week_table(doc, day)
    if day.weekday() < DAYS_PER_WEEK:
        # Only the day rows can be reached, so the supervisor row is never touched
        return table.rows[FIRST_DAY_ROW + day.weekday()]
    for row in weekend_rows(table):
        if parse_day(cell_text(row.cells[0])) == day:
            return row
    return None


def add_weekend_row(doc, day):
    """Add an extra row for weekend work: a copy of Friday's row, emptied, placed
    after Friday (and after Saturday's row for a Sunday), before the supervisor row."""
    table = week_table(doc, day)
    friday = table.rows[FIRST_DAY_ROW + DAYS_PER_WEEK - 1]
    anchor = friday._tr
    for row in weekend_rows(table):
        if (parse_day(cell_text(row.cells[0])) or day) < day:
            anchor = row._tr  # keep Saturday before Sunday
    new_tr = deepcopy(friday._tr)
    anchor.addnext(new_tr)
    row = next(r for r in table.rows if r._tr is new_tr)
    clear_cell(row.cells[0])
    clear_cell(row.cells[1], keep_lines=EMPTY_LINES)
    return row


def write_entry(day, activities, overwrite=False, bullets=True):
    """Write one day's Date and Activities cells, then save the file.

    activities is a list of tasks; each becomes one bullet point
    (bullets=False for plain wording such as "On leave").
    Weekend work gets its own extra row in that week's table.
    Refuses if the row already has text, unless overwrite=True.
    """
    doc = open_logbook()
    row = day_row(doc, day) or add_weekend_row(doc, day)
    date_cell, activities_cell = row.cells[0], row.cells[1]

    if (cell_text(date_cell) or cell_text(activities_cell)) and not overwrite:
        raise ValueError(f"{day:%d/%m/%Y} already has an entry; not overwriting it")

    # Drop any bullet the text already has ("*", "-", "•"), so it is never doubled
    tasks = [task.strip().lstrip("*-•").strip() for task in activities]
    set_cell_text(date_cell, [f"{day:{DATE_FORMAT}}"], align=DATE_ALIGN)
    set_cell_text(activities_cell, [task for task in tasks if task], bullets=bullets,
                  align=WD_ALIGN_PARAGRAPH.LEFT)
    doc.save(config.LOGBOOK_PATH)  # fails if the file is open in Word


def restore_entry(day, date_text, activities_text):
    """Put back a day's cells exactly as read before (used by undo).

    activities_text is cell_text() output, e.g. "• task one\n• task two", or "".
    A weekend row that was empty before is removed again, since writing added it.
    """
    doc = open_logbook()
    row = day_row(doc, day)
    if row is None:
        return  # nothing there: already as before
    if day.weekday() >= DAYS_PER_WEEK and not date_text and not activities_text:
        row._tr.getparent().remove(row._tr)
    else:
        tasks = [line.lstrip(BULLET).strip() for line in activities_text.splitlines() if line.strip()]
        set_cell_text(row.cells[0], [date_text] if date_text else [], align=DATE_ALIGN)
        set_cell_text(row.cells[1], tasks, bullets=True, align=WD_ALIGN_PARAGRAPH.LEFT)
    doc.save(config.LOGBOOK_PATH)


def reformat_entries(replace_wording=None):
    """Apply the current font, size and alignment to every written entry, then save.

    replace_wording: {old text: new text} for plain rows such as "On leave".
    Only Date and Activities cells of day rows are touched; never the supervisor row.
    Returns how many entries were reformatted.
    """
    doc = open_logbook()
    count = 0
    for table in find_week_tables(doc):
        rows = table.rows[FIRST_DAY_ROW:FIRST_DAY_ROW + DAYS_PER_WEEK] + weekend_rows(table)
        for row in rows:
            date_text, activities = cell_text(row.cells[0]), cell_text(row.cells[1])
            if not (date_text or activities):
                continue
            lines = activities.splitlines()
            bullets = any(line.startswith(BULLET) for line in lines)
            tasks = [line.lstrip(BULLET).strip() for line in lines]
            if replace_wording and not bullets and activities in replace_wording:
                tasks = [replace_wording[activities]]
            set_cell_text(row.cells[0], [date_text] if date_text else [], align=DATE_ALIGN)
            set_cell_text(row.cells[1], tasks, bullets=bullets, align=WD_ALIGN_PARAGRAPH.LEFT)
            count += 1
    doc.save(config.LOGBOOK_PATH)
    return count


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

    # Empty the day rows, and drop any weekend rows copied from the previous week
    for offset in range(DAYS_PER_WEEK):
        row = new_table.rows[FIRST_DAY_ROW + offset]
        clear_cell(row.cells[0])
        clear_cell(row.cells[1], keep_lines=EMPTY_LINES)
    for row in weekend_rows(new_table):
        row._tr.getparent().remove(row._tr)

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
