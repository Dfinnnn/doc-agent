"""The web API: the browser talks to the agent through these endpoints.

It only calls the same code the terminal uses (handle_message, decide, undo);
it never writes to the documents by itself.

Run with: python -m uvicorn app.api.main:app --host 127.0.0.1 --port 8000
"""

import threading

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from app import config
from app.agent.approval import decide, describe, pending_ops, refine_ops
from app.agent.loop import Session, handle_message, tell_agent

app = FastAPI(title="Document Assistant Agent")

# One chat for the one local user, made on the first message (like the terminal's).
state = {"session": None, "waiting_run": None}
# One request at a time: the session and the documents are shared, so a double
# click must not run two orders at once.
lock = threading.Lock()


def get_session():
    if state["session"] is None:
        state["session"] = Session()
    return state["session"]


def proposals(store, run_id):
    """The changes of a run that wait for approval, with the same preview text
    the terminal shows. Nothing is written."""
    return [{"id": op["id"], "kind": op["kind"], "target": op["target"],
             "issues": op["issues"], "preview": describe(op)}
            for op in pending_ops(store, run_id)]


def refuse_if_waiting(session):
    """Same rule as the terminal: decide on a preview before the next order."""
    waiting = state["waiting_run"]
    if waiting is not None and pending_ops(session.store, waiting):
        raise HTTPException(409, f"Run {waiting} has changes waiting for approval. "
                                 f"Approve or reject them first.")


class ChatRequest(BaseModel):
    """What the browser sends: {"text": "..."}. FastAPI checks it is there."""
    text: str


@app.get("/api/health")
def health():
    """A quick check that the server is running and which model it will use."""
    return {"status": "ok", "provider": config.LLM_PROVIDER, "model": config.LLM_MODEL}


@app.post("/api/chat")
def chat(request: ChatRequest):
    """Send one message to the agent. Returns its answer and any proposed changes.
    Proposed changes are only saved for approval; nothing is written here."""
    with lock:
        session = get_session()
        refuse_if_waiting(session)
        try:
            reply = handle_message(session, request.text)
        except ValueError as error:  # e.g. an empty message
            raise HTTPException(400, str(error))
        waiting_ops = proposals(session.store, reply["run_id"]) if reply["kind"] == "answer" else []
        state["waiting_run"] = reply["run_id"] if waiting_ops else None
        if waiting_ops:  # the same activity-log event the terminal records
            session.recorder(reply["run_id"])("preview", {"operations": [o["id"] for o in waiting_ops]})
        return {**reply, "proposals": waiting_ops}


@app.get("/api/runs/{run_id}/proposals")
def run_proposals(run_id: int):
    """Show a run's changes that wait for approval again (e.g. after a page reload)."""
    with lock:
        return {"run_id": run_id, "proposals": proposals(get_session().store, run_id)}


class RefineRequest(BaseModel):
    """Which waiting drafts to rewrite, and how: like "r 128 make it shorter"."""
    ids: list[int]
    instruction: str


@app.post("/api/runs/{run_id}/refine")
def run_refine(run_id: int, request: RefineRequest):
    """Rewrite waiting drafts (same code as "r <n> <change>" in the terminal).
    Nothing is written: the drafts stay waiting for approval, with fresh checks.
    Returns each draft before and now, plus the updated preview."""
    if not request.instruction.strip():
        raise HTTPException(400, "Say what to change, e.g. 'make it shorter'.")
    with lock:
        session = get_session()
        try:
            items = refine_ops(session.store, run_id, request.ids, request.instruction,
                               session.recorder(run_id))
        except ValueError as error:  # e.g. a number not in the preview
            raise HTTPException(400, str(error))
        return {"run_id": run_id, "refined": items,
                "proposals": proposals(session.store, run_id)}


class DecideRequest(BaseModel):
    """The user's decision on a preview.
    approved: the change numbers to write; the rest are rejected ([] = reject all).
    choices:  {"<number>": answer} for changes that need one:
              "overwrite" / "append" / "skip" for a date that already has content,
              "future" / "all" for a format change (new entries only / also existing)."""
    approved: list[int] = []
    choices: dict[str, str] = {}


@app.post("/api/runs/{run_id}/decide")
def run_decide(run_id: int, request: DecideRequest):
    """Approve or reject a run's proposed changes. The writing is done by decide(),
    the same code the terminal uses: it checks everything first, so a bad request
    writes nothing."""
    with lock:
        session = get_session()
        if not pending_ops(session.store, run_id):
            raise HTTPException(404, f"Run {run_id} has no changes waiting for approval.")
        record = session.recorder(run_id)
        try:
            results = decide(session.store, run_id, request.approved, request.choices,
                             record, answer="(browser)")
        except ValueError as error:  # e.g. a number not in the preview, a missing choice
            raise HTTPException(400, str(error))
        tell_agent(session.messages, results)  # so the agent knows what was written
        if state["waiting_run"] == run_id:
            state["waiting_run"] = None
        return {"run_id": run_id, "results": results}


class UndoRequest(BaseModel):
    """ids: the change numbers to undo; [] = the last approved changes (like "undo")."""
    ids: list[int] = []


@app.post("/api/undo")
def undo_changes(request: UndoRequest):
    """Put written changes back to their old values. Goes through handle_message,
    exactly like typing "undo" or "undo 3,5" in the terminal, with the same safety
    checks (e.g. no undo if the document was changed after the write)."""
    text = "undo " + ",".join(map(str, request.ids)) if request.ids else "undo"
    with lock:
        session = get_session()
        refuse_if_waiting(session)
        reply = handle_message(session, text)
        return {"run_id": reply["run_id"], "results": reply["text"].splitlines()}


@app.get("/api/runs")
def runs(limit: int = 20):
    """The newest runs for the activity panel, with how many changes are in each status."""
    with lock:
        return {"runs": get_session().store.recent_runs(limit), "waiting_run": state["waiting_run"]}


@app.get("/api/runs/{run_id}/events")
def run_events(run_id: int):
    """One run's activity log: every event (message, LLM call, tool call and result,
    preview, decision, write, undo, error) in order, as stored in SQLite."""
    with lock:
        store = get_session().store
        return {"run_id": run_id, "events": store.events(run_id),
                "operations": [{k: op[k] for k in ("id", "kind", "target", "status", "error")}
                               for op in store.operations(run_id)]}
