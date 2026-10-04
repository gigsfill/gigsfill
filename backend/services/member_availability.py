"""Member availability — the single source of truth.

2026-10-05. Artist-level blackouts are gone. They were a hard block that
removed a band from bookings, preferred-artist blasts, the open-gig digest
and hold offers — and that turned out to be the wrong shape for a live-music
marketplace, because a four-piece whose drummer is away is still a trio, and
a trio whose bass player is away is still a duo. Suppressing the offer took
the decision away from the band.

What is left is one fact: which members have marked themselves off. Everything
else is derived from it.

  members_off()  — who cannot play on a date, and why.
  everyone_off() — nobody in the band is free. The only thing that still
                   suppresses an email, because an offer no possible line-up
                   could accept is pure noise.

`SQL_NO_MEMBER_FREE` is the same rule as a fragment, for the blast and digest
queries that filter thousands of rows in SQL and cannot call into Python per
artist. It is written once here so the two can never drift.
"""
from sqlalchemy import text


# Statuses that mean a slot is a real commitment rather than a browse. The
# same four _check_artist_time_conflict uses in routes/gigs.py — defined once
# here so the calendar and the booking guard cannot drift on what "booked"
# means.
COMMITTED_SLOT_STATUSES = (
    "('booked','pending_contract','awaiting_venue_contract','pending_venue_approval')"
)


# Embedded in a WHERE as `AND NOT (...)` to keep a band that has anyone free.
# `{a}` is the alias of the artists row in the outer query; `{d}` is the name
# of the gig-date bind.
#
# "Nobody is free" is expressed as NOT EXISTS(a member with no day-off row)
# rather than "every member has a row", because the latter is false for a band
# with no members at all and would silently suppress nothing. The EXISTS guard
# makes the no-members case explicit instead: a band with no members is never
# treated as fully unavailable.
SQL_NO_MEMBER_FREE = """
    EXISTS (
        SELECT 1 FROM (
            SELECT user_id FROM artists WHERE id = {a}.id AND user_id IS NOT NULL
            UNION
            SELECT user_id FROM entity_users
            WHERE entity_type = 'artist' AND entity_id = {a}.id
        ) _any_m
    )
    AND NOT EXISTS (
        SELECT 1 FROM (
            SELECT user_id FROM artists WHERE id = {a}.id AND user_id IS NOT NULL
            UNION
            SELECT user_id FROM entity_users
            WHERE entity_type = 'artist' AND entity_id = {a}.id
        ) _free_m
        WHERE NOT EXISTS (
            SELECT 1 FROM member_days_off d
            WHERE d.user_id = _free_m.user_id
              AND (d.artist_id = 0 OR d.artist_id = {a}.id)
              AND date(d.day) = date({d})
        )
    )
"""


def sql_no_member_free(artist_alias: str = "a", date_bind: str = ":gdate") -> str:
    """The fragment above, bound to a query's own alias and date parameter."""
    return SQL_NO_MEMBER_FREE.format(a=artist_alias, d=date_bind)


def members_off(db, artist_id: int, day: str):
    """Who in this band cannot play on `day`.

    Returns [{user_id, name, scope, with_artist, venue}], where scope is
    "all"  — marked off from their own profile, so every band they play in
    "band" — marked off on this band's calendar
    "gig"  — committed to a gig with another band (derived; see
             availability._other_band_commitments)

    Imported lazily to avoid a circular import: routes.availability already
    imports this module for the SQL fragment.
    """
    if not day:
        return []
    from backend.routes.availability import _member_blackouts_for_gig
    return _member_blackouts_for_gig(db, artist_id, str(day)[:10])


def everyone_off(db, artist_id: int, day: str) -> bool:
    """True when no member of this band is free on `day`.

    The one case that still suppresses an email. It cannot hurt a stripped-down
    line-up: if a duo could play, at least one member is free and the offer
    still goes out.
    """
    if not day:
        return False
    row = db.execute(text(f"""
        SELECT CASE WHEN {sql_no_member_free('a', ':gdate')} THEN 1 ELSE 0 END AS hit
        FROM artists a WHERE a.id = :aid
    """), {"aid": artist_id, "gdate": str(day)[:10]}).first()
    return bool(row and row[0])


def describe_for_email(db, artist_id: int, day: str) -> str:
    """One plain-text line naming who is away, for the gig emails.

    Empty string when everyone is free, so callers can drop the line entirely
    rather than printing "Unavailable: none".
    """
    off = members_off(db, artist_id, day)
    if not off:
        return ""
    parts = []
    for m in off:
        if m.get("scope") == "gig" and m.get("with_artist"):
            parts.append(f"{m['name']} (playing with {m['with_artist']})")
        else:
            parts.append(m["name"])
    return ", ".join(parts)
