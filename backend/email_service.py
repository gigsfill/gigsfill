"""
Email service for GigsFill
Handles sending notification emails
"""
import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Dict, Optional
import re
logger = logging.getLogger("gigsfill.email_service")

# Notification types whose default (when no explicit row exists in
# email_preferences) is OFF. All other types default to ON.
#
# Product policy (May 2026):
#   * Long-lead-time blast emails (4-week, 2-week) default OFF — these are
#     "calendar planning" emails that fire many times and many users find
#     spammy. Artists must opt in via user-profile → Notifications.
#   * Urgent blasts (1-week, 36-hour, cancellation blasts) default ON — these
#     are time-sensitive "this gig is starting soon / opened up" notifications
#     where missing one is a real cost (the artist might have wanted to book).
#     Aligned with the user-profile UI defaults in app/static/js/user-profile.js.
#
# Authoritative — both email_service.user_has_email_enabled() and the inline
# gates in backend/scheduler.py read this constant.
BLAST_OFF_DEFAULTS = frozenset({
    'venue_open_gig_4w',
    'venue_open_gig_2w',
})

def _smtp_send(server_host, port, username, password, msg):
    """Send email via SMTP handling port 465 (SSL), 587 (STARTTLS), and other ports (plain/try-STARTTLS)."""
    if port == 465:
        with smtplib.SMTP_SSL(server_host, port, timeout=15) as s:
            s.login(username, password)
            s.send_message(msg)
    elif port in (587, 2587):
        with smtplib.SMTP(server_host, port, timeout=15) as s:
            s.ehlo(); s.starttls(); s.ehlo()
            s.login(username, password)
            s.send_message(msg)
    else:
        with smtplib.SMTP(server_host, port, timeout=15) as s:
            s.ehlo()
            try: s.starttls(); s.ehlo()
            except Exception: pass
            s.login(username, password)
            s.send_message(msg)

class EmailService:
    """Handles email sending and template processing"""
    
    def __init__(self, db):
        self.db = db
        # v97: Load SMTP settings from database
        from sqlalchemy import text
        
        # Default values
        self.smtp_server = "smtp.gmail.com"
        self.smtp_port = 587
        self.smtp_username = ""
        self.smtp_password = ""
        self.from_email = "noreply@gigsfill.com"
        self.from_name = ""
        self.enabled = False
        
        try:
            # Load SMTP settings from database
            settings = {}
            # v97: Fixed - look for BOTH naming conventions
            results = db.execute(
                text("SELECT setting_key, setting_value FROM platform_settings WHERE setting_key LIKE '%smtp%' OR setting_key LIKE '%email%'")
            ).fetchall()
            
            for row in results:
                settings[row[0]] = row[1]
            
            
            # v97: Check for BOTH naming conventions (platform_* and smtp_*)
            # Platform email (primary)
            if settings.get('platform_email'):
                self.smtp_username = settings['platform_email']
                self.from_email = settings['platform_email']
            elif settings.get('smtp_email'):
                self.smtp_username = settings['smtp_email']
                self.from_email = settings['smtp_email']
            
            # From name for display (e.g. "GigsFill Booking")
            if settings.get('platform_email_from_name'):
                self.from_name = settings['platform_email_from_name']
            
            # Platform password
            if settings.get('platform_email_password'):
                self.smtp_password = settings['platform_email_password']
            elif settings.get('smtp_password'):
                self.smtp_password = settings['smtp_password']
            
            # SMTP server
            if settings.get('platform_smtp_server'):
                self.smtp_server = settings['platform_smtp_server']
            elif settings.get('smtp_server'):
                self.smtp_server = settings['smtp_server']
            
            # SMTP port
            if settings.get('platform_smtp_port'):
                self.smtp_port = int(settings['platform_smtp_port'])
            elif settings.get('smtp_port'):
                self.smtp_port = int(settings['smtp_port'])
            
            # Enable if we have username and password
            if self.smtp_username and self.smtp_password:
                self.enabled = True
            else:
                pass  # Email not configured
                
        except Exception as e:
            self.enabled = False
    
    def get_template(self, notification_type: str) -> Optional[Dict[str, str]]:
        """Get email template for notification type"""
        from sqlalchemy import text
        
        # Try template_key column first (primary column in schema)
        try:
            result = self.db.execute(
                text("""
                    SELECT subject, body 
                    FROM email_templates 
                    WHERE template_key = :type
                    LIMIT 1
                """),
                {"type": notification_type}
            ).mappings().first()
            
            if result:
                return dict(result)
        except Exception as e:
            try:
                self.db.rollback()
            except:
                pass
        
        # Fallback to notification_type column if it exists
        try:
            result = self.db.execute(
                text("""
                    SELECT subject, body 
                    FROM email_templates 
                    WHERE notification_type = :type
                    LIMIT 1
                """),
                {"type": notification_type}
            ).mappings().first()
            
            if result:
                return dict(result)
        except Exception as e:
            try:
                self.db.rollback()
            except:
                pass

        # Final fallback: use in-memory TEMPLATES dict (catches newly added templates
        # that haven't been migrated to the DB yet via email_templates.py)
        try:
            from backend.email_templates import TEMPLATES
            if notification_type in TEMPLATES:
                t = TEMPLATES[notification_type]
                return {"subject": t.get("subject", ""), "body": t.get("body", "")}
        except Exception:
            pass

        return None
    
    def render_template(self, template: str, variables: Dict[str, str]) -> str:
        """Replace {{variable}} placeholders with actual values.
        Supports {{#var}}...{{/var}} conditional blocks (rendered only when var is truthy)."""
        import re
        result = template
        # Process {{#var}}...{{/var}} blocks first
        def _replace_block(m):
            key = m.group(1)
            inner = m.group(2)
            val = variables.get(key, '')
            return inner if val else ''
        result = re.sub(r'\{\{#(\w+)\}\}(.*?)\{\{/\1\}\}', _replace_block, result, flags=re.DOTALL)
        # Then replace plain {{variable}} placeholders
        for key, value in variables.items():
            placeholder = f"{{{{{key}}}}}"
            result = result.replace(placeholder, str(value or ''))
        return result
    
    def user_has_email_enabled(self, user_id: int, notification_type: str) -> bool:
        """Check if user has this email notification enabled"""
        from sqlalchemy import text
        
        # v97: Fixed table name - was checking user_email_preferences but should be email_preferences
        result = self.db.execute(
            text("""
                SELECT enabled 
                FROM email_preferences 
                WHERE user_id = :user_id AND notification_type = :type
            """),
            {"user_id": user_id, "type": notification_type}
        ).scalar()
        
        # Default to True if no preference set, EXCEPT blast types default to OFF
        # (BLAST_OFF_DEFAULTS is the canonical list — see top of this file).
        if result is None:
            return notification_type not in BLAST_OFF_DEFAULTS
        return bool(result)
    
    def _alert_admin_smtp_failure(self, failed_to: str, notification_type: str, error: str):
        """Send an alert to admin when SMTP fails — uses a separate direct SMTP call
        so the alert itself doesn't recurse. Throttled to once per 15 minutes."""
        import time
        now = time.time()
        # Class-level throttle so we don't spam admin with every failed email
        last = getattr(EmailService, '_last_smtp_alert', 0)
        if now - last < 900:  # 15 minutes
            return
        EmailService._last_smtp_alert = now
        try:
            import smtplib
            from email.mime.text import MIMEText
            from email.mime.multipart import MIMEMultipart
            if not self.smtp_username or not self.smtp_password:
                return
            msg = MIMEMultipart("alternative")
            msg['From'] = self.from_email or self.smtp_username
            msg['To']   = self.smtp_username  # alert goes to the platform email itself
            msg['Subject'] = '⚠️ GigsFill SMTP Failure Alert'
            body = f"""<p>An email failed to send on GigsFill.</p>
<ul>
  <li><b>To:</b> {failed_to}</li>
  <li><b>Type:</b> {notification_type}</li>
  <li><b>Error:</b> {error}</li>
</ul>
<p>Check Admin → Logs for details. If Gmail is blocking sends, check account security alerts.</p>"""
            msg.attach(MIMEText(body, 'html'))
            _smtp_send(self.smtp_server, self.smtp_port, self.smtp_username, self.smtp_password, msg)
            logger.info("Admin SMTP failure alert sent")
        except Exception:
            pass  # Don't recurse or crash on alert failure

    def send_notification_email(
        self,
        user_email: str,
        user_id: int,
        notification_type: str,
        variables: Dict[str, str],
        _smtp_server=None
    ) -> bool:
        """Send notification email to user. Pass _smtp_server to reuse an open connection."""
        import sys
        
        
        # Check if email is enabled
        if not self.enabled:
            logger.info(f"SKIPPED - email not enabled (server={self.smtp_server}, port={self.smtp_port}, user={self.smtp_username})")
            return False
        
        # Check user preferences
        if not self.user_has_email_enabled(user_id, notification_type):
            logger.info(f"SKIPPED - user {user_id} has {notification_type} disabled")
            return False
        
        # Get template
        template = self.get_template(notification_type)
        if not template:
            logger.info(f"SKIPPED - no template found for '{notification_type}'")
            return False
        
        # Render template
        subject = self.render_template(template['subject'], variables)
        body = self.render_template(template['body'], variables)
        
        try:
            # Create message — use "alternative" not "mixed" to avoid Outlook paperclip
            msg = MIMEMultipart("alternative")
            if self.from_name:
                from email.utils import formataddr
                msg['From'] = formataddr((self.from_name, self.from_email))
            else:
                msg['From'] = self.from_email
            msg['To'] = user_email
            msg['Subject'] = subject
            msg['X-Mailer'] = 'GigsFill'
            msg['List-Unsubscribe'] = f'<mailto:{self.from_email}?subject=unsubscribe>'
            msg['List-Unsubscribe-Post'] = 'List-Unsubscribe=One-Click'
            msg.attach(MIMEText(body, 'html'))
            
            logger.info(f"Sending to {user_email} via {self.smtp_server}:{self.smtp_port} from {self.from_email}")
            
            # Send email — reuse connection if provided
            if _smtp_server is not None:
                _smtp_server.send_message(msg)
            else:
                _smtp_send(self.smtp_server, self.smtp_port, self.smtp_username, self.smtp_password, msg)
            
            logger.info(f"SUCCESS - sent to {user_email}")
            
            # Also dispatch SMS if user has it enabled
            self._dispatch_sms(user_id, notification_type, variables)
            
            return True
            
        except Exception as e:
            logger.error(f"FAILED to send to {user_email}: {e}")
            # Alert admin on SMTP failures so silent failures are noticed
            self._alert_admin_smtp_failure(user_email, notification_type, str(e))
            # Still try SMS even if email fails
            self._dispatch_sms(user_id, notification_type, variables)
            return False
    
    def _dispatch_sms(self, user_id: int, notification_type: str, variables: dict):
        """SMS via carrier gateways disabled - carriers block automated emails.
        Re-enable with Twilio integration later."""
        return
        # --- Carrier gateway code (kept for reference) ---
        # try:
        #     from sqlalchemy import text as sa_text
        #     user_row = self.db.execute(
        #         sa_text("SELECT phone, sms_carrier FROM users WHERE id = :uid"),
        #         {"uid": user_id}
        #     ).mappings().first()
        #     if not user_row or not user_row.get("phone") or not user_row.get("sms_carrier"):
        #         return
        #     from backend.sms_service import SmsService
        #     sms = SmsService(self.db)
        #     sms.send_sms(
        #         user_id=user_id,
        #         phone=user_row["phone"],
        #         carrier=user_row["sms_carrier"],
        #         notification_type=notification_type,
        #         variables=variables
        #     )
        # except Exception as e:
        #     print(f"[EmailService] SMS dispatch error: {e}", flush=True)

    def _render_template_key(self, template_key: str, variables: dict) -> str:
        """Render a template by key with variables, returning HTML body."""
        template = self.get_template(template_key)
        if not template:
            return ''
        return self.render_template(template['body'], variables)

    def _send_raw(self, to_email: str, subject: str, html_body: str) -> bool:
        """Send a raw HTML email (bypasses user preference checks). For non-registered users."""
        if not self.enabled:
            return False
        try:
            from email.mime.multipart import MIMEMultipart as _MIME
            from email.mime.text import MIMEText as _Text
            msg = _MIME("alternative")  # "alternative" prevents Outlook paperclip icon
            msg['Subject'] = subject
            if self.from_name:
                from email.utils import formataddr
                msg['From'] = formataddr((self.from_name, self.from_email))
            else:
                msg['From'] = self.from_email
            msg['To'] = to_email
            msg.attach(_Text(html_body, 'html'))
            import smtplib as _smtp
            _smtp_send(self.smtp_server, self.smtp_port, self.smtp_username, self.smtp_password, msg)
            return True
        except Exception as e:
            logger.error(f"_send_raw FAILED to {to_email}: {e}")
            return False

    def _send_raw_to(self, to_email: str, subject: str, html_body: str) -> bool:
        """Alias for _send_raw (used for admin notifications)."""
        return self._send_raw(to_email, subject, html_body)


    def _send_raw_email(self, to_email: str, subject: str, html_body: str) -> bool:
        """Send a raw HTML email without adding any wrapper"""
        import smtplib
        from email.mime.text import MIMEText
        from email.mime.multipart import MIMEMultipart

        if not self.enabled:
            logger.info(f"SKIPPED raw email - not enabled")
            return False

        msg = MIMEMultipart('alternative')
        msg['Subject'] = subject
        if self.from_name:
            from email.utils import formataddr
            msg['From'] = formataddr((self.from_name, self.from_email))
        else:
            msg['From'] = self.from_email
        msg['To'] = to_email
        msg.attach(MIMEText(html_body, 'html'))
        
        try:
            _smtp_send(self.smtp_server, self.smtp_port, self.smtp_username, self.smtp_password, msg)
            logger.info(f"Raw email sent to {to_email}: {subject}")
            return True
        except Exception as e:
            logger.error(f"Raw email FAILED to {to_email}: {e}")
            return False
