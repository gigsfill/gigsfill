"""Per-gig overrides of the venue's room spec.

Every gig used to inherit the venue spec through a live JOIN, so a venue
whose default is "no PA" could not say it was bringing one for a particular
night — and because artist search FILTERS on these fields, that gig was
quietly dropped from results for everyone requiring sound equipment. The
venue lost applicants on the gig it cared most about, with nothing on screen
to explain it.
"""

import re
from pathlib import Path

from backend.services.gig_spec import (
    FIELDS, OVR_PREFIX, resolve, clean_payload, overridden_fields, select_sql,
)

ROOT = Path(__file__).resolve().parents[1]


def test_an_override_of_zero_beats_a_venue_default_of_one():
    """The case a naive `or` gets wrong, and the most likely override there
    is: a venue that normally has a stage but not for this gig."""
    venue = {"has_stage": 1, "has_sound_equipment": 1}
    assert resolve({"ovr_has_stage": 0}, venue)["has_stage"] == 0
    assert resolve({}, venue)["has_stage"] == 1


def test_an_empty_string_override_is_a_real_answer():
    venue = {"bar_tab_details": "Two drinks"}
    assert resolve({"ovr_bar_tab_details": ""}, venue)["bar_tab_details"] == ""
    assert resolve({}, venue)["bar_tab_details"] == "Two drinks"


def test_only_none_means_inherit():
    venue = {f: "venue" for f in FIELDS}
    assert resolve({OVR_PREFIX + f: None for f in FIELDS}, venue)["bar_tab_details"] == "venue"


def test_an_override_can_be_cleared():
    """A venue has to be able to undo one, not only set it."""
    assert clean_payload({"ovr_has_sound_equipment": None}) == {"ovr_has_sound_equipment": None}
    # Absent entirely is different from explicitly null: untouched.
    assert clean_payload({"title": "x"}) == {}


def test_booleans_survive_the_round_trip_from_a_form():
    assert clean_payload({"ovr_has_stage": "true"})["ovr_has_stage"] == 1
    assert clean_payload({"ovr_has_stage": False})["ovr_has_stage"] == 0
    assert clean_payload({"ovr_has_stage": "0"})["ovr_has_stage"] == 0


def test_a_bad_number_does_not_become_a_string_in_a_numeric_column():
    assert clean_payload({"ovr_stage_width_ft": "wide"})["ovr_stage_width_ft"] is None


def test_every_spec_consumer_resolves_rather_than_reading_the_venue():
    """Reading v.has_sound_equipment directly ignores the override, and the
    symptom is a gig quietly missing from search — not an error."""
    for rel in ("backend/routes/gigs.py", "backend/routes/gig_modal.py"):
        src = (ROOT / rel).read_text()
        for col in ("has_sound_equipment", "has_stage", "has_lighting"):
            for m in re.finditer(r"\bv\.%s\b" % col, src):
                line_start = src.rfind("\n", 0, m.start())
                line = src[line_start:src.find("\n", m.end())]
                assert "COALESCE" in line, f"{rel}: unresolved read -> {line.strip()}"


def test_the_contract_follows_the_override():
    """The terms say either "Sound equipment provided" or "Performer shall
    provide their own sound equipment" — the wrong obligation in a document
    someone signs."""
    src = (ROOT / "backend" / "routes" / "contracts.py").read_text()
    gen = src[src.index("def generate_auto_contract"):][:3000]
    assert "apply_to_venue" in gen
    # The preview has no gig and must keep using venue defaults.
    prev = src[src.index("def preview_auto_contract"):][:1500]
    assert "apply_to_venue" not in prev


def test_overrides_are_written_on_every_create_and_edit_path():
    src = (ROOT / "backend" / "routes" / "gigs.py").read_text()
    for fn in ("def create_gig", "def update_gig", "def update_recurring_series"):
        seg = src[src.index(fn):]
        seg = seg[:seg.index("\n@router.")] if "\n@router." in seg else seg
        assert "_save_spec" in seg, fn


def test_the_column_names_cannot_collide_with_the_venue_columns():
    """Several queries do `SELECT g.*, v.has_stage ...`; same-named columns on
    both sides collide in the row dict and the last one silently wins."""
    assert all(c.startswith(OVR_PREFIX) for c in
               [OVR_PREFIX + f for f in FIELDS])
    schema = (ROOT / "backend" / "db.py").read_text()
    block = schema[schema.index('_add_columns(cursor, "gigs", ['):][:2200]
    for f in FIELDS:
        assert f'"{OVR_PREFIX}{f} ' in block, f


def test_the_form_sends_every_key_so_clearing_works():
    """Sending only the filled controls would make an override impossible to
    undo — the server would never hear it should go back to inheriting."""
    js = (ROOT / "app" / "static" / "js" / "gig-spec-overrides.js").read_text()
    fn = js[js.index("payload: function ()"):][:600]
    assert 'raw === "" ? null' in fn


def test_the_form_offers_a_third_state_not_a_checkbox():
    """A checkbox has two states; this needs three. "Use my venue default"
    must be distinguishable from an explicit No."""
    js = (ROOT / "app" / "static" / "js" / "gig-spec-overrides.js").read_text()
    assert "Use my venue default" in js
    assert 'kind === "bool"' in js and "<select" in js
