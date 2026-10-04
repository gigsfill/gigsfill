"""Band calendar — member days off (2026-10-04).

Replaces three range-entry forms with one grid. The property that matters
most is not in the UI: a member marking a day off must NEVER make the artist
unavailable. A four-piece that also plays as a duo has to stay bookable when
the drummer is away, so member days stay soft and only an explicit, admin-set
band-wide mark hard-blocks.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "backend" / "db.py"
ROUTES = ROOT / "backend" / "routes" / "availability.py"
JS = ROOT / "app" / "static" / "js" / "band-calendar.js"


def _func(src: str, name: str) -> str:
    """The body of a top-level def, bounded by the next top-level statement
    rather than a fixed character window that comments can push past."""
    i = src.index("def " + name)
    rest = src[i:]
    lines = rest.splitlines(keepends=True)
    out = [lines[0]]
    for ln in lines[1:]:
        if ln.strip() and not ln[0].isspace() and not ln.startswith("@"):
            break
        if ln.startswith("@router."):
            break
        out.append(ln)
    return "".join(out)



def test_a_member_day_off_never_hard_blocks_the_artist():
    """The duo case: drummer out must not take the band off the market.
    Member days live in member_days_off; only artist_availability hard-blocks,
    and the member toggle must not write to it."""
    src = ROUTES.read_text()
    fn = _func(src, "toggle_band_day_off")
    assert "artist_availability" not in fn, "member toggle touches the hard-block table"
    assert "_toggle_day" in fn
    assert "artist_availability" not in _func(src, "toggle_my_day_off")


def test_nothing_can_block_a_whole_band_any_more():
    """2026-10-05: artist-level availability is gone. A band is unavailable
    only in the sense that its members are, and the one derived consequence
    (suppressing an email nobody could accept) lives in member_availability."""
    src = ROUTES.read_text()
    assert "toggle_band_wide_day" not in src
    assert "artist_availability" not in src
    for f in ("backend/routes/gigs.py", "backend/scheduler.py",
              "backend/services/gig_hold.py", "backend/services/open_gig_digest.py"):
        assert "artist_availability" not in (ROOT / f).read_text(), f


def test_member_toggle_requires_membership():
    """Any member may mark themselves off, but only a member."""
    src = ROUTES.read_text()
    fn = _func(src, "toggle_band_day_off")
    assert "_member_ids_for_artist" in fn and "403" in fn


def test_all_bands_scope_uses_zero_not_null():
    """Both SQLite and Postgres treat NULLs as distinct, so a nullable
    artist_id would let the same day be stored twice and the toggle would
    desync from the grid."""
    schema = DB.read_text()
    tbl = schema[schema.index("CREATE TABLE IF NOT EXISTS member_days_off"):]
    tbl = tbl[:tbl.index('"""')]
    assert "artist_id INTEGER NOT NULL DEFAULT 0" in tbl
    idx = schema[schema.index("idx_member_days_off_unique"):][:200]
    assert "user_id, artist_id, day" in idx


def test_deleting_a_user_or_artist_clears_their_days():
    """Orphan rows would attribute days to someone who no longer exists."""
    assert "member_days_off" in (ROOT / "backend" / "routes" / "me.py").read_text()
    assert "member_days_off" in (ROOT / "backend" / "routes" / "admin.py").read_text()
    ed = (ROOT / "backend" / "services" / "entity_delete.py").read_text()
    assert "DELETE FROM member_days_off WHERE artist_id = :aid" in ed


def test_booking_warning_names_who_is_away_and_reads_the_new_table():
    src = ROUTES.read_text()
    fn = _func(src, "_member_blackouts_for_gig")
    assert "member_days_off" in fn
    assert "user_availability" not in fn, "still reading the retired table"
    assert '"name"' in fn and "is_self" in fn


def test_a_member_in_both_scopes_counts_once():
    """Someone can hold a global row and a band row for the same day; the
    booking warning must not list them twice."""
    src = ROUTES.read_text()
    assert "seen" in _func(src, "_member_blackouts_for_gig")


def test_range_entry_is_gone():
    """The forms that collected start/end/reason are removed, along with the
    endpoints that fed them — otherwise a stray POST would write rows the
    calendar never reads."""
    src = ROUTES.read_text()
    for dead in ('@router.post("/api/me/availability")',
                 '@router.put("/api/me/availability/{blackout_id}")',
                 '@router.post("/api/artists/{artist_id}/availability")',
                 '@router.get("/api/artists/{artist_id}/member-availability")'):
        assert dead not in src, dead
    for gone in ("artist-availability.js", "artist-member-availability.js",
                 "user-availability.js"):
        assert not (ROOT / "app" / "static" / "js" / gone).exists(), gone


def test_no_reason_is_collected():
    """It was blank or 'Out of Town', and nobody deciding a booking saw it."""
    src = JS.read_text()
    # Comments explain why it is gone, so look at the code: the toggle sends
    # only a day, and nothing renders or reads a reason.
    body = "\n".join(l for l in src.splitlines()
                     if not l.strip().startswith(("*", "/*", "//")))
    assert "JSON.stringify({ day: day })" in body
    assert "reason" not in body.lower()


def test_dates_are_local_not_utc():
    """toISOString() is UTC, which puts anyone west of Greenwich on the wrong
    day for most of the evening — the hours a musician actually fills this in."""
    src = JS.read_text()
    body = "\n".join(l for l in src.splitlines()
                     if not l.strip().startswith(("*", "/*", "//")))
    assert "toISOString" not in body
    assert "getFullYear()" in body


# ── Cross-band: booked with one band shows as unavailable to the others ──────

def test_cross_band_conflicts_are_derived_not_stored():
    """Writing these at booking time would mean deleting them again on every
    cancel, decline, date change and roster change. gigs.py has three
    cancellation endpoints that the project's own notes call easy to fix one
    of and miss the others; a stale "unavailable" is worse than none."""
    src = ROUTES.read_text()
    fn = _func(src, "_other_band_commitments")
    assert "SELECT" in fn and "INSERT" not in fn and "DELETE" not in fn, \
        "cross-band conflicts are being written somewhere"
    # And nothing in the booking path writes them either.
    gigs = (ROOT / "backend" / "routes" / "gigs.py").read_text()
    assert "INSERT INTO member_days_off" not in gigs


def test_commitment_statuses_match_the_booking_conflict_check():
    """"Committed" has to mean the same thing here as it does in
    _check_artist_time_conflict, or the calendar and the booking guard
    disagree about whether a slot counts."""
    src = ROUTES.read_text()
    gigs = (ROOT / "backend" / "routes" / "gigs.py").read_text()
    wanted = {"booked", "pending_contract", "awaiting_venue_contract",
              "pending_venue_approval"}
    mod = (ROOT / "backend" / "services" / "member_availability.py").read_text()
    got = set(re.findall(r"'(\w+)'", mod[mod.index("COMMITTED_SLOT_STATUSES"):][:300]))
    assert wanted <= got, got
    # And availability.py uses that constant rather than its own copy.
    assert "COMMITTED_SLOT_STATUSES" in src
    assert "'booked','pending_contract','awaiting_venue_contract','pending_venue_approval'" in gigs


def test_deleted_bands_do_not_create_phantom_conflicts():
    src = ROUTES.read_text()
    fn = _func(src, "_other_band_commitments")
    assert fn.count("deleted_at IS NULL") >= 2


def test_a_cross_band_gig_still_only_warns():
    """Same rule as a clicked day: it names the clash and lets the band book
    anyway. Nothing here may touch the hard-block table."""
    src = ROUTES.read_text()
    assert "artist_availability" not in _func(src, "_other_band_commitments")


def test_the_reason_someone_is_away_is_never_shown():
    """2026-10-05: unavailable is unavailable. Whether a member has a day off
    or a gig with another band changes nothing for the band reading it, and
    band A has no business being told its singer plays with band B."""
    src = ROUTES.read_text()
    fn = _func(src, "_member_blackouts_for_gig")
    assert "_other_band_commitments" in fn, "cross-band conflicts no longer counted"
    for leak in ("with_artist", "venue_name", "other_artist_name"):
        assert leak not in fn, f"{leak} is still surfaced"
    # Not even fetched, which is the surest way not to leak it.
    q = _func(src, "_other_band_commitments")
    assert "a2.name" not in q and "venue_name" not in q
    js = JS.read_text()
    for leak in ("booked with", "with_artist", "gfbc-gig", "gigchip"):
        assert leak not in js, f"calendar still shows {leak}"
    mod = (ROOT / "backend" / "services" / "member_availability.py").read_text()
    assert "playing with" not in mod
    # Not even as a scope value in the payload: that would tell band B the
    # cause to anyone who opens the network tab.
    cal = _func(src, "get_band_calendar")
    code = "\n".join(l for l in cal.splitlines() if not l.strip().startswith("#"))
    assert '"gig"' not in code, "cause is still named in the payload"
    assert '"locked": True' in code


def test_untoggling_your_day_does_not_erase_a_derived_gig():
    """Otherwise clearing your own day would appear to clear a booking you
    are still committed to."""
    js = JS.read_text()
    fn = js[js.index("Cal.prototype.applyLocal"):]
    fn = fn[:fn.index("Cal.prototype.meId")]
    assert "m.locked" in fn and "if (gig) list.push(gig)" in fn


def test_only_an_email_nobody_could_accept_is_suppressed():
    """The one derived consequence of member availability: when NO member is
    free, the blast and digest skip the date. It cannot hurt a stripped-down
    line-up, because if a duo could play then someone is free and the offer
    still goes out."""
    mod = (ROOT / "backend" / "services" / "member_availability.py").read_text()
    assert "def everyone_off" in mod
    # Expressed as "no member is free", not "every member has a row": the
    # latter is vacuously true for a band with no members.
    assert "NOT EXISTS" in mod and "_free_m" in mod
    assert "_any_m" in mod, "no guard against the zero-member case"
    for f in ("backend/routes/gigs.py", "backend/scheduler.py"):
        src = (ROOT / f).read_text()
        assert "member_days_off" in src, f


def test_the_digest_names_who_is_away():
    """Dropping the artist-level block means the band hears about the gig; the
    email has to say who cannot make it or the band cannot judge the line-up."""
    src = (ROOT / "backend" / "services" / "open_gig_digest.py").read_text()
    assert "members_off_by_date" in src
    assert "Away:" in src


def test_the_calendar_mounts_itself_from_markup():
    """It used to appear only if a separate page-init file called mount().
    When that file was rewritten without a new cache-buster, browsers kept the
    old copy — which still revealed the panel but never mounted anything, so
    the tab rendered empty with no error at all. The element that needs a
    calendar now says so itself."""
    js = JS.read_text()
    assert "data-gfbc-mode" in js and "function autoMount" in js
    assert "DOMContentLoaded" in js
    for page, mode in (("artist-book-gigs.html", "band"), ("user-profile.html", "me")):
        html = (ROOT / "app" / page).read_text()
        assert f'data-gfbc-mode="{mode}"' in html, page


def test_two_callers_cannot_double_mount():
    """Markup and the init file both mount it; whichever runs first wins."""
    js = JS.read_text()
    fn = js[js.index("window.gfBandCalendar = {"):][:600]
    assert "mounted[key]" in fn


def test_edited_page_scripts_carry_a_cache_buster():
    """nginx serves /app/ with max-age=86400, so an edited file with an
    unchanged ?v= is invisible to every returning browser for a day."""
    import re
    for page in ("artist-book-gigs.html", "user-profile.html"):
        html = (ROOT / "app" / page).read_text()
        for src in re.findall(r'<script src="/app/static/js/([^"]+)"', html):
            name = src.split("?")[0]
            if name in ("band-calendar.js", "artist-book-gigs-init.js"):
                assert "?v=" in src, f"{page}: {name} has no cache-buster"


# ── "All dates" snapshot ────────────────────────────────────────────────────

def test_consecutive_days_merge_only_when_the_same_people_are_out():
    """A month grid answers "is the 14th free"; it is poor at "when are we out
    over the next year". The list collapses runs — but merging on date alone
    would print "Nov 14 - Nov 16" across three days that each had a different
    member away, which is simply false."""
    js = JS.read_text()
    fn = js[js.index("function mergeRuns"):]
    fn = fn[:fn.index("\n  Cal.prototype.openList")]
    assert "keyOf" in fn, "runs are merged without comparing who is out"
    assert "last.key === k" in fn
    assert "addDays(last.end, 1) === d" in fn, "merges non-adjacent days"


def test_the_snapshot_asks_for_its_own_range():
    """The grid holds three months; a snapshot is useless if it only lists
    whatever the user happened to page to.

    It starts at the 1st of the current month rather than today — a day
    marked earlier this month is still on the grid, and a list that omitted
    it would not be "all dates" — and runs 13 months so the same month next
    year is inside the window rather than just outside it."""
    js = JS.read_text()
    fn = js[js.index("Cal.prototype.openList"):][:2600]
    assert "_now.getMonth(), 1" in fn, "does not start at the 1st"
    assert "_now.getMonth() + 13, 0" in fn, "not a 13-month window"
    # Month arithmetic, not a day count: day counts drift on leap years and
    # cannot express "the same month next year".
    assert "addDays(from, 365)" not in fn


def test_the_sheet_is_not_parented_to_the_redrawn_grid():
    """render() rewrites root.innerHTML on every toggle, which would tear the
    sheet out from under whoever is reading it."""
    js = JS.read_text()
    fn = js[js.index("Cal.prototype.openList"):][:1800]
    assert "document.body.appendChild(host)" in fn
    assert "this.root.appendChild(host)" not in js


def test_both_calendars_offer_the_list():
    js = JS.read_text()
    assert 'data-list="1"' in js and ">All dates<" in js
    # Available in both modes: it is emitted in the shared header markup, not
    # inside a `this.o.mode === "band"` branch.
    # Bound to the innerHTML construction — the CSS block above it also
    # mentions these class names, so searching the whole file slices backwards.
    tpl = js[js.index("this.root.innerHTML ="):]
    tpl = tpl[:tpl.index('"</div>";') if '"</div>";' in tpl[:4000] else 4000]
    assert "data-list" in tpl, "button is not in the shared header markup"
    assert 'mode === "band"' not in tpl, "button is mode-gated"


def test_the_list_uses_columns_not_a_flex_row():
    """With min-width, the longest range ("Sat, Jan 30, 2027 - Mon, Feb 1,
    2027") pushed its own names column right and every line started somewhere
    different. Fixed grid tracks keep the three columns in line."""
    js = JS.read_text()
    rule = js[js.index('".gfbc-li {'):][:420]
    assert "display:grid" in rule and "grid-template-columns:252px" in rule
    assert "min-width:172px" not in js


def test_every_list_row_emits_all_three_cells():
    """A skipped cell slides the next row's content into the wrong column."""
    js = JS.read_text()
    row = js[js.index("return '<div class=\"gfbc-li\">'"):][:460]
    for cls in ("gfbc-li-d", "gfbc-li-n", "gfbc-li-c"):
        assert cls in row, cls
    assert "(who ?" not in row, "names cell is conditional"


def test_each_member_gets_their_own_square():
    """One marker per member on a day, carrying their initials in a colour
    that stays the same for them across months."""
    js = JS.read_text()
    rule = js[js.index('".gfbc-dot {'):][:260]
    assert "border-radius:4px" in rule, "still a circle"
    # One span per member in the day cell, not a single merged badge.
    assert "shown.map(function (m)" in js
    assert "hueFor(m.user_id)" in js


def test_inspecting_a_day_does_not_mark_you_off():
    """The panel used to appear only after a click, so the only way to read
    who was away was to mark yourself away and click again to undo it — and
    the help text told people to do exactly that.

    Click stays with the frequent action (marking days in bulk). Reading a day
    moved to hover and keyboard focus, neither of which changes state."""
    js = JS.read_text()
    assert "mouseenter" in js and 'c.addEventListener("focus"' in js
    peek = js[js.index("Cal.prototype.peek"):]
    peek = peek[:peek.index("Cal.prototype.showDetail")]
    # A hovered day must not be recorded as a chosen one, or it would survive
    # the next re-render as though it had been clicked.
    assert "this.selected" not in peek, "hover sets selection"
    assert "onDay" not in peek and "saveFraming" not in peek, "hover writes"
    assert "this.busy" in peek, "hover can redraw mid-save"


def test_the_help_text_no_longer_tells_people_to_click_to_inspect():
    html = (ROOT / "app" / "artist-book-gigs.html").read_text()
    panel = html[html.index('id="availabilitySection"'):]
    panel = panel[:panel.index("</div>", panel.index("bandCalendar"))]
    assert "Click a day to see who" not in panel, "still advertises the destructive path"
    assert "Hover a day" in panel


def test_long_press_inspects_on_touch():
    """Phones and tablets have no hover. Without this the only way to read a
    day is to tap it — which marks you off — then tap again to undo."""
    js = JS.read_text()
    assert "LONG_PRESS_MS" in js
    assert 'addEventListener("touchstart"' in js
    # A finger that moves is a scroll, not a press.
    for ev in ("touchmove", "touchend", "touchcancel"):
        assert f'addEventListener("{ev}"' in js, ev


def test_the_synthetic_click_after_a_long_press_is_swallowed():
    """Touch fires a click on release, which would toggle the day the press
    just revealed — the exact problem the gesture exists to solve."""
    js = JS.read_text()
    blk = js[js.index('c.addEventListener("click"'):][:700]
    assert "self._lpAt" in blk
    # Time-based, not a boolean: if the click never arrives (finger lifts off
    # the grid, page scrolls away) a sticky flag eats the next genuine tap.
    assert "Date.now() -" in blk


def test_touch_press_does_not_trigger_the_ios_callout():
    """iOS offers a copy/selection callout on long press, landing on top of
    the panel being revealed."""
    js = JS.read_text()
    assert "-webkit-touch-callout:none" in js
    assert "touch-action:manipulation" in js
    assert 'addEventListener("contextmenu"' in js


def test_both_pages_tell_touch_users_about_the_gesture():
    for page in ("artist-book-gigs.html", "user-profile.html"):
        html = (ROOT / "app" / page).read_text()
        assert "press and hold" in html, page


def test_the_day_panel_labels_who_is_unavailable():
    js = JS.read_text()
    assert 'class="gfbc-unavail">Unavailable:' in js
    # Not on the empty state — "Unavailable:" over nothing reads as a bug.
    free = js[js.index("Everyone is available."):]
    assert "gfbc-unavail" not in free[:200]


def test_the_current_member_is_listed_first():
    """In a list of their own band the one name someone scans for is their
    own, and the server's order does not put it first."""
    js = JS.read_text()
    fn = js[js.index("var ordered = off.slice().sort"):][:260]
    assert "b.is_self" in fn and "a.is_self" in fn


# ── Unavailable marks on the booking calendar ───────────────────────────────

MARKS = ROOT / "app" / "static" / "js" / "artist-availability-marks.js"

def _css_rule(js: str, start_marker: str) -> str:
    """A CSS rule out of the injected style string, bounded by its closing
    brace rather than a character count — comments get added between the
    selector and its declarations, which silently shortens a fixed window."""
    i = js.index(start_marker)
    j = js.index('}"', i)
    return js[i:j]



def test_the_booking_calendar_shows_who_is_unavailable():
    """The band calendar knew who could not play; the calendar an artist
    actually books from did not. You could look straight at an open Saturday
    with half the band away and only find out after applying."""
    assert MARKS.exists()
    html = (ROOT / "app" / "artist-book-gigs.html").read_text()
    assert "artist-availability-marks.js" in html


def test_marks_are_injected_not_built_into_the_render_loop():
    """artist.book-gigs.js is ~127KB and already exposes
    .calendar-day[data-date] as a decoration hook for external-gig bubbles."""
    js = MARKS.read_text()
    assert ".calendar-day[data-date]" in js
    assert "MutationObserver" in js
    # Without the guard the observer sees its own marks and loops.
    assert "if (_injecting) return;" in js


def test_marks_are_cleared_calendar_wide_before_reinjecting():
    """Clearing only the dates in the fresh set strands a mark on a day whose
    last absence was just undone."""
    js = MARKS.read_text()
    fn = js[js.index("function inject()"):][:900]
    assert 'cal.querySelectorAll("." + MARK).forEach' in fn


def test_the_booking_calendar_marks_are_red_for_everyone():
    """On the booking calendar the question is "can we take this gig", so the
    marks use the same red as "cannot play" elsewhere rather than a colour per
    member — per-member hues read as decoration, and the darker ones looked
    black at 16px. The band calendar keeps its per-member colours, where
    telling members apart is the point."""
    js = MARKS.read_text()
    assert "hueFor" not in js, "per-member hues are back on the booking calendar"
    rule = _css_rule(js, 'MARK + " b {')
    assert "background:#ef4444" in rule and "color:#fff" in rule
    band = (ROOT / "app" / "static" / "js" / "band-calendar.js").read_text()
    assert "(Number(id) * 47) % 360" in band, "band calendar lost its per-member colours"


def test_a_crowded_day_collapses_to_a_count():
    """Three 16px squares already crowd a cell that may also carry gig
    bubbles."""
    js = MARKS.read_text()
    assert "off.slice(0, 2)" in js and '"+" + (off.length - 2)' in js


def test_clearing_a_globally_marked_day_from_a_band_calendar_works():
    """Reported 2026-10-08: a member marked days from his profile, then could
    not un-mark them on the band calendar — "it's not saving".

    A profile day is stored once with artist_id 0 and shows on every band's
    calendar. The toggle only ever created or deleted a band-scoped row, so
    the global one was never touched: the day never cleared, and the first
    click made it MORE unavailable by adding a band row on top."""
    src = ROUTES.read_text()
    fn = _func(src, "_toggle_day")
    assert "_row(0)" in fn, "a band click still ignores the global row"
    assert "_other_artist_ids" in fn, "clearing would silently un-mark every band"
    # Both rows can exist at once — the old bug added a band row on every
    # failed attempt — so one click has to clear whichever are present.
    assert "if own or glob:" in fn, "a day with both rows still needs two clicks"


def test_clearing_one_band_preserves_the_others():
    """Dropping the global row alone would make the member available
    everywhere from one band's screen — the opposite data loss."""
    src = ROUTES.read_text()
    fn = _func(src, "_toggle_day")
    blk = fn[fn.index("if own or glob:"):fn.index("return False")]
    assert "for other in _other_artist_ids" in blk
    assert "INSERT OR IGNORE INTO member_days_off" in blk


def test_the_profile_calendar_toggle_stays_simple():
    """artist_id 0 has no second scope to fall back to; it must not try to
    convert anything."""
    src = ROUTES.read_text()
    fn = _func(src, "_toggle_day")
    # artist_id 0 has no second scope to fall back to, so `glob` must be None
    # there and the conversion branch must not run.
    assert "_row(0) if artist_id != 0 else None" in fn, \
        "the conversion is not gated to band calendars"


# ── Choosing which bands a day applies to ───────────────────────────────────

def test_a_day_can_be_scoped_to_chosen_bands():
    """A member in several bands saw only "all bands" or an opaque "1 band"
    with no way to change it — the only route to a per-band day was to open
    that band's calendar and click there."""
    src = ROUTES.read_text()
    assert '@router.post("/api/me/days-off/scope")' in src
    js = JS.read_text()
    assert "gfbc-band" in js and "saveScope" in js


def test_the_cell_names_the_band_instead_of_counting_it():
    js = JS.read_text()
    assert '"1 band"' in js and "artist_name" in js
    fn = js[js.index("Personal view: say where the day applies"):][:600]
    assert "off[0].artist_name" in fn


def test_the_scope_control_cannot_trap_the_user():
    """An "All bands" radio was tried first: with it selected the per-band
    boxes were disabled, and a lone radio cannot be unticked, so there was no
    way to narrow the day."""
    js = JS.read_text()
    assert 'name="gfbcScope"' not in js, "the dead-end radio is back"
    assert "disabled" not in js[js.index("var opts = bands.length > 1"):][:700]


def test_every_band_ticked_is_stored_as_all_bands():
    """So it stays right when they later join another band."""
    src = ROUTES.read_text()
    fn = _func(src, "set_day_scope")
    assert "set(ids) == mine" in fn


def test_scoping_rejects_a_band_you_are_not_in():
    src = ROUTES.read_text()
    fn = _func(src, "set_day_scope")
    assert "403" in fn and "not a member" in fn


def test_a_day_you_cannot_play_reads_red():
    js = JS.read_text()
    rule = js[js.index(".gfbc-cell.gfbc-mine {"):][:160]
    assert "239,68,68" in rule, "still amber"


def test_the_instruction_sits_against_the_grid():
    """People start clicking before they read the prose above the card."""
    js = JS.read_text()
    assert "gfbc-banner" in js
    assert "can\\u2019t</b> play" in js or "can’t</b> play" in js
    # Red, matching the colour a marked day takes — instruction and result
    # should not say different things.
    rule = js[js.index(".gfbc-banner {"):][:260]
    assert "239,68,68" in rule, "banner is not red"


def test_the_marks_survive_the_calendar_background_reset():
    """artist-book-gigs.html carries a blanket
    `.calendar > * * { background: transparent !important }` — its own comment
    warns it "nukes the background -> invisible text". The chips rendered
    transparent with white text until the rule matched that force."""
    js = MARKS.read_text()
    rule = _css_rule(js, 'MARK + " b {')
    assert "background:#ef4444 !important" in rule


def test_each_mark_says_what_it_means_on_hover():
    """Initials alone do not say what they mean, and hovering the square is
    what someone will try — the row-level title is not reachable that way."""
    js = MARKS.read_text()
    assert "b.title = m.name" in js and "unavailable" in js
    # The "+N" chip names the people it is standing in for.
    assert "more.title = off.slice(2)" in js
