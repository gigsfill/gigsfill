"""
Entity Users Routes
Handles multi-user access for Artists and Venues
"""
from fastapi import APIRouter, Depends, HTTPException, Query
import logging
from sqlalchemy import text
from datetime import datetime
from backend.utils import utcnow_naive
from typing import Optional
import secrets
import json

from backend.db import get_db
from backend.routes.auth import get_current_user
from backend.email_service import EmailService
logger = logging.getLogger("gigsfill.entity_users")

router = APIRouter()

# ============================================
# GET USERS FOR AN ENTITY
# ============================================
@router.get("/api/entity-users/artist/{artist_id}")
def get_artist_users(
    artist_id: int,
    user=Depends(get_current_user),
    db=Depends(get_db)
):
    """Get all users with access to this artist"""
    
    # Get artist owner
    artist_owner = db.execute(
        text("SELECT user_id FROM artists WHERE id = :aid"),
        {"aid": artist_id}
    ).scalar()
    
    if not artist_owner:
        raise HTTPException(404, "Artist not found")
    
    # Verify user has access to this artist
    access_check = db.execute(
        text("""
            SELECT 1 FROM entity_users 
            WHERE entity_type = 'artist' AND entity_id = :aid AND user_id = :uid
        """),
        {"aid": artist_id, "uid": user.id}
    ).scalar()
    
    # Check if user is owner or has entity_users access
    if not access_check and artist_owner != user.id:
        raise HTTPException(403, "You don't have access to this artist")
    
    # Get all users with access from entity_users
    users = db.execute(
        text("""
            SELECT 
                eu.id as entity_user_id,
                eu.user_id,
                eu.role,
                eu.created_at,
                u.first_name,
                u.last_name,
                u.email,
                u.phone
            FROM entity_users eu
            JOIN users u ON eu.user_id = u.id
            WHERE eu.entity_type = 'artist' AND eu.entity_id = :aid
            ORDER BY eu.created_at ASC
        """),
        {"aid": artist_id}
    ).mappings().all()
    
    users_list = [dict(u) for u in users]
    
    # Check if owner is already in the list
    owner_in_list = any(u['user_id'] == artist_owner for u in users_list)
    
    # If owner not in entity_users, add them (backwards compatibility for legacy artists)
    if not owner_in_list:
        owner_info = db.execute(
            text("""
                SELECT id as user_id, first_name, last_name, email, phone
                FROM users WHERE id = :uid
            """),
            {"uid": artist_owner}
        ).mappings().first()
        
        if owner_info:
            users_list.insert(0, {
                "entity_user_id": None,
                "user_id": owner_info['user_id'],
                "role": "owner",
                "created_at": None,
                "first_name": owner_info['first_name'],
                "last_name": owner_info['last_name'],
                "email": owner_info['email'],
                "phone": owner_info['phone']
            })
    
    # Also include pending/declined invitations
    invitations = db.execute(
        text("""
            SELECT ei.id, ei.invited_email, ei.status, ei.token, ei.created_at,
                   u.first_name, u.last_name, u.phone
            FROM entity_invitations ei
            LEFT JOIN users u ON LOWER(u.email) = LOWER(ei.invited_email)
            WHERE ei.entity_type = 'artist' AND ei.entity_id = :aid
            AND ei.status IN ('pending', 'declined')
            ORDER BY ei.created_at DESC
        """),
        {"aid": artist_id}
    ).mappings().all()
    
    for inv in invitations:
        # Don't duplicate if user already accepted and is in the list
        if not any(u.get('email', '').lower() == inv['invited_email'].lower() for u in users_list):
            users_list.append({
                "entity_user_id": None,
                "user_id": None,
                "role": inv['status'],  # 'pending' or 'declined'
                "created_at": inv['created_at'],
                "first_name": inv['first_name'],
                "last_name":  inv['last_name'],
                "email": inv['invited_email'],
                "phone": inv['phone'],
                "invitation_id": inv['id'],
                "invitation_token": inv['token']
            })
    
    return users_list

@router.get("/api/entity-users/venue/{venue_id}")
def get_venue_users(
    venue_id: int,
    user=Depends(get_current_user),
    db=Depends(get_db)
):
    """Get all users with access to this venue"""
    
    # Get venue owner
    venue_owner = db.execute(
        text("SELECT user_id FROM venues WHERE id = :vid"),
        {"vid": venue_id}
    ).scalar()
    
    if not venue_owner:
        raise HTTPException(404, "Venue not found")
    
    # Verify user has access to this venue
    access_check = db.execute(
        text("""
            SELECT 1 FROM entity_users 
            WHERE entity_type = 'venue' AND entity_id = :vid AND user_id = :uid
        """),
        {"vid": venue_id, "uid": user.id}
    ).scalar()
    
    # Check if user is owner or has entity_users access
    if not access_check and venue_owner != user.id:
        raise HTTPException(403, "You don't have access to this venue")
    
    # Get all users with access from entity_users
    users = db.execute(
        text("""
            SELECT 
                eu.id as entity_user_id,
                eu.user_id,
                eu.role,
                eu.created_at,
                u.first_name,
                u.last_name,
                u.email,
                u.phone
            FROM entity_users eu
            JOIN users u ON eu.user_id = u.id
            WHERE eu.entity_type = 'venue' AND eu.entity_id = :vid
            ORDER BY eu.created_at ASC
        """),
        {"vid": venue_id}
    ).mappings().all()
    
    users_list = [dict(u) for u in users]
    
    # Check if owner is already in the list
    owner_in_list = any(u['user_id'] == venue_owner for u in users_list)
    
    # If owner not in entity_users, add them (backwards compatibility for legacy venues)
    if not owner_in_list:
        owner_info = db.execute(
            text("""
                SELECT id as user_id, first_name, last_name, email, phone
                FROM users WHERE id = :uid
            """),
            {"uid": venue_owner}
        ).mappings().first()
        
        if owner_info:
            users_list.insert(0, {
                "entity_user_id": None,
                "user_id": owner_info['user_id'],
                "role": "owner",
                "created_at": None,
                "first_name": owner_info['first_name'],
                "last_name": owner_info['last_name'],
                "email": owner_info['email'],
                "phone": owner_info['phone']
            })
    
    # Also include pending/declined invitations
    invitations = db.execute(
        text("""
            SELECT ei.id, ei.invited_email, ei.status, ei.token, ei.created_at,
                   u.first_name, u.last_name, u.phone
            FROM entity_invitations ei
            LEFT JOIN users u ON LOWER(u.email) = LOWER(ei.invited_email)
            WHERE ei.entity_type = 'venue' AND ei.entity_id = :vid
            AND ei.status IN ('pending', 'declined')
            ORDER BY ei.created_at DESC
        """),
        {"vid": venue_id}
    ).mappings().all()
    
    for inv in invitations:
        if not any(u.get('email', '').lower() == inv['invited_email'].lower() for u in users_list):
            users_list.append({
                "entity_user_id": None,
                "user_id": None,
                "role": inv['status'],
                "created_at": inv['created_at'],
                "first_name": inv['first_name'],
                "last_name":  inv['last_name'],
                "email": inv['invited_email'],
                "phone": inv['phone'],
                "invitation_id": inv['id'],
                "invitation_token": inv['token']
            })
    
    return users_list

# ============================================
# LOOKUP USER BY EMAIL (for invite pre-fill)
# ============================================
@router.get("/api/users/lookup-by-email")
def lookup_user_by_email(
    email: str,
    user=Depends(get_current_user),
    db=Depends(get_db)
):
    """Check if an email belongs to an existing GigsFill user and return safe public fields."""
    if not email or '@' not in email:
        raise HTTPException(400, "Valid email required")
    found = db.execute(
        text("SELECT first_name, last_name, phone FROM users WHERE LOWER(email) = :email"),
        {"email": email.strip().lower()}
    ).mappings().first()
    if not found:
        return {"found": False}
    return {
        "found": True,
        "first_name": found["first_name"] or "",
        "last_name":  found["last_name"]  or "",
        "phone":      found["phone"]      or "",
    }


# ============================================
# INVITE USER TO ENTITY
# ============================================
@router.post("/api/entity-users/artist/{artist_id}/invite")
async def invite_user_to_artist(
    artist_id: int,
    data: dict,
    user=Depends(get_current_user),
    db=Depends(get_db)
):
    """Invite a user to have access to this artist"""
    return await _invite_user_to_entity('artist', artist_id, data, user, db)

@router.post("/api/entity-users/venue/{venue_id}/invite")
async def invite_user_to_venue(
    venue_id: int,
    data: dict,
    user=Depends(get_current_user),
    db=Depends(get_db)
):
    """Invite a user to have access to this venue"""
    return await _invite_user_to_entity('venue', venue_id, data, user, db)

@router.post("/api/entity-invitations/{invitation_id}/reinvite")
async def reinvite_user(
    invitation_id: int,
    user=Depends(get_current_user),
    db=Depends(get_db)
):
    """Re-send an invitation (for pending or declined invitations)"""
    # Get the invitation
    invitation = db.execute(
        text("SELECT * FROM entity_invitations WHERE id = :iid"),
        {"iid": invitation_id}
    ).mappings().first()
    
    if not invitation:
        raise HTTPException(404, "Invitation not found")
    
    # Verify user has access to this entity
    entity_type = invitation['entity_type']
    entity_id = invitation['entity_id']
    
    # Safe table selection — never interpolate user input into SQL identifiers
    if entity_type == 'artist':
        owner_id = db.execute(
            text("SELECT user_id FROM artists WHERE id = :eid"),
            {"eid": entity_id}
        ).scalar()
    elif entity_type == 'venue':
        owner_id = db.execute(
            text("SELECT user_id FROM venues WHERE id = :eid"),
            {"eid": entity_id}
        ).scalar()
    else:
        raise HTTPException(400, "Invalid entity type")
    
    access = db.execute(
        text("SELECT 1 FROM entity_users WHERE entity_type = :etype AND entity_id = :eid AND user_id = :uid"),
        {"etype": entity_type, "eid": entity_id, "uid": user.id}
    ).scalar()
    
    if not access and owner_id != user.id:
        raise HTTPException(403, "Access denied")
    
    # Generate new token
    new_token = secrets.token_urlsafe(32)
    
    # Update invitation: reset to pending, new token
    db.execute(
        text("""
            UPDATE entity_invitations 
            SET status = 'pending', token = :token, responded_at = NULL, created_at = CURRENT_TIMESTAMP
            WHERE id = :iid
        """),
        {"token": new_token, "iid": invitation_id}
    )
    db.commit()
    
    # Get inviter info
    inviter = db.execute(
        text("SELECT first_name, last_name FROM users WHERE id = :uid"),
        {"uid": user.id}
    ).mappings().first()
    
    inviter_name = f"{inviter['first_name']} {inviter['last_name']}"
    
    # Send email using template
    email_service = EmailService(db)
    _send_invitation_email(email_service, invitation['invited_email'], inviter_name, invitation['entity_name'], entity_type, new_token)
    
    # Also update notification for existing users
    existing_user = db.execute(
        text("SELECT id FROM users WHERE LOWER(email) = LOWER(:email)"),
        {"email": invitation['invited_email']}
    ).scalar()
    
    if existing_user:
        inviter_name = f"{inviter['first_name']} {inviter['last_name']}"
        try:
            db.execute(
                text("""
                    INSERT INTO notifications (user_id, notification_type, title, message, entity_type, entity_id, action_token, created_at)
                    VALUES (:uid, 'entity_invite', :title, :msg, :etype, :eid, :token, CURRENT_TIMESTAMP)
                """),
                {
                    "uid": existing_user,
                    "title": f"Invitation to manage {invitation['entity_name']}",
                    "msg": f"{inviter_name} has invited you to manage {entity_type} '{invitation['entity_name']}'",
                    "etype": entity_type,
                    "eid": entity_id,
                    "token": new_token
                }
            )
            db.commit()
        except Exception:
            db.rollback()
    
    return {"ok": True, "message": "Invitation re-sent successfully"}


def _send_invitation_email(email_service, to_email, inviter_name, entity_name, entity_type, token):
    """Send invitation email using the entity_invitation template"""
    import sys
    if not email_service.enabled:
        logger.info(f"Email service not enabled")
        return False

    template = email_service.get_template('entity_invitation')
    if not template:
        logger.info(f"entity_invitation template not found in DB")
        return False

    base_url = "https://gigsfill.com"
    variables = {
        'inviter_name': inviter_name,
        'entity_name': entity_name,
        'entity_type': entity_type,
        'accept_url': f"{base_url}/app/invited_user_create_user.html?token={token}",
        'decline_url': f"{base_url}/app/invited_user_declined.html?token={token}",
    }

    subject = email_service.render_template(template['subject'], variables)
    body = email_service.render_template(template['body'], variables)

    try:
        from email.mime.text import MIMEText
        from email.mime.multipart import MIMEMultipart
        import smtplib

        msg = MIMEMultipart()
        if email_service.from_name:
            from email.utils import formataddr
            msg['From'] = formataddr((email_service.from_name, email_service.from_email))
        else:
            msg['From'] = email_service.from_email
        msg['To'] = to_email
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
        return True
    except Exception as e:
        logger.error(f"Email send failed: {e}")
        return False


async def _invite_user_to_entity(entity_type: str, entity_id: int, data: dict, user, db):
    """Common logic for inviting a user to an entity"""

    invited_email = data.get('email', '').strip().lower()
    
    if not invited_email:
        raise HTTPException(400, "Email address is required")
    
    # Validate email format
    if '@' not in invited_email or '.' not in invited_email:
        raise HTTPException(400, "Invalid email address")
    
    # Get entity info (NOTE: venues use venue_name, artists use name)
    if entity_type == 'artist':
        entity = db.execute(
            text("SELECT id, name, user_id FROM artists WHERE id = :eid"),
            {"eid": entity_id}
        ).mappings().first()
    else:
        entity = db.execute(
            text("SELECT id, venue_name as name, user_id FROM venues WHERE id = :eid"),
            {"eid": entity_id}
        ).mappings().first()
    
    if not entity:
        raise HTTPException(404, f"{entity_type.capitalize()} not found")
    
    # Verify user has access to this entity
    access_check = db.execute(
        text("""
            SELECT 1 FROM entity_users 
            WHERE entity_type = :etype AND entity_id = :eid AND user_id = :uid
        """),
        {"etype": entity_type, "eid": entity_id, "uid": user.id}
    ).scalar()
    
    if not access_check and entity['user_id'] != user.id:
        raise HTTPException(403, f"You don't have access to this {entity_type}")
    
    # Check if the invited email is the entity owner
    owner_email = db.execute(
        text("SELECT email FROM users WHERE id = :uid"),
        {"uid": entity['user_id']}
    ).scalar()
    
    if owner_email and owner_email.lower() == invited_email:
        raise HTTPException(400, f"This user is already the owner of this {entity_type}")
    
    # Check if email already has access via entity_users
    existing_access = db.execute(
        text("""
            SELECT u.id, u.email FROM users u
            JOIN entity_users eu ON u.id = eu.user_id
            WHERE eu.entity_type = :etype AND eu.entity_id = :eid
            AND LOWER(u.email) = :email
        """),
        {"etype": entity_type, "eid": entity_id, "email": invited_email}
    ).mappings().first()
    
    if existing_access:
        raise HTTPException(400, f"This user is already assigned to this {entity_type}")
    
    # Check if the invited email is an existing GigsFill user
    existing_user = db.execute(
        text("SELECT id, first_name, last_name, email FROM users WHERE LOWER(email) = :email"),
        {"email": invited_email}
    ).mappings().first()
    
    # Get inviter info
    inviter = db.execute(
        text("SELECT first_name, last_name FROM users WHERE id = :uid"),
        {"uid": user.id}
    ).mappings().first()
    
    inviter_name = f"{inviter['first_name']} {inviter['last_name']}"
    
    if existing_user:
        # User exists in GigsFill - check for pending invitation first
        pending = db.execute(
            text("""
                SELECT id FROM entity_invitations 
                WHERE entity_type = :etype AND entity_id = :eid 
                AND LOWER(invited_email) = :email AND status = 'pending'
            """),
            {"etype": entity_type, "eid": entity_id, "email": invited_email}
        ).scalar()
        
        if pending:
            raise HTTPException(400, "An invitation is already pending for this user")
        
        # Generate token for email link
        token = secrets.token_urlsafe(32)
        
        # Create invitation record
        db.execute(
            text("""
                INSERT INTO entity_invitations 
                (entity_type, entity_id, entity_name, invited_email, invited_by_user_id,
                 inviter_first_name, inviter_last_name, token, status)
                VALUES (:etype, :eid, :ename, :email, :uid, :fname, :lname, :token, 'pending')
            """),
            {
                "etype": entity_type,
                "eid": entity_id,
                "ename": entity['name'],
                "email": invited_email,
                "uid": user.id,
                "fname": inviter['first_name'],
                "lname": inviter['last_name'],
                "token": token
            }
        )
        db.commit()
        
        # Initialize email service BEFORE notification insert (session is clean here)
        email_service = EmailService(db)
        
        # Create notification for existing user
        try:
            notification_message = f"{inviter_name} has invited you to manage {entity_type} '{entity['name']}'"
            db.execute(
                text("""
                    INSERT INTO notifications (user_id, notification_type, title, message, entity_type, entity_id, action_token, created_at)
                    VALUES (:uid, 'entity_invite', :title, :msg, :etype, :eid, :token, CURRENT_TIMESTAMP)
                """),
                {
                    "uid": existing_user['id'],
                    "title": f"Invitation to manage {entity['name']}",
                    "msg": notification_message,
                    "etype": entity_type,
                    "eid": entity_id,
                    "token": token
                }
            )
            db.commit()
        except Exception as e:
            db.rollback()
            # Columns might not exist yet - try simpler insert
            try:
                db.execute(
                    text("""
                        INSERT INTO notifications (user_id, notification_type, title, message, created_at)
                        VALUES (:uid, 'entity_invite', :title, :msg, CURRENT_TIMESTAMP)
                    """),
                    {
                        "uid": existing_user['id'],
                        "title": f"Invitation to manage {entity['name']}",
                        "msg": notification_message
                    }
                )
                db.commit()
            except Exception as e2:
                db.rollback()
                import sys
                logger.error(f"Notification insert failed: {e2}")
        
        # Send email using pre-initialized service
        _send_invitation_email(email_service, invited_email, inviter_name, entity['name'], entity_type, token)
        
        return {"ok": True, "message": "Invitation sent (user notified in app and via email)"}
    
    else:
        # New user - create invitation and send email
        # Check for pending invitation
        pending = db.execute(
            text("""
                SELECT id FROM entity_invitations 
                WHERE entity_type = :etype AND entity_id = :eid 
                AND LOWER(invited_email) = :email AND status = 'pending'
            """),
            {"etype": entity_type, "eid": entity_id, "email": invited_email}
        ).scalar()
        
        if pending:
            raise HTTPException(400, "An invitation is already pending for this email")
        
        # Generate unique token
        token = secrets.token_urlsafe(32)
        
        # Create invitation record
        db.execute(
            text("""
                INSERT INTO entity_invitations 
                (entity_type, entity_id, entity_name, invited_email, invited_by_user_id,
                 inviter_first_name, inviter_last_name, token, status)
                VALUES (:etype, :eid, :ename, :email, :uid, :fname, :lname, :token, 'pending')
            """),
            {
                "etype": entity_type,
                "eid": entity_id,
                "ename": entity['name'],
                "email": invited_email,
                "uid": user.id,
                "fname": inviter['first_name'],
                "lname": inviter['last_name'],
                "token": token
            }
        )
        db.commit()
        
        # Send invitation email
        email_service = EmailService(db)
        sent = _send_invitation_email(email_service, invited_email, inviter_name, entity['name'], entity_type, token)
        
        if sent:
            return {"ok": True, "message": "Invitation sent successfully"}
        elif not email_service.enabled:
            return {"ok": True, "message": "Invitation created (email service not configured)", "token": token}
        else:
            return {"ok": True, "message": "Invitation created but email failed to send", "token": token}

# ============================================
# REMOVE USER FROM ENTITY
# ============================================
@router.delete("/api/entity-users/artist/{artist_id}/remove/{target_user_id}")
def remove_user_from_artist(
    artist_id: int,
    target_user_id: int,
    user=Depends(get_current_user),
    db=Depends(get_db)
):
    """Remove a user's access to an artist"""
    return _remove_user_from_entity('artist', artist_id, target_user_id, user, db)

@router.delete("/api/entity-users/venue/{venue_id}/remove/{target_user_id}")
def remove_user_from_venue(
    venue_id: int,
    target_user_id: int,
    user=Depends(get_current_user),
    db=Depends(get_db)
):
    """Remove a user's access to a venue"""
    return _remove_user_from_entity('venue', venue_id, target_user_id, user, db)

def _remove_user_from_entity(entity_type: str, entity_id: int, target_user_id: int, user, db):
    """Common logic for removing a user from an entity"""
    
    # Get original ownership
    if entity_type == 'artist':
        owner_id = db.execute(
            text("SELECT user_id FROM artists WHERE id = :eid"),
            {"eid": entity_id}
        ).scalar()
    else:
        owner_id = db.execute(
            text("SELECT user_id FROM venues WHERE id = :eid"),
            {"eid": entity_id}
        ).scalar()
    
    # Cannot remove the original owner
    if target_user_id == owner_id:
        raise HTTPException(400, "Cannot remove the original owner")
    
    # If removing yourself (and you're not the owner), allow it
    if target_user_id == user.id:
        # Just remove and return
        result = db.execute(
            text("""
                DELETE FROM entity_users 
                WHERE entity_type = :etype AND entity_id = :eid AND user_id = :target_uid
            """),
            {"etype": entity_type, "eid": entity_id, "target_uid": target_user_id}
        )
        db.commit()
        
        if result.rowcount == 0:
            raise HTTPException(404, "User not found in this entity")
        
        return {"ok": True, "message": "You have been removed from this " + entity_type, "removed_self": True}
    
    # Otherwise, verify current user has access to remove others
    access_check = db.execute(
        text("""
            SELECT role FROM entity_users 
            WHERE entity_type = :etype AND entity_id = :eid AND user_id = :uid
        """),
        {"etype": entity_type, "eid": entity_id, "uid": user.id}
    ).scalar()
    
    if not access_check and owner_id != user.id:
        raise HTTPException(403, f"You don't have access to this {entity_type}")
    
    # Remove user access
    result = db.execute(
        text("""
            DELETE FROM entity_users 
            WHERE entity_type = :etype AND entity_id = :eid AND user_id = :target_uid
        """),
        {"etype": entity_type, "eid": entity_id, "target_uid": target_user_id}
    )
    db.commit()
    
    if result.rowcount == 0:
        raise HTTPException(404, "User not found in this entity")
    
    return {"ok": True, "message": "User removed successfully"}

# ============================================
# HANDLE INVITATION RESPONSES
# ============================================
@router.get("/api/invitations/{token}")
def get_invitation_details(token: str, db=Depends(get_db)):
    """Get invitation details by token"""
    
    invitation = db.execute(
        text("""
            SELECT 
                id, entity_type, entity_id, entity_name, invited_email,
                inviter_first_name, inviter_last_name, status, created_at
            FROM entity_invitations 
            WHERE token = :token
        """),
        {"token": token}
    ).mappings().first()
    
    if not invitation:
        raise HTTPException(404, "Invitation not found")
    
    result = dict(invitation)
    
    # Check if invited email already has an account
    existing_user = db.execute(
        text("SELECT id FROM users WHERE LOWER(email) = :email"),
        {"email": invitation['invited_email'].lower()}
    ).scalar()
    
    result['user_exists'] = existing_user is not None
    
    return result

@router.post("/api/invitations/{token}/accept")
def accept_invitation(
    token: str,
    data: dict,
    db=Depends(get_db)
):
    """Accept an invitation and create user account"""
    
    # Get invitation
    invitation = db.execute(
        text("SELECT * FROM entity_invitations WHERE token = :token"),
        {"token": token}
    ).mappings().first()
    
    if not invitation:
        raise HTTPException(404, "Invitation not found")
    
    if invitation['status'] != 'pending':
        raise HTTPException(400, f"Invitation already {invitation['status']}")
    
    # Validate required fields
    first_name = data.get('first_name', '').strip()
    last_name = data.get('last_name', '').strip()
    email = data.get('email', '').strip().lower()
    phone = data.get('phone', '').strip()
    password = data.get('password', '')
    
    if not all([first_name, last_name, email, password]):
        raise HTTPException(400, "Missing required fields")

    from backend.routes.auth import validate_password_or_raise
    validate_password_or_raise(password)
    
    # Check if email already exists
    existing = db.execute(
        text("SELECT id FROM users WHERE LOWER(email) = :email"),
        {"email": email}
    ).scalar()
    
    if existing:
        raise HTTPException(400, "An account with this email already exists. Please login instead.")
    
    # Create user account - use bcrypt to match auth.py
    import bcrypt
    salt = bcrypt.gensalt()
    hashed_password = bcrypt.hashpw(password.encode('utf-8'), salt).decode('utf-8')
    
    db.execute(
        text("""
            INSERT INTO users (first_name, last_name, email, phone, password, created_at)
            VALUES (:fname, :lname, :email, :phone, :pw, :created)
        """),
        {
            "fname": first_name,
            "lname": last_name,
            "email": email,
            "phone": phone,
            "pw": hashed_password,
            "created": utcnow_naive()
        }
    )
    db.commit()
    
    # Get new user ID
    new_user_id = db.execute(
        text("SELECT id FROM users WHERE LOWER(email) = :email"),
        {"email": email}
    ).scalar()
    
    # Add user to entity_users
    db.execute(
        text("""
            INSERT INTO entity_users (entity_type, entity_id, user_id, role, added_by_user_id)
            VALUES (:etype, :eid, :uid, 'member', :added_by)
        """),
        {
            "etype": invitation['entity_type'],
            "eid": invitation['entity_id'],
            "uid": new_user_id,
            "added_by": invitation['invited_by_user_id']
        }
    )
    
    # Update invitation status
    db.execute(
        text("""
            UPDATE entity_invitations 
            SET status = 'accepted', responded_at = :now
            WHERE token = :token
        """),
        {"token": token, "now": utcnow_naive()}
    )
    
    # Delete ALL entity_invite notifications for this entity+user
    try:
        db.execute(
            text("""
                DELETE FROM notifications 
                WHERE user_id = :uid AND notification_type = 'entity_invite'
                AND entity_type = :etype AND entity_id = :eid
            """),
            {"uid": new_user_id, "etype": invitation['entity_type'], "eid": invitation['entity_id']}
        )
    except Exception:
        pass
    
    db.commit()
    
    return {
        "ok": True,
        "message": "Account created successfully! You can now login.",
        "entity_type": invitation['entity_type'],
        "entity_name": invitation['entity_name']
    }

@router.post("/api/invitations/{token}/accept-existing")
def accept_invitation_existing_user(
    token: str,
    db=Depends(get_db)
):
    """Accept an invitation for a user who already has an account (via email link)"""
    
    # Get invitation
    invitation = db.execute(
        text("SELECT * FROM entity_invitations WHERE token = :token"),
        {"token": token}
    ).mappings().first()
    
    if not invitation:
        raise HTTPException(404, "Invitation not found")
    
    if invitation['status'] != 'pending':
        raise HTTPException(400, f"Invitation already {invitation['status']}")
    
    # Find the user by the invited email
    existing_user = db.execute(
        text("SELECT id FROM users WHERE LOWER(email) = :email"),
        {"email": invitation['invited_email'].lower()}
    ).scalar()
    
    if not existing_user:
        raise HTTPException(400, "No account found with this email. Please create an account first.")
    
    # Check if user already has access
    existing_access = db.execute(
        text("""
            SELECT 1 FROM entity_users 
            WHERE entity_type = :etype AND entity_id = :eid AND user_id = :uid
        """),
        {"etype": invitation['entity_type'], "eid": invitation['entity_id'], "uid": existing_user}
    ).scalar()
    
    if existing_access:
        raise HTTPException(400, f"You already have access to this {invitation['entity_type']}")
    
    # Add user to entity_users
    db.execute(
        text("""
            INSERT INTO entity_users (entity_type, entity_id, user_id, role, added_by_user_id)
            VALUES (:etype, :eid, :uid, 'member', :added_by)
        """),
        {
            "etype": invitation['entity_type'],
            "eid": invitation['entity_id'],
            "uid": existing_user,
            "added_by": invitation['invited_by_user_id']
        }
    )
    
    # Update invitation status
    db.execute(
        text("""
            UPDATE entity_invitations 
            SET status = 'accepted', responded_at = :now
            WHERE token = :token
        """),
        {"token": token, "now": utcnow_naive()}
    )
    
    # Delete ALL entity_invite notifications for this entity+user (cleans up old re-invite notifications too)
    try:
        db.execute(
            text("""
                DELETE FROM notifications 
                WHERE user_id = :uid AND notification_type = 'entity_invite'
                AND entity_type = :etype AND entity_id = :eid
            """),
            {"uid": existing_user, "etype": invitation['entity_type'], "eid": invitation['entity_id']}
        )
    except Exception:
        pass
    
    db.commit()
    
    return {
        "ok": True,
        "message": f"You now have access to {invitation['entity_name']}!",
        "entity_type": invitation['entity_type'],
        "entity_name": invitation['entity_name'],
        "entity_id": invitation['entity_id']
    }

@router.post("/api/invitations/{token}/decline")
def decline_invitation(token: str, db=Depends(get_db)):
    """Decline an invitation"""
    
    # Get invitation
    invitation = db.execute(
        text("SELECT * FROM entity_invitations WHERE token = :token"),
        {"token": token}
    ).mappings().first()
    
    if not invitation:
        raise HTTPException(404, "Invitation not found")
    
    if invitation['status'] != 'pending':
        raise HTTPException(400, f"Invitation already {invitation['status']}")
    
    # Update invitation status
    db.execute(
        text("""
            UPDATE entity_invitations 
            SET status = 'declined', responded_at = :now
            WHERE token = :token
        """),
        {"token": token, "now": utcnow_naive()}
    )
    
    # Delete ALL entity_invite notifications for this entity+user (cleans up old re-invite notifications too)
    try:
        invited_user = db.execute(
            text("SELECT id FROM users WHERE LOWER(email) = LOWER(:email)"),
            {"email": invitation['invited_email']}
        ).scalar()
        if invited_user:
            db.execute(
                text("""
                    DELETE FROM notifications 
                    WHERE user_id = :uid AND notification_type = 'entity_invite'
                    AND entity_type = :etype AND entity_id = :eid
                """),
                {"uid": invited_user, "etype": invitation['entity_type'], "eid": invitation['entity_id']}
            )
    except Exception:
        pass
    
    db.commit()
    
    return {
        "ok": True,
        "inviter_first_name": invitation['inviter_first_name'],
        "inviter_last_name": invitation['inviter_last_name'],
        "entity_name": invitation['entity_name']
    }

# ============================================
# EMAIL TEMPLATE
# ============================================
def create_invitation_email_html_from_template(body_text: str, entity_name: str, accept_url: str, decline_url: str) -> str:
    """Wrap template body text in branded HTML email"""
    
    # Convert newlines to <br> for HTML, and escape HTML special chars
    import html
    body_html = html.escape(body_text).replace('\n', '<br>\n')
    
    # Replace URLs with clickable links
    body_html = body_html.replace(
        html.escape(accept_url), 
        f'<a href="{accept_url}" style="color: #10F7CF;">{accept_url}</a>'
    )
    body_html = body_html.replace(
        html.escape(decline_url),
        f'<a href="{decline_url}" style="color: #ef4444;">{decline_url}</a>'
    )
    
    return f'''<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
</head>
<body style="margin: 0; padding: 0; font-family: Arial, sans-serif; background-color: #0a0a0a;">
    <table cellpadding="0" cellspacing="0" border="0" width="100%" style="background-color: #0a0a0a;">
        <tr>
            <td align="center" style="padding: 40px 20px;">
                <table cellpadding="0" cellspacing="0" border="0" width="600" style="max-width: 600px; background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%); border-radius: 12px; overflow: hidden; box-shadow: 0 10px 40px rgba(0,0,0,0.3);">
                    
                    <!-- Header -->
                    <tr>
                        <td style="background: linear-gradient(135deg, #7B2CBF 0%, #9D4EDD 100%); padding: 40px 30px; text-align: center;">
                            <h1 style="margin: 0; color: #ffffff; font-size: 32px; font-weight: bold;">🎸 GigsFill</h1>
                            <p style="margin: 10px 0 0 0; color: #E0E0E0; font-size: 14px;">You've been invited!</p>
                        </td>
                    </tr>
                    
                    <!-- Main content -->
                    <tr>
                        <td style="padding: 40px 30px;">
                            <div style="color: #E0E0E0; font-size: 16px; line-height: 1.8;">
                                {body_html}
                            </div>
                            
                            <!-- Accept/Decline Buttons -->
                            <table cellpadding="0" cellspacing="0" border="0" width="100%" style="margin: 30px 0;">
                                <tr>
                                    <td align="center" style="padding: 10px;">
                                        <a href="{accept_url}" style="display: inline-block; padding: 16px 50px; background: linear-gradient(135deg, #10b981 0%, #22c55e 100%); color: #ffffff; text-decoration: none; border-radius: 8px; font-size: 18px; font-weight: bold; box-shadow: 0 4px 15px rgba(16, 185, 129, 0.4);">
                                            ✓ ACCEPT
                                        </a>
                                    </td>
                                </tr>
                                <tr>
                                    <td align="center" style="padding: 10px;">
                                        <a href="{decline_url}" style="display: inline-block; padding: 12px 40px; background: transparent; border: 2px solid #ef4444; color: #ef4444; text-decoration: none; border-radius: 8px; font-size: 14px; font-weight: bold;">
                                            ✕ Decline
                                        </a>
                                    </td>
                                </tr>
                            </table>
                        </td>
                    </tr>
                    
                    <!-- Footer -->
                    <tr>
                        <td style="background: rgba(10, 10, 10, 0.6); padding: 30px; text-align: center; border-top: 1px solid rgba(255,255,255,0.1);">
                            <p style="margin: 0 0 20px 0; color: #808080; font-size: 12px;">
                                © 2026 GigsFill. Connecting artists with venues.
                            </p>
                        </td>
                    </tr>
                </table>
            </td>
        </tr>
    </table>
</body>
</html>'''

def create_invitation_email_html(inviter_first: str, inviter_last: str, entity_name: str, entity_type: str, token: str) -> str:
    """Create branded invitation email HTML (legacy fallback)"""
    
    base_url = "https://gigsfill.com"  # Update to your actual domain
    accept_url = f"{base_url}/app/invited_user_create_user.html?token={token}"
    decline_url = f"{base_url}/app/invited_user_declined.html?token={token}"
    
    entity_label = "artist" if entity_type == "artist" else "venue"
    
    return f'''<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
</head>
<body style="margin: 0; padding: 0; font-family: Arial, sans-serif; background-color: #0a0a0a;">
    <table cellpadding="0" cellspacing="0" border="0" width="100%" style="background-color: #0a0a0a;">
        <tr>
            <td align="center" style="padding: 40px 20px;">
                <table cellpadding="0" cellspacing="0" border="0" width="600" style="max-width: 600px; background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%); border-radius: 12px; overflow: hidden; box-shadow: 0 10px 40px rgba(0,0,0,0.3);">
                    
                    <!-- Header -->
                    <tr>
                        <td style="background: linear-gradient(135deg, #7B2CBF 0%, #9D4EDD 100%); padding: 40px 30px; text-align: center;">
                            <h1 style="margin: 0; color: #ffffff; font-size: 32px; font-weight: bold;">🎸 GigsFill</h1>
                            <p style="margin: 10px 0 0 0; color: #E0E0E0; font-size: 14px;">You've been invited!</p>
                        </td>
                    </tr>
                    
                    <!-- Main content -->
                    <tr>
                        <td style="padding: 40px 30px;">
                            <h2 style="margin: 0 0 20px 0; color: #10F7CF; font-size: 24px; font-weight: bold; text-align: center;">
                                {inviter_first} {inviter_last} has invited you to join GigsFill!
                            </h2>
                            
                            <table cellpadding="0" cellspacing="0" border="0" width="100%" style="background: rgba(123, 44, 191, 0.1); border-left: 4px solid #7B2CBF; border-radius: 8px; margin: 30px 0;">
                                <tr>
                                    <td style="padding: 25px;">
                                        <p style="margin: 0; color: #E0E0E0; font-size: 16px; line-height: 1.6; text-align: center;">
                                            If you accept, you will have full access to manage the {entity_label}:<br>
                                            <strong style="color: #9D4EDD; font-size: 20px;">{entity_name}</strong>
                                        </p>
                                    </td>
                                </tr>
                            </table>
                            
                            <!-- Accept/Decline Buttons -->
                            <table cellpadding="0" cellspacing="0" border="0" width="100%" style="margin: 30px 0;">
                                <tr>
                                    <td align="center" style="padding: 10px;">
                                        <a href="{accept_url}" style="display: inline-block; padding: 16px 50px; background: linear-gradient(135deg, #10b981 0%, #22c55e 100%); color: #ffffff; text-decoration: none; border-radius: 8px; font-size: 18px; font-weight: bold; box-shadow: 0 4px 15px rgba(16, 185, 129, 0.4);">
                                            ✓ ACCEPT
                                        </a>
                                    </td>
                                </tr>
                                <tr>
                                    <td align="center" style="padding: 10px;">
                                        <a href="{decline_url}" style="display: inline-block; padding: 12px 40px; background: transparent; border: 2px solid #ef4444; color: #ef4444; text-decoration: none; border-radius: 8px; font-size: 14px; font-weight: bold;">
                                            ✕ Decline
                                        </a>
                                    </td>
                                </tr>
                            </table>
                            
                            <p style="margin: 20px 0; color: #B0B0B0; font-size: 14px; line-height: 1.6; text-align: center;">
                                By accepting, you'll be able to create, edit, and manage gigs for <strong style="color: #9D4EDD;">{entity_name}</strong>.
                            </p>
                        </td>
                    </tr>
                    
                    <!-- Footer -->
                    <tr>
                        <td style="background: rgba(10, 10, 10, 0.6); padding: 30px; text-align: center; border-top: 1px solid rgba(255,255,255,0.1);">
                            <p style="margin: 0 0 10px 0; color: #B0B0B0; font-size: 14px;">
                                Questions? Reply to this email or contact support.
                            </p>
                            <p style="margin: 0 0 20px 0; color: #808080; font-size: 12px;">
                                © 2026 GigsFill. Connecting artists with venues.
                            </p>
                            <p style="margin: 0; color: #606060; font-size: 11px;">
                                If you didn't expect this invitation, you can safely ignore this email.
                            </p>
                        </td>
                    </tr>
                </table>
            </td>
        </tr>
    </table>
</body>
</html>'''
