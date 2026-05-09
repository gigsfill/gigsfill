#!/usr/bin/env python3
"""
Reset gig-related data in the database. Keeps users, artists, venues, admin, preferred_artists, etc.
Use for development/testing to clear all gigs and start fresh.

Run from project root: python scripts/reset_gigs_db.py
"""
import sqlite3
import os
from pathlib import Path

# Must match backend/db.py: app uses project_root/backend.db
_root = Path(__file__).resolve().parent.parent
DB_PATH = _root / "backend.db"
if not DB_PATH.exists():
    DB_PATH = _root / "backend" / "backend.db"

def table_exists(cursor, name):
    cursor.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,))
    return cursor.fetchone() is not None

def main():
    if not DB_PATH.exists():
        print(f"Database not found: {DB_PATH}")
        return
    print(f"Using database: {DB_PATH}")
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA foreign_keys = ON")
    cursor = conn.cursor()
    if not table_exists(cursor, "gigs"):
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        names = [r[0] for r in cursor.fetchall()]
        print("WARNING: 'gigs' table not found. Is this the correct database?")
        print(f"Tables in this DB: {', '.join(names) if names else '(none)'}")
        conn.close()
        return
    # Delete in order (respect FK: child tables first). Only touch tables that exist.
    if table_exists(cursor, "notifications"):
        cursor.execute("DELETE FROM notifications WHERE gig_id IS NOT NULL")
        print(f"Deleted from notifications (gig-related): {cursor.rowcount} rows")
    if table_exists(cursor, "gig_email_log"):
        cursor.execute("DELETE FROM gig_email_log")
        print(f"Deleted from gig_email_log: {cursor.rowcount} rows")
    if table_exists(cursor, "public_activity"):
        cursor.execute("DELETE FROM public_activity WHERE gig_id IS NOT NULL")
        print(f"Deleted from public_activity (gig-related): {cursor.rowcount} rows")
    tables = [
        "payment_cancellations",
        "transactions",
        "gig_contracts",
        "gig_slots",
        "gigs",
    ]
    for table in tables:
        if table_exists(cursor, table):
            cursor.execute(f"DELETE FROM {table}")
            print(f"Deleted from {table}: {cursor.rowcount} rows")
        else:
            print(f"Skipped {table} (table does not exist)")
    conn.commit()
    conn.close()
    print("Done. Gigs, slots, contracts, transactions, and related rows are cleared.")
    print("Users, artists, venues, preferred_artists, platform_settings are unchanged.")

if __name__ == "__main__":
    main()
