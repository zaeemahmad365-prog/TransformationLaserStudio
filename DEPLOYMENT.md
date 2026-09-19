# Deploy on Vercel

The original repository served static pages on Vercel, while `/api/admin/bookings`
existed only in the locally launched `server.py`. A local `settings.env` is not a
Vercel runtime configuration file. This change adds `api/index.py` and routes
`/api/*` to that Python function; `public/` remains the static output directory.

## 1. Configure the existing project

In Vercel → your project → Settings → Build and Deployment:

| Setting | Value |
| --- | --- |
| Root Directory | Repository root; leave blank, **not** `public` |
| Framework Preset | Other |
| Build Command | Empty; `vercel.json` sets this |
| Output Directory | `public`; `vercel.json` sets this |
| Install Command | Leave the override disabled; Python dependencies use `requirements.txt` |

Keep the existing GitHub connection and domain. No second public API host is needed.

## 2. Connect persistent PostgreSQL storage

Connect a PostgreSQL database, for example Neon, through the project's Storage /
[Vercel Marketplace](https://vercel.com/docs/storage/marketplace) integration.
Use the provider's pooled connection string and enable TLS. Set `DATABASE_URL` to
that string if the integration uses a different variable name. Choose a region
near your Vercel function region. Review the provider's plan before creating it.

The database user needs permission to create the private `tls_documents` table
in its default schema. The API creates it on first use and serializes schema
initialization, booking writes, sessions and login attempts across instances.
Use a dedicated database/schema for this application. Do not expose this table
through a public client API. With Supabase, disable Data API exposure for this
schema or revoke API-role access; this app connects directly with PostgreSQL.

The database must remain external to Vercel Functions. Storing JSON/SQLite in the
function directory or `/tmp` will not give durable shared bookings.

## 3. Add runtime environment variables

In Settings → Environment Variables, add these for **Production**:

| Variable | Value |
| --- | --- |
| `ADMIN_PIN` | A **new**, unique password of 16–256 characters from your password manager |
| `DATABASE_URL` | The private pooled PostgreSQL connection string with TLS |
| `SALON_EMAIL` | The studio's notification/contact email |
| `STUDIO_PHONE` | The studio's contact number |
| `SMTP_HOST` | Your SMTP host, e.g. `smtp.gmail.com` |
| `SMTP_PORT` | `587` for the supported STARTTLS transport |
| `SMTP_USER` | Your SMTP account |
| `SMTP_PASSWORD` | Its app password / SMTP credential |
| `SMTP_FROM` | The sender address permitted by your SMTP provider |

The first two are required for admin access. SMTP is required for actual email
delivery but does not prevent bookings from being saved. Keep all credentials
server-side; do not add `NEXT_PUBLIC_`, `VITE_` or another frontend prefix.
Mark secrets Sensitive in Vercel where available. Never commit settings.env or
paste credentials into a GitHub issue, pull request, source file or vercel.json.

Previous repository versions included a public default admin password. Treat
any password already used there as compromised and replace it. Removing it from
the latest code does not remove it from Git history.

Use separate database credentials and a different admin password for Preview.
Leave previews unconfigured until you have a separate test database, or use
Vercel deployment protection. Do not connect branch previews to live bookings.

## 4. Optional: migrate existing local bookings

The uploaded settings file does not contain bookings. Existing local requests
remain in your own `data/bookings.json`; they do not transfer automatically.
Back up that file outside the repository. With DATABASE_URL set privately in
settings.env or your shell, run from the repository root:

```sh
python -m pip install -r requirements.txt
python scripts/import_bookings.py /absolute/path/to/bookings.json
```

The importer runs locally, inserts only into an empty destination, preserves
booking IDs/statuses, and does not send emails. It refuses to overwrite existing
database bookings. Import into a preview database first if you need to rehearse.
Do not upload customer JSON files to GitHub or public website storage.

## 5. Deploy and verify

After configuring the environment, merge this change into your production
branch and redeploy. Environment changes apply to new deployments.

1. Open `/api/health`: expect HTTP 200 and JSON. This verifies API routing, not
   the database or email credentials.
2. Open `/api/admin/bookings` in a signed-out browser: expect HTTP 401. Without
   ADMIN_PIN configured, expect 503. A 404 means the root/routing setup is wrong.
3. Open `/admin.html`, enter your new password and click Sign in. Login should
   return 200 with an HttpOnly, Secure, SameSite=Strict cookie. Bookings should load.
4. On Preview with SMTP disabled, submit one synthetic booking. Confirm/decline
   it, export its appointment week, and check the downloaded workbook.
5. Reload / open another function instance: the booking must still exist.
6. Sign out: fetching bookings or exporting with that old cookie must fail.

A 503 at login or while accessing bookings means required configuration/storage
is unavailable. Check DATABASE_URL, database reachability and schema permissions.
A 429 means the login limit was reached; wait for the Retry-After interval.

## Security and operations

Passwords are checked on the server using a constant-time digest comparison.
Only a random session token reaches the cookie, and only its hash is stored in
the database. Sessions expire after eight hours, are revoked on logout and become
invalid when ADMIN_PIN changes on all active deployments. Admin writes require
an exact same-origin request and a custom CSRF header; no cross-origin API access
is enabled. API responses are not cached. Repository secrets/data are excluded
from deployment uploads/function bundles, and only public/ is served statically.

The built-in limiter applies to admin login. Public booking submissions and email
volume may need Vercel Firewall limits or CAPTCHA if the site receives abuse.
Maintain database backups and remove customer data when no longer needed.

References: [Vercel Python handlers](https://vercel.com/docs/functions/runtimes/python/api-directory),
[Vercel environment variables](https://vercel.com/docs/environment-variables),
[Vercel storage](https://vercel.com/docs/storage).
