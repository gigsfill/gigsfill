"""
Multi-artist user payout routing test
======================================
Reproduces the production scenario where one user owns multiple artist
profiles (e.g. user 1 owns both "Fridays Past" and "Fifty Proof"). Verifies
that when each artist books a slot, the payout transfer is routed to the
CORRECT Stripe Connect account — not to whichever artist happens to have
the lowest id (the bug we found on 2026-05-13).

This test uses an in-memory sqlite DB + a mock Stripe client to assert at
the function-call boundary: "what destination= did we pass to Stripe?"
No real Stripe API calls — fully deterministic, runs in milliseconds.
"""
import sqlite3
import pytest


# ─── Test fixtures ──────────────────────────────────────────────────────────

@pytest.fixture
def conn():
    """In-memory sqlite DB with the minimum schema the payout path touches."""
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript("""
        CREATE TABLE users (
            id INTEGER PRIMARY KEY,
            email TEXT
        );
        CREATE TABLE artists (
            id INTEGER PRIMARY KEY,
            user_id INTEGER,
            name TEXT
        );
        CREATE TABLE venues (
            id INTEGER PRIMARY KEY,
            user_id INTEGER,
            venue_name TEXT
        );
        CREATE TABLE gigs (
            id INTEGER PRIMARY KEY,
            venue_id INTEGER,
            artist_id INTEGER,
            date TEXT,
            start_time TEXT,
            end_time TEXT,
            pay REAL,
            title TEXT,
            status TEXT
        );
        CREATE TABLE gig_slots (
            id INTEGER PRIMARY KEY,
            gig_id INTEGER,
            slot_number INTEGER,
            start_time TEXT,
            end_time TEXT,
            pay REAL,
            status TEXT,
            artist_id INTEGER
        );
        CREATE TABLE entity_payment_settings (
            entity_type TEXT,
            entity_id INTEGER,
            stripe_customer_id TEXT,
            stripe_payment_method_id TEXT,
            stripe_connect_account_id TEXT,
            stripe_connect_onboarding_complete INTEGER DEFAULT 0
        );
        CREATE TABLE transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            gig_id INTEGER,
            from_user_id INTEGER,
            to_user_id INTEGER,
            artist_id INTEGER,
            parent_transaction_id INTEGER,
            transaction_type TEXT,
            status TEXT,
            amount_cents INTEGER,
            venue_charge_cents INTEGER,
            artist_payout_cents INTEGER,
            commission_cents INTEGER,
            credit_card_fee_cents INTEGER,
            payment_method_type TEXT,
            stripe_payment_intent_id TEXT,
            stripe_transfer_id TEXT,
            scheduled_process_at TEXT,
            processed_at TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            notes TEXT,
            charge_attempts INTEGER DEFAULT 0
        );
        CREATE TABLE entity_users (
            entity_type TEXT, entity_id INTEGER, user_id INTEGER
        );
        CREATE TABLE platform_settings (
            setting_key TEXT PRIMARY KEY, setting_value TEXT
        );
    """)

    # ── Seed: ONE user owns TWO artists with DIFFERENT Connect accounts ──
    c.execute("INSERT INTO users (id, email) VALUES (1, 'multi-artist@test.com')")
    c.execute("INSERT INTO artists (id, user_id, name) VALUES (1, 1, 'Fridays Past')")
    c.execute("INSERT INTO artists (id, user_id, name) VALUES (3, 1, 'Fifty Proof')")
    # Note: artist ids 1 and 3 (not 1 and 2) intentionally — exactly mirrors
    # the production data we just fixed, so any id-ordering bug repros here.

    # Distinct Connect accounts — these are what we'll assert the transfers go to
    c.execute("""INSERT INTO entity_payment_settings
        (entity_type, entity_id, stripe_connect_account_id, stripe_connect_onboarding_complete)
        VALUES ('artist', 1, 'acct_FRIDAYS_PAST', 1)""")
    c.execute("""INSERT INTO entity_payment_settings
        (entity_type, entity_id, stripe_connect_account_id, stripe_connect_onboarding_complete)
        VALUES ('artist', 3, 'acct_FIFTY_PROOF',  1)""")

    # Venue owned by user 2
    c.execute("INSERT INTO users (id, email) VALUES (2, 'venue@test.com')")
    c.execute("INSERT INTO venues (id, user_id, venue_name) VALUES (1, 2, '14 Cannons')")

    # One multi-slot gig at the venue, with slot 1 booked by artist 1 and slot 2 by artist 3
    c.execute("""INSERT INTO gigs (id, venue_id, date, start_time, end_time, pay, title, status)
                 VALUES (100, 1, '2026-06-01', '19:00', '23:00', 10.0, 'Test Gig', 'booked')""")
    c.execute("""INSERT INTO gig_slots (id, gig_id, slot_number, start_time, end_time, pay, status, artist_id)
                 VALUES (1, 100, 1, '19:00', '21:00', 10.0, 'booked', 1)""")
    c.execute("""INSERT INTO gig_slots (id, gig_id, slot_number, start_time, end_time, pay, status, artist_id)
                 VALUES (2, 100, 2, '21:00', '23:00', 20.0, 'booked', 3)""")

    # Transactions: parent venue_charge + 2 artist_payout children
    c.execute("""INSERT INTO transactions
        (id, gig_id, from_user_id, to_user_id, artist_id, transaction_type, status,
         amount_cents, venue_charge_cents, commission_cents, payment_method_type)
        VALUES (1000, 100, 2, NULL, NULL, 'venue_charge', 'scheduled',
                3000, 3500, 500, 'stripe')""")
    # Child for artist 1 (lowest id — this is the one that would've won
    # under the buggy user_id lookup):
    c.execute("""INSERT INTO transactions
        (id, gig_id, from_user_id, to_user_id, artist_id, parent_transaction_id,
         transaction_type, status, amount_cents, artist_payout_cents, payment_method_type)
        VALUES (1001, 100, 2, 1, 1, 1000,
                'artist_payout', 'scheduled', 1000, 834, 'stripe')""")
    # Child for artist 3 — under the buggy code, this transfer was routed to
    # artist 1's Connect account (because user_id=1 returned artist 1 first):
    c.execute("""INSERT INTO transactions
        (id, gig_id, from_user_id, to_user_id, artist_id, parent_transaction_id,
         transaction_type, status, amount_cents, artist_payout_cents, payment_method_type)
        VALUES (1002, 100, 2, 1, 3, 1000,
                'artist_payout', 'scheduled', 2000, 1666, 'stripe')""")

    c.commit()
    yield c
    c.close()


class _FakeTransfer:
    """Stand-in Stripe Transfer object that captures the arguments."""
    def __init__(self, **kw):
        self.id = "tr_FAKE_" + str(kw.get("amount", 0))
        self.kw = kw


class _MockStripe:
    """Minimal mock of the stripe module — just enough for _transfer_to_artists.
    Captures every Transfer.create() call so the test can assert routing."""
    def __init__(self):
        self.transfers_created = []
        # Transfer namespace
        self.Transfer = type("T", (), {"create": self._transfer_create})()

    def _transfer_create(_self, idempotency_key=None, **kw):
        # Note: the real stripe.Transfer.create takes idempotency_key as
        # a kwarg. Capture everything for inspection.
        kw["_idempotency_key"] = idempotency_key
        # Mimic stripe's typed return
        return _FakeTransfer(**kw)

    # _transfer_to_artists also references attributes when it constructs
    # error-handling branches. We don't expect them to fire in the happy
    # path, but ensure attribute access is safe.
    class error:
        class StripeError(Exception): pass


def _capture_factory():
    """Build a fresh _MockStripe whose Transfer.create captures into a list."""
    m = _MockStripe()
    captured = []
    real = m.Transfer.create
    def wrap(idempotency_key=None, **kw):
        captured.append({"idempotency_key": idempotency_key, **kw})
        return _FakeTransfer(**kw)
    m.Transfer.create = wrap
    return m, captured


# ─── The actual test ────────────────────────────────────────────────────────

def test_multi_artist_user_routes_payouts_to_correct_connect_accounts(conn):
    """
    Both artists belong to user 1. Slot 1 is artist 1's. Slot 2 is artist 3's.
    After the buggy code: BOTH transfers went to artist 1's Connect account.
    After the fix: each transfer goes to the artist's OWN Connect account.

    Asserts that:
      1. Two transfers are created (one per booked slot)
      2. The transfer for artist 1 (Fridays Past) → acct_FRIDAYS_PAST
      3. The transfer for artist 3 (Fifty Proof)  → acct_FIFTY_PROOF
      4. Source transaction is set to the venue charge (float-risk fix)
      5. Idempotency keys are deterministic per payout id
    """
    from backend.payout_scheduler import _transfer_to_artists

    stripe_mock, captured = _capture_factory()

    # Load the payout rows the way the scheduler does, including artist_id
    payout_rows = conn.execute("""
        SELECT t.id, t.gig_id, t.artist_id, t.amount_cents, t.venue_charge_cents,
               t.artist_payout_cents, t.commission_cents, t.to_user_id, t.from_user_id,
               t.status, g.venue_id, g.date as gig_date
        FROM transactions t
        JOIN gigs g ON t.gig_id = g.id
        WHERE t.parent_transaction_id = 1000
          AND t.transaction_type = 'artist_payout'
        ORDER BY t.id
    """).fetchall()

    assert len(payout_rows) == 2, "expected 2 child payout rows"

    # Call the function under test with the venue charge id
    _transfer_to_artists(
        conn, stripe_mock, payout_rows,
        charge_id="ch_TEST_VENUE_CHARGE",
        venue_id=1,
        parent_txn_id=1000,
        venue_charge_cents=3500,
    )

    # ── Assert 1: exactly two transfers fired ──
    assert len(captured) == 2, f"expected 2 transfers, got {len(captured)}: {captured}"

    # Map each transfer call to its destination Connect account
    transfers_by_dest = {t["destination"]: t for t in captured}

    # ── Assert 2: each artist gets THEIR Connect account ──
    assert "acct_FRIDAYS_PAST" in transfers_by_dest, \
        f"Fridays Past transfer not routed to her account. Got: {list(transfers_by_dest)}"
    assert "acct_FIFTY_PROOF" in transfers_by_dest, \
        f"Fifty Proof transfer not routed to her account. Got: {list(transfers_by_dest)}"

    # ── Assert 3: amount matches the artist's payout cents ──
    assert transfers_by_dest["acct_FRIDAYS_PAST"]["amount"] == 834, \
        "Fridays Past should receive $8.34 (her slot's payout)"
    assert transfers_by_dest["acct_FIFTY_PROOF"]["amount"] == 1666, \
        "Fifty Proof should receive $16.66 (her slot's payout)"

    # ── Assert 4: source_transaction is set to the venue charge ──
    for dest, t in transfers_by_dest.items():
        assert t.get("source_transaction") == "ch_TEST_VENUE_CHARGE", \
            f"Transfer to {dest} missing source_transaction (float-risk fix)"

    # ── Assert 5: idempotency keys are deterministic per payout id ──
    for t in captured:
        ik = t["idempotency_key"]
        assert ik and ik.startswith("payout_") and "_transfer" in ik, \
            f"Transfer idempotency_key looks wrong: {ik!r}"

    # ── Assert 6: DB rows were marked 'transferred' with the right transfer id ──
    rows_after = conn.execute(
        "SELECT id, status, stripe_transfer_id FROM transactions WHERE id IN (1001, 1002)"
    ).fetchall()
    for r in rows_after:
        assert r["status"] == "transferred", \
            f"Txn {r['id']} should be 'transferred', got {r['status']}"
        assert r["stripe_transfer_id"], \
            f"Txn {r['id']} should have a stripe_transfer_id set"


def test_legacy_row_without_artist_id_falls_back_to_user_lookup(conn):
    """Defensive: if a very old txn row has artist_id=NULL, the legacy fallback
    SQL (joining on user_id) kicks in. The fallback is allowed to be lossy
    on multi-artist users, but it must NOT crash and must NOT pick a random
    artist with no Connect account."""
    from backend.payout_scheduler import _transfer_to_artists

    # Insert a single legacy-style child row with artist_id=NULL.
    conn.execute("""INSERT INTO transactions
        (id, gig_id, from_user_id, to_user_id, artist_id, parent_transaction_id,
         transaction_type, status, amount_cents, artist_payout_cents, payment_method_type)
        VALUES (1003, 100, 2, 1, NULL, 1000,
                'artist_payout', 'scheduled', 1000, 834, 'stripe')""")
    conn.commit()

    stripe_mock, captured = _capture_factory()

    payout_rows = conn.execute(
        "SELECT * FROM transactions WHERE id = 1003"
    ).fetchall()

    _transfer_to_artists(
        conn, stripe_mock, payout_rows,
        charge_id="ch_TEST_VENUE_CHARGE",
        venue_id=1,
        parent_txn_id=1000,
        venue_charge_cents=3500,
    )

    # Fallback path: must still produce a transfer to ONE of the two onboarded
    # artists owned by user 1. We can't guarantee which (legacy is lossy by
    # design), but it must be a known onboarded account, not None or garbage.
    assert len(captured) == 1
    dest = captured[0]["destination"]
    assert dest in ("acct_FRIDAYS_PAST", "acct_FIFTY_PROOF"), \
        f"Legacy fallback picked unknown destination: {dest!r}"
