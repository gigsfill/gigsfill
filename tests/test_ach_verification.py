"""Finishing ACH bank setup (microdeposit verification).

A venue could start bank setup and get parked in Stripe's `requires_action`
state, but nothing in the app could complete it. The only route through was
Stripe's hosted page, whose link was stored and never shown — so a venue that
chose bank payment could not pay, with no message explaining why.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SC = ROOT / "backend" / "routes" / "stripe_connect.py"
JS = ROOT / "app" / "static" / "js" / "ach-verify-panel.js"


def _fn(src, name):
    i = src.index("def " + name)
    rest = src[i:].splitlines(keepends=True)
    out = [rest[0]]
    for ln in rest[1:]:
        if ln.strip() and not ln[0].isspace() and not ln.startswith("@"):
            break
        out.append(ln)
    return "".join(out)


def test_the_submit_endpoint_exists():
    assert '@router.post("/api/stripe/venue/{venue_id}/ach-verify")' in SC.read_text()


def test_both_microdeposit_variants_are_handled():
    """Stripe picks one of two flows. Asking for the wrong one leaves the
    venue unable to finish."""
    fn = _fn(SC.read_text(), "verify_ach_microdeposits")
    assert "amounts" in fn and "descriptor_code" in fn
    js = JS.read_text()
    assert 'microdeposit_type === "descriptor_code"' in js


def test_the_variant_is_stored_and_returned():
    """Without it the form has to guess."""
    src = SC.read_text()
    assert "ach_pending_microdeposit_type = :mdt" in src
    assert '"microdeposit_type"' in src
    schema = (ROOT / "backend" / "db.py").read_text()
    assert '"ach_pending_microdeposit_type TEXT"' in schema


def test_amounts_are_normalised_and_bounded():
    """A venue typing 0.32 means 32 cents. Accepting both readings beats
    failing on a reasonable one — but a value outside 1–99 cents is not a
    microdeposit and should not reach Stripe."""
    fn = _fn(SC.read_text(), "verify_ach_microdeposits")
    assert "n < 1" in fn and "round(n * 100)" in fn
    assert "v > 99" in fn


def test_it_does_not_duplicate_the_webhook():
    """setup_intent.succeeded already promotes the bank account to the
    venue's payment method; writing that row from two places invites drift."""
    fn = _fn(SC.read_text(), "verify_ach_microdeposits")
    assert "stripe_payment_method_id = " not in fn
    assert "setup_intent.succeeded" in SC.read_text()


def test_access_is_checked():
    fn = _fn(SC.read_text(), "verify_ach_microdeposits")
    assert "check_venue_access" in fn and "403" in fn


def test_stripes_own_error_reaches_the_venue():
    """Stripe's message is the only thing that says how many attempts remain."""
    fn = _fn(SC.read_text(), "verify_ach_microdeposits")
    assert "user_message" in fn
    js = JS.read_text()
    assert "out.detail" in js


def test_the_hosted_fallback_is_still_offered():
    """It is the only thing that works if the deposits never arrive and the
    venue needs Stripe support."""
    assert "verification_url" in JS.read_text()


def test_the_panel_hides_itself_when_nothing_is_pending():
    js = JS.read_text()
    assert 'if (!d || !d.pending) { host.innerHTML = ""; return; }' in js


def test_access_helper_is_called_correctly_everywhere_here():
    """check_venue_access is (db, venue_id, user_id) and RAISES 403 — it does
    not return a bool. All three ACH endpoints had it as
    `if not check_venue_access(db, user, venue_id)`: wrong argument order,
    wrong usage, and the name was never imported into this module, so every
    one raised NameError on its first line. ACH venue setup could not have
    worked at all."""
    # Comments quote the broken form on purpose, so read code only.
    src = "\n".join(l for l in SC.read_text().splitlines()
                    if not l.strip().startswith("#"))
    assert "check_venue_access(db, user, venue_id)" not in src
    for m in re.finditer(r"check_venue_access\(([^)]*)\)", src):
        args = [a.strip() for a in m.group(1).split(",")]
        if len(args) == 3:
            assert args[0] == "db" and args[2].endswith("user.id"), m.group(0)


def test_names_used_here_are_actually_in_scope():
    """This module has no top-level `import stripe` and no module-level import
    of check_venue_access; both are obtained per function. A new endpoint that
    forgets is a NameError on the first request, not at import."""
    fn = _fn(SC.read_text(), "verify_ach_microdeposits")
    assert "init_stripe(db)" in fn, "stripe client not obtained the module's way"
    assert "from backend.utils import check_venue_access" in fn
