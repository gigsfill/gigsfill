"""
GigsFill SQLAlchemy Models
===========================
Kept in sync with db.py setup_database().
All tables and columns defined here must match the CREATE TABLE statements in db.py.
"""

from sqlalchemy import Column, Integer, String, Text, Boolean, ForeignKey, DateTime, Float, UniqueConstraint
from sqlalchemy.orm import declarative_base, relationship
from datetime import datetime

Base = declarative_base()


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    email = Column(String, unique=True, nullable=False)
    password = Column(String, nullable=False)
    first_name = Column(String)
    last_name = Column(String)
    phone = Column(String)
    is_admin = Column(Boolean, default=False)  # Migrated from TEXT 'true'/'false' to INTEGER 0/1 on 2026-05-08; SQLAlchemy Boolean reads existing values correctly via type coercion.
    affiliate_code = Column(String, unique=True)
    password_changed_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)


class Artist(Base):
    __tablename__ = "artists"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)

    name = Column(String)
    city = Column(String)
    state = Column(String)
    latitude = Column(Float)
    longitude = Column(Float)
    bio = Column(Text)

    artist_type = Column(String)
    band_formats = Column(String)
    styles = Column(String)
    booking_contact = Column(String)

    spotify_url = Column(Text)
    instagram_url = Column(Text)
    facebook_url = Column(Text)
    youtube_url = Column(Text)
    twitter_url = Column(Text)
    tiktok_url = Column(Text)
    website_url = Column(Text)

    display_order = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User")


class Venue(Base):
    __tablename__ = "venues"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)

    venue_name = Column(String)
    description = Column(Text)

    address_line_1 = Column(String)
    address_line_2 = Column(String)
    city = Column(String)
    state = Column(String)
    postal_code = Column(String)
    latitude = Column(Float)
    longitude = Column(Float)

    venue_size = Column(String)

    has_stage = Column(Boolean, default=False)
    stage_width_ft = Column(Integer)
    stage_depth_ft = Column(Integer)
    setup_location_description = Column(Text)

    has_sound_equipment = Column(Boolean, default=False)
    sound_equipment_description = Column(Text)

    has_sound_engineer = Column(Boolean, default=False)
    sound_engineer_details = Column(Text)

    has_lighting = Column(Boolean, default=False)
    lighting_description = Column(Text)

    load_in_out_details = Column(Text)

    arrival_time_type = Column(String)
    arrival_no_earlier_than_hour = Column(Integer)
    arrival_no_earlier_than_period = Column(String)

    default_pay_dollars = Column(Integer, default=0)
    default_pay_cents = Column(Integer, default=0)

    bar_tab_details = Column(Text)
    food_tab_details = Column(Text)

    artist_frequency_days = Column(Integer)

    website_url = Column(Text)
    facebook_url = Column(Text)
    instagram_url = Column(Text)
    twitter_url = Column(Text)
    yelp_url = Column(Text)
    google_maps_url = Column(Text)

    display_order = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)

    # PRO / Payment status
    pro_certified = Column(Integer, default=0)
    pro_certified_at = Column(DateTime)
    payment_status = Column(String, default='active')
    payment_suspended_at = Column(DateTime)
    payment_suspension_reason = Column(Text)

    user = relationship("User")


class Gig(Base):
    __tablename__ = "gigs"

    id = Column(Integer, primary_key=True)
    venue_id = Column(Integer, ForeignKey("venues.id"), nullable=False)
    artist_id = Column(Integer, ForeignKey("artists.id"), nullable=True)

    date = Column(String, nullable=False)
    start_time = Column(String)
    end_time = Column(String)

    title = Column(String)
    pay = Column(Integer, default=0)
    notes = Column(Text)
    styles = Column(String)

    status = Column(String, default="open")

    artist_type = Column(String)
    band_formats = Column(String)

    is_recurring = Column(Boolean, default=False)
    recurring_group_id = Column(String)
    recurrence_pattern = Column(Text)
    recurring = Column(Integer)
    recurring_interval_weeks = Column(Integer)
    recurring_days_of_week = Column(Text)
    recurring_end_type = Column(Text)
    recurring_end_after = Column(Integer)
    recurring_end_by_date = Column(Text)

    is_multi_slot = Column(Integer, default=0)
    frequency_exempt = Column(Integer, default=0)

    contract_hold_artist_id = Column(Integer)
    contract_hold_expires_at = Column(Text)

    created_at = Column(DateTime, default=datetime.utcnow)

    venue = relationship("Venue")
    artist = relationship("Artist")


class GigSlot(Base):
    __tablename__ = "gig_slots"

    id = Column(Integer, primary_key=True)
    gig_id = Column(Integer, ForeignKey("gigs.id", ondelete="CASCADE"), nullable=False)
    slot_number = Column(Integer, nullable=False)
    start_time = Column(String, nullable=False)
    end_time = Column(String, nullable=False)
    pay = Column(Float, default=0)
    artist_id = Column(Integer, ForeignKey("artists.id"), nullable=True)
    status = Column(String, default="open")
    artist_type = Column(String)
    band_formats = Column(String)
    styles = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)

    gig = relationship("Gig")
    artist = relationship("Artist")


class PreferredArtist(Base):
    __tablename__ = "preferred_artists"

    id = Column(Integer, primary_key=True)
    venue_id = Column(Integer, ForeignKey("venues.id"), nullable=False)
    artist_id = Column(Integer, ForeignKey("artists.id"), nullable=False)

    status = Column(String, default="pending")
    frequency_days_override = Column(Integer, nullable=True)
    pay_dollars_override = Column(Integer, nullable=True)
    pay_cents_override = Column(Integer, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (UniqueConstraint("venue_id", "artist_id"),)

    venue = relationship("Venue")
    artist = relationship("Artist")


class ArtistMedia(Base):
    __tablename__ = "artist_media"

    id = Column(Integer, primary_key=True)
    artist_id = Column(Integer, ForeignKey("artists.id"), nullable=False)

    media_type = Column(String)
    title = Column(String)
    file_path = Column(String)
    video_url = Column(String)
    display_order = Column(Integer, default=0)

    artist = relationship("Artist")


class VenueMedia(Base):
    __tablename__ = "venue_media"

    id = Column(Integer, primary_key=True)
    venue_id = Column(Integer, ForeignKey("venues.id"), nullable=False)

    media_type = Column(String, nullable=False)
    title = Column(String)
    file_path = Column(String)
    video_url = Column(String)
    display_order = Column(Integer, default=0)

    venue = relationship("Venue", backref="media")


class Notification(Base):
    __tablename__ = "notifications"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)

    notification_type = Column(String, nullable=False)
    title = Column(String, nullable=False)
    message = Column(Text, nullable=False)

    gig_id = Column(Integer, ForeignKey("gigs.id"), nullable=True)
    venue_id = Column(Integer, ForeignKey("venues.id"), nullable=True)
    artist_id = Column(Integer, ForeignKey("artists.id"), nullable=True)

    cancellation_reason = Column(Text, nullable=True)
    entity_type = Column(String)
    entity_id = Column(Integer)
    action_token = Column(String)

    is_read = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User")
    gig = relationship("Gig")
    venue = relationship("Venue")
    artist = relationship("Artist")


class EntityUser(Base):
    __tablename__ = "entity_users"

    id = Column(Integer, primary_key=True)
    entity_type = Column(Text, nullable=False)
    entity_id = Column(Integer, nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    role = Column(Text, default="member")
    added_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (UniqueConstraint("entity_type", "entity_id", "user_id"),)

    user = relationship("User", foreign_keys=[user_id])
    added_by = relationship("User", foreign_keys=[added_by_user_id])


class EntityInvitation(Base):
    __tablename__ = "entity_invitations"

    id = Column(Integer, primary_key=True)
    entity_type = Column(Text, nullable=False)
    entity_id = Column(Integer, nullable=False)
    entity_name = Column(Text, nullable=False)
    invited_email = Column(Text, nullable=False)
    invited_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    inviter_first_name = Column(Text)
    inviter_last_name = Column(Text)
    token = Column(Text, nullable=False, unique=True)
    status = Column(Text, default="pending")
    created_at = Column(DateTime, default=datetime.utcnow)
    responded_at = Column(DateTime)

    invited_by = relationship("User")


class VenueContract(Base):
    __tablename__ = "venue_contracts"

    id = Column(Integer, primary_key=True)
    venue_id = Column(Integer, ForeignKey("venues.id"), nullable=False)
    contract_type = Column(Text, nullable=False)
    name = Column(Text, nullable=False, default="Standard Contract")
    is_active = Column(Integer, default=1)
    require_for_booking = Column(Integer, default=0)
    per_gig_pdf = Column(Integer, default=0)
    pdf_file_path = Column(Text)
    contract_body = Column(Text)
    custom_fields = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    venue = relationship("Venue")


class GigContract(Base):
    __tablename__ = "gig_contracts"

    id = Column(Integer, primary_key=True)
    gig_id = Column(Integer, ForeignKey("gigs.id"), nullable=False)
    venue_contract_id = Column(Integer, ForeignKey("venue_contracts.id"), nullable=False)
    venue_id = Column(Integer, ForeignKey("venues.id"), nullable=False)
    artist_id = Column(Integer, ForeignKey("artists.id"), nullable=False)
    contract_type = Column(Text, nullable=False)
    rendered_body = Column(Text)
    filled_fields = Column(Text)
    pdf_file_path = Column(Text)
    signed_pdf_path = Column(Text)
    status = Column(Text, default="pending")
    artist_signature_name = Column(Text)
    artist_signature_date = Column(DateTime)
    artist_signature_ip = Column(Text)
    venue_signature_name = Column(Text)
    venue_signature_date = Column(DateTime)
    venue_signature_ip = Column(Text)
    hold_expires_at = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)

    gig = relationship("Gig")
    venue_contract = relationship("VenueContract")
    venue = relationship("Venue")
    artist = relationship("Artist")


class GigEmailLog(Base):
    __tablename__ = "gig_email_log"

    id = Column(Integer, primary_key=True)
    gig_id = Column(Integer, ForeignKey("gigs.id"), nullable=False)
    venue_id = Column(Integer, ForeignKey("venues.id"), nullable=False)
    notification_key = Column(Text, nullable=False)
    sent_at = Column(DateTime, default=datetime.utcnow)
    recipient_count = Column(Integer, default=0)

    __table_args__ = (UniqueConstraint("gig_id", "notification_key"),)

    gig = relationship("Gig")
    venue = relationship("Venue")


class EmailSetting(Base):
    __tablename__ = "email_settings"

    id = Column(Integer, primary_key=True)
    smtp_server = Column(Text)
    smtp_port = Column(Integer)
    smtp_username = Column(Text)
    smtp_password = Column(Text)
    from_email = Column(Text)
    from_name = Column(Text)
    enabled = Column(Integer, default=1)


class EmailTemplate(Base):
    __tablename__ = "email_templates"

    id = Column(Integer, primary_key=True)
    template_key = Column(Text, nullable=False, unique=True)
    subject = Column(Text)
    body = Column(Text)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class EmailPreference(Base):
    __tablename__ = "email_preferences"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    notification_type = Column(Text, nullable=False)
    enabled = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (UniqueConstraint("user_id", "notification_type"),)

    user = relationship("User")


class SmsPreference(Base):
    __tablename__ = "sms_preferences"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    notification_type = Column(Text, nullable=False)
    enabled = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (UniqueConstraint("user_id", "notification_type"),)

    user = relationship("User")


class UserSetting(Base):
    __tablename__ = "user_settings"

    user_id = Column(Integer, ForeignKey("users.id"), primary_key=True)
    setting_key = Column(Text, primary_key=True)
    setting_value = Column(Text)

    __table_args__ = (UniqueConstraint("user_id", "setting_key"),)


class VenueEmailNotification(Base):
    __tablename__ = "venue_email_notifications"

    id = Column(Integer, primary_key=True)
    venue_id = Column(Integer, ForeignKey("venues.id"), nullable=False)
    notification_key = Column(Text, nullable=False)
    enabled = Column(Integer, default=0)
    time_value = Column(Integer, default=1)
    time_unit = Column(Text, default="weeks")
    radius_miles = Column(Integer, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (UniqueConstraint("venue_id", "notification_key"),)

    venue = relationship("Venue")


class VenueEmailHistory(Base):
    __tablename__ = "venue_email_history"

    id = Column(Integer, primary_key=True)
    venue_id = Column(Integer, ForeignKey("venues.id"), nullable=False)
    venue_name = Column(Text, nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    subject = Column(Text, nullable=False)
    body = Column(Text, nullable=False)
    recipient_count = Column(Integer, nullable=False)
    sent_at = Column(DateTime, default=datetime.utcnow)
    recipients_json = Column(Text)

    venue = relationship("Venue")
    user = relationship("User")


class PaymentMethod(Base):
    __tablename__ = "payment_methods"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)

    payment_type = Column(String, nullable=False)
    account_identifier = Column(String, nullable=False)
    account_display_name = Column(String)

    is_preferred = Column(Boolean, default=False)
    is_verified = Column(Boolean, default=False)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = relationship("User", backref="payment_methods")


class EntityPaymentSetting(Base):
    __tablename__ = "entity_payment_settings"

    id = Column(Integer, primary_key=True)
    entity_type = Column(String, nullable=False)
    entity_id = Column(Integer, nullable=False)
    default_payment_method = Column(String, default="stripe")
    stripe_account_id = Column(String)
    stripe_publishable_key = Column(String)
    stripe_secret_key = Column(String)
    stripe_onboarding_complete = Column(Boolean, default=False)
    stripe_customer_id = Column(String)
    stripe_payment_method_id = Column(String)
    stripe_connect_account_id = Column(String)
    stripe_connect_onboarding_complete = Column(Boolean, default=False)
    affiliate_stripe_connect_account_id = Column(String)
    affiliate_stripe_connect_onboarding_complete = Column(Boolean, default=False)
    paypal_email = Column(String)
    venmo_username = Column(String)
    zelle_email = Column(String)
    cashapp_cashtag = Column(String)
    bank_account_last4 = Column(String)
    bank_routing_last4 = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (UniqueConstraint("entity_type", "entity_id"),)


class Transaction(Base):
    __tablename__ = "transactions"

    id = Column(Integer, primary_key=True)
    gig_id = Column(Integer, ForeignKey("gigs.id"), nullable=False)

    from_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    to_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    artist_id = Column(Integer, ForeignKey("artists.id"), nullable=True)

    amount_cents = Column(Integer, nullable=False)
    venue_charge_cents = Column(Integer, nullable=False)
    artist_payout_cents = Column(Integer, nullable=False)
    commission_cents = Column(Integer, nullable=False)
    credit_card_fee_cents = Column(Integer, default=0)

    payment_method_type = Column(String)
    payment_method_from = Column(String)
    payment_method_to = Column(String)

    status = Column(String, default="pending")

    scheduled_process_at = Column(DateTime, nullable=True)
    processed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    charge_attempts = Column(Integer, default=0)
    last_charge_attempt_at = Column(DateTime)
    charge_failure_reason = Column(Text)
    cancel_reason = Column(Text)
    cancelled_at = Column(DateTime)
    platform_fee_charged_cents = Column(Integer, default=0)

    notes = Column(Text)

    stripe_payment_intent_id = Column(String)
    stripe_transfer_id = Column(String)
    external_transaction_id = Column(String)

    gig = relationship("Gig")
    from_user = relationship("User", foreign_keys=[from_user_id])
    to_user = relationship("User", foreign_keys=[to_user_id])
    artist = relationship("Artist")


class PaymentCancellation(Base):
    __tablename__ = "payment_cancellations"

    id = Column(Integer, primary_key=True)
    transaction_id = Column(Integer, ForeignKey("transactions.id"), nullable=False)
    gig_id = Column(Integer, ForeignKey("gigs.id"), nullable=False)
    cancelled_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    cancellation_reason = Column(Text, nullable=False)
    cancelled_at = Column(DateTime, default=datetime.utcnow)

    transaction = relationship("Transaction")
    gig = relationship("Gig")
    cancelled_by = relationship("User")


class PlatformSetting(Base):
    __tablename__ = "platform_settings"

    id = Column(Integer, primary_key=True)
    setting_key = Column(String, unique=True, nullable=False)
    setting_value = Column(String, nullable=False)
    description = Column(Text)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    updated_by = Column(Integer, ForeignKey("users.id"), nullable=True)


class PublicActivity(Base):
    __tablename__ = "public_activity"

    id = Column(Integer, primary_key=True)
    event_type = Column(Text, nullable=False)
    event_data = Column(Text)
    city = Column(Text)
    state = Column(Text)
    venue_id = Column(Integer, ForeignKey("venues.id"), nullable=True)
    artist_id = Column(Integer, ForeignKey("artists.id"), nullable=True)
    gig_id = Column(Integer, ForeignKey("gigs.id"), nullable=True)
    ip_hash = Column(Text)
    user_agent = Column(Text)
    session_id = Column(Text)
    referrer = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)

    venue = relationship("Venue")
    artist = relationship("Artist")
    gig = relationship("Gig")


class City(Base):
    __tablename__ = "cities"

    id = Column(Integer, primary_key=True)
    city = Column(Text, nullable=False)
    state = Column(Text, nullable=False)
    lat = Column(Float)
    lon = Column(Float)

    __table_args__ = (UniqueConstraint("city", "state"),)


class VenuePaymentOverride(Base):
    __tablename__ = "venue_payment_overrides"

    id = Column(Integer, primary_key=True)
    venue_id = Column(Integer, ForeignKey("venues.id"), nullable=False, unique=True)
    payments_suspended = Column(Boolean, default=True)
    suspended_by = Column(Integer, ForeignKey("users.id"))
    suspended_at = Column(DateTime, default=datetime.utcnow)
    notes = Column(Text)

    venue = relationship("Venue")
    suspended_by_user = relationship("User")


class SupportTicket(Base):
    __tablename__ = "support_tickets"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    user_email = Column(Text)
    user_name = Column(Text)
    category = Column(Text, nullable=False)
    subject = Column(Text, nullable=False)
    description = Column(Text, nullable=False)
    status = Column(Text, default="open")
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User")


class Recommendation(Base):
    __tablename__ = "recommendations"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    user_name = Column(Text)
    recipient_email = Column(Text, nullable=False)
    recipient_name = Column(Text)
    message = Column(Text)
    sent_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User")


class ArtistInvitation(Base):
    __tablename__ = "artist_invitations"

    id = Column(Integer, primary_key=True)
    venue_id = Column(Integer, ForeignKey("venues.id"), nullable=False)
    venue_name = Column(Text, nullable=False)
    invited_email = Column(Text, nullable=False)
    invited_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    inviter_name = Column(Text)
    message = Column(Text)
    status = Column(Text, default="pending")
    sent_at = Column(DateTime, default=datetime.utcnow)
    signed_up_at = Column(DateTime)
    signed_up_user_id = Column(Integer, ForeignKey("users.id"))
    resent_count = Column(Integer, default=0)
    last_resent_at = Column(DateTime)

    venue = relationship("Venue")
    invited_by = relationship("User", foreign_keys=[invited_by_user_id])
    signed_up_user = relationship("User", foreign_keys=[signed_up_user_id])


class W9Form(Base):
    __tablename__ = "w9_forms"

    id = Column(Integer, primary_key=True)
    entity_type = Column(Text, nullable=False)
    entity_id = Column(Integer, nullable=False)
    tax_name = Column(Text, nullable=False)
    business_name = Column(Text)
    tax_classification = Column(Text, nullable=False)
    other_classification = Column(Text)
    exempt_payee_code = Column(Text)
    fatca_exemption_code = Column(Text)
    address_line_1 = Column(Text)
    address_line_2 = Column(Text)
    city = Column(Text)
    state = Column(Text)
    zip_code = Column(Text)
    tin_type = Column(Text, nullable=False)
    tin_encrypted = Column(Text, nullable=False)
    tin_last4 = Column(Text, nullable=False)
    certified_at = Column(DateTime)
    tax_year = Column(Integer)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (UniqueConstraint("entity_type", "entity_id", "tax_year"),)


class VenueTaxSetting(Base):
    __tablename__ = "venue_tax_settings"

    id = Column(Integer, primary_key=True)
    venue_id = Column(Integer, ForeignKey("venues.id"), nullable=False, unique=True)
    require_w9 = Column(Integer, default=0)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    venue = relationship("Venue")


class Tax1099(Base):
    __tablename__ = "tax_1099s"

    id = Column(Integer, primary_key=True)
    venue_id = Column(Integer, ForeignKey("venues.id"), nullable=False)
    artist_id = Column(Integer, ForeignKey("artists.id"), nullable=False)
    tax_year = Column(Integer, nullable=False)
    total_earnings_cents = Column(Integer, nullable=False, default=0)
    gig_count = Column(Integer, default=0)
    artist_name = Column(Text)
    artist_tin_last4 = Column(Text)
    artist_address = Column(Text)
    venue_name = Column(Text)
    venue_address = Column(Text)
    venue_tin_last4 = Column(Text)
    status = Column(Text, default="generated")
    sent_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (UniqueConstraint("venue_id", "artist_id", "tax_year"),)

    venue = relationship("Venue")
    artist = relationship("Artist")


class ProLicense(Base):
    __tablename__ = "pro_licenses"

    id = Column(Integer, primary_key=True)
    venue_id = Column(Integer, ForeignKey("venues.id"), nullable=False)
    pro_name = Column(Text, nullable=False)
    license_number = Column(Text)
    expiration_date = Column(Text)
    license_file_path = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (UniqueConstraint("venue_id", "pro_name"),)

    venue = relationship("Venue")


# ── Affiliate Program ──────────────────────────────────────────────────────────

class AffiliateRecommendEmail(Base):
    __tablename__ = "affiliate_recommend_emails"

    id = Column(Integer, primary_key=True)
    sender_user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    recipient_email = Column(Text, nullable=False)
    affiliate_code = Column(Text, nullable=False)
    sent_at = Column(DateTime, default=datetime.utcnow)
    clicked = Column(Integer, default=0)
    clicked_at = Column(DateTime)

    sender = relationship("User")


class AffiliateReferral(Base):
    __tablename__ = "affiliate_referrals"

    id = Column(Integer, primary_key=True)
    affiliate_user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    venue_id = Column(Integer, ForeignKey("venues.id", ondelete="CASCADE"), nullable=False)
    link_method = Column(Text, nullable=False, default="cookie")
    initial_rate_percent = Column(Float, nullable=False, default=1.0)
    reduced_rate_percent = Column(Float, nullable=False, default=0.5)
    reduced_after_days = Column(Integer, nullable=False, default=365)
    linked_at = Column(DateTime, default=datetime.utcnow)
    manually_linked_by = Column(Integer, ForeignKey("users.id"))

    __table_args__ = (UniqueConstraint("venue_id"),)

    affiliate_user = relationship("User", foreign_keys=[affiliate_user_id])
    venue = relationship("Venue")


class AffiliateEarning(Base):
    __tablename__ = "affiliate_earnings"

    id = Column(Integer, primary_key=True)
    affiliate_user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    venue_id = Column(Integer, ForeignKey("venues.id", ondelete="CASCADE"), nullable=False)
    transaction_id = Column(Integer, ForeignKey("transactions.id", ondelete="CASCADE"), nullable=False)
    gig_fee_cents = Column(Integer, nullable=False, default=0)
    rate_percent = Column(Float, nullable=False, default=1.0)
    earned_cents = Column(Integer, nullable=False, default=0)
    quarter = Column(Text, nullable=False)
    payout_id = Column(Integer, ForeignKey("affiliate_payouts.id"))
    accrued_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (UniqueConstraint("transaction_id"),)

    affiliate_user = relationship("User")
    venue = relationship("Venue")


class AffiliatePayout(Base):
    __tablename__ = "affiliate_payouts"

    id = Column(Integer, primary_key=True)
    affiliate_user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    quarter = Column(Text, nullable=False)
    total_cents = Column(Integer, nullable=False, default=0)
    status = Column(Text, nullable=False, default="processing")
    stripe_transfer_id = Column(Text)
    notes = Column(Text)
    paid_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (UniqueConstraint("affiliate_user_id", "quarter"),)

    affiliate_user = relationship("User")
