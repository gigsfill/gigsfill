"""
Phase 7 — Service Layer Unit Tests
====================================
Tests for gig_cleanup, notification_service, and email_dispatch modules.
"""
import pytest
from sqlalchemy import text
from datetime import date, timedelta


# ─── gig_cleanup tests ────────────────────────────────────────

class TestCleanupGigRecords:
    """Test cleanup_gig_records: removes transactions, contracts, cancellations, contract notifications."""

    def test_full_cleanup_removes_all_related_records(self, db, seed_booked_gig):
        """Full cleanup (no artist_id) removes everything for the gig."""
        from backend.services.gig_cleanup import cleanup_gig_records
        gig_id = seed_booked_gig["gig_id"]

        # Verify records exist before cleanup
        assert db.execute(text("SELECT COUNT(*) FROM transactions WHERE gig_id = :g"), {"g": gig_id}).scalar() == 1
        assert db.execute(text("SELECT COUNT(*) FROM gig_contracts WHERE gig_id = :g"), {"g": gig_id}).scalar() == 1
        assert db.execute(text("SELECT COUNT(*) FROM payment_cancellations WHERE gig_id = :g"), {"g": gig_id}).scalar() == 1
        # Contract-type notifications
        notif_count = db.execute(
            text("SELECT COUNT(*) FROM notifications WHERE gig_id = :g AND notification_type IN ('contract_signed', 'contract_countersigned', 'gig_booked', 'contract_countersign_needed', 'contract_artist_signed', 'contract_pending')"),
            {"g": gig_id}
        ).scalar()
        assert notif_count >= 3

        cleanup_gig_records(db, gig_id)

        # Verify all removed
        assert db.execute(text("SELECT COUNT(*) FROM transactions WHERE gig_id = :g"), {"g": gig_id}).scalar() == 0
        assert db.execute(text("SELECT COUNT(*) FROM gig_contracts WHERE gig_id = :g"), {"g": gig_id}).scalar() == 0
        assert db.execute(text("SELECT COUNT(*) FROM payment_cancellations WHERE gig_id = :g"), {"g": gig_id}).scalar() == 0

    def test_artist_scoped_cleanup_only_removes_that_artist(self, db, seed_multi_slot_gig):
        """Cleanup with artist_id only removes that artist's records, keeps others."""
        from backend.services.gig_cleanup import cleanup_gig_records
        gig_id = seed_multi_slot_gig["gig_id"]
        alice_id = seed_multi_slot_gig["artist_alice"]
        charlie_id = seed_multi_slot_gig["artist_charlie"]

        # Before: 2 transactions, 2 contracts
        assert db.execute(text("SELECT COUNT(*) FROM transactions WHERE gig_id = :g"), {"g": gig_id}).scalar() == 2
        assert db.execute(text("SELECT COUNT(*) FROM gig_contracts WHERE gig_id = :g"), {"g": gig_id}).scalar() == 2

        cleanup_gig_records(db, gig_id, artist_id=alice_id)

        # Alice's records removed
        assert db.execute(text(
            "SELECT COUNT(*) FROM transactions WHERE gig_id = :g AND artist_id = :a"
        ), {"g": gig_id, "a": alice_id}).scalar() == 0
        assert db.execute(text(
            "SELECT COUNT(*) FROM gig_contracts WHERE gig_id = :g AND artist_id = :a"
        ), {"g": gig_id, "a": alice_id}).scalar() == 0

        # Charlie's records still exist
        assert db.execute(text(
            "SELECT COUNT(*) FROM transactions WHERE gig_id = :g AND artist_id = :a"
        ), {"g": gig_id, "a": charlie_id}).scalar() == 1
        assert db.execute(text(
            "SELECT COUNT(*) FROM gig_contracts WHERE gig_id = :g AND artist_id = :a"
        ), {"g": gig_id, "a": charlie_id}).scalar() == 1

    def test_cleanup_nonexistent_gig_no_error(self, db, seed_users):
        """Cleanup on a gig that doesn't exist should not raise."""
        from backend.services.gig_cleanup import cleanup_gig_records
        cleanup_gig_records(db, 99999)  # Should not raise


class TestDeleteGigCompletely:
    """Test delete_gig_completely: removes all records + gig itself + slots."""

    def test_deletes_gig_and_all_related(self, db, seed_booked_gig):
        from backend.services.gig_cleanup import delete_gig_completely
        gig_id = seed_booked_gig["gig_id"]

        delete_gig_completely(db, gig_id)

        assert db.execute(text("SELECT COUNT(*) FROM gigs WHERE id = :g"), {"g": gig_id}).scalar() == 0
        assert db.execute(text("SELECT COUNT(*) FROM transactions WHERE gig_id = :g"), {"g": gig_id}).scalar() == 0
        assert db.execute(text("SELECT COUNT(*) FROM gig_contracts WHERE gig_id = :g"), {"g": gig_id}).scalar() == 0

    def test_deletes_multi_slot_gig_and_slots(self, db, seed_multi_slot_gig):
        from backend.services.gig_cleanup import delete_gig_completely
        gig_id = seed_multi_slot_gig["gig_id"]

        delete_gig_completely(db, gig_id)

        assert db.execute(text("SELECT COUNT(*) FROM gigs WHERE id = :g"), {"g": gig_id}).scalar() == 0
        assert db.execute(text("SELECT COUNT(*) FROM gig_slots WHERE gig_id = :g"), {"g": gig_id}).scalar() == 0


# ─── notification_service tests ───────────────────────────────

class TestCreateNotification:
    """Test create_notification: single INSERT wrapper."""

    def test_creates_notification(self, db, seed_users):
        from backend.services.notification_service import create_notification

        create_notification(db, 1, "gig_booked", "Gig Booked", "Test message",
                           gig_id=100, venue_id=20, artist_id=10)
        db.commit()

        row = db.execute(text("SELECT * FROM notifications WHERE user_id = 1")).mappings().first()
        assert row is not None
        assert row["notification_type"] == "gig_booked"
        assert row["title"] == "Gig Booked"
        assert row["message"] == "Test message"
        assert row["gig_id"] == 100

    def test_creates_with_cancellation_reason(self, db, seed_users):
        from backend.services.notification_service import create_notification

        create_notification(db, 2, "gig_cancelled", "Cancelled", "Gig cancelled",
                           gig_id=100, venue_id=20, cancellation_reason="Double booked")
        db.commit()

        row = db.execute(text("SELECT cancellation_reason FROM notifications WHERE user_id = 2")).fetchone()
        assert row[0] == "Double booked"


class TestNotifyGigBooked:
    """Test notify_gig_booked: handles same-user deduplication."""

    def test_different_users_get_two_notifications(self, db, seed_booked_gig):
        from backend.services.notification_service import notify_gig_booked

        # Clear existing notifications first
        db.execute(text("DELETE FROM notifications"))
        db.commit()

        gig_details = {
            "artist_user_id": 1, "venue_user_id": 2,
            "artist_name": "Alice Band", "venue_name": "Bobs Bar",
            "date": "2026-03-01", "start_time": "20:00", "end_time": "23:00",
        }
        notify_gig_booked(db, gig_details, 100, 20, 10)
        db.commit()

        count = db.execute(text("SELECT COUNT(*) FROM notifications")).scalar()
        assert count == 2

        # Artist notification
        artist_notif = db.execute(text(
            "SELECT message FROM notifications WHERE user_id = 1"
        )).fetchone()
        assert "Bobs Bar" in artist_notif[0]

        # Venue notification
        venue_notif = db.execute(text(
            "SELECT message FROM notifications WHERE user_id = 2"
        )).fetchone()
        assert "Alice Band" in venue_notif[0]

    def test_same_user_gets_one_combined_notification(self, db, seed_same_user_gig):
        from backend.services.notification_service import notify_gig_booked

        gig_details = {
            "artist_user_id": 3, "venue_user_id": 3,
            "artist_name": "Charlie Solo", "venue_name": "Charlies Club",
            "date": "2026-03-10", "start_time": "21:00", "end_time": "23:30",
        }
        notify_gig_booked(db, gig_details, 300, 21, 11)
        db.commit()

        count = db.execute(text(
            "SELECT COUNT(*) FROM notifications WHERE user_id = 3 AND notification_type = 'gig_booked'"
        )).scalar()
        assert count == 1  # ONE combined, not two

        msg = db.execute(text(
            "SELECT message FROM notifications WHERE user_id = 3 AND notification_type = 'gig_booked'"
        )).fetchone()[0]
        assert "Charlie Solo" in msg
        assert "Charlies Club" in msg


class TestNotifyGigCancelled:
    """Test notify_gig_cancelled: handles artist/venue cancel and slot info."""

    def test_artist_cancel_notifies_both_parties(self, db, seed_users, seed_entities):
        from backend.services.notification_service import notify_gig_cancelled

        gig_details = {
            "artist_user_id": 1, "venue_user_id": 2,
            "artist_name": "Alice Band", "venue_name": "Bobs Bar",
            "date": "2026-03-01",
        }
        notify_gig_cancelled(db, gig_details, 100, 20, 10,
                             cancelled_by="artist", cancellation_reason="Schedule conflict")
        db.commit()

        # Both users get notified
        assert db.execute(text("SELECT COUNT(*) FROM notifications WHERE user_id = 1")).scalar() == 1
        assert db.execute(text("SELECT COUNT(*) FROM notifications WHERE user_id = 2")).scalar() == 1

        # Venue sees artist name in message
        venue_msg = db.execute(text("SELECT message FROM notifications WHERE user_id = 2")).fetchone()[0]
        assert "Alice Band" in venue_msg

    def test_cancel_with_slot_info(self, db, seed_users, seed_entities):
        from backend.services.notification_service import notify_gig_cancelled

        gig_details = {
            "artist_user_id": 1, "venue_user_id": 2,
            "artist_name": "Alice Band", "venue_name": "Bobs Bar",
            "date": "2026-03-01",
        }
        notify_gig_cancelled(db, gig_details, 200, 20, 10,
                             cancelled_by="artist",
                             slot_info="Slot 1 (6:00 PM - 8:00 PM)")
        db.commit()

        msg = db.execute(text("SELECT message FROM notifications WHERE user_id = 2")).fetchone()[0]
        assert "Slot 1" in msg

    def test_same_user_cancel_gets_one_notification(self, db, seed_users, seed_entities):
        from backend.services.notification_service import notify_gig_cancelled

        gig_details = {
            "artist_user_id": 3, "venue_user_id": 3,
            "artist_name": "Charlie Solo", "venue_name": "Charlies Club",
            "date": "2026-03-10",
        }
        notify_gig_cancelled(db, gig_details, 300, 21, 11,
                             cancelled_by="artist", cancellation_reason="Changed plans")
        db.commit()

        count = db.execute(text("SELECT COUNT(*) FROM notifications WHERE user_id = 3")).scalar()
        assert count == 1


# ─── format_time_12hr tests ───────────────────────────────────

class TestFormatTime12hr:

    def test_pm(self):
        from backend.services.notification_service import format_time_12hr
        assert format_time_12hr("14:30") == "2:30 PM"

    def test_am(self):
        from backend.services.notification_service import format_time_12hr
        assert format_time_12hr("09:00") == "9:00 AM"

    def test_noon(self):
        from backend.services.notification_service import format_time_12hr
        assert format_time_12hr("12:00") == "12:00 PM"

    def test_midnight(self):
        from backend.services.notification_service import format_time_12hr
        assert format_time_12hr("00:00") == "12:00 AM"

    def test_empty(self):
        from backend.services.notification_service import format_time_12hr
        assert format_time_12hr("") == ""
        assert format_time_12hr(None) == ""

    def test_invalid(self):
        from backend.services.notification_service import format_time_12hr
        result = format_time_12hr("not-a-time")
        assert isinstance(result, str)


# ─── email_dispatch tests ─────────────────────────────────────

class TestEmailDispatchSafety:
    """Verify email dispatch functions don't crash when email service is unavailable."""

    def test_send_booking_emails_no_crash_without_email_service(self, db, seed_entities):
        """send_booking_emails should catch exceptions gracefully."""
        from backend.services.email_dispatch import send_booking_emails

        # This should NOT raise even if EmailService/SMTP isn't configured
        gig_details = {
            "artist_name": "Alice Band", "venue_name": "Bobs Bar",
            "artist_id": 10, "venue_id": 20,
            "date": "2026-03-01", "start_time": "20:00", "end_time": "23:00",
            "pay": 200.00,
        }
        # Should not raise
        send_booking_emails(db, gig_details)

    def test_send_cancellation_emails_no_crash(self, db, seed_entities):
        """send_cancellation_emails should catch exceptions gracefully."""
        from backend.services.email_dispatch import send_cancellation_emails

        gig_details = {
            "artist_name": "Alice Band", "venue_name": "Bobs Bar",
            "artist_id": 10, "venue_id": 20,
            "date": "2026-03-01",
        }
        # Should not raise
        send_cancellation_emails(db, gig_details, cancellation_reason="Test cancel")
