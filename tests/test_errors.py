"""Each kind of failure gets a plain message that says what to do.
The errors are built by hand here: no network, no files."""

import httplib2
import httpx2 as httpx  # the HTTP library the openai package uses
import openai
import pytest
from googleapiclient.errors import HttpError

from app.errors import explain, raw

REQUEST = httpx.Request("POST", "https://llm.example/v1/chat/completions")


def api_error(cls, status):
    return cls("refused", response=httpx.Response(status, request=REQUEST), body=None)


def sheet_error(status):
    return HttpError(httplib2.Response({"status": status}), b"{}")


@pytest.mark.parametrize("error, expected", [
    (openai.APIConnectionError(request=REQUEST), "Couldn't reach the model"),
    (openai.APITimeoutError(request=REQUEST), "took too long"),
    (api_error(openai.AuthenticationError, 401), "Check LLM_API_KEY"),
    (api_error(openai.BadRequestError, 400), "Check LLM_API_KEY and LLM_MODEL"),
    (api_error(openai.RateLimitError, 429), "rate limit or credit"),
    (api_error(openai.NotFoundError, 404), "Check LLM_MODEL"),
    (sheet_error(404), "Check SHEET_ID"),
    (sheet_error(403), "Share it with the service account"),
    (sheet_error(503), "busy"),
    (PermissionError(13, "Permission denied", r"C:\Users\someone\Logs\log (Testing).docx"),
     "log (Testing).docx is open in another program (Word?)"),
    (ValueError("2026-10-05 already has entries; not overwriting them"),
     "2026-10-05 already has entries; not overwriting them"),
])
def test_plain_messages(error, expected):
    assert expected in explain(error)


def test_messages_never_show_folders():
    error = PermissionError(13, "Permission denied", r"C:\Users\someone\Secret folder\log.docx")
    assert "Secret folder" not in explain(error)


def test_unknown_errors_keep_their_type_and_raw_keeps_everything():
    error = KeyError("tasks")
    assert explain(error) == "KeyError: 'tasks'"
    assert raw(sheet_error(404)).startswith("HttpError: <HttpError 404")
