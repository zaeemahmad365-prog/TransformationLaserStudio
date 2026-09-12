"""Atomic documents for this small studio; PostgreSQL on Vercel, JSON locally."""
from contextlib import contextmanager
from copy import deepcopy
import json
from pathlib import Path
import threading


class StorageUnavailable(Exception):
    pass


class LocalStore:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.lock = threading.RLock()

    def read(self, key, default):
        with self.lock:
            path = self.directory / f'{key}.json'
            try:
                if not path.exists():
                    return deepcopy(default)
                return json.loads(path.read_text(encoding='utf-8'))
            except (OSError, ValueError) as exc:
                # Never silently replace unreadable bookings with an empty list.
                raise StorageUnavailable('Local data could not be read.') from exc

    def update(self, key, default, change):
        with self.lock:
            document = self.read(key, default)
            result = change(document)
            try:
                self.directory.mkdir(parents=True, exist_ok=True)
                path = self.directory / f'{key}.json'
                temporary = path.with_suffix('.tmp')
                temporary.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding='utf-8')
                temporary.chmod(0o600)
                temporary.replace(path)
            except OSError as exc:
                raise StorageUnavailable('Local data could not be saved.') from exc
            return result


class PostgresStore:
    def __init__(self, url, require_tls=True):
        self.url = url
        self.require_tls = require_tls
        self.ready = False
        self.lock = threading.Lock()

    @contextmanager
    def connection(self):
        try:
            import psycopg
            from psycopg.conninfo import conninfo_to_dict
        except ImportError as exc:
            raise StorageUnavailable('Install the PostgreSQL dependency.') from exc
        try:
            settings = conninfo_to_dict(self.url)
            if self.require_tls and settings.get('sslmode') not in ('require', 'verify-ca', 'verify-full'):
                settings['sslmode'] = 'require'
            with psycopg.connect(**settings, connect_timeout=10) as connection:
                # Bound locks/queries without relying on pooler startup options.
                connection.execute("SET LOCAL statement_timeout = '15000'")
                connection.execute("SET LOCAL lock_timeout = '10000'")
                if not self.ready:
                    with self.lock:
                        if not self.ready:
                            # Serialize schema initialization across cold starts.
                            connection.execute('SELECT pg_advisory_xact_lock(846271903)')
                            connection.execute('''CREATE TABLE IF NOT EXISTS tls_documents (
                                key TEXT PRIMARY KEY,
                                value JSONB NOT NULL
                            )''')
                            connection.commit()
                            self.ready = True
                            connection.execute("SET LOCAL statement_timeout = '15000'")
                            connection.execute("SET LOCAL lock_timeout = '10000'")
                yield connection
        except (psycopg.Error, ValueError) as exc:
            # Database exceptions can contain credentials. Do not return/log them.
            raise StorageUnavailable('Booking storage is unavailable.') from exc

    def read(self, key, default):
        with self.connection() as connection:
            row = connection.execute('SELECT value FROM tls_documents WHERE key = %s', (key,)).fetchone()
            return row[0] if row else deepcopy(default)

    def update(self, key, default, change):
        with self.connection() as connection:
            from psycopg.types.json import Jsonb
            connection.execute('INSERT INTO tls_documents (key, value) VALUES (%s, %s) ON CONFLICT DO NOTHING',
                               (key, Jsonb(default)))
            document = connection.execute('SELECT value FROM tls_documents WHERE key = %s FOR UPDATE',
                                          (key,)).fetchone()[0]
            result = change(document)
            connection.execute('UPDATE tls_documents SET value = %s WHERE key = %s', (Jsonb(document), key))
        # Return only AFTER commit, so the API never acknowledges an unsaved change.
        return result


class UnconfiguredStore:
    def read(self, *args):
        raise StorageUnavailable('DATABASE_URL is required on Vercel.')

    update = read
