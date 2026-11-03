# V1 Implementation Plan — Document Assistant Agent

Build order for `scope-v1.md`. Work one part at a time. Each part ends with something you run yourself, plus a check that proves it works. Don't start the next part until the current one passes.

The agent runs in the terminal first (Parts 0–9) and gets a web UI later (Part 10). This keeps the early parts about the agent, not about frontend work.

## Folder layout (grows part by part, not created all at once)
```
doc-agent/
  CLAUDE.md            rules for Claude Code
  README.md
  .env.example         names of the settings, no values
  .gitignore
  docs/                idea.md, scope-v1.md, plan-v1.md
  profiles/            worksheet.json, logbook.json (structure + style only; file IDs/paths come from .env)
  app/
    config.py          loads .env and the profiles
    integrations/      sheets.py, logbook_docx.py   (talking to the documents)
    llm/               base.py, openai_compat.py    (talking to the model)
    validation/        checks.py                    (rule-based checks)
    tools/             registry.py                  (the agent's action set)
    agent/             loop.py                      (the tool-calling loop)
    store/             db.py                        (SQLite: runs, events, operations)
    api/               main.py                      (FastAPI, Part 10)
  web/                 index.html, app.js, style.css (Part 10)
  tests/
```

## Parts

| # | Part | You learn | Done when |
|---|---|---|---|
| 0 | **Setup**: folder, virtual env, `git init`, `.gitignore`, `.env.example`, docs copied in | venv, pip, git basics, env vars | First commit contains no data or keys (`git status` checked together) |
| 1 | **Read the Sheet**: Google Cloud project, Sheets API, service account, share the Sheet copy, read one month tab into Python | API auth, service accounts, JSON | A script prints the October rows of the copy as clean records |
| 2 | **Write the Sheet**: write one day's row; create a month tab (dates, Weekend marking, Hours formula) | Writing via API, keeping formulas intact | A November tab appears in the copy and matches the existing tabs' layout |
| 3 | **Read/write the logbook**: map week tables to dates computed from Week 1; write entries in Times New Roman 12; add a week table; never touch the verify row | python-docx, document structure | The Week 1 entries and a new Week 5 table appear correctly in the `（Testing）` copy |
| 4 | **Profiles**: move the hard-coded structure and style from Parts 1–3 into `profiles/*.json` | Config-driven design | Parts 1–3 still work, now reading the profiles |
| 5 | **LLM adapter**: one OpenAI-compatible class configured via `.env`; rough notes in → structured draft entry out | LLM APIs, structured output, swapping providers | The same script works with Gemini and one other provider by changing only `.env` |
| 6 | **Validation checks**: rule functions (dates, headers, hours, Mode vs content, duplicates, Sheet vs logbook) + unit tests | Pure functions, pytest | `pytest` passes; an audit of the copies lists findings (it should catch the Week 4 header) |
| 7 | **Tools + agent loop (terminal)**: tool schemas; the LLM picks tools; read tools run directly; write tools only *propose* a change | Tool calling, the agent loop | In the terminal, "what did I do on 2/10?" is answered by the agent calling `read_sheet` |
| 8 | **Approval + store**: SQLite records runs, events and operations; approve → execute → save before/after; undo | Persistence, approval gate, audit trail | A proposed write is shown, you approve it, it is written, the activity log shows every step, and undo restores the old value |
| 9 | **Commands end to end (terminal)**: log update, sync, refine (draft / existing), summarize, Q&A, settings change | Putting it together | Each command from scope §3 works on the copies |
| 10 | **Web UI**: FastAPI endpoints + a plain HTML/JS page with chat, preview/diff, approve buttons and an activity panel | HTTP, REST, frontend ↔ backend | The same commands work from the browser |
| 11 | **Hardening and release prep**: error handling, README, "tested with" models, demo data, secret scan before going public | Shipping | A fresh clone runs with demo data; nothing private in the git history |

## Rules throughout
- Only the testing copies are touched until Part 9 passes; switch to the real files on purpose, not by default.
- Commit after each part, after reviewing `git status`.
- When a decision changes, update `docs/scope-v1.md` first, then the code.
