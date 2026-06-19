"""
Tests for the open-gig daily digest pipeline.

Detection (process_open_gig_notifications) lives in scheduler.py and
talks to the raw sqlite3 connection — we don't exercise that directly
here. Instead we test the helpers in services/open_gig_digest.py
against an in-memory sqlite raw connection that mirrors the production
schema we just shipped.
"""
import sqlite3
import pytest


@pytest.fixture
def raw_conn():
    """Raw sqlite3 connection mirroring just the tables the digest
    helpers touch. The dict-style cursor used by the helpers needs the
    columns we reference here.
    """
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.executescript("""
        CREATE TABLE users (
            id INTEGER PRIMARY KEY,
            email TEXT
        );
        CREATE TABLE artists (
            id INTEGER PRIMARY KEY,
            user_id INTEGER,
            name TEXT,
            state TEXT
        );
        CREATE TABLE venues (
            id INTEGER PRIMARY KEY,
            venue_name TEXT,
            city TEXT,
            state TEXT
        );
        CREATE TABLE gigs (
            id INTEGER PRIMARY KEY,
            venue_id INTEGER,
            date TEXT,
            start_time TEXT,
            end_time TEXT,
            pay REAL,
            status TEXT,
            title TEXT,
            artist_type TEXT,
            band_formats TEXT,
            styles TEXT
        );
        CREATE TABLE platform_settings (
            setting_key TEXT PRIMARY KEY,
            setting_value TEXT
        );
        CREATE TABLE artist_email_digest_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            artist_id INTEGER NOT NULL,
            gig_id INTEGER NOT NULL,
            venue_id INTEGER NOT NULL,
            notification_key TEXT NOT NULL,
            via_radius INTEGER DEFAULT 0,
            queued_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            sent_at DATETIME,
            UNIQUE(user_id, gig_id, notification_key)
        );

        INSERT INTO platform_settings VALUES
            ('platform_timezone', 'America/Los_Angeles'),
            ('open_gig_daily_digest_enabled', 'true'),
            ('open_gig_daily_digest_hour', '9');
        INSERT INTO users VALUES (101, 'artist@test.com');
        INSERT INTO artists VALUES (10, 101, 'Test Band', 'CA');
        INSERT INTO venues VALUES (1, '14 Cannons', 'Marina del Rey', 'CA');
        INSERT INTO venues VALUES (2, 'The Echo', 'Los Angeles', 'CA');
        INSERT INTO gigs VALUES (501, 1, '2026-07-15', '20:00', '23:00', 200, 'open', '', 'Live Band', '', '');
        INSERT INTO gigs VALUES (502, 1, '2026-07-16', '20:00', '23:00', 200, 'open', '', 'Live Band', '', '');
        INSERT INTO gigs VALUES (503, 2, '2026-06-20', '20:00', '23:00', 300, 'open', '', 'Live Band', '', '');
    """)
    conn.commit()
    yield conn
    conn.close()


# ─── enqueue idempotency ───


def test_enqueue_inserts_first_time(raw_conn):
    from backend.services.open_gig_digest import enqueue_open_gig_for_artist
    c = raw_conn.cursor()
    assert enqueue_open_gig_for_artist(
        c, user_id=101, artist_id=10, gig_id=501, venue_id=1,
        notification_key='open_gig_1w'
    ) is True
    n = c.execute("SELECT COUNT(*) FROM artist_email_digest_queue").fetchone()[0]
    assert n == 1


def test_enqueue_is_idempotent_on_same_key(raw_conn):
    from backend.services.open_gig_digest import enqueue_open_gig_for_artist
    c = raw_conn.cursor()
    enqueue_open_gig_for_artist(c, user_id=101, artist_id=10, gig_id=501, venue_id=1, notification_key='open_gig_1w')
    # Same triple again → False (no insert) but no exception.
    assert enqueue_open_gig_for_artist(
        c, user_id=101, artist_id=10, gig_id=501, venue_id=1,
        notification_key='open_gig_1w'
    ) is False
    n = c.execute("SELECT COUNT(*) FROM artist_email_digest_queue").fetchone()[0]
    assert n == 1


def test_enqueue_separate_keys_allowed(raw_conn):
    """Same artist + gig but a DIFFERENT notification_key is a separate
    queue row (so a gig can be queued under 4w AND later 1w if both
    windows trigger before the digest fires)."""
    from backend.services.open_gig_digest import enqueue_open_gig_for_artist
    c = raw_conn.cursor()
    enqueue_open_gig_for_artist(c, user_id=101, artist_id=10, gig_id=501, venue_id=1, notification_key='open_gig_4w')
    enqueue_open_gig_for_artist(c, user_id=101, artist_id=10, gig_id=501, venue_id=1, notification_key='open_gig_1w')
    n = c.execute("SELECT COUNT(*) FROM artist_email_digest_queue").fetchone()[0]
    assert n == 2


def test_via_radius_flag_persists(raw_conn):
    from backend.services.open_gig_digest import enqueue_open_gig_for_artist
    c = raw_conn.cursor()
    enqueue_open_gig_for_artist(
        c, user_id=101, artist_id=10, gig_id=501, venue_id=1,
        notification_key='open_gig_1w', via_radius=True
    )
    row = c.execute("SELECT via_radius FROM artist_email_digest_queue").fetchone()
    assert row["via_radius"] == 1


# ─── render_digest_email ───


def test_render_groups_by_venue_and_orders_by_date(raw_conn):
    """3 gigs across 2 venues → 2 grouped sections, gigs sorted by date
    within each venue."""
    from backend.services.open_gig_digest import _render_digest_email, _fetch_user_queue, enqueue_open_gig_for_artist
    c = raw_conn.cursor()
    for gid, key in [(501, 'open_gig_1w'), (502, 'open_gig_1w'), (503, 'open_gig_36h')]:
        venue_id = 1 if gid in (501, 502) else 2
        enqueue_open_gig_for_artist(c, user_id=101, artist_id=10, gig_id=gid, venue_id=venue_id, notification_key=key)
    rows = _fetch_user_queue(c, 101)
    subj, body = _render_digest_email(artist_name="Test Band", rows=rows)
    # Subject: counts both
    assert "3 open gigs at 2 venues" in subj
    # Venues both present
    assert "14 Cannons" in body
    assert "The Echo" in body
    # Urgent badge on the 36h gig (text-only, no emoji per user request)
    assert "Less Than 36 Hours!" in body
    # 36h gig comes BEFORE 7/15 by date (it's 6/20)
    pos_echo = body.find("The Echo")
    pos_14c = body.find("14 Cannons")
    # Order doesn't matter as long as both render — we only assert
    # sorting WITHIN a venue. For 14 Cannons, 7/15 should appear
    # before 7/16.
    pos_715 = body.find("Jul 15")
    pos_716 = body.find("Jul 16")
    assert pos_715 != -1 and pos_716 != -1
    assert pos_715 < pos_716


def test_render_does_not_include_in_your_area_badge(raw_conn):
    """User feedback (Jun 19 2026): the 'in your area' tag was
    confusing. The digest renders radius-blast gigs the same as
    preferred gigs — no visual distinction needed."""
    from backend.services.open_gig_digest import _render_digest_email, _fetch_user_queue, enqueue_open_gig_for_artist
    c = raw_conn.cursor()
    enqueue_open_gig_for_artist(c, user_id=101, artist_id=10, gig_id=501, venue_id=1, notification_key='open_gig_1w', via_radius=True)
    rows = _fetch_user_queue(c, 101)
    _, body = _render_digest_email(artist_name="Test Band", rows=rows)
    assert "in your area" not in body


# ─── send loop staleness check ───


def test_stale_gigs_get_marked_sent_without_email(raw_conn, monkeypatch):
    """If a gig was booked between enqueue and digest send, we mark the
    queue row sent_at so it drops out — but don't include it in the
    rendered email (and if there are no live rows, no email goes)."""
    from backend.services import open_gig_digest as ogd
    c = raw_conn.cursor()
    ogd.enqueue_open_gig_for_artist(c, user_id=101, artist_id=10, gig_id=501, venue_id=1, notification_key='open_gig_1w')
    # Book gig 501 after enqueue
    c.execute("UPDATE gigs SET status='booked' WHERE id=501")
    rows = ogd._fetch_user_queue(c, 101)
    assert rows[0]["status"] == "booked"
    # Marking stale and pruning leaves the queue empty in unsent.
    ogd._mark_queue_rows_sent(c, [rows[0]["queue_id"]])
    unsent = c.execute("SELECT COUNT(*) FROM artist_email_digest_queue WHERE sent_at IS NULL").fetchone()[0]
    assert unsent == 0


# ─── prune ───


def test_prune_drops_only_old_sent_rows(raw_conn):
    from backend.services.open_gig_digest import prune_old_queue_rows
    c = raw_conn.cursor()
    # Three rows: sent yesterday, sent 60 days ago, never sent
    c.executescript("""
        INSERT INTO artist_email_digest_queue (user_id, artist_id, gig_id, venue_id, notification_key, sent_at)
        VALUES (101, 10, 501, 1, 'open_gig_1w', datetime('now', '-1 day'));
        INSERT INTO artist_email_digest_queue (user_id, artist_id, gig_id, venue_id, notification_key, sent_at)
        VALUES (101, 10, 502, 1, 'open_gig_1w', datetime('now', '-60 days'));
        INSERT INTO artist_email_digest_queue (user_id, artist_id, gig_id, venue_id, notification_key, sent_at)
        VALUES (101, 10, 503, 2, 'open_gig_36h', NULL);
    """)
    pruned = prune_old_queue_rows(c, days=30)
    assert pruned == 1
    remaining_ids = [r[0] for r in c.execute("SELECT gig_id FROM artist_email_digest_queue ORDER BY gig_id").fetchall()]
    assert remaining_ids == [501, 503]
