"""Every user on an artist gets the artist's emails.

Three paths sent to the owner alone — the gig-confirmation reminder, the
open-gig blast to preferred artists, and the Stripe Connect health warnings.
A secondary user on a multi-user artist account never saw them.

Each user has their own `email_preferences` row, so the right default is that
everyone receives everything and silences it individually. That makes the
preference decision per recipient, not per artist: one member opting out must
not silence the rest.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCHED = ROOT / "backend" / "scheduler.py"
HEALTH = ROOT / "backend" / "services" / "connect_health.py"

# The membership union used everywhere: owner plus entity_users.
UNION = re.compile(
    r"SELECT user_id FROM artists WHERE id = \? AND user_id IS NOT NULL\s*"
    r"UNION\s*SELECT user_id FROM entity_users", re.S)


def test_the_gig_confirmation_reminder_reaches_every_member():
    src = SCHED.read_text()
    fn = src[src.index("def process_gig_confirmation"):src.index("def process_open_gig_notifications")]
    assert UNION.search(fn), "still owner-only"
    assert "for _uid, _uemail in _recipients" in fn


def test_the_open_gig_blast_reaches_every_member():
    src = SCHED.read_text()
    fn = src[src.index("def process_open_gig_notifications"):]
    assert UNION.search(fn), "still owner-only"
    assert "for _uid, _uemail in _recips" in fn


def test_the_connect_health_warning_reaches_every_member():
    src = HEALTH.read_text()
    assert "def _artist_recipients" in src
    assert UNION.search(src), "still owner-only"


def test_one_member_opting_out_does_not_silence_the_others():
    """The preference has to be read inside the per-recipient loop. Checking
    it once against the owner would make the owner's choice everyone's."""
    src = SCHED.read_text()
    for marker in ("def process_gig_confirmation", "def process_open_gig_notifications"):
        fn = src[src.index(marker):]
        fn = fn[:fn.index("\ndef ", 10)] if "\ndef " in fn[10:] else fn
        loop = fn[fn.index("WHERE user_id = ?"):]
        assert "_uid" in fn[:fn.index("WHERE user_id = ?")] or "_uid" in loop[:200], \
            f"{marker}: preference not read per recipient"


def test_a_member_with_no_email_is_skipped():
    """A row with a null or blank email would otherwise be handed to the
    mailer and fail per send."""
    for p in (SCHED, HEALTH):
        src = p.read_text()
        assert "TRIM(u.email) != ''" in src, p.name


def test_the_health_warning_falls_back_rather_than_sending_nothing():
    """If the membership lookup returns nothing — a deleted row, a bad join —
    the owner should still be told their payouts are blocked."""
    src = HEALTH.read_text()
    assert "or ([artist_email] if artist_email else [])" in src
    assert "or [artist_email]" in src
