# Carpentry Quote Tool

Generate professional, Arabic (RTL), SAR carpentry quotes — **one Excel file per
client, one sheet per item** — from a simple English web UI. Prices live in a
persistent Price Book, and every number in the generated sheet is a **live
formula** (nothing hardcoded).

It runs two ways from the **same code**:

- **Locally on Windows** — double-click `start.bat`.
- **On Vercel** — a serverless deployment with cloud storage (Vercel Blob).

---

## What it does

- **Price Book** (persists across sessions): every material/service with an
  Arabic name, unit, and base SAR price. Categories include Boards, Edge/CNC,
  Hardware (hinges, magnets, handles, slides), Adhesives, Lighting,
  Paint/Polish, Mirror/Glass, Transport, and Labour.
- **Projects** (one per client): company/client/unit/location metadata.
- **Items** (one Excel sheet each): name, place, time, **photo**, material lines,
  labour days, and transport.
  - **MDF/Veneer** lines capture length × width × thickness with a **Polish/Paint**
    toggle (area is auto-computed and priced per m²).
  - **Laminated** lines have a **CNC cutting / PVC edge binding** toggle.
- **Generated workbook** per client:
  - `الأسعار` (Prices) — the single source of prices; every sheet references it.
  - one sheet per item — line totals `= qty × price`, subtotal `SUM`, a
    category cost breakdown (`SUMIF`), and an estimated time (days/weeks/months).
  - `الملخص` (Summary) — per-item totals, grand total, and a project timeline.
- **Currency:** SAR, Western digits. **UI:** English. **Sheet:** Arabic, RTL.

---

## Run locally (Windows)

1. Install [Python 3.10+](https://www.python.org/downloads/) (tick *Add to PATH*).
2. Double-click **`start.bat`**.
3. The browser opens at `http://127.0.0.1:5000`.

In local mode, data is stored under `data/` (price book, projects, uploaded
photos). No internet or account needed.

---

## Deploy to Vercel

Storage uses **Vercel Blob** (no servers to manage). One-time setup:

```bash
# 1) Link this folder to a Vercel project
vercel link

# 2) Create a Blob store (adds BLOB_READ_WRITE_TOKEN to the project)
vercel blob create-store carpentry-quote-blob

# 3) Sign-in: set a session secret + the admin account
vercel env add SECRET_KEY production       # any long random string
vercel env add ADMIN_USERNAME production   # your login username
vercel env add ADMIN_PASSWORD production   # your login password

# 4) Deploy to production
vercel --prod
```

The app auto-detects `BLOB_READ_WRITE_TOKEN`: when present it uses Vercel Blob,
otherwise it falls back to local files. Mutable state (price book, projects) is
stored as immutable, timestamped versions and the newest is read via the
consistent List API, so there is no read-after-write CDN staleness.

### Local development against the cloud store

```bash
vercel env pull .env.local      # pulls BLOB_READ_WRITE_TOKEN
set -a; . ./.env.local; set +a  # (Windows: load the vars into your shell)
python api/index.py
```

---

## Security note

The app is gated by a username/password **login**. Passwords are hashed
(PBKDF2-HMAC-SHA256) and the session is an HMAC-signed, httpOnly cookie keyed by
`SECRET_KEY`. The first admin is created from `ADMIN_USERNAME` / `ADMIN_PASSWORD`
on first sign-in; the account then lives (hashed) in Blob. To change the
password later, delete the `users` blob and update the env, or add a
change-password flow. If `SECRET_KEY` is unset (plain local dev), auth is off.

---

## Project layout

```
api/
  index.py            Flask app (API + serves the UI); the Vercel function
  generator.py        openpyxl workbook generator (Arabic/RTL/SAR, formulas)
  store.py            storage layer: Vercel Blob  OR  local files
  seed_pricebook.json starter Price Book (extracted from a real workbook)
  web/                index.html, styles.css, app.js (English UI)
vercel.json           routes everything to the Python function
requirements.txt      Flask, openpyxl, vercel_blob, requests
start.bat             Windows local launcher
data/                 local-mode storage (ignored in cloud)
```
