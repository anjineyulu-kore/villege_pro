"""Village Organization app (Telugu/English, installable on Android, admin dashboard)."""
import os, io, csv, time, sqlite3, secrets, uuid, urllib.parse, hashlib, hmac, warnings
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from functools import wraps
from markupsafe import Markup
from flask import (Flask, g, render_template, request, redirect, url_for, session, flash, abort,
                   send_from_directory, jsonify, make_response, Response)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from werkzeug.middleware.proxy_fix import ProxyFix
from PIL import Image, ImageOps, UnidentifiedImageError
from i18n import T, AIM, CATS, REPORT_CATS, PROGRAMS, SITE_TEXT

from config import (
    BASE_DIR as BASE, CONTACT_PHONE, DB_PATH as DB, HTTPS, IS_PRODUCTION, ORG_NAME,
    REQUIRE_PHONE_FOR_VOTE, SECRET_KEY, THUMBNAILS_DIR as THUMBNAILS, TZ_HOURS, UPI_ID,
    UPLOADS_DIR as UPLOADS, VILLAGE_ADDRESS, WHATSAPP_GROUP_LINK, WHATSAPP_NUMBER,
)
from db import close_db, get_db as db, init_db, q, q1, run

os.makedirs(UPLOADS, exist_ok=True)
os.makedirs(THUMBNAILS, exist_ok=True)

app = Flask(__name__)
app.secret_key = SECRET_KEY or secrets.token_hex(32)
if IS_PRODUCTION:
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)
MAX_MEDIA_BYTES = 128 * 1024 * 1024
MAX_IMAGE_BYTES = 10 * 1024 * 1024
app.config.update(MAX_CONTENT_LENGTH=MAX_MEDIA_BYTES + 1024 * 1024, SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
                  SESSION_COOKIE_SECURE=HTTPS, PERMANENT_SESSION_LIFETIME=90 * 24 * 3600)

@app.teardown_appcontext
def close_connection(_error):
    close_db()

# ---------- language / helpers ----------
def lang():
    return session.get("lang", "te") if session.get("lang") in ("te", "en") else "te"

def tr(key, **values):
    text = T[lang()].get(key) or T["en"].get(key, key)
    return text.format(**values) if values else text

def site_text(key, **values):
    text = SITE_TEXT[lang()].get(key, SITE_TEXT["te"].get(key, key))
    return text.format(**values) if values else text

def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

VOTE_DURATIONS = (1, 6, 12, 24, 48, 72, 168)

def requested_vote_duration():
    custom_hours = request.form.get("custom_duration_hours", "").strip()
    if custom_hours:
        try:
            hours = Decimal(custom_hours)
            minutes = hours * 60
            if not hours.is_finite() or not Decimal("0.25") <= hours <= Decimal("8760"):
                return None
            if minutes != minutes.to_integral_value():
                return None
            return timedelta(minutes=int(minutes))
        except InvalidOperation:
            return None
    try:
        hours = int(request.form.get("duration_hours", "24"))
    except ValueError:
        return None
    return timedelta(hours=hours) if hours in VOTE_DURATIONS else None

def parse_vote_datetime(value):
    if not value:
        return None
    try:
        local = datetime.strptime(value, "%Y-%m-%dT%H:%M")
        return local.replace(tzinfo=timezone(timedelta(hours=TZ_HOURS))).astimezone(
            timezone.utc
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return None

def vote_datetime_input(value):
    try:
        utc = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        return utc.astimezone(timezone(timedelta(hours=TZ_HOURS))).strftime("%Y-%m-%dT%H:%M")
    except (TypeError, ValueError):
        return ""

def vote_datetime_display(value):
    try:
        utc = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        local = utc.astimezone(timezone(timedelta(hours=TZ_HOURS)))
        return local.strftime("%d-%m-%Y %I:%M %p")
    except (TypeError, ValueError):
        return ""

def contest_state(contest):
    if contest["status"] == "published" or contest["results_published"]:
        return "published"
    if contest["status"] != "open":
        return contest["status"]
    if contest["starts_at"] and contest["starts_at"] > now_iso():
        return "scheduled"
    return "open"

@app.before_request
def close_expired_contests():
    if request.endpoint == "static":
        return
    db().execute("UPDATE contests SET status='closed' WHERE status='open' AND closes_at IS NOT NULL AND closes_at<=? "
                 "AND (starts_at IS NULL OR starts_at<=?)",
                 (now_iso(), now_iso()))
    db().commit()

@app.before_request
def load_user():
    uid = session.get("uid")
    g.user = q1("SELECT * FROM users WHERE id=?", uid) if uid else None

@app.before_request
def check_csrf():
    if request.method == "POST":
        tok = session.get("csrf")
        if not tok or not secrets.compare_digest(request.form.get("csrf", ""), tok):
            abort(400)

@app.after_request
def headers(r):
    r.headers.setdefault("X-Content-Type-Options", "nosniff")
    r.headers.setdefault("X-Frame-Options", "DENY")
    r.headers.setdefault("Referrer-Policy", "same-origin")
    return r

@app.errorhandler(413)
def request_too_large(_):
    return render_template("upload_error.html"), 413

@app.context_processor
def inject():
    session.setdefault("csrf", secrets.token_hex(16))
    u = g.get("user")
    return dict(csrf=session["csrf"], org=ORG_NAME, user=u, t=tr, s=site_text, lang=lang(),
                whatsapp_share_url=whatsapp_share_url,
                whatsapp_group_link=WHATSAPP_GROUP_LINK or whatsapp_contact_url() or url_for("contact"),
                vote_datetime_display=vote_datetime_display,
                CATS=CATS, ACTIVITY_LABELS=ACTIVITY_LABELS,
                is_org_member=bool(u and (u["is_admin"] or u["role"] == "member")),
                require_phone_for_vote=REQUIRE_PHONE_FOR_VOTE,
                year=date.today().year, ep=request.endpoint or "")

def login_required(f):
    @wraps(f)
    def w(*a, **k):
        if not g.user:
            flash(tr("f_login_first")); return redirect(url_for("login"))
        return f(*a, **k)
    return w

def admin_required(f):
    @wraps(f)
    @login_required
    def w(*a, **k):
        if not g.user["is_admin"]: abort(403)
        return f(*a, **k)
    return w

def org_member_required(f):
    @wraps(f)
    @login_required
    def w(*a, **k):
        if not (g.user["is_admin"] or g.user["role"] == "member"): abort(403)
        return f(*a, **k)
    return w

IMAGE_FORMATS = {
    "jpg": "JPEG", "jpeg": "JPEG", "png": "PNG", "webp": "WEBP", "gif": "GIF",
}
ACTIVITY_OPTIONS = (
    "Clean-up drive", "Tree planting", "Health camp", "Blood donation",
    "Education workshop", "Sports and games", "Cultural program",
    "Awareness rally", "Community meeting", "Food distribution", "Books distribution",
)
ACTIVITY_LABELS = dict(zip(ACTIVITY_OPTIONS, (
    "activity_cleanup", "activity_tree_planting", "activity_health_camp", "activity_blood_donation",
    "activity_education", "activity_sports", "activity_culture", "activity_awareness",
    "activity_meeting", "activity_food_distribution", "activity_book_distribution",
)))
PROGRAM_KINDS = ("game", "education", "other")
MAX_EVENT_PROGRAMS = 20

def _image_extension(upload, extensions):
    safe_name = secure_filename(upload.filename or "")
    if not safe_name or "." not in safe_name:
        return None
    extension = safe_name.rsplit(".", 1)[-1].lower()
    if extension not in extensions or extension not in IMAGE_FORMATS:
        return None
    upload.stream.seek(0, os.SEEK_END)
    size = upload.stream.tell()
    upload.stream.seek(0)
    if not size or size > MAX_IMAGE_BYTES:
        return None
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(upload.stream) as image:
                if image.format != IMAGE_FORMATS[extension]:
                    return None
                image.verify()
    except (
        Image.DecompressionBombError, Image.DecompressionBombWarning,
        UnidentifiedImageError, OSError, ValueError,
    ):
        return None
    finally:
        upload.stream.seek(0)
    return extension

def _make_thumbnail(filename):
    source = os.path.join(UPLOADS, filename)
    target = os.path.join(THUMBNAILS, filename + ".webp")
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        with Image.open(source) as image:
            image.seek(0)
            image = ImageOps.exif_transpose(image)
            image.thumbnail((480, 480), Image.Resampling.LANCZOS)
            image = image.convert("RGBA" if "A" in image.getbands() else "RGB")
            image.save(target, "WEBP", quality=78, method=4)
    return target

def _save_image_upload(upload, extensions, max_bytes=MAX_IMAGE_BYTES):
    extension = _image_extension(upload, extensions)
    if not extension:
        return None
    upload.stream.seek(0, os.SEEK_END)
    size = upload.stream.tell()
    upload.stream.seek(0)
    if size > max_bytes:
        return None
    name = uuid.uuid4().hex + "." + extension
    upload.save(os.path.join(UPLOADS, name))
    try:
        _make_thumbnail(name)
    except (
        Image.DecompressionBombError, Image.DecompressionBombWarning,
        UnidentifiedImageError, OSError, ValueError,
    ):
        rm_file(name)
        return None
    return name

def save_media(f):
    if not f or not f.filename: return None
    safe_name = secure_filename(f.filename)
    ext = safe_name.rsplit(".", 1)[-1].lower() if "." in safe_name else ""
    if ext in IMAGE_FORMATS:
        name = _save_image_upload(f, IMAGE_FORMATS, MAX_IMAGE_BYTES)
        return (name, "image") if name else None
    head = f.stream.read(16); f.stream.seek(0)
    if ext == "mp4" and head[4:8] == b"ftyp":
        media_type = "video"
    elif ext == "webm" and head.startswith(b"\x1a\x45\xdf\xa3"):
        media_type = "video"
    else:
        return None
    f.stream.seek(0, os.SEEK_END)
    size = f.stream.tell()
    f.stream.seek(0)
    if size > MAX_MEDIA_BYTES:
        return None
    name = uuid.uuid4().hex + "." + ext
    f.save(os.path.join(UPLOADS, name))
    return name, media_type

def save_image(f):
    return _save_image_upload(f, IMAGE_FORMATS, MAX_IMAGE_BYTES) if f and f.filename else None

def save_program_person_image(f):
    return _save_image_upload(f, ("png", "jpg", "jpeg", "webp"), 8 * 1024 * 1024) if f and f.filename else None

def save_donation_qr(f):
    return _save_image_upload(f, ("png", "jpg", "jpeg", "webp"), 5 * 1024 * 1024) if f and f.filename else None

def rm_file(name):
    if name:
        if os.path.basename(name) != name:
            return
        for path in (os.path.join(UPLOADS, name), os.path.join(THUMBNAILS, name + ".webp")):
            try: os.remove(path)
            except FileNotFoundError: pass

def remove_if_unreferenced(name):
    if not name:
        return
    referenced = any(
        q1(sql, name)
        for sql in (
            "SELECT 1 FROM memories WHERE filename=?",
            "SELECT 1 FROM media WHERE filename_or_youtube_url=?",
            "SELECT 1 FROM entries WHERE filename=?",
            "SELECT 1 FROM updates WHERE photo=?",
            "SELECT 1 FROM events WHERE donation_qr=?",
            "SELECT 1 FROM event_program_people WHERE photo=?",
            "SELECT 1 FROM reports WHERE photo=?",
            "SELECT 1 FROM proposal_media WHERE filename=?",
            "SELECT 1 FROM winners WHERE photo=?",
        )
    )
    if not referenced:
        rm_file(name)

def abs_url(endpoint, **kw):
    base = os.environ.get("BASE_URL", "").rstrip("/")
    return (base or request.url_root.rstrip("/")) + url_for(endpoint, **kw)

def whatsapp_share_url(text, endpoint, **values):
    share_text = f"{text}\n{abs_url(endpoint, **values)}"
    return "https://wa.me/?text=" + urllib.parse.quote(share_text)

def whatsapp_contact_url(text="నమస్కారం"):
    number = "".join(char for char in WHATSAPP_NUMBER if char.isdigit())
    return f"https://wa.me/{number}?text={urllib.parse.quote(text)}" if number else None

def clean_cat(c, allowed=CATS, default="general"):
    return c if c in allowed else default

def parse_event_id(value):
    value = (value or "").strip()
    if not value:
        return None
    try:
        event_id = int(value)
    except ValueError:
        raise ValueError("Choose a valid event.")
    if not q1("SELECT id FROM events WHERE id=?", event_id):
        raise ValueError("Choose a valid event.")
    return event_id

def submitted_activities():
    selected = [value for value in request.form.getlist("activities") if value in ACTIVITY_OPTIONS]
    custom = request.form.get("activities_custom", "")
    selected.extend(line.strip()[:100] for line in custom.splitlines() if line.strip())
    return "\n".join(dict.fromkeys(selected))[:2000]

def activity_form_values(value=""):
    lines = (value or "").splitlines()
    return dict(selected_activities=[line for line in lines if line in ACTIVITY_OPTIONS],
                custom_activities="\n".join(line for line in lines if line not in ACTIVITY_OPTIONS))

def submitted_programs():
    fields = [request.form.getlist(name) for name in (
        "program_type", "program_title", "program_details", "program_result", "program_id", "program_time",
    )]
    count = max(map(len, fields), default=0)
    if count > MAX_EVENT_PROGRAMS:
        raise ValueError(tr("program_limit"))
    programs = []
    for index in range(count):
        kind = fields[0][index] if index < len(fields[0]) else "other"
        title = fields[1][index].strip()[:120] if index < len(fields[1]) else ""
        details = fields[2][index].strip()[:1000] if index < len(fields[2]) else ""
        result = fields[3][index].strip()[:1000] if index < len(fields[3]) else ""
        raw_id = fields[4][index].strip() if index < len(fields[4]) else ""
        program_time = fields[5][index].strip() if index < len(fields[5]) else ""
        try:
            program_id = int(raw_id) if raw_id else None
        except ValueError:
            raise ValueError(tr("program_invalid_id"))
        if program_time:
            try:
                datetime.strptime(program_time, "%H:%M")
            except ValueError:
                raise ValueError("Enter a valid program time.")
        if not any((title, details, result, program_time)):
            continue
        if not title:
            raise ValueError(tr("program_title_required"))
        if kind not in PROGRAM_KINDS:
            kind = "other"
        programs.append(dict(
            id=program_id, kind=kind, title=title, details=details,
            result=result, time=program_time,
        ))
    return programs

def event_program_form_values(rows):
    return dict(program_items=rows or [dict(id="", kind="game", title="", details="", result="", time="")])

def submitted_program_form_values():
    fields = [request.form.getlist(name) for name in (
        "program_type", "program_title", "program_details", "program_result", "program_id", "program_time",
    )]
    count = min(max(map(len, fields), default=0), MAX_EVENT_PROGRAMS)
    rows = []
    for index in range(count):
        rows.append(dict(
            kind=fields[0][index] if index < len(fields[0]) and fields[0][index] in PROGRAM_KINDS else "other",
            title=fields[1][index] if index < len(fields[1]) else "",
            details=fields[2][index] if index < len(fields[2]) else "",
            result=fields[3][index] if index < len(fields[3]) else "",
            id=fields[4][index] if index < len(fields[4]) else "",
            time=fields[5][index] if index < len(fields[5]) else "",
        ))
    return event_program_form_values(rows)

def throttled():
    ip = request.remote_addr or "unknown"
    now = time.time()
    con = db()
    con.execute("BEGIN IMMEDIATE")
    con.execute("DELETE FROM login_attempts WHERE ts<?", (now - 600,))
    attempts = con.execute(
        "SELECT COUNT(*) FROM login_attempts WHERE ip=? AND ts>=?",
        (ip, now - 600),
    ).fetchone()[0]
    if attempts < 8:
        con.execute("INSERT INTO login_attempts(ip,ts) VALUES(?,?)", (ip, now))
    con.commit()
    return attempts >= 8

# ---------- language, PWA ----------
@app.route("/lang/<code>")
def set_lang(code):
    if code in SITE_TEXT:
        session["lang"] = code; session.permanent = True
    nxt = request.args.get("next", "/")
    return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else "/")

@app.route("/manifest.webmanifest")
def manifest():
    icons = [dict(src=f"/static/icons/icon-{s}.png", sizes=f"{s}x{s}", type="image/png", purpose="any maskable") for s in (192, 512)]
    r = make_response(jsonify(name=ORG_NAME, short_name=ORG_NAME[:12], start_url="/", scope="/", display="standalone",
                              background_color="#FFF8EC", theme_color="#1E6B3A", lang="te", icons=icons))
    r.headers["Content-Type"] = "application/manifest+json"; return r

@app.route("/sw.js")
def sw():
    r = send_from_directory(os.path.join(BASE, "static"), "sw.js", mimetype="application/javascript")
    r.headers["Cache-Control"] = "no-cache"; return r

@app.route("/offline")
def offline():
    return render_template("offline.html")

@app.route("/uploads/<path:name>")
def uploaded(name):
    public = bool(
        q1("SELECT 1 FROM memories WHERE filename=?", name)
        or q1("SELECT 1 FROM media WHERE filename_or_youtube_url=?", name)
        or q1("SELECT 1 FROM entries WHERE filename=?", name)
        or q1("SELECT 1 FROM updates WHERE photo=?", name)
        or q1("SELECT 1 FROM events WHERE donation_qr=?", name)
        or q1("SELECT 1 FROM event_program_people WHERE photo=?", name)
        or q1("SELECT 1 FROM winners WHERE photo=?", name)
    )
    if not public:
        report = q1("SELECT user_id FROM reports WHERE photo=?", name)
        proposal = q1("SELECT p.user_id,p.status FROM proposal_media m JOIN event_proposals p ON p.id=m.proposal_id WHERE m.filename=?", name)
        owns_private_media = bool(
            g.user and (
                g.user["is_admin"]
                or report and report["user_id"] == g.user["id"]
                or proposal and proposal["status"] == "pending" and proposal["user_id"] == g.user["id"]
            )
        )
        if not owns_private_media: abort(404)
    r = send_from_directory(UPLOADS, name)
    r.headers["Cache-Control"] = "public, max-age=604800" if public else "private, no-store"
    return r

@app.route("/thumbnails/<path:name>")
def thumbnail(name):
    if os.path.basename(name) != name or secure_filename(name) != name:
        abort(404)
    public = bool(
        q1("SELECT 1 FROM memories WHERE filename=? AND media_type='image'", name)
        or q1("SELECT 1 FROM media WHERE filename_or_youtube_url=? AND kind='photo'", name)
        or q1("SELECT 1 FROM entries WHERE filename=?", name)
        or q1("SELECT 1 FROM updates WHERE photo=?", name)
        or q1("SELECT 1 FROM events WHERE donation_qr=?", name)
        or q1("SELECT 1 FROM event_program_people WHERE photo=?", name)
        or q1("SELECT 1 FROM winners WHERE photo=?", name)
    )
    if not public or not os.path.isfile(os.path.join(UPLOADS, name)):
        abort(404)
    thumbnail_name = name + ".webp"
    if not os.path.isfile(os.path.join(THUMBNAILS, thumbnail_name)):
        try:
            _make_thumbnail(name)
        except (
            Image.DecompressionBombError, Image.DecompressionBombWarning,
            UnidentifiedImageError, OSError, ValueError,
        ):
            app.logger.warning("Could not generate thumbnail for public image %s", name)
            abort(415)
    response = send_from_directory(THUMBNAILS, thumbnail_name, mimetype="image/webp")
    response.headers["Cache-Control"] = "public, max-age=2592000, immutable"
    return response

# ---------- auth ----------
@app.route("/register", methods=["GET", "POST"])
def register():
    code = os.environ.get("MEMBER_CODE")
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        phone = "".join(c for c in request.form.get("phone", "") if c.isdigit())
        pw = request.form.get("password", "")
        if code and request.form.get("code", "") != code: flash(tr("f_bad_code"))
        elif not name or len(phone) < 8 or len(pw) < 6: flash(tr("f_invalid"))
        elif q1("SELECT 1 FROM users WHERE phone=?", phone): flash(tr("f_dup"))
        else:
            run("INSERT INTO users(name,phone,pw) VALUES(?,?,?)", name, phone, generate_password_hash(pw))
            flash(tr("f_registered")); return redirect(url_for("login"))
    return render_template("auth.html", mode="register", need_code=bool(code))

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if throttled():
            flash(tr("f_toomany")); return render_template("auth.html", mode="login"), 429
        ident = request.form.get("phone", "").strip().lower()
        digits = "".join(c for c in ident if c.isdigit())
        u = q1("SELECT * FROM users WHERE phone=? OR phone=?", ident, digits)
        if u and check_password_hash(u["pw"], request.form.get("password", "")):
            lg, voter_id = session.get("lang"), session.get("voter_id")
            session.clear()
            session["uid"] = u["id"]; session.permanent = True
            if lg: session["lang"] = lg
            if voter_id: session["voter_id"] = voter_id
            db().execute(
                "DELETE FROM login_attempts WHERE ip=?",
                (request.remote_addr or "unknown",),
            )
            db().commit()
            return redirect(url_for("admin_dashboard") if u["is_admin"] else url_for("home"))
        flash(tr("f_bad_login"))
    return render_template("auth.html", mode="login")

@app.route("/logout", methods=["POST"])
def logout():
    lg, voter_id = session.get("lang"), session.get("voter_id")
    session.clear()
    if lg: session["lang"] = lg
    if voter_id: session["voter_id"] = voter_id
    return redirect(url_for("home"))

@app.route("/password", methods=["GET", "POST"])
@login_required
def change_password():
    if request.method == "POST":
        old, new = request.form.get("old", ""), request.form.get("new", "")
        if not check_password_hash(g.user["pw"], old): flash(tr("f_pw_wrong"))
        elif len(new) < 12: flash(tr("f_pw_weak"))
        else:
            run("UPDATE users SET pw=? WHERE id=?", generate_password_hash(new), g.user["id"])
            flash(tr("f_pw_ok")); return redirect(url_for("home"))
    return render_template("password.html")

# ---------- public pages ----------
@app.route("/")
def home():
    upcoming = q(
        "SELECT e.*,"
        "(SELECT filename FROM memories WHERE event_id=e.id AND media_type='image' ORDER BY id DESC LIMIT 1) cover "
        "FROM events e WHERE e.status='upcoming' AND e.date>=? ORDER BY e.date,e.id LIMIT 12",
        date.today().isoformat(),
    )
    updates = q("SELECT id,text,COALESCE(NULLIF(date,''),created) display_date FROM updates "
                "ORDER BY display_date DESC,id DESC LIMIT 3")
    memories = q("SELECT m.filename,m.caption,m.event_id,e.title FROM memories m "
                 "JOIN events e ON e.id=m.event_id WHERE m.media_type='image' ORDER BY m.id DESC LIMIT 4")
    open_contests = q(
        "SELECT c.*, (SELECT COUNT(*) FROM entries WHERE contest_id=c.id) entry_count "
        "FROM contests c WHERE c.status='open' AND (c.starts_at IS NULL OR c.starts_at<=?) ORDER BY c.id DESC LIMIT 1",
        now_iso(),
    )
    return render_template(
        "home.html", upcoming_events=upcoming, updates=updates, memories=memories,
        open_contests=open_contests,
        whatsapp_group_link=WHATSAPP_GROUP_LINK or whatsapp_contact_url() or url_for("contact"),
    )

def youtube_video_id(value):
    parsed = urllib.parse.urlparse(value or "")
    host = (parsed.hostname or "").lower()
    if host in ("youtube.com", "www.youtube.com", "m.youtube.com", "youtube-nocookie.com", "www.youtube-nocookie.com"):
        if parsed.path == "/watch":
            video_id = urllib.parse.parse_qs(parsed.query).get("v", [""])[0]
        else:
            parts = [part for part in parsed.path.split("/") if part]
            video_id = parts[1] if len(parts) == 2 and parts[0] in ("embed", "shorts", "live") else ""
    elif host in ("youtu.be", "www.youtu.be"):
        video_id = parsed.path.strip("/").split("/", 1)[0]
    else:
        return None
    if len(video_id) == 11 and all(char.isascii() and (char.isalnum() or char in "-_") for char in video_id):
        return video_id
    return None

@app.route("/gallery")
def gallery():
    kind = request.args.get("kind", "photo")
    if kind not in ("photo", "video"):
        kind = "photo"
    selected_event = request.args.get("event", "").strip()
    if selected_event:
        try:
            event_id = int(selected_event)
        except ValueError:
            abort(400)
        if not q1("SELECT id FROM events WHERE id=?", event_id):
            abort(404)
    else:
        event_id = None

    event_options = q("SELECT id,title FROM events ORDER BY date DESC,id DESC")
    sql = (
        "SELECT m.*,e.title event_title FROM media m "
        "JOIN events e ON e.id=m.event_id WHERE m.kind=?"
    )
    args = [kind]
    if event_id is not None:
        sql += " AND m.event_id=?"
        args.append(event_id)
    items = [dict(item) for item in q(sql + " ORDER BY m.id DESC", *args)]
    if kind == "video":
        for item in items:
            item["youtube_id"] = youtube_video_id(item["filename_or_youtube_url"])
            item["is_local_video"] = os.path.splitext(item["filename_or_youtube_url"])[1].lower() in (".mp4", ".webm")
            if not item["youtube_id"] and not item["is_local_video"]:
                app.logger.warning("Skipping invalid gallery video URL for media %s", item["id"])
    return render_template(
        "gallery.html", kind=kind, items=items, event_options=event_options,
        selected_event_id=event_id,
    )

@app.route("/goals")
@app.route("/about")
def about():
    return render_template("about.html", aim=AIM["te"])

@app.route("/contact")
def contact():
    phone = "".join(char for char in CONTACT_PHONE if char.isdigit() or char == "+")
    return render_template(
        "contact.html", contact_phone=phone, whatsapp_url=whatsapp_contact_url(),
        whatsapp_number=WHATSAPP_NUMBER,
        whatsapp_group_link=WHATSAPP_GROUP_LINK, upi_id=UPI_ID,
        village_address=VILLAGE_ADDRESS,
    )

@app.route("/more")
def more():
    return render_template("more.html")

@app.route("/events")
def events():
    occasion = request.args.get("occasion", "all")
    if occasion not in ("all", "sankranthi", "jan26", "aug15", "village-festival"):
        occasion = "all"
    rows = q(
        "SELECT e.*, (SELECT filename FROM memories WHERE event_id=e.id AND media_type='image' ORDER BY id LIMIT 1) cover "
        "FROM events e ORDER BY date DESC,id DESC"
    )
    if occasion != "all":
        def matches_event(row):
            details = " ".join(
                (row[key] or "") for key in ("occasion", "title", "category")
            ).casefold()
            if occasion == "sankranthi":
                return "sankranthi" in details or "sankranti" in details or "సంక్రాంతి" in details
            if occasion == "jan26":
                return "republic day" in details or "జనవరి 26" in details or row["date"][5:10] == "01-26"
            if occasion == "aug15":
                return "independence day" in details or "ఆగస్టు 15" in details or row["date"][5:10] == "08-15"
            return (
                "festival" in details or "పండుగ" in details
                or (row["category"] or "").casefold() == "culture"
            )
        rows = [row for row in rows if matches_event(row)]
    today = date.today().isoformat()
    upcoming = sorted((row for row in rows if row["date"] >= today), key=lambda row: row["date"])
    winners = {}
    for winner in q("SELECT event_id,name FROM winners ORDER BY id"):
        winners.setdefault(winner["event_id"], []).append(winner["name"])
    past_by_year = {}
    for row in sorted((row for row in rows if row["date"] < today), key=lambda row: row["date"], reverse=True):
        item = dict(row)
        item["winner"] = winners.get(row["id"], [])
        past_by_year.setdefault(row["date"][:4], []).append(item)
    past = [{"year": year, "events": items} for year, items in past_by_year.items()]
    return render_template("events.html", upcoming=upcoming, past=past, occasion=occasion)

@app.route("/events/<int:eid>")
def event(eid):
    ev = q1("SELECT * FROM events WHERE id=?", eid) or abort(404)
    games = q("SELECT * FROM games WHERE event_id=? ORDER BY time,id", eid)
    if not games:
        games = [
            dict(name=program["title"], time="", details=program["details"])
            for program in q("SELECT title,details FROM event_programs WHERE event_id=? ORDER BY position,id", eid)
        ]
    contests = q(
        "SELECT c.*,(SELECT COUNT(*) FROM votes v WHERE v.contest_id=c.id) vote_count "
        "FROM contests c WHERE c.event_id=? ORDER BY c.id",
        eid,
    )
    contests = [dict(contest, state=contest_state(contest)) for contest in contests]
    return render_template(
        "event.html", ev=ev, games=games, contests=contests,
        winners=q("SELECT * FROM winners WHERE event_id=? ORDER BY id", eid),
        media_items=q("SELECT * FROM media WHERE event_id=? ORDER BY id DESC", eid),
        mem=q("SELECT * FROM memories WHERE event_id=? ORDER BY id", eid),
        is_past=ev["status"] == "past" or ev["date"] < date.today().isoformat(),
    )

def calendar_response(rows, filename):
    def escape(value):
        return (str(value or "").replace("\\", "\\\\").replace("\r\n", "\n").replace("\r", "\n")
                .replace("\n", "\\n").replace(",", "\\,").replace(";", "\\;"))

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Village Organization//Events//EN", "CALSCALE:GREGORIAN"]
    for ev in rows:
        try:
            start = date.fromisoformat(ev["date"])
            end = start + timedelta(days=1)
        except (TypeError, ValueError, OverflowError):
            app.logger.warning("Skipping event %s with invalid calendar date: %r", ev["id"], ev["date"])
            continue
        lines.extend([
            "BEGIN:VEVENT",
            f"UID:village-event-{ev['id']}@village-organization",
            f"DTSTAMP:{stamp}",
            f"DTSTART;VALUE=DATE:{start:%Y%m%d}",
            f"DTEND;VALUE=DATE:{end:%Y%m%d}",
            f"SUMMARY:{escape(ev['title'])}",
            f"DESCRIPTION:{escape(ev['description'])}",
        ])
        if ev["activities"]:
            lines.append(f"COMMENT:{escape('Activities: ' + ev['activities'])}")
        if ev["venue"]:
            lines.append(f"LOCATION:{escape(ev['venue'])}")
        lines.extend([f"URL:{escape(abs_url('event', eid=ev['id']))}", "STATUS:CONFIRMED", "END:VEVENT"])
    lines.append("END:VCALENDAR")
    return Response("\r\n".join(lines) + "\r\n", content_type="text/calendar; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})

@app.route("/events/calendar.ics")
def events_calendar():
    rows = q("SELECT * FROM events WHERE date>=? ORDER BY date,id", date.today().isoformat())
    return calendar_response(rows, "village-events.ics")

@app.route("/events/<int:eid>/calendar.ics")
def event_calendar(eid):
    ev = q1("SELECT * FROM events WHERE id=?", eid) or abort(404)
    return calendar_response([ev], f"village-event-{eid}.ics")

@app.route("/events/propose", methods=["GET", "POST"])
@org_member_required
def event_propose():
    if request.method == "POST":
        title = request.form.get("title", "").strip()[:160]
        event_date = request.form.get("date", "").strip()
        try:
            date.fromisoformat(event_date)
        except ValueError:
            flash(tr("f_invalid_event_date"))
            activity_data = "\n".join(request.form.getlist("activities")) + "\n" + request.form.get("activities_custom", "")
            return render_template("event_proposal_form.html", activity_options=ACTIVITY_OPTIONS,
                                   **activity_form_values(activity_data))
        activities = submitted_activities()
        if not title or not activities:
            flash(tr("f_proposal_required"))
            activity_data = "\n".join(request.form.getlist("activities")) + "\n" + request.form.get("activities_custom", "")
            return render_template("event_proposal_form.html", activity_options=ACTIVITY_OPTIONS,
                                   **activity_form_values(activity_data))
        uploads, invalid = [], False
        for item in request.files.getlist("media"):
            if not item or not item.filename:
                continue
            saved = save_media(item)
            if saved is None:
                invalid = True
                break
            uploads.append(saved)
        if invalid:
            for name, _ in uploads: rm_file(name)
            flash(tr("f_bad_media"))
            activity_data = "\n".join(request.form.getlist("activities")) + "\n" + request.form.get("activities_custom", "")
            return render_template("event_proposal_form.html", activity_options=ACTIVITY_OPTIONS,
                                   **activity_form_values(activity_data))
        cur = db().execute(
            "INSERT INTO event_proposals(user_id,title,date,venue,description,category,activities,created) VALUES(?,?,?,?,?,?,?,?)",
            (g.user["id"], title, event_date, request.form.get("venue", "").strip()[:200],
             request.form.get("description", "").strip()[:4000], clean_cat(request.form.get("category")),
             activities, now_iso()))
        pid = cur.lastrowid
        try:
            db().executemany(
                "INSERT INTO proposal_media(proposal_id,filename,media_type) VALUES(?,?,?)",
                [(pid, name, media_type) for name, media_type in uploads])
            db().commit()
        except sqlite3.Error:
            db().rollback()
            for name, _ in uploads: rm_file(name)
            raise
        flash(tr("f_proposal_sent"))
        return redirect(url_for("my_proposals"))
    return render_template("event_proposal_form.html", activity_options=ACTIVITY_OPTIONS,
                           selected_activities=[], custom_activities="")

@app.route("/events/proposals")
@org_member_required
def my_proposals():
    rows = q("SELECT * FROM event_proposals WHERE user_id=? ORDER BY id DESC", g.user["id"])
    return render_template("my_proposals.html", rows=rows)

@app.route("/admin/proposals")
@admin_required
def admin_proposals():
    rows = q("SELECT p.*, u.name, u.phone FROM event_proposals p LEFT JOIN users u ON u.id=p.user_id ORDER BY p.id DESC")
    media = {}
    for row in rows:
        media[row["id"]] = q("SELECT * FROM proposal_media WHERE proposal_id=? ORDER BY id", row["id"])
    return render_template("admin_proposals.html", rows=rows, media=media)

@app.route("/admin/proposals/<int:pid>/approve", methods=["POST"])
@admin_required
def approve_proposal(pid):
    con = db()
    con.execute("BEGIN IMMEDIATE")
    proposal = con.execute("SELECT * FROM event_proposals WHERE id=?", (pid,)).fetchone()
    if not proposal:
        con.rollback(); abort(404)
    if proposal["status"] != "pending":
        con.rollback(); abort(409)
    files = con.execute("SELECT * FROM proposal_media WHERE proposal_id=? ORDER BY id", (pid,)).fetchall()
    cur = con.execute(
        "INSERT INTO events(title,date,venue,description,category,activities,occasion,place,status) "
        "VALUES(?,?,?,?,?,?,?,?,?)",
        (proposal["title"], proposal["date"], proposal["venue"], proposal["description"],
         clean_cat(proposal["category"]), proposal["activities"], clean_cat(proposal["category"]),
         proposal["venue"] or "", "upcoming" if proposal["date"] >= date.today().isoformat() else "past"),
    )
    eid = cur.lastrowid
    con.executemany("INSERT INTO memories(event_id,filename,caption,media_type) VALUES(?,?,?,?)",
                    [(eid, item["filename"], "", item["media_type"]) for item in files])
    con.execute("DELETE FROM proposal_media WHERE proposal_id=?", (pid,))
    con.execute("UPDATE event_proposals SET status='approved',event_id=? WHERE id=?", (eid, pid))
    con.commit()
    flash("Proposal approved and event published.")
    return redirect(url_for("admin_proposals"))

@app.route("/admin/proposals/<int:pid>/reject", methods=["POST"])
@admin_required
def reject_proposal(pid):
    con = db()
    con.execute("BEGIN IMMEDIATE")
    proposal = con.execute("SELECT id,status FROM event_proposals WHERE id=?", (pid,)).fetchone()
    if not proposal:
        con.rollback(); abort(404)
    if proposal["status"] != "pending":
        con.rollback(); abort(409)
    files = con.execute("SELECT filename FROM proposal_media WHERE proposal_id=?", (pid,)).fetchall()
    con.execute("DELETE FROM proposal_media WHERE proposal_id=?", (pid,))
    con.execute("UPDATE event_proposals SET status='rejected' WHERE id=?", (pid,))
    con.commit()
    for item in files: rm_file(item["filename"])
    flash("Proposal rejected.")
    return redirect(url_for("admin_proposals"))

@app.route("/admin/proposals/<int:pid>/delete", methods=["POST"])
@admin_required
def delete_proposal(pid):
    con = db()
    con.execute("BEGIN IMMEDIATE")
    proposal = con.execute("SELECT id FROM event_proposals WHERE id=?", (pid,)).fetchone()
    if not proposal:
        con.rollback(); abort(404)
    files = con.execute("SELECT filename FROM proposal_media WHERE proposal_id=?", (pid,)).fetchall()
    con.execute("DELETE FROM event_proposals WHERE id=?", (pid,))
    con.commit()
    for item in files: rm_file(item["filename"])
    return redirect(url_for("admin_proposals"))

@app.route("/updates")
def updates():
    cat = request.args.get("cat"); cat = cat if cat in CATS else None
    rows = q("SELECT * FROM updates " + ("WHERE category=? " if cat else "") + "ORDER BY pinned DESC, id DESC LIMIT 100", *([cat] if cat else []))
    return render_template("updates.html", rows=rows, cat=cat)

@app.route("/api/updates")
def api_updates():
    rows = q("SELECT * FROM updates ORDER BY pinned DESC, id DESC LIMIT 5")
    return jsonify([dict(id=r["id"], text=r["text"], t=r["created"], pinned=bool(r["pinned"]),
                         cat=tr("cat_" + (r["category"] or "general")), key=r["category"] or "general",
                         photo=url_for("uploaded", name=r["photo"]) if r["photo"] else None,
                         share=whatsapp_share_url(r["text"][:180], "updates")) for r in rows])

def winner_titles(cid):
    ent = q("SELECT e.title, (SELECT COUNT(*) FROM votes v WHERE v.entry_id=e.id) n "
            "FROM entries e WHERE contest_id=?", cid)
    top = max([e["n"] for e in ent], default=0)
    return [e["title"] or "—" for e in ent if top and e["n"] == top]

@app.route("/contests")
def contests():
    rows = q("SELECT c.*, (SELECT COUNT(*) FROM entries WHERE contest_id=c.id) n FROM contests c ORDER BY id DESC")
    rows = [{**dict(row), "state": contest_state(row)} for row in rows]
    return render_template("contests.html", rows=rows)

@app.route("/contests/<int:cid>")
def contest(cid):
    c = q1("SELECT * FROM contests WHERE id=?", cid) or abort(404)
    ent = q("SELECT e.*, (SELECT COUNT(*) FROM votes v WHERE v.entry_id=e.id) votes "
            "FROM entries e WHERE contest_id=? ORDER BY number,id", cid)
    device_id = request.cookies.get("rid") or secrets.token_urlsafe(32)
    vote_row = q1("SELECT entry_id FROM votes WHERE contest_id=? AND device_id=?", cid, device_id)
    if not vote_row and g.user:
        vote_row = q1(
            "SELECT v.entry_id FROM vote_accounts a JOIN votes v "
            "ON v.contest_id=a.contest_id AND v.device_id=a.device_id "
            "WHERE a.contest_id=? AND a.user_id=?",
            cid, g.user["id"],
        )
    mine = vote_row["entry_id"] if vote_row else None
    mine_number = q1("SELECT number FROM entries WHERE id=?", mine)["number"] if mine else None
    show_results = bool(c["results_published"] or (g.user and g.user["is_admin"]))
    state = contest_state(c)
    response = make_response(render_template(
        "contest.html", c=c, state=state, voting_open=state == "open", mine_number=mine_number,
        entries=ent, mine=mine, show=show_results, show_results=show_results,
        top=max([e["votes"] for e in ent], default=0), total=sum(e["votes"] for e in ent),
        already_voted=mine is not None, vote_durations=VOTE_DURATIONS,
        require_phone_for_vote=REQUIRE_PHONE_FOR_VOTE,
        starts_at_input=vote_datetime_input(c["starts_at"]),
        closes_at_input=vote_datetime_input(c["closes_at"]),
        starts_at_display=vote_datetime_display(c["starts_at"]),
        closes_at_display=vote_datetime_display(c["closes_at"]),
    ))
    response.set_cookie(
        "rid", device_id, max_age=60 * 60 * 24 * 365, httponly=True,
        samesite="Lax", secure=HTTPS,
    )
    return response

@app.route("/contest/<int:cid>")
def contest_short(cid):
    return contest(cid)

@app.route("/contest/<int:cid>/results")
def contest_results(cid):
    c = q1("SELECT * FROM contests WHERE id=?", cid) or abort(404)
    if not c["results_published"] and c["status"] != "published" and not (g.user and g.user["is_admin"]):
        abort(403)
    entries = q(
        "SELECT e.*,(SELECT COUNT(*) FROM votes v WHERE v.entry_id=e.id) votes "
        "FROM entries e WHERE contest_id=? ORDER BY number,id",
        cid,
    )
    total = sum(entry["votes"] for entry in entries)
    top = max((entry["votes"] for entry in entries), default=0)
    winners = [entry for entry in entries if top and entry["votes"] == top]
    return render_template(
        "contest_results.html", c=c, entries=entries, total=total,
        top=top, winners=winners, admin=bool(g.user and g.user["is_admin"]),
    )

@app.route("/contest/<int:cid>/results/save-winners", methods=["POST"])
@admin_required
def save_contest_winners(cid):
    c = q1("SELECT * FROM contests WHERE id=?", cid) or abort(404)
    if c["status"] == "open":
        flash("ముగిసిన ఓటింగ్ ఫలితాలనే విజేతలుగా భద్రపరచండి.")
        return redirect(url_for("contest_results", cid=cid))
    if not c["event_id"]:
        flash("విజేతలను భద్రపరచడానికి ముందుగా ఈ పోటీని ఒక ఈవెంట్‌కు జత చేయండి.")
        return redirect(url_for("contest_results", cid=cid))
    entries = q(
        "SELECT e.title,e.number,e.filename,(SELECT COUNT(*) FROM votes v WHERE v.entry_id=e.id) votes "
        "FROM entries e WHERE contest_id=?",
        cid,
    )
    top = max((entry["votes"] for entry in entries), default=0)
    winners = [entry for entry in entries if top and entry["votes"] == top]
    if not winners:
        flash("ఇంకా ఓట్లు లేవు.")
        return redirect(url_for("contest_results", cid=cid))
    con = db()
    con.execute("BEGIN IMMEDIATE")
    for winner in winners:
        name = winner["title"] or f"#{winner['number']}"
        exists = con.execute(
            "SELECT 1 FROM winners WHERE event_id=? AND category=? AND name=? AND photo=?",
            (c["event_id"], c["title"], name, winner["filename"]),
        ).fetchone()
        if not exists:
            con.execute(
                "INSERT INTO winners(event_id,category,name,photo) VALUES(?,?,?,?)",
                (c["event_id"], c["title"], name, winner["filename"]),
            )
    con.commit()
    flash("విజేతలు ఈవెంట్‌లో భద్రపరచబడ్డారు.")
    return redirect(url_for("contest_results", cid=cid))

@app.route("/events/<int:eid>/winners", methods=["POST"])
@admin_required
def add_winner(eid):
    q1("SELECT id FROM events WHERE id=?", eid) or abort(404)
    name = request.form.get("name", "").strip()[:160]
    if not name:
        flash("Winner name is required.")
        return redirect(url_for("event", eid=eid))
    uploaded = request.files.get("photo")
    photo = save_image(uploaded) if uploaded and uploaded.filename else None
    if uploaded and uploaded.filename and not photo:
        flash(tr("f_bad_img"))
        return redirect(url_for("event", eid=eid))
    run(
        "INSERT INTO winners(event_id,category,name,photo) VALUES(?,?,?,?)",
        eid, request.form.get("category", "").strip()[:120], name, photo,
    )
    flash("Winner added.")
    return redirect(url_for("event", eid=eid))

@app.route("/events/<int:eid>/winners/<int:wid>/edit", methods=["POST"])
@admin_required
def edit_winner(eid, wid):
    winner = q1("SELECT * FROM winners WHERE id=? AND event_id=?", wid, eid) or abort(404)
    name = request.form.get("name", "").strip()[:160]
    if not name:
        flash("Winner name is required.")
        return redirect(url_for("event", eid=eid))
    uploaded = request.files.get("photo")
    new_photo = save_image(uploaded) if uploaded and uploaded.filename else None
    if uploaded and uploaded.filename and not new_photo:
        flash(tr("f_bad_img"))
        return redirect(url_for("event", eid=eid))
    remove_photo = bool(request.form.get("remove_photo"))
    photo = None if remove_photo else (new_photo or winner["photo"])
    run(
        "UPDATE winners SET category=?,name=?,photo=? WHERE id=? AND event_id=?",
        request.form.get("category", "").strip()[:120], name, photo, wid, eid,
    )
    if winner["photo"] and winner["photo"] != photo:
        remove_if_unreferenced(winner["photo"])
    if new_photo and remove_photo:
        remove_if_unreferenced(new_photo)
    flash("Winner saved.")
    return redirect(url_for("event", eid=eid))

@app.route("/events/<int:eid>/winners/<int:wid>/delete", methods=["POST"])
@admin_required
def delete_winner(eid, wid):
    winner = q1("SELECT photo FROM winners WHERE id=? AND event_id=?", wid, eid) or abort(404)
    run("DELETE FROM winners WHERE id=? AND event_id=?", wid, eid)
    remove_if_unreferenced(winner["photo"])
    return redirect(url_for("event", eid=eid))

def _record_vote(cid, eid):
    con = db()
    con.execute("BEGIN IMMEDIATE")
    contest_row = con.execute("SELECT status,starts_at,closes_at FROM contests WHERE id=?", (cid,)).fetchone()
    if not contest_row:
        con.rollback(); abort(404)
    if (contest_row["status"] != "open"
            or (contest_row["starts_at"] and contest_row["starts_at"] > now_iso())
            or (contest_row["closes_at"] and contest_row["closes_at"] <= now_iso())):
        if (contest_row["status"] == "open" and contest_row["closes_at"]
                and contest_row["closes_at"] <= now_iso()):
            con.execute("UPDATE contests SET status='closed' WHERE id=?", (cid,))
        con.commit()
        flash(tr("f_vote_not_started") if contest_row["starts_at"] and contest_row["starts_at"] > now_iso()
              else tr("f_vote_closed"))
        return redirect(url_for("contest_short", cid=cid))
    if not con.execute("SELECT 1 FROM entries WHERE id=? AND contest_id=?", (eid, cid)).fetchone():
        con.rollback(); abort(404)
    device_id = request.cookies.get("rid")
    if not device_id:
        con.rollback()
        return redirect(url_for("contest_short", cid=cid))
    phone_hash = None
    if REQUIRE_PHONE_FOR_VOTE:
        phone = "".join(
            char for char in request.form.get("phone", "") if char.isascii() and char.isdigit()
        )
        if phone.startswith("0091") and len(phone) == 14:
            phone = phone[4:]
        elif phone.startswith("91") and len(phone) == 12:
            phone = phone[2:]
        if not 8 <= len(phone) <= 15:
            con.rollback()
            flash("ఓటు వేయడానికి సరైన ఫోన్ నంబర్ ఇవ్వండి.")
            return redirect(url_for("contest_short", cid=cid))
        phone_hash = hmac.new(
            app.secret_key.encode("utf-8"), phone.encode("ascii"), hashlib.sha256
        ).hexdigest()
    existing = con.execute(
        "SELECT 1 FROM votes WHERE contest_id=? AND device_id=?", (cid, device_id)
    ).fetchone()
    if not existing and phone_hash:
        existing = con.execute(
            "SELECT 1 FROM vote_phone_hashes WHERE contest_id=? AND phone_hash=?",
            (cid, phone_hash),
        ).fetchone()
    if not existing and g.user:
        existing = con.execute(
            "SELECT 1 FROM vote_accounts WHERE contest_id=? AND user_id=?", (cid, g.user["id"])
        ).fetchone()
    if existing:
        con.rollback()
        flash(tr("f_vote_already"))
    else:
        inserted = con.execute(
            "INSERT OR IGNORE INTO votes(contest_id,device_id,entry_id,ts) VALUES(?,?,?,?)",
            (cid, device_id, eid, time.time()),
        ).rowcount
        if inserted and g.user:
            con.execute(
                "INSERT INTO vote_accounts(contest_id,user_id,device_id) VALUES(?,?,?)",
                (cid, g.user["id"], device_id),
            )
        if inserted and phone_hash:
            con.execute(
                "INSERT INTO vote_phone_hashes(contest_id,phone_hash,device_id) VALUES(?,?,?)",
                (cid, phone_hash, device_id),
            )
        con.commit()
        flash(tr("f_vote_saved") if inserted else tr("f_vote_already"))
    return redirect(url_for("contest_short", cid=cid))

@app.route("/contest/<int:cid>/vote", methods=["POST"])
def vote_selected(cid):
    try:
        eid = int(request.form.get("entry_id", ""))
    except ValueError:
        abort(400)
    return _record_vote(cid, eid)

@app.route("/contests/<int:cid>/vote/<int:eid>", methods=["POST"])
def vote(cid, eid):
    return _record_vote(cid, eid)

@app.route("/archive")
def archive():
    d = {}
    Y = lambda y: d.setdefault(y, dict(events=0, photos=0, trees=0, winners=[]))
    for r in q("SELECT substr(date,1,4) y, COUNT(*) n FROM events GROUP BY y"): Y(r["y"])["events"] = r["n"]
    for r in q("SELECT substr(e.date,1,4) y, COUNT(*) n FROM memories m JOIN events e ON e.id=m.event_id WHERE m.media_type='image' GROUP BY y"): Y(r["y"])["photos"] = r["n"]
    for r in q("SELECT substr(date,1,4) y, SUM(qty) n FROM trees GROUP BY y"): Y(r["y"])["trees"] = r["n"]
    visibility = "AND results_published=1" if not (g.user and g.user["is_admin"]) else ""
    for c in q("SELECT * FROM contests WHERE status='closed' " + visibility + " ORDER BY id"):
        winners = winner_titles(c["id"])
        if winners:
            Y((c["created"] or "0000")[:4])["winners"].append((c["id"], c["title"], winners))
    return render_template("archive.html", data=sorted(d.items(), reverse=True))

@app.route("/trees")
def trees():
    return render_template("trees.html", rows=q("SELECT * FROM trees ORDER BY date DESC"), total=q1("SELECT COALESCE(SUM(qty),0) n FROM trees")["n"])

# ---------- member forms ----------
@app.route("/student", methods=["GET", "POST"])
@org_member_required
def student():
    if request.method == "POST":
        f = request.form; st, cls = f.get("student", "").strip(), f.get("cls", "").strip()
        if not st or not cls: flash(tr("f_required"))
        else:
            run("INSERT INTO apps(user_id,student,cls,school,phone,program,note,created) VALUES(?,?,?,?,?,?,?,?)", g.user["id"], st, cls,
                f.get("school", "").strip(), f.get("phone", "").strip(), clean_cat(f.get("program"), PROGRAMS, "other"), f.get("note", "").strip()[:500], now_iso())
            flash(tr("f_applied")); return redirect(url_for("more"))
    return render_template("student.html", PROGRAMS=PROGRAMS)

@app.route("/report", methods=["GET", "POST"])
@org_member_required
def report():
    if request.method == "POST":
        text = request.form.get("text", "").strip()[:1000]
        if not text: flash(tr("f_required"))
        else:
            upload = request.files.get("photo")
            photo = save_image(upload) if upload and upload.filename else None
            if upload and upload.filename and not photo:
                flash(tr("f_bad_img"))
                return render_template("report.html", REPORT_CATS=REPORT_CATS)
            run("INSERT INTO reports(user_id,category,text,photo,created) VALUES(?,?,?,?,?)", g.user["id"],
                clean_cat(request.form.get("category"), REPORT_CATS, "other"), text, photo, now_iso())
            flash(tr("f_reported")); return redirect(url_for("more"))
    return render_template("report.html", REPORT_CATS=REPORT_CATS)

# ---------- admin: events, updates, contests ----------
@app.route("/events/new", methods=["GET", "POST"])
@app.route("/events/<int:eid>/edit", methods=["GET", "POST"])
@admin_required
def event_form(eid=None):
    ev = q1("SELECT * FROM events WHERE id=?", eid) if eid else None
    if eid and not ev: abort(404)
    if request.method == "POST":
        f = request.form
        vals = (f.get("title", "").strip(), f.get("date", ""), f.get("venue", "").strip(), f.get("description", "").strip(), clean_cat(f.get("category")))
        form_activities = activity_form_values("\n".join(f.getlist("activities")) + "\n" + f.get("activities_custom", ""))
        form_programs = submitted_program_form_values()
        template_values = dict(ev=ev, activity_options=ACTIVITY_OPTIONS, **form_activities, **form_programs)
        try:
            date.fromisoformat(vals[1])
        except ValueError:
            flash("Enter a valid event date.")
            return render_template("event_form.html", **template_values)
        if not vals[0]:
            flash("Title and date are required.")
            return render_template("event_form.html", **template_values)
        existing_programs = {
            row["id"]: row for row in (
                q("SELECT id FROM event_programs WHERE event_id=?", eid) if ev else []
            )
        }
        try:
            programs = submitted_programs()
        except ValueError as error:
            flash(str(error))
            return render_template("event_form.html", **template_values)
        retained_ids = [program["id"] for program in programs if program.get("id") is not None]
        if len(retained_ids) != len(set(retained_ids)) or any(program_id not in existing_programs for program_id in retained_ids):
            abort(400)
        activities = submitted_activities()
        qr_file = request.files.get("donation_qr")
        new_qr = None
        if qr_file and qr_file.filename:
            new_qr = save_donation_qr(qr_file)
            if not new_qr:
                flash(tr("f_bad_donation_qr"))
                return render_template("event_form.html", **template_values)
        donation_qr = new_qr or (ev["donation_qr"] if ev else None)
        if f.get("remove_donation_qr"):
            donation_qr = None
            rm_file(new_qr)
        old_qr = ev["donation_qr"] if ev else None
        donation_message = f.get("donation_message", "").strip()[:500]
        occasion = vals[4]
        event_status = "upcoming" if vals[1] >= date.today().isoformat() else "past"
        if ev:
            db().execute("UPDATE events SET title=?,date=?,venue=?,description=?,category=?,activities=?,donation_qr=?,donation_message=?,occasion=?,place=?,status=? WHERE id=?",
                         (*vals, activities, donation_qr, donation_message, occasion, vals[2], event_status, eid))
        else:
            eid = db().execute("INSERT INTO events(title,date,venue,description,category,activities,donation_qr,donation_message,occasion,place,status) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                               (*vals, activities, donation_qr, donation_message, occasion, vals[2], event_status)).lastrowid
        removed_program_ids = set(existing_programs) - set(retained_ids)
        removed_photos = []
        for program_id in removed_program_ids:
            removed_photos.extend(
                row["photo"] for row in q("SELECT photo FROM event_program_people WHERE program_id=? AND photo IS NOT NULL", program_id)
            )
            db().execute("DELETE FROM event_programs WHERE id=? AND event_id=?", (program_id, eid))
        for position, program in enumerate(programs):
            if program.get("id") is not None:
                db().execute(
                    "UPDATE event_programs SET kind=?,title=?,details=?,result=?,position=?,time=? WHERE id=? AND event_id=?",
                    (program["kind"], program["title"], program["details"], program["result"],
                     position, program["time"], program["id"], eid),
                )
            else:
                db().execute(
                    "INSERT INTO event_programs(event_id,kind,title,details,result,position,time) VALUES(?,?,?,?,?,?,?)",
                    (eid, program["kind"], program["title"], program["details"],
                     program["result"], position, program["time"]),
                )
        db().execute("DELETE FROM games WHERE event_id=?", (eid,))
        db().execute(
            "INSERT INTO games(event_id,name,time,details) "
            "SELECT event_id,title,time,details FROM event_programs "
            "WHERE event_id=? AND kind='game' ORDER BY position,id",
            (eid,),
        )
        db().commit()
        for photo in removed_photos: remove_if_unreferenced(photo)
        if old_qr and old_qr != donation_qr: remove_if_unreferenced(old_qr)
        return redirect(url_for("event", eid=eid))
    values = activity_form_values(ev["activities"] if ev else "")
    programs = q("SELECT id,kind,title,details,result,time FROM event_programs WHERE event_id=? ORDER BY position,id", eid) if ev else []
    if ev and not programs:
        programs = [
            dict(id="", kind="game", title=game["name"], details=game["details"],
                 result="", time=game["time"])
            for game in q("SELECT name,time,details FROM games WHERE event_id=? ORDER BY time,id", eid)
        ]
    return render_template("event_form.html", ev=ev, activity_options=ACTIVITY_OPTIONS,
                           **values, **event_program_form_values(programs))

@app.route("/events/<int:eid>/memories", methods=["POST"])
@admin_required
def add_memories(eid):
    q1("SELECT id FROM events WHERE id=?", eid) or abort(404)
    files, invalid = [], False
    items = request.files.getlist("media") + request.files.getlist("photos")
    for f in items:
        if not f or not f.filename: continue
        saved = save_media(f)
        if saved:
            name, media_type = saved
            files.append((name, media_type))
        else:
            invalid = True
    if files:
        caption = request.form.get("caption", "").strip()[:300]
        connection = db()
        try:
            with connection:
                connection.executemany(
                    "INSERT INTO memories(event_id,filename,caption,media_type) VALUES(?,?,?,?)",
                    [(eid, name, caption, media_type) for name, media_type in files],
                )
                connection.executemany(
                    "INSERT INTO media(event_id,kind,filename_or_youtube_url,caption) VALUES(?,?,?,?)",
                    [(eid, "video" if media_type == "video" else "photo", name, caption)
                     for name, media_type in files],
                )
        except Exception:
            for name, _media_type in files:
                rm_file(name)
            raise
    n = len(files)
    if n: flash(f"{n} photo(s)/video(s) added.")
    if invalid or not n: flash(tr("f_bad_media"))
    return redirect(url_for("event", eid=eid))

@app.route("/events/<int:eid>/youtube", methods=["POST"])
@admin_required
def add_youtube_media(eid):
    q1("SELECT id FROM events WHERE id=?", eid) or abort(404)
    video_id = youtube_video_id(request.form.get("youtube_url", ""))
    if not video_id:
        flash("Enter a valid YouTube video link.")
        return redirect(url_for("event", eid=eid))
    caption = request.form.get("caption", "").strip()[:300]
    run(
        "INSERT INTO media(event_id,kind,filename_or_youtube_url,caption) VALUES(?,?,?,?)",
        eid, "video", f"https://www.youtube-nocookie.com/embed/{video_id}", caption,
    )
    flash("YouTube video added.")
    return redirect(url_for("event", eid=eid))

@app.route("/media/<int:mid>/edit", methods=["POST"])
@admin_required
def edit_media(mid):
    item = q1("SELECT * FROM media WHERE id=?", mid) or abort(404)
    caption = request.form.get("caption", "").strip()[:300]
    run("UPDATE media SET caption=? WHERE id=?", caption, mid)
    if item["kind"] in ("photo", "video") and os.path.basename(item["filename_or_youtube_url"]) == item["filename_or_youtube_url"]:
        run(
            "UPDATE memories SET caption=? WHERE event_id=? AND filename=?",
            caption, item["event_id"], item["filename_or_youtube_url"],
        )
    return redirect(url_for("event", eid=item["event_id"]))

@app.route("/media/<int:mid>/delete", methods=["POST"])
@admin_required
def delete_media(mid):
    item = q1("SELECT * FROM media WHERE id=?", mid) or abort(404)
    filename = item["filename_or_youtube_url"]
    is_local = (
        os.path.basename(filename) == filename
        and os.path.splitext(filename)[1].lower() in (".jpg", ".jpeg", ".png", ".webp", ".gif", ".mp4", ".webm")
    )
    connection = db()
    with connection:
        connection.execute("DELETE FROM media WHERE id=?", (mid,))
        if is_local and not connection.execute(
            "SELECT 1 FROM media WHERE event_id=? AND filename_or_youtube_url=?",
            (item["event_id"], filename),
        ).fetchone():
            connection.execute(
                "DELETE FROM memories WHERE event_id=? AND filename=?",
                (item["event_id"], filename),
            )
    if is_local:
        remove_if_unreferenced(filename)
    return redirect(url_for("event", eid=item["event_id"]))

@app.route("/events/<int:eid>/programs/<int:pid>/people", methods=["POST"])
@admin_required
def add_program_person(eid, pid):
    q1("SELECT id FROM events WHERE id=?", eid) or abort(404)
    q1("SELECT id FROM event_programs WHERE id=? AND event_id=?", pid, eid) or abort(404)
    name = request.form.get("name", "").strip()[:120]
    if not name:
        flash(tr("program_person_name_required"))
        return redirect(url_for("event", eid=eid))
    uploaded = request.files.get("photo")
    if not uploaded or not uploaded.filename:
        flash(tr("program_person_photo_required"))
        return redirect(url_for("event", eid=eid))
    photo = save_program_person_image(uploaded)
    if not photo:
        flash(tr("program_person_bad_photo"))
        return redirect(url_for("event", eid=eid))
    run("INSERT INTO event_program_people(program_id,name,photo,created) VALUES(?,?,?,?)", pid, name, photo, now_iso())
    return redirect(url_for("event", eid=eid))

@app.route("/events/<int:eid>/program-people/<int:person_id>/edit", methods=["POST"])
@admin_required
def edit_program_person(eid, person_id):
    person = q1(
        "SELECT pp.photo FROM event_program_people pp JOIN event_programs p ON p.id=pp.program_id "
        "WHERE pp.id=? AND p.event_id=?",
        person_id, eid,
    ) or abort(404)
    name = request.form.get("name", "").strip()[:120]
    if not name:
        flash(tr("program_person_name_required"))
        return redirect(url_for("event", eid=eid))
    uploaded = request.files.get("photo")
    new_photo = None
    if uploaded and uploaded.filename:
        new_photo = save_program_person_image(uploaded)
        if not new_photo:
            flash(tr("program_person_bad_photo"))
            return redirect(url_for("event", eid=eid))
    run("UPDATE event_program_people SET name=?,photo=COALESCE(?,photo) WHERE id=?", name, new_photo, person_id)
    if new_photo and person["photo"]: remove_if_unreferenced(person["photo"])
    return redirect(url_for("event", eid=eid))

@app.route("/events/<int:eid>/program-people/<int:person_id>/delete", methods=["POST"])
@admin_required
def del_program_person(eid, person_id):
    person = q1(
        "SELECT pp.photo FROM event_program_people pp JOIN event_programs p ON p.id=pp.program_id "
        "WHERE pp.id=? AND p.event_id=?",
        person_id, eid,
    ) or abort(404)
    run("DELETE FROM event_program_people WHERE id=?", person_id)
    remove_if_unreferenced(person["photo"])
    return redirect(url_for("event", eid=eid))

@app.route("/memories/<int:mid>/delete", methods=["POST"])
@admin_required
def del_memory(mid):
    m = q1("SELECT * FROM memories WHERE id=?", mid) or abort(404)
    run("DELETE FROM memories WHERE id=?", mid)
    run("DELETE FROM media WHERE event_id=? AND filename_or_youtube_url=?", m["event_id"], m["filename"])
    remove_if_unreferenced(m["filename"])
    return redirect(url_for("event", eid=m["event_id"]))

@app.route("/events/<int:eid>/delete", methods=["POST"])
@admin_required
def del_event(eid):
    ev = q1("SELECT donation_qr FROM events WHERE id=?", eid) or abort(404)
    files = {row["filename"] for row in q("SELECT filename FROM memories WHERE event_id=?", eid)}
    files.update(
        row["filename_or_youtube_url"] for row in q(
            "SELECT filename_or_youtube_url FROM media WHERE event_id=?", eid
        ) if os.path.basename(row["filename_or_youtube_url"]) == row["filename_or_youtube_url"]
    )
    files.update(
        row["photo"] for row in q(
            "SELECT pp.photo FROM event_program_people pp JOIN event_programs p ON p.id=pp.program_id "
            "WHERE p.event_id=? AND pp.photo IS NOT NULL", eid
        )
    )
    files.update(row["photo"] for row in q("SELECT photo FROM winners WHERE event_id=? AND photo IS NOT NULL", eid))
    files.add(ev["donation_qr"])
    run("DELETE FROM events WHERE id=?", eid)
    for filename in files:
        remove_if_unreferenced(filename)
    return redirect(url_for("events"))

@app.route("/updates/new", methods=["POST"])
@admin_required
def post_update():
    text = request.form.get("text", "").strip()[:800]
    if text:
        upload = request.files.get("photo")
        photo = save_image(upload) if upload and upload.filename else None
        if upload and upload.filename and not photo:
            flash(tr("f_bad_img"))
            return redirect(request.form.get("back") if request.form.get("back", "").startswith("/") and not request.form.get("back", "").startswith("//") else url_for("updates"))
        run("INSERT INTO updates(text,created,category,pinned,photo) VALUES(?,?,?,?,?)", text, now_iso(), clean_cat(request.form.get("category")),
            1 if request.form.get("pinned") else 0, photo)
    else:
        flash(tr("f_required"))
    back = request.form.get("back", "")
    return redirect(back if back.startswith("/") and not back.startswith("//") else url_for("updates"))

@app.route("/updates/<int:uid>/edit", methods=["POST"])
@admin_required
def edit_update(uid):
    update = q1("SELECT * FROM updates WHERE id=?", uid) or abort(404)
    back = request.form.get("back", "")
    redirect_target = back if back.startswith("/") and not back.startswith("//") else url_for("updates")
    text = request.form.get("text", "").strip()[:800]
    if not text:
        flash(tr("f_required"))
        return redirect(redirect_target)
    upload = request.files.get("photo")
    new_photo = save_image(upload) if upload and upload.filename else None
    if upload and upload.filename and not new_photo:
        flash(tr("f_bad_img"))
        return redirect(redirect_target)
    remove_photo = bool(request.form.get("remove_photo"))
    photo = None if remove_photo else (new_photo or update["photo"])
    run(
        "UPDATE updates SET text=?,category=?,photo=? WHERE id=?",
        text, clean_cat(request.form.get("category")), photo, uid,
    )
    if update["photo"] and update["photo"] != photo:
        remove_if_unreferenced(update["photo"])
    if new_photo and remove_photo:
        remove_if_unreferenced(new_photo)
    flash("Update saved.")
    return redirect(redirect_target)

@app.route("/updates/<int:uid>/pin", methods=["POST"])
@admin_required
def pin_update(uid):
    q1("SELECT id FROM updates WHERE id=?", uid) or abort(404)
    run("UPDATE updates SET pinned=1-COALESCE(pinned,0) WHERE id=?", uid); return redirect(url_for("updates"))

@app.route("/updates/<int:uid>/delete", methods=["POST"])
@admin_required
def del_update(uid):
    u = q1("SELECT photo FROM updates WHERE id=?", uid) or abort(404)
    run("DELETE FROM updates WHERE id=?", uid)
    remove_if_unreferenced(u["photo"])
    return redirect(url_for("updates"))

@app.route("/contests/new", methods=["GET", "POST"])
@admin_required
def contest_new():
    if request.method == "POST":
        title = request.form.get("title", "").strip()[:200]
        try:
            hours = int(request.form.get("duration_hours", "24"))
        except ValueError:
            hours = 0
        try:
            event_id = parse_event_id(request.form.get("event_id"))
        except ValueError as error:
            event_id = None
            flash(str(error))
        if not title:
            flash("Title is required.")
        elif hours not in VOTE_DURATIONS:
            flash(tr("f_vote_duration"))
        elif request.form.get("event_id", "").strip() and event_id is None:
            pass
        else:
            starts_at = parse_vote_datetime(request.form.get("starts_at", ""))
            if request.form.get("starts_at") and not starts_at:
                flash(tr("f_vote_datetime"))
            else:
                deadline_base = datetime.strptime(starts_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc) if starts_at else datetime.now(timezone.utc)
                closes_at = (deadline_base + timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
                cid = run("INSERT INTO contests(title,description,event_id,created,starts_at,closes_at,start,end) VALUES(?,?,?,?,?,?,?,?)",
                          title, request.form.get("description", "").strip()[:2000], event_id,
                          date.today().isoformat(), starts_at, closes_at, starts_at, closes_at)
                return redirect(url_for("contest", cid=cid))
    return render_template("contest_form.html", evs=q("SELECT id,title,date FROM events ORDER BY date DESC"),
                           vote_durations=VOTE_DURATIONS)

@app.route("/contests/<int:cid>/edit", methods=["GET", "POST"])
@admin_required
def contest_edit(cid):
    contest_row = q1("SELECT * FROM contests WHERE id=?", cid) or abort(404)
    if request.method == "POST":
        title = request.form.get("title", "").strip()[:200]
        description = request.form.get("description", "").strip()[:2000]
        try:
            event_id = parse_event_id(request.form.get("event_id"))
        except ValueError as error:
            event_id = None
            flash(str(error))
        if not title:
            flash("Title is required.")
        elif request.form.get("event_id", "").strip() and event_id is None:
            pass
        else:
            run("UPDATE contests SET title=?,description=?,event_id=? WHERE id=?",
                title, description, event_id, cid)
            flash("Contest saved.")
            return redirect(url_for("contest_short", cid=cid))
    return render_template(
        "contest_form.html", c=contest_row,
        evs=q("SELECT id,title,date FROM events ORDER BY date DESC"),
        vote_durations=VOTE_DURATIONS,
    )

@app.route("/contests/<int:cid>/entries", methods=["POST"])
@admin_required
def add_entry(cid):
    q1("SELECT id FROM contests WHERE id=?", cid) or abort(404)
    title = request.form.get("title", "").strip()[:200]
    saved = []
    invalid = False
    files = request.files.getlist("photos") + request.files.getlist("photo")
    for uploaded in files:
        if not uploaded or not uploaded.filename:
            continue
        filename = save_image(uploaded)
        if filename:
            saved.append(filename)
        else:
            invalid = True
    if saved:
        connection = db()
        try:
            connection.execute("BEGIN IMMEDIATE")
            number = connection.execute(
                "SELECT COALESCE(MAX(number),0)+1 FROM entries WHERE contest_id=?",
                (cid,),
            ).fetchone()[0]
            connection.executemany(
                "INSERT INTO entries(contest_id,filename,title,number,caption) VALUES(?,?,?,?,?)",
                [(cid, filename, title, number + index, title) for index, filename in enumerate(saved)],
            )
            connection.execute("UPDATE contests SET results_published=0 WHERE id=?", (cid,))
            connection.commit()
        except Exception:
            connection.rollback()
            for filename in saved:
                rm_file(filename)
            raise
    if invalid or not saved:
        flash(tr("f_bad_img"))
    return redirect(url_for("contest", cid=cid))

@app.route("/entries/<int:eid>/delete", methods=["POST"])
@admin_required
def del_entry(eid):
    e = q1("SELECT * FROM entries WHERE id=?", eid) or abort(404)
    run("DELETE FROM entries WHERE id=?", eid)
    run("UPDATE contests SET results_published=0 WHERE id=?", e["contest_id"])
    remove_if_unreferenced(e["filename"])
    return redirect(url_for("contest", cid=e["contest_id"]))

@app.route("/entries/<int:eid>/edit", methods=["POST"])
@admin_required
def edit_entry(eid):
    entry = q1("SELECT * FROM entries WHERE id=?", eid) or abort(404)
    title = request.form.get("title", "").strip()[:200]
    uploaded = request.files.get("photo")
    new_filename = save_image(uploaded) if uploaded and uploaded.filename else None
    if uploaded and uploaded.filename and not new_filename:
        flash(tr("f_bad_img"))
        return redirect(url_for("contest", cid=entry["contest_id"]))
    run(
        "UPDATE entries SET title=?,caption=?,filename=COALESCE(?,filename) WHERE id=?",
        title, title, new_filename, eid,
    )
    run("UPDATE contests SET results_published=0 WHERE id=?", entry["contest_id"])
    if new_filename:
        remove_if_unreferenced(entry["filename"])
    return redirect(url_for("contest", cid=entry["contest_id"]))

@app.route("/contests/<int:cid>/toggle", methods=["POST"])
@admin_required
def toggle(cid):
    c = q1("SELECT * FROM contests WHERE id=?", cid) or abort(404)
    if c["status"] == "open":
        new = "closed"
        run("UPDATE contests SET status='closed',results_published=0 WHERE id=?", cid)
    else:
        duration = requested_vote_duration()
        if duration is None:
            flash(tr("f_vote_duration"))
            return redirect(url_for("contest", cid=cid))
        new = "open"
        deadline = (datetime.now(timezone.utc) + duration).strftime("%Y-%m-%dT%H:%M:%SZ")
        run("UPDATE contests SET status='open',starts_at=NULL,closes_at=?,start=NULL,end=?,results_published=0 WHERE id=?",
            deadline, deadline, cid)
    return redirect(url_for("contest", cid=cid))

@app.route("/contests/<int:cid>/deadline", methods=["POST"])
@admin_required
def set_contest_deadline(cid):
    c = q1("SELECT * FROM contests WHERE id=?", cid) or abort(404)
    if c["status"] != "open":
        flash(tr("f_vote_closed"))
        return redirect(url_for("contest", cid=cid))
    duration = requested_vote_duration()
    if duration is None:
        flash(tr("f_vote_duration"))
    else:
        starts_at = c["starts_at"]
        if starts_at:
            start = datetime.strptime(starts_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            deadline = (start + duration).strftime("%Y-%m-%dT%H:%M:%SZ")
        else:
            deadline = (datetime.now(timezone.utc) + duration).strftime("%Y-%m-%dT%H:%M:%SZ")
        run("UPDATE contests SET closes_at=?,end=? WHERE id=?", deadline, deadline, cid)
        flash(tr("f_vote_deadline_set"))
    return redirect(url_for("contest", cid=cid))

@app.route("/contests/<int:cid>/schedule", methods=["POST"])
@admin_required
def set_contest_schedule(cid):
    c = q1("SELECT * FROM contests WHERE id=?", cid) or abort(404)
    start_text = request.form.get("starts_at", "")
    end_text = request.form.get("closes_at", "")
    starts_at = parse_vote_datetime(start_text)
    closes_at = parse_vote_datetime(end_text)
    if (start_text and not starts_at) or (end_text and not closes_at):
        flash(tr("f_vote_datetime"))
        return redirect(url_for("contest", cid=cid))
    if starts_at and closes_at and closes_at <= starts_at:
        flash(tr("f_vote_schedule_order"))
        return redirect(url_for("contest", cid=cid))
    if closes_at and closes_at <= now_iso():
        flash(tr("f_vote_end_future"))
        return redirect(url_for("contest", cid=cid))
    run("UPDATE contests SET status='open',starts_at=?,closes_at=?,start=?,end=?,results_published=0 WHERE id=?",
        starts_at, closes_at, starts_at, closes_at, cid)
    flash(tr("f_vote_schedule_saved"))
    return redirect(url_for("contest", cid=cid))

@app.route("/contests/<int:cid>/publish-results", methods=["POST"])
@admin_required
def publish_contest_results(cid):
    c = q1("SELECT * FROM contests WHERE id=?", cid) or abort(404)
    if c["status"] != "closed":
        abort(409)
    run("UPDATE contests SET results_published=1 WHERE id=?", cid)
    flash(tr("f_vote_results_published"))
    return redirect(url_for("contest", cid=cid))

@app.route("/contests/<int:cid>/unpublish-results", methods=["POST"])
@admin_required
def unpublish_contest_results(cid):
    q1("SELECT id FROM contests WHERE id=?", cid) or abort(404)
    run("UPDATE contests SET status='closed',results_published=0 WHERE id=?", cid)
    flash("Results are no longer public.")
    return redirect(url_for("contest", cid=cid))

@app.route("/contests/<int:cid>/reset-votes", methods=["POST"])
@admin_required
def reset_contest_votes(cid):
    contest_row = q1("SELECT id,event_id,title,status FROM contests WHERE id=?", cid) or abort(404)
    entry_files = [row["filename"] for row in q("SELECT filename FROM entries WHERE contest_id=?", cid)]
    connection = db()
    connection.execute("BEGIN IMMEDIATE")
    connection.execute("DELETE FROM vote_accounts WHERE contest_id=?", (cid,))
    connection.execute("DELETE FROM vote_phone_hashes WHERE contest_id=?", (cid,))
    connection.execute("DELETE FROM votes WHERE contest_id=?", (cid,))
    connection.execute(
        "UPDATE contests SET status=CASE WHEN status='published' THEN 'closed' ELSE status END,"
        "results_published=0 WHERE id=?",
        (cid,),
    )
    if contest_row["event_id"] and entry_files:
        connection.executemany(
            "DELETE FROM winners WHERE event_id=? AND category=? AND photo=?",
            [(contest_row["event_id"], contest_row["title"], filename) for filename in entry_files],
        )
    connection.commit()
    flash("Contest votes and saved contest winners were reset.")
    return redirect(url_for("contest", cid=cid))

@app.route("/contests/<int:cid>/delete", methods=["POST"])
@admin_required
def del_contest(cid):
    filenames = [entry["filename"] for entry in q("SELECT filename FROM entries WHERE contest_id=?", cid)]
    run("DELETE FROM contests WHERE id=?", cid)
    for filename in filenames:
        remove_if_unreferenced(filename)
    return redirect(url_for("contests"))

# ---------- admin dashboard ----------
def action(url, label, danger=False, select=None, current=None, confirm=None):
    return dict(url=url, label=label, danger=danger, select=select, current=current, confirm=confirm)

def link(url, text):
    return Markup('<a href="{}" target="_blank" rel="noopener">{}</a>').format(url, text)

@app.route("/admin")
@admin_required
def admin_dashboard():
    c = lambda sql: q1(sql)["n"]
    s = [("Organization members", c("SELECT COUNT(*) n FROM users WHERE role='member'"), "admin_members"),
         ("Pending event proposals", c("SELECT COUNT(*) n FROM event_proposals WHERE status='pending'"), "admin_proposals"),
         ("Events", c("SELECT COUNT(*) n FROM events"), "events"),
         ("Updates", c("SELECT COUNT(*) n FROM updates"), "updates"),
         ("Open contests", c("SELECT COUNT(*) n FROM contests WHERE status='open' AND (starts_at IS NULL OR starts_at<=strftime('%Y-%m-%dT%H:%M:%SZ','now'))"), "contests"),
         ("New problem reports", c("SELECT COUNT(*) n FROM reports WHERE status='new'"), "admin_reports"),
         ("New student applications", c("SELECT COUNT(*) n FROM apps WHERE status='new'"), "admin_apps"),
         ("Trees planted", c("SELECT COALESCE(SUM(qty),0) n FROM trees"), "admin_trees"), ("Event media", c("SELECT COUNT(*) n FROM memories"), "events")]
    event_rows = q("SELECT id,title,date,status FROM events ORDER BY date DESC,id DESC")
    contest_rows = q(
        "SELECT c.id,c.title,c.status,c.results_published,"
        "(SELECT COUNT(*) FROM votes v WHERE v.contest_id=c.id) vote_count "
        "FROM contests c ORDER BY c.id DESC"
    )
    return render_template(
        "admin_dashboard.html", stats=s, event_rows=event_rows,
        contest_rows=contest_rows,
    )

@app.route("/admin/members")
@admin_required
def admin_members():
    rows = []
    for u in q("SELECT * FROM users ORDER BY is_admin DESC, id"):
        acts = []
        if u["id"] != g.user["id"]:
            if u["is_admin"]:
                acts.append(action(url_for("admin_member_role", uid=u["id"], role="member"), "Make organization member"))
            elif u["role"] == "member":
                acts.extend([
                    action(url_for("admin_member_role", uid=u["id"], role="public"), "Remove member access"),
                    action(url_for("admin_member_role", uid=u["id"], role="admin"), "Make admin"),
                ])
            else:
                acts.extend([
                    action(url_for("admin_member_role", uid=u["id"], role="member"), "Promote to organization member"),
                    action(url_for("admin_member_role", uid=u["id"], role="admin"), "Make admin"),
                ])
            acts.append(action(url_for("admin_member_delete", uid=u["id"]), "Delete", True, confirm="Delete this account?"))
        role_name = "Admin" if u["is_admin"] else "Organization member" if u["role"] == "member" else "View-only account"
        rows.append(dict(cells=[u["name"], u["phone"], role_name], actions=acts))
    return render_template("admin_table.html", title="Accounts & permissions", cols=["Name", "Phone / username", "Access level"], rows=rows)

@app.route("/admin/members/<int:uid>/role/<role>", methods=["POST"])
@admin_required
def admin_member_role(uid, role):
    if role not in ("public", "member", "admin"): abort(400)
    if uid != g.user["id"]:
        run("UPDATE users SET role=?,is_admin=? WHERE id=?", role, 1 if role == "admin" else 0, uid)
    return redirect(url_for("admin_members"))

@app.route("/admin/members/<int:uid>/delete", methods=["POST"])
@admin_required
def admin_member_delete(uid):
    if uid != g.user["id"]: run("DELETE FROM users WHERE id=?", uid)
    return redirect(url_for("admin_members"))

@app.route("/admin/reports")
@admin_required
def admin_reports():
    rows = []
    for r in q("SELECT r.*, u.name, u.phone FROM reports r LEFT JOIN users u ON u.id=r.user_id ORDER BY r.id DESC"):
        rows.append(dict(cells=[r["created"][:10], r["category"], r["text"], f"{r['name']} ({r['phone']})" if r["name"] else "-",
                                link(url_for("uploaded", name=r["photo"]), "Photo") if r["photo"] else "-"],
                         actions=[action(url_for("admin_status", kind="reports", rid=r["id"]), "Save", select=["new", "in_progress", "resolved"], current=r["status"]),
                                  action(url_for("admin_delete", kind="reports", rid=r["id"]), "Delete", True, confirm="Delete this report?")]))
    return render_template("admin_table.html", title="Village problem reports", cols=["Date", "Type", "Problem", "From", "Photo"], rows=rows)

@app.route("/admin/applications")
@admin_required
def admin_apps():
    rows = []
    for a in q("SELECT * FROM apps ORDER BY id DESC"):
        rows.append(dict(cells=[a["created"][:10], a["student"], a["cls"], a["school"], a["phone"], a["program"], a["note"]],
                         actions=[action(url_for("admin_status", kind="apps", rid=a["id"]), "Save", select=["new", "approved", "rejected"], current=a["status"]),
                                  action(url_for("admin_delete", kind="apps", rid=a["id"]), "Delete", True, confirm="Delete this application?")]))
    return render_template("admin_table.html", title="Student applications", cols=["Date", "Student", "Class", "School", "Phone", "Program", "Note"],
                           rows=rows, export=url_for("admin_apps_csv"))

@app.route("/admin/applications.csv")
@admin_required
def admin_apps_csv():
    safe = lambda v: ("'" + str(v)) if str(v or "").startswith(("=", "+", "-", "@")) else (v or "")
    out = io.StringIO(); w = csv.writer(out)
    w.writerow(["Date", "Student", "Class", "School", "Phone", "Program", "Note", "Status"])
    for a in q("SELECT * FROM apps ORDER BY id"):
        w.writerow([safe(x) for x in (a["created"][:10], a["student"], a["cls"], a["school"], a["phone"], a["program"], a["note"], a["status"])])
    return Response("\ufeff" + out.getvalue(), mimetype="text/csv", headers={"Content-Disposition": "attachment; filename=applications.csv"})

@app.route("/admin/<kind>/<int:rid>/status", methods=["POST"])
@admin_required
def admin_status(kind, rid):
    allowed = {"reports": ["new", "in_progress", "resolved"], "apps": ["new", "approved", "rejected"]}
    if kind not in allowed or request.form.get("value") not in allowed[kind]: abort(400)
    run(f"UPDATE {kind} SET status=? WHERE id=?", request.form["value"], rid)
    return redirect(url_for("admin_reports" if kind == "reports" else "admin_apps"))

@app.route("/admin/<kind>/<int:rid>/delete", methods=["POST"])
@admin_required
def admin_delete(kind, rid):
    if kind not in ("reports", "apps"): abort(400)
    if kind == "reports":
        r = q1("SELECT photo FROM reports WHERE id=?", rid)
        if r: rm_file(r["photo"])
    run(f"DELETE FROM {kind} WHERE id=?", rid)
    return redirect(url_for("admin_reports" if kind == "reports" else "admin_apps"))

@app.route("/admin/trees", methods=["GET", "POST"])
@admin_required
def admin_trees():
    if request.method == "POST":
        try: n = int(request.form.get("qty", "0"))
        except ValueError: n = 0
        if n > 0 and request.form.get("date"):
            run("INSERT INTO trees(date,place,qty) VALUES(?,?,?)", request.form["date"], request.form.get("place", "").strip(), n)
        return redirect(url_for("admin_trees"))
    rows = [dict(cells=[r["date"], r["place"], r["qty"]], actions=[action(url_for("admin_tree_delete", tid=r["id"]), "Delete", True, confirm="Delete?")])
            for r in q("SELECT * FROM trees ORDER BY date DESC")]
    return render_template("admin_table.html", title="Tree planting log", cols=["Date", "Place", "Saplings"], rows=rows, tree_form=True)

@app.route("/admin/trees/<int:tid>/delete", methods=["POST"])
@admin_required
def admin_tree_delete(tid):
    run("DELETE FROM trees WHERE id=?", tid); return redirect(url_for("admin_trees"))

init_db(DB, UPLOADS)
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
