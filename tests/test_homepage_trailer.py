"""The homepage product trailer.

Embedded as a facade rather than a live <iframe>. A YouTube embed pulls
~1.5MB of script and sets tracking cookies on every page load, and
index.html is also the login screen — returning users would pay that on
every visit for a video they have already watched. The poster is one image;
YouTube loads only when someone presses play.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOME = ROOT / "app" / "index.html"
MAIN = ROOT / "backend" / "main.py"
VIDEO_ID = "PqGZRwmbcZU"


def test_no_iframe_until_someone_asks_for_one():
    """The whole point of the facade. A static <iframe> here would undo it."""
    html = HOME.read_text()
    body = html[html.index("<body"):]
    static_markup = body[:body.index("<script")]
    assert "<iframe" not in static_markup.lower()
    assert "youtube" not in static_markup.lower() or "i.ytimg.com" in static_markup


def test_trailer_sits_above_the_demo_request():
    """Order reads: sign in (returning users) -> see what this is (new
    visitors) -> ask for a demo. Someone who has just watched it is the
    person most likely to press the demo button."""
    html = HOME.read_text()
    assert html.index('id="gfTrailer"') < html.index('id="requestDemoBtn"')


def test_poster_falls_back_without_looping():
    """maxresdefault is not generated for every upload; hqdefault always
    is. A fallback that re-fires on its own failure spins forever."""
    html = HOME.read_text()
    img = re.search(r'<img src="https://i\.ytimg\.com[^>]*>', html).group(0)
    assert "maxresdefault" in img and "hqdefault" in img
    assert "this.onerror=null" in img, "fallback can loop"


def test_player_and_poster_point_at_the_same_video():
    html = HOME.read_text()
    assert html.count(VIDEO_ID) >= 2
    assert f"i.ytimg.com/vi/{VIDEO_ID}/" in html
    assert f"youtube-nocookie.com/embed/{VIDEO_ID}" in html


def test_nocookie_host_is_allowed_by_the_csp():
    """youtube-nocookie.com is a distinct origin from youtube.com — without
    it in frame-src the iframe silently stays blank."""
    csp = MAIN.read_text()
    assert "https://www.youtube-nocookie.com" in csp
    frame = csp[csp.index('"frame-src'):]
    frame = frame[:frame.index("media-src")]
    assert "youtube-nocookie" in frame, "allowed somewhere, but not in frame-src"


def test_play_control_is_replaced_not_left_behind():
    """A screen reader should not still offer a play button for a video
    that is already playing."""
    html = HOME.read_text()
    fn = html[html.index("function playTrailer()"):]
    fn = fn[:fn.index("\n  }") + 4]
    assert "replaceWith" in fn
    assert "createElement('div')" in fn, "button semantics kept after play"
