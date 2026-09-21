"""SQLite index for EML files. Stores metadata and tags only — never the original files."""

from __future__ import annotations

import os
import shutil
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterable

SCHEMA = """
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS emails (
    id INTEGER PRIMARY KEY,
    path TEXT NOT NULL UNIQUE,
    filename TEXT NOT NULL DEFAULT '',
    folder TEXT NOT NULL DEFAULT '',
    message_id TEXT NOT NULL DEFAULT '',
    in_reply_to TEXT NOT NULL DEFAULT '',
    date_iso TEXT NOT NULL DEFAULT '',
    date_ts INTEGER NOT NULL DEFAULT 0,
    sender TEXT NOT NULL DEFAULT '',
    sender_email TEXT NOT NULL DEFAULT '',
    recipients TEXT NOT NULL DEFAULT '',
    recipient_emails TEXT NOT NULL DEFAULT '',
    cc TEXT NOT NULL DEFAULT '',
    subject TEXT NOT NULL DEFAULT '',
    snippet TEXT NOT NULL DEFAULT '',
    body_text TEXT NOT NULL DEFAULT '',
    has_html INTEGER NOT NULL DEFAULT 0,
    has_attachments INTEGER NOT NULL DEFAULT 0,
    attachment_count INTEGER NOT NULL DEFAULT 0,
    size_bytes INTEGER NOT NULL DEFAULT 0,
    mtime_ns INTEGER NOT NULL DEFAULT 0,
    starred INTEGER NOT NULL DEFAULT 0,
    unread INTEGER NOT NULL DEFAULT 1,
    year INTEGER,
    indexed_at INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_emails_date ON emails(date_ts DESC);
CREATE INDEX IF NOT EXISTS idx_emails_folder ON emails(folder);
CREATE INDEX IF NOT EXISTS idx_emails_sender ON emails(sender_email);
CREATE INDEX IF NOT EXISTS idx_emails_starred ON emails(starred);
CREATE INDEX IF NOT EXISTS idx_emails_year ON emails(year);

CREATE TABLE IF NOT EXISTS attachments (
    id INTEGER PRIMARY KEY,
    email_id INTEGER NOT NULL REFERENCES emails(id) ON DELETE CASCADE,
    part_index INTEGER NOT NULL,
    filename TEXT NOT NULL DEFAULT '',
    content_type TEXT NOT NULL DEFAULT '',
    size_bytes INTEGER NOT NULL DEFAULT 0,
    content_id TEXT NOT NULL DEFAULT '',
    inline INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_att_email ON attachments(email_id);

CREATE TABLE IF NOT EXISTS tags (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
    color TEXT NOT NULL DEFAULT '#3b82f6'
);

CREATE TABLE IF NOT EXISTS email_tags (
    email_id INTEGER NOT NULL REFERENCES emails(id) ON DELETE CASCADE,
    tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    PRIMARY KEY (email_id, tag_id)
);

CREATE TABLE IF NOT EXISTS notes (
    email_id INTEGER PRIMARY KEY REFERENCES emails(id) ON DELETE CASCADE,
    note TEXT NOT NULL DEFAULT ''
);
"""

FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS emails_fts USING fts5(
    subject,
    sender,
    recipients,
    body_text,
    filename,
    folder,
    tags,
    tokenize = 'unicode61 remove_diacritics 2'
);
"""


def default_db_path() -> Path:
    return Path.home() / ".email-archive" / "archive.db"


def pointer_path() -> Path:
    override = os.environ.get("EMAIL_ARCHIVE_POINTER", "").strip()
    if override:
        return Path(override)
    return Path.home() / ".email-archive" / "index-path.txt"


def resolve_db_path(raw: str | Path) -> Path:
    """Accept a folder (`Y:\\Mails`) or a file (`Y:\\Mails\\archive.db`)."""
    path = Path(str(raw).strip().strip('"')).expanduser()
    if path.suffix.lower() in {".db", ".sqlite", ".sqlite3"}:
        return path
    return path / "archive.db"


def remembered_db_path() -> Path | None:
    try:
        text = pointer_path().read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not text:
        return None
    return resolve_db_path(text)


def remember_db_path(path: Path) -> None:
    dest = pointer_path()
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(str(Path(path)), encoding="utf-8")


def preferred_db_path(cli: str | None = None) -> Path:
    if cli and str(cli).strip():
        path = resolve_db_path(cli)
        remember_db_path(path)
        return path
    env = os.environ.get("EMAIL_ARCHIVE_DB", "").strip()
    if env:
        return resolve_db_path(env)
    remembered = remembered_db_path()
    if remembered:
        return remembered
    return default_db_path()


def looks_network_path(path: Path) -> bool:
    text = str(path)
    if text.startswith("\\\\") or text.startswith("//"):
        return True
    if os.name == "nt" and len(text) >= 2 and text[1] == ":":
        letter = text[0].upper()
        try:
            import ctypes

            kind = ctypes.windll.kernel32.GetDriveTypeW(f"{letter}:\\")
            return int(kind) == 4  # DRIVE_REMOTE
        except Exception:
            return letter == "Y"
    return False


def copy_index_file(src: Path, dest: Path) -> None:
    dest = Path(dest)
    src = Path(src)
    if not src.is_file():
        raise FileNotFoundError(src)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if src.resolve() == dest.resolve():
        return
    shutil.copy2(src, dest)


class Store:
    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = Path(db_path) if db_path else default_db_path()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False, timeout=30)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        self.fts_ok = False
        self._init_schema()

    def _init_schema(self) -> None:
        self._configure_journal()
        self.conn.executescript(SCHEMA)
        self.fts_ok = self._try_fts()
        self.conn.commit()

    def _configure_journal(self) -> None:
        self.conn.execute("PRAGMA busy_timeout=30000")
        if looks_network_path(self.db_path):
            self.conn.execute("PRAGMA journal_mode=DELETE")
            return
        try:
            self.conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.Error:
            self.conn.execute("PRAGMA journal_mode=DELETE")

    def checkpoint(self) -> None:
        try:
            self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            self.conn.commit()
        except sqlite3.Error:
            pass

    def _try_fts(self) -> bool:
        try:
            self.conn.executescript(FTS_SCHEMA)
            return True
        except sqlite3.Error:
            return False

    def close(self) -> None:
        self.conn.close()

    def get_meta(self, key: str, default: str | None = None) -> str | None:
        row = self.conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def set_meta(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        self.conn.commit()

    def archive_root(self) -> str:
        return self.get_meta("archive_root", "") or ""

    def set_archive_root(self, path: str) -> None:
        self.set_meta("archive_root", path)

    def unchanged_paths(self, root: Path) -> dict[str, int]:
        """Map absolute path -> mtime_ns for incremental indexing."""
        root_s = str(Path(root))
        rows = self.conn.execute("SELECT path, mtime_ns FROM emails").fetchall()
        return {r["path"]: r["mtime_ns"] for r in rows if _under_root(r["path"], root_s)}

    def all_indexed_paths(self) -> set[str]:
        return {r["path"] for r in self.conn.execute("SELECT path FROM emails")}

    def upsert_email(self, rec: dict[str, Any], tag_names: str = "") -> int:
        now = int(time.time())
        existing = self.conn.execute(
            "SELECT id, starred, unread FROM emails WHERE path = ?",
            (rec["path"],),
        ).fetchone()
        starred = existing["starred"] if existing else 0
        unread = existing["unread"] if existing else 1
        fields = (
            rec["path"],
            rec["filename"],
            rec["folder"],
            rec["message_id"],
            rec["in_reply_to"],
            rec["date_iso"],
            rec["date_ts"],
            rec["sender"],
            rec["sender_email"],
            rec["recipients"],
            rec["recipient_emails"],
            rec["cc"],
            rec["subject"],
            rec["snippet"],
            rec["body_text"],
            rec["has_html"],
            rec["has_attachments"],
            rec["attachment_count"],
            rec["size_bytes"],
            rec["mtime_ns"],
            starred,
            unread,
            rec.get("year"),
            now,
        )
        if existing:
            email_id = int(existing["id"])
            self.conn.execute(
                """UPDATE emails SET
                    filename=?, folder=?, message_id=?, in_reply_to=?, date_iso=?, date_ts=?,
                    sender=?, sender_email=?, recipients=?, recipient_emails=?, cc=?,
                    subject=?, snippet=?, body_text=?, has_html=?, has_attachments=?,
                    attachment_count=?, size_bytes=?, mtime_ns=?, year=?, indexed_at=?
                   WHERE id=?""",
                (
                    rec["filename"],
                    rec["folder"],
                    rec["message_id"],
                    rec["in_reply_to"],
                    rec["date_iso"],
                    rec["date_ts"],
                    rec["sender"],
                    rec["sender_email"],
                    rec["recipients"],
                    rec["recipient_emails"],
                    rec["cc"],
                    rec["subject"],
                    rec["snippet"],
                    rec["body_text"],
                    rec["has_html"],
                    rec["has_attachments"],
                    rec["attachment_count"],
                    rec["size_bytes"],
                    rec["mtime_ns"],
                    rec.get("year"),
                    now,
                    email_id,
                ),
            )
            self.conn.execute("DELETE FROM attachments WHERE email_id = ?", (email_id,))
        else:
            cur = self.conn.execute(
                """INSERT INTO emails(
                    path, filename, folder, message_id, in_reply_to, date_iso, date_ts,
                    sender, sender_email, recipients, recipient_emails, cc, subject,
                    snippet, body_text, has_html, has_attachments, attachment_count,
                    size_bytes, mtime_ns, starred, unread, year, indexed_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                fields,
            )
            email_id = int(cur.lastrowid)

        att_rows = [
            (
                email_id,
                a.get("part_index", 0),
                a.get("filename") or "",
                a.get("content_type") or "",
                int(a.get("size_bytes") or 0),
                a.get("content_id") or "",
                1 if a.get("inline") else 0,
            )
            for a in rec.get("attachments") or []
        ]
        if att_rows:
            self.conn.executemany(
                """INSERT INTO attachments(
                    email_id, part_index, filename, content_type, size_bytes, content_id, inline
                ) VALUES (?,?,?,?,?,?,?)""",
                att_rows,
            )

        if self.fts_ok:
            self._upsert_fts(email_id, rec, tag_names)
        return email_id

    def _tag_names_for(self, email_id: int) -> str:
        rows = self.conn.execute(
            """SELECT t.name FROM tags t
               JOIN email_tags et ON et.tag_id = t.id
               WHERE et.email_id = ?""",
            (email_id,),
        ).fetchall()
        return " ".join(r["name"] for r in rows)

    def _upsert_fts(self, email_id: int, rec: dict[str, Any], tag_names: str = "") -> None:
        if not tag_names:
            tag_names = self._tag_names_for(email_id)
        self.conn.execute("DELETE FROM emails_fts WHERE rowid = ?", (email_id,))
        self.conn.execute(
            """INSERT INTO emails_fts(rowid, subject, sender, recipients, body_text, filename, folder, tags)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                email_id,
                rec["subject"],
                rec["sender"] + " " + rec.get("sender_email", ""),
                rec["recipients"] + " " + rec.get("recipient_emails", ""),
                rec.get("body_text") or "",
                rec.get("filename") or "",
                rec.get("folder") or "",
                tag_names,
            ),
        )

    def delete_missing(self, keep_paths: set[str], root: str) -> int:
        rows = self.conn.execute("SELECT id, path FROM emails").fetchall()
        gone = [
            r["id"]
            for r in rows
            if _under_root(r["path"], root) and r["path"] not in keep_paths
        ]
        if not gone:
            return 0
        qmarks = ",".join("?" * len(gone))
        if self.fts_ok:
            self.conn.execute(f"DELETE FROM emails_fts WHERE rowid IN ({qmarks})", gone)
        self.conn.execute(f"DELETE FROM emails WHERE id IN ({qmarks})", gone)
        return len(gone)

    def commit(self) -> None:
        self.conn.commit()

    def stats(self) -> dict[str, Any]:
        total = self.conn.execute("SELECT COUNT(*) AS n FROM emails").fetchone()["n"]
        starred = self.conn.execute(
            "SELECT COUNT(*) AS n FROM emails WHERE starred = 1"
        ).fetchone()["n"]
        attached = self.conn.execute(
            "SELECT COUNT(*) AS n FROM emails WHERE has_attachments = 1"
        ).fetchone()["n"]
        unread = self.conn.execute(
            "SELECT COUNT(*) AS n FROM emails WHERE unread = 1"
        ).fetchone()["n"]
        size = self.conn.execute(
            "SELECT COALESCE(SUM(size_bytes), 0) AS n FROM emails"
        ).fetchone()["n"]
        return {
            "total": total,
            "starred": starred,
            "with_attachments": attached,
            "unread": unread,
            "bytes": size,
            "fts": self.fts_ok,
            "archive_root": self.archive_root(),
            "db_path": str(self.db_path),
            "last_index": self.get_meta("last_index"),
        }

    def folders(self) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """SELECT folder, COUNT(*) AS n FROM emails
               GROUP BY folder ORDER BY folder COLLATE NOCASE"""
        ).fetchall()
        return [{"folder": r["folder"], "count": r["n"]} for r in rows]

    def years(self) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """SELECT year, COUNT(*) AS n FROM emails
               WHERE year IS NOT NULL
               GROUP BY year ORDER BY year DESC"""
        ).fetchall()
        return [{"year": r["year"], "count": r["n"]} for r in rows]

    def list_tags(self) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """SELECT t.id, t.name, t.color, COUNT(et.email_id) AS n
               FROM tags t
               LEFT JOIN email_tags et ON et.tag_id = t.id
               GROUP BY t.id
               ORDER BY t.name COLLATE NOCASE"""
        ).fetchall()
        return [
            {"id": r["id"], "name": r["name"], "color": r["color"], "count": r["n"]}
            for r in rows
        ]

    def ensure_tag(self, name: str, color: str | None = None) -> int:
        name = name.strip()
        if not name:
            raise ValueError("Tag name is empty")
        row = self.conn.execute(
            "SELECT id FROM tags WHERE name = ? COLLATE NOCASE", (name,)
        ).fetchone()
        if row:
            if color:
                self.conn.execute("UPDATE tags SET color = ? WHERE id = ?", (color, row["id"]))
                self.conn.commit()
            return int(row["id"])
        cur = self.conn.execute(
            "INSERT INTO tags(name, color) VALUES (?, ?)",
            (name, color or "#3b82f6"),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def delete_tag(self, tag_id: int) -> None:
        self.conn.execute("DELETE FROM tags WHERE id = ?", (tag_id,))
        self.conn.commit()

    def set_starred(self, email_id: int, starred: bool) -> None:
        self.conn.execute(
            "UPDATE emails SET starred = ? WHERE id = ?",
            (1 if starred else 0, email_id),
        )
        self.conn.commit()

    def set_unread(self, email_id: int, unread: bool) -> None:
        self.conn.execute(
            "UPDATE emails SET unread = ? WHERE id = ?",
            (1 if unread else 0, email_id),
        )
        self.conn.commit()

    def set_note(self, email_id: int, note: str) -> None:
        self.conn.execute(
            """INSERT INTO notes(email_id, note) VALUES(?, ?)
               ON CONFLICT(email_id) DO UPDATE SET note=excluded.note""",
            (email_id, note),
        )
        self.conn.commit()

    def tag_email(self, email_id: int, tag_id: int, on: bool) -> None:
        if on:
            self.conn.execute(
                "INSERT OR IGNORE INTO email_tags(email_id, tag_id) VALUES (?, ?)",
                (email_id, tag_id),
            )
        else:
            self.conn.execute(
                "DELETE FROM email_tags WHERE email_id = ? AND tag_id = ?",
                (email_id, tag_id),
            )
        rec = self.conn.execute("SELECT * FROM emails WHERE id = ?", (email_id,)).fetchone()
        if rec and self.fts_ok:
            self.conn.execute("DELETE FROM emails_fts WHERE rowid = ?", (email_id,))
            self.conn.execute(
                """INSERT INTO emails_fts(rowid, subject, sender, recipients, body_text, filename, folder, tags)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (
                    email_id,
                    rec["subject"],
                    rec["sender"] + " " + rec["sender_email"],
                    rec["recipients"] + " " + rec["recipient_emails"],
                    rec["body_text"],
                    rec["filename"],
                    rec["folder"],
                    self._tag_names_for(email_id),
                ),
            )
        self.conn.commit()

    def get_email(self, email_id: int) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM emails WHERE id = ?", (email_id,)).fetchone()
        if not row:
            return None
        data = dict(row)
        data["attachments"] = [
            dict(r)
            for r in self.conn.execute(
                "SELECT * FROM attachments WHERE email_id = ? ORDER BY part_index",
                (email_id,),
            )
        ]
        data["tags"] = [
            {"id": r["id"], "name": r["name"], "color": r["color"]}
            for r in self.conn.execute(
                """SELECT t.id, t.name, t.color FROM tags t
                   JOIN email_tags et ON et.tag_id = t.id
                   WHERE et.email_id = ? ORDER BY t.name COLLATE NOCASE""",
                (email_id,),
            )
        ]
        note = self.conn.execute(
            "SELECT note FROM notes WHERE email_id = ?", (email_id,)
        ).fetchone()
        data["note"] = note["note"] if note else ""
        return data

    def tags_map(self, email_ids: Iterable[int]) -> dict[int, list[dict[str, Any]]]:
        ids = list(email_ids)
        if not ids:
            return {}
        qmarks = ",".join("?" * len(ids))
        rows = self.conn.execute(
            f"""SELECT et.email_id, t.id, t.name, t.color
                FROM email_tags et JOIN tags t ON t.id = et.tag_id
                WHERE et.email_id IN ({qmarks})
                ORDER BY t.name COLLATE NOCASE""",
            ids,
        ).fetchall()
        out: dict[int, list[dict[str, Any]]] = {i: [] for i in ids}
        for r in rows:
            out[r["email_id"]].append(
                {"id": r["id"], "name": r["name"], "color": r["color"]}
            )
        return out


def _under_root(path: str, root: str) -> bool:
    root = root.rstrip("\\/")
    if not root:
        return False
    if path == root:
        return True
    return path.startswith(root + "/") or path.startswith(root + "\\") or path.startswith(
        root + os.sep
    )
