"""Rough notes in, a structured draft entry out.

The LLM decides the wording and which field each fact goes in;
the code checks the result before anything uses it.
"""

import sys

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
