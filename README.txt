TRANSFORMATION LASER STUDIO
==========================

The public website and Python booking API run together on Vercel. See
DEPLOYMENT.md for the dashboard settings and required environment variables.

ADMIN
- Open /admin.html on the website and sign in with your private admin password.
- ADMIN_PIN is the server environment variable name, for compatibility with
  earlier local settings. It must contain a unique password of 16-256 characters.
- There is no default password. Credentials are never published in JavaScript,
  URLs, browser storage or server startup logs.
- Login creates an HttpOnly session cookie lasting eight hours. Sign out revokes
  it on the server. Changing ADMIN_PIN invalidates existing sessions after redeploy.
- Login attempts are limited in shared storage: five per address and fifty
  overall per fifteen minutes, including successful attempts.
- Booking lists, status changes and Excel exports all require a valid session.

LOCAL USE
1. Install Python 3.12 or later.
2. Run python -m pip install -r requirements.txt (also supplies Windows time zones).
3. Copy .env.example to settings.env. Set a new private ADMIN_PIN.
4. Use run_windows.bat, run_mac.command, run_linux.sh or python server.py.
5. Open the address printed in the terminal (normally http://127.0.0.1:8000).

Local file settings are ignored on Vercel. Locally, process environment values
win, then settings.env, then the legacy .env file. Restart after changing them.
Keep HOST=127.0.0.1; the local HTTP launcher is for your own computer.

STORAGE
With DATABASE_URL, bookings and admin sessions use private PostgreSQL storage.
Without it, the local launcher uses data/bookings.json and data/admin-auth.json.
Vercel requires DATABASE_URL and never falls back to JSON files or /tmp.
The database is accessed only from Python; do not expose its URL in public files.

Each create/update checks availability and saves in one transaction, preventing
concurrent requests from overwriting each other. The PostgreSQL adapter keeps
one locked JSON document for bookings, suitable for this small studio's volume.
At larger volumes, migrate to indexed booking/treatment tables and pagination.

Confirmed bookings are derived from the current booking records. The old
accepted_bookings.json file is no longer used. Existing local bookings.json data
continues to work. See DEPLOYMENT.md for importing it into PostgreSQL.

Weekly Excel exports include confirmed appointments scheduled in the selected
week, not the week the request arrived. Downloads require admin authentication.
A local export copy may also be saved to data/exports. On Vercel, exports are
created in memory and downloaded directly; customer data is never a static file.

EMAIL
Configure SALON_EMAIL and STUDIO_PHONE for studio contact details, and set
SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD and SMTP_FROM for email delivery.
For Gmail, use a Google App Password, never the account's normal password.
The current SMTP implementation uses STARTTLS (normally port 587).

A new request triggers a studio notification. Confirm/decline triggers a customer
email. If sending fails, the booking remains saved and Admin tells you to contact
the customer directly. Email is best effort; there is no automatic retry queue.
With no SMTP configuration, local mode writes .eml previews in data/outbox.
Vercel does not claim to save previews or exports to a persistent local directory.

TREATMENTS AND HOURS
Edit public/services.json to change treatment names, descriptions or prices.
The frontend and API verify the selected services against that catalogue.
Monday-Saturday: 10am-7pm. Sunday: 11am-7pm, nail treatments only.
Appointment times use Europe/London, including British Summer Time.
Different treatments may share the same date/time; an exact treatment already
pending or confirmed blocks another request for that slot. Confirmation also
checks for a conflicting confirmed treatment.

TESTS
python -m unittest discover -s tests -v
node --check public/admin.js

The HTTP tests use only synthetic bookings and mock email delivery. For a real
PostgreSQL integration run, set TEST_DATABASE_URL to a disposable database; the
integration tests use temporary keys and remove them afterwards.
