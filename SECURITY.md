# Nikaasi — Security

This covers the Nikaasi backend (`backend/`, FastAPI). The original Node.js prototype, which used a shared PIN, has been removed.

## Roles

| Role | How | Can |
|---|---|---|
| Citizen | anonymous | send reports (rate limited), read alerts and the public map |
| Authority | signed-in user, role `authority` | operate the dashboard, approve plans, view photos and history |
| Admin | signed-in user, role `admin` | everything above + manage users, audit log, database status |

There is always at least one active admin: the API refuses to demote or disable the last one.

## Accounts and sessions

- **Passwords** are hashed with **bcrypt** (cost 12, SHA-256 pre-hash so long passphrases aren't truncated). Minimum 10 characters, letters plus a number or symbol, common passwords rejected.
- **Sessions** are random 256-bit tokens in an `HttpOnly`, `SameSite=Strict` cookie, `Secure` with the `__Host-` prefix in production. Only the token's SHA-256 is stored, so a database leak can't be replayed.
- Sessions end after **2 h idle** or **12 h total**, on sign-out, and immediately when an admin disables the user or changes their role or password. Changing your own password signs out your other sessions.
- **Login throttling:** 20 attempts per IP and 5 failures per account per 15 minutes. Wrong passwords and unknown emails get the same message and take the same time, so attackers can't discover which emails exist.
- **First admin:** set `ADMIN_EMAIL` and `ADMIN_PASSWORD`. They're only used when the `users` table is empty. Remove `ADMIN_PASSWORD` from the environment after the first sign-in and change the password on the Users page. Or use the CLI, which prompts for passwords and never takes them as arguments:
  ```bash
  cd backend
  python -m app.cli create-user you@example.org --role admin
  python -m app.cli set-password you@example.org
  python -m app.cli list-users
  ```

## HTTP hardening

| Control | Detail |
|---|---|
| CSRF | Cookie is `SameSite=Strict`; in addition every POST/PATCH/PUT/DELETE to `/api/*` whose `Origin`/`Referer` is another site is rejected (403). Extra trusted origins: `ALLOWED_ORIGINS`. |
| Headers | `Content-Security-Policy` (scripts only from self, no framing, no plugins), `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: same-origin`, `Permissions-Policy`, `Cross-Origin-Opener-Policy`, `Cross-Origin-Resource-Policy`, `X-Permitted-Cross-Domain-Policies`; `Strict-Transport-Security` when cookies are secure; `Cache-Control: no-store` on the API |
| Input | Pydantic schemas with strict formats (sector `F/G/H-8..11`, reporter id `[A-Za-z0-9_.:-]`, report/photo ids), length and range limits, control characters stripped from free text; request bodies over 3 MB rejected; photos must be real JPEG/PNG/WebP (checked by content, not by name), ≤ 1.5 MB; SVG and other types refused |
| Rate limits (per IP) | Every request: API reads 240/min, API writes 60/min, pages/static 600/min (`RATE_LIMIT_*_PER_MIN`). Responses carry `RateLimit-Limit` / `RateLimit-Remaining`; 429s carry `Retry-After`. Blocks are written to the audit log (at most once per IP per 5 min). `/api/health` is exempt |
| Rate limits (per account / purpose) | Staff actions 30/min per user (`RATE_LIMIT_STAFF_PER_MIN`); "Ask Gemini" 5/min per user (paid API); sign-in 20 attempts/IP and 5 failures/account per 15 min; citizen reports 10/min and 60/h per IP plus 5 per reporter id per 10 min |
| Live stream | At most 6 open streams per IP and 300 in total (`MAX_STREAMS_PER_IP`, `MAX_STREAMS`). Staff streams re-check the session every 30 s and close when it is revoked or expires; these checks don't count as activity, so the idle timeout still applies |
| Server limits | At most 300 concurrent connections (`MAX_CONNECTIONS`, extra requests get 503), 5 s keep-alive timeout, 3 MB body limit |
| Host header | Only `localhost`, `127.0.0.1` and the hosts in `DOMAIN` / `ALLOWED_HOSTS` are accepted (400 otherwise): blocks Host-header spoofing and DNS rebinding |
| Admin step-up | Creating users and changing a role, status or password require the admin's **own password again** (`confirmPassword`). Failed confirmations count towards the account lockout and are audited. Admins cannot demote or disable themselves |
| Abuse | One vote per reporter in the severity rule, so repeated reports from one device don't raise severity |
| Privacy | Citizen photos are only served to signed-in users; GPS positions are never sent to browsers. Photo metadata (EXIF incl. GPS, XMP, comments) is stripped on upload. Citizens see a notice before sending, and `/privacy.html` explains the data use, including Google Gemini |
| Data retention | Daily job removes photos, exact GPS, names and device ids from reports older than `DATA_RETENTION_DAYS` (default 90); report text stays. Each purge is audited |
| Resource bounds | In memory: at most 100 resolved incidents, 300 tickets, 200 alerts, 500 reports, 200 photos (older ones come from PostgreSQL). Staff screens get active incidents plus the 20 latest resolved, so the live payload stays flat however long the server runs |
| Dependencies | Runtime versions pinned in `backend/requirements.txt`; test tools in `requirements-dev.txt` (not in the image). `python -m pip_audit -r requirements.txt` shows no known vulnerabilities (Starlette upgraded to 1.3.1, anyio 4.14.2, certifi 2026.10.18) |
| Errors | Unexpected errors return a generic message; details go to the server log only. Database connection errors are not shown to the browser |
| Production mode | `APP_ENV=production` disables `/docs` and `/openapi.json`, turns on secure cookies and HSTS |
| Audit log | Sign-ins (ok/failed/blocked), sign-outs, user changes, password changes, plan executions, scenario control, weather overrides and setting changes, with user, time and IP. Passwords never appear in it |

## Database

- The app connects as **`nikaasi_app`**, a role that is **not** a superuser and can't create databases or roles (connection limit 20). It owns only the `nikaasi` database. `CONNECT` is revoked from `PUBLIC`, and `CREATE` on schema `public` is revoked from `PUBLIC`.
- The password is random (32 characters) and lives only in the git-ignored `nikaasi/.env`.
- The schema is managed by **Alembic** migrations (`backend/migrations`), applied on startup or with `python -m app.cli migrate`.
- All queries go through SQLAlchemy with bound parameters, so there's no string-built SQL.

### Action needed on this machine: require passwords in PostgreSQL

The local PostgreSQL 18 server's `pg_hba.conf` (`C:\Program Files\PostgreSQL\18\data\pg_hba.conf`) currently uses **`trust`** for local connections. That means any local process can connect as any role **without a password**, including `postgres`. The `nikaasi_app` password only takes effect after you change that file:

1. Open the file as Administrator.
2. On the `host` lines for `127.0.0.1/32` and `::1/128`, change the method `trust` to `scram-sha-256`.
3. Before restarting, make sure you know the `postgres` password, or set one while trust is still on:
   ```bash
   psql -U postgres -h localhost -c "\password postgres"
   ```
4. Restart the service: *Services → postgresql-x64-18 → Restart*.

## Deployment checklist

- [ ] HTTPS in front of the app (reverse proxy / load balancer); then `COOKIE_SECURE=1` (default in production) and `TRUST_PROXY=1` if the proxy sets `X-Forwarded-For`
- [ ] `APP_ENV=production`
- [ ] `DOMAIN` / `ALLOWED_HOSTS` set to the public hostname (the deploy compose does this from `DOMAIN`)
- [ ] Strong `POSTGRES_PASSWORD`; database not published to the internet (docker-compose keeps it on the internal network)
- [ ] First admin created, `ADMIN_PASSWORD` removed from the environment, password changed
- [ ] `pg_hba.conf` uses `scram-sha-256`, not `trust`
- [ ] Gemini key restricted in Google AI Studio and rotated if it was ever shared
- [ ] Database backups (`pg_dump nikaasi`) scheduled
- [ ] Run a single app worker (`--workers 1`). The simulation, live updates and rate limits are in-process, so scaling out needs a shared store (e.g. Redis) first.

## Known limits

- Rate limits and the simulation are per-process (see the last checklist item).
- There's no email-based password reset. Admins reset passwords on the Users page or with the CLI.
- There's no multi-factor authentication yet.
- Citizen reports are anonymous by design. Abuse is limited by rate limits and by the fusion rule: a single report can't trigger a response.
