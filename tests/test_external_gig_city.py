"""External gigs do not block on the city list.

Reported 2026-10-06: typing "Hermosa Beach" and then clicking the Notes field
bounced focus straight back to City, with nothing on screen saying why.

Cause: attachCityValidation installs a capture-phase document click handler
(`blockClicks`) that swallows every click outside the city input and refocuses
it until the value matches us_cities.py. That list has real gaps — Hermosa
Beach, Manhattan Beach, El Segundo and Culver City are all missing — so the
form became unusable with no explanation.

It is the wrong gate for this form regardless of the list. An external gig is
the artist's private record of a show that did not happen through GigsFill;
the city is display-only and feeds no search, matching or radius query.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXT = ROOT / "app" / "static" / "js" / "artist-external-gigs.js"
CITY = ROOT / "app" / "static" / "js" / "city-autocomplete.js"


def test_external_gig_city_does_not_block_the_form():
    src = EXT.read_text()
    call = src[src.index("window.initCityAutocomplete({"):][:260]
    assert "validate: false" in call, "the blocking validator is back on this form"


def test_the_autocomplete_itself_is_kept():
    """Only the blocking is dropped — suggestions and the state auto-fill are
    the useful half and still run."""
    src = EXT.read_text()
    assert "window.initCityAutocomplete" in src
    assert "stateId: 'extGigVenueState'" in src


def test_other_forms_still_validate():
    """Signup and profile cities DO feed search and radius matching, so the
    opt-out must stay scoped to this one form."""
    optouts = [p.name for p in (ROOT / "app" / "static" / "js").glob("*.js")
               if "validate: false" in p.read_text()]
    assert optouts == ["artist-external-gigs.js"], optouts


def test_the_block_is_a_capture_phase_document_handler():
    """Documents why a click anywhere was being eaten — it is not scoped to
    the field or its form."""
    src = CITY.read_text()
    assert "document.addEventListener('click', blockClicks, true)" in src
    fn = src[src.index("function blockClicks"):][:700]
    assert "_blockedInput.focus()" in fn
