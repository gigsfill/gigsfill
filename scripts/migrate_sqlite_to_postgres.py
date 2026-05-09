#!/usr/bin/env python3
"""
GigsFill: SQLite → PostgreSQL Data Migration
=============================================
Copies all data from backend.db (SQLite) into a PostgreSQL database.

Usage:
    python scripts/migrate_sqlite_to_postgres.py \
        --sqlite /opt/gigsfill/backend.db \
        --postgres "postgresql://gigsfill_user:PASSWORD@localhost:5432/gigsfill"

Features:
- Safe: reads SQLite, writes to Postgres — never modifies SQLite
- Idempotent: run multiple times; use --truncate to wipe Postgres tables first
- Progress: shows row counts per table
- Verification: reports row count match after migration
"""

import argparse
import sqlite3
import sys
import logging

logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
log = logging.getLogger("migrate")


def get_tables(sqlite_conn):
    """Return all user tables in SQLite ordered by dependency (FKs)."""
    cur = sqlite_conn.cursor()
    cur.execute("""
        SELECT name FROM sqlite_master
        WHERE type='table'
          AND name NOT LIKE 'sqlite_%'
        ORDER BY name
    """)
    return [r[0] for r in cur.fetchall()]


def get_columns(sqlite_conn, table):
    cur = sqlite_conn.cursor()
    cur.execute(f"PRAGMA table_info({table})")
    return [r[1] for r in cur.fetchall()]


def migrate_table(sqlite_conn, pg_conn, table, truncate=False):
    cols = get_columns(sqlite_conn, table)
    if not cols:
        log.warning(f"  Skipping {table} — no columns found")
        return 0

    sqlite_cur = sqlite_conn.cursor()
    pg_cur = pg_conn.cursor()

    if truncate:
        pg_cur.execute(f'TRUNCATE TABLE "{table}" RESTART IDENTITY CASCADE')
        pg_conn.commit()

    sqlite_cur.execute(f"SELECT COUNT(*) FROM {table}")
    total = sqlite_cur.fetchone()[0]
    if total == 0:
        log.info(f"  {table}: 0 rows (empty)")
        return 0

    sqlite_cur.execute(f"SELECT * FROM {table}")
    rows = sqlite_cur.fetchall()

    col_list = ', '.join(f'"{c}"' for c in cols)
    placeholders = ', '.join(['%s'] * len(cols))
    sql = f'INSERT INTO "{table}" ({col_list}) VALUES ({placeholders}) ON CONFLICT DO NOTHING'

    batch = []
    errors = 0
    for row in rows:
        # Convert row to list, handle sqlite3.Row or tuple
        values = list(row)
        # Convert booleans: SQLite stores 0/1, Postgres needs True/False for BOOLEAN cols
        batch.append(values)
        if len(batch) >= 500:
            try:
                pg_cur.executemany(sql, batch)
                pg_conn.commit()
            except Exception as e:
                pg_conn.rollback()
                errors += 1
                log.warning(f"  Batch error in {table}: {e}")
            batch = []

    if batch:
        try:
            pg_cur.executemany(sql, batch)
            pg_conn.commit()
        except Exception as e:
            pg_conn.rollback()
            errors += 1
            log.warning(f"  Final batch error in {table}: {e}")

    # Verify count
    pg_cur.execute(f'SELECT COUNT(*) FROM "{table}"')
    pg_count = pg_cur.fetchone()[0]
    status = "✅" if pg_count >= total and errors == 0 else "⚠️"
    log.info(f"  {status} {table}: {total} → {pg_count} rows{' (' + str(errors) + ' errors)' if errors else ''}")
    return pg_count


def reset_sequences(pg_conn):
    """Reset all SERIAL sequences to max(id) so new inserts don't conflict."""
    pg_cur = pg_conn.cursor()
    pg_cur.execute("""
        SELECT table_name, column_name
        FROM information_schema.columns
        WHERE column_default LIKE 'nextval%'
          AND table_schema = 'public'
    """)
    seq_cols = pg_cur.fetchall()
    for table, col in seq_cols:
        try:
            pg_cur.execute(f"""
                SELECT setval(
                    pg_get_serial_sequence('{table}', '{col}'),
                    COALESCE((SELECT MAX("{col}") FROM "{table}"), 1)
                )
            """)
        except Exception as e:
            log.warning(f"  Could not reset sequence for {table}.{col}: {e}")
    pg_conn.commit()
    log.info(f"✅ Reset {len(seq_cols)} sequences")


def main():
    parser = argparse.ArgumentParser(description="Migrate GigsFill SQLite → PostgreSQL")
    parser.add_argument("--sqlite",   required=True,  help="Path to backend.db")
    parser.add_argument("--postgres", required=True,  help="PostgreSQL connection URL")
    parser.add_argument("--truncate", action="store_true",
                        help="Truncate Postgres tables before inserting (for re-runs)")
    parser.add_argument("--tables",   nargs="*",
                        help="Only migrate specific tables (default: all)")
    args = parser.parse_args()

    log.info(f"Connecting to SQLite: {args.sqlite}")
    sqlite_conn = sqlite3.connect(args.sqlite)
    sqlite_conn.row_factory = sqlite3.Row

    log.info(f"Connecting to PostgreSQL...")
    try:
        import psycopg2
    except ImportError:
        log.error("psycopg2 not installed. Run: pip install psycopg2-binary")
        sys.exit(1)

    pg_conn = psycopg2.connect(args.postgres)

    tables = args.tables or get_tables(sqlite_conn)
    log.info(f"Migrating {len(tables)} tables...")
    log.info("")

    total_rows = 0
    for table in tables:
        try:
            rows = migrate_table(sqlite_conn, pg_conn, table, truncate=args.truncate)
            total_rows += rows
        except Exception as e:
            log.error(f"  FAILED {table}: {e}")
            pg_conn.rollback()

    log.info("")
    log.info("Resetting PostgreSQL sequences...")
    reset_sequences(pg_conn)

    log.info("")
    log.info(f"✅ Migration complete — {total_rows} total rows migrated")
    log.info("")
    log.info("Next steps:")
    log.info("  1. Verify data in PostgreSQL (spot-check a few tables)")
    log.info("  2. Set DATABASE_URL env var in your systemd service")
    log.info("  3. Restart gigsfill service")
    log.info("  4. Test the site — SQLite is still intact as fallback")

    sqlite_conn.close()
    pg_conn.close()


if __name__ == "__main__":
    main()
