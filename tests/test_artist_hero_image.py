"""Artist-chosen hero image.

The profile hero used to be "whatever picture happens to be first", which
for the first band we looked at meant a dark backstage shot while a far
better daylight photo sat further down their gallery. Artists now pick it.

The security-relevant part is that `hero_media_id` is a client-supplied id
pointing at a row in `artist_media`. Without an ownership check, a crafted
PUT could set one artist's hero to another artist's upload — publishing
someone else's image on a page they don't control.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ARTISTS = ROOT / "backend" / "routes" / "artists.py"


def test_hero_media_id_is_ownership_checked():
    src = ARTISTS.read_text()
    idx = src.index("_hero_media_id = None")
    block = src[idx:idx + 1600]
    # Must verify the row belongs to this artist, not merely that it exists.
    assert "FROM artist_media" in block
    assert "artist_id = :aid" in block
    assert "id = :mid" in block
    assert "does not belong to this artist" in block


def test_hero_media_id_rejects_non_numeric():
    src = ARTISTS.read_text()
    idx = src.index("_hero_media_id = None")
    block = src[idx:idx + 1600]
    assert "must be a number" in block


def test_hero_can_be_cleared():
    """COALESCE keeps the existing value when the bind is NULL, so clearing
    needs its own UPDATE — otherwise a venue could set a cover and never
    get back to the default."""
    src = ARTISTS.read_text()
    idx = src.index("_hero_media_id = None")
    block = src[idx:idx + 1600]
    assert "SET hero_media_id = NULL" in block


def test_hero_media_id_in_schema_and_orm():
    """CLAUDE.md: db.py and models.py are kept in sync by hand."""
    assert "hero_media_id" in (ROOT / "backend" / "db.py").read_text()
    assert "hero_media_id" in (ROOT / "backend" / "models.py").read_text()


def test_public_artist_endpoint_exposes_hero_media_id():
    """The profile can't honour the choice if the field never reaches it."""
    src = ARTISTS.read_text()
    idx = src.index('WHERE id=:id\n              AND deleted_at IS NULL')
    assert "hero_media_id" in src[max(0, idx - 900):idx]


def test_profile_falls_back_when_no_hero_chosen():
    """Most artists won't set one, so the fallback chain has to hold:
    chosen -> first picture -> profile image -> gradient."""
    js = (ROOT / "app" / "static" / "js" / "artist-profile-v2.js").read_text()
    idx = js.index("var chosen = null;")
    block = js[idx:idx + 700]
    assert "a.hero_media_id" in block
    assert "pics[0]" in block
    assert "profile.file_path" in block


def test_profile_hero_only_uses_this_artists_media():
    """The chosen id is matched against the media list already fetched for
    this artist, so a stale or foreign id can't pull in another image."""
    js = (ROOT / "app" / "static" / "js" / "artist-profile-v2.js").read_text()
    idx = js.index("var chosen = null;")
    block = js[idx:idx + 700]
    assert "media.filter" in block


# ── focal point ─────────────────────────────────────────────────────

def test_focal_values_are_clamped_not_rejected():
    """These come from a drag, so a value a hair past the edge is a UI
    rounding artefact rather than an attack. Clamp, don't 400."""
    src = ARTISTS.read_text()
    idx = src.index("def _focal(key):")
    block = src[idx:idx + 900]
    assert "max(0.0, min(100.0" in block
    assert "must be a number" in block     # non-numeric still rejected


def test_focal_columns_in_schema_and_orm():
    for col in ("hero_focal_x", "hero_focal_y"):
        assert col in (ROOT / "backend" / "db.py").read_text(), col
        assert col in (ROOT / "backend" / "models.py").read_text(), col


def test_focal_defaults_to_centre_when_unset():
    """Most artists will never touch this, so NULL must render as centred
    rather than as 0% (which would pin every hero to the top-left)."""
    js = (ROOT / "app" / "static" / "js" / "artist-profile-v2.js").read_text()
    idx = js.index("hero_focal_x == null")
    block = js[idx - 200:idx + 400]
    assert "? 50 :" in block
    assert "backgroundPosition" in block


def test_drag_saves_are_debounced():
    """A drag fires continuously; one PUT per mousemove would hammer the API."""
    js = (ROOT / "app" / "static" / "js" / "artist.edit.js").read_text()
    idx = js.index("function save() {")
    block = js[idx:idx + 500]
    assert "clearTimeout(saveTimer)" in block
    assert "setTimeout" in block


def test_framer_preview_matches_the_hero_aspect():
    """If the preview isn't the hero's shape, framing it there tells the
    artist nothing about what actually renders."""
    html = (ROOT / "app" / "artist-edit.html").read_text()
    idx = html.index("#coverPreview {")
    block = html[idx:idx + 900]          # comment pushed the rule down
    assert "aspect-ratio: 64 / 21" in block

    # And it must be the SAME ratio the hero uses, or the preview shows a
    # crop the visitor never sees.
    profile = (ROOT / "app" / "artist-profile.html").read_text()
    hidx = profile.index(".v2-hero {")
    assert "aspect-ratio: 64 / 21" in profile[hidx:hidx + 700]


def test_hero_has_no_background_zoom():
    """A scale() on the hero background crops every edge, so whatever the
    artist frames on the edit page is not what renders."""
    for page in ("artist-profile.html", "venue-profile.html"):
        src = (ROOT / "app" / page).read_text()
        idx = src.index(".v2-hero-bg {")
        block = src[idx:src.index("}", idx)]
        assert "transform:" not in block, f"{page} hero background is transformed"


def test_cover_photo_saves_to_the_route_that_actually_accepts_put():
    """The artist update route is PUT /artists/{id} — there is no /api
    prefix on it, unlike venues which have both. Calling /api/artists/{id}
    reaches a GET-only route and 405s, which is exactly what the cover
    button did on first use. artist.edit.js already carries a comment about
    this same trap from an earlier occurrence.
    """
    js = (ROOT / "app" / "static" / "js" / "artist.edit.js").read_text()
    import re
    # Every PUT in this file must target /artists/, never /api/artists/.
    for m in re.finditer(r"fetch\(([^,]+),\s*\{([^}]*)", js, re.DOTALL):
        target, opts = m.group(1), m.group(2)
        if "PUT" not in opts:
            continue
        assert "/api/artists/" not in target, (
            f"PUT to a GET-only route: {target.strip()}"
        )


def test_artist_put_route_has_no_api_prefix():
    src = (ROOT / "backend" / "routes" / "artists.py").read_text()
    assert '@router.put("/artists/{artist_id}")' in src
    assert '@router.put("/api/artists/{artist_id}")' not in src


def test_profile_pages_resolve_vanity_urls():
    """gigsfill.com/venuedemo carries no query string; the slug resolver
    injects window._VANITY instead. Reading only the query param made every
    vanity link render "No venue specified"."""
    for name, kind in (("artist-profile-v2.js", "artist"), ("venue-profile-v2.js", "venue")):
        js = (ROOT / "app" / "static" / "js" / name).read_text()
        assert "window._VANITY" in js, name
        assert f'_VANITY.type === "{kind}"' in js, name
