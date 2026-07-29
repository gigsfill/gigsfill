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
from backend.rate_limiter import limiter
import logging
logger = logging.getLogger("gigsfill.vanity")

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
    # Audit fix (May 2026 part 6): affiliate / analytics tracking endpoints —
    # `/track/{code}` is the affiliate-link click tracker, `/stats/...` is
    # analytics. A vanity slug here would shadow those routes.
    'track', 'stats', 'r', 'ref', 'go', 'verify-email', 'reset-password',
    'forgot-password', 'unsubscribe', 'webhook', 'webhooks',
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


def maybe_update_slug_on_rename(db, entity_type: str, entity_id: int,
                                  old_name: str, new_name: str) -> None:
    """Auto-regenerate a vanity slug when an entity is renamed, ONLY IF
    the current stored slug matches the auto-generated slug from the OLD
    name. If the user customized the slug (e.g. "fridayspast" for a venue
    named "Fridays Past Bar & Grill"), we leave it — respecting the
    customization. Otherwise we update the slug to match the new name
    and register the old slug in `vanity_url_redirects` for 90 days so
    previously-shared links keep working.

    Called from venues.py `update_venue` and artists.py `update_artist`.

    Best-effort — any failure logs and continues; the rename itself
    is already committed at call time and shouldn't be rolled back
    just because vanity juggling failed.
    """
    try:
        if entity_type not in ('artist', 'venue'):
            return
        if not old_name or not new_name or old_name == new_name:
            return

        auto_old = slugify(old_name)
        auto_new = slugify(new_name)
        if not auto_new:
            return  # New name doesn't yield a valid slug; leave old one alone.

        cur = db.execute(
            text("SELECT slug FROM vanity_urls WHERE entity_type=:t AND entity_id=:i"),
            {"t": entity_type, "i": entity_id}
        ).first()
        if not cur:
            return  # No slug on record — nothing to migrate.
        current_slug = cur[0]

        # Only auto-rename if the user never customized. `auto_old` may
        # differ from `current_slug` if they picked a custom slug earlier.
        if current_slug != auto_old:
            return

        # Get a unique new slug (may need a suffix if new-name-derived
        # slug is already claimed by someone else).
        new_slug = _make_unique(db, auto_new)
        if new_slug == current_slug:
            return  # No change needed.

        db.execute(text("""
            UPDATE vanity_urls SET slug = :s, updated_at = CURRENT_TIMESTAMP
            WHERE entity_type = :t AND entity_id = :i
        """), {"s": new_slug, "t": entity_type, "i": entity_id})
        # Cleanup stale redirects for the slug we're now claiming as
        # live. If a user renames foo → bar → foo, the middle rename
        # parked `foo → bar` in the redirect table; now that `foo` is
        # live again we must delete that stale row so nothing tries
        # to point away from it (the resolver skips stale redirects
        # for live slugs anyway, but leaving them accrues cruft).
        try:
            db.execute(
                text("DELETE FROM vanity_url_redirects WHERE old_slug = :s"),
                {"s": new_slug}
            )
        except Exception:
            pass
        # Park the old slug as a 90-day redirect so pre-rename links resolve.
        try:
            db.execute(text("""
                INSERT OR REPLACE INTO vanity_url_redirects
                    (old_slug, new_slug, entity_type, entity_id, expires_at, reclaim_after)
                VALUES (:old, :new, :t, :i,
                        datetime('now', '+90 days'), datetime('now', '+120 days'))
            """), {"old": current_slug, "new": new_slug,
                   "t": entity_type, "i": entity_id})
        except Exception as _re:
            logger.warning(f"vanity redirect park on rename failed for {current_slug}→{new_slug}: {_re}")
        db.commit()
        logger.info(f"[VANITY] Auto-renamed {entity_type} #{entity_id} slug: {current_slug} → {new_slug}")
    except Exception as e:
        logger.warning(f"maybe_update_slug_on_rename({entity_type}, {entity_id}, {old_name!r}→{new_name!r}) failed: {e}")


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
# Cache keyed by template name → (mtime_ns, contents). Re-reads the file from
# disk only when the mtime changed since the cached entry was loaded.
#
# Previously this was a plain `{name: contents}` dict that only invalidated on
# API restart. That meant editing artist-profile.html / venue-profile.html in
# production wouldn't take effect on gigsfill.com/<slug> until someone ran
# `sudo systemctl restart gigsfill` — and there was no error message to
# explain why a fresh edit looked stale. Causing real bugs (audio captions
# not appearing, track-order changes not reflecting) that LOOKED like
# browser-side caching but were actually server-side. The mtime check is one
# stat() per request, cheaper than the file read it avoids.
_HTML_CACHE: dict = {}  # template_name -> (mtime_ns, contents)
APP_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'app')


def _load_profile_html(template_name: str) -> str:
    path = os.path.join(APP_DIR, template_name)
    try:
        mtime = os.stat(path).st_mtime_ns
    except FileNotFoundError:
        # Fall through to open() below so the existing FileNotFoundError
        # surfaces with the same message it always did.
        mtime = None
    cached = _HTML_CACHE.get(template_name)
    if cached and mtime is not None and cached[0] == mtime:
        return cached[1]
    with open(path, 'r', encoding='utf-8') as f:
        html = f.read()
    if mtime is not None:
        _HTML_CACHE[template_name] = (mtime, html)
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


# ─── Open Graph / Twitter card injection ────────────────────────────────────
# Scrapers (Facebook, iMessage, Slack, Twitter) don't run JavaScript, so we
# need real og:/twitter: meta tags in the SERVED HTML — not just placeholders
# the page's JS updates after fetch. We replace the generic ones the template
# ships with so each vanity page previews with its own name + image.

_PUBLIC_HOST = "https://gigsfill.com"
_DEFAULT_OG_IMAGE = _PUBLIC_HOST + "/app/static/img/gigsfill-logo_square.png"


def _attr_escape(s) -> str:
    import html as html_lib
    return html_lib.escape(str(s or ''), quote=True)


def _inject_meta_tags(html: str, *, title: str, description: str,
                      image_url: str, page_url: str, og_type: str = "profile") -> str:
    """Strip the template's generic og:*/twitter:* meta tags and insert a
    personalized block right after <head>. og_type is 'profile' for an
    artist/venue, 'website' for a city listing page."""
    block = (
        f'<meta property="og:type" content="{_attr_escape(og_type)}">'
        f'<meta property="og:title" content="{_attr_escape(title)}">'
        f'<meta property="og:description" content="{_attr_escape(description)}">'
        f'<meta property="og:image" content="{_attr_escape(image_url)}">'
        f'<meta property="og:url" content="{_attr_escape(page_url)}">'
        f'<meta property="og:site_name" content="GigsFill">'
        f'<meta name="twitter:card" content="summary_large_image">'
        f'<meta name="twitter:title" content="{_attr_escape(title)}">'
        f'<meta name="twitter:description" content="{_attr_escape(description)}">'
        f'<meta name="twitter:image" content="{_attr_escape(image_url)}">'
    )
    cleaned = re.sub(
        r'<meta\s+(?:property|name)="(?:og:[a-z_]+|twitter:[a-z_]+)"[^>]*>\s*\n?',
        '', html, flags=re.IGNORECASE)
    return re.sub(r'(<head[^>]*>)', r'\1' + block, cleaned,
                  count=1, flags=re.IGNORECASE)


def _profile_image_url(db, entity_type: str, entity_id: int) -> str:
    """Resolve the entity's profile image to an absolute URL. Falls back
    to the GigsFill logo so previews still have an image either way."""
    table = 'artist_media' if entity_type == 'artist' else 'venue_media'
    col = 'artist_id' if entity_type == 'artist' else 'venue_id'
    try:
        row = db.execute(
            text(f"""SELECT file_path FROM {table}
                     WHERE {col} = :i AND media_type = 'profile'
                     ORDER BY id DESC LIMIT 1"""),
            {"i": entity_id},
        ).first()
    except Exception:
        row = None
    if row and row[0]:
        path = row[0]
        if path.startswith('http'):
            return path
        if not path.startswith('/'):
            path = '/' + path
        return _PUBLIC_HOST + path
    return _DEFAULT_OG_IMAGE


def _truncate(s: str, n: int) -> str:
    s = (s or '').strip()
    if len(s) <= n:
        return s
    return s[:n - 1].rstrip() + '…'


def _inject_city_script(html: str, city: str, slug: str) -> str:
    r"""City variant of the VANITY injector. Serves public-gigs.html with the
    target city pre-loaded so the page filters to that city automatically.

    Audit fix (May 2026 part 7): `json.dumps()` does NOT escape `</script>` —
    a venue whose `city` is set to `</script><script>alert(1)//` would inject
    executable JS into every city-page visitor's browser. Replace `</` with
    `<\/` after JSON encoding so the closing tag can't appear inside the
    inline script context.
    """
    import json
    _city_json = json.dumps(city).replace("</", "<\\/")
    _slug_json = json.dumps(slug).replace("</", "<\\/")
    payload = (
        f'<script>window._VANITY={{type:"city",'
        f'city:{_city_json},slug:{_slug_json},path:"/{slug}"}};</script>'
    )
    return re.sub(r'(<head[^>]*>)', r'\1' + payload, html, count=1, flags=re.IGNORECASE)


def _find_city_for_slug(db, slug: str) -> Optional[str]:
    """Resolve a slug to a canonical city name. Looks first at cities our
    venues actually live in (so gigsfill.com/thousandoaks works exactly),
    then falls back to the full US city DB so visitors typing a real city
    we just don't have venues in yet still land on a tidy filter page
    (showing "no gigs in Spokane yet" beats showing a 404)."""
    # 1) Venue cities — match exactly so e.g. "Saint Paul" wins over "St Paul"
    #    if both are typed when GigsFill venues use the former spelling.
    for city, _city_slug in _all_city_slug_pairs(db):
        if _city_slug == slug:
            return city
    # 2) Fall back to the US cities catalog.
    return _US_SLUG_TO_CITY.get(slug)


def _build_us_slug_index():
    """One-time index of slugified US city name → canonical city name."""
    try:
        from backend.us_cities import US_CITIES
    except Exception:
        return {}
    out = {}
    for c in US_CITIES:
        name = c.get("city")
        if not name:
            continue
        s = slugify(name)
        if s and s not in out:  # first occurrence wins (largest city by list order)
            out[s] = name
    return out


_US_SLUG_TO_CITY = _build_us_slug_index()


def _all_city_slug_pairs(db):
    """List of (canonical_city, slug) pairs for all distinct venue cities.
    Empty list on DB error — callers can treat that as 'no suggestions'."""
    try:
        rows = db.execute(text("""
            SELECT DISTINCT city FROM venues WHERE city IS NOT NULL AND city != ''
        """)).fetchall()
    except Exception:
        return []
    pairs = []
    for (city,) in rows:
        if city:
            cs = slugify(city)
            if cs:
                pairs.append((city, cs))
    return pairs


def _suggest_for_unknown_slug(db, slug: str):
    """Return a (kind, label, href) tuple suggesting a likely match for an
    unknown slug — or None if nothing similar enough.

    kind  ∈ {'city', 'profile'}
    label = display string ('Thousand Oaks' or 'Fridays Past')
    href  = where to send the user ('/thousandoaks' or '/fridayspast')

    Uses difflib for fuzzy match. Cutoff 0.72 — high enough that "/oxnar"
    suggests "Oxnard" but a random slug like /foobarnone won't pull up
    a tenuous match."""
    import difflib

    candidates = []  # (kind, label, candidate_slug)
    # Cities first — they're the most common public-facing slug.
    for city, city_slug in _all_city_slug_pairs(db):
        candidates.append(('city', city, city_slug))
    # Then artist/venue slugs from vanity_urls.
    try:
        rows = db.execute(text("""
            SELECT v.slug, v.entity_type, v.entity_id,
                   CASE WHEN v.entity_type='artist'
                        THEN (SELECT name FROM artists WHERE id = v.entity_id)
                        ELSE (SELECT venue_name FROM venues WHERE id = v.entity_id)
                   END AS label
            FROM vanity_urls v
        """)).fetchall()
        for slug_row, _etype, _eid, label in rows:
            candidates.append(('profile', label or slug_row, slug_row))
    except Exception:
        pass

    if not candidates:
        return None
    all_slugs = [c[2] for c in candidates]
    match = difflib.get_close_matches(slug, all_slugs, n=1, cutoff=0.72)
    if not match:
        return None
    matched_slug = match[0]
    for kind, label, cand_slug in candidates:
        if cand_slug == matched_slug:
            return (kind, label, f'/{cand_slug}')
    return None


def _render_not_found(db, slug: str, original_input: Optional[str] = None) -> HTMLResponse:
    """Branded 404 page. Tries to suggest a near-match (city or profile);
    always shows the city search field so visitors land somewhere useful.
    Returns HTTP 404 so crawlers still see a not-found status."""
    import html as html_lib
    try:
        template = _load_profile_html('not-found.html')
    except FileNotFoundError:
        return HTMLResponse(status_code=404, content="<h1>Not found</h1>")

    label_for_user = original_input or slug
    safe_label = html_lib.escape(label_for_user)

    headline = "Sorry, we couldn't find that page."
    message = (
        f'"<span class="nf-slug">{safe_label}</span>" isn\'t a GigsFill artist, '
        'venue, or city we know about.'
    )

    suggestion_html = ''
    suggestion = _suggest_for_unknown_slug(db, slug)
    if suggestion:
        kind, label, href = suggestion
        safe_lbl = html_lib.escape(label)
        safe_href = html_lib.escape(href)
        if kind == 'city':
            suggestion_html = (
                f'<div class="nf-suggestion">'
                f'Did you mean <a href="{safe_href}">{safe_lbl}</a>?'
                f'</div>'
            )
        else:
            suggestion_html = (
                f'<div class="nf-suggestion">'
                f'Looking for <a href="{safe_href}">{safe_lbl}</a>?'
                f'</div>'
            )

    rendered = (template
                .replace('{{HEADLINE}}', headline)
                .replace('{{MESSAGE_HTML}}', message)
                .replace('{{SUGGESTION_HTML}}', suggestion_html))
    return HTMLResponse(status_code=404, content=rendered)


# ─── Closest-city suggestion from IP ────────────────────────────────────────
# Powers the splash-page autofill so visitors don't have to type their city.
# Uses ipapi.co (no key required, ~30k/month free) with a small in-process
# cache so repeated visits from the same IP don't burn the quota. Anything
# that fails returns {ok:false} — the frontend just doesn't autofill.

_IP_CITY_CACHE: dict = {}  # ip → (timestamp, payload)
_IP_CITY_TTL_SECONDS = 24 * 3600


def _haversine_miles(lat1, lon1, lat2, lon2):
    import math
    R = 3958.7613  # Earth radius in miles
    dLat = math.radians(lat2 - lat1)
    dLon = math.radians(lon2 - lon1)
    a = (math.sin(dLat / 2) ** 2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
         math.sin(dLon / 2) ** 2)
    return 2 * R * math.asin(math.sqrt(a))


def _client_ip(request: Request) -> Optional[str]:
    """Best-effort client IP. nginx forwards via X-Forwarded-For; the first
    entry is the real client (later hops are proxies)."""
    xff = request.headers.get('x-forwarded-for') or ''
    if xff:
        first = xff.split(',')[0].strip()
        if first:
            return first
    if request.client and request.client.host:
        return request.client.host
    return None


def _is_routable_ip(ip: str) -> bool:
    """Skip private / loopback / reserved IPs — they can't be geolocated."""
    try:
        import ipaddress
        addr = ipaddress.ip_address(ip)
        return addr.is_global
    except Exception:
        return False


@router.get("/api/geo/suggest-city", include_in_schema=False)
def suggest_city(request: Request):
    """Look up the requester's IP, then return the closest US city we know
    about (from us_cities.US_CITIES) so the splash page can pre-fill the
    'Enter city' field. Always returns 200 — failures surface as ok:false.

    Honors Do-Not-Track / Global Privacy Control signals: if either header
    is set, we skip the geolocation entirely. This is disclosed in the
    privacy section of /app/legal.html."""
    import time
    if (request.headers.get('dnt') == '1' or
            request.headers.get('sec-gpc') == '1'):
        return {"ok": False, "reason": "user_opted_out"}
    ip = _client_ip(request)
    if not ip or not _is_routable_ip(ip):
        return {"ok": False, "reason": "ip_not_routable"}

    cached = _IP_CITY_CACHE.get(ip)
    if cached and (time.time() - cached[0]) < _IP_CITY_TTL_SECONDS:
        return cached[1]

    # Look up geolocation. ipapi.co is keyless; short timeout so a slow
    # third party doesn't keep the splash spinning.
    try:
        import requests as _requests
        r = _requests.get(f"https://ipapi.co/{ip}/json/", timeout=1.5,
                          headers={"User-Agent": "GigsFill/1.0"})
        if r.status_code != 200:
            payload = {"ok": False, "reason": "geo_lookup_failed"}
            _IP_CITY_CACHE[ip] = (time.time(), payload)
            return payload
        data = r.json()
        lat = data.get("latitude")
        lon = data.get("longitude")
        if lat is None or lon is None:
            payload = {"ok": False, "reason": "no_coords"}
            _IP_CITY_CACHE[ip] = (time.time(), payload)
            return payload
    except Exception:
        return {"ok": False, "reason": "geo_lookup_error"}

    # Find the closest US_CITIES entry to (lat, lon).
    try:
        from backend.us_cities import US_CITIES
    except Exception:
        return {"ok": False, "reason": "no_city_db"}

    closest = None
    closest_miles = None
    for c in US_CITIES:
        try:
            miles = _haversine_miles(lat, lon, c["lat"], c["lon"])
        except Exception:
            continue
        if closest_miles is None or miles < closest_miles:
            closest_miles = miles
            closest = c

    if not closest or closest_miles is None or closest_miles > 50:
        payload = {"ok": False, "reason": "no_nearby_city"}
        _IP_CITY_CACHE[ip] = (time.time(), payload)
        return payload

    payload = {
        "ok": True,
        "city": closest["city"],
        "state": closest["state"],
        "lat": closest["lat"],
        "lon": closest["lon"],
        "miles": round(closest_miles, 1),
    }
    _IP_CITY_CACHE[ip] = (time.time(), payload)
    return payload


# ─── sitemap.xml (dynamic) ──────────────────────────────────────────────────
# robots.txt continues to be served by main.py from the static file. The
# sitemap is generated on the fly here so newly-created artist / venue /
# city slugs flow into Google indexing without a manual file edit.

from fastapi.responses import Response


@router.get("/sitemap.xml", include_in_schema=False)
def sitemap_xml(db=Depends(get_db)):
    """Build a sitemap from vanity_urls + distinct venue cities. Public-only
    pages — admin / auth-gated pages stay out."""
    urls = [
        (f"{_PUBLIC_HOST}/", "weekly", "1.0"),
        (f"{_PUBLIC_HOST}/app/legal.html", "monthly", "0.3"),
    ]
    try:
        rows = db.execute(
            text("SELECT slug FROM vanity_urls ORDER BY slug")
        ).fetchall()
        for (slug_row,) in rows:
            urls.append((f"{_PUBLIC_HOST}/{slug_row}", "weekly", "0.8"))
    except Exception:
        pass
    seen = set(u[0] for u in urls)
    for _city, city_slug in _all_city_slug_pairs(db):
        u = f"{_PUBLIC_HOST}/{city_slug}"
        if u not in seen:
            urls.append((u, "daily", "0.7"))
            seen.add(u)

    parts = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for loc, freq, pri in urls:
        parts.append(
            f'<url><loc>{_attr_escape(loc)}</loc>'
            f'<changefreq>{freq}</changefreq>'
            f'<priority>{pri}</priority></url>'
        )
    parts.append('</urlset>')
    return Response(content='\n'.join(parts), media_type='application/xml')


@router.get("/{slug}", response_class=HTMLResponse, include_in_schema=False)
def resolve_vanity(slug: str, request: Request, db=Depends(get_db)):
    """Catch-all resolver. Returns the profile HTML for a known slug,
    a branded not-found page for unknown slugs. Registered LAST."""
    original_input = slug
    slug = slug.lower().strip()

    # Bail on reserved words and obviously-non-slug paths fast — these are
    # routing collisions we never want to override (api/, app/, *.json, etc.).
    if not slug or slug in RESERVED_SLUGS or '.' in slug:
        raise HTTPException(404)
    if not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]*[a-z0-9])?', slug):
        raise HTTPException(404)

    # Jul 22 2026 order fix: check the LIVE vanity_urls table FIRST.
    # Only fall through to the redirect table if the requested slug
    # isn't currently active. Otherwise a user who renames back and
    # forth (foo → bar → foo) leaves a stale `foo → bar` redirect
    # that the resolver would follow instead of serving the live
    # `foo` entity. Previously the order was reversed (redirects
    # first) which caused exactly that bug — the "your public URL
    # is broken after rename" report.
    row = db.execute(
        text("SELECT entity_type, entity_id FROM vanity_urls WHERE slug = :s"),
        {"s": slug},
    ).first()

    # Audit fix (May 2026 part 10): if the user renamed their vanity URL,
    # keep the old slug working as a 301 redirect for 90 days. Without this,
    # every previously-shared link (social-media post, business card, email)
    # 404s the moment the user picks a new slug. Runs only when the slug
    # isn't currently live — a live slug always wins over a stale redirect.
    if not row:
        redirect_row = None
        try:
            redirect_row = db.execute(
                text("""SELECT new_slug FROM vanity_url_redirects
                        WHERE old_slug = :s AND expires_at > datetime('now')
                        LIMIT 1"""),
                {"s": slug},
            ).first()
        except Exception:
            pass
        if redirect_row:
            from fastapi.responses import RedirectResponse
            return RedirectResponse(f"/{redirect_row[0]}", status_code=301)
    if row:
        entity_type, entity_id = row[0], row[1]
        # Audit fix (May 2026 part 5): defense-in-depth — confirm the underlying
        # entity still exists. Even though we now clean vanity_urls on entity
        # delete (see me.py / artists.py / venues.py), older orphan rows may
        # exist in production. Fall through to not-found so a deleted artist's
        # old slug doesn't render an empty profile.
        _entity_table = "artists" if entity_type == "artist" else "venues"
        # BUG FIX (Jul 2026 audit): also require entity NOT tombstoned. Without
        # this, a race where the vanity_urls DELETE swallowed an error during
        # entity_delete could leave a resurrected slug pointing at a "[Deleted]
        # X" profile that renders fully to the public.
        _entity_exists = db.execute(
            text(f"SELECT 1 FROM {_entity_table} WHERE id = :i AND deleted_at IS NULL"),
            {"i": entity_id}
        ).first()
        if not _entity_exists:
            try:
                db.execute(text("DELETE FROM vanity_urls WHERE slug = :s"), {"s": slug})
                db.commit()
            except Exception:
                pass
            raise HTTPException(404)
        template = 'artist-profile.html' if entity_type == 'artist' else 'venue-profile.html'
        try:
            html = _load_profile_html(template)
        except FileNotFoundError:
            raise HTTPException(500, f"profile template {template} missing")
        # Pull name + short bio so the social card previews with real values
        # instead of "Artist Profile – GigsFill".
        if entity_type == 'artist':
            meta_row = db.execute(text(
                "SELECT name, bio, city, state FROM artists WHERE id=:i"
            ), {"i": entity_id}).first()
            name_disp = (meta_row[0] if meta_row else None) or "Artist"
            bio = (meta_row[1] if meta_row else '') or ''
            loc = ', '.join([p for p in [(meta_row[2] if meta_row else ''),
                                          (meta_row[3] if meta_row else '')] if p])
            title = f"{name_disp} · GigsFill"
            desc = _truncate(bio, 180) or (
                f"{name_disp} — live music artist on GigsFill"
                + (f" ({loc})" if loc else "")
            )
        else:
            meta_row = db.execute(text(
                "SELECT venue_name, description, city, state FROM venues WHERE id=:i"
            ), {"i": entity_id}).first()
            name_disp = (meta_row[0] if meta_row else None) or "Venue"
            descr = (meta_row[1] if meta_row else '') or ''
            loc = ', '.join([p for p in [(meta_row[2] if meta_row else ''),
                                          (meta_row[3] if meta_row else '')] if p])
            title = f"{name_disp} · GigsFill"
            desc = _truncate(descr, 180) or (
                f"{name_disp} — live music venue on GigsFill"
                + (f" ({loc})" if loc else "")
            )
        html = _inject_meta_tags(
            html,
            title=title, description=desc,
            image_url=_profile_image_url(db, entity_type, entity_id),
            page_url=f"{_PUBLIC_HOST}/{slug}",
            og_type='profile',
        )
        html = _inject_vanity_script(html, entity_type, entity_id, slug)
        return HTMLResponse(content=html)

    # No artist/venue match — try city slugs against distinct venue cities.
    # gigsfill.com/thousandoaks → public-gigs.html?city=Thousand Oaks
    city = _find_city_for_slug(db, slug)
    if city:
        try:
            html = _load_profile_html('public-gigs.html')
        except FileNotFoundError:
            raise HTTPException(500, "public-gigs template missing")
        html = _inject_meta_tags(
            html,
            title=f"Live music in {city} · GigsFill",
            description=f"Find local gigs, venues, and artists in {city} on GigsFill.",
            image_url=_DEFAULT_OG_IMAGE,
            page_url=f"{_PUBLIC_HOST}/{slug}",
            og_type='website',
        )
        html = _inject_city_script(html, city, slug)
        return HTMLResponse(content=html)

    return _render_not_found(db, slug, original_input=original_input)


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

    # Audit fix (May 2026 part 10): block re-claim of recently-freed slugs
    # within the 30-day cooldown to prevent reputation hijack. A user renames
    # `taylorswift` to `tsofficial`; the old slug is parked here. Attackers
    # can't immediately swoop in and claim `taylorswift` to redirect followers
    # to a clone profile.
    try:
        redir = db.execute(text("""
            SELECT entity_type, entity_id FROM vanity_url_redirects
            WHERE old_slug = :s AND reclaim_after > datetime('now')
        """), {"s": new_slug}).first()
        if redir and not (redir[0] == entity_type and redir[1] == entity_id):
            raise HTTPException(409,
                "That URL was recently used by another account and is in a 30-day cooldown. Try a different slug.")
    except HTTPException:
        raise
    except Exception:
        pass

    # Capture the entity's CURRENT slug so we can park it as a redirect.
    current_row = db.execute(
        text("""SELECT slug FROM vanity_urls
                WHERE entity_type = :t AND entity_id = :i"""),
        {"t": entity_type, "i": entity_id}
    ).first()
    current_slug = current_row[0] if current_row else None

    # Audit fix (May 2026 part 10): atomic UPDATE on rename instead of DELETE
    # + INSERT. Catch IntegrityError → 409 (race-loser when two PUTs land at
    # the same moment). Park the old slug as a 90-day redirect with a 30-day
    # reclaim cooldown.
    try:
        if current_slug == new_slug:
            # No-op rename — same slug as before
            db.commit()
        elif current_slug:
            # UPDATE the existing row's slug (single atomic statement).
            db.execute(text("""
                UPDATE vanity_urls SET slug = :s, updated_at = CURRENT_TIMESTAMP
                WHERE entity_type = :t AND entity_id = :i
            """), {"s": new_slug, "t": entity_type, "i": entity_id})
            # Jul 22 2026: clear any stale redirect that points AWAY
            # from the slug we're now claiming (see the comment on the
            # matching cleanup in maybe_update_slug_on_rename above).
            try:
                db.execute(
                    text("DELETE FROM vanity_url_redirects WHERE old_slug = :s"),
                    {"s": new_slug}
                )
            except Exception:
                pass
            # Park the old slug as a redirect
            try:
                db.execute(text("""
                    INSERT OR REPLACE INTO vanity_url_redirects
                        (old_slug, new_slug, entity_type, entity_id, expires_at, reclaim_after)
                    VALUES (:old, :new, :t, :i,
                            datetime('now', '+90 days'), datetime('now', '+120 days'))
                """), {"old": current_slug, "new": new_slug,
                       "t": entity_type, "i": entity_id})
            except Exception as _re:
                logger.warning(f"vanity redirect park failed for {current_slug}→{new_slug}: {_re}")
            db.commit()
        else:
            db.execute(text("""INSERT INTO vanity_urls (slug, entity_type, entity_id)
                               VALUES (:s, :t, :i)"""),
                       {"s": new_slug, "t": entity_type, "i": entity_id})
            db.commit()
    except Exception as e:
        db.rollback()
        msg = str(e).lower()
        if "unique" in msg or "constraint" in msg or "integrity" in msg:
            raise HTTPException(409, "That URL was just claimed by another user. Pick another.")
        raise

    # Audit log (best-effort)
    try:
        from backend.utils import log_admin_action
        # Not an admin action per se, but use the same audit table for slug changes.
        # Falls back silently if helper is admin-only.
        pass  # admin_audit_log is admin-actor only; we'd need a separate user_audit_log
    except Exception:
        pass

    return {
        "slug": new_slug,
        "url":  f"https://gigsfill.com/{new_slug}",
        "entity_type": entity_type,
        "entity_id": entity_id,
    }


@router.get("/api/vanity-lookup/{entity_type}/{entity_id}")
def lookup_vanity_slug(entity_type: str, entity_id: int, db=Depends(get_db)):
    """Public, unauthenticated read of an entity's vanity slug. Used by
    the calendar hover-card to turn artist names into vanity URL links
    without forcing the visitor through the access-checked /api/vanity/...
    endpoint (which is owner-only). Returns 404 if not found."""
    if entity_type not in ('artist', 'venue'):
        raise HTTPException(400, "entity_type must be 'artist' or 'venue'")
    row = db.execute(
        text("""SELECT slug FROM vanity_urls
                WHERE entity_type=:t AND entity_id=:i"""),
        {"t": entity_type, "i": entity_id},
    ).first()
    if not row:
        raise HTTPException(404, "no slug for this entity")
    return {
        "slug": row[0],
        "url":  f"/{row[0]}",
        "entity_type": entity_type,
        "entity_id": entity_id,
    }


@router.post("/api/vanity/check")
@limiter.limit("30/minute")
def check_slug_availability(request: Request, payload: dict, db=Depends(get_db)):
    """Check whether a slug is available WITHOUT requiring auth. Used by
    the edit-profile UI to give live feedback while the user types.
    Body: { "slug": "candidate" }

    Audit fix (May 2026 part 10):
      - Rate-limited 30/min/IP. Previously an attacker could enumerate the
        entire claimed-slug namespace at SMTP-free speed (and learn whether
        any brand was on the platform).
      - Returns 'reserved' separately from 'invalid_format' so the UI can
        give clearer feedback ("that name is reserved by GigsFill").
      - Also checks the redirect cooldown table.
    """
    candidate = (payload.get("slug") or "").strip().lower()
    if not candidate:
        return {"available": False, "reason": "invalid_format"}
    if candidate in RESERVED_SLUGS:
        return {"available": False, "reason": "reserved"}
    if not is_valid_slug(candidate):
        return {"available": False, "reason": "invalid_format"}
    existing = db.execute(
        text("SELECT 1 FROM vanity_urls WHERE slug = :s"),
        {"s": candidate},
    ).first()
    if existing:
        return {"available": False, "reason": "taken"}
    # Cooldown lockout
    try:
        cooldown = db.execute(text("""
            SELECT 1 FROM vanity_url_redirects
            WHERE old_slug = :s AND reclaim_after > datetime('now')
        """), {"s": candidate}).first()
        if cooldown:
            return {"available": False, "reason": "cooldown"}
    except Exception:
        pass
    return {"available": True}


# ─── One-time backfill ──────────────────────────────────────────────────────
def backfill_existing_entities(db) -> dict:
    """Walk every artist + venue, generate a slug if missing. Called once
    at startup from main.py. Idempotent — safe to run repeatedly."""
    inserted = {"artists": 0, "venues": 0}
    try:
        # BUG FIX (Jul 2026 audit): filter out tombstoned rows.
        # entity_delete DELETEs the vanity_urls row when an entity is
        # tombstoned, so without this filter every startup would re-slug
        # every tombstone and resurrect their "[Deleted] X" profile at a
        # public URL. deleted_at IS NULL keeps the backfill on live rows only.
        artist_rows = db.execute(text("""
            SELECT a.id, a.name FROM artists a
            WHERE a.deleted_at IS NULL
              AND NOT EXISTS (
                SELECT 1 FROM vanity_urls v
                WHERE v.entity_type='artist' AND v.entity_id = a.id
              )
        """)).fetchall()
        for aid, name in artist_rows:
            ensure_slug_for(db, 'artist', aid, name_hint=name or '')
            inserted["artists"] += 1
        venue_rows = db.execute(text("""
            SELECT v.id, v.venue_name FROM venues v
            WHERE v.deleted_at IS NULL
              AND NOT EXISTS (
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
