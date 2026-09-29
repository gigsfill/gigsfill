"""ACH (us_bank_account) support for venue payments.

Why this module exists: a card charge is synchronous — `PaymentIntent.create`
either succeeds or raises `CardError`, so `status='charged'` genuinely means
the money is secured. ACH is not. An ACH PaymentIntent returns `processing`
and settles over 3-5 business days, and it can still *return* afterwards
(insufficient funds, closed account, unauthorized debit).

That breaks the payout invariant. Artist payouts gate on the parent venue
charge being in `('charged','paid','transferred')`, so marking an ACH charge
`charged` while it's merely `processing` would transfer money to the artist
that GigsFill has not actually received — and an ACH return days later would
leave the platform eating the payout with no way to recover it.

**The policy is: artists are paid only after the ACH settles.** Chosen
deliberately over paying on the normal next-day schedule; it costs artists a
few days when a venue pays by bank, and costs the platform nothing.

The mechanism reuses machinery that already existed rather than adding a
scheduler. An ACH charge parks the parent in `CHARGE_PROCESSING`, which is
NOT in the payout gate's allowed set, so the child payout rows simply stay
`scheduled` and no transfer fires. When Stripe confirms settlement, the
`payment_intent.succeeded` webhook flips the parent to `charged`, and the
existing hourly stalled-transfer sweep — which already picks up `scheduled`
children whose parent has reached `charged` — pays the artists on its next
run. See `payout_scheduler.py`, the `stalled` query.

Fee note, since it drove the decision to support ACH at all: ACH is 0.8%
capped at $5, against 2.9% + 30c for cards. On a $200 gig that's ~$1.60
instead of ~$6.10.
"""

import logging

logger = logging.getLogger("gigsfill.ach")

# Parent venue_charge status while an ACH debit is in flight. Deliberately
# NOT one of ('charged','paid','transferred') — that's what holds the
# artist payout back, without needing any change to the payout gate.
CHARGE_PROCESSING = "charge_processing"

# Stripe's payment method type for US bank debits.
PM_TYPE = "us_bank_account"

# Platform setting gating the whole feature. Off unless explicitly enabled,
# because offering `us_bank_account` on a Stripe account that hasn't enabled
# ACH makes SetupIntent.create raise — which would break card setup too.
SETTING_KEY = "ach_payments_enabled"


def is_enabled(db) -> bool:
    """Whether ACH is turned on for this platform.

    Accepts either a SQLAlchemy session or a raw DBAPI connection, since
    this is read from both the request path and the scheduler. Any failure
    reads as disabled — never enable a payment rail by accident.
    """
    try:
        from sqlalchemy import text as _t
        row = db.execute(
            _t("SELECT setting_value FROM platform_settings WHERE setting_key = :k"),
            {"k": SETTING_KEY},
        ).mappings().first()
        value = row["setting_value"] if row else None
    except Exception:
        try:
            row = db.execute(
                "SELECT setting_value FROM platform_settings WHERE setting_key = ?",
                (SETTING_KEY,),
            ).fetchone()
            value = row["setting_value"] if row else None
        except Exception as e:
            logger.warning("[ACH] could not read %s, treating as disabled: %s", SETTING_KEY, e)
            return False
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")


def payment_method_types(db) -> list:
    """Payment method types to offer a venue on a SetupIntent."""
    return ["card", PM_TYPE] if is_enabled(db) else ["card"]


def classify_intent_status(pi_status):
    """Map a PaymentIntent status onto what it means for our books.

    Returns one of:
      "charged"    — funds secured, safe to pay the artist
      "processing" — ACH in flight; hold the payout until it settles
      "failed"     — did not go through
      "unknown"    — anything unrecognized; callers treat it as not-charged

    `requires_action` and `requires_payment_method` both count as failures
    here: an off-session charge that needs the customer back at the keyboard
    hasn't taken the money, and our scheduler has no way to prompt them.
    """
    status = str(pi_status or "").strip().lower()
    if status == "succeeded":
        return "charged"
    if status == "processing":
        return "processing"
    if status in ("requires_payment_method", "requires_action", "requires_confirmation",
                  "canceled", "cancelled"):
        return "failed"
    if not status:
        return "unknown"
    return "unknown"


def is_ach_intent(payment_intent) -> bool:
    """Whether a PaymentIntent (object or dict) was paid by bank debit."""
    if payment_intent is None:
        return False
    try:
        types = (payment_intent.get("payment_method_types")
                 if isinstance(payment_intent, dict)
                 else getattr(payment_intent, "payment_method_types", None)) or []
        if PM_TYPE in list(types):
            return True
    except Exception:
        pass
    # Fall back to the charge's own payment_method_details, which is what a
    # webhook payload carries once a charge exists.
    try:
        charge = (payment_intent.get("latest_charge")
                  if isinstance(payment_intent, dict)
                  else getattr(payment_intent, "latest_charge", None))
        if isinstance(charge, dict):
            details = charge.get("payment_method_details") or {}
            return PM_TYPE in details
    except Exception:
        pass
    return False


def settlement_note(gig_date=None) -> str:
    """Human wording for why a payout is waiting. Used in emails and the UI."""
    return (
        "This venue paid by bank transfer (ACH), which takes a few business "
        "days to clear. Your payout is scheduled and will be sent "
        "automatically as soon as it does."
    )
