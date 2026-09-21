"""Turn a search box string into SQL filters plus an optional FTS query."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from .store import Store

SORTS = {
    "date_desc": "e.date_ts DESC, e.id DESC",
    "date_asc": "e.date_ts ASC, e.id ASC",
    "sender": "e.sender COLLATE NOCASE ASC, e.date_ts DESC",
    "subject": "e.subject COLLATE NOCASE ASC, e.date_ts DESC",
    "size_desc": "e.size_bytes DESC, e.date_ts DESC",
    "size_asc": "e.size_bytes ASC, e.date_ts DESC",
    "folder": "e.folder COLLATE NOCASE ASC, e.date_ts DESC",
}

TOKEN_RE = re.compile(
    r'\s*(?:(\w+):(?:"((?:[^"\\]|\\.)*)"|(\S+))|"((?:[^"\\]|\\.)*)"|(\S+))',
    re.UNICODE,
)

FTS_SPECIAL = re.compile(r'[\^"\*\(\):]')


def _unquote(value: str) -> str:
    return value.replace('\\"', '"').replace("\\\\", "\\")


def parse_query(raw: str) -> dict[str, Any]:
    """Split `from:x "quoted" after:2020-01-01 rest` into structured filters."""
    out: dict[str, Any] = {
        "from": [],
        "to": [],
        "subject": [],
        "tag": [],
        "folder": [],
        "filename": [],
        "has": [],
        "after": None,
        "before": None,
        "year": None,
        "terms": [],
        "phrases": [],
    }
    if not raw or not raw.strip():
        return out
    for match in TOKEN_RE.finditer(raw.strip()):
        key, qval, sval, phrase, word = match.groups()
        if key:
            val = _unquote(qval) if qval is not None else sval
            key_l = key.lower()
            if key_l in ("from", "to", "subject", "tag", "folder", "filename", "has"):
                out[key_l].append(val)
            elif key_l == "after":
                out["after"] = _parse_day(val, end=False)
            elif key_l == "before":
                out["before"] = _parse_day(val, end=True)
            elif key_l == "year":
                try:
                    out["year"] = int(val)
                except ValueError:
                    out["terms"].append(val)
            else:
                out["terms"].append(f"{key}:{val}")
        elif phrase is not None:
            out["phrases"].append(_unquote(phrase))
        elif word:
            if word.startswith("-") and len(word) > 1:
                out["terms"].append(word)
            else:
                out["terms"].append(word)
    return out


def _parse_day(value: str, end: bool) -> int | None:
    value = value.strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d-%m-%Y", "%Y-%m", "%Y"):
        try:
            dt = datetime.strptime(value, fmt)
            if fmt in ("%Y-%m", "%Y") and end:
                if fmt == "%Y":
                    dt = datetime(dt.year, 12, 31, 23, 59, 59, tzinfo=timezone.utc)
                else:
                    month = dt.month % 12 + 1
                    year = dt.year + (1 if dt.month == 12 else 0)
                    from datetime import timedelta

                    dt = datetime(year, month, 1, tzinfo=timezone.utc) - timedelta(seconds=1)
                return int(dt.replace(tzinfo=timezone.utc).timestamp())
            if end:
                dt = dt.replace(hour=23, minute=59, second=59, tzinfo=timezone.utc)
            else:
                dt = dt.replace(tzinfo=timezone.utc)
            return int(dt.timestamp())
        except ValueError:
            continue
    return None


def _like_contains(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _fts_quote(term: str) -> str:
    cleaned = term.replace('"', " ")
    if not cleaned.strip():
        return ""
    if FTS_SPECIAL.search(cleaned) or " " in cleaned:
        return '"' + cleaned.replace('"', "") + '"'
    return cleaned


def build_fts_match(parsed: dict[str, Any]) -> str:
    bits: list[str] = []
    for phrase in parsed["phrases"]:
        q = _fts_quote(phrase)
        if q:
            bits.append(q)
    for term in parsed["terms"]:
        if term.startswith("-") and len(term) > 1:
            q = _fts_quote(term[1:])
            if q:
                bits.append("NOT " + q)
        else:
            q = _fts_quote(term)
            if q:
                bits.append(q)
    return " AND ".join(bits)


def search(
    store: Store,
    q: str = "",
    folder: str | None = None,
    tag: str | None = None,
    starred: bool | None = None,
    unread: bool | None = None,
    has_attachments: bool | None = None,
    year: int | None = None,
    sort: str = "date_desc",
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    parsed = parse_query(q)
    where: list[str] = ["1=1"]
    params: list[Any] = []
    joins: list[str] = []

    if folder:
        if folder == "(root)":
            where.append("e.folder = ''")
        else:
            where.append("(e.folder = ? OR e.folder LIKE ?)")
            params.extend([folder, folder.rstrip("/\\") + "/%"])
            # Also match Windows-style in stored paths: we store posix-ish folders
            # using forward slashes, so the LIKE above is enough.

    if tag:
        joins.append("JOIN email_tags etf ON etf.email_id = e.id JOIN tags tf ON tf.id = etf.tag_id")
        where.append("tf.name = ? COLLATE NOCASE")
        params.append(tag)

    if starred is True:
        where.append("e.starred = 1")
    if unread is True:
        where.append("e.unread = 1")
    if has_attachments is True or "attachment" in [h.lower() for h in parsed["has"]]:
        where.append("e.has_attachments = 1")

    if year is not None:
        where.append("e.year = ?")
        params.append(year)
    elif parsed["year"] is not None:
        where.append("e.year = ?")
        params.append(parsed["year"])

    if parsed["after"] is not None:
        where.append("e.date_ts >= ?")
        params.append(parsed["after"])
    if parsed["before"] is not None:
        where.append("e.date_ts <= ?")
        params.append(parsed["before"])

    for val in parsed["from"]:
        where.append("(e.sender LIKE ? ESCAPE '\\' OR e.sender_email LIKE ? ESCAPE '\\')")
        like = _like_contains(val)
        params.extend([like, like])
    for val in parsed["to"]:
        where.append(
            "(e.recipients LIKE ? ESCAPE '\\' OR e.recipient_emails LIKE ? ESCAPE '\\')"
        )
        like = _like_contains(val)
        params.extend([like, like])
    for val in parsed["subject"]:
        where.append("e.subject LIKE ? ESCAPE '\\'")
        params.append(_like_contains(val))
    for val in parsed["folder"]:
        where.append("e.folder LIKE ? ESCAPE '\\'")
        params.append(_like_contains(val))
    for val in parsed["filename"]:
        where.append("e.filename LIKE ? ESCAPE '\\'")
        params.append(_like_contains(val))
    for i, val in enumerate(parsed["tag"]):
        et_alias = f"etq{i}"
        t_alias = f"tq{i}"
        joins.append(
            f"JOIN email_tags {et_alias} ON {et_alias}.email_id = e.id "
            f"JOIN tags {t_alias} ON {t_alias}.id = {et_alias}.tag_id"
        )
        where.append(f"{t_alias}.name = ? COLLATE NOCASE")
        params.append(val)

    fts_match = build_fts_match(parsed)
    use_fts = bool(fts_match) and store.fts_ok
    if use_fts:
        joins.append("JOIN emails_fts ON emails_fts.rowid = e.id")
        where.append("emails_fts MATCH ?")
        params.append(fts_match)
    elif fts_match:
        # LIKE fallback: apply each term/phrase against several columns
        for phrase in parsed["phrases"] + [t for t in parsed["terms"] if not t.startswith("-")]:
            where.append(
                """(e.subject LIKE ? ESCAPE '\\' OR e.sender LIKE ? ESCAPE '\\'
                    OR e.recipients LIKE ? ESCAPE '\\' OR e.body_text LIKE ? ESCAPE '\\'
                    OR e.filename LIKE ? ESCAPE '\\' OR e.folder LIKE ? ESCAPE '\\')"""
            )
            like = _like_contains(phrase)
            params.extend([like] * 6)

    order = SORTS.get(sort, SORTS["date_desc"])
    join_sql = " ".join(joins)
    where_sql = " AND ".join(where)

    count_sql = f"SELECT COUNT(DISTINCT e.id) AS n FROM emails e {join_sql} WHERE {where_sql}"
    total = store.conn.execute(count_sql, params).fetchone()["n"]

    list_sql = f"""
        SELECT DISTINCT e.id, e.filename, e.folder, e.date_iso, e.date_ts, e.sender,
               e.sender_email, e.recipients, e.subject, e.snippet, e.has_html,
               e.has_attachments, e.attachment_count, e.size_bytes, e.starred, e.unread, e.year
        FROM emails e
        {join_sql}
        WHERE {where_sql}
        ORDER BY {order}
        LIMIT ? OFFSET ?
    """
    rows = store.conn.execute(list_sql, params + [limit, offset]).fetchall()
    items = [dict(r) for r in rows]
    tags = store.tags_map(i["id"] for i in items)
    for item in items:
        item["tags"] = tags.get(item["id"], [])
        item["body_text"] = None  # never send full body in list
    return {"total": total, "offset": offset, "limit": limit, "emails": items}
