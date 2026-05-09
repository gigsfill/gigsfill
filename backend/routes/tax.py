"""
Tax routes - W9 form management, venue tax settings, and 1099 generation/sending.
TIN (SSN/EIN) is encrypted at rest and only last 4 digits are returned to frontend.
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import text
from backend.db import get_db
from backend.routes.auth import get_current_user
import base64
import hashlib
import os
from datetime import datetime
from backend.utils import utcnow_naive

router = APIRouter()

# ==========================================
# TIN ENCRYPTION
# ==========================================
_TIN_KEY = os.environ.get("TIN_ENCRYPTION_KEY", "gigsfill-tin-key-change-in-production-2024")

def _encrypt_tin(tin: str) -> str:
    key = hashlib.sha256(_TIN_KEY.encode()).digest()
    tin_bytes = tin.encode()
    encrypted = bytes(b ^ key[i % len(key)] for i, b in enumerate(tin_bytes))
    return base64.b64encode(encrypted).decode()

def _decrypt_tin(encrypted: str) -> str:
    key = hashlib.sha256(_TIN_KEY.encode()).digest()
    encrypted_bytes = base64.b64decode(encrypted.encode())
    decrypted = bytes(b ^ key[i % len(key)] for i, b in enumerate(encrypted_bytes))
    return decrypted.decode()


def _check_artist_access(artist_id, user_id, db):
    return db.execute(
        text("""SELECT 1 FROM artists a WHERE a.id = :aid AND (a.user_id = :uid
            OR EXISTS (SELECT 1 FROM entity_users eu WHERE eu.entity_type = 'artist' AND eu.entity_id = a.id AND eu.user_id = :uid))"""),
        {"aid": artist_id, "uid": user_id}
    ).first()


def _check_venue_access(venue_id, user_id, db):
    return db.execute(
        text("""SELECT 1 FROM venues v WHERE v.id = :vid AND (v.user_id = :uid
            OR EXISTS (SELECT 1 FROM entity_users eu WHERE eu.entity_type = 'venue' AND eu.entity_id = v.id AND eu.user_id = :uid))"""),
        {"vid": venue_id, "uid": user_id}
    ).first()


# ==========================================
# ARTIST W9 - GET
# ==========================================
@router.get("/api/artists/{artist_id}/w9")
def get_artist_w9(artist_id: int, user=Depends(get_current_user), db=Depends(get_db)):
    if not _check_artist_access(artist_id, user.id, db):
        raise HTTPException(403, "Access denied")
    
    current_year = datetime.now().year
    w9 = db.execute(
        text("SELECT * FROM w9_forms WHERE entity_type = 'artist' AND entity_id = :eid ORDER BY tax_year DESC LIMIT 1"),
        {"eid": artist_id}
    ).mappings().first()
    
    if w9:
        result = dict(w9)
        # Decrypt TIN for the owner to view/edit their own data
        if result.get("tin_encrypted"):
            try:
                result["tin"] = _decrypt_tin(result["tin_encrypted"])
            except Exception:
                result["tin"] = ""
        result.pop("tin_encrypted", None)
        result["needs_recertification"] = (w9["tax_year"] or 0) < current_year
        return result
    else:
        artist = db.execute(text("SELECT name, city, state FROM artists WHERE id = :aid"), {"aid": artist_id}).mappings().first()
        user_data = db.execute(text("SELECT first_name, last_name FROM users WHERE id = :uid"), {"uid": user.id}).mappings().first()
        return {
            "status": "not_filed",
            "prefill": {
                "tax_name": f"{user_data['first_name'] or ''} {user_data['last_name'] or ''}".strip() if user_data else "",
                "city": artist["city"] if artist else "",
                "state": artist["state"] if artist else ""
            }
        }


# ==========================================
# ARTIST W9 - SAVE
# ==========================================
@router.put("/api/artists/{artist_id}/w9")
def save_artist_w9(artist_id: int, data: dict, user=Depends(get_current_user), db=Depends(get_db)):
    if not _check_artist_access(artist_id, user.id, db):
        raise HTTPException(403, "Access denied")
    
    required = ["tax_name", "tax_classification", "tin_type", "tin"]
    for field in required:
        if not data.get(field, "").strip():
            raise HTTPException(400, f"Missing required field: {field}")
    
    tin = data["tin"].strip().replace("-", "").replace(" ", "")
    if len(tin) != 9 or not tin.isdigit():
        raise HTTPException(400, "TIN must be exactly 9 digits")
    if not data.get("certified"):
        raise HTTPException(400, "You must certify the information is correct")
    
    current_year = datetime.now().year
    tin_encrypted = _encrypt_tin(tin)
    tin_last4 = tin[-4:]
    now = utcnow_naive().isoformat()
    
    existing = db.execute(
        text("SELECT id FROM w9_forms WHERE entity_type = 'artist' AND entity_id = :eid AND tax_year = :year"),
        {"eid": artist_id, "year": current_year}
    ).first()
    
    params = {
        "tax_name": data["tax_name"].strip(), "business_name": data.get("business_name", "").strip() or None,
        "tax_classification": data["tax_classification"], "other_classification": data.get("other_classification", "").strip() or None,
        "exempt_payee_code": data.get("exempt_payee_code", "").strip() or None,
        "fatca_exemption_code": data.get("fatca_exemption_code", "").strip() or None,
        "address_line_1": data.get("address_line_1", "").strip() or None, "address_line_2": data.get("address_line_2", "").strip() or None,
        "city": data.get("city", "").strip() or None, "state": data.get("state", "").strip() or None,
        "zip_code": data.get("zip_code", "").strip() or None,
        "tin_type": data["tin_type"], "tin_encrypted": tin_encrypted, "tin_last4": tin_last4,
        "certified_at": now, "updated_at": now
    }
    
    if existing:
        params["id"] = existing[0]
        db.execute(text("""UPDATE w9_forms SET tax_name=:tax_name, business_name=:business_name, tax_classification=:tax_classification,
            other_classification=:other_classification, exempt_payee_code=:exempt_payee_code, fatca_exemption_code=:fatca_exemption_code,
            address_line_1=:address_line_1, address_line_2=:address_line_2, city=:city, state=:state, zip_code=:zip_code,
            tin_type=:tin_type, tin_encrypted=:tin_encrypted, tin_last4=:tin_last4, certified_at=:certified_at, updated_at=:updated_at
            WHERE id=:id"""), params)
    else:
        params["entity_id"] = artist_id
        params["tax_year"] = current_year
        db.execute(text("""INSERT INTO w9_forms (entity_type, entity_id, tax_name, business_name, tax_classification,
            other_classification, exempt_payee_code, fatca_exemption_code, address_line_1, address_line_2,
            city, state, zip_code, tin_type, tin_encrypted, tin_last4, certified_at, tax_year, updated_at)
            VALUES ('artist', :entity_id, :tax_name, :business_name, :tax_classification,
            :other_classification, :exempt_payee_code, :fatca_exemption_code, :address_line_1, :address_line_2,
            :city, :state, :zip_code, :tin_type, :tin_encrypted, :tin_last4, :certified_at, :tax_year, :updated_at)"""), params)
    
    db.commit()
    return {"status": "saved", "tax_year": current_year, "tin_last4": tin_last4}


# ==========================================
# ARTIST W9 - RECERTIFY
# ==========================================
@router.post("/api/artists/{artist_id}/w9/recertify")
def recertify_artist_w9(artist_id: int, user=Depends(get_current_user), db=Depends(get_db)):
    if not _check_artist_access(artist_id, user.id, db):
        raise HTTPException(403, "Access denied")
    
    current_year = datetime.now().year
    latest = db.execute(
        text("SELECT * FROM w9_forms WHERE entity_type = 'artist' AND entity_id = :eid ORDER BY tax_year DESC LIMIT 1"),
        {"eid": artist_id}
    ).mappings().first()
    
    if not latest:
        raise HTTPException(404, "No existing W9 to recertify")
    if latest["tax_year"] == current_year:
        return {"status": "already_current", "tax_year": current_year}
    
    now = utcnow_naive().isoformat()
    db.execute(text("""INSERT OR REPLACE INTO w9_forms (entity_type, entity_id, tax_name, business_name, tax_classification,
        other_classification, exempt_payee_code, fatca_exemption_code, address_line_1, address_line_2,
        city, state, zip_code, tin_type, tin_encrypted, tin_last4, certified_at, tax_year, updated_at)
        VALUES ('artist', :eid, :tax_name, :business_name, :tax_classification, :other_classification,
        :exempt_payee_code, :fatca_exemption_code, :address_line_1, :address_line_2, :city, :state,
        :zip_code, :tin_type, :tin_encrypted, :tin_last4, :certified_at, :tax_year, :updated_at)"""), {
        "eid": artist_id, "tax_name": latest["tax_name"], "business_name": latest["business_name"],
        "tax_classification": latest["tax_classification"], "other_classification": latest["other_classification"],
        "exempt_payee_code": latest["exempt_payee_code"], "fatca_exemption_code": latest["fatca_exemption_code"],
        "address_line_1": latest["address_line_1"], "address_line_2": latest["address_line_2"],
        "city": latest["city"], "state": latest["state"], "zip_code": latest["zip_code"],
        "tin_type": latest["tin_type"], "tin_encrypted": latest["tin_encrypted"], "tin_last4": latest["tin_last4"],
        "certified_at": now, "tax_year": current_year, "updated_at": now
    })
    db.commit()
    return {"status": "recertified", "tax_year": current_year}


# ==========================================
# ARTIST W9 STATUS CHECK (used by booking)
# ==========================================
@router.get("/api/artists/{artist_id}/w9-status")
def get_artist_w9_status(artist_id: int, user=Depends(get_current_user), db=Depends(get_db)):
    # Audit fix (May 2026): was unauthenticated — any visitor could probe
    # whether an artist has filed a W9. Now require authentication; the
    # endpoint is consumed by venue-side W9-required gates and the artist's
    # own profile, both of which are logged-in flows.
    current_year = datetime.now().year
    w9 = db.execute(
        text("SELECT tax_year FROM w9_forms WHERE entity_type = 'artist' AND entity_id = :eid ORDER BY tax_year DESC LIMIT 1"),
        {"eid": artist_id}
    ).first()
    if not w9:
        return {"has_w9": False, "current_year": False}
    return {"has_w9": True, "current_year": w9[0] == current_year}


# ==========================================
# VENUE TAX SETTINGS - GET
# ==========================================
@router.get("/api/venues/{venue_id}/tax-settings")
def get_venue_tax_settings(venue_id: int, user=Depends(get_current_user), db=Depends(get_db)):
    if not _check_venue_access(venue_id, user.id, db):
        raise HTTPException(403, "Access denied")
    settings = db.execute(text("SELECT * FROM venue_tax_settings WHERE venue_id = :vid"), {"vid": venue_id}).mappings().first()
    return dict(settings) if settings else {"venue_id": venue_id, "require_w9": 0}


# ==========================================
# VENUE TAX SETTINGS - UPDATE
# ==========================================
@router.put("/api/venues/{venue_id}/tax-settings")
def update_venue_tax_settings(venue_id: int, data: dict, user=Depends(get_current_user), db=Depends(get_db)):
    if not _check_venue_access(venue_id, user.id, db):
        raise HTTPException(403, "Access denied")
    
    require_w9 = 1 if data.get("require_w9") else 0
    now = utcnow_naive().isoformat()
    existing = db.execute(text("SELECT id FROM venue_tax_settings WHERE venue_id = :vid"), {"vid": venue_id}).first()
    if existing:
        db.execute(text("UPDATE venue_tax_settings SET require_w9 = :rw, updated_at = :now WHERE venue_id = :vid"),
                   {"rw": require_w9, "now": now, "vid": venue_id})
    else:
        db.execute(text("INSERT INTO venue_tax_settings (venue_id, require_w9, updated_at) VALUES (:vid, :rw, :now)"),
                   {"vid": venue_id, "rw": require_w9, "now": now})
    db.commit()
    return {"status": "saved", "require_w9": require_w9}


# ==========================================
# VENUE W9 REQUIREMENT CHECK (booking flow)
# ==========================================
@router.get("/api/venues/{venue_id}/requires-w9")
def venue_requires_w9(venue_id: int, db=Depends(get_db)):
    settings = db.execute(text("SELECT require_w9 FROM venue_tax_settings WHERE venue_id = :vid"), {"vid": venue_id}).first()
    return {"requires_w9": bool(settings and settings[0])}


# ==========================================
# 1099 GENERATION
# ==========================================
@router.post("/api/venues/{venue_id}/generate-1099s")
def generate_1099s(venue_id: int, data: dict, user=Depends(get_current_user), db=Depends(get_db)):
    if not _check_venue_access(venue_id, user.id, db):
        raise HTTPException(403, "Access denied")
    
    tax_year = data.get("tax_year", datetime.now().year - 1)
    
    venue = db.execute(text("SELECT name, city, state FROM venues WHERE id = :vid"), {"vid": venue_id}).mappings().first()
    if not venue:
        raise HTTPException(404, "Venue not found")
    
    venue_w9 = db.execute(
        text("SELECT tin_last4, address_line_1, city, state, zip_code FROM w9_forms WHERE entity_type = 'venue' AND entity_id = :vid ORDER BY tax_year DESC LIMIT 1"),
        {"vid": venue_id}
    ).mappings().first()
    
    venue_address = ""
    venue_tin_last4 = ""
    if venue_w9:
        venue_address = ", ".join(filter(None, [venue_w9["address_line_1"], venue_w9["city"], venue_w9["state"], venue_w9["zip_code"]]))
        venue_tin_last4 = venue_w9["tin_last4"] or ""
    
    # 1099 earnings = actual paid-out amount per artist for this venue this tax year.
    # Sum from `transactions` (the source of truth for money movement) — NOT
    # `gigs.pay` which is the listed pay and ignores per-artist overrides
    # plus completely missed multi-slot gigs (where g.artist_id IS NULL and
    # the booked artist lives on `gig_slots.artist_id`). Uses
    #   - transaction_type IN ('artist_payout', 'single'): the new model
    #     per-slot child rows + the legacy combined rows from before May 2026
    #   - status='paid': the row actually transferred to the artist's Stripe
    #     account; 'scheduled' rows haven't fired yet, 'pending_transfer' is
    #     awaiting retry, and we don't 1099 either of those
    # Filtering by `g.date` year matches the calendar-year basis the IRS expects.
    # COUNT(DISTINCT t.gig_id) so a multi-slot gig where one artist booked two
    # slots counts as one gig — not two — for the gig_count column.
    # 60000 cents = $600 threshold (IRS 1099-NEC reporting floor).
    earnings = db.execute(text("""
        SELECT t.artist_id, a.name as artist_name,
               SUM(t.amount_cents) as total_cents,
               COUNT(DISTINCT t.gig_id) as gig_count
        FROM transactions t
        JOIN artists a ON a.id = t.artist_id
        JOIN gigs g ON g.id = t.gig_id
        WHERE g.venue_id = :vid
          AND t.transaction_type IN ('artist_payout', 'single')
          AND t.status = 'paid'
          AND t.artist_id IS NOT NULL
          AND substr(g.date, 1, 4) = :year
        GROUP BY t.artist_id, a.name
        HAVING total_cents >= 60000
    """), {"vid": venue_id, "year": str(tax_year)}).mappings().fetchall()

    generated = []
    for e in earnings:
        artist_w9 = db.execute(
            text("SELECT tin_last4, address_line_1, city, state, zip_code FROM w9_forms WHERE entity_type = 'artist' AND entity_id = :aid ORDER BY tax_year DESC LIMIT 1"),
            {"aid": e["artist_id"]}
        ).mappings().first()

        artist_address = ""
        artist_tin_last4 = ""
        if artist_w9:
            artist_address = ", ".join(filter(None, [artist_w9["address_line_1"], artist_w9["city"], artist_w9["state"], artist_w9["zip_code"]]))
            artist_tin_last4 = artist_w9["tin_last4"] or ""

        # total_cents already in cents — store as-is, NO rounding/truncation.
        # The previous code did `int(total_pay) * 100` which lost any cents.
        earnings_cents = int(e["total_cents"])

        db.execute(text("""
            INSERT INTO tax_1099s (venue_id, artist_id, tax_year, total_earnings_cents, gig_count,
                artist_name, artist_tin_last4, artist_address, venue_name, venue_address, venue_tin_last4, status)
            VALUES (:vid, :aid, :year, :earnings, :gigs, :aname, :atin, :aaddr, :vname, :vaddr, :vtin, 'generated')
            ON CONFLICT(venue_id, artist_id, tax_year) DO UPDATE SET
                total_earnings_cents = :earnings, gig_count = :gigs, artist_name = :aname,
                artist_tin_last4 = :atin, artist_address = :aaddr, venue_name = :vname,
                venue_address = :vaddr, venue_tin_last4 = :vtin,
                status = CASE WHEN tax_1099s.status = 'sent' THEN 'sent' ELSE 'generated' END
        """), {
            "vid": venue_id, "aid": e["artist_id"], "year": tax_year,
            "earnings": earnings_cents, "gigs": e["gig_count"], "aname": e["artist_name"],
            "atin": artist_tin_last4, "aaddr": artist_address,
            "vname": venue["name"], "vaddr": venue_address, "vtin": venue_tin_last4
        })
        generated.append({"artist_id": e["artist_id"], "artist_name": e["artist_name"], "total_cents": earnings_cents})
    
    db.commit()
    return {"status": "generated", "tax_year": tax_year, "count": len(generated), "artists": generated}


# ==========================================
# 1099 LIST
# ==========================================
@router.get("/api/venues/{venue_id}/1099s")
def get_venue_1099s(venue_id: int, tax_year: int = None, user=Depends(get_current_user), db=Depends(get_db)):
    if not _check_venue_access(venue_id, user.id, db):
        raise HTTPException(403, "Access denied")
    if not tax_year:
        tax_year = datetime.now().year - 1
    records = db.execute(text("""
        SELECT t.* FROM tax_1099s t WHERE t.venue_id = :vid AND t.tax_year = :year ORDER BY t.artist_name
    """), {"vid": venue_id, "year": tax_year}).mappings().fetchall()
    return {"tax_year": tax_year, "records": [dict(r) for r in records]}


# ==========================================
# VIEW 1099 DETAIL
# ==========================================
@router.get("/api/venues/{venue_id}/1099s/{record_id}")
def get_1099_detail(venue_id: int, record_id: int, user=Depends(get_current_user), db=Depends(get_db)):
    if not _check_venue_access(venue_id, user.id, db):
        raise HTTPException(403, "Access denied")
    record = db.execute(text("SELECT * FROM tax_1099s WHERE id = :rid AND venue_id = :vid"), {"rid": record_id, "vid": venue_id}).mappings().first()
    if not record:
        raise HTTPException(404, "1099 not found")
    return dict(record)


# ==========================================
# SEND 1099
# ==========================================
@router.post("/api/venues/{venue_id}/1099s/{record_id}/send")
def send_1099(venue_id: int, record_id: int, user=Depends(get_current_user), db=Depends(get_db)):
    if not _check_venue_access(venue_id, user.id, db):
        raise HTTPException(403, "Access denied")
    
    record = db.execute(text("SELECT * FROM tax_1099s WHERE id = :rid AND venue_id = :vid"), {"rid": record_id, "vid": venue_id}).mappings().first()
    if not record:
        raise HTTPException(404, "1099 not found")
    
    artist_user = db.execute(text("""
        SELECT u.id as user_id, u.email, a.name as artist_name
        FROM artists a JOIN users u ON u.id = a.user_id WHERE a.id = :aid
    """), {"aid": record["artist_id"]}).mappings().first()
    
    if not artist_user:
        raise HTTPException(404, "Artist user not found")
    
    earnings_dollars = record["total_earnings_cents"] / 100
    now = utcnow_naive().isoformat()
    
    # Send email
    try:
        from backend.email_service import EmailService
        email_service = EmailService(db)
        if email_service.enabled and artist_user.get("email"):
            import smtplib
            from email.mime.multipart import MIMEMultipart
            from email.mime.text import MIMEText
            
            subject = f"Your 1099-NEC from {record['venue_name']} for Tax Year {record['tax_year']}"
            body = f"""<div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
<h2 style="color: #333;">1099-NEC Tax Document</h2>
<p>Hi {artist_user['artist_name']},</p>
<p>{record['venue_name']} has issued you a 1099-NEC for tax year {record['tax_year']}.</p>
<div style="background: #f5f5f5; border: 1px solid #ddd; border-radius: 8px; padding: 20px; margin: 20px 0;">
<h3 style="margin-top: 0; color: #333;">Form 1099-NEC &mdash; Nonemployee Compensation</h3>
<table style="width: 100%; border-collapse: collapse;">
<tr><td style="padding: 8px 0; color: #666;">Payer (Venue):</td><td style="padding: 8px 0; font-weight: bold;">{record['venue_name']}</td></tr>
<tr><td style="padding: 8px 0; color: #666;">Payer TIN:</td><td style="padding: 8px 0;">***-**-{record['venue_tin_last4'] or 'N/A'}</td></tr>
<tr><td style="padding: 8px 0; color: #666;">Recipient:</td><td style="padding: 8px 0; font-weight: bold;">{record['artist_name']}</td></tr>
<tr><td style="padding: 8px 0; color: #666;">Recipient TIN:</td><td style="padding: 8px 0;">***-**-{record['artist_tin_last4'] or 'N/A'}</td></tr>
<tr><td style="padding: 8px 0; color: #666;">Tax Year:</td><td style="padding: 8px 0; font-weight: bold;">{record['tax_year']}</td></tr>
<tr><td style="padding: 8px 0; color: #666;">Gigs Performed:</td><td style="padding: 8px 0;">{record['gig_count']}</td></tr>
<tr style="border-top: 2px solid #333;"><td style="padding: 12px 0; font-weight: bold; font-size: 1.1em;">Box 1 &mdash; Nonemployee Compensation:</td>
<td style="padding: 12px 0; font-weight: bold; font-size: 1.2em; color: #333;">${earnings_dollars:,.2f}</td></tr>
</table></div>
<p style="color: #666; font-size: 0.9em;">This is an important tax document. Please retain for your records.</p>
<p style="color: #666; font-size: 0.9em;">View this 1099 in your GigsFill account under the Taxes tab.</p>
<p style="margin-top: 24px;">&mdash; GigsFill</p></div>"""
            
            msg = MIMEMultipart()
            if email_service.from_name:
                from email.utils import formataddr
                msg['From'] = formataddr((email_service.from_name, email_service.from_email))
            else:
                msg['From'] = email_service.from_email
            msg['To'] = artist_user["email"]
            msg['Subject'] = subject
            msg.attach(MIMEText(body, 'html'))
            
            if email_service.smtp_port == 465:
                with smtplib.SMTP_SSL(email_service.smtp_server, email_service.smtp_port, timeout=15) as server:
                    server.login(email_service.smtp_username, email_service.smtp_password)
                    server.send_message(msg)
            else:
                with smtplib.SMTP(email_service.smtp_server, email_service.smtp_port, timeout=15) as server:
                    server.starttls()
                    server.login(email_service.smtp_username, email_service.smtp_password)
                    server.send_message(msg)
    except Exception:
        pass
    
    # Notification for artist (and all secondary entity_users on the artist).
    # Audit fix (May 2026): two bugs fixed in one pass.
    #   1. Column was `type` — actual schema is `notification_type`. Wrapped in
    #      try/except so it failed silently — venue saw "Send 1099" succeed
    #      but artists never saw the notification.
    #   2. Notified only `artist.user_id`. Multi-user artist co-managers
    #      missed the notification entirely. Fan out via get_all_entity_users.
    try:
        from backend.utils import get_all_entity_users
        from backend.services.notification_service import create_notification
        msg = f"Your 1099-NEC from {record['venue_name']} for {record['tax_year']} is ready. Total earnings: ${earnings_dollars:,.2f}"
        for u in get_all_entity_users(db, "artist", record["artist_id"]):
            create_notification(
                db, u["user_id"], "tax_1099", "1099-NEC Ready", msg,
                artist_id=record["artist_id"], venue_id=record["venue_id"]
            )
    except Exception as _ne:
        import logging
        logging.getLogger("gigsfill.tax").warning(f"1099 notification fanout error: {_ne}")
    
    db.execute(text("UPDATE tax_1099s SET status = 'sent', sent_at = :now WHERE id = :rid"), {"now": now, "rid": record_id})
    db.commit()
    return {"status": "sent", "artist_name": artist_user["artist_name"]}


# ==========================================
# SEND ALL 1099s
# ==========================================
@router.post("/api/venues/{venue_id}/1099s/send-all")
def send_all_1099s(venue_id: int, data: dict, user=Depends(get_current_user), db=Depends(get_db)):
    if not _check_venue_access(venue_id, user.id, db):
        raise HTTPException(403, "Access denied")
    
    tax_year = data.get("tax_year", datetime.now().year - 1)
    records = db.execute(
        text("SELECT id FROM tax_1099s WHERE venue_id = :vid AND tax_year = :year AND status != 'sent'"),
        {"vid": venue_id, "year": tax_year}
    ).fetchall()
    
    sent_count = 0
    for rec in records:
        try:
            send_1099(venue_id, rec[0], user, db)
            sent_count += 1
        except Exception:
            pass
    
    return {"status": "complete", "sent_count": sent_count, "total": len(records)}


# ==========================================
# ARTIST VIEW THEIR 1099s
# ==========================================
@router.get("/api/artists/{artist_id}/1099s")
def get_artist_1099s(artist_id: int, user=Depends(get_current_user), db=Depends(get_db)):
    if not _check_artist_access(artist_id, user.id, db):
        raise HTTPException(403, "Access denied")
    records = db.execute(text("""
        SELECT * FROM tax_1099s WHERE artist_id = :aid AND status = 'sent' ORDER BY tax_year DESC, venue_name
    """), {"aid": artist_id}).mappings().fetchall()
    return {"records": [dict(r) for r in records]}


# ==========================================
# USER (AFFILIATE) W9 — reuses entity_type='user'
# ==========================================

@router.get("/api/users/{user_id}/w9")
def get_user_w9(user_id: int, user=Depends(get_current_user), db=Depends(get_db)):
    """Get current user's W9 for affiliate payouts."""
    if user.id != user_id:
        _check_admin(user)
    year = datetime.now().year
    row = db.execute(text("""
        SELECT * FROM w9_forms WHERE entity_type = 'user' AND entity_id = :uid ORDER BY tax_year DESC LIMIT 1
    """), {"uid": user_id}).mappings().first()
    if not row:
        return {"w9": None, "tax_year": year}
    return {"w9": {
        "tax_name": row["tax_name"], "business_name": row["business_name"],
        "tax_classification": row["tax_classification"], "address_line_1": row["address_line_1"],
        "address_line_2": row["address_line_2"], "city": row["city"], "state": row["state"],
        "zip_code": row["zip_code"], "tin_type": row["tin_type"], "tin_last4": row["tin_last4"],
        "certified_at": row["certified_at"], "tax_year": row["tax_year"],
    }, "tax_year": row["tax_year"]}


@router.put("/api/users/{user_id}/w9")
def save_user_w9(user_id: int, data: dict, user=Depends(get_current_user), db=Depends(get_db)):
    """Save W9 info for a user (affiliate payouts)."""
    if user.id != user_id:
        _check_admin(user)
    year = datetime.now().year
    tin_raw = (data.get("tin") or "").replace("-", "").replace(" ", "")
    if not tin_raw:
        raise HTTPException(400, "TIN required")
    tin_enc  = _encrypt_tin(tin_raw)
    tin_last4 = tin_raw[-4:]

    existing = db.execute(text(
        "SELECT id FROM w9_forms WHERE entity_type = 'user' AND entity_id = :uid AND tax_year = :yr"
    ), {"uid": user_id, "yr": year}).first()

    if existing:
        db.execute(text("""
            UPDATE w9_forms SET
                tax_name = :tax_name, business_name = :business_name,
                tax_classification = :tc, other_classification = :oc,
                address_line_1 = :a1, address_line_2 = :a2, city = :city,
                state = :state, zip_code = :zip,
                tin_type = :tt, tin_encrypted = :te, tin_last4 = :tl4,
                certified_at = :cat, updated_at = CURRENT_TIMESTAMP
            WHERE entity_type = 'user' AND entity_id = :uid AND tax_year = :yr
        """), {"tax_name": data.get("tax_name"), "business_name": data.get("business_name"),
               "tc": data.get("tax_classification"), "oc": data.get("other_classification"),
               "a1": data.get("address_line_1"), "a2": data.get("address_line_2"),
               "city": data.get("city"), "state": data.get("state"), "zip": data.get("zip_code"),
               "tt": data.get("tin_type", "ssn"), "te": tin_enc, "tl4": tin_last4,
               "cat": data.get("certified_at"), "uid": user_id, "yr": year})
    else:
        db.execute(text("""
            INSERT INTO w9_forms
                (entity_type, entity_id, tax_name, business_name, tax_classification, other_classification,
                 address_line_1, address_line_2, city, state, zip_code, tin_type, tin_encrypted, tin_last4,
                 certified_at, tax_year)
            VALUES ('user', :uid, :tax_name, :business_name, :tc, :oc, :a1, :a2, :city, :state, :zip,
                    :tt, :te, :tl4, :cat, :yr)
        """), {"uid": user_id, "tax_name": data.get("tax_name"), "business_name": data.get("business_name"),
               "tc": data.get("tax_classification"), "oc": data.get("other_classification"),
               "a1": data.get("address_line_1"), "a2": data.get("address_line_2"),
               "city": data.get("city"), "state": data.get("state"), "zip": data.get("zip_code"),
               "tt": data.get("tin_type", "ssn"), "te": tin_enc, "tl4": tin_last4,
               "cat": data.get("certified_at"), "yr": year})
    db.commit()
    return {"ok": True}


# ==========================================
# AFFILIATE 1099 GENERATION (end-of-year admin tool)
# ==========================================

def _check_admin(user):
    # Audit fix (May 2026): centralized via to_admin_bool helper.
    from backend.utils import to_admin_bool
    if not to_admin_bool(getattr(user, "is_admin", None)):
        raise HTTPException(403, "Admin only")


@router.post("/api/admin/affiliate/generate-1099s")
async def generate_affiliate_1099s(request: Request, user=Depends(get_current_user), db=Depends(get_db)):
    """
    Generate affiliate 1099s for users who earned over the threshold in a given calendar year.
    Called manually by admin at year-end (or automatically December 31).
    """
    _check_admin(user)
    data = await request.json()
    tax_year = int(data.get("tax_year", datetime.now().year))

    threshold_cents = int(db.execute(text(
        "SELECT COALESCE(setting_value, '60000') FROM platform_settings WHERE setting_key = 'affiliate_1099_threshold_cents'"
    )).scalar() or 60000)

    # Find all affiliates with paid earnings over threshold for the year
    affiliates = db.execute(text("""
        SELECT
            ae.affiliate_user_id,
            u.first_name, u.last_name, u.email,
            SUM(ae.earned_cents) as total_cents,
            COUNT(ae.id) as txn_count
        FROM affiliate_earnings ae
        JOIN affiliate_payouts ap ON ap.id = ae.payout_id
        JOIN users u ON u.id = ae.affiliate_user_id
        WHERE ap.status = 'paid'
          AND strftime('%Y', ap.paid_at) = :yr
        GROUP BY ae.affiliate_user_id
        HAVING SUM(ae.earned_cents) >= :threshold
    """), {"yr": str(tax_year), "threshold": threshold_cents}).mappings().all()

    generated = []
    for aff in affiliates:
        uid = aff["affiliate_user_id"]
        # Get W9
        w9 = db.execute(text("""
            SELECT * FROM w9_forms WHERE entity_type = 'user' AND entity_id = :uid
            ORDER BY tax_year DESC LIMIT 1
        """), {"uid": uid}).mappings().first()

        record = {
            "user_id": uid,
            "tax_year": tax_year,
            "full_name": f"{aff['first_name'] or ''} {aff['last_name'] or ''}".strip() or aff["email"],
            "email": aff["email"],
            "total_earned_cents": aff["total_cents"],
            "txn_count": aff["txn_count"],
            "has_w9": bool(w9),
            "tin_last4": w9["tin_last4"] if w9 else None,
            "address": f"{w9['address_line_1'] or ''}, {w9['city'] or ''}, {w9['state'] or ''} {w9['zip_code'] or ''}".strip(", ") if w9 else None,
        }
        generated.append(record)

    return {"tax_year": tax_year, "threshold_dollars": threshold_cents / 100, "records": generated, "count": len(generated)}


@router.get("/api/users/{user_id}/affiliate-1099s")
def get_user_affiliate_1099s(user_id: int, user=Depends(get_current_user), db=Depends(get_db)):
    """User views their own affiliate 1099 history."""
    if user.id != user_id:
        _check_admin(user)

    from datetime import datetime
    year = datetime.now().year

    # Build year-by-year summaries from paid earnings
    rows = db.execute(text("""
        SELECT
            strftime('%Y', ap.paid_at) as tax_year,
            SUM(ae.earned_cents) as total_cents,
            COUNT(ae.id) as txn_count
        FROM affiliate_earnings ae
        JOIN affiliate_payouts ap ON ap.id = ae.payout_id
        WHERE ae.affiliate_user_id = :uid AND ap.status = 'paid'
        GROUP BY tax_year
        ORDER BY tax_year DESC
    """), {"uid": user_id}).mappings().all()

    threshold = int(db.execute(text(
        "SELECT COALESCE(setting_value,'60000') FROM platform_settings WHERE setting_key='affiliate_1099_threshold_cents'"
    )).scalar() or 60000)

    return {"records": [dict(r) for r in rows], "threshold_cents": threshold}
