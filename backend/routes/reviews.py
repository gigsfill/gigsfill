"""
Artist Reviews & Ratings  (two-way)
=====================================
Venues can rate artists after a completed gig (1–5 stars + review text).
Artists can rate venues after a completed gig (1–5 stars + review text).

Endpoints:
  POST   /api/venues/{venue_id}/gigs/{gig_id}/review       — venue submits review of artist
  GET    /api/artists/{artist_id}/reviews                   — artist's public reviews
  GET    /api/artists/{artist_id}/reviews/summary           — avg + count (for profile cards)
  GET    /api/venues/{venue_id}/gigs/{gig_id}/review        — check if venue already reviewed
  POST   /api/artists/{artist_id}/gigs/{gig_id}/review      — artist submits review of venue
  GET    /api/venues/{venue_id}/reviews                     — venue's public reviews
  GET    /api/venues/{venue_id}/reviews/summary             — avg + count (for venue cards)
  GET    /api/artists/{artist_id}/gigs/{gig_id}/venue-review — check if artist already reviewed
  PUT    /api/admin/reviews/{review_id}/visibility          — admin hide/show
  GET    /api/admin/reviews                                  — admin list all reviews
"""
import logging
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from backend.routes.auth import get_current_user
from backend.routes.admin import check_admin
from backend.db import get_db
from backend.utils import check_venue_access, check_artist_access

logger = logging.getLogger("gigsfill.reviews")
router = APIRouter()

_TABLE_CREATED_ARTIST_REVIEWS = False
_TABLE_CREATED_VENUE_REVIEWS = False

def _ensure_artist_reviews_table(db):
    global _TABLE_CREATED_ARTIST_REVIEWS
    if _TABLE_CREATED_ARTIST_REVIEWS:
        return
    try:
        db.execute(text("""CREATE TABLE IF NOT EXISTS artist_reviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                gig_id INTEGER NOT NULL,
                venue_id INTEGER NOT NULL,
                artist_id INTEGER NOT NULL,
                reviewer_user_id INTEGER NOT NULL,
                rating INTEGER NOT NULL,
                review_text TEXT DEFAULT '',
                is_visible INTEGER DEFAULT 1,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )"""))
        db.commit()
        # Ensure avg_rating and review_count columns exist on artists table
        try:
            db.execute(text("ALTER TABLE artists ADD COLUMN avg_rating REAL DEFAULT NULL"))
            db.commit()
        except Exception:
            pass
        try:
            db.execute(text("ALTER TABLE artists ADD COLUMN review_count INTEGER DEFAULT 0"))
            db.commit()
        except Exception:
            pass
        _TABLE_CREATED_ARTIST_REVIEWS = True
    except Exception:
        pass
    finally:
        _TABLE_CREATED_ARTIST_REVIEWS = True



# ── SUBMIT REVIEW ──────────────────────────────────────────────────────────────
@router.post("/api/venues/{venue_id}/gigs/{gig_id}/review")
def submit_review(venue_id: int, gig_id: int, data: dict,
                  user=Depends(get_current_user), db=Depends(get_db)):
    """Venue submits a star rating + review for an artist after a completed gig."""
    check_venue_access(db, venue_id, user.id)

    rating = data.get("rating")
    review_text = str(data.get("review_text", "")).strip()[:2000]

    if not isinstance(rating, int) or rating < 1 or rating > 5:
        raise HTTPException(400, "Rating must be an integer between 1 and 5")

    # Verify gig belongs to venue and is completed
    gig = db.execute(
        text("""
            SELECT g.id, g.status, g.date, gs.artist_id
            FROM gigs g
            LEFT JOIN gig_slots gs ON gs.gig_id = g.id AND gs.status = 'booked'
            WHERE g.id = :gid AND g.venue_id = :vid
            LIMIT 1
        """),
        {"gid": gig_id, "vid": venue_id}
    ).mappings().first()

    if not gig:
        raise HTTPException(404, "Gig not found")
    if gig["status"] not in ("booked", "completed", "closed"):
        raise HTTPException(400, "Reviews can only be submitted for booked or completed gigs")
    if not gig["artist_id"]:
        raise HTTPException(400, "No artist booked for this gig")

    artist_id = gig["artist_id"]

    # Upsert using INSERT OR REPLACE to handle UNIQUE(gig_id, venue_id, artist_id)
    try:
        db.rollback()
        db.execute(
            text("""
                INSERT OR REPLACE INTO artist_reviews (gig_id, venue_id, artist_id, reviewer_user_id, rating, review_text)
                VALUES (:gig_id, :vid, :aid, :uid, :rating, :text)
            """),
            {"gig_id": gig_id, "vid": venue_id, "aid": artist_id,
             "uid": user.id, "rating": rating, "text": review_text}
        )
        db.commit()
    except Exception as e:
        db.rollback()
        logger.error(f"Review submit error: {type(e).__name__}: {e}")
        raise HTTPException(500, f"Failed to save review: {type(e).__name__}")

    # Recompute artist's cached average (update artists table if avg_rating column exists)
    try:
        avg = db.execute(
            text("""
                SELECT ROUND(AVG(rating), 1) as avg, COUNT(*) as cnt
                FROM artist_reviews
                WHERE artist_id = :aid AND is_visible = 1
            """),
            {"aid": artist_id}
        ).mappings().first()
        if avg:
            try:
                db.execute(
                    text("UPDATE artists SET avg_rating = :avg, review_count = :cnt WHERE id = :aid"),
                    {"avg": avg["avg"], "cnt": avg["cnt"], "aid": artist_id}
                )
                db.commit()
            except Exception:
                pass  # avg_rating column may not exist yet — will be added by _add_columns
    except Exception:
        pass

    return {"ok": True, "message": "Review submitted"}


# ── CHECK IF VENUE REVIEWED THIS GIG ──────────────────────────────────────────
@router.get("/api/venues/{venue_id}/gigs/{gig_id}/review")
def get_gig_review(venue_id: int, gig_id: int,
                   user=Depends(get_current_user), db=Depends(get_db)):
    """Returns the venue's review for this gig (if it exists)."""
    check_venue_access(db, venue_id, user.id)

    review = db.execute(
        text("""
            SELECT r.id, r.rating, r.review_text, r.created_at,
                   a.name as artist_name
            FROM artist_reviews r
            JOIN artists a ON a.id = r.artist_id
            WHERE r.gig_id = :gid AND r.venue_id = :vid
        """),
        {"gid": gig_id, "vid": venue_id}
    ).mappings().first()

    if not review:
        return {"review": None}

    return {"review": dict(review)}


@router.get("/api/venues/{venue_id}/artists/{artist_id}/review")
def get_venue_artist_review(venue_id: int, artist_id: int,
                             user=Depends(get_current_user), db=Depends(get_db)):
    """Check if venue has a general review for this artist (not gig-specific)."""
    check_venue_access(db, venue_id, user.id)
    row = db.execute(
        text("""SELECT id, rating, review_text FROM artist_reviews
                WHERE gig_id IS NULL AND venue_id=:vid AND artist_id=:aid"""),
        {"vid": venue_id, "aid": artist_id}
    ).mappings().first()
    if not row:
        return {"reviewed": False}
    return {"reviewed": True, "rating": row["rating"], "review_text": row["review_text"] or ""}


# ── ARTIST'S PUBLIC REVIEWS ────────────────────────────────────────────────────
@router.get("/api/artists/{artist_id}/reviews")
def get_artist_reviews(artist_id: int, page: int = 1, limit: int = 10,
                       db=Depends(get_db)):
    """Public endpoint — returns visible reviews for an artist, paginated."""
    _ensure_artist_reviews_table(db)
    limit = min(limit, 50)
    offset = (page - 1) * limit

    reviews = db.execute(
        text("""
            SELECT r.id, r.rating, r.review_text, r.created_at, r.venue_id,
                   v.venue_name as venue_name, v.city, v.state,
                   g.date as gig_date, g.title as gig_title
            FROM artist_reviews r
            JOIN venues v ON v.id = r.venue_id
            LEFT JOIN gigs g ON g.id = r.gig_id AND r.gig_id IS NOT NULL
            WHERE r.artist_id = :aid AND r.is_visible = 1
            ORDER BY r.created_at DESC
            LIMIT :limit OFFSET :offset
        """),
        {"aid": artist_id, "limit": limit, "offset": offset}
    ).mappings().all()

    total = db.execute(
        text("SELECT COUNT(*) FROM artist_reviews WHERE artist_id = :aid AND is_visible = 1"),
        {"aid": artist_id}
    ).scalar() or 0

    return {
        "reviews": [dict(r) for r in reviews],
        "total": total,
        "page": page,
        "pages": max(1, (total + limit - 1) // limit)
    }


# ── ARTIST RATING SUMMARY ─────────────────────────────────────────────────────
@router.get("/api/artists/{artist_id}/reviews/summary")
def get_artist_review_summary(artist_id: int, db=Depends(get_db)):
    """Returns average rating + count for use in profile cards and search results."""
    _ensure_artist_reviews_table(db)
    summary = db.execute(
        text("""
            SELECT
                ROUND(AVG(rating), 1) as avg_rating,
                COUNT(*) as review_count,
                SUM(CASE WHEN rating = 5 THEN 1 ELSE 0 END) as five_star,
                SUM(CASE WHEN rating = 4 THEN 1 ELSE 0 END) as four_star,
                SUM(CASE WHEN rating = 3 THEN 1 ELSE 0 END) as three_star,
                SUM(CASE WHEN rating = 2 THEN 1 ELSE 0 END) as two_star,
                SUM(CASE WHEN rating = 1 THEN 1 ELSE 0 END) as one_star
            FROM artist_reviews
            WHERE artist_id = :aid AND is_visible = 1
        """),
        {"aid": artist_id}
    ).mappings().first()

    return dict(summary) if summary else {
        "avg_rating": None, "review_count": 0,
        "five_star": 0, "four_star": 0, "three_star": 0,
        "two_star": 0, "one_star": 0
    }



# ── SUBMIT GENERAL REVIEW (no specific gig) ───────────────────────────────────
@router.post("/api/venues/{venue_id}/artists/{artist_id}/review")
def submit_general_review(venue_id: int, artist_id: int, data: dict,
                           user=Depends(get_current_user), db=Depends(get_db)):
    """Venue submits a general star rating for an artist (not tied to a specific gig)."""
    _ensure_artist_reviews_table(db)
    check_venue_access(db, venue_id, user.id)

    rating = data.get("rating")
    review_text = str(data.get("review_text", "")).strip()[:2000]

    if not isinstance(rating, int) or rating < 1 or rating > 5:
        raise HTTPException(400, "Rating must be between 1 and 5")

    # Verify artist exists
    artist = db.execute(
        text("SELECT id FROM artists WHERE id = :aid"),
        {"aid": artist_id}
    ).mappings().first()
    if not artist:
        raise HTTPException(404, "Artist not found")

    try:
        db.rollback()
        # One review per venue+artist pair (gig_id=NULL = general review, not gig-specific)
        # Upsert: delete any existing general review, insert fresh
        db.execute(
            text("DELETE FROM artist_reviews WHERE gig_id IS NULL AND venue_id = :vid AND artist_id = :aid"),
            {"vid": venue_id, "aid": artist_id}
        )
        db.execute(
            text("""
                INSERT INTO artist_reviews (gig_id, venue_id, artist_id, reviewer_user_id, rating, review_text)
                VALUES (NULL, :vid, :aid, :uid, :rating, :text)
            """),
            {"vid": venue_id, "aid": artist_id, "uid": user.id, "rating": rating, "text": review_text}
        )
        db.commit()
    except Exception as e:
        db.rollback()
        logger.error(f"General review submit error: {type(e).__name__}: {e}")
        raise HTTPException(500, f"Failed to save review: {type(e).__name__}")

    # Recompute average
    try:
        avg = db.execute(
            text("SELECT ROUND(AVG(rating),1) as avg, COUNT(*) as cnt FROM artist_reviews WHERE artist_id=:aid AND is_visible=1"),
            {"aid": artist_id}
        ).mappings().first()
        if avg:
            try:
                db.execute(
                    text("UPDATE artists SET avg_rating=:avg, review_count=:cnt WHERE id=:aid"),
                    {"avg": avg["avg"], "cnt": avg["cnt"], "aid": artist_id}
                )
                db.commit()
            except Exception:
                pass
    except Exception:
        pass

    return {"ok": True, "message": "Review submitted"}


# ── GIGS PENDING REVIEW (for venue dashboard prompt) ─────────────────────────
@router.get("/api/venues/{venue_id}/reviews/pending")
def get_pending_reviews(venue_id: int, user=Depends(get_current_user), db=Depends(get_db)):
    """Returns completed gigs that the venue hasn't reviewed yet."""
    check_venue_access(db, venue_id, user.id)

    pending = db.execute(
        text("""
            SELECT g.id as gig_id, g.title, g.date, a.id as artist_id, a.name as artist_name
            FROM gigs g
            JOIN gig_slots gs ON gs.gig_id = g.id AND gs.status = 'booked'
            JOIN artists a ON a.id = gs.artist_id
            WHERE g.venue_id = :vid
              AND g.status IN ('completed', 'closed')
              AND NOT EXISTS (
                SELECT 1 FROM artist_reviews r
                WHERE r.gig_id = g.id AND r.venue_id = :vid
              )
            ORDER BY g.date DESC
            LIMIT 20
        """),
        {"vid": venue_id}
    ).mappings().all()

    return {"pending": [dict(p) for p in pending]}


# ── ADMIN: LIST ALL REVIEWS ───────────────────────────────────────────────────
@router.get("/api/admin/reviews")
def admin_list_reviews(page: int = 1, limit: int = 50,
                       admin=Depends(check_admin), db=Depends(get_db)):
    limit = min(limit, 100)
    offset = (page - 1) * limit

    reviews = db.execute(
        text("""
            SELECT r.id, r.gig_id, r.rating, r.review_text, r.is_visible, r.created_at,
                   a.name as artist_name, v.venue_name as venue_name
            FROM artist_reviews r
            JOIN artists a ON a.id = r.artist_id
            JOIN venues v ON v.id = r.venue_id
            ORDER BY r.created_at DESC
            LIMIT :limit OFFSET :offset
        """),
        {"limit": limit, "offset": offset}
    ).mappings().all()

    total = db.execute(text("SELECT COUNT(*) FROM artist_reviews")).scalar() or 0

    return {"reviews": [dict(r) for r in reviews], "total": total}


# ── ADMIN: TOGGLE REVIEW VISIBILITY ──────────────────────────────────────────
@router.put("/api/admin/reviews/{review_id}/visibility")
def toggle_review_visibility(review_id: int, data: dict,
                              admin=Depends(check_admin), db=Depends(get_db)):
    visible = 1 if data.get("is_visible") else 0
    db.execute(
        text("UPDATE artist_reviews SET is_visible = :v WHERE id = :id"),
        {"v": visible, "id": review_id}
    )
    db.commit()

    # Recompute artist avg
    review = db.execute(
        text("SELECT artist_id FROM artist_reviews WHERE id = :id"),
        {"id": review_id}
    ).mappings().first()
    if review:
        try:
            avg = db.execute(
                text("SELECT ROUND(AVG(rating),1) as avg, COUNT(*) as cnt FROM artist_reviews WHERE artist_id=:aid AND is_visible=1"),
                {"aid": review["artist_id"]}
            ).mappings().first()
            db.execute(
                text("UPDATE artists SET avg_rating = :avg, review_count = :cnt WHERE id = :aid"),
                {"avg": avg["avg"] or 0, "cnt": avg["cnt"] or 0, "aid": review["artist_id"]}
            )
            db.commit()
        except Exception:
            pass

    return {"ok": True}


# ── VENUE EDIT / DELETE THEIR OWN REVIEW ──────────────────────────────────────
@router.put("/api/venues/{venue_id}/artists/{artist_id}/review")
def update_artist_review(venue_id: int, artist_id: int, data: dict,
                          user=Depends(get_current_user), db=Depends(get_db)):
    check_venue_access(db, venue_id, user.id)
    rating = data.get("rating")
    review_text = str(data.get("review_text", "")).strip()[:2000]
    try:
        rating = int(rating)
    except (TypeError, ValueError):
        raise HTTPException(400, "Invalid rating")
    if not (1 <= rating <= 5):
        raise HTTPException(400, "Invalid rating")
    result = db.execute(
        text("""UPDATE artist_reviews
                SET rating=:r, review_text=:t
                WHERE venue_id=:vid AND artist_id=:aid"""),
        {"r": rating, "t": review_text, "vid": venue_id, "aid": artist_id}
    )
    db.commit()
    if result.rowcount == 0:
        raise HTTPException(404, "Review not found")
    return {"ok": True, "message": "Review updated"}


@router.delete("/api/venues/{venue_id}/artists/{artist_id}/review")
def delete_artist_review(venue_id: int, artist_id: int,
                          user=Depends(get_current_user), db=Depends(get_db)):
    check_venue_access(db, venue_id, user.id)
    result = db.execute(
        text("DELETE FROM artist_reviews WHERE venue_id=:vid AND artist_id=:aid"),
        {"vid": venue_id, "aid": artist_id}
    )
    db.commit()
    if result.rowcount == 0:
        raise HTTPException(404, "Review not found")
    return {"ok": True, "message": "Review deleted"}


@router.delete("/api/reviews/{review_id}")
def delete_review_by_id(review_id: int, user=Depends(get_current_user), db=Depends(get_db)):
    """Venue deletes a specific review by id — only if they own it."""
    row = db.execute(
        text("SELECT venue_id FROM artist_reviews WHERE id = :rid"),
        {"rid": review_id}
    ).first()
    if not row:
        raise HTTPException(404, "Review not found")
    check_venue_access(db, row[0], user.id)
    db.execute(text("DELETE FROM artist_reviews WHERE id = :rid"), {"rid": review_id})
    db.commit()
    return {"ok": True, "message": "Review deleted"}


@router.delete("/api/artists/{artist_id}/venues/{venue_id}/review")
def delete_venue_review(artist_id: int, venue_id: int,
                        user=Depends(get_current_user), db=Depends(get_db)):
    """Artist deletes their general review of a venue."""
    _ensure_venue_reviews_table(db)
    check_artist_access(db, artist_id, user.id)
    result = db.execute(
        text("DELETE FROM venue_reviews WHERE artist_id=:aid AND venue_id=:vid"),
        {"aid": artist_id, "vid": venue_id}
    )
    db.commit()
    if result.rowcount == 0:
        raise HTTPException(404, "Review not found")
    _refresh_venue_stats(db, venue_id)
    return {"ok": True, "message": "Review deleted"}


# ══════════════════════════════════════════════════════════════════════════════
# VENUE REVIEWS (written by artists)
# ══════════════════════════════════════════════════════════════════════════════

def _ensure_venue_reviews_table(db):
    """Create venue_reviews table on first use (migration-free)."""
    global _TABLE_CREATED_VENUE_REVIEWS
    if _TABLE_CREATED_VENUE_REVIEWS:
        return
    try:
        db.execute(text("""CREATE TABLE IF NOT EXISTS venue_reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            gig_id INTEGER,
            venue_id INTEGER NOT NULL,
            artist_id INTEGER NOT NULL,
            reviewer_user_id INTEGER NOT NULL,
            rating INTEGER NOT NULL CHECK(rating BETWEEN 1 AND 5),
            review_text TEXT DEFAULT '',
            is_visible INTEGER DEFAULT 1,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )"""))
        db.commit()
        # Add avg_rating / review_count to venues table if not present
        for col, defval in [("avg_rating", "NULL"), ("review_count", "0")]:
            try:
                db.execute(text(f"ALTER TABLE venues ADD COLUMN {col} REAL DEFAULT {defval}"))
                db.commit()
            except Exception:
                pass
        _TABLE_CREATED_VENUE_REVIEWS = True
    except Exception:
        pass
    finally:
        _TABLE_CREATED_VENUE_REVIEWS = True


def _refresh_venue_stats(db, venue_id: int):
    """Recompute and store avg_rating + review_count on the venues row."""
    row = db.execute(
        text("SELECT ROUND(AVG(rating),1), COUNT(*) FROM venue_reviews WHERE venue_id=:vid AND is_visible=1"),
        {"vid": venue_id}
    ).first()
    avg, cnt = (row[0] or None), (row[1] or 0)
    try:
        db.execute(
            text("UPDATE venues SET avg_rating=:avg, review_count=:cnt WHERE id=:vid"),
            {"avg": avg, "cnt": cnt, "vid": venue_id}
        )
        db.commit()
    except Exception:
        pass


@router.post("/api/artists/{artist_id}/gigs/{gig_id}/review")
def submit_venue_review(artist_id: int, gig_id: int, data: dict,
                         user=Depends(get_current_user), db=Depends(get_db)):
    """Artist submits a general review of the venue (gig_id kept in URL for compat, ignored for uniqueness)."""
    _ensure_venue_reviews_table(db)
    check_artist_access(db, artist_id, user.id)

    rating = data.get("rating")
    review_text = (data.get("review_text") or "").strip()

    if not rating or int(rating) not in range(1, 6):
        raise HTTPException(400, "Rating must be 1–5")

    # Derive venue_id from the gig for context, but review is per artist+venue (not per gig)
    gig = db.execute(
        text("""SELECT g.id, g.venue_id FROM gigs g
                WHERE g.id = :gid AND (
                    g.artist_id = :aid
                    OR EXISTS (SELECT 1 FROM gig_slots s WHERE s.gig_id=g.id AND s.artist_id=:aid)
                )"""),
        {"gid": gig_id, "aid": artist_id}
    ).mappings().first()

    if not gig:
        raise HTTPException(404, "Gig not found or you were not the booked artist")

    venue_id = gig["venue_id"]
    _submit_venue_review_general(db, artist_id, venue_id, user.id, int(rating), review_text)
    return {"ok": True, "message": "Review submitted"}


@router.post("/api/artists/{artist_id}/venues/{venue_id}/review")
def submit_venue_review_direct(artist_id: int, venue_id: int, data: dict,
                                user=Depends(get_current_user), db=Depends(get_db)):
    """Artist submits a general review of a venue (no gig required)."""
    _ensure_venue_reviews_table(db)
    check_artist_access(db, artist_id, user.id)

    rating = data.get("rating")
    review_text = (data.get("review_text") or "").strip()
    if not rating or int(rating) not in range(1, 6):
        raise HTTPException(400, "Rating must be 1–5")

    venue = db.execute(text("SELECT id FROM venues WHERE id=:vid"), {"vid": venue_id}).first()
    if not venue:
        raise HTTPException(404, "Venue not found")

    _submit_venue_review_general(db, artist_id, venue_id, user.id, int(rating), review_text)
    return {"ok": True, "message": "Review submitted"}


@router.get("/api/artists/{artist_id}/venues/{venue_id}/review")
def get_artist_venue_review_general(artist_id: int, venue_id: int,
                                     user=Depends(get_current_user), db=Depends(get_db)):
    """Check if this artist has reviewed this venue (general, not gig-specific)."""
    _ensure_venue_reviews_table(db)
    check_artist_access(db, artist_id, user.id)
    row = db.execute(
        text("SELECT id, rating, review_text FROM venue_reviews WHERE gig_id IS NULL AND venue_id=:vid AND artist_id=:aid"),
        {"vid": venue_id, "aid": artist_id}
    ).mappings().first()
    if not row:
        return {"reviewed": False}
    return {"reviewed": True, "rating": row["rating"], "review_text": row["review_text"] or ""}


def _submit_venue_review_general(db, artist_id, venue_id, user_id, rating, review_text):
    """Upsert a general (non-gig) venue review. One per artist+venue pair."""
    try:
        db.rollback()
        db.execute(
            text("DELETE FROM venue_reviews WHERE gig_id IS NULL AND venue_id=:vid AND artist_id=:aid"),
            {"vid": venue_id, "aid": artist_id}
        )
        db.execute(
            text("""INSERT INTO venue_reviews (gig_id, venue_id, artist_id, reviewer_user_id, rating, review_text)
                    VALUES (NULL, :vid, :aid, :uid, :r, :t)"""),
            {"vid": venue_id, "aid": artist_id, "uid": user_id, "r": rating, "t": review_text}
        )
        db.commit()
        _refresh_venue_stats(db, venue_id)
    except Exception as e:
        db.rollback()
        raise


@router.get("/api/artists/{artist_id}/gigs/{gig_id}/venue-review")
def get_artist_venue_review(artist_id: int, gig_id: int,
                              user=Depends(get_current_user), db=Depends(get_db)):
    """Check whether this artist has already reviewed the venue for this gig."""
    _ensure_venue_reviews_table(db)
    check_artist_access(db, artist_id, user.id)
    row = db.execute(
        text("SELECT id, rating, review_text FROM venue_reviews WHERE gig_id=:gid AND artist_id=:aid"),
        {"gid": gig_id, "aid": artist_id}
    ).mappings().first()
    if not row:
        return {"reviewed": False}
    return {"reviewed": True, "rating": row["rating"], "review_text": row["review_text"], "id": row["id"]}


@router.get("/api/venues/{venue_id}/reviews")
def get_venue_reviews(venue_id: int, page: int = 1, limit: int = 10, db=Depends(get_db)):
    """Public list of reviews written about a venue by artists."""
    _ensure_venue_reviews_table(db)
    offset = (page - 1) * limit
    rows = db.execute(
        text("""SELECT vr.id, vr.rating, vr.review_text, vr.created_at,
                       a.name as artist_name, a.id as artist_id
                FROM venue_reviews vr
                JOIN artists a ON vr.artist_id = a.id
                WHERE vr.venue_id = :vid AND vr.is_visible = 1
                ORDER BY vr.created_at DESC
                LIMIT :lim OFFSET :off"""),
        {"vid": venue_id, "lim": limit, "off": offset}
    ).mappings().all()
    total = db.execute(
        text("SELECT COUNT(*) FROM venue_reviews WHERE venue_id=:vid AND is_visible=1"),
        {"vid": venue_id}
    ).scalar() or 0
    return {
        "reviews": [dict(r) for r in rows],
        "total": total,
        "page": page,
        "pages": max(1, (total + limit - 1) // limit)
    }


@router.get("/api/venues/{venue_id}/reviews/summary")
def get_venue_review_summary(venue_id: int, db=Depends(get_db)):
    """Average rating, count, and per-star breakdown for a venue."""
    _ensure_venue_reviews_table(db)
    row = db.execute(
        text("""
            SELECT ROUND(AVG(rating),1) as avg_rating,
                   COUNT(*) as review_count,
                   SUM(CASE WHEN rating = 5 THEN 1 ELSE 0 END) as five_star,
                   SUM(CASE WHEN rating = 4 THEN 1 ELSE 0 END) as four_star,
                   SUM(CASE WHEN rating = 3 THEN 1 ELSE 0 END) as three_star,
                   SUM(CASE WHEN rating = 2 THEN 1 ELSE 0 END) as two_star,
                   SUM(CASE WHEN rating = 1 THEN 1 ELSE 0 END) as one_star
            FROM venue_reviews WHERE venue_id=:vid AND is_visible=1
        """),
        {"vid": venue_id}
    ).mappings().first()
    return {
        "avg_rating": row["avg_rating"],
        "review_count": row["review_count"] or 0,
        "five_star":  row["five_star"]  or 0,
        "four_star":  row["four_star"]  or 0,
        "three_star": row["three_star"] or 0,
        "two_star":   row["two_star"]   or 0,
        "one_star":   row["one_star"]   or 0,
    }
