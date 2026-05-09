#!/usr/bin/env python3
"""
Delete all gigs dated 2026-03-28 and later, plus all related records.
Run: cd /opt/gigsfill && python3 cleanup_gigs.py
"""
import sqlite3

DB = "/opt/gigsfill/backend.db"
conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row

CUTOFF = "2026-03-28"

# Find gigs to delete
gigs = conn.execute(
    "SELECT id, date, status, title FROM gigs WHERE date >= ? ORDER BY date",
    (CUTOFF,)
).fetchall()

if not gigs:
    print(f"No gigs found on or after {CUTOFF}")
    conn.close()
    exit(0)

print(f"Found {len(gigs)} gig(s) to delete:")
for g in gigs:
    print(f"  gig_id={g['id']}  date={g['date']}  status={g['status']}  title={g['title'] or '(no title)'}")

confirm = input("\nDelete all of these? Type YES to confirm: ").strip()
if confirm != "YES":
    print("Aborted.")
    conn.close()
    exit(0)

gig_ids = [g["id"] for g in gigs]
placeholders = ",".join("?" * len(gig_ids))

tables = [
    ("gig_slots",        "gig_id"),
    ("gig_contracts",    "gig_id"),
    ("gig_waitlist",     "gig_id"),
    ("waitlist_offered", "gig_id"),
    ("gig_email_log",    "gig_id"),
    ("transactions",     "gig_id"),
    ("notifications",    "gig_id"),
    ("messages",         "gig_id"),
    ("reviews",          "gig_id"),
    ("gig_flyers",       "gig_id"),
]

for table, col in tables:
    try:
        n = conn.execute(
            f"DELETE FROM {table} WHERE {col} IN ({placeholders})", gig_ids
        ).rowcount
        if n:
            print(f"  Deleted {n} rows from {table}")
    except sqlite3.OperationalError as e:
        if "no such table" not in str(e):
            print(f"  Warning ({table}): {e}")

n = conn.execute(
    f"DELETE FROM gigs WHERE id IN ({placeholders})", gig_ids
).rowcount
print(f"  Deleted {n} gigs from gigs table")

conn.commit()
conn.close()
print(f"\nDone. {len(gig_ids)} gig(s) deleted.")
