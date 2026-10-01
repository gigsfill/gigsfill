"""Artist-chosen hero image.

The profile hero used to be "whatever picture happens to be first", which
for the first band we looked at meant a dark backstage shot while a far
better daylight photo sat further down their gallery. Artists now pick it.

The security-relevant part is that `hero_media_id` is a client-supplied id
pointing at a row in `artist_media`. Without an ownership check, a crafted
PUT could set one artist's hero to another artist's upload — publishing
someone else's image on a page they don't control.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ARTISTS = ROOT / "backend" / "routes" / "artists.py"


def test_hero_media_id_is_ownership_checked():
    src = ARTISTS.read_text()
    idx = src.index("_hero_media_id = None")
    block = src[idx:idx + 1600]
    # Must verify the row belongs to this artist, not merely that it exists.
    assert "FROM artist_media" in block
    assert "artist_id = :aid" in block
    assert "id = :mid" in block
    assert "does not belong to this artist" in block


def test_hero_media_id_rejects_non_numeric():
    src = ARTISTS.read_text()
    idx = src.index("_hero_media_id = None")
    block = src[idx:idx + 1600]
    assert "must be a number" in block


def test_hero_can_be_cleared():
    """COALESCE keeps the existing value when the bind is NULL, so clearing
    needs its own UPDATE — otherwise a venue could set a cover and never
    get back to the default."""
    src = ARTISTS.read_text()
    idx = src.index("_hero_media_id = None")
    block = src[idx:idx + 1600]
    assert "SET hero_media_id = NULL" in block


def test_hero_media_id_in_schema_and_orm():
    """CLAUDE.md: db.py and models.py are kept in sync by hand."""
    assert "hero_media_id" in (ROOT / "backend" / "db.py").read_text()
    assert "hero_media_id" in (ROOT / "backend" / "models.py").read_text()


def test_public_artist_endpoint_exposes_hero_media_id():
    """The profile can't honour the choice if the field never reaches it."""
    src = ARTISTS.read_text()
    idx = src.index('WHERE id=:id\n              AND deleted_at IS NULL')
    assert "hero_media_id" in src[max(0, idx - 900):idx]


def test_profile_falls_back_when_no_hero_chosen():
    """Most artists won't set one, so the fallback chain has to hold:
    chosen -> first picture -> profile image -> gradient."""
    js = (ROOT / "app" / "static" / "js" / "artist-profile-v2.js").read_text()
    idx = js.index("var chosen = null;")
    block = js[idx:idx + 700]
    assert "a.hero_media_id" in block
    assert "pics[0]" in block
    assert "profile.file_path" in block


def test_profile_hero_only_uses_this_artists_media():
    """The chosen id is matched against the media list already fetched for
    this artist, so a stale or foreign id can't pull in another image."""
    js = (ROOT / "app" / "static" / "js" / "artist-profile-v2.js").read_text()
    idx = js.index("var chosen = null;")
    block = js[idx:idx + 700]
    assert "media.filter" in block
