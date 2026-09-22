"""
Tests for the venue-approval reminder cadence.

`approval_reminder_decision` is the pure decision half of the scheduler's
`_remind_pending_venue_approvals` task. Two cadences overlap here and the
boundaries are easy to get wrong, so they're pinned explicitly:

  - inside 72h: countdown ladder (3d / 2d / 1d), once per tier
  - outside 72h: weekly nudge, so far-out requests don't go silent
"""
from datetime import datetime, timedelta

from backend.scheduler import approval_reminder_decision


NOW = datetime(2026, 9, 22, 12, 0, 0)


def _ago(**kw):
    """Timestamp `kw` before NOW, in the naive-UTC string form the
    pending_approval_tokens columns actually store."""
    return (NOW - timedelta(**kw)).strftime("%Y-%m-%d %H:%M:%S")


# ── Countdown ladder ────────────────────────────────────────────────

def test_inside_24h_fires_1day_tier():
    tier, weekly = approval_reminder_decision(
        hours_until_gig=10, tier_sent=0,
        created_at=_ago(days=1), last_weekly_at=None, now_utc=NOW,
    )
    assert (tier, weekly) == (3, False)


def test_boundaries_are_inclusive_at_upper_edge():
    # Exactly 72h out should already be in the 3-day window.
    assert approval_reminder_decision(
        hours_until_gig=72, tier_sent=0,
        created_at=_ago(days=1), last_weekly_at=None, now_utc=NOW,
    ) == (1, False)
    assert approval_reminder_decision(
        hours_until_gig=48, tier_sent=0,
        created_at=_ago(days=1), last_weekly_at=None, now_utc=NOW,
    ) == (2, False)
    assert approval_reminder_decision(
        hours_until_gig=24, tier_sent=0,
        created_at=_ago(days=1), last_weekly_at=None, now_utc=NOW,
    ) == (3, False)


def test_tier_does_not_resend_at_same_or_lower_urgency():
    # 2-day tier already sent; still in the 2-day window -> nothing due.
    assert approval_reminder_decision(
        hours_until_gig=40, tier_sent=2,
        created_at=_ago(days=1), last_weekly_at=None, now_utc=NOW,
    ) == (0, False)


def test_tier_escalates_to_more_urgent_window():
    # 2-day already sent, gig now inside 24h -> 1-day tier fires.
    assert approval_reminder_decision(
        hours_until_gig=10, tier_sent=2,
        created_at=_ago(days=1), last_weekly_at=None, now_utc=NOW,
    ) == (3, False)


def test_request_made_inside_a_window_skips_earlier_tiers():
    # Requested 40h out — already past the 3-day band (48-72h), so the
    # first nudge this token ever gets is the 2-day tier. Tier 1 never
    # fires for it.
    assert approval_reminder_decision(
        hours_until_gig=40, tier_sent=0,
        created_at=_ago(hours=1), last_weekly_at=None, now_utc=NOW,
    ) == (2, False)


def test_tier_is_the_band_not_the_rounded_day_count():
    # 60h is 2.5 days, but it sits in the 48-72h band, so the "3-day"
    # tier is what fires. Tier numbers track bands, not rounded days.
    assert approval_reminder_decision(
        hours_until_gig=60, tier_sent=0,
        created_at=_ago(hours=1), last_weekly_at=None, now_utc=NOW,
    ) == (1, False)


# ── Weekly nudge ────────────────────────────────────────────────────

def test_weekly_fires_a_week_after_request_when_far_out():
    assert approval_reminder_decision(
        hours_until_gig=24 * 21, tier_sent=0,
        created_at=_ago(days=8), last_weekly_at=None, now_utc=NOW,
    ) == (0, True)


def test_weekly_silent_before_seven_days_elapse():
    assert approval_reminder_decision(
        hours_until_gig=24 * 21, tier_sent=0,
        created_at=_ago(days=3), last_weekly_at=None, now_utc=NOW,
    ) == (0, False)


def test_weekly_measures_from_last_weekly_not_created_at():
    # Requested a month ago but nudged 2 days ago -> not due yet.
    assert approval_reminder_decision(
        hours_until_gig=24 * 21, tier_sent=0,
        created_at=_ago(days=30), last_weekly_at=_ago(days=2), now_utc=NOW,
    ) == (0, False)
    # Same request, last nudge 8 days ago -> due.
    assert approval_reminder_decision(
        hours_until_gig=24 * 21, tier_sent=0,
        created_at=_ago(days=30), last_weekly_at=_ago(days=8), now_utc=NOW,
    ) == (0, True)


def test_weekly_exactly_seven_days_fires():
    assert approval_reminder_decision(
        hours_until_gig=24 * 21, tier_sent=0,
        created_at=_ago(days=7), last_weekly_at=None, now_utc=NOW,
    ) == (0, True)


def test_weekly_never_fires_inside_the_countdown_window():
    # Long-pending request that has now come inside 72h: the countdown
    # ladder owns it, not the weekly cadence.
    tier, weekly = approval_reminder_decision(
        hours_until_gig=50, tier_sent=0,
        created_at=_ago(days=30), last_weekly_at=_ago(days=20), now_utc=NOW,
    )
    assert weekly is False
    assert tier == 1  # 50h sits in the 48-72h band


def test_weekly_ignores_tier_sent():
    # tier_sent only governs the countdown ladder. A token that somehow
    # carries a tier while still far out still gets its weekly nudge.
    assert approval_reminder_decision(
        hours_until_gig=24 * 21, tier_sent=3,
        created_at=_ago(days=8), last_weekly_at=None, now_utc=NOW,
    ) == (0, True)


# ── Malformed input ─────────────────────────────────────────────────

def test_unparseable_timestamp_suppresses_rather_than_spams():
    # A bad timestamp must not fire every single tick.
    assert approval_reminder_decision(
        hours_until_gig=24 * 21, tier_sent=0,
        created_at="not-a-date", last_weekly_at=None, now_utc=NOW,
    ) == (0, False)


def test_missing_timestamps_suppress():
    assert approval_reminder_decision(
        hours_until_gig=24 * 21, tier_sent=0,
        created_at=None, last_weekly_at=None, now_utc=NOW,
    ) == (0, False)


def test_iso_timestamp_with_t_separator_and_z_suffix_parses():
    # created_at is written as naive UTC, but be tolerant of ISO variants.
    ts = (NOW - timedelta(days=8)).isoformat() + "Z"
    assert approval_reminder_decision(
        hours_until_gig=24 * 21, tier_sent=0,
        created_at=ts, last_weekly_at=None, now_utc=NOW,
    ) == (0, True)
