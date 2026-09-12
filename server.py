#!/usr/bin/env python3
import json
import os
import re
import shutil
import zipfile
import smtplib
import subprocess
import ssl
import sys
import threading
import webbrowser
import uuid
from functools import wraps
from http.cookies import SimpleCookie, CookieError
from zoneinfo import ZoneInfo

import auth
from storage import LocalStore, PostgresStore, StorageUnavailable, UnconfiguredStore
from datetime import datetime, timezone, timedelta
from email.message import EmailMessage
from io import BytesIO
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape
from urllib.parse import urlparse, unquote, parse_qs

ROOT = Path(__file__).resolve().parent
PUBLIC = ROOT / 'public'
DATA = Path(os.getenv('TLS_DATA_DIR', str(ROOT / 'data')))
EXPORTS = DATA / 'exports'
OUTBOX = DATA / 'outbox'
APP_BUILD = '2026-09-vercel-admin-sessions'
ON_VERCEL = os.getenv('VERCEL') == '1'


def load_settings():
    """Process environment wins; local settings.env overrides the legacy .env."""
    if ON_VERCEL:
        return
    supplied = set(os.environ)
    for env_file in (ROOT / '.env', ROOT / 'settings.env'):
        if not env_file.exists():
            continue
        for raw in env_file.read_text(encoding='utf-8').splitlines():
            line = raw.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, value = line.split('=', 1)
            key, value = key.strip(), value.strip().strip('"').strip("'")
            if key not in supplied:
                os.environ[key] = value


load_settings()
HOST = os.getenv('HOST', '127.0.0.1')
PORT = int(os.getenv('PORT', '8000'))
SALON_EMAIL = os.getenv('SALON_EMAIL') or 'zaeemahmad365@gmail.com'
STUDIO_PHONE = os.getenv('STUDIO_PHONE') or '07719598265'
ADMIN_PIN = os.getenv('ADMIN_PIN', '')
SMTP_HOST = os.getenv('SMTP_HOST', 'smtp.gmail.com')
SMTP_PORT = int(os.getenv('SMTP_PORT', '587'))
SMTP_USER = os.getenv('SMTP_USER', '')
SMTP_PASSWORD = os.getenv('SMTP_PASSWORD', '')
SMTP_FROM = os.getenv('SMTP_FROM') or SMTP_USER or SALON_EMAIL
AUTO_OPEN = os.getenv('AUTO_OPEN', '1') == '1'

DATABASE_URL = os.getenv('DATABASE_URL', '')
STORE = (PostgresStore(DATABASE_URL) if DATABASE_URL else
         UnconfiguredStore() if ON_VERCEL else LocalStore(DATA))
LOCAL_FILES = not ON_VERCEL and not DATABASE_URL
COOKIE_NAME = '__Host-tls_admin' if ON_VERCEL else 'tls_admin'


class RequestError(Exception):
    def __init__(self, status, message):
        self.status = status
        super().__init__(message)


def api_errors(method):
    @wraps(method)
    def wrapped(self):
        try:
            return method(self)
        except RequestError as exc:
            return self.send_json(exc.status, {'error': str(exc)})
        except StorageUnavailable:
            # Do not print driver exceptions or return connection details.
            return self.send_json(503, {'error': 'Booking storage is unavailable. Please contact the studio.'})
    return wrapped


def read_bookings():
    return STORE.read('bookings', [])


def accepted_bookings(bookings):
    accepted = []
    for booking in bookings:
        if booking.get('status') == 'confirmed':
            snapshot = dict(booking)
            snapshot['acceptedAt'] = booking.get('statusUpdatedAt') or booking.get('createdAt')
            accepted.append(snapshot)
    return sorted(accepted, key=lambda b: (b.get('date', ''), b.get('time', '')))


def reconcile_accepted_bookings():
    # Derive from the single source of truth instead of a second mutable file.
    return accepted_bookings(read_bookings())


def current_iso_week():
    today = datetime.now().date()
    year, week, _ = today.isocalendar()
    return f'{year}-W{week:02d}'


def iso_week_range(value):
    match = re.fullmatch(r'(\d{4})-W(\d{2})', clean(value, 8))
    if not match:
        raise ValueError('Choose a valid week.')
    year, week = int(match.group(1)), int(match.group(2))
    try:
        start = datetime.fromisocalendar(year, week, 1).date()
    except ValueError as exc:
        raise ValueError('Choose a valid week.') from exc
    return start, start + timedelta(days=6), f'{year}-W{week:02d}'


def excel_column_name(number):
    result = ''
    while number:
        number, remainder = divmod(number - 1, 26)
        result = chr(65 + remainder) + result
    return result


def xlsx_inline_cell(ref, value, style=0):
    text = xml_escape(str(value if value is not None else ''))
    preserve = ' xml:space="preserve"' if text.startswith(' ') or text.endswith(' ') or '\n' in text else ''
    style_attr = f' s="{style}"' if style else ''
    return f'<c r="{ref}" t="inlineStr"{style_attr}><is><t{preserve}>{text}</t></is></c>'


def xlsx_number_cell(ref, value, style=0):
    style_attr = f' s="{style}"' if style else ''
    return f'<c r="{ref}"{style_attr}><v>{float(value or 0):.2f}</v></c>'


def build_accepted_bookings_xlsx(bookings, week_label, start_date, end_date):
    headers = [
        'Booking ID', 'Appointment date', 'Appointment time', 'Customer name',
        'Email', 'Phone', 'Treatments', 'Categories', 'Total (£)',
        'Customer notes', 'Studio note', 'Accepted at', 'Request received'
    ]
    rows = []
    for b in sorted(bookings, key=lambda item: (item.get('date', ''), item.get('time', ''), item.get('name', ''))):
        treatment_names = '; '.join(
            f"{s.get('name', '')}{' — ' + s.get('variant', '') if s.get('variant') else ''}"
            for s in b.get('services', [])
        )
        categories = '; '.join(dict.fromkeys(s.get('category', '') for s in b.get('services', []) if s.get('category')))
        rows.append([
            b.get('id', ''), b.get('date', ''), b.get('time', ''), b.get('name', ''),
            b.get('email', ''), b.get('phone', ''), treatment_names, categories,
            float(b.get('total', 0) or 0), b.get('notes', ''), b.get('studioNote', ''),
            b.get('acceptedAt', b.get('statusUpdatedAt', '')), b.get('createdAt', '')
        ])

    sheet_rows = []
    header_cells = ''.join(xlsx_inline_cell(f'{excel_column_name(i)}1', value, 1) for i, value in enumerate(headers, 1))
    sheet_rows.append(f'<row r="1" ht="24" customHeight="1">{header_cells}</row>')
    for r_index, row in enumerate(rows, 2):
        cells = []
        for c_index, value in enumerate(row, 1):
            ref = f'{excel_column_name(c_index)}{r_index}'
            if c_index == 9:
                cells.append(xlsx_number_cell(ref, value, 2))
            else:
                cells.append(xlsx_inline_cell(ref, value))
        sheet_rows.append(f'<row r="{r_index}">{"".join(cells)}</row>')

    last_row = max(1, len(rows) + 1)
    widths = [18, 15, 14, 22, 30, 18, 44, 24, 13, 34, 34, 28, 28]
    cols = ''.join(f'<col min="{i}" max="{i}" width="{width}" customWidth="1"/>' for i, width in enumerate(widths, 1))
    worksheet = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <dimension ref="A1:M{last_row}"/>
  <sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>
  <sheetFormatPr defaultRowHeight="18"/>
  <cols>{cols}</cols>
  <sheetData>{''.join(sheet_rows)}</sheetData>
  <autoFilter ref="A1:M{last_row}"/>
  <pageMargins left="0.35" right="0.35" top="0.5" bottom="0.5" header="0.2" footer="0.2"/>
</worksheet>'''

    styles = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <numFmts count="1"><numFmt numFmtId="164" formatCode="£#,##0.00"/></numFmts>
  <fonts count="2">
    <font><sz val="11"/><name val="Calibri"/><family val="2"/></font>
    <font><b/><sz val="11"/><color rgb="FF2D241E"/><name val="Calibri"/><family val="2"/></font>
  </fonts>
  <fills count="3">
    <fill><patternFill patternType="none"/></fill>
    <fill><patternFill patternType="gray125"/></fill>
    <fill><patternFill patternType="solid"><fgColor rgb="FFE8D5B4"/><bgColor indexed="64"/></patternFill></fill>
  </fills>
  <borders count="2">
    <border><left/><right/><top/><bottom/><diagonal/></border>
    <border><left style="thin"><color rgb="FFD8CEC5"/></left><right style="thin"><color rgb="FFD8CEC5"/></right><top style="thin"><color rgb="FFD8CEC5"/></top><bottom style="thin"><color rgb="FFD8CEC5"/></bottom><diagonal/></border>
  </borders>
  <cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
  <cellXfs count="3">
    <xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf>
    <xf numFmtId="0" fontId="1" fillId="2" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1"><alignment vertical="center" wrapText="1"/></xf>
    <xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1" applyAlignment="1"><alignment vertical="top"/></xf>
  </cellXfs>
  <cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>
</styleSheet>'''

    workbook = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets><sheet name="Accepted bookings" sheetId="1" r:id="rId1"/></sheets>
</workbook>'''
    workbook_rels = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>'''
    root_rels = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
  <Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>
</Relationships>'''
    content_types = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
  <Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
  <Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
  <Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>
</Types>'''
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')
    core = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" xmlns:dcmitype="http://purl.org/dc/dcmitype/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <dc:title>Accepted bookings {week_label}</dc:title><dc:creator>Transformation Laser Studio</dc:creator>
  <dc:description>Accepted appointments from {start_date.isoformat()} to {end_date.isoformat()}</dc:description>
  <dcterms:created xsi:type="dcterms:W3CDTF">{now}</dcterms:created><dcterms:modified xsi:type="dcterms:W3CDTF">{now}</dcterms:modified>
</cp:coreProperties>'''
    app = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes"><Application>Transformation Laser Studio</Application></Properties>'''

    output = BytesIO()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('[Content_Types].xml', content_types)
        z.writestr('_rels/.rels', root_rels)
        z.writestr('xl/workbook.xml', workbook)
        z.writestr('xl/_rels/workbook.xml.rels', workbook_rels)
        z.writestr('xl/worksheets/sheet1.xml', worksheet)
        z.writestr('xl/styles.xml', styles)
        z.writestr('docProps/core.xml', core)
        z.writestr('docProps/app.xml', app)
    return output.getvalue()


def clean(value, limit=500):
    value = str(value or '').strip()
    return value[:limit]


def valid_email(value):
    return bool(re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value))


def service_signature(s):
    return (clean(s.get('category'), 80), clean(s.get('name'), 120), clean(s.get('variant'), 80), round(float(s.get('price', 0)), 2))


def treatment_key(service):
    # Identity used for same-time booking conflicts. Price is intentionally
    # ignored so changing a price later does not change whether slots clash.
    return (
        clean(service.get('category'), 80),
        clean(service.get('name'), 120),
        clean(service.get('variant'), 80),
    )


def booking_treatment_keys(booking):
    return {treatment_key(service) for service in booking.get('services', []) if isinstance(service, dict)}


def load_catalogue():
    return json.loads((PUBLIC / 'services.json').read_text(encoding='utf-8'))


def allowed_service_signatures():
    allowed = set()
    for category in load_catalogue():
        for item in category['items']:
            if item.get('variants'):
                for v in item['variants']:
                    allowed.add((category['category'], item['name'], v['label'], round(float(v['price']), 2)))
            else:
                allowed.add((category['category'], item['name'], '', round(float(item['price']), 2)))
    return allowed


ALLOWED = allowed_service_signatures()


def send_email(to_addr, subject, text, tag):
    msg = EmailMessage()
    msg['From'] = SMTP_FROM
    msg['To'] = to_addr
    msg['Subject'] = subject
    msg.set_content(text)

    if SMTP_USER and SMTP_PASSWORD:
        context = ssl.create_default_context()
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as smtp:
            smtp.starttls(context=context)
            smtp.login(SMTP_USER, SMTP_PASSWORD)
            smtp.send_message(msg)
        return True

    if not LOCAL_FILES:
        return False
    OUTBOX.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    safe = re.sub(r'[^A-Za-z0-9_-]+', '-', tag)[:50]
    (OUTBOX / f'{stamp}-{safe}.eml').write_text(msg.as_string(), encoding='utf-8')
    return False


def booking_lines(services):
    return '\n'.join(f"- {s['name']}{' — ' + s['variant'] if s.get('variant') else ''}: £{s['price']:.2f}" for s in services)


def salon_notification(b):
    text = f"""New booking request {b['id']}

Received: {b['createdAt']}
Customer: {b['name']}
Email: {b['email']}
Phone: {b['phone']}
Preferred appointment: {b['date']} at {b['time']}

Treatments:
{booking_lines(b['services'])}

Treatment total: £{b['total']:.2f}

Notes: {b['notes'] or 'None'}

Review this request in the Studio Admin page. Requests are listed in the order received.
"""
    return send_email(SALON_EMAIL, f"New booking request {b['id']} — {b['date']} {b['time']}", text, f"studio-{b['id']}")


def customer_status_email(b, status, note):
    if status == 'confirmed':
        subject = f"Your Transformation Laser Studio appointment is confirmed — {b['date']} at {b['time']}"
        status_text = f"Your appointment has been made for the following treatment(s) on {b['date']} at {b['time']}."
    else:
        subject = f"Update on your Transformation Laser Studio booking request — {b['id']}"
        status_text = "We’re sorry, but the requested appointment time could not be confirmed. Please contact the studio or submit another request for a different date/time."
    text = f"""Hello {b['name']},

{status_text}

Treatments:
{booking_lines(b['services'])}

Treatment total: £{b['total']:.2f}

{('Studio note: ' + note) if note else ''}

For any enquiries, contact Transformation Laser Studio:
Email: {SALON_EMAIL}
Phone: {STUDIO_PHONE}

Transformation Laser Studio
Hair & Beauty
"""
    return send_email(b['email'], subject, text, f"customer-{status}-{b['id']}")


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(PUBLIC), **kwargs)

    def log_message(self, format, *args):
        sys.stdout.write("[%s] %s\n" % (self.log_date_time_string(), format % args))

    def send_json(self, status, payload, headers=None):
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def send_download(self, payload, filename, booking_count, saved_copy=''):
        self.send_response(200)
        self.send_header('Content-Type', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        self.send_header('Content-Disposition', f'attachment; filename="{filename}"')
        self.send_header('Content-Length', str(len(payload)))
        self.send_header('X-Export-Count', str(booking_count))
        if saved_copy:
            self.send_header('X-Saved-Copy', saved_copy)
        self.end_headers()
        self.wfile.write(payload)

    def json_body(self):
        if self.headers.get('Content-Type', '').split(';')[0].lower() != 'application/json':
            return None
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if length <= 0 or length > 100_000:
                return None
            return json.loads(self.rfile.read(length).decode('utf-8'))
        except Exception:
            return None

    def session_token(self):
        try:
            cookies = SimpleCookie()
            cookies.load(self.headers.get('Cookie', ''))
            return cookies[COOKIE_NAME].value if COOKIE_NAME in cookies else ''
        except CookieError:
            return ''

    def require_admin(self):
        if not auth.configured(ADMIN_PIN):
            raise RequestError(503, 'Admin login is not configured. Contact the studio owner.')
        if not auth.valid_session(STORE, self.session_token(), ADMIN_PIN):
            raise RequestError(401, 'Please sign in to view bookings.')

    def require_same_origin(self):
        # A custom header + JSON prevent cross-origin form submissions. Check
        # Origin too; no CORS permission is granted to other websites.
        scheme = 'https' if ON_VERCEL else 'http'
        expected = f"{scheme}://{self.headers.get('Host', '')}"
        if (self.headers.get('Origin') != expected or
                self.headers.get('X-CSRF-Protection') != '1'):
            raise RequestError(403, 'Please use the admin page on this website.')

    def session_cookie(self, token, max_age=auth.SESSION_SECONDS):
        value = f'{COOKIE_NAME}={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age={max_age}'
        return value + ('; Secure' if ON_VERCEL else '')

    def login(self):
        if not auth.configured(ADMIN_PIN):
            raise RequestError(503, 'Admin login is not configured. Contact the studio owner.')
        data = self.json_body()
        if not isinstance(data, dict) or not isinstance(data.get('password'), str):
            raise RequestError(400, 'Enter your admin password.')
        address = (self.headers.get('X-Vercel-Forwarded-For', 'unknown') if ON_VERCEL
                   else self.client_address[0])
        retry = auth.login_limit(STORE, address[:200], ADMIN_PIN)
        if retry:
            return self.send_json(429, {'error': 'Too many login attempts. Try again in 15 minutes.'},
                                  {'Retry-After': str(retry)})
        if not auth.matches(data['password'], ADMIN_PIN):
            raise RequestError(401, 'Incorrect admin password.')
        token = auth.create_session(STORE, ADMIN_PIN, self.session_token())
        return self.send_json(200, {'ok': True}, {'Set-Cookie': self.session_cookie(token)})

    def end_headers(self):
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Referrer-Policy', 'same-origin')
        super().end_headers()

    def list_directory(self, path):
        self.send_error(404)
        return None

    def do_HEAD(self):
        # Never let inherited static handling route an API request to disk.
        if urlparse(self.path).path.startswith('/api'):
            self.send_response(405)
            self.send_header('Allow', 'GET, POST')
            self.end_headers()
            return
        return super().do_HEAD()

    @api_errors
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path == '/api/health':
            return self.send_json(200, {'ok': True, 'build': APP_BUILD})
        if path == '/api/admin/bookings':
            self.require_admin()
            bookings = sorted(read_bookings(), key=lambda b: b.get('createdAt',''))
            return self.send_json(200, {'bookings': bookings})
        if path == '/api/admin/accepted-bookings':
            self.require_admin()
            accepted = reconcile_accepted_bookings()
            return self.send_json(200, {'acceptedBookings': accepted, 'count': len(accepted)})
        if path == '/api/admin/accepted-bookings/export':
            self.require_admin()
            week_value = (parse_qs(parsed.query).get('week') or [current_iso_week()])[0]
            try:
                start_date, end_date, week_label = iso_week_range(week_value)
            except ValueError as exc:
                return self.send_json(400, {'error': str(exc)})
            accepted = []
            # Rebuild before every export so confirmed bookings are never missed.
            for booking in reconcile_accepted_bookings():
                try:
                    appointment_date = datetime.strptime(booking.get('date', ''), '%Y-%m-%d').date()
                except ValueError:
                    continue
                if start_date <= appointment_date <= end_date:
                    accepted.append(booking)
            try:
                payload = build_accepted_bookings_xlsx(accepted, week_label, start_date, end_date)
            except Exception as exc:
                print('Excel build error:', type(exc).__name__)
                return self.send_json(500, {'error': 'Could not build the Excel file.'})
            filename = f'accepted-bookings-{week_label}.xlsx'
            saved_copy = ''
            if LOCAL_FILES:
                try:
                    EXPORTS.mkdir(parents=True, exist_ok=True)
                    (EXPORTS / filename).write_bytes(payload)
                    saved_copy = filename
                except OSError:
                    # The download still works if a local copy is locked.
                    pass
            return self.send_download(payload, filename, len(accepted), saved_copy)
        if path.startswith('/api'):
            return self.send_json(404, {'error': 'Not found.'})
        return super().do_GET()

    @api_errors
    def do_POST(self):
        path = unquote(urlparse(self.path).path)
        if path.startswith('/api/admin/'):
            self.require_same_origin()
        if path == '/api/admin/login':
            return self.login()
        if path == '/api/admin/logout':
            auth.revoke_session(STORE, self.session_token())
            return self.send_json(200, {'ok': True}, {'Set-Cookie': self.session_cookie('', 0)})
        if path == '/api/bookings':
            return self.create_booking()
        match = re.fullmatch(r'/api/admin/bookings/([A-Za-z0-9-]+)', path)
        if match:
            self.require_admin()
            return self.update_booking(match.group(1))
        return self.send_json(404, {'error': 'Not found.'})

    def create_booking(self):
        data = self.json_body()
        if not isinstance(data, dict):
            return self.send_json(400, {'error': 'Invalid request.'})

        name = clean(data.get('name'), 80)
        email = clean(data.get('email'), 120).lower()
        phone = clean(data.get('phone'), 30)
        date = clean(data.get('date'), 10)
        time = clean(data.get('time'), 5)
        notes = clean(data.get('notes'), 800)
        raw_services = data.get('services') or []

        if not all([name, email, phone, date, time]) or not valid_email(email):
            return self.send_json(400, {'error': 'Please complete your name, email, phone, date and time.'})
        try:
            requested = datetime.strptime(f'{date} {time}', '%Y-%m-%d %H:%M')
            if requested.replace(tzinfo=ZoneInfo('Europe/London')) < datetime.now(ZoneInfo('Europe/London')):
                return self.send_json(400, {'error': 'Please choose a future date and time.'})
            requested_minutes = requested.hour * 60 + requested.minute
            opening_minutes = 11 * 60 if requested.weekday() == 6 else 10 * 60
            closing_minutes = 19 * 60
            if requested_minutes < opening_minutes or requested_minutes > closing_minutes:
                hours = '11am and 7pm on Sunday' if requested.weekday() == 6 else '10am and 7pm, Monday to Saturday'
                return self.send_json(400, {'error': f'Please choose a time between {hours}.'})
        except ValueError:
            return self.send_json(400, {'error': 'Please choose a valid date and time.'})

        if not isinstance(raw_services, list) or not raw_services or len(raw_services) > 20:
            return self.send_json(400, {'error': 'Please choose at least one treatment.'})
        services = []
        seen = set()
        for raw in raw_services:
            if not isinstance(raw, dict):
                return self.send_json(400, {'error': 'Invalid treatment selection.'})
            try:
                sig = service_signature(raw)
            except Exception:
                return self.send_json(400, {'error': 'Invalid treatment selection.'})
            if sig not in ALLOWED:
                return self.send_json(400, {'error': 'A treatment or price did not match the current price list. Refresh the page and try again.'})
            if sig in seen:
                continue
            seen.add(sig)
            services.append({'category': sig[0], 'name': sig[1], 'variant': sig[2], 'price': sig[3]})

        if requested.weekday() == 6 and any(s['category'] != 'Nails' for s in services):
            return self.send_json(400, {'error': 'Sundays are available for nail treatments only. Please choose Nails or select another day.'})

        total = round(sum(s['price'] for s in services), 2)
        stamp = datetime.now(timezone.utc).isoformat()
        booking_id = 'TLS-' + uuid.uuid4().hex
        booking = {
            'id': booking_id,
            'status': 'pending',
            'createdAt': stamp,
            'name': name,
            'email': email,
            'phone': phone,
            'date': date,
            'time': time,
            'notes': notes,
            'services': services,
            'total': total,
            'statusUpdatedAt': None,
            'studioNote': ''
        }
        def insert(bookings):
            requested_treatments = {treatment_key(service) for service in services}
            for existing in bookings:
                if existing.get('date') != date or existing.get('time') != time:
                    continue
                if existing.get('status') not in ('pending', 'confirmed'):
                    continue
                clashes = requested_treatments & booking_treatment_keys(existing)
                if clashes:
                    conflict_names = ', '.join(sorted({key[1] + (f' — {key[2]}' if key[2] else '') for key in clashes}))
                    raise RequestError(409, f'That time already has a request or confirmed booking for {conflict_names}. You can choose a different treatment at the same time, or choose another time.'
                    )

            bookings.append(booking)

        STORE.update('bookings', [], insert)
        try:
            email_sent = salon_notification(booking)
        except Exception as e:
            print('Email notification error:', type(e).__name__)
            email_sent = False
        return self.send_json(201, {'ok': True, 'bookingId': booking_id, 'total': total, 'emailSent': email_sent})

    def update_booking(self, booking_id):
        data = self.json_body()
        if not isinstance(data, dict):
            raise RequestError(400, 'Invalid request.')
        status = clean(data.get('status'), 20)
        note = clean(data.get('note'), 1200)
        if status not in ('confirmed', 'declined'):
            return self.send_json(400, {'error': 'Status must be confirmed or declined.'})
        def change(bookings):
            target = None
            for b in bookings:
                if b.get('id') == booking_id:
                    target = b
                    break
            if not target:
                raise RequestError(404, 'Booking not found.')

            # Different treatments may share the same date/time. Only prevent a
            # confirmation when another confirmed booking contains an exact same
            # treatment (category + name + variant) in that slot.
            if status == 'confirmed':
                target_treatments = booking_treatment_keys(target)
                for existing in bookings:
                    if existing.get('id') == booking_id or existing.get('status') != 'confirmed':
                        continue
                    if existing.get('date') != target.get('date') or existing.get('time') != target.get('time'):
                        continue
                    clashes = target_treatments & booking_treatment_keys(existing)
                    if clashes:
                        conflict_names = ', '.join(sorted({key[1] + (f' — {key[2]}' if key[2] else '') for key in clashes}))
                        raise RequestError(409, f'Cannot confirm this booking because {conflict_names} is already confirmed for {target.get("date")} at {target.get("time")}. Different treatments can still be confirmed at that same time.'
                        )

            target['status'] = status
            target['statusUpdatedAt'] = datetime.now(timezone.utc).isoformat()
            target['studioNote'] = note
            return dict(target), len(accepted_bookings(bookings))

        target, accepted_count = STORE.update('bookings', [], change)
        try:
            email_sent = customer_status_email(target, status, note)
        except Exception as e:
            print('Customer email error:', type(e).__name__)
            email_sent = False
        return self.send_json(200, {'ok': True, 'emailSent': email_sent, 'acceptedCount': accepted_count})


def open_browser(port=PORT):
    url = f'http://{HOST}:{port}'
    candidates = []
    if sys.platform.startswith('win'):
        for env_name in ('PROGRAMFILES', 'PROGRAMFILES(X86)', 'LOCALAPPDATA'):
            base = os.getenv(env_name)
            if base:
                candidates.append(str(Path(base) / 'Google' / 'Chrome' / 'Application' / 'chrome.exe'))
    elif sys.platform == 'darwin':
        candidates.append('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')
    else:
        for command in ('google-chrome', 'google-chrome-stable', 'chromium', 'chromium-browser'):
            found = shutil.which(command)
            if found:
                candidates.append(found)
    for executable in candidates:
        try:
            if Path(executable).exists():
                subprocess.Popen([executable, url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return
        except Exception:
            pass
    try:
        webbrowser.open(url)
    except Exception:
        pass


if __name__ == '__main__':
    if HOST not in ('127.0.0.1', 'localhost', '::1'):
        raise RuntimeError('Keep the local launcher on localhost. Use Vercel for HTTPS hosting.')
    # Read confirmed bookings before the browser opens.
    try:
        repaired = reconcile_accepted_bookings()
    except Exception as exc:
        print('Accepted-bookings reconciliation error:', type(exc).__name__)
        repaired = []

    selected_port = PORT
    server = None
    for candidate_port in range(PORT, PORT + 10):
        try:
            server = ThreadingHTTPServer((HOST, candidate_port), Handler)
            selected_port = candidate_port
            break
        except OSError:
            if candidate_port == PORT:
                print(f'Port {PORT} is already in use. Trying another local port so an older server cannot interfere...')
            continue
    if server is None:
        raise RuntimeError(f'Could not start the website on ports {PORT}-{PORT + 9}. Close older website windows/servers and try again.')

    print('\nTransformation Laser Studio — local website')
    print(f'Build:   {APP_BUILD}')
    print(f'Website: http://{HOST}:{selected_port}')
    print(f'Admin:   http://{HOST}:{selected_port}/admin.html')
    print('Admin: configured' if auth.configured(ADMIN_PIN) else 'Admin: set ADMIN_PIN to a private password of 16–256 characters.')
    print(f'Data:    {DATA}')
    print(f'Accepted bookings currently saved: {len(repaired)}')
    if SMTP_USER and SMTP_PASSWORD:
        print(f'Email: SMTP enabled; studio notifications -> {SALON_EMAIL}')
    elif LOCAL_FILES:
        print(f'Email: preview mode (SMTP not configured). Messages are saved in {OUTBOX}')
        print('To send real emails, add your Gmail App Password to SMTP_PASSWORD in settings.env.')
    print('Press Ctrl+C to stop the website.\n')
    if AUTO_OPEN:
        threading.Timer(0.8, lambda: open_browser(selected_port)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\nStopped.')
