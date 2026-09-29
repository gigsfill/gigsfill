"""Stripe statement descriptor construction.

The 22-character cap is a hard Stripe limit and the "must contain a letter"
rule is easy to trip with a date-only suffix, so both get thorough coverage
here — a rejected descriptor fails the whole charge, not just the label.
"""

import os
from datetime import date, datetime

import pytest

from backend.services import statement_descriptor as sd


@pytest.fixture(autouse=True)
def _default_prefix(monkeypatch):
    """Pin the prefix so budget maths is deterministic regardless of .env."""
    monkeypatch.setenv("STRIPE_STATEMENT_PREFIX", "GIGSFILL")


# ── budget ──────────────────────────────────────────────────────────

def test_budget_leaves_room_for_prefix_and_separator():
    # 22 cap - 8 ("GIGSFILL") - 1 separator = 13
    assert sd.suffix_budget() == 13


def test_budget_respects_configured_prefix(monkeypatch):
    monkeypatch.setenv("STRIPE_STATEMENT_PREFIX", "GF")
    assert sd.suffix_budget() == 19


def test_budget_never_negative(monkeypatch):
    monkeypatch.setenv("STRIPE_STATEMENT_PREFIX", "X" * 40)
    assert sd.suffix_budget() == 0


def test_no_suffix_when_prefix_fills_the_cap(monkeypatch):
    monkeypatch.setenv("STRIPE_STATEMENT_PREFIX", "X" * 40)
    assert sd.build_suffix(gig_date="2026-10-15", name="Bon Jovi") is None


# ── sanitizing ──────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("Bon Jovi", "BON JOVI"),
    ("Bon  Jovi", "BON JOVI"),          # collapse runs of whitespace
    ("  Bon Jovi  ", "BON JOVI"),
    ("Bon-Jovi", "BON JOVI"),           # punctuation becomes a space
    ("Bon'Jovi", "BON JOVI"),           # Stripe rejects apostrophes outright
    ('Bon"Jovi', "BON JOVI"),
    ("Bon<Jovi>", "BON JOVI"),
    ("Café Münchén", "CAF M NCH N"),    # non-ASCII dropped, not transliterated
    ("", ""),
    (None, ""),
])
def test_sanitize(raw, expected):
    assert sd.sanitize(raw) == expected


@pytest.mark.parametrize("bad", ["<", ">", "\\", '"', "'", "*", "%"])
def test_stripe_forbidden_characters_never_survive(bad):
    assert bad not in sd.build_suffix(gig_date="2026-10-15", name=f"A{bad}B")


# ── date rendering ──────────────────────────────────────────────────

@pytest.mark.parametrize("value", [
    "2026-10-15",
    "2026-10-15 20:00:00",
    date(2026, 10, 15),
    datetime(2026, 10, 15, 20, 0, 0),
])
def test_date_formats_all_render_mmdd(value):
    assert sd.build_suffix(gig_date=value, name="Bon Jovi").startswith("1015 ")


def test_unparseable_date_is_dropped_not_fatal():
    assert sd.build_suffix(gig_date="not a date", name="Bon Jovi") == "BON JOVI"


# ── composition and the 22-char cap ─────────────────────────────────

def test_date_and_name():
    assert sd.build_suffix(gig_date="2026-10-15", name="Bon Jovi") == "1015 BON JOVI"


def test_long_name_is_truncated_to_budget():
    out = sd.build_suffix(gig_date="2026-10-15", name="The Extremely Long Band Name")
    assert out == "1015 THE EXTR"
    assert len(out) <= sd.suffix_budget()


def test_full_descriptor_never_exceeds_stripe_cap():
    out = sd.build_suffix(gig_date="2026-10-15", name="The Extremely Long Band Name")
    full = f"GIGSFILL {out}"
    assert len(full) <= sd.STRIPE_DESCRIPTOR_MAX_LEN


def test_name_only_when_no_date():
    assert sd.build_suffix(name="Bon Jovi") == "BON JOVI"


def test_returns_none_when_nothing_to_say():
    assert sd.build_suffix() is None
    assert sd.build_suffix(gig_date=None, name=None) is None


def test_name_that_sanitizes_to_nothing_falls_back_to_date():
    # A name of only punctuation must not produce a stray separator.
    assert sd.build_suffix(gig_date="2026-10-15", name="***") == "GIG 1015"


# ── the "must contain a letter" rule ────────────────────────────────

def test_date_only_gets_a_letter_prefix():
    """Stripe rejects an all-digit descriptor, so a bare date needs help."""
    out = sd.build_suffix(gig_date="2026-10-15")
    assert out == "GIG 1015"
    assert any(c.isalpha() for c in out)


def test_numeric_band_name_still_yields_a_letter():
    out = sd.build_suffix(gig_date="2026-10-15", name="3030")
    assert any(c.isalpha() for c in out), out
    assert len(out) <= sd.suffix_budget()


def test_every_generated_suffix_contains_a_letter():
    """Sweep the realistic input space — a miss here is a failed charge."""
    names = [None, "", "***", "3030", "Bon Jovi", "X", "1", "  ", "日本",
             "The Extremely Long Band Name That Goes On"]
    dates = [None, "2026-10-15", "2026-01-01", "bogus"]
    for n in names:
        for d in dates:
            out = sd.build_suffix(gig_date=d, name=n)
            if out is None:
                continue
            assert any(c.isalpha() for c in out), f"name={n!r} date={d!r} -> {out!r}"
            assert len(out) <= sd.suffix_budget(), f"name={n!r} date={d!r} -> {out!r}"


# ── the two call-site wrappers ──────────────────────────────────────

def test_gig_charge_names_the_artist():
    assert sd.for_gig_charge("2026-10-15", "Bon Jovi") == "1015 BON JOVI"


def test_platform_fee_omits_the_artist_name():
    """A cancellation fee is billed by the platform, not paid to the artist.

    Putting the artist's name on it would misrepresent the charge on the
    venue's statement.
    """
    out = sd.for_platform_fee("2026-10-15")
    assert out == "FEE 1015"
    assert "BON" not in out


# ── call-site coverage ──────────────────────────────────────────────

def test_every_payment_intent_create_sets_a_descriptor():
    """Each venue card charge must carry a statement descriptor.

    Source inspection, because these calls need a live Stripe key, a
    customer and a saved payment method to reach. The point is to catch a
    NEW charge site being added without a descriptor, which would silently
    reintroduce the bare "GigsFill.com" line this work removed.
    """
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "backend"
    sites = []
    for path in root.rglob("*.py"):
        src = path.read_text()
        for m in re.finditer(r"PaymentIntent\.create\((.*?)\n\s*\)", src, re.DOTALL):
            sites.append((path.name, m.group(1)))

    assert len(sites) == 4, (
        f"expected 4 PaymentIntent.create sites, found {len(sites)}: "
        f"{[s[0] for s in sites]} — if a charge site was added or removed, "
        "confirm it sets a descriptor and update this count"
    )

    missing = [name for name, body in sites
               if "descriptor" not in body and "_desc" not in body]
    assert not missing, f"PaymentIntent.create without a statement descriptor in: {missing}"


def test_transfers_do_not_attempt_a_statement_descriptor():
    """Transfer has no statement_descriptor field — setting one would 400.

    Guards against someone "fixing" the artist side by copying the venue
    pattern onto a Transfer, which Stripe rejects outright. Inspects the
    call bodies rather than raw lines, so prose mentioning the field in a
    comment doesn't trip it.
    """
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "backend"
    offenders = []
    for path in root.rglob("*.py"):
        src = path.read_text()
        for m in re.finditer(r"Transfer\.create\((.*?)\n\s*\)", src, re.DOTALL):
            body = m.group(1)
            code = "\n".join(
                ln for ln in body.splitlines() if not ln.strip().startswith("#")
            )
            if "statement_descriptor" in code:
                offenders.append(f"{path.name}: {code.strip()[:120]}")
    assert not offenders, offenders
