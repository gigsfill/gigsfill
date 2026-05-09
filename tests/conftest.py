"""
GigsFill Test Configuration
============================
In-memory SQLite database with core table schemas and test fixtures.
Used by all test modules via pytest fixtures.
"""
import pytest
import sys
import os
from pathlib import Path
from datetime import datetime, date, timedelta

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import create_engine, text, event
from sqlalchemy.orm import sessionmaker


# ─── In-memory DB engine ───────────────────────────────────────

@pytest.fixture(scope="function")
def db():
    """Create a fresh in-memory SQLite DB per test with all required tables."""
    engine = create_engine("sqlite:///:memory:", echo=False)
    
    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()
    
    Session = sessionmaker(bind=engine)
    session = Session()
    
    # Create core tables
    session.execute(text("""
        CREATE TABLE users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            first_name VARCHAR, last_name VARCHAR,
            email VARCHAR UNIQUE NOT NULL,
            password_hash VARCHAR NOT NULL,
            role VARCHAR DEFAULT 'user',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            account_locked BOOLEAN DEFAULT 0,
            failed_login_attempts INTEGER DEFAULT 0
        )
    """))
    session.execute(text("""
        CREATE TABLE artists (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name VARCHAR NOT NULL,
            city VARCHAR, state VARCHAR,
            artist_type VARCHAR DEFAULT 'solo',
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """))
    session.execute(text("""
        CREATE TABLE venues (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            venue_name VARCHAR NOT NULL,
            city VARCHAR, state VARCHAR,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """))
    session.execute(text("""
        CREATE TABLE gigs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            venue_id INTEGER NOT NULL,
            artist_id INTEGER,
            date DATE NOT NULL,
            start_time VARCHAR, end_time VARCHAR,
            pay REAL DEFAULT 0,
            status VARCHAR DEFAULT 'open',
            title VARCHAR,
            notes TEXT,
            is_multi_slot BOOLEAN DEFAULT 0,
            artist_type VARCHAR,
            band_formats VARCHAR,
            is_recurring BOOLEAN DEFAULT 0,
            recurring_group_id VARCHAR,
            FOREIGN KEY (venue_id) REFERENCES venues(id),
            FOREIGN KEY (artist_id) REFERENCES artists(id)
        )
    """))
    session.execute(text("""
        CREATE TABLE gig_slots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            gig_id INTEGER NOT NULL,
            slot_number INTEGER NOT NULL,
            start_time VARCHAR, end_time VARCHAR,
            pay REAL DEFAULT 0,
            status VARCHAR DEFAULT 'open',
            artist_id INTEGER,
            FOREIGN KEY (gig_id) REFERENCES gigs(id),
            FOREIGN KEY (artist_id) REFERENCES artists(id)
        )
    """))
    session.execute(text("""
        CREATE TABLE notifications (
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
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """))
    session.execute(text("""
        CREATE TABLE transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            gig_id INTEGER NOT NULL,
            from_user_id INTEGER NOT NULL,
            to_user_id INTEGER NOT NULL,
            artist_id INTEGER,
            amount_cents INTEGER NOT NULL,
            venue_charge_cents INTEGER NOT NULL,
            artist_payout_cents INTEGER NOT NULL,
            commission_cents INTEGER NOT NULL,
            status VARCHAR DEFAULT 'pending',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (gig_id) REFERENCES gigs(id)
        )
    """))
    session.execute(text("""
        CREATE TABLE gig_contracts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            gig_id INTEGER NOT NULL,
            venue_contract_id INTEGER NOT NULL,
            venue_id INTEGER NOT NULL,
            artist_id INTEGER NOT NULL,
            contract_type TEXT NOT NULL,
            status TEXT DEFAULT 'pending',
            rendered_body TEXT,
            FOREIGN KEY (gig_id) REFERENCES gigs(id)
        )
    """))
    session.execute(text("""
        CREATE TABLE payment_cancellations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            transaction_id INTEGER NOT NULL,
            gig_id INTEGER NOT NULL,
            cancelled_by_user_id INTEGER NOT NULL,
            cancellation_reason TEXT NOT NULL,
            cancelled_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (transaction_id) REFERENCES transactions(id)
        )
    """))
    session.execute(text("""
        CREATE TABLE entity_users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            entity_type VARCHAR NOT NULL,
            entity_id INTEGER NOT NULL,
            role VARCHAR DEFAULT 'member',
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """))
    session.commit()
    
    yield session
    
    session.close()
    engine.dispose()


# ─── Seed data fixtures ────────────────────────────────────────

@pytest.fixture
def seed_users(db):
    """Create 3 test users: artist_owner, venue_owner, dual_owner (owns both)."""
    db.execute(text("""
        INSERT INTO users (id, first_name, last_name, email, password_hash)
        VALUES
            (1, 'Alice', 'Artist', 'alice@test.com', 'hash1'),
            (2, 'Bob', 'Venue', 'bob@test.com', 'hash2'),
            (3, 'Charlie', 'Both', 'charlie@test.com', 'hash3')
    """))
    db.commit()
    return {"artist_user": 1, "venue_user": 2, "dual_user": 3}


@pytest.fixture
def seed_entities(db, seed_users):
    """Create artist and venue entities."""
    db.execute(text("""
        INSERT INTO artists (id, user_id, name, city, state)
        VALUES
            (10, 1, 'Alice Band', 'Nashville', 'TN'),
            (11, 3, 'Charlie Solo', 'Austin', 'TX')
    """))
    db.execute(text("""
        INSERT INTO venues (id, user_id, venue_name, city, state)
        VALUES
            (20, 2, 'Bobs Bar', 'Nashville', 'TN'),
            (21, 3, 'Charlies Club', 'Austin', 'TX')
    """))
    db.commit()
    return {
        "artist_alice": 10, "artist_charlie": 11,
        "venue_bob": 20, "venue_charlie": 21,
    }


@pytest.fixture
def seed_booked_gig(db, seed_entities):
    """Create a booked single-artist gig with transaction and contract."""
    gig_date = (date.today() + timedelta(days=7)).isoformat()
    db.execute(text("""
        INSERT INTO gigs (id, venue_id, artist_id, date, start_time, end_time, pay, status)
        VALUES (100, 20, 10, :d, '20:00', '23:00', 200.00, 'booked')
    """), {"d": gig_date})
    db.execute(text("""
        INSERT INTO transactions (id, gig_id, from_user_id, to_user_id, artist_id,
            amount_cents, venue_charge_cents, artist_payout_cents, commission_cents, status)
        VALUES (1000, 100, 2, 1, 10, 20000, 21000, 18000, 2000, 'pending')
    """))
    db.execute(text("""
        INSERT INTO gig_contracts (id, gig_id, venue_contract_id, venue_id, artist_id, contract_type, status)
        VALUES (500, 100, 1, 20, 10, 'standard', 'countersigned')
    """))
    db.execute(text("""
        INSERT INTO notifications (id, user_id, notification_type, title, message, gig_id, venue_id, artist_id)
        VALUES
            (2000, 1, 'gig_booked', 'Gig Booked', 'You booked a gig', 100, 20, 10),
            (2001, 2, 'gig_booked', 'Gig Booked', 'Artist booked your gig', 100, 20, 10),
            (2002, 1, 'contract_signed', 'Contract Signed', 'Contract signed', 100, 20, 10),
            (2003, 2, 'contract_countersigned', 'Countersigned', 'Countersigned', 100, 20, 10)
    """))
    db.execute(text("""
        INSERT INTO payment_cancellations (id, transaction_id, gig_id, cancelled_by_user_id, cancellation_reason)
        VALUES (3000, 1000, 100, 2, 'test cancellation')
    """))
    db.commit()
    return {"gig_id": 100, "venue_id": 20, "artist_id": 10}


@pytest.fixture
def seed_multi_slot_gig(db, seed_entities):
    """Create a multi-slot gig with 2 booked slots by different artists."""
    gig_date = (date.today() + timedelta(days=14)).isoformat()
    db.execute(text("""
        INSERT INTO gigs (id, venue_id, date, start_time, end_time, pay, status, is_multi_slot)
        VALUES (200, 20, :d, '18:00', '23:00', 0, 'booked', 1)
    """), {"d": gig_date})
    db.execute(text("""
        INSERT INTO gig_slots (id, gig_id, slot_number, start_time, end_time, pay, status, artist_id)
        VALUES
            (300, 200, 1, '18:00', '20:00', 150.00, 'booked', 10),
            (301, 200, 2, '20:30', '23:00', 200.00, 'booked', 11)
    """))
    db.execute(text("""
        INSERT INTO transactions (id, gig_id, from_user_id, to_user_id, artist_id,
            amount_cents, venue_charge_cents, artist_payout_cents, commission_cents, status)
        VALUES
            (1100, 200, 2, 1, 10, 15000, 15750, 13500, 1500, 'pending'),
            (1101, 200, 2, 3, 11, 20000, 21000, 18000, 2000, 'pending')
    """))
    db.execute(text("""
        INSERT INTO gig_contracts (id, gig_id, venue_contract_id, venue_id, artist_id, contract_type, status)
        VALUES
            (510, 200, 1, 20, 10, 'standard', 'countersigned'),
            (511, 200, 1, 20, 11, 'standard', 'countersigned')
    """))
    db.execute(text("""
        INSERT INTO notifications (id, user_id, notification_type, title, message, gig_id, venue_id, artist_id)
        VALUES
            (2100, 1, 'gig_booked', 'Slot Booked', 'Slot 1 booked', 200, 20, 10),
            (2101, 3, 'gig_booked', 'Slot Booked', 'Slot 2 booked', 200, 20, 11),
            (2102, 1, 'contract_signed', 'Signed', 'Contract signed', 200, 20, 10)
    """))
    db.commit()
    return {
        "gig_id": 200, "venue_id": 20,
        "slot1_id": 300, "slot2_id": 301,
        "artist_alice": 10, "artist_charlie": 11,
    }


@pytest.fixture
def seed_same_user_gig(db, seed_entities):
    """Create a booked gig where the same user owns both artist and venue."""
    gig_date = (date.today() + timedelta(days=10)).isoformat()
    db.execute(text("""
        INSERT INTO gigs (id, venue_id, artist_id, date, start_time, end_time, pay, status)
        VALUES (300, 21, 11, :d, '21:00', '23:30', 300.00, 'booked')
    """), {"d": gig_date})
    db.commit()
    return {"gig_id": 300, "venue_id": 21, "artist_id": 11, "user_id": 3}
