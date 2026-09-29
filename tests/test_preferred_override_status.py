"""Preferred-artist overrides must only apply on an APPROVED row.

Background: venues can now invite an artist to become preferred, and that
invitation writes `preferred_artists` with `status='invited'` — it grants
nothing until the artist accepts. Before that change an approved row was
essentially the only kind that existed in practice, so several override
reads joined on (venue_id, artist_id) alone and happened to be correct.

They aren't correct any more. A row can now legitimately sit in `invited`,
`denied`, `revoked` or `pending`, and any read without a status filter will
apply that row's pay or frequency override to an artist the venue never
approved.

Two of those reads (the booking-frequency gate in `create_hold_waitlist`
and in `respond_to_hold_offer`) were missing the filter and were fixed
2026-09-29. These tests pin the rule down for both override kinds.
"""

import re
from pathlib import Path

import pytest
from sqlalchemy import text

from backend.services.gig_hold import _effective_pay_for_artist

GIG_HOLD_SRC = Path(__file__).resolve().parents[1] / "backend" / "services" / "gig_hold.py"

# Every status a preferred_artists row can hold that is NOT an approval.
NON_APPROVED = ["pending", "invited", "denied", "revoked", "banned"]


def _add_pref(db, venue_id, artist_id, status, pay_dollars=None, freq=None):
    db.execute(
        text("""INSERT INTO preferred_artists
                    (venue_id, artist_id, status, pay_dollars_override,
                     pay_cents_override, frequency_days_override)
                VALUES (:v, :a, :s, :pd, 0, :f)"""),
        {"v": venue_id, "a": artist_id, "s": status, "pd": pay_dollars, "f": freq},
    )
    db.commit()


# ── pay override ────────────────────────────────────────────────────

def test_pay_override_applies_when_approved(db, seed_entities):
    """Baseline: an approved row with a higher override wins over slot pay."""
    _add_pref(db, 20, 10, "approved", pay_dollars=300)
    slot = {"deal_type": "flat", "pay": 200}
    assert _effective_pay_for_artist(db, slot, 20, 10) == 300.0


@pytest.mark.parametrize("status", NON_APPROVED)
def test_pay_override_ignored_when_not_approved(db, seed_entities, status):
    """An un-approved row must not raise the artist's pay.

    The `invited` case is the one that matters most: a venue inviting an
    artist and setting a generous override should not pay that rate to
    someone who never accepted the invitation.
    """
    _add_pref(db, 20, 10, status, pay_dollars=300)
    slot = {"deal_type": "flat", "pay": 200}
    assert _effective_pay_for_artist(db, slot, 20, 10) == 200.0


def test_pay_override_never_lowers_pay(db, seed_entities):
    """The override is a floor, not a fixed rate — a lower one is ignored."""
    _add_pref(db, 20, 10, "approved", pay_dollars=50)
    slot = {"deal_type": "flat", "pay": 200}
    assert _effective_pay_for_artist(db, slot, 20, 10) == 200.0


def test_pay_override_skipped_on_door_slot_without_opt_in(db, seed_entities):
    """Door slots ignore the override unless the venue opted in per slot."""
    _add_pref(db, 20, 10, "approved", pay_dollars=300)
    slot = {"deal_type": "door", "guarantee_cents": 20000, "apply_override": 0}
    assert _effective_pay_for_artist(db, slot, 20, 10) == 200.0

    slot_opted_in = {"deal_type": "door", "guarantee_cents": 20000, "apply_override": 1}
    assert _effective_pay_for_artist(db, slot_opted_in, 20, 10) == 300.0


def test_no_preferred_row_returns_base_pay(db, seed_entities):
    slot = {"deal_type": "flat", "pay": 200}
    assert _effective_pay_for_artist(db, slot, 20, 10) == 200.0


# ── frequency override ──────────────────────────────────────────────

def test_every_frequency_override_read_filters_on_approved():
    """Source check: no `frequency_days_override` read may omit the status filter.

    The two frequency reads live inline inside `create_hold_waitlist` and
    `respond_to_hold_offer`, both of which need a booked gig, slots and a
    live offer token to reach — far too much scaffolding to be a useful
    regression guard. Since the SQL is inline string literals, inspecting
    the source is what actually pins the invariant: this fails if anyone
    adds a new read, or drops the filter from an existing one.
    """
    src = GIG_HOLD_SRC.read_text()

    # Each SELECT ... frequency_days_override ... up to the closing quote of
    # the SQL literal. Non-greedy so we stop at the end of each statement.
    reads = re.findall(
        r"SELECT[^\"']*?frequency_days_override.*?(?=\"\"\")",
        src,
        re.DOTALL,
    )
    assert reads, "expected to find frequency_days_override reads — did the file move?"

    offenders = [r for r in reads if "status" not in r]
    assert not offenders, (
        "frequency_days_override read(s) missing a status filter — an "
        "invited/denied/revoked artist would get the venue's frequency "
        f"override applied:\n\n{offenders}"
    )


def test_frequency_reads_are_both_still_present():
    """Guards the test above against silently passing if the reads are renamed."""
    src = GIG_HOLD_SRC.read_text()
    assert src.count("frequency_days_override") == 2, (
        "expected exactly 2 frequency_days_override reads in gig_hold.py; "
        "if that changed, review the new one for a status filter and update "
        "this count"
    )
