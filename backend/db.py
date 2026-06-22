"""
GigsFill Database Configuration and Setup
==========================================
This module handles all database configuration, table creation, and initial data population.
If no database exists, it will be created automatically with all required tables.
"""

import sqlite3
import os
from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Database path / URL
# Set DATABASE_URL env var to switch to PostgreSQL:
#   export DATABASE_URL="postgresql://user:password@host:5432/gigsfill"
DB_PATH = Path(__file__).parent.parent / "backend.db"
DATABASE_URL = os.environ.get("DATABASE_URL") or f"sqlite:///{DB_PATH}"

# Normalize DigitalOcean / Heroku postgres:// -> postgresql://
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# SQLAlchemy setup
from sqlalchemy import event

# ── Database engine — supports both SQLite (dev/legacy) and PostgreSQL (prod) ──
_IS_POSTGRES = DATABASE_URL.startswith("postgresql")

if _IS_POSTGRES:
    # PostgreSQL: use connection pooling
    engine = create_engine(
        DATABASE_URL,
        pool_size=10,          # maintain up to 10 persistent connections
        max_overflow=20,       # allow 20 extra connections under burst load
        pool_timeout=30,       # wait up to 30s for a connection before error
        pool_pre_ping=True,    # verify connections are alive before using
        pool_recycle=1800,     # recycle connections every 30 min
    )
    import logging as _lg
    _lg.getLogger("gigsfill.db").info("✅ Database: PostgreSQL with connection pooling")

    # Audit fix (May 2026 part 10c): translate SQLite-flavored SQL on the
    # fly for every text() query the ORM issues. The _sqlite_to_pg function
    # is defined below this engine-creation block; we attach the listener
    # lazily after class definition (see _attach_pg_translation_listener
    # at the end of this module).
else:
    # SQLite: single-writer with WAL mode for concurrent reads
    engine = create_engine(
        DATABASE_URL,
        connect_args={"check_same_thread": False},
        pool_size=5,
        max_overflow=10,
    )

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=10000")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA cache_size=-16000")
        cursor.execute("PRAGMA wal_autocheckpoint=500")
        cursor.execute("PRAGMA temp_store=MEMORY")
        cursor.execute("PRAGMA mmap_size=134217728")
        cursor.close()

    import logging as _lg
    _lg.getLogger("gigsfill.db").info("✅ Database: SQLite with WAL mode")

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def get_db():
    """FastAPI dependency for database sessions"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def get_db_connection():
    """
    Get a raw database connection compatible with both SQLite and PostgreSQL.
    
    Returns a connection whose cursor uses dict-style rows (sqlite3.Row equivalent).
    Uses ? placeholders for SQLite, %s for PostgreSQL — handled transparently.
    All callers can use the same API: conn.execute(), conn.commit(), conn.close()
    """
    if _IS_POSTGRES:
        import psycopg2
        import psycopg2.extras
        # Parse postgresql:// URL
        conn = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
        conn.autocommit = False
        # Wrap to translate ? -> %s so existing callers need no changes
        return _PgCompatConn(conn)
    else:
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        return conn


def _sqlite_to_pg(sql: str) -> str:
    """Translate SQLite-flavored SQL to PostgreSQL.

    Audit fix (May 2026 part 10c): the codebase has ~190 SQLite-specific
    patterns spread across raw `conn.execute(...)` calls AND SQLAlchemy
    `db.execute(text(...))` calls. Rather than rewriting each one (high
    risk of breaking SQLite + huge surface area), we translate at the
    boundary. This function is called from BOTH:
      - _PgCompatConn._translate() — covers raw-conn callers
      - sqlalchemy.event.listen 'before_cursor_execute' — covers ORM text() callers

    Pattern coverage (best-effort, not 100% — see the doc string at the
    bottom of this file for what's NOT translated):
      * `?` placeholders → `%s`
      * `INSERT OR IGNORE INTO` → `INSERT INTO ... ON CONFLICT DO NOTHING`
      * `INSERT OR REPLACE INTO` → flagged (caller must use ON CONFLICT DO UPDATE)
      * `datetime('now', '-N days|hours|minutes|seconds')` → `(CURRENT_TIMESTAMP - INTERVAL 'N <unit>')`
      * `datetime('now', '+N days|hours|...')` → `(CURRENT_TIMESTAMP + INTERVAL '...')`
      * `datetime('now')` → `CURRENT_TIMESTAMP`
      * `date('now')` → `CURRENT_DATE`
      * `date('now', '+N days')` → `(CURRENT_DATE + INTERVAL 'N days')`
      * `julianday(a) - julianday(b)` → `EXTRACT(EPOCH FROM (a::timestamp - b::timestamp))/86400.0`
      * `last_insert_rowid()` → `lastval()`
      * `strftime('%Y', x)` → `to_char(x::timestamp, 'YYYY')`
      * `strftime('%Y-%m', x)` → `to_char(x::timestamp, 'YYYY-MM')`
      * `strftime('%Y-%m-%d', x)` → `to_char(x::timestamp, 'YYYY-MM-DD')`
      * `INTEGER PRIMARY KEY AUTOINCREMENT` → `SERIAL PRIMARY KEY`

    Patterns NOT translated (run a manual audit if migrating):
      * `INSERT OR REPLACE` (requires column-list awareness)
      * Complex `strftime` formats outside the ones above
      * `PRAGMA` statements (no-ops on PG; setup code already branches)
      * SQLite-specific `||` string-concat patterns work natively on PG — no-op
      * `JSON_*` functions (not used in this codebase)
    """
    if not sql:
        return sql
    import re as _re
    s = sql
    # Placeholder translation
    s = s.replace("?", "%s")
    # INTEGER PRIMARY KEY AUTOINCREMENT (mainly in CREATE TABLE)
    s = _re.sub(
        r'\bINTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT\b',
        'SERIAL PRIMARY KEY', s, flags=_re.IGNORECASE
    )
    # INSERT OR IGNORE → INSERT ... ON CONFLICT DO NOTHING (append at end before any RETURNING)
    if _re.search(r'\bINSERT\s+OR\s+IGNORE\b', s, _re.IGNORECASE):
        s = _re.sub(r'\bINSERT\s+OR\s+IGNORE\b', 'INSERT', s, flags=_re.IGNORECASE)
        # Append ON CONFLICT DO NOTHING before the terminator (handles trailing ; or RETURNING)
        if not _re.search(r'\bON\s+CONFLICT\b', s, _re.IGNORECASE):
            # Insert before optional trailing semicolon
            s = s.rstrip(' ;\n\r\t')
            s = s + " ON CONFLICT DO NOTHING"
    # INSERT OR REPLACE: too complex to autotranslate (needs column list).
    # Log a warning so admin can find the call site during migration.
    if _re.search(r'\bINSERT\s+OR\s+REPLACE\b', s, _re.IGNORECASE):
        # Strip the SQLite-only keyword so PG at least parses; the semantic
        # is wrong (will fail on conflict) but the error will be loud.
        s = _re.sub(r'\bINSERT\s+OR\s+REPLACE\b', 'INSERT', s, flags=_re.IGNORECASE)
        import logging as _l
        _l.getLogger('gigsfill.db').warning(
            "SQL contains INSERT OR REPLACE which needs manual ON CONFLICT DO UPDATE for PG: %s",
            sql[:200]
        )
    # date/datetime arithmetic
    # datetime('now', '-N <unit>') / datetime('now', '+N <unit>')
    def _dt_interval(m):
        sign, n, unit = m.group(1), m.group(2), m.group(3).lower()
        # Normalize unit
        unit_map = {'day': 'days', 'days': 'days', 'hour': 'hours', 'hours': 'hours',
                    'minute': 'minutes', 'minutes': 'minutes',
                    'second': 'seconds', 'seconds': 'seconds',
                    'month': 'months', 'months': 'months', 'year': 'years', 'years': 'years'}
        unit = unit_map.get(unit, unit)
        return f"(CURRENT_TIMESTAMP {sign} INTERVAL '{n} {unit}')"
    s = _re.sub(
        r"datetime\(\s*'now'\s*,\s*'\s*([+-])\s*(\d+)\s+(\w+)\s*'\s*\)",
        _dt_interval, s, flags=_re.IGNORECASE
    )
    s = _re.sub(
        r"date\(\s*'now'\s*,\s*'\s*([+-])\s*(\d+)\s+(\w+)\s*'\s*\)",
        lambda m: f"(CURRENT_DATE {m.group(1)} INTERVAL '{m.group(2)} {m.group(3)}')",
        s, flags=_re.IGNORECASE
    )
    s = _re.sub(r"datetime\(\s*'now'\s*\)", 'CURRENT_TIMESTAMP', s, flags=_re.IGNORECASE)
    s = _re.sub(r"date\(\s*'now'\s*\)", 'CURRENT_DATE', s, flags=_re.IGNORECASE)
    # julianday subtraction. Special-case 'now' since the inner-arg rewrite
    # needs to apply before the subtraction pattern matches.
    s = _re.sub(
        r"julianday\(\s*'now'\s*\)",
        "julianday(CURRENT_TIMESTAMP)", s, flags=_re.IGNORECASE
    )
    s = _re.sub(
        r"julianday\(([^)]+)\)\s*-\s*julianday\(([^)]+)\)",
        r"EXTRACT(EPOCH FROM ((\1)::timestamp - (\2)::timestamp))/86400.0",
        s, flags=_re.IGNORECASE
    )
    # last_insert_rowid()
    s = _re.sub(r'\blast_insert_rowid\(\s*\)', 'lastval()', s, flags=_re.IGNORECASE)
    # strftime('%Y', x), strftime('%Y-%m', x), strftime('%Y-%m-%d', x)
    s = _re.sub(
        r"strftime\(\s*'%Y'\s*,\s*([^)]+)\)",
        r"to_char((\1)::timestamp, 'YYYY')", s, flags=_re.IGNORECASE
    )
    s = _re.sub(
        r"strftime\(\s*'%Y-%m'\s*,\s*([^)]+)\)",
        r"to_char((\1)::timestamp, 'YYYY-MM')", s, flags=_re.IGNORECASE
    )
    s = _re.sub(
        r"strftime\(\s*'%Y-%m-%d'\s*,\s*([^)]+)\)",
        r"to_char((\1)::timestamp, 'YYYY-MM-DD')", s, flags=_re.IGNORECASE
    )
    return s


class _PgCompatConn:
    """
    Thin wrapper around psycopg2 connection that translates sqlite3-style ?
    placeholders to PostgreSQL %s — lets all existing raw-SQL callers work
    unchanged when we switch DATABASE_URL to postgresql://.
    """
    def __init__(self, conn):
        self._conn = conn
        self.row_factory = None  # accepted but ignored (psycopg2 uses RealDictCursor)

    @staticmethod
    def _translate(sql):
        """Translate SQLite → PG using the central rewriter."""
        return _sqlite_to_pg(sql)

    def execute(self, sql, params=None):
        cur = self._conn.cursor()
        cur.execute(self._translate(sql), params or ())
        return cur

    def cursor(self):
        return _PgCompatCursor(self._conn.cursor())

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type:
            self.rollback()
        else:
            self.commit()
        self.close()


class _PgCompatCursor:
    """Wraps psycopg2 cursor to translate SQLite SQL → PostgreSQL."""
    def __init__(self, cur):
        self._cur = cur

    def execute(self, sql, params=None):
        self._cur.execute(_sqlite_to_pg(sql), params or ())
        return self

    def executemany(self, sql, seq):
        self._cur.executemany(_sqlite_to_pg(sql), seq)

    def fetchone(self):
        return self._cur.fetchone()

    def fetchall(self):
        return self._cur.fetchall()

    @property
    def rowcount(self):
        return self._cur.rowcount

    @property
    def lastrowid(self):
        return self._cur.lastrowid

    def close(self):
        self._cur.close()

    def __iter__(self):
        return iter(self._cur)

def _setup_conn():
    """Return a raw connection for schema setup — routes to SQLite or PostgreSQL."""
    if _IS_POSTGRES:
        import psycopg2
        conn = psycopg2.connect(DATABASE_URL)
        conn.autocommit = False
        return conn
    else:
        return sqlite3.connect(str(DB_PATH))


def _setup_placeholder(sql):
    """Translate SQLite SQL → PostgreSQL for setup-time DDL/DML."""
    if _IS_POSTGRES:
        return _sqlite_to_pg(sql).replace(
            "INTEGER PRIMARY KEY", "SERIAL PRIMARY KEY"
        )
    return sql


def setup_database():
    """
    Create all database tables and populate initial data.
    Safe to run multiple times - uses CREATE TABLE IF NOT EXISTS.
    Works with both SQLite and PostgreSQL.
    """
    db_path = str(DB_PATH)
    conn = _setup_conn()
    cursor = conn.cursor()
    
    # ==========================================
    # USERS
    # ==========================================
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email VARCHAR NOT NULL UNIQUE,
            password VARCHAR NOT NULL,
            first_name VARCHAR,
            last_name VARCHAR,
            phone VARCHAR,
            is_admin INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # Audit fix (May 2026 part 6): declare columns here so Postgres deploys
    # have them at startup. Previously these were added lazily via SQLite-only
    # PRAGMA paths inside auth.py / me.py which silently no-op on Postgres,
    # so email-change, verify-email, signup-collision throttle, and
    # delete-preview all 500'd on PG.
    _add_columns(cursor, "users", [
        "email_verified INTEGER DEFAULT 0",
        "signup_collision_last_at TIMESTAMP",
        # iCal calendar feed token — random UUID. Surfaced via
        # /api/calendar/{token}.ics so users can subscribe in Google
        # Calendar / Apple Calendar / Outlook without needing auth on
        # every fetch (the token IS the auth).
        "calendar_token TEXT",
        # SMS opt-in. Users with a phone + carrier can elect to receive
        # urgent (1-week, 36-hour, cancellation) notifications via SMS
        # in addition to email. Sent via the carrier's email-to-SMS
        # gateway — no Twilio account required.
        "sms_notifications_enabled INTEGER DEFAULT 0",
    ])

    # ==========================================
    # ARTISTS
    # ==========================================
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS artists (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name VARCHAR,
            city VARCHAR,
            state VARCHAR,
            latitude FLOAT,
            longitude FLOAT,
            bio TEXT,
            artist_type VARCHAR,
            band_formats VARCHAR,
            styles VARCHAR,
            booking_contact VARCHAR,
            spotify_url TEXT,
            instagram_url TEXT,
            facebook_url TEXT,
            youtube_url TEXT,
            twitter_url TEXT,
            tiktok_url TEXT,
            website_url TEXT,
            display_order INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)
    _add_columns(cursor, "artists", [
        "latitude FLOAT",
        "longitude FLOAT",
        "spotify_url TEXT",
        "instagram_url TEXT",
        "facebook_url TEXT",
        "youtube_url TEXT",
        "twitter_url TEXT",
        "tiktok_url TEXT",
        "website_url TEXT",
        "display_order INTEGER DEFAULT 0",
        "styles VARCHAR",
        "avg_rating REAL DEFAULT NULL",
        "review_count INTEGER DEFAULT 0",
        # Comma-separated list of brand keys in the order the user wants them
        # displayed on the public profile's Social Media tab. NULL means use
        # the natural fallback order from artist-profile.html. Example:
        # "instagram,spotify,website,facebook,youtube,twitter,tiktok"
        "social_order TEXT",
    ])
    
    # ==========================================
    # VENUES
    # ==========================================
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS venues (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            venue_name VARCHAR,
            description TEXT,
            address_line_1 VARCHAR,
            address_line_2 VARCHAR,
            city VARCHAR,
            state VARCHAR,
            postal_code VARCHAR,
            latitude FLOAT,
            longitude FLOAT,
            venue_size VARCHAR,
            has_stage BOOLEAN DEFAULT 0,
            stage_width_ft INTEGER,
            stage_depth_ft INTEGER,
            setup_location_description TEXT,
            has_sound_equipment BOOLEAN DEFAULT 0,
            sound_equipment_description TEXT,
            has_sound_engineer BOOLEAN DEFAULT 0,
            sound_engineer_details TEXT,
            has_lighting BOOLEAN DEFAULT 0,
            lighting_description TEXT,
            load_in_out_details TEXT,
            arrival_time_type VARCHAR,
            arrival_no_earlier_than_hour INTEGER,
            arrival_no_earlier_than_period VARCHAR,
            default_pay_dollars INTEGER DEFAULT 0,
            default_pay_cents INTEGER DEFAULT 0,
            bar_tab_details TEXT,
            food_tab_details TEXT,
            artist_frequency_days INTEGER,
            website_url TEXT,
            facebook_url TEXT,
            instagram_url TEXT,
            twitter_url TEXT,
            yelp_url TEXT,
            google_maps_url TEXT,
            display_order INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)
    _add_columns(cursor, "venues", [
        "latitude FLOAT",
        "longitude FLOAT",
        "description TEXT",
        "address_line_2 VARCHAR",
        "venue_size VARCHAR",
        "default_pay_dollars INTEGER DEFAULT 0",
        "default_pay_cents INTEGER DEFAULT 0",
        "artist_frequency_days INTEGER",
        "stage_width_ft INTEGER",
        "stage_depth_ft INTEGER",
        "setup_location_description TEXT",
        "sound_equipment_description TEXT",
        "has_sound_engineer BOOLEAN DEFAULT 0",
        "sound_engineer_details TEXT",
        "lighting_description TEXT",
        "load_in_out_details TEXT",
        "arrival_time_type VARCHAR",
        "arrival_no_earlier_than_hour INTEGER",
        "arrival_no_earlier_than_period VARCHAR",
        "bar_tab_details TEXT",
        "food_tab_details TEXT",
        "website_url TEXT",
        "facebook_url TEXT",
        "instagram_url TEXT",
        "twitter_url TEXT",
        "yelp_url TEXT",
        "google_maps_url TEXT",
        "display_order INTEGER DEFAULT 0",
        # Comma-separated brand keys for the order of social tiles on the
        # public venue profile. NULL = fallback to natural order.
        "social_order TEXT",
    ])
    
    # ==========================================
    # GIGS
    # ==========================================
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS gigs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            venue_id INTEGER NOT NULL,
            artist_id INTEGER,
            date VARCHAR NOT NULL,
            start_time VARCHAR,
            end_time VARCHAR,
            title VARCHAR,
            pay INTEGER,
            notes TEXT,
            status VARCHAR DEFAULT 'open',
            artist_type VARCHAR,
            band_formats VARCHAR,
            styles VARCHAR,
            is_recurring BOOLEAN DEFAULT 0,
            recurring_group_id VARCHAR,
            recurrence_pattern TEXT,
            recurring INTEGER,
            recurring_interval_weeks INTEGER,
            recurring_days_of_week TEXT,
            recurring_end_type TEXT,
            recurring_end_after INTEGER,
            recurring_end_by_date TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (venue_id) REFERENCES venues(id),
            FOREIGN KEY (artist_id) REFERENCES artists(id)
        )
    """)
    _add_columns(cursor, "gigs", [
        "title VARCHAR",
        "pay INTEGER",
        "is_recurring BOOLEAN DEFAULT 0",
        "recurring_group_id VARCHAR",
        "recurrence_pattern TEXT",
        "recurring INTEGER",
        "recurring_interval_weeks INTEGER",
        "recurring_days_of_week TEXT",
        "recurring_end_type TEXT",
        "recurring_end_after INTEGER",
        "recurring_end_by_date TEXT",
        "frequency_exempt INTEGER DEFAULT 0",
        "is_multi_slot INTEGER DEFAULT 1",
        "contract_hold_artist_id INTEGER",
        "contract_hold_expires_at TEXT",
        "styles VARCHAR",
        "radius_blast_token VARCHAR",
        "last_cancelled_artist_id INTEGER",
    ])

    # Add PRO certification columns to venues
    # Venue rating aggregates — mirror artists.avg_rating /
    # artists.review_count. Maintained by the bidirectional-review
    # writer in routes/reviews.py whenever an artist submits a venue
    # review (POST /api/artists/{id}/venues/{vid}/review).
    _add_columns(cursor, "venues", [
        "avg_rating REAL DEFAULT NULL",
        "review_count INTEGER DEFAULT 0",
    ])
    _add_columns(cursor, "venues", [
        "pro_certified INTEGER DEFAULT 0",
        "pro_certified_at TIMESTAMP",
        "payment_status VARCHAR DEFAULT 'active'",
        "payment_suspended_at TIMESTAMP",
        "payment_suspension_reason TEXT",
        "auto_flyers INTEGER DEFAULT 0",
        "default_flyer_template_id INTEGER",
        # Per-venue timezone (May 2026). IANA string, e.g. "America/Los_Angeles".
        # Auto-derived from venues.state on first access via
        # backend.utils.get_venue_timezone_str() if NULL/empty. Used to compute
        # `transactions.scheduled_process_at` in venue-local time → UTC.
        "timezone TEXT",
    ])
    
    # ==========================================
    # GIG_SLOTS — every gig has one or more slots
    # ==========================================
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS gig_slots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            gig_id INTEGER NOT NULL,
            slot_number INTEGER NOT NULL,
            start_time VARCHAR NOT NULL,
            end_time VARCHAR NOT NULL,
            pay REAL DEFAULT 0,
            artist_id INTEGER,
            status VARCHAR DEFAULT 'open',
            artist_type VARCHAR,
            band_formats VARCHAR,
            styles VARCHAR,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (gig_id) REFERENCES gigs(id) ON DELETE CASCADE,
            FOREIGN KEY (artist_id) REFERENCES artists(id)
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_gig_slots_gig ON gig_slots(gig_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_gig_slots_artist ON gig_slots(artist_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_gig_slots_status ON gig_slots(gig_id, status)")
    # Perf fix (Jun 2026): door_settle.py + payout queries filter by
    # (gig_id, artist_id, status='scheduled'). Add a covering composite.
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_gig_slots_gig_artist_status ON gig_slots(gig_id, artist_id, status)")
    # _add_columns kept for existing DBs that pre-date inline column definitions
    _add_columns(cursor, "gig_slots", [
        "artist_type VARCHAR",
        "band_formats VARCHAR",
        "styles VARCHAR",
    ])
    # ── Door deal / split-pay (Jun 2026) ─────────────────────────────────
    # `pay` on a slot is the venue-promised guarantee. For door deals,
    # the venue may also commit a percentage of door receipts on top of
    # (or in lieu of) the guarantee. After the show the venue files
    # door_receipts_cents via POST /api/gigs/{id}/slots/{sid}/settle;
    # backend computes final_pay = guarantee + (receipts * door_pct/100)
    # and updates the existing payout transaction.
    #   deal_type: 'flat' (default — only `pay` matters) | 'door'
    #              (compute settled_pay from inputs below)
    #   door_pct: int 0-100 — share of receipts paid to the artist
    #   guarantee_cents: floor pay regardless of receipts (can be 0)
    #   door_receipts_cents: total door collected (entered post-show)
    #   settled_pay_cents: the computed final pay (set on settle)
    #   settled_at: timestamp of settlement
    _add_columns(cursor, "gig_slots", [
        "deal_type VARCHAR DEFAULT 'flat'",
        "door_pct INTEGER DEFAULT 0",
        "guarantee_cents INTEGER DEFAULT 0",
        "door_receipts_cents INTEGER",
        "settled_pay_cents INTEGER",
        "settled_at TIMESTAMP",
    ])
    
    # ==========================================
    # VENUE CONTRACTS (contract templates per venue)
    # ==========================================
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS venue_contracts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            venue_id INTEGER NOT NULL,
            contract_type TEXT NOT NULL,
            name TEXT NOT NULL DEFAULT 'Standard Contract',
            is_active INTEGER DEFAULT 1,
            require_for_booking INTEGER DEFAULT 0,
            pdf_file_path TEXT,
            contract_body TEXT,
            custom_fields TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (venue_id) REFERENCES venues(id)
        )
    """)
    
    # ==========================================
    # GIG CONTRACTS (per-booking contract instances)
    # ==========================================
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS gig_contracts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            gig_id INTEGER NOT NULL,
            venue_contract_id INTEGER NOT NULL,
            venue_id INTEGER NOT NULL,
            artist_id INTEGER NOT NULL,
            contract_type TEXT NOT NULL,
            rendered_body TEXT,
            filled_fields TEXT,
            pdf_file_path TEXT,
            signed_pdf_path TEXT,
            status TEXT DEFAULT 'pending',
            artist_signature_name TEXT,
            artist_signature_date TIMESTAMP,
            artist_signature_ip TEXT,
            venue_signature_name TEXT,
            venue_signature_date TIMESTAMP,
            venue_signature_ip TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (gig_id) REFERENCES gigs(id),
            FOREIGN KEY (venue_contract_id) REFERENCES venue_contracts(id),
            FOREIGN KEY (venue_id) REFERENCES venues(id),
            FOREIGN KEY (artist_id) REFERENCES artists(id)
        )
    """)
    
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_venue_contracts_venue ON venue_contracts(venue_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_gig_contracts_gig ON gig_contracts(gig_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_gig_contracts_artist ON gig_contracts(artist_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_gig_contracts_status ON gig_contracts(status)")
    
    # Add contract hold columns
    _add_columns(cursor, "venue_contracts", [
        "per_gig_pdf INTEGER DEFAULT 0",
    ])
    _add_columns(cursor, "gig_contracts", [
        "hold_expires_at TEXT",
    ])
    
    # ==========================================
    # GIG_EMAIL_LOG (tracks automated emails sent to avoid duplicates)
    # ==========================================
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS gig_email_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            gig_id INTEGER NOT NULL,
            venue_id INTEGER NOT NULL,
            notification_key TEXT NOT NULL,
            sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            recipient_count INTEGER DEFAULT 0,
            FOREIGN KEY (gig_id) REFERENCES gigs(id),
            FOREIGN KEY (venue_id) REFERENCES venues(id),
            UNIQUE(gig_id, notification_key)
        )
    """)

    # notification_sent_log: tracks cancel/delete notification cleanup
    # separate from gig_email_log (which tracks scheduled blast emails)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS notification_sent_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            gig_id INTEGER NOT NULL,
            notification_key TEXT NOT NULL,
            sent_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_nsl_gig ON notification_sent_log(gig_id)"
    )

    # ==========================================
    # ARTIST_MEDIA
    # ==========================================
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS artist_media (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            artist_id INTEGER NOT NULL,
            media_type VARCHAR,
            title VARCHAR,
            file_path VARCHAR,
            video_url VARCHAR,
            display_order INTEGER DEFAULT 0,
            -- Per-item caption / notes (audio rows on artist-edit show this above
            -- the audio player). Title is short (max ~65 chars); caption is for
            -- longer context / description.
            caption TEXT,
            FOREIGN KEY (artist_id) REFERENCES artists(id)
        )
    """)
    
    # ==========================================
    # VENUE_MEDIA
    # ==========================================
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS venue_media (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            venue_id INTEGER NOT NULL,
            media_type VARCHAR NOT NULL,
            title VARCHAR,
            file_path VARCHAR,
            video_url VARCHAR,
            display_order INTEGER DEFAULT 0,
            -- Mirror of artist_media.caption; symmetrical because the same
            -- /api/media/{id} PUT updates either table by id.
            caption TEXT,
            FOREIGN KEY (venue_id) REFERENCES venues(id)
        )
    """)
    
    # ==========================================
    # PREFERRED_ARTISTS
    # ==========================================
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS preferred_artists (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            venue_id INTEGER NOT NULL,
            artist_id INTEGER NOT NULL,
            status VARCHAR DEFAULT 'pending',
            frequency_days_override INTEGER,
            pay_dollars_override INTEGER,
            pay_cents_override INTEGER,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (venue_id) REFERENCES venues(id),
            FOREIGN KEY (artist_id) REFERENCES artists(id),
            UNIQUE(venue_id, artist_id)
        )
    """)
    _add_columns(cursor, "preferred_artists", [
        "status VARCHAR DEFAULT 'pending'",
        "frequency_days_override INTEGER",
        "pay_dollars_override INTEGER",
        "pay_cents_override INTEGER",
    ])
    
    # ==========================================
    # ADMIN AUDIT LOG
    # ==========================================
    # Audit fix (May 2026): every admin mutation should leave a record so we
    # can reconstruct manual interventions on real users / financial state.
    # `before_json` and `after_json` are best-effort snapshots of the row(s)
    # touched. `metadata_json` carries flow-specific context (e.g. reason
    # for force-cancel). Audit writes are best-effort and NEVER fail the
    # admin action that triggered them.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS admin_audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            admin_user_id INTEGER,
            admin_email TEXT,
            action TEXT NOT NULL,
            target_table TEXT,
            target_id TEXT,
            before_json TEXT,
            after_json TEXT,
            metadata_json TEXT,
            ip_address TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_admin_audit_log_admin_user ON admin_audit_log(admin_user_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_admin_audit_log_created_at ON admin_audit_log(created_at)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_admin_audit_log_target ON admin_audit_log(target_table, target_id)")

    # ==========================================
    # SYSTEM ALERTS (Jun 2026)
    # ==========================================
    # Operational issues that need admin attention — surfaced as a banner
    # on admin.html. Self-clearing: when the underlying condition is
    # detected as resolved (e.g. a stripe webhook successfully verifies
    # again), `resolved_at` is set automatically. Admin can also manually
    # acknowledge to dismiss before auto-resolve catches up.
    #
    # Grouping: one ACTIVE row per (alert_type) at a time. Repeated
    # incidents increment `count` and bump `last_seen_at` instead of
    # spawning duplicate rows — keeps the banner from spamming when a
    # condition fires once per minute for an hour.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS system_alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            alert_type TEXT NOT NULL,
            severity TEXT NOT NULL,
            message TEXT NOT NULL,
            details TEXT,
            first_seen_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            last_seen_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            count INTEGER DEFAULT 1,
            resolved_at DATETIME,
            resolved_by TEXT,
            acknowledged_at DATETIME,
            acknowledged_by TEXT
        )
    """)
    # The partial index makes the "active alert per type" lookup an
    # index seek instead of a scan. Filtering on resolved_at IS NULL
    # at the index level matches the hot read pattern.
    cursor.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_system_alerts_active_type
        ON system_alerts(alert_type) WHERE resolved_at IS NULL
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_system_alerts_resolved ON system_alerts(resolved_at)")

    # ==========================================
    # USED RESET TOKENS (H9 single-use guard, May 2026)
    # ==========================================
    # Reset password tokens carry a `jti` claim (random uuid). After a token
    # is consumed by /api/reset-password, its jti is recorded here. The route
    # rejects any token whose jti is already present — preventing replay if a
    # link is leaked (URL-bar history, email forwarding, browser sync, etc).
    # Rows older than RESET_TOKEN_MAX_AGE (1h) are pruned opportunistically
    # at write time; the index supports both lookup and pruning.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS used_reset_tokens (
            jti TEXT PRIMARY KEY,
            used_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_used_reset_tokens_used_at ON used_reset_tokens(used_at)")

    # ==========================================
    # VANITY URLS (slug → entity) — May 2026
    # ==========================================
    # Public profile share URLs: gigsfill.com/fridayspast resolves to either
    # an artist or venue. One table holds both so the slug namespace is
    # shared (only one entity can own a given slug). Slugs are lowercased
    # at insertion. Lookup is by primary key for resolver-route latency.
    # See backend/routes/vanity.py for slug generation rules, reserved
    # words, and the resolver route.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS vanity_urls (
            slug         TEXT PRIMARY KEY,
            entity_type  TEXT NOT NULL CHECK(entity_type IN ('artist','venue')),
            entity_id    INTEGER NOT NULL,
            created_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at   DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_vanity_entity ON vanity_urls(entity_type, entity_id)")

    # Vanity slug redirect log (May 2026 part 10): when a user renames their
    # vanity URL, the OLD slug is kept here as a redirect record for 90 days
    # so previously-shared links still work, AND so the slug can't be
    # immediately re-claimed by someone else (reputation-hijack defense).
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS vanity_url_redirects (
            old_slug      TEXT PRIMARY KEY,
            new_slug      TEXT NOT NULL,
            entity_type   TEXT NOT NULL,
            entity_id     INTEGER NOT NULL,
            created_at    DATETIME DEFAULT CURRENT_TIMESTAMP,
            expires_at    DATETIME NOT NULL,
            -- After expires_at + a cooldown (default 30d), the slug is fully
            -- released and can be claimed by anyone.
            reclaim_after DATETIME NOT NULL
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_vanity_redirects_new_slug ON vanity_url_redirects(new_slug)")

    # Venue private notes per (venue, gig, artist) — May 2026 part 10i.
    # Powers the My Artists → Past Gigs modal's editable Notes column. These
    # are the VENUE's private notes about a specific past gig with an artist
    # (e.g. "great crowd response", "showed up late"), distinct from the
    # public gigs.notes field.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS venue_gig_notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            venue_id  INTEGER NOT NULL,
            gig_id    INTEGER NOT NULL,
            artist_id INTEGER NOT NULL,
            notes     TEXT,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(venue_id, gig_id, artist_id)
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_venue_gig_notes ON venue_gig_notes(venue_id, artist_id)")

    # Artist private notes per (artist, gig, venue) — May 2026 part 10j.
    # Mirror of venue_gig_notes for the artist-side My Venues → Past Gigs modal.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS artist_gig_notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            artist_id INTEGER NOT NULL,
            gig_id    INTEGER NOT NULL,
            venue_id  INTEGER NOT NULL,
            notes     TEXT,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(artist_id, gig_id, venue_id)
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_artist_gig_notes ON artist_gig_notes(artist_id, venue_id)")

    # ==========================================
    # NOTIFICATIONS
    # ==========================================
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            notification_type VARCHAR NOT NULL,
            title VARCHAR NOT NULL,
            message TEXT NOT NULL,
            gig_id INTEGER,
            venue_id INTEGER,
            artist_id INTEGER,
            cancellation_reason TEXT,
            is_read BOOLEAN DEFAULT 0,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id),
            FOREIGN KEY (gig_id) REFERENCES gigs(id),
            FOREIGN KEY (venue_id) REFERENCES venues(id),
            FOREIGN KEY (artist_id) REFERENCES artists(id)
        )
    """)
    _add_columns(cursor, "notifications", [
        "notification_type VARCHAR",
        "gig_id INTEGER",
        "venue_id INTEGER",
        "artist_id INTEGER",
        "cancellation_reason TEXT",
        "entity_type VARCHAR",
        "entity_id INTEGER",
        "action_token VARCHAR",
    ])
    
    # ==========================================
    # ENTITY_USERS
    # ==========================================
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS entity_users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_type TEXT NOT NULL,
            entity_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            role TEXT DEFAULT 'member',
            added_by_user_id INTEGER,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id),
            FOREIGN KEY (added_by_user_id) REFERENCES users(id),
            UNIQUE(entity_type, entity_id, user_id)
        )
    """)
    _add_columns(cursor, "entity_users", [
        "added_by_user_id INTEGER",
        "created_at DATETIME DEFAULT CURRENT_TIMESTAMP",
    ])
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_entity_users_lookup ON entity_users(entity_type, entity_id)")
    # Add radius_miles to venue_email_notifications (for existing DBs pre-dating this column)
    _add_columns(cursor, "venue_email_notifications", [
        "radius_miles INTEGER DEFAULT NULL",
        "blast_all_radius INTEGER DEFAULT NULL",      # miles for open-gig radius blast (NULL = disabled)
        "blast_all_enabled INTEGER DEFAULT 0",        # whether radius blast is enabled for this row
        "blink_enabled INTEGER DEFAULT 0",            # whether to blink gig bubble after email fires
        "blink_color TEXT DEFAULT NULL",              # hex color for blink (NULL = use system default)
    ])
    
    # ==========================================
    # ENTITY_INVITATIONS
    # ==========================================
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS entity_invitations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_type TEXT NOT NULL,
            entity_id INTEGER NOT NULL,
            entity_name TEXT NOT NULL,
            invited_email TEXT NOT NULL,
            invited_by_user_id INTEGER NOT NULL,
            inviter_first_name TEXT,
            inviter_last_name TEXT,
            token TEXT NOT NULL UNIQUE,
            status TEXT DEFAULT 'pending',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            responded_at DATETIME,
            FOREIGN KEY (invited_by_user_id) REFERENCES users(id)
        )
    """)
    
    # ==========================================
    # EMAIL_TEMPLATES
    # ==========================================
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS email_templates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            template_key TEXT NOT NULL UNIQUE,
            subject TEXT,
            body TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # ==========================================
    # EMAIL_PREFERENCES
    # ==========================================
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS email_preferences (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            notification_type TEXT NOT NULL,
            enabled BOOLEAN DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id),
            UNIQUE(user_id, notification_type)
        )
    """)
    
    # ==========================================
    # SMS_PREFERENCES
    # ==========================================
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sms_preferences (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            notification_type TEXT NOT NULL,
            enabled BOOLEAN DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id),
            UNIQUE(user_id, notification_type)
        )
    """)
    
    # ==========================================
    # USER_SETTINGS (key-value per user, e.g. sms_carrier)
    # ==========================================
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_settings (
            user_id INTEGER NOT NULL,
            setting_key TEXT NOT NULL,
            setting_value TEXT,
            UNIQUE(user_id, setting_key)
        )
    """)
    
    # ==========================================
    # VENUE_EMAIL_NOTIFICATIONS (automated gig emails)
    # ==========================================
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS venue_email_notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            venue_id INTEGER NOT NULL,
            notification_key TEXT NOT NULL,
            enabled INTEGER DEFAULT 0,
            time_value INTEGER DEFAULT 1,
            time_unit TEXT DEFAULT 'weeks',
            radius_miles INTEGER DEFAULT NULL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (venue_id) REFERENCES venues(id),
            UNIQUE(venue_id, notification_key)
        )
    """)
    
    # ==========================================
    # VENUE_EMAIL_HISTORY
    # ==========================================
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS venue_email_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            venue_id INTEGER NOT NULL,
            venue_name TEXT NOT NULL,
            user_id INTEGER NOT NULL,
            subject TEXT NOT NULL,
            body TEXT NOT NULL,
            recipient_count INTEGER NOT NULL,
            sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            recipients_json TEXT,
            FOREIGN KEY (venue_id) REFERENCES venues(id),
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)
    
    # ==========================================
    # PAYMENT_METHODS
    # ==========================================
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS payment_methods (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            payment_type VARCHAR NOT NULL,
            account_identifier VARCHAR NOT NULL,
            account_display_name VARCHAR,
            is_preferred BOOLEAN DEFAULT 0,
            is_verified BOOLEAN DEFAULT 0,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)
    
    # ==========================================
    # ENTITY_PAYMENT_SETTINGS (For Venues and Artists)
    # ==========================================
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS entity_payment_settings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_type VARCHAR NOT NULL,
            entity_id INTEGER NOT NULL,
            default_payment_method VARCHAR DEFAULT 'stripe',
            stripe_account_id VARCHAR,
            stripe_publishable_key VARCHAR,
            stripe_secret_key VARCHAR,
            stripe_onboarding_complete BOOLEAN DEFAULT 0,
            paypal_email VARCHAR,
            venmo_username VARCHAR,
            zelle_email VARCHAR,
            cashapp_cashtag VARCHAR,
            bank_account_last4 VARCHAR,
            bank_routing_last4 VARCHAR,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(entity_type, entity_id)
        )
    """)
    
    _add_columns(cursor, "entity_payment_settings", [
        "stripe_customer_id VARCHAR",
        "stripe_payment_method_id VARCHAR",
        "stripe_connect_account_id VARCHAR",
        "stripe_connect_onboarding_complete BOOLEAN DEFAULT 0",
    ])
    
    # ==========================================
    # TRANSACTIONS
    # ==========================================
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            gig_id INTEGER NOT NULL,
            from_user_id INTEGER NOT NULL,
            to_user_id INTEGER NOT NULL,
            artist_id INTEGER,
            amount_cents INTEGER NOT NULL,
            venue_charge_cents INTEGER NOT NULL,
            artist_payout_cents INTEGER NOT NULL,
            commission_cents INTEGER NOT NULL,
            credit_card_fee_cents INTEGER DEFAULT 0,
            payment_method_type VARCHAR,
            payment_method_from VARCHAR,
            payment_method_to VARCHAR,
            status VARCHAR DEFAULT 'pending',
            scheduled_process_at DATETIME,
            processed_at DATETIME,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            notes TEXT,
            stripe_payment_intent_id VARCHAR,
            stripe_transfer_id VARCHAR,
            external_transaction_id VARCHAR,
            FOREIGN KEY (gig_id) REFERENCES gigs(id),
            FOREIGN KEY (from_user_id) REFERENCES users(id),
            FOREIGN KEY (to_user_id) REFERENCES users(id)
        )
    """)
    _add_columns(cursor, "transactions", [
        "credit_card_fee_cents INTEGER DEFAULT 0",
        "scheduled_process_at DATETIME",
        "stripe_payment_intent_id VARCHAR",
        "stripe_transfer_id VARCHAR",
        "external_transaction_id VARCHAR",
        "artist_id INTEGER",
        "charge_attempts INTEGER DEFAULT 0",
        "last_charge_attempt_at DATETIME",
        "charge_failure_reason TEXT",
        "cancel_reason TEXT",
        "cancelled_at DATETIME",
        "platform_fee_charged_cents INTEGER DEFAULT 0",
        "transaction_type VARCHAR DEFAULT 'single'",
        "parent_transaction_id INTEGER",
        # Jun 2026 audit: replace the "notes LIKE 'Slot {id}%'"
        # disambiguation pattern with a real column. The notes-LIKE
        # filter shipped in door_settle.py and cancel_slot_payment
        # to handle "same artist booked two slots on the same gig"
        # — it works but is fragile (notes also carries audit
        # suffixes, regex-style chars in the notes break the LIKE
        # match). Code that needs to know which slot a child row
        # represents now reads transactions.slot_id directly.
        # Booking writes it; legacy rows (NULL) fall back to the
        # notes parse via the helper in services/payment_helpers.
        "slot_id INTEGER",
    ])
    
    # ==========================================
    # PAYMENT_CANCELLATIONS
    # ==========================================
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS payment_cancellations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            transaction_id INTEGER NOT NULL,
            gig_id INTEGER NOT NULL,
            cancelled_by_user_id INTEGER NOT NULL,
            cancellation_reason TEXT NOT NULL,
            cancelled_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (transaction_id) REFERENCES transactions(id),
            FOREIGN KEY (gig_id) REFERENCES gigs(id),
            FOREIGN KEY (cancelled_by_user_id) REFERENCES users(id)
        )
    """)
    
    # ==========================================
    # PLATFORM_SETTINGS
    # ==========================================
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS platform_settings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            setting_key VARCHAR NOT NULL UNIQUE,
            setting_value VARCHAR NOT NULL,
            description TEXT,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_by INTEGER,
            FOREIGN KEY (updated_by) REFERENCES users(id)
        )
    """)
    
    # Insert default platform settings
    default_settings = [
        ("commission_percentage", "5", "Platform commission percentage"),
        ("credit_card_fee_percentage", "3.5", "Credit card processing fee percentage"),
        ("payment_processing_hour", "17", "Hour of day (24h) to process payments (17 = 5pm)"),
        ("payment_processing_delay_days", "1", "Days after gig to process payment"),
        ("platform_email", "", "Email address for sending notifications"),
        ("platform_email_password", "", "App password for email"),
        ("platform_smtp_server", "smtp.gmail.com", "SMTP server address"),
        ("platform_smtp_port", "587", "SMTP port"),
        ("support_email", "", "Support email address"),
        ("support_email_password", "", "Support email app password"),
        ("support_smtp_server", "smtp.gmail.com", "Support SMTP server"),
        ("support_smtp_port", "587", "Support SMTP port"),
        ("support_display_name", "", "Display name for support emails"),
        ("admin_alert_email", "", "Email address for admin system alerts (chargebacks, failures, etc)"),
        # Admin payment accounts
        ("admin_stripe_publishable_key", "", "Stripe publishable key"),
        ("admin_stripe_secret_key", "", "Stripe secret key"),
        ("admin_stripe_webhook_secret", "", "Stripe webhook secret"),
        ("platform_fee_percent", "10", "Platform fee percentage charged to venue"),
        ("platform_fee_split", "split", "How fee is split: split, venue_only, artist_only"),
        ("platform_min_fee", "20", "Minimum platform fee in dollars"),
        ("payments_enabled", "0", "Whether live Stripe payments are active"),
        ("payout_time", "17:00", "Daily payout time in 24hr format (HH:MM)"),
        ("platform_timezone", "America/Los_Angeles", "Platform timezone for scheduling (IANA format)"),
        ("admin_paypal_email", "", "PayPal business email"),
        ("admin_paypal_client_id", "", "PayPal client ID"),
        ("admin_paypal_client_secret", "", "PayPal client secret"),
        ("admin_venmo_username", "", "Venmo business username"),
        ("admin_venmo_link", "", "Venmo business profile link"),
        ("admin_zelle_email", "", "Zelle email"),
        ("admin_zelle_phone", "", "Zelle phone"),
        ("admin_cashapp_cashtag", "", "Cash App $cashtag"),
        ("far_booking_alert_miles", "50", "Distance (mi) beyond which a booking flags a far-away-artist alert to the venue + soft notice to the artist"),
        # Rate limits — integer "requests per minute" per source IP. Read live by
        # backend/rate_limiter.py via callable getters; admin can edit from
        # Platform Settings UI without a restart. Keep in sync with rate_limiter.py:_DEFAULTS.
        ("rate_login",          "5",  "Login attempts per minute per IP"),
        ("rate_signup",         "3",  "Account creations per minute per IP"),
        ("rate_password_reset", "3",  "Password reset requests per minute per IP"),
        ("rate_support",        "2",  "Support tickets per minute per IP"),
        ("rate_email_send",     "10", "Email-sending actions (invites, messages, recommendations) per minute per IP"),
        ("rate_aff_track",      "30", "Affiliate click tracking pings per minute per IP"),
        # Part 10p (Phase 3): async bounce detection via IMAP polling of the
        # platform email inbox. When enabled, the scheduler reads UNSEEN DSN
        # messages every 30 minutes, parses the failed recipient, and marks
        # any matching artist_invitations row status='bounced' with the SMTP
        # diagnostic in bounce_reason. Off by default — admin must explicitly
        # configure IMAP server + opt in.
        ("bounce_check_enabled",       "false", "Enable async bounce polling via IMAP (off by default)"),
        ("bounce_check_imap_server",   "",      "IMAP server hostname for bounce inbox (e.g. imap.gmail.com)"),
        ("bounce_check_imap_port",     "993",   "IMAP SSL port (typically 993)"),
        ("bounce_check_imap_username", "",      "IMAP username (defaults to platform_email if blank)"),
        ("bounce_check_imap_password", "",      "IMAP password / app password (defaults to platform_email_password if blank)"),
        ("bounce_check_last_run_at",   "",      "Timestamp the scheduler last polled the bounce inbox (set by scheduler)"),
        ("bounce_check_last_result",   "",      "Summary of last poll: '<scanned> scanned, <bounced> bounced' or error reason"),
        # Jun 19 2026: open-gig notification consolidation. When true (default),
        # the hourly scheduler ENQUEUES open-gig notifications to
        # artist_email_digest_queue and the morning digest job sends one
        # consolidated email per artist (grouped by venue). When false, the
        # original per-gig per-window send path is used. Flag exists so we can
        # revert quickly if the digest pipeline has issues — flip to false in
        # admin Platform Settings without a code change.
        ("open_gig_daily_digest_enabled", "true", "Bundle open-gig notifications into one morning email per artist (recommended). Set to false to revert to per-gig per-window send."),
        ("open_gig_daily_digest_hour",    "9",    "Local-time hour (0-23) to send each artist's daily open-gig digest. Defaults to 9 (9 AM)."),
    ]

    for setting_key, setting_value, description in default_settings:
        try:
            cursor.execute(
                "INSERT INTO platform_settings (setting_key, setting_value, description) VALUES (?, ?, ?)",
                (setting_key, setting_value, description)
            )
        except sqlite3.IntegrityError:
            pass  # Setting already exists
    
    # ==========================================
    # FLYERS
    # ==========================================
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS flyers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            venue_id INTEGER,
            gig_id INTEGER,
            artist_id INTEGER,
            name TEXT,
            canvas_data TEXT DEFAULT '{}',
            thumbnail_data TEXT DEFAULT '',
            is_template INTEGER DEFAULT 0,
            size_preset TEXT DEFAULT 'instagram_post',
            width INTEGER DEFAULT 1080,
            height INTEGER DEFAULT 1350,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    _add_columns(cursor, "flyers", [
        "size_preset TEXT DEFAULT 'instagram_post'",
        "width INTEGER DEFAULT 1080",
        "height INTEGER DEFAULT 1350",
        "thumbnail_data TEXT DEFAULT ''",
    ])
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_flyers_venue_id ON flyers(venue_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_flyers_gig_id ON flyers(gig_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_flyers_is_template ON flyers(is_template)")

    # ==========================================
    # PUBLIC_ACTIVITY (Analytics)
    # ==========================================
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS public_activity (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT NOT NULL,
            event_data TEXT,
            city TEXT,
            state TEXT,
            venue_id INTEGER,
            artist_id INTEGER,
            gig_id INTEGER,
            ip_hash TEXT,
            user_agent TEXT,
            session_id TEXT,
            referrer TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (venue_id) REFERENCES venues(id),
            FOREIGN KEY (artist_id) REFERENCES artists(id),
            FOREIGN KEY (gig_id) REFERENCES gigs(id)
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_public_activity_event_type ON public_activity(event_type)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_public_activity_city ON public_activity(city)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_public_activity_created_at ON public_activity(created_at)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_public_activity_session ON public_activity(session_id)")
    
    # ==========================================
    # VENUE PAYMENT OVERRIDES (Admin Free Trial)
    # ==========================================
    cursor.execute("""
        -- Jun 19 2026: queue of pending open-gig notifications collapsed
        -- into a single daily email per artist. Detection (the hourly
        -- scheduler — process_open_gig_notifications) enqueues here
        -- instead of sending. The morning digest job (send_daily_artist_digest)
        -- groups by user, renders one email per venue, sends, and marks
        -- sent_at. Unique constraint on (user_id, gig_id, notification_key)
        -- means re-detection across hours/days is a no-op.
        CREATE TABLE IF NOT EXISTS artist_email_digest_queue (
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
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_digest_queue_unsent ON artist_email_digest_queue(sent_at) WHERE sent_at IS NULL")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_digest_queue_user ON artist_email_digest_queue(user_id, sent_at)")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS venue_payment_overrides (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            venue_id INTEGER NOT NULL UNIQUE,
            payments_suspended BOOLEAN DEFAULT 1,
            suspended_by INTEGER,
            suspended_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            notes TEXT,
            FOREIGN KEY (venue_id) REFERENCES venues(id),
            FOREIGN KEY (suspended_by) REFERENCES users(id)
        )
    """)
    # Additive columns for the off-platform-pay flagging system:
    # - fee_pct_override: if NOT NULL, overrides platform_fee_percent
    #   for THIS venue's bookings (admin tool to recoup lost revenue
    #   from venues that route real pay off-platform via $0 listings).
    # - zero_pay_booking_count: running counter of bookings made with
    #   amount_cents=0 (flat-$0 or pure-door no-guarantee). Auto-bumped
    #   by _flag_zero_pay_booking() on every such booking event.
    # - last_zero_pay_at: timestamp of the most recent $0 booking so
    #   admin can sort flagged venues by recency.
    _add_columns(cursor, "venue_payment_overrides", [
        "fee_pct_override REAL",
        "zero_pay_booking_count INTEGER DEFAULT 0",
        "last_zero_pay_at DATETIME",
    ])

    conn.commit()
    conn.close()
    
    # Create additional tables with separate connection (avoids lock issues)
    conn2 = _setup_conn()
    c2 = conn2.cursor()
    
    # SUPPORT TICKETS
    c2.execute("""
        CREATE TABLE IF NOT EXISTS support_tickets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            user_email TEXT,
            user_name TEXT,
            category TEXT NOT NULL,
            subject TEXT NOT NULL,
            description TEXT NOT NULL,
            status TEXT DEFAULT 'open',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)
    # Seed autoincrement so ticket IDs start at 100000
    c2.execute("""
        INSERT OR IGNORE INTO sqlite_sequence (name, seq)
        VALUES ('support_tickets', 99999)
    """)
    
    # COMING SOON EMAIL CAPTURE
    c2.execute("""
        CREATE TABLE IF NOT EXISTS coming_soon_emails (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL UNIQUE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # RECOMMENDATIONS (Recommend GigsFill to others)
    c2.execute("""
        CREATE TABLE IF NOT EXISTS recommendations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            user_name TEXT,
            recipient_email TEXT NOT NULL,
            recipient_name TEXT,
            message TEXT,
            sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)
    
    # ARTIST INVITATIONS (Venues invite artists to join GigsFill)
    c2.execute("""
        CREATE TABLE IF NOT EXISTS artist_invitations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            venue_id INTEGER NOT NULL,
            venue_name TEXT NOT NULL,
            invited_email TEXT NOT NULL,
            invited_by_user_id INTEGER NOT NULL,
            inviter_name TEXT,
            message TEXT,
            -- Status state machine:
            --   pending          → email sent, no action yet
            --   bounced          → SMTP returned 5xx (write bounce_reason)
            --   signed_up        → invitee created a GigsFill account via the token link
            --   preferred_requested → invitee requested preferred status after signup
            --   preferred_approved  → venue approved their preferred request
            --   preferred_denied    → venue denied
            --   declined         → invitee clicked the decline link in the email
            --   expired          → token_expires_at passed
            status TEXT DEFAULT 'pending',
            sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            signed_up_at TIMESTAMP,
            signed_up_user_id INTEGER,
            resent_count INTEGER DEFAULT 0,
            last_resent_at TIMESTAMP,
            -- Part 10p: token-based multi-venue invite tracking. One click in the
            -- modal writes N×M rows (N emails × M venues); all rows for one
            -- (email, click) share the same token AND invite_group_id. The token
            -- consumes once (any signup or accept-preferred via that token
            -- updates all sibling rows together). invite_group_id is also kept
            -- on rows where the email was already a GigsFill user — even though
            -- there's no signup link to click, the popup flow still uses it to
            -- group "which venues did Janelle invite Jenny for".
            token TEXT,
            token_expires_at TIMESTAMP,
            invite_group_id TEXT,
            bounce_reason TEXT,
            declined_at TIMESTAMP,
            preferred_requested_at TIMESTAMP,
            FOREIGN KEY (venue_id) REFERENCES venues(id),
            FOREIGN KEY (invited_by_user_id) REFERENCES users(id)
        )
    """)
    # Part 10p audit fix: indexes for the token-based lookups + per-email queries.
    # The live DB was migrated via ALTER+CREATE INDEX; fresh installs need these
    # at table-creation time. Without them, by-token lookups and the 24h dedup
    # check degrade to full table scans as the table grows.
    c2.execute("CREATE INDEX IF NOT EXISTS idx_artist_invitations_token ON artist_invitations(token)")
    c2.execute("CREATE INDEX IF NOT EXISTS idx_artist_invitations_invite_group ON artist_invitations(invite_group_id)")
    c2.execute("CREATE INDEX IF NOT EXISTS idx_artist_invitations_email ON artist_invitations(LOWER(invited_email))")

    conn2.commit()
    conn2.close()
    
    # Third connection for W9 and additional tables
    conn_w9 = _setup_conn()
    c_w9 = conn_w9.cursor()
    
    # ==========================================
    # W9 TAX FORMS
    # ==========================================
    c_w9.execute("""
        CREATE TABLE IF NOT EXISTS w9_forms (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_type TEXT NOT NULL,
            entity_id INTEGER NOT NULL,
            tax_name TEXT NOT NULL,
            business_name TEXT,
            tax_classification TEXT NOT NULL,
            other_classification TEXT,
            exempt_payee_code TEXT,
            fatca_exemption_code TEXT,
            address_line_1 TEXT,
            address_line_2 TEXT,
            city TEXT,
            state TEXT,
            zip_code TEXT,
            tin_type TEXT NOT NULL,
            tin_encrypted TEXT NOT NULL,
            tin_last4 TEXT NOT NULL,
            certified_at TIMESTAMP,
            tax_year INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(entity_type, entity_id, tax_year)
        )
    """)
    
    # ==========================================
    # VENUE TAX SETTINGS
    # ==========================================
    c_w9.execute("""
        CREATE TABLE IF NOT EXISTS venue_tax_settings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            venue_id INTEGER NOT NULL UNIQUE,
            require_w9 INTEGER DEFAULT 0,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (venue_id) REFERENCES venues(id)
        )
    """)
    
    # ==========================================
    # 1099 RECORDS
    # ==========================================
    c_w9.execute("""
        CREATE TABLE IF NOT EXISTS tax_1099s (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            venue_id INTEGER NOT NULL,
            artist_id INTEGER NOT NULL,
            tax_year INTEGER NOT NULL,
            total_earnings_cents INTEGER NOT NULL DEFAULT 0,
            gig_count INTEGER DEFAULT 0,
            artist_name TEXT,
            artist_tin_last4 TEXT,
            artist_address TEXT,
            venue_name TEXT,
            venue_address TEXT,
            venue_tin_last4 TEXT,
            status TEXT DEFAULT 'generated',
            sent_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(venue_id, artist_id, tax_year),
            FOREIGN KEY (venue_id) REFERENCES venues(id),
            FOREIGN KEY (artist_id) REFERENCES artists(id)
        )
    """)
    
    # ==========================================
    # PRO LICENSES (performing rights org licenses per venue)
    # ==========================================
    c_w9.execute("""
        CREATE TABLE IF NOT EXISTS pro_licenses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            venue_id INTEGER NOT NULL,
            pro_name TEXT NOT NULL,
            license_number TEXT,
            expiration_date TEXT,
            license_file_path TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(venue_id, pro_name),
            FOREIGN KEY (venue_id) REFERENCES venues(id)
        )
    """)
    
    conn_w9.commit()
    conn_w9.close()
    
    # Add performance indexes
    conn3 = _setup_conn()
    c3 = conn3.cursor()
    
    # Core lookup indexes
    c3.execute("CREATE INDEX IF NOT EXISTS idx_artists_user_id ON artists(user_id)")
    c3.execute("CREATE INDEX IF NOT EXISTS idx_venues_user_id ON venues(user_id)")
    c3.execute("CREATE INDEX IF NOT EXISTS idx_gigs_venue_id ON gigs(venue_id)")
    c3.execute("CREATE INDEX IF NOT EXISTS idx_gigs_artist_id ON gigs(artist_id)")
    c3.execute("CREATE INDEX IF NOT EXISTS idx_gigs_status ON gigs(status)")
    c3.execute("CREATE INDEX IF NOT EXISTS idx_gigs_date ON gigs(date)")
    c3.execute("CREATE INDEX IF NOT EXISTS idx_gigs_venue_date ON gigs(venue_id, date)")
    c3.execute("CREATE INDEX IF NOT EXISTS idx_gigs_artist_status ON gigs(artist_id, status)")
    c3.execute("CREATE INDEX IF NOT EXISTS idx_gigs_venue_status ON gigs(venue_id, status)")
    c3.execute("CREATE INDEX IF NOT EXISTS idx_preferred_artists_venue ON preferred_artists(venue_id)")
    c3.execute("CREATE INDEX IF NOT EXISTS idx_preferred_artists_artist ON preferred_artists(artist_id)")
    c3.execute("CREATE INDEX IF NOT EXISTS idx_preferred_artists_status ON preferred_artists(status)")
    c3.execute("CREATE INDEX IF NOT EXISTS idx_preferred_artists_venue_artist ON preferred_artists(venue_id, artist_id)")
    c3.execute("CREATE INDEX IF NOT EXISTS idx_notifications_user_id ON notifications(user_id)")
    c3.execute("CREATE INDEX IF NOT EXISTS idx_notifications_user_read ON notifications(user_id, is_read)")
    c3.execute("CREATE INDEX IF NOT EXISTS idx_entity_users_user ON entity_users(user_id)")
    c3.execute("CREATE INDEX IF NOT EXISTS idx_entity_users_entity ON entity_users(entity_type, entity_id)")
    c3.execute("CREATE INDEX IF NOT EXISTS idx_entity_invitations_email ON entity_invitations(invited_email)")
    c3.execute("CREATE INDEX IF NOT EXISTS idx_entity_invitations_entity ON entity_invitations(entity_type, entity_id)")
    c3.execute("CREATE INDEX IF NOT EXISTS idx_users_email ON users(email)")
    c3.execute("CREATE INDEX IF NOT EXISTS idx_artist_media_artist ON artist_media(artist_id)")
    c3.execute("CREATE INDEX IF NOT EXISTS idx_venue_media_venue ON venue_media(venue_id)")
    c3.execute("CREATE INDEX IF NOT EXISTS idx_gig_email_log_gig ON gig_email_log(gig_id)")
    # Add sent_for_date to gig_email_log if not present — allows re-sending when venue changes timing
    _add_columns(c3, "gig_email_log", ["sent_for_date TEXT"])

    # DATA FIX: The open_gig scheduler incorrectly stamped radius_blast_token on gigs
    # for open_gig_4w/2w/1w/36h notifications. That makes is_blast_open=1 on the artist
    # calendar turning all gigs amber. Clear the token from any open gig that was NOT
    # explicitly blasted via the radius_blast process (i.e. last_notification_key is NOT
    # 'radius_blast' or 'cancelled_blast').
    # Audit fix (May 2026 part 10g): ALSO clear frequency_exempt in the same
    # sweep. frequency_exempt=1 is only ever set together with radius_blast_token
    # (see gigs.py:5496/5954/6045). The earlier version of this cleanup nulled
    # the token but left frequency_exempt=1 — an impossible state that made the
    # gig modal treat the gig as bookable by non-preferred artists (the modal's
    # not_preferred check was gated on `not gig_freq_exempt`). Keep the two
    # columns consistent: no token → no exemption.
    try:
        c3.execute("""
            UPDATE gigs
            SET radius_blast_token = NULL,
                frequency_exempt = 0
            WHERE status = 'open'
              AND (radius_blast_token IS NOT NULL OR COALESCE(frequency_exempt, 0) = 1)
              AND id NOT IN (
                  SELECT gig_id FROM gig_email_log
                  WHERE notification_key IN ('radius_blast', 'cancelled_blast')
              )
        """)
        cleared = c3.rowcount
        if cleared:
            import logging as _log
            _log.getLogger("gigsfill.db").info(f"DB fix: cleared radius_blast_token + frequency_exempt from {cleared} non-blast open gigs")
    except Exception as _e:
        pass
    c3.execute("CREATE INDEX IF NOT EXISTS idx_support_tickets_user ON support_tickets(user_id)")
    c3.execute("CREATE INDEX IF NOT EXISTS idx_w9_forms_entity ON w9_forms(entity_type, entity_id)")
    c3.execute("CREATE INDEX IF NOT EXISTS idx_tax_1099s_venue_year ON tax_1099s(venue_id, tax_year)")
    c3.execute("CREATE INDEX IF NOT EXISTS idx_tax_1099s_artist_year ON tax_1099s(artist_id, tax_year)")

    # Affiliate 1099s — separate table because the issuer is the platform
    # (not a venue), payee is a USER (not an artist), and amounts come from
    # affiliate_payouts.status='paid' rather than transactions. Mirrors the
    # tax_1099s shape so the same admin "Send 1099" / status-tracking flow
    # can be reused.
    # Added: May 2026 part 9c — admin-side affiliate 1099 generator
    # previously built an in-memory list and returned JSON without persisting
    # anything; no proof of issuance for IRS audit, no idempotency, no PDF.
    c3.execute("""
        CREATE TABLE IF NOT EXISTS affiliate_tax_1099s (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            affiliate_user_id INTEGER NOT NULL,
            tax_year INTEGER NOT NULL,
            total_earnings_cents INTEGER NOT NULL DEFAULT 0,
            payout_count INTEGER DEFAULT 0,
            recipient_name TEXT,
            recipient_tin_last4 TEXT,
            recipient_address TEXT,
            payer_name TEXT,
            payer_tin_last4 TEXT,
            payer_address TEXT,
            status TEXT DEFAULT 'generated',
            sent_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(affiliate_user_id, tax_year),
            FOREIGN KEY (affiliate_user_id) REFERENCES users(id)
        )
    """)
    c3.execute("CREATE INDEX IF NOT EXISTS idx_aff_1099s_year ON affiliate_tax_1099s(tax_year)")
    c3.execute("CREATE INDEX IF NOT EXISTS idx_aff_1099s_user ON affiliate_tax_1099s(affiliate_user_id, tax_year)")
    c3.execute("CREATE INDEX IF NOT EXISTS idx_pro_licenses_venue ON pro_licenses(venue_id)")
    
    conn3.commit()
    conn3.close()
    
    # ─── MIGRATION: ensure flyers.venue_id is nullable (for admin site-wide templates) ───
    try:
        import sqlite3 as _sqlite3
        _mc = _sqlite3.connect(str(db_path))
        _mc.row_factory = _sqlite3.Row
        _cur = _mc.cursor()
        # Check if venue_id has a NOT NULL constraint on flyers table
        _tbl_info = _cur.execute("PRAGMA table_info(flyers)").fetchall()
        _venue_col = next((r for r in _tbl_info if r['name'] == 'venue_id'), None)
        if _venue_col and _venue_col['notnull'] == 1:
            # Recreate table without NOT NULL on venue_id
            _cur.executescript("""
                PRAGMA foreign_keys=OFF;
                BEGIN;
                CREATE TABLE IF NOT EXISTS flyers_new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    venue_id INTEGER,
                    gig_id INTEGER,
                    artist_id INTEGER,
                    name TEXT,
                    canvas_data TEXT DEFAULT '{}',
                    thumbnail_data TEXT DEFAULT '',
                    is_template INTEGER DEFAULT 0,
                    size_preset TEXT DEFAULT 'instagram_post',
                    width INTEGER DEFAULT 1080,
                    height INTEGER DEFAULT 1350,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                );
                INSERT INTO flyers_new SELECT id, venue_id, gig_id, artist_id, name, canvas_data,
                    COALESCE(thumbnail_data,''), is_template,
                    COALESCE(size_preset,'instagram_post'), COALESCE(width,1080), COALESCE(height,1350),
                    created_at, updated_at FROM flyers;
                DROP TABLE flyers;
                ALTER TABLE flyers_new RENAME TO flyers;
                COMMIT;
                PRAGMA foreign_keys=ON;
            """)
            _mc.commit()
        _mc.close()
    except Exception as _e:
        import logging as _log
        _log.getLogger("gigsfill.db").warning(f"flyers migration: {_e}")
    
    # ==========================================
    # NEW TABLES: reviews, messages, availability
    # ==========================================
    conn4 = _setup_conn()
    c4 = conn4.cursor()

    c4.execute("""
        CREATE TABLE IF NOT EXISTS artist_reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            gig_id INTEGER NOT NULL,
            venue_id INTEGER NOT NULL,
            artist_id INTEGER NOT NULL,
            reviewer_user_id INTEGER NOT NULL,
            rating INTEGER NOT NULL CHECK(rating BETWEEN 1 AND 5),
            review_text TEXT DEFAULT '',
            is_visible INTEGER DEFAULT 1,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(gig_id, venue_id, artist_id),
            FOREIGN KEY (gig_id) REFERENCES gigs(id),
            FOREIGN KEY (venue_id) REFERENCES venues(id),
            FOREIGN KEY (artist_id) REFERENCES artists(id)
        )
    """)
    c4.execute("CREATE INDEX IF NOT EXISTS idx_reviews_artist ON artist_reviews(artist_id)")
    c4.execute("CREATE INDEX IF NOT EXISTS idx_reviews_venue ON artist_reviews(venue_id)")
    c4.execute("CREATE INDEX IF NOT EXISTS idx_reviews_gig ON artist_reviews(gig_id)")

    c4.execute("""
        CREATE TABLE IF NOT EXISTS review_link_tokens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            token TEXT UNIQUE NOT NULL,
            direction TEXT NOT NULL,
            gig_id INTEGER NOT NULL,
            venue_id INTEGER,
            artist_id INTEGER,
            user_id INTEGER,
            expires_at DATETIME NOT NULL,
            used_at DATETIME,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    c4.execute("""
        CREATE TABLE IF NOT EXISTS venue_reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            gig_id INTEGER,
            venue_id INTEGER NOT NULL,
            artist_id INTEGER NOT NULL,
            reviewer_user_id INTEGER NOT NULL,
            rating INTEGER NOT NULL CHECK(rating BETWEEN 1 AND 5),
            review_text TEXT DEFAULT '',
            is_visible INTEGER DEFAULT 1,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(gig_id, venue_id, artist_id)
        )
    """)

    # ==========================================
    # GIG TEMPLATES — Reusable slot config per venue.
    # ==========================================
    # Venues often program the same shape every week (Friday: 7-9pm Live
    # Band, 9pm-12am DJ). A template stores the slot definitions as JSON
    # so the venue can apply it to any new gig with one click. Field is
    # opaque to the backend — the FE serializes the same shape it sends
    # into POST /api/venues/{id}/gigs (start_time/end_time/pay/
    # artist_type/band_formats/styles per slot).
    c4.execute("""
        CREATE TABLE IF NOT EXISTS gig_templates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            venue_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            slots_json TEXT NOT NULL,
            notes TEXT DEFAULT '',
            created_by_user_id INTEGER,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (venue_id) REFERENCES venues(id)
        )
    """)
    c4.execute("CREATE INDEX IF NOT EXISTS idx_gig_templates_venue ON gig_templates(venue_id)")

    c4.execute("""
        CREATE TABLE IF NOT EXISTS gig_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            gig_id INTEGER NOT NULL,
            sender_user_id INTEGER NOT NULL,
            sender_type TEXT NOT NULL CHECK(sender_type IN ('venue', 'artist', 'admin')),
            sender_name TEXT NOT NULL DEFAULT '',
            body TEXT NOT NULL,
            is_read INTEGER DEFAULT 0,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (gig_id) REFERENCES gigs(id),
            FOREIGN KEY (sender_user_id) REFERENCES users(id)
        )
    """)
    c4.execute("CREATE INDEX IF NOT EXISTS idx_messages_gig ON gig_messages(gig_id)")
    c4.execute("CREATE INDEX IF NOT EXISTS idx_messages_sender ON gig_messages(sender_user_id)")
    c4.execute("CREATE INDEX IF NOT EXISTS idx_messages_unread ON gig_messages(is_read, gig_id)")

    c4.execute("""
        CREATE TABLE IF NOT EXISTS artist_availability (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            artist_id INTEGER NOT NULL,
            blackout_start DATE NOT NULL,
            blackout_end DATE NOT NULL,
            reason TEXT DEFAULT '',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (artist_id) REFERENCES artists(id)
        )
    """)
    c4.execute("CREATE INDEX IF NOT EXISTS idx_availability_artist ON artist_availability(artist_id)")
    c4.execute("CREATE INDEX IF NOT EXISTS idx_availability_dates ON artist_availability(blackout_start, blackout_end)")

    # Per-user member-level availability (May 21 2026). Sibling to
    # artist_availability — that's band-wide ("the band can't perform"),
    # this is per-member ("Jim Smith can't perform on these dates").
    #   - artist_id NULL → applies to every artist this user is a member of
    #   - artist_id set  → applies only to that specific artist
    # Booking-time: band-level rows hard-block; user-level rows surface
    # as a soft warning that the booking artist can confirm through.
    c4.execute("""
        CREATE TABLE IF NOT EXISTS user_availability (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            artist_id INTEGER,
            blackout_start DATE NOT NULL,
            blackout_end DATE NOT NULL,
            reason TEXT DEFAULT '',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id),
            FOREIGN KEY (artist_id) REFERENCES artists(id)
        )
    """)
    c4.execute("CREATE INDEX IF NOT EXISTS idx_user_availability_user ON user_availability(user_id)")
    c4.execute("CREATE INDEX IF NOT EXISTS idx_user_availability_artist ON user_availability(artist_id)")
    c4.execute("CREATE INDEX IF NOT EXISTS idx_user_availability_dates ON user_availability(blackout_start, blackout_end)")

    try:
        c4.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_transactions_gig_artist_unique
            ON transactions(gig_id, artist_id)
            WHERE status NOT IN ('cancelled', 'payment_cancelled')
        """)
    except Exception:
        pass

    # Perf indexes (Jun 2026 — audit fixes):
    # - transactions composite covers door_settle.py WHERE (gig_id, artist_id, status)
    # - users(calendar_token) covers /api/calendar/{token}.ics lookup (Google polls hourly)
    # - users(vanity_slug) + artists(vanity_slug) + venues(vanity_slug) cover the
    #   slug resolver hit on every gigsfill.com/<slug> redirect
    # - gig_waitlist(gig_id, offer_sent, offer_expires_at) covers sequential offer cycle
    # - notifications(user_id, created_at DESC) covers /api/notifications sort
    try:
        c4.execute("CREATE INDEX IF NOT EXISTS idx_transactions_gig_artist_status ON transactions(gig_id, artist_id, status)")
    except Exception:
        pass
    # Hot path: payout_scheduler hourly sweep does
    #   SELECT * FROM transactions WHERE status='scheduled' AND scheduled_process_at <= ?
    # At 10K artists × N transactions/month this grows fast. The
    # composite (status, scheduled_process_at) makes the sweep an
    # index-range scan instead of a table scan.
    try:
        c4.execute("CREATE INDEX IF NOT EXISTS idx_transactions_status_scheduled ON transactions(status, scheduled_process_at)")
    except Exception:
        pass
    try:
        c4.execute("CREATE INDEX IF NOT EXISTS idx_users_calendar_token ON users(calendar_token)")
    except Exception:
        pass
    try:
        c4.execute("CREATE INDEX IF NOT EXISTS idx_artists_vanity_slug ON artists(vanity_slug)")
    except Exception:
        pass
    try:
        c4.execute("CREATE INDEX IF NOT EXISTS idx_venues_vanity_slug ON venues(vanity_slug)")
    except Exception:
        pass
    try:
        c4.execute("CREATE INDEX IF NOT EXISTS idx_waitlist_offer_cycle ON gig_waitlist(gig_id, offer_sent, offer_expires_at)")
    except Exception:
        pass
    try:
        c4.execute("CREATE INDEX IF NOT EXISTS idx_notifications_user_created ON notifications(user_id, created_at)")
    except Exception:
        pass

    extra_settings = [
        ("stripe_processing_fee_percent", "2.9", "Stripe processing fee percentage"),
        ("stripe_per_transaction_fee", "0.30", "Stripe per-transaction fee in dollars"),
    ]
    for setting_key, setting_value, desc in extra_settings:
        try:
            c4.execute(
                "INSERT INTO platform_settings (setting_key, setting_value, description) VALUES (?, ?, ?)",
                (setting_key, setting_value, desc)
            )
        except sqlite3.IntegrityError:
            pass

    # ==========================================
    # GIG WAITLIST
    # ==========================================
    c4.execute("""
        CREATE TABLE IF NOT EXISTS gig_waitlist (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            gig_id INTEGER NOT NULL,
            artist_id INTEGER NOT NULL,
            notified INTEGER DEFAULT 0,
            notified_at DATETIME,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(gig_id, artist_id),
            FOREIGN KEY (gig_id) REFERENCES gigs(id) ON DELETE CASCADE,
            FOREIGN KEY (artist_id) REFERENCES artists(id) ON DELETE CASCADE
        )
    """)
    c4.execute("CREATE INDEX IF NOT EXISTS idx_waitlist_gig ON gig_waitlist(gig_id)")
    c4.execute("CREATE INDEX IF NOT EXISTS idx_waitlist_artist ON gig_waitlist(artist_id)")
    _add_columns(c4, "gig_waitlist", [
        "offer_sent INTEGER DEFAULT 0",
        "offer_sent_at DATETIME",
        "offer_expires_at DATETIME",
        "offer_token TEXT",
        "offer_declined INTEGER DEFAULT 0",
    ])

    # gig_cancelled_artists (May 2026 part 9):
    # Track every artist who has cancelled a slot on a gig, so the blast
    # filter can exclude ALL of them — not just the most recent. The legacy
    # `gigs.last_cancelled_artist_id` single-int column overwrites itself on
    # successive cancellations and silently re-includes earlier cancellers
    # in blasts (multi-slot gigs hit this constantly).
    c4.execute("""
        CREATE TABLE IF NOT EXISTS gig_cancelled_artists (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            gig_id INTEGER NOT NULL,
            artist_id INTEGER NOT NULL,
            cancelled_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(gig_id, artist_id),
            FOREIGN KEY (gig_id) REFERENCES gigs(id) ON DELETE CASCADE,
            FOREIGN KEY (artist_id) REFERENCES artists(id) ON DELETE CASCADE
        )
    """)
    c4.execute("CREATE INDEX IF NOT EXISTS idx_cancelled_artists_gig ON gig_cancelled_artists(gig_id)")

    # waitlist_offered: persists token after artist is removed from gig_waitlist
    # so respond_to_offer still works after the row is deleted
    c4.execute("""
        CREATE TABLE IF NOT EXISTS waitlist_offered (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            gig_id INTEGER NOT NULL,
            artist_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            offer_token TEXT UNIQUE NOT NULL,
            offer_expires_at DATETIME NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    c4.execute("CREATE INDEX IF NOT EXISTS idx_wlo_token ON waitlist_offered(offer_token)")
    c4.execute("CREATE INDEX IF NOT EXISTS idx_wlo_gig ON waitlist_offered(gig_id)")

    # venue_artist_bans: permanent ban — artist can never book at this venue
    c4.execute("""
        CREATE TABLE IF NOT EXISTS venue_artist_bans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            venue_id INTEGER NOT NULL,
            artist_id INTEGER NOT NULL,
            banned_by INTEGER NOT NULL,
            reason TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(venue_id, artist_id),
            FOREIGN KEY (venue_id) REFERENCES venues(id) ON DELETE CASCADE,
            FOREIGN KEY (artist_id) REFERENCES artists(id) ON DELETE CASCADE
        )
    """)
    c4.execute("CREATE INDEX IF NOT EXISTS idx_bans_venue ON venue_artist_bans(venue_id)")
    c4.execute("CREATE INDEX IF NOT EXISTS idx_bans_artist ON venue_artist_bans(artist_id)")

    conn4.commit()
    conn4.close()

    # Populate email templates from email_templates.py
    _populate_email_templates(db_path)

    # ==========================================
    # AFFILIATE PROGRAM
    # ==========================================
    conn_aff = _setup_conn()
    c_aff = conn_aff.cursor()

    # affiliate_code column on users
    _add_columns(c_aff, "users", [
        "affiliate_code TEXT",
        "last_login TIMESTAMP",
        # password_changed_at: when set, every session token issued before this
        # timestamp is treated as invalid (forces re-login on every device after
        # password change / reset). See verify_session_token in auth.py.
        "password_changed_at TIMESTAMP",
    ])

    # Backfill affiliate codes for existing users who don't have one
    existing = c_aff.execute("SELECT id FROM users WHERE affiliate_code IS NULL").fetchall()
    import secrets as _secrets
    for (uid,) in existing:
        for _ in range(20):
            code = "AFF-" + _secrets.token_hex(4).upper()
            try:
                c_aff.execute("UPDATE users SET affiliate_code = ? WHERE id = ?", (code, uid))
                break
            except Exception:
                continue
    conn_aff.commit()

    # Unique index on affiliate_code
    c_aff.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_affiliate_code ON users(affiliate_code)")

    # Emails sent via Recommend GigsFill
    c_aff.execute("""
        CREATE TABLE IF NOT EXISTS affiliate_recommend_emails (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sender_user_id INTEGER NOT NULL,
            recipient_email TEXT NOT NULL COLLATE NOCASE,
            recipient_name TEXT,
            sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            affiliate_code TEXT NOT NULL,
            clicked INTEGER DEFAULT 0,
            clicked_at TIMESTAMP,
            FOREIGN KEY (sender_user_id) REFERENCES users(id)
        )
    """)
    c_aff.execute("CREATE INDEX IF NOT EXISTS idx_aff_email_recipient ON affiliate_recommend_emails(recipient_email)")
    c_aff.execute("CREATE INDEX IF NOT EXISTS idx_aff_email_sender ON affiliate_recommend_emails(sender_user_id)")

    # Links affiliate user → referred venue
    c_aff.execute("""
        CREATE TABLE IF NOT EXISTS affiliate_referrals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            affiliate_user_id INTEGER NOT NULL,
            venue_id INTEGER NOT NULL UNIQUE,
            linked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            link_method TEXT DEFAULT 'email',
            initial_rate_percent REAL NOT NULL DEFAULT 1.0,
            reduced_rate_percent REAL NOT NULL DEFAULT 0.5,
            reduced_after_days INTEGER NOT NULL DEFAULT 365,
            manually_linked_by INTEGER,
            FOREIGN KEY (affiliate_user_id) REFERENCES users(id),
            FOREIGN KEY (venue_id) REFERENCES venues(id)
        )
    """)
    c_aff.execute("CREATE INDEX IF NOT EXISTS idx_aff_referrals_user ON affiliate_referrals(affiliate_user_id)")

    # Per-transaction accrued earnings
    c_aff.execute("""
        CREATE TABLE IF NOT EXISTS affiliate_earnings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            affiliate_user_id INTEGER NOT NULL,
            venue_id INTEGER NOT NULL,
            transaction_id INTEGER NOT NULL UNIQUE,
            gig_fee_cents INTEGER NOT NULL,
            rate_percent REAL NOT NULL,
            earned_cents INTEGER NOT NULL,
            quarter TEXT NOT NULL,
            accrued_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            payout_id INTEGER,
            FOREIGN KEY (affiliate_user_id) REFERENCES users(id),
            FOREIGN KEY (venue_id) REFERENCES venues(id),
            FOREIGN KEY (transaction_id) REFERENCES transactions(id)
        )
    """)
    c_aff.execute("CREATE INDEX IF NOT EXISTS idx_aff_earnings_user ON affiliate_earnings(affiliate_user_id)")
    c_aff.execute("CREATE INDEX IF NOT EXISTS idx_aff_earnings_quarter ON affiliate_earnings(quarter)")
    c_aff.execute("CREATE INDEX IF NOT EXISTS idx_aff_earnings_txn ON affiliate_earnings(transaction_id)")

    # Quarterly payout records
    c_aff.execute("""
        CREATE TABLE IF NOT EXISTS affiliate_payouts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            affiliate_user_id INTEGER NOT NULL,
            quarter TEXT NOT NULL,
            total_cents INTEGER NOT NULL,
            status TEXT DEFAULT 'pending',
            stripe_transfer_id TEXT,
            paid_at TIMESTAMP,
            notes TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(affiliate_user_id, quarter),
            FOREIGN KEY (affiliate_user_id) REFERENCES users(id)
        )
    """)
    c_aff.execute("CREATE INDEX IF NOT EXISTS idx_aff_payouts_user ON affiliate_payouts(affiliate_user_id)")

    # Stripe Connect for affiliate payouts (reuse entity_payment_settings pattern)
    # Ensure recipient_name column exists in existing DBs
    _add_columns(c_aff, "affiliate_recommend_emails", [
        "recipient_name TEXT",
        # Audit fix (May 2026 part 9c): cap resends per recipient row
        # so an affiliate can't spam the same person every 6 seconds.
        "resend_count INTEGER DEFAULT 0",
        "last_resent_at DATETIME",
    ])
    _add_columns(c_aff, "entity_payment_settings", [
        "affiliate_stripe_connect_account_id TEXT",
        "affiliate_stripe_connect_onboarding_complete INTEGER DEFAULT 0",
    ])

    # Affiliate settings in platform_settings
    aff_defaults = [
        ("affiliate_rate_percent",         "1.0",   "Affiliate payout rate (%)"),
        ("affiliate_reduced_rate_percent",  "0.5",   "Reduced affiliate rate after cutoff (%)"),
        ("affiliate_reduced_after_days",    "365",   "Days after venue signup before rate reduces"),
        ("affiliate_min_payout_cents",      "5000",  "Minimum quarterly payout threshold (cents)"),
        ("affiliate_1099_threshold_cents",  "60000", "Annual earnings threshold for 1099 (cents)"),
        ("affiliate_enabled",               "true",  "Enable affiliate program"),
        ("affiliate_daily_send_cap",        "50",    "Max recommend-emails per affiliate per 24h (0=unlimited)"),
    ]
    for key, val, desc in aff_defaults:
        try:
            c_aff.execute(
                "INSERT INTO platform_settings (setting_key, setting_value, description) VALUES (?, ?, ?)",
                (key, val, desc)
            )
        except Exception:
            pass

    conn_aff.commit()
    conn_aff.close()

    # ==========================================
    # AUDIT FIX (May 2026 part 6): Idempotency / per-artist approval tables
    # These were created lazily by their callers (gigs.py:_ensure_approval_columns,
    # stripe_connect.py:stripe_webhook); both used SQLite-only PRAGMA
    # introspection that silently failed on Postgres. Declaring here so the
    # tables exist at process start regardless of which codepath runs first.
    # ==========================================
    conn_idem = get_db_connection()
    c_idem = conn_idem.cursor()
    try:
        c_idem.execute("""
            CREATE TABLE IF NOT EXISTS pending_approval_tokens (
                token TEXT PRIMARY KEY,
                gig_id INTEGER NOT NULL,
                artist_id INTEGER NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        c_idem.execute("CREATE INDEX IF NOT EXISTS idx_pending_approval_gig_artist ON pending_approval_tokens(gig_id, artist_id)")
        c_idem.execute("""
            CREATE TABLE IF NOT EXISTS stripe_webhook_events (
                id TEXT PRIMARY KEY,
                event_type TEXT,
                received_at TEXT
            )
        """)
        conn_idem.commit()
    finally:
        conn_idem.close()

def _add_columns(cursor, table, columns):
    """Add columns if they don't exist (ignores duplicate column name).

    Audit fix (May 2026 part 7): catch a broader Exception so Postgres
    `psycopg2.errors.DuplicateColumn` ALSO gets swallowed. Previously only
    `sqlite3.OperationalError` was caught — on PG, every restart after the
    columns existed crashed `setup_database()` and the service refused to
    start. Also issue a rollback after the failed ALTER so the surrounding
    connection isn't left in an aborted-transaction state.
    """
    for col in columns:
        try:
            cursor.execute(f"ALTER TABLE {table} ADD COLUMN {col}")
        except Exception:
            # Column already exists OR ALTER not supported in this context.
            # Roll back the cursor's transaction (PG poisons subsequent
            # statements until rollback after a failed DDL).
            try:
                cursor.connection.rollback()
            except Exception:
                pass

def _populate_email_templates(db_path):
    """Populate email templates - upsert to add any missing templates"""
    conn = _setup_conn()
    cursor = conn.cursor()
    
    try:
        from backend.email_templates import TEMPLATES
        
        for template_key, template in TEMPLATES.items():
            try:
                cursor.execute("""
                    INSERT INTO email_templates (template_key, subject, body)
                    VALUES (?, ?, ?)
                    ON CONFLICT(template_key) DO UPDATE SET
                        subject = excluded.subject,
                        body = excluded.body,
                        updated_at = CURRENT_TIMESTAMP
                """, (template_key, template['subject'], template['body']))
            except Exception:
                pass
        
        conn.commit()
    except ImportError:
        pass
    except Exception:
        pass
    finally:
        conn.close()

# Audit fix (May 2026 part 10c): SQLAlchemy event listener that translates
# every text() query from SQLite syntax to PostgreSQL syntax when running
# on PG. Attached AFTER all helper definitions to avoid forward-ref issues.
# No-op on SQLite (the function is never registered).
if _IS_POSTGRES:
    @event.listens_for(engine, "before_cursor_execute")
    def _pg_translate_text_sql(conn_ev, cursor, statement, parameters, context, executemany):
        # SQLAlchemy already converts our `?` to `%s` for psycopg2 because we
        # built the engine from a postgresql:// URL; what we need to add are
        # the SQLite-specific function patterns (datetime(), strftime(),
        # julianday(), last_insert_rowid(), INSERT OR IGNORE).
        # Avoid double-translating `?` (SQLAlchemy already did it) by only
        # rewriting the function patterns.
        new_statement = _sqlite_to_pg_funcs_only(statement)
        return new_statement, parameters

    def _sqlite_to_pg_funcs_only(sql: str) -> str:
        """Same as _sqlite_to_pg but skips the `?` → `%s` step since
        SQLAlchemy handled that for ORM-issued queries already."""
        if not sql:
            return sql
        import re as _re
        s = sql
        s = _re.sub(
            r'\bINTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT\b',
            'SERIAL PRIMARY KEY', s, flags=_re.IGNORECASE
        )
        if _re.search(r'\bINSERT\s+OR\s+IGNORE\b', s, _re.IGNORECASE):
            s = _re.sub(r'\bINSERT\s+OR\s+IGNORE\b', 'INSERT', s, flags=_re.IGNORECASE)
            if not _re.search(r'\bON\s+CONFLICT\b', s, _re.IGNORECASE):
                s = s.rstrip(' ;\n\r\t') + " ON CONFLICT DO NOTHING"
        if _re.search(r'\bINSERT\s+OR\s+REPLACE\b', s, _re.IGNORECASE):
            s = _re.sub(r'\bINSERT\s+OR\s+REPLACE\b', 'INSERT', s, flags=_re.IGNORECASE)
        def _dt_interval(m):
            sign, n, unit = m.group(1), m.group(2), m.group(3).lower()
            unit_map = {'day': 'days', 'days': 'days', 'hour': 'hours', 'hours': 'hours',
                        'minute': 'minutes', 'minutes': 'minutes',
                        'second': 'seconds', 'seconds': 'seconds',
                        'month': 'months', 'months': 'months',
                        'year': 'years', 'years': 'years'}
            unit = unit_map.get(unit, unit)
            return f"(CURRENT_TIMESTAMP {sign} INTERVAL '{n} {unit}')"
        s = _re.sub(
            r"datetime\(\s*'now'\s*,\s*'\s*([+-])\s*(\d+)\s+(\w+)\s*'\s*\)",
            _dt_interval, s, flags=_re.IGNORECASE
        )
        s = _re.sub(
            r"date\(\s*'now'\s*,\s*'\s*([+-])\s*(\d+)\s+(\w+)\s*'\s*\)",
            lambda m: f"(CURRENT_DATE {m.group(1)} INTERVAL '{m.group(2)} {m.group(3)}')",
            s, flags=_re.IGNORECASE
        )
        s = _re.sub(r"datetime\(\s*'now'\s*\)", 'CURRENT_TIMESTAMP', s, flags=_re.IGNORECASE)
        s = _re.sub(r"date\(\s*'now'\s*\)", 'CURRENT_DATE', s, flags=_re.IGNORECASE)
        s = _re.sub(
            r"julianday\(([^)]+)\)\s*-\s*julianday\(([^)]+)\)",
            r"EXTRACT(EPOCH FROM ((\1)::timestamp - (\2)::timestamp))/86400.0",
            s, flags=_re.IGNORECASE
        )
        s = _re.sub(r'\blast_insert_rowid\(\s*\)', 'lastval()', s, flags=_re.IGNORECASE)
        s = _re.sub(
            r"strftime\(\s*'%Y'\s*,\s*([^)]+)\)",
            r"to_char((\1)::timestamp, 'YYYY')", s, flags=_re.IGNORECASE
        )
        s = _re.sub(
            r"strftime\(\s*'%Y-%m'\s*,\s*([^)]+)\)",
            r"to_char((\1)::timestamp, 'YYYY-MM')", s, flags=_re.IGNORECASE
        )
        s = _re.sub(
            r"strftime\(\s*'%Y-%m-%d'\s*,\s*([^)]+)\)",
            r"to_char((\1)::timestamp, 'YYYY-MM-DD')", s, flags=_re.IGNORECASE
        )
        return s


# Run setup if this module is executed directly
if __name__ == "__main__":
    setup_database()
