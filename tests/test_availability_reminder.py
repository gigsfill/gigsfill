"""Weekly "update your availability" reminder.

The trigger is the coverage horizon, not the last time someone clicked. Those
differ in ways that matter: last-activity nags a member who sat down last
month and marked a whole year, while the horizon asks the question that
actually matters — do we know whether this member is free over the window
venues are booking into?
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SVC = ROOT / "backend" / "services" / "availability_reminder.py"
ROUTES = ROOT / "backend" / "routes" / "availability.py"


def test_the_trigger_is_the_horizon_not_the_last_click():
    src = SVC.read_text()
    fn = src[src.index("def members_needing_reminder"):src.index("def bands_for")]
    assert "MAX(d.day)" in fn, "not measuring how far ahead they are covered"
    assert "created_at" not in fn, "fell back to last-activity"


def test_a_member_with_nothing_marked_is_still_asked():
    """No marks is not the same as "available" — it means we cannot tell.
    The copy has to say that rather than assuming either way."""
    src = SVC.read_text()
    sentence = src[src.index("def _horizon_sentence"):][:700]
    assert "no idea which dates to avoid" in sentence


def test_an_always_free_member_can_stop_the_reminder():
    """Someone genuinely free for months has nothing to mark, so their horizon
    never moves. Without an acknowledgement this chases them forever on
    exactly the evidence that they are available."""
    src = SVC.read_text()
    assert "ACK_QUIET_DAYS" in src and "def record_ack" in src
    fn = src[src.index("def members_needing_reminder"):src.index("def bands_for")]
    assert "last_ack" in fn, "acknowledgement is recorded but never honoured"
    assert "MAX_UNANSWERED" in fn, "no cap on unanswered reminders"


def test_engagement_resets_the_unanswered_counter():
    """A member who ignored five emails then finally marked days should not be
    silenced after one more lapse."""
    assert "send_count = 0" in ROUTES.read_text()
    assert "def clear_on_update" in SVC.read_text()


def test_the_ack_token_cannot_be_forged_or_reused_elsewhere():
    src = SVC.read_text()
    assert '_ACK_SALT = "availability-all-clear"' in src, "shares a salt with another flow"
    assert "_ACK_MAX_AGE" in src, "a leaked link would work forever"
    fn = src[src.index("def read_ack_token"):][:400]
    assert "max_age=_ACK_MAX_AGE" in fn
    assert "return None" in fn, "invalid tokens must not raise into the route"


def test_the_ack_page_distinguishes_a_bad_link_from_a_failed_save():
    """Telling someone their good link is invalid sends them hunting for a
    problem that is ours."""
    src = ROUTES.read_text()
    fn = src[src.index("def availability_all_clear"):]
    assert "Link not valid" in fn and "Couldn't save that" in fn and "All clear" in fn


def test_a_missing_template_does_not_raise():
    """get_template falls back to a `notification_type` column this schema
    does not have, so a missing row raises rather than returning None."""
    src = SVC.read_text()
    blk = src[src.index("def send_availability_reminders"):][:900]
    assert "except Exception:" in blk and "tpl = None" in blk


def test_email_preferences_are_respected():
    src = SVC.read_text()
    assert "email_preferences" in src


def test_reminder_state_is_cleaned_up_with_the_user():
    for p in ("backend/routes/me.py", "backend/routes/admin.py"):
        assert "availability_reminders" in (ROOT / p).read_text(), p


def test_the_job_is_idempotent_per_tick():
    """It runs on every scheduler tick, so the weekday+hour gate and
    last_sent_at both have to hold."""
    sched = (ROOT / "backend" / "scheduler.py").read_text()
    fn = sched[sched.index("def _send_availability_reminders"):][:700]
    assert "weekday() != 0" in fn and "hour != 10" in fn
    assert "MIN_GAP_DAYS" in SVC.read_text()
