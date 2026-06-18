"""
Contract Routes
Handles venue contract templates and per-gig contract instances.

Three contract types:
  - pdf_upload: Venue uploads a PDF, artist downloads/signs/re-uploads
  - custom_builder: Venue builds a digital contract with fillable fields
  - auto_generated: System generates contract from venue/gig data + boilerplate
"""
from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File
import logging
from sqlalchemy import text
from datetime import datetime
from backend.utils import utcnow_naive, to_admin_bool
import json, os, re, shutil, tempfile, uuid
import html as _html


def _ce(s) -> str:
    """Contract-HTML escape — used everywhere user-controlled values land
    in the generated PERFORMANCE AGREEMENT or custom-template body. Without
    this, an artist or venue with HTML in their display name (e.g.
    "<img src=x onerror=...>") would XSS the contract viewer/signer.
    """
    if s is None:
        return ""
    return _html.escape(str(s), quote=True)

logger = logging.getLogger("gigsfill.contracts")


async def _read_and_validate_pdf(file: "UploadFile", max_mb: int = 20) -> bytes:
    """Read an UploadFile fully, verify size + filename + magic bytes.

    FIX (May 2026): Added magic-byte validation. Previously contracts.py only
    checked the filename suffix and the size, but a user could upload any file
    (e.g. an executable, or HTML) renamed to .pdf and we'd happily save it.
    Now we also confirm the file actually starts with the PDF magic bytes
    (`%PDF-`).

    Raises HTTPException on any failure.
    Returns the validated bytes ready to write to disk.
    """
    if not file.filename or not file.filename.lower().endswith('.pdf'):
        raise HTTPException(400, "Only PDF files are accepted")

    # Read full content. FastAPI buffers to disk when over 1MB so this is fine.
    content = await file.read()

    # Size check (after read so we know the actual size)
    size_limit = max_mb * 1024 * 1024
    if len(content) > size_limit:
        raise HTTPException(400, f"File too large. Maximum size: {max_mb} MB")
    if len(content) < 5:
        raise HTTPException(400, "File appears empty or corrupted")

    # Magic bytes: PDFs start with %PDF- (per ISO 32000-1)
    if not content.startswith(b'%PDF-'):
        raise HTTPException(400, "File is not a valid PDF (content does not match PDF format)")

    return content


def _apply_slot_pay_override(db, slot_id: int, venue_id: int, artist_id: int):
    """After booking a slot, update its pay to max(current_pay, artist_override) if override exists.

    Override only applies when the relationship is 'approved' — pending/denied/revoked
    rows still carry the old override columns, but the per-artist negotiated rate no
    longer applies. Missed in the part 10m pay-override audit; fixed here.

    DOOR-DEAL SAFETY (Jun 2026): when the slot has deal_type='door', the
    pay column carries the guarantee floor that the venue explicitly set
    for THIS slot. Letting the venue's standing per-artist override raise
    that floor here would (a) silently desync the booking charge from the
    "guarantee + X% of door" terms shown in the email + contract, and (b)
    take away the venue's ability to use door split as a per-gig opt-out
    from the standing override (which is the design the user asked for —
    pick door split for the gig where you don't want to pay the full
    override fee). Door slots skip the override; the guarantee is final.
    """
    try:
        slot = db.execute(
            text("SELECT pay, deal_type FROM gig_slots WHERE id = :sid"),
            {"sid": slot_id}
        ).mappings().first()
        if not slot:
            return
        # Door slots use guarantee terms set per-gig — standing override
        # does not apply.
        if (slot.get("deal_type") or "").lower() == "door":
            return
        base_pay = float(slot["pay"] or 0)
        override = db.execute(
            text("SELECT pay_dollars_override, pay_cents_override FROM preferred_artists WHERE venue_id=:vid AND artist_id=:aid AND status='approved'"),
            {"vid": venue_id, "aid": artist_id}
        ).mappings().first()
        if override and override["pay_dollars_override"] is not None:
            override_pay = float(override["pay_dollars_override"]) + float(override["pay_cents_override"] or 0) / 100
            if override_pay > base_pay:
                db.execute(
                    text("UPDATE gig_slots SET pay = :pay WHERE id = :sid"),
                    {"pay": override_pay, "sid": slot_id}
                )
    except Exception as _e:
        logger.warning(f"_apply_slot_pay_override failed: {_e}")



from backend.db import get_db
from backend.routes.auth import get_current_user
from backend.routes.gigs import _create_booking_transaction, _ensure_approval_columns, _is_same_day_booking
from backend.services.email_dispatch import format_email_date, send_approval_request_emails

router = APIRouter()

UPLOAD_DIR = "app/static/uploads/contracts"


def _contract_display_filename(venue_name: str, artist_name: str, gig_date, suffix: str = "") -> str:
    """Build clean filename: YYYY_MM_DD_VenueName_ArtistName.pdf (no spaces). suffix is _02, _03 for duplicates."""
    if hasattr(gig_date, "strftime"):
        date_str = gig_date.strftime("%Y_%m_%d")
    else:
        date_str = (str(gig_date or "")[:10].replace("-", "_") or "date")
    venue_safe = re.sub(r"[^a-zA-Z0-9]", "", (venue_name or "Venue")[:60])
    artist_safe = re.sub(r"[^a-zA-Z0-9]", "", (artist_name or "Artist")[:60])
    return f"{date_str}_{venue_safe}_{artist_safe}{suffix}.pdf"

# ============================================
# HELPERS
# ============================================

def check_venue_access(db, venue_id: int, user_id: int):
    """Verify user has access to this venue (owner or entity_user)"""
    row = db.execute(
        text("""
            SELECT 1 FROM venues v
            WHERE v.id = :vid AND (
                v.user_id = :uid
                OR EXISTS (
                    SELECT 1 FROM entity_users eu
                    WHERE eu.entity_type = 'venue' AND eu.entity_id = v.id AND eu.user_id = :uid
                )
            )
        """),
        {"vid": venue_id, "uid": user_id}
    ).first()
    if not row:
        raise HTTPException(403, "You don't have access to this venue")


def check_artist_access(db, artist_id: int, user_id: int):
    """Verify user has access to this artist (owner or entity_user)"""
    row = db.execute(
        text("""
            SELECT 1 FROM artists a
            WHERE a.id = :aid AND (
                a.user_id = :uid
                OR EXISTS (
                    SELECT 1 FROM entity_users eu
                    WHERE eu.entity_type = 'artist' AND eu.entity_id = a.id AND eu.user_id = :uid
                )
            )
        """),
        {"aid": artist_id, "uid": user_id}
    ).first()
    if not row:
        raise HTTPException(403, "You don't have access to this artist")


def format_pay(pay_cents):
    """Format cents to dollar string"""
    if not pay_cents:
        return "$0"
    dollars = pay_cents // 100 if pay_cents > 99 else pay_cents
    return f"${dollars:,}"


def format_time_12hr(time_str):
    """Convert 24hr time to 12hr format"""
    if not time_str:
        return ''
    try:
        parts = str(time_str).split(':')
        hours = int(parts[0])
        minutes = parts[1]
        ampm = 'PM' if hours >= 12 else 'AM'
        hours = hours % 12 or 12
        return f"{hours}:{minutes} {ampm}"
    except:
        return str(time_str)


AUTO_CONTRACT_DISCLAIMER = """DISCLAIMER: This contract template is provided by GigsFill for convenience only and does not constitute legal advice. GigsFill is not a law firm, is not a party to this agreement, makes no representations or warranties regarding the enforceability, completeness, or legal sufficiency of this document, and assumes no liability whatsoever for its use. Both parties are solely responsible for understanding and agreeing to the terms herein. Both parties are strongly encouraged to consult with a licensed attorney before executing any contract. By signing this document, both parties acknowledge they have read and understood this disclaimer and agree to use this template at their own risk."""


def _get_effective_pay_str(db, gig, venue_id, artist_id, slot_id=None):
    """Get the effective pay for a gig considering pay overrides from preferred_artists.

    Door-split aware (Jun 2026): when the gig (or the booked slot) has
    deal_type='door', the contract pay line shows
    "$<guarantee> guarantee + <pct>% of door receipts" instead of the
    flat pay amount, so the artist sees the same terms in the contract
    as they did when booking.
    """
    # Door-split path — check the slot table first (multi-slot gigs)
    # then fall back to the gig itself (single-slot legacy). When
    # slot_id is provided (preview path before booking), look it up
    # directly — the by-artist filter would miss because slot.artist_id
    # isn't set until _apply_slot_booking runs.
    deal_type = None
    door_pct = 0
    guarantee_cents = 0
    try:
        if slot_id:
            slot_row = db.execute(
                text("SELECT deal_type, door_pct, guarantee_cents FROM gig_slots WHERE id = :sid"),
                {"sid": slot_id}
            ).mappings().first()
        else:
            slot_row = db.execute(
                text("""SELECT deal_type, door_pct, guarantee_cents
                        FROM gig_slots
                        WHERE gig_id = :gid AND artist_id = :aid
                          AND status IN ('booked','pending_contract','awaiting_venue_contract')
                        ORDER BY slot_number ASC LIMIT 1"""),
                {"gid": gig.get("id"), "aid": artist_id}
            ).mappings().first()
        if slot_row:
            deal_type = (slot_row.get("deal_type") or "").lower() or None
            door_pct = int(slot_row.get("door_pct") or 0)
            guarantee_cents = int(slot_row.get("guarantee_cents") or 0)
    except Exception:
        pass
    if deal_type == "door" and (guarantee_cents > 0 or door_pct > 0):
        gua_dollars = guarantee_cents / 100.0
        return f"${gua_dollars:,.2f} guarantee + {door_pct}% of door receipts"

    pay = float(gig.get("pay", 0) or 0)
    pay_override = db.execute(
        text("SELECT pay_dollars_override, pay_cents_override FROM preferred_artists WHERE venue_id = :vid AND artist_id = :aid AND status = 'approved'"),
        {"vid": venue_id, "aid": artist_id}
    ).mappings().first()
    if pay_override and pay_override["pay_dollars_override"] is not None:
        override_pay = float(pay_override["pay_dollars_override"]) + float(pay_override["pay_cents_override"] or 0) / 100
        if override_pay > pay:
            pay = override_pay
    return f"${pay:,.2f}" if pay else "$0"


def generate_auto_contract(db, gig_id: int, venue_id: int, artist_id: int, slot_id: int = None) -> str:
    """Generate a contract from venue profile data, gig details, and boilerplate.

    slot_id: pass when known so multi-slot Performance Time renders the
    SPECIFIC slot's times (7-9 PM), not the gig umbrella (7-11 PM).
    Required at PREVIEW time (before booking) — the slot row doesn't
    yet have artist_id set, so the by-artist lookup falls back to the
    umbrella. At save time (post-_apply_slot_booking) the by-artist
    lookup also works, but passing slot_id is cleaner.
    """
    
    # Get venue data
    venue = db.execute(
        text("""
            SELECT v.*, u.first_name || ' ' || u.last_name as owner_name, u.email as owner_email, u.phone as owner_phone
            FROM venues v JOIN users u ON v.user_id = u.id
            WHERE v.id = :vid
        """),
        {"vid": venue_id}
    ).mappings().first()
    
    # Get artist data
    artist = db.execute(
        text("""
            SELECT a.*, u.first_name || ' ' || u.last_name as owner_name, u.email as owner_email, u.phone as owner_phone
            FROM artists a JOIN users u ON a.user_id = u.id
            WHERE a.id = :aid
        """),
        {"aid": artist_id}
    ).mappings().first()
    
    # Get gig data
    gig = db.execute(
        text("SELECT * FROM gigs WHERE id = :gid"),
        {"gid": gig_id}
    ).mappings().first()
    
    if not venue or not artist or not gig:
        raise HTTPException(404, "Venue, artist, or gig not found")
    
    # Format values — every user-controlled string gets _ce()'d before
    # landing in HTML. Without this, an artist or venue display name like
    # `<img src=x onerror=alert(1)>` becomes a stored XSS that fires on
    # every viewer of the contract PDF / signed HTML.
    venue_name = _ce(venue["venue_name"] or "Venue")
    venue_address = _ce(", ".join(filter(None, [
        venue.get("address_line_1", ""),
        venue.get("address_line_2", ""),
        venue.get("city", ""),
        venue.get("state", ""),
        venue.get("postal_code", "")
    ])))
    artist_name = _ce(artist["name"] or "Artist")
    artist_location = _ce(", ".join(filter(None, [artist.get("city", ""), artist.get("state", "")])))
    # Pre-escaped views of owner contact + per-section fields so the f-strings
    # below don't need _ce() at every interpolation site.
    venue_owner_name  = _ce(venue.get("owner_name")  or "")
    venue_owner_email = _ce(venue.get("owner_email") or "")
    venue_owner_phone = _ce(venue.get("owner_phone") or "")
    artist_owner_name  = _ce(artist.get("owner_name")  or "")
    artist_owner_email = _ce(artist.get("owner_email") or "")
    artist_owner_phone = _ce(artist.get("owner_phone") or "")
    artist_type_esc     = _ce(artist.get("artist_type") or "N/A")
    artist_band_fmts    = _ce(artist.get("band_formats") or "")
    artist_styles       = _ce(artist.get("styles") or "")

    gig_date = _ce(gig["date"] or "")
    # Performance Time must reflect THIS artist's specific slot, not the
    # umbrella gig start/end (multi-slot: 7-9 + 9-11 would otherwise read
    # as "7-11" on the contract — wrong for the 7-9 booker). For single-
    # slot gigs the slot times equal the gig times so the lookup is a
    # harmless echo. Same pattern as the door-deal lookup in
    # _get_effective_pay_str above.
    # When slot_id is explicit, look it up directly — works at preview
    # time when the slot isn't booked yet. Falls back to the by-artist
    # lookup for backward compat (some legacy call sites don't have a
    # slot_id handy, and post-booking the slot DOES have artist_id).
    if slot_id:
        _slot_times = db.execute(
            text("SELECT start_time, end_time FROM gig_slots WHERE id = :sid"),
            {"sid": slot_id}
        ).mappings().first()
    else:
        _slot_times = db.execute(
            text("""SELECT start_time, end_time
                    FROM gig_slots
                    WHERE gig_id = :gid AND artist_id = :aid
                      AND status IN ('booked','pending_contract','awaiting_venue_contract')
                    ORDER BY slot_number ASC LIMIT 1"""),
            {"gid": gig.get("id"), "aid": artist_id}
        ).mappings().first()
    if _slot_times and _slot_times.get("start_time"):
        eff_start = _slot_times.get("start_time")
        eff_end   = _slot_times.get("end_time") or gig.get("end_time", "")
    else:
        eff_start = gig.get("start_time", "")
        eff_end   = gig.get("end_time", "")
    start_time = _ce(format_time_12hr(eff_start))
    end_time   = _ce(format_time_12hr(eff_end))
    # Door-aware pay line. `_get_effective_pay_str` returns content built
    # from numeric fields only — safe to interpolate without escaping.
    pay_str = _get_effective_pay_str(db, gig, venue_id, artist_id, slot_id=slot_id)
    gig_title = _ce(gig.get("title", "Live Performance"))
    
    # Build performance time string
    time_str = ""
    if start_time and end_time:
        time_str = f"{start_time} to {end_time}"
    elif start_time:
        time_str = f"Starting at {start_time}"
    
    # Build sections
    sections = []
    
    # Header
    sections.append(f"""<h2 style="text-align:center; margin-bottom:4px;">PERFORMANCE AGREEMENT</h2>
<p style="text-align:center; color:#6b7280; margin-top:0;">Between {venue_name} and {artist_name}</p>
<hr style="border:none; border-top:1px solid #333; margin:20px 0;">""")
    
    # Section 1: Parties
    sections.append(f"""<h3>1. PARTIES</h3>
<p>This Performance Agreement ("Agreement") is entered into as of the date of last signature below, by and between:</p>
<p><strong>Venue:</strong> {venue_name}<br>
Address: {venue_address or '{{venue_address}}'}<br>
Contact: {venue_owner_name or '{{venue_contact_name}}'}<br>
Email: {venue_owner_email or '{{venue_email}}'}<br>
Phone: {venue_owner_phone or '{{venue_phone}}'}</p>
<p><strong>Performer:</strong> {artist_name}<br>
Location: {artist_location or '{{artist_location}}'}<br>
Artist Type: {artist_type_esc}{(' — Lineup: ' + artist_band_fmts) if artist_band_fmts else ''}{(' — Styles: ' + artist_styles) if artist_styles else ''}<br>
Contact: {artist_owner_name or '{{artist_contact_name}}'}<br>
Email: {artist_owner_email or '{{artist_email}}'}<br>
Phone: {artist_owner_phone or '{{artist_phone}}'}</p>""")
    
    # Section 2: Performance Details
    sections.append(f"""<h3>2. PERFORMANCE DETAILS</h3>
<p><strong>Event:</strong> {gig_title}<br>
<strong>Date:</strong> {gig_date}<br>
<strong>Performance Time:</strong> {time_str or '{{performance_time}}'}<br>
<strong>Compensation:</strong> {pay_str}</p>""")
    
    # Section 3: Venue Amenities & Setup
    amenities_parts = []
    
    if venue.get("has_stage"):
        stage_info = "Stage provided"
        dims = []
        if venue.get("stage_width_ft"):
            dims.append(f"{venue['stage_width_ft']}ft wide")
        if venue.get("stage_depth_ft"):
            dims.append(f"{venue['stage_depth_ft']}ft deep")
        if dims:
            stage_info += f" ({', '.join(dims)})"
        amenities_parts.append(stage_info)
    else:
        if venue.get("setup_location_description"):
            amenities_parts.append(f"Setup Location: {venue['setup_location_description']}")
    
    if venue.get("has_sound_equipment"):
        sound_info = "Sound equipment provided"
        if venue.get("sound_equipment_description"):
            sound_info += f" — {venue['sound_equipment_description']}"
        amenities_parts.append(sound_info)
    else:
        amenities_parts.append("Performer shall provide their own sound equipment")
    
    if venue.get("has_sound_engineer"):
        eng_info = "Sound engineer provided"
        if venue.get("sound_engineer_details"):
            eng_info += f" — {venue['sound_engineer_details']}"
        amenities_parts.append(eng_info)
    
    if venue.get("has_lighting"):
        light_info = "Lighting provided"
        if venue.get("lighting_description"):
            light_info += f" — {venue['lighting_description']}"
        amenities_parts.append(light_info)
    
    amenities_html = "".join(f"<li>{a}</li>" for a in amenities_parts) if amenities_parts else "<li>Contact venue for details</li>"
    sections.append(f"""<h3>3. VENUE AMENITIES &amp; SETUP</h3>
<ul>{amenities_html}</ul>""")
    
    # Section 4: Load In/Out & Arrival
    arrival_parts = []
    if venue.get("arrival_time_type") == "no_earlier_than" and venue.get("arrival_no_earlier_than_hour"):
        period = venue.get("arrival_no_earlier_than_period", "PM")
        arrival_parts.append(f"Performer shall arrive no earlier than {venue['arrival_no_earlier_than_hour']}:00 {period}")
    elif venue.get("arrival_time_type") == "flexible":
        arrival_parts.append("Arrival time is flexible — coordinate with venue in advance")
    
    if venue.get("load_in_out_details"):
        arrival_parts.append(f"Load In/Out: {venue['load_in_out_details']}")
    
    if arrival_parts:
        arrival_html = "".join(f"<p>{a}</p>" for a in arrival_parts)
        sections.append(f"""<h3>4. ARRIVAL &amp; LOAD IN/OUT</h3>
{arrival_html}""")
    else:
        sections.append("""<h3>4. ARRIVAL &amp; LOAD IN/OUT</h3>
<p>Performer shall coordinate arrival and load-in/out times with the Venue in advance of the performance date.</p>""")
    
    # Section 5: Compensation & Payment
    payment_section = f"""<h3>5. COMPENSATION &amp; PAYMENT</h3>
<p>Venue agrees to pay Performer <strong>{pay_str}</strong> for the performance described in Section 2.</p>
<p>Payment shall be made on the date of performance unless otherwise agreed upon in writing by both parties.</p>"""
    
    if venue.get("bar_tab_details"):
        payment_section += f"\n<p><strong>Bar Tab:</strong> {venue['bar_tab_details']}</p>"
    if venue.get("food_tab_details"):
        payment_section += f"\n<p><strong>Food Tab:</strong> {venue['food_tab_details']}</p>"
    
    sections.append(payment_section)
    
    # Section 6: Performance Obligations
    sections.append("""<h3>6. PERFORMANCE OBLIGATIONS</h3>
<p>Performer agrees to:</p>
<ul>
<li>Arrive at the agreed-upon time and be ready to perform at the scheduled start time</li>
<li>Perform for the full duration specified in Section 2</li>
<li>Conduct themselves in a professional manner throughout the engagement</li>
<li>Comply with all venue rules, noise ordinances, and applicable laws</li>
</ul>
<p>Venue agrees to:</p>
<ul>
<li>Provide the amenities and setup described in Section 3</li>
<li>Ensure a safe performance environment</li>
<li>Make payment as described in Section 5</li>
<li>Provide reasonable access for load-in and load-out</li>
</ul>""")
    
    # Section 7: Cancellation
    sections.append("""<h3>7. CANCELLATION</h3>
<p>Either party may cancel this Agreement by providing written notice (including email or message through the GigsFill platform) to the other party.</p>
<p>If the Venue cancels with less than 48 hours notice, Venue shall pay Performer 50% of the agreed compensation unless otherwise negotiated.</p>
<p>If the Performer cancels with less than 48 hours notice, Performer may be subject to removal from the Venue's preferred artist list.</p>""")
    
    # Section 8: Force Majeure
    sections.append("""<h3>8. FORCE MAJEURE</h3>
<p>Neither party shall be liable for failure to perform due to circumstances beyond their reasonable control, including but not limited to: acts of God, severe weather, natural disasters, government orders, pandemics, power outages, or other events that make the performance impossible or impractical. In such cases, both parties shall make reasonable efforts to reschedule.</p>""")
    
    # Section 9: Liability & Indemnification
    sections.append("""<h3>9. LIABILITY &amp; INDEMNIFICATION</h3>
<p>Each party shall be responsible for their own negligent acts or omissions. Performer shall be responsible for any damage to Venue property caused by Performer or Performer's crew. Venue shall maintain adequate liability insurance for events held at its premises. Neither party shall be liable for indirect, incidental, or consequential damages.</p>""")
    
    # Section 10: Independent Contractor
    sections.append("""<h3>10. INDEPENDENT CONTRACTOR</h3>
<p>Performer is an independent contractor and not an employee, agent, or partner of the Venue. Performer is solely responsible for all applicable taxes, including self-employment taxes, arising from compensation received under this Agreement.</p>""")
    
    # Section 11: Entire Agreement
    sections.append("""<h3>11. ENTIRE AGREEMENT</h3>
<p>This Agreement constitutes the entire understanding between the parties and supersedes all prior negotiations, representations, or agreements relating to this subject matter. This Agreement may only be modified by written consent of both parties.</p>""")
    
    # Section 12: Signatures
    sections.append(f"""<h3>12. SIGNATURES</h3>
<p>By signing below, both parties acknowledge they have read, understood, and agree to the terms of this Agreement.</p>
<div style="display:grid; grid-template-columns:1fr 1fr; gap:32px; margin-top:24px;">
<div>
<p><strong>Venue Representative:</strong></p>
<p>Name: ____________________________</p>
<p>Title: ____________________________</p>
<p>Date: ____________________________</p>
</div>
<div>
<p><strong>Performer:</strong></p>
<p>Name: ____________________________</p>
<p>Title: ____________________________</p>
<p>Date: ____________________________</p>
</div>
</div>""")
    
    # Disclaimer
    sections.append(f"""<hr style="border:none; border-top:1px solid #333; margin:30px 0 20px 0;">
<p style="font-size:0.75rem; color:#6b7280; line-height:1.6; font-style:italic;">{AUTO_CONTRACT_DISCLAIMER}</p>""")
    
    return "\n\n".join(sections)


# ============================================
# VENUE CONTRACT TEMPLATES — CRUD
# ============================================

@router.get("/api/venues/{venue_id}/contracts")
def list_venue_contracts(venue_id: int, user=Depends(get_current_user), db=Depends(get_db)):
    """Get all contract templates for a venue"""
    check_venue_access(db, venue_id, user.id)
    
    rows = db.execute(
        text("""
            SELECT id, venue_id, contract_type, name, is_active, require_for_booking,
                   pdf_file_path, contract_body, custom_fields, created_at, updated_at
            FROM venue_contracts
            WHERE venue_id = :vid
            ORDER BY is_active DESC, updated_at DESC
        """),
        {"vid": venue_id}
    ).mappings().all()
    
    return [dict(r) for r in rows]


@router.get("/api/venues/{venue_id}/contracts/active")
def get_active_contract(venue_id: int, db=Depends(get_db)):
    """Get the active contract template for a venue (public — used by artist booking flow)"""
    row = db.execute(
        text("""
            SELECT id, venue_id, contract_type, name, is_active, require_for_booking,
                   pdf_file_path, contract_body, custom_fields, COALESCE(per_gig_pdf, 0) as per_gig_pdf
            FROM venue_contracts
            WHERE venue_id = :vid AND is_active = 1
            LIMIT 1
        """),
        {"vid": venue_id}
    ).mappings().first()
    
    if not row:
        return {"has_contract": False}
    
    result = dict(row)
    result["has_contract"] = True
    return result


@router.post("/api/venues/{venue_id}/contracts")
def create_venue_contract(venue_id: int, data: dict, user=Depends(get_current_user), db=Depends(get_db)):
    """Create a new contract template"""
    check_venue_access(db, venue_id, user.id)
    
    contract_type = data.get("contract_type", "").strip()
    if contract_type not in ("pdf_upload", "custom_builder", "auto_generated"):
        raise HTTPException(400, "Invalid contract type")
    
    name = data.get("name", "Standard Contract").strip() or "Standard Contract"
    require_for_booking = 1 if data.get("require_for_booking") else 0
    per_gig_pdf = 1 if data.get("per_gig_pdf") else 0
    contract_body = data.get("contract_body", "")
    custom_fields = json.dumps(data.get("custom_fields", [])) if data.get("custom_fields") else "[]"
    
    # Deactivate any existing active contract for this venue
    db.execute(
        text("UPDATE venue_contracts SET is_active = 0 WHERE venue_id = :vid"),
        {"vid": venue_id}
    )

    db.execute(
        text("""
            INSERT INTO venue_contracts
                (venue_id, contract_type, name, is_active, require_for_booking, per_gig_pdf, contract_body, custom_fields, created_at, updated_at)
            VALUES (:vid, :type, :name, 1, :req, :pgp, :body, :fields, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """),
        {
            "vid": venue_id,
            "type": contract_type,
            "name": name,
            "req": require_for_booking,
            "pgp": per_gig_pdf,
            "body": contract_body,
            "fields": custom_fields,
        }
    )

    # Get the new ID
    new_id = db.execute(text("SELECT last_insert_rowid()")).scalar()

    # Audit fix (May 2026 part 8): post-insert race-loser reconciliation.
    # Two concurrent POSTs both ran the DEACTIVATE-then-INSERT pattern above
    # and both ended up with `is_active=1`. Force the winner (lowest new id)
    # to be the only active row by deactivating everything else after.
    db.execute(
        text("UPDATE venue_contracts SET is_active = 0 WHERE venue_id = :vid AND id != :keep"),
        {"vid": venue_id, "keep": new_id}
    )
    db.commit()

    return {"ok": True, "contract_id": new_id}


@router.put("/api/venues/{venue_id}/contracts/{contract_id}")
def update_venue_contract(venue_id: int, contract_id: int, data: dict, user=Depends(get_current_user), db=Depends(get_db)):
    """Update an existing contract template"""
    check_venue_access(db, venue_id, user.id)
    
    # Verify contract belongs to venue
    existing = db.execute(
        text("SELECT id FROM venue_contracts WHERE id = :cid AND venue_id = :vid"),
        {"cid": contract_id, "vid": venue_id}
    ).first()
    if not existing:
        raise HTTPException(404, "Contract not found")
    
    updates = []
    params = {"cid": contract_id, "vid": venue_id}
    
    if "name" in data:
        updates.append("name = :name")
        params["name"] = data["name"].strip() or "Standard Contract"
    if "contract_type" in data:
        if data["contract_type"] not in ("pdf_upload", "custom_builder", "auto_generated"):
            raise HTTPException(400, "Invalid contract type")
        updates.append("contract_type = :type")
        params["type"] = data["contract_type"]
    if "require_for_booking" in data:
        updates.append("require_for_booking = :req")
        params["req"] = 1 if data["require_for_booking"] else 0
    if "contract_body" in data:
        updates.append("contract_body = :body")
        params["body"] = data["contract_body"]
    if "custom_fields" in data:
        updates.append("custom_fields = :fields")
        params["fields"] = json.dumps(data["custom_fields"]) if isinstance(data["custom_fields"], list) else data["custom_fields"]
    if "is_active" in data:
        if data["is_active"]:
            # Deactivate others first
            db.execute(
                text("UPDATE venue_contracts SET is_active = 0 WHERE venue_id = :vid"),
                {"vid": venue_id}
            )
        updates.append("is_active = :active")
        params["active"] = 1 if data["is_active"] else 0
    if "per_gig_pdf" in data:
        updates.append("per_gig_pdf = :pgp")
        params["pgp"] = 1 if data["per_gig_pdf"] else 0
    
    if updates:
        updates.append("updated_at = CURRENT_TIMESTAMP")
        sql = f"UPDATE venue_contracts SET {', '.join(updates)} WHERE id = :cid AND venue_id = :vid"
        db.execute(text(sql), params)
        # Audit fix (May 2026 part 8): if this update activated the contract,
        # ensure it's the ONLY active row for the venue (race-loser cleanup).
        if "is_active" in data and data["is_active"]:
            db.execute(
                text("UPDATE venue_contracts SET is_active = 0 WHERE venue_id = :vid AND id != :keep"),
                {"vid": venue_id, "keep": contract_id}
            )
        db.commit()

    return {"ok": True}


@router.delete("/api/venues/{venue_id}/contracts/{contract_id}")
def delete_venue_contract(venue_id: int, contract_id: int, user=Depends(get_current_user), db=Depends(get_db)):
    """Delete a contract template"""
    check_venue_access(db, venue_id, user.id)
    
    # Check for existing gig contracts using this template
    in_use = db.execute(
        text("SELECT COUNT(*) FROM gig_contracts WHERE venue_contract_id = :cid"),
        {"cid": contract_id}
    ).scalar()
    
    if in_use and in_use > 0:
        # Deactivate instead of deleting
        db.execute(
            text("UPDATE venue_contracts SET is_active = 0 WHERE id = :cid AND venue_id = :vid"),
            {"cid": contract_id, "vid": venue_id}
        )
        db.commit()
        return {"ok": True, "deactivated": True, "message": "Contract is in use by existing gigs. It has been deactivated instead of deleted."}
    
    db.execute(
        text("DELETE FROM venue_contracts WHERE id = :cid AND venue_id = :vid"),
        {"cid": contract_id, "vid": venue_id}
    )
    db.commit()
    return {"ok": True}


# ============================================
# PDF UPLOAD FOR CONTRACT TEMPLATE
# ============================================

@router.post("/api/venues/{venue_id}/contracts/{contract_id}/upload-pdf")
async def upload_contract_pdf(
    venue_id: int,
    contract_id: int,
    file: UploadFile = File(...),
    user=Depends(get_current_user),
    db=Depends(get_db)
):
    """Upload a PDF for a pdf_upload contract template"""
    check_venue_access(db, venue_id, user.id)
    
    # Verify contract belongs to venue
    existing = db.execute(
        text("SELECT id, contract_type FROM venue_contracts WHERE id = :cid AND venue_id = :vid"),
        {"cid": contract_id, "vid": venue_id}
    ).mappings().first()
    if not existing:
        raise HTTPException(404, "Contract not found")
    
    # Validate file (suffix + size + magic bytes)
    content = await _read_and_validate_pdf(file, max_mb=20)
    
    # Create upload directory
    contract_dir = os.path.join(UPLOAD_DIR, f"venue_{venue_id}")
    os.makedirs(contract_dir, exist_ok=True)
    
    # Save file with unique name
    filename = f"contract_{contract_id}_{uuid.uuid4().hex[:8]}.pdf"
    file_path = os.path.join(contract_dir, filename)
    
    with open(file_path, "wb") as f:
        f.write(content)
    
    # Update contract record
    web_path = f"/{file_path}"
    db.execute(
        text("UPDATE venue_contracts SET pdf_file_path = :path, updated_at = CURRENT_TIMESTAMP WHERE id = :cid"),
        {"path": web_path, "cid": contract_id}
    )
    db.commit()
    
    return {"ok": True, "file_path": web_path}


# ============================================
# GIG CONTRACTS — Creating instances when gig is booked
# ============================================

@router.post("/api/gigs/{gig_id}/contract")
def create_gig_contract(gig_id: int, request: Request, user=Depends(get_current_user), db=Depends(get_db)):
    """Create a contract instance for a booked gig"""
    artist_id = request.query_params.get('artist_id')
    if not artist_id:
        raise HTTPException(400, "artist_id required")
    artist_id = int(artist_id)
    _slot_id_q = request.query_params.get('slot_id')
    try:
        _create_slot_id = int(_slot_id_q) if _slot_id_q else None
    except ValueError:
        _create_slot_id = None
    
    # Get gig info
    gig = db.execute(
        text("SELECT id, venue_id, artist_id FROM gigs WHERE id = :gid"),
        {"gid": gig_id}
    ).mappings().first()
    if not gig:
        raise HTTPException(404, "Gig not found")
    
    venue_id = gig["venue_id"]
    
    # Verify user has access to either the venue or artist
    venue_access = db.execute(
        text("""
            SELECT 1 FROM venues v WHERE v.id = :vid AND (
                v.user_id = :uid
                OR EXISTS (SELECT 1 FROM entity_users eu WHERE eu.entity_type='venue' AND eu.entity_id=v.id AND eu.user_id=:uid)
            )
        """),
        {"vid": venue_id, "uid": user.id}
    ).first()
    
    artist_access = db.execute(
        text("""
            SELECT 1 FROM artists a WHERE a.id = :aid AND (
                a.user_id = :uid
                OR EXISTS (SELECT 1 FROM entity_users eu WHERE eu.entity_type='artist' AND eu.entity_id=a.id AND eu.user_id=:uid)
            )
        """),
        {"aid": artist_id, "uid": user.id}
    ).first()
    
    if not venue_access and not artist_access:
        raise HTTPException(403, "No access to this gig")
    
    # Check if contract already exists for this gig
    existing = db.execute(
        text("SELECT id, status FROM gig_contracts WHERE gig_id = :gid AND artist_id = :aid"),
        {"gid": gig_id, "aid": artist_id}
    ).mappings().first()
    
    if existing:
        return {"ok": True, "contract_id": existing["id"], "status": existing["status"], "already_exists": True}
    
    # Get active contract template for venue
    template = db.execute(
        text("SELECT * FROM venue_contracts WHERE venue_id = :vid AND is_active = 1 LIMIT 1"),
        {"vid": venue_id}
    ).mappings().first()
    
    if not template:
        return {"ok": False, "has_contract": False}
    
    template = dict(template)
    
    # Generate rendered body for auto_generated contracts
    rendered_body = ""
    pdf_file_path = None
    
    if template["contract_type"] == "auto_generated":
        rendered_body = generate_auto_contract(db, gig_id, venue_id, artist_id, slot_id=_create_slot_id)
    elif template["contract_type"] == "custom_builder":
        # Render the custom template with known variables
        rendered_body = template.get("contract_body", "")
        # Auto-fill known placeholders
        gig_data = db.execute(text("SELECT * FROM gigs WHERE id = :gid"), {"gid": gig_id}).mappings().first()
        artist_data = db.execute(
            text("SELECT a.*, u.first_name||' '||u.last_name as full_name, u.email, u.phone FROM artists a JOIN users u ON a.user_id=u.id WHERE a.id=:aid"),
            {"aid": artist_id}
        ).mappings().first()
        venue_data = db.execute(text("SELECT * FROM venues WHERE id = :vid"), {"vid": venue_id}).mappings().first()
        
        if rendered_body:
            replacements = {
                "artist_name": artist_data["name"] if artist_data else "",
                "artist_contact_name": artist_data["full_name"] if artist_data else "",
                "artist_email": artist_data["email"] if artist_data else "",
                "artist_phone": artist_data["phone"] if artist_data else "",
                "artist_city": artist_data["city"] if artist_data else "",
                "artist_state": artist_data["state"] if artist_data else "",
                "venue_name": venue_data["venue_name"] if venue_data else "",
                "venue_address": venue_data["address_line_1"] if venue_data else "",
                "venue_city": venue_data["city"] if venue_data else "",
                "venue_state": venue_data["state"] if venue_data else "",
                "gig_date": gig_data["date"] if gig_data else "",
                # Slot-aware times — see generate_auto_contract for rationale.
                # Multi-slot gig + per-artist contract → use THIS artist's
                # slot start/end so the contract doesn't span the umbrella.
                "gig_start_time": format_time_12hr(
                    (db.execute(text("""SELECT start_time FROM gig_slots WHERE gig_id = :gid AND artist_id = :aid
                                         AND status IN ('booked','pending_contract','awaiting_venue_contract')
                                         ORDER BY slot_number ASC LIMIT 1"""),
                                {"gid": gig_id, "aid": artist_id}).scalar()) or (gig_data["start_time"] if gig_data else "")
                ),
                "gig_end_time": format_time_12hr(
                    (db.execute(text("""SELECT end_time FROM gig_slots WHERE gig_id = :gid AND artist_id = :aid
                                         AND status IN ('booked','pending_contract','awaiting_venue_contract')
                                         ORDER BY slot_number ASC LIMIT 1"""),
                                {"gid": gig_id, "aid": artist_id}).scalar()) or (gig_data["end_time"] if gig_data else "")
                ),
                # Door-aware: _get_effective_pay_str returns the door split
                # terms ("$X guarantee + Y% of door receipts") when this
                # artist's slot has deal_type='door'; otherwise the flat
                # override-aware pay.
                "gig_pay": _get_effective_pay_str(db, dict(gig_data), venue_id, artist_id) if gig_data else "$0",
                "gig_title": gig_data["title"] if gig_data else "",
            }
            for key, val in replacements.items():
                # gig_pay is built from numeric fields (safe). All other
                # values are user-controlled (artist/venue display names,
                # email, addresses) and must be HTML-escaped before being
                # spliced into the template body — same XSS class as the
                # auto-generated contract path above.
                safe_val = val if key == "gig_pay" else _ce(val)
                rendered_body = rendered_body.replace(f"{{{{{key}}}}}", str(safe_val or ""))
    elif template["contract_type"] == "pdf_upload":
        pdf_file_path = template.get("pdf_file_path", "")
    
    # Create gig contract instance
    db.execute(
        text("""
            INSERT INTO gig_contracts
                (gig_id, venue_contract_id, venue_id, artist_id, contract_type,
                 rendered_body, filled_fields, pdf_file_path, status, created_at)
            VALUES (:gig_id, :vc_id, :vid, :aid, :type,
                    :body, '{}', :pdf, 'pending', CURRENT_TIMESTAMP)
        """),
        {
            "gig_id": gig_id,
            "vc_id": template["id"],
            "vid": venue_id,
            "aid": artist_id,
            "type": template["contract_type"],
            "body": rendered_body,
            "pdf": pdf_file_path,
        }
    )
    db.commit()
    
    new_id = db.execute(text("SELECT last_insert_rowid()")).scalar()
    
    return {"ok": True, "contract_id": new_id, "contract_type": template["contract_type"], "status": "pending"}


@router.get("/api/gig-contracts/{contract_id}")
def get_gig_contract(contract_id: int, user=Depends(get_current_user), db=Depends(get_db)):
    """Get a gig contract instance (for viewing/signing)"""
    row = db.execute(
        text("""
            SELECT gc.*, vc.name as template_name, vc.custom_fields,
                   v.venue_name, a.name as artist_name,
                   g.date as gig_date, g.start_time, g.end_time, g.pay, g.title as gig_title
            FROM gig_contracts gc
            LEFT JOIN venue_contracts vc ON gc.venue_contract_id = vc.id
            LEFT JOIN venues v ON gc.venue_id = v.id
            LEFT JOIN artists a ON gc.artist_id = a.id
            LEFT JOIN gigs g ON gc.gig_id = g.id
            WHERE gc.id = :cid
        """),
        {"cid": contract_id}
    ).mappings().first()
    
    if not row:
        raise HTTPException(404, "Contract not found")
    
    row = dict(row)
    
    # Verify access — user must be associated with either the venue or the artist
    venue_access = db.execute(
        text("""
            SELECT 1 FROM venues v WHERE v.id = :vid AND (
                v.user_id = :uid
                OR EXISTS (SELECT 1 FROM entity_users eu WHERE eu.entity_type='venue' AND eu.entity_id=v.id AND eu.user_id=:uid)
            )
        """),
        {"vid": row["venue_id"], "uid": user.id}
    ).first()
    
    artist_access = db.execute(
        text("""
            SELECT 1 FROM artists a WHERE a.id = :aid AND (
                a.user_id = :uid
                OR EXISTS (SELECT 1 FROM entity_users eu WHERE eu.entity_type='artist' AND eu.entity_id=a.id AND eu.user_id=:uid)
            )
        """),
        {"aid": row["artist_id"], "uid": user.id}
    ).first()
    
    # Audit fix (May 2026 part 6): admin bypass — `get_gig_contract_by_gig`
    # and `get_contract_preview` already honor admin; this endpoint didn't,
    # so admins doing support/audit got 403 fetching contract details by
    # contract_id. Mirrors the part-5 pattern in get_gig_contract_by_gig.
    is_admin_user = to_admin_bool(getattr(user, "is_admin", "false"))
    if not venue_access and not artist_access and not is_admin_user:
        raise HTTPException(403, "No access to this contract")

    row["is_venue_user"] = bool(venue_access)
    row["is_artist_user"] = bool(artist_access)

    return row


@router.get("/api/gigs/{gig_id}/contract")
def get_gig_contract_by_gig(gig_id: int, request: Request, user=Depends(get_current_user), db=Depends(get_db)):
    """Get contract for a specific gig (by gig ID). Use latest contract (ORDER BY id DESC) so re-booked gigs get the current one."""
    artist_id = request.query_params.get('artist_id')

    query = """
        SELECT gc.*, a.name as artist_name
        FROM gig_contracts gc
        LEFT JOIN artists a ON a.id = gc.artist_id
        WHERE gc.gig_id = :gid
    """
    params = {"gid": gig_id}

    if artist_id:
        query += " AND gc.artist_id = :aid"
        params["aid"] = int(artist_id)

    query += " ORDER BY gc.id DESC LIMIT 1"
    row = db.execute(text(query), params).mappings().first()

    if not row:
        return {"has_contract": False}

    # Audit fix (May 2026 part 5): authorize the caller against the contract's
    # gig before returning. Previously any logged-in user could GET this and
    # walk the contract body, both signatures' names + IPs, signed PDF paths,
    # and the rendered_body PII for any artist/venue combination.
    _gig = db.execute(
        text("SELECT venue_id FROM gigs WHERE id = :gid"),
        {"gid": gig_id}
    ).mappings().first()
    _venue_id = _gig["venue_id"] if _gig else None
    _contract_artist_id = row["artist_id"]
    _allowed = False
    if _venue_id is not None:
        try:
            check_venue_access(db, _venue_id, user.id)
            _allowed = True
        except Exception:
            pass
    if not _allowed and _contract_artist_id is not None:
        try:
            check_artist_access(db, _contract_artist_id, user.id)
            _allowed = True
        except Exception:
            pass
    if not _allowed:
        # Admins can always see (matches the rest of the contract endpoints).
        from backend.utils import to_admin_bool
        if not to_admin_bool(getattr(user, "is_admin", "false")):
            raise HTTPException(403, "Not authorized to view this contract")

    result = dict(row)
    result["has_contract"] = True
    return result


# ============================================
# ARTIST SIGNING
# ============================================

@router.post("/api/gig-contracts/{contract_id}/sign")
def sign_contract(contract_id: int, data: dict, request: Request, user=Depends(get_current_user), db=Depends(get_db)):
    """Artist signs a contract"""
    contract = db.execute(
        text("SELECT * FROM gig_contracts WHERE id = :cid"),
        {"cid": contract_id}
    ).mappings().first()
    
    if not contract:
        raise HTTPException(404, "Contract not found")
    
    contract = dict(contract)
    
    # Verify artist access
    check_artist_access(db, contract["artist_id"], user.id)
    
    if contract["status"] not in ("pending",):
        raise HTTPException(400, "Contract has already been signed")

    # Audit fix (May 2026 part 10): block pdf_upload bypass. The
    # sign_contract endpoint is for the DIGITAL signing flow; pdf_upload
    # contracts must be signed by uploading a signed PDF via
    # upload_signed_pdf. Without this gate, a malicious artist who knows
    # the contract_id could POST signature_name here and silently flip a
    # pdf_upload contract to 'artist_signed' without ever uploading a
    # signed PDF — then the venue countersign would "fully execute" a
    # contract with no artist signature on the document.
    # Bug fix (Jun 2026): the previous check compared `contract_type` to
    # the literal string "digital", which is not a value the schema ever
    # produces (valid types are pdf_upload | custom_builder |
    # auto_generated, see CHECK constraint on venue_contracts insert).
    # As a result the gate fired for EVERY typed contract and silently
    # blocked custom_builder + auto_generated contracts from ever being
    # signed via this endpoint. Correct behavior: block ONLY pdf_upload.
    if contract.get("contract_type") == "pdf_upload":
        raise HTTPException(
            400,
            "This contract requires uploading a signed PDF — use the PDF upload flow to sign it."
        )

    signature_name = data.get("signature_name", "").strip()
    if not signature_name:
        raise HTTPException(400, "Signature name is required")
    
    # Get client IP
    client_ip = request.client.host if request.client else "unknown"
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        client_ip = forwarded.split(",")[0].strip()
    
    # Save filled fields if provided
    filled_fields = json.dumps(data.get("filled_fields", {}))
    
    now = utcnow_naive().isoformat() + 'Z'
    
    # Audit fix (May 2026 part 3): atomic-claim guard. The Python-side
    # status check above is non-atomic; two concurrent sign requests
    # (double-clicked button, retried email link) would both pass the
    # check and both UPDATE — firing duplicate transition logic + emails
    # downstream. Constrain the UPDATE to status='pending' and check
    # rowcount; if the row was already signed, return idempotently.
    _sign_res = db.execute(
        text("""
            UPDATE gig_contracts SET
                status = 'artist_signed',
                filled_fields = :fields,
                artist_signature_name = :sig_name,
                artist_signature_date = :sig_date,
                artist_signature_ip = :sig_ip
            WHERE id = :cid AND status = 'pending'
        """),
        {
            "fields": filled_fields,
            "sig_name": signature_name,
            "sig_date": now,
            "sig_ip": client_ip,
            "cid": contract_id,
        }
    )
    if _sign_res.rowcount == 0:
        # Lost the race — somebody else already advanced the contract.
        # Return idempotently so the user sees "Signed" instead of an
        # error if they double-clicked.
        db.commit()
        return {"ok": True, "already_signed": True}
    
    # Transition gig (and any held slots) from pending → booked.
    # Audit fix (May 2026 part 2): previously this only matched
    # gig.status='pending_contract', which is the single-slot signal.
    # For multi-slot the gig stays 'open' and the SLOT is pending_contract.
    # The old code left the slot stuck in pending_contract forever, with
    # no transaction created and no booking emails sent. Now we mirror
    # the multi-slot handling from countersign_contract:
    #   - Flip every slot held for this artist (pending_contract/open) to booked
    #   - If all slots booked → mark parent gig 'booked'; otherwise just
    #     clear the contract hold on the parent and leave status 'open'.
    gig = db.execute(
        text("""SELECT id, status, venue_id, pay, date
                FROM gigs WHERE id = :gid"""),
        {"gid": contract["gig_id"]}
    ).mappings().first()
    if gig and gig["status"] in ("pending_contract", "open", "awaiting_venue_contract"):
        artist_id_local = contract["artist_id"]
        # Audit fix (May 2026 part 5): defer the booking-transaction creation
        # and booking emails to countersign_contract, matching what
        # upload_signed_pdf already does. Previously this path flipped slots
        # directly to 'booked' and created the venue charge at artist-sign —
        # if the venue never countersigned, the scheduler would still charge
        # the venue on payout day. The "fewer undos if venue never signs"
        # invariant only held for the PDF path, not the electronic path.
        # Now both paths move slots to 'awaiting_venue_contract' and parent
        # gig matches; the countersign handler flips slots to 'booked',
        # applies pay overrides, creates the venue charge, and sends booking
        # emails — all in one atomic step gated on `status='artist_signed'`.
        db.execute(
            text("""UPDATE gig_slots SET status = 'awaiting_venue_contract'
                    WHERE gig_id = :gid AND artist_id = :aid
                      AND status IN ('pending_contract', 'open')"""),
            {"gid": contract["gig_id"], "aid": artist_id_local}
        )
        # Parent gig: if every slot is now in awaiting-state (no still-open
        # slots), flip parent to awaiting_venue_contract too so the calendar
        # bubble reflects "awaiting venue countersign". Otherwise leave the
        # parent 'open' so other slots remain bookable.
        still_open = db.execute(
            text("SELECT COUNT(*) FROM gig_slots WHERE gig_id = :gid AND status = 'open'"),
            {"gid": contract["gig_id"]}
        ).scalar() or 0
        # Audit fix (May 2026 part 6): set a 48h countersign deadline instead of
        # NULL. The expiration sweep (_cleanup_expired_holds_impl) keys off
        # contract_hold_expires_at < now and skips NULL — so leaving NULL here
        # would permanently lock the slot in awaiting_venue_contract if the
        # venue never countersigns.
        from datetime import timedelta as _td_sign
        _countersign_deadline = (utcnow_naive() + _td_sign(hours=48)).isoformat()
        if still_open == 0:
            db.execute(
                text("""UPDATE gigs SET status = 'awaiting_venue_contract',
                              artist_id = :aid,
                              contract_hold_artist_id = :aid,
                              contract_hold_expires_at = :exp,
                              radius_blast_token = NULL
                        WHERE id = :gid"""),
                {"gid": contract["gig_id"], "aid": artist_id_local, "exp": _countersign_deadline}
            )
        else:
            db.execute(
                text("""UPDATE gigs SET artist_id = :aid,
                              contract_hold_artist_id = :aid,
                              contract_hold_expires_at = :exp,
                              radius_blast_token = NULL
                        WHERE id = :gid"""),
                {"gid": contract["gig_id"], "aid": artist_id_local, "exp": _countersign_deadline}
            )
    
    # Notify venue that artist signed
    try:
        venue_data = db.execute(text("SELECT venue_name, user_id FROM venues WHERE id = :vid"), {"vid": contract["venue_id"]}).mappings().first()
        artist_data = db.execute(text("SELECT name FROM artists WHERE id = :aid"), {"aid": contract["artist_id"]}).mappings().first()
        gig_data = db.execute(text("SELECT date, title FROM gigs WHERE id = :gid"), {"gid": contract["gig_id"]}).mappings().first()
        if venue_data and artist_data:
            gig_date_fmt = ""
            if gig_data and gig_data.get("date"):
                try:
                    from datetime import datetime
                    d = datetime.strptime(gig_data["date"][:10], "%Y-%m-%d")
                    gig_date_fmt = f"{d.month}/{d.day}/{d.year}"
                except Exception:
                    gig_date_fmt = gig_data["date"][:10] if gig_data.get("date") else ""
            msg_venue = f"{artist_data['name']} has signed the contract for a gig on {gig_date_fmt}." if gig_date_fmt else f"{artist_data['name']} has signed the contract for a gig."
            db.execute(text("""INSERT INTO notifications (user_id, notification_type, title, message, gig_id, venue_id, artist_id, is_read, created_at)
                VALUES (:uid, 'contract_countersign_needed', :t, :m, :gid, :vid, :aid, 0, CURRENT_TIMESTAMP)"""),
                {"uid": venue_data["user_id"], "t": "Contract Signed",
                 "m": msg_venue,
                 "gid": contract["gig_id"], "vid": contract["venue_id"], "aid": contract["artist_id"]})
    except Exception:
        pass
    
    db.commit()
    
    # NOTE: Venue email is sent by book-with-contract when the contract is first created.
    # sign_contract is only called for pre-existing pending contracts (PDF upload flow via
    # contract-sign.html). Do NOT send another email here — it would be a duplicate.
    
    return {"ok": True, "status": "artist_signed"}


# ============================================
# PDF VENUE COUNTERSIGNATURE STAMP
# ============================================

def _generate_signature_page_pdf(venue_name, signature_name, sig_date, sig_ip, artist_name="", gig_date="", contract_id=0):
    """Generate a single-page raw PDF with the venue's digital countersignature.
    Uses the same raw-PDF approach as _generate_pdf_native — no external packages."""
    
    PW, PH = 612, 792  # US Letter
    
    def esc(t):
        return str(t).replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)')
    
    # Format the signature date nicely
    nice_date = sig_date
    try:
        dt = datetime.fromisoformat(sig_date.replace('Z', '+00:00'))
        nice_date = dt.strftime('%B %d, %Y at %I:%M %p UTC')
    except Exception:
        pass
    
    # Build page content stream
    lines = []
    y = PH - 72  # Start 1 inch from top
    
    # Decorative header line
    lines.append(f'0.467 0.333 0.800 RG 2 w 54 {y} m 558 {y} l S')  # Purple line
    y -= 30
    
    # Title
    lines.append(f'BT /F2 16 Tf 54 {y} Td (VENUE COUNTERSIGNATURE) Tj ET')
    y -= 28
    lines.append(f'0.5 0.5 0.5 RG 0.5 w 54 {y} m 300 {y} l S')  # Gray underline
    lines.append('0 0 0 RG')  # Reset to black
    y -= 30
    
    # Contract info — audit fix (May 2026 part 10b): explicit legal language
    # tying this page to the artist-signed page. This page is APPENDED to the
    # artist's signed PDF; the legal reading is that both pages together
    # constitute the fully-executed contract.
    lines.append(f'BT /F1 10 Tf 54 {y} Td (This page is the venue countersignature of the contract on the preceding pages.) Tj ET')
    y -= 16
    lines.append(f'BT /F1 10 Tf 54 {y} Td (By countersigning, the venue accepts and agrees to all terms set forth therein.) Tj ET')
    y -= 16
    lines.append(f'BT /F1 10 Tf 54 {y} Td (The artist signature on the prior pages and this countersignature together) Tj ET')
    y -= 16
    lines.append(f'BT /F1 10 Tf 54 {y} Td (constitute the fully-executed contract between the parties.) Tj ET')
    y -= 28
    
    # Details box background
    box_top = y + 8
    box_height = 130
    lines.append(f'0.96 0.96 0.98 rg 48 {box_top - box_height} {PW - 96} {box_height} re f')
    lines.append(f'0.85 0.85 0.90 RG 0.5 w 48 {box_top - box_height} {PW - 96} {box_height} re S')
    lines.append('0 0 0 rg 0 0 0 RG')
    
    # Details
    lines.append(f'BT /F2 10 Tf 66 {y} Td (Venue:) Tj ET')
    lines.append(f'BT /F1 10 Tf 160 {y} Td ({esc(venue_name)}) Tj ET')
    y -= 20
    
    lines.append(f'BT /F2 10 Tf 66 {y} Td (Signed By:) Tj ET')
    lines.append(f'BT /F1 10 Tf 160 {y} Td ({esc(signature_name)}) Tj ET')
    y -= 20
    
    lines.append(f'BT /F2 10 Tf 66 {y} Td (Date:) Tj ET')
    lines.append(f'BT /F1 10 Tf 160 {y} Td ({esc(nice_date)}) Tj ET')
    y -= 20
    
    lines.append(f'BT /F2 10 Tf 66 {y} Td (IP Address:) Tj ET')
    lines.append(f'BT /F1 10 Tf 160 {y} Td ({esc(sig_ip)}) Tj ET')
    y -= 20
    
    if contract_id:
        lines.append(f'BT /F2 10 Tf 66 {y} Td (Contract ID:) Tj ET')
        lines.append(f'BT /F1 10 Tf 160 {y} Td ({esc(contract_id)}) Tj ET')
        y -= 20
    
    if gig_date:
        lines.append(f'BT /F2 10 Tf 66 {y} Td (Gig Date:) Tj ET')
        lines.append(f'BT /F1 10 Tf 160 {y} Td ({esc(gig_date)}) Tj ET')
        y -= 20
    
    y -= 20
    
    # Signature rendering (cursive-style using Times-Italic)
    lines.append(f'BT /F1 9 Tf 54 {y} Td (Venue Signature:) Tj ET')
    y -= 6
    lines.append(f'0.75 0.75 0.75 RG 0.5 w 54 {y} m 350 {y} l S')  # Signature line
    lines.append('0 0 0 RG')
    y -= 2
    lines.append(f'BT /F3 20 Tf 60 {y - 18} Td ({esc(signature_name)}) Tj ET')
    y -= 30
    lines.append(f'BT /F1 8 Tf 54 {y - 8} Td (Countersigned: {esc(nice_date)}) Tj ET')
    y -= 40
    
    # If we also have the artist signature info, show it
    if artist_name:
        lines.append(f'BT /F1 9 Tf 54 {y} Td (Artist Signature:) Tj ET')
        y -= 6
        lines.append(f'0.75 0.75 0.75 RG 0.5 w 54 {y} m 350 {y} l S')
        lines.append('0 0 0 RG')
        y -= 2
        lines.append(f'BT /F3 20 Tf 60 {y - 18} Td ({esc(artist_name)}) Tj ET')
        y -= 50
    
    # Status stamp
    y -= 20
    # Green "FULLY EXECUTED" stamp
    lines.append(f'0.133 0.545 0.133 RG 2 w')
    stamp_x, stamp_y = 160, y - 10
    lines.append(f'{stamp_x} {stamp_y} 290 40 re S')
    lines.append(f'BT /F2 18 Tf 0.133 0.545 0.133 rg {stamp_x + 18} {stamp_y + 12} Td (FULLY EXECUTED) Tj ET')
    lines.append('0 0 0 rg 0 0 0 RG')
    
    y -= 70
    
    # Footer
    lines.append(f'0.75 0.75 0.75 RG 0.5 w 54 {y} m 558 {y} l S')
    lines.append('0 0 0 RG')
    y -= 14
    footer = f'Generated by GigsFill  |  Contract ID: {contract_id}  |  {utcnow_naive().strftime("%B %d, %Y")}'
    lines.append(f'BT /F1 7 Tf 0.5 0.5 0.5 rg 180 {y} Td ({esc(footer)}) Tj ET')
    
    # Audit fix (May 2026 part 10b): transliterate non-latin-1 characters
    # (e.g. accented vowels in artist names like "José" or "Beyoncé") to
    # their closest ASCII equivalent before encoding. The previous
    # errors='replace' rendered them as "?", making signatures unreadable.
    stream_content = '\n'.join(lines)
    try:
        import unicodedata as _ud
        stream_content = _ud.normalize('NFKD', stream_content).encode('ascii', 'ignore').decode('ascii') \
            if any(ord(c) > 127 for c in stream_content) else stream_content
        # Re-apply NFC for any chars that survived
    except Exception:
        pass
    stream_bytes = stream_content.encode('latin-1', errors='replace')
    stream_len = len(stream_bytes)
    
    # Build minimal raw PDF with one page
    objects = []
    
    # Obj 1: Catalog
    objects.append(b'1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj')
    # Obj 2: Pages
    objects.append(b'2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj')
    # Obj 3: Page
    objects.append(f'3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {PW} {PH}] /Contents 4 0 R /Resources << /Font << /F1 5 0 R /F2 6 0 R /F3 7 0 R >> >> >>\nendobj'.encode())
    # Obj 4: Content stream
    objects.append(f'4 0 obj\n<< /Length {stream_len} >>\nstream\n'.encode() + stream_bytes + b'\nendstream\nendobj')
    # Obj 5-7: Fonts
    objects.append(b'5 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj')
    objects.append(b'6 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>\nendobj')
    objects.append(b'7 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Times-Italic >>\nendobj')
    
    # Assemble PDF
    pdf = bytearray(b'%PDF-1.4\n')
    offsets = []
    for obj in objects:
        offsets.append(len(pdf))
        pdf.extend(obj)
        pdf.extend(b'\n')
    
    xref_offset = len(pdf)
    pdf.extend(f'xref\n0 {len(objects) + 1}\n'.encode())
    pdf.extend(b'0000000000 65535 f \n')
    for off in offsets:
        pdf.extend(f'{off:010d} 00000 n \n'.encode())
    
    pdf.extend(f'trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n'.encode())
    
    return bytes(pdf)


def _stamp_venue_signature_on_pdf(db, contract, signature_name, sig_date, sig_ip):
    """Append a venue countersignature page to the artist's signed PDF.
    Uses pypdf to merge the existing signed PDF with a generated signature page."""
    
    signed_path = contract.get("signed_pdf_path", "")
    logger.debug(f"[PDF STAMP INNER] signed_pdf_path from contract: '{signed_path}'")
    if not signed_path:
        logger.warning(f"No signed_pdf_path for contract {contract.get('id')}")
        logger.debug(f"[PDF STAMP INNER] ABORT: No signed_pdf_path")
        return "abort_no_path"
    
    # Resolve the file path — try multiple approaches
    file_path = None
    candidates = [
        signed_path,                          # as-is from DB
        signed_path.lstrip("/"),              # strip leading slash (relative)
        os.path.join(".", signed_path.lstrip("/")),  # explicit relative
    ]
    logger.debug(f"[PDF STAMP INNER] CWD: {os.getcwd()}")
    for candidate in candidates:
        exists = os.path.exists(candidate)
        logger.debug(f"[PDF STAMP INNER] Trying path: '{candidate}' → exists={exists}")
        if exists:
            file_path = candidate
            break
    
    if not file_path:
        logger.warning(f"Signed PDF not found. Tried: {candidates}")
        logger.debug(f"[PDF STAMP INNER] ABORT: File not found at any candidate path")
        return "abort_file_not_found"
    
    original_size = os.path.getsize(file_path)
    logger.debug(f"[PDF STAMP INNER] Found PDF at: {file_path} (size={original_size} bytes)")
    # Get venue/artist/gig info for the signature page
    gig_info = db.execute(
        text("""SELECT g.date, v.venue_name, a.name as artist_name
                FROM gigs g LEFT JOIN venues v ON v.id = g.venue_id 
                LEFT JOIN artists a ON a.id = :aid WHERE g.id = :gid"""),
        {"gid": contract["gig_id"], "aid": contract["artist_id"]}
    ).mappings().first()
    
    venue_name = (gig_info and gig_info.get("venue_name")) or "Venue"
    artist_name = (gig_info and gig_info.get("artist_name")) or ""
    gig_date = str((gig_info and gig_info.get("date")) or "")[:10]
    
    # Also get the artist signature name from the contract record
    artist_sig_name = contract.get("artist_signature_name") or artist_name
    
    # Generate the countersignature page
    sig_page_pdf = _generate_signature_page_pdf(
        venue_name=venue_name,
        signature_name=signature_name,
        sig_date=sig_date,
        sig_ip=sig_ip,
        artist_name=artist_sig_name,
        gig_date=gig_date,
        contract_id=contract.get("id", 0)
    )
    logger.debug(f"[PDF STAMP INNER] Generated signature page ({len(sig_page_pdf)} bytes)")
    # Try to merge with pypdf/PyPDF2
    pdf_lib = None
    PdfReader = None
    PdfWriter = None
    import sys
    logger.debug(f"[PDF STAMP INNER] Python executable: {sys.executable}")
    logger.debug(f"[PDF STAMP INNER] sys.path: {sys.path[:5]}")
    try:
        from pypdf import PdfReader as _R, PdfWriter as _W
        PdfReader, PdfWriter = _R, _W
        pdf_lib = "pypdf"
        logger.debug(f"[PDF STAMP INNER] pypdf imported successfully")
    except ImportError as ie1:
        logger.debug(f"[PDF STAMP INNER] pypdf import failed: {ie1}")
        try:
            from PyPDF2 import PdfReader as _R, PdfWriter as _W
            PdfReader, PdfWriter = _R, _W
            pdf_lib = "PyPDF2"
            logger.debug(f"[PDF STAMP INNER] PyPDF2 imported successfully")
        except ImportError as ie2:
            pdf_lib = None
            logger.debug(f"[PDF STAMP INNER] PyPDF2 import also failed: {ie2}")
            logger.debug(f"[PDF STAMP INNER] Neither pypdf nor PyPDF2 available!")
    if not pdf_lib:
        # No PDF library available — save signature page as separate file alongside
        sig_path = file_path.replace('.pdf', '_countersigned.pdf')
        with open(sig_path, 'wb') as f:
            f.write(sig_page_pdf)
        logger.warning(f"No pypdf/PyPDF2 available; saved separate countersign page to {sig_path}")
        logger.debug(f"[PDF STAMP INNER] FALLBACK: Saved separate file to {sig_path}")
        return "fallback_no_pdf_lib"
    
    logger.info(f"Using {pdf_lib} to merge signature page")
    
    # Write signature page to a temp file IN THE SAME DIRECTORY (avoid cross-device issues)
    target_dir = os.path.dirname(file_path) or "."
    sig_tmp_path = os.path.join(target_dir, f"_sig_tmp_{contract.get('id', 0)}.pdf")
    
    try:
        with open(sig_tmp_path, 'wb') as f:
            f.write(sig_page_pdf)
        logger.debug(f"[PDF STAMP INNER] Wrote sig temp file: {sig_tmp_path}")
        # Read existing signed PDF and append signature page
        writer = PdfWriter()
        
        reader = PdfReader(file_path)
        logger.debug(f"[PDF STAMP INNER] Original PDF has {len(reader.pages)} pages")
        for page in reader.pages:
            writer.add_page(page)
        
        sig_reader = PdfReader(sig_tmp_path)
        logger.debug(f"[PDF STAMP INNER] Signature page PDF has {len(sig_reader.pages)} pages")
        for page in sig_reader.pages:
            writer.add_page(page)
        
        # Write merged PDF directly to the SAME path (overwrite in place)
        # Use a temp output in the same directory to avoid cross-device moves
        out_tmp_path = os.path.join(target_dir, f"_merged_tmp_{contract.get('id', 0)}.pdf")
        with open(out_tmp_path, 'wb') as out_f:
            writer.write(out_f)
        
        merged_size = os.path.getsize(out_tmp_path)
        logger.debug(f"[PDF STAMP INNER] Merged PDF written to temp: {out_tmp_path} ({merged_size} bytes)")
        # Replace original with merged version (same filesystem = atomic rename)
        shutil.move(out_tmp_path, file_path)
        
        final_size = os.path.getsize(file_path)
        logger.debug(f"[PDF STAMP INNER] SUCCESS: Replaced {file_path} (was {original_size} → now {final_size} bytes)")
        # Verify the stamp actually made the file bigger
        if final_size <= original_size:
            logger.debug(f"[PDF STAMP INNER] WARNING: File size did NOT increase! Stamp may have failed.")
        else:
            logger.debug(f"[PDF STAMP INNER] VERIFIED: File grew by {final_size - original_size} bytes ✓")
        # Update DB with the resolved path (so download endpoint can find it reliably)
        db.execute(
            text("UPDATE gig_contracts SET signed_pdf_path = :path WHERE id = :cid"),
            {"path": f"/{file_path.lstrip('/')}", "cid": contract.get("id")}
        )
        
        logger.info(f"Successfully stamped venue countersignature onto PDF: {file_path}")
        stamp_result = "merged"
        
    except Exception as e:
        logger.error(f"PDF merge failed: {e}", exc_info=True)
        logger.debug(f"[PDF STAMP INNER] MERGE ERROR: {e}")
        import traceback
        traceback.print_exc()
        # Fallback: save signature page as separate file
        sig_path = file_path.replace('.pdf', '_countersigned.pdf')
        with open(sig_path, 'wb') as f:
            f.write(sig_page_pdf)
        logger.info(f"Saved separate countersign page to {sig_path}")
        logger.debug(f"[PDF STAMP INNER] FALLBACK: Saved separate file to {sig_path}")
        stamp_result = f"fallback_merge_failed: {e}"
    finally:
        # Clean up temp files
        for tmp in [sig_tmp_path, os.path.join(target_dir, f"_merged_tmp_{contract.get('id', 0)}.pdf")]:
            try:
                if os.path.exists(tmp):
                    os.unlink(tmp)
            except Exception:
                pass
    
    return stamp_result


@router.post("/api/gig-contracts/{contract_id}/countersign")
def countersign_contract(contract_id: int, data: dict, request: Request, user=Depends(get_current_user), db=Depends(get_db)):
    """Venue countersigns a contract — stamps digital signature onto PDF for pdf_upload contracts"""
    contract = db.execute(
        text("SELECT * FROM gig_contracts WHERE id = :cid"),
        {"cid": contract_id}
    ).mappings().first()
    
    if not contract:
        raise HTTPException(404, "Contract not found")
    
    contract = dict(contract)
    
    check_venue_access(db, contract["venue_id"], user.id)
    
    if contract["status"] != "artist_signed":
        raise HTTPException(400, "Artist must sign first")
    
    signature_name = data.get("signature_name", "").strip()
    if not signature_name:
        raise HTTPException(400, "Signature name is required")
    
    client_ip = request.client.host if request.client else "unknown"
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        client_ip = forwarded.split(",")[0].strip()
    
    now = utcnow_naive().isoformat() + 'Z'

    # Audit fix (May 2026 part 10): for pdf_upload contracts, STAMP THE PDF
    # FIRST. If the stamp fails, refuse to mark the contract fully_signed
    # — otherwise the DB says "executed" but the file on disk lacks the
    # venue signature. Previously this UPDATE ran first, then stamp errors
    # were swallowed silently and the gig still became 'booked'.
    pdf_stamp_result = "skipped"
    if contract.get("contract_type") == "pdf_upload":
        if not contract.get("signed_pdf_path"):
            raise HTTPException(409, "Cannot countersign: artist has not uploaded a signed PDF yet.")
        try:
            stamp_status = _stamp_venue_signature_on_pdf(db, contract, signature_name, now, client_ip)
            pdf_stamp_result = stamp_status or "returned_none"
            # Treat known-failure return strings the same as exceptions
            if isinstance(stamp_status, str) and stamp_status.startswith(("abort_", "error:", "fallback_")):
                raise RuntimeError(f"stamp returned {stamp_status}")
        except Exception as e:
            logger.error(f"PDF stamp failed for contract {contract_id}: {e}", exc_info=True)
            # Alert admin so an operator can intervene before the venue tries again
            try:
                from backend.payout_scheduler import _send_admin_alert
                from backend.db import get_db_connection
                _c = get_db_connection()
                try:
                    _send_admin_alert(
                        _c,
                        f"Countersign stamp failed — contract #{contract_id}",
                        f"<p>Venue tried to countersign contract <strong>#{contract_id}</strong> "
                        f"on gig #{contract.get('gig_id')} but the PDF stamp failed:</p>"
                        f"<pre>{str(e)[:300]}</pre>"
                        f"<p>The contract has NOT been marked fully_signed. The venue will need "
                        f"to retry. If this persists, the artist's uploaded PDF may be corrupt.</p>"
                    )
                finally:
                    _c.close()
            except Exception:
                pass
            raise HTTPException(
                500,
                "Could not stamp signature onto the contract PDF. "
                "Your countersign was NOT recorded. Please retry; if this persists, contact support."
            )

    # Audit fix (May 2026 part 3): atomic-claim guard. The Python check
    # above is non-atomic; a double-clicked countersign would otherwise
    # re-fire _create_booking_transaction + booking emails. Constrain to
    # status='artist_signed' and bail idempotently on lost-race.
    _cs_res = db.execute(
        text("""
            UPDATE gig_contracts SET
                status = 'fully_signed',
                venue_signature_name = :sig_name,
                venue_signature_date = :sig_date,
                venue_signature_ip = :sig_ip
            WHERE id = :cid AND status = 'artist_signed'
        """),
        {
            "sig_name": signature_name,
            "sig_date": now,
            "sig_ip": client_ip,
            "cid": contract_id,
        }
    )
    if _cs_res.rowcount == 0:
        db.commit()
        return {"ok": True, "already_countersigned": True}

    logger.info(f"Countersign contract {contract_id}: type={contract.get('contract_type')} pdf_stamp={pdf_stamp_result}")
    if False:  # vestigial legacy block kept removed
        try:
            stamp_status = _stamp_venue_signature_on_pdf(db, contract, signature_name, now, client_ip)
            pdf_stamp_result = stamp_status or "returned_none"
        except Exception as e:
            logger.error(f"Failed to stamp venue signature on PDF contract {contract_id}: {e}", exc_info=True)
            pdf_stamp_result = f"error: {e}"
    elif contract.get("contract_type") == "pdf_upload":
        logger.warning(f"Contract {contract_id} is pdf_upload but has no signed_pdf_path")
        logger.debug(f"[PDF STAMP] WARNING: Contract {contract_id} is pdf_upload but has no signed_pdf_path")
        pdf_stamp_result = "no_signed_pdf_path"
    else:
        logger.debug(f"[PDF STAMP] Skipping stamp: contract_type={contract.get('contract_type')} (not pdf_upload)")
        pdf_stamp_result = f"not_pdf_upload: {contract.get('contract_type')}"
    
    # Delete old pre-countersign notifications FIRST (so they are gone before we create the new one; delete by gig_id only so we never miss due to artist_id/venue_id type or null)
    db.execute(
        text("""DELETE FROM notifications 
        WHERE gig_id = :gid AND notification_type IN ('contract_countersign_needed', 'contract_artist_signed')"""),
        {"gid": contract["gig_id"]}
    )
    
    # Notify both parties that contract is fully signed (venue: "Contract Signed — Booking Confirmed"; activity center shows "X booked a gig at Y on Date at Time. Countersign Completed ✓")
    try:
        _create_booking_notifications(db, contract["gig_id"], contract["venue_id"], contract["artist_id"], "fully_signed")
    except Exception:
        pass
    
    # Transition gig from pending_contract to booked (use contract's venue_id/artist_id so we never use stale gig row)
    # For multi-slot gigs, parent status may still be "open" — also match that case
    gig_before = db.execute(
        text("SELECT id, status, venue_id, artist_id, pay, date, start_time, end_time FROM gigs WHERE id = :gid"),
        {"gid": contract["gig_id"]}
    ).mappings().first()
    venue_id = contract["venue_id"]
    artist_id = contract["artist_id"]
    if gig_before and gig_before["status"] in ('pending_contract', 'open', 'awaiting_venue_contract'):
        # Audit fix (May 2026 part 5): include 'awaiting_venue_contract' in the
        # IN clause — that's the new intermediate state sign_contract leaves
        # slots in (previously sign_contract flipped them directly to 'booked',
        # but that meant the venue charge fired before countersign).
        db.execute(
            text("UPDATE gig_slots SET status = 'booked' WHERE gig_id = :gid AND artist_id = :aid AND status IN ('pending_contract', 'open', 'awaiting_venue_contract')"),
            {"gid": contract["gig_id"], "aid": artist_id}
        )
        # Apply pay override to all newly booked slots
        try:
            booked_slot_ids = db.execute(
                text("SELECT id, pay FROM gig_slots WHERE gig_id = :gid AND artist_id = :aid AND status = 'booked'"),
                {"gid": contract["gig_id"], "aid": artist_id}
            ).mappings().all()
            for s in booked_slot_ids:
                _apply_slot_pay_override(db, s["id"], venue_id, artist_id)
        except Exception as _pe:
            logger.warning(f"Pay override on countersign failed: {_pe}")
        # If all slots are now booked, mark parent gig booked too
        open_slots = db.execute(
            text("SELECT COUNT(*) FROM gig_slots WHERE gig_id = :gid AND status NOT IN ('booked')"),
            {"gid": contract["gig_id"]}
        ).scalar()
        # Audit fix (May 2026 part 7): on the multi-slot path, do NOT
        # unconditionally NULL `contract_hold_*` — if other artists are still
        # awaiting countersign on other slots of this gig, their hold tracker
        # would be nuked and the expiration sweep could never reap them. Only
        # clear those columns when they point at the artist we just
        # countersigned (or when no other in-flight contracts remain on this
        # gig). Per-contract `gig_contracts.hold_expires_at` is still the
        # source of truth for the sweep (post-part-7).
        if open_slots == 0:
            db.execute(
                text("UPDATE gigs SET status = 'booked', artist_id = :aid, "
                     "    contract_hold_artist_id = NULL, "
                     "    contract_hold_expires_at = NULL, "
                     "    radius_blast_token = NULL "
                     "WHERE id = :gid"),
                {"gid": contract["gig_id"], "aid": artist_id}
            )
        else:
            db.execute(
                text("UPDATE gigs SET artist_id = :aid, "
                     "    contract_hold_artist_id = CASE WHEN contract_hold_artist_id = :aid THEN NULL ELSE contract_hold_artist_id END, "
                     "    contract_hold_expires_at = CASE WHEN contract_hold_artist_id = :aid OR contract_hold_artist_id IS NULL THEN NULL ELSE contract_hold_expires_at END, "
                     "    radius_blast_token = NULL "
                     "WHERE id = :gid"),
                {"gid": contract["gig_id"], "aid": artist_id}
            )
            # Set any pending_contract slots for this artist to booked
            db.execute(
                text("UPDATE gig_slots SET status = 'booked' WHERE gig_id = :gid AND artist_id = :aid AND status = 'pending_contract'"),
                {"gid": contract["gig_id"], "aid": artist_id}
            )
        # Audit fix (May 2026 part 9): for multi-slot gigs, gigs.pay is MAX(slot.pay)
        # — using it would charge every artist the highest booked slot's pay, not
        # their own. Sum THIS artist's booked-slot pays instead. Falls back to
        # gigs.pay for single-slot gigs (where pay lives on the gig row, not on
        # gig_slots).
        _slot_pay_sum = db.execute(
            text("""SELECT COALESCE(SUM(pay), 0) FROM gig_slots
                    WHERE gig_id = :gid AND artist_id = :aid AND status = 'booked'"""),
            {"gid": contract["gig_id"], "aid": artist_id}
        ).scalar() or 0
        gig_pay = _slot_pay_sum if _slot_pay_sum > 0 else (gig_before.get("pay") or 0)
        gig_date = gig_before.get("date")
        gig_start = gig_before.get("start_time")
        gig_end = gig_before.get("end_time")
        # Audit fix (May 2026 part 5): clear last_cancelled_artist_id on a
        # successful countersign so a previously-cancelled artist isn't
        # permanently excluded from future cancellation blasts on this gig.
        # Mirrors book_slot's clearing at gigs.py:3999.
        try:
            db.execute(
                text("UPDATE gigs SET last_cancelled_artist_id = NULL WHERE id = :gid"),
                {"gid": contract["gig_id"]}
            )
        except Exception:
            pass
        # Remove the booking artist from waitlist (they booked — no longer waiting)
        # Only clear ALL waitlist entries if gig is now fully booked (no open slots)
        # Preserve waitlist for partially-booked multi-slot gigs
        try:
            from backend.routes.waitlist import cleanup_gig_waitlist as _cwl_cs
            # Only remove the booking artist from waitlist — not others
            db.execute(text("DELETE FROM gig_waitlist WHERE gig_id = :gid AND artist_id = :aid"),
                       {"gid": contract["gig_id"], "aid": artist_id})
            # Log waitlist state for debugging
            _wl_count = db.execute(text("SELECT COUNT(*) FROM gig_waitlist WHERE gig_id=:gid AND (offer_declined=0 OR offer_declined IS NULL)"),
                                   {"gid": contract["gig_id"]}).scalar()
            logger.info(f"[COUNTERSIGN] waitlist count after removing booking artist: {_wl_count}")
            open_left = db.execute(text("SELECT COUNT(*) FROM gig_slots WHERE gig_id = :gid AND status = 'open'"),
                                   {"gid": contract["gig_id"]}).scalar()
            logger.info(f"[COUNTERSIGN] open slots remaining: {open_left}, waitlist preserved for cancellation handling")
            # Do NOT clear waitlist on full booking — keep it for cancellation handling.
            # cleanup_gig_waitlist is only for gig deletion/cancellation.
            db.commit()
        except Exception as _wle:
            logger.warning(f"Waitlist cleanup on countersign failed: {_wle}")
        # Create payment transaction only now that venue has countersigned (gig officially booked)
        _create_booking_transaction(
            db, contract["gig_id"], venue_id, artist_id,
            gig_pay, gig_date
        )
        # Send booked-gig emails — only for the slot just countersigned
        try:
            from backend.services.email_dispatch import send_booking_emails
            _cs_slot = db.execute(text(
                "SELECT id FROM gig_slots WHERE gig_id=:gid AND artist_id=:aid AND status='booked' ORDER BY id DESC LIMIT 1"
            ), {"gid": contract["gig_id"], "aid": artist_id}).scalar()
            send_booking_emails(db, contract["gig_id"], slot_id=_cs_slot)
        except Exception as e:
            import traceback
            logger.error(f"Booking email error: {e}\n{traceback.format_exc()}")
    
    db.commit()
    
    return {"ok": True, "status": "fully_signed", "pdf_stamp": pdf_stamp_result}


# ============================================
# UPLOAD SIGNED PDF (Tier 1 — artist uploads signed copy)
# ============================================

@router.post("/api/gig-contracts/{contract_id}/upload-signed")
async def upload_signed_pdf(
    contract_id: int,
    file: UploadFile = File(...),
    user=Depends(get_current_user),
    db=Depends(get_db)
):
    """Artist uploads a signed PDF for a pdf_upload contract"""
    contract = db.execute(
        text("SELECT * FROM gig_contracts WHERE id = :cid"),
        {"cid": contract_id}
    ).mappings().first()
    
    if not contract:
        raise HTTPException(404, "Contract not found")
    
    contract = dict(contract)
    check_artist_access(db, contract["artist_id"], user.id)

    # Audit fix (May 2026 part 5): pre-check status BEFORE consuming the
    # upload body. Previously a stale upload on a fully_signed / cancelled
    # contract still read all 20 MB into memory before the 409 — trivial
    # DoS vector against an authenticated endpoint.
    if contract.get("status") not in ("pending", "awaiting_venue_upload"):
        raise HTTPException(409, "Contract is no longer awaiting an artist upload — the venue may have already countersigned.")

    # Validate file (suffix + size + magic bytes)
    content = await _read_and_validate_pdf(file, max_mb=20)

    # Gig date, venue name, artist name for filename: 2026_04_08_14Cannons_FridaysPast.pdf
    # Audit fix (May 2026 part 5): JOIN artist on contract["artist_id"] not
    # g.artist_id — for multi-slot gigs gigs.artist_id is NULL, so the
    # filename would degrade to "...Artist.pdf" instead of the signer's name.
    gig_row = db.execute(
        text("""SELECT g.date, v.venue_name, a.name as artist_name
                FROM gigs g
                LEFT JOIN venues v ON v.id = g.venue_id
                LEFT JOIN artists a ON a.id = :aid
                WHERE g.id = :gid"""),
        {"gid": contract["gig_id"], "aid": contract["artist_id"]}
    ).mappings().first()
    venue_name = (gig_row and gig_row.get("venue_name")) or "Venue"
    artist_name = (gig_row and gig_row.get("artist_name")) or "Artist"
    gig_date = (gig_row and gig_row.get("date")) or contract.get("created_at")
    
    contract_dir = os.path.join(UPLOAD_DIR, f"signed/gig_{contract['gig_id']}")
    os.makedirs(contract_dir, exist_ok=True)
    
    # Audit fix (May 2026 part 7): unlink the prior signed PDF on re-upload.
    # Without this, the suffix-incrementing logic below produces _02, _03 …
    # filenames and the previous file is orphaned on disk with no DB
    # reference. Look up any existing path on this contract row and remove
    # it before writing the new one.
    _prior_pdf = contract.get("signed_pdf_path")
    if _prior_pdf:
        try:
            _prior_abs = _prior_pdf.lstrip("/")
            if os.path.isfile(_prior_abs):
                os.unlink(_prior_abs)
        except Exception as _pre_ue:
            logger.warning(f"upload_signed_pdf: failed to remove prior PDF {_prior_pdf}: {_pre_ue}")

    base_name = _contract_display_filename(venue_name, artist_name, gig_date, suffix="")
    filename = base_name
    suffix_num = 2
    while os.path.exists(os.path.join(contract_dir, filename)):
        filename = _contract_display_filename(venue_name, artist_name, gig_date, suffix=f"_{suffix_num:02d}")
        suffix_num += 1
    file_path = os.path.join(contract_dir, filename)

    # Audit fix (May 2026 part 5): pre-check status BEFORE writing the file to
    # disk. Previously a stale upload (status already 'fully_signed' or
    # 'cancelled') would still write a PDF that nothing references, leaving
    # orphan files on disk forever. Pre-checking is best-effort; the atomic
    # rowcount guard below is still authoritative against races.
    _pre = db.execute(
        text("SELECT status FROM gig_contracts WHERE id = :cid"),
        {"cid": contract_id}
    ).first()
    if _pre and _pre[0] not in ("pending", "awaiting_venue_upload"):
        raise HTTPException(409, "Contract is no longer awaiting an artist upload — the venue may have already countersigned.")

    with open(file_path, "wb") as f:
        f.write(content)

    web_path = f"/{file_path}"
    # Audit fix (May 2026 part 3): only flip to artist_signed when the
    # contract is still waiting for that signature. Previously this
    # blindly overwrote status, so an artist re-uploading after the
    # venue countersigned (status='fully_signed') would regress the
    # contract back to artist_signed and blank the venue side from the
    # UI. Now we constrain to ('pending', 'awaiting_venue_upload') and
    # reject otherwise.
    _up_res = db.execute(
        text("""
            UPDATE gig_contracts SET
                signed_pdf_path = :path,
                status = 'artist_signed',
                artist_signature_name = :sig_name,
                artist_signature_date = CURRENT_TIMESTAMP
            WHERE id = :cid AND status IN ('pending', 'awaiting_venue_upload')
        """),
        {"path": web_path, "cid": contract_id, "sig_name": artist_name}
    )
    if _up_res.rowcount == 0:
        # Status was something else (fully_signed, expired, cancelled).
        # Don't regress it — surface the conflict.
        # Audit fix (May 2026 part 5): race-lost path — the pre-check passed
        # but another writer (e.g. venue countersign) advanced the status
        # between then and the UPDATE. Delete the orphan PDF we just wrote.
        try:
            os.unlink(file_path)
        except Exception as _ue:
            logger.warning(f"upload_signed_pdf: failed to remove orphan file {file_path}: {_ue}")
        raise HTTPException(409, "Contract is no longer awaiting an artist upload — the venue may have already countersigned.")
    
    # Transition gig — handle both single-slot (status=pending_contract) 
    # and multi-slot (status=open with contract_hold set)
    gig = db.execute(
        text("SELECT id, status, venue_id, artist_id, pay, date FROM gigs WHERE id = :gid"),
        {"gid": contract["gig_id"]}
    ).mappings().first()
    _is_pending = gig and gig["status"] in ("pending_contract", "open")
    if _is_pending:
        # Audit fix (May 2026 part 6): set a 48h countersign deadline rather than
        # NULL so the expiration sweep can release this slot if the venue ghosts.
        # Previously the NULL meant `_cleanup_expired_holds_impl` could never
        # reap it (the WHERE clause requires NOT NULL), permanently locking the
        # artist in pending_contract.
        from datetime import timedelta as _td_upload
        _upload_deadline = (utcnow_naive() + _td_upload(hours=48)).isoformat()
        db.execute(
            text("""
                UPDATE gigs SET contract_hold_artist_id = :aid,
                                contract_hold_expires_at = :exp
                WHERE id = :gid
            """),
            {"gid": contract["gig_id"], "aid": contract["artist_id"], "exp": _upload_deadline}
        )
        # Do NOT create payment - that happens at venue countersign
        # Delete old contract_pending notifications for BOTH parties
        db.execute(
            text("DELETE FROM notifications WHERE gig_id = :gid AND notification_type IN ('contract_pending', 'contract_sign_needed')"),
            {"gid": contract["gig_id"]}
        )
        # Notify venue to countersign + artist that upload succeeded
        try:
            venue_data = db.execute(text("SELECT venue_name, user_id FROM venues WHERE id = :vid"), {"vid": gig["venue_id"]}).mappings().first()
            artist_data = db.execute(text("SELECT name, user_id FROM artists WHERE id = :aid"), {"aid": contract["artist_id"]}).mappings().first()
            gig_data = db.execute(text("SELECT date, title FROM gigs WHERE id = :gid"), {"gid": contract["gig_id"]}).mappings().first()
            if venue_data and artist_data:
                venue_name = venue_data["venue_name"]
                artist_name = artist_data["name"]
                gig_label = gig_data["title"] or str(gig_data["date"])[:10] if gig_data else str(contract["gig_id"])
                db.execute(
                    text("""INSERT INTO notifications (user_id, notification_type, title, message, gig_id, venue_id, artist_id, is_read, created_at)
                        VALUES (:uid, 'contract_countersign_needed', :title, :msg, :gid, :vid, :aid, 0, CURRENT_TIMESTAMP)"""),
                    {"uid": venue_data["user_id"], "title": "Contract Uploaded — Countersign Needed",
                     "msg": f"{artist_name} has signed and uploaded the contract for {gig_label}. Please countersign within 48 hours.",
                     "gid": contract["gig_id"], "vid": gig["venue_id"], "aid": contract["artist_id"]}
                )
                if artist_data.get("user_id"):
                    db.execute(
                        text("""INSERT INTO notifications (user_id, notification_type, title, message, gig_id, venue_id, artist_id, is_read, created_at)
                            VALUES (:uid, 'contract_artist_signed', :title, :msg, :gid, :vid, :aid, 0, CURRENT_TIMESTAMP)"""),
                        {"uid": artist_data["user_id"], "title": "Venue Signing — 48 Hours to Sign",
                         "msg": f"You uploaded the contract for {gig_label} at {venue_name}. Venue has 48 hours to countersign.",
                         "gid": contract["gig_id"], "vid": gig["venue_id"], "aid": contract["artist_id"]}
                    )
                # Send email to venue prompting countersign
                try:
                    from backend.services.email_dispatch import send_contract_sign_email
                    from sqlalchemy import text as _t2
                    # Clear idempotency so venue gets notified fresh when artist uploads signed PDF
                    _key = f"contract_sign_needed_{contract['artist_id']}"
                    db.execute(_t2("DELETE FROM gig_email_log WHERE gig_id=:gid AND notification_key=:key"),
                               {"gid": contract["gig_id"], "key": _key})
                    db.commit()
                    gig_date_str = str(gig_data["date"])[:10] if gig_data else ""
                    send_contract_sign_email(db, gig["venue_id"], contract["artist_id"], contract["gig_id"], gig_date_str)
                except Exception as _ce:
                    logger.warning(f"send_contract_sign_email failed: {_ce}")
        except Exception:
            pass
    db.commit()
    
    return {"ok": True, "signed_pdf_path": web_path, "status": "artist_signed"}


# ============================================
# LIST GIG CONTRACTS FOR VENUE/ARTIST
# ============================================

@router.get("/api/venues/{venue_id}/gig-contracts")
def list_venue_gig_contracts(venue_id: int, user=Depends(get_current_user), db=Depends(get_db)):
    """List all gig contract instances for a venue"""
    check_venue_access(db, venue_id, user.id)
    
    rows = db.execute(
        text("""
            SELECT gc.*, a.name as artist_name, g.date as gig_date, g.title as gig_title,
                   vc.name as template_name, v.venue_name
            FROM gig_contracts gc
            LEFT JOIN artists a ON gc.artist_id = a.id
            LEFT JOIN gigs g ON gc.gig_id = g.id
            LEFT JOIN venues v ON gc.venue_id = v.id
            LEFT JOIN venue_contracts vc ON gc.venue_contract_id = vc.id
            WHERE gc.venue_id = :vid
              AND gc.status IN ('pending','artist_signed','awaiting_venue_upload','fully_signed')
            ORDER BY gc.created_at DESC
        """),
        {"vid": venue_id}
    ).mappings().all()

    return [dict(r) for r in rows]


@router.get("/api/artists/{artist_id}/gig-contracts")
def list_artist_gig_contracts(artist_id: int, user=Depends(get_current_user), db=Depends(get_db)):
    """List all gig contract instances for an artist"""
    check_artist_access(db, artist_id, user.id)

    # Audit fix (May 2026 part 5): widen status filter beyond 'fully_signed'
    # so artists/venues see in-flight contracts (pending sign, awaiting upload,
    # artist-signed, awaiting countersign). Previously the page only ever
    # showed completed contracts — the artist couldn't find the "Upload Signed
    # PDF" link or chase a venue's countersign from this view.
    rows = db.execute(
        text("""
            SELECT gc.*, v.venue_name, g.date as gig_date, g.title as gig_title,
                   vc.name as template_name, a.name as artist_name
            FROM gig_contracts gc
            LEFT JOIN venues v ON gc.venue_id = v.id
            LEFT JOIN gigs g ON gc.gig_id = g.id
            LEFT JOIN artists a ON gc.artist_id = a.id
            LEFT JOIN venue_contracts vc ON gc.venue_contract_id = vc.id
            WHERE gc.artist_id = :aid
              AND gc.status IN ('pending','artist_signed','awaiting_venue_upload','fully_signed')
            ORDER BY gc.created_at DESC
        """),
        {"aid": artist_id}
    ).mappings().all()
    
    return [dict(r) for r in rows]


# ============================================
# AUTO-GENERATE PREVIEW (for venue setup)
# ============================================

@router.get("/api/venues/{venue_id}/contracts/auto-preview")
def preview_auto_contract(venue_id: int, user=Depends(get_current_user), db=Depends(get_db)):
    """Generate the full auto-contract text using real venue data + {{placeholder}} tags for artist/gig data"""
    check_venue_access(db, venue_id, user.id)
    
    # Get venue data
    venue = db.execute(
        text("SELECT * FROM venues WHERE id = :vid"),
        {"vid": venue_id}
    ).mappings().first()
    
    if not venue:
        raise HTTPException(404, "Venue not found")
    
    venue = dict(venue)
    owner = db.execute(
        text("SELECT first_name||' '||last_name as name, email, phone FROM users WHERE id = :uid"),
        {"uid": venue["user_id"]}
    ).mappings().first()
    
    venue_name = venue.get("venue_name", "Your Venue")
    venue_address = ", ".join(filter(None, [
        venue.get("address_line_1", ""),
        venue.get("address_line_2", ""),
        venue.get("city", ""),
        venue.get("state", ""),
        venue.get("postal_code", "")
    ]))
    owner_name = owner["name"] if owner else ""
    owner_email = owner["email"] if owner else ""
    owner_phone = owner["phone"] if owner else ""
    
    # Build the FULL contract with {{placeholders}} for artist/gig data
    sections = []
    
    # Header
    sections.append(f"""PERFORMANCE AGREEMENT
Between {venue_name} and {{{{artist_name}}}}
---

""")
    
    # Section 1: Parties
    sections.append(f"""1. PARTIES

This Performance Agreement ("Agreement") is entered into as of the date of last signature below, by and between:

Venue: {venue_name}
Address: {venue_address or '(Venue address not on file)'}
Contact: {owner_name or '(Not on file)'}
Email: {owner_email or '(Not on file)'}
Phone: {owner_phone or '(Not on file)'}

Performer: {{{{artist_name}}}}
Location: {{{{artist_city}}}}, {{{{artist_state}}}}
Contact: {{{{artist_contact_name}}}}
Email: {{{{artist_email}}}}
Phone: {{{{artist_phone}}}}

""")
    
    # Section 2: Performance Details
    sections.append("""2. PERFORMANCE DETAILS

Event: {{gig_title}}
Date: {{gig_date}}
Performance Time: {{gig_start_time}} to {{gig_end_time}}
Compensation: {{gig_pay}}

""")
    
    # Section 3: Venue Amenities & Setup
    amenities_lines = []
    
    if venue.get("has_stage"):
        stage_info = "- Stage provided"
        dims = []
        if venue.get("stage_width_ft"):
            dims.append(f"{venue['stage_width_ft']}ft wide")
        if venue.get("stage_depth_ft"):
            dims.append(f"{venue['stage_depth_ft']}ft deep")
        if dims:
            stage_info += f" ({', '.join(dims)})"
        amenities_lines.append(stage_info)
    else:
        if venue.get("setup_location_description"):
            amenities_lines.append(f"- Setup Location: {venue['setup_location_description']}")
    
    if venue.get("has_sound_equipment"):
        sound_info = "- Sound equipment provided"
        if venue.get("sound_equipment_description"):
            sound_info += f" — {venue['sound_equipment_description']}"
        amenities_lines.append(sound_info)
    else:
        amenities_lines.append("- Performer shall provide their own sound equipment")
    
    if venue.get("has_sound_engineer"):
        eng_info = "- Sound engineer provided"
        if venue.get("sound_engineer_details"):
            eng_info += f" — {venue['sound_engineer_details']}"
        amenities_lines.append(eng_info)
    
    if venue.get("has_lighting"):
        light_info = "- Lighting provided"
        if venue.get("lighting_description"):
            light_info += f" — {venue['lighting_description']}"
        amenities_lines.append(light_info)
    
    amenities_text = "\n".join(amenities_lines) if amenities_lines else "- Contact venue for details"
    sections.append(f"""3. VENUE AMENITIES & SETUP

{amenities_text}

""")
    
    # Section 4: Arrival & Load In/Out
    arrival_lines = []
    if venue.get("arrival_time_type") == "no_earlier_than" and venue.get("arrival_no_earlier_than_hour"):
        period = venue.get("arrival_no_earlier_than_period", "PM")
        arrival_lines.append(f"Performer shall arrive no earlier than {venue['arrival_no_earlier_than_hour']}:00 {period}.")
    elif venue.get("arrival_time_type") == "flexible":
        arrival_lines.append("Arrival time is flexible — coordinate with venue in advance.")
    else:
        arrival_lines.append("Performer shall coordinate arrival time with the Venue in advance of the performance date.")
    
    if venue.get("load_in_out_details"):
        arrival_lines.append(f"Load In/Out: {venue['load_in_out_details']}")
    else:
        arrival_lines.append("Performer shall coordinate load-in and load-out times with the Venue.")
    
    sections.append(f"""4. ARRIVAL & LOAD IN/OUT

{chr(10).join(arrival_lines)}

""")
    
    # Section 5: Compensation & Payment
    payment_lines = [
        "Venue agrees to pay Performer {{gig_pay}} for the performance described in Section 2.",
        "Payment shall be made on the date of performance unless otherwise agreed upon in writing by both parties."
    ]
    if venue.get("bar_tab_details"):
        payment_lines.append(f"Bar Tab: {venue['bar_tab_details']}")
    if venue.get("food_tab_details"):
        payment_lines.append(f"Food Tab: {venue['food_tab_details']}")
    
    sections.append(f"""5. COMPENSATION & PAYMENT

{chr(10).join(payment_lines)}

""")
    
    # Section 6: Performance Obligations
    sections.append("""6. PERFORMANCE OBLIGATIONS

Performer agrees to:
- Arrive at the agreed-upon time and be ready to perform at the scheduled start time
- Perform for the full duration specified in Section 2
- Conduct themselves in a professional manner throughout the engagement
- Comply with all venue rules, noise ordinances, and applicable laws

Venue agrees to:
- Provide the amenities and setup described in Section 3
- Ensure a safe performance environment
- Make payment as described in Section 5
- Provide reasonable access for load-in and load-out

""")
    
    # Section 7: Cancellation
    sections.append("""7. CANCELLATION

Either party may cancel this Agreement by providing written notice (including email or message through the GigsFill platform) to the other party.

If the Venue cancels with less than 48 hours notice, Venue shall pay Performer 50% of the agreed compensation unless otherwise negotiated.

If the Performer cancels with less than 48 hours notice, Performer may be subject to removal from the Venue's preferred artist list.

""")
    
    # Section 8: Force Majeure
    sections.append("""8. FORCE MAJEURE

Neither party shall be liable for failure to perform due to circumstances beyond their reasonable control, including but not limited to: acts of God, severe weather, natural disasters, government orders, pandemics, power outages, or other events that make the performance impossible or impractical. In such cases, both parties shall make reasonable efforts to reschedule.

""")
    
    # Section 9: Liability & Indemnification
    sections.append("""9. LIABILITY & INDEMNIFICATION

Each party shall be responsible for their own negligent acts or omissions. Performer shall be responsible for any damage to Venue property caused by Performer or Performer's crew. Venue shall maintain adequate liability insurance for events held at its premises. Neither party shall be liable for indirect, incidental, or consequential damages.

""")
    
    # Section 10: Independent Contractor
    sections.append("""10. INDEPENDENT CONTRACTOR

Performer is an independent contractor and not an employee, agent, or partner of the Venue. Performer is solely responsible for all applicable taxes, including self-employment taxes, arising from compensation received under this Agreement.

""")
    
    # Section 11: Entire Agreement
    sections.append("""11. ENTIRE AGREEMENT

This Agreement constitutes the entire understanding between the parties and supersedes all prior negotiations, representations, or agreements relating to this subject matter. This Agreement may only be modified by written consent of both parties.

""")
    
    # Section 12: Signatures
    sections.append("""12. SIGNATURES

By signing below, both parties acknowledge they have read, understood, and agree to the terms of this Agreement.

Venue Representative:
Name: ____________________________
Title: ____________________________
Date: ____________________________

Performer:
Name: ____________________________
Title: ____________________________
Date: ____________________________

""")
    
    # Disclaimer
    sections.append(f"""---
{AUTO_CONTRACT_DISCLAIMER}""")
    
    contract_text = "\n".join(sections)
    
    return {"contract_text": contract_text}


# ============================================
# BOOK WITH CONTRACT — unified booking + contract flow
# ============================================


def _apply_slot_booking(db, gig_id: int, slot_id, artist_id: int, hold_expires, text_fn):
    """
    Unified slot/gig status update for ALL contract types.
    Rule: slot → pending_contract, gig.status stays open (other slots bookable),
    contract_hold set on parent gig, blast token cleared.

    Audit fix (May 2026): atomic-claim guard with `WHERE ... status='open'`.
    Without it, two artists hitting `book_with_contract` simultaneously can
    both pass the prior status check and both UPDATE — last write wins, but
    the loser is already mid-contract-creation. Caller checks rowcount and
    raises 409 SLOT_TAKEN. Mirrors the pattern shipped for book_gig/book_slot.
    """
    if slot_id:
        result = db.execute(
            text_fn("""UPDATE gig_slots
                       SET artist_id = :aid, status = 'pending_contract'
                       WHERE id = :sid AND status = 'open'"""),
            {"aid": artist_id, "sid": int(slot_id)}
        )
        if (result.rowcount or 0) == 0:
            raise HTTPException(409, "SLOT_TAKEN: This slot was just booked by someone else. Please refresh and try a different slot.")
        db.execute(text_fn("""UPDATE gigs SET
                contract_hold_artist_id = :aid,
                contract_hold_expires_at = :exp,
                radius_blast_token = NULL
            WHERE id = :gid"""),
            {"aid": artist_id, "gid": gig_id, "exp": hold_expires.isoformat()})
    else:
        # Single-slot gig: update parent gig status directly. Same guard.
        result = db.execute(
            text_fn("""UPDATE gigs SET
                    artist_id = :aid, status = 'pending_contract',
                    contract_hold_artist_id = :aid,
                    contract_hold_expires_at = :exp,
                    radius_blast_token = NULL
                WHERE id = :gid AND status = 'open'"""),
            {"aid": artist_id, "gid": gig_id, "exp": hold_expires.isoformat()}
        )
        if (result.rowcount or 0) == 0:
            raise HTTPException(409, "SLOT_TAKEN: This gig was just booked by someone else. Please refresh.")


@router.post("/api/gigs/{gig_id}/book-with-contract")
def book_with_contract(gig_id: int, data: dict, request: Request, user=Depends(get_current_user), db=Depends(get_db)):
    """
    Unified booking endpoint that handles contract signing flow.
    For digital contracts: books gig + creates contract + signs it atomically.
    For PDF contracts: books gig as pending_contract with hold.
    For per-gig PDF: books gig as awaiting_venue_contract with hold.
    """
    from datetime import timedelta
    
    artist_id = data.get("artist_id") or request.query_params.get("artist_id")
    if not artist_id:
        raise HTTPException(400, "artist_id required")
    artist_id = int(artist_id)
    
    # Verify artist access
    artist = db.execute(
        text("""
            SELECT a.id, a.name FROM artists a
            WHERE a.id = :aid AND (a.user_id = :uid
                OR EXISTS (SELECT 1 FROM entity_users eu WHERE eu.entity_type='artist' AND eu.entity_id=a.id AND eu.user_id=:uid))
        """),
        {"aid": artist_id, "uid": user.id}
    ).mappings().first()
    if not artist:
        raise HTTPException(403, "Artist does not belong to you")
    
    # Load gig
    gig = db.execute(
        text("SELECT id, venue_id, status, date, title, start_time, end_time, pay FROM gigs WHERE id = :gid"),
        {"gid": gig_id}
    ).mappings().first()
    if not gig:
        raise HTTPException(404, "Gig not found")
    
    # Handle slot booking
    slot_id = data.get("slot_id")
    if slot_id:
        slot = db.execute(
            text("SELECT id, status, start_time, end_time FROM gig_slots WHERE id = :sid AND gig_id = :gid"),
            {"sid": int(slot_id), "gid": gig_id}
        ).mappings().first()
        if not slot:
            raise HTTPException(404, "Slot not found")
        if slot["status"] != "open":
            raise HTTPException(403, "Slot is not open for booking")
    elif gig["status"] != "open":
        raise HTTPException(403, "Gig is not open for booking")

    venue_id = gig["venue_id"]

    # Audit fix (May 2026 part 5): mirror book_slot's one-slot-per-artist guard.
    # Without this, an artist with entity_user access could book multiple slots
    # on the same multi-slot gig via the contract path. The booking would land
    # twice but _create_booking_transaction silently refuses the second
    # artist_payout child, leaving a slot booked with no payout row → fee imbalance.
    if slot_id:
        _existing_slot_bwc = db.execute(
            text("""SELECT id FROM gig_slots
                    WHERE gig_id = :gid AND artist_id = :aid AND id != :sid
                      AND status IN ('booked','pending_venue_approval','pending_contract','awaiting_venue_contract')"""),
            {"gid": gig_id, "aid": artist_id, "sid": int(slot_id)}
        ).first()
        if _existing_slot_bwc:
            raise HTTPException(403, "You already have a slot booked or pending on this gig. Each artist can only hold one slot per event.")

    # ── Pre-booking checks — shared with book_gig and book_slot ─────────────
    _blast_token = request.query_params.get("blast_token") or data.get("blast_token") or ""
    from backend.routes.gigs import _run_prebooking_checks, _is_same_day_booking, _ensure_approval_columns, _enforce_no_artist_time_overlap, _check_artist_matches_gig
    _check_result = _run_prebooking_checks(db, gig_id, artist_id, venue_id, str(gig.get("date", "")), _blast_token)
    # Slot-aware match gate (multi-slot gigs can override per slot).
    # _run_prebooking_checks already ran the gig-level version; this
    # call wins when slot_id is supplied with its own artist_type /
    # band_formats. Idempotent — passing slot_id=None matches the
    # earlier gig-level check exactly so no double error.
    if slot_id:
        _check_artist_matches_gig(db, dict(gig), artist_id, slot_id=slot_id)
    # Hard-block on time overlap with the artist's other commitments.
    # Use slot times if booking a slot; gig umbrella times for single-slot.
    if slot_id and slot:
        _enforce_no_artist_time_overlap(db, artist_id, gig_id, venue_id,
                                        gig.get("date"), slot["start_time"], slot["end_time"])
    else:
        _enforce_no_artist_time_overlap(db, artist_id, gig_id, venue_id,
                                        gig.get("date"), gig.get("start_time"), gig.get("end_time"))
    # ─────────────────────────────────────────────────────────────────────────

    # Same-day check: radius artists (non-preferred) need venue approval even with contract.
    # Audit fix (May 2026 part 5): re-read `preferred_artists` directly instead
    # of trusting `_check_result["pref"]`, which `_run_prebooking_checks`
    # reassigns to a synthetic `{"status":"approved"}` for blast tokens.
    # Same bug pattern that book_gig was fixed for earlier this audit.
    _real_pref_bwc = db.execute(
        text("SELECT status FROM preferred_artists WHERE venue_id = :vid AND artist_id = :aid"),
        {"vid": venue_id, "aid": artist_id}
    ).first()
    _is_preferred_bwc = bool(_real_pref_bwc and _real_pref_bwc[0] == "approved")
    _ensure_approval_columns(db)
    if _is_same_day_booking(str(gig.get("date", "")), gig.get("start_time")) and not _is_preferred_bwc:
        # Route to pending_venue_approval — no contract flow for non-preferred same-day.
        # Audit fix (May 2026 part 5): atomic claim guard. Without `AND status='open'`
        # two concurrent same-day requests on the same slot would both pass the
        # Python-side status check and both UPDATE. Rowcount check raises 409
        # SLOT_TAKEN on the second one, matching `_apply_slot_booking`.
        if slot_id:
            _claim = db.execute(
                text("UPDATE gig_slots SET artist_id = :aid, status = 'pending_venue_approval', approval_requested_at = :now "
                     "WHERE id = :sid AND status = 'open'"),
                {"aid": artist_id, "sid": int(slot_id), "now": utcnow_naive().isoformat()}
            )
            if (_claim.rowcount or 0) == 0:
                raise HTTPException(409, "SLOT_TAKEN: Another booking just claimed this slot. Please refresh.")
            # Audit fix (May 2026 part 5): only flip the parent gig to
            # pending_venue_approval when NO other open slots remain. The prior
            # unconditional UPDATE blocked other artists from booking the gig's
            # still-open slots until this one was approved/denied. Mirrors the
            # book_slot logic at gigs.py:3878.
            _other_open_bwc = db.execute(
                text("SELECT COUNT(*) FROM gig_slots WHERE gig_id=:gid AND id!=:sid AND status='open'"),
                {"gid": gig_id, "sid": int(slot_id)}
            ).scalar()
            if not _other_open_bwc:
                db.execute(
                    text("UPDATE gigs SET artist_id = :aid, status = 'pending_venue_approval', approval_requested_at = :now WHERE id = :gid"),
                    {"aid": artist_id, "gid": gig_id, "now": utcnow_naive().isoformat()}
                )
        else:
            db.execute(
                text("UPDATE gigs SET artist_id = :aid, status = 'pending_venue_approval', approval_requested_at = :now WHERE id = :gid"),
                {"aid": artist_id, "gid": gig_id, "now": utcnow_naive().isoformat()}
            )
        try:
            from backend.services.email_dispatch import send_approval_request_emails
            gig_details_a = db.execute(text("""
                SELECT g.id, g.date, g.title, g.start_time, g.end_time, g.pay,
                       g.venue_id, v.venue_name, v.user_id as venue_user_id,
                       a.name as artist_name, a.user_id as artist_user_id
                FROM gigs g JOIN venues v ON g.venue_id=v.id JOIN artists a ON a.id=:aid
                WHERE g.id=:gid"""), {"gid": gig_id, "aid": artist_id}).mappings().first()
            if gig_details_a:
                send_approval_request_emails(db, dict(gig_details_a), artist_id)
        except Exception as _ae:
            logger.warning(f"Same-day approval email error: {_ae}")
        db.commit()
        return {"ok": True, "pending_approval": True}

    # Get active contract template
    template = db.execute(
        text("SELECT * FROM venue_contracts WHERE venue_id = :vid AND is_active = 1 LIMIT 1"),
        {"vid": venue_id}
    ).mappings().first()
    if not template:
        raise HTTPException(400, "No active contract found for this venue")
    template = dict(template)
    
    contract_type = template["contract_type"]
    per_gig_pdf = template.get("per_gig_pdf", 0)
    signature_name = data.get("signature_name", "").strip()
    
    logger.warning(f"book_with_contract: gig={gig_id}, contract_type={contract_type}, per_gig_pdf={per_gig_pdf}, sig_name={bool(signature_name)}")
    
    now = utcnow_naive()
    
    # ---- DIGITAL CONTRACTS (builder / auto_generated) ----
    if contract_type in ("custom_builder", "auto_generated"):
        if not signature_name:
            raise HTTPException(400, "Signature name is required for digital contracts")
        
        # Render the contract body
        rendered_body = ""
        if contract_type == "auto_generated":
            # Pass slot_id so generate_auto_contract picks the right slot's
            # Performance Time / door terms for multi-slot gigs. slot_id
            # comes from the request body (set above on line ~2362).
            rendered_body = generate_auto_contract(db, gig_id, venue_id, artist_id, slot_id=slot_id)
        else:
            rendered_body = template.get("contract_body", "")
            # Fill placeholders
            artist_data = db.execute(
                text("SELECT a.*, u.first_name||' '||u.last_name as full_name, u.email, u.phone FROM artists a JOIN users u ON a.user_id=u.id WHERE a.id=:aid"),
                {"aid": artist_id}
            ).mappings().first()
            venue_data = db.execute(text("SELECT * FROM venues WHERE id = :vid"), {"vid": venue_id}).mappings().first()
            if rendered_body:
                replacements = {
                    "artist_name": artist_data["name"] if artist_data else "",
                    "artist_contact_name": artist_data["full_name"] if artist_data else "",
                    "artist_email": artist_data["email"] if artist_data else "",
                    "artist_phone": artist_data["phone"] if artist_data else "",
                    "artist_city": artist_data["city"] if artist_data else "",
                    "artist_state": artist_data["state"] if artist_data else "",
                    "venue_name": venue_data["venue_name"] if venue_data else "",
                    "venue_address": venue_data["address_line_1"] if venue_data else "",
                    "gig_date": gig["date"] or "",
                    "gig_start_time": format_time_12hr(gig["start_time"]) if gig["start_time"] else "",
                    "gig_end_time": format_time_12hr(gig["end_time"]) if gig["end_time"] else "",
                    "gig_pay": _get_effective_pay_str(db, gig, venue_id, artist_id),
                    "gig_title": gig["title"] or "",
                }
                for key, val in replacements.items():
                    rendered_body = rendered_body.replace(f"{{{{{key}}}}}", str(val or ""))
        
        # Set 48-hour hold for venue to countersign (gig reopens if venue doesn't act)
        hold_expires = now + timedelta(hours=48)
        
        # Book the gig (or slot) — unified logic for all contract types
        _apply_slot_booking(db, gig_id, slot_id, artist_id, hold_expires, text)
        if slot_id:
            _apply_slot_pay_override(db, int(slot_id), gig["venue_id"], artist_id)
        
        # Create gig contract signed
        client_ip = request.client.host if request.client else "unknown"
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            client_ip = forwarded.split(",")[0].strip()
        
        db.execute(
            text("""
                INSERT INTO gig_contracts
                    (gig_id, venue_contract_id, venue_id, artist_id, contract_type,
                     rendered_body, filled_fields, status,
                     artist_signature_name, artist_signature_date, artist_signature_ip, hold_expires_at, created_at)
                VALUES (:gig_id, :vc_id, :vid, :aid, :type,
                        :body, '{}', 'artist_signed',
                        :sig_name, :sig_date, :sig_ip, :exp, CURRENT_TIMESTAMP)
            """),
            {
                "gig_id": gig_id, "vc_id": template["id"], "vid": venue_id, "aid": artist_id,
                "type": contract_type, "body": rendered_body,
                "sig_name": signature_name, "sig_date": now.isoformat() + 'Z', "sig_ip": client_ip,
                "exp": hold_expires.isoformat(),
            }
        )
        contract_id = db.execute(text("SELECT last_insert_rowid()")).scalar()
        # Audit fix (May 2026 part 7): align slot status with contract status.
        # `_apply_slot_booking` set the slot to 'pending_contract', but for the
        # digital path the contract is already 'artist_signed' — the part-5
        # invariant says these should pair. Without this, the calendar bubble
        # and any consumer keyed off slot status mislabels the slot as
        # "awaiting artist signature" when the artist has already signed.
        if slot_id:
            db.execute(
                text("""UPDATE gig_slots SET status = 'awaiting_venue_contract'
                        WHERE id = :sid AND artist_id = :aid AND status = 'pending_contract'"""),
                {"sid": int(slot_id), "aid": artist_id}
            )
        db.commit()

        # Remove artist from waitlist now that they've booked (via contract)
        try:
            from backend.routes.waitlist import cleanup_gig_waitlist as _cwl_bwc
            db.execute(text("DELETE FROM gig_waitlist WHERE gig_id = :gid AND artist_id = :aid"),
                       {"gid": gig_id, "aid": artist_id})
            db.commit()
        except Exception as _wle:
            logger.warning(f"Waitlist cleanup on book-with-contract failed: {_wle}")

        # Do NOT create payment transaction until venue countersigns (fewer undos if venue never signs)

        # Create notifications — for digital contracts, artist signed but venue needs to countersign
        _create_booking_notifications(db, gig_id, venue_id, artist_id, "artist_signed")
        db.commit()  # Commit the notifications
        
        # Email venue only: artist has signed — please countersign to confirm (booked emails sent after countersign)
        try:
            from backend.services.email_dispatch import send_contract_sign_email
            send_contract_sign_email(db, venue_id, artist_id, gig_id, str(gig.get("date", ""))[:10])
        except Exception as e:
            logger.error(f"Venue contract sign email error: {e}", exc_info=True)
        
        return {"ok": True, "status": "booked", "contract_id": contract_id, "contract_status": "artist_signed"}
    
    # ---- PDF CONTRACT (per-gig: venue uploads unique PDF per gig) ----
    elif contract_type == "pdf_upload" and per_gig_pdf:
        hold_expires = now + timedelta(hours=48)
        
        # Book the gig (or slot) — unified logic
        _apply_slot_booking(db, gig_id, slot_id, artist_id, hold_expires, text)
        if slot_id:
            _apply_slot_pay_override(db, int(slot_id), gig["venue_id"], artist_id)
        elif not slot_id:
            # Single-slot per-gig PDF: mark as awaiting venue upload instead
            db.execute(text("UPDATE gigs SET status = 'awaiting_venue_contract' WHERE id = :gid AND status = 'pending_contract'"),
                       {"gid": gig_id})
        
        # Create a placeholder gig_contract (no PDF yet)
        db.execute(
            text("""
                INSERT INTO gig_contracts
                    (gig_id, venue_contract_id, venue_id, artist_id, contract_type,
                     rendered_body, filled_fields, status, hold_expires_at, created_at)
                VALUES (:gig_id, :vc_id, :vid, :aid, 'pdf_upload',
                        '', '{}', 'awaiting_venue_upload', :exp, CURRENT_TIMESTAMP)
            """),
            {
                "gig_id": gig_id, "vc_id": template["id"], "vid": venue_id, "aid": artist_id,
                "exp": hold_expires.isoformat(),
            }
        )
        contract_id = db.execute(text("SELECT last_insert_rowid()")).scalar()
        db.commit()
        
        # Do NOT create payment transaction until venue countersigns
        # Notify venue to upload contract
        _create_booking_notifications(db, gig_id, venue_id, artist_id, "awaiting_venue_contract")
        db.commit()
        
        # Email venue users
        try:
            from backend.services.email_dispatch import send_contract_sign_email
            send_contract_sign_email(db, venue_id, artist_id, gig_id, gig["date"])
        except Exception as e:
            logger.error(f"Per-gig PDF email error: {e}")
        
        return {"ok": True, "status": "awaiting_venue_contract", "contract_id": contract_id, "hold_expires": hold_expires.isoformat()}
    
    # ---- PDF CONTRACT (standard: same template for all gigs) ----
    elif contract_type == "pdf_upload":
        hold_expires = now + timedelta(hours=24)
        
        # Book the gig (or slot) — unified logic for all contract types
        _apply_slot_booking(db, gig_id, slot_id, artist_id, hold_expires, text)
        if slot_id:
            _apply_slot_pay_override(db, int(slot_id), gig["venue_id"], artist_id)
        
        # Create gig contract with PDF reference
        db.execute(
            text("""
                INSERT INTO gig_contracts
                    (gig_id, venue_contract_id, venue_id, artist_id, contract_type,
                     pdf_file_path, rendered_body, filled_fields, status, hold_expires_at, created_at)
                VALUES (:gig_id, :vc_id, :vid, :aid, 'pdf_upload',
                        :pdf, '', '{}', 'pending', :exp, CURRENT_TIMESTAMP)
            """),
            {
                "gig_id": gig_id, "vc_id": template["id"], "vid": venue_id, "aid": artist_id,
                "pdf": template.get("pdf_file_path", ""), "exp": hold_expires.isoformat(),
            }
        )
        contract_id = db.execute(text("SELECT last_insert_rowid()")).scalar()
        db.commit()
        
        # Do NOT create payment transaction until venue countersigns
        _create_booking_notifications(db, gig_id, venue_id, artist_id, "pending_contract")
        db.commit()
        # Email sent after artist uploads signed PDF (in upload_signed_pdf endpoint)
        # to avoid sending before venue has anything to countersign
        
        return {
            "ok": True, "status": "pending_contract", "contract_id": contract_id,
            "pdf_url": template.get("pdf_file_path", ""),
            "hold_expires": hold_expires.isoformat(),
        }
    
    raise HTTPException(400, "Unsupported contract type")


def _create_booking_notifications(db, gig_id, venue_id, artist_id, status):
    """Create notifications for both parties during contract booking flow.

    Audit fix (May 2026 part 7): fan out to ALL entity_users (owner + team
    members) on both sides for EVERY status — not just 'booked'. Previously
    only the primary venues.user_id / artists.user_id received the
    pending_contract / awaiting_venue_contract / artist_signed / fully_signed
    notifications, so multi-user accounts missed every step of the contract
    flow including the final "Booking Confirmed".
    """
    try:
        gig_data = db.execute(text("SELECT date, title FROM gigs WHERE id = :gid"), {"gid": gig_id}).mappings().first()
        venue_data = db.execute(text("SELECT venue_name, user_id FROM venues WHERE id = :vid"), {"vid": venue_id}).mappings().first()
        artist_data = db.execute(text("SELECT name, user_id FROM artists WHERE id = :aid"), {"aid": artist_id}).mappings().first()

        gig_label = gig_data["title"] or gig_data["date"] if gig_data else str(gig_id)
        venue_name = venue_data["venue_name"] if venue_data else "Venue"
        artist_name = artist_data["name"] if artist_data else "Artist"

        # Slot suffix — Activity Center enriches the message with the
        # slot's actual start_time when the body contains "Slot N" (see
        # backend/routes/notifications.py:86). Without this, multi-slot
        # gigs fall back to the parent gig's start_time and every slot
        # notification reads as the umbrella start. For a 2-slot gig
        # (7-9 + 9-11) Fifty Proof's notification said 7pm AND Fridays
        # Past's said 7pm too — Fridays Past's should say 9pm.
        _slot_rows = db.execute(
            text("""SELECT slot_number FROM gig_slots
                    WHERE gig_id = :gid AND artist_id = :aid
                      AND status IN ('booked','pending_contract','awaiting_venue_contract','pending_venue_approval')
                    ORDER BY slot_number"""),
            {"gid": gig_id, "aid": artist_id}
        ).fetchall()
        if len(_slot_rows) == 1:
            slot_suffix = f" Slot {_slot_rows[0][0]}"
        elif len(_slot_rows) > 1:
            slot_suffix = f" Slots {', '.join(str(r[0]) for r in _slot_rows)}"
        else:
            slot_suffix = ""

        # Audit fix (May 2026 part 7): compute entity-user lists once for fan-out.
        from backend.utils import get_all_entity_users as _gaeu
        try:
            _venue_uids = [u["user_id"] for u in _gaeu(db, "venue", venue_id) if u.get("user_id")]
        except Exception:
            _venue_uids = [venue_data["user_id"]] if venue_data else []
        try:
            _artist_uids = [u["user_id"] for u in _gaeu(db, "artist", artist_id) if u.get("user_id")]
        except Exception:
            _artist_uids = [artist_data["user_id"]] if artist_data else []
        # Dedup overlap (user owns BOTH artist AND venue) so they get one row.
        _shared = set(_venue_uids) & set(_artist_uids)
        def _ins_venue(ntype, title, message):
            for uid in _venue_uids:
                db.execute(text("""INSERT INTO notifications (user_id, notification_type, title, message, gig_id, venue_id, artist_id, is_read, created_at)
                    VALUES (:uid, :ntype, :t, :m, :gid, :vid, :aid, 0, CURRENT_TIMESTAMP)"""),
                    {"uid": uid, "ntype": ntype, "t": title, "m": message, "gid": gig_id, "vid": venue_id, "aid": artist_id})
        def _ins_artist(ntype, title, message):
            for uid in _artist_uids:
                if uid in _shared:
                    continue  # already inserted on the venue side
                db.execute(text("""INSERT INTO notifications (user_id, notification_type, title, message, gig_id, venue_id, artist_id, is_read, created_at)
                    VALUES (:uid, :ntype, :t, :m, :gid, :vid, :aid, 0, CURRENT_TIMESTAMP)"""),
                    {"uid": uid, "ntype": ntype, "t": title, "m": message, "gid": gig_id, "vid": venue_id, "aid": artist_id})
        
        if status == "booked":
            _ins_venue('gig_booked', "Gig Booked & Contract Signed",
                       f"{artist_name} has booked and signed the contract for {gig_label}{slot_suffix}.")
            _ins_artist('gig_booked', "Gig Booked!",
                        f"You've booked {gig_label}{slot_suffix} at {venue_name}. Contract signed.")

        elif status == "pending_contract":
            _ins_venue('contract_pending', "Gig Held — Awaiting Contract Signature",
                       f"{artist_name} is booking {gig_label}{slot_suffix}. Awaiting their signed contract (24hr hold).")
            _ins_artist('contract_pending', "Contract Required — 24 Hours to Sign",
                        f"Download, sign, and upload the contract for {gig_label}{slot_suffix} at {venue_name} within 24 hours to confirm your booking.")

        elif status == "awaiting_venue_contract":
            _ins_venue('contract_upload_needed', "Contract Upload Needed — 48 Hours",
                       f"{artist_name} wants to book {gig_label}{slot_suffix}. Upload a contract within 48 hours.")
            _ins_artist('contract_awaiting_venue', "Awaiting Contract From Venue",
                        f"Your booking for {gig_label}{slot_suffix} at {venue_name} is being held. The venue will upload a contract within 48 hours.")
        
        elif status == "artist_signed":
            # Get contract_id for the countersign link
            gc = db.execute(text("SELECT id FROM gig_contracts WHERE gig_id = :gid AND artist_id = :aid ORDER BY id DESC LIMIT 1"),
                {"gid": gig_id, "aid": artist_id}).first()
            contract_id = gc[0] if gc else None
            # Venue: "Artist signed — countersign needed"; message uses "a gig on {date}"
            gig_date_fmt = ""
            if gig_data and gig_data.get("date"):
                try:
                    from datetime import datetime
                    d = datetime.strptime(gig_data["date"][:10], "%Y-%m-%d")
                    gig_date_fmt = f"{d.month}/{d.day}/{d.year}"
                except Exception:
                    gig_date_fmt = gig_data["date"][:10] if gig_data.get("date") else ""
            msg_venue = f"{artist_name} has signed the contract for a gig{slot_suffix} on {gig_date_fmt}." if gig_date_fmt else f"{artist_name} has signed the contract for a gig{slot_suffix}."
            _ins_venue('contract_countersign_needed', "Contract Signed", msg_venue)
            # Artist: "X has signed the contract for a gig at {venue} on {date}." (second line added in activity center)
            gig_date_fmt_artist = ""
            if gig_data and gig_data.get("date"):
                try:
                    d = datetime.strptime(gig_data["date"][:10], "%Y-%m-%d")
                    gig_date_fmt_artist = f"{d.month}/{d.day}/{d.year}"
                except Exception:
                    gig_date_fmt_artist = gig_data["date"][:10] if gig_data.get("date") else ""
            msg_artist = f"{artist_name} has signed the contract for a gig{slot_suffix} at {venue_name} on {gig_date_fmt_artist}." if gig_date_fmt_artist else f"{artist_name} has signed the contract for a gig{slot_suffix} at {venue_name}."
            _ins_artist('contract_artist_signed', "Contract Signed — Awaiting Venue", msg_artist)

        elif status == "fully_signed":
            _ins_venue('gig_booked', "Contract Signed — Booking Confirmed",
                       f"The contract for {gig_label}{slot_suffix} with {artist_name} is fully signed. Booking confirmed!")
            _ins_artist('gig_booked', "Gig Booked — Contract Confirmed!",
                        f"Your gig {gig_label}{slot_suffix} at {venue_name} is confirmed! The venue has countersigned the contract.")
        
        db.commit()
    except Exception:
        pass


# ============================================
# CONTRACT PREVIEW — renders contract with filled placeholders for signing modal
# ============================================

@router.get("/api/gigs/{gig_id}/contract-preview")
def get_contract_preview(gig_id: int, request: Request, user=Depends(get_current_user), db=Depends(get_db)):
    """Get rendered contract text for the signing modal before booking.

    Accepts an optional `slot_id` query param so multi-slot gigs render
    the SPECIFIC slot's Performance Time + door terms in the preview.
    Without it, the slot's artist_id isn't set yet (booking hasn't
    happened) and the by-artist fallback in generate_auto_contract
    returns the gig umbrella (e.g. 7-11 PM for a 7-9 + 9-11 gig).
    """
    artist_id = request.query_params.get("artist_id")
    if not artist_id:
        raise HTTPException(400, "artist_id required")
    artist_id = int(artist_id)
    _slot_id_q = request.query_params.get("slot_id")
    try:
        slot_id = int(_slot_id_q) if _slot_id_q else None
    except ValueError:
        slot_id = None

    gig = db.execute(text("SELECT * FROM gigs WHERE id = :gid"), {"gid": gig_id}).mappings().first()
    if not gig:
        raise HTTPException(404, "Gig not found")

    venue_id = gig["venue_id"]

    # Audit fix (May 2026 part 5): authorize the caller against the venue OR the
    # supplied artist before rendering. Previously any logged-in user could pass
    # any artist_id and receive that artist's email/phone/city/state interpolated
    # into the response — a PII scrape across every artist for any venue's template.
    _allowed = False
    try:
        check_venue_access(db, venue_id, user.id)
        _allowed = True
    except Exception:
        pass
    if not _allowed:
        try:
            check_artist_access(db, artist_id, user.id)
            _allowed = True
        except Exception:
            pass
    if not _allowed:
        if not to_admin_bool(getattr(user, "is_admin", "false")):
            raise HTTPException(403, "Not authorized to preview this contract")
    template = db.execute(
        text("SELECT * FROM venue_contracts WHERE venue_id = :vid AND is_active = 1 LIMIT 1"),
        {"vid": venue_id}
    ).mappings().first()
    if not template:
        return {"has_contract": False}
    template = dict(template)
    
    contract_type = template["contract_type"]
    rendered_body = ""
    
    if contract_type == "auto_generated":
        rendered_body = generate_auto_contract(db, gig_id, venue_id, artist_id, slot_id=slot_id)
    elif contract_type == "custom_builder":
        rendered_body = template.get("contract_body", "")
        # Fill placeholders
        artist_data = db.execute(
            text("SELECT a.*, u.first_name||' '||u.last_name as full_name, u.email, u.phone FROM artists a JOIN users u ON a.user_id=u.id WHERE a.id=:aid"),
            {"aid": artist_id}
        ).mappings().first()
        venue_data = db.execute(text("SELECT * FROM venues WHERE id = :vid"), {"vid": venue_id}).mappings().first()
        # Slot-aware times (custom_builder preview) — use the specific
        # slot when slot_id is supplied so {{gig_start_time}} renders
        # 7:00 PM not the umbrella 7-11 PM.
        _eff_start, _eff_end = gig.get("start_time"), gig.get("end_time")
        if slot_id:
            _sr = db.execute(text("SELECT start_time, end_time FROM gig_slots WHERE id = :sid"), {"sid": slot_id}).mappings().first()
            if _sr:
                _eff_start, _eff_end = _sr.get("start_time") or _eff_start, _sr.get("end_time") or _eff_end
        if rendered_body:
            replacements = {
                "artist_name": artist_data["name"] if artist_data else "",
                "artist_contact_name": artist_data["full_name"] if artist_data else "",
                "artist_email": artist_data["email"] if artist_data else "",
                "artist_phone": artist_data["phone"] if artist_data else "",
                "artist_city": artist_data["city"] if artist_data else "",
                "artist_state": artist_data["state"] if artist_data else "",
                "venue_name": venue_data["venue_name"] if venue_data else "",
                "venue_address": venue_data["address_line_1"] if venue_data else "",
                "gig_date": gig["date"] or "",
                "gig_start_time": format_time_12hr(_eff_start) if _eff_start else "",
                "gig_end_time": format_time_12hr(_eff_end) if _eff_end else "",
                "gig_pay": _get_effective_pay_str(db, gig, venue_id, artist_id, slot_id=slot_id),
                "gig_title": gig["title"] or "",
            }
            for key, val in replacements.items():
                rendered_body = rendered_body.replace(f"{{{{{key}}}}}", str(val or ""))
    elif contract_type == "pdf_upload":
        return {
            "has_contract": True,
            "contract_type": "pdf_upload",
            "pdf_url": template.get("pdf_file_path", ""),
            "per_gig_pdf": template.get("per_gig_pdf", 0),
            "require_for_booking": template.get("require_for_booking", 0),
            "name": template.get("name", ""),
        }
    
    return {
        "has_contract": True,
        "contract_type": contract_type,
        "rendered_body": rendered_body,
        "require_for_booking": template.get("require_for_booking", 0),
        "name": template.get("name", ""),
    }


# ============================================
# VENUE UPLOADS PER-GIG PDF
# ============================================

@router.post("/api/venues/{venue_id}/gigs/{gig_id}/upload-gig-pdf")
async def upload_gig_pdf(
    venue_id: int,
    gig_id: int,
    file: UploadFile = File(...),
    user=Depends(get_current_user),
    db=Depends(get_db)
):
    """Venue uploads a gig-specific PDF contract. Transitions gig from awaiting_venue_contract to pending_contract."""
    from datetime import timedelta
    
    check_venue_access(db, venue_id, user.id)

    gig = db.execute(
        text("SELECT id, status, artist_id, COALESCE(is_multi_slot, 0) as is_multi_slot FROM gigs WHERE id = :gid AND venue_id = :vid"),
        {"gid": gig_id, "vid": venue_id}
    ).mappings().first()
    if not gig:
        raise HTTPException(404, "Gig not found")
    if gig["status"] != "awaiting_venue_contract":
        raise HTTPException(400, "Gig is not awaiting a venue contract upload")
    # Audit fix (May 2026 part 2): refuse multi-slot gigs at this single-
    # slot-shaped endpoint. The blind `UPDATE gigs SET status = 'pending_contract'`
    # below would corrupt the parent and hide the other slots from artist
    # search. Multi-slot per-gig-PDF uploads need to be slot-scoped (a
    # separate endpoint or an explicit `slot_id` query arg).
    if gig["is_multi_slot"]:
        raise HTTPException(
            400,
            "This endpoint is single-slot only. Multi-slot per-gig PDF uploads "
            "must specify a slot_id (not yet implemented)."
        )
    
    # Validate file (suffix + size + magic bytes)
    content = await _read_and_validate_pdf(file, max_mb=20)
    
    # Save file
    contract_dir = os.path.join(UPLOAD_DIR, f"gig_contracts/venue_{venue_id}")
    os.makedirs(contract_dir, exist_ok=True)
    filename = f"gig_{gig_id}_{uuid.uuid4().hex[:8]}.pdf"
    file_path = os.path.join(contract_dir, filename)
    with open(file_path, "wb") as f:
        f.write(content)
    web_path = f"/{file_path}"
    
    # Update gig_contract with the PDF
    now = utcnow_naive()
    hold_expires = now + timedelta(hours=24)
    
    gc = db.execute(
        text("SELECT id FROM gig_contracts WHERE gig_id = :gid AND artist_id = :aid ORDER BY id DESC LIMIT 1"),
        {"gid": gig_id, "aid": gig["artist_id"]}
    ).mappings().first()
    
    if gc:
        db.execute(
            text("""UPDATE gig_contracts SET pdf_file_path = :pdf, status = 'pending', hold_expires_at = :exp WHERE id = :cid"""),
            {"pdf": web_path, "exp": hold_expires.isoformat(), "cid": gc["id"]}
        )
    
    # Transition gig to pending_contract (artist now has 24 hours).
    # Audit fix (May 2026 part 2): require the gig to STILL be in
    # `awaiting_venue_contract`. Without this, a stale request that beats
    # a cancellation could re-bind a cancelled (now open) gig back to
    # pending_contract referencing an artist who already walked away.
    result = db.execute(
        text("""UPDATE gigs SET status = 'pending_contract',
                                contract_hold_expires_at = :exp
                WHERE id = :gid AND status = 'awaiting_venue_contract'"""),
        {"exp": hold_expires.isoformat(), "gid": gig_id}
    )
    if result.rowcount == 0:
        raise HTTPException(409, "Gig is no longer awaiting a venue contract (was the booking cancelled or already uploaded?).")
    
    # Notify artist
    try:
        artist_data = db.execute(text("SELECT name, user_id FROM artists WHERE id = :aid"), {"aid": gig["artist_id"]}).mappings().first()
        venue_data = db.execute(text("SELECT venue_name FROM venues WHERE id = :vid"), {"vid": venue_id}).mappings().first()
        gig_data = db.execute(text("SELECT date, title FROM gigs WHERE id = :gid"), {"gid": gig_id}).mappings().first()
        gig_label = gig_data["title"] or gig_data["date"] if gig_data else str(gig_id)
        if artist_data:
            db.execute(text("""INSERT INTO notifications (user_id, notification_type, title, message, gig_id, venue_id, artist_id, is_read, created_at)
                VALUES (:uid, 'contract_ready', :t, :m, :gid, :vid, :aid, 0, CURRENT_TIMESTAMP)"""),
                {"uid": artist_data["user_id"], "t": "Contract Ready — 24 Hours to Sign",
                 "m": f"The contract for {gig_label} at {venue_data['venue_name'] if venue_data else 'venue'} is ready. Download, sign, and upload within 24 hours.",
                 "gid": gig_id, "vid": venue_id, "aid": gig["artist_id"]})
    except Exception:
        pass
    
    db.commit()
    return {"ok": True, "pdf_url": web_path, "hold_expires": hold_expires.isoformat()}


# ============================================
# HOLD CLEANUP — release expired contract holds
# ============================================

@router.post("/api/contract-holds/cleanup")
def cleanup_expired_holds(user=Depends(get_current_user), db=Depends(get_db)):
    """Admin-only HTTP wrapper. Audit fix (May 2026 part 2): previously
    unauthenticated, which let any anonymous caller fire the side effects
    (cancellation emails, flyer mutations, blast re-triggers). The
    scheduler now invokes ``_cleanup_expired_holds_impl(db)`` directly
    so it can keep running without an HTTP request.

    Audit fix (May 2026): the prior version filtered on `g.status IN
    ('pending_contract','awaiting_venue_contract')`. Multi-slot gigs keep
    `g.status='open'` and only the slot row flips to pending_contract — those
    holds NEVER matched and the artist was pinned to the slot forever. Now:
      - Match purely on `contract_hold_expires_at < now` (status-agnostic),
        plus an OR clause that also picks up gigs whose only signal is a
        slot row stuck in pending_contract/awaiting_venue_contract.
      - Reset the slot row(s) for the held artist.
      - Set `last_cancelled_artist_id` so the existing blast filters
        exclude the released artist (otherwise they'd be re-blasted seconds
        later).
      - Strip the held artist's logo from the gig flyer if present.
      - Send the same cancellation emails the cancel paths use, so artist
        and venue get the standard "your hold expired" notification.
      - Fire the cancelled-gig blast for short-lead gigs (within 7 days).
    """
    if not to_admin_bool(getattr(user, "is_admin", None)):
        raise HTTPException(403, "Admin access required")
    return _cleanup_expired_holds_impl(db)


def _cleanup_expired_holds_impl(db):
    """Actual hold-cleanup work. Called by the auth-gated HTTP route above
    and by the hourly scheduler. Returns the same shape either way.

    Audit fix (May 2026 part 7): iterate per `gig_contracts` row (each artist
    has their own row with their own `hold_expires_at`) instead of joining
    on the parent gig's single `contract_hold_expires_at`. The old approach
    couldn't track multi-slot gigs where Artist B signs slot 2 at T (deadline
    T+48h) and Artist C signs slot 3 at T+1h (deadline T+49h) — the parent
    column got overwritten to C's deadline so B's slot stayed stuck forever.
    Per-contract iteration releases each (gig, artist) pair independently.
    """
    now = utcnow_naive().isoformat()

    expired = db.execute(
        text("""
            SELECT
                gc.id as contract_id, gc.gig_id, gc.artist_id, gc.status as contract_status,
                gc.hold_expires_at,
                g.venue_id, g.date, g.title, g.start_time, g.end_time
            FROM gig_contracts gc
            JOIN gigs g ON g.id = gc.gig_id
            WHERE gc.hold_expires_at IS NOT NULL
              AND gc.hold_expires_at < :now
              AND gc.status IN ('pending', 'awaiting_venue_upload', 'artist_signed')
        """),
        {"now": now}
    ).mappings().all()

    released = []
    # Deferred imports — these modules import contracts.py too.
    from backend.services.gig_cleanup import cleanup_gig_records
    from backend.services.email_dispatch import send_cancellation_emails
    try:
        from backend.routes.gigs import (
            _delete_flyer_if_no_bookings_remain,
            _remove_artist_logo_from_flyer,
            fire_cancelled_gig_blast,
        )
    except Exception:
        _delete_flyer_if_no_bookings_remain = None
        _remove_artist_logo_from_flyer = None
        fire_cancelled_gig_blast = None

    for gig in expired:
        gig = dict(gig)
        gig_id = gig["gig_id"]
        held_aid = gig.get("artist_id")
        contract_id = gig["contract_id"]

        # 1. Reset the slot row(s) the held artist is pinned to. Restores pay
        #    to the gig's listed pay (clears any per-artist override).
        if held_aid:
            db.execute(
                text("""
                    UPDATE gig_slots SET
                        artist_id = NULL,
                        status = 'open',
                        approval_requested_at = NULL,
                        pay = (SELECT g.pay FROM gigs g WHERE g.id = :gid)
                    WHERE gig_id = :gid
                      AND artist_id = :aid
                      AND status IN ('pending_contract','awaiting_venue_contract')
                """),
                {"gid": gig_id, "aid": held_aid}
            )

        # 2. Mark just THIS contract row expired (don't touch other artists'
        #    pending contracts on the same gig).
        # Audit fix (May 2026 part 8): atomic claim — only expire if still
        # in an in-flight status. Without this, if a venue countersigned
        # mid-sweep (status='fully_signed'), this would blindly regress the
        # contract back to 'expired' and blank the booking from the artist's UI.
        _claim = db.execute(
            text("UPDATE gig_contracts SET status = 'expired' "
                 "WHERE id = :cid "
                 "AND status IN ('pending', 'awaiting_venue_upload', 'artist_signed')"),
            {"cid": contract_id}
        )
        if (_claim.rowcount or 0) == 0:
            # Status changed during the sweep (venue countersigned this contract
            # right as we tried to expire it). Skip to avoid regressing fully_signed.
            logger.info(f"[HOLD_EXPIRED] contract {contract_id} status changed during sweep — skipping expire")
            continue

        # 3. Reset the parent gigs row ONLY if no other in-flight contracts
        #    or booked slots remain on this gig. Otherwise leave the parent
        #    alone so co-pending artists' state is preserved.
        _other_inflight = db.execute(
            text("""SELECT 1 FROM gig_contracts gc2
                    WHERE gc2.gig_id = :gid AND gc2.id != :cid
                      AND gc2.status IN ('pending','awaiting_venue_upload','artist_signed','fully_signed')
                    LIMIT 1"""),
            {"gid": gig_id, "cid": contract_id}
        ).first()
        _other_booked_slot = db.execute(
            text("SELECT 1 FROM gig_slots WHERE gig_id = :gid AND status = 'booked' LIMIT 1"),
            {"gid": gig_id}
        ).first()
        if not _other_inflight and not _other_booked_slot:
            db.execute(
                text("""
                    UPDATE gigs SET
                        status = CASE WHEN status IN ('pending_contract','awaiting_venue_contract')
                                      THEN 'open' ELSE status END,
                        artist_id = CASE WHEN artist_id = :aid THEN NULL ELSE artist_id END,
                        contract_hold_artist_id = NULL,
                        contract_hold_expires_at = NULL,
                        radius_blast_token = NULL
                    WHERE id = :gid
                """),
                {"gid": gig_id, "aid": held_aid}
            )
        else:
            # Just clear the per-gig tracker if it pointed at this released artist;
            # the cancellation log (below) lets blast still exclude them.
            db.execute(
                text("""
                    UPDATE gigs SET
                        contract_hold_artist_id = CASE WHEN contract_hold_artist_id = :aid THEN NULL ELSE contract_hold_artist_id END,
                        contract_hold_expires_at = CASE WHEN contract_hold_artist_id = :aid THEN NULL ELSE contract_hold_expires_at END
                    WHERE id = :gid
                """),
                {"gid": gig_id, "aid": held_aid}
            )
        # Audit fix (May 2026 part 9): write to gig_cancelled_artists (and the legacy
        # last_cancelled_artist_id column) so every cancelled artist is excluded by
        # the blast filter, not just the most recent.
        try:
            from backend.routes.gigs import _record_artist_cancellation as _rac
            _rac(db, gig_id, held_aid)
        except Exception as _rae:
            logger.warning(f"_record_artist_cancellation failed for gig {gig_id} artist {held_aid}: {_rae}")

        # 4. Clean up any stale transactions for this artist (rare on
        #    pending_contract since txns aren't created until countersign,
        #    but defensive — covers any leftover from edge paths).
        if held_aid:
            try:
                cleanup_gig_records(db, gig_id, int(held_aid))
            except Exception as _ce:
                logger.warning(f"[HOLD_EXPIRED] cleanup_gig_records skip gig {gig_id} aid {held_aid}: {_ce}")

        # 5. Strip the held artist's logo from the flyer (preserved when
        #    other slots are still booked) or delete the flyer entirely.
        if _delete_flyer_if_no_bookings_remain:
            try:
                _delete_flyer_if_no_bookings_remain(db, gig_id, cancelled_artist_id=int(held_aid) if held_aid else None)
            except Exception as _fe:
                logger.warning(f"[HOLD_EXPIRED] flyer cleanup skip gig {gig_id}: {_fe}")

        db.commit()

        # 6. Look up names for emails + notifications.
        gig_label = gig["title"] or gig["date"] or str(gig_id)
        venue_data = db.execute(text("SELECT venue_name, user_id FROM venues WHERE id = :vid"), {"vid": gig["venue_id"]}).mappings().first()
        artist_data = db.execute(text("SELECT name, user_id FROM artists WHERE id = :aid"), {"aid": held_aid}).mappings().first() if held_aid else None

        # 7. In-app notifications (kept for backward compat with existing UI).
        try:
            if artist_data:
                db.execute(text("""INSERT INTO notifications (user_id, notification_type, title, message, gig_id, venue_id, artist_id, is_read, created_at)
                    VALUES (:uid, 'contract_expired', :t, :m, :gid, :vid, :aid, 0, CURRENT_TIMESTAMP)"""),
                    {"uid": artist_data["user_id"], "t": "Contract Hold Expired",
                     "m": f"Your hold on {gig_label} at {venue_data['venue_name'] if venue_data else 'venue'} has expired. The gig is now open for other artists.",
                     "gid": gig_id, "vid": gig["venue_id"], "aid": held_aid})
            if venue_data:
                db.execute(text("""INSERT INTO notifications (user_id, notification_type, title, message, gig_id, venue_id, artist_id, is_read, created_at)
                    VALUES (:uid, 'contract_expired', :t, :m, :gid, :vid, :aid, 0, CURRENT_TIMESTAMP)"""),
                    {"uid": venue_data["user_id"], "t": "Contract Hold Expired",
                     "m": f"The contract hold for {gig_label} by {artist_data['name'] if artist_data else 'artist'} has expired. The gig is now open.",
                     "gid": gig_id, "vid": gig["venue_id"], "aid": held_aid})
            db.commit()
        except Exception as _ne:
            logger.warning(f"[HOLD_EXPIRED] notification insert skip gig {gig_id}: {_ne}")

        # 8. Cancellation emails — same dispatcher the cancel paths use.
        if held_aid and venue_data and artist_data:
            try:
                send_cancellation_emails(
                    db,
                    {
                        "id": gig_id,
                        "artist_name": artist_data["name"],
                        "venue_name": venue_data["venue_name"],
                        "artist_id": held_aid,
                        "venue_id": gig["venue_id"],
                        "date": gig["date"],
                        "start_time": gig.get("start_time"),
                        "end_time": gig.get("end_time"),
                    },
                    cancellation_reason="Contract hold expired (48h timeout)",
                    cancelled_by="venue",  # treat as a release rather than artist-initiated cancel
                )
            except Exception as _ee:
                logger.warning(f"[HOLD_EXPIRED] email skip gig {gig_id}: {_ee}")

        # 9. Fire the cancelled-gig blast for short-lead gigs.
        if fire_cancelled_gig_blast:
            try:
                fire_cancelled_gig_blast(db, gig_id, gig["venue_id"])
            except Exception as _be:
                logger.warning(f"[HOLD_EXPIRED] blast skip gig {gig_id}: {_be}")

        released.append(gig_id)

    return {"ok": True, "released_count": len(released), "released_gig_ids": released}


# ============================================
# DOWNLOAD CONTRACT AS PDF (zero external dependencies)
# ============================================
from fastapi.responses import StreamingResponse
import io, re as _re

def _decode_entities(s):
    """Decode common HTML entities"""
    s = s.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
    s = s.replace('&nbsp;', ' ').replace('&#39;', "'").replace('&quot;', '"')
    s = s.replace('&middot;', chr(183)).replace('&bull;', chr(8226))
    s = s.replace('&mdash;', chr(8212)).replace('&ndash;', chr(8211))
    s = s.replace('&rsquo;', chr(8217)).replace('&lsquo;', chr(8216))
    s = _re.sub(r'&#(\d+);', lambda m: chr(int(m.group(1))), s)
    return s

def _pdf_escape(text):
    """Escape special characters for PDF text strings"""
    return text.replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)')

def _parse_html_to_blocks(html_str):
    """Parse contract HTML into structured blocks for PDF rendering.
    Returns list of dicts with keys: type, text, segments, centered, indent, style_hint"""
    if not html_str:
        return [{'type': 'p', 'segments': [('', False)]}]
    
    blocks = []
    
    # Normalize: remove newlines within tags, collapse whitespace
    html = _re.sub(r'>\s+<', '><', html_str)
    
    def extract_text_segments(inner_html):
        """Parse inline HTML into list of (text, is_bold) segments"""
        segs = []
        # Split on <strong>, <b>, </strong>, </b>
        parts = _re.split(r'(</?(?:strong|b)>)', inner_html)
        bold = False
        for part in parts:
            if part in ('<strong>', '<b>'):
                bold = True
                continue
            elif part in ('</strong>', '</b>'):
                bold = False
                continue
            # Handle <br> as newline
            sub_parts = _re.split(r'<br\s*/?>', part)
            for i, sp in enumerate(sub_parts):
                # Strip remaining inline tags
                cleaned = _re.sub(r'<[^>]+>', '', sp)
                cleaned = _decode_entities(cleaned).strip()
                if cleaned:
                    segs.append((cleaned, bold))
                if i < len(sub_parts) - 1:
                    segs.append(('\n', False))
        return segs if segs else [('', False)]
    
    def is_centered(tag_str):
        return 'text-align:center' in tag_str or 'text-align: center' in tag_str
    
    def is_italic_hint(tag_str):
        return 'font-style:italic' in tag_str or 'font-style: italic' in tag_str
    
    def is_small_hint(tag_str):
        return 'font-size:0.75' in tag_str or 'font-size: 0.75' in tag_str
    
    # Split into block-level elements
    # Match: h1-h6, p, ul, ol, div, hr, and grid divs
    pattern = r'(<h[1-6][^>]*>.*?</h[1-6]>|<p[^>]*>.*?</p>|<ul[^>]*>.*?</ul>|<ol[^>]*>.*?</ol>|<div[^>]*>.*?</div>|<hr[^>]*/?>)'
    parts = _re.split(pattern, html, flags=_re.DOTALL)
    
    for part in parts:
        part = part.strip()
        if not part:
            continue
        
        # H2 — main title
        m = _re.match(r'<h2([^>]*)>(.*?)</h2>', part, _re.DOTALL)
        if m:
            attrs, inner = m.group(1), m.group(2)
            blocks.append({
                'type': 'h2',
                'segments': extract_text_segments(inner),
                'centered': True,
            })
            continue
        
        # H3 — section header
        m = _re.match(r'<h3([^>]*)>(.*?)</h3>', part, _re.DOTALL)
        if m:
            attrs, inner = m.group(1), m.group(2)
            blocks.append({
                'type': 'h3',
                'segments': extract_text_segments(inner),
                'centered': is_centered(attrs),
            })
            continue
        
        # H4-H6
        m = _re.match(r'<h[4-6]([^>]*)>(.*?)</h[4-6]>', part, _re.DOTALL)
        if m:
            attrs, inner = m.group(1), m.group(2)
            blocks.append({
                'type': 'h4',
                'segments': extract_text_segments(inner),
                'centered': is_centered(attrs),
            })
            continue
        
        # HR
        if _re.match(r'<hr', part):
            blocks.append({'type': 'hr'})
            continue
        
        # UL / OL
        m = _re.match(r'<(ul|ol)[^>]*>(.*?)</\1>', part, _re.DOTALL)
        if m:
            list_type = m.group(1)
            items_html = m.group(2)
            items = _re.findall(r'<li[^>]*>(.*?)</li>', items_html, _re.DOTALL)
            for idx, item_html in enumerate(items):
                prefix = f"{idx+1}." if list_type == 'ol' else chr(8226)
                segs = extract_text_segments(item_html)
                blocks.append({
                    'type': 'li',
                    'segments': segs,
                    'prefix': prefix,
                    'indent': 24,
                })
            continue
        
        # P
        m = _re.match(r'<p([^>]*)>(.*?)</p>', part, _re.DOTALL)
        if m:
            attrs, inner = m.group(1), m.group(2)
            blocks.append({
                'type': 'p',
                'segments': extract_text_segments(inner),
                'centered': is_centered(attrs),
                'italic': is_italic_hint(attrs),
                'small': is_small_hint(attrs),
            })
            continue
        
        # DIV — treat like p, skip grid containers (signature blanks)
        m = _re.match(r'<div([^>]*)>(.*?)</div>', part, _re.DOTALL)
        if m:
            attrs, inner = m.group(1), m.group(2)
            # If it contains nested block elements, extract text simply
            if '<p' in inner or '<div' in inner:
                # Recurse on inner content
                sub_blocks = _parse_html_to_blocks(inner)
                blocks.extend(sub_blocks)
            else:
                blocks.append({
                    'type': 'p',
                    'segments': extract_text_segments(inner),
                    'centered': is_centered(attrs),
                    'italic': is_italic_hint(attrs),
                    'small': is_small_hint(attrs),
                })
            continue
        
        # Fallback: plain text outside any tags
        cleaned = _re.sub(r'<[^>]+>', '', part)
        cleaned = _decode_entities(cleaned).strip()
        if cleaned:
            blocks.append({
                'type': 'p',
                'segments': [(cleaned, False)],
            })
    
    return blocks


def _generate_pdf_native(contract, body_html):
    """Generate a PDF using only Python stdlib — no external packages needed.
    Parses HTML structure to preserve headings, bold, indents, bullets, etc."""
    
    blocks = _parse_html_to_blocks(body_html or '')
    
    # Page dimensions (US Letter: 612 x 792 points)
    PW, PH = 612, 792
    ML, MR, MT, MB = 54, 54, 54, 54
    UW = PW - ML - MR
    
    # Font metrics: average character width at 1pt for each font
    FONT_CW = {'F1': 0.52, 'F2': 0.56, 'F3': 0.50, 'F4': 0.52}
    # F1=Helvetica, F2=Helvetica-Bold, F3=Times-Italic, F4=Helvetica-Oblique
    
    def measure_text(text, font, size):
        """Approximate width in points"""
        return len(text) * FONT_CW.get(font, 0.52) * size
    
    def wrap_segments(segments, font_size, max_width, default_bold=False):
        """Word-wrap a list of (text, is_bold) segments into lines.
        Each line is a list of (text, font_name) tuples."""
        lines = []
        current_line = []
        current_width = 0
        
        for text, is_bold in segments:
            if text == '\n':
                lines.append(current_line if current_line else [('', 'F1')])
                current_line = []
                current_width = 0
                continue
            
            fn = 'F2' if (is_bold or default_bold) else 'F1'
            words = text.split()
            
            for word in words:
                w_width = measure_text(word + ' ', fn, font_size)
                if current_width + w_width > max_width and current_line:
                    lines.append(current_line)
                    current_line = []
                    current_width = 0
                
                if current_line and current_line[-1][1] == fn:
                    # Append to same font run
                    current_line[-1] = (current_line[-1][0] + ' ' + word, fn)
                else:
                    prefix = ' ' if current_line else ''
                    current_line.append((prefix + word, fn))
                current_width += w_width
        
        if current_line:
            lines.append(current_line)
        
        return lines if lines else [[('', 'F1')]]
    
    # Build render items: list of dicts describing each renderable piece
    # Each item: {type, lines, size, spacing_before, spacing_after, indent, centered}
    items = []
    
    for block in blocks:
        btype = block.get('type', 'p')
        centered = block.get('centered', False)
        indent = block.get('indent', 0)
        segments = block.get('segments', [('', False)])
        
        if btype == 'hr':
            items.append({'type': 'hr', 'spacing_before': 10, 'spacing_after': 10})
            continue
        
        if btype == 'h2':
            avail = UW - indent
            lines = wrap_segments(segments, 14, avail, default_bold=True)
            items.append({
                'type': 'text', 'lines': lines, 'size': 14, 'font': 'F2',
                'spacing_before': 4, 'spacing_after': 2,
                'indent': indent, 'centered': True,
            })
            continue
        
        if btype == 'h3':
            avail = UW - indent
            lines = wrap_segments(segments, 11, avail, default_bold=True)
            items.append({
                'type': 'text', 'lines': lines, 'size': 11, 'font': 'F2',
                'spacing_before': 16, 'spacing_after': 4,
                'indent': indent, 'centered': centered,
            })
            continue
        
        if btype == 'h4':
            avail = UW - indent
            lines = wrap_segments(segments, 10, avail, default_bold=True)
            items.append({
                'type': 'text', 'lines': lines, 'size': 10, 'font': 'F2',
                'spacing_before': 12, 'spacing_after': 4,
                'indent': indent, 'centered': centered,
            })
            continue
        
        if btype == 'li':
            prefix = block.get('prefix', chr(8226))
            li_indent = indent or 24
            avail = UW - li_indent
            lines = wrap_segments(segments, 10, avail)
            # Prepend bullet/number to first line
            if lines and lines[0]:
                first_text = lines[0][0][0]
                first_font = lines[0][0][1]
                lines[0][0] = (prefix + '  ' + first_text.lstrip(), first_font)
            items.append({
                'type': 'text', 'lines': lines, 'size': 10, 'font': 'F1',
                'spacing_before': 1, 'spacing_after': 2,
                'indent': li_indent, 'centered': False,
            })
            continue
        
        # p / div / default
        sz = 8 if block.get('small') else 10
        avail = UW - indent
        lines = wrap_segments(segments, sz, avail)
        fn = 'F4' if block.get('italic') else 'F1'
        items.append({
            'type': 'text', 'lines': lines, 'size': sz, 'font': fn,
            'spacing_before': 2, 'spacing_after': 4,
            'indent': indent, 'centered': centered,
        })
    
    # Add signatures section
    items.append({'type': 'hr', 'spacing_before': 16, 'spacing_after': 8})
    
    if contract.get("artist_signature_name"):
        items.append({'type': 'text', 'lines': [[('Artist Signature', 'F1')]], 'size': 9, 'font': 'F1', 'spacing_before': 0, 'spacing_after': 2, 'indent': 0, 'centered': False})
        items.append({'type': 'text', 'lines': [[(_pdf_escape(str(contract["artist_signature_name"])), 'F3')]], 'size': 14, 'font': 'F3', 'spacing_before': 0, 'spacing_after': 1, 'indent': 0, 'centered': False})
        sig_date = str(contract.get("artist_signature_date", "") or "")
        items.append({'type': 'text', 'lines': [[(f'Signed: {sig_date}', 'F1')]], 'size': 8, 'font': 'F1', 'spacing_before': 0, 'spacing_after': 10, 'indent': 0, 'centered': False})
    
    if contract.get("venue_signature_name"):
        items.append({'type': 'text', 'lines': [[('Venue Signature', 'F1')]], 'size': 9, 'font': 'F1', 'spacing_before': 0, 'spacing_after': 2, 'indent': 0, 'centered': False})
        items.append({'type': 'text', 'lines': [[(_pdf_escape(str(contract["venue_signature_name"])), 'F3')]], 'size': 14, 'font': 'F3', 'spacing_before': 0, 'spacing_after': 1, 'indent': 0, 'centered': False})
        sig_date = str(contract.get("venue_signature_date", "") or "")
        items.append({'type': 'text', 'lines': [[(f'Countersigned: {sig_date}', 'F1')]], 'size': 8, 'font': 'F1', 'spacing_before': 0, 'spacing_after': 10, 'indent': 0, 'centered': False})
    
    if not contract.get("artist_signature_name") and not contract.get("venue_signature_name"):
        items.append({'type': 'text', 'lines': [[('No signatures yet.', 'F1')]], 'size': 9, 'font': 'F1', 'spacing_before': 0, 'spacing_after': 8, 'indent': 0, 'centered': False})
    
    # Footer
    items.append({'type': 'hr', 'spacing_before': 16, 'spacing_after': 4})
    footer = f"Generated by GigsFill  |  Contract ID: {contract['id']}  |  {utcnow_naive().strftime('%B %d, %Y')}"
    items.append({'type': 'text', 'lines': [[(footer, 'F1')]], 'size': 7, 'font': 'F1', 'spacing_before': 0, 'spacing_after': 0, 'indent': 0, 'centered': True})
    
    # === Paginate into PDF content streams ===
    page_streams = []
    ops = []
    y = PH - MT
    
    def item_height(item):
        if item['type'] == 'hr':
            return item['spacing_before'] + 1 + item['spacing_after']
        num_lines = len(item.get('lines', []))
        line_h = item['size'] * 1.5
        return item['spacing_before'] + num_lines * line_h + item['spacing_after']
    
    for item in items:
        h = item_height(item)
        if y - h < MB and ops:
            page_streams.append('\n'.join(ops))
            ops = []
            y = PH - MT
        
        if item['type'] == 'hr':
            y -= item['spacing_before']
            ops.append(f'0.75 0.75 0.75 RG 0.5 w {ML} {y:.0f} m {PW-MR} {y:.0f} l S 0 0 0 RG')
            y -= 1 + item['spacing_after']
            continue
        
        y -= item['spacing_before']
        sz = item['size']
        line_h = sz * 1.5
        indent = item.get('indent', 0)
        centered = item.get('centered', False)
        
        for line_segs in item.get('lines', []):
            # Calculate line width for centering
            if centered:
                total_w = sum(measure_text(seg_text, seg_font, sz) for seg_text, seg_font in line_segs)
                x_start = ML + (UW - total_w) / 2
            else:
                x_start = ML + indent
            
            # Render segments with font switching
            ops.append('BT')
            ops.append(f'{x_start:.1f} {y:.0f} Td')
            for seg_text, seg_font in line_segs:
                if not seg_text:
                    continue
                esc = _pdf_escape(seg_text)
                ops.append(f'/{seg_font} {sz} Tf ({esc}) Tj')
            ops.append('ET')
            y -= line_h
        
        y -= item['spacing_after']
    
    if ops:
        page_streams.append('\n'.join(ops))
    if not page_streams:
        page_streams.append(f'BT /F1 10 Tf {ML} {PH-MT} Td ((No content)) Tj ET')
    
    # === Build PDF file ===
    num_pages = len(page_streams)
    font_dict = '/F1 3 0 R /F2 4 0 R /F3 5 0 R /F4 6 0 R'
    page_obj_nums = [8 + 2*i for i in range(num_pages)]
    kids = ' '.join(f'{n} 0 R' for n in page_obj_nums)
    
    obj_strs = []
    # 1: Catalog
    obj_strs.append('<< /Type /Catalog /Pages 2 0 R >>')
    # 2: Pages
    obj_strs.append(f'<< /Type /Pages /Kids [{kids}] /Count {num_pages} >>')
    # 3: Helvetica
    obj_strs.append('<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>')
    # 4: Helvetica-Bold
    obj_strs.append('<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>')
    # 5: Times-Italic
    obj_strs.append('<< /Type /Font /Subtype /Type1 /BaseFont /Times-Italic /Encoding /WinAnsiEncoding >>')
    # 6: Helvetica-Oblique (for italic paragraphs like disclaimers)
    obj_strs.append('<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Oblique /Encoding /WinAnsiEncoding >>')
    
    for i, stream_text in enumerate(page_streams):
        stream_bytes = stream_text.encode('cp1252', errors='replace')
        stream_obj_num = 7 + 2*i
        page_obj_num = 8 + 2*i
        obj_strs.append(f'<< /Length {len(stream_bytes)} >>\nstream\n{stream_text}\nendstream')
        obj_strs.append(
            f'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {PW} {PH}] '
            f'/Contents {stream_obj_num} 0 R '
            f'/Resources << /Font << {font_dict} >> >> >>'
        )
    
    # Write PDF bytes
    buf = io.BytesIO()
    buf.write(b'%PDF-1.4\n%\xe2\xe3\xcf\xd3\n')
    
    offsets = []
    for idx, obj_content in enumerate(obj_strs):
        obj_num = idx + 1
        offsets.append(buf.tell())
        if '\nstream\n' in obj_content:
            parts = obj_content.split('\nstream\n', 1)
            header = parts[0]
            rest = parts[1]
            stream_end = rest.rfind('\nendstream')
            stream_data = rest[:stream_end]
            buf.write(f'{obj_num} 0 obj\n{header}\nstream\n'.encode('cp1252', errors='replace'))
            buf.write(stream_data.encode('cp1252', errors='replace'))
            buf.write(b'\nendstream\nendobj\n')
        else:
            buf.write(f'{obj_num} 0 obj\n{obj_content}\nendobj\n'.encode('cp1252', errors='replace'))
    
    xref_offset = buf.tell()
    buf.write(b'xref\n')
    buf.write(f'0 {len(obj_strs) + 1}\n'.encode())
    buf.write(b'0000000000 65535 f \n')
    for off in offsets:
        buf.write(f'{off:010d} 00000 n \n'.encode())
    
    buf.write(b'trailer\n')
    buf.write(f'<< /Size {len(obj_strs) + 1} /Root 1 0 R >>\n'.encode())
    buf.write(b'startxref\n')
    buf.write(f'{xref_offset}\n'.encode())
    buf.write(b'%%EOF\n')
    
    buf.seek(0)
    return buf


@router.get("/api/gig-contracts/{contract_id}/download-pdf")
def download_contract_pdf(contract_id: int, request: Request, user=Depends(get_current_user), db=Depends(get_db)):
    """Generate and download/view a contract as a PDF file (no external dependencies).
    Pass ?inline=1 to view in browser instead of downloading."""
    
    inline = request.query_params.get("inline") == "1"
    
    try:
        row = db.execute(
            text("""
                SELECT gc.*, vc.name as template_name,
                       v.venue_name, a.name as artist_name,
                       g.date as gig_date, g.start_time, g.end_time, g.pay, g.title as gig_title
                FROM gig_contracts gc
                LEFT JOIN venue_contracts vc ON gc.venue_contract_id = vc.id
                LEFT JOIN venues v ON gc.venue_id = v.id
                LEFT JOIN artists a ON gc.artist_id = a.id
                LEFT JOIN gigs g ON gc.gig_id = g.id
                WHERE gc.id = :cid
            """),
            {"cid": contract_id}
        ).mappings().first()
    except Exception as e:
        # Audit fix (May 2026 part 4): log the original exception with
        # stack so admin can diagnose. Previously this catch swallowed
        # the trace.
        logger.error(f"download_contract_pdf DB query failed for contract {contract_id}: {e}",
                     exc_info=True)
        raise HTTPException(500, "Database error. Please try again.")

    if not row:
        raise HTTPException(404, "Contract not found")
    
    contract = dict(row)
    
    # Verify access
    venue_access = db.execute(
        text("SELECT 1 FROM venues v WHERE v.id = :vid AND (v.user_id = :uid OR EXISTS (SELECT 1 FROM entity_users eu WHERE eu.entity_type='venue' AND eu.entity_id=v.id AND eu.user_id=:uid))"),
        {"vid": contract["venue_id"], "uid": user.id}
    ).first()
    artist_access = db.execute(
        text("SELECT 1 FROM artists a WHERE a.id = :aid AND (a.user_id = :uid OR EXISTS (SELECT 1 FROM entity_users eu WHERE eu.entity_type='artist' AND eu.entity_id=a.id AND eu.user_id=:uid))"),
        {"aid": contract["artist_id"], "uid": user.id}
    ).first()
    # Audit fix (May 2026 part 6): admin bypass for support/audit access.
    if not venue_access and not artist_access and not to_admin_bool(getattr(user, "is_admin", "false")):
        raise HTTPException(403, "No access to this contract")

    # For PDF upload contracts, serve the file directly (not redirect, to avoid browser cache)
    if contract["contract_type"] == "pdf_upload":
        pdf_path = contract.get("signed_pdf_path") or contract.get("pdf_file_path")
        logger.debug(f"[PDF DOWNLOAD] Contract {contract_id}: contract_type=pdf_upload, signed_pdf_path={contract.get('signed_pdf_path')}, pdf_file_path={contract.get('pdf_file_path')}, using={pdf_path}")
        if pdf_path:
            # Resolve file on disk
            resolved = None
            for candidate in [pdf_path, pdf_path.lstrip("/"), os.path.join(".", pdf_path.lstrip("/"))]:
                exists = os.path.exists(candidate)
                print(f"[PDF DOWNLOAD] Trying: '{candidate}' → exists={exists}" + (f" size={os.path.getsize(candidate)}" if exists else ""))
                if exists and not resolved:
                    resolved = candidate
            # Audit fix (May 2026 part 3): defend against a malicious / corrupted
            # DB value pointing at a file outside UPLOAD_DIR. realpath() resolves
            # symlinks and ".." segments; anything that doesn't end up under the
            # expected uploads root is rejected before serving.
            if resolved:
                try:
                    _allowed_root = os.path.realpath(UPLOAD_DIR)
                    _resolved_abs = os.path.realpath(resolved)
                    if not (_resolved_abs == _allowed_root or _resolved_abs.startswith(_allowed_root + os.sep)):
                        logger.error(f"download_contract_pdf: refused path-traversal — resolved={_resolved_abs} not under {_allowed_root}")
                        raise HTTPException(404, "No PDF file found")
                except HTTPException:
                    raise
                except Exception as _safe_e:
                    logger.error(f"download_contract_pdf: realpath check failed: {_safe_e}")
                    raise HTTPException(500, "PDF file could not be served")
            if resolved:
                from fastapi.responses import FileResponse
                venue_name = contract.get("venue_name") or "Venue"
                artist_name = contract.get("artist_name") or "Artist"
                gig_date = contract.get("gig_date")
                filename = _contract_display_filename(venue_name, artist_name, gig_date)
                logger.info(f"Serving PDF contract {contract_id}: {resolved} (size={os.path.getsize(resolved)}, inline={inline})")
                disposition = "inline" if inline else "attachment"
                return FileResponse(
                    resolved,
                    media_type="application/pdf",
                    filename=filename,
                    headers={
                        "Content-Disposition": f'{disposition}; filename="{filename}"',
                        "Cache-Control": "no-cache, no-store, must-revalidate",
                        "Pragma": "no-cache",
                        "Expires": "0"
                    }
                )
            else:
                # Audit fix (May 2026 part 5): the previous fallback redirected
                # to whatever the DB row contained. A poisoned signed_pdf_path
                # (absolute URL, javascript: scheme, off-site host) would have
                # bypassed the realpath traversal guard above and turned this
                # endpoint into an open redirect. Now we only redirect when the
                # value is an app-local relative path AND it doesn't escape
                # UPLOAD_DIR — otherwise we surface a 404.
                _safe_redirect = None
                try:
                    _pdf_path_str = (pdf_path or "").strip()
                    if (_pdf_path_str
                            and not _pdf_path_str.startswith("//")
                            and ":" not in _pdf_path_str.split("/")[0]  # rejects 'http:', 'javascript:', etc.
                            and "\\" not in _pdf_path_str):
                        _rel = _pdf_path_str.lstrip("/")
                        _candidate_abs = os.path.realpath(os.path.join(".", _rel))
                        _allowed_root = os.path.realpath(UPLOAD_DIR)
                        if _candidate_abs == _allowed_root or _candidate_abs.startswith(_allowed_root + os.sep):
                            _safe_redirect = "/" + _rel
                except Exception:
                    _safe_redirect = None
                if _safe_redirect:
                    logger.warning(f"PDF file not found on disk for contract {contract_id}; redirecting to static path {_safe_redirect}")
                    from fastapi.responses import RedirectResponse
                    return RedirectResponse(url=_safe_redirect)
                logger.warning(f"PDF file not found for contract {contract_id}: tried {pdf_path}")
        raise HTTPException(404, "No PDF file found")
    
    body = contract.get("rendered_body", "") or ""
    venue_name = contract.get("venue_name") or "Venue"
    artist_name = contract.get("artist_name") or "Artist"
    gig_date = contract.get("gig_date")
    filename = _contract_display_filename(venue_name, artist_name, gig_date)
    
    try:
        buf = _generate_pdf_native(contract, body)
    except Exception as e:
        import traceback
        logger.error(f"PDF generation error: {traceback.format_exc()}")
        raise HTTPException(500, "PDF generation failed. Please try again.")

    # Append signature page(s) for signed contracts
    try:
        from pypdf import PdfReader as _R, PdfWriter as _W
        import io as _io
        writer = _W()
        # Add contract body pages
        for page in _R(_io.BytesIO(buf.read())).pages:
            writer.add_page(page)
        buf.seek(0)
        # Artist signature page
        if contract.get("artist_signature_name"):
            artist_sig_page = _generate_signature_page_pdf(
                venue_name=venue_name,
                signature_name=contract["artist_signature_name"],
                sig_date=str(contract.get("artist_signature_date", "")),
                sig_ip=str(contract.get("artist_signature_ip", "")),
                artist_name=artist_name,
                gig_date=str(gig_date or ""),
                contract_id=contract["id"]
            )
            for page in _R(_io.BytesIO(artist_sig_page)).pages:
                writer.add_page(page)
        # Venue countersignature page
        if contract.get("venue_signature_name"):
            venue_sig_page = _generate_signature_page_pdf(
                venue_name=venue_name,
                signature_name=contract["venue_signature_name"],
                sig_date=str(contract.get("venue_signature_date", "")),
                sig_ip=str(contract.get("venue_signature_ip", "")),
                artist_name=artist_name,
                gig_date=str(gig_date or ""),
                contract_id=contract["id"]
            )
            for page in _R(_io.BytesIO(venue_sig_page)).pages:
                writer.add_page(page)
        out = _io.BytesIO()
        writer.write(out)
        out.seek(0)
        buf = out
    except ImportError:
        buf.seek(0)  # pypdf not available, return unsigned PDF
    except Exception as _sig_err:
        logger.warning(f"Could not append signature pages to contract {contract['id']}: {_sig_err}")
        buf.seek(0)
    
    disposition = "inline" if inline else "attachment"
    return StreamingResponse(
        buf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'{disposition}; filename="{filename}"',
            "Cache-Control": "no-cache, no-store, must-revalidate",
        }
    )


@router.get("/api/gig-contracts/{contract_id}/debug-pdf")
def debug_contract_pdf(contract_id: int, user=Depends(get_current_user), db=Depends(get_db)):
    """Diagnostic endpoint — shows file paths and sizes for a contract's PDFs.

    Audit fix (May 2026 part 10): admin-only. Previously any authenticated
    user could poll any contract_id and get back full server filesystem
    paths (signed_pdf_path_db, pdf_file_path_db, server_cwd, file sizes,
    modified times) — information disclosure about internal storage
    layout. Now gated to admin.
    """
    from backend.utils import to_admin_bool
    if not to_admin_bool(getattr(user, "is_admin", None)):
        raise HTTPException(403, "Admin only")
    contract = db.execute(
        text("SELECT id, contract_type, signed_pdf_path, pdf_file_path, status, venue_signature_name, venue_signature_date FROM gig_contracts WHERE id = :cid"),
        {"cid": contract_id}
    ).mappings().first()

    if not contract:
        return {"error": "Contract not found"}
    
    contract = dict(contract)
    result = {
        "contract_id": contract_id,
        "contract_type": contract.get("contract_type"),
        "status": contract.get("status"),
        "venue_signature_name": contract.get("venue_signature_name"),
        "venue_signature_date": contract.get("venue_signature_date"),
        "signed_pdf_path_db": contract.get("signed_pdf_path"),
        "pdf_file_path_db": contract.get("pdf_file_path"),
        "server_cwd": os.getcwd(),
        "file_checks": {}
    }
    
    for label, path in [("signed_pdf_path", contract.get("signed_pdf_path")), ("pdf_file_path", contract.get("pdf_file_path"))]:
        if not path:
            result["file_checks"][label] = "not set in DB"
            continue
        checks = {}
        for desc, candidate in [("as-is", path), ("lstrip /", path.lstrip("/")), ("./relative", os.path.join(".", path.lstrip("/")))]:
            exists = os.path.exists(candidate)
            info = {"path": candidate, "exists": exists}
            if exists:
                stat = os.stat(candidate)
                info["size_bytes"] = stat.st_size
                info["modified"] = datetime.fromtimestamp(stat.st_mtime).isoformat()
            checks[desc] = info
        result["file_checks"][label] = checks
    
    # Also check for _countersigned.pdf fallback file
    signed = contract.get("signed_pdf_path", "")
    if signed:
        cs_path = signed.lstrip("/").replace('.pdf', '_countersigned.pdf')
        cs_exists = os.path.exists(cs_path)
        result["countersigned_fallback"] = {
            "path": cs_path,
            "exists": cs_exists,
            "size_bytes": os.path.getsize(cs_path) if cs_exists else None
        }
    
    return result

