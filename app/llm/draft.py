"""Rough notes in, a structured draft entry out.

The LLM decides the wording and which field each fact goes in;
the code checks the result before anything uses it.
"""

import json
import re
import sys
from datetime import date

from app.llm.openai_compat import OpenAICompatLLM

# Field name -> expected type. Sheet fields are text; logbook activities and
# missing are lists of short strings.
DRAFT_FIELDS = {
    "tasks": str,
    "challenges": str,
    "learnings": str,
    "next_steps": str,
    "activities": list,
    "missing": list,
}

SYSTEM_PROMPT = """You turn an intern's rough work notes into a draft log entry.

Reply with JSON only, with exactly these keys:
- "tasks": what was done. Formal and concise; one sentence per task, separated by "\\n".
- "challenges": problems faced. Same style.
- "learnings": what was learned. Same style.
- "next_steps": what comes next. Same style.
- "activities": a list of short, plain, direct sentences for a university logbook,
  one per task (no bullet characters).
- "missing": a list of the field names above that the notes give no information for.

Rules:
- Use only facts stated in the notes. Rewording is allowed; new facts are not.
- Never invent activities, causes, results, tools or learnings.
- If the notes say nothing for a field, set it to "" (or [] for lists) and add its
  name to "missing". Do not guess.
"""


def check_draft(data):
    """Return the draft if every field is present with the right type; otherwise raise."""
    problems = []
    for field, expected in DRAFT_FIELDS.items():
        if field not in data:
            problems.append(f"'{field}' is missing")
        elif not isinstance(data[field], expected):
            problems.append(f"'{field}' should be {expected.__name__}, got {type(data[field]).__name__}")
        elif expected is list and not all(isinstance(item, str) for item in data[field]):
            problems.append(f"'{field}' should only contain text")
    if problems:
        raise ValueError("The draft is not usable: " + "; ".join(problems))
    # Keep only our fields, in our order (ignore anything extra the model added)
    return {field: data[field] for field in DRAFT_FIELDS}


def draft_entry(notes, llm=None):
    """Ask the LLM for a draft from rough notes, then check it."""
    llm = llm or OpenAICompatLLM.from_config()
    return check_draft(llm.chat_json(SYSTEM_PROMPT, notes))


LOG_UPDATE_PROMPT = """You turn an intern's rough work notes into log entries, one per date.
Today is {today}. Dates in the notes are dd/mm (day first). Notes with no date are for today.

Reply with JSON only: {{"days": [ {{
  "date": "dd/mm" exactly as in the notes (add "/yyyy" only if the notes give a year),
  "source_notes": the exact lines of the notes for this date, copied unchanged,
  "tasks": Sheet style (formal, precise). Every task in the notes becomes its own
           paragraph of about 2 sentences, separated by "\n". Name the project,
           client or system the notes mention. Never merge or drop tasks.,
  "challenges": same style, only if the notes state a problem or difficulty,
  "learnings": same style, only if the notes state something learned,
  "next_steps": same style, only if the notes state what comes next,
  "activities": logbook style (plain, direct): a list with one entry per task,
                1-2 sentences each, same facts as "tasks",
  "mode": one of WFO, WFH, Leave, Medical Leave, Public Holidays ONLY if the notes say it, else "",
  "clock_in", "clock_out", "break_time": "HH:MM" ONLY if the notes give them, else "",
  "missing": names of the fields above the notes give no information for
}} ] }}

Rules:
- "Elaborate" means fuller wording of the facts given (what, for whom, why, the
  outcome if stated). Never add facts: no new tools, results, causes, numbers,
  people or details that are not in the notes.
- If the notes do not state something, leave that field "" (or []) and list it in "missing".
"""

TIME_FIELDS = ["clock_in", "clock_out", "break_time"]


def resolve_date(text, today):
    """"21/10" -> the most recent 21/10 that is not after today (code decides the year)."""
    parts = [int(p) for p in text.strip().split("/")]
    if len(parts) == 3:
        return date(parts[2], parts[1], parts[0])
    day, month = parts
    candidate = date(today.year, month, day)
    return candidate if candidate <= today else date(today.year - 1, month, day)


def draft_log_update(notes, today, llm=None):
    """Draft one entry per date from rough notes. Returns a list of checked day drafts."""
    llm = llm or OpenAICompatLLM.from_config()
    reply = llm.chat_json(LOG_UPDATE_PROMPT.format(today=f"{today:%A %d/%m/%Y}"), notes)
    days = reply.get("days")
    if not isinstance(days, list) or not days:
        raise ValueError("The draft has no days in it")
    drafts = []
    for day in days:
        draft = check_draft(day)  # same field and type checks as a single draft
        extra = {field: str(day.get(field) or "") for field in ["source_notes", "mode", *TIME_FIELDS]}
        try:
            extra["date"] = f"{resolve_date(str(day.get('date', '')), today):%d/%m/%Y}"
        except (ValueError, TypeError):
            raise ValueError(f"The draft has an unreadable date: {day.get('date')!r}")
        for field in ("tasks", "challenges", "learnings", "next_steps"):
            draft[field] = re.sub(r"\n\s*\n+", "\n", draft[field]).strip()  # no blank lines
        drafts.append({**extra, **draft})
    return drafts


SYNC_PROMPT = """You rewrite company work-log entries for a university logbook.

You get JSON {"days": [{"date": "dd/mm/yyyy", "tasks": "..."}]}. For each day, rewrite
"tasks" in plain, direct logbook style: a list with one entry per task (each line of
"tasks" is one task), 1-2 sentences each.

Rules: keep exactly the same facts; never add tools, results, causes, numbers or
details; never merge or drop tasks.

Reply with JSON only: {"days": [{"date": "dd/mm/yyyy", "activities": ["...", "..."]}]}
"""


def draft_sync(days, llm=None):
    """Rewrite Sheet tasks in logbook style. days: [{"date", "tasks"}].
    Returns {date: [activities]} for the days the model answered correctly."""
    llm = llm or OpenAICompatLLM.from_config()
    reply = llm.chat_json(SYNC_PROMPT, json.dumps({"days": days}, ensure_ascii=False))
    out = {}
    for day in reply.get("days", []):
        activities = day.get("activities") if isinstance(day, dict) else None
        if isinstance(activities, list) and all(isinstance(a, str) for a in activities):
            out[str(day.get("date"))] = [a for a in activities if a.strip()]
    return out


REFINE_PROMPT = """You refine an intern's log entry following the user's instruction.

You get JSON {"fields": {...}, "facts": "...", "instruction": "..."}.
"fields" is the current text; "facts" is what the entry is based on (notes or the
work log). Rewrite only what the instruction asks; keep the same keys and types
("activities" stays a list with one entry per task).

Rules: use only facts from "facts" or stated in the instruction itself; never add
tools, results, causes, numbers or details; never drop tasks unless told to.

Reply with JSON only: {"fields": {...same keys...}, "changes": "one or two sentences: what you changed and why"}
"""


def refine_text(fields, facts, instruction, llm=None):
    """Rewrite some fields following an instruction. Returns (new fields, changes)."""
    llm = llm or OpenAICompatLLM.from_config()
    reply = llm.chat_json(REFINE_PROMPT, json.dumps(
        {"fields": fields, "facts": facts, "instruction": instruction}, ensure_ascii=False))
    new = reply.get("fields")
    if not isinstance(new, dict) or set(new) != set(fields):
        raise ValueError("The refined draft does not have the same fields")
    for key, value in new.items():
        if not isinstance(value, type(fields[key])):
            raise ValueError(f"The refined '{key}' has the wrong type")
    if "activities" in new:
        new["activities"] = [a for a in new["activities"] if a.strip()]
    return new, str(reply.get("changes", ""))


if __name__ == "__main__":
    # Usage: python -m app.llm.draft "rough notes about the day"
    draft = draft_entry(sys.argv[1])
    for field, value in draft.items():
        print(f"\n{field}:")
        if isinstance(value, list):
            for item in value:
                print(f"  - {item}")
        else:
            print("  " + value.replace("\n", "\n  "))
