"""
Phase 7 — Data Integrity & Edge Case Tests
=============================================
Tests that verify data consistency across booking/cancel/delete flows.
"""
import pytest
from sqlalchemy import text
from datetime import date, timedelta


class TestBookingDataIntegrity:
    """Verify booking flow creates correct related records."""

    def test_booked_gig_has_all_related_records(self, db, seed_booked_gig):
        """A booked gig should have transaction, contract, and notifications."""
        gig_id = seed_booked_gig["gig_id"]

        txn = db.execute(text("SELECT * FROM transactions WHERE gig_id = :g"), {"g": gig_id}).mappings().first()
        assert txn is not None
        assert txn["status"] == "pending"
        assert txn["artist_id"] == 10

        contract = db.execute(text("SELECT * FROM gig_contracts WHERE gig_id = :g"), {"g": gig_id}).mappings().first()
        assert contract is not None
        assert contract["status"] == "countersigned"

        notifs = db.execute(text(
            "SELECT * FROM notifications WHERE gig_id = :g ORDER BY user_id"
        ), {"g": gig_id}).mappings().all()
        assert len(notifs) >= 2  # At least one for each party

    def test_multi_slot_gig_has_per_artist_records(self, db, seed_multi_slot_gig):
        """Multi-slot gig should have separate transactions and contracts per artist."""
        gig_id = seed_multi_slot_gig["gig_id"]

        txns = db.execute(text(
            "SELECT artist_id FROM transactions WHERE gig_id = :g ORDER BY artist_id"
        ), {"g": gig_id}).fetchall()
        assert len(txns) == 2
        assert txns[0][0] == 10  # Alice
        assert txns[1][0] == 11  # Charlie

        contracts = db.execute(text(
            "SELECT artist_id FROM gig_contracts WHERE gig_id = :g ORDER BY artist_id"
        ), {"g": gig_id}).fetchall()
        assert len(contracts) == 2


class TestCancelDataIntegrity:
    """Verify cancel flows clean up ALL related records without leaving orphans."""

    def test_artist_cancel_removes_all_financial_records(self, db, seed_booked_gig):
        """After artist cancels, no transactions/contracts/cancellations should remain."""
        from backend.services.gig_cleanup import cleanup_gig_records
        gig_id = seed_booked_gig["gig_id"]

        cleanup_gig_records(db, gig_id)
        db.commit()

        assert db.execute(text("SELECT COUNT(*) FROM transactions WHERE gig_id = :g"), {"g": gig_id}).scalar() == 0
        assert db.execute(text("SELECT COUNT(*) FROM gig_contracts WHERE gig_id = :g"), {"g": gig_id}).scalar() == 0
        assert db.execute(text("SELECT COUNT(*) FROM payment_cancellations WHERE gig_id = :g"), {"g": gig_id}).scalar() == 0

    def test_cancel_removes_contract_notifications_keeps_cancel_notifications(self, db, seed_booked_gig):
        """Cleanup removes contract-type notifications but NOT gig_cancelled notifications."""
        from backend.services.gig_cleanup import cleanup_gig_records
        from backend.services.notification_service import create_notification
        gig_id = seed_booked_gig["gig_id"]

        # Add a cancel notification that should survive
        create_notification(db, 1, "gig_cancelled", "Cancelled", "Gig was cancelled",
                           gig_id=gig_id, venue_id=20, artist_id=10)
        db.commit()

        cleanup_gig_records(db, gig_id)
        db.commit()

        # Contract notifications removed
        contract_notifs = db.execute(text("""
            SELECT COUNT(*) FROM notifications
            WHERE gig_id = :g AND notification_type IN ('contract_signed', 'contract_countersigned', 'gig_booked')
        """), {"g": gig_id}).scalar()
        assert contract_notifs == 0

        # Cancel notification preserved
        cancel_notifs = db.execute(text("""
            SELECT COUNT(*) FROM notifications
            WHERE gig_id = :g AND notification_type = 'gig_cancelled'
        """), {"g": gig_id}).scalar()
        assert cancel_notifs == 1

    def test_multi_slot_cancel_one_artist_preserves_other(self, db, seed_multi_slot_gig):
        """Cancelling one artist in multi-slot preserves the other artist's records."""
        from backend.services.gig_cleanup import cleanup_gig_records
        gig_id = seed_multi_slot_gig["gig_id"]
        alice = seed_multi_slot_gig["artist_alice"]
        charlie = seed_multi_slot_gig["artist_charlie"]

        cleanup_gig_records(db, gig_id, artist_id=alice)
        db.commit()

        # Alice: gone
        assert db.execute(text(
            "SELECT COUNT(*) FROM transactions WHERE gig_id = :g AND artist_id = :a"
        ), {"g": gig_id, "a": alice}).scalar() == 0

        # Charlie: still there
        assert db.execute(text(
            "SELECT COUNT(*) FROM transactions WHERE gig_id = :g AND artist_id = :a"
        ), {"g": gig_id, "a": charlie}).scalar() == 1
        assert db.execute(text(
            "SELECT COUNT(*) FROM gig_contracts WHERE gig_id = :g AND artist_id = :a"
        ), {"g": gig_id, "a": charlie}).scalar() == 1


class TestDeleteDataIntegrity:
    """Verify delete flows remove gig + all related records completely."""

    def test_delete_gig_completely_no_orphans(self, db, seed_booked_gig):
        """After delete_gig_completely, nothing remains for this gig_id."""
        from backend.services.gig_cleanup import delete_gig_completely
        gig_id = seed_booked_gig["gig_id"]

        delete_gig_completely(db, gig_id)
        db.commit()

        for table in ['gig_slots', 'transactions', 'gig_contracts', 'payment_cancellations']:
            count = db.execute(text(f"SELECT COUNT(*) FROM {table} WHERE gig_id = :g"), {"g": gig_id}).scalar()
            assert count == 0, f"Orphan rows in {table}"
        # gigs uses 'id' not 'gig_id'
        assert db.execute(text("SELECT COUNT(*) FROM gigs WHERE id = :g"), {"g": gig_id}).scalar() == 0, "Orphan gig row"

    def test_delete_multi_slot_gig_removes_all_slots(self, db, seed_multi_slot_gig):
        """Deleting a multi-slot gig removes all slots."""
        from backend.services.gig_cleanup import delete_gig_completely
        gig_id = seed_multi_slot_gig["gig_id"]

        delete_gig_completely(db, gig_id)
        db.commit()

        assert db.execute(text("SELECT COUNT(*) FROM gig_slots WHERE gig_id = :g"), {"g": gig_id}).scalar() == 0
        assert db.execute(text("SELECT COUNT(*) FROM gigs WHERE id = :g"), {"g": gig_id}).scalar() == 0


class TestNotificationDeduplication:
    """Verify same-user dedup logic across all notification paths."""

    def test_book_same_user_one_notification(self, db, seed_users, seed_entities, seed_same_user_gig):
        from backend.services.notification_service import notify_gig_booked

        details = {
            "artist_user_id": 3, "venue_user_id": 3,
            "artist_name": "Charlie Solo", "venue_name": "Charlies Club",
            "date": "2026-03-10", "start_time": "21:00", "end_time": "23:30",
        }
        notify_gig_booked(db, details, 300, 21, 11)
        db.commit()

        # Should create exactly 1 notification, not 2
        count = db.execute(text(
            "SELECT COUNT(*) FROM notifications WHERE gig_id = 300 AND notification_type = 'gig_booked'"
        )).scalar()
        assert count == 1

    def test_cancel_same_user_one_notification(self, db, seed_users, seed_entities, seed_same_user_gig):
        from backend.services.notification_service import notify_gig_cancelled

        details = {
            "artist_user_id": 3, "venue_user_id": 3,
            "artist_name": "Charlie Solo", "venue_name": "Charlies Club",
            "date": "2026-03-10",
        }
        notify_gig_cancelled(db, details, 300, 21, 11,
                             cancelled_by="artist", cancellation_reason="Can't make it")
        db.commit()

        count = db.execute(text(
            "SELECT COUNT(*) FROM notifications WHERE gig_id = 300 AND notification_type = 'gig_cancelled'"
        )).scalar()
        assert count == 1

    def test_different_users_get_separate_notifications(self, db, seed_users, seed_entities, seed_booked_gig):
        from backend.services.notification_service import notify_gig_booked

        db.execute(text("DELETE FROM notifications"))
        db.commit()

        details = {
            "artist_user_id": 1, "venue_user_id": 2,
            "artist_name": "Alice Band", "venue_name": "Bobs Bar",
            "date": "2026-03-01", "start_time": "20:00", "end_time": "23:00",
        }
        notify_gig_booked(db, details, 100, 20, 10)
        db.commit()

        user1_count = db.execute(text("SELECT COUNT(*) FROM notifications WHERE user_id = 1")).scalar()
        user2_count = db.execute(text("SELECT COUNT(*) FROM notifications WHERE user_id = 2")).scalar()
        assert user1_count == 1
        assert user2_count == 1


class TestEdgeCases:
    """Edge cases and error handling."""

    def test_cleanup_empty_gig_no_error(self, db, seed_users, seed_entities):
        """Cleanup on a gig with no financial records should not error."""
        from backend.services.gig_cleanup import cleanup_gig_records
        
        gig_date = (date.today() + timedelta(days=5)).isoformat()
        db.execute(text(
            "INSERT INTO gigs (id, venue_id, date, status) VALUES (999, 20, :d, 'open')"
        ), {"d": gig_date})
        db.commit()

        cleanup_gig_records(db, 999)  # Should not raise

    def test_notification_with_none_values(self, db, seed_users):
        """Creating notification with optional None fields should work."""
        from backend.services.notification_service import create_notification

        create_notification(db, 1, "gig_cancelled", "Test", "Test msg",
                           gig_id=None, venue_id=None, artist_id=None,
                           cancellation_reason=None)
        db.commit()

        row = db.execute(text("SELECT * FROM notifications WHERE user_id = 1")).mappings().first()
        assert row is not None
        assert row["gig_id"] is None

    def test_double_cleanup_idempotent(self, db, seed_booked_gig):
        """Running cleanup twice should be safe (idempotent)."""
        from backend.services.gig_cleanup import cleanup_gig_records
        gig_id = seed_booked_gig["gig_id"]

        cleanup_gig_records(db, gig_id)
        db.commit()
        # Second call should not error
        cleanup_gig_records(db, gig_id)
        db.commit()

        assert db.execute(text("SELECT COUNT(*) FROM transactions WHERE gig_id = :g"), {"g": gig_id}).scalar() == 0

    def test_delete_already_deleted_gig(self, db, seed_booked_gig):
        """Deleting an already-deleted gig should be safe."""
        from backend.services.gig_cleanup import delete_gig_completely
        gig_id = seed_booked_gig["gig_id"]

        delete_gig_completely(db, gig_id)
        db.commit()
        # Second delete should not error
        delete_gig_completely(db, gig_id)
        db.commit()

    def test_format_time_edge_cases(self):
        """format_time_12hr handles edge cases."""
        from backend.services.notification_service import format_time_12hr

        assert format_time_12hr("00:00") == "12:00 AM"
        assert format_time_12hr("12:00") == "12:00 PM"
        assert format_time_12hr("23:59") == "11:59 PM"
        assert format_time_12hr("") == ""
        assert format_time_12hr(None) == ""
