"""The US city list.

Regenerated 2026-10-06 from the US Census 2023 Gazetteer "Places" file (public
domain) after a venue in Hermosa Beach could not finish signup: the hand-built
4,792-entry list was missing it, and the city field blocks until the value
validates.
"""

from backend import us_cities


def test_the_public_surface_is_intact():
    """Regenerating this module once dropped calculate_distance,
    find_nearest_cities and get_cities_by_state, and the API would not boot —
    routes/cities.py imports them at module level. Every name other modules
    rely on is pinned here."""
    for name in ("US_CITIES", "find_city", "search_cities", "calculate_distance",
                 "find_nearest_cities", "get_cities_by_state", "haversine_distance"):
        assert hasattr(us_cities, name), f"us_cities.{name} is gone"


def test_the_cities_that_prompted_this_are_present():
    for city, state in (("Hermosa Beach", "CA"), ("Manhattan Beach", "CA"),
                        ("El Segundo", "CA"), ("Culver City", "CA"),
                        ("Calabasas", "CA"), ("Agoura Hills", "CA")):
        assert us_cities.find_city(city, state), f"{city}, {state} still missing"


def test_a_name_ending_in_city_is_not_truncated():
    """The Census appends the place type in lowercase ("Indianapolis city"),
    while a name genuinely ending in that word capitalises it ("Carson City").
    Stripping case-insensitively turned Nevada's capital into "Carson"."""
    assert us_cities.find_city("Carson City", "NV")
    assert us_cities.find_city("Oklahoma City", "OK")
    assert us_cities.find_city("Kansas City", "MO")
    assert us_cities.find_city("Jersey City", "NJ")
    assert not us_cities.find_city("Carson", "NV"), "truncated name leaked in"


def test_consolidated_city_counties_answer_to_their_everyday_name():
    """Nashville is filed as "Nashville-Davidson" and Athens as "Athens-Clarke
    County". Without an alias, searching "Nashville" finds small towns in AR,
    GA and IL but not Music City."""
    for city, state in (("Nashville", "TN"), ("Athens", "GA"), ("Augusta", "GA"),
                        ("Lexington", "KY"), ("Indianapolis", "IN")):
        hit = us_cities.find_city(city, state)
        assert hit, f"{city}, {state} not found"
        assert hit["lat"] and hit["lon"], f"{city} has no coordinates"


def test_coverage_is_materially_better_than_the_old_list():
    """The old list held 4,792 with 202 in California, against roughly 480
    incorporated CA cities alone."""
    assert len(us_cities.US_CITIES) > 30000, len(us_cities.US_CITIES)
    ca = us_cities.get_cities_by_state("CA")
    assert len(ca) > 1000, len(ca)
    assert len({c["state"] for c in us_cities.US_CITIES}) >= 51


def test_every_row_has_usable_coordinates():
    """artist.book-gigs.js and public-gigs.js filter by radius client-side off
    this payload, so a row without coordinates silently drops out of search."""
    bad = [c for c in us_cities.US_CITIES
           if not isinstance(c.get("lat"), float) or not isinstance(c.get("lon"), float)
           or not (-180 <= c["lon"] <= 180) or not (-90 <= c["lat"] <= 90)]
    assert not bad, bad[:5]


def test_lookups_are_indexed_not_scanned():
    """At 32k rows a linear find_city cost 0.4ms and ran on every blur."""
    src = (__import__("pathlib").Path(us_cities.__file__)).read_text()
    assert "_BY_NAME_STATE" in src and "_BY_NAME" in src
    fn = src[src.index("def find_city"):src.index("def search_cities")]
    assert "for " not in fn, "find_city still scans the table"


def test_no_duplicate_city_state_pairs():
    seen = set()
    for c in us_cities.US_CITIES:
        k = (c["city"].lower(), c["state"])
        assert k not in seen, k
        seen.add(k)
