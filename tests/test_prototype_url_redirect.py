"""The `-v2` prototype profile URLs redirect to the vanity slug.

The redesigned profiles were prototyped as `artist-profile-v2.html` /
`venue-profile-v2.html`, then promoted by copying them over the canonical
filenames. The `-v2` files were left behind as byte-identical duplicates,
so the prototype URL kept serving a perfectly working page — which made the
problem invisible. It was not a 404 or an error; it was the right page at
the wrong address.

Two things followed from that. The site-wide vanity rewriter in
`vanity-url-editor.js` matches `/app/(artist|venue)-profile.html?...`, so it
never upgraded the `-v2` spelling, and anyone who bookmarked or shared the
prototype URL handed out
`gigsfill.com/app/artist-profile-v2.html?artist_id=1` in place of
`gigsfill.com/fridayspast`.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
MAIN = ROOT / "backend" / "main.py"


def test_prototype_duplicates_are_gone():
    """Left in place they keep serving a second copy of the live page at a
    URL nothing canonicalises."""
    for name in ("artist-profile-v2.html", "venue-profile-v2.html"):
        assert not (APP / name).exists(), f"{name} is back"


def test_the_promoted_pages_are_still_there():
    for name in ("artist-profile.html", "venue-profile.html"):
        assert (APP / name).exists(), name


def test_redirect_is_registered_before_the_static_mount():
    """StaticFiles would otherwise answer first — and once the duplicates
    are deleted it answers 404, so the redirect would never run."""
    src = MAIN.read_text()
    route = src.index('@app.get("/app/{entity_type}-profile-v2.html"')
    mount = src.index('app.mount("/app", StaticFiles')
    assert route < mount, "redirect registered after the mount"


def test_redirect_prefers_the_slug_but_never_breaks_the_link():
    """A link already in the wild must keep working even if the vanity
    lookup fails or the entity has no slug."""
    src = MAIN.read_text()
    fn = src[src.index("def _redirect_prototype_profile_urls"):]
    fn = fn[:fn.index('app.mount("/app"')]
    # Canonical page is the starting target; the slug upgrades it.
    assert 'target = f"/app/{entity_type}-profile.html?{entity_type}_id={entity_id}"' in fn
    assert 'target = f"/{row[0]}"' in fn
    # A lookup failure falls through to that target rather than raising.
    assert "except Exception:" in fn
    # Only the two real entity types; anything else is not a profile.
    assert '("artist", "venue")' in fn
    # A permanent redirect, so browsers and crawlers stop using the old URL.
    assert "status_code=301" in fn


def test_db_session_is_closed_on_every_path():
    """next(get_db()) is a hand-rolled session outside FastAPI's dependency
    lifecycle, so nothing closes it for us. Leaking one per request on a
    page crawlers hit would exhaust the pool."""
    src = MAIN.read_text()
    fn = src[src.index("def _redirect_prototype_profile_urls"):]
    fn = fn[:fn.index('app.mount("/app"')]
    assert "finally:" in fn and "db.close()" in fn
