"""Per-gig overrides of the venue's room spec.

2026-10-07. A venue's spec — stage, PA, sound engineer, lighting, load-in,
arrival window, bar and food tabs — used to be a property of the venue alone,
and every gig inherited it live through a JOIN.

That broke a normal case. A venue whose default is "no PA" but who is
providing one for a big night had no way to say so, and the gig was **filtered
out of artist search** for everyone who ticks "has sound equipment" — losing
applicants on the gig the venue cared most about. The reverse was worse: a
venue defaulting to "PA provided" that is not providing one for a particular
night had artists apply believing there was a PA and find out on the night.

Now each gig carries a nullable `ovr_*` column per field. NULL means "inherit
the venue default", which is the common case, so nothing needed backfilling.

**Everything that reads a spec field must come through here.** Reading
`v.has_sound_equipment` directly is the bug this module exists to prevent: it
silently ignores the override, and because artist search filters on these
fields the symptom is a gig quietly missing from results rather than anything
that looks like an error.
"""

# Every overridable field, with the SQL type of the venue column it shadows.
# Booleans are stored 0/1; the venue columns are the same shape.
FIELDS = (
    "has_stage",
    "stage_width_ft",
    "stage_depth_ft",
    "setup_location_description",
    "has_sound_equipment",
    "sound_equipment_description",
    "has_sound_engineer",
    "sound_engineer_details",
    "has_lighting",
    "lighting_description",
    "load_in_out_details",
    "arrival_time_type",
    "arrival_no_earlier_than_hour",
    "arrival_no_earlier_than_period",
    "bar_tab_details",
    "food_tab_details",
)

BOOL_FIELDS = frozenset({
    "has_stage", "has_sound_equipment", "has_sound_engineer", "has_lighting",
})
NUM_FIELDS = frozenset({"stage_width_ft", "stage_depth_ft"})

OVR_PREFIX = "ovr_"


ENABLED_COL = OVR_PREFIX + "enabled"


def select_sql(gig_alias: str = "g", venue_alias: str = "v") -> str:
    """The SELECT fragment that resolves every field, aliased to the plain
    venue column name so existing consumers keep working unchanged.

    All-or-nothing on `ovr_enabled`, not a per-field COALESCE. When a venue
    ticks "this gig is different" the form prefills from the venue and the gig
    then carries a complete copy, so a field left exactly as the venue had it
    is still that gig's own answer. A COALESCE would quietly re-point such a
    field at the venue the next time the venue profile changed, and only that
    field — the gig would be half snapshot, half live.
    """
    flag = f"COALESCE({gig_alias}.{ENABLED_COL}, 0)"
    return ",\n".join(
        f"CASE WHEN {flag} = 1 THEN {gig_alias}.{OVR_PREFIX}{f} "
        f"ELSE {venue_alias}.{f} END AS {f}"
        for f in FIELDS
    )


def resolve(gig_row, venue_row) -> dict:
    """Resolve in Python, for callers that already hold both rows.

    An override of 0 or "" is a real answer — "no stage", "no bar tab" — so
    only None counts as absent. `or` would quietly fall back to the venue
    default for exactly the values a venue is most likely to be overriding.
    """
    gig_row = gig_row or {}
    venue_row = venue_row or {}
    on = str(gig_row.get(ENABLED_COL) or 0) in ("1", "True", "true")
    if not on:
        return {f: venue_row.get(f) for f in FIELDS}
    return {f: gig_row.get(OVR_PREFIX + f) for f in FIELDS}


def overridden_fields(gig_row) -> list:
    """Which fields this gig overrides. Drives the "differs from your usual
    setup" badge so a venue can see at a glance that a gig is not standard."""
    gig_row = gig_row or {}
    if str(gig_row.get(ENABLED_COL) or 0) not in ("1", "True", "true"):
        return []
    return list(FIELDS)


def clean_payload(data: dict) -> dict:
    """Pull the override block out of an API payload.

    The form is all-or-nothing, matching what a venue sees:

      ovr_enabled 0/absent → clears every field and the gig inherits again.
                             Clearing matters: a venue that ticks the box and
                             then unticks it must actually go back to the
                             venue defaults, not keep a stale copy that no
                             longer shows anywhere in the form.
      ovr_enabled 1        → stores every field. The form prefills from the
                             venue, so even untouched fields are a deliberate
                             statement of what this gig provides.
    """
    src = data.get("spec_overrides") if isinstance(data.get("spec_overrides"), dict) else data
    if ENABLED_COL not in src and "spec_override_enabled" not in src:
        return {}                       # absent entirely: leave the gig as-is

    raw_flag = src.get(ENABLED_COL, src.get("spec_override_enabled"))
    on = str(raw_flag).strip().lower() in ("1", "true", "yes", "on")
    if not on:
        out = {ENABLED_COL: 0}
        out.update({OVR_PREFIX + f: None for f in FIELDS})
        return out

    out = {ENABLED_COL: 1}
    for f in FIELDS:
        raw = src.get(OVR_PREFIX + f, src.get(f))
        if f in BOOL_FIELDS:
            out[OVR_PREFIX + f] = 1 if str(raw).strip().lower() in ("1", "true", "yes", "on") else 0
        elif f in NUM_FIELDS:
            try:
                out[OVR_PREFIX + f] = None if raw in (None, "") else float(raw)
            except (TypeError, ValueError):
                out[OVR_PREFIX + f] = None
        else:
            txt = "" if raw is None else str(raw).strip()[:2000]
            out[OVR_PREFIX + f] = txt or None
    return out


def apply_to_venue(db, gig_id, venue: dict) -> dict:
    """Overlay a gig's overrides onto a venue row fetched with `SELECT v.*`.

    For the paths that already hold a whole venue dict — contract generation
    most importantly, where the rendered terms say either "Sound equipment
    provided" or "Performer shall provide their own sound equipment". Getting
    that wrong puts the wrong obligation in a document someone signs.

    Returns the dict unchanged when the gig is gone or has no overrides.
    """
    from sqlalchemy import text as _text
    if not gig_id or venue is None:
        return dict(venue or {})
    cols = ", ".join(OVR_PREFIX + f for f in FIELDS)
    try:
        row = db.execute(_text(f"SELECT {cols} FROM gigs WHERE id = :gid"),
                         {"gid": gig_id}).mappings().first()
    except Exception:
        return dict(venue)
    if not row:
        return dict(venue)
    out = dict(venue)
    out.update(resolve(row, venue))
    return out


def save_overrides(db, gig_ids, data: dict) -> int:
    """Write overrides for one gig or a whole recurring series.

    Applied as its own UPDATE after the rows exist, rather than threaded
    through the INSERTs — gig creation already builds several different
    statements (single, multi-slot, recurring series) and adding sixteen
    columns to each is how one path quietly gets missed.

    `gig_ids` may be a single id or an iterable, so a series gets the same
    spec in one call. Returns the number of columns written; 0 means the
    payload carried no override keys and nothing was touched.
    """
    from sqlalchemy import text as _text
    cols = clean_payload(data)
    if not cols:
        return 0
    ids = [gig_ids] if isinstance(gig_ids, (int, str)) else list(gig_ids or [])
    ids = [int(i) for i in ids if i]
    if not ids:
        return 0
    assigns = ", ".join(f"{k} = :{k}" for k in cols)
    for gid in ids:
        params = dict(cols)
        params["gid"] = gid
        db.execute(_text(f"UPDATE gigs SET {assigns} WHERE id = :gid"), params)
    return len(cols)


def gigs_with_own_setup(db, venue_id: int, from_date=None):
    """Future gigs at this venue that carry their own spec copy.

    A venue that edits its room settings needs to know which gigs will NOT
    pick the changes up — that is the cost of the snapshot model, and leaving
    a venue to discover it when an artist turns up expecting the old setup is
    not acceptable.

    Each row carries the fields that now differ from the venue's current
    answers, so the venue can see whether the drift actually matters: a gig
    pinned only because of its PA, on a venue that just edited its bar tab,
    needs no action.

    Past gigs are excluded. Rewriting what a finished gig advertised would
    change the record after the fact, including on a signed contract.
    """
    from sqlalchemy import text as _text
    from datetime import date as _date
    frm = str(from_date or _date.today())[:10]
    cols = ", ".join("g." + OVR_PREFIX + f for f in FIELDS)
    rows = db.execute(_text(f"""
        SELECT g.id, g.date, g.start_time, g.title, g.status, {cols}
        FROM gigs g
        WHERE g.venue_id = :vid
          AND COALESCE(g.{ENABLED_COL}, 0) = 1
          AND date(g.date) >= date(:frm)
          AND (g.status IS NULL OR g.status != 'cancelled')
        ORDER BY g.date, g.start_time
    """), {"vid": venue_id, "frm": frm}).mappings().all()
    if not rows:
        return []

    venue = db.execute(_text("SELECT * FROM venues WHERE id = :v"),
                       {"v": venue_id}).mappings().first() or {}

    def _same(a, b):
        # 0 vs "0" vs False all mean the same thing across SQLite and PG, and
        # an empty string and NULL are both "nothing said".
        if a is None and b is None:
            return True
        if a in (None, "") and b in (None, ""):
            return True
        try:
            return float(a) == float(b)
        except (TypeError, ValueError):
            return str(a).strip() == str(b).strip()

    out = []
    for r in rows:
        differs = [f for f in FIELDS if not _same(r[OVR_PREFIX + f], venue.get(f))]
        out.append({
            "gig_id": r["id"], "date": str(r["date"])[:10],
            "start_time": r["start_time"], "title": r["title"],
            "status": r["status"], "differs": differs,
        })
    return out


def copy_venue_defaults_to(db, venue_id: int, gig_ids) -> int:
    """Overwrite these gigs' stored copies with the venue's current answers.

    Keeps the flag ON. The gig stays pinned — it was marked different for a
    reason and the venue may want it to stay that way — it is simply re-based
    on today's venue settings. A venue that wants the gig to follow the venue
    from now on unticks the box on the gig instead, which clears the copy.

    Scoped to gigs belonging to this venue, so a crafted id list cannot reach
    another venue's gigs.
    """
    from sqlalchemy import text as _text
    ids = [int(i) for i in (gig_ids or []) if str(i).strip()]
    if not ids:
        return 0
    venue = db.execute(_text("SELECT * FROM venues WHERE id = :v"),
                       {"v": venue_id}).mappings().first()
    if not venue:
        return 0
    assigns = ", ".join(f"{OVR_PREFIX}{f} = :{f}" for f in FIELDS)
    params = {f: venue.get(f) for f in FIELDS}
    n = 0
    for gid in ids:
        p = dict(params)
        p["gid"] = gid
        p["vid"] = venue_id
        res = db.execute(_text(
            f"UPDATE gigs SET {assigns} WHERE id = :gid AND venue_id = :vid "
            f"AND COALESCE({ENABLED_COL}, 0) = 1"), p)
        n += (res.rowcount or 0)
    return n
