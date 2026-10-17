import concurrent.futures
from contextlib import closing
from email import policy
from email.parser import BytesParser
import http.client
from io import BytesIO
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import uuid
import zipfile

# All tests use synthetic credentials/data; never load local secrets or send email.
with patch.dict(os.environ, {'VERCEL': '1', 'DATABASE_URL': '', 'ADMIN_PIN': ''}):
    import server
    import auth
    from storage import LocalStore, PostgresStore, StorageUnavailable, UnconfiguredStore

PASSWORD = 'synthetic-test-password-6842'
ROOT = Path(__file__).resolve().parents[1]


class QuietHandler(server.Handler):
    def log_message(self, *args):
        pass


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.store = LocalStore(self.temporary.name)
        self.patches = [patch.object(server, 'STORE', self.store),
                        patch.object(server, 'ADMIN_PIN', PASSWORD),
                        patch.object(server, 'ON_VERCEL', True),
                        patch.object(server, 'LOCAL_FILES', False),
                        patch.object(server, 'COOKIE_NAME', '__Host-tls_admin'),
                        patch.object(server, 'salon_notification', return_value=False),
                        patch.object(server, 'customer_status_email', return_value=False)]
        for item in self.patches:
            item.start()
        self.http = server.ThreadingHTTPServer(('127.0.0.1', 0), QuietHandler)
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.http.server_port

    def tearDown(self):
        self.http.shutdown()
        self.http.server_close()
        self.thread.join()
        for item in reversed(self.patches):
            item.stop()
        self.temporary.cleanup()

    def request(self, method, path, payload=None, cookie='', headers=None):
        request_headers = {'Origin': f'https://127.0.0.1:{self.port}',
                           'X-CSRF-Protection': '1', 'X-Vercel-Forwarded-For': '192.0.2.1'}
        if cookie:
            request_headers['Cookie'] = cookie
        body = None
        if payload is not None:
            body = json.dumps(payload)
            request_headers['Content-Type'] = 'application/json'
        request_headers.update(headers or {})
        with closing(http.client.HTTPConnection('127.0.0.1', self.port, timeout=10)) as connection:
            connection.request(method, path, body=body, headers=request_headers)
            response = connection.getresponse()
            data = response.read()
            return response.status, dict(response.getheaders()), data

    def login(self):
        status, headers, _ = self.request('POST', '/api/admin/login', {'password': PASSWORD})
        self.assertEqual(status, 200)
        return headers['Set-Cookie'].split(';')[0]

    def booking(self, service_index=0):
        category, name, variant, price = sorted(server.ALLOWED)[service_index]
        return {'name': 'Synthetic Customer', 'email': 'test@example.invalid', 'phone': '00000000000',
                'date': '2099-06-15', 'time': '12:00', 'notes': '<script>test</script>',
                'services': [{'category': category, 'name': name, 'variant': variant, 'price': price}]}

    def test_all_booking_reads_exports_and_writes_need_session(self):
        for path in ['/api/admin/bookings', '/api/admin/accepted-bookings',
                     '/api/admin/accepted-bookings/export?week=2099-W25']:
            with self.subTest(path=path):
                status, headers, body = self.request('GET', path, headers={'X-Admin-Pin': PASSWORD})
                self.assertEqual(status, 401)
                self.assertEqual(headers['Cache-Control'], 'no-store')
                self.assertNotIn(b'bookings.json', body)
        self.assertEqual(self.request('POST', '/api/admin/bookings/TLS-example', {'status': 'confirmed'})[0], 401)

    def test_invalid_configured_password_fails_closed(self):
        for password in ['', 'short', '1234567', '        ', ' 1234567 ', 'x' * 257]:
            with self.subTest(password=password), patch.object(server, 'ADMIN_PIN', password):
                self.assertEqual(self.request('POST', '/api/admin/login', {'password': password})[0], 503)
                self.assertEqual(self.request('GET', '/api/admin/bookings')[0], 503)

    def test_password_length_boundaries_allow_authenticated_access(self):
        for password in ['test-8pw', 'x' * 256]:
            with self.subTest(length=len(password)), patch.object(server, 'ADMIN_PIN', password):
                self.assertEqual(self.request('GET', '/api/admin/bookings')[0], 401)
                self.assertEqual(self.request('POST', '/api/admin/login', {'password': 'wrong'})[0], 401)
                status, headers, _ = self.request('POST', '/api/admin/login', {'password': password})
                self.assertEqual(status, 200)
                cookie = headers['Set-Cookie'].split(';')[0]
                self.assertEqual(self.request('GET', '/api/admin/bookings', cookie=cookie)[0], 200)

    def test_bad_password_and_invalid_payload(self):
        status, headers, _ = self.request('POST', '/api/admin/login', {'password': 'wrong'})
        self.assertEqual(status, 401)
        self.assertNotIn('Set-Cookie', headers)
        for payload in [[], {'password': 123}, {'password': None}]:
            self.assertEqual(self.request('POST', '/api/admin/login', payload)[0], 400)

    def test_cookie_login_expiry_rotation_and_logout(self):
        status, headers, _ = self.request('POST', '/api/admin/login', {'password': PASSWORD})
        self.assertEqual(status, 200)
        cookie_header = headers['Set-Cookie']
        for flag in ['HttpOnly', 'Secure', 'SameSite=Strict', 'Path=/', 'Max-Age=28800']:
            self.assertIn(flag, cookie_header)
        cookie = cookie_header.split(';')[0]
        token = cookie.split('=', 1)[1]
        self.assertNotIn(PASSWORD, cookie_header)
        stored = (Path(self.temporary.name) / 'admin-auth.json').read_text()
        self.assertNotIn(token, stored)
        self.assertNotIn(PASSWORD, stored)
        self.assertEqual(self.request('GET', '/api/admin/bookings', cookie=cookie)[0], 200)
        # A new instance reads the same stored session, not process memory.
        with patch.object(server, 'STORE', LocalStore(self.temporary.name)):
            self.assertEqual(self.request('GET', '/api/admin/bookings', cookie=cookie)[0], 200)
        with patch.object(auth.time, 'time', return_value=time.time() + auth.SESSION_SECONDS + 1):
            self.assertEqual(self.request('GET', '/api/admin/bookings', cookie=cookie)[0], 401)
        with patch.object(server, 'ADMIN_PIN', 'new-synthetic-password-6842'):
            self.assertEqual(self.request('GET', '/api/admin/bookings', cookie=cookie)[0], 401)
        status, headers, _ = self.request('POST', '/api/admin/logout', cookie=cookie)
        self.assertEqual(status, 200)
        self.assertIn('Max-Age=0', headers['Set-Cookie'])
        self.assertEqual(self.request('GET', '/api/admin/bookings', cookie=cookie)[0], 401)

    def test_cross_origin_and_missing_csrf_header_rejected(self):
        cookie = self.login()
        for path, payload in [('/api/admin/login', {'password': PASSWORD}),
                              ('/api/admin/logout', None),
                              ('/api/admin/bookings/TLS-example', {'status': 'confirmed'})]:
            for headers in [{'Origin': 'https://untrusted.invalid'}, {'Origin': ''},
                            {'X-CSRF-Protection': ''}]:
                self.assertEqual(self.request('POST', path, payload, cookie, headers)[0], 403)

    def test_login_rate_limit_is_shared_and_expires(self):
        for _ in range(5):
            self.assertEqual(self.request('POST', '/api/admin/login', {'password': 'wrong'})[0], 401)
        with patch.object(server, 'STORE', LocalStore(self.temporary.name)):
            status, headers, _ = self.request('POST', '/api/admin/login', {'password': PASSWORD})
            self.assertEqual(status, 429)
            self.assertGreater(int(headers['Retry-After']), 0)
        with patch.object(auth.time, 'time', return_value=time.time() + auth.LOGIN_WINDOW + 1):
            self.assertEqual(self.request('POST', '/api/admin/login', {'password': PASSWORD})[0], 200)

    def test_global_login_limit(self):
        for index in range(50):
            self.assertEqual(auth.login_limit(self.store, f'192.0.2.{index}', PASSWORD), 0)
        self.assertGreater(auth.login_limit(self.store, '198.51.100.1', PASSWORD), 0)

    def test_booking_create_confirm_export_decline(self):
        cookie = self.login()
        status, _, body = self.request('POST', '/api/bookings', self.booking())
        self.assertEqual(status, 201)
        booking_id = json.loads(body)['bookingId']
        self.assertEqual(self.request('POST', '/api/bookings', self.booking())[0], 409)
        status, _, body = self.request('POST', f'/api/admin/bookings/{booking_id}',
                                       {'status': 'confirmed', 'expectedRevision': 0, 'notifyCustomer': True, 'customerMessage': 'Synthetic note'}, cookie)
        self.assertEqual(status, 200)
        self.assertFalse(json.loads(body)['emailSent'])
        status, _, body = self.request('GET', '/api/admin/bookings', cookie=cookie)
        self.assertEqual(json.loads(body)['bookings'][0]['status'], 'confirmed')
        year, week, _ = server.datetime.strptime('2099-06-15', '%Y-%m-%d').isocalendar()
        status, headers, body = self.request('GET', f'/api/admin/accepted-bookings/export?week={year}-W{week:02}', cookie=cookie)
        self.assertEqual(status, 200)
        self.assertEqual(headers['X-Export-Count'], '1')
        self.assertNotIn('X-Saved-Copy', headers)
        with zipfile.ZipFile(BytesIO(body)) as workbook:
            sheet = workbook.read('xl/worksheets/sheet1.xml')
            self.assertIn(b'Synthetic Customer', sheet)
            self.assertIn(b'&lt;script&gt;', sheet)
        self.assertFalse((Path(self.temporary.name) / 'exports').exists())
        self.assertEqual(self.request('POST', f'/api/admin/bookings/{booking_id}', {'status': 'declined', 'expectedRevision': 1, 'internalNote': 'No practitioner available'}, cookie)[0], 200)
        body = self.request('GET', '/api/admin/accepted-bookings', cookie=cookie)[2]
        self.assertEqual(json.loads(body)['count'], 0)

    def test_concurrent_same_treatment_is_not_double_booked(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            statuses = list(pool.map(lambda _: self.request('POST', '/api/bookings', self.booking())[0], range(4)))
        self.assertEqual(sorted(statuses), [201, 409, 409, 409])
        self.assertEqual(len(self.store.read('bookings', [])), 1)

    def test_different_treatments_can_share_a_slot(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            statuses = list(pool.map(lambda i: self.request('POST', '/api/bookings', self.booking(i))[0], [0, 1]))
        self.assertEqual(statuses, [201, 201])
        self.assertEqual(len(self.store.read('bookings', [])), 2)

    def test_invalid_booking_and_update_return_json_errors(self):
        payload = self.booking()
        payload['services'][0]['price'] = -100
        self.assertEqual(self.request('POST', '/api/bookings', payload)[0], 400)
        cookie = self.login()
        self.assertEqual(self.request('POST', '/api/admin/bookings/TLS-example', [], cookie)[0], 400)
        self.assertEqual(self.request('GET', '/api/admin/accepted-bookings/export?week=wrong', cookie=cookie)[0], 400)

    def test_storage_failure_does_not_leak_details_or_claim_success(self):
        with patch.object(server, 'STORE', UnconfiguredStore()):
            status, _, body = self.request('POST', '/api/admin/login', {'password': PASSWORD})
            self.assertEqual(status, 503)
            self.assertNotIn(b'DATABASE_URL', body)
            self.assertEqual(self.request('POST', '/api/bookings', self.booking())[0], 503)

    def test_static_secrets_are_unreachable_and_health_is_minimal(self):
        for path in ['/settings.env', '/.env', '/server.py', '/data/bookings.json', '/assets/', '/api/unknown']:
            self.assertEqual(self.request('GET', path)[0], 404)
        status, _, body = self.request('GET', '/api/health')
        self.assertEqual(status, 200)
        self.assertEqual(set(json.loads(body)), {'ok', 'build'})
        self.assertEqual(self.request('HEAD', '/api/admin/bookings')[0], 405)

    def create_one(self, **changes):
        payload = {**self.booking(), **changes}
        status, _, body = self.request('POST', '/api/bookings', payload)
        self.assertEqual(status, 201, body)
        return json.loads(body)['bookingId']

    def edit(self, booking_id, cookie, **changes):
        return self.request('POST', f'/api/admin/bookings/{booking_id}', changes, cookie)

    def test_newest_submission_first_even_after_edit(self):
        older = self.create_one()
        newer = self.create_one(time='13:00')
        cookie = self.login()
        self.assertEqual(self.edit(older, cookie, expectedRevision=0, name='Edited customer')[0], 200)
        rows = json.loads(self.request('GET', '/api/admin/bookings', cookie=cookie)[2])['bookings']
        self.assertEqual([b['id'] for b in rows], [newer, older])

    def test_decline_reason_is_internal_and_email_is_explicit(self):
        booking_id = self.create_one()
        cookie = self.login()
        with patch.object(server, 'customer_status_email', return_value=True) as send:
            self.assertEqual(self.edit(booking_id, cookie, expectedRevision=0, status='declined')[0], 400)
            status, _, body = self.edit(booking_id, cookie, expectedRevision=0, status='declined',
                                      internalNote='PRIVATE staffing reason')
            self.assertEqual(status, 200)
            self.assertIsNone(json.loads(body)['emailSent'])
            send.assert_not_called()
            status, _, body = self.edit(booking_id, cookie, expectedRevision=1, status='pending',
                                      date='2099-06-16', notifyCustomer=True,
                                      customerMessage='Please confirm the new date.')
            self.assertEqual(status, 200, body)
            self.assertTrue(json.loads(body)['emailSent'])
            self.assertEqual(send.call_args.args[1:], ('pending', 'Please confirm the new date.'))
            saved = self.store.read('bookings', [])[0]
            self.assertEqual(saved['internalNote'], 'PRIVATE staffing reason')
            self.assertNotIn('closedAt', saved)

    def test_reschedule_reopen_conflicts_and_server_validation(self):
        booking_id = self.create_one()
        self.create_one(time='13:00')
        cookie = self.login()
        for changes, expected in [({'time': '13:00'}, 409), ({'date': '2000-01-01'}, 400),
                                  ({'time': '09:00'}, 400), ({'email': 'bad'}, 400),
                                  ({'services': []}, 400), ({'services': [{'name': 'fake'}]}, 400)]:
            with self.subTest(changes=changes):
                self.assertEqual(self.edit(booking_id, cookie, expectedRevision=0, **changes)[0], expected)
        original = self.store.read('bookings', [])[0]
        self.assertEqual(original['revision'], 0)
        self.assertEqual(self.edit(booking_id, cookie, expectedRevision=0, status='declined', internalNote='Unavailable')[0], 200)
        self.assertEqual(self.edit(booking_id, cookie, expectedRevision=1, status='pending', time='13:00')[0], 409)
        self.assertEqual(self.edit(booking_id, cookie, expectedRevision=1, status='confirmed', time='14:00')[0], 200)
        self.assertEqual(self.store.read('bookings', [])[0]['createdAt'], original['createdAt'])
        # The old slot becomes available; the rescheduled slot is now blocked.
        self.assertEqual(self.request('POST', '/api/bookings', self.booking())[0], 201)
        self.assertEqual(self.request('POST', '/api/bookings', {**self.booking(), 'time': '14:00'})[0], 409)

    def test_concurrent_edits_reject_stale_changes(self):
        booking_id = self.create_one()
        cookie = self.login()
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            statuses = list(pool.map(lambda value: self.edit(booking_id, cookie,
                expectedRevision=0, phone=value)[0], ['11111111111', '22222222222']))
        self.assertEqual(sorted(statuses), [200, 409])
        self.assertEqual(self.store.read('bookings', [])[0]['revision'], 1)

    def test_deletion_requires_session_csrf_confirmation_revision_and_no_hold(self):
        booking_id = self.create_one()
        cookie = self.login()
        path = f'/api/admin/bookings/{booking_id}'
        payload = {'confirmDelete': True, 'expectedRevision': 0}
        self.assertEqual(self.request('DELETE', path, payload)[0], 401)
        self.assertEqual(self.request('DELETE', path, payload, cookie, {'Origin': 'https://bad.invalid'})[0], 403)
        self.assertEqual(self.request('DELETE', path, payload, cookie, {'X-CSRF-Protection': ''})[0], 403)
        self.assertEqual(self.request('DELETE', path, {}, cookie)[0], 400)
        self.assertEqual(self.edit(booking_id, cookie, expectedRevision=0, retentionHold=True,
                                   holdReason='Unresolved complaint')[0], 200)
        self.assertEqual(self.request('DELETE', path, {**payload, 'expectedRevision': 1}, cookie)[0], 409)
        self.assertEqual(self.edit(booking_id, cookie, expectedRevision=1, retentionHold=False)[0], 200)
        self.assertEqual(self.request('DELETE', path, payload, cookie)[0], 409)
        with patch.object(server, 'customer_status_email') as send:
            self.assertEqual(self.request('DELETE', path, {**payload, 'expectedRevision': 2}, cookie)[0], 200)
            send.assert_not_called()
        self.assertEqual(self.store.read('bookings', []), [])
        self.assertEqual(json.loads(self.request('GET', '/api/admin/accepted-bookings', cookie=cookie)[2])['count'], 0)
        self.assertEqual(self.request('DELETE', path, payload, cookie)[0], 404)

    def test_cleanup_is_authenticated_for_cron_and_runs_on_read(self):
        booking_id = self.create_one()
        def age(rows):
            rows[0].update(date='2020-01-01', status='confirmed', internalNote='Private old note')
        self.store.update('bookings', [], age)
        path = '/api/cron/booking-retention'
        with patch.object(server, 'CRON_SECRET', ''):
            self.assertEqual(self.request('GET', path)[0], 401)
        with patch.object(server, 'CRON_SECRET', 'synthetic-cron-secret-only-for-tests'):
            self.assertEqual(self.request('GET', path)[0], 401)
            self.assertEqual(len(self.store.read('bookings', [])), 1)
            status, _, body = self.request('GET', path, headers={'Authorization': 'Bearer synthetic-cron-secret-only-for-tests'})
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body)['deletedCount'], 1)
            self.assertNotIn(booking_id.encode(), body)
        self.create_one()
        self.store.update('bookings', [], age)
        cookie = self.login()
        self.assertEqual(json.loads(self.request('GET', '/api/admin/bookings', cookie=cookie)[2])['bookings'], [])
        self.assertEqual(self.store.read('bookings', []), [])

    def test_reschedule_updates_export_week_and_reprices_services(self):
        booking_id = self.create_one()
        cookie = self.login()
        replacement = self.booking(1)['services']
        self.assertEqual(self.edit(booking_id, cookie, expectedRevision=0, date='2099-06-23',
                                  status='confirmed', services=replacement)[0], 200)
        saved = self.store.read('bookings', [])[0]
        self.assertEqual(saved['total'], replacement[0]['price'])
        for date, count in [('2099-06-15', '0'), ('2099-06-23', '1')]:
            year, week, _ = server.datetime.strptime(date, '%Y-%m-%d').isocalendar()
            status, headers, _ = self.request('GET', f'/api/admin/accepted-bookings/export?week={year}-W{week:02}', cookie=cookie)
            self.assertEqual(status, 200)
            self.assertEqual(headers['X-Export-Count'], count)

    def test_hold_can_be_added_to_past_booking_without_rescheduling(self):
        booking_id = self.create_one()
        self.store.update('bookings', [], lambda rows: rows[0].update(date='2026-09-01'))
        cookie = self.login()
        status, _, body = self.edit(booking_id, cookie, expectedRevision=0, retentionHold=True,
                                   holdReason='Complaint under review')
        self.assertEqual(status, 200, body)
        self.assertTrue(self.store.read('bookings', [])[0]['retentionHold'])

    @unittest.skipUnless(os.getenv('TEST_DATABASE_URL'), 'TEST_DATABASE_URL is not set')
    def test_postgres_cancelled_reopen_validation_and_rescheduling(self):
        prefix = 'reopen-test-' + uuid.uuid4().hex + '-'

        class ScopedPostgresStore(PostgresStore):
            def read(self, key, default):
                return super().read(prefix + key, default)

            def update(self, key, default, change):
                return super().update(prefix + key, default, change)

        store = ScopedPostgresStore(os.environ['TEST_DATABASE_URL'], require_tls=False)
        try:
            with patch.object(server, 'STORE', store):
                booking_id = self.create_one()
                cookie = self.login()
                self.assertEqual(self.edit(booking_id, cookie, expectedRevision=0, status='confirmed')[0], 200)
                self.assertEqual(self.edit(booking_id, cookie, expectedRevision=1, status='cancelled',
                                          internalNote='Staff unavailable')[0], 200)
                # Reopening must require a future appointment, even when the old
                # date was left unchanged in the editor.
                store.update('bookings', [], lambda rows: rows[0].update(date='2000-01-01'))
                cancelled = store.read('bookings', [])[0]
                for changes, message in [
                    ({}, 'future date and time'),
                    ({'date': '2099-06-15', 'time': '09:00'}, 'time between'),
                    ({'date': '2099-06-15', 'email': 'invalid'}, 'name, email, phone'),
                    ({'date': '2099-06-15', 'services': []}, 'one and twenty treatments'),
                ]:
                    with self.subTest(changes=changes):
                        status, _, body = self.edit(booking_id, cookie, expectedRevision=2,
                                                   status='pending', **changes)
                        self.assertEqual(status, 400, body)
                        self.assertIn(message, json.loads(body)['error'])
                        self.assertEqual(store.read('bookings', [])[0], cancelled)
                status, _, body = self.edit(booking_id, cookie, expectedRevision=2,
                                           status='pending', date='2099-06-15')
                self.assertEqual(status, 200, body)
                saved = store.read('bookings', [])[0]
                self.assertEqual(saved['status'], 'pending')
                self.assertEqual(saved['revision'], 3)
                self.assertNotIn('closedAt', saved)
                self.assertEqual(saved['internalNote'], 'Staff unavailable')
                self.assertEqual(saved['createdAt'], cancelled['createdAt'])
                server.customer_status_email.assert_not_called()
        finally:
            with store.connection() as connection:
                connection.execute('DELETE FROM tls_documents WHERE key IN (%s, %s)',
                                   (prefix + 'bookings', prefix + 'admin-auth'))



class EmailTests(unittest.TestCase):
    def message(self, status):
        booking = {
            'id': 'TLS-synthetic', 'name': 'Sam <Guest> & Customer',
            'email': 'customer@example.invalid', 'date': '2099-06-15', 'time': '12:00',
            'services': [{'name': 'Brows & lashes', 'variant': '', 'price': 25}],
            'total': 25,
        }
        with patch.object(server, 'SMTP_USER', 'sender@example.invalid'), \
                patch.object(server, 'SMTP_PASSWORD', 'synthetic-mail-password'), \
                patch.object(server, 'SMTP_FROM', 'sender@example.invalid'), \
                patch.object(server, 'SALON_EMAIL', 'studio@example.invalid'), \
                patch.object(server, 'STUDIO_PHONE', '00000000000'), \
                patch.object(server.smtplib, 'SMTP') as smtp:
            self.assertTrue(server.customer_status_email(
                booking, status, 'Bring <ID> & arrive early.\nThank you.'))
            client = smtp.return_value.__enter__.return_value
            client.starttls.assert_called_once()
            client.send_message.assert_called_once()
            sent = client.send_message.call_args.args[0]
            return BytesParser(policy=policy.default).parsebytes(sent.as_bytes())

    def test_confirmation_embeds_logo_and_escapes_customer_content(self):
        message = self.message('confirmed')
        self.assertEqual(message['To'], 'customer@example.invalid')
        self.assertEqual(message.get_content_type(), 'multipart/alternative')
        plain = message.get_body(preferencelist=('plain',)).get_content()
        html = message.get_body(preferencelist=('html',)).get_content()
        self.assertIn('Sam <Guest> & Customer', plain)
        self.assertIn('Sam &lt;Guest&gt; &amp; Customer', html)
        self.assertIn('Bring &lt;ID&gt; &amp; arrive early.<br>Thank you.', html)
        self.assertNotIn('<Guest>', html)
        self.assertIn('2099-06-15 at 12:00', html)
        self.assertIn('£25.00', html)
        images = [part for part in message.walk() if part.get_content_type() == 'image/png']
        self.assertEqual(len(images), 1)
        logo = images[0]
        self.assertEqual(logo.get_content_disposition(), 'inline')
        self.assertEqual(logo.get_filename(), 'transformation-logo.png')
        self.assertEqual(logo.get_payload(decode=True),
                         (ROOT / 'public/assets/transformation-logo.png').read_bytes())
        self.assertIn('src="cid:' + logo['Content-ID'][1:-1] + '"', html)
        self.assertLess(html.index('Phone: 00000000000'), html.index('<img'))
        self.assertNotIn('Transformation Laser Studio<br>Hair', html)
        self.assertTrue(plain.endswith('Transformation Laser Studio\nHair & Beauty\n'))

    def test_decline_keeps_plain_text_email(self):
        message = self.message('declined')
        self.assertEqual(message.get_content_type(), 'text/plain')
        self.assertIn('could not be confirmed', message.get_content())
        self.assertIn('Phone: 00000000000', message.get_content())


class StorageTests(unittest.TestCase):
    def test_corrupt_local_bookings_are_not_silently_lost(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'bookings.json'
            path.write_text('invalid data')
            with self.assertRaises(StorageUnavailable):
                LocalStore(directory).update('bookings', [], lambda rows: rows.append({}))
            self.assertEqual(path.read_text(), 'invalid data')

    def test_failed_change_leaves_saved_document_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            store = LocalStore(directory)
            store.update('bookings', [], lambda rows: rows.append({'id': 'old'}))
            def failing(rows):
                rows.clear()
                raise RuntimeError('Synthetic failure')
            with self.assertRaises(RuntimeError):
                store.update('bookings', [], failing)
            self.assertEqual(store.read('bookings', []), [{'id': 'old'}])

    def test_vercel_entrypoint_import_never_writes_to_disk(self):
        code = '''from unittest.mock import patch
from pathlib import Path
with patch.object(Path, 'mkdir', side_effect=AssertionError('Filesystem write')):
    from api.index import handler
    from http.server import BaseHTTPRequestHandler
    assert issubclass(handler, BaseHTTPRequestHandler)
'''
        env = {**os.environ, 'VERCEL': '1', 'ADMIN_PIN': '', 'DATABASE_URL': '', 'PYTHONDONTWRITEBYTECODE': '1'}
        result = subprocess.run([sys.executable, '-c', code], cwd=ROOT, env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_vercel_entrypoint_starts_with_blank_port_settings(self):
        code = '''from api.index import handler
import json
import server
print(json.dumps([server.PORT, server.SMTP_PORT]))
'''
        cases = [(None, None, [8000, 587]), ('', '', [8000, 587]),
                 (' \t', ' \t', [8000, 587]), ('8123', '2525', [8123, 2525])]
        for port, smtp_port, expected in cases:
            with self.subTest(port=port, smtp_port=smtp_port):
                env = {**os.environ, 'VERCEL': '1', 'ADMIN_PIN': '', 'DATABASE_URL': '',
                       'PYTHONDONTWRITEBYTECODE': '1'}
                for key, value in [('PORT', port), ('SMTP_PORT', smtp_port)]:
                    env.pop(key, None)
                    if value is not None:
                        env[key] = value
                result = subprocess.run([sys.executable, '-c', code], cwd=ROOT, env=env,
                                        capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout), expected)


@unittest.skipUnless(os.getenv('TEST_DATABASE_URL'), 'TEST_DATABASE_URL is not set')
class PostgresIntegrationTests(unittest.TestCase):
    def test_atomic_writes_survive_new_instances_and_rollback(self):
        url = os.environ['TEST_DATABASE_URL']
        # TLS can be disabled ONLY for the explicitly configured disposable test DB.
        store = PostgresStore(url, require_tls=False)
        key = 'integration-' + uuid.uuid4().hex
        try:
            store.update(key, [], lambda rows: rows.append('initial'))
            def append(index):
                another = PostgresStore(url, require_tls=False)
                another.update(key, [], lambda rows: rows.append(index))
            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
                list(pool.map(append, range(12)))
            self.assertEqual(len(PostgresStore(url, require_tls=False).read(key, [])), 13)
            def failing(rows):
                rows.clear()
                raise RuntimeError('Synthetic rollback')
            with self.assertRaises(RuntimeError):
                store.update(key, [], failing)
            self.assertEqual(len(store.read(key, [])), 13)
        finally:
            with store.connection() as connection:
                connection.execute('DELETE FROM tls_documents WHERE key = %s', (key,))


if __name__ == '__main__':
    unittest.main()
