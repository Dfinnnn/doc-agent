"""decide() checks everything before it writes: a bad request writes nothing.

Uses a fake store with no real documents, so nothing here reaches the Sheet or the logbook.
"""

import pytest

from app.agent import approval
from app.agent.approval import decide, needs_choice


@pytest.fixture(autouse=True)
def logbook_not_locked(monkeypatch):
    """The lock check opens the real logbook file; tests pretend it is free."""
    monkeypatch.setattr(approval.logbook_docx, "check_writable", lambda: None)


class FakeStore:
    def __init__(self, ops):
        self.ops = ops
        self.updates = []  # (id, status) for every status change decide() makes

    def operations(self, run_id, status):
        return [op for op in self.ops if op["status"] == status]

    def update_operation(self, op_id, status, **details):
        self.updates.append((op_id, status))

    def update_proposal(self, op_id, proposal, issues):
        pass


def make_ops():
    """#1 has a same-date conflict, #2 has a block issue."""
    return [
        {"id": 1, "kind": "sheet_row", "target": "02/10/2026", "status": "proposed",
         "proposal": {"tasks": "x"}, "issues": [{"severity": "conflict", "text": "has content"}]},
        {"id": 2, "kind": "logbook_entry", "target": "02/10/2026", "status": "proposed",
         "proposal": {"activities": ["x"]}, "issues": [{"severity": "block", "text": "bad"}]},
    ]


def no_record(kind, data):
    pass


@pytest.mark.parametrize("chosen, choices", [
    ([9], {}),                 # a number that is not in the preview
    ([1], {}),                 # a conflict with no overwrite / append / skip answer
    ([1], {"1": "explode"}),   # an answer that is not allowed
])
def test_bad_request_writes_nothing(chosen, choices):
    store = FakeStore(make_ops())
    with pytest.raises(ValueError):
        decide(store, 1, chosen, choices, no_record)
    assert store.updates == []


def test_skip_and_block_are_not_written():
    store = FakeStore(make_ops())
    results = decide(store, 1, [1, 2], {"1": "skip"}, no_record)
    assert results == ["#1 skipped by you, not written", "#2 skipped: it has a block issue"]
    assert store.updates == [(1, "rejected"), (2, "rejected")]


def test_reject_all():
    store = FakeStore(make_ops())
    results = decide(store, 1, [], {}, no_record)
    assert results == ["#1 rejected, not written", "#2 rejected, not written"]


def test_locked_logbook_stops_before_anything_is_written(monkeypatch):
    def locked():
        raise PermissionError(13, "Permission denied", "log (Testing).docx")
    monkeypatch.setattr(approval.logbook_docx, "check_writable", locked)
    ops = make_ops()
    ops[1]["issues"] = []  # a normal logbook entry this time
    store, events = FakeStore(ops), []
    with pytest.raises(ValueError, match="Nothing was written. log \\(Testing\\).docx is open"):
        decide(store, 1, [1, 2], {"1": "overwrite"}, lambda kind, data: events.append(kind))
    assert store.updates == [] and events == []  # not even the Sheet row, no decision logged


def test_sheet_only_decision_does_not_need_the_logbook(monkeypatch):
    def locked():
        raise PermissionError(13, "Permission denied", "log.docx")
    monkeypatch.setattr(approval.logbook_docx, "check_writable", locked)
    store = FakeStore(make_ops())
    assert decide(store, 1, [1], {"1": "skip"}, no_record) == [
        "#1 skipped by you, not written", "#2 rejected, not written"]


def test_needs_choice():
    conflict, block = make_ops()
    assert needs_choice(conflict) == "conflict"
    assert needs_choice(block) is None  # it will not be written anyway
    refine = {**conflict, "proposal": {"tasks": "x", "overwrite": True}}
    assert needs_choice(refine) is None  # a refine of an existing entry is a chosen overwrite
    font = {"id": 3, "kind": "setting", "target": "font", "issues": [], "proposal": {}}
    assert needs_choice(font) == "scope"
    signed = {**font, "target": "signed_up_to_week"}
    assert needs_choice(signed) is None
