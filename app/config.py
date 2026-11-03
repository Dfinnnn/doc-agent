"""Loads settings from .env so the rest of the code reads them from one place."""

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
