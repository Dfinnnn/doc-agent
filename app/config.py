"""Loads settings from .env so the rest of the code reads them from one place."""

import json
import os
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


# Google Sheets
SHEET_ID = _required("SHEET_ID")
# Relative paths in .env are taken from the project folder, so scripts work from any folder
SERVICE_ACCOUNT_FILE = PROJECT_ROOT / _required("GOOGLE_SERVICE_ACCOUNT_FILE")

# University Logbook
LOGBOOK_PATH = Path(_required("LOGBOOK_PATH"))
# Development guard: only the testing copy may be used until Part 9 passes.
# Remove this on purpose when switching to the real logbook.
if "Testing" not in LOGBOOK_PATH.name:
    raise RuntimeError(f"LOGBOOK_PATH is not the testing copy: {LOGBOOK_PATH.name}")


# LLM: any OpenAI-compatible provider (Gemini, OpenAI, DeepSeek, Groq...)
LLM_PROVIDER = _required("LLM_PROVIDER")  # a label for logs, e.g. "gemini"
LLM_MODEL = _required("LLM_MODEL")
LLM_API_KEY = _required("LLM_API_KEY")
LLM_BASE_URL = _required("LLM_BASE_URL")


# Document profiles: structure and style of each document (no IDs or paths)
def _load_profile(name):
    with open(PROJECT_ROOT / "profiles" / f"{name}.json", encoding="utf-8") as file:
        return json.load(file)


WORKSHEET = _load_profile("worksheet")
LOGBOOK = _load_profile("logbook")
