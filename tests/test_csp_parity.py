"""The nginx CSP and the app CSP must not drift.

nginx serves /app/ straight off disk, so FastAPI's CSP middleware never runs
for a static page. Every profile, the media lightbox and the homepage are
governed by the `set $CSP` line in the vhost alone.

They drifted for months: the Instagram / TikTok / Vimeo / Facebook hosts added
to the app in Jul 2026 were never copied across, so those embeds were blocked
in production the whole time — while passing every local test, because tests
hit :8001 directly and that bypasses nginx entirely.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VHOST = Path("/etc/nginx/sites-enabled/gigsfill")


def _app_hosts():
    src = (ROOT / "backend" / "main.py").read_text()
    blk = src[src.index('csp = "; ".join(['):]
    blk = blk[:blk.index("])")]
    code = "\n".join(l for l in blk.splitlines() if not l.strip().startswith("#"))
    return set(re.findall(r"https://[^\s\"]+", code))


def test_every_host_the_app_allows_is_allowed_by_nginx():
    """A host in main.py but not the vhost is allowed nowhere that matters:
    the pages doing the embedding are all static."""
    if not VHOST.exists():
        return                      # not the droplet
    try:
        vhost = VHOST.read_text()
    except PermissionError:
        return
    nginx_hosts = set(re.findall(r"https://[^\s;\"]+", vhost[vhost.index("set $CSP"):]))
    missing = sorted(_app_hosts() - nginx_hosts)
    assert not missing, f"nginx CSP is missing: {missing}"


def test_the_app_csp_still_lists_the_social_embed_hosts():
    """Guards the other direction — dropping them from main.py would make the
    parity test above pass by deleting the requirement."""
    hosts = _app_hosts()
    for h in ("https://www.instagram.com", "https://www.tiktok.com",
              "https://player.vimeo.com", "https://www.facebook.com"):
        assert h in hosts, h


def test_the_vhost_says_why_it_has_to_be_kept_in_step():
    """Whoever edits main.py next needs to find out that a second copy
    exists, and the only place they will look is the vhost."""
    if not VHOST.exists():
        return
    try:
        vhost = VHOST.read_text()
    except PermissionError:
        return
    head = vhost[max(0, vhost.index("set $CSP") - 900):vhost.index("set $CSP")]
    assert "backend/main.py" in head
