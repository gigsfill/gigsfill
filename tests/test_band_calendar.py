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
    # Available in both modes: the button lives in the shared header.
    hdr = js[js.index("var legend"):js.index("this.root.innerHTML")]
    assert "data-list" not in hdr, "button should not be mode-gated"


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
