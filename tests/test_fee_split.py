"""
Tests for the platform fee split, including the venue-elected
"we cover the artist's share" option.

`_apply_fee_split` is the single source of truth shared by the booking
path (`_create_booking_transaction`) and the recompute path
(`_recompute_gig_fees`). They used to carry duplicate copies of this
logic, which is exactly how the two drift apart and start disagreeing
about what an artist is owed.

Integer-cents behaviour is pinned deliberately: the venue absorbs the
odd cent (`total // 2`, then `total - venue`), matching what production
already did before the shared helper existed.
"""
import pytest

from backend.routes.gigs import _apply_fee_split


# ── platform-wide splits, no venue election ─────────────────────────

def test_split_halves_with_venue_taking_the_odd_cent():
    assert _apply_fee_split(2000, "split", False) == (1000, 1000)
    # 1501 is odd — venue gets 750, artist 751. Pinning the direction so
    # a future refactor can't silently flip who eats the remainder.
    assert _apply_fee_split(1501, "split", False) == (750, 751)


def test_venue_only_puts_everything_on_the_venue():
    assert _apply_fee_split(2000, "venue_only", False) == (2000, 0)


def test_artist_only_puts_everything_on_the_artist():
    assert _apply_fee_split(2000, "artist_only", False) == (0, 2000)


def test_unknown_split_value_falls_back_to_half():
    # Defensive: a typo'd platform_fee_split must not silently zero the
    # fee or dump it all on one side.
    assert _apply_fee_split(2000, "", False) == (1000, 1000)
    assert _apply_fee_split(2000, "nonsense", False) == (1000, 1000)


# ── venue-elected absorption ────────────────────────────────────────

def test_venue_absorbing_overrides_the_default_split():
    assert _apply_fee_split(2000, "split", True) == (2000, 0)


def test_venue_absorbing_overrides_artist_only():
    # The whole point: a venue that elected to cover the artist can't be
    # overridden by a platform-wide setting that pushes cost the other way.
    assert _apply_fee_split(2000, "artist_only", True) == (2000, 0)


def test_venue_absorbing_is_a_noop_when_already_venue_only():
    assert _apply_fee_split(2000, "venue_only", True) == (2000, 0)


def test_artist_never_pays_when_venue_absorbs():
    for split in ("split", "venue_only", "artist_only", "weird"):
        _, artist = _apply_fee_split(1234, split, True)
        assert artist == 0, f"artist charged under split={split!r}"


# ── invariants that must hold everywhere ────────────────────────────

@pytest.mark.parametrize("total", [0, 1, 2, 3, 999, 1000, 1001, 2000, 123456])
@pytest.mark.parametrize("split", ["split", "venue_only", "artist_only"])
@pytest.mark.parametrize("absorbs", [True, False])
def test_halves_always_sum_to_the_total(total, split, absorbs):
    """The platform's take must not change based on who pays it — a
    rounding bug here would quietly over- or under-charge."""
    venue, artist = _apply_fee_split(total, split, absorbs)
    assert venue + artist == total


@pytest.mark.parametrize("total", [0, 1, 999, 1000, 2001])
@pytest.mark.parametrize("split", ["split", "venue_only", "artist_only"])
@pytest.mark.parametrize("absorbs", [True, False])
def test_neither_side_is_ever_negative(total, split, absorbs):
    venue, artist = _apply_fee_split(total, split, absorbs)
    assert venue >= 0 and artist >= 0


def test_zero_fee_stays_zero():
    assert _apply_fee_split(0, "split", False) == (0, 0)
    assert _apply_fee_split(0, "split", True) == (0, 0)


def test_one_cent_fee_does_not_round_away():
    # total // 2 == 0 for 1 cent, so the artist takes it under 'split'.
    assert _apply_fee_split(1, "split", False) == (0, 1)
    # ...but not when the venue absorbs.
    assert _apply_fee_split(1, "split", True) == (1, 0)


# ── realistic money, end to end ─────────────────────────────────────

def test_median_gig_economics():
    """$200 gig at a 10% platform fee — the numbers a venue and artist
    actually see."""
    pay_cents = 200_00
    total_fee = int(pay_cents * 0.10)          # $20.00

    venue, artist = _apply_fee_split(total_fee, "split", False)
    assert (venue, artist) == (1000, 1000)     # $10 each
    assert pay_cents - artist == 190_00        # artist nets $190

    venue, artist = _apply_fee_split(total_fee, "split", True)
    assert (venue, artist) == (2000, 0)        # venue pays all $20
    assert pay_cents - artist == 200_00        # artist nets the full $200
