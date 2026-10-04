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
    FIELDS, OVR_PREFIX, ENABLED_COL, resolve, clean_payload, overridden_fields,
    select_sql,
)

ROOT = Path(__file__).resolve().parents[1]


def test_the_gig_keeps_its_own_copy_once_the_box_is_ticked():
    """All-or-nothing, not per field. The form prefills from the venue, so a
    field left exactly as the venue had it is still this gig's own answer."""
    venue = {"has_stage": 1, "has_sound_equipment": 0, "bar_tab_details": "Two drinks"}
    gig = {ENABLED_COL: 1, "ovr_has_stage": 0, "ovr_has_sound_equipment": 1,
           "ovr_bar_tab_details": "Two drinks"}
    got = resolve(gig, venue)
    assert got["has_sound_equipment"] == 1
    assert got["has_stage"] == 0, "an explicit 0 must beat a venue default of 1"


def test_an_untickedbox_inherits_everything():
    venue = {"has_stage": 1, "has_sound_equipment": 0}
    # Stale values may still sit in the columns; the flag is what decides.
    gig = {ENABLED_COL: 0, "ovr_has_sound_equipment": 1}
    assert resolve(gig, venue)["has_sound_equipment"] == 0


def test_a_later_venue_edit_does_not_reach_an_overridden_gig():
    """The point of the model: what the gig advertised stays what it
    advertised, even after the venue changes its standing answers."""
    gig = {ENABLED_COL: 1, "ovr_has_sound_equipment": 1, "ovr_bar_tab_details": "Open tab"}
    before = resolve(gig, {"has_sound_equipment": 0, "bar_tab_details": "Two drinks"})
    after = resolve(gig, {"has_sound_equipment": 1, "bar_tab_details": "CHANGED"})
    assert before["has_sound_equipment"] == after["has_sound_equipment"] == 1
    assert before["bar_tab_details"] == after["bar_tab_details"] == "Open tab"


def test_unticking_clears_the_stored_copy():
    """A venue that ticks then unticks must genuinely go back to the venue
    defaults, not keep a copy that no longer appears anywhere in the form."""
    out = clean_payload({"ovr_enabled": 0})
    assert out[ENABLED_COL] == 0
    assert all(out[OVR_PREFIX + f] is None for f in FIELDS)


def test_ticking_stores_every_field():
    out = clean_payload({"ovr_enabled": 1, "ovr_has_sound_equipment": "true",
                         "ovr_bar_tab_details": "Open tab tonight"})
    assert out[ENABLED_COL] == 1
    assert out["ovr_has_sound_equipment"] == 1
    assert len(out) == len(FIELDS) + 1, "a partial write would leave stale fields"


def test_an_absent_block_leaves_the_gig_untouched():
    """Endpoints that know nothing about the spec must not wipe it."""
    assert clean_payload({"title": "x"}) == {}


def test_a_bad_number_does_not_become_a_string_in_a_numeric_column():
    out = clean_payload({"ovr_enabled": 1, "ovr_stage_width_ft": "wide"})
    assert out["ovr_stage_width_ft"] is None


def test_every_spec_consumer_resolves_rather_than_reading_the_venue():
    """Reading v.has_sound_equipment directly ignores the override, and the
    symptom is a gig quietly missing from search — not an error."""
    for rel in ("backend/routes/gigs.py", "backend/routes/gig_modal.py"):
        src = (ROOT / rel).read_text()
        for col in ("has_sound_equipment", "has_stage", "has_lighting"):
            for m in re.finditer(r"\bv\.%s\b" % col, src):
                line_start = src.rfind("\n", 0, m.start())
                line = src[line_start:src.find("\n", m.end())]
                assert "CASE WHEN" in line or "COALESCE" in line, \
                    f"{rel}: unresolved read -> {line.strip()}"


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


def test_the_form_always_sends_the_flag():
    """Unticking has to reach the server as an explicit 0 or the gig keeps
    showing a spec the venue already turned off."""
    js = (ROOT / "app" / "static" / "js" / "gig-spec-overrides.js").read_text()
    fn = js[js.index("payload: function ()"):][:700]
    assert "ovr_enabled: enabled() ? 1 : 0" in fn


def test_the_form_prefills_from_the_venue():
    """It is the venue edit form, pre-filled — not a blank page they retype."""
    js = (ROOT / "app" / "static" / "js" / "gig-spec-overrides.js").read_text()
    assert "function prefill()" in js
    # Re-ticking a gig that already has its own copy must not overwrite it.
    mount = js[js.index('chk.addEventListener("change"'):][:400]
    assert "!_hasOwnCopy" in mount


# ── Telling the venue which gigs won't follow a settings change ─────────────

def test_affected_gigs_exclude_the_past():
    """Rewriting what a finished gig advertised would change the record after
    the fact, including on a signed contract."""
    src = (ROOT / "backend" / "services" / "gig_spec.py").read_text()
    fn = src[src.index("def gigs_with_own_setup"):src.index("def copy_venue_defaults_to")]
    assert "date(g.date) >= date(:frm)" in fn
    assert "!= 'cancelled'" in fn


def test_affected_gigs_report_what_actually_differs():
    """A gig pinned only for its PA, at a venue that just edited its bar tab,
    needs no action — so the venue is told which fields drifted, not just
    that the gig is pinned."""
    src = (ROOT / "backend" / "services" / "gig_spec.py").read_text()
    fn = src[src.index("def gigs_with_own_setup"):src.index("def copy_venue_defaults_to")]
    assert '"differs"' in fn
    # 0 / "0" / False and ""/NULL must not read as differences.
    assert "def _same" in fn and "float(a) == float(b)" in fn


def test_the_sync_cannot_reach_another_venues_gigs():
    src = (ROOT / "backend" / "services" / "gig_spec.py").read_text()
    fn = src[src.index("def copy_venue_defaults_to"):]
    assert "venue_id = :vid" in fn, "a crafted id list could hit other venues"
    routes = (ROOT / "backend" / "routes" / "gigs.py").read_text()
    seg = routes[routes.index("def sync_venue_defaults_to_gigs"):][:900]
    assert "check_venue_access" in seg


def test_the_warning_only_fires_for_a_spec_change():
    """A venue editing its description or socials should not be asked about
    gig setups."""
    js = (ROOT / "app" / "static" / "js" / "venue-spec-drift.js").read_text()
    assert "function touchesSpec" in js
    fn = js[js.index("function touchesSpec"):][:600]
    assert "SPEC_KEYS.some" in fn


def test_the_warning_hooks_fetch_not_one_save_handler():
    """venue.edit.js PUTs to /api/venues/{id} from six places and more get
    added; hooking one and missing the rest makes the warning appear only
    sometimes, which is worse than not having it."""
    js = (ROOT / "app" / "static" / "js" / "venue-spec-drift.js").read_text()
    assert "window.fetch = function" in js
    assert "res && res.ok" in js, "warns even when the save failed"
