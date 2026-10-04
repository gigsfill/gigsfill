"""No modal closes on a backdrop click.

Reported 2026-10-08: a venue opened the Venue Gig Details modal, clicked
outside it by mistake, and the modal vanished. Across the app this pattern
was everywhere — 28 sites in 22 files — and in a modal holding typed input or
something the user was mid-way through reading, one stray click threw it away
with no warning and no undo.

The listeners are kept rather than deleted: a backdrop click must still be
swallowed, or it falls through to whatever sits underneath, which is how one
misclick ended up dismissing two things.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JS = sorted((ROOT / "app" / "static" / "js").glob("*.js"))


def test_no_backdrop_click_closes_a_modal():
    offenders = []
    pat = re.compile(r"\w+\.target\s*===\s*(?:overlay|modal|this)\s*\)\s*(?!\s*\w+\.stopPropagation)")
    for p in JS:
        for m in pat.finditer(p.read_text()):
            line = p.read_text()[:m.start()].count("\n") + 1
            offenders.append(f"{p.name}:{line}")
    assert not offenders, offenders


def test_the_backdrop_click_is_still_swallowed():
    """Deleting the listener instead would let the click reach whatever is
    underneath the overlay."""
    n = sum(p.read_text().count("stopPropagation") for p in JS)
    assert n >= 20, f"only {n} guards found"


def test_every_modal_still_has_a_way_out():
    """Removing backdrop dismiss from a modal with no close control would
    trap the user in it."""
    trapped = []
    for p in JS:
        s = p.read_text()
        if "target === overlay" not in s and "target === modal" not in s:
            continue
        has_close = re.search(r"aria-label=.Close|&times;|✕|×|Cancel|closeBtn|[Cc]lose\(", s)
        has_esc = "Escape" in s
        if not (has_close or has_esc):
            trapped.append(p.name)
    assert not trapped, trapped


def test_the_details_modal_unbinds_its_escape_handler():
    """It was removed only inside the Escape branch, so closing by any other
    route left the listener attached — one more per open, each holding a
    closure over a modal that no longer exists."""
    s = (ROOT / "app" / "static" / "js" / "venue-gig-details-modal.js").read_text()
    fn = s[s.index("function _closeModal"):][:500]
    assert "removeEventListener('keydown'" in fn


def test_changed_scripts_carry_a_cache_buster():
    """nginx serves /app/ with max-age=86400, so an edited file referenced
    without ?v= is invisible to every returning browser for a day — the fix
    would simply not arrive."""
    for html in (ROOT / "app").glob("*.html"):
        s = html.read_text()
        for m in re.finditer(r'/app/static/js/([\w.\-]+\.js)(\?v=\d+)?"', s):
            if m.group(1) in ("security.js", "event-delegate.js"):
                continue  # never modified here
            assert m.group(2) or m.group(1) not in _CHANGED, \
                f"{html.name}: {m.group(1)} has no cache-buster"


_CHANGED = {
    "activity-center.js", "admin-directory.js", "admin-init.js",
    "artist-booked-gigs-modal.js", "artist-profile-init.js", "artist.edit.js",
    "contact-modal.js", "demo-request-modal.js", "gf-modals.js", "messages.js",
    "modals.js", "modals.preview.js", "my-artists.js", "my-venues-redesign.js",
    "onboarding-checklist.js", "public-gigs-list-modal.js", "user-affiliate.js",
    "venue-gig-details-modal.js", "venue-profile-init.js",
    "venue-stripe-payment.js", "venue.create-gigs.js", "venue.edit.js",
}
