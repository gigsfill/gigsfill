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
    assert f"www.youtube.com/embed/{VIDEO_ID}" in html


def test_embed_host_is_one_the_nginx_csp_allows():
    """The policy that governs this page is the `set $CSP` line in the nginx
    vhost, NOT the one in backend/main.py: nginx serves /app/ straight off
    disk (`alias /opt/gigsfill/app/`), so FastAPI's middleware never runs
    for a static page.

    That is how youtube-nocookie.com shipped blank — it is a distinct origin
    from youtube.com, absent from the nginx policy, and every local test hit
    :8001 directly, which bypasses nginx. Pinning the host here keeps the
    page on an origin that policy actually permits.
    """
    html = HOME.read_text()
    assert "youtube-nocookie.com/embed" not in html
    assert "https://www.youtube.com/embed/" in html

    vhost = Path("/etc/nginx/sites-enabled/gigsfill")
    if not vhost.exists():        # not the droplet; the assertion above stands
        return
    try:
        policy = vhost.read_text()
    except PermissionError:
        return
    frame = policy[policy.index("frame-src"):]
    frame = frame[:frame.index(";")]
    assert "https://www.youtube.com" in frame, "nginx CSP does not allow the embed host"


def test_play_control_is_replaced_not_left_behind():
    """A screen reader should not still offer a play button for a video
    that is already playing."""
    html = HOME.read_text()
    fn = html[html.index("function playTrailer()"):]
    fn = fn[:fn.index("\n  }") + 4]
    assert "replaceWith" in fn
    assert "createElement('div')" in fn, "button semantics kept after play"


def test_heading_matches_the_other_panel_headings():
    """The gradient treatment is copied verbatim from the "Search for live
    music" panel rather than re-typed, so the two cannot drift apart."""
    html = HOME.read_text()
    ref = re.search(r'<h3 style="([^"]*)">Search for live music in your town\.</h3>', html)
    mine = re.search(r'<h3 style="([^"]*)">GigsFill - Summary of Features</h3>', html)
    assert ref and mine, "heading markup changed shape"
    assert mine.group(1) == ref.group(1), "trailer heading has drifted from the reference"
