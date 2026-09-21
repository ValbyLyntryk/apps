"""Parse .eml files into a flat record used by the index and viewer."""

from __future__ import annotations

import email.policy
import email.utils
from datetime import datetime, timezone
from email.message import EmailMessage, Message
from email.parser import BytesParser
from pathlib import Path
from typing import Any

BODY_TEXT_LIMIT = 200_000
SNIPPET_LIMIT = 280


class ParseError(Exception):
    """Raised when an EML file cannot be read as an email message."""


def _as_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _decode_payload(part: Message) -> str:
    try:
        content = part.get_content()
    except Exception:
        payload = part.get_payload(decode=True)
        if payload is None:
            raw = part.get_payload()
            return _as_str(raw)
        charset = part.get_content_charset() or "utf-8"
        try:
            return payload.decode(charset, errors="replace")
        except LookupError:
            return payload.decode("utf-8", errors="replace")
    if isinstance(content, bytes):
        return content.decode("utf-8", errors="replace")
    return _as_str(content)


def _addresses(header_value: str) -> tuple[str, str]:
    """Return (display, emails-only) from a header."""
    display_parts: list[str] = []
    emails: list[str] = []
    for name, addr in email.utils.getaddresses([header_value or ""]):
        addr = (addr or "").strip()
        name = (name or "").strip()
        if name and addr:
            display_parts.append(f"{name} <{addr}>")
        elif addr:
            display_parts.append(addr)
        elif name:
            display_parts.append(name)
        if addr:
            emails.append(addr.lower())
    return ", ".join(display_parts), ", ".join(emails)


def _parse_date(value: str) -> tuple[str, int]:
    if not value:
        return "", 0
    try:
        dt = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError, OverflowError):
        return value.strip(), 0
    if dt is None:
        return value.strip(), 0
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    ts = int(dt.timestamp())
    iso = dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return iso, ts


def _walk_bodies_and_parts(msg: Message) -> tuple[str, str, list[dict[str, Any]]]:
    text_parts: list[str] = []
    html_parts: list[str] = []
    attachments: list[dict[str, Any]] = []
    part_index = 0

    def consider(part: Message) -> None:
        nonlocal part_index
        if part.is_multipart():
            return
        ctype = (part.get_content_type() or "application/octet-stream").lower()
        disp = (part.get_content_disposition() or "").lower()
        filename = part.get_filename() or ""
        cid = (part.get("Content-ID") or "").strip().strip("<>")
        is_attachment = disp == "attachment" or (bool(filename) and disp != "inline")
        is_inline_file = disp == "inline" and (bool(filename) or bool(cid)) and not ctype.startswith(
            "text/"
        )

        if is_attachment or is_inline_file:
            payload = None
            try:
                payload = part.get_payload(decode=True)
            except Exception:
                payload = None
            size = len(payload) if isinstance(payload, (bytes, bytearray)) else 0
            attachments.append(
                {
                    "part_index": part_index,
                    "filename": filename or (f"inline-{cid}" if cid else f"part-{part_index}"),
                    "content_type": ctype,
                    "size_bytes": size,
                    "content_id": cid,
                    "inline": disp == "inline",
                }
            )
            part_index += 1
            return

        if ctype == "text/plain":
            body = _decode_payload(part).strip()
            if body:
                text_parts.append(body)
        elif ctype == "text/html":
            body = _decode_payload(part).strip()
            if body:
                html_parts.append(body)
        part_index += 1

    if msg.is_multipart():
        for part in msg.walk():
            consider(part)
    else:
        consider(msg)

    text = "\n\n".join(text_parts)
    html = "\n".join(html_parts)
    if not text and html:
        text = html_to_text(html)
    return text, html, attachments


def html_to_text(html: str) -> str:
    """Very small HTML-to-text fallback for indexing when no text/plain part exists."""
    import html as htmlmod
    import re

    stripped = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html)
    stripped = re.sub(r"(?i)<br\s*/?>", "\n", stripped)
    stripped = re.sub(r"(?i)</p>", "\n", stripped)
    stripped = re.sub(r"(?i)</div>", "\n", stripped)
    stripped = re.sub(r"(?s)<[^>]+>", " ", stripped)
    stripped = htmlmod.unescape(stripped)
    stripped = re.sub(r"[ \t]+\n", "\n", stripped)
    stripped = re.sub(r"\n{3,}", "\n\n", stripped)
    stripped = re.sub(r"[ \t]{2,}", " ", stripped)
    return stripped.strip()


def snippet_from(text: str) -> str:
    compact = " ".join((text or "").split())
    if len(compact) <= SNIPPET_LIMIT:
        return compact
    return compact[: SNIPPET_LIMIT - 1].rstrip() + "…"


def parse_eml_bytes(data: bytes, path: Path | None = None) -> dict[str, Any]:
    try:
        msg = BytesParser(policy=email.policy.default).parsebytes(data)
    except Exception as exc:
        raise ParseError(f"Could not parse email: {exc}") from exc
    return _record_from_message(msg, data, path)


def parse_eml_file(path: Path) -> dict[str, Any]:
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise ParseError(f"Could not read {path}: {exc}") from exc
    return parse_eml_bytes(data, path)


def _record_from_message(msg: Message, data: bytes, path: Path | None) -> dict[str, Any]:
    subject = _as_str(msg.get("Subject", "")).strip()
    sender_disp, sender_emails = _addresses(_as_str(msg.get("From", "")))
    to_disp, to_emails = _addresses(_as_str(msg.get("To", "")))
    cc_disp, cc_emails = _addresses(_as_str(msg.get("Cc", "")))
    date_iso, date_ts = _parse_date(_as_str(msg.get("Date", "")))
    text, html, attachments = _walk_bodies_and_parts(msg)
    text = text[:BODY_TEXT_LIMIT]
    recipients_disp = ", ".join(p for p in (to_disp, cc_disp) if p)
    recipients_emails = ", ".join(p for p in (to_emails, cc_emails) if p)

    filename = path.name if path else ""
    folder = ""
    mtime_ns = 0
    size_bytes = len(data)
    if path is not None:
        try:
            st = path.stat()
            mtime_ns = getattr(st, "st_mtime_ns", int(st.st_mtime * 1_000_000_000))
            size_bytes = st.st_size
        except OSError:
            pass

    if not date_ts and mtime_ns:
        date_ts = int(mtime_ns / 1_000_000_000)
        date_iso = datetime.fromtimestamp(date_ts, tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )

    return {
        "path": str(path) if path else "",
        "filename": filename,
        "folder": folder,
        "message_id": _as_str(msg.get("Message-ID", "")).strip().strip("<>"),
        "in_reply_to": _as_str(msg.get("In-Reply-To", "")).strip().strip("<>"),
        "date_iso": date_iso,
        "date_ts": date_ts,
        "sender": sender_disp,
        "sender_email": sender_emails,
        "recipients": recipients_disp,
        "recipient_emails": recipients_emails,
        "cc": cc_disp,
        "subject": subject or "(no subject)",
        "snippet": snippet_from(text),
        "body_text": text,
        "has_html": 1 if html else 0,
        "has_attachments": 1 if any(not a.get("inline") for a in attachments) else 0,
        "attachment_count": sum(1 for a in attachments if not a.get("inline")),
        "size_bytes": size_bytes,
        "mtime_ns": mtime_ns,
        "attachments": attachments,
        "year": datetime.fromtimestamp(date_ts, tz=timezone.utc).year if date_ts else None,
    }


def load_message(path: Path) -> EmailMessage:
    data = path.read_bytes()
    msg = BytesParser(policy=email.policy.default).parsebytes(data)
    if not isinstance(msg, EmailMessage):
        # policy.default returns EmailMessage
        raise ParseError("Unexpected message type")
    return msg


def iter_parts(msg: Message) -> list[Message]:
    parts: list[Message] = []
    if msg.is_multipart():
        for part in msg.walk():
            if not part.is_multipart():
                parts.append(part)
    else:
        parts.append(msg)
    return parts


def get_html_body(msg: Message) -> str:
    _, html, _ = _walk_bodies_and_parts(msg)
    return html


def get_text_body(msg: Message) -> str:
    text, html, _ = _walk_bodies_and_parts(msg)
    return text or html_to_text(html)


def find_part_by_index(msg: Message, part_index: int) -> Message | None:
    idx = 0
    for part in iter_parts(msg):
        if idx == part_index:
            return part
        idx += 1
    return None


def find_part_by_cid(msg: Message, content_id: str) -> Message | None:
    want = content_id.strip().strip("<>").lower()
    if not want:
        return None
    for part in iter_parts(msg):
        cid = (part.get("Content-ID") or "").strip().strip("<>").lower()
        if cid == want:
            return part
    return None


def decode_part_bytes(part: Message) -> bytes:
    payload = part.get_payload(decode=True)
    if isinstance(payload, (bytes, bytearray)):
        return bytes(payload)
    raw = part.get_payload()
    if isinstance(raw, bytes):
        return raw
    return _as_str(raw).encode("utf-8", errors="replace")
