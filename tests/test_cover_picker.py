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
        # Bound by the enclosing branch rather than a character count — a
        # fixed window keeps breaking as lines are added inside it.
        start = js.index("var _overlayOn")
        end = js.index('ov.style.display = "none"', start)
        block = js[start:end]
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


def test_overlay_row_requires_a_logo_to_exist():
    """With no logo uploaded there is nothing to overlay, so the control
    must not appear at all."""
    src = JS.read_text()
    idx = src.index("function overlayAvailable()")
    block = src[idx:src.index("}", idx) + 1]
    assert "logo &&" in block


def test_logo_size_is_clamped():
    """Floor keeps the mark recognisable; 1.0 lets a wordmark span the band."""
    for route in ("artists.py", "venues.py"):
        src = (ROOT / "backend" / "routes" / route).read_text()
        idx = src.index('if "hero_logo_scale" in data:')
        assert "max(0.15, min(1.0" in src[idx:idx + 400], route


def test_logo_height_tracks_width():
    """Scaling only max-width lets a tall badge overflow the band."""
    src = JS.read_text()
    idx = src.index("ovImg.style.maxWidth")
    assert "maxHeight" in src[idx:idx + 400]


def test_saving_is_reported_including_failure():
    """Auto-save with no feedback leaves the user hunting for a Save button,
    and silence after a failure is worse than an error."""
    src = JS.read_text()
    idx = src.index("function setStatus(")
    block = src[idx:idx + 900]
    for kind in ("saving", "saved", "error"):
        assert f'"{kind}"' in block or f"'{kind}'" in block, kind
    assert "Could not save" in block
    # Every write path reports.
    for fn in ("saveFraming", "selectMedia", "clearMedia"):
        i = src.index(fn)
        assert "setStatus" in src[i:i + 900], fn


def test_media_grids_match_between_profile_and_editor():
    """The editor is meant to preview the arrangement. Videos were 4-up and
    photos 5-up on the profile while the editor ran 6-8, so ordering media
    there told you nothing about the result."""
    for profile, editor in (("artist-profile.html", "artist-edit.html"),
                            ("venue-profile.html", "venue-edit.html")):
        pro = (ROOT / "app" / profile).read_text()
        edi = (ROOT / "app" / editor).read_text()
        for sel in (".v2-media-grid {", ".v2-photo-grid {"):
            i = pro.index(sel)
            # Window has to clear the explanatory comment inside the rule.
            assert "repeat(4, 1fr)" in pro[i:i + 700], f"{profile} {sel}"
        i = edi.index(".media-grid {")
        assert "repeat(4, 1fr)" in edi[i:i + 400], editor


def test_few_photo_special_case_is_gone():
    """It rendered two photos large, contradicting the fixed 4-up the editor
    now mirrors."""
    for name in ("artist-profile-v2.js", "venue-profile-v2.js"):
        js = (ROOT / "app" / "static" / "js" / name).read_text()
        assert 'classList.add("few")' not in js, name


def test_remove_clears_every_background_setting():
    """Clearing only the image left the overlay toggle, logo size, fade,
    zoom and band height behind, so the next image picked up the previous
    one's settings — the logo already switched on at the old size."""
    src = JS.read_text()
    idx = src.index("async function clearMedia()")
    block = src[idx:src.index("}", src.index("});", idx))]
    for field in ("hero_media_id", "hero_focal_x", "hero_zoom", "hero_ratio",
                  "hero_logo_overlay", "hero_logo_opacity", "hero_logo_scale"):
        assert field in block, f"{field} not cleared by Remove"


def test_preview_applies_the_same_scrim_as_the_hero():
    """Without it the logo was previewed over a bright photo and rendered
    over a darkened one, so one Fade value looked like two settings."""
    for page in ("artist-edit.html", "venue-edit.html"):
        html = (ROOT / "app" / page).read_text()
        assert 'id="gfCoverPreviewScrim"' in html, page
        idx = html.index("#gfCoverPreviewScrim {")
        block = html[idx:idx + 600]
        assert "linear-gradient(180deg" in block and "radial-gradient" in block, page
        # And the same saturation treatment as the hero background.
        i2 = html.index("#gfCoverPreviewImg {")
        assert "saturate(0.9)" in html[i2:i2 + 400], page


def test_scrim_radial_scales_with_the_box():
    """A fixed 1200x400 radial cannot look the same in a 640px preview."""
    for page in ("artist-profile.html", "venue-profile.html", "artist-edit.html"):
        html = (ROOT / "app" / page).read_text()
        assert "radial-gradient(94% 95%" in html, page
        assert "radial-gradient(1200px 400px" not in html, page


def test_logo_sits_above_the_scrim_in_the_preview():
    """Same order as the profile: above the scrim so it stays legible, below
    nothing else in the box."""
    for page in ("artist-edit.html", "venue-edit.html"):
        html = (ROOT / "app" / page).read_text()
        img = html.index('id="gfCoverPreviewImg"')
        scrim = html.index('id="gfCoverPreviewScrim"')
        logo = html.index('id="gfCoverPreviewLogo"')
        assert img < scrim < logo, page


# ── logo positioning ────────────────────────────────────────────────

def test_grabbing_the_logo_moves_the_logo_not_the_background():
    """One preview, two draggable things. Which one moves is decided by
    what was grabbed, so there's no mode to remember."""
    src = JS.read_text()
    idx = src.index("function onDown(")
    block = src[idx:src.index("function onMove(", idx)]
    assert 'e.target === logoEl' in block
    assert '"logo"' in block and '"bg"' in block


def test_logo_follows_the_cursor_but_background_inverts():
    """Dragging an object moves it with you; panning a background behind a
    window moves the other way, like a photo under glass. Getting these the
    same way round would make one of them feel broken."""
    src = JS.read_text()
    idx = src.index("function onMove(")
    block = src[idx:src.index("function onUp(", idx)]
    assert "sfx + dx" in block, "logo should track the cursor"
    assert "sfx - dx" in block, "background should pan inversely"


def test_logo_position_is_persisted_and_clamped():
    src = JS.read_text()
    assert "hero_logo_x" in src and "hero_logo_y" in src
    for route in ("artists.py", "venues.py"):
        rsrc = (ROOT / "backend" / "routes" / route).read_text()
        # Reuses the focal clamp, which bounds 0-100.
        assert '_logo_x = _focal("hero_logo_x")' in rsrc, route


def test_profile_places_the_logo_where_it_was_put():
    for name in ("artist-profile-v2.js", "venue-profile-v2.js"):
        js = (ROOT / "app" / "static" / "js" / name).read_text()
        assert "hero_logo_x" in js, name
        idx = js.index("hero_logo_x")
        block = js[idx:idx + 500]
        assert 'ov.style.left' in block and 'ov.style.top' in block, name


def test_logo_position_resets_with_everything_else():
    src = JS.read_text()
    idx = src.index("async function clearMedia()")
    block = src[idx:src.index("}", src.index("});", idx))]
    assert "hero_logo_x" in block and "hero_logo_y" in block


def test_logo_position_is_editable_and_resettable():
    """Drag has no undo and nudging a logo back to dead centre by hand is
    near impossible, so the numbers are editable fields and re-centring is
    one click."""
    for page in ("artist-edit.html", "venue-edit.html"):
        html = (ROOT / "app" / page).read_text()
        row = html[html.index('id="gfCoverLogoPosRow"'):]
        row = row[:row.index("</div>", row.index("gf-row-val"))]
        for need in ('id="gfCoverLogoX"', 'id="gfCoverLogoY"',
                     'id="gfCoverLogoCenter"', 'type="number"',
                     'min="0"', 'max="100"'):
            assert need in row, f"{page}: {need}"
        # The button sits with the values it resets, inside the same cell.
        assert row.index("gfCoverLogoY") < row.index("gfCoverLogoCenter"), page

    src = JS.read_text()
    centre = src[src.index('t.closest("#gfCoverLogoCenter")'):][:300]
    assert "state.logoX = 50" in centre and "state.logoY = 50" in centre


def test_typed_position_is_clamped_and_tolerates_mid_edit():
    """A half-typed field parses to NaN and an out-of-range number would
    push the logo outside the band; neither may move the logo."""
    src = JS.read_text()
    blk = src[src.index('e.target.id === "gfCoverLogoX"'):][:700]
    assert "isFinite" in blk and 'e.target.value === ""' in blk
    assert "Math.max(0, Math.min(100, n))" in blk


def test_drag_writes_the_fields_but_not_while_typing():
    """Otherwise typing "5" on the way to "50" gets rewritten to 5% under
    the cursor."""
    src = JS.read_text()
    blk = src[src.index('var xi = $("gfCoverLogoX")'):][:400]
    assert "document.activeElement !== xi" in blk
    assert "document.activeElement !== yi" in blk


def test_background_thumbnail_and_name_open_the_dialog():
    """People click the picture they mean to change before they look for a
    button."""
    src = JS.read_text()
    assert 'id="gfCoverPick"' in src
    # Remove must still remove: its check comes first and returns.
    assert src.index('t.closest("#gfCoverClear")') < src.index('t.closest("#gfCoverPick")')
    pick = src[src.index('t.closest("#gfCoverPick")'):][:120]
    assert "open()" in pick
    for page in ("artist-edit.html", "venue-edit.html"):
        html = (ROOT / "app" / page).read_text()
        assert ".gf-cover-pick {" in html, page


def test_control_rows_are_ordered_and_named_for_what_they_do():
    """Height first because it sets the band's shape, and framing inside a
    box whose proportions are about to change is backwards. Labels say which
    thing they affect, since Zoom/Size/Fade alone did not."""
    html = (ROOT / "app" / "artist-edit.html").read_text()
    start = html.index('<div class="gf-rows">')
    rows = html[start:html.index('class="gf-cover-foot"', start)]
    order = [rows.index(x) for x in ("Height", "Image Zoom", "Logo on Top?",
                                     "Logo Size", "Logo Fade", "Logo Position")]
    assert order == sorted(order), "control rows are out of order"


def test_logo_controls_read_as_one_group():
    """A rule on the first row rather than a wrapper, which would have
    broken the shared grid alignment. No closing rule — the dialog footer's
    own border lands immediately below the last row and closes the group,
    so a second line there was just a double rule."""
    for page in ("artist-edit.html", "venue-edit.html"):
        html = (ROOT / "app" / page).read_text()
        assert "gf-group-top" in html, page
        assert "gf-group-end" not in html, f"{page}: stray closing rule"
        i = html.index(".gf-row.gf-group-top {")
        assert "border-top" in html[i:i + 200], page


def test_out_of_range_entry_is_corrected_on_leaving_the_field():
    """A number input holds "480" or "" quite happily while the logo has
    already clamped to the edge, so the field is rewritten on focusout to
    what the logo actually did."""
    src = JS.read_text()
    blk = src[src.index('addEventListener("focusout"'):][:500]
    assert "gfCoverLogoX" in blk and "gfCoverLogoY" in blk
    assert "Math.round(state.logoX)" in blk and "Math.round(state.logoY)" in blk


def test_dialog_labels_match_the_page_behind_it():
    """Height / Image Zoom / Logo Size are the same kind of thing as
    "Artist Name" or "City"; a heavier, brighter second style read as a
    different class of label."""
    import re
    for page in ("artist-edit.html", "venue-edit.html"):
        html = (ROOT / "app" / page).read_text()
        form = re.search(r"\.form-row label \{(.*?)\}", html, re.S).group(1)
        want = {k: v for k, v in re.findall(r"([\w-]+):\s*([^;]+);", form)}
        row = re.search(r"\.gf-row-lbl \{(.*?)\}", html, re.S).group(1)
        got = {k: v for k, v in re.findall(r"([\w-]+):\s*([^;]+);", row)}
        for prop in ("font-size", "font-weight", "color"):
            assert got[prop].strip() == want[prop].strip(), f"{page}: {prop}"


def test_dialog_rules_are_visible_against_the_dialog():
    """--border is rgba(148,163,184,0.1), which all but disappears on the
    dialog's #151b28 — and these rules carry meaning, fencing the logo
    group and dividing the header and footer."""
    import re
    for page in ("artist-edit.html", "venue-edit.html"):
        html = (ROOT / "app" / page).read_text()
        alpha = float(re.search(r"--gf-rule: rgba\([\d, ]+,\s*([\d.]+)\)", html).group(1))
        assert alpha > 0.1, f"{page}: rule no brighter than --border"
        for sel in (r"\.gf-cover-head", r"\.gf-cover-foot",
                    r"\.gf-row\.gf-group-top"):
            blk = re.search(sel + r" \{(.*?)\}", html, re.S).group(1)
            assert "var(--gf-rule)" in blk, f"{page}: {sel}"


def test_thumbnail_has_no_hover_tooltip():
    """It just opens the dialog; a tooltip trailing the pointer is noise."""
    src = JS.read_text()
    blk = src[src.index('class="gf-cover-pick"'):][:200]
    assert "title=" not in blk
