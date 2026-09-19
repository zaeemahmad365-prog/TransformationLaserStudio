"""Server-side admin sessions and shared login throttling."""
import hashlib
import hmac
import secrets
import time

SESSION_SECONDS = 8 * 60 * 60
LOGIN_WINDOW = 15 * 60
EMPTY_AUTH = {'sessions': {}, 'limits': {}}


def configured(password):
    return isinstance(password, str) and 16 <= len(password.strip()) <= len(password) <= 256


def matches(supplied, password):
    if not configured(password) or not isinstance(supplied, str) or len(supplied) > 256:
        return False
    return hmac.compare_digest(hashlib.sha256(supplied.encode()).digest(),
                               hashlib.sha256(password.encode()).digest())


def fingerprint(password):
    return hmac.new(password.encode(), b'tls-admin-session-version', hashlib.sha256).hexdigest()


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def prune(document, now):
    document['sessions'] = {key: value for key, value in document['sessions'].items() if value['expires'] > now}
    document['limits'] = {key: value for key, value in document['limits'].items() if value['expires'] > now}


def login_limit(store, address, password):
    """Reserve an attempt atomically. Returns seconds until retry, or zero."""
    now = int(time.time())
    address_key = hmac.new(password.encode(), address.encode(), hashlib.sha256).hexdigest()

    def reserve(document):
        prune(document, now)
        keys = [('ip:' + address_key, 5), ('global', 50)]
        for key, limit in keys:
            bucket = document['limits'].get(key)
            if bucket and bucket['count'] >= limit:
                return max(1, bucket['expires'] - now)
        for key, _ in keys:
            bucket = document['limits'].setdefault(key, {'count': 0, 'expires': now + LOGIN_WINDOW})
            bucket['count'] += 1
        return 0

    return store.update('admin-auth', EMPTY_AUTH, reserve)


def create_session(store, password, old_token=''):
    token = secrets.token_urlsafe(32)
    now = int(time.time())

    def create(document):
        prune(document, now)
        document['sessions'].pop(token_hash(old_token), None)
        document['sessions'][token_hash(token)] = {'expires': now + SESSION_SECONDS, 'version': fingerprint(password)}

    store.update('admin-auth', EMPTY_AUTH, create)
    return token


def valid_session(store, token, password):
    if not configured(password) or not token or len(token) > 128:
        return False
    session = store.read('admin-auth', EMPTY_AUTH)['sessions'].get(token_hash(token))
    return bool(session and session['expires'] > time.time()
                and hmac.compare_digest(session['version'], fingerprint(password)))


def revoke_session(store, token):
    def revoke(document):
        prune(document, int(time.time()))
        document['sessions'].pop(token_hash(token), None)
    store.update('admin-auth', EMPTY_AUTH, revoke)
