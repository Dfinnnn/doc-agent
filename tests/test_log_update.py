"""log_update proposes nothing for a date with nothing to log, and an empty change
can never be approved. The LLM draft and the documents are replaced by fakes here,
so these tests call no API and touch no file."""

from datetime import date

import pytest

from app.tools import registry
from app.tools.registry import BLOCK, LEAVE_WORDING, empty_change_issues


def draft(day, **fields):
    """One draft as draft_log_update returns it; every field empty unless given."""
    base = {"date": day, "source_notes": "", "mode": "", "clock_in": "", "clock_out": "",
            "break_time": "", "tasks": "", "challenges": "", "learnings": "", "next_steps": "",
            "activities": [], "missing": []}
    return base | fields


@pytest.fixture
def run(monkeypatch):
    """Run log_update with the given drafts; returns (result, saved proposals)."""
    saved = []

    def fake_save(kind, target, proposal, issues):
        saved.append({"kind": kind, "target": target, "proposal": proposal,
                      "issues": issues + empty_change_issues(kind, proposal)})
        return len(saved)

    monkeypatch.setattr(registry, "save_proposal", fake_save)
    monkeypatch.setattr(registry, "propose_week_tables", lambda week: [])
    monkeypatch.setattr(registry, "check_draft_against_notes", lambda notes, d: [])
    monkeypatch.setattr(registry, "date_in_notes", lambda day, notes, today: True)
    monkeypatch.setattr(registry.logbook_docx, "read_logbook", lambda: [])
    monkeypatch.setattr(registry.sheets, "read_month", lambda year, month: [])

    def go(drafts, notes="notes"):
        monkeypatch.setattr(registry, "draft_log_update", lambda n, today: drafts)
        return registry.log_update(notes), saved
    return go


def test_empty_notes_propose_nothing(run):
    result, saved = run([draft("05/11/2026")], notes="today:")
    assert saved == []
    assert result["days"] == []
    assert result["not_proposed"][0]["date"] == "05/11/2026"


def test_tasks_propose_a_sheet_row_and_a_logbook_entry(run):
    result, saved = run([draft("04/11/2026", tasks="Built X.", activities=["Built X."])])
    assert [s["kind"] for s in saved] == ["sheet_row", "logbook_entry"]
    assert all(not any(i["severity"] == BLOCK for i in s["issues"]) for s in saved)


def test_leave_day_gets_the_plain_wording(run):
    result, saved = run([draft("06/11/2026", mode="Leave")])  # a Friday
    entry = next(s for s in saved if s["kind"] == "logbook_entry")
    assert entry["proposal"]["activities"] == [LEAVE_WORDING["Leave"]]
    assert entry["proposal"]["bullets"] is False


def test_only_times_update_the_sheet_but_not_the_logbook(run):
    result, saved = run([draft("04/11/2026", clock_in="09:00", clock_out="18:00")])
    assert [s["kind"] for s in saved] == ["sheet_row"]


@pytest.mark.parametrize("kind, proposal", [
    ("logbook_entry", {"activities": []}),
    ("logbook_entry", {"activities": ["  "]}),
    ("sheet_row", {"date": "05/11/2026", "source": "today:"}),
])
def test_empty_changes_are_blocked(kind, proposal):
    assert [i["severity"] for i in empty_change_issues(kind, proposal)] == [BLOCK]


def test_changes_with_content_are_not_blocked():
    assert empty_change_issues("logbook_entry", {"activities": ["Built X."]}) == []
    assert empty_change_issues("sheet_row", {"mode": "WFO"}) == []
    assert date(2026, 11, 6).weekday() == 4  # the leave test above uses a weekday
