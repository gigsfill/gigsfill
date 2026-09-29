"""ACH (us_bank_account) venue payments.

The invariant under test: **an ACH charge that has not settled must never
open the artist payout gate.** Artist payouts transfer only when the parent
venue_charge is in ('charged','paid','transferred'). A card charge is
synchronous so 'charged' is true the moment PaymentIntent.create returns; an
ACH debit comes back `processing`, settles over 3-5 business days, and can
still return afterwards. Marking it 'charged' early would transfer money to
the artist that GigsFill never received.

The chosen policy is to hold the payout until settlement, so these tests pin
down that `charge_processing` stays outside the gate and that the existing
stalled-transfer sweep picks the children up once the webhook flips the
parent.
"""

import re
from pathlib import Path

import pytest
from sqlalchemy import text

from backend.services import ach

PAYOUT_SCHEDULER = Path(__file__).resolve().parents[1] / "backend" / "payout_scheduler.py"
STRIPE_CONNECT = Path(__file__).resolve().parents[1] / "backend" / "routes" / "stripe_connect.py"

# The only parent states that may release an artist payout.
PAYOUT_GATE = ("charged", "paid", "transferred")


# ── the core invariant ──────────────────────────────────────────────

def test_processing_status_is_outside_the_payout_gate():
    """The whole design rests on this. If it ever becomes true, ACH charges
    would pay artists before the money actually arrives."""
    assert ach.CHARGE_PROCESSING not in PAYOUT_GATE


def test_payout_gate_in_source_still_excludes_processing():
    """Guards the SQL itself, not just the constant.

    Someone widening the gate to 'help' ACH along would silently reintroduce
    the exact risk this feature was designed around.
    """
    src = PAYOUT_SCHEDULER.read_text()
    gates = re.findall(r"p\.status IN \(([^)]*)\)", src)
    assert gates, "expected to find the parent-status gate in the stalled sweep"
    for g in gates:
        assert ach.CHARGE_PROCESSING not in g, (
            f"payout gate includes {ach.CHARGE_PROCESSING!r} — an unsettled ACH "
            f"charge would release the artist payout: {g}"
        )


def test_defense_in_depth_guard_also_excludes_processing():
    """The pre-Transfer re-read guard must reject a processing parent too."""
    src = PAYOUT_SCHEDULER.read_text()
    guards = re.findall(r'_pstatus not in \(([^)]*)\)', src)
    assert guards, "expected the pre-Transfer parent-status guard"
    for g in guards:
        assert ach.CHARGE_PROCESSING not in g


# ── PaymentIntent status classification ─────────────────────────────

@pytest.mark.parametrize("status,expected", [
    ("succeeded", "charged"),
    ("processing", "processing"),
    ("requires_payment_method", "failed"),
    ("requires_action", "failed"),
    ("requires_confirmation", "failed"),
    ("canceled", "failed"),
    ("cancelled", "failed"),
    ("", "unknown"),
    (None, "unknown"),
    ("something_new_from_stripe", "unknown"),
])
def test_classify_intent_status(status, expected):
    assert ach.classify_intent_status(status) == expected


def test_classification_is_case_and_whitespace_tolerant():
    assert ach.classify_intent_status("  SUCCEEDED  ") == "charged"
    assert ach.classify_intent_status("Processing") == "processing"


def test_only_succeeded_ever_counts_as_charged():
    """Any status we don't explicitly recognize must not bank money."""
    for status in ["processing", "requires_action", "canceled", "weird", "", None]:
        assert ach.classify_intent_status(status) != "charged"


# ── ACH detection ───────────────────────────────────────────────────

def test_detects_ach_from_payment_method_types():
    assert ach.is_ach_intent({"payment_method_types": ["us_bank_account"]})
    assert not ach.is_ach_intent({"payment_method_types": ["card"]})


def test_detects_ach_from_charge_details():
    """Webhook payloads carry the method on the charge, not the intent."""
    assert ach.is_ach_intent({
        "payment_method_types": [],
        "latest_charge": {"payment_method_details": {"us_bank_account": {"last4": "6789"}}},
    })


def test_non_ach_and_malformed_inputs_are_safe():
    assert not ach.is_ach_intent(None)
    assert not ach.is_ach_intent({})
    assert not ach.is_ach_intent({"payment_method_types": None})
    assert not ach.is_ach_intent({"latest_charge": None})
    assert not ach.is_ach_intent(object())


# ── feature flag ────────────────────────────────────────────────────

def _set_flag(db, value):
    db.execute(text("DELETE FROM platform_settings WHERE setting_key = :k"),
               {"k": ach.SETTING_KEY})
    if value is not None:
        db.execute(
            text("INSERT INTO platform_settings (setting_key, setting_value) VALUES (:k, :v)"),
            {"k": ach.SETTING_KEY, "v": value},
        )
    db.commit()


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on", " true "])
def test_flag_enabled_values(db, value):
    _set_flag(db, value)
    assert ach.is_enabled(db) is True


@pytest.mark.parametrize("value", ["0", "false", "no", "off", "", "banana"])
def test_flag_disabled_values(db, value):
    _set_flag(db, value)
    assert ach.is_enabled(db) is False


def test_flag_absent_means_disabled(db):
    """A brand-new install must not silently accept bank payments."""
    _set_flag(db, None)
    assert ach.is_enabled(db) is False


def test_unreadable_settings_table_means_disabled():
    """A payment rail must never switch on because a query failed."""
    class Broken:
        def execute(self, *a, **kw):
            raise RuntimeError("no such table")
    assert ach.is_enabled(Broken()) is False


def test_payment_method_types_follow_the_flag(db):
    _set_flag(db, "0")
    assert ach.payment_method_types(db) == ["card"]
    _set_flag(db, "1")
    assert ach.payment_method_types(db) == ["card", ach.PM_TYPE]


# ── webhook wiring ──────────────────────────────────────────────────

def test_succeeded_webhook_only_touches_processing_rows():
    """It must not disturb card charges, which are already 'charged'."""
    src = STRIPE_CONNECT.read_text()
    idx = src.index('elif event_type == "payment_intent.succeeded":')
    block = src[idx:idx + 2500]
    assert "t.status = 'charge_processing'" in block, (
        "the succeeded handler must scope to charge_processing rows only"
    )
    assert "status = 'charged'" in block


def test_failed_webhook_covers_an_ach_return():
    """An ACH return arrives while the parent sits in charge_processing.

    If that status isn't in the handler's filter, the return is silently
    dropped: the parent stays 'charge_processing' forever, the artist is
    never paid, and nobody is told.
    """
    src = STRIPE_CONNECT.read_text()
    idx = src.index('elif event_type == "payment_intent.payment_failed":')
    block = src[idx:idx + 1500]
    assert "charge_processing" in block


def test_ach_return_email_does_not_say_card_declined():
    """Wording matters here — it's a returned bank debit, not a decline."""
    src = STRIPE_CONNECT.read_text()
    assert "Bank payment returned" in src
    assert "The artist has not been paid" in src


# ── charge path ─────────────────────────────────────────────────────

def test_charge_path_does_not_transfer_while_processing():
    """The processing branch must bail out before _transfer_to_artists."""
    src = PAYOUT_SCHEDULER.read_text()
    idx = src.index('if _pi_state == "processing":')
    block = src[idx:src.index('if _pi_state != "charged":')]
    assert "_transfer_to_artists" not in block, (
        "the ACH-processing branch must not transfer to artists"
    )
    assert "continue" in block


def test_charge_path_rejects_unknown_intent_states():
    """requires_action and friends must take the failure path, not 'charged'."""
    src = PAYOUT_SCHEDULER.read_text()
    assert 'if _pi_state != "charged":' in src
    idx = src.index('if _pi_state != "charged":')
    assert "_handle_charge_failure" in src[idx:idx + 900]


# ── admin plumbing ──────────────────────────────────────────────────

def test_admin_get_and_put_both_know_the_ach_key():
    """The GET and PUT handlers keep separate `payment_keys` lists.

    Adding the key to only one is a silent failure: the toggle renders and
    appears to save, but the value is dropped on write (or never loads).
    """
    src = (Path(__file__).resolve().parents[1] / "backend" / "routes" / "admin.py").read_text()
    get_idx = src.index('@router.get("/api/admin/payment-settings")')
    put_idx = src.index('@router.put("/api/admin/payment-settings")')
    get_block = src[get_idx:put_idx]
    put_block = src[put_idx:put_idx + 3000]
    assert "'ach_payments_enabled'" in get_block, "GET payment_keys missing the ACH key"
    assert "'ach_payments_enabled'" in put_block, "PUT payment_keys missing the ACH key"


def test_setup_intent_falls_back_to_card_only_on_error():
    """Naming us_bank_account on a Stripe account without ACH raises, and
    that must not take card setup down with it."""
    src = STRIPE_CONNECT.read_text()
    idx = src.index("pm_types = ach.payment_method_types(db)")
    block = src[idx:idx + 1400]
    assert 'pm_types = ["card"]' in block
    assert "SetupIntent.create" in block
