"""Create optional Telugu sample records and placeholder rangoli images."""

import os
import sqlite3
from datetime import date, timedelta

from PIL import Image, ImageDraw

from config import DB_PATH, THUMBNAILS_DIR, UPLOADS_DIR
from db import init_db


EVENT_TITLE = "[గ్రామం పేరు] గ్రామోత్సవం"
CONTEST_TITLE = "[సంవత్సరం] సంక్రాంతి ముగ్గుల పోటీ"


def create_placeholder(filename, background, accent, offset):
    source = os.path.join(UPLOADS_DIR, filename)
    if not os.path.exists(source):
        image = Image.new("RGB", (640, 640), background)
        draw = ImageDraw.Draw(image)
        draw.ellipse((100 + offset, 100, 540 - offset, 540), outline=accent, width=18)
        draw.ellipse((180, 180 + offset, 460, 460 - offset), outline=accent, width=12)
        draw.polygon(
            ((320, 215), (360, 295), (450, 320), (360, 345), (320, 425),
             (280, 345), (190, 320), (280, 295)),
            fill=accent,
        )
        image.save(source, "PNG", optimize=True)
    with Image.open(source) as image:
        image.thumbnail((480, 480), Image.Resampling.LANCZOS)
        image.save(os.path.join(THUMBNAILS_DIR, filename + ".webp"), "WEBP", quality=78)


def main():
    os.makedirs(UPLOADS_DIR, exist_ok=True)
    os.makedirs(THUMBNAILS_DIR, exist_ok=True)
    init_db(DB_PATH, UPLOADS_DIR)
    con = sqlite3.connect(DB_PATH)
    con.execute("PRAGMA foreign_keys=ON")
    try:
        event = con.execute("SELECT id FROM events WHERE title=?", (EVENT_TITLE,)).fetchone()
        if event:
            event_id = event[0]
        else:
            event_id = con.execute(
                "INSERT INTO events(title,date,venue,place,occasion,description,category,status) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (
                    EVENT_TITLE, (date.today() + timedelta(days=14)).isoformat(),
                    "[స్థలం]", "[గ్రామం పేరు]", "సంక్రాంతి",
                    "[కార్యక్రమ వివరాలు]", "culture", "upcoming",
                ),
            ).lastrowid

        game = con.execute(
            "SELECT id FROM games WHERE event_id=? AND name=?", (event_id, "ముగ్గుల పోటీ")
        ).fetchone()
        if not game:
            con.execute(
                "INSERT INTO games(event_id,name,time,details) VALUES(?,?,?,?)",
                (event_id, "ముగ్గుల పోటీ", "ఉదయం 9:00", "[నియమాలు]"),
            )

        contest = con.execute(
            "SELECT id FROM contests WHERE event_id=? AND title=?",
            (event_id, CONTEST_TITLE),
        ).fetchone()
        if contest:
            contest_id = contest[0]
        else:
            contest_id = con.execute(
                "INSERT INTO contests(event_id,title,description,status,created,results_published) "
                "VALUES(?,?,?,?,date('now'),0)",
                (event_id, CONTEST_TITLE, "[ఓటింగ్ వివరాలు]", "open"),
            ).lastrowid

        sample_entries = (
            ("seed-rangoli-1.png", "FFF8EC", "D87823", 0, "వసంత ముగ్గు"),
            ("seed-rangoli-2.png", "E7F1DE", "1E6B3A", 45, "పూల ముగ్గు"),
        )
        for number, (filename, bg, accent, offset, caption) in enumerate(sample_entries, 1):
            create_placeholder(filename, "#" + bg, "#" + accent, offset)
            con.execute(
                "INSERT OR IGNORE INTO entries(contest_id,number,filename,title,caption) "
                "VALUES(?,?,?,?,?)",
                (contest_id, number, filename, caption, caption),
            )
            if not con.execute(
                "SELECT 1 FROM media WHERE event_id=? AND filename_or_youtube_url=?",
                (event_id, filename),
            ).fetchone():
                con.execute(
                    "INSERT INTO media(event_id,kind,filename_or_youtube_url,caption) "
                    "VALUES(?,'photo',?,?)",
                    (event_id, filename, caption),
                )

        if not con.execute(
            "SELECT 1 FROM updates WHERE text=?", ("[తాజా సమాచారం]",)
        ).fetchone():
            con.execute(
                "INSERT INTO updates(text,created,date,category) "
                "VALUES(?,datetime('now'),date('now'),'general')",
                ("[తాజా సమాచారం]",),
            )
        con.commit()
        print("Telugu sample event, contest, photos, and update are ready.")
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


if __name__ == "__main__":
    main()
