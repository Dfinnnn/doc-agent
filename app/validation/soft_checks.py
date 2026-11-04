"""Soft checks: an LLM compares a draft with the notes it came from.

The LLM can be wrong in both directions, so every finding is labelled
"possible" and only warns. The rule checks in checks.py stay the detector.
"""

import json
import sys

from app.llm.draft import draft_entry
from app.llm.openai_compat import OpenAICompatLLM
from app.validation.checks import check_draft_against_notes, finding

SOFT_CHECK_PROMPT = """You check a draft log entry against the rough notes it was written from.

List every phrase in the draft that adds information the notes do not contain:
new objects, tools, results, causes, numbers, people or details. Rewording and
fuller phrasing of the same facts is fine and must not be listed.

Reply with JSON only: {"added": [{"field": "<draft field>", "text": "<phrase>", "reason": "<short>"}]}
Reply {"added": []} if nothing was added.
"""


def soft_check_draft(notes, draft, llm=None):
    """Ask the LLM which parts of the draft are not supported by the notes."""
    llm = llm or OpenAICompatLLM.from_config()
    reply = llm.chat_json(SOFT_CHECK_PROMPT, json.dumps({"notes": notes, "draft": draft}))
    findings = []
    for item in reply.get("added", []):
        if not isinstance(item, dict):
            continue
        findings.append(finding(
            f"draft {item.get('field', '?')}",
            f"Possible added fact: '{item.get('text', '')}'",
            item.get("reason", "not found in the notes"), "warn",
            "remove it, or add it to the notes if it is true"))
    return findings


if __name__ == "__main__":
    # Usage: python -m app.validation.soft_checks "rough notes"
    notes = sys.argv[1]
    draft = draft_entry(notes)
    print("Draft activities:")
    for activity in draft["activities"]:
        print(f"  - {activity}")
    for label, findings in (("Rule checks", check_draft_against_notes(notes, draft)),
                            ("AI soft check", soft_check_draft(notes, draft))):
        print(f"\n{label}: {len(findings) or 'nothing found'}")
        for f in findings:
            print(f"  [{f['severity']}] {f['where']}: {f['what']} ({f['cause']})")
