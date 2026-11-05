"""Unit tests for the SQLite store. Each test gets its own empty database file."""

import pytest

from app.store.db import PROPOSED, UNDONE, WRITTEN, Store


@pytest.fixture
def store(tmp_path):
    # tmp_path is a fresh temporary folder pytest makes for each test
    return Store(tmp_path / "test.db")


def test_run_and_events_are_kept_in_order(store):
    run = store.start_run("what did I do on 2/10?", "gemini", "gemini-3.6-flash")
    store.log(run, "user_message", {"text": "what did I do on 2/10?"})
    store.log(run, "tool_call", {"name": "read_sheet", "arguments": {"date_from": "02/10/2026"}})
    events = store.events(run)
    assert [e["kind"] for e in events] == ["user_message", "tool_call"]
    assert events[1]["data"]["arguments"] == {"date_from": "02/10/2026"}
    assert store.last_run_id() == run


def test_operation_moves_from_proposed_to_written_with_before_and_after(store):
    run = store.start_run("log it", "gemini", "m")
    op = store.add_operation(run, "logbook_entry", "18/08/2026", {"activities": ["a"]}, [])
    assert store.operation(op)["status"] == PROPOSED

    store.update_operation(op, WRITTEN, before={"activities": ""}, after={"activities": "• a"})
    saved = store.operation(op)
    assert saved["status"] == WRITTEN
    assert saved["before"] == {"activities": ""}
    assert saved["after"] == {"activities": "• a"}


def test_operations_can_be_filtered_by_status(store):
    run = store.start_run("x", "gemini", "m")
    first = store.add_operation(run, "sheet_row", "02/11/2026", {}, [])
    store.add_operation(run, "sheet_row", "03/11/2026", {}, [])
    store.update_operation(first, UNDONE)
    assert [o["target"] for o in store.operations(run, PROPOSED)] == ["03/11/2026"]
    assert len(store.operations(run)) == 2


def test_recent_runs_newest_first_with_status_counts(store):
    old = store.start_run("first", "gemini", "m")
    new = store.start_run("second", "gemini", "m")
    store.add_operation(new, "sheet_row", "02/11/2026", {}, [])
    written = store.add_operation(new, "sheet_row", "03/11/2026", {}, [])
    store.update_operation(written, WRITTEN)
    runs = store.recent_runs()
    assert [r["id"] for r in runs] == [new, old]
    assert runs[0]["statuses"] == {"proposed": 1, "written": 1}
    assert runs[1]["statuses"] == {}
    assert len(store.recent_runs(limit=1)) == 1


def test_missing_operation_returns_none(store):
    assert store.operation(999) is None
