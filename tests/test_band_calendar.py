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


def test_only_an_artist_admin_can_block_the_whole_band():
    """It silences gig blasts and the open-gig digest for everyone."""
    src = ROUTES.read_text()
    assert "check_artist_access" in _func(src, "toggle_band_wide_day")


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
