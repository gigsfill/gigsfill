"""
Vanity URLs for public profiles (May 2026)
==========================================
Lets artists and venues share a clean URL like ``gigsfill.com/fridayspast``
instead of ``gigsfill.com/app/artist-profile.html?artist_id=1``.

Mechanics:
  1. The ``vanity_urls`` table maps slug → (entity_type, entity_id). One
     row per artist/venue. Slug namespace is global (artists and venues
     compete for the same slugs).
  2. The resolver route ``GET /{slug}`` is registered LAST in main.py so
     specific routes win first. It reads the matching artist-profile.html
     or venue-profile.html, injects a small ``<script>window._VANITY={...}``
     block, and returns the patched HTML. The page's existing JS reads the
     injected ids and behaves identically to the ?artist_id=N path.
  3. The page then calls ``history.replaceState`` to keep the clean URL in
     the address bar — visitors who copy the URL get ``/fridayspast`` not
     ``/app/artist-profile.html?artist_id=1``.

Backward compat: the legacy ``?artist_id=N`` / ``?venue_id=N`` URLs keep
working forever. Old emails, bookmarks, shared screenshots all unchanged.

Reserved slugs: short list of paths the app already uses (api, app, health,
static, login, signup, admin, etc.). Trying to set a reserved slug as a
vanity URL returns 400.
"""

import re
import os
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import text

from backend.db import get_db
from backend.routes.auth import get_current_user
from backend.utils import check_artist_access, check_venue_access

router = APIRouter()

# ─── Reserved slugs ─────────────────────────────────────────────────────────
# Anything used by the app's routing — both nginx and FastAPI. If we add
# new top-level paths in the future, add them here too. Slugs are matched
# case-insensitive.
RESERVED_SLUGS = {
    # nginx / FastAPI routing roots
    'app', 'api', 'health', 'static', 'sw.js', 'robots.txt', 'sitemap.xml',
    'favicon.ico', 'manifest.json',
    # Reserved future top-level paths
    'admin', 'login', 'signup', 'signin', 'logout', 'signout',
    'profile', 'settings', 'help', 'support', 'feedback', 'pricing',
    'terms', 'privacy', 'legal', 'about', 'contact', 'blog', 'docs',
    'features', 'faq', 'home', 'index',
    # Marketing / future
    'discover', 'venues', 'artists', 'gigs', 'calendar', 'search',
    'browse', 'explore',
}


# ─── Slug generation ────────────────────────────────────────────────────────
_NAME_TO_SLUG = re.compile(r'[^a-z0-9]+')


def slugify(name: str) -> str:
    """Convert a display name to a slug candidate. Collapses non-alnum to
    nothing (so "Fridays Past" → "fridayspast"), lowercases everything.

    Returns an empty string for input that contains no alphanumerics —
    caller should treat as "needs manual slug" rather than fall through to
    a default.
    """
    if not name:
        return ''
    s = name.lower().strip()
    # First pass: replace runs of non-alphanumeric with single space then
    # strip — gives "fridays past". Second pass: collapse to "fridayspast"
    # (per user's preference for the Twitter-style collapsed form).
    s = _NAME_TO_SLUG.sub('', s)
    return s[:60]  # enforce a reasonable length


def is_valid_slug(slug: str) -> bool:
    """Slugs must be 2-60 chars, lowercase alnum + hyphen only (no leading/
    trailing hyphens), and must NOT be a reserved word."""
    if not slug or len(slug) < 2 or len(slug) > 60:
        return False
    if slug != slug.lower():
        return False
    if not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]*[a-z0-9])?', slug):
        return False
    if slug in RESERVED_SLUGS:
        return False
    return True


def _make_unique(db, base: str) -> str:
    """Append -2, -3, … to `base` until an available slug is found. Caller
    must have already normalized + validated `base`."""
    candidate = base
    n = 2
    while True:
        existing = db.execute(
            text("SELECT 1 FROM vanity_urls WHERE slug = :s"),
            {"s": candidate},
        ).first()
        if not existing and candidate not in RESERVED_SLUGS:
            return candidate
        candidate = f"{base}-{n}"
        n += 1
        if n > 999:
            # Pathological — fall back to entity-id-stamped slug.
            import uuid
            return f"{base}-{uuid.uuid4().hex[:6]}"


def ensure_slug_for(db, entity_type: str, entity_id: int, name_hint: str = '') -> str:
    """Look up the vanity slug for an entity. If none exists, generate one
    from `name_hint` (or "{entity_type}{id}" fallback) and persist it.
    Used by the get-vanity-url endpoint and by the one-time backfill."""
    if entity_type not in ('artist', 'venue'):
        raise ValueError("entity_type must be 'artist' or 'venue'")
    row = db.execute(
        text("SELECT slug FROM vanity_urls WHERE entity_type=:t AND entity_id=:i"),
        {"t": entity_type, "i": entity_id},
    ).first()
    if row:
        return row[0]

    base = slugify(name_hint) or f"{entity_type}{entity_id}"
    if not is_valid_slug(base):
        # Trim further or fall back
        base = f"{entity_type}{entity_id}"
    slug = _make_unique(db, base)
    db.execute(
        text("""INSERT INTO vanity_urls (slug, entity_type, entity_id)
                VALUES (:s, :t, :i)"""),
        {"s": slug, "t": entity_type, "i": entity_id},
    )
    db.commit()
    return slug


# ─── Resolver route ─────────────────────────────────────────────────────────
# This MUST be registered last in main.py so it doesn't shadow /api/*, /app/*,
# /health, etc. The route returns the appropriate profile HTML with an
# inline <script> that tells the page which entity to load and how to keep
# the clean URL in the address bar.

# Cache the profile HTML in memory so we don't hit disk for every visit.
# Templates rarely change at runtime; on file edit the API restarts anyway.
_HTML_CACHE: dict = {}
APP_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'app')


def _load_profile_html(template_name: str) -> str:
    if template_name in _HTML_CACHE:
        return _HTML_CACHE[template_name]
    path = os.path.join(APP_DIR, template_name)
    with open(path, 'r', encoding='utf-8') as f:
        html = f.read()
    _HTML_CACHE[template_name] = html
    return html


def _inject_vanity_script(html: str, entity_type: str, entity_id: int, slug: str) -> str:
    """Prepend a small inline script inside <head> so the page's existing
    JS can read window._VANITY before it queries URLSearchParams. The page
    treats _VANITY as the highest-priority source for the entity id."""
    payload = (
        f'<script>'
        f'window._VANITY={{type:"{entity_type}",id:{int(entity_id)},'
        f'slug:"{slug}",path:"/{slug}"}};'
        f'</script>'
    )
    # Insert right after <head ...> opening tag so it runs before anything else.
    return re.sub(r'(<head[^>]*>)', r'\1' + payload, html, count=1, flags=re.IGNORECASE)


@router.get("/{slug}", response_class=HTMLResponse, include_in_schema=False)
def resolve_vanity(slug: str, request: Request, db=Depends(get_db)):
    """Catch-all resolver. Returns the profile HTML for a known slug,
    404s for unknown slugs (and reserved words). Registered LAST."""
    slug = slug.lower().strip()

    # Bail on reserved words and obviously-non-slug paths fast.
    if not slug or slug in RESERVED_SLUGS or '.' in slug:
        raise HTTPException(404)
    if not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]*[a-z0-9])?', slug):
        raise HTTPException(404)

    row = db.execute(
        text("SELECT entity_type, entity_id FROM vanity_urls WHERE slug = :s"),
        {"s": slug},
    ).first()
    if not row:
        raise HTTPException(404)

    entity_type, entity_id = row[0], row[1]
    template = 'artist-profile.html' if entity_type == 'artist' else 'venue-profile.html'
    try:
        html = _load_profile_html(template)
    except FileNotFoundError:
        raise HTTPException(500, f"profile template {template} missing")

    html = _inject_vanity_script(html, entity_type, entity_id, slug)
    return HTMLResponse(content=html)


# ─── Management endpoints ───────────────────────────────────────────────────


@router.get("/api/vanity/{entity_type}/{entity_id}")
def get_vanity_for_entity(entity_type: str, entity_id: int,
                          user=Depends(get_current_user),
                          db=Depends(get_db)):
    """Return the current vanity slug for an artist or venue, auto-
    generating one if the entity doesn't have one yet. Requires access
    to the entity (owner or entity_users member)."""
    if entity_type == 'artist':
        check_artist_access(db, entity_id, user.id)
        name = db.execute(text("SELECT name FROM artists WHERE id=:i"),
                          {"i": entity_id}).scalar() or ''
    elif entity_type == 'venue':
        check_venue_access(db, entity_id, user.id)
        name = db.execute(text("SELECT venue_name FROM venues WHERE id=:i"),
                          {"i": entity_id}).scalar() or ''
    else:
        raise HTTPException(400, "entity_type must be 'artist' or 'venue'")

    slug = ensure_slug_for(db, entity_type, entity_id, name_hint=name)
    return {
        "slug": slug,
        "url":  f"https://gigsfill.com/{slug}",
        "entity_type": entity_type,
        "entity_id": entity_id,
    }


@router.put("/api/vanity/{entity_type}/{entity_id}")
def update_vanity_for_entity(entity_type: str, entity_id: int,
                              payload: dict,
                              user=Depends(get_current_user),
                              db=Depends(get_db)):
    """Update an entity's vanity slug. Validates format + reserved words
    + collision against existing slugs (whether on artists or venues).
    Returns the new slug + full URL on success.

    Body: { "slug": "new-desired-slug" }
    """
    if entity_type not in ('artist', 'venue'):
        raise HTTPException(400, "entity_type must be 'artist' or 'venue'")
    if entity_type == 'artist':
        check_artist_access(db, entity_id, user.id)
    else:
        check_venue_access(db, entity_id, user.id)

    new_slug = (payload.get("slug") or "").strip().lower()
    if not is_valid_slug(new_slug):
        raise HTTPException(400,
            "Slug must be 2-60 characters, lowercase letters/numbers/hyphens "
            "only (no leading/trailing hyphen), and not a reserved word.")

    # Collision check — but allow re-saving an entity's CURRENT slug.
    existing = db.execute(
        text("""SELECT entity_type, entity_id FROM vanity_urls
                WHERE slug = :s"""), {"s": new_slug}).first()
    if existing and not (existing[0] == entity_type and existing[1] == entity_id):
        raise HTTPException(409, "That URL is already taken — pick another.")

    # Upsert. SQLite doesn't have a single-statement upsert that's pretty
    # across schemas, so DELETE the current row + INSERT the new one. The
    # PK is the slug, so deleting by entity_type+entity_id is correct.
    db.execute(text("""DELETE FROM vanity_urls
                       WHERE entity_type = :t AND entity_id = :i"""),
               {"t": entity_type, "i": entity_id})
    db.execute(text("""INSERT INTO vanity_urls (slug, entity_type, entity_id)
                       VALUES (:s, :t, :i)"""),
               {"s": new_slug, "t": entity_type, "i": entity_id})
    db.commit()

    return {
        "slug": new_slug,
        "url":  f"https://gigsfill.com/{new_slug}",
        "entity_type": entity_type,
        "entity_id": entity_id,
    }


@router.post("/api/vanity/check")
def check_slug_availability(payload: dict, db=Depends(get_db)):
    """Check whether a slug is available WITHOUT requiring auth. Used by
    the edit-profile UI to give live feedback while the user types.
    Body: { "slug": "candidate" }
    """
    candidate = (payload.get("slug") or "").strip().lower()
    if not is_valid_slug(candidate):
        return {"available": False, "reason": "invalid_format"}
    existing = db.execute(
        text("SELECT 1 FROM vanity_urls WHERE slug = :s"),
        {"s": candidate},
    ).first()
    if existing:
        return {"available": False, "reason": "taken"}
    return {"available": True}


# ─── One-time backfill ──────────────────────────────────────────────────────
def backfill_existing_entities(db) -> dict:
    """Walk every artist + venue, generate a slug if missing. Called once
    at startup from main.py. Idempotent — safe to run repeatedly."""
    inserted = {"artists": 0, "venues": 0}
    try:
        artist_rows = db.execute(text("""
            SELECT a.id, a.name FROM artists a
            WHERE NOT EXISTS (
                SELECT 1 FROM vanity_urls v
                WHERE v.entity_type='artist' AND v.entity_id = a.id
            )
        """)).fetchall()
        for aid, name in artist_rows:
            ensure_slug_for(db, 'artist', aid, name_hint=name or '')
            inserted["artists"] += 1
        venue_rows = db.execute(text("""
            SELECT v.id, v.venue_name FROM venues v
            WHERE NOT EXISTS (
                SELECT 1 FROM vanity_urls vu
                WHERE vu.entity_type='venue' AND vu.entity_id = v.id
            )
        """)).fetchall()
        for vid, name in venue_rows:
            ensure_slug_for(db, 'venue', vid, name_hint=name or '')
            inserted["venues"] += 1
    except Exception as e:
        import logging
        logging.getLogger("gigsfill.vanity").warning(
            f"vanity backfill failed (non-fatal): {e}", exc_info=True)
    return inserted
