"""Testing copies by default; the real files only with USE_REAL_FILES=yes, and never
mixed up with the testing ones. Settings are plain dicts here: .env is not read."""

from pathlib import Path

import pytest

from app.config import files_for, sheet_id

TEST = {"TEST_SHEET_ID": "test-sheet-123", "TEST_LOGBOOK_PATH": r"C:\logs\log (Testing).docx",
        "GOOGLE_SERVICE_ACCOUNT_FILE": "secrets/service-account.json"}
REAL = {"REAL_SHEET_ID": "real-sheet-456", "REAL_LOGBOOK_PATH": r"C:\logs\log.docx"}


def test_testing_copies_are_the_default():
    assert files_for(TEST | REAL) == ("testing", "test-sheet-123",
                                      Path(r"C:\logs\log (Testing).docx"), "secrets/service-account.json")


def test_old_setting_names_still_work_for_testing():
    old = {"SHEET_ID": "test-sheet-123", "LOGBOOK_PATH": r"C:\logs\log (Testing).docx",
           "GOOGLE_SERVICE_ACCOUNT_FILE": "secrets/service-account.json"}
    assert files_for(old)[1] == "test-sheet-123"


def test_real_files_only_with_yes():
    assert files_for(TEST | REAL | {"USE_REAL_FILES": "yes"}) == (
        "real", "real-sheet-456", Path(r"C:\logs\log.docx"), "secrets/service-account.json")


def test_each_mode_can_have_its_own_google_key():
    keys = {"REAL_GOOGLE_SERVICE_ACCOUNT_FILE": "secrets/service-account-real.json"}
    assert files_for(TEST | REAL | keys)[3] == "secrets/service-account.json"
    assert files_for(TEST | REAL | keys | {"USE_REAL_FILES": "yes"})[3] == "secrets/service-account-real.json"


def test_a_full_sheet_link_gives_its_id():
    link = "https://docs.google.com/spreadsheets/d/1AbC-dEf_9/edit?gid=0#gid=0"
    assert sheet_id(link) == "1AbC-dEf_9"
    assert files_for(TEST | REAL | {"USE_REAL_FILES": "yes", "REAL_SHEET_ID": link})[1] == "1AbC-dEf_9"


@pytest.mark.parametrize("settings, reason", [
    ({**TEST, "TEST_LOGBOOK_PATH": r"C:\logs\log.docx"}, "not a testing copy"),
    ({**TEST, "USE_REAL_FILES": "true"}, "must be yes or no"),
    ({**TEST, "USE_REAL_FILES": "yes"}, "needs REAL_SHEET_ID and REAL_LOGBOOK_PATH"),
    ({**TEST, **REAL, "USE_REAL_FILES": "yes", "REAL_SHEET_ID": "test-sheet-123"},
     "same Sheet as the testing one"),
    ({**TEST, **REAL, "USE_REAL_FILES": "yes", "REAL_LOGBOOK_PATH": r"C:\logs\log (Testing).docx"},
     "points to the testing copy"),
    ({"GOOGLE_SERVICE_ACCOUNT_FILE": "k.json"}, "TEST_SHEET_ID and TEST_LOGBOOK_PATH must be set"),
    ({k: v for k, v in TEST.items() if k != "GOOGLE_SERVICE_ACCOUNT_FILE"},
     "GOOGLE_SERVICE_ACCOUNT_FILE"),
])
def test_mixed_up_settings_refuse_to_start(settings, reason):
    with pytest.raises(RuntimeError, match=reason):
        files_for(settings)
