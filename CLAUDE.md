# CLAUDE.md — Document Assistant Agent

## What this is
A local, single-user chat agent that turns orders ("log update", "sync", "refine", "audit"…) into previewed, approved changes on two documents: a company work log (Google Sheet) and a university logbook (.docx). It is also the user's learning project.

Read before working: `docs/idea.md` (purpose), `docs/scope-v1.md` (what V1 does and its rules), `docs/plan-v1.md` (build order).

## How to work with this user
- **One part at a time**, following `docs/plan-v1.md`. Don't build ahead or create files for later parts.
- **Before writing code:** say in a few plain sentences what this step does and why, then wait for "go".
- **Keep explanations simple and short.** The user is still learning Python and APIs. Explain new concepts in one or two lines, with no jargon dumps.
- **Small steps:** one file or one function at a time where possible. After each step, give the exact command to run (Windows PowerShell) and what output to expect. The user runs it.
- **Each part ends** with the plan's "Done when" check passing.
- **Ask instead of assuming** when something is unclear. Don't agree just to agree; push back with a reason.
- **No new libraries, frameworks or abstractions without asking.** No LangGraph, MCP or vector DB in V1.

## Hard rules
- **Never commit secrets or data:** no `.env`, service-account JSON, API keys, or the real or testing log files. Real data lives outside this repo in `../Automation Daily Log/`. Check `git status` before every commit.
- **Testing copies by default** (`TEST_SHEET_ID`, `TEST_LOGBOOK_PATH` in `.env`). The real files are used only when the user sets `USE_REAL_FILES=yes` themselves. Never switch that setting or the `REAL_*` values for them, and never test or debug against the real files.
- **Agent design rules (from scope):**
  - No write without preview + approval.
  - The LLM decides intent and fields; code decides format.
  - Validation is rule-based code; the LLM only explains findings.
  - Never write the logbook's supervisor verify/stamp/comment rows.
  - Week dates are computed from Week 1 (17/08), never read from the header text.
  - No invented facts in generated text.
- **Errors:** report them plainly; never claim a write succeeded unless it was confirmed.

## Stack
Python, FastAPI (Part 10), plain HTML/CSS/JS, SQLite, python-docx, Google Sheets API (service account), OpenAI SDK as the LLM adapter (OpenAI-compatible providers via `.env`).

## Skills
The "Ui ux pro max" plugin (v2.13.0) is installed. It was reviewed in an earlier session.
- **Allowed:** `ui-ux-pro-max:ui-ux-pro-max`, only its search (`search.py` with `--domain` / `--design-system`). It is stdlib Python, uses local data and needs no network.
  - Results are suggestions; this project's rules always win.
  - No `--persist` without asking first. Never `--force`.
  - Its stacks don't include plain HTML; never use the `html-tailwind` stack advice for code.
- **Not allowed (don't load or run):** `ui-styling` (shadcn/Tailwind, runs npx), `design` (calls outside APIs, needs keys and pip installs), `design-system`, `slides`, `banner-design`, `brand`.
- Never click or run a plugin update.

## When decisions change
Update `docs/scope-v1.md` (and `docs/plan-v1.md` if the order changes) before changing code.
