"""Venue profile redesign + venue cover photo.

The spec block is the point of this page: GigsFill already collects stage
dimensions, PA, sound engineer, lighting, load-in, arrival window, typical
pay and bar/food tabs, and the old profile showed none of it. That data is
what an artist decides on.

Critically it stays behind the existing artist-only gate in
/api/venues/{id}/public. That gate is a deliberate 2026-08-08 audit fix —
without it an anonymous scraper could enumerate every venue's operations
catalogue — so the redesign renders what the server chose to send rather
than asking for more.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VENUES = ROOT / "backend" / "routes" / "venues.py"
PAGE_JS = ROOT / "app" / "static" / "js" / "venue-profile-v2.js"


def test_artist_only_gate_still_strips_the_spec_fields():
    """The single most important assertion here. If this ever passes
    vacuously the venue ops catalogue is public."""
    src = VENUES.read_text()
    idx = src.index("if not viewer_is_artist:")
    block = src[idx:idx + 900]
    for field in ("stage_width_ft", "stage_depth_ft", "sound_equipment_description",
                  "sound_engineer_details", "lighting_description",
                  "load_in_out_details", "default_pay_dollars",
                  "bar_tab_details", "food_tab_details"):
        assert field in block, f"{field} is no longer stripped for non-artists"


def test_newly_exposed_fields_are_not_sensitive():
    """Only rating and the hero pointer were added to the public payload.
    Rating already appears on venue cards; the hero fields point at one of
    the venue's own public images."""
    src = VENUES.read_text()
    i = src.index("hero_focal_y\n            FROM venues")
    added = src[i - 400:i]
    assert "avg_rating" in added and "hero_media_id" in added
    # The gated list must not have shrunk to let something through.
    gate = src[src.index("if not viewer_is_artist:"):][:900]
    assert "artist_frequency_days" in gate


def test_page_renders_only_what_the_server_sent():
    """No client-side re-request of gated data, and no pretending."""
    js = PAGE_JS.read_text()
    idx = js.index("function renderSpecs")
    block = js[idx:idx + 600]
    assert "viewer_is_artist" in block
    assert "v2SpecsLocked" in block


def test_venue_hero_media_is_ownership_checked():
    """Same risk as the artist side: a crafted id could otherwise publish
    another venue's upload."""
    src = VENUES.read_text()
    idx = src.index('if "hero_media_id" in data:')
    block = src[idx:idx + 1200]
    assert "FROM venue_media" in block
    assert "venue_id = :vid" in block
    assert "does not belong to this venue" in block


def test_venue_hero_can_be_cleared():
    src = VENUES.read_text()
    idx = src.index('if "hero_media_id" in data:')
    assert "SET hero_media_id = NULL" in src[idx:idx + 1200]


def test_venue_focal_is_clamped():
    src = VENUES.read_text()
    idx = src.index('    def _focal(key):')
    assert "max(0.0, min(100.0" in src[idx:idx + 600]


def test_venue_hero_columns_in_schema_and_orm():
    db_py = (ROOT / "backend" / "db.py").read_text()
    models = (ROOT / "backend" / "models.py").read_text()
    # Both artists and venues now carry these, so expect two of each.
    for col in ("hero_media_id", "hero_focal_x", "hero_focal_y"):
        assert db_py.count(col) >= 2, col
        assert models.count(col) >= 2, col


def test_site_wide_gigs_are_narrowed_to_this_venue():
    """/api/gigs/public returns every public gig on the platform. Rendering
    it unfiltered would show other venues' bookings on this page."""
    js = PAGE_JS.read_text()
    assert "Number(g.venue_id) === Number(venueId)" in js


def test_spec_rows_have_no_icons():
    """Nine emoji down the left of a facts table was decoration competing
    with the labels, not information."""
    js = PAGE_JS.read_text()
    idx = js.index("function renderSpecs")
    block = js[idx:js.index("function formatArrival")]
    assert "v2-spec-ico" not in block
    # No emoji survived in the row definitions.
    assert not any(ord(ch) > 0x2500 for ch in block), "emoji left in spec rows"


def test_spec_body_text_is_one_treatment():
    """Value and detail used to differ — white headline, muted detail — so a
    row with both looked like a different kind of row from one with only a
    detail. Stage and Arrival read unlike Load in / out for no reason the
    content justified."""
    html = (ROOT / "app" / "venue-profile-v2.html").read_text()
    assert ".v2-spec-sub" not in html, "second body style still defined"
    idx = html.index(".v2-spec-value {")
    block = html[idx:idx + 220]
    assert "var(--v2-dim)" in block, "body text should use the muted tone"

    js = PAGE_JS.read_text()
    idx = js.index('$("v2Specs").innerHTML')
    render = js[idx:idx + 600]
    assert render.count("v2-spec-value") == 2, "value and detail should share a class"
