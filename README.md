# Village community website

A mobile-first Telugu village-community website built with Flask, SQLite, Jinja templates, and plain JavaScript. Images are validated and thumbnail versions are generated for public grids. No JavaScript framework or build step is required.

## Run locally

Use Python 3.10 or later:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
$env:ADMIN_USER = "village-admin"
$env:ADMIN_PASSWORD = "choose-a-private-password-of-12-characters-or-more"
$env:SECRET_KEY = (python -c "import secrets; print(secrets.token_hex(32))")
flask --app app run
```

Open `http://127.0.0.1:5000`. The first run creates the admin account from `ADMIN_USER` and `ADMIN_PASSWORD`; use those values on the login page. Keep the same `SECRET_KEY` between restarts if you enable phone-limited voting. The development secret is temporary if that variable is omitted.

Optional sample content can be added with:

```powershell
python seed.py
```

This adds a Telugu placeholder event, a game, an open photo contest, two generated sample images, and one update. It does not erase existing content. Change the bracketed placeholders in the admin pages before sharing the site.

### Test a voting flow

1. Sign in as the admin, open **Events**, and create an event and a contest, or use the seeded contest.
2. Upload at least two JPG, PNG, WEBP, or GIF photos. Each image is limited to 10 MB.
3. Open the contest page in a private browser window, select an entry, and submit one vote.
4. Submit again in the same browser to verify that the device vote is rejected. Clearing cookies can bypass cookie-based voting; this is acceptable for an informal village contest.
5. As admin, close voting, publish results, and check the results page. Use **Save winners** to add the winning entries to the event.

Set `REQUIRE_PHONE_FOR_VOTE=1` to also limit each contest to one submitted phone number. The number is normalized and stored only as an HMAC-SHA256 hash; there is no phone verification, so this is not identity proof. Keep `SECRET_KEY` stable when this option is enabled.

## Configuration

Set environment variables in the hosting dashboard or a private local environment. Do not commit credentials or `.env` files.

| Variable | Purpose |
|---|---|
| `APP_ENV` | Set to `production` on a live deployment. Production refuses to start without `ADMIN_USER`, `ADMIN_PASSWORD`, and `SECRET_KEY`. |
| `ADMIN_USER` | Initial admin login name; at least one initial admin is bootstrapped from this setting. |
| `ADMIN_PASSWORD` | Initial admin password, at least 12 characters. |
| `SECRET_KEY` | Stable, randomly generated Flask session/CSRF secret. Generate with `python -c "import secrets; print(secrets.token_hex(32))"`. Do not derive it from the password. |
| `DATA_DIR` | Persistent data directory; defaults to the project folder. |
| `DB_PATH` | SQLite database path; defaults to `DATA_DIR/village.db`. |
| `UPLOAD_DIR` | Uploaded images and videos; defaults to `DATA_DIR/uploads`. |
| `REQUIRE_PHONE_FOR_VOTE` | Set to `1`, `true`, `yes`, or `on` to require one phone number per contest vote. Defaults to off. |
| `ORG_NAME` | Group name shown in the header. Defaults to `శేషంపల్లి`. |
| `CONTACT_PHONE`, `WHATSAPP_NUMBER`, `WHATSAPP_GROUP_LINK` | Public contact details; WhatsApp number uses international digits. |
| `UPI_ID`, `VILLAGE_ADDRESS` | Optional contact-page details. |
| `BASE_URL` | Public HTTPS URL used to make share links. |
| `TZ_HOURS` | Local time offset for contest schedules; defaults to India Standard Time (`5.5`). |
| `MEMBER_CODE` | Optional registration code for organization members. |

Database migrations run automatically and preserve existing votes. When upgrading an older database, set the new admin environment variables before the first start; the first admin account is updated once to those credentials. Back up the database and uploads before deploying an upgrade.

## Deploy on PythonAnywhere (free tier)

1. Create a PythonAnywhere account and a **Web** app using a supported Python version. Put the project in your home directory (for example, `/home/<username>/village_pro`).
2. Open a Bash console, create a virtual environment, and install dependencies:
   ```bash
   cd ~/village_pro
   python3 -m venv .venv
   . .venv/bin/activate
   pip install -r requirements.txt
   ```
3. In the Web tab, set the source directory to the project folder. Edit the WSGI configuration file so the project directory is on `sys.path`, set `APP_ENV`, `ADMIN_USER`, `ADMIN_PASSWORD`, `SECRET_KEY`, and optional contact settings in that private WSGI file, then import `application` from `wsgi`. Do not put that file in a public repository.
4. Keep `DB_PATH` and `UPLOAD_DIR` under your persistent home directory (for example, `/home/<username>/village_data/village.db` and `/home/<username>/village_data/uploads`). Create the uploads directory and ensure the web app can write to it.
5. Save the WSGI file and press **Reload** in the Web tab. Visit the PythonAnywhere site, sign in with the configured admin values, and run the voting-flow test above. Back up the database and uploads regularly.

The app's `wsgi.py` exports both `app` and `application`. PythonAnywhere's free tier uses its hosted domain and has resource limits; keep uploads modest and verify the current account limits in its dashboard.

## Deploy on Render with persistent storage

1. Push the project to a private Git repository and create a Render **Web Service** from it.
2. Set the build command to `pip install -r requirements.txt` and the start command to `gunicorn wsgi:app`.
3. Attach a persistent disk to the service on a Render plan that supports disks. A free ephemeral filesystem is not suitable for votes or uploads because it can be replaced during deploys or restarts.
4. Mount the disk at `/var/data`, then set `APP_ENV=production`, `DATA_DIR=/var/data`, `DB_PATH=/var/data/village.db`, and `UPLOAD_DIR=/var/data/uploads`. Add `ADMIN_USER`, a private `ADMIN_PASSWORD` of at least 12 characters, a generated `SECRET_KEY`, and the optional group/contact variables in Render's environment settings.
5. Deploy, open the service URL, sign in, and test voting and results. The persistent disk is attached to one service instance; keep SQLite on that mounted disk and back up the database and uploads independently.

## Five-step event guide

1. **Add an event:** Admin → Events → create the occasion, date, place, and description.
2. **Add games:** Edit the event and add games, activities, and times.
3. **Create a contest:** Add a contest to the event; choose its opening and closing times.
4. **Upload photos and open voting:** Add contest photos, then open or schedule voting and share the contest link. Admins can use a preset duration or enter a custom duration from 15 minutes to one year; the schedule form also accepts exact local start/end times.
5. **Publish results and save winners:** Close voting, publish the results, then save the winning entries to the event's winners.

## Security and operations

- Every POST form uses a CSRF token. Session and voting cookies are HttpOnly and SameSite=Lax; cookies are Secure in production.
- The header's **ENG / తెలుగు** switch changes the main public-page labels and remembers the selection for the session. Event titles, news, and other user-entered content remain as entered. Text fields are marked for Telugu input; if Telugu letters do not appear on the device keyboard, enable Telugu in Android keyboard settings and switch to it from the keyboard.
- Login failures are rate-limited in SQLite, shared across app workers using the same database.
- Uploads use sanitized filenames, extension and size limits, and Pillow image decoding checks. Images are limited to 10 MB; videos are limited to 128 MB per file.
- Cookie voting can be bypassed by clearing cookies or changing devices. The optional hashed-phone limit raises the effort but does not verify ownership.
- Keep `SECRET_KEY`, admin credentials, `village.db`, and `uploads/` private. Back up `village.db` and `uploads/` together.
