"""The Yahoo OAuth helper.

Only the parsing is testable without a browser and a real Yahoo app, but that is
the part that will actually annoy somebody at 11pm: pasting the address bar is
the obvious move, and it has to work.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

_spec = importlib.util.spec_from_file_location(
    "yahoo_login",
    Path(__file__).resolve().parents[1] / "scripts" / "yahoo_login.py")
yahoo_login = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(yahoo_login)


def test_the_whole_redirected_url_works():
    """What you get by copying the address bar, which is what the script tells
    you to do."""
    assert yahoo_login.code_from(
        "https://localhost:8000/?code=abc123&state=x") == "abc123"


def test_a_bare_code_works_too():
    assert yahoo_login.code_from("abc123") == "abc123"


def test_whitespace_from_a_sloppy_paste_is_ignored():
    assert yahoo_login.code_from("  abc123\n") == "abc123"


def test_a_url_with_no_code_is_not_mistaken_for_a_code():
    """Pasting the wrong URL should fail at Yahoo with a clear error rather than
    sending the entire URL as if it were a code."""
    pasted = "https://localhost:8000/?error=access_denied"
    assert yahoo_login.code_from(pasted) == pasted


def test_an_error_redirect_is_read_as_an_error_not_a_code():
    """Yahoo answers the authorize request with ?error=... instead of ?code=...
    when it refuses. Sending that on as if it were a code turns a clear message
    into a confusing one."""
    said = yahoo_login.error_from(
        "https://localhost:8000/?error=invalid_scope&error_description=invalid+scope")
    assert "invalid_scope" in said
    assert "invalid scope" in said


def test_a_good_redirect_has_no_error():
    assert yahoo_login.error_from("https://localhost:8000/?code=abc123") == ""


def test_an_error_without_a_description_still_reads():
    assert yahoo_login.error_from(
        "https://localhost:8000/?error=access_denied") == "access_denied"
