from datetime import datetime, timedelta, timezone
import unittest

from retention import booking_view, deletion_due, prune_bookings


class RetentionTests(unittest.TestCase):
    def booking(self, **changes):
        return {'id': 'TLS-test', 'date': '2026-06-01', 'time': '12:00',
                'status': 'pending', 'retentionVersion': 1, **changes}

    def test_exact_boundary_and_london_summer_time(self):
        row = self.booking()
        due = deletion_due(row)
        self.assertEqual(due.isoformat(), '2026-08-30T12:00:00+01:00')
        rows = [row]
        self.assertEqual(prune_bookings(rows, due - timedelta(seconds=1)), [])
        self.assertEqual(prune_bookings(rows, due), ['TLS-test'])
        self.assertEqual(rows, [])

    def test_future_confirmed_and_pending_survive_old_submission(self):
        rows = [self.booking(status=status, date='2027-01-01', createdAt='2020-01-01T00:00:00Z')
                for status in ['pending', 'confirmed']]
        self.assertEqual(prune_bookings(rows, datetime(2026, 10, 3, tzinfo=timezone.utc)), [])
        self.assertEqual(len(rows), 2)

    def test_closed_uses_closure_not_appointment_or_latest_edit(self):
        for status in ['declined', 'cancelled']:
            row = self.booking(status=status, date='2099-01-01', closedAt='2026-06-01T12:00:00Z',
                               updatedAt='2026-10-01T12:00:00Z')
            self.assertEqual(deletion_due(row), datetime(2026, 8, 30, 12, tzinfo=timezone.utc))

    def test_legacy_gets_one_review_window(self):
        row = self.booking(date='2020-01-01')
        del row['retentionVersion']
        rows = [row]
        now = datetime(2026, 10, 3, tzinfo=timezone.utc)
        self.assertEqual(prune_bookings(rows, now), [])
        self.assertEqual(deletion_due(row), now + timedelta(days=7))
        self.assertEqual(prune_bookings(rows, now + timedelta(days=6)), [])
        self.assertEqual(prune_bookings(rows, now + timedelta(days=7)), ['TLS-test'])

    def test_hold_and_incomplete_records_never_get_guessed_deletion(self):
        rows = [self.booking(retentionHold=True, holdReason='Dispute'), self.booking(date='bad'),
                self.booking(status='unknown'), self.booking(status='declined')]
        self.assertEqual(prune_bookings(rows, datetime(2030, 1, 1, tzinfo=timezone.utc)), [])
        for row in rows:
            self.assertIsNone(booking_view(row)['deleteAfter'])

    def test_hold_release_makes_overdue_record_eligible(self):
        rows = [self.booking(retentionHold=True)]
        now = datetime(2026, 10, 3, tzinfo=timezone.utc)
        self.assertEqual(prune_bookings(rows, now), [])
        rows[0]['retentionHold'] = False
        self.assertEqual(prune_bookings(rows, now), ['TLS-test'])


if __name__ == '__main__':
    unittest.main()
