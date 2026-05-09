"""
Review deep-link tokens.
POST /api/review-link/generate  → creates a signed token (used by scheduler)
GET  /api/review-link?token=XYZ → validates token, returns data for the review page
POST /api/review-link/submit    → submits the review via token (no login needed)
"""
import secrets
import logging
from datetime import datetime, timedelta
from backend.utils import utcnow_naive
from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import text
from backend.db import get_db, get_db_connection
from fastapi import Depends

router = APIRouter()
logger = logging.getLogger("gigsfill.review_links")

def _ensure_review_tokens_table():
    import sqlite3 as _sq3
    conn = get_db_connection()
    conn.row_factory = _sq3.Row
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS review_link_tokens (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token TEXT UNIQUE NOT NULL,
                direction TEXT NOT NULL,   -- 'venue_rates_artist' or 'artist_rates_venue'
                gig_id INTEGER NOT NULL,
                venue_id INTEGER,
                artist_id INTEGER,
                user_id INTEGER,           -- user who should submit (for context)
                expires_at DATETIME NOT NULL,
                used_at DATETIME,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()
    finally:
        conn.close()

_ensure_review_tokens_table()


def generate_review_token(direction: str, gig_id: int, venue_id: int,
                          artist_id: int, user_id: int) -> str:
    """Create a 30-day token for a review email link. Called from scheduler."""
    import sqlite3 as _sq3
    token = secrets.token_urlsafe(32)
    expires = (utcnow_naive() + timedelta(days=30)).isoformat()
    conn = get_db_connection()
    conn.row_factory = _sq3.Row
    try:
        conn.execute("""
            INSERT INTO review_link_tokens
                (token, direction, gig_id, venue_id, artist_id, user_id, expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (token, direction, gig_id, venue_id, artist_id, user_id, expires))
        conn.commit()
    finally:
        conn.close()
    return token


@router.get("/api/review-link")
def get_review_link(token: str):
    """Validate token and return context data for the review.html page."""
    conn = get_db_connection()
    conn.row_factory = __import__('sqlite3').Row
    try:
        row = conn.execute("""
            SELECT rlt.*,
                   g.title as gig_title, g.date as gig_date,
                   a.name as artist_name,
                   v.venue_name
            FROM review_link_tokens rlt
            JOIN gigs g ON g.id = rlt.gig_id
            JOIN artists a ON a.id = rlt.artist_id
            JOIN venues v ON v.id = rlt.venue_id
            WHERE rlt.token = ?
        """, (token,)).fetchone()

        if not row:
            raise HTTPException(404, "Invalid or expired review link")
        if row["used_at"]:
            raise HTTPException(410, "This review link has already been used")
        if datetime.fromisoformat(row["expires_at"]) < utcnow_naive():
            raise HTTPException(410, "This review link has expired")

        return {
            "direction":   row["direction"],
            "gig_id":      row["gig_id"],
            "venue_id":    row["venue_id"],
            "artist_id":   row["artist_id"],
            "gig_title":   row["gig_title"],
            "gig_date":    str(row["gig_date"] or "")[:10],
            "artist_name": row["artist_name"],
            "venue_name":  row["venue_name"],
        }
    finally:
        conn.close()


@router.post("/api/review-link/submit")
def submit_review_via_token(data: dict, db=Depends(get_db)):
    """Submit a review using a token (no login required)."""
    token   = data.get("token", "").strip()
    rating  = int(data.get("rating", 0))
    review_text = (data.get("review_text") or "").strip()

    if not token:
        raise HTTPException(400, "Token required")
    if not (1 <= rating <= 5):
        raise HTTPException(400, "Rating must be 1–5")

    import sqlite3 as _sq3
    conn = get_db_connection()
    conn.row_factory = _sq3.Row
    try:
        row = conn.execute("""
            SELECT * FROM review_link_tokens WHERE token = ?
        """, (token,)).fetchone()

        if not row:
            raise HTTPException(404, "Invalid or expired review link")
        if row["used_at"]:
            raise HTTPException(410, "This review link has already been used")
        if datetime.fromisoformat(row["expires_at"]) < utcnow_naive():
            raise HTTPException(410, "This review link has expired")

        gig_id    = row["gig_id"]
        venue_id  = row["venue_id"]
        artist_id = row["artist_id"]
        user_id   = row["user_id"]
        direction = row["direction"]

        if direction == "venue_rates_artist":
            # INSERT OR REPLACE so re-submission updates existing
            conn.execute("""
                INSERT INTO artist_reviews
                    (gig_id, venue_id, artist_id, reviewer_user_id, rating, review_text)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(gig_id, venue_id, artist_id) DO UPDATE
                SET rating=excluded.rating, review_text=excluded.review_text
            """, (gig_id, venue_id, artist_id, user_id, rating, review_text))

        elif direction == "artist_rates_venue":
            # Ensure venue_reviews table exists
            conn.execute("""
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
            conn.execute("""
                INSERT INTO venue_reviews
                    (gig_id, venue_id, artist_id, reviewer_user_id, rating, review_text)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(gig_id, venue_id, artist_id) DO UPDATE
                SET rating=excluded.rating, review_text=excluded.review_text
            """, (gig_id, venue_id, artist_id, user_id, rating, review_text))
        else:
            raise HTTPException(400, "Unknown review direction")

        # Mark token used
        conn.execute(
            "UPDATE review_link_tokens SET used_at = ? WHERE token = ?",
            (utcnow_naive().isoformat(), token)
        )
        conn.commit()
        return {"ok": True}

    finally:
        conn.close()
