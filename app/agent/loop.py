"""The agent loop, in the terminal: the LLM picks tools, our code runs them.

Run with: python -m app.agent.loop
"""

import json
import re
import time
from datetime import date

from app import config
from app.agent.approval import format_events, review, undo
from app.llm.openai_compat import OpenAICompatLLM
from app.store.db import WRITTEN, Store
from app.tools.registry import run_tool, set_context, tool_schemas

MAX_ROUNDS = 8  # tool rounds per message, so the loop can never run forever

SYSTEM_PROMPT = """You are a document assistant for an intern's two work records:
a company work log (Google Sheet) and a university logbook (.docx).

Today is {today}. Dates are written dd/mm (day first). If the year is not given,
use the most recent date that is not in the future. Pass dates to tools as dd/mm/yyyy.

What you can do now: read both records, search them, run the audit, and PROPOSE
changes. After your answer, the app shows the proposals to the user, who approves
or rejects them; the app does the writing and reports it, not you. To change a
draft that is waiting for approval, the user types "r <number> <change>" at the
approval prompt. If asked for something you cannot do, say no, not yet, and say
what you can do instead.

Rules:
- Answer only from tool results. If the records do not say, say so; never guess.
- Never invent a value the user did not give: no times, Mode, challenges,
  learnings, next steps, clients or causes from your own guess or from other
  days. Leave it out and ask the user for it.
- For a format change (font, size, date alignment, leave wording), call
  change_setting; the app asks the user whether to also reformat existing entries.
  Columns and tables cannot be changed by chat.
- To log work, call log_update with the user's notes exactly as written. Do not
  draft the entries yourself and do not use write_sheet_rows for this.
- If the user asks how something would be written, show the text; do not call
  write tools. Call write tools only when the user asks to log or write.
- Write tools only propose changes. Never say that something was saved or written.
- If a tool proposes nothing (0 changes), say so plainly and give the reasons from
  the result. Do not say that proposals are shown.
- Undo is done by the app: the user types "undo" (the last approved changes) or
  "undo 3" / "undo 3,5" (those change numbers). If asked to undo, tell them this.
- Read only what the question needs. Do not search other dates for examples.
- Records change between messages (the user approves writes and undoes them).
  When asked what a record holds now, read it again; do not answer from earlier results.
- If a request needs something your tools cannot do, say so and why.
- Keep answers short and plain.
"""


def short(text, limit=200):
    return text if len(text) <= limit else text[:limit] + "..."


def call_llm(llm, messages, tools, record):
    """One LLM call, timed and recorded in the activity log."""
    started = time.perf_counter()
    reply = llm.chat_tools(messages, tools)
    record("llm_call", {
        "model": config.LLM_MODEL,  # from our config, not the model's own claim
        "seconds": round(time.perf_counter() - started, 2),
        "tokens": reply["tokens"],
        "tools_chosen": [c["name"] for c in reply["tool_calls"]],
        "text": short(reply["content"] or ""),
    })
    messages.append(reply["message"])
    return reply


def ask(llm, messages, tools, record):
    """Run one user message through the loop; returns the final answer.

    record(kind, data) saves an event to the activity log.
    """
    for _ in range(MAX_ROUNDS):
        reply = call_llm(llm, messages, tools, record)
        if not reply["tool_calls"]:
            return reply["content"] or "(no answer)"
        for call in reply["tool_calls"]:
            print(f"  [tool] {call['name']}({json.dumps(call['arguments'], ensure_ascii=False)})")
            record("tool_call", {"name": call["name"], "arguments": call["arguments"]})
            result = run_tool(call["name"], call["arguments"])
            print(f"  [result] {short(result)}")
            record("tool_result", {"name": call["name"], "result": short(result, 500)})
            messages.append({"role": "tool", "tool_call_id": call["id"], "content": result})

    # Out of rounds: one last call with no tools, so it must answer with what it has
    print(f"  [limit] {MAX_ROUNDS} tool rounds used; asking for an answer without tools")
    record("limit", {"rounds": MAX_ROUNDS})
    messages.append({"role": "user", "content": "Tool limit reached. Answer now using only "
                     "the results above, and say what you could not check."})
    reply = call_llm(llm, messages, None, record)
    return reply["content"] or "(no answer)"


def undo_targets(text, store):
    """Which operations an undo command means, or None if it is not an undo command.

    "undo" / "undo all" / "undo last": every written change of the last run that wrote.
    "undo 6" / "undo 6,14" / "undo #6 #14": those operations.
    """
    text = text.strip().lower()
    if re.fullmatch(r"undo(\s+(for\s+)?(all|last|everything))?", text):
        run = store.last_written_run()
        return [op["id"] for op in store.operations(run, WRITTEN)
                if op["kind"] in ("sheet_row", "logbook_entry")] if run else []
    if re.fullmatch(r"undo\s+#?\d+([\s,]+#?\d+)*", text):
        return [int(n) for n in re.findall(r"\d+", text)]
    return None


def tell_agent(messages, lines):
    """Let the LLM know what the app wrote or undid, so it does not rely on old reads."""
    if lines:
        messages.append({"role": "user", "content": "[Note from the app, not the user] "
                         + " ".join(lines) + " The records changed; read them again before "
                         "answering about their current content."})


def main():
    llm = OpenAICompatLLM.from_config()
    store = Store()
    tools = tool_schemas()
    messages = [{"role": "system",
                 "content": SYSTEM_PROMPT.format(today=f"{date.today():%A %d/%m/%Y}")}]
    print(f"Agent ready ({config.LLM_PROVIDER} / {config.LLM_MODEL}).")
    print("Commands: 'log' (last run's activity), 'log 5', 'undo' (last approved changes), "
          "'undo 3' or 'undo 3,5', 'exit'.")
    while True:
        text = input("\nyou> ").strip()
        if text.lower() in ("exit", "quit"):
            break
        if not text:
            continue

        # Commands handled by code, without the LLM
        words = text.lower().split()
        if words[0] == "log" and len(words) <= 2:
            run = int(words[1]) if len(words) == 2 and words[1].isdigit() else store.last_run_id()
            print(f"\nActivity log of run {run}:\n{format_events(store.events(run))}")
            continue

        run_id = store.start_run(text, config.LLM_PROVIDER, config.LLM_MODEL)
        to_undo = undo_targets(text, store)
        if to_undo is not None:
            if not to_undo:
                results = ["There are no written changes to undo."]
            else:
                # Newest first, so changes to the same cell unwind in the right order
                results = [undo(store, op_id, lambda kind, data: store.log(run_id, kind, data))
                           for op_id in sorted(to_undo, reverse=True)]
            print("\n" + "\n".join(results))
            tell_agent(messages, results)
            continue
        set_context(store, run_id)  # write tools save their proposals to this run

        def record(kind, data):
            store.log(run_id, kind, data)

        record("user_message", {"text": text})
        messages.append({"role": "user", "content": text})
        try:
            answer = ask(llm, messages, tools, record)
            record("answer", {"text": answer})
            print(f"\nagent> {answer}")
            tell_agent(messages, review(store, run_id, record))  # the user approves; the code writes
        except Exception as error:  # report plainly, keep the chat going
            record("error", {"error": f"{type(error).__name__}: {error}"})
            print(f"\nagent> Error: {type(error).__name__}: {error}")


if __name__ == "__main__":
    main()
