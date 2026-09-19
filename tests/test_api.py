import concurrent.futures
from contextlib import closing
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

    def test_missing_or_short_password_fails_closed(self):
        for password in ['', 'short']:
            with patch.object(server, 'ADMIN_PIN', password):
                self.assertEqual(self.request('POST', '/api/admin/login', {'password': password})[0], 503)
                self.assertEqual(self.request('GET', '/api/admin/bookings')[0], 503)

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
                                       {'status': 'confirmed', 'note': 'Synthetic note'}, cookie)
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
        self.assertEqual(self.request('POST', f'/api/admin/bookings/{booking_id}', {'status': 'declined'}, cookie)[0], 200)
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
