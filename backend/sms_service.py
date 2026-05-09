"""
SMS service for GigsFill
Sends text messages via carrier email-to-SMS gateways using existing SMTP
"""
import smtplib
import logging
import re
from email.mime.text import MIMEText
from typing import Optional, Dict
logger = logging.getLogger("gigsfill.sms_service")


# Carrier email-to-SMS gateways
CARRIER_GATEWAYS = {
    'att':        'txt.att.net',
    'tmobile':    'tmomail.net',
    'verizon':    'vtext.com',
    'sprint':     'messaging.sprintpcs.com',
    'uscellular': 'email.uscc.net',
    'cricket':    'sms.cricketwireless.net',
    'boost':      'sms.myboostmobile.com',
    'googlefi':   'msg.fi.google.com',
    'metro':      'mymetropcs.com',
    'xfinity':    'vtext.com',
    'visible':    'vtext.com',
    'mint':       'tmomail.net',
}

# Human-readable carrier names for UI
CARRIER_NAMES = {
    'att':        'AT&T',
    'tmobile':    'T-Mobile',
    'verizon':    'Verizon',
    'sprint':     'Sprint',
    'uscellular': 'US Cellular',
    'cricket':    'Cricket',
    'boost':      'Boost Mobile',
    'googlefi':   'Google Fi',
    'metro':      'Metro by T-Mobile',
    'xfinity':    'Xfinity Mobile',
    'visible':    'Visible',
    'mint':       'Mint Mobile',
}

# Short SMS templates (max ~155 chars to leave room for carrier overhead)
SMS_TEMPLATES = {
    'artist_gig_booked':         'GigsFill: You booked a gig at {venue_name} on {date} at {start_time}. Pay: ${pay}',
    'artist_gig_cancelled':      'GigsFill: Your gig at {venue_name} on {date} has been cancelled.',
    'artist_preferred_request':   'GigsFill: Preferred request sent to {venue_name}.',
    'artist_preferred_approved':  'GigsFill: {venue_name} approved you as preferred artist!',
    'artist_preferred_denied':    'GigsFill: {venue_name} denied your preferred artist request.',
    'artist_preferred_revoked':   'GigsFill: {venue_name} revoked your preferred status.',
    'venue_gig_booked':          'GigsFill: {artist_name} booked a gig on {date} at {start_time}. Pay: ${pay}',
    'venue_gig_cancelled':       'GigsFill: {artist_name} cancelled gig on {date} at {venue_name}.',
    'venue_preferred_request':    'GigsFill: {artist_name} requested preferred status at {venue_name}.',
    'venue_preferred_approved':   'GigsFill: You approved {artist_name} as preferred artist.',
    'venue_preferred_denied':     'GigsFill: You denied {artist_name} preferred request.',
    'venue_preferred_revoked':    'GigsFill: You revoked preferred status for {artist_name}.',
    'venue_payment_charged':     'GigsFill: ${total_charged} charged for {artist_name} gig on {date}.',
    'artist_payment_sent':       'GigsFill: ${payout_amount} payout sent for {venue_name} gig on {date}!',
}


class SmsService:
    """Sends SMS via carrier email-to-SMS gateways using SMTP"""

    def __init__(self, db):
        self.db = db
        from sqlalchemy import text as sa_text

        # Reuse SMTP config from platform_settings (same as EmailService)
        self.smtp_server = ""
        self.smtp_port = 587
        self.smtp_username = ""
        self.smtp_password = ""
        self.from_email = "noreply@gigsfill.com"
        self.enabled = False

        try:
            settings = {}
            results = db.execute(
                sa_text("SELECT setting_key, setting_value FROM platform_settings WHERE setting_key LIKE '%smtp%' OR setting_key LIKE '%email%'")
            ).fetchall()
            for row in results:
                settings[row[0]] = row[1]

            self.smtp_server = settings.get('platform_smtp_server') or settings.get('platform_email_smtp_server') or settings.get('smtp_server') or ''
            port_str = settings.get('platform_smtp_port') or settings.get('platform_email_smtp_port') or settings.get('smtp_port') or '587'
            self.smtp_port = int(port_str)
            self.smtp_username = settings.get('platform_email') or settings.get('platform_email_smtp_username') or settings.get('smtp_username') or ''
            self.smtp_password = settings.get('platform_email_password') or settings.get('platform_email_smtp_password') or settings.get('smtp_password') or ''
            self.from_email = settings.get('platform_email') or settings.get('platform_email_from') or settings.get('smtp_from_email') or 'noreply@gigsfill.com'
            self.enabled = bool(self.smtp_server and self.smtp_username and self.smtp_password)
        except Exception:
            pass

    @staticmethod
    def normalize_phone(phone: str) -> Optional[str]:
        """Strip phone to digits only, return 10-digit US number or None"""
        if not phone:
            return None
        digits = re.sub(r'\D', '', phone)
        # Handle 1+10 digit (US country code)
        if len(digits) == 11 and digits.startswith('1'):
            digits = digits[1:]
        return digits if len(digits) == 10 else None

    def user_has_sms_enabled(self, user_id: int, notification_type: str) -> bool:
        """Check if user has SMS enabled for this notification type"""
        from sqlalchemy import text as sa_text
        try:
            result = self.db.execute(
                sa_text("SELECT enabled FROM sms_preferences WHERE user_id = :uid AND notification_type = :type"),
                {"uid": user_id, "type": notification_type}
            ).scalar()
        except Exception:
            # Table doesn't exist yet
            return False
        # Default to False (opt-in) - user must explicitly enable SMS
        if result is None:
            return False
        return bool(result)

    def send_sms(self, user_id: int, phone: str, carrier: str,
                 notification_type: str, variables: Dict[str, str]) -> bool:
        """Send SMS to a user via carrier email gateway"""
        if not self.enabled:
            return False

        # Check user preference
        if not self.user_has_sms_enabled(user_id, notification_type):
            return False

        # Validate phone
        clean_phone = self.normalize_phone(phone)
        if not clean_phone:
            return False

        # Get gateway domain
        gateway = CARRIER_GATEWAYS.get(carrier)
        if not gateway:
            logger.info(f"Unknown carrier: {carrier}")
            return False

        # Get message template
        template = SMS_TEMPLATES.get(notification_type)
        if not template:
            return False

        # Render message
        message = template
        for key, value in variables.items():
            message = message.replace('{' + key + '}', str(value or ''))

        # Truncate to 160 chars
        if len(message) > 160:
            message = message[:157] + '...'

        # Build email-to-SMS address
        sms_email = f"{clean_phone}@{gateway}"

        try:
            msg = MIMEText(message, 'plain')
            msg['From'] = self.from_email
            msg['To'] = sms_email
            # No subject for SMS - some carriers show it, keep it empty
            msg['Subject'] = ''

            logger.info(f"Sending to {sms_email} via {self.smtp_server}:{self.smtp_port}")

            if self.smtp_port == 465:
                with smtplib.SMTP_SSL(self.smtp_server, self.smtp_port, timeout=15) as server:
                    server.login(self.smtp_username, self.smtp_password)
                    server.send_message(msg)
            else:
                with smtplib.SMTP(self.smtp_server, self.smtp_port, timeout=15) as server:
                    server.starttls()
                    server.login(self.smtp_username, self.smtp_password)
                    server.send_message(msg)

            logger.info(f"SMS sent to {sms_email}")
            return True

        except Exception as e:
            logger.error(f"Error sending to {sms_email}: {e}")
            return False
