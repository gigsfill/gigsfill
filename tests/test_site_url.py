"""Stripe redirect URLs must come from settings, not be hardcoded.

A May 2026 audit fixed one `AccountLink.create` to read `site_url` from
platform_settings and missed the other, which stayed pinned to
https://gigsfill.com. Today that is the right value so nothing was visibly
broken — but a domain change or staging deploy would have sent artists to
production part-way through Connect onboarding, silently, and only via one of
the two paths. The two now share `_resolve_site_url`.
"""

import re
from pathlib import Path

import pytest
from sqlalchemy import text

from backend.routes.stripe_connect import _resolve_site_url

SRC = Path(__file__).resolve().parents[1] / "backend" / "routes" / "stripe_connect.py"


def _set(db, key, value):
    db.execute(text("DELETE FROM platform_settings WHERE setting_key = :k"), {"k": key})
    if value is not None:
        db.execute(
            text("INSERT INTO platform_settings (setting_key, setting_value) VALUES (:k, :v)"),
            {"k": key, "v": value},
        )
    db.commit()


def test_prefers_site_url(db):
    _set(db, "site_url", "https://staging.gigsfill.com")
    _set(db, "base_url", "https://gigsfill.com")
    assert _resolve_site_url(db) == "https://staging.gigsfill.com"


def test_falls_back_to_base_url(db):
    _set(db, "site_url", None)
    _set(db, "base_url", "https://other.example.com")
    assert _resolve_site_url(db) == "https://other.example.com"


def test_falls_back_to_production_when_unset(db):
    _set(db, "site_url", None)
    _set(db, "base_url", None)
    assert _resolve_site_url(db) == "https://gigsfill.com"


def test_strips_trailing_slash(db):
    """URLs are f-string concatenated with paths; a trailing slash would
    produce a double slash in the redirect."""
    _set(db, "site_url", "https://gigsfill.com/")
    assert _resolve_site_url(db) == "https://gigsfill.com"


@pytest.mark.parametrize("dev", ["http://127.0.0.1:8001", "http://localhost:8001"])
def test_rejects_localhost(db, dev):
    """Stripe has to be able to reach the URL. A dev value leaking into a live
    AccountLink dead-ends the artist mid-onboarding."""
    _set(db, "site_url", dev)
    assert _resolve_site_url(db) == "https://gigsfill.com"


def test_no_account_link_hardcodes_the_domain():
    """Both call sites must resolve the URL, not pin it."""
    src = SRC.read_text()
    for m in re.finditer(r"AccountLink\.create\((.*?)\n\s*\)", src, re.DOTALL):
        body = m.group(1)
        code = "\n".join(l for l in body.splitlines() if not l.strip().startswith("#"))
        assert "https://gigsfill.com" not in code, (
            f"AccountLink.create hardcodes the domain instead of using "
            f"_resolve_site_url:\n{code}"
        )


def test_both_account_links_are_still_present():
    """Guards the test above against passing vacuously if a call is renamed."""
    assert SRC.read_text().count("AccountLink.create(") == 2
