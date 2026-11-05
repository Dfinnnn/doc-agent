"""SQLite store: runs, their event timeline, and the operations (proposed changes).

One file, data/agent.db (ignored by git: it holds before/after values from the records).
"""

import json
import sqlite3
from datetime import datetime

from app import config

DB_PATH = config.PROJECT_ROOT / "data" / "agent.db"

# Operation statuses, in the order an operation moves through them
PROPOSED, APPROVED, REJECTED, WRITTEN, FAILED, UNDONE = (
    "proposed", "approved", "rejected", "written", "failed", "undone")

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY,
    started_at TEXT, message TEXT, provider TEXT, model TEXT
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY,
    run_id INTEGER REFERENCES runs(id),
    at TEXT, kind TEXT, data TEXT
);
CREATE TABLE IF NOT EXISTS operations (
    id INTEGER PRIMARY KEY,
    run_id INTEGER REFERENCES runs(id),
    kind TEXT, target TEXT,
    proposal TEXT, issues TEXT,
    before TEXT, after TEXT,
    status TEXT, error TEXT,
    created_at TEXT, updated_at TEXT
);
"""

JSON_FIELDS = ("data", "proposal", "issues", "before", "after")


def now():
    return datetime.now().isoformat(timespec="seconds")


def to_dict(row):
    """A database row -> dict, with the JSON columns turned back into Python values."""
    out = dict(row)
    for field in JSON_FIELDS:
        if out.get(field) is not None:
            out[field] = json.loads(out[field])
    return out


class Store:
    def __init__(self, path=DB_PATH):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row  # rows behave like dicts
        self.conn.executescript(SCHEMA)

    def _insert(self, sql, values):
        cursor = self.conn.execute(sql, values)
        self.conn.commit()
        return cursor.lastrowid

    # ---- runs and events ----

    def start_run(self, message, provider, model):
        return self._insert(
            "INSERT INTO runs (started_at, message, provider, model) VALUES (?, ?, ?, ?)",
            (now(), message, provider, model))

    def log(self, run_id, kind, data):
        """Add one event to a run's timeline, e.g. log(1, "tool_call", {...})."""
        return self._insert(
            "INSERT INTO events (run_id, at, kind, data) VALUES (?, ?, ?, ?)",
            (run_id, now(), kind, json.dumps(data, ensure_ascii=False, default=str)))

    def events(self, run_id):
        rows = self.conn.execute("SELECT * FROM events WHERE run_id = ? ORDER BY id", (run_id,))
        return [to_dict(row) for row in rows]

    def last_written_run(self):
        """The most recent run with written changes that undo can restore: Sheet rows and
        logbook entries, not new tabs or week tables (None if there is none)."""
        row = self.conn.execute(
            "SELECT MAX(run_id) FROM operations WHERE status = ? "
            "AND kind IN ('sheet_row', 'logbook_entry')", (WRITTEN,)).fetchone()
        return row[0]

    def last_run_id(self):
        row = self.conn.execute("SELECT MAX(id) FROM runs").fetchone()
        return row[0]

    # ---- operations ----

    def add_operation(self, run_id, kind, target, proposal, issues):
        """Save a proposed change. Returns its id."""
        return self._insert(
            "INSERT INTO operations (run_id, kind, target, proposal, issues, status, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (run_id, kind, target, json.dumps(proposal, ensure_ascii=False),
             json.dumps(issues, ensure_ascii=False), PROPOSED, now(), now()))

    def operation(self, op_id):
        row = self.conn.execute("SELECT * FROM operations WHERE id = ?", (op_id,)).fetchone()
        return to_dict(row) if row else None

    def operations(self, run_id, status=None):
        sql, values = "SELECT * FROM operations WHERE run_id = ?", [run_id]
        if status:
            sql, values = sql + " AND status = ?", values + [status]
        return [to_dict(row) for row in self.conn.execute(sql + " ORDER BY id", values)]

    def update_proposal(self, op_id, proposal, issues):
        """Replace a still-proposed operation's content (used when refining a draft)."""
        self.conn.execute(
            "UPDATE operations SET proposal = ?, issues = ?, updated_at = ? WHERE id = ?",
            (json.dumps(proposal, ensure_ascii=False), json.dumps(issues, ensure_ascii=False),
             now(), op_id))
        self.conn.commit()

    def update_operation(self, op_id, status, **fields):
        """Change an operation's status, and optionally before / after / error."""
        sets, values = ["status = ?", "updated_at = ?"], [status, now()]
        for name in ("before", "after", "error"):
            if name in fields:
                value = fields[name]
                sets.append(f"{name} = ?")
                values.append(value if name == "error" else json.dumps(value, ensure_ascii=False))
        self.conn.execute(f"UPDATE operations SET {', '.join(sets)} WHERE id = ?", values + [op_id])
        self.conn.commit()
