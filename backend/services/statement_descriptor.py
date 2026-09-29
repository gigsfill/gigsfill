"""Build Stripe statement descriptors for venue card charges.

Venues were seeing a bare "GigsFill.com" on their card statement with no
indication of which gig it was for, which makes reconciliation painful when
a venue books several artists in a month. This puts the gig date and the
counterparty name on the line item instead.

Scope — read this before extending it to the artist side:

  * **Venue card charges (PaymentIntent)** — controllable. That's what this
    module is for.
  * **Artist payouts (Transfer)** — NOT controllable. `Transfer` has no
    `statement_descriptor` field at all (verified against stripe-python
    14.3.0). A Transfer moves money between Stripe balances; it is never
    itself a bank transaction, so it has no statement text to set. What the
    artist's bank shows comes from the *Payout* (Connect balance → bank),
    which for Express accounts is scheduled and labelled by Stripe, batches
    several transfers together, and therefore can't carry per-gig detail
    even in principle. For artists we enrich `description` and `metadata`
    instead, which surfaces in their Stripe Express dashboard.

Stripe's constraints, all of which this module enforces:

  * The complete descriptor — the account's prefix plus the suffix we
    supply — must be **at most 22 characters**.
  * It must contain **at least one letter**. A date-only suffix like
    "1015" is rejected, which is why `fallback` exists.
  * `< > \\ " '` are rejected outright. We go further and allow only
    ``[A-Za-z0-9 ]``, since accented and non-Latin characters render
    unpredictably across card issuers.

The prefix is an account-level setting we can't read without an API call on
every charge, so it's configured via `STRIPE_STATEMENT_PREFIX` and defaults
to "GIGSFILL". If the real prefix in the Stripe Dashboard is longer than
what's configured here, Stripe truncates rather than erroring — the result
is ugly, not broken. Check it under Settings → Business → Public details.
"""

import os
import re
from datetime import date, datetime

# Stripe's hard cap on the complete descriptor (prefix + separator + suffix).
STRIPE_DESCRIPTOR_MAX_LEN = 22

DEFAULT_PREFIX = "GIGSFILL"

# Conservative allow-list. Stripe only forbids < > \ " ' but issuers mangle
# anything outside plain ASCII alphanumerics, so we strip more than required.
_DISALLOWED = re.compile(r"[^A-Za-z0-9 ]")
_MULTISPACE = re.compile(r"\s+")


def configured_prefix():
    """The account's statement descriptor prefix, per env config."""
    return (os.getenv("STRIPE_STATEMENT_PREFIX") or DEFAULT_PREFIX).strip()


def suffix_budget(prefix=None):
    """Characters available for the suffix, after the prefix and separator.

    Stripe renders the full descriptor as prefix + separator + suffix, so
    the budget is the 22-char cap minus the prefix minus one separator.
    Returns 0 when the prefix alone already fills the cap.
    """
    p = configured_prefix() if prefix is None else prefix
    return max(0, STRIPE_DESCRIPTOR_MAX_LEN - len(p) - 1)


def sanitize(value):
    """Strip to Stripe-safe characters, uppercase, collapse whitespace."""
    if not value:
        return ""
    cleaned = _DISALLOWED.sub(" ", str(value))
    cleaned = _MULTISPACE.sub(" ", cleaned).strip()
    return cleaned.upper()


def _mmdd(gig_date):
    """Render a gig date as MMDD. Accepts date, datetime or ISO-ish string."""
    if not gig_date:
        return ""
    if isinstance(gig_date, datetime):
        return gig_date.strftime("%m%d")
    if isinstance(gig_date, date):
        return gig_date.strftime("%m%d")
    text = str(gig_date).strip()[:10]
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, fmt).strftime("%m%d")
        except ValueError:
            continue
    return ""


def _has_letter(value):
    return any(ch.isalpha() for ch in value)


def build_suffix(gig_date=None, name=None, fallback="GIG", prefix=None):
    """Build a `statement_descriptor_suffix`, or None to leave it unset.

    Produces "MMDD NAME", trimming the name to whatever the budget allows.
    Returning None is meaningful: it tells the caller to omit the parameter
    entirely so Stripe falls back to the account default, which is always
    preferable to sending something Stripe will reject.

    `fallback` covers the case where there's a date but no usable name —
    a bare "1015" has no letter and Stripe refuses it.
    """
    budget = suffix_budget(prefix)
    if budget <= 0:
        return None

    mmdd = _mmdd(gig_date)
    clean_name = sanitize(name)

    if mmdd and clean_name:
        # Date first: it's fixed-width and the part a venue scans for.
        room_for_name = budget - len(mmdd) - 1
        if room_for_name >= 1:
            candidate = f"{mmdd} {clean_name[:room_for_name]}".strip()
        else:
            candidate = mmdd
    elif clean_name:
        candidate = clean_name[:budget]
    elif mmdd:
        candidate = mmdd
    else:
        return None

    # Stripe rejects a descriptor with no letters, so a date-only suffix
    # needs the fallback word in front of it.
    if not _has_letter(candidate):
        fb = sanitize(fallback) or "GIG"
        combined = f"{fb} {candidate}".strip()
        candidate = combined[:budget] if len(combined) > budget else combined
        if not _has_letter(candidate):
            candidate = fb[:budget]

    candidate = candidate.strip()
    return candidate or None


def for_gig_charge(gig_date, artist_name):
    """Venue's card statement for a gig payment: date + who they paid."""
    return build_suffix(gig_date=gig_date, name=artist_name, fallback="GIG")


def for_platform_fee(gig_date):
    """Venue's card statement for a cancellation/platform fee.

    Deliberately omits the artist name — this charge is a platform fee, not
    a payment to the artist, and labelling it with the artist's name would
    misrepresent what the venue is being billed for.
    """
    return build_suffix(gig_date=gig_date, name=None, fallback="FEE")
