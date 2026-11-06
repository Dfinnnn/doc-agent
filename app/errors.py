"""Turn a failure into one plain sentence that says what to do.

The raw error still goes into the activity log; this is only what the user reads.
Messages never contain keys, IDs or folder paths.
"""

import socket

import httplib2
import openai
from docx.opc.exceptions import PackageNotFoundError
from google.auth.exceptions import RefreshError, TransportError
from googleapiclient.errors import HttpError


def explain(error):
    """A short, plain message for a failure (falls back to "Type: message")."""
    # ---- The model ----
    if isinstance(error, openai.APITimeoutError):
        return "The model took too long to answer. Try again in a moment."
    if isinstance(error, openai.APIConnectionError):
        return ("Couldn't reach the model. Check the internet connection, or LLM_BASE_URL "
                "in .env.")
    if isinstance(error, openai.RateLimitError):
        return "The model's rate limit or credit is used up. Wait a little, then try again."
    if isinstance(error, (openai.AuthenticationError, openai.PermissionDeniedError)):
        return "The model refused the API key. Check LLM_API_KEY in .env."
    if isinstance(error, openai.NotFoundError):
        return "The model was not found. Check LLM_MODEL and LLM_BASE_URL in .env."
    if isinstance(error, openai.BadRequestError):
        return ("The model refused the request. Check LLM_API_KEY and LLM_MODEL in .env "
                "(some providers answer a wrong key this way).")
    if isinstance(error, openai.InternalServerError):
        return "The model's provider had a problem. Try again in a moment."

    # ---- Google Sheets ----
    if isinstance(error, HttpError):
        status = error.resp.status
        if status == 404:
            return "Google can't find the Sheet. Check SHEET_ID in .env."
        if status == 403:
            return ("Google refused access to the Sheet. Share it with the service account "
                    "email as Editor.")
        if status == 400:
            return ("Google Sheets refused the request. If the file is an .xlsx, open it in "
                    "Google Sheets, use File > Save as Google Sheets, and use the new link.")
        if status == 429 or status >= 500:
            return "Google Sheets is busy. Wait a little, then try again."
        return f"Google Sheets refused the request (HTTP {status})."
    if isinstance(error, RefreshError):
        return ("Google refused the service-account key. Check the key file named in "
                "GOOGLE_SERVICE_ACCOUNT_FILE.")
    if isinstance(error, (TransportError, httplib2.ServerNotFoundError)):
        return "Couldn't reach Google. Check the internet connection."

    # ---- The logbook file ----
    if isinstance(error, PermissionError):
        name = _name(error.filename) if error.filename else "The logbook"
        return f"{name} is open in another program (Word?). Close it, then try again."
    if isinstance(error, PackageNotFoundError):
        return ("The logbook can't be opened. Close it if it is open in Word, and check "
                "LOGBOOK_PATH in .env.")
    if isinstance(error, FileNotFoundError):
        return f"A file is missing: {_name(error.filename)}." if error.filename else "A file is missing."

    # ---- Anything else that waited too long ----
    if isinstance(error, (TimeoutError, socket.timeout)):
        return "A connection took too long. Try again in a moment."
    if isinstance(error, ValueError):
        return str(error)  # our own checks already write plain messages
    return f"{type(error).__name__}: {error}"


def raw(error):
    """The full error for the activity log ("Type: message"), so nothing is hidden there."""
    return f"{type(error).__name__}: {error}"


def _name(path):
    """Only the file name, never the folders around it."""
    return str(path).replace("\\", "/").rsplit("/", 1)[-1]
