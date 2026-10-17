import unittest
from unittest.mock import MagicMock, patch

import psycopg

from booking_rules import InvalidBooking
from storage import PostgresStore, StorageUnavailable


class PostgresErrorTests(unittest.TestCase):
    def setUp(self):
        self.store = PostgresStore('postgresql://synthetic.invalid/test')
        self.store.ready = True
        self.connection = MagicMock()
        self.connection.__enter__.return_value = self.connection
        self.connection.execute.return_value.fetchone.return_value = ([],)

    def test_booking_validation_reaches_api_without_becoming_storage_error(self):
        error = InvalidBooking('Please choose a future date and time.')

        def change(rows):
            raise error

        with patch('psycopg.connect', return_value=self.connection):
            with self.assertRaises(InvalidBooking) as caught:
                self.store.update('bookings', [], change)
        self.assertIs(caught.exception, error)
        self.assertIs(self.connection.__exit__.call_args.args[1], error)

    def test_invalid_connection_settings_remain_private_storage_errors(self):
        error = ValueError('Synthetic private connection settings')
        with patch('psycopg.conninfo.conninfo_to_dict', side_effect=error):
            with self.assertRaises(StorageUnavailable) as caught:
                self.store.read('bookings', [])
        self.assertNotIn('private', str(caught.exception))

    def test_database_connect_query_and_commit_failures_remain_storage_errors(self):
        for stage in ('connect', 'query', 'commit'):
            with self.subTest(stage=stage):
                self.setUp()
                error = psycopg.OperationalError('Synthetic private database details')
                if stage == 'query':
                    self.connection.execute.side_effect = error
                elif stage == 'commit':
                    self.connection.__exit__.side_effect = error
                with patch('psycopg.connect', return_value=self.connection,
                           side_effect=error if stage == 'connect' else None):
                    with self.assertRaises(StorageUnavailable) as caught:
                        self.store.update('bookings', [], lambda rows: rows.append({}))
                self.assertNotIn('private', str(caught.exception))
