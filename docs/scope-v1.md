# V1 Scope — Document Assistant Agent

Companion to `idea.md`. This fixes *what* V1 does and its rules. It is not an implementation plan.

## 1. V1 goal
A local, single-user chat agent that turns orders ("log update", "sync", "refine", "audit"…) into previewed, approved changes on two documents, with every step visible in an activity log.

## 2. Documents (document profiles)
Each document is described by a config file (location, structure, field mapping, style rules). Adding a document = adding a profile, not new code. V1 ships two:

| Profile | Where | Structure | Style |
|---|---|---|---|
| Company Worksheet | Google Sheet, owned by company Workspace account | One tab per month; one row per calendar day; columns: Date, Clock-in, Clock-out, Break, Hours (formula), Mode, Tasks Completed, Challenges, Learnings, Next Steps, Supervisor's Comment | Formal, concise |
| University Logbook (BSD4812) | Local `.docx` | One table per week from Week 1 (17/08), 24 weeks; Date + Activities rows + supervisor verify/stamp/comment row. Week dates are **computed from the Week 1 start**, never read from the header text. | Plain, direct |

Profiles are plain JSON/YAML files, not a plugin system.

## 3. Commands in V1
1. **Log update**: rough notes for one or more dates → draft entries for both documents → preview → approve → write.
2. **Sync**: read the Sheet (master by default) from 17/08 to today → find dates missing in the Logbook → rewrite in Logbook style → add week tables as needed → preview → write.
3. **Refine**
   - (i) Refining a draft in the preview loop: the agent rewrites and states what changed and why. Nothing written yet.
   - (ii) Refining existing entries (a date or range): shows old vs new, and the old value is stored for undo.
4. **Structure extension (automatic)**: a new month tab or week table is proposed inside the same preview when a write needs it. Built by code, not the LLM.
5. **Summarize (on demand)**: `scope = project | month | date range`. No stored project registry.
6. **Audit**: runs the same checks as §5a across whole documents and reports findings (see §5a for format). Can also be run after any change.
7. **Q&A over records** (read-only): e.g. "when did I deploy FiRxt?"

The source/target of each operation comes from the order (e.g. sync = Sheet → Logbook). The Sheet is the default master.

## 4. Tools (the agent's action set)
Read: `read_sheet`, `read_logbook`, `search_records`, `audit`
Write (approval-gated): `write_sheet_rows`, `create_month_tab`, `write_logbook_entries`, `add_week_table`
LLM-backed: `summarize`, drafting/refining (inside the agent loop)

One LLM in a tool-calling loop. No LangGraph in V1.

## 5. Rules
- **Approval:** no write without preview and an explicit approve.
- **Overwrites:** show a diff and store the old value in the operation history; undo is possible (Sheet rows, logbook entries and settings). New month tabs and week tables are not removed by undo; they are removed by hand if needed.
- **Same-date conflict:** the agent asks: overwrite / append / skip.
- **LLM decides intent and fields; code decides format.** E.g. "worked Saturday 26/9" → fill that day's existing Sheet row (Mode = WFO) and add an extra Logbook row.
- **Supervisor rows are never written:** the agent never writes into a week's "Verified by / Official Stamps / Comment" row.
- **Signed weeks warn, not block:** signing happens on an exported PDF, not on the .docx. After the user marks Week 12 / Week 24 as signed (a profile flag), editing Weeks 1–12 / 13–24 shows a warning that the .docx will no longer match the signed PDF.
- **Format changes by chat:** the user can change whitelisted profile settings in an order (font, size, date alignment, leave / public-holiday wording, last signed week), e.g. "from now on use Arial 12". The app asks whether the change applies to future entries only or also reformats existing ones, then shows it as an approval-gated change. Structure (columns, tables) cannot be changed this way. A different file with its own format = a new profile (created by hand in V1; drafting a profile by chat is a V2 candidate).
- **Non-working days:** sync reads the Sheet's Mode column (Weekend / Leave / Public Holidays / Medical Leave). For new entries, the user states it in the order.
- **Truthfulness:** no invented activities, causes, results or learnings. Rewording is allowed; new facts are not.
- **Errors:** report failures plainly; never claim a write succeeded unless it was confirmed.

## 5a. Validation and findings
Detection is done by **code (rules)**. The LLM only explains findings and suggests fixes. It is not trusted as the detector, because it can miss real problems and report false ones.

**When:** (1) automatically on every proposed write, shown in the preview; (2) on demand via Audit.

**Hard checks (code):**
- The date exists and falls in the right month tab / week table.
- The week header dates match the computed dates (e.g. Week 4 header duplicating Week 3).
- No overwrite of a non-empty cell without confirmation.
- Hours = Clock-out − Clock-in − Break; the formula is intact.
- Mode matches content (e.g. Mode = Leave but tasks are filled; work on a Weekend row without Mode changed).
- No future dates; no duplicate dates; headers intact; required fields filled.
- Sheet and Logbook are consistent for the same date.

**Soft checks (LLM, labelled "possible"):** text copied across days, Next Steps not matching the next day, wording that adds facts not present in the source.

**Each finding states:** where (tab/cell or week/row), what is wrong, the likely cause (rule-based, e.g. "header dates = Week 3 dates, probably a copy-paste"), severity (block / warn), and a suggested fix that the user can approve.

**Limits (stated honestly):** the agent cannot verify that work actually happened; it can only check consistency. Orders outside its tools are declined with a reason rather than improvised.

## 6. Activity log
Every run records an event timeline (stored in SQLite, shown in a UI panel):
- the user's order
- each LLM call: model, chosen tool + arguments, short stated plan, tokens/latency
- each tool result (or error)
- the preview shown, the user's decision (approve / edit / reject)
- each write, with before/after values (the same record powers undo)

It shows the model's actual tool calls and outputs, not hidden reasoning.

## 7. LLM provider
- A small in-house adapter behind one interface; config via `.env` (`LLM_PROVIDER`, `LLM_MODEL`, `LLM_API_KEY`, `LLM_BASE_URL`).
- First adapter: OpenAI-compatible (OpenAI SDK + `base_url`). It covers OpenAI, Gemini, DeepSeek, Qwen, Grok and Groq, to be verified at implementation. A native Anthropic adapter is added only if needed.
- Default model: Gemini (existing credit). The README states "tested with X, Y"; other models are configurable but not guaranteed.
- Note: a Claude Pro subscription does not include API access.

## 8. Stack
Python, FastAPI, plain HTML/CSS/JS, SQLite, python-docx, Google Sheets API, OpenAI SDK.

## 8a. UI rules (Part 10)
- Plain HTML/CSS/JS in `web/` (`index.html`, `app.js`, `style.css`). No framework, CDN, Tailwind, GSAP or web fonts; system fonts only.
- Backend: FastAPI in `app/api/main.py`, run with uvicorn. Any other new library needs asking first. `requirements.txt` stays up to date.
- Panels: **chat**, **preview/diff** (Approve / Reject / Refine, plus the overwrite / append / skip and "new only / reformat existing" choices), **activity log** (read from the SQLite store), and **Undo** for written changes.
- The UI never writes directly. Every write goes through the same preview + approval code the terminal uses, so block issues still can't be approved.
- Basics: readable contrast, every control usable by keyboard, visible focus, works at laptop width.

## 9. Run and deploy
- V1 runs locally; the code goes on GitHub.
- A showcase deploy comes later, using demo data (a sample Sheet and a downloadable .docx). Frontend on Vercel/Netlify; backend on Render, Railway or Fly (not Vercel serverless: no lasting files or SQLite).

## 10. Google access
A service account is created in a Google Cloud project under the personal account and shared on the Sheet copy (and later the real Sheet). The Sheet can stay in the company account; sharing outside the company is allowed (confirmed).

## 10a. Repo
- Code lives in a new folder inside `Assistance Agent/` (`doc-agent/`). Data files stay outside it, in `Automation Daily Log/`.
- GitHub: private first, public later. No keys, `.env`, service-account JSON or company data are ever committed, because history stays visible once the repo is public. Tests and the showcase use made-up demo data.
- Development uses only the testing copies (Sheet copy, `（Testing）.docx`).

## 11. Out of scope for V1
Multi-user, LangGraph, MCP, vector DB, screenshots in the logbook, stored project registry, learning path, Google Docs output, and drafting new documents from records (V2 candidate).

## 12. Open items
- Logbook template: the Week 4 header shows Week 3's dates (copy-paste typo; should be 07/09–11/09). Fix by hand, or let the first Audit propose the fix.
- Logbook formatting: entries in Times New Roman, size 12. Date as dd/mm/yyyy (centred, as in the template). Activities as bullet points ("•"), one per task, left-aligned. Leave / holiday rows use plain wording ("On leave" / "Public holiday"), set in the profile.
- Drafting style (to fine-tune; the Part 5 prompt is a first version):
  - Real notes are already bullet lists per date, one bullet per task (e.g. "fixing the OCR bug in system A", "building the RAG for company B's project"), often several dates in one order.
  - Every task in the notes becomes its own entry, never merged or dropped.
  - Each task is written out fully: about 2 sentences or more, precise, naming the project / client / system from the notes. Not one-line summaries.
  - "Elaborate" means fuller wording of the facts given (what, for whom, why, outcome if stated), never added facts.
  - The Sheet and the Logbook may word the same task differently (Sheet formal, Logbook plain), but both keep the same facts and level of detail.
