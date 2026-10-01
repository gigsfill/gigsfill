"""Setlist editor: paste is the only way in.

There were two input paths — a paste box and a one-song-at-a-time
title/artist form. The form duplicated the paste box for a worse
experience: a covers band with 200 songs is the normal case, and nobody
enters that two fields at a time. The paste box accepts a single line just
as happily, so removing the form lost nothing.

The backend keeps both POST /setlist and POST /setlist/bulk. Only the UI
narrowed — the single-add endpoint stays for API callers and in case the
form is ever wanted back.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JS = ROOT / "app" / "static" / "js" / "artist-setlist-edit.js"


def test_single_song_form_is_gone():
    src = JS.read_text()
    for gone in ("setlistAddTitle", "setlistAddArtist", "setlistAddBtn", "setlistAddStatus"):
        assert gone not in src, f"{gone} still present"


def test_no_orphaned_handlers():
    """Removing markup without its handler leaves dead listeners that throw
    on a null element."""
    src = JS.read_text()
    assert "setlistBulkToggle" not in src
    assert "setlistBulkBody" not in src


def test_paste_box_is_visible_without_a_toggle():
    """It's the only control now; hiding it behind show/hide was a step to
    reach the only thing on offer."""
    src = JS.read_text()
    assert 'id="setlistBulkText"' in src
    assert "display:none" not in src[src.index("setlistBulkText") - 600:src.index("setlistBulkText")]


def test_button_label_and_heading():
    src = JS.read_text()
    assert "Add Songs" in src
    assert "Parse & Add" not in src
    assert "Bulk paste" not in src


def test_bulk_endpoint_is_still_what_it_posts_to():
    src = JS.read_text()
    assert "/setlist/bulk" in src


def test_backend_keeps_both_endpoints():
    """The UI narrowed; the API did not."""
    src = (ROOT / "backend" / "routes" / "setlist.py").read_text()
    assert '@router.post("/api/artists/{artist_id}/setlist")' in src
    assert '@router.post("/api/artists/{artist_id}/setlist/bulk")' in src
