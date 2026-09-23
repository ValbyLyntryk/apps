"""Parse .eml files into a flat record used by the index and viewer."""

from __future__ import annotations

import email.policy
import email.utils
from datetime import datetime, timezone
from email.message import Message
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


_CHARSET_ALIASES = {
    "utf8": "utf-8",
    "utf-8": "utf-8",
    "latin1": "latin-1",
    "iso-8859-1": "latin-1",
    "iso8859-1": "latin-1",
    "windows-1252": "cp1252",
    "cp-1252": "cp1252",
    "ansi_x3.4-1968": "ascii",
    "us-ascii": "ascii",
    "ascii": "ascii",
    "gb2312": "gb18030",
    "gbk": "gb18030",
    "ks_c_5601-1987": "cp949",
}


def _decode_bytes(payload: bytes, charset: str | None) -> str:
    names: list[str] = []
    if charset:
        names.append(_CHARSET_ALIASES.get(charset.lower().strip().strip("\"'"), charset))
    names.extend(["utf-8", "cp1252", "latin-1"])
    seen: set[str] = set()
    for name in names:
        if not name or name in seen:
            continue
        seen.add(name)
        try:
            return payload.decode(name, errors="strict" if name not in {"latin-1", "cp1252"} else "replace")
        except (LookupError, UnicodeDecodeError):
            continue
    return payload.decode("utf-8", errors="replace")


def _decode_payload(part: Message) -> str:
    try:
        content = part.get_content()
        if isinstance(content, bytes):
            return _decode_bytes(content, part.get_content_charset())
        if content is not None:
            return _as_str(content)
    except Exception:
        pass
    try:
        payload = part.get_payload(decode=True)
    except Exception:
        payload = None
    if isinstance(payload, (bytes, bytearray)):
        return _decode_bytes(bytes(payload), part.get_content_charset())
    raw = part.get_payload()
    if isinstance(raw, bytes):
        return _decode_bytes(raw, part.get_content_charset())
    return _as_str(raw)


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


TNEF_SIGNATURE = 0x223E9F78
TNEF_ATT_BODY = {0x0002800C, 0x0001800C}  # attBody as atpText / atpString
RTF_TYPES = {"text/rtf", "application/rtf", "text/richtext", "application/x-rtf"}
TNEF_TYPES = {
    "application/ms-tnef",
    "application/vnd.ms-tnef",
    "application/x-tnef",
    "application/tnef",
}
_SKIP_SNIFF_PREFIXES = ("image/", "audio/", "video/")
_SKIP_SNIFF_TYPES = {
    "application/pdf",
    "application/zip",
    "application/x-zip-compressed",
    "application/gzip",
    "application/x-gzip",
    "application/pkcs7-signature",
    "application/pkcs7-mime",
    "application/pgp-signature",
    "application/octet-stream",  # still sniffed; listed so callers can override
}
_BINARY_MAGIC = (
    b"%PDF",
    b"\x89PNG",
    b"\xff\xd8\xff",
    b"PK\x03\x04",
    b"GIF87a",
    b"GIF89a",
    b"\x00\x00\x01\x00",
    b"\xd0\xcf\x11\xe0",
)
_HTML_MARKERS = (
    b"<html",
    b"<!doctype html",
    b"<body",
    b"<div",
    b"<p>",
    b"<p ",
    b"<table",
    b"<span",
    b"<font",
    b"<br",
    b"<meta",
    b"<h1",
    b"<h2",
    b"<center",
)


def rtf_to_text(rtf: str | bytes) -> str:
    """Best-effort RTF to plain text. Stdlib only; good enough for archival bodies."""
    import re

    if isinstance(rtf, (bytes, bytearray)):
        text = bytes(rtf).decode("latin-1", errors="replace")
    else:
        text = rtf or ""
    brace = text.find("{\\rtf")
    if brace == -1:
        brace = text.find("{\\RTF")
    if brace > 0:
        text = text[brace:]

    def _hex(m: Any) -> str:
        try:
            return bytes.fromhex(m.group(1)).decode("cp1252", errors="replace")
        except Exception:
            return ""

    def _uni(m: Any) -> str:
        try:
            n = int(m.group(1))
            if n < 0:
                n += 65536
            return chr(n & 0xFFFF)
        except Exception:
            return ""

    text = re.sub(r"\\'([0-9a-fA-F]{2})", _hex, text)
    text = re.sub(r"\\u(-?\d+)\s?", _uni, text)
    text = re.sub(r"\{\\\*[^{}]*\}", " ", text)
    text = re.sub(r"\\(pard|par|line|row)\d*\s?", "\n", text)
    text = re.sub(r"\\tab\d*\s?", "\t", text)
    text = re.sub(r"\\[a-zA-Z]+\d*\s?", "", text)
    text = re.sub(r"\\[^a-zA-Z]", "", text)
    text = text.replace("{", "").replace("}", "")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


def _part_payload_bytes(part: Message) -> bytes:
    try:
        payload = part.get_payload(decode=True)
    except Exception:
        payload = None
    if isinstance(payload, (bytes, bytearray)):
        return bytes(payload)
    raw = part.get_payload()
    if isinstance(raw, bytes):
        return raw
    if isinstance(raw, str):
        return raw.encode("utf-8", errors="replace")
    return b""


def _looks_like_known_binary(data: bytes) -> bool:
    if not data:
        return False
    head = data[:8]
    return any(head.startswith(magic) for magic in _BINARY_MAGIC)


def _should_sniff(ctype: str) -> bool:
    if ctype.startswith(_SKIP_SNIFF_PREFIXES):
        return False
    if ctype in _SKIP_SNIFF_TYPES and ctype != "application/octet-stream":
        return False
    return True


def _extract_html_from_bytes(data: bytes) -> str:
    if not data or _looks_like_known_binary(data):
        return ""
    chunk = data
    if data.startswith(b"\xff\xfe") or data.startswith(b"\xfe\xff"):
        try:
            text = data.decode("utf-16", errors="replace")
        except Exception:
            return ""
        low = text.lstrip().lower()
        if "<html" in low or "<!doctype html" in low or "<body" in low or low.startswith("<"):
            return text.strip()[:BODY_TEXT_LIMIT]
        return ""
    low = data.lower()
    start = -1
    for marker in _HTML_MARKERS:
        i = low.find(marker)
        if i != -1 and (start == -1 or i < start):
            start = i
    if start == -1:
        return ""
    chunk = data[start : start + BODY_TEXT_LIMIT]
    end = chunk.lower().find(b"</html>")
    if end != -1:
        chunk = chunk[: end + 7]
    return _decode_bytes(chunk, "utf-8").strip()


def _extract_rtf_from_bytes(data: bytes) -> str:
    if not data:
        return ""
    i = data.find(b"{\\rtf")
    if i == -1:
        i = data.find(b"{\\RTF")
    if i == -1:
        return ""
    return rtf_to_text(data[i : i + BODY_TEXT_LIMIT])


def _extract_plain_from_bytes(data: bytes) -> str:
    if not data or _looks_like_known_binary(data):
        return ""
    sample = data[:8000]
    if sample.startswith(b"\xff\xfe") or sample.startswith(b"\xfe\xff"):
        try:
            return data[:BODY_TEXT_LIMIT].decode("utf-16", errors="replace").strip()
        except Exception:
            return ""
    nul = sample.count(0)
    if nul > len(sample) * 0.08:
        if len(sample) > 3 and sample[1::2].count(0) > len(sample) * 0.3:
            try:
                return data[:BODY_TEXT_LIMIT].decode("utf-16-le", errors="replace").strip()
            except Exception:
                return ""
        return ""
    text = _decode_bytes(data[:BODY_TEXT_LIMIT], None).replace("\x00", "")
    if len(text.strip()) < 2:
        return ""
    probe = text[:4000]
    printable = sum(1 for c in probe if c.isprintable() or c in "\r\n\t")
    if printable < len(probe) * 0.88:
        return ""
    letters = sum(1 for c in probe if c.isalpha())
    if letters < 8:
        return ""
    return text.strip()


def extract_tnef_bodies(data: bytes) -> tuple[str, str]:
    """Pull attBody plus HTML / RTF blobs out of winmail.dat. No extra packages."""
    texts: list[str] = []
    htmls: list[str] = []
    if len(data) >= 6 and int.from_bytes(data[:4], "little") == TNEF_SIGNATURE:
        pos = 6
        n = len(data)
        while pos + 9 <= n:
            lvl = data[pos]
            if lvl not in (1, 2):
                break
            attr_id = int.from_bytes(data[pos + 1 : pos + 5], "little")
            length = int.from_bytes(data[pos + 5 : pos + 9], "little")
            pos += 9
            if length < 0 or pos + length + 2 > n:
                break
            payload = data[pos : pos + length]
            pos += length + 2
            if attr_id in TNEF_ATT_BODY and payload:
                decoded = _decode_bytes(payload.rstrip(b"\x00"), None).strip()
                if decoded:
                    texts.append(decoded)
            html = _extract_html_from_bytes(payload)
            if html:
                htmls.append(html)
            rtxt = _extract_rtf_from_bytes(payload)
            if rtxt:
                texts.append(rtxt)
    html = _extract_html_from_bytes(data)
    if html:
        htmls.append(html)
    rtxt = _extract_rtf_from_bytes(data)
    if rtxt:
        texts.append(rtxt)

    def _unique_join(parts: list[str]) -> str:
        seen: set[str] = set()
        out: list[str] = []
        for part in parts:
            if part and part not in seen:
                seen.add(part)
                out.append(part)
        return "\n\n".join(out)

    return _unique_join(texts), _unique_join(htmls)


def _raw_after_headers(data: bytes) -> bytes:
    for sep in (b"\r\n\r\n", b"\n\n"):
        i = data.find(sep)
        if i != -1:
            return data[i + len(sep) :]
    return data


def _nested_message(part: Message) -> Message | None:
    try:
        content = part.get_content()
        if isinstance(content, Message):
            return content
        if isinstance(content, (bytes, bytearray)) and content:
            return parse_message_bytes(bytes(content))
        if isinstance(content, str) and ":" in content[:800]:
            return parse_message_bytes(content.encode("utf-8", errors="replace"))
    except Exception:
        pass
    payload = part.get_payload()
    if isinstance(payload, list) and payload:
        first = payload[0]
        if isinstance(first, Message):
            return first
        if isinstance(first, (bytes, bytearray)):
            try:
                return parse_message_bytes(bytes(first))
            except Exception:
                pass
        if isinstance(first, str):
            try:
                return parse_message_bytes(first.encode("utf-8", errors="replace"))
            except Exception:
                pass
    if isinstance(payload, Message):
        return payload
    if isinstance(payload, (bytes, bytearray)):
        try:
            return parse_message_bytes(bytes(payload))
        except Exception:
            pass
    if isinstance(payload, str):
        try:
            return parse_message_bytes(payload.encode("utf-8", errors="replace"))
        except Exception:
            pass
    decoded = _part_payload_bytes(part)
    if decoded:
        try:
            return parse_message_bytes(decoded)
        except Exception:
            pass
    return None


def _att_record(
    part_index: int,
    filename: str,
    ctype: str,
    size: int,
    cid: str,
    inline: bool,
) -> dict[str, Any]:
    return {
        "part_index": part_index,
        "filename": filename or (f"inline-{cid}" if cid else f"part-{part_index}"),
        "content_type": ctype,
        "size_bytes": size,
        "content_id": cid,
        "inline": inline,
    }


def _walk_bodies_and_parts(
    msg: Message, raw: bytes | None = None, _depth: int = 0
) -> tuple[str, str, list[dict[str, Any]]]:
    text_parts: list[str] = []
    html_parts: list[str] = []
    attachments: list[dict[str, Any]] = []
    leftover_blobs: list[bytes] = []
    part_index = 0
    if _depth > 8:
        return "", "", []

    def consider(part: Message) -> None:
        nonlocal part_index
        if part.is_multipart():
            return
        ctype = (part.get_content_type() or "application/octet-stream").lower()
        disp = (part.get_content_disposition() or "").lower()
        filename = part.get_filename() or ""
        cid = (part.get("Content-ID") or "").strip().strip("<>")
        fname_l = filename.lower()

        if ctype in {"message/rfc822", "message/global"} or ctype.startswith("message/rfc822"):
            nested = _nested_message(part)
            if nested is not None:
                nested_text, nested_html, _nested_atts = _walk_bodies_and_parts(
                    nested, None, _depth + 1
                )
                if nested_text:
                    text_parts.append(nested_text)
                if nested_html:
                    html_parts.append(nested_html)
            payload = _part_payload_bytes(part)
            if filename or disp == "attachment":
                attachments.append(
                    _att_record(part_index, filename or "forwarded.eml", ctype, len(payload), cid, False)
                )
            part_index += 1
            return

        is_text_body = ctype in {"text/plain", "text/html"} or ctype.startswith("text/plain") or ctype.startswith(
            "text/html"
        )
        is_rtf = ctype in RTF_TYPES or fname_l.endswith(".rtf")
        is_tnef = ctype in TNEF_TYPES or fname_l == "winmail.dat" or (
            fname_l.endswith(".dat") and "tnef" in ctype
        )

        if is_text_body:
            body = _decode_payload(part).strip()
            if body:
                if ctype.startswith("text/html"):
                    html_parts.append(body)
                else:
                    text_parts.append(body)
            if disp == "attachment" and filename:
                payload = _part_payload_bytes(part)
                attachments.append(_att_record(part_index, filename, ctype, len(payload), cid, False))
            part_index += 1
            return

        payload = _part_payload_bytes(part)

        if is_rtf:
            rtxt = rtf_to_text(payload) if payload else rtf_to_text(_decode_payload(part))
            if rtxt:
                text_parts.append(rtxt)
            if filename or disp == "attachment":
                attachments.append(_att_record(part_index, filename or "body.rtf", ctype, len(payload), cid, False))
            part_index += 1
            return

        if is_tnef or fname_l == "winmail.dat":
            ttext, thtml = extract_tnef_bodies(payload)
            if ttext:
                text_parts.append(ttext)
            if thtml:
                html_parts.append(thtml)
            attachments.append(
                _att_record(part_index, filename or "winmail.dat", ctype, len(payload), cid, disp == "inline")
            )
            part_index += 1
            return

        sniffed_html = ""
        sniffed_rtf = ""
        if payload and _should_sniff(ctype):
            sniffed_html = _extract_html_from_bytes(payload)
            if sniffed_html:
                html_parts.append(sniffed_html)
            else:
                sniffed_rtf = _extract_rtf_from_bytes(payload)
                if sniffed_rtf:
                    text_parts.append(sniffed_rtf)

        is_binary_attach = disp == "attachment" or bool(filename) or bool(cid)
        if is_binary_attach or ctype.startswith("application/") or not (sniffed_html or sniffed_rtf):
            if payload and not sniffed_html and not sniffed_rtf:
                leftover_blobs.append(payload)
            if is_binary_attach or ctype.startswith("application/"):
                attachments.append(
                    _att_record(
                        part_index,
                        filename,
                        ctype,
                        len(payload),
                        cid,
                        disp == "inline",
                    )
                )
        part_index += 1

    if msg.is_multipart():
        for part in msg.walk():
            consider(part)
    else:
        consider(msg)

    if not text_parts and not html_parts:
        for blob in leftover_blobs:
            html = _extract_html_from_bytes(blob)
            if html:
                html_parts.append(html)
                continue
            rtxt = _extract_rtf_from_bytes(blob)
            if rtxt:
                text_parts.append(rtxt)
                continue
            plain = _extract_plain_from_bytes(blob)
            if plain:
                text_parts.append(plain)
        if not text_parts and not html_parts:
            raw_bytes = raw
            if not raw_bytes:
                try:
                    raw_bytes = msg.as_bytes()
                except Exception:
                    raw_bytes = b""
            if raw_bytes:
                rest = _raw_after_headers(raw_bytes)
                html = _extract_html_from_bytes(rest)
                if html:
                    html_parts.append(html)
                else:
                    rtxt = _extract_rtf_from_bytes(rest)
                    if rtxt:
                        text_parts.append(rtxt)
                    elif not msg.is_multipart():
                        plain = _extract_plain_from_bytes(rest)
                        if plain:
                            text_parts.append(plain)

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
        msg = parse_message_bytes(data)
    except ParseError:
        raise
    except Exception as exc:
        raise ParseError(f"Could not parse email: {exc}") from exc
    return _record_from_message(msg, data, path)


def parse_eml_file(path: Path) -> dict[str, Any]:
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise ParseError(f"Could not read {path}: {exc}") from exc
    return parse_eml_bytes(data, path)


def normalize_raw_bytes(data: bytes) -> bytes:
    """Accept UTF-16 dumps and Apple .emlx wrappers."""
    if data.startswith(b"\xff\xfe") or data.startswith(b"\xfe\xff"):
        data = data.decode("utf-16", errors="replace").encode("utf-8", errors="replace")
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    nl = data.find(b"\n")
    if 0 < nl <= 16 and data[:nl].strip().isdigit():
        data = data[nl + 1 :]
        marker = data.rfind(b"\n<?xml")
        if marker != -1:
            data = data[:marker]
    return data


def parse_message_bytes(data: bytes) -> Message:
    data = normalize_raw_bytes(data)
    errors: list[str] = []
    for pol in (email.policy.default, email.policy.compat32):
        try:
            return BytesParser(policy=pol).parsebytes(data)
        except Exception as exc:
            errors.append(str(exc))
    raise ParseError("; ".join(errors) or "Could not parse email")


def _record_from_message(msg: Message, data: bytes, path: Path | None) -> dict[str, Any]:
    subject = _as_str(msg.get("Subject", "")).strip()
    sender_disp, sender_emails = _addresses(_as_str(msg.get("From", "")))
    to_disp, to_emails = _addresses(_as_str(msg.get("To", "")))
    cc_disp, cc_emails = _addresses(_as_str(msg.get("Cc", "")))
    bcc_disp, bcc_emails = _addresses(_as_str(msg.get("Bcc", "")))
    date_iso, date_ts = _parse_date(_as_str(msg.get("Date", "")))
    text, html, attachments = _walk_bodies_and_parts(msg, data)
    text = text[:BODY_TEXT_LIMIT]
    recipients_disp = ", ".join(p for p in (to_disp, cc_disp, bcc_disp) if p)
    recipients_emails = ", ".join(p for p in (to_emails, cc_emails, bcc_emails) if p)

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


def load_message(path: Path) -> Message:
    data = path.read_bytes()
    return parse_message_bytes(data)


def iter_parts(msg: Message) -> list[Message]:
    parts: list[Message] = []
    if msg.is_multipart():
        for part in msg.walk():
            if not part.is_multipart():
                parts.append(part)
    else:
        parts.append(msg)
    return parts


def _message_raw(msg: Message) -> bytes | None:
    try:
        return msg.as_bytes()
    except Exception:
        return None


def get_html_body(msg: Message) -> str:
    _, html, _ = _walk_bodies_and_parts(msg, _message_raw(msg))
    return html


def get_text_body(msg: Message) -> str:
    text, html, _ = _walk_bodies_and_parts(msg, _message_raw(msg))
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
