"""The second lock on the app.

Cloudflare Access is the first: it refuses unauthenticated requests at
Cloudflare's own network, before a packet reaches the mini. This is what
happens if that lock is not there — bound to 0.0.0.0 by mistake, reached from
the LAN on port 8501, an ingress rule pointing at the wrong hostname.

The property that matters most is that it fails CLOSED. Every way of being
unsure has to stop the page, because the page drives a live ESPN session.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from combine import access

GOOD = {"COMBINE_ACCESS_TEAM": "combine.cloudflareaccess.com",
        "COMBINE_ACCESS_AUD": "abc123"}


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for name in ("COMBINE_ACCESS_TEAM", "COMBINE_ACCESS_AUD",
                 "COMBINE_ACCESS_EMAILS"):
        monkeypatch.delenv(name, raising=False)


def configure(monkeypatch, **extra):
    for name, value in {**GOOD, **extra}.items():
        monkeypatch.setenv(name, value)


def claims(email="william@example.com"):
    return lambda _token, _team, _aud: {"email": email}


def test_it_is_off_until_it_is_configured():
    """A checkout on a laptop has to run. The app says so in the sidebar rather
    than pretending it is protected."""
    verdict = access.check({})
    assert verdict.ok and verdict.off
    assert "not configured" in verdict.why


def test_half_configured_is_still_off():
    """A team domain with no audience tag would verify a token issued for any
    other application on the same Cloudflare team."""
    import os

    os.environ["COMBINE_ACCESS_TEAM"] = "combine.cloudflareaccess.com"
    try:
        assert access.configured() is False
    finally:
        del os.environ["COMBINE_ACCESS_TEAM"]


def test_a_request_without_a_token_is_refused(monkeypatch):
    configure(monkeypatch)
    verdict = access.check({"host": "combine.example.com"})
    assert not verdict.ok and verdict.enforced
    assert "did not come through" in verdict.why


def test_a_token_that_does_not_verify_is_refused(monkeypatch):
    configure(monkeypatch)

    def boom(_token, _team, _aud):
        raise ValueError("signature mismatch")

    verdict = access.check({access.HEADER: "nonsense"}, decode=boom)
    assert not verdict.ok
    assert "did not verify" in verdict.why and "ValueError" in verdict.why


def test_a_verified_token_gets_in_and_says_who(monkeypatch):
    configure(monkeypatch)
    verdict = access.check({access.HEADER: "t"}, decode=claims())
    assert verdict.ok and verdict.email == "william@example.com"


def test_the_header_is_found_whatever_case_the_proxy_used(monkeypatch):
    configure(monkeypatch)
    for spelling in ("Cf-Access-Jwt-Assertion", "CF-ACCESS-JWT-ASSERTION",
                     access.HEADER):
        assert access.check({spelling: "t"}, decode=claims()).ok


def test_the_app_keeps_its_own_copy_of_the_allowlist(monkeypatch):
    """The Access policy is already an allowlist. This is the same list written
    down where Cloudflare cannot edit it, so widening the policy by accident
    does not widen the app."""
    configure(monkeypatch, COMBINE_ACCESS_EMAILS="william@example.com")
    assert access.check({access.HEADER: "t"}, decode=claims()).ok
    stranger = access.check({access.HEADER: "t"},
                            decode=claims("someone@else.com"))
    assert not stranger.ok
    assert "someone@else.com is not on this app's list" in stranger.why


def test_the_allowlist_ignores_case_and_spacing(monkeypatch):
    configure(monkeypatch,
              COMBINE_ACCESS_EMAILS=" William@Example.com , other@example.com ")
    assert access.check({access.HEADER: "t"},
                        decode=claims("william@example.com")).ok
    assert access.check({access.HEADER: "t"},
                        decode=claims("other@example.com")).ok


def test_an_empty_allowlist_means_trust_the_access_policy(monkeypatch):
    configure(monkeypatch, COMBINE_ACCESS_EMAILS="   ")
    assert access.check({access.HEADER: "t"},
                        decode=claims("anyone@example.com")).ok


def test_a_token_with_no_email_is_refused_when_a_list_exists(monkeypatch):
    configure(monkeypatch, COMBINE_ACCESS_EMAILS="william@example.com")
    verdict = access.check({access.HEADER: "t"},
                           decode=lambda *_a: {"sub": "no-email"})
    assert not verdict.ok


def test_headers_that_are_not_a_mapping_do_not_crash_the_page(monkeypatch):
    """`st.context.headers` is None outside a request, and a crash here would
    take the whole app down rather than refusing one page load."""
    configure(monkeypatch)
    assert not access.check(None).ok
    assert not access.check(object()).ok


def test_the_team_domain_survives_being_pasted_with_a_scheme(monkeypatch):
    """The dashboard shows it as a URL, so it gets pasted as one."""
    configure(monkeypatch,
              COMBINE_ACCESS_TEAM="https://combine.cloudflareaccess.com/")
    assert access.team() == "combine.cloudflareaccess.com"
