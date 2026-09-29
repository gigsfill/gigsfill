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


# ── microdeposit fallback ───────────────────────────────────────────

def test_pending_bank_is_not_saved_as_the_active_payment_method():
    """A bank awaiting microdeposits CANNOT be charged.

    If the frontend saved it via /save-payment-method the venue would look
    ready to book while every charge failed. The microdeposit branch must
    bail out before that call.
    """
    js = (Path(__file__).resolve().parents[1] / "app" / "static" / "js"
          / "venue-stripe-payment.js").read_text()
    start = js.index("verify_with_microdeposits")
    end = js.index("var pmId = collected.setupIntent.payment_method")
    block = js[start:end]
    assert "save-payment-method" not in block, (
        "microdeposit branch must not save the unusable payment method"
    )
    assert "ach-pending-verification" in block
    assert "return;" in block


def test_pending_verification_columns_exist_in_both_schema_and_orm():
    """CLAUDE.md: db.py and models.py are kept in sync by hand."""
    db_py = (Path(__file__).resolve().parents[1] / "backend" / "db.py").read_text()
    models_py = (Path(__file__).resolve().parents[1] / "backend" / "models.py").read_text()
    for col in ("ach_pending_setup_intent_id", "ach_pending_verification_url",
                "ach_pending_bank_last4"):
        assert col in db_py, f"{col} missing from db.py"
        assert col in models_py, f"{col} missing from models.py ORM mirror"


def test_setup_intent_succeeded_webhook_clears_the_pending_state():
    """Promoting a verified bank must also clear the pending markers,
    otherwise the admin panel reports it as stuck forever."""
    src = STRIPE_CONNECT.read_text()
    idx = src.index('elif event_type == "setup_intent.succeeded":')
    block = src[idx:idx + 3500]
    assert "ach_pending_setup_intent_id = NULL" in block
    assert "stripe_payment_method_id = ?" in block


def test_billing_identity_is_resolved_server_side():
    """`venues` has no email column — a venue's contact address lives on the
    owning user. Reading it from /api/venues/{id} in the browser always came
    back empty and blocked every venue at the guard."""
    src = STRIPE_CONNECT.read_text()
    assert '"billing_email": billing_email' in src
    assert "get_all_entity_users" in src
    js = (Path(__file__).resolve().parents[1] / "app" / "static" / "js"
          / "venue-stripe-payment.js").read_text()
    assert "venueGetBillingIdentity" not in js, "dead client-side lookup still present"
    assert "setup.billing_email" in js


def test_venues_table_really_has_no_email_column(db):
    """Pins the fact above, so the client-side lookup isn't reintroduced."""
    cols = [r[1] for r in db.execute(text("PRAGMA table_info(venues)")).fetchall()]
    assert "email" not in cols


# ── admin visibility ────────────────────────────────────────────────

def test_ach_in_flight_route_precedes_the_txn_id_route():
    """FastAPI matches in declaration order: if /{txn_id} came first it
    would swallow 'ach-in-flight' and try to parse it as an integer."""
    src = (Path(__file__).resolve().parents[1] / "backend" / "routes"
           / "admin_payments.py").read_text()
    assert (src.index('"/api/admin/payments/ach-in-flight"')
            < src.index('"/api/admin/payments/{txn_id}"'))


def test_ach_in_flight_reports_held_payouts_and_staleness(db, seed_entities):
    """The numbers an admin actually acts on: how much artist money is held,
    and which charges are past the normal settlement window."""
    from backend.routes.admin_payments import ach_in_flight
    from datetime import datetime, timedelta

    db.execute(text("""INSERT INTO gigs (id, venue_id, date, pay, status)
                       VALUES (500, 20, '2026-09-01', 200, 'completed')"""))
    old = (datetime.utcnow() - timedelta(days=12)).isoformat()
    db.execute(text("""
        INSERT INTO transactions (id, gig_id, from_user_id, to_user_id, amount_cents,
                                  venue_charge_cents, artist_payout_cents,
                                  commission_cents, status, transaction_type,
                                  created_at, stripe_payment_intent_id)
        VALUES (900, 500, 2, 1, 20000, 22000, 0, 2000, 'charge_processing',
                'venue_charge', :c, 'pi_test')
    """), {"c": old})
    db.execute(text("""
        INSERT INTO transactions (id, gig_id, from_user_id, to_user_id, artist_id,
                                  amount_cents, artist_payout_cents, venue_charge_cents,
                                  commission_cents, status, transaction_type,
                                  parent_transaction_id)
        VALUES (901, 500, 2, 1, 10, 20000, 19000, 0, 1000, 'scheduled',
                'artist_payout', 900)
    """))
    db.commit()

    class A: id = 1; is_admin = 'true'
    out = ach_in_flight(admin=A(), db=db)
    assert out["count"] == 1
    row = out["charges"][0]
    assert row["transaction_id"] == 900
    assert row["held_payouts"] == 1
    assert row["held_payout_cents"] == 19000
    assert row["stale"] is True, "a 12-day-old ACH charge should flag as overdue"
    assert out["stale_count"] == 1
    assert out["held_payout_cents"] == 19000


def test_ach_in_flight_is_empty_when_nothing_is_processing(db, seed_entities):
    from backend.routes.admin_payments import ach_in_flight
    class A: id = 1; is_admin = 'true'
    out = ach_in_flight(admin=A(), db=db)
    assert out == {"charges": [], "count": 0, "stale_count": 0, "total_cents": 0,
                   "held_payout_cents": 0, "pending_verification": []}


# ── payment method display ──────────────────────────────────────────

def test_payment_method_endpoint_describes_a_bank_account():
    """It used to read pm.card.* unconditionally.

    For a us_bank_account `pm.card` is None, so it raised AttributeError into
    a bare `except` and returned has_card=False — a venue who had just linked
    a bank saw the empty "add a payment method" form as though nothing had
    happened, while the charge path used the bank quite happily.
    """
    src = STRIPE_CONNECT.read_text()
    idx = src.index("def get_venue_payment_method")
    block = src[idx:idx + 3000]
    assert 'pm_type == "us_bank_account"' in block
    assert '"bank_name"' in block
    # The card branch must be guarded rather than assumed.
    assert 'pm_type == "card" and getattr(pm, "card", None)' in block


def test_payment_method_endpoint_does_not_hide_unknown_types():
    """An unrendered type must still report that a method exists — claiming
    otherwise would prompt the venue to add a second one."""
    src = STRIPE_CONNECT.read_text()
    idx = src.index("def get_venue_payment_method")
    block = src[idx:idx + 3000]
    tail = block[block.index("unrendered payment method type"):]
    assert '"has_card": True' in tail


def test_saved_method_stays_visible_and_both_rails_are_offered():
    """The UI contract the rewrite is built on."""
    html = (Path(__file__).resolve().parents[1] / "app" / "venue-create-gigs.html").read_text()
    for el in ("venuePickCardBtn", "venuePickBankBtn", "venueCardPane",
               "venueBankPane", "venueAchPending"):
        assert f'id="{el}"' in html, f"{el} missing from the payment section"
    # The old always-expanded card form + buried bank divider is gone.
    assert 'id="venueAchSection"' not in html


def test_card_element_is_only_mounted_into_a_visible_pane():
    """Mounting Stripe's Element into a hidden container yields a
    zero-height iframe the venue can't type into."""
    js = (Path(__file__).resolve().parents[1] / "app" / "static" / "js"
          / "venue-stripe-payment.js").read_text()
    idx = js.index("window.venuePickMethod")
    block = js[idx:idx + 900]
    assert "initVenueStripeCard()" in block
    assert "!isOpen" in block


def test_stripe_config_error_cannot_wipe_the_bank_option():
    """initVenueStripeCard writes error HTML into its container. Pointed at
    the whole chooser it would delete the bank rail too."""
    js = (Path(__file__).resolve().parents[1] / "app" / "static" / "js"
          / "venue-stripe-payment.js").read_text()
    idx = js.index("async function initVenueStripeCard")
    block = js[idx:idx + 600]
    assert "getElementById('venueCardPane')" in block
