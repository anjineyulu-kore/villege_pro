import os
import sqlite3
from datetime import date, datetime, timedelta, timezone

from flask import g

from config import ADMIN_PASSWORD, ADMIN_USER, DB_PATH, TZ_HOURS, UPLOADS_DIR


SCHEMA = """
CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, name TEXT NOT NULL, phone TEXT UNIQUE NOT NULL, pw TEXT NOT NULL, is_admin INTEGER DEFAULT 0, role TEXT NOT NULL DEFAULT 'public');
CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, title TEXT NOT NULL, date TEXT NOT NULL, venue TEXT, description TEXT, category TEXT DEFAULT 'general', activities TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS event_programs(id INTEGER PRIMARY KEY, event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE, kind TEXT NOT NULL DEFAULT 'other', title TEXT NOT NULL, details TEXT NOT NULL DEFAULT '', result TEXT NOT NULL DEFAULT '', position INTEGER NOT NULL DEFAULT 0, time TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS event_program_people(id INTEGER PRIMARY KEY, program_id INTEGER NOT NULL REFERENCES event_programs(id) ON DELETE CASCADE, name TEXT NOT NULL, photo TEXT, created TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS memories(id INTEGER PRIMARY KEY, event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE, filename TEXT NOT NULL, caption TEXT, media_type TEXT NOT NULL DEFAULT 'image');
CREATE TABLE IF NOT EXISTS event_proposals(id INTEGER PRIMARY KEY, user_id INTEGER REFERENCES users(id) ON DELETE SET NULL, title TEXT NOT NULL, date TEXT NOT NULL, venue TEXT, description TEXT, category TEXT DEFAULT 'general', activities TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'pending', created TEXT NOT NULL, event_id INTEGER REFERENCES events(id) ON DELETE SET NULL);
CREATE TABLE IF NOT EXISTS proposal_media(id INTEGER PRIMARY KEY, proposal_id INTEGER NOT NULL REFERENCES event_proposals(id) ON DELETE CASCADE, filename TEXT NOT NULL, media_type TEXT NOT NULL, caption TEXT);
CREATE TABLE IF NOT EXISTS contests(id INTEGER PRIMARY KEY, title TEXT NOT NULL, description TEXT, event_id INTEGER REFERENCES events(id) ON DELETE SET NULL, status TEXT DEFAULT 'open', created TEXT);
CREATE TABLE IF NOT EXISTS entries(id INTEGER PRIMARY KEY, contest_id INTEGER NOT NULL REFERENCES contests(id) ON DELETE CASCADE, filename TEXT NOT NULL, title TEXT);
CREATE TABLE IF NOT EXISTS updates(id INTEGER PRIMARY KEY, text TEXT NOT NULL, created TEXT NOT NULL, category TEXT DEFAULT 'general', pinned INTEGER DEFAULT 0, photo TEXT);
CREATE TABLE IF NOT EXISTS reports(id INTEGER PRIMARY KEY, user_id INTEGER REFERENCES users(id) ON DELETE SET NULL, category TEXT, text TEXT NOT NULL, photo TEXT, status TEXT DEFAULT 'new', created TEXT);
CREATE TABLE IF NOT EXISTS apps(id INTEGER PRIMARY KEY, user_id INTEGER REFERENCES users(id) ON DELETE SET NULL, student TEXT, cls TEXT, school TEXT, phone TEXT, program TEXT, note TEXT, status TEXT DEFAULT 'new', created TEXT);
CREATE TABLE IF NOT EXISTS trees(id INTEGER PRIMARY KEY, date TEXT NOT NULL, place TEXT, qty INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS games(id INTEGER PRIMARY KEY, event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE, name TEXT NOT NULL, time TEXT NOT NULL DEFAULT '', details TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS winners(id INTEGER PRIMARY KEY, event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE, category TEXT NOT NULL DEFAULT '', name TEXT NOT NULL, photo TEXT);
CREATE TABLE IF NOT EXISTS media(id INTEGER PRIMARY KEY, event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE, kind TEXT NOT NULL CHECK(kind IN ('photo','video')), filename_or_youtube_url TEXT NOT NULL, caption TEXT);
CREATE TABLE IF NOT EXISTS votes(contest_id INTEGER NOT NULL REFERENCES contests(id) ON DELETE CASCADE, device_id TEXT NOT NULL, entry_id INTEGER NOT NULL REFERENCES entries(id) ON DELETE CASCADE, ts REAL, PRIMARY KEY(contest_id, device_id));
CREATE TABLE IF NOT EXISTS vote_accounts(contest_id INTEGER NOT NULL, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, device_id TEXT NOT NULL, PRIMARY KEY(contest_id, user_id), UNIQUE(contest_id, device_id), FOREIGN KEY(contest_id, device_id) REFERENCES votes(contest_id, device_id) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS vote_phone_hashes(contest_id INTEGER NOT NULL, phone_hash TEXT NOT NULL, device_id TEXT NOT NULL, PRIMARY KEY(contest_id, phone_hash), UNIQUE(contest_id, device_id), FOREIGN KEY(contest_id, device_id) REFERENCES votes(contest_id, device_id) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS login_attempts(id INTEGER PRIMARY KEY, ip TEXT NOT NULL, ts REAL NOT NULL);
CREATE INDEX IF NOT EXISTS login_attempts_ip_ts ON login_attempts(ip,ts);
CREATE TABLE IF NOT EXISTS schema_migrations(version TEXT PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
"""


def _columns(con, table):
    return {row[1] for row in con.execute(f"PRAGMA table_info({table})")}


def _add_column(con, table, column, declaration):
    if column not in _columns(con, table):
        con.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")


def _table_exists(con, table):
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def _parse_legacy_datetime(value):
    if not value:
        return None
    try:
        local = datetime.strptime(value, "%Y-%m-%dT%H:%M")
    except ValueError:
        return None
    return local.replace(tzinfo=timezone(timedelta(hours=TZ_HOURS))).astimezone(
        timezone.utc
    ).strftime("%Y-%m-%dT%H:%M:%SZ")


def _migrate_single_contest(con, uploads_dir):
    if not _table_exists(con, "legacy_single_votes"):
        return

    event = con.execute(
        "SELECT id FROM events WHERE title='Sankranthi Rangoli Contest' ORDER BY id LIMIT 1"
    ).fetchone()
    if event:
        event_id = event[0]
    else:
        event_id = con.execute(
            "INSERT INTO events(title,date,venue,description,category,activities,occasion,place,status) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            ("Sankranthi Rangoli Contest", date.today().isoformat(), "", "", "culture", "",
             "Sankranthi", "", "upcoming"),
        ).lastrowid

    contest = con.execute(
        "SELECT id FROM contests WHERE title='Sankranthi Rangoli Contest' AND event_id=? ORDER BY id LIMIT 1",
        (event_id,),
    ).fetchone()
    if contest:
        contest_id = contest[0]
    else:
        contest_id = con.execute(
            "INSERT INTO contests(title,description,event_id,status,created,results_published) "
            "VALUES(?,?,?,?,?,0)",
            ("Sankranthi Rangoli Contest", "", event_id, "open", date.today().isoformat()),
        ).lastrowid

    files_by_number = {}
    if os.path.isdir(uploads_dir):
        for filename in os.listdir(uploads_dir):
            number, extension = os.path.splitext(filename)
            if number.isdigit() and extension.lower() in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
                files_by_number.setdefault(int(number), filename)

    for number, filename in sorted(files_by_number.items()):
        if not con.execute(
            "SELECT 1 FROM entries WHERE contest_id=? AND number=?", (contest_id, number)
        ).fetchone():
            con.execute(
                "INSERT INTO entries(contest_id,filename,title,number,caption) VALUES(?,?,?,?,?)",
                (contest_id, filename, f"#{number}", number, ""),
            )

    settings = {}
    if _table_exists(con, "settings") and {"k", "v"} <= _columns(con, "settings"):
        settings = dict(con.execute("SELECT k,v FROM settings"))
    status = settings.get("status", "open")
    if status not in ("open", "closed", "published"):
        status = "open"
    starts_at = _parse_legacy_datetime(settings.get("start", ""))
    closes_at = _parse_legacy_datetime(settings.get("end", ""))
    con.execute(
        "UPDATE contests SET status=?,starts_at=?,closes_at=?,start=?,end=?,results_published=? WHERE id=?",
        (status, starts_at, closes_at, starts_at, closes_at, int(status == "published"), contest_id),
    )

    rows = con.execute(
        "SELECT device_id,choice,ts FROM legacy_single_votes"
    ).fetchall()
    for device_id, choice, ts in rows:
        if not device_id or choice is None:
            continue
        try:
            number = int(choice)
        except (TypeError, ValueError):
            continue
        entry = con.execute(
            "SELECT id FROM entries WHERE contest_id=? AND number=?", (contest_id, number)
        ).fetchone()
        if not entry:
            filename = files_by_number.get(number, f"{number}.jpg")
            entry_id = con.execute(
                "INSERT INTO entries(contest_id,filename,title,number,caption) VALUES(?,?,?,?,?)",
                (contest_id, filename, f"#{number}", number, ""),
            ).lastrowid
        else:
            entry_id = entry[0]
        con.execute(
            "INSERT OR IGNORE INTO votes(contest_id,device_id,entry_id,ts) VALUES(?,?,?,?)",
            (contest_id, str(device_id), entry_id, ts),
        )


def _migrate_existing_votes(con):
    if _table_exists(con, "legacy_account_votes"):
        for contest_id, entry_id, user_id in con.execute(
            "SELECT contest_id,entry_id,user_id FROM legacy_account_votes"
        ):
            device_id = f"legacy-user:{user_id}"
            con.execute(
                "INSERT OR IGNORE INTO votes(contest_id,device_id,entry_id,ts) VALUES(?,?,?,0)",
                (contest_id, device_id, entry_id),
            )
            con.execute(
                "INSERT OR IGNORE INTO vote_accounts(contest_id,user_id,device_id) VALUES(?,?,?)",
                (contest_id, user_id, device_id),
            )

    if _table_exists(con, "device_votes"):
        columns = _columns(con, "device_votes")
        if {"contest_id", "entry_id", "voter_id"} <= columns:
            for contest_id, entry_id, voter_id in con.execute(
                "SELECT contest_id,entry_id,voter_id FROM device_votes"
            ):
                if voter_id:
                    con.execute(
                        "INSERT OR IGNORE INTO votes(contest_id,device_id,entry_id,ts) VALUES(?,?,?,0)",
                        (contest_id, str(voter_id), entry_id),
                    )


def init_db(database_path=DB_PATH, uploads_dir=UPLOADS_DIR):
    os.makedirs(os.path.dirname(os.path.abspath(database_path)), exist_ok=True)
    con = sqlite3.connect(database_path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    try:
        existing_vote_columns = _columns(con, "votes") if _table_exists(con, "votes") else set()
        if {"device_id", "choice"} <= existing_vote_columns:
            if not _table_exists(con, "legacy_single_votes"):
                con.execute("ALTER TABLE votes RENAME TO legacy_single_votes")
        elif {"contest_id", "entry_id", "user_id"} <= existing_vote_columns:
            if not _table_exists(con, "legacy_account_votes"):
                con.execute("ALTER TABLE votes RENAME TO legacy_account_votes")

        con.executescript(SCHEMA)
        _add_column(con, "users", "role", "TEXT NOT NULL DEFAULT 'public'")
        _add_column(con, "event_programs", "time", "TEXT NOT NULL DEFAULT ''")
        con.execute("UPDATE users SET role='admin' WHERE is_admin=1")
        for column, declaration in (
            ("activities", "TEXT NOT NULL DEFAULT ''"),
            ("donation_qr", "TEXT"),
            ("donation_message", "TEXT NOT NULL DEFAULT ''"),
            ("occasion", "TEXT NOT NULL DEFAULT ''"),
            ("place", "TEXT NOT NULL DEFAULT ''"),
            ("status", "TEXT NOT NULL DEFAULT 'past'"),
        ):
            _add_column(con, "events", column, declaration)
        con.execute("UPDATE events SET place=COALESCE(NULLIF(place,''),venue,'')")
        con.execute("UPDATE events SET occasion=COALESCE(NULLIF(occasion,''),category,'')")
        con.execute(
            "UPDATE events SET status=CASE WHEN date>=date('now') THEN 'upcoming' ELSE 'past' END"
        )

        for column, declaration in (
            ("created", "TEXT"),
            ("closes_at", "TEXT"),
            ("starts_at", "TEXT"),
            ("start", "TEXT"),
            ("end", "TEXT"),
            ("results_published", "INTEGER NOT NULL DEFAULT 0"),
        ):
            _add_column(con, "contests", column, declaration)
        con.execute("UPDATE contests SET created=date('now') WHERE created IS NULL OR created=''")
        con.execute("UPDATE contests SET start=starts_at WHERE start IS NULL AND starts_at IS NOT NULL")
        con.execute("UPDATE contests SET end=closes_at WHERE end IS NULL AND closes_at IS NOT NULL")

        for column, declaration in (
            ("number", "INTEGER"),
            ("caption", "TEXT NOT NULL DEFAULT ''"),
        ):
            _add_column(con, "entries", column, declaration)
        for contest in con.execute("SELECT id FROM contests"):
            used = set()
            next_number = 1
            for entry in con.execute(
                "SELECT id,number,title FROM entries WHERE contest_id=? ORDER BY id", (contest["id"],)
            ):
                number = entry["number"]
                if number is None or number <= 0 or number in used:
                    while next_number in used:
                        next_number += 1
                    number = next_number
                used.add(number)
                next_number = max(next_number, number + 1)
                con.execute(
                    "UPDATE entries SET number=?,caption=COALESCE(NULLIF(caption,''),?) WHERE id=?",
                    (number, entry["title"] or "", entry["id"]),
                )
        con.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS entries_contest_number ON entries(contest_id,number)"
        )

        _add_column(con, "memories", "media_type", "TEXT NOT NULL DEFAULT 'image'")
        _add_column(con, "updates", "category", "TEXT DEFAULT 'general'")
        _add_column(con, "updates", "pinned", "INTEGER DEFAULT 0")
        _add_column(con, "updates", "photo", "TEXT")
        _add_column(con, "updates", "date", "TEXT")
        con.execute("UPDATE updates SET date=created WHERE date IS NULL OR date=''")

        with con:
            if not con.execute(
                "SELECT 1 FROM schema_migrations WHERE version='0001_legacy_media_games_winners'"
            ).fetchone():
                con.execute(
                    "INSERT INTO games(event_id,name,time,details) "
                    "SELECT event_id,title,'',details FROM event_programs WHERE kind='game'"
                )
                con.execute(
                    "INSERT INTO winners(event_id,category,name,photo) "
                    "SELECT p.event_id,p.title,pp.name,pp.photo FROM event_program_people pp "
                    "JOIN event_programs p ON p.id=pp.program_id"
                )
                con.execute(
                    "INSERT INTO media(event_id,kind,filename_or_youtube_url,caption) "
                    "SELECT event_id,CASE WHEN media_type='video' THEN 'video' ELSE 'photo' END,filename,caption FROM memories"
                )
                con.execute(
                    "INSERT INTO schema_migrations(version) VALUES('0001_legacy_media_games_winners')"
                )
            _migrate_existing_votes(con)
            _migrate_single_contest(con, uploads_dir)

        admin = con.execute(
            "SELECT id FROM users WHERE is_admin=1 "
            "ORDER BY CASE WHEN phone=? THEN 0 ELSE 1 END,id LIMIT 1",
            (ADMIN_USER,),
        ).fetchone()
        if not admin and ADMIN_USER and ADMIN_PASSWORD:
            from werkzeug.security import generate_password_hash

            con.execute(
                "INSERT INTO users(name,phone,pw,is_admin,role) VALUES(?,?,?,1,'admin')",
                ("Admin", ADMIN_USER, generate_password_hash(ADMIN_PASSWORD)),
            )
            con.execute(
                "INSERT OR IGNORE INTO schema_migrations(version) VALUES('0002_admin_environment_credentials')"
            )
        elif admin and ADMIN_USER and ADMIN_PASSWORD and not con.execute(
            "SELECT 1 FROM schema_migrations WHERE version='0002_admin_environment_credentials'"
        ).fetchone():
            from werkzeug.security import generate_password_hash

            username_conflict = con.execute(
                "SELECT 1 FROM users WHERE phone=? AND id<>?",
                (ADMIN_USER, admin["id"]),
            ).fetchone()
            if username_conflict:
                raise RuntimeError(
                    "ADMIN_USER is already assigned to another account. Choose a unique value."
                )
            con.execute(
                "UPDATE users SET phone=?,pw=? WHERE id=?",
                (ADMIN_USER, generate_password_hash(ADMIN_PASSWORD), admin["id"]),
            )
            con.execute(
                "INSERT INTO schema_migrations(version) VALUES('0002_admin_environment_credentials')"
            )
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys=ON")
    return g.db


def close_db():
    connection = g.pop("db", None)
    if connection is not None:
        connection.close()


def q(sql, *args):
    return get_db().execute(sql, args).fetchall()


def q1(sql, *args):
    return get_db().execute(sql, args).fetchone()


def run(sql, *args):
    cursor = get_db().execute(sql, args)
    get_db().commit()
    return cursor.lastrowid
