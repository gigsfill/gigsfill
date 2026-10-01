"""Public profile background image picker.

Replaces per-tile "Use as Cover" buttons on both edit pages. That wording
never said what it did — cover of what? — and the implementation had drifted
into two separate IIFEs on the artist page, one holding `markHero` and the
other holding the helpers it called, which threw "el is not defined" as soon
as the logo button was wired in. One shared module now serves both pages.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JS = ROOT / "app" / "static" / "js" / "cover-picker.js"
PAGES = ("artist-edit.html", "venue-edit.html")


def test_one_module_serves_both_edit_pages():
    """The duplication is what let the two pages drift apart in the first
    place — venue-edit sat at aspect-ratio 32/9 for a while after the artist
    side was corrected to 64/21."""
    for page in PAGES:
        html = (ROOT / "app" / page).read_text()
        assert "cover-picker.js" in html, page
        assert "gfCoverPicker.init" in html, page


def test_old_per_card_buttons_are_gone():
    for page in PAGES:
        html = (ROOT / "app" / page).read_text()
        assert "useLogoAsCover" not in html, page
        assert 'id="coverPreview"' not in html, page
    for js in ("artist.edit.js", "venue.edit.js"):
        src = (ROOT / "app" / "static" / "js" / js).read_text()
        assert "Use as Cover" not in src, js
        assert "markHero" not in src, js


def test_edit_pages_only_supply_values_and_endpoints():
    """All the behaviour lives in the module; the pages hand over state."""
    for js in ("artist.edit.js", "venue.edit.js"):
        src = (ROOT / "app" / "static" / "js" / js).read_text()
        assert "gfCoverPicker.setState" in src, js


def test_artist_put_url_has_no_api_prefix():
    """PUT /artists/{id} exists; PUT /api/artists/{id} does not, and calling
    it 405s. The venue route has both."""
    html = (ROOT / "app" / "artist-edit.html").read_text()
    idx = html.index("gfCoverPicker.init")
    block = html[idx - 400:idx + 400]
    assert 'putUrl: "/artists/"' in block
    assert 'putUrl: "/api/artists/"' not in block


def test_logo_is_offered_first_among_the_tiles():
    """It's the most likely choice; hunting for it among stage photos is
    needless work."""
    src = JS.read_text()
    idx = src.index("media = logo.concat(pics)")
    assert idx > 0


def test_preview_mirrors_the_profile_exactly():
    """Outer box clips, inner layer carries image + transform. Transforming
    the box scaled the preview itself rather than zooming the picture."""
    src = JS.read_text()
    idx = src.index("function applyPreview()")
    block = src[idx:idx + 900]
    assert "gfCoverPreviewImg" in block
    assert "transformOrigin" in block
    assert "aspectRatio" in block


def test_framing_saves_are_debounced():
    src = JS.read_text()
    idx = src.index("function saveFraming()")
    block = src[idx:idx + 600]
    assert "clearTimeout(saveTimer)" in block
    assert "setTimeout" in block


def test_choosing_a_new_background_resets_the_frame():
    """Inheriting the previous image's crop lands a new picture somewhere
    arbitrary."""
    src = JS.read_text()
    idx = src.index("async function selectMedia")
    block = src[idx:idx + 600]
    assert "state.x = 50" in block and "state.zoom = 1" in block


# ── logo overlay ────────────────────────────────────────────────────

def test_overlay_hidden_when_the_logo_is_already_the_background():
    """Laying the logo over itself is pointless, and the control would
    invite exactly that."""
    src = JS.read_text()
    idx = src.index("function overlayAvailable()")
    block = src[idx:idx + 400]
    assert "Number(logo.id) !== Number(state.mediaId)" in block


def test_overlay_opacity_floors_above_zero():
    """Zero is indistinguishable from the overlay being off, which the
    toggle already expresses."""
    for route in ("artists.py", "venues.py"):
        src = (ROOT / "backend" / "routes" / route).read_text()
        idx = src.index('if "hero_logo_opacity" in data:')
        assert "max(0.1, min(1.0" in src[idx:idx + 400], route


def test_profile_hides_the_corner_plate_when_overlaying():
    """Otherwise the same mark renders twice in one band."""
    for name in ("artist-profile-v2.js", "venue-profile-v2.js"):
        js = (ROOT / "app" / "static" / "js" / name).read_text()
        idx = js.index("_overlayOn")
        block = js[idx:idx + 900]
        assert 'plate.style.display = "none"' in block, name


def test_overlay_sits_above_the_scrim_but_below_the_text():
    """Above the scrim so the mark stays legible on a dark photo; below the
    text so it never competes with the name."""
    for page in ("artist-profile.html", "venue-profile.html"):
        html = (ROOT / "app" / page).read_text()
        scrim = html.index('class="v2-hero-scrim"')
        logo = html.index('id="v2HeroLogo"')
        inner = html.index('class="v2-hero-inner"')
        assert scrim < logo < inner, page
