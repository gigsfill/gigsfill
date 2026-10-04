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


def select_sql(gig_alias: str = "g", venue_alias: str = "v") -> str:
    """The SELECT fragment that resolves every field, aliased to the plain
    venue column name so existing consumers keep working unchanged.

    Callers that previously listed `v.has_sound_equipment` swap that line for
    this fragment and get the resolved value under the same key.
    """
    return ",\n".join(
        f"COALESCE({gig_alias}.{OVR_PREFIX}{f}, {venue_alias}.{f}) AS {f}"
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
    out = {}
    for f in FIELDS:
        v = gig_row.get(OVR_PREFIX + f)
        out[f] = venue_row.get(f) if v is None else v
    return out


def overridden_fields(gig_row) -> list:
    """Which fields this gig overrides. Drives the "differs from your usual
    setup" badge so a venue can see at a glance that a gig is not standard."""
    gig_row = gig_row or {}
    return [f for f in FIELDS if gig_row.get(OVR_PREFIX + f) is not None]


def clean_payload(data: dict) -> dict:
    """Pull override values out of an API payload, normalised.

    Accepts either `ovr_has_stage` or a nested `spec_overrides` object. An
    explicit null clears the override and returns the gig to the venue
    default — that has to stay expressible, or a venue could set an override
    and never undo it.
    """
    src = data.get("spec_overrides") if isinstance(data.get("spec_overrides"), dict) else data
    out = {}
    for f in FIELDS:
        key = OVR_PREFIX + f
        raw = src.get(key, src.get(f) if "spec_overrides" in data else None)
        if key not in src and not ("spec_overrides" in data and f in src):
            continue                      # absent entirely: leave as-is
        if raw is None or (isinstance(raw, str) and raw.strip() == "" and f not in BOOL_FIELDS):
            out[key] = None               # explicit clear
            continue
        if f in BOOL_FIELDS:
            out[key] = 1 if str(raw).strip().lower() in ("1", "true", "yes", "on") else 0
        elif f in NUM_FIELDS:
            try:
                out[key] = float(raw)
            except (TypeError, ValueError):
                out[key] = None
        else:
            out[key] = str(raw).strip()[:2000] or None
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
