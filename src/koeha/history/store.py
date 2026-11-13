import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone

from koeha.core.events import Caption


class History:
    def __init__(self, path):
        self.path = path
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY, created TEXT, title TEXT);
                CREATE TABLE IF NOT EXISTS captions(
                    session TEXT, id INTEGER, start REAL, end REAL, text TEXT, translation TEXT,
                    translation_error TEXT, PRIMARY KEY(session, id));
            """)

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path, timeout=10)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def create(self, title):
        session = uuid.uuid4().hex
        with self.connect() as db:
            db.execute("INSERT INTO sessions VALUES(?,?,?)",
                       (session, datetime.now(timezone.utc).isoformat(timespec="seconds"), title))
        return session

    def save(self, session, caption):
        if not caption.final:
            return
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO captions VALUES(?,?,?,?,?,?,?)",
                       (session, caption.id, caption.start, caption.end, caption.text,
                        caption.translation, caption.translation_error))

    def sessions(self):
        with self.connect() as db:
            return db.execute("SELECT id,created,title FROM sessions ORDER BY created DESC").fetchall()

    def captions(self, session):
        with self.connect() as db:
            return [Caption(row[0], row[1], row[2], row[3], True, row[4] or "", row[5] or "")
                    for row in db.execute("SELECT id,start,end,text,translation,translation_error FROM captions "
                                          "WHERE session=? ORDER BY id", (session,))]

    def delete(self, session):
        with self.connect() as db:
            db.execute("DELETE FROM captions WHERE session=?", (session,))
            db.execute("DELETE FROM sessions WHERE id=?", (session,))


def timestamp(seconds, separator=","):
    milliseconds = max(0, round(seconds * 1000))
    hours, remainder = divmod(milliseconds, 3600000)
    minutes, remainder = divmod(remainder, 60000)
    seconds, milliseconds = divmod(remainder, 1000)
    return f"{hours:02}:{minutes:02}:{seconds:02}{separator}{milliseconds:03}"


def cue_text(text):
    """A blank line ends an SRT/VTT cue and "-->" starts a timing line, so neither may appear in cue text."""
    return "\n".join(line.strip().replace("-->", "->") for line in text.splitlines() if line.strip())


def local_time(created):
    """Sessions store UTC ISO timestamps; show them in the viewer's local time."""
    try:
        moment = datetime.fromisoformat(created)
    except (TypeError, ValueError):
        return str(created)[:19].replace("T", "  ")
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone().strftime("%Y-%m-%d  %H:%M:%S")


def export(captions, path):
    from dataclasses import asdict
    captions = sorted((c for c in captions if c.final and c.text), key=lambda c: c.id)
    suffix = path.suffix.lower()
    if suffix == ".json":
        text = json.dumps([asdict(c) for c in captions], ensure_ascii=False, indent=2)
    elif suffix in {".srt", ".vtt"}:
        separator = "." if suffix == ".vtt" else ","
        blocks = []
        for index, caption in enumerate(captions, 1):
            content = cue_text(caption.text + ("\n" + caption.translation if caption.translation else ""))
            blocks.append(f"{index}\n{timestamp(caption.start, separator)} --> "
                          f"{timestamp(caption.end, separator)}\n{content}\n")
        text = ("WEBVTT\n\n" if suffix == ".vtt" else "") + "\n".join(blocks)
    else:
        text = "\n\n".join(c.text + ("\n" + c.translation if c.translation else "") for c in captions)
    path.write_text(text, encoding="utf-8")
