"""Loads settings from .env so the rest of the code reads them from one place."""

import json
import os
import re
from pathlib import Path

from dotenv import load_dotenv

# The doc-agent folder (this file is doc-agent/app/config.py)
PROJECT_ROOT = Path(__file__).resolve().parent.parent

load_dotenv(PROJECT_ROOT / ".env")


def _required(name):
    """Return a setting from .env, or stop with a clear message if it is empty."""
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is missing in .env (see .env.example)")
    return value


def sheet_id(value):
    """A Sheet ID, or the ID taken from a full Sheet link (.../spreadsheets/d/<ID>/edit)."""
    match = re.search(r"/spreadsheets/d/([A-Za-z0-9_-]+)", value)
    return match.group(1) if match else value


def files_for(env):
    """Which Sheet, logbook and Google key to use, from the .env settings in `env` (a dict).

    Testing copies by default. The real files only with USE_REAL_FILES=yes, and
    only if they really differ from the testing ones. Returns (mode, sheet_id,
    logbook_path, key_file); raises with a plain reason if the settings are mixed up.
    Each mode may have its own service-account key (TEST_/REAL_GOOGLE_SERVICE_ACCOUNT_FILE),
    e.g. when the real Sheet belongs to another Google account; otherwise both
    use GOOGLE_SERVICE_ACCOUNT_FILE.
    """
    def get(*names):  # the first of these names that is set (old names still work)
        return next((env[n].strip() for n in names if (env.get(n) or "").strip()), "")

    def key(mode):  # this mode's service-account key, else the shared one
        found = get(f"{mode}_GOOGLE_SERVICE_ACCOUNT_FILE", "GOOGLE_SERVICE_ACCOUNT_FILE")
        if not found:
            raise RuntimeError(f"{mode}_GOOGLE_SERVICE_ACCOUNT_FILE (or GOOGLE_SERVICE_ACCOUNT_FILE) "
                               "must be set in .env")
        return found

    switch = (env.get("USE_REAL_FILES") or "no").strip().lower()
    if switch not in ("yes", "no"):
        raise RuntimeError(f"USE_REAL_FILES must be yes or no, not '{switch}'")
    test_sheet = sheet_id(get("TEST_SHEET_ID", "SHEET_ID"))
    test_logbook = get("TEST_LOGBOOK_PATH", "LOGBOOK_PATH")

    if switch == "no":
        if not test_sheet or not test_logbook:
            raise RuntimeError("TEST_SHEET_ID and TEST_LOGBOOK_PATH must be set in .env "
                               "(see .env.example)")
        if "Testing" not in Path(test_logbook).name:
            raise RuntimeError(f"TEST_LOGBOOK_PATH is not a testing copy: {Path(test_logbook).name}")
        return "testing", test_sheet, Path(test_logbook), key("TEST")

    real_sheet = sheet_id(get("REAL_SHEET_ID"))
    real_logbook = get("REAL_LOGBOOK_PATH")
    if not real_sheet or not real_logbook:
        raise RuntimeError("USE_REAL_FILES=yes needs REAL_SHEET_ID and REAL_LOGBOOK_PATH in .env")
    if real_sheet == test_sheet:
        raise RuntimeError("REAL_SHEET_ID is the same Sheet as the testing one")
    if "Testing" in Path(real_logbook).name or Path(real_logbook) == Path(test_logbook or "."):
        raise RuntimeError("REAL_LOGBOOK_PATH points to the testing copy")
    return "real", real_sheet, Path(real_logbook), key("REAL")


# Which documents: "testing" (default) or "real" (only with USE_REAL_FILES=yes)
FILES_MODE, SHEET_ID, LOGBOOK_PATH, _KEY_FILE = files_for(os.environ)
# Each mode keeps its own history, so undo never puts a testing value into a real file
DB_FILE = PROJECT_ROOT / "data" / ("agent-real.db" if FILES_MODE == "real" else "agent.db")
# Relative paths in .env are taken from the project folder, so scripts work from any folder
SERVICE_ACCOUNT_FILE = PROJECT_ROOT / _KEY_FILE


# LLM: any OpenAI-compatible provider (Gemini, OpenAI, DeepSeek, Groq...)
LLM_PROVIDER = _required("LLM_PROVIDER")  # a label for logs, e.g. "gemini"
LLM_MODEL = _required("LLM_MODEL")
LLM_API_KEY = _required("LLM_API_KEY")
LLM_BASE_URL = _required("LLM_BASE_URL")


# Document profiles: structure and style of each document (no IDs or paths)
def _load_profile(name):
    with open(PROJECT_ROOT / "profiles" / f"{name}.json", encoding="utf-8") as file:
        return json.load(file)


def save_profile(name, data):
    """Write a profile back to its file (used when the user changes a setting by chat)."""
    with open(PROJECT_ROOT / "profiles" / f"{name}.json", "w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)
        file.write("\n")


WORKSHEET = _load_profile("worksheet")
LOGBOOK = _load_profile("logbook")
