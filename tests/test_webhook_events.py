"""The webhook event contract.

Stripe delivers only the events an endpoint is subscribed to. A handler for an
unsubscribed event is therefore dead code that *looks* alive — nothing errors,
the event simply never arrives, and the failure is invisible until someone
notices the downstream effect.

That is exactly what happened: 2026-09-29 the live endpoint was found
subscribed to 4 of the 9 events the handler implements. `payment_intent.succeeded`
was missing, which meant an ACH charge would park in `charge_processing`
forever and never pay the artist; `charge.refunded`, `transfer.reversed` and
`charge.dispute.closed` had been handled-but-unsubscribed for months, so
refunds and dispute outcomes issued from the Stripe Dashboard never synced
back to `transactions`.

These tests keep the in-code list honest. They cannot check Stripe's remote
subscription — `GET /api/admin/payments/webhook-health` does that live, and
the admin Payments tab shows a banner when it drifts.
"""

import re
from pathlib import Path

from backend.routes.stripe_connect import STRIPE_WEBHOOK_EVENTS

SRC = Path(__file__).resolve().parents[1] / "backend" / "routes" / "stripe_connect.py"


def _handled_in_source():
    """Event names from real `event_type == "..."` branches.

    Comment lines are stripped first: the docstring above STRIPE_WEBHOOK_EVENTS
    quotes the branch syntax as prose, and a naive scan matches that too.
    """
    code = "\n".join(
        line for line in SRC.read_text().splitlines()
        if not line.lstrip().startswith("#")
    )
    return set(re.findall(r'event_type == "([a-z_.]+)"', code))


def test_declared_list_matches_the_implemented_handlers():
    """Adding an `elif event_type == "..."` branch must update the list.

    Without this the list silently goes stale, and it's the list that tells
    anyone which events Stripe needs to be subscribed to.
    """
    declared = set(STRIPE_WEBHOOK_EVENTS)
    handled = _handled_in_source()
    assert declared == handled, (
        f"declared but not handled: {sorted(declared - handled)}; "
        f"handled but not declared: {sorted(handled - declared)}"
    )


def test_list_has_no_duplicates_and_is_sorted():
    """Sorted so diffs stay readable when events are added."""
    assert len(STRIPE_WEBHOOK_EVENTS) == len(set(STRIPE_WEBHOOK_EVENTS))
    assert STRIPE_WEBHOOK_EVENTS == sorted(STRIPE_WEBHOOK_EVENTS)


def test_the_events_ach_settlement_depends_on_are_declared():
    """Named explicitly because their absence is silent.

    Without payment_intent.succeeded an ACH charge never leaves
    charge_processing and the artist is never paid — no error anywhere.
    """
    assert "payment_intent.succeeded" in STRIPE_WEBHOOK_EVENTS
    assert "setup_intent.succeeded" in STRIPE_WEBHOOK_EVENTS


def test_the_money_reconciliation_events_are_declared():
    """Dashboard-issued refunds, reversals and dispute outcomes must sync back,
    or our transactions table diverges from Stripe with no warning."""
    for event in ("charge.refunded", "transfer.reversed", "charge.dispute.closed"):
        assert event in STRIPE_WEBHOOK_EVENTS


def test_webhook_health_endpoint_precedes_the_txn_id_route():
    """FastAPI matches in declaration order; /{txn_id} would swallow this."""
    src = (Path(__file__).resolve().parents[1] / "backend" / "routes"
           / "admin_payments.py").read_text()
    assert (src.index('"/api/admin/payments/webhook-health"')
            < src.index('"/api/admin/payments/{txn_id}"'))


def test_webhook_health_reports_missing_events_not_just_a_boolean():
    """An admin needs to know WHICH events to add, not merely that something
    is wrong."""
    src = (Path(__file__).resolve().parents[1] / "backend" / "routes"
           / "admin_payments.py").read_text()
    idx = src.index("def webhook_health")
    block = src[idx:idx + 2600]
    assert '"missing": missing' in block
    assert '"subscribed": subscribed' in block
    # A disabled endpoint delivers nothing even with a perfect event list.
    assert 'ep.status == "enabled"' in block
